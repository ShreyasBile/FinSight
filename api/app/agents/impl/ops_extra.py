"""Phase 5 agents: Fraud Patterns (14), Fraud Triage (15), Notifier (26) and
the Process-Mining Analyst (28).

All four are deterministic at their core. Patterns, Triage and the Analyst
may add ONE narration call (only when asked — never on the assessment path),
and that text passes the grounding gate in FinSightAgent.run(): a number the
model states that is not in the facts drops the narration.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..base import AgentOutcome, FinSightAgent
from ..registry import implements


class _Narration(BaseModel):
    text: str = Field(description="2-4 plain sentences; only figures that appear in the facts")


def _narrate(spec, backstory: str, facts, ask: str) -> str | None:
    import json
    from ..crewai_compat import kickoff_structured
    prompt = (f"{ask}\n\nFacts (JSON):\n{json.dumps(facts, default=str)[:6000]}\n\n"
              "Use only these facts. Do not add numbers that are not in them. "
              "Do not recommend approving or rejecting anything.")
    return kickoff_structured(spec, backstory, prompt, _Narration, timeout_s=90).text.strip()


# ── 14 · Fraud Patterns ─────────────────────────────────────────────────────
def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _monthly_gst(parsed: dict) -> float | None:
    t = _num((parsed or {}).get("turnover"))
    if not t:
        return None
    period = str((parsed or {}).get("return_period") or "").lower()
    if any(k in period for k in ("annual", "fy", "year", "gstr-9", "gstr9")):
        return t / 12
    if "quarter" in period or "q1" in period or "q2" in period or "q3" in period or "q4" in period:
        return t / 3
    return t


@implements("fraud_patterns")
class FraudPatternsAgent(FinSightAgent):
    """Transaction-level statement patterns. Rows come from the intake step
    (born-digital text layer or OCR); payslip net and GST turnover come from
    the case's other documents."""

    def input_digest(self, inputs):
        docs = (inputs.get("case") or {}).get("documents") or {}
        stmt = docs.get("bank_statement") or {}
        return {"transactions": len((inputs.get("extra") or {}).get("transactions") or stmt.get("transactions") or []),
                "has_payslip": "salary_slip" in docs, "has_gst": "gst_return" in docs}

    def execute(self, inputs):
        from ..tools.fraud_patterns import scan
        case, extra = inputs.get("case") or {}, inputs.get("extra") or {}
        docs = case.get("documents") or {}
        stmt = docs.get("bank_statement") or {}
        tx = extra.get("transactions") or stmt.get("transactions") or []
        payslip = extra.get("payslip_net", _num(((docs.get("salary_slip") or {}).get("parsed") or {}).get("net_salary")))
        gst = extra.get("gst_monthly_turnover", _monthly_gst((docs.get("gst_return") or {}).get("parsed")))
        res = self.tool("fraud_patterns.scan", scan, tx, payslip, gst)
        res["source"] = stmt.get("transactions_source") or ("provided" if extra.get("transactions") else None)
        flags = res["flags"]
        if not res["evaluable"]:
            return AgentOutcome(payload=res, ai_outcome="skip", status="skipped",
                                summary=f"Only {res['n_transactions']} transaction row(s) readable — no pattern scan",
                                output_summary={"n_transactions": res["n_transactions"]})
        return AgentOutcome(
            payload=res, ai_outcome="flag" if flags else "pass", score=res["score"],
            summary=(f"{len(flags)} pattern(s): " + ", ".join(f["pattern"] for f in flags)) if flags
                    else f"No suspicious patterns in {res['n_transactions']} transactions",
            output_summary={"patterns": [f["pattern"] for f in flags], "n_transactions": res["n_transactions"],
                            "score": res["score"]},
            narration_evidence=res if inputs.get("narrate") else None)

    def narrate(self, inputs, outcome):
        return _narrate(self.spec, "You explain bank-statement red flags to a loan officer in plain words.",
                        outcome.payload, "Explain these statement findings for a loan officer.")


