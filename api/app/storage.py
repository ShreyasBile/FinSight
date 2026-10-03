"""Application audit store: persists every processed application to disk and to
a single Excel workbook, with a role-separated view of the data.

Layout (all under <repo>/storage/):
    storage/
      applications.xlsx           two sheets — "Manager" (full PII) and
                                  "Officer" (PII masked, e.g. XXXX XXXX 1234)
      <APP-ID>/
        pan_original.png          the borrower's uploaded file, untouched
        pan_masked.jpg            the PII-redacted render (officer-safe)
        aadhaar_original.jpg
        aadhaar_masked.jpg
        ...

Each document contributes one row per sheet. The Manager sheet shows the full
extracted values and links the ORIGINAL file; the Officer sheet shows the same
row with sensitive identifiers masked and links the MASKED file — so an officer
never sees an unredacted Aadhaar/PAN/account number, on screen or on disk-link.

Every row also carries the full processing audit trail: when the document was
uploaded and how long each stage took (preprocessing, extraction, masking,
tampering detection, face matching), plus the tampering verdict and the
application-level face-match result.

Deterministic, local, no network. Never raises to the caller — a storage
failure must not fail a submission (returns {"ok": False, "error": ...}).
"""
import base64
import io
import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

log = logging.getLogger("finsight.storage")

STORAGE_DIR = Path(__file__).resolve().parents[2] / "storage"
XLSX_PATH = STORAGE_DIR / "applications.xlsx"

MASK_PENDING = "(masked copy being prepared)"
MASK_UNAVAILABLE = "(masked render unavailable)"

# One writer at a time — openpyxl load→append→save is read-modify-write and two
# concurrent submissions would clobber each other.
_lock = threading.Lock()

# Field-key substrings whose VALUES are masked in the Officer view — ONLY the
# PAN number and the Aadhaar number (last 4 kept), matching the image masker.
# Every other field, including account numbers, DOB, names and addresses, stays
# visible: an officer needs them, and the ask is to redact the main PII only.
_MASK_KEY_TOKENS = ("aadhaar", "aadhar", "pan")

_HEADERS = [
    "Application ID", "Applicant", "Loan Type", "Submitted At",
    "Document", "File Name", "Uploaded At",
    "Extracted Fields",
    "Tampering Risk", "Tampering Findings",
    "Face Match",
    "Preprocess (s)", "Extraction (s)", "Masking (s)", "Tampering (s)", "Face (s)",
    "Stored File",
]
_HEADER_FILL = PatternFill("solid", fgColor="1F2937")
_HEADER_FONT = Font(color="FFFFFF", bold=True)
_WRAP = Alignment(wrap_text=True, vertical="top")


def _mask_idnum(s: str) -> str:
    """Keep the last 4 alphanumeric characters, replace every earlier one with
    'X', preserving spaces/dashes: '1234 5678 9012' -> 'XXXX XXXX 9012',
    'ABCDE1234F' -> 'XXXXXX234F'. Already-masked values pass through unchanged."""
    s = str(s)
    alnum_idx = [i for i, c in enumerate(s) if c.isalnum()]
    keep = set(alnum_idx[-4:])
    return "".join("X" if (c.isalnum() and i not in keep) else c
                   for i, c in enumerate(s))


def _mask_field(key: str, value) -> str:
    kl = str(key).lower()
    if any(t in kl for t in _MASK_KEY_TOKENS):
        return _mask_idnum(value)
    return str(value)


def _flatten_fields(fields: dict | None) -> dict:
    """OCR fields may be {k: v} or {k: {'value': v, ...}} — flatten to {k: v}."""
    out = {}
    for k, v in (fields or {}).items():
        if isinstance(v, dict) and "value" in v:
            v = v["value"]
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        out[k] = v
    return out


