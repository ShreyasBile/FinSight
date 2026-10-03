"""UnderwritingFlow — income → risk → Underwriting Crew (supervisor + memo)
→ draft offer → next workflow step → notification draft.

Runs on demand ("Generate credit memo", after an assessment) — never on the
borrower's upload path. Two LLM calls (one per crew agent); everything else
is deterministic. Results land in the case file; the memo is also written to
ops.generated_reports as a DRAFT when the application exists in SQL Server.
"""
from __future__ import annotations

import json
import re
import time

from .. import case_store
from ..context import steps_by_agent
from ..crewai_compat import flow_classes
from ..llm import gateway, privacy
from ..llm.catalog import HOSTED_PROVIDERS
from ..registry import IMPLEMENTATIONS, REGISTRY, get_agent
from ..tracing.recorder import RECORDER

_Flow = None


def _finish(run, step, agent, inputs):
    """Fill a pre-opened step (Crew agents) from the agent's deterministic guard."""
    outcome = agent.execute(inputs)
    step.status = outcome.status if outcome.status != "ok" else "ok"
    step.ai_outcome, step.summary = outcome.ai_outcome, outcome.summary[:400]
    step.output_summary, step.warnings = outcome.output_summary, list(outcome.warnings) + step.warnings
    if step.llm_calls:
        c = step.llm_calls[-1]
        step.model = {"provider": c.get("provider"), "model": c.get("model")}
        step.duration_ms = round(sum((x.get("latency_ms") or 0) + (x.get("queue_wait_ms") or 0)
                                     for x in step.llm_calls) + 5, 1)
    RECORDER.close_step(step)
    return outcome


def _flow_class():
    global _Flow
    if _Flow is not None:
        return _Flow
    Flow, start, listen, router, and_, or_ = flow_classes()

    class UnderwritingFlow(Flow):
        @start()
        def income(self):
            self.out["income"] = get_agent("income_analysis").run({"case": self.case}).payload
            return True

        @listen(income)
        def risk(self, _):
            self.out["risk"] = get_agent("risk_scoring").run(
                {"case": self.case, "income": self.out["income"], "extra": self.extra}).payload
            return True

        @listen(risk)
        def crew(self, _):
            from ..crews.underwriting import run_crew
            from ..impl.underwriting import baseline_recommendation, evidence_bundle
            ev = evidence_bundle(self.case, self.out["income"], self.out["risk"], self.extra)
            base = baseline_recommendation(ev)
            ev_llm = ev
            snap = gateway.active_snapshot()
            if any(snap.resolve(a).provider in HOSTED_PROVIDERS for a in ("underwriting_supervisor", "credit_memo")) \
                    and snap.redact_text_for_hosted:
                ev_llm = privacy.pseudonymize(ev)
            sup_step = RECORDER.open_step(self.run_rec, REGISTRY["underwriting_supervisor"])
            memo_step = RECORDER.open_step(self.run_rec, REGISTRY["credit_memo"])
            token = steps_by_agent.set({"underwriting_supervisor": sup_step, "credit_memo": memo_step})
            rec_llm = memo_llm = None
            warns = []
            if not self.extra.get("no_llm"):
                try:
                    rec_llm, memo_llm, warns = run_crew(ev_llm, base)
                finally:
                    steps_by_agent.reset(token)
            else:
                steps_by_agent.reset(token)
            sup_step.warnings.extend(w for w in warns if "supervisor" in w or "crew" in w)
            memo_step.warnings.extend(w for w in warns if "memo" in w)
            rec = _finish(self.run_rec, sup_step, get_agent("underwriting_supervisor"),
                          {"evidence": ev, "llm_recommendation": rec_llm}).payload
            memo = _finish(self.run_rec, memo_step, get_agent("credit_memo"),
                           {"evidence": ev, "recommendation": rec, "llm_memo": memo_llm}).payload
            self.out.update(evidence=ev, recommendation=rec, memo=memo)
            return True

        @listen(crew)
        def offer(self, _):
            self.out["offer"] = get_agent("offer_generation").run(
                {"case": self.case, "risk": self.out["risk"], "income": self.out["income"], "extra": self.extra}).payload
            return True

        @listen(offer)
        def route(self, _):
            self.out["workflow"] = get_agent("workflow_router").run(
                {"case": self.case, "extra": self.extra, "status": self.extra.get("status")}).payload
            if "notifier" in IMPLEMENTATIONS and not self.extra.get("no_notify"):
                rec, wf = self.out["recommendation"], self.out["workflow"] or {}
                offer = self.out.get("offer") or {}
                self.out["notifications"] = get_agent("notifier").run(
                    {"case": self.case, "event": "underwriting_completed",
                     "context": {"recommendation": rec["recommendation"].replace("_", " "),
                                 # "underReview" -> "under review": a status code is not a sentence
                                 "next_step": re.sub(r"(?<!^)([A-Z])", r" \1", wf.get("next") or "officer review").lower(),
                                 "requires_head": rec.get("requires_head"),
                                 "amount": offer.get("requested_amount") or self.extra.get("loan_amount")
                                 or (self.case.get("loan") or {}).get("loan_amount")}}).payload
            return True

    _Flow = UnderwritingFlow
    return UnderwritingFlow


