"""CaseAssessmentFlow — the case-level evidence agents (diagram 5).

    start ─┬─ cross_verification ─┐
           ├─ dedupe ─────────────┼─ and_ ─ fraud_fusion ─ policy_check ─ router
           └─ fraud_patterns ─────┘            (0 LLM calls on this path)

Runs after the document agents have filled the case file. The router step
only labels the outcome for the reviewer ('refer' when fraud is HIGH or a
mandatory rule failed); it never changes a status — a person decides.
"""
from __future__ import annotations

from .. import case_store
from ..crewai_compat import flow_classes
from ..registry import IMPLEMENTATIONS, get_agent
from ..tracing.recorder import RECORDER

_Flow = None


def _flow_class():
    global _Flow
    if _Flow is not None:
        return _Flow
    Flow, start, listen, router, and_, or_ = flow_classes()

    class CaseAssessmentFlow(Flow):
        @start()
        def begin(self):
            return True

        @listen(begin)
        def cross_check(self, _):
            r = get_agent("cross_verification").run({"case": self.case})
            self.out["cross_checks"] = r.payload
            return r.payload

        @listen(begin)
        def dedupe(self, _):
            r = get_agent("dedupe").run({"case": self.case, "case_id": self.case_id})
            self.out["dedupe"] = r.payload
            return r.payload

        @listen(begin)
        def patterns(self, _):
            stmt = (self.case.get("documents") or {}).get("bank_statement") or {}
            if not stmt.get("transactions") and not self.extra.get("transactions"):
                self.out["patterns"] = None
                return None
            try:
                r = get_agent("fraud_patterns").run({"case": self.case, "extra": self.extra})
            except KeyError:          # agent not built in this deployment
                self.out["patterns"] = None
                return None
            self.out["patterns"] = r.payload
            return r.payload

        @listen(and_(cross_check, dedupe, patterns))
        def fuse(self):
            r = get_agent("fraud_fusion").run({"case": self.case, "cross_checks": self.out.get("cross_checks"),
                                               "dedupe": self.out.get("dedupe"),
                                               "patterns": self.out.get("patterns")})
            self.out["fraud"] = r.payload
            return r.payload

        @listen(fuse)
        def policy(self, fraud):
            r = get_agent("policy_check").run({"case": self.case, "extra": self.extra, "assessment": fraud})
            self.out["policy"] = r.payload
            return r.payload

        @router(policy)
        def route(self):
            fraud, policy = self.out["fraud"], self.out["policy"]
            mandatory_fail = any(c["status"] == "FAIL" and c.get("rule_type") == "mandatory"
                                 for c in policy.get("checks", []))
            self.out["route"] = "refer" if (fraud["tier"] == "HIGH" or mandatory_fail) else "continue"
            return self.out["route"]

    _Flow = CaseAssessmentFlow
    return CaseAssessmentFlow


def run_assessment(case_id: str, extra: dict | None = None, actor: dict | None = None) -> dict:
    case = case_store.get(case_id)
    extra = extra or {}
    with RECORDER.run("assess", case_id, case_key=case.get("application_no"), actor=actor) as run:
        flow = _flow_class()()
        flow.case, flow.case_id, flow.extra, flow.out = case, case_id, extra, {}
        flow.kickoff()
        out = flow.out
        from ..tools import fraud_fusion
        fraud_fusion.persist_sql(case_id, case.get("application_no"), out["fraud"], run.run_id)
        if out.get("route") == "refer" and "notifier" in IMPLEMENTATIONS and not extra.get("no_notify"):
            get_agent("notifier").run({"case": case, "event": "assessment_referred",
                                       "context": {"fraud_tier": out["fraud"]["tier"],
                                                   "policy_status": out["policy"]["overall_status"]}})
        result = {"run_id": run.run_id, "case_id": case_id, "cross_checks": out.get("cross_checks"),
                  "dedupe": out.get("dedupe"), "patterns": out.get("patterns"), "fraud": out["fraud"],
                  "policy": out["policy"], "route": out.get("route")}
    case_store.set_section(case_id, "assessment", {k: v for k, v in result.items() if k != "policy"}
                           | {"policy_status": result["policy"]["overall_status"]})
    return result


def run_policy_only(body: dict) -> dict:
    """POST /agents/policy/check: {case_id? | application_no? | context?, extra?}."""
    from ..tracing.ids import normalize_case_id
    case_id = normalize_case_id(body.get("case_id")) if body.get("case_id") else None
    if not case_id and body.get("application_no"):
        from .app_evidence import ensure_case
        case_id = ensure_case(body["application_no"])
    case = case_store.get(case_id) if case_id else {"case_id": "adhoc", "documents": {}}
    if case_id is None and not body.get("context"):
        from fastapi import HTTPException
        raise HTTPException(422, "Send case_id, application_no or a full context")
    assessment = (case or {}).get("assessment", {}) or {}
    with RECORDER.run("policy", case_id, actor=None) as run:
        r = get_agent("policy_check").run({"case": case, "context": body.get("context"),
                                           "extra": body.get("extra"),
                                           "assessment": assessment.get("fraud")})
        return {**r.payload, "run_id": run.run_id}
