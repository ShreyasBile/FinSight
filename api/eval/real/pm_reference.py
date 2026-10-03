"""Independent reference figures for the process-mining test, computed with pm4py (the standard
open-source process-mining library; AGPL, used only here, in the separate evaluation environment).

Run with the TOOLS interpreter:  finsight-eval-data\\.venv\\Scripts\\python.exe pm_reference.py <events.csv> <out.json>
events.csv: case_id, idx, activity, ts (UTC ISO), resource — 'complete' events only, as FinSight's views use.
"""
import json
import sys

import pandas as pd
import pm4py


def main(src: str, dst: str):
    df = pd.read_csv(src)
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    df = df.sort_values(["case_id", "ts", "idx"], kind="stable")
    log = pm4py.format_dataframe(df, case_id="case_id", activity_key="activity", timestamp_key="ts")

    variants = pm4py.get_variants(log)        # {(a1, a2, ...): count}
    var = sorted(((">".join(k), int(v if isinstance(v, int) else len(v))) for k, v in variants.items()),
                 key=lambda x: (-x[1], x[0]))
    perf, _, _ = pm4py.discover_performance_dfg(log)
    freq, _, _ = pm4py.discover_dfg(log)
    edges = []
    for (a, b), stats in perf.items():
        n = int(freq[(a, b)])
        mean_s = float(stats["mean"]) if isinstance(stats, dict) else float(stats)
        edges.append({"from": a, "to": b, "count": n, "avg_wait_hours": mean_s / 3600.0,
                      "total_wait_hours": mean_s * n / 3600.0})
    edges.sort(key=lambda e: -e["total_wait_hours"])

    per = df.groupby(["case_id", "activity"]).size().reset_index(name="n")
    rw = per[per["n"] > 1].assign(rep=lambda d: d["n"] - 1).groupby("activity").agg(repeats=("rep", "sum"),
                                                                                       cases=("case_id", "nunique"))
    rework = [{"activity": a, "repeats": int(r.repeats), "cases": int(r.cases)}
              for a, r in rw.sort_values("repeats", ascending=False).iterrows()]
    out = {"cases": int(df["case_id"].nunique()), "events": int(len(df)), "variants": len(var),
           "top_variants": [{"sequence": s, "cases": c} for s, c in var[:10]], "bottlenecks": edges[:10],
           "rework": rework[:10], "pm4py": pm4py.__version__}
    json.dump(out, open(dst, "w", encoding="utf-8"), indent=1)
    print(json.dumps({k: out[k] for k in ("cases", "events", "variants")}))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