# ── 15 · Fraud Triage ───────────────────────────────────────────────────────
@implements("fraud_triage")
class FraudTriageAgent(FinSightAgent):
    """Proposals only — a fraud officer opens (or dismisses) every case."""

    def input_digest(self, inputs):
        return {"cases": len(inputs.get("cases") or []), "limit": inputs.get("limit", 20)}

    def execute(self, inputs):
        from ..tools.fraud_triage import triage
        res = self.tool("fraud_triage.triage", triage, inputs.get("cases") or [], inputs.get("limit", 20),
                        inputs.get("already_open"))
        props = res["proposals"]
        rings = [p for p in props if p["kind"] == "ring"]
        return AgentOutcome(
            payload=res, ai_outcome="flag" if props else "pass",
            score=max((p.get("confidence") or 0 for p in props), default=0.0),
            summary=(f"{len(props)} proposal(s): {len(rings)} ring/pair, {len(props) - len(rings)} case"
                     if props else f"No proposals from {res['cases_scanned']} cases"),
            output_summary={"proposals": len(props), "rings": len(rings), "links": res["links"],
                            "cases_scanned": res["cases_scanned"]},
            narration_evidence={"proposals": [{k: p.get(k) for k in ("proposal_id", "kind", "severity", "size",
                                                                   "reasons", "confidence")} for p in props[:8]]}
            if inputs.get("narrate") and props else None)

    def narrate(self, inputs, outcome):
        return _narrate(self.spec, "You brief a fraud investigations lead on which proposals to look at first.",
                        outcome.narration_evidence, "Summarise these triage proposals in priority order.")


# ── 26 · Notifier ───────────────────────────────────────────────────────────
@implements("notifier")
class NotifierAgent(FinSightAgent):
    """In-app notification drafts from templates. No external channel exists."""

    def input_digest(self, inputs):
        return {"event": inputs.get("event"), "application_no": (inputs.get("case") or {}).get("application_no")}

    def execute(self, inputs):
        from ..tools import notifier
        case = inputs.get("case") or {}
        ctx = {"application_no": case.get("application_no") or "this application",
               "applicant": (case.get("applicant") or {}).get("name") or "the applicant",
               "product": (case.get("loan") or {}).get("loan_type") or "loan",
               **(inputs.get("context") or {})}
        drafts = self.tool("notifier.render", notifier.render, inputs["event"], ctx)
        written = {"written": 0, "reason": "dry run"}
        if inputs.get("write", True):
            try:
                written = self.tool("notifier.write", notifier.write, drafts, case.get("application_no"))
            except Exception as exc:  # noqa: BLE001 - a notification must never fail the flow
                written = {"written": 0, "reason": f"{type(exc).__name__}: {str(exc)[:120]}"}
        ready = [d for d in drafts if not d.get("skipped")]
        skipped = [d for d in drafts if d.get("skipped")]
        return AgentOutcome(
            payload={"drafts": drafts, "sql": written, "external_sends": 0},
            ai_outcome="info", status="ok",
            summary=f"{len(ready)} in-app notification(s) drafted, {written.get('written', 0)} written"
                    + (f", {len(skipped)} skipped (missing data)" if skipped else ""),
            warnings=[f"{d['template']}: missing {', '.join(d['missing'])}" for d in skipped],
            output_summary={"event": inputs["event"], "drafted": len(ready), "written": written.get("written", 0),
                            "roles": sorted({d["role"] for d in ready})})


# ── 28 · Process-Mining Analyst ─────────────────────────────────────────────
@implements("process_mining_analyst")
class ProcessMiningAnalystAgent(FinSightAgent):
    def input_digest(self, inputs):
        return {"process_code": inputs.get("process_code", "loan_application"),
                "include_demo": inputs.get("include_demo", True)}

    def execute(self, inputs):
        from ..tools.process_mining import insights
        f = self.tool("process_mining.insights", insights, inputs.get("process_code", "loan_application"),
                      inputs.get("include_demo", True))
        if not f.get("available"):
            return AgentOutcome(payload=f, ai_outcome="skip", status="degraded",
                                summary=f"Process data unavailable: {f.get('reason')}")
        return AgentOutcome(
            payload=f, ai_outcome="info",
            summary=" ".join(f["findings"][:2]) or "No findings",
            output_summary={"cases": f["overview"]["cases"], "conformance_pct": f["conformance"]["conformance_pct"],
                            "overdue": f["stuck"]["overdue_cases"], "findings": len(f["findings"])},
            narration_evidence={k: f[k] for k in ("overview", "events", "conformance", "findings")}
            | {"bottlenecks": f["bottlenecks"][:3], "stuck": {"overdue_cases": f["stuck"]["overdue_cases"]},
               "rework": f["rework"][:3], "agent_latency": f["agent_latency"][:3]}
            if inputs.get("narrate") else None)

    def narrate(self, inputs, outcome):
        return _narrate(self.spec, "You are a process-mining analyst briefing a head of lending operations.",
                        outcome.narration_evidence,
                        "Write a short briefing: conformance, the biggest bottleneck, overdue cases, rework.")
