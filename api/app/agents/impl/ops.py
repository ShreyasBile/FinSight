"""Operations crew — agent 27 (audit store) here; notifier and the
process-mining analyst live in ops_extra.py."""
from __future__ import annotations

from ..base import AgentOutcome, FinSightAgent
from ..registry import implements


@implements("audit_storage")
class AuditStorageAgent(FinSightAgent):
    """Writes the submitted application to storage/<APP-ID>/ and
    applications.xlsx (Manager sheet full PII, Officer sheet masked) — never
    raises, a storage failure must not fail the submission. When SQL Server is
    reachable the application is also persisted to core.loan_applications
    (see sql_persist.py) so officer screens and process mining can see it."""

    def input_digest(self, inputs):
        r = inputs["record"]
        return {"app_id": r.get("app_id"), "documents": [d.get("doc_type") for d in r.get("documents", [])]}

    def execute(self, inputs):
        from ...storage import store_application
        record = inputs["record"]
        res = self.tool("storage.store_application", store_application, record)
        sql = None
        if res.get("ok") and inputs.get("persist_sql", True):
            try:
                from ..sql_persist import persist_application
                sql = self.tool("sql_persist.persist_application", persist_application,
                                record, inputs.get("case_id"), inputs.get("actor"))
            except Exception as exc:  # noqa: BLE001 - SQL is optional; xlsx already succeeded
                sql = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
        warnings = []
        if sql is not None and not sql.get("ok"):
            warnings.append(f"not written to SQL Server: {sql.get('error')}")
        return AgentOutcome(
            payload={"store": res, "sql": sql},
            ai_outcome="pass" if res.get("ok") else "fail",
            status="ok" if res.get("ok") else "degraded",
            summary=(f"Stored {res.get('rows', 0)} document row(s)" if res.get("ok")
                     else f"Storage failed: {res.get('error')}")
                    + (" · SQL ✓" if sql and sql.get("ok") else ""),
            warnings=warnings,
            output_summary={"rows": res.get("rows"), "ok": res.get("ok"),
                            "sql_ok": (sql or {}).get("ok"), "application_no": (sql or {}).get("application_no")})
