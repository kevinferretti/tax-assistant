"""
FastAPI app: serves the single-page UI and the agent API.

Endpoints
  GET  /                       -> the chat UI
  GET  /api/health             -> health check (used by deploy)
  POST /api/upload             -> stash a W-2 image/PDF on the session
  POST /api/use-sample         -> stash the bundled sample W-2 on the session
  POST /api/chat               -> Server-Sent Events stream of a turn
  GET  /api/download/{token}   -> download a generated 1040 PDF
  GET  /api/trace              -> the PII-masked observation trail (current session)
"""
from __future__ import annotations

import io
import json
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Request, Response, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles

from .agent import run_turn
from .observability import public_trace
from .sessions import STORE

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
SAMPLE_W2 = ROOT / "testdata" / "w2_images" / "01_single_40k_baseline.png"
MAX_UPLOAD = 12 * 1024 * 1024  # 12 MB
COOKIE = "tax_sid"

app = FastAPI(title="Agentic 2025 Form 1040 Assistant")
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


# ---- session cookie helper --------------------------------------------------
def _session(request: Request):
    return STORE.get_or_create(request.cookies.get(COOKIE))


def _with_cookie(response: Response, sid: str) -> Response:
    response.set_cookie(COOKIE, sid, httponly=True, samesite="lax", max_age=7200)
    return response


# ---- pages / health ---------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    return (WEB / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health():
    return {"status": "ok"}


# ---- W-2 intake -------------------------------------------------------------
def _to_image(data: bytes, content_type: str, filename: str) -> tuple[bytes, str]:
    """Normalize an upload to an image the vision model can read (PDF -> PNG)."""
    is_pdf = (content_type == "application/pdf") or filename.lower().endswith(".pdf")
    if is_pdf:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(data)
        try:
            img = doc[0].render(scale=200 / 72).to_pil()
        finally:
            doc.close()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), "image/png"
    return data, (content_type or "image/png")


@app.post("/api/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    session = _session(request)
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        return _with_cookie(JSONResponse({"error": "File too large (max 12 MB)."}, 413), session.id)
    img_bytes, mime = _to_image(data, file.content_type or "", file.filename or "")
    session.pending_w2 = {"bytes": img_bytes, "mime": mime}
    session.record("w2_uploaded", f"User uploaded a W-2 ({file.filename or 'image'}).")
    return _with_cookie(JSONResponse({"ok": True}), session.id)


@app.post("/api/use-sample")
def use_sample(request: Request):
    session = _session(request)
    data = SAMPLE_W2.read_bytes()
    session.pending_w2 = {"bytes": data, "mime": "image/png"}
    session.record("w2_uploaded", "User loaded the sample W-2 (Jordan A. Avery).")
    return _with_cookie(JSONResponse({"ok": True}), session.id)


# ---- chat (SSE) -------------------------------------------------------------
def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    message = (body or {}).get("message") or ""
    just_uploaded = bool((body or {}).get("just_uploaded"))
    session = _session(request)

    def stream():
        try:
            for ev in run_turn(session, message, just_uploaded=just_uploaded):
                yield _sse(ev)
        except Exception as e:  # never leave the stream hanging
            yield _sse({"type": "error", "message": f"Unexpected error: {e}"})
            yield _sse({"type": "done"})

    resp = StreamingResponse(stream(), media_type="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return _with_cookie(resp, session.id)


# ---- download ---------------------------------------------------------------
@app.get("/api/download/{token}")
def download(request: Request, token: str):
    session = _session(request)
    pdf = session.get_pdf(token)
    if not pdf:
        return JSONResponse({"error": "Not found or expired."}, 404)
    return Response(
        content=pdf.content, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{pdf.filename}"'},
    )


# ---- observation trail ------------------------------------------------------
@app.get("/api/trace")
def trace(request: Request):
    session = _session(request)
    resp = JSONResponse({
        "session": session.id[:8],
        "questions_asked": session.log.questions_asked(),
        "events": public_trace(session.log),
    })
    return _with_cookie(resp, session.id)
