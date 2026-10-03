"""Tampering fusion, pass 4 (DEVELOPMENT data only): which pixel signals are evidence ALONE?

After the genuine floors (pass 3) a third of genuine real receipts were still flagged, and the fraud
fusion test showed what that costs: 73% of clean two-document cases came out MEDIUM. The floors cannot
remove these flags - ELA and noise SATURATE (score 1.0) on ordinary genuine scans, so they sit above
any floor.

Rule (fixed, no search): a pixel signal needs corroboration when, in every document family where it
fires alone at all, it fires alone on GENUINE development documents at least as often as on forged ones
(a likelihood ratio <= 1: the lone hit says nothing about forgery). "Alone" = it is the only signal with
evidence (calibrated score x confidence >= corroboration_min). Such a signal keeps its weight in the
average and counts in full once any other signal backs it; it just cannot raise the tier by itself.
Weights, confidences, floors, the corroboration factor and both tier cut-offs are unchanged.

    python -m eval.real.run_real --agent calibrate_tampering4 --split dev
"""
from __future__ import annotations

import copy
import json

from eval.real import core as R
from eval.real.calibrate_tampering import dev_rows, evaluate, score_all

PIXEL = ("cnn", "ela", "noise", "copy_move", "photo_region")
FAMILY = {"findit": "page", "sidtd": "id", "midv": "id"}
MIN = 0.10


def lone_rates(rows) -> dict:
    """Per family and pixel signal: share of forged / genuine documents where it is the ONLY signal with evidence."""
    from app.tampering import fusion
    from app.tampering.config import CFG
    conf = vars(CFG.fusion.single_signal_confidence)
    out = {}
    for fam in ("page", "id"):
        rs = [r for r in rows if FAMILY[r["set"]] == fam]
        f, g = [r for r in rs if r["forged"]], [r for r in rs if not r["forged"]]
        out[fam] = {"n_forged": len(f), "n_genuine": len(g)}
        for s in PIXEL:
            def lone(r):
                d = {k: fusion.calibrated(k, float(v), fam) * conf.get(k, 0.0) for k, v in r["signals"].items() if v is not None}
                return d.get(s, 0.0) >= MIN and not any(v >= MIN for k, v in d.items() if k != s)
            kf, kg = sum(lone(r) for r in f), sum(lone(r) for r in g)
            out[fam][s] = {"forged": kf, "genuine": kg, "forged_rate": round(kf / len(f), 4) if f else None,
                           "genuine_rate": round(kg / len(g), 4) if g else None}
    return out


def choose(rates) -> list[str]:
    needs = []
    for s in PIXEL:
        fams = [rates[fam][s] for fam in rates if rates[fam][s]["forged"] + rates[fam][s]["genuine"] > 0
                and rates[fam]["n_forged"]]
        if fams and all((x["genuine_rate"] or 0) >= (x["forged_rate"] or 0) for x in fams):
            needs.append(s)
    return needs


def calibrate4(split: str = "dev") -> dict:
    from app.tampering.config import CFG
    rows = dev_rows()
    base = CFG.fusion
    med, high = base.tier_medium, base.tier_high
    rates = lone_rates(rows)
    needs = choose(rates)

    def run(needs_list):
        c = copy.deepcopy(base)
        c.needs_corroboration, c.corroboration_min = list(needs_list), MIN
        sc = score_all(rows, c)
        by = {}
        for s in ("findit", "sidtd"):
            rs = [(r, x) for r, x in zip(rows, sc) if r["set"] == s]
            by[s] = evaluate([r for r, _ in rs], [x for _, x in rs], med, high)
        ph = [x for r, x in zip(rows, sc) if r["set"] == "midv"]
        by["midv_genuine_photos"] = {"genuine_flagged": sum(x >= med for x in ph) / len(ph),
                                     "genuine_high": sum(x >= high for x in ph) / len(ph), "n": len(ph)} if ph else None
        return by
    before, after = run([]), run(needs)
    out = {"rule": "a pixel signal needs corroboration when its lone hits are at least as frequent on genuine as on "
                   "forged development documents in every family where it fires alone",
           "corroboration_min": MIN, "lone_hit_rates": rates, "needs_corroboration": needs,
           "before_on_dev": before, "after_on_dev": after,
           "unchanged": ["weights", "single_signal_confidence", "genuine_floor", "corroboration_factor", "tier_medium", "tier_high"]}
    (R.RESULTS / "tampering_fusion_proposal4.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    r3 = lambda d: {k: round(v, 3) if isinstance(v, float) else v for k, v in (d or {}).items()}  # noqa: E731
    for fam, v in rates.items():
        print(fam, {k: (x["forged"], x["genuine"]) if isinstance(x, dict) else x for k, x in v.items()})
    print("needs corroboration:", needs)
    for s in before:
        print(f"  {s}: before {r3(before[s])}")
        print(f"  {' ' * len(s)}  after  {r3(after[s])}")
    return out
