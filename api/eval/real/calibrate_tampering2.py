"""Second, targeted calibration pass for the tampering fusion (DEVELOPMENT data only).

The first pass (calibrate_tampering.py: random search over every weight and confidence) cut the false
flags but also moved numbers for signals this data barely exercises (business rules: 11 dev documents).
The dev analysis showed where the false flags actually come from: the corroboration path in which one
of the two "agreeing" signals is the CNN — a classifier trained on synthetic pages that scores >= 0.5 on
80% of real GENUINE documents. So this pass changes only what the evidence supports:

  * pixel signals only (cnn, ela, noise, copy_move, photo_region): their single-signal confidences and
    their weights in the average (on real scans these signals sit at mid values on GENUINE pages, so
    the average alone crossed the MEDIUM cut-off). business_rules, pdf_forensics, metadata and authenticity keep their numbers
    (deterministic observations; too few dev cases to justify moving them), as do both tier cut-offs
    and the corroboration factor, so every documented rule of the fusion still holds.

Selection rule (same as before, fixed before looking): maximise dev forged recall subject to dev genuine
flagged <= 30% and genuine HIGH <= 10%; ties -> higher dev AUC, then the smallest change from today.
"""
from __future__ import annotations

import json
import random

from eval import common as C
from eval.real import core as R
from eval.real.calibrate_tampering import _cfg, dev_rows, evaluate, score_all

PIXEL = ("cnn", "ela", "noise", "copy_move", "photo_region")
CONF = (0.0, 0.10, 0.20, 0.32, 0.45, 0.55, 0.65)
WEIGHT = (0.0, 0.03, 0.06, 0.12, 0.17)
N_SEARCH = 6000


def calibrate2(split: str = "dev", optimise_on: str = "findit") -> dict:
    from app.tampering.config import CFG
    rows = dev_rows()
    base = CFG.fusion
    conf0 = dict(vars(base.single_signal_confidence))
    med, high = base.tier_medium, base.tier_high

    def run(conf, weights):
        sc = score_all(rows, _cfg(base, weights, conf, base.corroboration_factor, med, high))
        ev = evaluate(rows, sc, med, high)
        by = {s: evaluate([r for r in rows if r["set"] == s], [x for r, x in zip(rows, sc) if r["set"] == s], med, high)
              for s in ("findit", "sidtd")}
        return ev, by
    w0 = dict(base.weights)
    current, current_by = run(conf0, w0)
    rng = random.Random(R.SEED)
    best = None
    for i in range(N_SEARCH):
        conf, w = dict(conf0), dict(w0)
        for k in PIXEL:
            conf[k], w[k] = rng.choice(CONF), rng.choice(WEIGHT)
        ev, by = run(conf, w)
        tgt = by[optimise_on] if optimise_on else ev
        ok = tgt["genuine_flagged"] <= 0.30 + 1e-9 and tgt["genuine_high"] <= 0.10 + 1e-9
        change = sum(abs(conf[k] - conf0[k]) + abs(w[k] - w0[k]) for k in PIXEL)
        key = (tgt["forged_recall"] if ok else -1, round(tgt["auc"] or 0, 4), -round(change, 3))
        if best is None or key > best[0]:
            best = (key, conf, w, ev, by)
        if (i + 1) % 1500 == 0:
            print(f"  [search] {i + 1}/{N_SEARCH} best {best[0]}", flush=True)
    _, conf, w, ev, by = best
    keys = list(PIXEL)
    w_cnn = w["cnn"]
    out = {"dev_n": len(rows), "optimised_on": optimise_on or "all", "constraints_met": best[0][0] >= 0, "current_on_dev": current, "current_by_dataset": current_by, "chosen_on_dev": ev,
           "chosen_by_dataset": by, "config": {"single_signal_confidence": {k: conf[k] for k in keys}, "weights": {k: w[k] for k in keys}},
           "unchanged": ["business_rules", "pdf_forensics", "metadata", "authenticity", "tier_medium", "tier_high",
                         "corroboration_factor"]}
    (R.RESULTS / "tampering_fusion_proposal2.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    r4 = lambda d: {k: round(v, 4) if isinstance(v, float) else v for k, v in d.items()}  # noqa: E731
    print("current", r4(current))
    print("chosen ", r4(ev), out["config"], "constraints met:", out["constraints_met"])
    for s in ("findit", "sidtd"):
        print(" ", s, "current", r4(current_by[s]), "| chosen", r4(by[s]))
    return out
