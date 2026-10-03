"""Masked (officer-safe) copies of submitted documents, made AFTER the submission
has returned.

The wizard used to mask every document in the browser before it would send the
application: five OCR passes in parallel on a laptop CPU, 2-3 minutes during which
the borrower watched a "Saving…" spinner. Now the application is written first
(seconds) and the masked copies are made here, one document at a time, on a single
background worker so they never compete with the interactive requests for CPU.

Each job goes through the normal mask flow (so it is traced like any other agent
run), then storage.attach_masked() saves the file and fills in the workbook, and
the document row gets its mask_ms. Everything is best effort: a failed mask only
means the officer sees "masked copy unavailable" for that document.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("finsight.mask_jobs")

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="finsight-mask")
_pending: dict[str, int] = {}


def schedule(app_no: str, doc_key: str, doc_type: str, label: str, data: bytes,
             parsed: dict | None = None, case_id: str | None = None, actor: dict | None = None):
    """Queue one document. doc_key names the stored files (<doc_key>_original.*,
    <doc_key>_masked.jpg); doc_type picks the masking rules."""
    _pending[app_no] = _pending.get(app_no, 0) + 1
    return _pool.submit(_run, app_no, doc_key, doc_type, label, data, parsed or {}, case_id, actor or {})


def pending(app_no: str) -> int:
    """Masked copies still to be made for this application."""
    return _pending.get(app_no, 0)


def _run(app_no, doc_key, doc_type, label, data, parsed, case_id, actor):
    from .agents.flows import document_flows as flows
    from .storage import attach_masked
    t0 = time.perf_counter()
    masked = None
    try:
        out = flows.run_mask(data, parsed, doc_type, case_id, actor)
        masked = (out.get("masked") or {}).get("masked_image")
    except Exception:  # noqa: BLE001 - background work must not raise
        log.exception("masking %s/%s failed", app_no, doc_key)
    finally:
        _pending[app_no] = max(0, _pending.get(app_no, 1) - 1)
    ms = round((time.perf_counter() - t0) * 1000)
    attach_masked(app_no, doc_key, label, masked, ms)
    if masked:
        _record_ms(app_no, doc_key, ms)


def _record_ms(app_no: str, doc_key: str, ms: int):
    from .config import settings
    if not settings.db_enabled:
        return
    try:
        from . import db
        db.execute("""UPDATE d SET mask_ms = ?
                      FROM core.uploaded_documents d
                      JOIN core.loan_applications a ON a.application_id = d.application_id
                      WHERE a.application_no = ? AND d.file_path = ?""",
                   (ms, app_no, f"storage/{app_no}/{doc_key}_original"))
    except Exception:  # noqa: BLE001 - timing is nice to have
        log.warning("could not record mask_ms for %s/%s", app_no, doc_key)
