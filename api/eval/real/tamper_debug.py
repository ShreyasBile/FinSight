"""Development diagnostic: what still flags genuine receipts once the pixel signals are floored?
(dev data only; prints aggregate counts)   python -m eval.real.tamper_debug"""
import collections

from eval import common as C


def main():
    C.setup_env(provider="ollama", db=False)
    from app.tampering import fusion
    from app.tampering.config import CFG
    from eval.real import calibrate_tampering as K
    rows = K.dev_rows()
    base = CFG.fusion
    conf0, w0 = dict(vars(base.single_signal_confidence)), dict(base.weights)
    pix = ("cnn", "ela", "noise", "copy_move", "photo_region")
    gen = {s: sorted(r["signals"][s] for r in rows if not r["forged"] and r["signals"].get(s) is not None) for s in pix}
    for p in (0.90, 0.95, 0.99):
        floors = {s: round(min(gen[s][min(len(gen[s]) - 1, int(p * len(gen[s])))], 0.99), 4) for s in pix}
        cfg = K._cfg(base, w0, conf0, base.corroboration_factor, base.tier_medium, base.tier_high, floors)
        sc = K.score_all(rows, cfg)
        for st in ("findit", "sidtd"):
            rs = [(r, x) for r, x in zip(rows, sc) if r["set"] == st]
            ev = K.evaluate([r for r, _ in rs], [x for _, x in rs], base.tier_medium, base.tier_high)
            print(f"p{int(p * 100)} {st}: " + ", ".join(f"{k}={v:.3f}" for k, v in ev.items()), floors if st == "findit" else "")
        old, CFG.fusion = CFG.fusion, cfg
        why = collections.Counter()
        for r in rows:
            if r["set"] == "findit" and not r["forged"]:
                out = fusion.fuse(K._signals(r["signals"]))
                if out["risk_tier"] != "LOW":
                    c = out["contributions"]
                    why["corro:" + "+".join(sorted(c["_corroborated_signals"]["signals"])) if "_corroborated_signals" in c
                        else ("dom:" + c["_dominant_signal"]["signal"] if "_dominant_signal" in c else "avg")] += 1
        CFG.fusion = old
        print("   genuine receipts still flagged by:", why.most_common(6))
    for sig in ("metadata", "business_rules", "copy_move"):
        for st in ("findit", "sidtd"):
            g = [r["signals"][sig] for r in rows if r["set"] == st and not r["forged"] and r["signals"].get(sig) is not None]
            f = [r["signals"][sig] for r in rows if r["set"] == st and r["forged"] and r["signals"].get(sig) is not None]
            print(sig, st, "genuine", len(g), collections.Counter(round(v, 1) for v in g).most_common(4),
                  "| forged", len(f), collections.Counter(round(v, 1) for v in f).most_common(4))


if __name__ == "__main__":
    main()