def run_underwriting(case_id: str, extra: dict | None = None, actor: dict | None = None) -> dict:
    extra = extra or {}
    case = case_store.get(case_id)
    t0 = time.perf_counter()
    with RECORDER.run("underwrite", case_id, case_key=case.get("application_no"), actor=actor) as run:
        flow = _flow_class()()
        flow.case, flow.extra, flow.out, flow.run_rec = case, extra, {}, run
        flow.kickoff()
        out = flow.out
        result = {"run_id": run.run_id, "case_id": case_id, "income": out.get("income"), "risk": out.get("risk"),
                  "recommendation": out.get("recommendation"), "memo": out.get("memo"),
                  "offer": out.get("offer"), "workflow": out.get("workflow"),
                  "notifications": out.get("notifications"), "duration_ms": round((time.perf_counter() - t0) * 1000)}
    case_store.update(case_id, lambda c: c.update({
        "underwriting": {"income": result["income"], "risk": result["risk"],
                         "recommendation": result["recommendation"], "run_id": result["run_id"]},
        "memo": result["memo"], "offer": result["offer"], "workflow": result["workflow"]}))
    _persist_memo(case, result, actor)
    persist_risk(case, result.get("risk"), result.get("run_id"))
    return result


def _risk_level(overall: float) -> str:
    """Same bands as the app's RiskBadge: <40 low, <65 medium, <80 high, else critical."""
    return "low" if overall < 40 else "medium" if overall < 65 else "high" if overall < 80 else "critical"


def persist_risk(case: dict, risk: dict | None, run_id: str | None) -> bool:
    """Risk Scoring result -> risk.risk_assessments (only for applications in SQL), so the lists,
    dashboard and portfolio pages show the score the agents computed instead of "Not scored"."""
    from ...config import settings
    if not (settings.db_enabled and settings.agents_sql_sink and case.get("application_no") and risk
            and risk.get("overall") is not None):
        return False
    try:
        import pyodbc
        overall = float(risk["overall"])
        with pyodbc.connect(settings.db_connection_string, autocommit=True, timeout=5) as conn:
            cur = conn.cursor()
            row = cur.execute("SELECT application_id FROM core.loan_applications WHERE application_no = ?",
                              (case["application_no"],)).fetchone()
            if not row:
                return False
            prev = cur.execute("SELECT TOP 1 assessment_no, risk_score FROM risk.risk_assessments "
                               "WHERE application_id = ? ORDER BY assessed_at DESC", (row[0],)).fetchone()
            cur.execute(
                """INSERT INTO risk.risk_assessments (application_id, overall_risk_level, risk_score, assessment_data,
                       model_version, assessment_no, previous_score, trigger_reason, is_automated, feature_snapshot)
                   VALUES (?,?,?,?,?,?,?,?,1,?)""",
                (row[0], _risk_level(overall), round(overall / 100, 3),
                 json.dumps({"overall": overall, "grade": risk.get("grade"),
                             "default_probability": risk.get("default_probability"),
                             "decision_advisory": risk.get("decision_advisory"), "run_id": run_id}),
                 "agents:risk_scoring", (prev[0] + 1) if prev else 1, prev[1] if prev else None,
                 "agent_underwriting" if prev is None else "manual_recheck",
                 json.dumps({"inputs_defaulted": risk.get("inputs_defaulted")})[:4000]))
        return True
    except Exception:  # noqa: BLE001 - persistence is best effort
        return False


def _persist_memo(case: dict, result: dict, actor: dict | None = None):
    """Draft memo → ops.generated_reports (only for applications in SQL)."""
    from ...config import settings
    if not (settings.db_enabled and settings.agents_sql_sink and case.get("application_no") and result.get("memo")):
        return
    try:
        import pyodbc
        with pyodbc.connect(settings.db_connection_string, autocommit=True, timeout=5) as conn:
            cur = conn.cursor()
            row = cur.execute("SELECT application_id FROM core.loan_applications WHERE application_no = ?",
                              (case["application_no"],)).fetchone()
            if not row:
                return
            n = cur.execute("SELECT COUNT(*) FROM ops.generated_reports").fetchone()[0] + 1
            # the person who asked for the memo (the agents drafted it); unknown ids stay NULL
            cur.execute("""INSERT INTO ops.generated_reports (report_no, application_id, report_type, status,
                             content_json, generation_ms, generated_by)
                           VALUES (?,?,?,?,?,?, (SELECT user_id FROM ops.users
                                                  WHERE user_id = TRY_CAST(? AS UNIQUEIDENTIFIER)))""",
                        (f"CM-AI-{n:05d}", row[0], "credit_memo", "draft",
                         json.dumps({"memo": result["memo"]["memo"], "recommendation": result["recommendation"],
                                     "source": result["memo"]["source"], "run_id": result["run_id"]}),
                         result.get("duration_ms"), str((actor or {}).get("id") or "")))
    except Exception:  # noqa: BLE001 - persistence is best effort
        pass
