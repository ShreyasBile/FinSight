"""Process Mining Analyst on a real loan-application event log (BPI Challenge 2017, Dutch bank).

1. A test-split sample of BPI 2017 cases ('complete' events, the events FinSight's views use).
2. Reference figures from pm4py (pm_reference.py, run in the separate tools environment).
3. A THROWAWAY database (FinSightEvalPM) built from FinSight's own schema scripts — with the
   database name rewritten and a guard that refuses to run any statement that still names the
   real FinSight database (script 01 has ALTER DATABASE ... WITH ROLLBACK IMMEDIATE, which would
   drop every open connection to it). The sample is loaded with the insert triggers disabled, so
   no synthetic "Application created" events are mixed into the real traces, then FinSight's
   own pm.usp_refresh_variants runs.
4. The analyst runs against that database; its figures are compared with pm4py's.
5. The throwaway database is dropped.

What is compared (FinSight computes these from raw events with its own SQL): case and event
counts, number of variants, the top variants with their case counts, the bottleneck edges
(directly-follows pairs ranked by total waiting time) with their mean and total waits, and rework
(repeated activities). Durations stored on the case rows are loaded values, so not compared.
"""
from __future__ import annotations

import csv
import json
import re
import subprocess
import uuid
from datetime import datetime, timezone

from eval import common as C
from eval.real import core as R

BPI = R.RAW / "bpi2017" / "BPI_Challenge_2017.xes"
WORK = R.DATA_ROOT / "work" / "pm"
TOOLS_PY = R.DATA_ROOT / ".venv" / "Scripts" / "python.exe"
EVAL_DB = "FinSightEvalPM"
SCRIPTS = ["01-database-and-reference.sql", "02-core-tables.sql", "03-process-mining.sql",
           "04-triggers-and-procedures.sql", "05-views-process-mining.sql", "06-ui-support-tables.sql", "08-agentic-layer.sql"]
CLOSING = {"A_Pending", "A_Denied", "A_Cancelled"}


# ── 1. sample ───────────────────────────────────────────────────────────────
def build_sample(split: str, n: int) -> dict:
    from lxml import etree
    WORK.mkdir(parents=True, exist_ok=True)
    ev_csv, case_csv = WORK / f"bpi2017_{split}_{n}_events.csv", WORK / f"bpi2017_{split}_{n}_cases.csv"
    if ev_csv.exists() and case_csv.exists():
        return {"events": ev_csv, "cases": case_csv}
    traces = {}
    local = lambda t: t.rsplit("}", 1)[-1]  # noqa: E731
    for _, el in etree.iterparse(str(BPI), events=("end",), huge_tree=True):
        if local(el.tag) != "trace":
            continue
        attrs = {c.get("key"): c.get("value") for c in el if local(c.tag) != "event"}
        cid = attrs.get("concept:name")
        if cid and R.split_of(cid) == split:
            evs = []
            for i, e in enumerate(c for c in el if local(c.tag) == "event"):
                a = {x.get("key"): x.get("value") for x in e}
                if a.get("lifecycle:transition", "complete") == "complete":
                    evs.append((i, a.get("concept:name"), a.get("time:timestamp"), a.get("org:resource", "")))
            if evs:
                traces[cid] = {"events": evs, "amount": attrs.get("RequestedAmount"), "goal": attrs.get("LoanGoal")}
        el.clear()
        while el.getprevious() is not None:
            del el.getparent()[0]
    pick = R.sample([{"id": k} for k in traces], n)
    with open(ev_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "idx", "activity", "ts", "resource"])
        for p in pick:
            for i, act, ts, res in traces[p["id"]]["events"]:
                w.writerow([p["id"], i, act, _utc(ts), res])
    with open(case_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "amount", "goal", "start", "end", "closed"])
        for p in pick:
            t = traces[p["id"]]
            ts = sorted(_utc(e[2]) for e in t["events"])
            w.writerow([p["id"], t["amount"], t["goal"], ts[0], ts[-1], int(any(e[1] in CLOSING for e in t["events"]))])
    return {"events": ev_csv, "cases": case_csv}


def _utc(ts: str) -> str:
    d = datetime.fromisoformat(ts)
    return d.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="milliseconds")


# ── 2. reference ────────────────────────────────────────────────────────────
def reference(files: dict) -> dict:
    out = files["events"].with_suffix(".reference.json")
    if not out.exists():
        import os
        r = subprocess.run([str(TOOLS_PY), str(C.EVAL_DIR / "real" / "pm_reference.py"), str(files["events"]), str(out)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})    # pm4py prints an emoji banner
        if r.returncode != 0:
            raise RuntimeError(f"pm4py reference failed: {r.stderr[-800:]}")
    return json.loads(out.read_text(encoding="utf-8"))


# ── 3. throwaway database ───────────────────────────────────────────────────
def _pipe() -> str:
    import re as _re
    cmd = "Get-CimInstance Win32_Process -Filter \"Name='sqlservr.exe'\" | ForEach-Object { $_.CommandLine }"
    o = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True).stdout
    m = _re.search(r"-s(LOCALDB#[0-9A-F]+)", o)
    if not m:
        raise RuntimeError("LocalDB is not running — start FinSight from the desktop shortcut first")
    return "np:\\\\.\\pipe\\" + m.group(1) + "\\tsql\\query"


