"""Tests for the independent verification pass."""
from decimal import Decimal

from app import tax_tables_2025 as T
from app.tax_engine import DependentFact, TaxFacts, compute_1040
from app.verification import verify


def test_verify_passes_on_clean_returns():
    for fs in (T.SINGLE, T.MFJ, T.MFS, T.HOH, T.QSS):
        res = compute_1040(TaxFacts(filing_status=fs, wages=Decimal("40000"),
                                    withholding_w2=Decimal("3000")))
        vr = verify(res)
        assert vr.passed, f"{fs}: {[(c.name, c.detail) for c in vr.failures]}"


def test_verify_passes_full_credit_stack():
    kids = [DependentFact(qualifies_ctc=True, is_eitc_qualifying_child=True),
            DependentFact(qualifies_ctc=True, is_eitc_qualifying_child=True)]
    res = compute_1040(TaxFacts(filing_status=T.HOH, wages=Decimal("40000"),
                                withholding_w2=Decimal("1500"), dependents=kids))
    vr = verify(res)
    assert vr.passed, [(c.name, c.detail) for c in vr.failures]


def test_verify_catches_tampered_tax():
    res = compute_1040(TaxFacts(filing_status=T.SINGLE, wages=Decimal("40000")))
    # Simulate a corrupted tax line (e.g., a hypothetical LLM-injected number).
    res.lines["16"].amount = Decimal("1")
    vr = verify(res)
    assert not vr.passed
    assert any(c.name.startswith("line16") for c in vr.failures)


def test_verify_catches_broken_refund():
    res = compute_1040(TaxFacts(filing_status=T.SINGLE, wages=Decimal("40000"),
                                withholding_w2=Decimal("3200")))
    res.refund = Decimal("99999")
    vr = verify(res)
    assert not vr.passed
    assert any("refund" in c.name for c in vr.failures)


def test_verify_catches_illegal_eitc():
    res = compute_1040(TaxFacts(filing_status=T.SINGLE, wages=Decimal("40000")))
    res.lines["27"].amount = Decimal("5000")  # no kids -> max childless credit is $649
    vr = verify(res)
    assert not vr.passed
    assert any(c.name == "eitc_within_max" for c in vr.failures)


def test_two_tax_methods_agree_on_clean_return():
    res = compute_1040(TaxFacts(filing_status=T.SINGLE, wages=Decimal("40000")))
    vr = verify(res)
    agree = [c for c in vr.checks if c.name == "line16_tax_two_methods_agree"]
    assert agree and agree[0].passed
