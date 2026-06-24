"""
Fill the *official* IRS 2025 Form 1040 (assets/f1040_2025.pdf).

The IRS form is an XFA/AcroForm hybrid, and XFA-aware viewers render
inconsistently from plain AcroForm value injection. To guarantee the downloaded
return looks right in *every* viewer (browser inline, Acrobat, Preview, pdfium),
we stamp the computed values directly onto the official page content at each
field's mapped rectangle and then flatten the form. The output is the genuine
government PDF with our numbers permanently rendered on it.

Field rectangles are read from the form's own AcroForm widgets, so placement
tracks the real boxes (it is not hand-tuned pixel guessing).

Hard rule: only values that came out of the deterministic engine AND passed the
independent verification pass are ever stamped. ``fill_1040`` raises otherwise.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, IndirectObject, NameObject
from reportlab.pdfgen import canvas

from . import tax_tables_2025 as T
from .tax_engine import DependentFact, Form1040Result, dollars
from .verification import verify, VerificationResult

FORM_PATH = Path(__file__).resolve().parent.parent / "assets" / "f1040_2025.pdf"

P1 = "topmostSubform[0].Page1[0]."
P2 = "topmostSubform[0].Page2[0]."

# Line id -> AcroForm field name (the dollar boxes).
LINE_FIELDS: dict[str, str] = {
    "1a": P1 + "f1_47[0]", "1z": P1 + "f1_57[0]", "2b": P1 + "f1_59[0]",
    "3b": P1 + "f1_61[0]", "4b": P1 + "f1_63[0]", "5b": P1 + "f1_66[0]",
    "6b": P1 + "f1_69[0]", "7": P1 + "f1_70[0]", "8": P1 + "f1_72[0]",
    "9": P1 + "f1_73[0]", "10": P1 + "f1_74[0]", "11a": P1 + "f1_75[0]",
    "11b": P2 + "f2_01[0]", "12e": P2 + "f2_02[0]", "13a": P2 + "f2_03[0]",
    "13b": P2 + "f2_04[0]", "14": P2 + "f2_05[0]", "15": P2 + "f2_06[0]",
    "16": P2 + "f2_08[0]", "17": P2 + "f2_09[0]", "18": P2 + "f2_10[0]",
    "19": P2 + "f2_11[0]", "20": P2 + "f2_12[0]", "21": P2 + "f2_13[0]",
    "22": P2 + "f2_14[0]", "23": P2 + "f2_15[0]", "24": P2 + "f2_16[0]",
    "25a": P2 + "f2_17[0]", "25b": P2 + "f2_18[0]", "25c": P2 + "f2_19[0]",
    "25d": P2 + "f2_20[0]", "26": P2 + "f2_21[0]", "27": P2 + "f2_23[0]",
    "28": P2 + "f2_24[0]", "29": P2 + "f2_25[0]", "31": P2 + "f2_27[0]",
    "32": P2 + "f2_28[0]", "33": P2 + "f2_29[0]", "34": P2 + "f2_30[0]",
    "35a": P2 + "f2_31[0]", "37": P2 + "f2_35[0]", "38": P2 + "f2_36[0]",
}

HEADER_FIELDS = {
    "first_name": P1 + "f1_14[0]",
    "last_name": P1 + "f1_15[0]",
    "ssn": P1 + "f1_16[0]",
    "spouse_first": P1 + "f1_17[0]",
    "spouse_last": P1 + "f1_18[0]",
    "spouse_ssn": P1 + "f1_19[0]",
    "address": P1 + "Address_ReadOrder[0].f1_20[0]",
    "apt": P1 + "Address_ReadOrder[0].f1_21[0]",
    "city": P1 + "Address_ReadOrder[0].f1_22[0]",
    "state": P1 + "Address_ReadOrder[0].f1_23[0]",
    "zip": P1 + "Address_ReadOrder[0].f1_24[0]",
}

# Filing-status checkbox -> the specific widget FQN to mark.
FILING_STATUS_CHECKBOX = {
    T.SINGLE: P1 + "Checkbox_ReadOrder[0].c1_8[0]",
    T.MFJ: P1 + "Checkbox_ReadOrder[0].c1_8[1]",
    T.MFS: P1 + "Checkbox_ReadOrder[0].c1_8[2]",
    T.HOH: P1 + "c1_8[0]",
    T.QSS: P1 + "c1_8[1]",
}
DIGITAL_ASSETS_NO = P1 + "c1_10[1]"  # the "No" widget

DEP_FIRST = [P1 + f"Table_Dependents[0].Row1[0].f1_{31+i}[0]" for i in range(4)]
DEP_LAST = [P1 + f"Table_Dependents[0].Row2[0].f1_{35+i}[0]" for i in range(4)]
DEP_SSN = [P1 + f"Table_Dependents[0].Row3[0].f1_{39+i}[0]" for i in range(4)]
DEP_REL = [P1 + f"Table_Dependents[0].Row4[0].f1_{43+i}[0]" for i in range(4)]
DEP_CTC = [P1 + f"Table_Dependents[0].Row7[0].Dependent{n}[0].c1_{27+n}[0]" for n in range(1, 5)]
DEP_ODC = [P1 + f"Table_Dependents[0].Row7[0].Dependent{n}[0].c1_{27+n}[1]" for n in range(1, 5)]

# Lines that always show a value even when $0 (running totals on the form).
ALWAYS_SHOW = {"1a", "1z", "9", "11a", "11b", "12e", "14", "15", "16", "18",
               "22", "24", "25d", "33"}


@dataclass
class TaxpayerIdentity:
    first_name: str = ""
    last_name: str = ""
    ssn: str = ""
    spouse_first_name: str = ""
    spouse_last_name: str = ""
    spouse_ssn: str = ""
    address: str = ""
    apt: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""
    dependents: list[DependentFact] = field(default_factory=list)


def _fmt_money(amount: Decimal) -> str:
    amount = dollars(amount)
    if amount < 0:
        return f"({abs(int(amount)):,})"
    return f"{int(amount):,}"


def _digits(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _full_name(obj) -> str:
    parts, seen, cur = [], 0, obj
    while cur is not None and seen < 12:
        t = cur.get("/T")
        if t:
            parts.append(str(t))
        parent = cur.get("/Parent")
        cur = parent.get_object() if parent is not None else None
        seen += 1
    return ".".join(reversed(parts))


@lru_cache(maxsize=1)
def _field_boxes() -> dict[str, tuple[int, tuple[float, float, float, float]]]:
    """Map each widget's fully-qualified name -> (page_index, rect)."""
    reader = PdfReader(str(FORM_PATH))
    boxes: dict[str, tuple[int, tuple]] = {}
    for pidx, page in enumerate(reader.pages):
        for a in (page.get("/Annots") or []):
            obj = a.get_object()
            if obj.get("/Subtype") != "/Widget":
                continue
            rect = obj.get("/Rect")
            if not rect:
                continue
            name = _full_name(obj)
            boxes[name] = (pidx, tuple(float(v) for v in rect))
    return boxes


