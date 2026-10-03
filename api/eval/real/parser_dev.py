"""Development check for the statement parser: row recall / precision on the DEV statements
(same matching as money_real.statement_parse; nothing cached, nothing reported).

  python -m eval.real.parser_dev [n_scanned]
"""
from __future__ import annotations

import json
import sys

from eval import common as C


def main(n_scanned: int = 4):
    C.setup_env(provider="ollama", db=False)
    from app.agents.tools.transactions import parse_best
    from eval.real.intake_real import any_date, statement_items, truth_rows
    items = statement_items("dev", None)
    items = [i for i in items if i["kind"] == "digital"] + [i for i in items if i["kind"] == "scanned"][:n_scanned]
    key = lambda d, a, b: (any_date(d), round(float(a or 0), 0), round(float(b or 0), 0))  # noqa: E731
    agg = {}
    for it in items:
        truth = truth_rows(json.loads(it["truth"].read_text(encoding="utf-8")))
        got = parse_best(it["path"].read_bytes(), None)
        rows = got.get("transactions") or []
        want = {}
        for x in truth:
            k = key(x["date"], x["credit"] or x["debit"], x["balance"])
            want.setdefault(k, []).append(x["credit"] > 0)
        hit = side_ok = 0
        for r in rows:
            k = key(r["date"], r["credit"] or r["debit"], r["balance"])
            if want.get(k):
                side_ok += want[k].pop() == (r["credit"] > 0)
                hit += 1
        a = agg.setdefault(it["layout"], {"truth": 0, "parsed": 0, "hit": 0, "n": 0, "recon": 0, "closing_ok": 0, "src": set()})
        a["truth"] += len(truth)
        a["parsed"] += len(rows)
        a["hit"] += hit
        a["side"] = a.get("side", 0) + side_ok
        a["n"] += 1
        a["recon"] += sum(1 for r in rows if r.get("reconciled"))
        a["closing_ok"] += bool(got.get("closing_matches"))
        a["src"].add(got.get("source"))
    for k, a in sorted(agg.items()):
        print(k, {"statements": a["n"], "recall": round(a["hit"] / a["truth"], 4),
                  "precision": round(a["hit"] / a["parsed"], 4) if a["parsed"] else None,
                  "reconciled": round(a["recon"] / a["parsed"], 4) if a["parsed"] else None,
                  "side_correct": round(a.get("side", 0) / a["hit"], 4) if a["hit"] else None,
                  "closing_matches": a["closing_ok"], "source": sorted(a["src"])})


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 4)
