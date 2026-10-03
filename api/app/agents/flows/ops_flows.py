"""Operations flows — Fraud Triage over every case file, and Process-Mining
insights. Single-agent runs (no crew needed); both are read-only."""
from __future__ import annotations

from .. import case_store
from ..registry import get_agent
from ..tracing.recorder import RECORDER


def run_triage(limit: int = 20, narrate: bool = False, actor: dict | None = None) -> dict:
    cases = case_store.list_cases(limit=500, full=True)
    with RECORDER.run("triage", None, actor=actor) as run:
        r = get_agent("fraud_triage").run({"cases": cases, "limit": limit, "narrate": narrate})
        return {**r.payload, "narrative": r.narrative, "run_id": run.run_id, "warnings": r.warnings}


def run_process_insights(process_code: str = "loan_application", include_demo: bool = True,
                         narrate: bool = False, actor: dict | None = None) -> dict:
    with RECORDER.run("process_insights", None, actor=actor) as run:
        r = get_agent("process_mining_analyst").run({"process_code": process_code,
                                                     "include_demo": include_demo, "narrate": narrate})
        return {**r.payload, "narrative": r.narrative, "run_id": run.run_id, "warnings": r.warnings}
