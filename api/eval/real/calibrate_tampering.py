"""Refit the tampering fusion (config.yaml: fusion.*) on the DEVELOPMENT split of real documents.

Why: on the real test split (Find it again! receipts + SIDTD ID templates) the fusion as configured on
synthetic pages flagged 84% of genuine documents and ranked forged below genuine (AUC 0.36) — the
config's own comment says to refit once labelled verdicts exist. They now do.

What may change: only the fusion's numbers — per-signal weights, single-signal confidences, the
corroboration factor and the two tier cutoffs. Signals themselves and the Stage-1 gate are untouched,
so the per-signal scores already cached for the test split stay exact and the test split is re-scored
ONCE with the chosen config (no re-run, no second look).

Signals with no evidence in this data (pdf_forensics: no PDFs; authenticity: off on these paths) keep
their numbers, and two design rules are enforced on every candidate:
  * a lone authenticity verdict (0.85) never reaches HIGH;
  * a PDF utility re-save (0.60) lands in MEDIUM, not LOW and not HIGH.
Selection rule (fixed before the search): maximise dev forged recall subject to dev genuine flagged
<= 30% and genuine HIGH <= 10% (the registered secondary targets); ties -> higher dev AUC.

  bash run_until_done.sh <log> --agent calibrate_tampering   # dev run (cached) + search; writes the proposal
"""
from __future__ import annotations

import copy
import json
import random

from eval import common as C
from eval.real import core as R

TUNED = ("metadata", "business_rules", "copy_move", "ela", "noise", "cnn", "photo_region")
W_GRID = (0.0, 0.05, 0.10, 0.15, 0.20, 0.30)
CONF_GRID = (0.0, 0.15, 0.25, 0.32, 0.45, 0.55, 0.70, 0.85, 1.0)
CORRO_GRID = (0.0, 0.25, 0.5)


def dev_rows(n_findit_genuine: int = 130, n_sidtd: int = 200):
    from eval.real import evidence_real as E
    f = E.findit_items("dev")
    forged = [x for x in f if x["forged"]]
    genuine = R.sample([x for x in f if not x["forged"]], n_findit_genuine)
    items = forged + genuine + E.sidtd_items("dev", n_sidtd)
    cache = R.ItemCache("evidence", "prod-v1-dev")
    out = cache.run(items, E.production_run, label="dev production", every=25,
                    on_error={"tier": None, "score": None, "signals": {}, "ms": None})
    rows = [{**{k: it[k] for k in ("id", "set", "forged", "kind")}, **o} for it, o in zip(items, out) if o.get("tier")]
    # genuine phone photos of an ID card (MIDV-500 dev frames): what real camera captures score
    rows += [{k: v for k, v in r.items() if k not in ("tier_as_run", "score_as_run")} for r in E.midv_rows("dev")]
    return rows


def _signals(sig: dict):
    from app.tampering.signals.base import SignalResult
    return [SignalResult(name=k, available=True, score=float(v)) for k, v in sig.items() if v is not None]


def score_all(rows, fusion_cfg) -> list[float]:
    from app.tampering import fusion
    from app.tampering.config import CFG
    old = CFG.fusion
    CFG.fusion = fusion_cfg
    try:
        return [fusion.fuse(_signals(r["signals"]), r.get("doc_type"))["fused_score"] for r in rows]
    finally:
        CFG.fusion = old


def _cfg(base, weights, conf, corro, med, high, floors=None):
    from types import SimpleNamespace
    c = copy.deepcopy(base)
    if floors is not None:
        c.genuine_floor = dict(floors)
    c.weights = dict(weights)
    c.single_signal_confidence = SimpleNamespace(**conf)
    c.corroboration_factor = corro
    c.tier_medium, c.tier_high = med, high
    return c


def _rules_ok(conf: dict, med: float, high: float) -> bool:
    return 0.85 * conf["authenticity"] < high and med <= 0.60 * conf["pdf_forensics"] < high


def evaluate(rows, scores, med, high) -> dict:
    f = [s for r, s in zip(rows, scores) if r["forged"]]
    g = [s for r, s in zip(rows, scores) if not r["forged"]]
    return {"forged_recall": sum(s >= med for s in f) / len(f), "forged_high": sum(s >= high for s in f) / len(f),
            "genuine_flagged": sum(s >= med for s in g) / len(g), "genuine_high": sum(s >= high for s in g) / len(g),
            "auc": C.auc(f, g)}


