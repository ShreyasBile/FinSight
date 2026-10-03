"""Shared machinery for the real-data evaluation (protocol: finsight-eval-data/PROTOCOL.md).

What this adds to the synthetic harness (eval/common.py):
  * datasets live outside the repository (FINSIGHT_EVAL_DATA, default D:\\novacinternship\\finsight-eval-data)
  * deterministic, GROUP-wise dev/test splits (a person / document / loan never straddles both)
  * 95% intervals: Wilson for proportions, percentile bootstrap for everything else
  * a run record (git commit, model, versions, hardware, data hashes) with every result
  * a per-item result cache on the data drive, so a long run interrupted by a usage limit or a
    restart resumes where it stopped instead of starting over
  * results written to eval/results_real/<agent>.json and rated against targets_real.yaml,
    falling back to targets.yaml for metrics that already had a pre-registered target
Logging rule: aggregates only. Nothing here prints a field value read from a document.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from eval import common as C

DATA_ROOT = Path(os.environ.get("FINSIGHT_EVAL_DATA", r"D:\novacinternship\finsight-eval-data"))
RAW = DATA_ROOT / "raw"
CACHE = DATA_ROOT / "work" / "cache"
RESULTS = C.EVAL_DIR / "results_real"
SEED = 20261001                       # split seed, fixed in PROTOCOL.md
DEV_FRACTION = 0.3


# ── splits and sampling ─────────────────────────────────────────────────────
def _u(key: str, salt: str = "") -> float:
    """A stable number in [0, 1) for a key: the same item lands in the same split on every
    machine and in every run, whatever order the items are listed in."""
    h = hashlib.sha256(f"{SEED}|{salt}|{key}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2 ** 64


def split_of(group_key: str, dev_fraction: float = DEV_FRACTION) -> str:
    return "dev" if _u(str(group_key), "split") < dev_fraction else "test"


def take(items, split: str, group=lambda x: x["id"], dev_fraction: float = DEV_FRACTION):
    return [x for x in items if split_of(group(x), dev_fraction) == split]


def sample(items, n: int | None, key=lambda x: x["id"], salt: str = "sample"):
    """Deterministic sample of n items (stable order by hash, so n=200 is a prefix of n=400)."""
    ordered = sorted(items, key=lambda x: _u(str(key(x)), salt))
    return ordered if n is None else ordered[:n]


def stratified(items, n: int, strata=lambda x: x["label"], key=lambda x: x["id"]):
    """n items with every stratum represented in proportion (at least one each)."""
    groups: dict = {}
    for x in items:
        groups.setdefault(strata(x), []).append(x)
    total = len(items)
    out = []
    for g, xs in groups.items():
        k = max(1, round(n * len(xs) / total))
        out += sample(xs, k, key, salt=f"strat-{g}")
    return sample(out, None, key)


# ── statistics ──────────────────────────────────────────────────────────────
def wilson(k: int, n: int, z: float = 1.96) -> dict:
    if n == 0:
        return {"value": None, "ci95": [None, None], "k": 0, "n": 0}
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return {"value": round(p, 4), "ci95": [round(max(0, c - h), 4), round(min(1, c + h), 4)], "k": k, "n": n}


def bootstrap(stat, rows, b: int = 1000, seed: int = 7) -> dict:
    """Percentile bootstrap interval of stat(rows)."""
    if not rows:
        return {"value": None, "ci95": [None, None], "n": 0}
    value = stat(rows)
    rng = random.Random(seed)
    vals = []
    for _ in range(b):
        s = [rows[rng.randrange(len(rows))] for _ in rows]
        try:
            v = stat(s)
        except Exception:  # noqa: BLE001 - a degenerate resample (one class only) is skipped
            v = None
        if v is not None:
            vals.append(v)
    vals.sort()
    lo = vals[int(0.025 * (len(vals) - 1))] if vals else None
    hi = vals[int(0.975 * (len(vals) - 1))] if vals else None
    r = lambda v: round(v, 4) if isinstance(v, float) else v  # noqa: E731
    return {"value": r(value), "ci95": [r(lo), r(hi)], "n": len(rows)}


def auc(pos, neg):
    return C.auc(pos, neg)


def auc_ci(rows, score=lambda r: r["score"], label=lambda r: r["label"]) -> dict:
    def stat(s):
        p = [score(r) for r in s if label(r)]
        n = [score(r) for r in s if not label(r)]
        return C.auc(p, n) if p and n else None
    return bootstrap(stat, rows)


def latency(ms_values) -> dict:
    return C.latency(ms_values)


# ── per-item cache (resumable runs) ─────────────────────────────────────────
class ItemCache:
    """One JSON file per (agent, item). Holds only what the evaluator computed (scores, tiers,
    verdicts) — never document text — so the cache is safe to keep on disk."""

    def __init__(self, agent_id: str, version: str):
        self.dir = CACHE / agent_id / version
        self.dir.mkdir(parents=True, exist_ok=True)

    def _p(self, item_id: str) -> Path:
        return self.dir / (hashlib.sha1(str(item_id).encode()).hexdigest() + ".json")

    def get(self, item_id: str):
        p = self._p(item_id)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def put(self, item_id: str, value: dict):
        self._p(item_id).write_text(json.dumps(value, default=str), encoding="utf-8")
        return value

    def run(self, items, fn, key=lambda x: x["id"], label: str = "", every: int = 25, on_error: dict | None = None):
        """fn(item) -> dict for every item not already cached; progress every `every` items.
        An item whose fn raises is returned as {**on_error, "error": ...} and NOT cached, so the run
        goes on, the failure is counted by the evaluator, and a rerun tries that item again."""
        out, t0, fresh, failed = [], time.time(), 0, 0
        for i, it in enumerate(items, 1):
            k = key(it)
            got = self.get(k)
            if got is None:
                try:
                    got = self.put(k, fn(it))
                except Exception as exc:  # noqa: BLE001 - one bad document must not stop a 3-hour run
                    got = {**(on_error or {}), "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
                    failed += 1
                fresh += 1
            out.append(got)
            if i % every == 0 or i == len(items):
                rate = (time.time() - t0) / max(1, fresh)
                print(f"  [{label}] {i}/{len(items)} ({fresh} new, {failed} failed, {rate:.1f} s/item)", flush=True)
        return out


# ── run record and leakage ──────────────────────────────────────────────────
def sha256_file(p: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def leakage(test_hashes: set[str], train_dirs: list[Path], exts=(".png", ".jpg", ".jpeg", ".pdf", ".tif")) -> dict:
    """How many test items are byte-identical to a file the model was trained/tuned on."""
    train = set()
    for d in train_dirs:
        if d.exists():
            for f in d.rglob("*"):
                if f.suffix.lower() in exts and f.is_file():
                    train.add(sha256_file(f))
    hit = len(test_hashes & train)
    return {"checked_against": [str(d) for d in train_dirs], "train_files": len(train),
            "test_items": len(test_hashes), "identical": hit}


def _git(*args) -> str:
    try:
        return subprocess.run(["git", *args], cwd=str(C.API_DIR.parent), capture_output=True, text=True,
                              timeout=20).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def run_record(**extra) -> dict:
    gpu = ""
    try:
        gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.agents.llm import settings_store
        llm = settings_store.load().get("default")
    except Exception:  # noqa: BLE001
        llm = None
    return {"git_commit": _git("rev-parse", "--short", "HEAD"), "git_dirty": bool(_git("status", "--porcelain")),
            "python": sys.version.split()[0], "platform": platform.platform(), "cpu": platform.processor(),
            "gpu": gpu, "llm": llm, "split_seed": SEED, "dev_fraction": DEV_FRACTION,
            "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **extra}


# ── targets and saving ──────────────────────────────────────────────────────
def targets_real() -> dict:
    p = C.EVAL_DIR / "targets_real.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}


def _val(m):
    return m.get("value") if isinstance(m, dict) else m


def save(agent_id: str, split: str, metrics: dict, data: dict, **extra) -> dict:
    """metrics: name -> number or {"value", "ci95", ...}. data: dataset card (name, source, n, ...)."""
    t = targets_real().get(agent_id) or C.targets().get(agent_id, {})
    prim = t.get("primary", {})
    pv = _val(metrics.get(prim.get("metric"))) if prim else None
    out = {"agent_id": agent_id, "split": split, "data": data, "metrics": metrics, "target": prim,
           "primary_value": pv, "rating": C.rate(pv, prim.get("target"), prim.get("higher", True)) if prim else None,
           "secondary": [{**s, "value": _val(metrics.get(s["metric"])),
                          "rating": C.rate(_val(metrics.get(s["metric"])), s["target"], s.get("higher", True))}
                         for s in t.get("secondary", [])],
           "run": run_record(), **extra}
    RESULTS.mkdir(parents=True, exist_ok=True)
    if os.environ.get("EVAL_SMOKE"):           # tiny code-check runs never overwrite a real result
        split = f"{split}.smoke"
    (RESULTS / f"{agent_id}.{split}.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    ci = (metrics.get(prim.get("metric")) or {}).get("ci95") if isinstance(metrics.get(prim.get("metric")), dict) else None
    print(f"[{agent_id}/{split}] {prim.get('metric')}={pv} ci95={ci} target={prim.get('target')} -> {out['rating']}")
    return out