@dataclass
class _Placement:
    fqn: str
    text: str
    align: str  # "left" | "right"


def _collect_placements(result: Form1040Result, identity: TaxpayerIdentity):
    texts: list[_Placement] = []
    checks: list[str] = []

    for line, fqn in LINE_FIELDS.items():
        amt = result.amt(line)
        if amt != 0 or line in ALWAYS_SHOW:
            texts.append(_Placement(fqn, _fmt_money(amt), "right"))

    fs = result.facts.filing_status
    texts += [
        _Placement(HEADER_FIELDS["first_name"], identity.first_name, "left"),
        _Placement(HEADER_FIELDS["last_name"], identity.last_name, "left"),
        _Placement(HEADER_FIELDS["ssn"], _digits(identity.ssn), "left"),
        _Placement(HEADER_FIELDS["address"], identity.address, "left"),
        _Placement(HEADER_FIELDS["city"], identity.city, "left"),
        _Placement(HEADER_FIELDS["state"], identity.state, "left"),
        _Placement(HEADER_FIELDS["zip"], identity.zip, "left"),
    ]
    if identity.apt:
        texts.append(_Placement(HEADER_FIELDS["apt"], identity.apt, "left"))
    if fs in (T.MFJ, T.MFS, T.QSS) and identity.spouse_first_name:
        texts += [
            _Placement(HEADER_FIELDS["spouse_first"], identity.spouse_first_name, "left"),
            _Placement(HEADER_FIELDS["spouse_last"], identity.spouse_last_name, "left"),
            _Placement(HEADER_FIELDS["spouse_ssn"], _digits(identity.spouse_ssn), "left"),
        ]

    for i, dep in enumerate(identity.dependents[:4]):
        texts += [
            _Placement(DEP_FIRST[i], dep.first_name, "left"),
            _Placement(DEP_LAST[i], dep.last_name, "left"),
            _Placement(DEP_SSN[i], _digits(dep.ssn), "left"),
            _Placement(DEP_REL[i], dep.relationship, "left"),
        ]
        if dep.qualifies_ctc:
            checks.append(DEP_CTC[i])
        elif dep.qualifies_odc:
            checks.append(DEP_ODC[i])

    checks.append(FILING_STATUS_CHECKBOX[fs])
    checks.append(DIGITAL_ASSETS_NO)
    return texts, checks


