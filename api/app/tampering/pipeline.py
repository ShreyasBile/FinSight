"""The cascade orchestrator — the single entry point for analyzing a document.

    from app.tampering.pipeline import analyze_document
    report = analyze_document(file_bytes, "statement.pdf",
                              doc_type="bank_statement", ocr_fields={...})

Flow (mirrors the agreed FinSight integration architecture):

  detect format (magic bytes, never file extension)
        │
  STAGE 1 — cheap deterministic gate, always runs
        images: metadata + business_rules + copy_move
        pdfs:   pdf_forensics + business_rules (+ copy_move on page render)
        │
  gate: every stage-1 score < gate_threshold?  → fast-track LOW, stop
        │ (otherwise)
  STAGE 2 — visual forensics on ESCALATED documents only
        camera photos:       ela + noise + cnn(grad-cam)
        scan-wrapper PDFs:   extract embedded scan image → same as photo
        born-digital PDFs:   noise on rendered page only (no compression
                             history → ELA/CNN would produce noise, not evidence)
        │
  FUSION — weighted combination over available signals → score/tier/explanations

The report is non-blocking by design: it attaches evidence for a human, it
never auto-rejects. Artifacts (heatmaps/overlays) are PIL images on the
returned object for UIs; .to_json() strips them for API/storage use.

Agent layer hooks (keyword-only, behaviour-neutral): an `observer` callback
sees every signal as it completes (recorded as a tool call in the run trace),
and `authenticity_result` injects the Authenticity agent's verdict instead of
re-running the vision check here.
"""
import io
import time
from dataclasses import dataclass, field

from PIL import Image

from app.tampering.config import CFG
from app.tampering.fusion import fuse
from app.tampering.signals import (authenticity, business_rules, cnn_classifier, copy_move,
                                   ela, metadata, noise, patch_cnn, pdf_forensics, photo_region)
from app.tampering.signals.base import SignalResult, unavailable


@dataclass
class AnalysisReport:
    file_name: str
    doc_type: str
    source_format: str          # camera_image | pdf_scan | pdf_digital
    fast_tracked: bool
    fused_score: float
    risk_tier: str
    contributions: dict
    explanations: list
    signals: list = field(default_factory=list)      # SignalResults, with artifacts
    def artifacts(self) -> dict:
        out = {}
        for s in self.signals:
            for label, img in s.artifacts.items():
                out[f"{s.name}:{label}"] = img
        return out

    def to_json(self) -> dict:
        return {
            "file_name": self.file_name,
            "doc_type": self.doc_type,
            "source_format": self.source_format,
            "fast_tracked": self.fast_tracked,
            "fused_score": self.fused_score,
            "risk_tier": self.risk_tier,
            "contributions": self.contributions,
            "explanations": self.explanations,
            "signals": [s.to_dict(include_artifacts=True) for s in self.signals],
        }


def _detect_format(file_bytes: bytes) -> str:
    if file_bytes[:5] == b"%PDF-":
        return "pdf"
    return "image"


def _safe(signal_name: str, fn, *args, observer=None) -> SignalResult:
    """Run one signal; a crash in any single detector must degrade to an
    'unavailable' result (excluded from fusion), never kill the analysis.
    `observer(name, ms, result)` is told about every signal that ran."""
    t0 = time.perf_counter()
    try:
        result = fn(*args)
    except Exception as exc:
        result = unavailable(signal_name, f"signal error: {type(exc).__name__}: {exc}")
    _notify(observer, signal_name, round((time.perf_counter() - t0) * 1000, 1), result)
    return result


def _notify(observer, name, ms, result):
    if observer is None:
        return
    try:
        observer(name, ms, result)
    except Exception:  # noqa: BLE001 - tracing must never affect detection
        pass


def _unreadable_report(file_name: str, doc_type: str, detail: str) -> AnalysisReport:
    """A file we cannot decode can't be cleared OR condemned automatically —
    surface it as MEDIUM/needs-human with an explicit file_integrity finding."""
    integrity = SignalResult(
        "file_integrity", True, 0.5,
        [f"File could not be decoded as an image or PDF ({detail}) — "
         "possibly corrupt, truncated, or a disguised format; needs manual review"])
    return AnalysisReport(
        file_name=file_name, doc_type=doc_type, source_format="unreadable",
        fast_tracked=False, fused_score=0.5, risk_tier="MEDIUM",
        contributions={"file_integrity": {"score": 0.5, "normalized_weight": 1.0,
                                          "contribution": 0.5}},
        explanations=[f"[file_integrity] {integrity.findings[0]}"],
        signals=[integrity])


