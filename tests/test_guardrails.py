"""Tests for guardrails: PII masking, question budget, scope."""
from app.guardrails import (
    mask_ssn, mask_pii, redact_for_model, budget_remaining, can_ask_question,
    detect_out_of_scope, QUESTION_BUDGET,
)


def test_mask_ssn_formats():
    assert mask_ssn("555-12-3456") == "***-**-3456"
    assert mask_ssn("555123456") == "***-**-3456"
    assert mask_ssn("") == ""


def test_mask_pii_in_free_text():
    masked = mask_pii("My SSN is 555-12-3456 and that's it")
    assert "555-12-3456" not in masked
    assert "3456" in masked


def test_redact_for_model_hides_ssn():
    data = {"employee_ssn": "555-12-3456", "box1_wages": "40000",
            "nested": {"spouse_ssn": "555-99-0000"}}
    red = redact_for_model(data)
    assert red["employee_ssn"] == "***-**-3456"
    assert red["nested"]["spouse_ssn"] == "***-**-0000"
    assert red["box1_wages"] == "40000"


def test_question_budget():
    assert budget_remaining(0) == QUESTION_BUDGET
    assert budget_remaining(5) == 0
    assert budget_remaining(7) == 0
    assert can_ask_question(4) is True
    assert can_ask_question(5) is False


def test_detect_out_of_scope():
    assert detect_out_of_scope("can you do my California state tax return") is not None
    assert detect_out_of_scope("represent me in an audit") is not None
    assert detect_out_of_scope("I have crypto gains") is not None
    assert detect_out_of_scope("I am single with one job") is None
