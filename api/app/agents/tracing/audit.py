"""Best-effort audit of LLM-selection changes (agent.llm_setting_audit)."""
from __future__ import annotations

import json
import logging
import threading

from ...config import settings

log = logging.getLogger("finsight.agents.audit")


def record_setting_change(before: dict, after: dict, actor_id: str | None) -> None:
    if not (settings.db_enabled and settings.agents_sql_sink):
        return
    change = {"default": after.get("default"), "lanes": after.get("lanes"),
              "agents": after.get("agents"), "hosted_ack": after.get("hosted_ack")}

    def _write():
        try:
            import pyodbc
            with pyodbc.connect(settings.db_connection_string, autocommit=True, timeout=5) as conn:
                conn.cursor().execute(
                    "INSERT INTO agent.llm_setting_audit (actor_id, revision_from, revision_to, hosted_in_use, change_json) "
                    "VALUES (?,?,?,?,?)",
                    (actor_id, before.get("revision"), after.get("revision"),
                     1 if after.get("hosted_ack") else 0, json.dumps(change)))
        except Exception as exc:  # noqa: BLE001
            log.info("llm setting audit not written: %s", exc)

    threading.Thread(target=_write, daemon=True).start()