def authenticity_image(file_bytes: bytes):
    """The exact image the authenticity signal assesses for this file: the
    decoded upload for images; the embedded scan (else a page-1 render) for
    PDFs. None when the file cannot be decoded."""
    if _detect_format(file_bytes) == "image":
        try:
            image = Image.open(io.BytesIO(file_bytes))
            image.load()
            return image.convert("RGB")
        except Exception:
            return None
    scan = pdf_forensics.extract_scan_image(file_bytes)
    if scan is not None:
        return scan
    return next(iter(pdf_forensics.rasterize_first_pages(file_bytes, 1)), None)


def run_authenticity(file_bytes: bytes, doc_type: str = "generic",
                     ocr_fields: dict | None = None, observer=None) -> SignalResult:
    """The authenticity signal on its own — used by the Authenticity agent, whose
    result is then injected into analyze_document(authenticity_result=...)."""
    reason = authenticity.skip_reason(doc_type)
    if reason:  # don't render a page image for a check that won't run
        return unavailable("authenticity", reason)
    return _safe("authenticity", authenticity.run, authenticity_image(file_bytes),
                 doc_type, ocr_fields, observer=observer)


def analyze_document(file_bytes: bytes, file_name: str,
                     doc_type: str = "generic",
                     ocr_fields: dict | None = None,
                     *, observer=None,
                     authenticity_result: SignalResult | None = None) -> AnalysisReport:
    fmt = _detect_format(file_bytes)
    stage1 = _stage1(file_bytes, fmt, doc_type, ocr_fields, observer, authenticity_result)
    if isinstance(stage1, str):
        return _unreadable_report(file_name, doc_type, stage1)
    source_format, forensic_image, signals = stage1

    fast_tracked = _gate(signals, doc_type)
    signals.extend(_stage2(file_bytes, forensic_image, doc_type, fast_tracked, observer))

    fusion = fuse(signals, doc_type)
    return AnalysisReport(
        file_name=file_name,
        doc_type=doc_type,
        source_format=source_format,
        fast_tracked=fast_tracked,
        fused_score=fusion["fused_score"],
        risk_tier=fusion["risk_tier"],
        contributions=fusion["contributions"],
        explanations=fusion["explanations"],
        signals=signals,
    )


def _authenticity(observer, injected, image, doc_type, ocr_fields) -> SignalResult:
    if injected is not None:
        _notify(observer, "authenticity", 0.0, injected)
        return injected
    return _safe("authenticity", authenticity.run, image, doc_type, ocr_fields, observer=observer)


def _stage1(file_bytes, fmt, doc_type, ocr_fields, observer, injected_auth):
    """STAGE 1 — cheap deterministic signals plus the fenced authenticity check.
    Returns (source_format, forensic_image, signals), or the decode error text
    (str) for a file that is not a readable image/PDF."""
    signals: list[SignalResult] = []
    if fmt == "image":
        try:
            image = Image.open(io.BytesIO(file_bytes))
            image.load()                    # force full decode NOW — truncated
            image = image.convert("RGB")    # files fail here, not mid-signal
        except Exception as exc:
            return str(exc) or type(exc).__name__
        forensic_image = image        # stage-2 target, carries real compression history

        signals.append(_safe("metadata", metadata.run, image, observer=observer))
        signals.append(_safe("business_rules", business_rules.run, ocr_fields, doc_type, observer=observer))
        signals.append(_safe("copy_move", copy_move.run, image, doc_type, ocr_fields, observer=observer))
        signals.append(unavailable("pdf_forensics", "not a PDF"))
        # Fabrication check on the image itself — catches a from-scratch fake that
        # the forensic signals (which only detect EDITS) are blind to. Runs before
        # the gate so a fabricated document can't fast-track to LOW. Gets the OCR
        # fields so cited numbers are exact (and verifiable — see _verify_reasons).
        signals.append(_authenticity(observer, injected_auth, image, doc_type, ocr_fields))
        return "camera_image", forensic_image, signals

    source_format = "pdf_digital"
    forensic_image = None
    signals.append(_safe("pdf_forensics", pdf_forensics.run, file_bytes, observer=observer))
    signals.append(_safe("business_rules", business_rules.run, ocr_fields, doc_type, observer=observer))

    # scan-wrapper PDF? extract the embedded image — it has a real
    # compression history and can take the full photo branch in stage 2
    scan = pdf_forensics.extract_scan_image(file_bytes)
    if scan is not None:
        source_format = "pdf_scan"
        forensic_image = scan
        signals.append(_safe("copy_move", copy_move.run, scan, doc_type, ocr_fields, observer=observer))
    else:
        # BORN-DIGITAL PDF (text/vector, e.g. a computer-generated pay slip):
        # copy_move is meaningless here and skipped, exactly like ELA/CNN.
        # Its whole premise — pixel-identical regions are suspicious — only
        # holds for photos/scans, where sensor+compression noise makes
        # genuine duplication rare. A rendered digital page repeats every
        # label/header/symbol ("AMOUNT" x2, the currency sign, table cells)
        # PIXEL-IDENTICALLY by construction, so copy_move fires on every
        # structured document. Born-digital forgeries are caught by
        # pdf_forensics (producer, re-saves) and business_rules instead.
        signals.append(unavailable(
            "copy_move",
            "born-digital PDF — rendered text/labels repeat pixel-identically "
            "by construction, so duplication detection is not meaningful"))
    signals.append(unavailable("metadata", "EXIF does not apply to PDFs (see pdf_forensics)"))
    # Fabrication check on a rendered page (or the embedded scan) — the layer
    # that catches an AI/template-generated PDF that is internally consistent
    # and carries no forensic edit traces. Before the gate, like the image branch.
    auth_preview = None
    if injected_auth is None and authenticity.skip_reason(doc_type) is None:
        auth_preview = scan if scan is not None else next(
            iter(pdf_forensics.rasterize_first_pages(file_bytes, 1)), None)
    signals.append(_authenticity(observer, injected_auth, auth_preview, doc_type, ocr_fields))
    return source_format, forensic_image, signals