def _rewrite(sql: str) -> str:
    sql = re.sub(r"DB_ID\('FinSight'\)", f"DB_ID('{EVAL_DB}')", sql)
    sql = re.sub(r"\bCREATE DATABASE FinSight\b", f"CREATE DATABASE {EVAL_DB}", sql)
    sql = re.sub(r"\bALTER DATABASE FinSight\b", f"ALTER DATABASE {EVAL_DB}", sql)
    sql = re.sub(r"\bUSE FinSight\b", f"USE {EVAL_DB}", sql)
    # guard: no executable statement may still name the real database
    code = "\n".join(ln for ln in sql.splitlines() if not ln.strip().startswith("--"))
    bad = re.findall(r"(?i)\b(USE|ALTER DATABASE|CREATE DATABASE|DROP DATABASE)\s+\[?FinSight\]?\b|DB_ID\('FinSight'\)", code)
    if bad:
        raise RuntimeError(f"refusing to run: a statement still targets the real FinSight database: {bad[:3]}")
    return sql


def build_db(pipe: str):
    WORK.mkdir(parents=True, exist_ok=True)
    drop_db(pipe)
    for s in SCRIPTS:
        src = (C.API_DIR.parent / "docs" / "mssql" / s).read_text(encoding="utf-8")
        dst = WORK / f"evalpm_{s}"
        dst.write_text(_rewrite(src), encoding="utf-8")
        r = subprocess.run(["sqlcmd", "-S", pipe, "-E", "-I", "-b", "-f", "65001", "-i", str(dst)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise RuntimeError(f"{s} failed: {(r.stdout + r.stderr)[-600:]}")


def drop_db(pipe: str):
    import pyodbc
    with pyodbc.connect(f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={pipe};DATABASE=master;Trusted_Connection=yes;",
                        autocommit=True, timeout=30) as cn:
        cn.execute(f"IF DB_ID('{EVAL_DB}') IS NOT NULL BEGIN ALTER DATABASE {EVAL_DB} SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
                   f"DROP DATABASE {EVAL_DB}; END")


def load(pipe: str, files: dict) -> dict:
    import pyodbc
    cn = pyodbc.connect(f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={pipe};DATABASE={EVAL_DB};Trusted_Connection=yes;",
                        autocommit=False, timeout=60)
    cur = cn.cursor()
    cur.fast_executemany = True
    assert cur.execute("SELECT DB_NAME()").fetchone()[0] == EVAL_DB
    for t in ("core.loan_applications", "core.application_status_history", "core.customers"):
        cur.execute(f"DISABLE TRIGGER ALL ON {t}")
    events = list(csv.DictReader(open(files["events"], encoding="utf-8")))
    cases = list(csv.DictReader(open(files["cases"], encoding="utf-8")))
    acts = sorted({e["activity"] for e in events})
    cur.executemany("INSERT INTO pm.activity (activity_code, activity_name, process_code, activity_category) VALUES (?,?,?,?)",
                    [(a[:60], a[:120], "loan_application", "BPI 2017") for a in acts])
    act_id = {r[0]: r[1] for r in cur.execute("SELECT activity_code, activity_id FROM pm.activity WHERE activity_category = 'BPI 2017'")}
    ids = {c["case_id"]: str(uuid.uuid4()) for c in cases}
    cust = {c["case_id"]: str(uuid.uuid4()) for c in cases}
    cur.executemany("INSERT INTO core.customers (customer_id, first_name, last_name, date_of_birth, mobile, environment_code) "
                    "VALUES (?,?,?,?,?,'prod')",
                    [(cust[c["case_id"]], "BPI", c["case_id"][-12:], "1980-01-01", "0000000000") for c in cases])
    rows = []
    for c in cases:
        st, en = datetime.fromisoformat(c["start"]), datetime.fromisoformat(c["end"])   # datetimes, not strings
        dur = int((en - st).total_seconds())
        closed = int(c["closed"])
        # application_no is NVARCHAR(20): "Application_1000086665" -> "BPI1000086665"
        rows.append((ids[c["case_id"]], ("BPI" + "".join(ch for ch in c["case_id"] if ch.isdigit()))[:20], cust[c["case_id"]], "personal_loan",
                     "rejected" if closed else "submitted", float(c["amount"] or 0) or None, st, st, st,
                     en if closed else None, dur if closed else None, closed, en))
    cur.fast_executemany = False          # this insert mixes NULLs and values per column
    cur.executemany("""INSERT INTO core.loan_applications (application_id, application_no, customer_id, loan_product, status_code,
                           loan_amount, applied_at, submitted_at, case_started_at, case_ended_at, case_duration_sec,
                           is_case_closed, last_activity_at, environment_code)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'prod')""", rows)
    cur.fast_executemany = True
    seq: dict = {}
    ev_rows = []
    for e in sorted(events, key=lambda e: (e["case_id"], e["ts"], int(e["idx"]))):
        k = e["case_id"]
        seq[k] = seq.get(k, 0) + 1
        ev_rows.append(("loan_application", ids[k], seq[k], act_id[e["activity"][:60]], e["activity"][:120], "complete",
                        datetime.fromisoformat(e["ts"]), "client", (e["resource"] or "")[:150], "human", 0, "prod", 1))
    cur.executemany("""INSERT INTO pm.event_log (process_code, case_id, event_seq, activity_id, activity_name,
                           lifecycle_transition, occurred_at, timestamp_source, resource_name, resource_type_code,
                           is_automated, environment_code, is_valid_for_mining)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", ev_rows)
    cur.execute("EXEC pm.usp_refresh_variants @process_code = 'loan_application'")
    cn.commit()
    cn.close()
    return {"cases": len(cases), "events": len(ev_rows), "activities": len(acts)}


# ── 4. compare ──────────────────────────────────────────────────────────────
def _close(a, b, rel=0.005) -> bool:
    return a is not None and b is not None and abs(float(a) - float(b)) <= rel * max(abs(float(b)), 1e-9) + 1e-6


def eval_pm(split: str = "test", n: int = 2000) -> dict:
    from app.agents.registry import get_agent
    from app.config import settings
    files = build_sample(split, n)
    ref = reference(files)
    pipe = _pipe()
    build_db(pipe)
    try:
        loaded = load(pipe, files)
        old = (settings.db_server, settings.db_name, settings.db_enabled)
        settings.db_server, settings.db_name, settings.db_enabled = pipe, EVAL_DB, True
        try:
            with C.Timer() as t:
                f = get_agent("process_mining_analyst").execute({"process_code": "loan_application", "include_demo": False}).payload
        finally:
            settings.db_server, settings.db_name, settings.db_enabled = old
    finally:
        drop_db(pipe)
    checks = []

    def check(name, got, want, ok):
        checks.append({"figure": name, "analyst": got, "pm4py": want, "match": bool(ok)})
    check("cases", f["overview"]["cases"], ref["cases"], f["overview"]["cases"] == ref["cases"])
    check("events", f["events"]["events"], ref["events"], f["events"]["events"] == ref["events"])
    check("variants", f["conformance"]["variants"], ref["variants"], f["conformance"]["variants"] == ref["variants"])
    for i, (a, b) in enumerate(zip(f["top_variants"][:5], ref["top_variants"][:5]), 1):
        check(f"variant #{i} cases", a["case_count"], b["cases"], a["case_count"] == b["cases"])
        check(f"variant #{i} sequence", a["activity_sequence"][:60], b["sequence"][:60], a["activity_sequence"] == b["sequence"])
    refb = {(e["from"], e["to"]): e for e in ref["bottlenecks"]}
    for i, b in enumerate(f["bottlenecks"][:5], 1):
        r = refb.get((b["from_activity"], b["to_activity"]))
        check(f"bottleneck #{i} edge", f"{b['from_activity']} -> {b['to_activity']}",
              f"{ref['bottlenecks'][i - 1]['from']} -> {ref['bottlenecks'][i - 1]['to']}",
              r is not None and ref["bottlenecks"][i - 1] is r)
        if r:
            check(f"bottleneck #{i} count", b["transition_count"], r["count"], b["transition_count"] == r["count"])
            check(f"bottleneck #{i} avg wait h", round(float(b["avg_wait_hours"]), 3), round(r["avg_wait_hours"], 3),
                  _close(b["avg_wait_hours"], r["avg_wait_hours"]))
            check(f"bottleneck #{i} total wait h", round(float(b["total_wait_hours"]), 1), round(r["total_wait_hours"], 1),
                  _close(b["total_wait_hours"], r["total_wait_hours"]))
    refr = {x["activity"]: x for x in ref["rework"]}
    for i, x in enumerate(f["rework"][:5], 1):
        r = refr.get(x["activity_name"])
        check(f"rework #{i} repeats ({x['activity_name']})", x["repeats"], r and r["repeats"], r is not None and x["repeats"] == r["repeats"])
    ok = sum(c["match"] for c in checks)
    metrics = {"figure_agreement": R.wilson(ok, len(checks)),
               "top_variant_match": 1.0 if f["top_variants"] and ref["top_variants"] and
               f["top_variants"][0]["activity_sequence"] == ref["top_variants"][0]["sequence"] else 0.0,
               "checks": checks, "analyst_seconds": round(t.ms / 1000, 2), "findings": f.get("findings")}
    data = {"name": "BPI Challenge 2017 (31,509 loan applications, Dutch bank), 'complete' events", "split": split,
            "sample_cases": loaded["cases"], "events": loaded["events"], "activities": loaded["activities"],
            "reference": f"pm4py {ref.get('pm4py')} (separate environment)", "database": f"{EVAL_DB} (dropped after the run)"}
    return R.save("process_mining_analyst", split, metrics, data)
