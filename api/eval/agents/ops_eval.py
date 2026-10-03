"""Operations evaluation: Notifier (26), Process-Mining Analyst (28) and
Liveness (29, Phase 6 — see eval_liveness).

Notifier — every lifecycle event is rendered with a full context and with
data missing; the check is exact: the right recipients per event (head only
when the amount needs head approval / the fraud tier is HIGH), no unfilled
placeholder, every context value present in the text, nothing sent half
filled, and zero outbound network connections while drafting and writing.
The SQL write is exercised on a throwaway 'EVAL-' application created
through the Audit/Storage SQL path and removed afterwards.

Process-Mining Analyst — every headline figure the analyst reports is
recomputed with an independent query on the BASE tables (pm.event_log,
pm.conformance_result, core.loan_applications, agent.step …) rather than the
view the analyst reads; the narration is asked for three times and must pass
the grounding gate.
"""
from __future__ import annotations

import re
import socket

from eval import common as C

EVAL_APP = "EVAL-NOTIFY-0001"


class _NoNetwork:
    """Counts (and blocks) every outbound TCP connection attempt."""

    def __enter__(self):
        self.attempts = []
        self._orig = socket.socket.connect

        def guard(sock, addr, *a, **k):
            self.attempts.append(str(addr))
            raise OSError("network blocked by evaluation")
        socket.socket.connect = guard
        return self

    def __exit__(self, *a):
        socket.socket.connect = self._orig


def _expected_roles(event: str, ctx: dict) -> set:
    roles = {"application_submitted": {"officer", "user"}, "assessment_referred": {"officer"},
             "underwriting_completed": {"officer"}, "offer_drafted": {"officer"},
             "more_info_requested": {"user"}, "documents_verified": {"user"}}[event]
    if event == "assessment_referred" and ctx.get("fraud_tier") == "HIGH":
        roles |= {"head"}
    if event == "underwriting_completed" and ctx.get("requires_head"):
        roles |= {"head"}
    return roles


_FULL = {"application_no": "APP-2026-7788", "applicant": "Asha Nair", "product": "Home Loan",
         "fraud_tier": "HIGH", "policy_status": "FAIL", "recommendation": "request more info",
         "next_step": "incomeVerification", "requires_head": True, "amount": 650000, "rate": 10.5,
         "tenure": 120, "emi": 8772.4, "documents": "bank statement (last 6 months)"}


def _cleanup(app_no: str):
    from app.config import settings
    import pyodbc
    with pyodbc.connect(settings.db_connection_string, autocommit=True, timeout=5) as conn:
        row = conn.execute("SELECT application_id, customer_id FROM core.loan_applications WHERE application_no = ?",
                           (app_no,)).fetchone()
        if not row:
            return
        # only the throwaway EVAL- application's own rows (incl. what the insert
        # triggers wrote: status history -> pm.event_log), so process-mining
        # figures are not skewed by evaluation runs
        conn.execute("DELETE FROM pm.event_log WHERE case_key = ?", (app_no,))
        conn.execute("DELETE FROM core.application_status_history WHERE application_id = ?", (row[0],))
        conn.execute("DELETE FROM audit.business_events WHERE application_id = ?", (row[0],))
        conn.execute("DELETE FROM ops.notifications WHERE application_id = ?", (row[0],))
        conn.execute("DELETE FROM core.uploaded_documents WHERE application_id = ?", (row[0],))
        conn.execute("DELETE FROM core.loan_applications WHERE application_id = ?", (row[0],))
        conn.execute("DELETE FROM core.customers WHERE customer_id = ? AND environment_code = 'demo' "
                     "AND NOT EXISTS (SELECT 1 FROM core.loan_applications WHERE customer_id = ?)", (row[1], row[1]))


