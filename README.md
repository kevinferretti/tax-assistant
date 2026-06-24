# Form — an agentic 2025 Form 1040 assistant

Chat with a warm assistant, hand it a W-2, and walk away with a **completed, downloadable
2025 IRS Form 1040** — built on a harness where the LLM converses but a deterministic engine
owns every number.

**▶ Live: https://tax.kevinferretti.com** &nbsp;·&nbsp; *Educational demo — fake data only, not tax advice, not for real filing.*

---

## The four pillars (real and enforced, not cosmetic)

| Pillar | How it's realized |
|---|---|
| **Chat loop + state** | Event-sourced session: state = `fold(append-only events)`; conversation streamed over SSE. |
| **Tools** | The model's only powers — `extract_w2` (vision + confidence), `update_return` (validated), `compute_tax` (deterministic engine), `generate_1040_pdf` (the real form), `ask_user`. |
| **Guardrails** | Hard **5-question budget** (`ask_user` refused past the limit); **PII boundary** (SSN never reaches the chat model, masked in logs/trace); scope detection; **PDF refused unless an independent verification pass succeeds**. |
| **Observation** | The event log *is* the trace — PII-masked at `/api/trace`, in a collapsible UI activity panel, and in structured server logs. |

See [DECISIONS.md](DECISIONS.md) for the full reasoning behind each open-item choice.

## Run it locally (one command)

Needs Docker and an OpenAI API key.

```bash
cp .env.example .env          # then put your OPENAI_API_KEY in .env
docker build -t tax-assistant . && docker run --rm -p 8000:8000 --env-file .env tax-assistant
# open http://localhost:8000  →  click "Try a sample W-2"
```

Without Docker:

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
echo "OPENAI_API_KEY=sk-..." > .env
uvicorn app.main:app --port 8000
```

The model ids are **verified against your account at startup** (best available GPT-5-family
chat + vision); override with `OPENAI_MODEL` / `OPENAI_VISION_MODEL`.

## Tests & evals

```bash
pip install -r requirements-dev.txt
pytest                      # deterministic unit tests (engine, EITC, verification, PDF, guardrails, state)
python -m evals.run_evals   # opt-in evals (golden 1040s; + LLM conversation/red-team if a key is set)
```

The eval suite is **excluded from CI** (`pytest.ini` scopes to `tests/`) so it never auto-runs.

## Architecture (where to look)

```
app/tax_engine.py      deterministic 2025 1040 math (Tax-Table method, CTC/ODC/ACTC, EITC) + provenance
app/tax_tables_2025.py OBBBA-updated 2025 figures, as auditable data
app/verification.py    independent "second accountant" re-derivation + invariants (gates the PDF)
app/pdf_filler.py      stamps + flattens the official IRS f1040.pdf
app/schemas.py         validated W2/Dependent + ReturnState (eligibility derived in code)
app/events.py          event-sourced state (fold) — also the observation trail
app/guardrails.py      question budget, PII masking, scope
app/tools.py           the 5 agent tools + W-2 vision extraction
app/agent.py           streaming chat loop; budget enforced via ask_user
app/main.py            FastAPI: SSE chat, upload, sample, download, /api/trace
web/                   hand-built vanilla UI (no build step)
```

## Deployment

Single container behind a shared **Caddy** edge proxy on an OVH VPS (auto-TLS), joined to the
proxy network under the `tax-assistant` alias. Auto-deploys on push to `main` via
[`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) once these repo secrets are set:
`OPENAI_API_KEY`, `OVH_SSH_PRIVATE_KEY`, `OVH_SSH_KNOWN_HOSTS`.
