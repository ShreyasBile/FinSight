"""Stage-1 signal: file metadata forensics (images).

Cheapest possible evidence: if the file itself says "I was last touched by
Photoshop", no CNN is needed. Reads EXIF from JPEG/PNG via Pillow, AND the
XMP metadata block (a separate standard from classic EXIF) that PNG exports
from web design tools — Canva, Figma, Photopea — routinely carry in an
`xmp:CreatorTool` field even when they carry no classic EXIF Software tag at
all. A design-tool export with no camera EXIF is exactly the profile of a
from-scratch fabrication, not a photographed document, so this is checked
even when EXIF itself is absent.

Scoring philosophy: individual observations each carry a configured score and
the signal reports the MAX (strongest single piece of evidence), not the sum —
three weak hints don't add up to proof, but one editor tag is damning.
Missing EXIF entirely is deliberately scored low: WhatsApp, Telegram and most
upload pipelines strip EXIF from perfectly genuine photos.
"""
import re

from PIL import Image
from PIL.ExifTags import TAGS

from app.tampering.config import CFG
from app.tampering.signals.base import SignalResult, unavailable

_C = CFG.signals.metadata


def _xmp_editor_hit(image: Image.Image) -> str | None:
    """Search the raw XMP block (however Pillow exposed it — JPEG uses the
    'xmp' info key, PNG uses 'XML:com.adobe.xmp') for a known editor name.
    Returns the matched keyword, or None."""
    xmp = image.info.get("xmp") or image.info.get("XML:com.adobe.xmp")
    if not xmp:
        return None
    if isinstance(xmp, bytes):
        xmp = xmp.decode("utf-8", errors="ignore")
    xmp_lower = xmp.lower()
    for editor in _C.editor_software:
        if editor in xmp_lower:
            return editor
    return None


def _xmp_creator_tool(image: Image.Image) -> str | None:
    """Pull the human-readable xmp:CreatorTool value for the finding text,
    e.g. 'Canva doc=... user=...'. Best-effort regex — this is for display,
    the actual detection above is a plain substring search."""
    xmp = image.info.get("xmp") or image.info.get("XML:com.adobe.xmp")
    if not xmp:
        return None
    if isinstance(xmp, bytes):
        xmp = xmp.decode("utf-8", errors="ignore")
    m = re.search(r"<xmp:CreatorTool>(.*?)</xmp:CreatorTool>", xmp, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else None


def run(image: Image.Image) -> SignalResult:
    try:
        raw_exif = image.getexif()
    except Exception as exc:
        return unavailable("metadata", f"could not read EXIF: {exc}")

    findings = []
    score = 0.0

    # XMP check runs regardless of whether classic EXIF exists — a design-tool
    # PNG export commonly has ZERO classic EXIF but a damning XMP CreatorTool.
    xmp_hit = _xmp_editor_hit(image)
    if xmp_hit:
        creator_tool = _xmp_creator_tool(image)
        detail = f" ('{creator_tool}')" if creator_tool else f" ('{xmp_hit}')"
        findings.append(f"XMP metadata names an editing/design tool{detail}")
        score = max(score, _C.score_editor_found)

    if not raw_exif or len(raw_exif) == 0:
        if not findings:
            findings.append(
                "No EXIF metadata at all (weak signal — messaging apps strip EXIF from genuine photos too)")
            score = max(score, _C.score_missing_exif)
        return SignalResult("metadata", True, score, findings)

    tags = {}
    for tag_id, value in raw_exif.items():
        tag_name = TAGS.get(tag_id, str(tag_id))
        tags[tag_name] = value

    software = str(tags.get("Software", "")).lower()
    if software:
        for editor in _C.editor_software:
            if editor in software:
                findings.append(f"EXIF Software tag names an editing tool: '{tags['Software']}'")
                score = max(score, _C.score_editor_found)
                break
        else:
            findings.append(f"EXIF Software: '{tags['Software']}' (not a known editor)")

    # DateTime (last modified) vs DateTimeOriginal (capture time). A photo
    # edited after capture gets a newer DateTime.
    dt = tags.get("DateTime")
    ifd_exif = None
    try:
        ifd_exif = raw_exif.get_ifd(0x8769)  # Exif SubIFD holds DateTimeOriginal
    except Exception:
        pass
    dt_original = None
    if ifd_exif:
        dt_original = ifd_exif.get(0x9003)  # DateTimeOriginal
    if dt and dt_original and str(dt) != str(dt_original):
        findings.append(
            f"Modified after capture: DateTime='{dt}' differs from DateTimeOriginal='{dt_original}'")
        score = max(score, _C.score_date_mismatch)

    if not findings:
        findings.append("EXIF present, no editing traces found")

    return SignalResult("metadata", True, score, findings)
