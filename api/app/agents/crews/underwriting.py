"""The Underwriting Crew: Underwriting Supervisor → Credit Memo writer.

A real CrewAI Crew (Process.sequential): the supervisor task produces a
structured recommendation, and the memo task receives it as context. Both
agents run on the FinSight gateway LLM (Settings-selected model, privacy
guard, queue, tracing). Both outputs then pass deterministic guards before
anything is kept:

  * tighten-only — the supervisor may make the rule baseline STRICTER
    (approve → refer), never looser; a looser answer is replaced by the baseline;
  * grounding    — every figure in the rationale / memo must exist in the
    evidence bundle; otherwise the deterministic text is used.
"""
from __future__ import annotations

import json

from typing import Literal

from pydantic import BaseModel, Field

STRICTNESS = {"approve_recommended": 0, "request_more_info": 1, "refer_to_officer": 2,
              "decline_review_recommended": 3}
LABEL = {"approve_recommended": "Approve (recommended)", "request_more_info": "Request more information",
         "refer_to_officer": "Refer to officer", "decline_review_recommended": "Decline review recommended"}


class UWRecommendation(BaseModel):
    # an enum in the schema: with constrained decoding the model can only
    # emit one of the four advisory steps
    recommendation: Literal["approve_recommended", "request_more_info", "refer_to_officer",
                            "decline_review_recommended"] = Field(description="the recommended next step")
    drivers: list[str] = Field(description="3-5 short evidence-based reasons")
    rationale: str = Field(description="2-4 sentences explaining the recommendation")
    caveats: list[str] = Field(default_factory=list, description="what a human should still check")


class CreditMemo(BaseModel):
    executive_summary: str
    applicant_profile: str
    income_assessment: str
    risk_assessment: str
    fraud_and_compliance: str
    recommendation: str
    conditions: list[str] = Field(default_factory=list)


MEMO_SECTIONS = ["executive_summary", "applicant_profile", "income_assessment", "risk_assessment",
                 "fraud_and_compliance", "recommendation"]

SUPERVISOR_BACKSTORY = (
    "You are a senior credit underwriter at an Indian NBFC. You read an evidence bundle produced by "
    "automated checks and recommend ONE next step for a human officer. You never approve or reject a loan "
    "yourself. You only use figures that appear in the evidence. You do not judge whether an income "
    "'sounds realistic'. The rule baseline is a floor: you may recommend something stricter, never looser.")
MEMO_BACKSTORY = (
    "You write concise credit memos for an Indian NBFC credit committee. Every number you write must "
    "appear in the evidence bundle exactly. You describe; you do not decide. Plain professional English, "
    "amounts in rupees (Rs).")


def run_crew(evidence: dict, baseline: dict, timeout_s: float = 180) -> tuple[UWRecommendation | None, CreditMemo | None, list[str]]:
    """Returns (recommendation, memo, warnings). Either may be None if the LLM
    path failed; callers then use the deterministic fallback."""
    from ..crewai_compat import crew_classes, gateway_llm
    from ..registry import REGISTRY
    from ..crewai_compat import crewai
    Crew, Task, Process = crew_classes()
    c = crewai()
    warnings = []
    sup_spec, memo_spec = REGISTRY["underwriting_supervisor"], REGISTRY["credit_memo"]
    supervisor = c.Agent(role=sup_spec.role, goal=sup_spec.goal, backstory=SUPERVISOR_BACKSTORY,
                         llm=gateway_llm("underwriting_supervisor", timeout_s), verbose=False,
                         allow_delegation=False, max_iter=2)
    writer = c.Agent(role=memo_spec.role, goal=memo_spec.goal, backstory=MEMO_BACKSTORY,
                     llm=gateway_llm("credit_memo", timeout_s), verbose=False, allow_delegation=False, max_iter=2)
    ev = json.dumps(evidence, default=str)
    t1 = Task(description=(f"Evidence bundle (JSON):\n{ev}\n\nRule baseline recommendation: "
                           f"{baseline['recommendation']} (reasons: {'; '.join(baseline['reasons'])}).\n"
                           "Recommend one next step. It must be the baseline or STRICTER."),
              expected_output="A structured underwriting recommendation.", agent=supervisor,
              output_pydantic=UWRecommendation)
    t2 = Task(description=(f"Evidence bundle (JSON):\n{ev}\n\nWrite the credit memo for this application, using "
                           "the underwriting recommendation from the previous task. Sections: executive summary, "
                           "applicant profile, income assessment, risk assessment, fraud and compliance, "
                           "recommendation, conditions."),
              expected_output="A structured credit memo.", agent=writer, output_pydantic=CreditMemo, context=[t1])
    crew = Crew(agents=[supervisor, writer], tasks=[t1, t2], process=Process.sequential, verbose=False,
                memory=False)
    try:
        crew.kickoff()
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"underwriting crew failed: {type(exc).__name__}: {str(exc)[:160]}")
        return None, None, warnings
    rec = getattr(t1.output, "pydantic", None) if t1.output else None
    memo = getattr(t2.output, "pydantic", None) if t2.output else None
    if rec is None:
        warnings.append("supervisor returned no structured output")
    if memo is None:
        warnings.append("memo writer returned no structured output")
    return rec, memo, warnings
