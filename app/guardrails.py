"""
Guardrails — enforced in code, not just asked of the model.

1. Question budget: a hard cap of 5 user-facing questions, counted from the
   event log. The agent loop must check ``budget_remaining`` before asking.
2. PII boundary: the conversational model never receives a full SSN, and the
   observation trail / logs mask it. Only deterministic code writes the real SSN
   into the PDF.
3. Scope: the system stays on 2025 federal Form 1040 for a W-2 earner and never
   pretends to give individualized tax/legal advice.
"""
from __future__ import annotations

import re

QUESTION_BUDGET = 5

DISCLAIMER = (
    "This is an educational tool, not tax advice, and not for actual filing."
)

# ---- Question budget --------------------------------------------------------
def budget_remaining(questions_asked: int) -> int:
    return max(0, QUESTION_BUDGET - questions_asked)


def can_ask_question(questions_asked: int) -> bool:
    return questions_asked < QUESTION_BUDGET


# ---- PII masking ------------------------------------------------------------
_SSN_RE = re.compile(r"\b(\d{3})[-\s]?(\d{2})[-\s]?(\d{4})\b")


def mask_ssn(value: str) -> str:
    """'555-12-3456' or '555123456' -> '***-**-3456'."""
    if not value:
        return ""
    digits = re.sub(r"\D", "", value)
    if len(digits) == 9:
        return f"***-**-{digits[-4:]}"
    return value


def mask_pii(text: str) -> str:
    """Redact any SSN-shaped substrings in free text (model context, logs, trace)."""
    if not text:
        return text
    return _SSN_RE.sub(lambda m: f"***-**-{m.group(3)}", text)


# Keys in extracted W-2 data that are sensitive identifiers.
_SENSITIVE_KEYS = {"employee_ssn", "ssn", "spouse_ssn"}


def redact_for_model(data):
    """Recursively mask SSNs so nothing with a full SSN is sent to the LLM or trace."""
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if k in _SENSITIVE_KEYS and isinstance(v, str):
                out[k] = mask_ssn(v)
            else:
                out[k] = redact_for_model(v)
        return out
    if isinstance(data, list):
        return [redact_for_model(v) for v in data]
    if isinstance(data, str):
        return mask_pii(data)
    return data


# ---- Scope ------------------------------------------------------------------
_OUT_OF_SCOPE_PATTERNS = [
    (r"\b(state|california|new york|ny|ca)\s+(tax|return)\b", "state tax returns"),
    (r"\b(20(1\d|2[0-4]))\b.*\b(return|taxes|file)", "a prior-year return"),
    (r"\b(crypto|bitcoin|nft|stock options|k-1|schedule c|self-?employ)", "complex income types"),
    (r"\b(represent me|audit defense|legal advice|sue|lawsuit)\b", "legal representation"),
    (r"\b(amend|amended return|1040-?x)\b", "amended returns"),
]


def detect_out_of_scope(text: str):
    """Return a short reason string if the user asks for something out of scope."""
    if not text:
        return None
    low = text.lower()
    for pattern, reason in _OUT_OF_SCOPE_PATTERNS:
        if re.search(pattern, low):
            return reason
    return None
