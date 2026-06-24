"""
Observation — make the agent's behavior legible.

The event log already records everything; this module exposes it safely (PII
masked) as (a) a structured trace for the UI's Agent Activity panel and the
``/api/session/{id}/trace`` endpoint, and (b) structured server logs. Same
source of truth as state, so what the judge sees is exactly what happened.
"""
from __future__ import annotations

import logging
import sys

from .events import Event, EventLog
from .guardrails import mask_pii, redact_for_model

logger = logging.getLogger("tax_assistant")
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)

# A small icon hint per category for the activity panel (UI may override).
ICON = {
    "system": "•",
    "conversation": "💬",
    "tool": "🛠",
    "compute": "🧮",
    "guardrail": "🛡",
    "error": "⚠",
}


def public_event(ev: Event) -> dict:
    """A single event, with all PII masked, ready to ship to the browser."""
    return {
        "seq": ev.seq,
        "ts": ev.ts,
        "kind": ev.kind,
        "category": ev.category,
        "icon": ICON.get(ev.category, "•"),
        "summary": mask_pii(ev.summary),
        "data": redact_for_model(ev.data),
    }


def public_trace(log: EventLog) -> list[dict]:
    return [public_event(e) for e in log.events]


def log_event(session_id: str, ev: Event) -> None:
    """Structured server log line, PII-safe."""
    logger.info("session=%s seq=%s kind=%s | %s",
                session_id[:8], ev.seq, ev.kind, mask_pii(ev.summary))
