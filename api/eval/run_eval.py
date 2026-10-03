"""Run agent evaluations.

    cd api
    .venv-agents/Scripts/python -m eval.run_eval --agent validation
    .venv-agents/Scripts/python -m eval.run_eval --group intake
    .venv-agents/Scripts/python -m eval.run_eval --all

Results: api/eval/results/<agent>.json (metrics, target, rating, failures).
Local model only (FINSIGHT_FORBID_HOSTED); SQL sink off so eval runs never
enter the process-mining log.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import traceback

from eval import common as C

EVALUATORS = {
    # agent_id: (module, function, needs_llm)
    "validation": ("intake_eval", "eval_validation", False),
    "preprocessing": ("intake_eval", "eval_preprocessing", False),
    "extraction": ("intake_eval", "eval_extraction", True),
    "doc_identity": ("intake_eval", "eval_doc_identity", True),
    "pii_masking": ("privacy_eval", "eval_masking", False),
    "face_verification": ("evidence_eval", "eval_face", False),
    "tampering_forensics": ("evidence_eval", "eval_tampering", True),
    "authenticity": ("evidence_eval", "eval_authenticity", True),
    "tamper_explainer": ("evidence_eval", "eval_explainer", True),
    "tamper_locator": ("evidence_eval", "eval_locator", False),
    "cross_verification": ("case_eval", "eval_cross_verification", False),
    "dedupe": ("case_eval", "eval_dedupe", False),
    "fraud_fusion": ("case_eval", "eval_fraud_fusion", False),
    "policy_check": ("case_eval", "eval_policy", False),
    "audit_storage": ("case_eval", "eval_audit_storage", False),
    "fraud_patterns": ("fraud_eval", "eval_patterns", False),
    "fraud_triage": ("fraud_eval", "eval_triage", False),
    "kb_ingestion": ("compliance_eval", "eval_kb", False),
    "rule_authoring": ("compliance_eval", "eval_rule_authoring", True),
    "regulatory_qa": ("compliance_eval", "eval_regulatory_qa", True),
    "income_analysis": ("underwriting_eval", "eval_income", False),
    "risk_scoring": ("underwriting_eval", "eval_risk", False),
    "underwriting_supervisor": ("underwriting_eval", "eval_supervisor", True),
    "credit_memo": ("underwriting_eval", "eval_memo", True),
    "offer_generation": ("underwriting_eval", "eval_offer", False),
    "workflow_router": ("underwriting_eval", "eval_router", False),
    "notifier": ("ops_eval", "eval_notifier", False),
    "process_mining_analyst": ("ops_eval", "eval_pm", False),
    "liveness": ("ops_eval", "eval_liveness", False),
}
# agents whose evaluation data is entirely synthetic or public (no real
# applicant document) — the only ones a hosted model may be evaluated on
SYNTHETIC_ONLY = {"extraction", "rule_authoring", "regulatory_qa", "underwriting_supervisor", "credit_memo",
                  "process_mining_analyst", "fraud_patterns", "income_analysis"}

GROUPS = {
    "intake": ["validation", "preprocessing", "extraction", "doc_identity"],
    "privacy": ["pii_masking"],
    "evidence": ["face_verification", "tampering_forensics", "authenticity", "tamper_explainer", "tamper_locator"],
    "case": ["cross_verification", "dedupe", "fraud_fusion", "policy_check", "audit_storage"],
    "fraud": ["fraud_patterns", "fraud_triage"],
    "compliance": ["kb_ingestion", "rule_authoring", "regulatory_qa"],
    "underwriting": ["income_analysis", "risk_scoring", "credit_memo", "underwriting_supervisor",
                     "offer_generation", "workflow_router"],
    "ops": ["notifier", "process_mining_analyst", "liveness"],
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", action="append", default=[])
    ap.add_argument("--group", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--model", default="qwen2.5vl:7b")
    ap.add_argument("--provider", default="ollama", choices=["ollama", "anthropic"])
    a = ap.parse_args()
    agents = list(a.agent)
    for g in a.group:
        agents += GROUPS[g]
    if a.all:
        agents = list(EVALUATORS)
    if a.provider == "anthropic":
        bad = [x for x in agents if x not in SYNTHETIC_ONLY]
        if bad:
            print(f"refused: {bad} use real documents; a hosted model may only see synthetic data "
                  f"({sorted(SYNTHETIC_ONLY)})")
            return 2
    C.setup_env(provider=a.provider, model=a.model)
    summary = {}
    for agent_id in agents:
        mod, fn, _ = EVALUATORS[agent_id]
        try:
            out = getattr(importlib.import_module(f"eval.agents.{mod}"), fn)()
            summary[agent_id] = {"value": out.get("primary_value"), "rating": out.get("rating")}
        except Exception as exc:  # noqa: BLE001 - keep going, record the failure
            traceback.print_exc()
            summary[agent_id] = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(summary, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
