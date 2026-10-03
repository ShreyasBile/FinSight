"""Fraud triage: turn per-case evidence into PROPOSALS a fraud officer can
accept or dismiss. Nothing here inserts into fraud.fraud_cases / fraud_rings —
opening a case stays a human action (maker-checker).

Two proposal kinds:

  open_case   one application whose own evidence warrants a fraud case:
              fused tier HIGH, or MEDIUM carried by a dominant floor
              (confident face mismatch, exact duplicate identity, HIGH tamper)
  ring        applications linked by identity evidence that should NOT link
              different people: the same face under different PANs, one PAN
              with different faces, a bank account or the very same file
              shared across different PANs. A repeat applicant (same PAN AND
              same face) is a customer, not a ring, and is never proposed.
              Two linked applications = a "pair" ring; three or more = a ring.
"""
from __future__ import annotations

import json
from itertools import combinations

import numpy as np

_SUSPICIOUS = {
    "face_across_ids": ("same face, different PAN", 0.9),
    "id_different_face": ("same PAN / Aadhaar, different face", 0.9),
    "shared_account": ("same bank account, different PAN", 0.7),
    "shared_file": ("identical file submitted under a different PAN", 0.95),
}


def _index():
    """(keys_by_case, faces_by_case) from the dedupe index (hashed keys only)."""
    from . import dedupe
    keys, faces = {}, {}
    with dedupe._lock:
        conn = dedupe._db()
        for cid, kt, kv in conn.execute("SELECT case_id, key_type, key_value FROM keys"):
            keys.setdefault(cid, {}).setdefault(kt, set()).add(kv)
        for cid, _dt, emb in conn.execute("SELECT case_id, doc_type, embedding FROM faces"):
            faces.setdefault(cid, []).append(np.asarray(json.loads(emb), dtype=np.float32))
        conn.close()
    return keys, faces


def _best_face(a: list, b: list) -> float | None:
    if not a or not b:
        return None
    best = -1.0
    for x in a:
        for y in b:
            best = max(best, float(x @ y / ((np.linalg.norm(x) * np.linalg.norm(y)) or 1.0)))
    return best


def links(keys: dict, faces: dict) -> list[dict]:
    """Suspicious pairwise links between cases."""
    from .dedupe import FACE_DEDUPE_T, FACE_DIFFERENT_T      # 1:N threshold: every pair of cases is compared
    out = []
    for a, b in combinations(sorted(set(keys) | set(faces)), 2):
        ka, kb = keys.get(a, {}), keys.get(b, {})
        pan_a, pan_b = ka.get("pan_hmac", set()), kb.get("pan_hmac", set())
        ids_a = pan_a | ka.get("aadhaar_hmac", set())
        ids_b = pan_b | kb.get("aadhaar_hmac", set())
        same_id = bool(ids_a & ids_b)
        diff_pan = bool(pan_a and pan_b and not (pan_a & pan_b))
        sim = _best_face(faces.get(a, []), faces.get(b, []))
        kinds = []
        if sim is not None and sim >= FACE_DEDUPE_T and diff_pan:
            kinds.append("face_across_ids")
        if same_id and sim is not None and sim < FACE_DIFFERENT_T:
            kinds.append("id_different_face")
        if ka.get("account_hmac", set()) & kb.get("account_hmac", set()) and diff_pan:
            kinds.append("shared_account")
        if ka.get("file_sha256", set()) & kb.get("file_sha256", set()) and (diff_pan or not same_id):
            kinds.append("shared_file")
        if kinds:
            out.append({"a": a, "b": b, "kinds": kinds,
                        "face_similarity": round(sim, 3) if sim is not None else None,
                        "strength": max(_SUSPICIOUS[k][1] for k in kinds)})
    return out


def _components(edges: list[dict]) -> list[set]:
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for e in edges:
        parent[find(e["a"])] = find(e["b"])
    groups = {}
    for x in parent:
        groups.setdefault(find(x), set()).add(x)
    return [g for g in groups.values() if len(g) >= 2]


def case_proposals(cases: list[dict], already_open: set[str]) -> list[dict]:
    out = []
    for c in cases:
        a = (c.get("assessment") or {}).get("fraud") or {}
        tier, floors = a.get("tier"), a.get("floors") or []
        if not tier or (tier != "HIGH" and not (tier == "MEDIUM" and floors)):
            continue
        app = c.get("application_no")
        if app and app in already_open:
            continue
        reasons = []
        for comp, info in (a.get("components") or {}).items():
            if info.get("available") and (info.get("score") or 0) >= 0.5:
                reasons += [f"{comp}: {e}" for e in (info.get("evidence") or [])[:2]]
        out.append({"kind": "open_case", "case_ids": [c["case_id"]], "application_nos": [app] if app else [],
                    "severity": "high" if tier == "HIGH" else "medium",
                    "confidence": a.get("fused_score"), "floors": floors,
                    "reasons": reasons[:6] or [f"fused fraud tier {tier}"],
                    "suggested_action": "Open a fraud case and assign an investigator"})
    return out


def ring_proposals(edges: list[dict], app_of: dict) -> list[dict]:
    out = []
    for comp in _components(edges):
        inner = [e for e in edges if e["a"] in comp and e["b"] in comp]
        kinds = sorted({k for e in inner for k in e["kinds"]})
        strength = max(e["strength"] for e in inner)
        size = len(comp)
        out.append({"kind": "ring", "case_ids": sorted(comp),
                    "application_nos": sorted(a for a in (app_of.get(c) for c in comp) if a),
                    "size": size, "link_types": kinds,
                    "severity": "high" if size >= 3 or strength >= 0.9 else "medium",
                    "confidence": round(min(1.0, strength + 0.05 * (len(inner) - 1)), 3),
                    "reasons": [_SUSPICIOUS[k][0] for k in kinds],
                    "links": [{"a": e["a"], "b": e["b"], "kinds": e["kinds"],
                               "face_similarity": e["face_similarity"]} for e in inner],
                    "suggested_action": ("Review as a possible fraud ring" if size >= 3
                                         else "Review the linked pair of applications")})
    return out


def open_fraud_applications() -> set[str]:
    """application_nos that already have an open fraud case (SQL; empty if down)."""
    from ...config import settings
    if not settings.db_enabled:
        return set()
    try:
        import pyodbc
        with pyodbc.connect(settings.db_connection_string, timeout=3) as conn:
            rows = conn.execute("""SELECT a.application_no FROM fraud.fraud_cases f
                                   JOIN core.loan_applications a ON a.application_id = f.application_id
                                   WHERE f.status IN ('open','investigating','escalated')""").fetchall()
        return {r[0] for r in rows if r[0]}
    except Exception:  # noqa: BLE001
        return set()


def triage(cases: list[dict], limit: int = 20, already_open: set[str] | None = None) -> dict:
    keys, faces = _index()
    edges = links(keys, faces)
    app_of = {c["case_id"]: c.get("application_no") for c in cases}
    already_open = open_fraud_applications() if already_open is None else already_open
    props = ring_proposals(edges, app_of) + case_proposals(cases, already_open)
    rank = {"high": 0, "medium": 1}
    props.sort(key=lambda p: (rank.get(p["severity"], 2), -(p.get("confidence") or 0), -len(p["case_ids"])))
    for i, p in enumerate(props, 1):
        p["proposal_id"] = f"P{i:03d}"
    return {"proposals": props[:limit], "total": len(props), "cases_scanned": len(cases),
            "indexed_cases": len(set(keys) | set(faces)), "links": len(edges),
            "skipped_already_open": len(already_open)}
