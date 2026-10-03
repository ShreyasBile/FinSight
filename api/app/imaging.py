"""Shared document-image helpers: load any upload as a page image, and OCR it
into word tokens.

These used to live as private helpers in tampering/locate.py and were imported
from there by masking, face verification and two tampering signals. They are
infrastructure every agent's tool needs, so they live here now;
tampering/locate.py re-exports them for backward compatibility.
"""
from __future__ import annotations

import io
import re

# a money/number group: 1,56,400.00 · 156400 · 720 — captured as printed so it
# can be normalised to a numeric value and matched by VALUE (Indian and Western
# digit grouping differ, so string compare alone is too brittle).
NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def num_value(s: str) -> float | None:
    """Parse a printed number (any grouping, optional decimals) to a value."""
    digits = re.sub(r"[^\d.]", "", s)
    if not digits or digits == ".":
        return None
    try:
        return float(digits)
    except ValueError:
        return None


def norm_alnum(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def load_image(file_bytes: bytes, max_dim: int = 1600):
    """A crisp render of the document (page 1 for PDFs) for OCR + drawing.
    Prefers a PDF's embedded scan (native orientation) over a page raster."""
    from PIL import Image
    if file_bytes[:5] == b"%PDF-":
        from .tampering.signals.pdf_forensics import extract_scan_image
        from .utils import render_pdf_to_images
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
    # Cap the long edge: big enough for OCR to read every figure, small enough
    # to keep one OCR pass a couple of seconds rather than tens.
    w, h = im.size
    if max(w, h) > max_dim:
        s = max_dim / max(w, h)
        im = im.resize((int(w * s), int(h * s)))
    return im


def ocr_tokens(pil_image) -> list[dict]:
    """RapidOCR word boxes: [{text, value, alnum, box:(x0,y0,x1,y1)}]."""
    import numpy as np
    from .preprocess import _get_ocr_reader

    reader = _get_ocr_reader(("en",))
    tokens = []
    for pts, text, _conf in reader.readtext(np.array(pil_image)):
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        tokens.append({
            "text": text,
            "value": num_value(text),
            "alnum": norm_alnum(text),
            "box": (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))),
        })
    return tokens
