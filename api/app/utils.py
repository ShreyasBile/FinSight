import base64
import io
import json
import re

from .pdfium_lock import pdfium_serialized


def to_b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def guess_mime(filename: str, content_type: str | None) -> str:
    if content_type and content_type != "application/octet-stream":
        return content_type
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        return "application/pdf"
    if name.endswith(".png"):
        return "image/png"
    if name.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if name.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


@pdfium_serialized
def render_pdf_to_images(pdf_bytes: bytes, max_pages: int = 4, scale: float = 2.0, max_dimension: int = 2000) -> list[bytes]:
    """Rasterize the first `max_pages` PDF pages to PNG bytes (for image-only VLMs).

    Caps the long edge at `max_dimension` px regardless of the PDF's native
    page size. A fixed `scale` alone assumes a ~72-DPI source page; phone-scan
    PDFs commonly embed each page at their camera resolution (page size in
    points == image size in pixels), so scale=2.0 on those produces multi-
    thousand-pixel bitmaps — well past what any vision model needs for legible
    text, and large enough that some vision APIs reject the request outright.
    """
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(pdf_bytes)
    out: list[bytes] = []
    try:
        for i in range(min(len(pdf), max_pages)):
            page = pdf[i]
            w_pt, h_pt = page.get_size()
            page_scale = min(scale, max_dimension / max(w_pt, h_pt))
            bitmap = page.render(scale=page_scale)
            pil = bitmap.to_pil()
            buf = io.BytesIO()
            pil.save(buf, format="PNG")
            out.append(buf.getvalue())
    finally:
        pdf.close()
    return out


_JSON_FENCE = re.compile(r"```(?:json)?|```")


def parse_json_blob(raw: str) -> dict | None:
    """Best-effort JSON extraction from a model response (handles code fences / prose)."""
    if not raw:
        return None
    cleaned = _JSON_FENCE.sub("", raw).strip()
    try:
        val = json.loads(cleaned)
        return val if isinstance(val, dict) else None
    except json.JSONDecodeError:
        pass
    # fall back to the first {...} span
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            val = json.loads(cleaned[start:end + 1])
            return val if isinstance(val, dict) else None
        except json.JSONDecodeError:
            return None
    return None
