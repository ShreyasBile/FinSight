"""Shared helpers for the agent evaluation harness.

Every evaluator runs the REAL agent code (through the FastAPI app or the
agent classes), writes api/eval/results/<agent>.json, and rates the primary
metric against targets.yaml (fixed before the run). Synthetic data only may
reach a hosted model; real documents are local-only and reported in aggregate.
"""
from __future__ import annotations

import json
import math
import os
import re
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

EVAL_DIR = Path(__file__).resolve().parent
API_DIR = EVAL_DIR.parent
RESULTS = EVAL_DIR / "results"
SYNTH = EVAL_DIR / "datasets" / "synth" / "out"
REAL = EVAL_DIR / "datasets" / "real"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

_DATA_DIR = None


def setup_env(provider: str = "ollama", model: str = "qwen2.5vl:7b", authenticity: bool = True,
              explain: bool = True, db: bool = True, sink: bool = False):
    """Isolated environment. Must run before importing app modules. Local-only
    unless provider='anthropic', which run_eval allows ONLY for agents whose
    test data is entirely synthetic or public (SYNTHETIC_ONLY there)."""
    global _DATA_DIR
    _DATA_DIR = tempfile.mkdtemp(prefix="finsight-eval-")
    hosted = provider == "anthropic"
    os.environ.update({
        "FINSIGHT_DATA_DIR": _DATA_DIR, "FINSIGHT_FORBID_HOSTED": "false" if hosted else "true",
        "AGENTS_SQL_SINK": "true" if sink else "false", "DB_ENABLED": "true" if db else "false",
        "TAMPERING_AUTHENTICITY_ENABLED": "true" if authenticity else "false",
        "TAMPERING_EXPLAIN_ENABLED": "true" if explain else "false",
        "OCR_PROVIDER": provider, "PYTHONIOENCODING": "utf-8",
        # evaluation rows are tagged 'demo': the app lists only 'prod' (real) applications,
        # so a throwaway EVAL- row can never show up in a screen even if a run is interrupted
        "AGENTS_ENV": "demo",
    })
    Path(_DATA_DIR, "llm_settings.json").write_text(json.dumps({
        "revision": 1, "default": {"provider": provider, "model": model, "temperature": 0.0,
                                   "num_ctx": 8192, "timeout_s": 180, "keep_alive": "30m", "effort": "low"},
        "lanes": {}, "agents": {},
        # synthetic/public data only (enforced by run_eval) — the ack a Head gives in Settings
        "hosted_ack": {"by": "evaluation", "at": "synthetic-only run"} if hosted else None}), encoding="utf-8")
    return _DATA_DIR


def fresh_compliance_db() -> str:
    """Point the compliance module at a freshly seeded throwaway database, so
    the evaluation measures the agents against the 14 seeded rules — not
    whatever rules someone added to the live knowledge base."""
    from app.compliance import db as dbm
    from app.compliance import startup as compliance_startup
    path = str(Path(tempfile.mkdtemp(prefix="finsight-compliance-")) / "compliance.db")
    dbm.DB_PATH = path
    compliance_startup()
    return path


def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def manifest(name: str) -> list[dict]:
    return json.loads((SYNTH / name / "manifest.json").read_text())


def read(rel: str) -> bytes:
    return (SYNTH / rel).read_bytes()


# ── metrics ──────────────────────────────────────────────────────────────────
def pct(values, q: float):
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] if lo == hi else v[lo] + (v[hi] - v[lo]) * (k - lo)


def latency(values_ms) -> dict:
    v = [x for x in values_ms if x is not None]
    return {"n": len(v), "p50_s": round(pct(v, 0.5) / 1000, 2) if v else None,
            "p95_s": round(pct(v, 0.95) / 1000, 2) if v else None,
            "mean_s": round(statistics.mean(v) / 1000, 2) if v else None}


def auc(scores_pos, scores_neg) -> float | None:
    if not scores_pos or not scores_neg:
        return None
    wins = 0.0
    for p in scores_pos:
        for n in scores_neg:
            wins += 1.0 if p > n else 0.5 if p == n else 0.0
    return round(wins / (len(scores_pos) * len(scores_neg)), 4)


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
    return {"precision": round(p, 4) if p is not None else None, "recall": round(r, 4) if r is not None else None,
            "f1": round(f, 4) if f is not None else None, "tp": tp, "fp": fp, "fn": fn}


def norm_text(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def to_num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[₹,\s]|Rs\.?", "", str(v or ""))
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


def field_match(key: str, expected, got) -> bool:
    """Normalised comparison: numbers within 0.5%, dates by value, text ignoring
    case/spacing/punctuation. Aadhaar accepts the masked form only when the last
    four digits match (the prompt tells the model never to invent digits)."""
    if expected is None:
        return got in (None, "", "null")
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        g = to_num(got)
        return g is not None and abs(g - float(expected)) <= max(1.0, 0.005 * abs(float(expected)))
    e, g = norm_text(expected), norm_text(got)
    if key == "aadhaar_number" and "X" in g:
        return e[-4:] == g[-4:]
    if key in ("dob",):
        from app.docrules import parse_date_any
        return parse_date_any(expected) == parse_date_any(got)
    if key == "gender":
        return e[:1] == g[:1] and bool(g)
    if key in ("address",):
        return _token_overlap(expected, got) >= 0.8
    return e == g


def _token_overlap(a, b) -> float:
    ta = set(re.findall(r"[a-z0-9]+", str(a).lower()))
    tb = set(re.findall(r"[a-z0-9]+", str(b or "").lower()))
    return len(ta & tb) / len(ta) if ta else 0.0


# ── rating + persistence ─────────────────────────────────────────────────────
def targets() -> dict:
    return yaml.safe_load((EVAL_DIR / "targets.yaml").read_text(encoding="utf-8"))


def rate(value, target, higher: bool = True) -> str:
    if value is None:
        return "Not measured"
    if higher:
        if value >= target:
            return "Strong"
        return "Acceptable" if value >= target * 0.9 else "Weak"
    if value <= target:
        return "Strong"
    tol = max(target * 0.1, 0.01) if target else 0.01
    return "Acceptable" if value <= target + tol else "Weak"


def save(agent_id: str, metrics: dict, **extra) -> dict:
    t = targets().get(agent_id, {})
    prim = t.get("primary", {})
    out = {"agent_id": agent_id, "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "metrics": metrics, "target": prim,
           "primary_value": metrics.get(prim.get("metric")) if prim else None,
           "rating": rate(metrics.get(prim.get("metric")), prim.get("target"), prim.get("higher", True)) if prim else None,
           "secondary": [{**s, "value": metrics.get(s["metric"]),
                          "rating": rate(metrics.get(s["metric"]), s["target"], s.get("higher", True))}
                         for s in t.get("secondary", [])],
           **extra}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{agent_id}.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(f"[{agent_id}] {prim.get('metric')}={out['primary_value']} target={prim.get('target')} -> {out['rating']}")
    return out


class Timer:
    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *a):
        self.ms = (time.perf_counter() - self.t0) * 1000
