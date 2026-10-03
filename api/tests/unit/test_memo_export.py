"""Credit memo export (api/app/memo_export.py): both formats render, carry the logo, and
never print an unmasked PAN or Aadhaar number. A made-up applicant; no database needed."""
import html
import io
import re
import zipfile
from datetime import datetime

from app.memo_export import inr, mask_aadhaar, mask_pan, render_docx, render_pdf

MODEL = {
    "ref": "CM-AI-00001", "generated_at": "2026-09-30T10:12:00", "exported_at": datetime(2026, 10, 1),
    "prepared_by": "Test Officer", "status": "DRAFT — advisory", "source": "template",
    "application_no": "APP-TEST0001", "product": "Two Wheeler Loan", "branch": "JAI", "officer": "Test Officer",
    "head": None, "applied_at": "2026-09-27T05:30:12", "status_label": "Under Review",
    "applicant": {"name": "Test Applicant", "pan": mask_pan("ABCDE1234F"), "aadhaar": mask_aadhaar("1234 5678 9012"),
                  "dob": "01 Jan 1990", "occupation": "Salaried", "location": "Pune"},
    "loan": {"requested": 150000, "tenure": 24},
    "recommendation": {"key": "refer_to_officer", "label": "Refer for a closer review", "tone": "warn",
                       "rationale": "No CIBIL score on file & income has one source <unverified>.",
                       "baseline": "Refer for a closer review", "model": "", "requires_head": False},
    "kpis": {"risk_score": 38.6, "risk_grade": "B", "fraud": "Low", "fraud_score": 0.1, "policy": "Pass_with_warnings",
             "income_used": 60000, "income_declared": 60000},
    "sections": [("Executive summary", "Line one.\nLine two."), ("Applicant profile", "—"), ("Income assessment", "—"),
                 ("Risk assessment", "—"), ("Fraud and compliance", "—"), ("Recommendation", "Refer.")],
    "conditions": ["Pull a bureau report"], "risk_reasons": [],
    "income": {"estimates": [{"basis": "net salary on the payslip", "monthly": 60000}], "mismatches": [], "used": 60000,
               "basis": "only source on file"},
    "drivers": [{"label": "Credit history", "score": 50, "weight": 0.15, "note": "Data unavailable"}],
    "documents": [{"label": "PAN Card", "status": "Pending", "tamper": "Low", "attempt": 1}],
    "offer": {"amount": 150000, "rate": 12.5, "tenure": 24, "emi": 7096.0, "fee": 1500, "requested": 150000, "capped": False},
    "next_step": "", "notes": [],
}


def test_masks_and_indian_grouping():
    assert mask_pan("ABCDE1234F") == "XXXXX1234F" and mask_pan("") == "—"
    assert mask_aadhaar("1234 5678 9012") == "XXXX-XXXX-9012" and mask_aadhaar("XXXXXXXX9012") == "XXXX-XXXX-9012"
    assert inr(168730) == "₹1,68,730" and inr(12345678) == "₹1,23,45,678" and inr(None) == "—"


def test_pdf_renders_with_masked_identifiers():
    data = render_pdf(MODEL)
    assert data[:5] == b"%PDF-" and len(data) > 20000          # the logo alone is ~50 KB uncompressed
    assert b"ABCDE1234F" not in data and b"5678" not in data


def test_word_renders_with_logo_and_masked_identifiers():
    z = zipfile.ZipFile(io.BytesIO(render_docx(MODEL)))
    body = html.unescape(re.sub(r"<[^>]+>", "", z.read("word/document.xml").decode("utf-8")))
    assert any(n.startswith("word/media/") for n in z.namelist())   # the letterhead logo
    assert "Credit Memorandum" in body and "XXXXX1234F" in body and "XXXX-XXXX-9012" in body
    assert "ABCDE1234F" not in body and "5678" not in body
    assert "<unverified>" in body                                    # text is escaped, not dropped
