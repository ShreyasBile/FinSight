"""Code check for the new evaluators: each one end to end on a handful of DEVELOPMENT items, so a bug
surfaces in minutes instead of hours into a test run. Results go to <agent>.dev.smoke.json and the
item caches use a separate 'smoke' version, so nothing here is reported or reused.

  EVAL_SMOKE=1 python -m eval.real.run_real --agent smoke --split dev
"""
from __future__ import annotations

import os
import time
import traceback

from eval.real import core as R

STEPS = [
    ("validation", "intake_real", "eval_validation", {}),
    ("cross_verification", "case_real", "eval_cross_verification", {}),
    ("policy_check", "case_real", "eval_policy_check", {"n": 30}),
    ("income_analysis", "money_real", "eval_income_analysis", {"n": 30}),
    ("fraud_patterns", "money_real", "eval_fraud_patterns", {"n_clean": 20, "n_each": 4}),
    ("dedupe", "case_real", "eval_dedupe", {"n_index": 120}),
    ("fraud_triage", "case_real", "eval_fraud_triage", {"n_index": 120}),
    ("preprocessing", "intake_real", "eval_preprocessing", {"n_scans": 3, "n_timed": 2}),
    ("pii_masking", "intake_real", "eval_pii_masking", {"n_pan": 3, "n_pages": 6}),
    ("extraction", "intake_real", "eval_extraction", {"n_pan": 2, "n_receipts": 2, "n_stmt": 2}),
    ("doc_identity", "intake_real", "eval_doc_identity", {}),
    ("offer_generation", "uw_real", "eval_offer_generation", {"n": 30}),
    ("underwriting_supervisor", "uw_real", "eval_supervisor", {"n": 3}),
    ("credit_memo", "uw_real", "eval_credit_memo", {"n": 3}),
    ("workflow_router", "ops_real", "eval_workflow_router", {"n": 30}),
    ("notifier", "ops_real", "eval_notifier", {"n": 30}),
    ("audit_storage", "ops_real", "eval_audit_storage", {"n": 3}),
    ("fraud_fusion", "ops_real", "eval_fraud_fusion", {"n_clean": 20, "n_each": 5}),
]


def run(split: str = "dev", only: str | None = None) -> dict:
    import importlib
    os.environ["EVAL_SMOKE"] = "1"
    orig = R.ItemCache.__init__

    def smoke_cache(self, agent_id, version):              # never mix smoke items into real caches
        # component caches other evaluators only READ (tamper and face results) stay as they are
        orig(self, agent_id, version if agent_id in ("evidence", "face_verification") else f"smoke-{version}")
    R.ItemCache.__init__ = smoke_cache
    only = set((only or os.environ.get("SMOKE_ONLY") or "").split(",")) - {""}
    out = {}
    for agent, mod, fn, kw in STEPS:
        if only and agent not in only:
            continue
        t = time.time()
        try:
            f = getattr(importlib.import_module(f"eval.real.{mod}"), fn)
            if agent == "doc_identity":                    # its wrong-upload pool is fixed-size; cut it down
                from eval.real import intake_real as I
                full = I.wrong_uploads
                I.wrong_uploads = lambda split="dev": full(split)[:4]
                full_aad = I.aadhaar_items
                I.aadhaar_items = lambda: full_aad()[:2]
            f(split="dev", **kw)
            out[agent] = f"OK {time.time() - t:.0f}s"
        except Exception:  # noqa: BLE001
            out[agent] = "ERROR " + traceback.format_exc()[-1500:]
        print(f"[smoke] {agent}: {out[agent][:2000]}", flush=True)
    return out
