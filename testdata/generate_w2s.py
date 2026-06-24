#!/usr/bin/env python3
"""Generate a variety of realistic *fake* Form W-2 PDFs for testing the
tax-filing assistant.

All data here is fabricated test data: fake names, fake SSNs (using the
555-xx-xxxx range reserved/never-issued for examples), fake EINs, and fake
addresses. There is no real PII. Do not use for any real filing.

Each profile is internally consistent:
  * Social Security wages/tax  (boxes 3 / 4)  -> 6.2% of box 3
  * Medicare wages/tax         (boxes 5 / 6)  -> 1.45% of box 5
  * Box 1 (federal taxable wages) is reduced by pre-tax 401(k) deferrals
    (box 12 code D) and pre-tax health premiums (section 125), which is how a
    real W-2 behaves: code D lowers box 1 but NOT boxes 3/5; section-125 health
    lowers boxes 1, 3 and 5.
  * Federal income tax withheld (box 2) is a realistic amount for the wage
    level, generally chosen to land near the true 2025 liability so the
    assistant produces a small refund or small balance due (good for demos).

Outputs:
  testdata/w2_pdfs/<id>.pdf      one PDF per profile (Copy B, employee's copy)
  testdata/w2_images/<id>.png    PNG render of each (for image-upload flows)
  testdata/manifest.json         ground-truth box values + expected scenario

Regenerate with:
  .venv/Scripts/python testdata/generate_w2s.py
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent
PDF_DIR = ROOT / "w2_pdfs"
IMG_DIR = ROOT / "w2_images"

# 2025 payroll-tax constants
SS_RATE = 0.062
MEDICARE_RATE = 0.0145
SS_WAGE_BASE_2025 = 176_100.0


def money(x: float) -> str:
    return f"{x:,.2f}"


@dataclass
class W2:
    """A single fake W-2. Box values are derived from the gross-pay model so
    the form is internally consistent."""

    id: str
    scenario: str  # human description of what this case exercises

    # identity
    employee_ssn: str
    employee_name: str
    employee_address: str
    employer_ein: str
    employer_name: str
    employer_address: str
    control_number: str

    # compensation model (used to derive the numbered boxes)
    gross_pay: float
    pretax_401k: float = 0.0       # box 12 code D; lowers box 1 only
    pretax_health: float = 0.0     # section 125; lowers boxes 1, 3, 5
    fed_withheld: float = 0.0      # box 2 (set explicitly, realistic)
    dependent_care: float = 0.0    # box 10
    retirement_plan_box13: bool = False
    box12: list[tuple[str, float]] = field(default_factory=list)  # extra codes
    box14: list[tuple[str, float]] = field(default_factory=list)

    # state/local
    state: str = ""
    state_employer_id: str = ""
    state_tax: float = 0.0

    # for the messy/partial stretch case: boxes to blank out on the PDF only
    blank_boxes: tuple[str, ...] = ()

    # expected filing scenario (drives eval expectations downstream)
    expected_filing_status: str = "single"

    def boxes(self) -> dict:
        box1 = round(self.gross_pay - self.pretax_401k - self.pretax_health, 2)
        ss_wages = round(min(self.gross_pay - self.pretax_health, SS_WAGE_BASE_2025), 2)
        medicare_wages = round(self.gross_pay - self.pretax_health, 2)
        box4 = round(ss_wages * SS_RATE, 2)
        box6 = round(medicare_wages * MEDICARE_RATE, 2)

        b12 = list(self.box12)
        if self.pretax_401k:
            b12.insert(0, ("D", round(self.pretax_401k, 2)))

        state_wages = box1 if self.state else 0.0
        return {
            "box1_wages": box1,
            "box2_fed_withheld": round(self.fed_withheld, 2),
            "box3_ss_wages": ss_wages,
            "box4_ss_tax": box4,
            "box5_medicare_wages": medicare_wages,
            "box6_medicare_tax": box6,
            "box10_dependent_care": round(self.dependent_care, 2),
            "box12": [{"code": c, "amount": round(a, 2)} for c, a in b12],
            "box13_retirement_plan": self.retirement_plan_box13 or bool(self.pretax_401k),
            "box14": [{"label": l, "amount": round(a, 2)} for l, a in self.box14],
            "box15_state": self.state,
            "box15_state_employer_id": self.state_employer_id,
            "box16_state_wages": round(state_wages, 2) if self.state else 0.0,
            "box17_state_tax": round(self.state_tax, 2),
        }


# --------------------------------------------------------------------------- #
# The dataset. All centered on the ~$40k W-2 earner the challenge targets,
# with deliberate variety so different code paths / filing statuses are tested.
# --------------------------------------------------------------------------- #
PROFILES: list[W2] = [
    W2(
        id="01_single_40k_baseline",
        scenario="Canonical case: single filer, ~$40k wages, withholding set "
        "to produce a small refund. The happy path.",
        employee_ssn="555-12-3456",
        employee_name="Jordan A. Avery",
        employee_address="142 Maple Street, Apt 3B, Columbus, OH 43215",
        employer_ein="34-1928374",
        employer_name="Buckeye Retail Group LLC",
        employer_address="800 Commerce Way, Columbus, OH 43219",
        control_number="A1B2-0001",
        gross_pay=40_000.00,
        fed_withheld=3_180.00,
        state="OH",
        state_employer_id="OH-558812",
        state_tax=612.00,
        expected_filing_status="single",
    ),
    W2(
        id="02_single_38k_no_state_tax",
        scenario="Single, ~$38k, employer in Texas (no state income tax). "
        "Boxes 15-17 effectively empty — tests handling of no-state-tax W-2s.",
        employee_ssn="555-22-7788",
        employee_name="Marcus Lee Tran",
        employee_address="2207 Bluebonnet Ln, Austin, TX 78704",
        employer_ein="74-5566778",
        employer_name="Lone Star Logistics Inc",
        employer_address="500 Industrial Pkwy, Austin, TX 78744",
        control_number="LS-2207",
        gross_pay=38_250.00,
        fed_withheld=2_910.00,
        state="",  # TX has no state income tax
        expected_filing_status="single",
    ),
    W2(
        id="03_mfj_42k_primary_earner",
        scenario="Married filing jointly: this is the primary earner's W-2 "
        "(~$42k). Tests that filing status changes the standard deduction and "
        "tax. Assistant should ask about a spouse.",
        employee_ssn="555-33-1212",
        employee_name="Priya N. Kapoor",
        employee_address="58 Birchwood Ct, Rochester, NY 14620",
        employer_ein="16-3344556",
        employer_name="Genesee Community Health Partners",
        employer_address="120 Lakeview Ave, Rochester, NY 14608",
        control_number="GCH-0588",
        gross_pay=42_400.00,
        fed_withheld=3_050.00,
        state="NY",
        state_employer_id="NY-447781",
        state_tax=1_510.00,
        expected_filing_status="married_filing_jointly",
    ),
    W2(
        id="04_single_45k_401k_deferral",
        scenario="Single, ~$45k gross with a $3,600 pre-tax 401(k) deferral "
        "(box 12 code D). Box 1 < boxes 3/5, and box 13 'Retirement plan' is "
        "checked. Tests that the agent reads box 1 (not gross) for AGI.",
        employee_ssn="555-44-9090",
        employee_name="Devon R. Whitfield",
        employee_address="3391 Cedar Hollow Rd, Raleigh, NC 27604",
        employer_ein="56-7788990",
        employer_name="Tarheel Software Co",
        employer_address="44 Research Dr, Raleigh, NC 27709",
        control_number="THS-3391",
        gross_pay=45_000.00,
        pretax_401k=3_600.00,
        fed_withheld=3_240.00,
        retirement_plan_box13=True,
        box12=[("DD", 6_840.00)],  # employer-sponsored health coverage (info only)
        state="NC",
        state_employer_id="NC-220145",
        state_tax=1_380.00,
        expected_filing_status="single",
    ),
    W2(
        id="05_hoh_36k_dependent_care",
        scenario="Head of household, ~$36k, with $2,500 in box 10 dependent "
        "care benefits — implies a child/dependent. Tests filing status = HoH "
        "and that the agent may ask about dependents.",
        employee_ssn="555-55-3434",
        employee_name="Alicia M. Booker",
        employee_address="77 Sycamore Ave, Peoria, IL 61604",
        employer_ein="36-2211009",
        employer_name="Prairie State Hospitality",
        employer_address="910 Riverfront Dr, Peoria, IL 61602",
        control_number="PSH-0077",
        gross_pay=36_500.00,
        pretax_health=1_800.00,
        fed_withheld=2_180.00,
        dependent_care=2_500.00,
        state="IL",
        state_employer_id="IL-339204",
        state_tax=1_080.00,
        expected_filing_status="head_of_household",
    ),
    W2(
        id="06_single_41k_pretax_health",
        scenario="Single, ~$41k gross with $2,400 pre-tax health premiums "
        "(section 125). Box 1 reduced AND boxes 3/5 reduced — different from the "
        "401(k) case. Tests correct reading of reduced SS/Medicare wages.",
        employee_ssn="555-66-5656",
        employee_name="Samantha K. Ortiz",
        employee_address="615 Willow Bend, Tempe, AZ 85281",
        employer_ein="86-1100220",
        employer_name="Desert Valley Schools District",
        employer_address="2300 University Dr, Tempe, AZ 85281",
        control_number="DVS-0615",
        gross_pay=41_200.00,
        pretax_health=2_400.00,
        fed_withheld=2_760.00,
        box14=[("AZ SUI", 41.20)],
        state="AZ",
        state_employer_id="AZ-771230",
        state_tax=905.00,
        expected_filing_status="single",
    ),
    W2(
        id="07_single_39k_underwithheld",
        scenario="Single, ~$39k but deliberately UNDER-withheld (box 2 low), so "
        "the taxpayer owes a small balance due instead of a refund. Tests the "
        "'amount you owe' path on the 1040.",
        employee_ssn="555-77-1001",
        employee_name="Eli J. Nakamura",
        employee_address="1820 Foster St, Portland, OR 97227",
        employer_ein="93-4455661",
        employer_name="Rose City Bicycle Works",
        employer_address="55 Industrial Loop, Portland, OR 97210",
        control_number="RCB-1820",
        gross_pay=39_100.00,
        fed_withheld=1_650.00,  # intentionally low
        state="OR",
        state_employer_id="OR-118827",
        state_tax=1_640.00,
        expected_filing_status="single",
    ),
    W2(
        id="08_single_messy_partial",
        scenario="STRETCH/robustness: a messy, partial W-2. Box 2 (federal "
        "withholding) and the control number are missing/blank, and amounts use "
        "an inconsistent format. Tests graceful recovery and follow-up "
        "questions when the input is incomplete.",
        employee_ssn="555-88-2002",
        employee_name="Casey Morgan",
        employee_address="404 Elm St, Dayton, OH 45402",
        employer_ein="31-9988776",
        employer_name="Gem City Diner",
        employer_address="12 Main St, Dayton, OH 45402",
        control_number="",
        gross_pay=37_800.00,
        fed_withheld=0.0,  # missing on the form
        state="OH",
        state_employer_id="OH-558812",
        state_tax=540.00,
        blank_boxes=("box2_fed_withheld",),
        expected_filing_status="single",
    ),
]


# --------------------------------------------------------------------------- #
# PDF rendering — a clean, legible W-2 (Copy B) laid out like the official form.
# Not pixel-perfect, but every box is labeled with its number + name so OCR /
# vision models extract values reliably.
# --------------------------------------------------------------------------- #
def draw_w2_pdf(w2: W2, path: Path) -> None:
    b = w2.boxes()
    c = canvas.Canvas(str(path), pagesize=letter)
    W, H = letter

    left = 0.6 * inch
    right = W - 0.6 * inch
    top = H - 0.7 * inch
    form_w = right - left

    def label(x, y, text, size=6):
        c.setFont("Helvetica", size)
        c.drawString(x + 2, y - 8, text)

    def value(x, y, text, size=10, bold=True):
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(x + 4, y - 22, text)

    def cell(x, y, w, h, lab, val, lab_size=6, val_size=10):
        c.setLineWidth(0.5)
        c.rect(x, y - h, w, h)
        label(x, y, lab, lab_size)
        if val:
            value(x, y, val, val_size)

    # Title
    c.setFont("Helvetica-Bold", 13)
    c.drawString(left, top + 6, "Form W-2  Wage and Tax Statement")
    c.setFont("Helvetica", 8)
    c.drawString(left, top - 6, "2025")
    c.setFont("Helvetica", 7)
    c.drawRightString(right, top + 6, "Copy B — To Be Filed With Employee's FEDERAL Tax Return")
    c.drawRightString(right, top - 4, "OMB No. 1545-0008  (FAKE TEST DATA — NOT A REAL W-2)")

    y = top - 18
    # Box a: SSN (full width top strip)
    cell(left, y, form_w, 26, "a  Employee's social security number", w2.employee_ssn, val_size=11)
    y -= 26

    # Two-column body: left = identity, right = money boxes
    col_split = left + form_w * 0.50
    left_w = col_split - left
    right_x = col_split
    right_w = right - col_split

    # ----- LEFT identity column -----
    ly = y
    cell(left, ly, left_w, 26, "b  Employer identification number (EIN)", w2.employer_ein, val_size=11)
    ly -= 26
    emp = f"{w2.employer_name}\n{w2.employer_address}"
    c.rect(left, ly - 46, left_w, 46)
    label(left, ly, "c  Employer's name, address, and ZIP code")
    c.setFont("Helvetica-Bold", 9)
    c.drawString(left + 4, ly - 22, w2.employer_name)
    c.setFont("Helvetica", 8)
    c.drawString(left + 4, ly - 36, w2.employer_address)
    ly -= 46
    cell(left, ly, left_w, 22, "d  Control number", w2.control_number, val_size=9)
    ly -= 22
    c.rect(left, ly - 46, left_w, 46)
    label(left, ly, "e/f  Employee's name, address, and ZIP code")
    c.setFont("Helvetica-Bold", 9)
    c.drawString(left + 4, ly - 22, w2.employee_name)
    c.setFont("Helvetica", 8)
    c.drawString(left + 4, ly - 36, w2.employee_address)
    ly -= 46

    # ----- RIGHT money-box grid (two sub-columns) -----
    def show(key, raw):
        if key in w2.blank_boxes:
            return ""
        return raw

    half = right_w / 2
    ry = y
    rows = [
        ("1  Wages, tips, other comp.", money(b["box1_wages"]),
         "2  Federal income tax withheld", show("box2_fed_withheld", money(b["box2_fed_withheld"]))),
        ("3  Social security wages", money(b["box3_ss_wages"]),
         "4  Social security tax withheld", money(b["box4_ss_tax"])),
        ("5  Medicare wages and tips", money(b["box5_medicare_wages"]),
         "6  Medicare tax withheld", money(b["box6_medicare_tax"])),
        ("7  Social security tips", "",
         "8  Allocated tips", ""),
        ("9", "",
         "10  Dependent care benefits", money(b["box10_dependent_care"]) if b["box10_dependent_care"] else ""),
    ]
    rh = 26
    for l1, v1, l2, v2 in rows:
        cell(right_x, ry, half, rh, l1, v1)
        cell(right_x + half, ry, half, rh, l2, v2)
        ry -= rh

    # Box 11 / 12a
    box12 = b["box12"]
    def b12_str(i):
        return f"{box12[i]['code']}  {money(box12[i]['amount'])}" if i < len(box12) else ""
    cell(right_x, ry, half, rh, "11  Nonqualified plans", "")
    cell(right_x + half, ry, half, rh, "12a  Code / amount", b12_str(0))
    ry -= rh
    # Box 13 checkboxes / 12b
    c.rect(right_x, ry - rh, half, rh)
    label(right_x, ry, "13  Retirement plan")
    c.setFont("Helvetica-Bold", 9)
    chk = "[X] Retirement plan" if b["box13_retirement_plan"] else "[ ] Retirement plan"
    c.drawString(right_x + 4, ry - 22, chk)
    cell(right_x + half, ry, half, rh, "12b  Code / amount", b12_str(1))
    ry -= rh
    # Box 14 / 12c
    b14 = b["box14"]
    b14_str = "  ".join(f"{x['label']} {money(x['amount'])}" for x in b14)
    cell(right_x, ry, half, rh, "14  Other", b14_str, val_size=8)
    cell(right_x + half, ry, half, rh, "12c  Code / amount", b12_str(2))
    ry -= rh

    # ----- State/local strip across the bottom of whichever column is higher -----
    bottom_y = min(ly, ry) - 6
    sw = form_w / 5
    has_state = bool(b["box15_state"])
    state_cells = [
        ("15  State", b["box15_state"]),
        ("     Employer's state ID no.", b["box15_state_employer_id"]),
        ("16  State wages, tips, etc.", money(b["box16_state_wages"]) if has_state else ""),
        ("17  State income tax", money(b["box17_state_tax"]) if has_state else ""),
        ("18-20  Local", ""),
    ]
    for i, (lab, val) in enumerate(state_cells):
        cell(left + i * sw, bottom_y, sw, 28, lab, val, val_size=9)

    # Footer note
    c.setFont("Helvetica-Oblique", 7)
    c.drawString(left, bottom_y - 40,
                 "Fake W-2 generated for software testing only. SSN uses the reserved 555-xx-xxxx example range. "
                 "Not valid for any tax filing.")
    c.setFont("Helvetica", 7)
    c.drawString(left, bottom_y - 52, f"Test case: {w2.id}")

    c.showPage()
    c.save()


def render_png(pdf_path: Path, png_path: Path) -> bool:
    try:
        import pypdfium2 as pdfium
    except Exception:
        return False
    pdf = pdfium.PdfDocument(str(pdf_path))
    page = pdf[0]
    bitmap = page.render(scale=2.0)
    img = bitmap.to_pil()
    img.save(str(png_path))
    pdf.close()
    return True


def main() -> None:
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    IMG_DIR.mkdir(parents=True, exist_ok=True)

    manifest = {
        "note": "Fake W-2 test data. No real PII. SSNs use the reserved "
        "555-xx-xxxx example range. For testing only — not for any real filing.",
        "tax_year": 2025,
        "count": len(PROFILES),
        "profiles": [],
    }

    pngs_ok = True
    for w2 in PROFILES:
        pdf_path = PDF_DIR / f"{w2.id}.pdf"
        png_path = IMG_DIR / f"{w2.id}.png"
        draw_w2_pdf(w2, pdf_path)
        ok = render_png(pdf_path, png_path)
        pngs_ok = pngs_ok and ok

        manifest["profiles"].append({
            "id": w2.id,
            "scenario": w2.scenario,
            "expected_filing_status": w2.expected_filing_status,
            "pdf": f"w2_pdfs/{w2.id}.pdf",
            "image": f"w2_images/{w2.id}.png" if ok else None,
            "employee": {
                "name": w2.employee_name,
                "ssn": w2.employee_ssn,
                "address": w2.employee_address,
            },
            "employer": {
                "name": w2.employer_name,
                "ein": w2.employer_ein,
                "address": w2.employer_address,
            },
            "ground_truth_boxes": w2.boxes(),
            "blanked_on_form": list(w2.blank_boxes),
        })

    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"Wrote {len(PROFILES)} W-2 PDFs -> {PDF_DIR}")
    print(f"PNG renders {'OK' if pngs_ok else 'SKIPPED (pypdfium2 unavailable)'} -> {IMG_DIR}")
    print(f"Ground-truth manifest -> {ROOT / 'manifest.json'}")


if __name__ == "__main__":
    main()
