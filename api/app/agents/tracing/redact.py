"""Keep traces PII-free.

Traces are written to disk and to SQL and shown on dashboards, so they carry
only tiers, scores, counts, field NAMES, digests and timings — never field
values, names, ID numbers or images."""
from __future__ import annotations

import hashlib
import re

_DATA_URI = re.compile(r"^data:[\w/+.-]+;base64,")
_PII_TEXT = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b|\b\d{4}\s?\d{4}\s?\d{4}\b|\b\d{9,18}\b")


def digest(data: bytes | str | None, n: int = 12) -> str | None:
    if data is None:
        return None
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:n]


def scrub_text(s: str, limit: int = 300) -> str:
    """Mask ID-number-shaped substrings in free text (findings, summaries)."""
    if not isinstance(s, str):
        return s
    s = _PII_TEXT.sub(lambda m: "X" * max(0, len(m.group(0)) - 4) + m.group(0)[-4:], s)
    return s[:limit]


def field_names(parsed: dict | None) -> list[str]:
    return sorted((parsed or {}).keys())


def safe_summary(obj, depth: int = 0):
    """Recursively drop images and long strings; mask ID numbers."""
    if depth > 4:
        return "…"
    if isinstance(obj, dict):
        return {k: safe_summary(v, depth + 1) for k, v in list(obj.items())[:40]
                if k not in ("image", "masked_image", "annotated_image", "cleaned_preview",
                             "document_preview", "thumb", "heatmaps", "raw", "_embeddings")}
    if isinstance(obj, list):
        return [safe_summary(v, depth + 1) for v in obj[:20]]
    if isinstance(obj, str):
        if _DATA_URI.match(obj):
            return "<image>"
        return scrub_text(obj, 240)
    return obj
