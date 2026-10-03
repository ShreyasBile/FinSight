"""A second, character-level read of the PAN number on a PAN card.

The reading model gets the PAN number wrong more often than any other printed field (real-data test,
2026-10-02: 75% right, against 91-95% for names and dates) - one look-alike character in a
ten-character code. OCR reads characters, not meaning, and the PAN format rejects most of its own
misreads. Measured on 110 development cards (api/eval/real/extraction_dev.py pan):

    the model alone                                   84 right
    OCR of the upload as it came, one PAN-shaped token: 78 of 80 right; never wrong where the model was right
    the rule below                                    96 right, none of the model's correct reads changed

Rule, in order:
  1. the OCR text the flow already has (the cleaned image) holds exactly one PAN-shaped token and it is
     the model's read                                 -> confirmed, nothing else runs;
  2. otherwise the upload itself is read by OCR (one more pass, 1-3 s); exactly one PAN-shaped token
                                                      -> that is the number;
  3. otherwise, when the model's read is not PAN-shaped: the cleaned image's single token if there is
     one, else the model's read with look-alike characters put right (O/0, I/1, S/5, B/8, Z/2, G/6
     by what the position requires);
  4. otherwise the model's read stands (validation still checks its format).
Nothing else on the card is touched.
"""
from __future__ import annotations

import re
from typing import Callable

# five letters right before four digits, then one letter: also found when OCR glues the label to it
# ("PermanentAccountNumberABCPE1234F"), because the letters are anchored by the digits that follow
_PAN = re.compile(r"[A-Z]{5}[0-9]{4}[A-Z]")
_PAN_FULL = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def ocr_pan(page_text: str | None) -> str | None:
    """The page's PAN number as OCR read it, or None unless there is exactly one candidate."""
    found = set(_PAN.findall((page_text or "").upper()))
    return next(iter(found)) if len(found) == 1 else None


def lookalike_repair(value: str) -> str:
    """A ten-character read with look-alike characters put right for their position."""
    from ...mask import _TO_DIGIT, _TO_LETTER
    if len(value) != 10:
        return value
    return "".join(_TO_DIGIT.get(ch, ch) if 5 <= i <= 8 and not ch.isdigit()
                   else _TO_LETTER.get(ch, ch) if not 5 <= i <= 8 and ch.isdigit() else ch
                   for i, ch in enumerate(value))


def cross_read_pan(parsed: dict | None, page_text: str | None,
                   read_upload: Callable[[], str | None] | None = None) -> dict | None:
    """Confirm or correct parsed['pan_number'] in place (see the module rule). `read_upload` returns the
    OCR text of the upload as it came; it is called only when step 1 does not confirm the model's read.
    Returns {"pan_number": "confirmed" | "corrected" | "repaired", "by": ...} for the trace, or None when
    the model's read was left as it is."""
    if not isinstance(parsed, dict):
        return None
    got = re.sub(r"[^A-Z0-9]", "", str(parsed.get("pan_number") or "").upper())
    clean = ocr_pan(page_text)
    if clean and clean == got:
        return {"pan_number": "confirmed", "by": "ocr of the cleaned image"}
    upload = None
    if read_upload is not None:
        try:
            upload = ocr_pan(read_upload())
        except Exception:  # noqa: BLE001 - a failed second read leaves the remaining steps
            upload = None
    if upload:
        if upload == got:
            return {"pan_number": "confirmed", "by": "ocr of the upload"}
        parsed["pan_number"] = upload
        return {"pan_number": "corrected", "by": "ocr of the upload"}
    if not _PAN_FULL.match(got):
        if clean:
            parsed["pan_number"] = clean
            return {"pan_number": "corrected", "by": "ocr of the cleaned image"}
        fixed = lookalike_repair(got)
        if fixed != got and _PAN_FULL.match(fixed):
            parsed["pan_number"] = fixed
            return {"pan_number": "repaired", "by": "look-alike characters"}
    return None
