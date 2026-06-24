"""
Tax year 2025 federal parameters — the single source of truth for every number
the engine uses. All values reflect the One Big Beautiful Bill Act (OBBBA, July
2025) inflation/structural updates and IRS Rev. Proc. 2024-40.

Keeping these as plain data (not buried in code) is deliberate: a reviewer can
diff this file against the IRS publications and verify accuracy line by line.

Sources:
  - Standard deduction & brackets: IRS Rev. Proc. 2024-40 / Tax Foundation 2025.
  - Child Tax Credit $2,200/child, refundable portion $1,700: OBBBA (2025).
  - EITC parameters: IRS Rev. Proc. 2024-40 (2025 amounts).
"""
from __future__ import annotations

from decimal import Decimal

TAX_YEAR = 2025

# Canonical filing-status keys used throughout the codebase.
SINGLE = "single"
MFJ = "mfj"  # married filing jointly
MFS = "mfs"  # married filing separately
HOH = "hoh"  # head of household
QSS = "qss"  # qualifying surviving spouse

FILING_STATUSES = (SINGLE, MFJ, MFS, HOH, QSS)

FILING_STATUS_LABELS = {
    SINGLE: "Single",
    MFJ: "Married filing jointly",
    MFS: "Married filing separately",
    HOH: "Head of household",
    QSS: "Qualifying surviving spouse",
}

# --- Standard deduction (Form 1040 line 12e) -------------------------------
STANDARD_DEDUCTION = {
    SINGLE: Decimal("15750"),
    MFJ: Decimal("31500"),
    MFS: Decimal("15750"),
    HOH: Decimal("23625"),
    QSS: Decimal("31500"),
}

# Additional standard deduction for age 65+ or blindness (per box checked).
ADDITIONAL_STD_DEDUCTION = {
    SINGLE: Decimal("2000"),
    HOH: Decimal("2000"),
    MFJ: Decimal("1600"),
    MFS: Decimal("1600"),
    QSS: Decimal("1600"),
}

# --- Ordinary income tax brackets (marginal) -------------------------------
# Each entry: (lower_bound_inclusive, marginal_rate). MFS = MFJ thresholds / 2.
BRACKETS = {
    SINGLE: [
        (Decimal("0"), Decimal("0.10")),
        (Decimal("11925"), Decimal("0.12")),
        (Decimal("48475"), Decimal("0.22")),
        (Decimal("103350"), Decimal("0.24")),
        (Decimal("197300"), Decimal("0.32")),
        (Decimal("250525"), Decimal("0.35")),
        (Decimal("626350"), Decimal("0.37")),
    ],
    MFJ: [
        (Decimal("0"), Decimal("0.10")),
        (Decimal("23850"), Decimal("0.12")),
        (Decimal("96950"), Decimal("0.22")),
        (Decimal("206700"), Decimal("0.24")),
        (Decimal("394600"), Decimal("0.32")),
        (Decimal("501050"), Decimal("0.35")),
        (Decimal("751600"), Decimal("0.37")),
    ],
    MFS: [
        (Decimal("0"), Decimal("0.10")),
        (Decimal("11925"), Decimal("0.12")),
        (Decimal("48475"), Decimal("0.22")),
        (Decimal("103350"), Decimal("0.24")),
        (Decimal("197300"), Decimal("0.32")),
        (Decimal("250525"), Decimal("0.35")),
        (Decimal("375800"), Decimal("0.37")),
    ],
    HOH: [
        (Decimal("0"), Decimal("0.10")),
        (Decimal("17000"), Decimal("0.12")),
        (Decimal("64850"), Decimal("0.22")),
        (Decimal("103350"), Decimal("0.24")),
        (Decimal("197300"), Decimal("0.32")),
        (Decimal("250500"), Decimal("0.35")),
        (Decimal("626350"), Decimal("0.37")),
    ],
}
BRACKETS[QSS] = BRACKETS[MFJ]

# Below this taxable income the IRS *requires* the Tax Table ($50-band midpoint);
# at or above it, the Tax Computation Worksheet (direct formula) is used.
TAX_TABLE_CEILING = Decimal("100000")
TAX_TABLE_BAND = Decimal("50")

# --- Child Tax Credit / Credit for Other Dependents (Schedule 8812) --------
CTC_PER_CHILD = Decimal("2200")          # line 19 component, per qualifying child
CTC_REFUNDABLE_PER_CHILD = Decimal("1700")  # max Additional CTC (line 28), per child
ODC_PER_DEPENDENT = Decimal("500")       # credit for other dependents (nonrefundable)
CTC_PHASEOUT_THRESHOLD = {               # MAGI where CTC/ODC begins to phase out
    SINGLE: Decimal("200000"),
    MFS: Decimal("200000"),
    HOH: Decimal("200000"),
    QSS: Decimal("200000"),
    MFJ: Decimal("400000"),
}
CTC_PHASEOUT_PER = Decimal("1000")       # $50 reduction per $1,000 (or fraction) over
CTC_PHASEOUT_RATE = Decimal("50")
ACTC_EARNED_INCOME_FLOOR = Decimal("2500")   # ACTC = 15% of earned income over this
ACTC_RATE = Decimal("0.15")

# --- Earned Income Tax Credit (EITC) ---------------------------------------
# Per number of qualifying children (0,1,2,3+).
EITC_INVESTMENT_INCOME_LIMIT = Decimal("11950")  # hard cliff for 2025

# credit (phase-in) rate, earned-income amount where max credit is reached,
# maximum credit, phaseout rate.
EITC_PARAMS = {
    0: {
        "credit_rate": Decimal("0.0765"),
        "earned_income_amount": Decimal("8490"),
        "max_credit": Decimal("649"),
        "phaseout_rate": Decimal("0.0765"),
    },
    1: {
        "credit_rate": Decimal("0.34"),
        "earned_income_amount": Decimal("12730"),
        "max_credit": Decimal("4328"),
        "phaseout_rate": Decimal("0.1598"),
    },
    2: {
        "credit_rate": Decimal("0.40"),
        "earned_income_amount": Decimal("17880"),
        "max_credit": Decimal("7152"),
        "phaseout_rate": Decimal("0.2106"),
    },
    3: {  # 3 or more
        "credit_rate": Decimal("0.45"),
        "earned_income_amount": Decimal("17880"),
        "max_credit": Decimal("8046"),
        "phaseout_rate": Decimal("0.2106"),
    },
}

# Phaseout begin threshold depends on whether the return is "joint" (MFJ/QSS).
EITC_PHASEOUT_THRESHOLD = {
    "single": {0: Decimal("10620"), 1: Decimal("23350"), 2: Decimal("23350"), 3: Decimal("23350")},
    "joint": {0: Decimal("17730"), 1: Decimal("30470"), 2: Decimal("30470"), 3: Decimal("30470")},
}

# Childless EITC age test (taxpayer must be at least 25 and under 65).
EITC_CHILDLESS_MIN_AGE = 25
EITC_CHILDLESS_MAX_AGE = 64
