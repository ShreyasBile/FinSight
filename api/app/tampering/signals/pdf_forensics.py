"""Stage-1 signal: PDF structure forensics (PDFs only).

A born-digital PDF has no JPEG compression history, so ELA/CNN forensics are
meaningless on it — but the FILE STRUCTURE testifies instead:

- Incremental updates: PDF appends edits after the original %%EOF instead of
  rewriting the file. More than one %%EOF/startxref means the document was
  saved again by some tool AFTER it was created. Bank-generated statements
  are written once.
- Producer/Creator strings: banks and payroll systems emit their generator
  names; "iLovePDF" or "Photoshop" in a salary slip's Producer is a red flag.
- CreationDate vs ModDate mismatch: document modified after creation.

Works on raw bytes — no full PDF parser dependency needed.
"""
import re

import pypdfium2 as pdfium

from ...pdfium_lock import pdfium_serialized

from app.tampering.config import CFG
from app.tampering.signals.base import SignalResult, unavailable

_C = CFG.signals.pdf_forensics

# Developer / scripting PDF libraries. Legitimate for building web apps, but a
# RED FLAG as the Producer of a document that should come from an institution:
# a real employer's payroll system or a bank's statement generator does not emit
# PDFs via jsPDF (a browser library) or reportlab (a Python script). Fabricated
# salary-slip / bank-statement generators are built on exactly these tools.
_GENERATION_LIBRARIES = [
    "jspdf", "pdfkit", "reportlab", "fpdf", "tcpdf", "dompdf", "mpdf",
    "wkhtmltopdf", "pdfmake", "weasyprint", "prince", "phantomjs",
    "puppeteer", "html2pdf",
    # additional scripting/library producers seen on fabricated documents
    "pdf-lib", "itext", "itextsharp", "pdfsharp", "migradoc", "aspose",
    "openpdf", "pdfbox", "flying saucer", "openhtmltopdf", "gotenberg",
    "chromium", "headless chrome", "skia/pdf", "cairo", "quill", "jasperreports",
]

# PDF UTILITIES — unlock / compress / merge / split tools. A statement passing
# through one of these is COMMON AND USUALLY LEGITIMATE: banks email
# password-protected statements and borrowers unlock them (iLovePDF, Smallpdf…)
# before uploading. Treat as a WEAK re-save note, NOT the strong "editing tool"
# score, so a genuine unlocked statement isn't flagged like a doctored one.
_PDF_UTILITIES = [
    "ilovepdf", "smallpdf", "sejda", "pdfescape", "pdf24", "pdf2go",
    "soda pdf", "qpdf", "ghostscript", "pdftk", "pdf candy", "cleverpdf",
]


@pdfium_serialized
def run(pdf_bytes: bytes) -> SignalResult:
    findings = []
    score = 0.0

    # --- incremental update detection on raw bytes ---
    eof_count = pdf_bytes.count(b"%%EOF")
    startxref_count = pdf_bytes.count(b"startxref")
    revisions = max(eof_count, startxref_count)
    if revisions > 1:
        findings.append(
            f"{revisions} save generations detected ({eof_count}×%%EOF) — the file was "
            "modified and re-saved after it was first created")
        score = max(score, _C.score_incremental_update)

    # --- metadata via pdfium ---
    creation = modification = producer = creator = None
    try:
        # NB: no explicit .close() anywhere in this module — pypdfium2 v4
        # finalizes objects in the right order via GC; closing a document
        # before its child pages/bitmaps triggers shutdown warnings.
        pdf = pdfium.PdfDocument(pdf_bytes)
        meta_keys = {"Producer": None, "Creator": None, "CreationDate": None, "ModDate": None}
        for key in meta_keys:
            try:
                meta_keys[key] = pdf.get_metadata_value(key)
            except Exception:
                meta_keys[key] = None
        producer, creator = meta_keys["Producer"], meta_keys["Creator"]
        creation, modification = meta_keys["CreationDate"], meta_keys["ModDate"]
    except Exception as exc:
        # metadata failure shouldn't kill the byte-level findings
        findings.append(f"(could not parse PDF metadata: {exc})")

    gen_score = getattr(_C, "score_generation_library", 0.62)
    util_score = getattr(_C, "score_utility_resave", 0.30)
    for label, value in (("Producer", producer), ("Creator", creator)):
        if not value:
            continue
        low = str(value).lower()
        utility = next((u for u in _PDF_UTILITIES if u in low), None)
        editor = next((e for e in CFG.signals.metadata.editor_software if e in low), None)
        gen_lib = next((g for g in _GENERATION_LIBRARIES if g in low), None)
        # utilities checked FIRST — many (iLovePDF, Smallpdf) also appear in the
        # editor list, but a re-save/unlock is far weaker evidence than an edit
        if utility:
            findings.append(
                f"PDF {label} shows the file was re-processed by '{value}' after the "
                "bank/employer issued it. That is often just unlocking or compressing "
                "the file — but the same tool can also edit its contents, and the "
                "file's data cannot tell which happened, so the figures should be "
                "verified against the original")
            score = max(score, util_score)
        elif editor:
            findings.append(f"PDF {label} names an editing tool: '{value}'")
            score = max(score, _C.score_editor_producer)
        elif gen_lib:
            findings.append(
                f"PDF {label} is a document-generation library ('{value}') — genuine "
                "documents from an employer or bank are produced by their own systems, "
                "not a script or browser library; this is a common trait of fabricated "
                "salary slips and statements")
            score = max(score, gen_score)
        else:
            findings.append(f"PDF {label}: '{value}'")

    if creation and modification and creation != modification:
        findings.append(f"Modified after creation: CreationDate='{creation}' vs ModDate='{modification}'")
        score = max(score, _C.score_date_mismatch)

    if not findings:
        findings.append("Single save generation, no editing traces in structure or metadata")

    return SignalResult("pdf_forensics", True, score, findings)


@pdfium_serialized
def extract_scan_image(pdf_bytes: bytes) -> "Image.Image | None":
    """If the PDF is a scan wrapper (one page-covering embedded image), pull the
    ORIGINAL embedded image out so the camera-photo forensics branch (ELA/CNN)
    can run on real compression history instead of a freshly rendered page."""
    from PIL import Image  # local import keeps module load light
    import pypdfium2.raw as pdfium_c

    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
        page = pdf[0]
        page_w, page_h = page.get_size()
        images = [o for o in page.get_objects()
                  if o.type == pdfium_c.FPDF_PAGEOBJ_IMAGE]
        if len(images) != 1:
            return None
        obj = images[0]
        l, b, r, t = obj.get_pos()
        coverage = ((r - l) * (t - b)) / (page_w * page_h)
        if coverage < 0.8:
            return None  # small decorative image, not a full-page scan
        bitmap = obj.get_bitmap(render=False)
        return bitmap.to_pil().convert("RGB")
    except Exception:
        return None


@pdfium_serialized
def rasterize_first_pages(pdf_bytes: bytes, max_pages: int, max_dim: int = 2000):
    """Fallback when there's no extractable scan image: render pages to PIL.
    Same size-capping logic as FinSight's OCR backend."""
    from PIL import Image  # noqa: F401
    pages = []
    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
        for i in range(min(len(pdf), max_pages)):
            page = pdf[i]
            w_pt, h_pt = page.get_size()
            scale = min(2.0, max_dim / max(w_pt, h_pt))
            bitmap = page.render(scale=scale)
            pages.append(bitmap.to_pil().convert("RGB"))
    except Exception:
        pass  # unreadable/corrupt PDF -> no pages; callers handle empty list
    return pages
