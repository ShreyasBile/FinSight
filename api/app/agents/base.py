"""FinSightAgent — the tool-first agent every FinSight feature runs as.

    execute(inputs)  deterministic core: calls the existing FinSight module
                     (the agent's tool) and returns an AgentOutcome. This is
                     the ONLY place a score, tier or verdict is set.
    narrate(...)     optional, LLM agents only: one structured CrewAI call that
                     writes text about the outcome. Its output passes a
                     grounding gate (every number must exist in the evidence)
                     or is dropped; it can never change the outcome.

run() wraps both in a traced step: timing, tool calls, LLM calls (with model,
latency, queue wait), a PII-free output summary, and the process-mining event.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .context import current_run, note_tool_call
from .registry import AgentSpec, is_enabled
from .tracing.recorder import RECORDER

log = logging.getLogger("finsight.agents")


@dataclass
class AgentOutcome:
    payload: Any = None                 # what the endpoint / next agent consumes
    ai_outcome: str = "info"            # pass | flag | fail | uncertain | info
    score: float | None = None          # 0..1 risk-style score (tampering, fraud)
    confidence: float | None = None     # 0..1, only where it has a defined meaning
    summary: str = ""                   # one PII-free line for the activity panel
    output_summary: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    status: str = "ok"                  # ok | degraded | skipped | cached
    narration_evidence: dict | None = None
    model: dict | None = None


@dataclass
class AgentResult:
    agent_id: str
    payload: Any
    ai_outcome: str
    score: float | None
    confidence: float | None
    summary: str
    status: str
    duration_ms: float | None
    run_id: str | None = None
    step_id: str | None = None
    narrative: str | None = None
    warnings: list = field(default_factory=list)


class FinSightAgent:
    spec: AgentSpec  # set by @implements
    backstory: str = ""

    # ── subclass API ─────────────────────────────────────────────────────────
    def execute(self, inputs: dict) -> AgentOutcome:  # pragma: no cover - abstract
        raise NotImplementedError

    def narrate(self, inputs: dict, outcome: AgentOutcome) -> str | None:
        """LLM agents override. Must return text only — never numbers that
        are not in outcome.narration_evidence (enforced by run())."""
        return None

    def input_digest(self, inputs: dict) -> dict:
        return {}

    def case_scope(self, inputs: dict) -> str | None:
        return inputs.get("_doc_case")

    # ── helpers for subclasses ──────────────────────────────────────────────
    @staticmethod
    def tool(name: str, fn, *args, **kwargs):
        """Call one deterministic tool and record it as a tool call."""
        t0 = time.perf_counter()
        status = "ok"
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            status = f"error: {type(exc).__name__}"
            raise
        finally:
            note_tool_call({"tool": name, "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
                            "status": status})

    # ── orchestration ────────────────────────────────────────────────────────
    def run(self, inputs: dict, *, flow: str | None = None, case_id: str | None = None,
            case_key: str | None = None, actor: dict | None = None) -> AgentResult:
        """Run inside the active run (flows/endpoints open one), or open a
        single-step run of its own."""
        if not is_enabled(self.spec.id):
            outcome = self.execute(inputs)   # kill switch: tool only, untraced
            return AgentResult(self.spec.id, outcome.payload, outcome.ai_outcome, outcome.score,
                               outcome.confidence, outcome.summary, outcome.status, None)
        run = current_run.get()
        if run is None:
            with RECORDER.run(flow or self.spec.id, case_id, case_key, actor):
                return self._run_step(current_run.get(), inputs)
        return self._run_step(run, inputs)

    def _run_step(self, run, inputs: dict) -> AgentResult:
        with RECORDER.step(run, self.spec, self.input_digest(inputs), self.case_scope(inputs)) as step:
            outcome = self.execute(inputs)
            narrative = None
            if self.spec.llm and outcome.status not in ("skipped", "cached") \
                    and outcome.narration_evidence is not None:
                narrative = self._safe_narrate(inputs, outcome, step)
            step.status = outcome.status if outcome.status != "ok" else "ok"
            step.ai_outcome = outcome.ai_outcome
            step.score = _clip(outcome.score)
            step.ai_confidence = _clip(outcome.confidence)
            step.summary = outcome.summary[:400]
            step.output_summary = outcome.output_summary
            step.warnings = list(outcome.warnings)
            step.narrative = narrative
            if outcome.model:
                step.model = outcome.model
            elif step.llm_calls:
                c = step.llm_calls[-1]
                step.model = {"provider": c.get("provider"), "model": c.get("model")}
            return AgentResult(self.spec.id, outcome.payload, outcome.ai_outcome, step.score,
                               step.ai_confidence, step.summary, step.status, None,
                               run_id=run.run_id, step_id=step.step_id, narrative=narrative,
                               warnings=list(outcome.warnings))

    def _safe_narrate(self, inputs, outcome, step) -> str | None:
        try:
            text = self.narrate(inputs, outcome)
        except Exception as exc:  # noqa: BLE001 - narration is optional
            step.warnings.append(f"narration unavailable: {type(exc).__name__}")
            outcome.warnings.append(f"narration unavailable: {type(exc).__name__}")
            return None
        if not text:
            return None
        ok, bad = grounded(text, outcome.narration_evidence)
        if not ok:
            step.warnings.append(f"narration dropped: cites numbers not in the evidence {sorted(bad)[:5]}")
            outcome.warnings.append("narration dropped by grounding gate")
            return None
        return text


# ── grounding gate ───────────────────────────────────────────────────────────
_NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def _numbers(obj, acc: set):
    if isinstance(obj, dict):
        for v in obj.values():
            _numbers(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _numbers(v, acc)
    elif isinstance(obj, bool):
        return
    elif isinstance(obj, (int, float)):
        acc.add(round(float(obj), 2))
        if isinstance(obj, float) and 0 < abs(obj) <= 1:
            acc.add(round(float(obj) * 100, 2))  # 0.43 may be written as 43%
    elif isinstance(obj, str):
        for m in _NUM.findall(obj):
            try:
                acc.add(round(float(m.replace(",", "")), 2))
            except ValueError:
                pass


def grounded(text: str, evidence) -> tuple[bool, set]:
    """Every number >= 10 the text cites must appear in the evidence
    (small numbers — counts, section ordinals — are allowed)."""
    allowed: set = set()
    _numbers(evidence, allowed)
    # a figure rounded the way people say it ("about 35%" for 34.6) is the same figure, not a new one
    allowed |= {float(round(a)) for a in allowed} | {round(a, 1) for a in allowed}
    cited = set()
    for m in _NUM.findall(text or ""):
        try:
            v = round(float(m.replace(",", "")), 2)
        except ValueError:
            continue
        if abs(v) >= 10:
            cited.add(v)
    bad = {v for v in cited if v not in allowed and round(v) not in allowed}
    return (not bad), bad


def _clip(v):
    if v is None:
        return None
    try:
        return round(max(0.0, min(1.0, float(v))), 4)
    except (TypeError, ValueError):
        return None
