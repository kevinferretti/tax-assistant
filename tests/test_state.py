"""Tests for schemas, event-sourced state fold, and conversions to engine types."""
from decimal import Decimal

from app import tax_tables_2025 as T
from app.schemas import W2, Dependent, ReturnState, normalize_filing_status
from app.events import EventLog, W2_EXTRACTED, RETURN_UPDATED
from app.tax_engine import compute_1040


def test_normalize_filing_status():
    assert normalize_filing_status("Married Filing Jointly") == T.MFJ
    assert normalize_filing_status("married_filing_jointly") == T.MFJ
    assert normalize_filing_status("head of household") == T.HOH
    assert normalize_filing_status("single") == T.SINGLE
    assert normalize_filing_status("nonsense") is None


def test_w2_validation_and_warnings():
    w = W2(box1_wages="40,000", box2_fed_withheld="$3,180")
    assert w.box1_wages == Decimal("40000")
    assert w.box2_fed_withheld == Decimal("3180")
    messy = W2(box1_wages="37800", box2_fed_withheld="0")
    assert any("withheld" in m for m in messy.warnings())


def test_dependent_eligibility_rules():
    young = Dependent(relationship="Daughter", age=8, ssn="555-00-1111")
    assert young.qualifies_ctc and young.is_eitc_qualifying_child and not young.qualifies_odc
    teen = Dependent(relationship="Son", age=17, ssn="555-00-2222")
    assert not teen.qualifies_ctc and teen.qualifies_odc  # 17 -> ODC, not CTC
    student = Dependent(relationship="Son", age=20, is_full_time_student=True, ssn="x")
    assert student.is_eitc_qualifying_child  # under 24 + student
    parent = Dependent(relationship="Parent", age=70, ssn="555-00-3333")
    assert parent.qualifies_odc and not parent.qualifies_ctc


def test_event_fold_builds_state():
    log = EventLog()
    log.append(W2_EXTRACTED, "extracted W-2",
               w2={"employee_name": "Jordan A. Avery", "employee_ssn": "555-12-3456",
                   "box1_wages": "40000", "box2_fed_withheld": "3180"})
    log.append(RETURN_UPDATED, "set filing status", patch={"filing_status": "single"})
    state = log.state()
    assert state.filing_status == T.SINGLE
    assert state.has_w2
    assert state.is_ready
    facts = state.to_tax_facts()
    assert facts.wages == Decimal("40000")
    assert facts.withholding_w2 == Decimal("3180")


def test_correction_via_new_event():
    log = EventLog()
    log.append(RETURN_UPDATED, "set status", patch={"filing_status": "single"})
    log.append(RETURN_UPDATED, "correct status", patch={"filing_status": "head of household"})
    assert log.state().filing_status == T.HOH  # latest wins (replayable correction)


def test_state_to_identity_splits_name_and_address():
    log = EventLog()
    log.append(W2_EXTRACTED, "w2", w2={
        "employee_name": "Jordan A. Avery", "employee_ssn": "555-12-3456",
        "employee_address": "142 Maple Street, Apt 3B, Columbus, OH 43215",
        "box1_wages": "40000"})
    ident = log.state().to_identity()
    assert ident.last_name == "Avery"
    assert ident.city == "Columbus" and ident.state == "OH" and ident.zip == "43215"


def test_full_pipeline_state_to_engine():
    log = EventLog()
    log.append(W2_EXTRACTED, "w2", w2={"box1_wages": "40000", "box2_fed_withheld": "3180",
                                       "employee_name": "Jordan Avery", "employee_ssn": "555-12-3456"})
    log.append(RETURN_UPDATED, "status", patch={"filing_status": "single"})
    res = compute_1040(log.state().to_tax_facts())
    assert res.refund == Decimal("505")  # 3,180 - 2,675
