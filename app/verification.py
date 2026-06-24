"""
Independent verification pass — the "second accountant".

This module deliberately does NOT trust tax_engine. It re-derives the tax a
second way (its own bracket walk + the IRS subtraction-amount worksheet identity)
and asserts a battery of arithmetic and statutory-bounds invariants on the
engine's output. The PDF generator refuses to emit a return unless verification
passes, so a downloaded 1040 is guaranteed to be internally consistent and within
legal credit limits — not just whatever the model said.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from . import tax_tables_2025 as T
from .tax_engine import Form1040Result, dollars, D


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class VerificationResult:
    passed: bool
    checks: list[Check] = field(default_factory=list)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.checks],
        }


# Independent bracket walk (separate code from engine.tax_from_brackets on purpose).
def _independent_bracket_tax(amount: Decimal, status: str) -> Decimal:
    amount = D(amount)
    if amount <= 0:
        return Decimal("0")
    rows = T.BRACKETS[status]
    total = Decimal("0")
    prev_threshold = Decimal("0")
    prev_rate = None
    # Walk thresholds; tax the slice in each band.
    for i in range(len(rows)):
        lower, rate = rows[i]
        upper = rows[i + 1][0] if i + 1 < len(rows) else None
        if amount <= lower:
            break
        slice_top = amount if upper is None else min(amount, upper)
        total += (slice_top - lower) * rate
    return total


# Independent re-derivation via the IRS "subtraction amount" identity:
#   tax = amount * top_marginal_rate - K,  where K is precomputed per bracket.
# K is derived here from the bracket table independently of the engine.
def _subtraction_amounts(status: str) -> list[tuple[Decimal, Decimal, Decimal]]:
    """Return [(lower, rate, K)] so that tax(x in band) = x*rate - K."""
    rows = T.BRACKETS[status]
    out = []
    # cumulative tax up to each threshold
    cum = Decimal("0")
    for i, (lower, rate) in enumerate(rows):
        if i > 0:
            prev_lower, prev_rate = rows[i - 1]
            cum += (lower - prev_lower) * prev_rate
        # For x in this band: tax = cum + (x - lower)*rate = x*rate - (lower*rate - cum)
        K = lower * rate - cum
        out.append((lower, rate, K))
    return out


def _tax_via_subtraction(amount: Decimal, status: str) -> Decimal:
    amount = D(amount)
    if amount <= 0:
        return Decimal("0")
    band = None
    for lower, rate, K in _subtraction_amounts(status):
        if amount > lower:
            band = (rate, K)
    rate, K = band
    return amount * rate - K


def verify(result: Form1040Result) -> VerificationResult:
    checks: list[Check] = []
    f = result.facts

    def check(name, passed, detail):
        checks.append(Check(name, bool(passed), detail))

    def amt(line):
        return result.amt(line)

    # ---- Arithmetic invariants (re-add the engine's own lines) ----
    income_sum = (amt("1z") + amt("2b") + amt("3b") + amt("4b") + amt("5b")
                  + amt("6b") + amt("7") + amt("8"))
    check("line9_total_income", amt("9") == income_sum,
          f"line 9 ${amt('9'):,} vs recomputed ${income_sum:,}")

    check("line11a_agi", amt("11a") == amt("9") - amt("10"),
          f"AGI ${amt('11a'):,} vs line9-line10 ${amt('9') - amt('10'):,}")
    check("line11b_carry", amt("11b") == amt("11a"), "11b should equal 11a")

    deductions = amt("12e") + amt("13a") + amt("13b")
    check("line14_deductions", amt("14") == deductions,
          f"line 14 ${amt('14'):,} vs ${deductions:,}")

    expected_ti = max(Decimal("0"), amt("11b") - amt("14"))
    check("line15_taxable_income", amt("15") == expected_ti,
          f"taxable income ${amt('15'):,} vs max(0, 11b-14) ${expected_ti:,}")
    check("taxable_income_nonneg", amt("15") >= 0, "taxable income must be >= 0")

    # ---- Independent tax re-derivation (two methods must agree with line 16) ----
    ti = amt("15")
    if ti <= 0:
        expected_tax = Decimal("0")
    elif ti < T.TAX_TABLE_CEILING:
        band_floor = (ti // T.TAX_TABLE_BAND) * T.TAX_TABLE_BAND
        midpoint = band_floor + T.TAX_TABLE_BAND / 2
        expected_tax = dollars(_independent_bracket_tax(midpoint, f.filing_status))
        cross = dollars(_tax_via_subtraction(midpoint, f.filing_status))
    else:
        expected_tax = dollars(_independent_bracket_tax(ti, f.filing_status))
        cross = dollars(_tax_via_subtraction(ti, f.filing_status))
    check("line16_tax_independent", amt("16") == expected_tax,
          f"tax ${amt('16'):,} vs independent bracket walk ${expected_tax:,}")
    if ti > 0:
        check("line16_tax_two_methods_agree", abs(cross - expected_tax) <= Decimal("1"),
              f"subtraction-method ${cross:,} vs bracket-walk ${expected_tax:,}")

    check("line18", amt("18") == amt("16") + amt("17"), "line 18 = 16 + 17")
    check("line21", amt("21") == amt("19") + amt("20"), "line 21 = 19 + 20")
    check("line22", amt("22") == max(Decimal("0"), amt("18") - amt("21")),
          "line 22 = max(0, 18 - 21)")
    check("line24_total_tax", amt("24") == amt("22") + amt("23"), "line 24 = 22 + 23")

    check("line25d_withholding", amt("25d") == amt("25a") + amt("25b") + amt("25c"),
          "line 25d = 25a + 25b + 25c")
    line32 = amt("27") + amt("28") + amt("29") + amt("31")
    check("line32_refundable", amt("32") == line32, "line 32 = 27 + 28 + 29 + 31")
    check("line33_total_payments", amt("33") == amt("25d") + amt("26") + amt("32"),
          "line 33 = 25d + 26 + 32")

    # ---- Refund / owed reconciliation ----
    if amt("33") >= amt("24"):
        check("refund_reconciles", result.refund == amt("33") - amt("24") and result.amount_owed == 0,
              f"refund ${result.refund:,} should be ${amt('33') - amt('24'):,}")
    else:
        check("owed_reconciles", result.amount_owed == amt("24") - amt("33") and result.refund == 0,
              f"owed ${result.amount_owed:,} should be ${amt('24') - amt('33'):,}")
    check("not_both_refund_and_owed", not (result.refund > 0 and result.amount_owed > 0),
          "cannot have both a refund and a balance due")

    # ---- Statutory bounds on credits ----
    check("ctc_le_tax", amt("19") <= amt("18"),
          f"nonrefundable CTC/ODC ${amt('19'):,} must be <= line 18 ${amt('18'):,}")

    n_kids_eitc = sum(1 for d in f.dependents if d.is_eitc_qualifying_child)
    eitc_max = T.EITC_PARAMS[min(n_kids_eitc, 3)]["max_credit"]
    check("eitc_within_max", 0 <= amt("27") <= eitc_max,
          f"EITC ${amt('27'):,} must be within [0, ${eitc_max:,}] for {n_kids_eitc} kids")

    n_ctc = sum(1 for d in f.dependents if d.qualifies_ctc)
    actc_cap = T.CTC_REFUNDABLE_PER_CHILD * n_ctc
    check("actc_within_cap", 0 <= amt("28") <= actc_cap,
          f"Additional CTC ${amt('28'):,} must be within [0, ${actc_cap:,}]")

    # ---- Sanity: every line rounded to whole dollars ----
    non_whole = [k for k, v in result.lines.items() if v.amount != dollars(v.amount)]
    check("all_lines_whole_dollars", not non_whole,
          f"non-whole-dollar lines: {non_whole}" if non_whole else "all lines whole dollars")

    passed = all(c.passed for c in checks)
    return VerificationResult(passed=passed, checks=checks)
