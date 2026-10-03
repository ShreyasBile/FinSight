"""Run a real-data evaluation:  python -m eval.real.run_real --agent risk_scoring [--split test|dev] [--n 5000]

Local model only (Ollama): every dataset here contains real people's data or is evaluated as if
it did. Results: eval/results_real/<agent>.<split>.json. See finsight-eval-data/PROTOCOL.md."""
from __future__ import annotations

import argparse
import importlib
import inspect
import sys
import time

from eval import common as C

EVALUATORS = {
    "risk_scoring": ("risk_real", "eval_risk"),
    "tampering_forensics": ("evidence_real", "eval_tampering"),
    "authenticity": ("evidence_real", "eval_authenticity"),
    "tamper_locator": ("evidence_real", "eval_locator"),
    "tamper_explainer": ("evidence_real", "eval_explainer"),
    "face_verification": ("face_real", "eval_face"),
    "liveness": ("face_real", "eval_liveness"),
    "kb_ingestion": ("compliance_real", "eval_kb"),
    "regulatory_qa": ("compliance_real", "eval_regulatory_qa"),
    "rule_authoring": ("compliance_real", "eval_rule_authoring"),
    "process_mining_analyst": ("pm_real", "eval_pm"),
    # Group 2
    "preprocessing": ("intake_real", "eval_preprocessing"),
    "extraction": ("intake_real", "eval_extraction"),
    "doc_identity": ("intake_real", "eval_doc_identity"),
    "pii_masking": ("intake_real", "eval_pii_masking"),
    "validation": ("intake_real", "eval_validation"),
    "dedupe": ("case_real", "eval_dedupe"),
    "fraud_triage": ("case_real", "eval_fraud_triage"),
    "cross_verification": ("case_real", "eval_cross_verification"),
    "policy_check": ("case_real", "eval_policy_check"),
    "fraud_patterns": ("money_real", "eval_fraud_patterns"),
    "income_analysis": ("money_real", "eval_income_analysis"),
    "underwriting_supervisor": ("uw_real", "eval_supervisor"),
    # Group 3
    "credit_memo": ("uw_real", "eval_credit_memo"),
    "offer_generation": ("uw_real", "eval_offer_generation"),
    "fraud_fusion": ("ops_real", "eval_fraud_fusion"),
    "workflow_router": ("ops_real", "eval_workflow_router"),
    "notifier": ("ops_real", "eval_notifier"),
    "audit_storage": ("ops_real", "eval_audit_storage"),
    # improvement runs (development data only)
    "calibrate_tampering": ("calibrate_tampering", "calibrate"),
    "calibrate_tampering3": ("calibrate_tampering3", "calibrate3"),
    "calibrate_tampering4": ("calibrate_tampering4", "calibrate4"),
    "locator_heatmap_dev": ("locator_heatmap", "experiment"),
    "smoke": ("smoke", "run"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", action="append", required=True)
    ap.add_argument("--split", default="test", choices=["test", "dev"])
    ap.add_argument("--n", type=int, default=None)
    args = ap.parse_args()
    import tempfile
    from eval.real.core import DATA_ROOT
    tmp = DATA_ROOT / "work" / "tmp"             # everything on D:, including throwaway data dirs
    tmp.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(tmp)
    data_dir = C.setup_env(provider="ollama", model="qwen2.5vl:7b", db=False)   # local only, never hosted
    import json
    import os
    if os.environ.get("EVAL_KEEP_ALIVE"):
        # Evaluation-only: let Ollama unload the model after each call, so the tampering CNN and OCR do
        # not run while the runner holds ~13 GB (the machine's commit limit was being reached).
        p = os.path.join(data_dir, "llm_settings.json")
        s = json.loads(open(p, encoding="utf-8").read())
        s["default"]["keep_alive"] = os.environ["EVAL_KEEP_ALIVE"]
        open(p, "w", encoding="utf-8").write(json.dumps(s))
    for a in args.agent:
        mod, fn = EVALUATORS[a]
        f = getattr(importlib.import_module(f"eval.real.{mod}"), fn)
        kw = {"split": args.split}
        if args.n and "n" in inspect.signature(f).parameters:
            kw["n"] = args.n
        t = time.time()
        f(**kw)
        print(f"[{a}] finished in {time.time() - t:,.0f} s", flush=True)


if __name__ == "__main__":
    sys.exit(main())
