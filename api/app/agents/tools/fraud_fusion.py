"""Fraud Fusion — one advisory fraud score from every evidence component.

    fused = Σ wᵢ · componentᵢ / Σ wᵢ      (over AVAILABLE components only — an
                                           absent component is absent, not "0")
    floors: face_mismatch_confident · exact_duplicate_identity · tamper_high
            one floor  → tier at least MEDIUM   (a single strong red flag can't
            two floors → HIGH                    be averaged away by clean ones)
    tiers:  MEDIUM ≥ 0.30 · HIGH ≥ 0.60        (same bands as tampering fusion)

Weights are versioned (weights_version is snapshotted on every assessment) and
live in api/data/fraud_weights.yaml, mirrored to fraud.fraud_weight_sets.
The result is evidence for a human: it never rejects anything.
"""
from __future__ import annotations

import json

import yaml

from ...config import settings

DEFAULT = {
    # v2: calibrated 2026-09-25 on 400 labelled scenarios (eval/agents/case_eval.py),
    # held-out tier agreement 0.93 vs 0.60 for v1 (plain weights) and 0.79 for a
    # calibrated weighted sum alone. v1 = weights + floors only; v2 adds the
    # dominant/corroboration path the tampering fusion already uses.
    "weights_version": "v2",
    "weights": {"tampering": 0.35, "face": 0.2, "cross_check": 0.2, "validation": 0.15, "dedupe": 0.1, "patterns": 0.1},
    "tiers": {"medium": 0.2, "high": 0.6},
    "confidence": {"tampering": 0.59, "face": 0.6, "cross_check": 0.4, "validation": 0.6, "dedupe": 0.5, "patterns": 0.6},
    "corroboration_factor": 0.1,
    "face_uncertain": 0.35,
}
V1 = {"weights_version": "v1",
      "weights": {"tampering": 0.30, "face": 0.20, "cross_check": 0.20,
                  "validation": 0.10, "dedupe": 0.10, "patterns": 0.10},
      "tiers": {"medium": 0.30, "high": 0.60}}
COMPONENTS = ("tampering", "face", "cross_check", "validation", "dedupe", "patterns")


def load_weights() -> dict:
    p = settings.data_dir / "fraud_weights.yaml"
    if p.exists():
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            # an operator's file wins unless it is an older version than the code's default
            if data.get("weights") and str(data.get("weights_version", "")) >= DEFAULT["weights_version"]:
                return {**DEFAULT, **data}
        except (OSError, yaml.YAMLError):
            pass
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# Fraud Fusion weights (agents/tools/fraud_fusion.py). Bump weights_version\n"
                 "# whenever a value changes — every assessment snapshots the version it used.\n"
                 + yaml.safe_dump(DEFAULT, sort_keys=False), encoding="utf-8")
    return dict(DEFAULT)


# ── component mappings ───────────────────────────────────────────────────────
def face_component(face: dict | None, uncertain_value: float | None = None):
    """match → 0 · uncertain → review weight (weights file 'face_uncertain',
    0.20 in v1) · mismatch → 0.6..1.0 by how far below the 'different' band ·
    insufficient → unavailable. A low-quality face can never produce 'mismatch'
    (face.service already downgrades it)."""
    if not face or face.get("overall") in (None, "insufficient"):
        return None, ["no comparable faces"]
    overall = face["overall"]
    if overall == "match":
        return 0.0, ["same person on every document with a face"]
    if overall == "uncertain":
        u = uncertain_value if uncertain_value is not None else load_weights().get("face_uncertain", 0.20)
        return u, ["face similarity inconclusive — human comparison suggested"]
    diff = [p for p in face.get("pairs", []) if p.get("verdict") == "different"]
    sim = min((p["similarity"] for p in diff), default=0.0)
    c = round(min(1.0, 0.6 + 0.4 * (1 - max(0.0, sim) / 0.25)), 4)
    return c, [f"faces differ between {p['a']} and {p['b']} (similarity {p['similarity']:.2f})" for p in diff][:3]


def tampering_component(documents: dict):
    scored = [(dt, d["tampering"]) for dt, d in documents.items() if d.get("tampering")]
    if not scored:
        return None, ["no document has been through the integrity check"], False
    dt, t = max(scored, key=lambda x: x[1].get("risk_score") or 0)
    ev = [f"{d}: {x['risk_tier']} ({x['risk_score']:.2f})" for d, x in scored]
    high = any(x.get("risk_tier") == "HIGH" for _, x in scored)
    return float(t.get("risk_score") or 0.0), ev, high


_CHECK_SEV = {"error": 0.9, "warn": 0.3, "ok": 0.0}


