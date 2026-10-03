"""Duplicate-identity detection across applications.

Keys (never the raw numbers — keyed HMAC-SHA256 with DEDUPE_HMAC_KEY):
  file_sha256   the exact uploaded file seen in another application
  pan_hmac      normalised PAN
  aadhaar_hmac  only when all 12 digits were read (masked numbers are skipped)
  account_hmac  bank account numbers of 9+ digits (masked ones skipped)
Faces: SFace embeddings in a local index (biometric data stays in api/data,
never in SQL); matched by cosine similarity. Searching one face against EVERY
other applicant (1:N) uses FACE_DEDUPE_T, far stricter than SFace's 0.363
verification threshold — see the note on the constant.

What a hit means (the component score fed to Fraud Fusion):
  1.00  the exact same file was submitted in another application
  0.95  identity conflict — same PAN/Aadhaar with a DIFFERENT face, or the same
        face with a DIFFERENT PAN (synthetic / stolen identity pattern)
  0.35  consistent repeat applicant (same PAN and same face) — worth a look
        (existing exposure), not a fraud signal by itself
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import threading
import time

import numpy as np

from ...config import settings

_lock = threading.Lock()
FACE_MATCH_T = 0.363      # SFace 1:1 verification (two documents of ONE applicant)
# 1:N search threshold. 0.363 is right for comparing two photos; against a whole index it is not:
# measured on real faces of guaranteed-different people (LFW folds 1-3, 1.08M pairs), 0.363 matches
# 8 pairs in 10,000 — with 1,000 applicants indexed, more than half of all NEW applicants would get a
# false "same face, different PAN" hit, which is a 0.95 floor. At 0.50 it is about 1 pair in a million
# (~1% false alerts at 10,000 indexed) while 91% of true same-person pairs still match.
# Chosen on development folds only; see eval/real/case_real.py (choose_dedupe_threshold).
FACE_DEDUPE_T = 0.50
FACE_DIFFERENT_T = 0.25


def _db() -> sqlite3.Connection:
    p = settings.data_dir / "dedupe.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, check_same_thread=False)
    conn.execute("CREATE TABLE IF NOT EXISTS keys (case_id TEXT, key_type TEXT, key_value TEXT, doc_type TEXT, created_at REAL)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_keys ON keys(key_type, key_value)")
    conn.execute("CREATE TABLE IF NOT EXISTS faces (case_id TEXT, doc_type TEXT, embedding TEXT, staged INTEGER, created_at REAL)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_faces ON faces(case_id)")
    return conn


def _hmac(value: str) -> str:
    return hmac.new(settings.dedupe_hmac_key.encode(), value.encode(), hashlib.sha256).hexdigest()


def _norm_pan(v) -> str | None:
    s = re.sub(r"[^A-Z0-9]", "", str(v or "").upper())
    return s if re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", s) else None


def _norm_aadhaar(v) -> str | None:
    s = str(v or "").upper()
    if "X" in s:
        return None
    d = re.sub(r"\D", "", s)
    return d if len(d) == 12 else None


def _norm_account(v) -> str | None:
    s = str(v or "").upper()
    if "X" in s:
        return None
    d = re.sub(r"\D", "", s)
    return d if len(d) >= 9 else None


def keys_for_case(case: dict) -> list[dict]:
    keys = []
    pans, aadhaars, accounts = set(), set(), set()
    for doc_type, doc in (case.get("documents") or {}).items():
        if doc.get("sha256"):
            keys.append({"key_type": "file_sha256", "key_value": doc["sha256"], "doc_type": doc_type})
        p = doc.get("parsed") or {}
        for k in ("pan_number", "pan"):
            n = _norm_pan(p.get(k))
            if n:
                pans.add((n, doc_type))
        n = _norm_aadhaar(p.get("aadhaar_number"))
        if n:
            aadhaars.add((n, doc_type))
        n = _norm_account(p.get("account_number"))
        if n:
            accounts.add((n, doc_type))
    for n, dt in pans:
        keys.append({"key_type": "pan_hmac", "key_value": _hmac("PAN:" + n), "doc_type": dt})
    for n, dt in aadhaars:
        keys.append({"key_type": "aadhaar_hmac", "key_value": _hmac("AADHAAR:" + n), "doc_type": dt})
    for n, dt in accounts:
        keys.append({"key_type": "account_hmac", "key_value": _hmac("ACCT:" + n), "doc_type": dt})
    return keys


def stage_faces(case_id: str, embeddings: dict) -> None:
    """Remember this case's face embeddings (from the Face Verification agent).
    Replaces any earlier set for the same case."""
    with _lock:
        conn = _db()
        conn.execute("DELETE FROM faces WHERE case_id = ?", (case_id,))
        for doc_type, embs in (embeddings or {}).items():
            for e in embs:
                conn.execute("INSERT INTO faces VALUES (?,?,?,?,?)",
                             (case_id, doc_type, json.dumps(e), 1, time.time()))
        conn.commit()
        conn.close()


def _faces(conn, case_id: str | None = None, exclude: str | None = None):
    if case_id:
        rows = conn.execute("SELECT case_id, doc_type, embedding FROM faces WHERE case_id = ?", (case_id,)).fetchall()
    else:
        rows = conn.execute("SELECT case_id, doc_type, embedding FROM faces WHERE case_id <> ?", (exclude or "",)).fetchall()
    return [(r[0], r[1], np.asarray(json.loads(r[2]), dtype=np.float32)) for r in rows]


def faces_for_case(case_id: str, doc_types: tuple = ("pan", "aadhaar", "passport", "driving_license",
                                                        "voter_id", "photograph")) -> list:
    """The ID-photo embeddings staged for this case (Liveness compares the
    selfie against them)."""
    with _lock:
        conn = _db()
        rows = _faces(conn, case_id=case_id)
        conn.close()
    return [e for _, dt, e in rows if dt in doc_types]


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    return float(np.dot(a, b) / (na * nb)) if na and nb else 0.0


def check(case_id: str, case: dict, register: bool = True) -> dict:
    """Look this case up against every other case in the index; optionally add
    its keys to the index afterwards (so the next application can match it)."""
    keys = keys_for_case(case)
    with _lock:
        conn = _db()
        key_hits: dict[str, set] = {}
        for k in keys:
            rows = conn.execute("SELECT DISTINCT case_id FROM keys WHERE key_type = ? AND key_value = ? AND case_id <> ?",
                                (k["key_type"], k["key_value"], case_id)).fetchall()
            for (other,) in rows:
                key_hits.setdefault(other, set()).add(k["key_type"])
        mine = _faces(conn, case_id=case_id)
        others = _faces(conn, exclude=case_id) if mine else []
        face_best: dict[str, float] = {}
        for _, _, a in mine:
            for other, _, b in others:
                s = _cos(a, b)
                face_best[other] = max(face_best.get(other, -1.0), s)
        if register:
            conn.execute("DELETE FROM keys WHERE case_id = ?", (case_id,))
            for k in keys:
                conn.execute("INSERT INTO keys VALUES (?,?,?,?,?)",
                             (case_id, k["key_type"], k["key_value"], k["doc_type"], time.time()))
        conn.commit()
        conn.close()

    hits = []
    for other in set(key_hits) | {o for o, s in face_best.items() if s >= FACE_DEDUPE_T}:
        types = key_hits.get(other, set())
        sim = face_best.get(other)
        face_match = sim is not None and sim >= FACE_DEDUPE_T
        face_diff = sim is not None and sim < FACE_DIFFERENT_T
        id_match = bool(types & {"pan_hmac", "aadhaar_hmac"})
        if "file_sha256" in types:
            score, kind = 1.0, "same_file_reused"
        elif id_match and face_diff:
            score, kind = 0.95, "same_id_different_face"
        elif face_match and not id_match and _has_other_pan(other, keys):
            score, kind = 0.95, "same_face_different_id"
        elif id_match or face_match or "account_hmac" in types:
            score, kind = 0.35, "repeat_applicant"
        else:
            continue
        hits.append({"other_case": other, "keys": sorted(types), "face_similarity":
                     round(sim, 3) if sim is not None else None, "kind": kind, "score": score})
    hits.sort(key=lambda h: -h["score"])
    score = max((h["score"] for h in hits), default=0.0)
    return {"hits": hits, "score": score, "keys_checked": sorted({k["key_type"] for k in keys}),
            "faces_checked": len(mine), "floor": any(h["score"] >= 0.95 for h in hits)}


def _has_other_pan(other_case: str, my_keys: list[dict]) -> bool:
    """Other case carries a PAN, and it is not one of mine."""
    mine = {k["key_value"] for k in my_keys if k["key_type"] == "pan_hmac"}
    if not mine:
        return False
    with _lock:
        conn = _db()
        rows = conn.execute("SELECT key_value FROM keys WHERE case_id = ? AND key_type = 'pan_hmac'",
                            (other_case,)).fetchall()
        conn.close()
    theirs = {r[0] for r in rows}
    return bool(theirs) and not (theirs & mine)


def reset_index():
    """Tests / evaluation only."""
    with _lock:
        conn = _db()
        conn.execute("DELETE FROM keys")
        conn.execute("DELETE FROM faces")
        conn.commit()
        conn.close()
