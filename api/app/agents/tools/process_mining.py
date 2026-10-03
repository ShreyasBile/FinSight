"""Read-only process-mining facts from the pm.* / agent.* views.

Every figure the analyst reports comes from one of these SELECTs; the optional
narration is grounded against the returned facts (any number it states must
be among them). Nothing here writes.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal


def _rows(cur, sql: str, params=()) -> list[dict]:
    cur.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [{c: _plain(v) for c, v in zip(cols, r)} for r in cur.fetchall()]


def _plain(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat(timespec="seconds")
    return v


def _r(v, n=1):
    return round(float(v), n) if v is not None else None


def insights(process_code: str = "loan_application", include_demo: bool = True) -> dict:
    from ...config import settings
    if not settings.db_enabled:
        return {"available": False, "reason": "SQL Server disabled"}
    import pyodbc
    envs = ("prod", "demo") if include_demo else ("prod",)
    env_sql = f"({','.join('?' * len(envs))})"
    with pyodbc.connect(settings.db_connection_string, timeout=5) as conn:
        cur = conn.cursor()
        ov = _rows(cur, f"""SELECT COUNT(*) AS cases, SUM(CASE WHEN is_closed = 1 THEN 1 ELSE 0 END) AS closed,
                                   AVG(CAST(duration_hours AS FLOAT)) AS avg_duration_hours,
                                   AVG(CAST(is_straight_through AS FLOAT)) AS straight_through_rate,
                                   AVG(CAST(sla_breached AS FLOAT)) AS sla_breach_rate
                            FROM pm.vw_case_overview WHERE environment_code IN {env_sql}""", envs)[0]
        variants = _rows(cur, """SELECT TOP 5 variant_id, activity_sequence, case_count, share_pct,
                                        avg_duration_hours, is_conformant, is_happy_path
                                 FROM pm.vw_variant_summary WHERE process_code = ?
                                 ORDER BY case_count DESC""", (process_code,))
        conf = _rows(cur, """SELECT SUM(case_count) AS cases,
                                    SUM(CASE WHEN is_conformant = 1 THEN case_count ELSE 0 END) AS conformant,
                                    COUNT(*) AS variants
                             FROM pm.vw_variant_summary WHERE process_code = ?""", (process_code,))[0]
        bottlenecks = _rows(cur, """SELECT TOP 5 from_activity, to_activity, transition_count, avg_wait_hours,
                                           total_wait_hours, share_of_total_wait, is_designed
                                    FROM pm.vw_bottlenecks WHERE process_code = ?
                                    ORDER BY total_wait_hours DESC""", (process_code,))
        stuck = _rows(cur, """SELECT TOP 5 status, stage_group, idle_hours, target_hours, overdue_factor,
                                     wait_reason, is_customer_side
                              FROM pm.vw_stuck_cases ORDER BY overdue_factor DESC""")
        n_stuck = _rows(cur, "SELECT COUNT(*) AS n FROM pm.vw_stuck_cases WHERE overdue_factor > 1")[0]["n"]
        rework = _rows(cur, """SELECT TOP 5 activity_code, activity_name, SUM(occurrences - 1) AS repeats,
                                      COUNT(*) AS cases
                               FROM pm.vw_rework WHERE process_code = ?
                               GROUP BY activity_code, activity_name ORDER BY SUM(occurrences - 1) DESC""",
                       (process_code,))
        agents = _rows(cur, """SELECT TOP 8 agent_id, runs, p50_ms, p95_ms, degraded, errors, flagged
                               FROM agent.vw_step_stats ORDER BY p95_ms DESC""")
        events = _rows(cur, f"""SELECT COUNT(*) AS events, COUNT(DISTINCT case_id) AS cases,
                                       AVG(CAST(is_automated AS FLOAT)) AS automated_share
                                FROM pm.vw_event_log_flat
                                WHERE process_code = ? AND environment_code IN {env_sql}""",
                       (process_code, *envs))[0]
    cases = conf["cases"] or 0
    facts = {
        "available": True, "process_code": process_code, "environments": list(envs),
        "overview": {"cases": ov["cases"], "closed": ov["closed"],
                     "avg_duration_hours": _r(ov["avg_duration_hours"]),
                     "straight_through_pct": _r((ov["straight_through_rate"] or 0) * 100),
                     "sla_breach_pct": _r((ov["sla_breach_rate"] or 0) * 100)},
        # rework comes from pm.vw_rework (event log); core.loan_applications.rework_count
        # is a denormalised column nothing maintains yet
        "events": {"events": events["events"], "cases": events["cases"],
                   "automated_pct": _r((events["automated_share"] or 0) * 100)},
        "conformance": {"variants": conf["variants"], "cases": cases,
                        "conformant_cases": conf["conformant"] or 0,
                        "conformance_pct": _r(100 * (conf["conformant"] or 0) / cases) if cases else None},
        "top_variants": variants, "bottlenecks": bottlenecks,
        "stuck": {"overdue_cases": n_stuck, "worst": stuck},
        "rework": rework, "agent_latency": agents,
    }
    facts["findings"] = findings(facts)
    return facts


def findings(f: dict) -> list[str]:
    """Deterministic headline findings (the narration may only rephrase these)."""
    out = []
    c = f["conformance"]
    if c["conformance_pct"] is not None:
        out.append(f"{c['conformance_pct']}% of {c['cases']} cases follow a conformant path "
                   f"across {c['variants']} variants.")
    if f["bottlenecks"]:
        b = f["bottlenecks"][0]
        out.append(f"Largest wait: {b['from_activity']} → {b['to_activity']}, "
                   f"{_r(b['avg_wait_hours'])} h on average over {b['transition_count']} transitions "
                   f"({_r((b['share_of_total_wait'] or 0) * 100 if (b['share_of_total_wait'] or 0) <= 1 else b['share_of_total_wait'])}% of all waiting).")
    if f["stuck"]["overdue_cases"]:
        out.append(f"{f['stuck']['overdue_cases']} open cases are past their stage target.")
    if f["rework"] and (f["rework"][0].get("repeats") or 0) > 0:
        r = f["rework"][0]
        out.append(f"Most repeated step: {r['activity_name']} ({r['repeats']} repeats in {r['cases']} cases).")
    if f["agent_latency"]:
        a = f["agent_latency"][0]
        out.append(f"Slowest agent at p95: {a['agent_id']} ({_r(a['p95_ms'] / 1000 if a['p95_ms'] else 0)} s).")
    return out
