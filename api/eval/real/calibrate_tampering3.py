"""Tampering fusion — the adopted calibration (DEVELOPMENT data only): per-family genuine floors.

Why not the first two passes: re-weighting cannot help when the problem is each pixel signal's SCALE.
Pass 1 (random search over every number) moved settings for signals this data never exercises; pass 2
showed that pushing pixel weights to zero makes a lone editor tag HIGH. On real captures the pixel
signals sit high on GENUINE documents — the CNN at >= 0.5 on 80% of them, ELA / noise / copy-move at
their maximum on many — so the fix states, per signal and per document family, what genuine real
documents reach, and counts only what exceeds it (app/tampering/fusion.py::calibrated).

Rule (fixed, no search): floor = the 95th percentile of the signal's score on genuine development
documents of that family —
    page  real scanned receipts (Find it again!, train + val, genuine)
    id    real phone photos of an ID card (MIDV-500 dev frames) + SIDTD genuine ID templates (dev)
Weights, single-signal confidences, the corroboration factor and both tier cut-offs stay as designed,
so every documented rule of the fusion still holds (a lone authenticity verdict stays below HIGH, a PDF
utility re-save stays MEDIUM, a failed business rule decides alone).

A search over percentiles was run first (tamper_debug.py, 2026-10-02): no setting reaches "genuine
flagged <= 30%" with more recall, because genuine receipts whose signals SATURATE stay above any
floor; the 95th percentile is where genuine HIGH falls to ~1% and little recall is left to lose.
"""
from __future__ import annotations

import json

from eval.real import core as R
from eval.real.calibrate_tampering import _cfg, dev_rows, evaluate, score_all

PIXEL = ("cnn", "ela", "noise", "copy_move", "photo_region")
PCT = 0.95
FAMILY = {"findit": "page", "sidtd": "id", "midv": "id"}


def floors_from(rows) -> dict:
    out = {}
    for fam in ("page", "id"):
        out[fam] = {}
        for s in PIXEL:
            g = sorted(r["signals"][s] for r in rows if not r["forged"] and FAMILY[r["set"]] == fam
                       and r["signals"].get(s) is not None)
            if len(g) >= 20:
                out[fam][s] = round(min(g[min(len(g) - 1, int(PCT * len(g)))], 0.99), 3)
    return out


def calibrate3(split: str = "dev") -> dict:
    from app.tampering.config import CFG
    rows = dev_rows()
    base = CFG.fusion
    conf0, w0 = dict(vars(base.single_signal_confidence)), dict(base.weights)
    med, high = base.tier_medium, base.tier_high
    floors = floors_from(rows)

    def run(fl):
        sc = score_all(rows, _cfg(base, w0, conf0, base.corroboration_factor, med, high, fl))
        by = {}
        for s in ("findit", "sidtd"):
            rs = [(r, x) for r, x in zip(rows, sc) if r["set"] == s]
            by[s] = evaluate([r for r, _ in rs], [x for _, x in rs], med, high)
        ph = [x for r, x in zip(rows, sc) if r["set"] == "midv"]
        by["midv_genuine_photos"] = {"genuine_flagged": sum(x >= med for x in ph) / len(ph), "genuine_high": sum(x >= high for x in ph) / len(ph),
                                     "n": len(ph)} if ph else None
        return by
    before, after = run({}), run(floors)
    out = {"rule": f"floor = {int(PCT * 100)}th percentile of genuine development scores, per family and pixel signal",
           "dev_counts": {s: sum(1 for r in rows if r["set"] == s) for s in ("findit", "sidtd", "midv")},
           "genuine_floor": floors, "before_on_dev": before, "after_on_dev": after,
           "unchanged": ["weights", "single_signal_confidence", "corroboration_factor", "tier_medium", "tier_high"]}
    (R.RESULTS / "tampering_fusion_proposal3.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    r3 = lambda d: {k: round(v, 3) if isinstance(v, float) else v for k, v in (d or {}).items()}  # noqa: E731
    print("floors", floors)
    for s in before:
        print(f"  {s}: before {r3(before[s])}")
        print(f"  {' ' * len(s)}  after  {r3(after[s])}")
    return out
