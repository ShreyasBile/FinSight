"""Run / step records shown in the Agent Activity views and written to SQL."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class StepRecord:
    step_id: str
    run_id: str
    seq: int
    agent_id: str
    agent_name: str
    agent_number: int
    crew: str
    kind: str
    activity_code: str | None
    component_code: str | None
    process: str
    started_at: str
    status: str = "running"            # running | ok | degraded | skipped | cached | error
    ai_outcome: str | None = None      # pass | flag | fail | uncertain | info
    ai_confidence: float | None = None
    score: float | None = None
    summary: str = ""
    ended_at: str | None = None
    duration_ms: float | None = None
    model: dict | None = None          # {provider, model, source} when an LLM was used
    llm_calls: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    input_digest: dict = field(default_factory=dict)
    output_summary: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    error: str | None = None
    narrative: str | None = None       # LLM-written text that passed the grounding gate
    case_scope: str | None = None      # document case GUID for DOC_* activities

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunRecord:
    run_id: str
    flow: str
    case_id: str | None
    case_key: str | None
    started_at: str
    status: str = "running"
    ended_at: str | None = None
    duration_ms: float | None = None
    actor: dict = field(default_factory=dict)
    settings_revision: int | None = None
    environment: str = "demo"
    steps: list = field(default_factory=list)

    @staticmethod
    def new(flow: str, case_id: str | None, case_key: str | None, actor: dict | None,
            settings_revision: int | None, environment: str) -> "RunRecord":
        return RunRecord(run_id=str(uuid.uuid4()), flow=flow, case_id=case_id, case_key=case_key,
                         started_at=now_iso(), actor=actor or {},
                         settings_revision=settings_revision, environment=environment)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["steps"] = [s.to_dict() if isinstance(s, StepRecord) else s for s in self.steps]
        return d
