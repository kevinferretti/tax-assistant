"""
The agentic chat loop.

Streams a turn: the model converses (tokens streamed to the UI) and calls tools.
Control returns to the user when the model either finishes narrating or asks a
question via ``ask_user``. The 5-question budget is enforced right here — ask_user
past the limit is refused and the model is told to proceed.

``run_turn`` is a generator of event dicts the SSE endpoint forwards:
  {"type": "token"|"tool"|"question"|"state"|"pdf"|"notice"|"done"|"error", ...}
"""
from __future__ import annotations

import json

from .events import (USER_MESSAGE, ASSISTANT_MESSAGE, QUESTION_ASKED, GUARDRAIL, ERROR)
from .guardrails import can_ask_question, detect_out_of_scope, QUESTION_BUDGET
from .model_select import get_models
from .prompts import SYSTEM_PROMPT, runtime_context
from .tools import TOOL_SCHEMAS, run_tool

MAX_ROUNDS = 8  # tool/think rounds before we force the turn to end


def _state_summary(state) -> str:
    from decimal import Decimal
    bits = []
    if state.has_w2:
        wages = sum((w.box1_wages for w in state.w2s), Decimal(0))
        emp = state.w2s[0].employee_name if state.w2s else ""
        bits.append(f"W-2 for {emp or 'taxpayer'}, wages ${wages:,.0f}")
    if state.filing_status:
        bits.append(f"filing status = {state.filing_status}")
    if state.dependents:
        bits.append(f"{len(state.dependents)} dependent(s)")
    return "; ".join(bits)


def _build_messages(session) -> list[dict]:
    state = session.state()
    ctx = runtime_context(
        questions_asked=session.log.questions_asked(),
        budget=QUESTION_BUDGET,
        state_summary=_state_summary(state),
        has_pending_w2=bool(getattr(session, "pending_w2", None)),
    )
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": ctx}, *session.messages]


def _accumulate_tool_calls(acc: dict, deltas) -> None:
    for d in deltas:
        idx = d.index
        slot = acc.setdefault(idx, {"id": None, "name": None, "args": ""})
        if d.id:
            slot["id"] = d.id
        if d.function and d.function.name:
            slot["name"] = d.function.name
        if d.function and d.function.arguments:
            slot["args"] += d.function.arguments


def run_turn(session, user_text: str | None, *, just_uploaded: bool = False):
    cfg = get_models()

    if user_text:
        session.record(USER_MESSAGE, user_text)
        session.messages.append({"role": "user", "content": user_text})
        oos = detect_out_of_scope(user_text)
        if oos:
            session.record(GUARDRAIL, f"User asked about {oos} — out of scope; will redirect.",
                           reason=oos)
    if just_uploaded:
        session.messages.append({"role": "user",
                                 "content": "[The user uploaded a W-2 image.]"})

    for _ in range(MAX_ROUNDS):
        try:
            stream = cfg.client.chat.completions.create(
                model=cfg.chat_model,
                messages=_build_messages(session),
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                stream=True,
            )
        except Exception as e:
            session.record(ERROR, f"Model call failed: {e}")
            yield {"type": "error", "message": "Sorry — I hit a snag reaching the model."}
            yield {"type": "done"}
            return

        content_parts: list[str] = []
        tool_acc: dict = {}
        finish_reason = None
        for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if delta and delta.content:
                content_parts.append(delta.content)
                yield {"type": "token", "text": delta.content}
            if delta and delta.tool_calls:
                _accumulate_tool_calls(tool_acc, delta.tool_calls)
            if choice.finish_reason:
                finish_reason = choice.finish_reason

        content = "".join(content_parts)
        tool_calls = [tool_acc[i] for i in sorted(tool_acc)]

        # No tool calls -> the model finished narrating; end the turn.
        if not tool_calls:
            if content:
                session.messages.append({"role": "assistant", "content": content})
                session.record(ASSISTANT_MESSAGE, content)
            yield {"type": "done"}
            return

        # Commit the assistant message (with its tool calls) to history.
        session.messages.append({
            "role": "assistant",
            "content": content or None,
            "tool_calls": [{"id": tc["id"], "type": "function",
                            "function": {"name": tc["name"], "arguments": tc["args"] or "{}"}}
                           for tc in tool_calls],
        })
        if content:
            session.record(ASSISTANT_MESSAGE, content)

        end_question: str | None = None
        for tc in tool_calls:
            name = tc["name"]
            try:
                args = json.loads(tc["args"] or "{}")
            except json.JSONDecodeError:
                args = {}

            if name == "ask_user":
                question = (args.get("question") or "").strip()
                if can_ask_question(session.log.questions_asked()):
                    session.record(QUESTION_ASKED, question)
                    _tool_reply(session, tc["id"], {"status": "asked"})
                    end_question = end_question or question
                else:
                    session.record(GUARDRAIL,
                                   "Question budget reached — refusing further questions.",
                                   budget=QUESTION_BUDGET)
                    _tool_reply(session, tc["id"], {
                        "error": "Question budget exhausted. Do not ask more questions. "
                                 "Proceed with reasonable defaults: call compute_tax then "
                                 "generate_1040_pdf."})
                    yield {"type": "notice",
                           "message": "Reached the 5-question limit — finishing up with what we have."}
                continue

            # Action tool.
            yield {"type": "tool", "name": name, "status": "running"}
            result = run_tool(session, name, args)
            _tool_reply(session, tc["id"], result)
            yield {"type": "tool", "name": name, "status": "done",
                   "ok": "error" not in result}
            if name in ("compute_tax", "generate_1040_pdf") and "refund" in result:
                yield {"type": "state", "result": result}
            if name == "generate_1040_pdf" and result.get("download_token"):
                yield {"type": "pdf", "token": result["download_token"],
                       "filename": result["filename"], "url": result["download_url"]}

        if end_question is not None:
            yield {"type": "question", "text": end_question}
            yield {"type": "done"}
            return
        # else: loop again so the model can react to tool results

    yield {"type": "done"}


def _tool_reply(session, tool_call_id: str, payload: dict) -> None:
    session.messages.append({
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": json.dumps(payload, default=str),
    })
