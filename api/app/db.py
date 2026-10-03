"""SQL Server access for the lending data (customers, applications, documents,
fraud cases, notifications).

Deliberately thin: pyodbc + parameterised SQL, no ORM. The schema is owned by
the scripts in docs/mssql/ and is the single source of truth — an ORM's model
layer would just be a second, silently-drifting copy of it.

Connections are pooled by pyodbc itself (on by default). Every helper returns
plain dicts so FastAPI can serialise them without a model layer in between.

If the database is unreachable the helpers raise DatabaseUnavailable, which the
router turns into a 503. The OCR/tampering/compliance endpoints do not touch
this module, so document processing keeps working with the database down.
"""
from __future__ import annotations

import decimal
import logging
import threading
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Iterable, Sequence

from .config import settings

log = logging.getLogger("finsight.db")

try:
    import pyodbc
except ImportError:  # pragma: no cover - surfaced clearly at call time
    pyodbc = None


class DatabaseUnavailable(RuntimeError):
    """Raised when the lending database cannot be reached."""


_probe_lock = threading.Lock()
_probe_result: tuple[bool, str] | None = None


def _connect():
    if pyodbc is None:
        raise DatabaseUnavailable(
            "pyodbc is not installed — run: pip install pyodbc")
    if not settings.db_enabled:
        raise DatabaseUnavailable("Database access is disabled (DB_ENABLED=false)")
    try:
        return pyodbc.connect(settings.db_connection_string, autocommit=False)
    except Exception as exc:  # noqa: BLE001 - re-raised as our own type
        raise DatabaseUnavailable(
            f"Cannot connect to {settings.db_server}/{settings.db_name}: {exc}") from exc


@contextmanager
def connection():
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def _jsonable(v: Any) -> Any:
    """pyodbc hands back Decimal / datetime / bytes; make them JSON-safe."""
    if isinstance(v, decimal.Decimal):
        # Money and ratios both live in DECIMAL columns. float is right for
        # display; anything doing arithmetic on money should do it in SQL.
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    return v


def _rows(cursor) -> list[dict]:
    cols = [c[0] for c in cursor.description]
    return [{c: _jsonable(v) for c, v in zip(cols, row)} for row in cursor.fetchall()]


def query(sql: str, params: Sequence[Any] = ()) -> list[dict]:
    """Run a SELECT and return a list of dicts."""
    with connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        return _rows(cur)


def query_one(sql: str, params: Sequence[Any] = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def scalar(sql: str, params: Sequence[Any] = ()) -> Any:
    with connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        row = cur.fetchone()
        return _jsonable(row[0]) if row else None


def execute(sql: str, params: Sequence[Any] = ()) -> int:
    """Run one INSERT/UPDATE/DELETE. Returns affected row count."""
    with connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        n = cur.rowcount
        conn.commit()
        return n


def execute_returning(sql: str, params: Sequence[Any] = ()) -> dict | None:
    """Run a statement with an OUTPUT clause and return the first output row."""
    with connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        rows = _rows(cur) if cur.description else []
        conn.commit()
        return rows[0] if rows else None


def set_session_context(conn, actor_id: str | None = None,
                        source_system: str = "finsight_api") -> None:
    """Tell the audit triggers who is acting.

    The triggers read SESSION_CONTEXT, which is scoped to the connection — so
    this has to run on the SAME connection as the DML that follows, not as a
    separate call.
    """
    cur = conn.cursor()
    cur.execute(
        "EXEC ops.usp_set_session_context @actor_id = ?, @source_system = ?",
        (actor_id, source_system))
    cur.close()


def set_change_context(cur, actor_id: str | None = None, reason_code: str | None = None,
                       change_reason: str | None = None, wait_reason: str | None = None,
                       source_system: str = "finsight_api") -> None:
    """Who is changing an application and why, for the update trigger's status-history
    row (09-decision-workflow.sql). Sets every key each time, so calling it with no
    arguments clears them - do that before the connection goes back to the pool."""
    for key, value in (("actor_id", actor_id), ("source_system", source_system),
                       ("reason_code", reason_code), ("wait_reason", wait_reason),
                       ("change_reason", (change_reason or None) and str(change_reason)[:1000])):
        cur.execute("EXEC sp_set_session_context @key = ?, @value = ?", (key, value))


def log_event(conn, process_code: str, case_id: str, activity_code: str,
              **kw) -> int | None:
    """Write one row to pm.event_log through the stored procedure.

    Uses the caller's connection so the event lands in the same transaction as
    the change it describes — an event for a write that later rolls back would
    be a lie in the process log.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            DECLARE @eid BIGINT;
            EXEC pm.usp_log_event
                 @process_code = ?, @case_id = ?, @activity_code = ?,
                 @resource_id = ?, @resource_type = ?, @duration_ms = ?,
                 @component_code = ?, @model_name = ?, @model_version = ?,
                 @ai_confidence = ?, @ai_outcome = ?, @channel_code = ?,
                 @source_system = ?, @attributes = ?, @environment_code = ?,
                 @event_id = @eid OUTPUT;
            SELECT @eid;
            """,
            (process_code, case_id, activity_code,
             kw.get("resource_id"), kw.get("resource_type"), kw.get("duration_ms"),
             kw.get("component_code"), kw.get("model_name"), kw.get("model_version"),
             kw.get("ai_confidence"), kw.get("ai_outcome"), kw.get("channel_code", "web"),
             kw.get("source_system", "finsight_api"), kw.get("attributes"),
             kw.get("environment_code", "prod")))
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None
    except Exception:  # noqa: BLE001 - telemetry must never break the write
        log.exception("pm.usp_log_event failed for %s/%s", activity_code, case_id)
        return None
    finally:
        cur.close()


def health() -> dict:
    """Cheap connectivity probe for /health and the frontend's startup check."""
    global _probe_result
    try:
        with connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT DB_NAME(), (SELECT COUNT(*) FROM core.loan_applications)")
            name, apps = cur.fetchone()
        result = {"ok": True, "database": name, "applications": int(apps),
                  "server": settings.db_server}
    except DatabaseUnavailable as exc:
        result = {"ok": False, "error": str(exc), "server": settings.db_server}
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                  "server": settings.db_server}
    with _probe_lock:
        _probe_result = (result["ok"], result.get("error", ""))
    return result