def eval_notifier() -> dict:
    from app.agents.registry import get_agent
    from app.agents.sql_persist import persist_application
    from app.agents.tools import notifier
    agent = get_agent("notifier")
    events = sorted({t["event"] for t in notifier.TEMPLATES.values()})
    checks, fails = 0, []
    correct = 0
    variants = []
    for ev in events:
        variants.append((ev, dict(_FULL)))
        if ev == "underwriting_completed":
            variants.append((ev, {**_FULL, "requires_head": False}))
        if ev == "assessment_referred":
            variants.append((ev, {**_FULL, "fraud_tier": "MEDIUM"}))
    with _NoNetwork() as net:
        for ev, ctx in variants:
            out = agent.execute({"case": {}, "event": ev, "context": ctx, "write": False}).payload
            drafts = [d for d in out["drafts"] if not d.get("skipped")]
            roles = {d["role"] for d in drafts}
            ok = roles == _expected_roles(ev, ctx)
            for d in drafts:
                text = d["title"] + " " + d["body"]
                ok &= "{" not in text and "}" not in text and d["channel"] == "in_app"
                t = notifier.TEMPLATES[d["template"]]
                for v in notifier.variables(t):
                    want = ctx[v]
                    if v in ("amount", "emi"):
                        want = notifier._inr(want)
                    ok &= str(want) in text
                if "amount" in notifier.variables(t):
                    ok &= "Rs 6,50,000" in text
            checks += 1
            correct += ok
            if not ok:
                fails.append({"event": ev, "roles": sorted(roles), "expected": sorted(_expected_roles(ev, ctx))})
        # missing data: nothing half-filled
        out = agent.execute({"case": {}, "event": "offer_drafted", "context": {"application_no": "APP-1"},
                             "write": False}).payload
        half_filled = sum(1 for d in out["drafts"] if not d.get("skipped"))
        # SQL write on a throwaway application
        _cleanup(EVAL_APP)
        sql_written = sql_rows = 0
        sql_error = None
        try:
            rec = {"app_id": EVAL_APP, "applicant_name": "Eval Applicant", "loan_type": "Home Loan",
                   "documents": [{"doc_type": "pan", "fields": {"pan_number": "ZZEVA0001Z", "dob": "01/01/1990",
                                                               "full_name": "EVAL APPLICANT"}}]}
            persisted = persist_application(rec, None)
            case = {"application_no": EVAL_APP, "applicant": {"name": "Eval Applicant"},
                    "loan": {"loan_type": "Home Loan"}}
            res = agent.execute({"case": case, "event": "underwriting_completed",
                                 "context": {**_FULL, "application_no": EVAL_APP}}).payload
            sql_written = res["sql"].get("written", 0)
            from app.config import settings
            import pyodbc
            with pyodbc.connect(settings.db_connection_string, timeout=5) as conn:
                sql_rows = conn.execute("""SELECT COUNT(*) FROM ops.notifications n
                                           JOIN core.loan_applications a ON a.application_id = n.application_id
                                           WHERE a.application_no = ? AND n.channel = 'in_app'""",
                                        (EVAL_APP,)).fetchone()[0]
            persisted_ok = persisted.get("ok")
        except Exception as exc:  # noqa: BLE001
            sql_error, persisted_ok = f"{type(exc).__name__}: {str(exc)[:160]}", False
        finally:
            _cleanup(EVAL_APP)
    metrics = {"template_accuracy": round(correct / checks, 4), "checks": checks,
               "external_sends": len(net.attempts), "half_filled_sent": half_filled,
               "sql_application_created": bool(persisted_ok), "sql_rows_written": sql_written,
               "sql_rows_read_back": sql_rows, "sql_error": sql_error,
               "templates": len(notifier.TEMPLATES), "events": len(events)}
    return C.save("notifier", metrics, failures=fails)


# ── Process-Mining Analyst ──────────────────────────────────────────────────
def _close(a, b, tol=0.15) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol


