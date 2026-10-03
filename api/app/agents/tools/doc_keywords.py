"""What does the page itself say it is? A cheap, deterministic second opinion
for the Document Identity agent.

The identity rules (ported from the browser) only check that the requested
document's KEY FIELD came back — and a salary slip prints a PAN, a bank
statement prints an account holder's name, so a wrong upload often passes.
Reading the page's own words ("Permanent Account Number", "Statement of
Account", "Earnings / Deductions") catches those: the upload is only accepted
when nothing on the page contradicts the requested type.

Text comes from the PDF text layer when there is one (instant), otherwise one
RapidOCR pass on a <=1000 px render (~0.5-1.5 s, run in parallel with the VLM).
"""
from __future__ import annotations

import io
import re

# phrases printed on each document type (lower-case, matched on normalised text)
KEYWORDS: dict[str, list[str]] = {
    "pan": ["permanent account number", "income tax department", "pan card"],
    "aadhaar": ["aadhaar", "unique identification", "uidai", "enrolment", "aam aadmi"],
    "salary_slip": ["salary slip", "payslip", "pay slip", "earnings", "deductions", "net salary", "net pay",
                    "gross salary", "basic pay"],
    "bank_statement": ["statement of account", "account statement", "opening balance", "closing balance",
                       "withdrawal", "deposit", "debit", "credit", "ifsc", "transaction"],
    "itr": ["income tax return", "acknowledgement", "assessment year", "gross total income"],
    "form16": ["form no. 16", "form 16", "section 203", "tax deducted", "tan of"],
    "gst_return": ["gstr", "gstin", "taxable turnover", "return period"],
    "vehicle_quotation": ["quotation", "ex-showroom", "ex showroom", "on road", "on-road", "dealer"],
    "rc_book": ["registration certificate", "chassis", "engine no", "regn"],
    "insurance": ["policy no", "insured", "premium", "sum insured"],
    "cibil": ["cibil", "credit score", "credit information report"],
}
# types whose phrases are distinctive enough to contradict a different request
DISTINCTIVE = {"pan", "aadhaar", "salary_slip", "bank_statement", "itr", "form16", "gst_return",
               "vehicle_quotation", "cibil"}


def page_text(data: bytes, mime: str | None = None, max_dim: int = 1000) -> str:
    """Lower-cased text of page 1 (text layer or OCR). Empty string on failure."""
    try:
        if data[:5] == b"%PDF-":
            import pypdfium2 as pdfium
            from ...pdfium_lock import PDFIUM_LOCK
            with PDFIUM_LOCK:                       # PDFium is not thread-safe
                pdf = pdfium.PdfDocument(data)
                try:
                    text = pdf[0].get_textpage().get_text_bounded() or ""
                finally:
                    pdf.close()
            if len(text.strip()) > 40:
                return text.lower()
        from PIL import Image
        from ...imaging import load_image, ocr_tokens
        im = load_image(data, max_dim=max_dim) if data[:5] == b"%PDF-" else Image.open(io.BytesIO(data)).convert("RGB")
        if im is None:
            return ""
        w, h = im.size
        if max(w, h) > max_dim:
            s = max_dim / max(w, h)
            im = im.resize((int(w * s), int(h * s)))
        return " ".join(t["text"] for t in ocr_tokens(im)).lower()
    except Exception:  # noqa: BLE001 - no text = no second opinion, never a failure
        return ""


def _squash(s: str) -> str:
    # RapidOCR often runs bold capitals together ("incometaxdepartment"), so
    # phrases are matched with spaces and punctuation removed on both sides
    return re.sub(r"[^a-z0-9]", "", s.lower())


_SQUASHED = {dt: sorted({_squash(k) for k in kws}) for dt, kws in KEYWORDS.items()}


def scores(text: str) -> dict[str, int]:
    t = _squash(text)
    return {dt: sum(1 for k in kws if k in t) for dt, kws in _SQUASHED.items()}


_ID_CODE = {"pan": re.compile(r"[a-z]{5}[0-9]{4}[a-z]")}


def unconfirmed(requested: str, text: str) -> bool:
    """A readable page on which OCR finds neither the requested ID card's heading nor a code of its
    format. NOT a reason to refuse the upload - on development data 9% of genuine PAN cards read this
    way (small or stylised print) - but every foreign ID card uploaded as a PAN did, including the
    47% whose document number the reading model returned as a PAN-shaped code and that therefore
    passed the field check. Shown to the officer as a warning."""
    rx = _ID_CODE.get(requested)
    if rx is None or len((text or "").strip()) < 40:
        return False
    return scores(text).get(requested, 0) == 0 and not rx.search(text.lower())


# Phrases only that document prints. A receipt also says "debit", "credit" and "total", and the reading
# model fills a "balance" from its total - one non-zero balance is all the field rule asks of a statement,
# so every receipt uploaded as a bank statement passed (10 of 10, real-data test 2026-10-02).
OWN_WORDS = {
    "bank_statement": ["statement of account", "account statement", "opening balance", "closing balance", "ifsc",
                       "account number", "account no", "micr", "value date", "transaction date", "txn date",
                       "withdrawal", "passbook", "customer id", "branch code"],
}
_OWN_SQUASHED = {dt: sorted({_squash(k) for k in kws}) for dt, kws in OWN_WORDS.items()}


def lacks_own_words(requested: str, text: str) -> bool:
    """A readable page that carries none of the phrases only the requested document prints."""
    own = _OWN_SQUASHED.get(requested)
    if not own or len((text or "").strip()) < 40:
        return False
    t = _squash(text)
    return not any(k in t for k in own)


def contradiction(requested: str, text: str) -> dict | None:
    """A different, distinctive document type is clearly printed on the page
    while the requested type's own phrases are (almost) absent."""
    if not text.strip():
        return None
    s = scores(text)
    mine = s.get(requested, 0)
    others = sorted(((v, k) for k, v in s.items() if k != requested and k in DISTINCTIVE), reverse=True)
    if not others:
        return None
    top_v, top_k = others[0]
    # ID cards print few words, so one decisive phrase is enough for them
    need = 1 if top_k in ("pan", "aadhaar") else 2
    if top_v >= need and top_v >= mine + 2 or (top_v >= need and mine == 0):
        return {"looks_like": top_k, "evidence": top_v, "requested_evidence": mine}
    return None
