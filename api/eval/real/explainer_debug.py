"""Development diagnostic for the Tamper Explainer (dev documents only): which figures does the local
model cite that the findings do not contain?   python -m eval.real.explainer_debug [n]"""
import asyncio
import sys

from eval import common as C


def main(n: int = 24):
    C.setup_env(provider="ollama", db=False)
    from app.agents.base import grounded
    from app.tampering.explain import explain
    from eval.real import calibrate_tampering as K
    from eval.real import core as R
    from eval.real import evidence_real as E
    rows = [E._refused(r) for r in K.dev_rows() if r["set"] != "midv"]
    by_id = {it["id"]: it for it in E.findit_items("dev") + E.sidtd_items("dev", 200)}
    flagged = R.sample([r for r in rows if r["tier"] in ("MEDIUM", "HIGH")], n, salt="explainer-debug")
    ok = total = 0
    kinds = {}
    for r in flagged:
        t = {"risk_tier": r["tier"], "risk_score": r["score"], "explanations": r["explanations"],
             "signals": [{"name": k, "score": v, "available": True} for k, v in (r.get("signals") or {}).items()]}
        res = asyncio.run(explain(r["doc_type"], t, by_id[r["id"]]["path"].read_bytes()))
        if res is None:
            kinds["no_answer"] = kinds.get("no_answer", 0) + 1
            continue
        ev = {"findings": t["explanations"], "signals": t["signals"]}
        sents = [res.get("summary", "")] + list(res.get("issues", []))
        bad_all = set()
        for s in sents:
            g, bad = grounded(s, ev)
            bad_all |= bad
        total += 1
        ok += not bad_all
        if bad_all and len([k for k in kinds if k.startswith("ex")]) < 8:
            allowed = set()
            from app.agents.base import _numbers
            _numbers(ev, allowed)
            near = {b: min(allowed, key=lambda a: abs(a - b)) if allowed else None for b in bad_all}
            kinds[f"ex{len(kinds)}"] = {"cited_not_in_findings": sorted(bad_all), "nearest_in_findings": near,
                                        "sentence": next(s for s in sents if not grounded(s, ev)[0])[:160]}
    print("fully grounded", ok, "/", total)
    for k, v in kinds.items():
        print(k, v)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 24)
