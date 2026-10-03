"""Per-run context carried through contextvars.

`run_in_threadpool` copies the context into the worker thread, so a step
opened in an endpoint is visible to the gateway call made deep inside an
existing module (e.g. authenticity.py) — which is how LLM calls get
attributed to the right agent step without threading a parameter through
every function.
"""
from __future__ import annotations

import contextvars

current_run: contextvars.ContextVar = contextvars.ContextVar("finsight_run", default=None)
current_step: contextvars.ContextVar = contextvars.ContextVar("finsight_step", default=None)
current_snapshot: contextvars.ContextVar = contextvars.ContextVar("finsight_llm_snapshot", default=None)
# agent_id -> open StepRecord, for Crews where several agents share one kickoff
steps_by_agent: contextvars.ContextVar = contextvars.ContextVar("finsight_steps_by_agent", default=None)


def note_llm_call(record: dict) -> None:
    """Attach one LLM call record to the active step, if any."""
    step = current_step.get()
    if step is not None:
        try:
            step.llm_calls.append(record)
        except Exception:  # noqa: BLE001 - tracing never breaks the call
            pass


def note_tool_call(record: dict) -> None:
    step = current_step.get()
    if step is not None:
        try:
            step.tool_calls.append(record)
        except Exception:  # noqa: BLE001
            pass