def eval_pm(narrations: int = 3) -> dict:
    from app.agents.registry import get_agent
    from app.config import settings
    import pyodbc
    agent = get_agent("process_mining_analyst")
    with C.Timer() as t:
        f = agent.execute({"process_code": "loan_application", "include_demo": True}).payload
    envs = ("prod", "demo")
    with pyodbc.connect(settings.db_connection_string, timeout=5) as conn:
        q = lambda sql, *p: conn.execute(sql, p).fetchone()  # noqa: E731
        direct = {
            "cases": q("SELECT COUNT(*) FROM core.loan_applications WHERE environment_code IN (?,?)", *envs)[0],
            "closed": q("SELECT SUM(CASE WHEN is_case_closed = 1 THEN 1 ELSE 0 END) FROM core.loan_applications "
                        "WHERE environment_code IN (?,?)", *envs)[0],
            "straight_through_pct": q("SELECT 100.0 * AVG(CAST(is_straight_through AS FLOAT)) FROM core.loan_applications "
                                      "WHERE environment_code IN (?,?)", *envs)[0],
            "events": q("SELECT COUNT(*) FROM pm.event_log WHERE process_code = 'loan_application' "
                        "AND environment_code IN (?,?) AND is_valid_for_mining = 1", *envs)[0],
            "event_cases": q("SELECT COUNT(DISTINCT case_id) FROM pm.event_log WHERE process_code = 'loan_application' "
                             "AND environment_code IN (?,?) AND is_valid_for_mining = 1", *envs)[0],
            "conformance_pct": q("SELECT 100.0 * SUM(CASE WHEN is_conformant = 1 THEN case_count ELSE 0 END) "
                                 "/ NULLIF(SUM(case_count), 0) FROM pm.case_variant WHERE process_code = 'loan_application'")[0],
            "variants": q("SELECT COUNT(*) FROM pm.case_variant WHERE process_code = 'loan_application'")[0],
            "top_rework_repeats": q("""SELECT TOP 1 SUM(n - 1) FROM (SELECT case_id, activity_id, COUNT(*) AS n
                                        FROM pm.event_log WHERE process_code = 'loan_application'
                                          AND lifecycle_transition = 'complete'
                                        GROUP BY case_id, activity_id HAVING COUNT(*) > 1) x
                                       GROUP BY activity_id ORDER BY SUM(n - 1) DESC""")
            ,
            "slowest_agent_p95_ms": q("""SELECT TOP 1 p95 FROM (SELECT DISTINCT agent_id,
                                           PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms)
                                           OVER (PARTITION BY agent_id) AS p95
                                         FROM agent.step WHERE status <> 'cached') s ORDER BY p95 DESC""")[0],
        }
        conf_cases = q("SELECT COUNT(*), SUM(CAST(is_conformant AS INT)) FROM pm.conformance_result")
    direct["top_rework_repeats"] = direct["top_rework_repeats"][0] if direct["top_rework_repeats"] else 0
    got = {
        "cases": f["overview"]["cases"], "closed": f["overview"]["closed"],
        "straight_through_pct": f["overview"]["straight_through_pct"],
        "events": f["events"]["events"], "event_cases": f["events"]["cases"],
        "conformance_pct": f["conformance"]["conformance_pct"], "variants": f["conformance"]["variants"],
        "top_rework_repeats": (f["rework"][0]["repeats"] if f["rework"] else 0),
        "slowest_agent_p95_ms": (f["agent_latency"][0]["p95_ms"] if f["agent_latency"] else None),
    }
    comparisons = []
    for k in got:
        tol = 0.15 if k.endswith("_pct") else (1.0 if k.endswith("_ms") else 0)
        comparisons.append({"figure": k, "analyst": got[k], "direct_sql": _plain(direct[k]),
                            "equal": _close(got[k], direct[k], tol)})
    # narration: grounded?
    grounded = attempted = 0
    samples = []
    for _ in range(narrations):
        r = agent.run({"process_code": "loan_application", "include_demo": True, "narrate": True})
        attempted += 1
        if r.narrative:
            grounded += 1
            samples.append(r.narrative[:400])
        else:
            samples.append("DROPPED: " + "; ".join(r.warnings)[:200])
    ok = sum(c["equal"] for c in comparisons)
    metrics = {"query_correctness": round(ok / len(comparisons), 4), "figures_checked": len(comparisons),
               "narration_grounded_rate": round(grounded / attempted, 4) if attempted else None,
               "narrations": attempted, "latency_ms": round(t.ms),
               "conformance_result_rows": conf_cases[0], "findings": f["findings"]}
    return C.save("process_mining_analyst", metrics, comparisons=comparisons, narration_samples=samples)


def _plain(v):
    try:
        return round(float(v), 2) if v is not None else None
    except (TypeError, ValueError):
        return v


# ── Liveness ────────────────────────────────────────────────────────────────
def _lfw_people(n: int):
    """[(name, img_a, img_b)] — two different full-frame (250x250) photos of the
    same public LFW person, from pairsDevTest (read locally, no download)."""
    from PIL import Image
    home = C.API_DIR / "eval" / "datasets" / "external" / "lfw" / "lfw_home"
    lines = (home / "pairsDevTest.txt").read_text().splitlines()[1:501]
    out, seen = [], set()
    for ln in lines:
        name, i, j = ln.split("\t")
        if name in seen:
            continue
        seen.add(name)
        p = lambda k: home / "lfw_funneled" / name / f"{name}_{int(k):04d}.jpg"  # noqa: E731
        if p(i).exists() and p(j).exists():
            out.append((name, Image.open(p(i)).convert("RGB"), Image.open(p(j)).convert("RGB")))
        if len(out) >= n:
            break
    return out


def _jpeg(im, q=90) -> bytes:
    import io
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=q)
    return buf.getvalue()


def _acer(live_scores, attack_scores, t):
    bpcer = sum(s < t for s in live_scores) / len(live_scores)
    apcer = sum(s >= t for s in attack_scores) / len(attack_scores)
    return (apcer + bpcer) / 2, apcer, bpcer


