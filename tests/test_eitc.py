"""EITC-specific tests across child counts, joint thresholds, and eligibility gates."""
from decimal import Decimal

from app import tax_tables_2025 as T
from app.tax_engine import DependentFact, TaxFacts, compute_eitc, compute_1040


def kid(eitc=True, ctc=False):
    return DependentFact(first_name="K", last_name="T", relationship="Son",
                         is_eitc_qualifying_child=eitc, qualifies_ctc=ctc)


def test_eitc_childless_phaseout():
    # Single, no kids, earned 15,000. threshold 10,620, rate 7.65%.
    # band midpoint 15,025 -> 649 - 0.0765*(15,025-10,620)=649-336.98=312.02 -> 312
    facts = TaxFacts(filing_status=T.SINGLE, wages=Decimal("15000"), taxpayer_age=30)
    credit, prov = compute_eitc(facts, Decimal("15000"))
    assert credit == Decimal("312")


def test_eitc_one_child_at_40k():
    # Single, 1 kid, earned 40,000. threshold 23,350, rate 15.98%, max 4,328.
    # midpoint 40,025 -> 4,328 - 0.1598*(40,025-23,350)=4,328-2,664.71=1,663.29 -> 1,663
    facts = TaxFacts(filing_status=T.SINGLE, wages=Decimal("40000"), dependents=[kid()])
    credit, _ = compute_eitc(facts, Decimal("40000"))
    assert credit == Decimal("1663")


def test_eitc_two_kids_at_40k_hoh():
    facts = TaxFacts(filing_status=T.HOH, wages=Decimal("40000"),
                     dependents=[kid(), kid()])
    credit, _ = compute_eitc(facts, Decimal("40000"))
    assert credit == Decimal("3640")


def test_eitc_joint_threshold_is_higher():
    # MFJ gets a higher phaseout start, so more credit at the same income.
    single = TaxFacts(filing_status=T.SINGLE, wages=Decimal("40000"), dependents=[kid(), kid()])
    joint = TaxFacts(filing_status=T.MFJ, wages=Decimal("40000"), dependents=[kid(), kid()])
    c_single, _ = compute_eitc(single, Decimal("40000"))
    c_joint, _ = compute_eitc(joint, Decimal("40000"))
    assert c_joint > c_single


def test_eitc_investment_income_cliff():
    facts = TaxFacts(filing_status=T.SINGLE, wages=Decimal("20000"),
                     dependents=[kid()], investment_income=Decimal("12000"))
    credit, prov = compute_eitc(facts, Decimal("32000"))
    assert credit == Decimal("0")
    assert "cliff" in prov.lower() or "limit" in prov.lower()


def test_eitc_childless_age_gate():
    facts = TaxFacts(filing_status=T.SINGLE, wages=Decimal("12000"), taxpayer_age=20)
    credit, _ = compute_eitc(facts, Decimal("12000"))
    assert credit == Decimal("0")


def test_eitc_mfs_excluded():
    facts = TaxFacts(filing_status=T.MFS, wages=Decimal("20000"), dependents=[kid()])
    credit, _ = compute_eitc(facts, Decimal("20000"))
    assert credit == Decimal("0")


def test_eitc_zero_at_high_income():
    # 1 kid single phases out fully by ~50,434.
    facts = TaxFacts(filing_status=T.SINGLE, wages=Decimal("60000"), dependents=[kid()])
    credit, _ = compute_eitc(facts, Decimal("60000"))
    assert credit == Decimal("0")
