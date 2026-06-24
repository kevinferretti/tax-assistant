"""PDF tests: stamp the official 1040, then read the rendered text back."""
import io
from decimal import Decimal

import pytest
from pypdf import PdfReader

from app import tax_tables_2025 as T
from app.tax_engine import DependentFact, TaxFacts, compute_1040
from app.pdf_filler import fill_1040, TaxpayerIdentity


def _text(pdf_bytes):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return [p.extract_text() for p in reader.pages]


def test_fill_and_readback_single_40k():
    res = compute_1040(TaxFacts(filing_status=T.SINGLE, wages=Decimal("40000"),
                                withholding_w2=Decimal("3180")))
    ident = TaxpayerIdentity(first_name="Jordan A.", last_name="Avery",
                             ssn="555-12-3456", address="142 Maple Street",
                             apt="3B", city="Columbus", state="OH", zip="43215")
    pdf = fill_1040(res, ident)
    assert pdf[:4] == b"%PDF"
    pages = _text(pdf)
    page1, page2 = pages[0], pages[1]
    assert "40,000" in page1            # wages line 1a
    assert "Avery" in page1             # name stamped
    assert "555123456" in page1         # SSN digits
    assert "24,250" in page2            # taxable income line 15
    assert "2,675" in page2             # tax line 16


def test_form_is_flattened():
    res = compute_1040(TaxFacts(filing_status=T.SINGLE, wages=Decimal("40000")))
    pdf = fill_1040(res, TaxpayerIdentity(first_name="A", last_name="B"))
    reader = PdfReader(io.BytesIO(pdf))
    # No interactive AcroForm left after flattening.
    assert "/AcroForm" not in reader.trailer["/Root"]
    assert reader.get_fields() in (None, {})


def test_fill_refuses_unverified():
    res = compute_1040(TaxFacts(filing_status=T.SINGLE, wages=Decimal("40000")))
    res.lines["16"].amount = Decimal("1")  # corrupt -> verification fails
    with pytest.raises(ValueError):
        fill_1040(res, TaxpayerIdentity(first_name="X", last_name="Y"))


def test_fill_with_dependents_and_hoh():
    kids = [DependentFact(first_name="Sam", last_name="Booker", ssn="555-00-1111",
                          relationship="Daughter", qualifies_ctc=True,
                          is_eitc_qualifying_child=True)]
    res = compute_1040(TaxFacts(filing_status=T.HOH, wages=Decimal("34700"),
                                withholding_w2=Decimal("2180"), dependents=kids))
    ident = TaxpayerIdentity(first_name="Alicia M.", last_name="Booker",
                             ssn="555-55-3434", city="Peoria", state="IL",
                             zip="61604", dependents=kids)
    pdf = fill_1040(res, ident)
    assert "Sam" in _text(pdf)[0]
