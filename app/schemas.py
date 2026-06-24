"""
Validated data models (pydantic) and the derived return state.

These schemas are a guardrail in their own right: the W-2 the vision model
produces must parse into ``W2`` (types + ranges enforced), and the conversational
agent can only mutate the return through validated patches. Tax-credit
*eligibility* is derived here in code (not by the LLM), so the rules that drive
real dollars are deterministic and auditable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from . import tax_tables_2025 as T
from .tax_engine import DependentFact, TaxFacts
from .pdf_filler import TaxpayerIdentity


# --------------------------------------------------------------------------
# Filing-status normalization (accept many spellings -> canonical engine key)
# --------------------------------------------------------------------------
_FS_ALIASES = {
    "single": T.SINGLE, "s": T.SINGLE,
    "married_filing_jointly": T.MFJ, "mfj": T.MFJ, "married filing jointly": T.MFJ,
    "joint": T.MFJ, "married": T.MFJ,
    "married_filing_separately": T.MFS, "mfs": T.MFS,
    "married filing separately": T.MFS, "separately": T.MFS,
    "head_of_household": T.HOH, "hoh": T.HOH, "head of household": T.HOH,
    "qualifying_surviving_spouse": T.QSS, "qss": T.QSS,
    "qualifying surviving spouse": T.QSS, "widow": T.QSS, "widower": T.QSS,
}


def normalize_filing_status(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    key = value.strip().lower().replace("-", "_")
    if key in T.FILING_STATUSES:
        return key
    return _FS_ALIASES.get(key) or _FS_ALIASES.get(key.replace("_", " "))


# --------------------------------------------------------------------------
# W-2
# --------------------------------------------------------------------------
class Box12Entry(BaseModel):
    code: str = ""
    amount: Decimal = Decimal("0")


class W2(BaseModel):
    """A single W-2. Field names mirror testdata/manifest.json for direct evals."""
    employee_name: str = ""
    employee_ssn: str = ""
    employee_address: str = ""
    employer_name: str = ""
    employer_ein: str = ""
    box1_wages: Decimal = Decimal("0")
    box2_fed_withheld: Decimal = Decimal("0")
    box3_ss_wages: Decimal = Decimal("0")
    box4_ss_tax: Decimal = Decimal("0")
    box5_medicare_wages: Decimal = Decimal("0")
    box6_medicare_tax: Decimal = Decimal("0")
    box10_dependent_care: Decimal = Decimal("0")
    box12: list[Box12Entry] = Field(default_factory=list)
    box13_retirement_plan: bool = False
    box15_state: str = ""
    box16_state_wages: Decimal = Decimal("0")
    box17_state_tax: Decimal = Decimal("0")

    @field_validator("box1_wages", "box2_fed_withheld", "box3_ss_wages",
                     "box4_ss_tax", "box5_medicare_wages", "box6_medicare_tax",
                     "box10_dependent_care", "box16_state_wages", "box17_state_tax",
                     mode="before")
    @classmethod
    def _non_negative_money(cls, v):
        if v in (None, ""):
            return Decimal("0")
        d = Decimal(str(v).replace(",", "").replace("$", "").strip() or "0")
        if d < 0:
            raise ValueError("money amounts cannot be negative")
        return d

    def warnings(self) -> list[str]:
        """Soft, non-fatal data-quality flags for the agent to reason about."""
        w = []
        if self.box1_wages <= 0:
            w.append("Box 1 wages are missing or zero.")
        if self.box2_fed_withheld > self.box1_wages and self.box1_wages > 0:
            w.append("Federal withholding (box 2) exceeds wages (box 1) — unusual.")
        if self.box1_wages > 0 and self.box2_fed_withheld == 0:
            w.append("No federal income tax was withheld (box 2 is blank).")
        return w


class W2Extraction(BaseModel):
    """Result of vision extraction: the W-2 plus per-field confidence (0-1)."""
    w2: W2
    confidences: dict[str, float] = Field(default_factory=dict)

    def low_confidence_fields(self, threshold: float = 0.75) -> list[str]:
        return sorted(
            k for k, v in self.confidences.items()
            if v < threshold and k in ("box1_wages", "box2_fed_withheld", "employee_ssn")
        )


# --------------------------------------------------------------------------
# Dependents (agent supplies facts; eligibility derived in code)
# --------------------------------------------------------------------------
_CHILD_RELATIONSHIPS = {
    "son", "daughter", "child", "stepchild", "foster child", "grandchild",
    "brother", "sister", "stepbrother", "stepsister", "niece", "nephew",
    "half-brother", "half-sister",
}


class Dependent(BaseModel):
    first_name: str = ""
    last_name: str = ""
    ssn: str = ""
    relationship: str = "Son"
    age: Optional[int] = None
    is_full_time_student: bool = False
    months_lived_with_taxpayer: int = 12

    @field_validator("age", mode="before")
    @classmethod
    def _coerce_age(cls, v):
        if v in (None, ""):
            return None
        return int(v)

    @property
    def _is_child_relationship(self) -> bool:
        return self.relationship.strip().lower() in _CHILD_RELATIONSHIPS

    @property
    def qualifies_ctc(self) -> bool:
        # Child Tax Credit: qualifying child under 17 with an SSN.
        return (self._is_child_relationship and self.age is not None
                and self.age < 17 and bool(self.ssn))

    @property
    def qualifies_odc(self) -> bool:
        # Credit for Other Dependents: a dependent who is not a CTC child.
        return not self.qualifies_ctc

    @property
    def is_eitc_qualifying_child(self) -> bool:
        # EITC qualifying child: relationship + age (<19, or <24 & student) + residency.
        if not self._is_child_relationship or self.age is None:
            return False
        age_ok = self.age < 19 or (self.age < 24 and self.is_full_time_student)
        return age_ok and self.months_lived_with_taxpayer >= 7

    def to_fact(self) -> DependentFact:
        return DependentFact(
            first_name=self.first_name, last_name=self.last_name, ssn=self.ssn,
            relationship=self.relationship, qualifies_ctc=self.qualifies_ctc,
            qualifies_odc=self.qualifies_odc,
            is_eitc_qualifying_child=self.is_eitc_qualifying_child,
            lived_with_taxpayer_in_us=self.months_lived_with_taxpayer >= 7,
        )


# --------------------------------------------------------------------------
# Derived return state (the fold target — see events.py)
# --------------------------------------------------------------------------
def _split_name(full: str) -> tuple[str, str]:
    parts = (full or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]


def _split_address(addr: str) -> dict:
    """Best-effort 'street, city, ST zip' -> components."""
    out = {"address": addr or "", "apt": "", "city": "", "state": "", "zip": ""}
    if not addr:
        return out
    parts = [p.strip() for p in addr.split(",")]
    if len(parts) >= 3:
        out["address"] = parts[0]
        if len(parts) >= 4:
            out["apt"] = parts[1]
            out["city"] = parts[2]
            tail = parts[3]
        else:
            out["city"] = parts[1]
            tail = parts[2]
        bits = tail.split()
        if bits:
            out["state"] = bits[0]
        if len(bits) > 1:
            out["zip"] = bits[1]
    return out


@dataclass
class ReturnState:
    """Everything known about the return so far. Derived by folding events."""
    w2s: list[W2] = field(default_factory=list)
    filing_status: Optional[str] = None
    dependents: list[Dependent] = field(default_factory=list)
    # extra answers the W-2 can't supply
    other_income: Decimal = Decimal("0")
    adjustments: Decimal = Decimal("0")
    investment_income: Decimal = Decimal("0")
    taxpayer_age: Optional[int] = None
    spouse_name: str = ""
    spouse_ssn: str = ""
    spouse_age: Optional[int] = None
    can_be_claimed_as_dependent: bool = False
    confirmed_fields: set = field(default_factory=set)

    # ---- readiness ----
    @property
    def has_w2(self) -> bool:
        return any(w.box1_wages > 0 for w in self.w2s)

    def missing_for_filing(self) -> list[str]:
        missing = []
        if not self.has_w2:
            missing.append("a W-2 with wages")
        if not self.filing_status:
            missing.append("filing status")
        return missing

    @property
    def is_ready(self) -> bool:
        return not self.missing_for_filing()

    # ---- conversions to engine / pdf types ----
    def to_tax_facts(self) -> TaxFacts:
        wages = sum((w.box1_wages for w in self.w2s), Decimal("0"))
        withholding = sum((w.box2_fed_withheld for w in self.w2s), Decimal("0"))
        return TaxFacts(
            filing_status=self.filing_status or T.SINGLE,
            wages=wages,
            schedule_1_income=self.other_income,
            adjustments=self.adjustments,
            withholding_w2=withholding,
            dependents=[d.to_fact() for d in self.dependents],
            investment_income=self.investment_income,
            taxpayer_age=self.taxpayer_age,
            spouse_age=self.spouse_age,
            can_be_claimed_as_dependent=self.can_be_claimed_as_dependent,
        )

    def to_identity(self) -> TaxpayerIdentity:
        primary = self.w2s[0] if self.w2s else W2()
        first, last = _split_name(primary.employee_name)
        addr = _split_address(primary.employee_address)
        sfirst, slast = _split_name(self.spouse_name)
        return TaxpayerIdentity(
            first_name=first, last_name=last, ssn=primary.employee_ssn,
            spouse_first_name=sfirst, spouse_last_name=slast, spouse_ssn=self.spouse_ssn,
            address=addr["address"], apt=addr["apt"], city=addr["city"],
            state=addr["state"], zip=addr["zip"],
            dependents=[d.to_fact() for d in self.dependents],
        )
