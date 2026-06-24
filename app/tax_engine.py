"""
Deterministic 2025 Form 1040 tax engine.

This module is the ONLY place tax math happens. The LLM never computes a number;
it only gathers facts which are handed to ``compute_1040``. Every output line
carries machine- and human-readable *provenance* (how it was derived) so the
result is auditable and the independent verification pass (verification.py) can
re-derive and cross-check it.

Money is handled in ``Decimal`` and rounded to whole dollars (IRS convention,
round-half-up) at the points the real form rounds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from . import tax_tables_2025 as T


# --------------------------------------------------------------------------
# Money helpers
# --------------------------------------------------------------------------
def D(x) -> Decimal:
    """Coerce to Decimal safely (via str to avoid binary float artifacts)."""
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x or 0))


def dollars(x) -> Decimal:
    """Round to a whole dollar, round-half-up (the IRS rounding rule)."""
    return D(x).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------
# Inputs (plain dataclasses; the pydantic TaxReturn schema converts into these)
# --------------------------------------------------------------------------
@dataclass
class DependentFact:
    first_name: str = ""
    last_name: str = ""
    ssn: str = ""
    relationship: str = ""
    # Credit eligibility (decided upstream from age/relationship/residency):
    qualifies_ctc: bool = False   # qualifying child under 17 -> Child Tax Credit
    qualifies_odc: bool = False   # other dependent -> Credit for Other Dependents
    is_eitc_qualifying_child: bool = False  # counts toward EITC child count
    lived_with_taxpayer_in_us: bool = True


@dataclass
class TaxFacts:
    filing_status: str = T.SINGLE

    # Income
    wages: Decimal = Decimal("0")              # 1a (sum of W-2 box 1)
    other_earned_income: Decimal = Decimal("0")  # 1h (rare; folded into 1z)
    taxable_interest: Decimal = Decimal("0")   # 2b
    ordinary_dividends: Decimal = Decimal("0") # 3b
    ira_taxable: Decimal = Decimal("0")        # 4b
    pensions_taxable: Decimal = Decimal("0")   # 5b
    social_security_taxable: Decimal = Decimal("0")  # 6b
    capital_gain: Decimal = Decimal("0")       # 7
    schedule_1_income: Decimal = Decimal("0")  # 8 (additional income)
    adjustments: Decimal = Decimal("0")        # 10 (adjustments to income)
    qbi_deduction: Decimal = Decimal("0")      # 13a

    # Payments
    withholding_w2: Decimal = Decimal("0")     # 25a
    withholding_1099: Decimal = Decimal("0")   # 25b
    other_withholding: Decimal = Decimal("0")  # 25c
    estimated_payments: Decimal = Decimal("0") # 26

    # Dependents & EITC context
    dependents: list[DependentFact] = field(default_factory=list)
    investment_income: Decimal = Decimal("0")  # for EITC cliff
    taxpayer_age: Optional[int] = None         # for childless EITC age test / 65+ std ded
    spouse_age: Optional[int] = None
    num_blind: int = 0
    can_be_claimed_as_dependent: bool = False
    eitc_lived_in_us_half_year: bool = True    # childless EITC residency test

    def __post_init__(self):
        # Normalize all money fields to Decimal.
        for f in (
            "wages", "other_earned_income", "taxable_interest", "ordinary_dividends",
            "ira_taxable", "pensions_taxable", "social_security_taxable", "capital_gain",
            "schedule_1_income", "adjustments", "qbi_deduction", "withholding_w2",
            "withholding_1099", "other_withholding", "estimated_payments",
            "investment_income",
        ):
            setattr(self, f, D(getattr(self, f)))

    @property
    def is_joint(self) -> bool:
        return self.filing_status in (T.MFJ, T.QSS)

    @property
    def earned_income(self) -> Decimal:
        """Earned income for EITC / ACTC (wages + other earned)."""
        return self.wages + self.other_earned_income


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------
@dataclass
class LineItem:
    line: str
    label: str
    amount: Decimal
    provenance: str


@dataclass
class Form1040Result:
    facts: TaxFacts
    lines: dict[str, LineItem] = field(default_factory=dict)
    refund: Decimal = Decimal("0")
    amount_owed: Decimal = Decimal("0")

    def amt(self, line: str) -> Decimal:
        item = self.lines.get(line)
        return item.amount if item else Decimal("0")

    def as_dict(self) -> dict:
        return {
            "filing_status": self.facts.filing_status,
            "refund": str(self.refund),
            "amount_owed": str(self.amount_owed),
            "lines": {
                k: {"line": v.line, "label": v.label,
                    "amount": str(v.amount), "provenance": v.provenance}
                for k, v in self.lines.items()
            },
        }


# --------------------------------------------------------------------------
# Core tax computations
# --------------------------------------------------------------------------
def tax_from_brackets(amount: Decimal, filing_status: str) -> Decimal:
    """Marginal-bracket tax on an amount (unrounded)."""
    amount = D(amount)
    if amount <= 0:
        return Decimal("0")
    brackets = T.BRACKETS[filing_status]
    tax = Decimal("0")
    for i, (lower, rate) in enumerate(brackets):
        upper = brackets[i + 1][0] if i + 1 < len(brackets) else None
        if amount <= lower:
            break
        top = amount if upper is None else min(amount, upper)
        tax += (top - lower) * rate
    return tax


def compute_tax(taxable_income: Decimal, filing_status: str) -> tuple[Decimal, str]:
    """
    Form 1040 line 16 tax. Returns (tax, method_description).

    For taxable income below $100,000 the IRS *requires* the Tax Table, which
    taxes the midpoint of the $50 band the income falls in. At/above $100,000
    the Tax Computation Worksheet uses the bracket formula directly.
    """
    ti = dollars(taxable_income)
    if ti <= 0:
        return Decimal("0"), "Taxable income is $0, so tax is $0."
    if ti < T.TAX_TABLE_CEILING:
        band_floor = (ti // T.TAX_TABLE_BAND) * T.TAX_TABLE_BAND
        midpoint = band_floor + T.TAX_TABLE_BAND / 2
        tax = dollars(tax_from_brackets(midpoint, filing_status))
        return tax, (
            f"Tax Table: taxable income ${ti:,} falls in the "
            f"${band_floor:,}-${band_floor + T.TAX_TABLE_BAND:,} band; "
            f"tax computed on the ${midpoint:,} midpoint = ${tax:,}."
        )
    tax = dollars(tax_from_brackets(ti, filing_status))
    return tax, (
        f"Tax Computation Worksheet (income >= $100,000): bracket formula on "
        f"${ti:,} = ${tax:,}."
    )


def standard_deduction(facts: TaxFacts) -> tuple[Decimal, str]:
    """Form 1040 line 12e standard deduction, including 65+/blind additions."""
    base = T.STANDARD_DEDUCTION[facts.filing_status]
    prov = f"Base standard deduction for {T.FILING_STATUS_LABELS[facts.filing_status]} = ${base:,}."

    # Count extra boxes for age 65+ and blindness.
    extra_boxes = facts.num_blind
    if facts.taxpayer_age is not None and facts.taxpayer_age >= 65:
        extra_boxes += 1
    if facts.is_joint and facts.spouse_age is not None and facts.spouse_age >= 65:
        extra_boxes += 1
    addition = Decimal("0")
    if extra_boxes:
        per = T.ADDITIONAL_STD_DEDUCTION[facts.filing_status]
        addition = per * extra_boxes
        prov += f" Plus {extra_boxes} x ${per:,} age/blindness addition = +${addition:,}."

    total = base + addition

    # Dependent-of-another limitation (rare for this profile, but correct to apply).
    if facts.can_be_claimed_as_dependent:
        limit = max(Decimal("1350"), facts.earned_income + Decimal("450"))
        limit = min(limit, base) + addition
        if limit < total:
            total = limit
            prov += (" Limited because taxpayer can be claimed as a dependent "
                     f"(greater of $1,350 or earned income + $450), capped at base.")
    return dollars(total), prov


def compute_ctc_odc(facts: TaxFacts, agi: Decimal, tax_before: Decimal):
    """
    Child Tax Credit + Credit for Other Dependents (nonrefundable, line 19) and
    the refundable Additional CTC (line 28). Returns (ctc_odc_line19, actc_line28,
    prov19, prov28).
    """
    n_ctc = sum(1 for d in facts.dependents if d.qualifies_ctc)
    n_odc = sum(1 for d in facts.dependents if d.qualifies_odc)
    if n_ctc == 0 and n_odc == 0:
        return Decimal("0"), Decimal("0"), "No qualifying dependents.", "No qualifying children for ACTC."

    ctc_potential = T.CTC_PER_CHILD * n_ctc
    odc_potential = T.ODC_PER_DEPENDENT * n_odc
    total_potential = ctc_potential + odc_potential

    # Phaseout: $50 per $1,000 (or fraction) of MAGI over the threshold.
    threshold = T.CTC_PHASEOUT_THRESHOLD[facts.filing_status]
    phaseout = Decimal("0")
    if agi > threshold:
        over = agi - threshold
        steps = (over / T.CTC_PHASEOUT_PER).to_integral_value(rounding=ROUND_HALF_UP)
        # round UP to next $1,000 increment
        steps = ((over + T.CTC_PHASEOUT_PER - 1) // T.CTC_PHASEOUT_PER)
        phaseout = steps * T.CTC_PHASEOUT_RATE
    allowed = max(Decimal("0"), total_potential - phaseout)

    # Nonrefundable portion limited to tax (line 18).
    line19 = min(allowed, dollars(tax_before))
    prov19 = (
        f"{n_ctc} child(ren) x ${T.CTC_PER_CHILD:,} (CTC) + {n_odc} other "
        f"dependent(s) x ${T.ODC_PER_DEPENDENT:,} (ODC) = ${total_potential:,}"
        + (f", less ${phaseout:,} phaseout" if phaseout else "")
        + f"; limited to tax of ${dollars(tax_before):,} -> ${line19:,}."
    )

    # Additional CTC (refundable), only for the CTC (child) portion.
    actc = Decimal("0")
    prov28 = "No refundable Additional Child Tax Credit."
    if n_ctc > 0:
        allowed_ctc = max(Decimal("0"), ctc_potential - phaseout)  # phaseout hits CTC first
        unused_ctc = max(Decimal("0"), allowed_ctc - max(Decimal("0"), line19))
        refundable_cap = T.CTC_REFUNDABLE_PER_CHILD * n_ctc
        earned_based = T.ACTC_RATE * max(Decimal("0"), facts.earned_income - T.ACTC_EARNED_INCOME_FLOOR)
        actc = min(unused_ctc, refundable_cap, dollars(earned_based))
        actc = max(Decimal("0"), dollars(actc))
        prov28 = (
            f"Additional CTC = min(unused CTC ${dollars(unused_ctc):,}, "
            f"${T.CTC_REFUNDABLE_PER_CHILD:,} x {n_ctc} = ${refundable_cap:,}, "
            f"15% x (earned ${facts.earned_income:,} - $2,500) = ${dollars(earned_based):,}) "
            f"= ${actc:,}."
        )
    return dollars(line19), actc, prov19, prov28


def _eitc_band_value(income: Decimal) -> Decimal:
    """EIC table uses the midpoint of the $50 band (like the tax table)."""
    income = dollars(income)
    band_floor = (income // T.TAX_TABLE_BAND) * T.TAX_TABLE_BAND
    return band_floor + T.TAX_TABLE_BAND / 2


def _eitc_for_income(income: Decimal, n_kids: int, joint: bool) -> Decimal:
    p = T.EITC_PARAMS[min(n_kids, 3)]
    thresh = T.EITC_PHASEOUT_THRESHOLD["joint" if joint else "single"][min(n_kids, 3)]
    val = _eitc_band_value(income)
    phase_in = p["credit_rate"] * val
    credit = min(phase_in, p["max_credit"])
    if val > thresh:
        credit = p["max_credit"] - p["phaseout_rate"] * (val - thresh)
    return max(Decimal("0"), credit)


def compute_eitc(facts: TaxFacts, agi: Decimal) -> tuple[Decimal, str]:
    """Earned Income Tax Credit (line 27)."""
    n_kids = sum(1 for d in facts.dependents if d.is_eitc_qualifying_child)

    # Eligibility gates.
    if facts.filing_status == T.MFS:
        return Decimal("0"), "EITC not computed for Married Filing Separately in this profile."
    if facts.investment_income > T.EITC_INVESTMENT_INCOME_LIMIT:
        return Decimal("0"), (
            f"Investment income ${facts.investment_income:,} exceeds the "
            f"${T.EITC_INVESTMENT_INCOME_LIMIT:,} limit -> EITC is $0 (hard cliff)."
        )
    if facts.earned_income <= 0:
        return Decimal("0"), "No earned income -> no EITC."
    if n_kids == 0:
        age = facts.taxpayer_age
        if age is not None and not (T.EITC_CHILDLESS_MIN_AGE <= age <= T.EITC_CHILDLESS_MAX_AGE):
            return Decimal("0"), (
                f"Childless EITC requires age {T.EITC_CHILDLESS_MIN_AGE}-"
                f"{T.EITC_CHILDLESS_MAX_AGE}; taxpayer age {age} -> $0."
            )
        if not facts.eitc_lived_in_us_half_year:
            return Decimal("0"), "Childless EITC requires US residence over half the year -> $0."

    # Credit is the smaller of the value computed on earned income vs AGI
    # (only matters once AGI is in the phaseout range).
    by_earned = _eitc_for_income(facts.earned_income, n_kids, facts.is_joint)
    thresh = T.EITC_PHASEOUT_THRESHOLD["joint" if facts.is_joint else "single"][min(n_kids, 3)]
    if agi > thresh:
        by_agi = _eitc_for_income(agi, n_kids, facts.is_joint)
        credit = min(by_earned, by_agi)
        basis = (f"lesser of credit on earned income (${dollars(by_earned):,}) and "
                 f"on AGI (${dollars(by_agi):,})")
    else:
        credit = by_earned
        basis = f"credit on earned income ${facts.earned_income:,}"
    credit = dollars(credit)
    return credit, (
        f"EITC with {n_kids} qualifying child(ren), "
        f"{'joint' if facts.is_joint else 'non-joint'} thresholds: {basis} = ${credit:,}."
    )


# --------------------------------------------------------------------------
# Top-level: build the whole 1040
# --------------------------------------------------------------------------
def compute_1040(facts: TaxFacts) -> Form1040Result:
    if facts.filing_status not in T.FILING_STATUSES:
        raise ValueError(f"Unknown filing status: {facts.filing_status!r}")

    res = Form1040Result(facts=facts)

    def put(line, label, amount, prov):
        res.lines[line] = LineItem(line, label, dollars(amount), prov)

    # --- Income ---
    put("1a", "Wages (W-2 box 1)", facts.wages, "Sum of W-2 box 1 wages.")
    put("1z", "Total wages",
        facts.wages + facts.other_earned_income,
        "Lines 1a-1h: wages plus other earned income.")
    put("2b", "Taxable interest", facts.taxable_interest, "Taxable interest.")
    put("3b", "Ordinary dividends", facts.ordinary_dividends, "Ordinary dividends.")
    put("4b", "IRA distributions (taxable)", facts.ira_taxable, "Taxable IRA distributions.")
    put("5b", "Pensions/annuities (taxable)", facts.pensions_taxable, "Taxable pensions.")
    put("6b", "Social security (taxable)", facts.social_security_taxable, "Taxable Social Security.")
    put("7", "Capital gain/(loss)", facts.capital_gain, "Capital gain or loss.")
    put("8", "Additional income (Sch 1)", facts.schedule_1_income, "Schedule 1 additional income.")

    total_income = (res.amt("1z") + res.amt("2b") + res.amt("3b") + res.amt("4b")
                    + res.amt("5b") + res.amt("6b") + res.amt("7") + res.amt("8"))
    put("9", "Total income", total_income, "Sum of lines 1z, 2b, 3b, 4b, 5b, 6b, 7, 8.")

    put("10", "Adjustments to income (Sch 1)", facts.adjustments, "Schedule 1 adjustments.")
    agi = res.amt("9") - res.amt("10")
    put("11a", "Adjusted gross income", agi, "Line 9 minus line 10.")
    put("11b", "AGI (carried to page 2)", agi, "Amount from line 11a.")

    # --- Deductions & taxable income ---
    std, std_prov = standard_deduction(facts)
    put("12e", "Standard deduction", std, std_prov)
    put("13a", "Qualified business income deduction", facts.qbi_deduction, "QBI deduction.")
    put("13b", "Additional deductions (Sch 1-A)", Decimal("0"), "Schedule 1-A deductions (none).")
    deductions_total = res.amt("12e") + res.amt("13a") + res.amt("13b")
    put("14", "Total deductions", deductions_total, "Lines 12e + 13a + 13b.")
    taxable_income = max(Decimal("0"), res.amt("11b") - res.amt("14"))
    put("15", "Taxable income", taxable_income,
        "Line 11b minus line 14 (not below $0).")

    # --- Tax & nonrefundable credits ---
    tax, tax_prov = compute_tax(taxable_income, facts.filing_status)
    put("16", "Tax", tax, tax_prov)
    put("17", "Amount from Schedule 2, line 3", Decimal("0"), "No Schedule 2 additional tax.")
    line18 = res.amt("16") + res.amt("17")
    put("18", "Add lines 16 and 17", line18, "Line 16 + line 17.")

    line19, actc, prov19, prov28 = compute_ctc_odc(facts, agi, line18)
    put("19", "Child tax credit / ODC", line19, prov19)
    put("20", "Amount from Schedule 3, line 8", Decimal("0"), "No Schedule 3 nonrefundable credits.")
    line21 = res.amt("19") + res.amt("20")
    put("21", "Add lines 19 and 20", line21, "Line 19 + line 20.")
    line22 = max(Decimal("0"), res.amt("18") - res.amt("21"))
    put("22", "Subtract line 21 from line 18", line22, "Line 18 minus line 21 (not below $0).")
    put("23", "Other taxes (Sch 2, line 21)", Decimal("0"), "No other taxes.")
    total_tax = res.amt("22") + res.amt("23")
    put("24", "Total tax", total_tax, "Line 22 + line 23.")

    # --- Payments & refundable credits ---
    put("25a", "W-2 withholding", facts.withholding_w2, "Federal income tax withheld (W-2 box 2).")
    put("25b", "1099 withholding", facts.withholding_1099, "Withholding from 1099s.")
    put("25c", "Other withholding", facts.other_withholding, "Other federal withholding.")
    wh_total = res.amt("25a") + res.amt("25b") + res.amt("25c")
    put("25d", "Total withholding", wh_total, "Lines 25a + 25b + 25c.")
    put("26", "Estimated tax payments", facts.estimated_payments, "2025 estimated payments.")

    eitc, eitc_prov = compute_eitc(facts, agi)
    put("27", "Earned income credit (EIC)", eitc, eitc_prov)
    put("28", "Additional child tax credit", actc, prov28)
    put("29", "American opportunity credit", Decimal("0"), "No education credit.")
    put("31", "Schedule 3, line 13", Decimal("0"), "No other refundable credits.")
    line32 = res.amt("27") + res.amt("28") + res.amt("29") + res.amt("31")
    put("32", "Total other payments/refundable credits", line32,
        "Lines 27 + 28 + 29 + 31.")
    total_payments = res.amt("25d") + res.amt("26") + res.amt("32")
    put("33", "Total payments", total_payments, "Lines 25d + 26 + 32.")

    # --- Refund or amount owed ---
    if res.amt("33") >= res.amt("24"):
        overpaid = res.amt("33") - res.amt("24")
        put("34", "Overpayment", overpaid, "Line 33 minus line 24.")
        put("35a", "Refund", overpaid, "Amount refunded to you.")
        put("37", "Amount you owe", Decimal("0"), "No balance due.")
        res.refund = overpaid
        res.amount_owed = Decimal("0")
    else:
        owed = res.amt("24") - res.amt("33")
        put("34", "Overpayment", Decimal("0"), "No overpayment.")
        put("35a", "Refund", Decimal("0"), "No refund.")
        put("37", "Amount you owe", owed, "Line 24 minus line 33.")
        res.refund = Decimal("0")
        res.amount_owed = owed

    return res