def _build_overlay(page_sizes, texts, checks) -> PdfReader:
    """Render a reportlab overlay (one page per form page) with the values."""
    boxes = _field_boxes()
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    pages: dict[int, list] = {0: [], 1: []}
    for p in texts:
        loc = boxes.get(p.fqn)
        if loc:
            pages[loc[0]].append(("text", p, loc[1]))
    for fqn in checks:
        loc = boxes.get(fqn)
        if loc:
            pages[loc[0]].append(("check", None, loc[1]))

    for pidx, (w, h) in enumerate(page_sizes):
        c.setPageSize((w, h))
        c.setFillColorRGB(0.05, 0.05, 0.2)  # dark navy ink, like a real fill
        for kind, p, rect in pages.get(pidx, []):
            x0, y0, x1, y1 = rect
            box_h = y1 - y0
            if kind == "text":
                if not p.text:
                    continue
                size = 9 if box_h >= 11 else max(6, box_h - 2)
                c.setFont("Helvetica", size)
                baseline = y0 + (box_h - size) / 2 + 1.5
                if p.align == "right":
                    c.drawRightString(x1 - 3, baseline, p.text)
                else:
                    c.drawString(x0 + 2, baseline, p.text)
            else:  # checkbox mark
                size = min(box_h, x1 - x0) + 1
                c.setFont("Helvetica-Bold", max(8, size))
                c.drawCentredString((x0 + x1) / 2, y0 + 1, "X")
        c.showPage()
    c.save()
    buf.seek(0)
    return PdfReader(buf)


def _flatten(writer: PdfWriter) -> None:
    """Remove widget annotations and the AcroForm/XFA so the stamp is the only
    rendered content (no double-rendering in form-aware viewers)."""
    for page in writer.pages:
        if "/Annots" in page:
            kept = ArrayObject(
                a for a in page["/Annots"]
                if a.get_object().get("/Subtype") != "/Widget"
            )
            page[NameObject("/Annots")] = kept
    root = writer._root_object
    if "/AcroForm" in root:
        del root[NameObject("/AcroForm")]


def fill_1040(result: Form1040Result, identity: TaxpayerIdentity) -> bytes:
    """Return bytes of a filled, flattened official 1040. Raises if verification fails."""
    vr: VerificationResult = verify(result)
    if not vr.passed:
        raise ValueError(
            "Refusing to generate PDF: verification failed -> "
            + "; ".join(f"{c.name}: {c.detail}" for c in vr.failures)
        )

    reader = PdfReader(str(FORM_PATH))
    page_sizes = [(float(p.mediabox.width), float(p.mediabox.height)) for p in reader.pages]

    texts, checks = _collect_placements(result, identity)
    overlay = _build_overlay(page_sizes, texts, checks)

    writer = PdfWriter()
    writer.append(reader)
    for i, page in enumerate(writer.pages):
        if i < len(overlay.pages):
            page.merge_page(overlay.pages[i])
    _flatten(writer)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