def cross_check_component(checks: list | None):
    if not checks:
        return None, ["fewer than two documents to compare"]
    worst = max(_CHECK_SEV.get(c["level"], 0.0) for c in checks)
    ev = [f"{c['level']}: {c['title']}" for c in checks if c["level"] != "ok"] or ["all cross-document checks agree"]
    return worst, ev


def validation_component(documents: dict):
    total = errs = warns = 0
    ev = []
    for dt, d in documents.items():
        flags = (d.get("validation") or {}).get("flags") or {}
        total += len(flags)
        e = [k for k, f in flags.items() if f == "error"]
        w = [k for k, f in flags.items() if f == "warn"]
        errs += len(e)
        warns += len(w)
        if e:
            ev.append(f"{dt}: failed format/checksum on {', '.join(e)}")
    if not total:
        return None, ["no validated fields"]
    return round(min(1.0, (errs + 0.3 * warns) / total * 3), 4), ev or ["all extracted fields pass format checks"]


def fuse(components: dict[str, float | None], floors: list[str], weights_doc: dict | None = None) -> dict:
    w = (weights_doc or load_weights())
    weights, tiers = w["weights"], w["tiers"]
    avail = {k: v for k, v in components.items() if v is not None and weights.get(k, 0) > 0}
    total = sum(weights[k] for k in avail)
    fused = sum(weights[k] * v for k, v in avail.items()) / total if total else 0.0
    weighted_avg = fused
    # Dominant-evidence + corroboration path (v2+; same mechanics as the
    # tampering fusion): averaging measures agreement, but one decisive
    # component must not be washed out by several quiet ones, and two
    # independent components agreeing is stronger than either alone.
    conf = w.get("confidence") or {}
    dominant_path = None
    if conf:
        ranked = sorted(((v * conf.get(k, 0.0), k) for k, v in avail.items()), reverse=True)
        d1 = ranked[0] if ranked else (0.0, None)
        d2 = ranked[1] if len(ranked) > 1 else (0.0, None)
        corro = min(1.0, d1[0] + w.get("corroboration_factor", 0.0) * d2[0]) if d2[0] > 0 else d1[0]
        best = max(fused, d1[0], corro)
        if best > fused:
            dominant_path = {"path": "corroborated" if corro > d1[0] else "dominant",
                             "components": [d1[1], d2[1]] if corro > d1[0] else [d1[1]], "value": round(best, 4)}
            fused = best
    if len(floors) >= 2:
        fused = max(fused, tiers["high"])
    elif len(floors) == 1:
        fused = max(fused, tiers["medium"])
    fused = round(min(1.0, max(0.0, fused)), 4)
    tier = "HIGH" if fused >= tiers["high"] else "MEDIUM" if fused >= tiers["medium"] else "LOW"
    dominant = max(avail, key=lambda k: weights[k] * avail[k]) if avail else None
    return {"fused_score": fused, "weighted_average": round(weighted_avg, 4), "tier": tier,
            "weights_version": w["weights_version"], "dominant_component": dominant,
            "dominant_path": dominant_path,
            "floors": floors, "available": sorted(avail), "advisory": True,
            "normalized_weights": {k: round(weights[k] / total, 4) for k in avail} if total else {}}


def persist_sql(case_id: str, application_no: str | None, assessment: dict, run_id: str | None):
    """Best effort: fraud.fraud_assessments (+ the weight set it used)."""
    if not (settings.db_enabled and settings.agents_sql_sink):
        return False
    try:
        import pyodbc
        with pyodbc.connect(settings.db_connection_string, autocommit=True, timeout=5) as conn:
            cur = conn.cursor()
            w = load_weights()
            cur.execute("IF NOT EXISTS (SELECT 1 FROM fraud.fraud_weight_sets WHERE weights_version = ?) "
                        "INSERT INTO fraud.fraud_weight_sets (weights_version, weights_json, is_active, created_by) "
                        "VALUES (?,?,0,'agents')", (w["weights_version"], w["weights_version"], json.dumps(w)))
            comp = assessment["components"]
            cur.execute(
                """INSERT INTO fraud.fraud_assessments (case_id, application_no, weights_version, fused_score, tier,
                     face_component, tampering_component, cross_check_component, validation_component,
                     dedupe_component, patterns_component, floors_json, evidence_json, run_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (case_id, application_no, assessment["weights_version"], assessment["fused_score"],
                 assessment["tier"], *[comp[k]["score"] for k in COMPONENTS],
                 json.dumps(assessment["floors"]),
                 json.dumps({k: comp[k]["evidence"] for k in COMPONENTS})[:30000], run_id))
        return True
    except Exception:  # noqa: BLE001
        return False