def eval_liveness(n: int = 40) -> dict:
    """Bona fide = phone-style captures of full-frame LFW photos (real people,
    real cameras); attacks = the SAME photos printed-and-rephotographed or shown
    on a screen and re-captured (forge.print_attack / screen_attack). The live
    threshold is calibrated on even-indexed people and reported on the odd
    half. Face-to-ID match: selfie (photo A) against a synthetic PAN card
    carrying photo B of the same person, and against another person's card."""
    import random
    from app.face import liveness
    from app.face.service import analyze_document
    from eval.datasets.synth import forge, render
    from eval.datasets.synth.gen_docs import identities
    rng = random.Random(29)
    people = _lfw_people(n)
    idts = identities(len(people), seed=2929)
    rows, lat = [], []
    cards = []
    for k, (name, a, b) in enumerate(people):
        selfie = forge.camera_capture(a.resize((500, 500)), rng)
        attacks = {"print": forge.print_attack(a.resize((500, 500)), rng),
                   "screen": forge.screen_attack(a.resize((500, 500)), rng)}
        card = forge.camera_capture(render.pan_card(idts[k], face=b, seed=k), rng)
        faces = analyze_document(_jpeg(card)).get("faces") or []
        cards.append([f["embedding"] for f in faces if f.get("embedding") is not None])
        with C.Timer() as t:
            live = liveness.check(_jpeg(selfie))
        lat.append(t.ms)
        rows.append({"k": k, "kind": "live", "score": live.get("live_score"), "verdict": live["verdict"]})
        for kind, im in attacks.items():
            r = liveness.check(_jpeg(im))
            rows.append({"k": k, "kind": kind, "score": r.get("live_score"), "verdict": r["verdict"]})
    scored = [r for r in rows if r["score"] is not None]
    no_face = [r for r in rows if r["score"] is None]
    cal = [r for r in scored if r["k"] % 2 == 0]
    test = [r for r in scored if r["k"] % 2 == 1]
    grid = [i / 100 for i in range(5, 96)]
    best_t = min(grid, key=lambda t: _acer([r["score"] for r in cal if r["kind"] == "live"],
                                           [r["score"] for r in cal if r["kind"] != "live"], t)[0])
    live_t = [r["score"] for r in test if r["kind"] == "live"]
    att_t = [r["score"] for r in test if r["kind"] != "live"]
    acer_cal, apcer_cal, bpcer_cal = _acer(live_t, att_t, best_t)
    acer_def, apcer_def, bpcer_def = _acer(live_t, att_t, liveness.LIVE_T)
    per_attack = {kind: round(sum(r["score"] >= best_t for r in test if r["kind"] == kind)
                              / max(1, sum(1 for r in test if r["kind"] == kind)), 4) for kind in ("print", "screen")}
    # face-to-ID match on the held-out half
    same_ok = diff_ok = same_n = diff_n = 0
    for k, (name, a, b) in enumerate(people):
        if k % 2 == 0 or not cards[k]:
            continue
        selfie = _jpeg(forge.camera_capture(a.resize((500, 500)), random.Random(k)))
        r = liveness.check(selfie, cards[k])
        if r.get("match"):
            same_n += 1
            same_ok += r["match"]["verdict"] == "match"
        other = cards[(k + 2) % len(cards)]
        if other:
            r = liveness.check(selfie, other)
            if r.get("match"):
                diff_n += 1
                diff_ok += r["match"]["verdict"] != "match"
    lt = C.latency(lat)
    metrics = {"acer": round(acer_cal, 4), "apcer": round(apcer_cal, 4), "bpcer": round(bpcer_cal, 4),
               "threshold_calibrated": best_t, "acer_at_default_0_5": round(acer_def, 4),
               "apcer_at_default": round(apcer_def, 4), "bpcer_at_default": round(bpcer_def, 4),
               "attack_pass_rate": per_attack, "no_face_detected": len(no_face),
               "id_match_rate": round(same_ok / same_n, 4) if same_n else None,
               "impostor_rejected_rate": round(diff_ok / diff_n, 4) if diff_n else None,
               "people": len(people), "test_live": len(live_t), "test_attacks": len(att_t),
               "p50_s": lt["p50_s"], "p95_s": lt["p95_s"]}
    return C.save("liveness", metrics, data="LFW full-frame photos (public) as bona fide captures; print and "
                  "screen attacks SIMULATED from the same photos — a proxy, not a certified PAD test")
