"""One process-wide lock for PDFium.

PDFium (pypdfium2) is not thread-safe. The agent flows run a document's
steps in parallel — the page-text / statement-row read beside the model
call that rasterises the same PDF — and two threads inside PDFium at once
crashed the whole process with no Python traceback (measured during the
Document Identity evaluation). Every function that opens a PDF with
pypdfium2 is serialised through this lock; the work inside is milliseconds
to a few hundred ms, so the cost of waiting is small.
"""
from __future__ import annotations

import functools
import threading

PDFIUM_LOCK = threading.RLock()


def pdfium_serialized(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with PDFIUM_LOCK:
            return fn(*args, **kwargs)
    return wrapper
