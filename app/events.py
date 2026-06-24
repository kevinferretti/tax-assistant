"""
Event-sourced state.

Every meaningful thing that happens — a W-2 extracted, an answer given, a tool
called, a guardrail tripped, the tax computed, the PDF produced — is appended to
an immutable event log. Two payoffs from one mechanism:

  * The return's state is ``fold(events)`` — pure, replayable, and naturally
    supports mid-conversation corrections (just append a new RETURN_UPDATED).
  * The log *is* the observation trail surfaced to the UI and the trace endpoint.
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .schemas import Dependent, ReturnState, W2, normalize_filing_status

# Event kinds.
SESSION_STARTED = "session_started"
USER_MESSAGE = "user_message"
ASSISTANT_MESSAGE = "assistant_message"
QUESTION_ASKED = "question_asked"
W2_UPLOADED = "w2_uploaded"
W2_EXTRACTED = "w2_extracted"
RETURN_UPDATED = "return_updated"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"
TAX_COMPUTED = "tax_computed"
VERIFICATION = "verification"
PDF_GENERATED = "pdf_generated"
GUARDRAIL = "guardrail"
ERROR = "error"

# UI category per kind (drives grouping/coloring in the activity panel).
CATEGORY = {
    SESSION_STARTED: "system",
    USER_MESSAGE: "conversation",
    ASSISTANT_MESSAGE: "conversation",
    QUESTION_ASKED: "guardrail",
    W2_UPLOADED: "tool",
    W2_EXTRACTED: "tool",
    RETURN_UPDATED: "tool",
    TOOL_CALL: "tool",
    TOOL_RESULT: "tool",
    TAX_COMPUTED: "compute",
    VERIFICATION: "compute",
    PDF_GENERATED: "tool",
    GUARDRAIL: "guardrail",
    ERROR: "error",
}

_counter = itertools.count(1)


@dataclass
class Event:
    seq: int
    ts: float
    kind: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def category(self) -> str:
        return CATEGORY.get(self.kind, "system")

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "kind": self.kind,
            "category": self.category,
            "summary": self.summary,
            "data": self.data,
        }


class EventLog:
    def __init__(self):
        self.events: list[Event] = []

    def append(self, kind: str, summary: str, **data) -> Event:
        ev = Event(seq=next(_counter), ts=time.time(), kind=kind, summary=summary, data=data)
        self.events.append(ev)
        return ev

    def of_kind(self, *kinds: str) -> list[Event]:
        return [e for e in self.events if e.kind in kinds]

    def questions_asked(self) -> int:
        return len(self.of_kind(QUESTION_ASKED))

    def state(self) -> ReturnState:
        return fold(self.events)


def _dec(v) -> Decimal:
    return Decimal(str(v if v not in (None, "") else 0))


def fold(events: list[Event]) -> ReturnState:
    """Pure reduction of domain events into the current ReturnState."""
    state = ReturnState()
    for ev in events:
        if ev.kind == W2_EXTRACTED:
            w2_data = ev.data.get("w2") or {}
            try:
                state.w2s.append(W2(**w2_data))
            except Exception:
                pass
        elif ev.kind == RETURN_UPDATED:
            _apply_patch(state, ev.data.get("patch") or {})
    return state


def _apply_patch(state: ReturnState, patch: dict) -> None:
    if "filing_status" in patch:
        fs = normalize_filing_status(patch["filing_status"])
        if fs:
            state.filing_status = fs
    if "dependents" in patch and patch["dependents"] is not None:
        # full replacement so the agent can correct a prior answer
        deps = []
        for d in patch["dependents"]:
            try:
                deps.append(Dependent(**d) if isinstance(d, dict) else d)
            except Exception:
                pass
        state.dependents = deps
    for money_key in ("other_income", "adjustments", "investment_income"):
        if money_key in patch and patch[money_key] is not None:
            setattr(state, money_key, _dec(patch[money_key]))
    for plain_key in ("spouse_name", "spouse_ssn"):
        if patch.get(plain_key) is not None:
            setattr(state, plain_key, str(patch[plain_key]))
    for age_key in ("taxpayer_age", "spouse_age"):
        if patch.get(age_key) is not None:
            try:
                setattr(state, age_key, int(patch[age_key]))
            except (TypeError, ValueError):
                pass
    if patch.get("can_be_claimed_as_dependent") is not None:
        state.can_be_claimed_as_dependent = bool(patch["can_be_claimed_as_dependent"])
    for cf in patch.get("confirmed_fields", []) or []:
        state.confirmed_fields.add(cf)
