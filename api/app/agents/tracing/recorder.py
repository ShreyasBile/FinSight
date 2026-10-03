"""TraceRecorder — records every agent run and step.

Three destinations, all fed from here:
  * an in-memory ring (last 400 runs) the UI polls for live progress;
  * PII-free JSON files under storage/agent_traces/<date>/<run_id>.json (also
    the fallback history when SQL Server is down) + an index for case lookup;
  * the SQL sink (pm.event_log + agent.run/agent.step), in the background.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from collections import OrderedDict, deque
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from ...config import settings
from ..context import current_run, current_snapshot, current_step
from .models import RunRecord, StepRecord, now_iso
from .sink_sql import SINK

TRACE_DIR = Path(__file__).resolve().parents[4] / "storage" / "agent_traces"
_RING_MAX = 400


class TraceRecorder:
    def __init__(self):
        self._lock = threading.RLock()
        self._ring: "OrderedDict[str, RunRecord]" = OrderedDict()
        self._durations: dict[str, deque] = {}
        self._counts: dict[str, dict] = {}

    # ── runs ──
    def start_run(self, flow: str, case_id: str | None = None, case_key: str | None = None,
                  actor: dict | None = None, snapshot=None) -> RunRecord:
        run = RunRecord.new(flow, case_id, case_key, actor,
                            getattr(snapshot, "revision", None), settings.agents_env)
        run._t0 = time.perf_counter()  # type: ignore[attr-defined]
        with self._lock:
            self._ring[run.run_id] = run
            while len(self._ring) > _RING_MAX:
                self._ring.popitem(last=False)
        return run

    def finish_run(self, run: RunRecord, status: str | None = None) -> RunRecord:
        with self._lock:
            run.ended_at = now_iso()
            run.duration_ms = round((time.perf_counter() - getattr(run, "_t0", time.perf_counter())) * 1000, 1)
            statuses = {s.status for s in run.steps}
            run.status = status or ("error" if "error" in statuses
                                    else "degraded" if "degraded" in statuses else "ok")
        d = run.to_dict()
        self._persist(d)
        SINK.submit(d)
        return run

    @contextmanager
    def run(self, flow: str, case_id: str | None = None, case_key: str | None = None,
            actor: dict | None = None, snapshot=None):
        """Open a run, bind it (and the LLM settings snapshot) to the context."""
        from ..llm.settings_store import snapshot as take_snapshot
        snap = snapshot or take_snapshot()
        run = self.start_run(flow, case_id, case_key, actor, snap)
        t_run, t_snap = current_run.set(run), current_snapshot.set(snap)
        try:
            yield run
        except Exception:
            self.finish_run(run, "error")
            raise
        else:
            self.finish_run(run)
        finally:
            current_run.reset(t_run)
            current_snapshot.reset(t_snap)

    # ── steps ──
    @contextmanager
    def step(self, run: RunRecord, spec, input_digest: dict | None = None,
             case_scope: str | None = None):
        with self._lock:
            seq = len(run.steps) + 1
            step = StepRecord(
                step_id=str(uuid.uuid4()), run_id=run.run_id, seq=seq, agent_id=spec.id,
                agent_name=spec.name, agent_number=spec.number, crew=spec.crew, kind=spec.kind,
                activity_code=spec.activity_code, component_code=spec.component_code,
                process=spec.process, started_at=now_iso(), input_digest=input_digest or {},
                case_scope=case_scope)
            step.resource_type = spec.resource_type  # type: ignore[attr-defined]
            step.agent_version = spec.version        # type: ignore[attr-defined]
            run.steps.append(step)
        t0 = time.perf_counter()
        token = current_step.set(step)
        try:
            yield step
            if step.status == "running":
                step.status = "ok"
        except Exception as exc:
            step.status = "error"
            step.error = f"{type(exc).__name__}: {str(exc)[:300]}"
            raise
        finally:
            current_step.reset(token)
            step.ended_at = now_iso()
            if step.duration_ms is None:
                step.duration_ms = round((time.perf_counter() - t0) * 1000, 1)
            self._account(step)

    # explicit open/close — for agents that run inside one CrewAI Crew kickoff,
    # where a context manager per step cannot wrap each agent's turn
    def open_step(self, run: RunRecord, spec, input_digest: dict | None = None) -> StepRecord:
        with self._lock:
            step = StepRecord(
                step_id=str(uuid.uuid4()), run_id=run.run_id, seq=len(run.steps) + 1, agent_id=spec.id,
                agent_name=spec.name, agent_number=spec.number, crew=spec.crew, kind=spec.kind,
                activity_code=spec.activity_code, component_code=spec.component_code,
                process=spec.process, started_at=now_iso(), input_digest=input_digest or {})
            step.resource_type = spec.resource_type  # type: ignore[attr-defined]
            step.agent_version = spec.version        # type: ignore[attr-defined]
            step._t0 = time.perf_counter()           # type: ignore[attr-defined]
            run.steps.append(step)
        return step

    def mark_started(self, step: StepRecord):
        step.started_at = now_iso()
        step._t0 = time.perf_counter()  # type: ignore[attr-defined]

    def close_step(self, step: StepRecord, status: str | None = None):
        if status:
            step.status = status
        elif step.status == "running":
            step.status = "ok"
        step.ended_at = now_iso()
        if step.duration_ms is None:
            step.duration_ms = round((time.perf_counter() - getattr(step, "_t0", time.perf_counter())) * 1000, 1)
        self._account(step)

    def _account(self, step: StepRecord):
        with self._lock:
            c = self._counts.setdefault(step.agent_id, {"runs": 0, "degraded": 0, "errors": 0,
                                                        "cached": 0, "flagged": 0, "llm_calls": 0})
            c["runs"] += 1
            c["degraded"] += step.status == "degraded"
            c["errors"] += step.status == "error"
            c["cached"] += step.status == "cached"
            c["flagged"] += step.ai_outcome in ("flag", "fail")
            c["llm_calls"] += len(step.llm_calls)
            if step.status not in ("cached", "skipped") and step.duration_ms is not None:
                self._durations.setdefault(step.agent_id, deque(maxlen=500)).append(step.duration_ms)

    # ── queries ──
    def get(self, run_id: str) -> dict | None:
        with self._lock:
            run = self._ring.get(run_id)
            if run is not None:
                return run.to_dict()
        for p in TRACE_DIR.glob(f"*/{run_id}.json"):
            return json.loads(p.read_text(encoding="utf-8"))
        return None

    def runs(self, case_id: str | None = None, agent_id: str | None = None,
             limit: int = 50) -> list[dict]:
        with self._lock:
            live = [r.to_dict() for r in reversed(self._ring.values())]
        seen = {r["run_id"] for r in live}
        if case_id:
            for entry in self._index(case_id):
                if entry["run_id"] not in seen:
                    r = self.get(entry["run_id"])
                    if r:
                        live.append(r)
                        seen.add(r["run_id"])
        out = []
        for r in live:
            if case_id and r.get("case_id") != case_id:
                continue
            if agent_id and not any(s["agent_id"] == agent_id for s in r["steps"]):
                continue
            out.append(r)
        out.sort(key=lambda r: r["started_at"], reverse=True)
        return out[:limit]

    def stats(self) -> dict:
        out = {}
        with self._lock:
            for agent_id, c in self._counts.items():
                ds = sorted(self._durations.get(agent_id, []))
                out[agent_id] = {**c,
                                 "p50_ms": round(ds[len(ds) // 2], 1) if ds else None,
                                 "p95_ms": round(ds[min(len(ds) - 1, int(len(ds) * 0.95))], 1) if ds else None}
        return out

    # ── persistence ──
    def _persist(self, d: dict):
        try:
            day = datetime.now().strftime("%Y-%m-%d")
            folder = TRACE_DIR / day
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{d['run_id']}.json").write_text(json.dumps(d, default=str), encoding="utf-8")
            with (TRACE_DIR / "index.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"run_id": d["run_id"], "case_id": d.get("case_id"),
                                    "flow": d["flow"], "started_at": d["started_at"], "day": day}) + "\n")
        except Exception:  # noqa: BLE001 - tracing must never fail a request
            pass

    def _index(self, case_id: str) -> list[dict]:
        p = TRACE_DIR / "index.jsonl"
        if not p.exists():
            return []
        out = []
        try:
            for line in p.read_text(encoding="utf-8").splitlines()[-20000:]:
                if case_id in line:
                    e = json.loads(line)
                    if e.get("case_id") == case_id:
                        out.append(e)
        except Exception:  # noqa: BLE001
            return []
        return out[-100:]


RECORDER = TraceRecorder()