def _fields_cell(fields: dict, mask: bool) -> str:
    """Render extracted fields as a readable multi-line cell, masking the
    sensitive identifiers when `mask` is True (the Officer view)."""
    lines = []
    for k, v in fields.items():
        label = k.replace("_", " ").title()
        val = _mask_field(k, v) if mask else str(v)
        lines.append(f"{label}: {val}")
    return "\n".join(lines)


def _tampering_cells(t: dict | None) -> tuple[str, str]:
    if not t:
        return "Not analysed", ""
    tier = t.get("risk_tier", "?")
    score = t.get("risk_score", t.get("fused_score"))
    risk = f"{tier}" + (f" ({float(score):.2f})" if score is not None else "")
    findings = [e for e in (t.get("explanations") or [])
                if not any(sk in e for sk in ("skipped:", "Risk driven", "reads as a genuine"))]
    return risk, "\n".join(f"• {re.sub(r'^\[[^\]]+\]\s*', '', f)}" for f in findings[:6])


def _decode_data(b64: str) -> bytes | None:
    if not b64:
        return None
    if "," in b64 and b64.strip().startswith("data:"):
        b64 = b64.split(",", 1)[1]
    try:
        return base64.b64decode(b64)
    except Exception:  # noqa: BLE001
        return None


def _ext_for(mime: str, default: str = "png") -> str:
    m = (mime or "").lower()
    if "jpeg" in m or "jpg" in m:
        return "jpg"
    if "png" in m:
        return "png"
    if "pdf" in m:
        return "pdf"
    return default


def _style_sheet(ws, widths):
    ws.freeze_panes = "A2"
    for c, cell in enumerate(ws[1], start=1):
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = _WRAP
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _load_or_create():
    if XLSX_PATH.exists():
        try:
            return load_workbook(XLSX_PATH)
        except Exception:  # noqa: BLE001 - corrupt/locked -> start fresh, don't lose the submission
            log.exception("applications.xlsx unreadable — recreating")
    wb = Workbook()
    wb.remove(wb.active)
    widths = [16, 20, 16, 20, 16, 22, 20, 46, 16, 46, 26, 13, 13, 12, 13, 10, 40]
    for name in ("Manager", "Officer"):
        ws = wb.create_sheet(name)
        ws.append(_HEADERS)
        _style_sheet(ws, widths)
    return wb


def _secs(timings: dict, key: str) -> str:
    v = (timings or {}).get(key)
    if v is None:
        return ""
    try:
        return f"{float(v) / 1000.0:.1f}"
    except (TypeError, ValueError):
        return ""


