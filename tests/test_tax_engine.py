"""
Core tax-engine tests with hand-computed 2025 expected values.

Each expectation is derived by hand in the comments so a reviewer can confirm the
engine matches the real form, not just itself.
"""
from decimal import Decimal

import pytest

from app import tax_tables_2025 as T
from app.tax_engine import (
    DependentFact, TaxFacts, compute_1040, compute_tax, tax_from_brackets, dollars,
)


def amt(res, line):
    return res.amt(line)


# --------------------------------------------------------------------------
# Tax-method selection and bracket math
# --------------------------------------------------------------------------
def test_tax_table_uses_band_midpoint_single():
    # TI 24,250 -> band [24,250, 24,300) midpoint 24,275.
    # 10% * 11,925 = 1,192.50 ; 12% * (24,275-11,925)=12,350 -> 1,482.00 ; total 2,674.50 -> 2,675
    tax, method = compute_tax(Decimal("24250"), T.SINGLE)
    assert tax == Decimal("2675")
    assert "Tax Table" in method


def test_tax_worksheet_above_100k_single():
    # TI 120,000 (>= 100k) uses the worksheet (direct bracket formula).
    # 1,192.50 + 4,386 + 12,072.50 + 24%*(120,000-103,350)=3,996 = 21,647
    tax, method = compute_tax(Decimal("120000"), T.SINGLE)
    assert tax == Decimal("21647")
    assert "Worksheet" in method


def test_zero_taxable_income_zero_tax():
    tax, _ = compute_tax(Decimal("0"), T.SINGLE)
    assert tax == Decimal("0")


def test_mfs_brackets_are_half_of_mfj():
    for (lo_mfs, r_mfs), (lo_mfj, r_mfj) in zip(T.BRACKETS[T.MFS], T.BRACKETS[T.MFJ]):
        assert r_mfs == r_mfj
        # thresholds align with MFJ/2 for the lower brackets
    assert T.BRACKETS[T.MFS][1][0] == T.BRACKETS[T.MFJ][1][0] / 2


# --------------------------------------------------------------------------
# Canonical profile: single ~$40k W-2 earner
# --------------------------------------------------------------------------
def test_single_40k_refund():
    facts = TaxFacts(
        filing_status=T.SINGLE,
        wages=Decimal("40000"),
        withholding_w2=Decimal("3200"),
    )
    res = compute_1040(facts)
    assert amt(res, "9") == Decimal("40000")      # total income
    assert amt(res, "11a") == Decimal("40000")    # AGI
    assert amt(res, "12e") == Decimal("15750")    # standard deduction
    assert amt(res, "15") == Decimal("24250")     # taxable income
    assert amt(res, "16") == Decimal("2675")      # tax
    assert amt(res, "24") == Decimal("2675")      # total tax
    assert amt(res, "25d") == Decimal("3200")     # withholding
    assert amt(res, "33") == Decimal("3200")      # total payments
    assert res.refund == Decimal("525")           # 3,200 - 2,675
    assert res.amount_owed == Decimal("0")


def test_single_40k_balance_due():
    facts = TaxFacts(
        filing_status=T.SINGLE,
        wages=Decimal("40000"),
        withholding_w2=Decimal("2000"),
    )
    res = compute_1040(facts)
    assert amt(res, "24") == Decimal("2675")
    assert res.amount_owed == Decimal("675")      # 2,675 - 2,000
    assert res.refund == Decimal("0")


def test_mfj_single_earner_40k():
    # MFJ std deduction 31,500 -> TI 8,500 -> band midpoint 8,525, all 10% = 852.50 -> 853
    facts = TaxFacts(
        filing_status=T.MFJ,
        wages=Decimal("40000"),
        withholding_w2=Decimal("2000"),
    )
    res = compute_1040(facts)
    assert amt(res, "12e") == Decimal("31500")
    assert amt(res, "15") == Decimal("8500")
    assert amt(res, "16") == Decimal("853")
    assert res.refund == Decimal("1147")          # 2,000 - 853


# --------------------------------------------------------------------------
# Head of household with two children: CTC + ACTC + EITC stack
# --------------------------------------------------------------------------
def test_hoh_two_kids_full_stack():
    kids = [
        DependentFact(first_name="A", last_name="B", relationship="Daughter",
                      qualifies_ctc=True, is_eitc_qualifying_child=True),
        DependentFact(first_name="C", last_name="B", relationship="Son",
                      qualifies_ctc=True, is_eitc_qualifying_child=True),
    ]
    facts = TaxFacts(
        filing_status=T.HOH,
        wages=Decimal("40000"),
        withholding_w2=Decimal("1500"),
        dependents=kids,
    )
    res = compute_1040(facts)
    # HoH std 23,625 -> TI 16,375 -> all 10% on midpoint 16,375 -> 1,637.50 -> 1,638
    assert amt(res, "12e") == Decimal("23625")
    assert amt(res, "15") == Decimal("16375")
    assert amt(res, "16") == Decimal("1638")
    # CTC limited to tax -> line 19 = 1,638, total tax 0
    assert amt(res, "19") == Decimal("1638")
    assert amt(res, "24") == Decimal("0")
    # ACTC: unused CTC 4,400-1,638=2,762; cap 3,400; earned 5,625 -> 2,762
    assert amt(res, "28") == Decimal("2762")
    # EITC 2 kids @ 40k: 7,152 - 0.2106*(40,025-23,350) = 3,640
    assert amt(res, "27") == Decimal("3640")
    # total payments 1,500 + 3,640 + 2,762 = 7,902
    assert amt(res, "33") == Decimal("7902")
    assert res.refund == Decimal("7902")


def test_provenance_present_on_every_line():
    res = compute_1040(TaxFacts(filing_status=T.SINGLE, wages=Decimal("40000")))
    for line, item in res.lines.items():
        assert item.provenance, f"line {line} missing provenance"


def test_invalid_filing_status_rejected():
    with pytest.raises(ValueError):
        compute_1040(TaxFacts(filing_status="bogus", wages=Decimal("40000")))


def test_dollar_rounding_half_up():
    assert dollars(Decimal("2674.50")) == Decimal("2675")
    assert dollars(Decimal("100.49")) == Decimal("100")