def _gate(signals: list[SignalResult], doc_type: str) -> bool:
    """Fast-tracking requires AFFIRMATIVE evidence of cleanliness, not merely an
    absence of evidence: business rules must have actually run and passed
    (a document with no OCR fields at all tells us nothing) and every
    stage-1 signal that ran must be quiet. In FinSight, OCR always runs
    before this pipeline, so legitimate documents still hit the fast path.

    ID documents (see config: pipeline.always_escalate_doc_types) never
    fast-track — always escalate to Stage 2, regardless of the Stage-1
    score. See the config.yaml comment for why: this doc-type family has
    the thinnest Stage-1 coverage of any document, so "found nothing" is a
    weaker signal here than elsewhere."""
    stage1_scores = [s.score for s in signals if s.available]
    business = next((s for s in signals if s.name == "business_rules"), None)
    rules_affirmed = business is not None and business.available and business.score == 0.0
    always_escalate = doc_type in CFG.pipeline.always_escalate_doc_types
    return bool(
        not always_escalate
        and stage1_scores
        and max(stage1_scores) < CFG.pipeline.gate_threshold
        and rules_affirmed
    )


def _stage2(file_bytes, forensic_image, doc_type, fast_tracked, observer) -> list[SignalResult]:
    """STAGE 2 — visual forensics on escalated documents only."""
    signals: list[SignalResult] = []
    if fast_tracked:
        for name in ("ela", "noise", "cnn", "patch_cnn", "photo_region"):
            signals.append(unavailable(name, "fast-tracked: stage-1 gate found no evidence"))
        return signals
    if forensic_image is not None:
        ela_result = _safe("ela", ela.run, forensic_image, observer=observer)
        signals.append(ela_result)
        signals.append(_safe("noise", noise.run, forensic_image, observer=observer))
        ela_map = ela_result.artifacts.get("ela_map")
        if ela_map is not None:
            signals.append(_safe("cnn", cnn_classifier.run, ela_map, forensic_image, observer=observer))
        else:
            signals.append(unavailable("cnn", "no ELA map produced"))
        # Patch detector trained on REAL forged receipts / ID cards; names the region it suspects
        signals.append(_safe("patch_cnn", patch_cnn.run, forensic_image, doc_type, observer=observer))
        # Targeted photo-vs-rest-of-card check (ID docs only) — catches a
        # pasted-in photo that ela/noise's whole-page analysis misses or
        # deliberately excludes (see photo_region.py docstring).
        signals.append(_safe("photo_region", photo_region.run, forensic_image, doc_type, observer=observer))
    else:
        # born-digital PDF: rendered page has no compression history;
        # only noise inconsistency is meaningful (embedded raster content)
        pages = pdf_forensics.rasterize_first_pages(file_bytes, 1)
        if pages:
            signals.append(_safe("noise", noise.run, pages[0], observer=observer))
        signals.append(unavailable(
            "ela", "born-digital PDF — rendered pages carry no JPEG compression history"))
        signals.append(unavailable(
            "cnn", "born-digital PDF — CNN operates on ELA maps of real compressed images"))
        signals.append(unavailable(
            "patch_cnn", "born-digital PDF — the patch detector was trained on scans and photos"))
    return signals
