"""Development check (dev split): when a wrong document uploaded as a PAN card passes the identity gate,
how did its 'PAN number' become valid - read as such by the model, or made valid by the cross-read?
    python -m eval.real.identity_dev2"""
import collections
import json

from eval import common as C


def main(n_ids: int = 40, n_receipts: int = 20):
    C.setup_env(provider="ollama", db=False)
    from app.agents.flows import document_flows as flows
    from app.agents.tools.doc_keywords import scores
    from eval.real import core as R
    from eval.real import evidence_real as E
    from eval.real import intake_real as I
    flows._parse_statement_later = lambda *a, **k: None
    docs = [("foreign_id", it["id"], it["path"].read_bytes(), it["mime"]) for it in E.sidtd_items("dev", n_ids // 2)][:n_ids] + \
           [("receipt", it["id"], I._png(it["image"]), "image/png") for it in I.sroie_items("dev", n_receipts)]
    cache = R.ItemCache("doc_identity", "wrong-as-pan-dev-v1")
    agg = collections.Counter()
    for kind, key, data, mime in docs:
        r = cache.get(key)
        if r is None:
            res = flows.run_extract(data, mime, "pan", "x")
            ext = res.get("extraction") or {}
            text = ((res.get("preprocess") or {}).get("page_text") or "")
            r = cache.put(key, {"passed": bool((res.get("identity") or {}).get("ok")),
                                "cross_read": (ext.get("cross_read") or {}).get("pan_number"),
                                "says_pan": scores(text)["pan"] > 0, "readable": len(text.strip()) >= 40})
        agg[(kind, "PASSED" if r["passed"] else "caught", r["cross_read"], "says_pan" if r["says_pan"] else "no heading")] += 1
    for k, v in sorted(agg.items(), key=str):
        print(v, k)
    print(json.dumps({"n": len(docs), "passed": sum(v for k, v in agg.items() if k[1] == "PASSED")}))


if __name__ == "__main__":
    main()