def store_application(record: dict) -> dict:
    """Persist one application. `record`:
        {app_id, applicant_name, loan_type, submitted_at,
         face_check: {overall, summary, time_ms},
         documents: [{doc_type, label, file_name, mime, uploaded_at,
                      fields, original_b64, masked_b64,
                      timings: {preprocess_ms, extract_ms, mask_ms, tampering_ms},
                      tampering: {...}}]}
    Returns {"ok": True, "dir": <path>, "xlsx": <path>, "rows": n} or
    {"ok": False, "error": ...}. Never raises."""
    try:
        app_id = str(record.get("app_id") or f"APP-{datetime.now():%Y%m%d%H%M%S}")
        app_id = re.sub(r"[^A-Za-z0-9._-]", "_", app_id)
        applicant = str(record.get("applicant_name") or "")
        loan_type = str(record.get("loan_type") or "")
        submitted = str(record.get("submitted_at") or datetime.now().isoformat(timespec="seconds"))
        face = record.get("face_check") or {}
        face_overall = str(face.get("overall") or "not run")
        face_summary = str(face.get("summary") or "")
        face_cell = f"{face_overall}\n{face_summary}".strip()
        face_secs = _secs({"t": face.get("time_ms")}, "t")

        with _lock:
            STORAGE_DIR.mkdir(parents=True, exist_ok=True)
            app_dir = STORAGE_DIR / app_id
            app_dir.mkdir(parents=True, exist_ok=True)

            wb = _load_or_create()
            ws_mgr, ws_off = wb["Manager"], wb["Officer"]
            rows = 0

            for d in record.get("documents", []):
                doc_type = str(d.get("doc_type") or "document")
                label = str(d.get("label") or doc_type)
                fields = _flatten_fields(d.get("fields"))
                timings = d.get("timings") or {}
                risk_cell, findings_cell = _tampering_cells(d.get("tampering"))

                # save the original + masked files
                orig_bytes = _decode_data(d.get("original_b64"))
                mask_bytes = _decode_data(d.get("masked_b64"))
                orig_rel = mask_rel = ""
                if orig_bytes:
                    fn = f"{doc_type}_original.{_ext_for(d.get('mime'))}"
                    (app_dir / fn).write_bytes(orig_bytes)
                    orig_rel = f"{app_id}/{fn}"
                if mask_bytes:
                    fn = f"{doc_type}_masked.jpg"
                    (app_dir / fn).write_bytes(mask_bytes)
                    mask_rel = f"{app_id}/{fn}"

                common = [app_id, applicant, loan_type, submitted,
                          label, str(d.get("file_name") or ""), str(d.get("uploaded_at") or ""),
                          None,  # fields cell filled per-sheet below
                          risk_cell, findings_cell, face_cell,
                          _secs(timings, "preprocess_ms"), _secs(timings, "extract_ms"),
                          _secs(timings, "mask_ms"), _secs(timings, "tampering_ms"), face_secs,
                          None]  # stored file per-sheet

                mgr = list(common)
                mgr[7] = _fields_cell(fields, mask=False)
                mgr[16] = orig_rel or mask_rel
                ws_mgr.append(mgr)

                off = list(common)
                off[7] = _fields_cell(fields, mask=True)
                # no masked render yet: the API makes one in the background (mask_jobs.py)
                # and attach_masked() then fills this cell in
                off[16] = mask_rel or (MASK_PENDING if orig_bytes else MASK_UNAVAILABLE)
                ws_off.append(off)

                for ws in (ws_mgr, ws_off):
                    for cell in ws[ws.max_row]:
                        cell.alignment = _WRAP
                rows += 1

            wb.save(XLSX_PATH)

        return {"ok": True, "dir": str(app_dir), "xlsx": str(XLSX_PATH), "rows": rows}
    except Exception as exc:  # noqa: BLE001 - storage must never fail a submission
        log.exception("store_application failed")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def attach_masked(app_id: str, doc_key: str, label: str, masked_b64: str | None,
                  mask_ms: float | None = None) -> dict:
    """Record a masked render made after the submission returned (mask_jobs.py):
    save <doc_key>_masked.jpg and point both sheets' rows for that document at it.
    masked_b64=None records that masking failed. Never raises."""
    try:
        app_id = re.sub(r"[^A-Za-z0-9._-]", "_", str(app_id))
        data = _decode_data(masked_b64) if masked_b64 else None
        with _lock:
            rel = MASK_UNAVAILABLE
            if data:
                app_dir = STORAGE_DIR / app_id
                app_dir.mkdir(parents=True, exist_ok=True)
                fn = f"{doc_key}_masked.jpg"
                (app_dir / fn).write_bytes(data)
                rel = f"{app_id}/{fn}"
            if XLSX_PATH.exists():
                wb = _load_or_create()
                for name in ("Manager", "Officer"):
                    ws = wb[name]
                    for row in ws.iter_rows(min_row=2):
                        if row[0].value != app_id or row[4].value != label:
                            continue
                        if name == "Officer" and row[16].value in (MASK_PENDING, MASK_UNAVAILABLE, None, ""):
                            row[16].value = rel
                        elif name == "Manager" and not row[16].value and data:
                            row[16].value = rel
                        if data and mask_ms is not None and not row[13].value:
                            row[13].value = _secs({"m": mask_ms}, "m")
                wb.save(XLSX_PATH)
        return {"ok": bool(data), "path": rel}
    except Exception as exc:  # noqa: BLE001 - never fail the caller
        log.exception("attach_masked failed")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
