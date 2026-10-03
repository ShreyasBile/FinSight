"""Per-application evidence ("case file") shared by the agents.

The browser still calls each document endpoint at its own UX moment
(extract at upload, integrity/face at Review & Submit, mask on hover). Each of
those agent runs writes its result here under the wizard's case id, so the
case-level agents (cross-check, dedupe, fraud fusion, policy check,
underwriting) work from evidence already gathered — no re-upload, no re-run.

Stored under storage/agent/cases/ (same privacy class as storage/: local,
never committed). Holds extracted field values because the cross-document and
underwriting agents need them; never holds document images.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parents[3] / "storage" / "agent" / "cases"
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _lock(case_id: str) -> threading.RLock:
    with _locks_guard:
        return _locks.setdefault(case_id, threading.RLock())


def _path(case_id: str) -> Path:
    return CASE_DIR / f"{case_id}.json"


def _blank(case_id: str) -> dict:
    return {"case_id": case_id, "application_no": None, "created_at": _now(), "updated_at": _now(),
            "applicant": {}, "loan": {}, "documents": {}, "face": None, "liveness": None,
            "assessment": None, "underwriting": None, "memo": None, "offer": None,
            "workflow": None, "notifications": [], "source": "wizard"}


def get(case_id: str) -> dict:
    with _lock(case_id):
        p = _path(case_id)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return _blank(case_id)


def exists(case_id: str) -> bool:
    return _path(case_id).exists()


def update(case_id: str, fn) -> dict:
    """Read-modify-write under the case lock; fn(case) mutates in place."""
    with _lock(case_id):
        case = get(case_id)
        fn(case)
        case["updated_at"] = _now()
        CASE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _path(case_id).with_suffix(".tmp")
        tmp.write_text(json.dumps(case, default=str, indent=1), encoding="utf-8")
        os.replace(tmp, _path(case_id))
        return copy.deepcopy(case)


def put_document(case_id: str, doc_type: str, **fields) -> dict:
    def _fn(case):
        doc = case["documents"].setdefault(doc_type, {"doc_type": doc_type})
        doc.update({k: v for k, v in fields.items() if v is not None})
        doc["updated_at"] = _now()
    return update(case_id, _fn)


def set_section(case_id: str, key: str, value) -> dict:
    return update(case_id, lambda c: c.__setitem__(key, value))


def link_application(case_id: str, application_no: str, applicant: dict | None = None,
                     loan: dict | None = None) -> dict:
    def _fn(case):
        case["application_no"] = application_no
        if applicant:
            case["applicant"].update(applicant)
        if loan:
            case["loan"].update(loan)
    case = update(case_id, _fn)
    index = _app_index()
    index[application_no] = case_id
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    (CASE_DIR / "_applications.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    return case


def _app_index() -> dict:
    p = CASE_DIR / "_applications.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def by_application(application_no: str) -> str | None:
    return _app_index().get(application_no)


def list_cases(limit: int = 50, full: bool = False) -> list[dict]:
    """Newest first. full=True returns whole case files (fraud triage reads the
    assessments); otherwise a small summary per case."""
    if not CASE_DIR.exists():
        return []
    out = []
    for p in sorted(CASE_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.name.startswith("_"):
            continue
        try:
            c = json.loads(p.read_text(encoding="utf-8"))
            out.append(c if full else {"case_id": c["case_id"], "application_no": c.get("application_no"),
                                       "documents": sorted(c.get("documents", {})), "updated_at": c.get("updated_at")})
        except (OSError, ValueError, KeyError):
            continue
        if len(out) >= limit:
            break
    return out
