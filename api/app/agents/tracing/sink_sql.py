"""SQL Server sink: every agent step → one pm.event_log event (via
pm.usp_log_event) plus agent.run / agent.step rows.

Never on the request path: runs are queued and a background thread writes
them. If the database is unreachable the circuit opens for 60 s and runs are
spooled to storage/agent/pending_sql/*.jsonl; they are replayed with the SAME
idempotency keys once the database is back, so a retry can never create a
duplicate event. Cached and skipped steps are shown in the UI but not logged
as process events (they would inflate activity frequencies).
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ...config import settings
from . import ids

log = logging.getLogger("finsight.agents.sql")

SPOOL_DIR = Path(__file__).resolve().parents[4] / "storage" / "agent" / "pending_sql"


def _dt(iso: str | None):
    if not iso:
        return None
    d = datetime.fromisoformat(iso)
    return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d


class SqlSink:
    def __init__(self):
        self._q: queue.Queue = queue.Queue(maxsize=5000)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._breaker_until = 0.0
        self._conn = None
        self.stats = {"runs_written": 0, "events_written": 0, "spooled": 0, "replayed": 0,
                      "last_error": None, "unknown_activity": []}

    # ── public ──
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="finsight-sql-sink", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def submit(self, run_dict: dict):
        if not (settings.agents_sql_sink and settings.db_enabled):
            return
        self.start()
        try:
            self._q.put_nowait(run_dict)
        except queue.Full:
            self._spool(run_dict)

    def flush(self, timeout: float = 30.0) -> bool:
        """Wait until queued runs are written (tests / eval harness)."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._q.unfinished_tasks == 0:
                return True
            time.sleep(0.1)
        return False

    def status(self) -> dict:
        spooled = sum(1 for _ in SPOOL_DIR.glob("*.jsonl")) if SPOOL_DIR.exists() else 0
        return {"enabled": bool(settings.agents_sql_sink and settings.db_enabled),
                "breaker_open": time.time() < self._breaker_until,
                "queued": self._q.qsize(), "spool_files": spooled, **self.stats}

    # ── worker ──
    def _loop(self):
        last_replay = 0.0
        while not self._stop.is_set():
            try:
                job = self._q.get(timeout=1.0)
            except queue.Empty:
                if time.time() - last_replay > 30 and time.time() >= self._breaker_until:
                    last_replay = time.time()
                    self._replay()
                continue
            try:
                if time.time() < self._breaker_until:
                    self._spool(job)
                else:
                    self._write(job)
            except Exception as exc:  # noqa: BLE001 - never kill the sink thread
                self.stats["last_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                log.warning("agent SQL sink: %s — spooling, retry in 60s", self.stats["last_error"])
                self._breaker_until = time.time() + 60
                self._close()
                self._spool(job)
            finally:
                self._q.task_done()

    def _connection(self):
        if self._conn is None:
            import pyodbc
            self._conn = pyodbc.connect(settings.db_connection_string, autocommit=True)
        return self._conn

    def _close(self):
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:  # noqa: BLE001
            pass
        self._conn = None

    def _write(self, run: dict):
        conn = self._connection()
        cur = conn.cursor()
        cur.execute("EXEC ops.usp_set_session_context @actor_id = ?, @source_system = ?",
                    (None, "finsight_agents"))
        run_id = run["run_id"]
        case_id = run.get("case_id")
        cur.execute(
            """MERGE agent.run AS t USING (SELECT ? AS run_id) AS s ON t.run_id = s.run_id
               WHEN MATCHED THEN UPDATE SET status = ?, ended_at = ?, duration_ms = ?, step_count = ?
               WHEN NOT MATCHED THEN INSERT (run_id, flow, case_id, case_key, status, started_at, ended_at,
                    duration_ms, actor_role, actor_id, settings_revision, environment_code, step_count)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?);""",
            (run_id, run["status"], _dt(run.get("ended_at")), _int(run.get("duration_ms")), len(run["steps"]),
             run_id, run["flow"][:60], case_id, (run.get("case_key") or None), run["status"],
             _dt(run["started_at"]), _dt(run.get("ended_at")), _int(run.get("duration_ms")),
             (run.get("actor") or {}).get("role"), (run.get("actor") or {}).get("id"),
             run.get("settings_revision"), run.get("environment") or settings.agents_env, len(run["steps"])))
        for step in run["steps"]:
            event_id = None
            if step["status"] not in ("cached", "skipped") and step.get("activity_code"):
                event_id = self._event(cur, run, step)
            if not self._exists(cur, step["step_id"]):
                llm = step.get("llm_calls") or []
                model = step.get("model") or {}
                cur.execute(
                    """INSERT INTO agent.step (step_id, run_id, seq, agent_id, activity_code, component_code,
                         status, ai_outcome, ai_confidence, score, started_at, duration_ms, model_provider,
                         model_name, llm_calls, llm_ms, llm_queue_ms, tool_calls, summary, output_json, event_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (step["step_id"], run_id, step["seq"], step["agent_id"], step.get("activity_code"),
                     step.get("component_code"), step["status"], step.get("ai_outcome"),
                     _dec(step.get("ai_confidence")), _dec(step.get("score")), _dt(step["started_at"]),
                     _int(step.get("duration_ms")), model.get("provider"), model.get("model"),
                     len(llm), _int(sum(c.get("latency_ms") or 0 for c in llm)) if llm else None,
                     _int(sum(c.get("queue_wait_ms") or 0 for c in llm)) if llm else None,
                     len(step.get("tool_calls") or []), (step.get("summary") or "")[:400] or None,
                     json.dumps(step.get("output_summary") or {}, default=str)[:30000], event_id))
                for tc in (step.get("tool_calls") or [])[:40]:
                    cur.execute("INSERT INTO agent.tool_call (step_id, tool, duration_ms, status, detail) VALUES (?,?,?,?,?)",
                                (step["step_id"], str(tc.get("tool"))[:60], _int(tc.get("duration_ms")),
                                 str(tc.get("status") or "")[:20], str(tc.get("detail") or "")[:300] or None))
        self.stats["runs_written"] += 1

    @staticmethod
    def _exists(cur, step_id) -> bool:
        cur.execute("SELECT 1 FROM agent.step WHERE step_id = ?", (step_id,))
        return cur.fetchone() is not None

    def _event(self, cur, run: dict, step: dict):
        process = step.get("process") or "loan_application"
        if process == "document":
            case = step.get("case_scope") or run.get("case_id") or run["run_id"]
        elif process == "agent_ops":
            case = run["run_id"]
        else:
            case = run.get("case_id") or run["run_id"]
        llm = step.get("llm_calls") or []
        model = step.get("model") or {}
        key = ids.idempotency_key(case, step.get("activity_code"), step["agent_id"], step["step_id"])
        attrs = json.dumps({"agent_id": step["agent_id"], "run_id": run["run_id"], "step_id": step["step_id"],
                            "flow": run["flow"], "status": step["status"], "llm_calls": len(llm),
                            "tools": [t.get("tool") for t in (step.get("tool_calls") or [])][:12]})
        try:
            cur.execute(
                """DECLARE @eid BIGINT;
                   EXEC pm.usp_log_event @process_code = ?, @case_id = ?, @activity_code = ?,
                        @occurred_at = ?, @case_key = ?, @resource_type = ?, @started_at = ?,
                        @completed_at = ?, @duration_ms = ?, @component_code = ?, @model_name = ?,
                        @model_version = ?, @ai_confidence = ?, @ai_outcome = ?, @is_exception = ?,
                        @exception_type = ?, @channel_code = ?, @source_system = ?, @correlation_id = ?,
                        @idempotency_key = ?, @environment_code = ?, @attributes = ?, @event_id = @eid OUTPUT;
                   SELECT @eid;""",
                (process, case, step["activity_code"], _dt(step.get("ended_at") or step["started_at"]),
                 (run.get("case_key") or None), step.get("resource_type") or "system",
                 _dt(step["started_at"]), _dt(step.get("ended_at")), _int(step.get("duration_ms")),
                 step.get("component_code"), (model.get("model") or None),
                 step.get("agent_version") or "1.0", _dec(step.get("ai_confidence")),
                 step.get("ai_outcome"), 1 if step["status"] == "error" else 0,
                 (step.get("error") or "")[:60] or None, "web", "finsight_agents", run["run_id"],
                 key, run.get("environment") or settings.agents_env, attrs))
            while cur.description is None and cur.nextset():
                pass
            row = cur.fetchone() if cur.description else None
            self.stats["events_written"] += 1
            return int(row[0]) if row and row[0] is not None else None
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "Unknown activity_code" in msg:
                code = step.get("activity_code")
                if code not in self.stats["unknown_activity"]:
                    self.stats["unknown_activity"].append(code)
                return None  # migration 08 not applied: keep the step, skip the event
            raise

    def _spool(self, job: dict):
        SPOOL_DIR.mkdir(parents=True, exist_ok=True)
        p = SPOOL_DIR / f"{datetime.now():%Y%m%d}.jsonl"
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(job, default=str) + "\n")
        self.stats["spooled"] += 1

    def _replay(self):
        if not SPOOL_DIR.exists():
            return
        for p in sorted(SPOOL_DIR.glob("*.jsonl")):
            lines = p.read_text(encoding="utf-8").splitlines()
            try:
                for line in lines:
                    if line.strip():
                        self._write(json.loads(line))
                        self.stats["replayed"] += 1
                p.unlink()
            except Exception as exc:  # noqa: BLE001 - keep the file, try later
                self.stats["last_error"] = f"replay: {type(exc).__name__}: {str(exc)[:200]}"
                self._breaker_until = time.time() + 60
                self._close()
                return


def _int(v):
    try:
        return int(round(float(v))) if v is not None else None
    except (TypeError, ValueError):
        return None


def _dec(v):
    try:
        return round(max(0.0, min(1.0, float(v))), 4) if v is not None else None
    except (TypeError, ValueError):
        return None


SINK = SqlSink()