def _tiers(rows, scores, conf):
    """Lowest cutoffs meeting the genuine limits on dev (and the design rules)."""
    g = sorted(s for r, s in zip(rows, scores) if not r["forged"])
    q = lambda p: g[min(len(g) - 1, int(p * len(g)))]  # noqa: E731
    med = max(0.15, round(q(0.70) + 1e-4, 4))
    high = max(med + 0.05, round(q(0.90) + 1e-4, 4))
    # the design rules bound the cutoffs: PDF re-save stays MEDIUM, lone authenticity stays below HIGH
    med = min(med, 0.60 * conf["pdf_forensics"])
    high = max(high, 0.85 * conf["authenticity"] + 1e-4)
    return med, high


def search(rows, n: int = 3000, seed: int = R.SEED):
    from app.tampering.config import CFG
    base = CFG.fusion
    conf0 = vars(base.single_signal_confidence)
    rng = random.Random(seed)
    cur = evaluate(rows, score_all(rows, base), base.tier_medium, base.tier_high)
    best = (cur["forged_recall"] if cur["genuine_flagged"] <= 0.30 and cur["genuine_high"] <= 0.10 else -1,
            cur["auc"], {"weights": dict(base.weights), "conf": conf0, "corro": base.corroboration_factor,
                         "med": base.tier_medium, "high": base.tier_high}, cur)
    for i in range(n):
        w = dict(base.weights)
        conf = dict(conf0)
        for s in TUNED:
            w[s] = rng.choice(W_GRID)
            conf[s] = rng.choice(CONF_GRID)
        if sum(w[s] for s in TUNED) == 0:
            continue
        corro = rng.choice(CORRO_GRID)
        cand = _cfg(base, w, conf, corro, 0.3, 0.6)
        sc = score_all(rows, cand)
        med, high = _tiers(rows, sc, conf)
        if not _rules_ok(conf, med, high):
            continue
        ev = evaluate(rows, sc, med, high)
        ok = ev["genuine_flagged"] <= 0.30 + 1e-9 and ev["genuine_high"] <= 0.10 + 1e-9
        key = (ev["forged_recall"] if ok else -1, ev["auc"] or 0)
        if key > best[:2]:
            best = (*key, {"weights": w, "conf": conf, "corro": corro, "med": med, "high": high}, ev)
        if (i + 1) % 500 == 0:
            print(f"  [search] {i + 1}/{n} best dev recall={best[0]:.3f} auc={best[1]:.3f}", flush=True)
    return best, cur


def per_signal(rows) -> dict:
    out = {}
    for s in sorted({k for r in rows for k in r["signals"]}):
        f = [r["signals"][s] for r in rows if r["forged"] and r["signals"].get(s) is not None]
        g = [r["signals"][s] for r in rows if not r["forged"] and r["signals"].get(s) is not None]
        out[s] = {"auc": round(C.auc(f, g), 3) if f and g else None, "n_forged": len(f), "n_genuine": len(g),
                  "genuine_ge_0.5": round(sum(v >= 0.5 for v in g) / len(g), 3) if g else None,
                  "forged_ge_0.5": round(sum(v >= 0.5 for v in f) / len(f), 3) if f else None}
    return out


def calibrate(split: str = "dev"):
    """Run through run_real (--agent calibrate_tampering): dev data only, whatever --split says."""
    rows = dev_rows()
    print(json.dumps({"dev_rows": len(rows), "forged": sum(r["forged"] for r in rows)}))
    print(json.dumps(per_signal(rows), indent=1))
    best, cur = search(rows)
    prop = {"current_on_dev": cur, "chosen_on_dev": best[3], "config": best[2], "dev_n": len(rows),
            "per_signal_dev": per_signal(rows),
            "by_dataset_dev": {s: evaluate([r for r in rows if r["set"] == s],
                                           score_all([r for r in rows if r["set"] == s],
                                                     _cfg(__import__("app.tampering.config", fromlist=["CFG"]).CFG.fusion,
                                                          best[2]["weights"], best[2]["conf"], best[2]["corro"],
                                                          best[2]["med"], best[2]["high"])),
                                           best[2]["med"], best[2]["high"]) for s in ("findit", "sidtd")}}
    p = R.RESULTS / "tampering_fusion_proposal.json"
    p.write_text(json.dumps(prop, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: prop[k] for k in ("current_on_dev", "chosen_on_dev", "config")}, indent=1, default=str))


