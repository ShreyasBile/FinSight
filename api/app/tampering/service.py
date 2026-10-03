"""Thin adapter between FinSight's /extract endpoint and the tampering pipeline.

Keeps the FastAPI layer clean: one call in, one JSON-ready dict out, and every
failure isolated so tampering can NEVER break OCR extraction.

IMPORTANT: pass the ORIGINAL uploaded bytes, not FinSight's preprocessed image.
Preprocessing (deskew / perspective-warp / IQA) rewrites the pixels and destroys
the JPEG compression history and sensor-noise floor that ELA and the noise signal
read — running forensics on a cleaned image measures the cleaner, not the forger.
"""
import base64
import io
import logging

log = logging.getLogger("finsight.tampering")

# tier -> whether it should force human review of the document
_REVIEW_TIERS = {"MEDIUM", "HIGH"}

# Which signals produce a heatmap worth showing a human, and how much to trust
# it as "here is exactly what's wrong". copy_move's overlay marks the literal
# duplicated regions (precise, deterministic); the CNN/ELA/noise maps are the
# WEAK signals — their heatmaps show "areas the model found unusual" and can be
# off, so they're labelled as supporting, not proof. Ordered by trust: the
# frontend shows the first available one as the primary visual.
_ARTIFACT_TRUST = [
    ("copy_move", "match_overlay", "precise",
     "The highlighted regions are duplicated — the same content appears in two places."),
    ("cnn", "tamper_localization", "supporting",
     "Warm colours mark areas the model found unusual. This is a supporting hint, not proof."),
    ("ela", "ela_map", "supporting",
     "Brighter areas compress differently from the rest of the page."),
    ("noise", "noise_map", "supporting",
     "Highlighted blocks have a different noise texture from the rest of the page."),
]


def _encode_artifact(img, max_dim: int = 520) -> str | None:
    """Downscale a PIL heatmap and return a base64 PNG data URI (keeps the API
    payload small — full-page overlays would be hundreds of KB each)."""
    try:
        im = img.convert("RGB")
        w, h = im.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            im = im.resize((int(w * scale), int(h * scale)))
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001 - a heatmap that won't encode is not fatal
        log.exception("could not encode tampering heatmap")
        return None


def _document_preview(file_bytes: bytes, max_dim: int = 900) -> str | None:
    """A clean (no-overlay) downscaled render of the document, so the frontend
    can show a real hover-preview even for PDFs (the browser can't render a PDF
    page itself). Images already have a client-side object URL, but returning
    this for every analysed doc keeps the frontend logic uniform."""
    from PIL import Image
    try:
        if file_bytes[:5] == b"%PDF-":
            # prefer the embedded scan image (its NATIVE orientation — a
            # landscape card scanned into a portrait PDF page renders sideways
            # if we rasterize the page instead); fall back to page render
            from .signals.pdf_forensics import extract_scan_image
            from ..utils import render_pdf_to_images
            scan = extract_scan_image(file_bytes)
            if scan is not None:
                im = scan
            else:
                pages = render_pdf_to_images(file_bytes, max_pages=1)
                if not pages:
                    return None
                im = Image.open(io.BytesIO(pages[0]))
        else:
            im = Image.open(io.BytesIO(file_bytes))
        im = im.convert("RGB")
        w, h = im.size
        if max(w, h) > max_dim:
            s = max_dim / max(w, h)
            im = im.resize((int(w * s), int(h * s)))
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=82)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001 - a preview that won't render is not fatal
        return None


def _collect_heatmaps(report) -> list[dict]:
    """Encode the show-worthy heatmaps for a flagged document, most-trusted first.
    Only includes a signal's heatmap if that signal actually fired (score >= 0.2),
    so we never show a heatmap for a signal that found nothing."""
    by_name = {s.name: s for s in report.signals}
    out = []
    for sig_name, label, trust, caption in _ARTIFACT_TRUST:
        s = by_name.get(sig_name)
        if not s or not s.available or s.score < 0.2:
            continue
        img = s.artifacts.get(label)
        if img is None:
            continue
        uri = _encode_artifact(img)
        if uri:
            out.append({"signal": sig_name, "trust": trust, "caption": caption, "image": uri})
    return out


def analyze(file_bytes: bytes, filename: str, doc_type: str,
            ocr_fields: dict | None, *, observer=None,
            authenticity_result=None, include_internals: bool = False) -> dict | None:
    """Run the tampering cascade; return a compact dict for the API response,
    or None if analysis could not run (never raises).

    Agent-layer extras: `observer` sees each signal as it runs;
    `authenticity_result` injects the Authenticity agent's verdict;
    `include_internals` adds "_internals" (fusion contributions) for the fraud
    fusion agent — the caller strips it before responding to a client."""
    try:
        # imported lazily so a torch/cv2 import problem can't stop the OCR
        # service from booting — tampering just reports unavailable instead
        from app.tampering.pipeline import analyze_document
        report = analyze_document(file_bytes, filename, doc_type=doc_type,
                                  ocr_fields=ocr_fields, observer=observer,
                                  authenticity_result=authenticity_result)
    except Exception:
        log.exception("tampering analysis failed — returning null")
        return None

    signals = []
    for s in report.signals:
        signals.append({
            "name": s.name,
            "available": s.available,
            "score": round(float(s.score), 3) if s.available else None,
            "findings": list(s.findings) if s.available else [],
            "skip_reason": s.skip_reason,
        })

    # heatmaps only for flagged docs (a clean doc has nothing to show, and the
    # base64 payload isn't free)
    heatmaps = _collect_heatmaps(report) if report.risk_tier in _REVIEW_TIERS else []

    result = {
        "risk_tier": report.risk_tier,           # LOW | MEDIUM | HIGH
        "risk_score": report.fused_score,        # 0..1
        "fast_tracked": report.fast_tracked,     # true = cleared at stage 1
        "source_format": report.source_format,   # camera_image | pdf_scan | ...
        "needs_review": report.risk_tier in _REVIEW_TIERS,
        "explanations": report.explanations,
        "signals": signals,
        "heatmaps": heatmaps,                    # [{signal, trust, caption, image(dataURI)}]
        "document_preview": _document_preview(file_bytes),  # clean render for hover-preview
    }
    if include_internals:
        result["_internals"] = {"fused_score": report.fused_score,
                                "contributions": report.contributions}
    return result
