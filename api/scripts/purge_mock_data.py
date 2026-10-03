"""Remove mock (seed) and test data from FinSight, keeping only the applications you name.

    cd api
    .venv-agents\\Scripts\\python scripts\\purge_mock_data.py --keep APP-24657408            # dry run
    .venv-agents\\Scripts\\python scripts\\purge_mock_data.py --keep APP-24657408 --apply    # backup, then delete

KEPT   the applications named with --keep and everything that belongs to them (customer, documents, offers,
       fraud / risk rows, process events, agent traces, case file); the demo users (ops.users), the borrower
       persona's customer record, and every reference / configuration table (products, statuses, RBI rules ...).
REMOVED every other application and everything hanging off it, all notifications / policy tickets / reports,
       process-mining events and their derived tables, agent traces of other cases, the per-case files under
       storage/agent and storage/agent_traces, and dedupe-index entries of removed cases.

A dry run does the whole delete inside a transaction and rolls it back, so its counts are exact.
--apply first takes a full database backup and a zip of the files it is about to delete
(api/data/backups/pre-purge-<timestamp>/), then commits, rebuilds the process-mining tables and
cleans the files. The kept applications are also marked environment 'prod' and, if unassigned,
given to the default underwriting officer so they show up in every screen.

Restore the database with:  RESTORE DATABASE FinSight FROM DISK = '<backup>.bak' WITH REPLACE
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path

API = Path(__file__).resolve().parents[1]
ROOT = API.parent
sys.path.insert(0, str(API))

import pyodbc  # noqa: E402

from app.config import settings  # noqa: E402

# tables with audit triggers that would write a fresh "DELETE" row for every row removed
TRIGGERED = ["comp.compliance_check_results", "core.customers", "core.disbursements",
             "core.loan_offers", "fraud.fraud_cases", "risk.risk_assessments"]

DOOMED = "SELECT application_id FROM #doomed_apps"
NOT_KEPT_CASE = "case_id NOT IN (SELECT case_id FROM #keep_cases)"

SETUP = [
    "CREATE TABLE #keep_apps (application_id uniqueidentifier PRIMARY KEY)",
    "CREATE TABLE #keep_cases (case_id uniqueidentifier PRIMARY KEY)",
    "CREATE TABLE #keep_customers (customer_id uniqueidentifier PRIMARY KEY)",
    "CREATE TABLE #doomed_apps (application_id uniqueidentifier PRIMARY KEY)",
    "CREATE TABLE #doomed_customers (customer_id uniqueidentifier PRIMARY KEY)",
    "CREATE TABLE #doomed_cases (case_id uniqueidentifier PRIMARY KEY)",
]

# (label, sql) in dependency order; every statement runs on the same connection as the temp tables
DELETES = [
    ("fraud escalations", "DELETE FROM fraud.fraud_escalations WHERE case_id IN (SELECT case_id FROM #doomed_cases)"),
    ("fraud signals", "DELETE FROM fraud.fraud_signals WHERE case_id IN (SELECT case_id FROM #doomed_cases) OR document_id IN "
                      f"(SELECT document_id FROM core.uploaded_documents WHERE application_id IN ({DOOMED}))"),
    ("fraud cases", "DELETE FROM fraud.fraud_cases WHERE case_id IN (SELECT case_id FROM #doomed_cases)"),
    ("fraud assessments (agents)", f"DELETE FROM fraud.fraud_assessments WHERE {NOT_KEPT_CASE}"),
    ("fraud ring members", "DELETE FROM fraud.fraud_ring_members WHERE customer_id IN (SELECT customer_id FROM #doomed_customers)"),
    ("compliance check results", f"DELETE FROM comp.compliance_check_results WHERE application_id IN ({DOOMED})"),
    ("application form responses", f"DELETE FROM core.application_form_responses WHERE application_id IN ({DOOMED})"),
    ("field verification log", f"DELETE FROM core.field_verification_log WHERE application_id IN ({DOOMED})"),
    ("document extraction results", "DELETE FROM core.document_extraction_results WHERE document_id IN "
                                    f"(SELECT document_id FROM core.uploaded_documents WHERE application_id IN ({DOOMED}))"),
    ("status history", f"DELETE FROM core.application_status_history WHERE application_id IN ({DOOMED})"),
    ("disbursements", f"DELETE FROM core.disbursements WHERE application_id IN ({DOOMED})"),
    ("loan offers", f"DELETE FROM core.loan_offers WHERE application_id IN ({DOOMED})"),
    ("uploaded documents", f"DELETE FROM core.uploaded_documents WHERE application_id IN ({DOOMED})"),
    ("generated reports", f"DELETE FROM ops.generated_reports WHERE application_id IN ({DOOMED})"),
    ("notifications", f"DELETE FROM ops.notifications WHERE application_id IS NULL OR application_id IN ({DOOMED})"),
    ("policy tickets", f"DELETE FROM ops.policy_tickets WHERE application_id IS NULL OR application_id IN ({DOOMED})"),
    ("risk assessments", f"DELETE FROM risk.risk_assessments WHERE application_id IN ({DOOMED})"),
    ("business events", f"DELETE FROM audit.business_events WHERE application_id IN ({DOOMED}) "
                        "OR customer_id IN (SELECT customer_id FROM #doomed_customers)"),
    ("process events (pm.event_log)", f"DELETE FROM pm.event_log WHERE {NOT_KEPT_CASE}"),
    ("process case metrics", f"DELETE FROM pm.case_metrics WHERE {NOT_KEPT_CASE}"),
    ("process conformance", f"DELETE FROM pm.conformance_result WHERE {NOT_KEPT_CASE}"),
    ("process graph snapshots", "DELETE FROM pm.dfg_snapshot"),
    ("loan applications", f"DELETE FROM core.loan_applications WHERE application_id IN ({DOOMED})"),
    # variants are referenced by applications (FK_apps_variant): only after the applications are gone
    ("process variants", "DELETE FROM pm.case_variant WHERE variant_id NOT IN "
                         "(SELECT variant_id FROM pm.case_metrics WHERE variant_id IS NOT NULL) "
                         "AND variant_id NOT IN (SELECT variant_id FROM core.loan_applications WHERE variant_id IS NOT NULL)"),
    ("customers", "DELETE FROM core.customers WHERE customer_id IN (SELECT customer_id FROM #doomed_customers)"),
    ("agent tool calls", "DELETE FROM agent.tool_call WHERE step_id IN (SELECT step_id FROM agent.step WHERE run_id IN "
                         "(SELECT run_id FROM agent.run WHERE case_id IS NULL OR case_id NOT IN (SELECT case_id FROM #keep_cases)))"),
    ("agent steps", "DELETE FROM agent.step WHERE run_id IN "
                    "(SELECT run_id FROM agent.run WHERE case_id IS NULL OR case_id NOT IN (SELECT case_id FROM #keep_cases))"),
    ("agent runs", "DELETE FROM agent.run WHERE case_id IS NULL OR case_id NOT IN (SELECT case_id FROM #keep_cases)"),
    ("audit-log details", "DELETE FROM audit.audit_log_details WHERE log_id IN (SELECT log_id FROM audit.audit_log WHERE "
                          f"application_id IN ({DOOMED}) OR (table_name = 'core.customers' AND entity_id IN "
                          "(SELECT customer_id FROM #doomed_customers)) OR (table_name = 'core.loan_applications' AND entity_id IN "
                          f"({DOOMED})))"),
    ("audit-log rows", f"DELETE FROM audit.audit_log WHERE application_id IN ({DOOMED}) OR (table_name = 'core.customers' "
                       f"AND entity_id IN (SELECT customer_id FROM #doomed_customers)) OR (table_name = 'core.loan_applications' "
                       f"AND entity_id IN ({DOOMED}))"),
]

# Audit rows about records that no longer exist at all (e.g. throwaway rows an earlier test inserted and
# deleted). Run after the deletes above; the rows of the kept records are untouched.
_GONE = [("core.loan_applications", "SELECT application_id FROM core.loan_applications"),
         ("core.customers", "SELECT customer_id FROM core.customers"),
         ("fraud.fraud_cases", "SELECT case_id FROM fraud.fraud_cases"),
         ("core.loan_offers", "SELECT offer_id FROM core.loan_offers"),
         ("core.disbursements", "SELECT disbursement_id FROM core.disbursements"),
         ("risk.risk_assessments", "SELECT assessment_id FROM risk.risk_assessments"),
         ("comp.compliance_check_results", "SELECT check_id FROM comp.compliance_check_results")]
DELETES_AUDIT_GONE = [
    ("audit-log rows of gone records",
     "DELETE FROM audit.audit_log WHERE " + " OR ".join(
         f"(table_name = '{t}' AND entity_id NOT IN ({q}))" for t, q in _GONE)),
]


def connect(autocommit: bool = False) -> pyodbc.Connection:
    return pyodbc.connect(settings.db_connection_string, autocommit=autocommit, timeout=15)


def scalar(cur, sql, *params):
    return cur.execute(sql, params).fetchone()[0]


def backup(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    bak = dest / f"{settings.db_name}.bak"
    with connect(autocommit=True) as c:
        cur = c.cursor()
        cur.execute(f"BACKUP DATABASE [{settings.db_name}] TO DISK = N'{bak}' WITH INIT, COPY_ONLY, "
                    f"NAME = N'{settings.db_name} before mock-data purge'")
        while cur.nextset():          # BACKUP reports progress as extra result sets; drain them
            pass
    with zipfile.ZipFile(dest / "files.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for base in (ROOT / "storage" / "agent", ROOT / "storage" / "agent_traces"):
            for p in base.rglob("*") if base.exists() else []:
                if p.is_file():
                    z.write(p, p.relative_to(ROOT))
        for p in (API / "data" / "dedupe.db",):
            if p.exists():
                z.write(p, p.relative_to(ROOT))
    return bak


def clean_files(keep_cases: set[str], apply: bool) -> None:
    removed = {"case files": 0, "trace files": 0, "trace index lines": 0, "dedupe keys": 0, "dedupe faces": 0}
    cases = ROOT / "storage" / "agent" / "cases"
    for p in cases.glob("*.json") if cases.exists() else []:
        if p.stem.lower() not in keep_cases:
            removed["case files"] += 1
            if apply:
                p.unlink()
    traces = ROOT / "storage" / "agent_traces"
    for p in traces.glob("*/*.json") if traces.exists() else []:
        try:
            cid = str(json.loads(p.read_text(encoding="utf-8")).get("case_id") or "").lower()
        except (OSError, ValueError):
            cid = ""
        if cid not in keep_cases:
            removed["trace files"] += 1
            if apply:
                p.unlink()
    idx = traces / "index.jsonl"
    if idx.exists():
        lines = idx.read_text(encoding="utf-8").splitlines()
        kept = [ln for ln in lines if ln.strip() and str(json.loads(ln).get("case_id") or "").lower() in keep_cases]
        removed["trace index lines"] = len(lines) - len(kept)
        if apply:
            idx.write_text("".join(ln + "\n" for ln in kept), encoding="utf-8")
    db = API / "data" / "dedupe.db"
    if db.exists():
        con = sqlite3.connect(db)
        marks = ",".join("?" * len(keep_cases)) or "''"
        for table in ("keys", "faces"):
            n = con.execute(f"SELECT COUNT(*) FROM {table} WHERE lower(case_id) NOT IN ({marks})", tuple(keep_cases)).fetchone()[0]
            removed["dedupe " + table] = n
            if apply:
                con.execute(f"DELETE FROM {table} WHERE lower(case_id) NOT IN ({marks})", tuple(keep_cases))
        con.commit()
        con.close()
    print("\nFiles" + (" removed:" if apply else " that would be removed:"))
    for k, v in removed.items():
        print(f"  {k:<22}{v:>6}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", action="append", required=True, metavar="APP-NO",
                    help="application number to keep (repeatable)")
    ap.add_argument("--apply", action="store_true", help="really delete (default: dry run)")
    ap.add_argument("--no-backup", action="store_true", help="skip the backup (not recommended)")
    args = ap.parse_args()

    with connect() as conn:
        cur = conn.cursor()
        marks = ",".join("?" * len(args.keep))
        found = [r[0] for r in cur.execute(f"SELECT application_no FROM core.loan_applications WHERE application_no IN ({marks})", args.keep)]
        missing = sorted(set(args.keep) - set(found))
        if missing:
            print(f"REFUSING: these applications do not exist: {', '.join(missing)}. Nothing was changed.")
            return 2
        total = scalar(cur, "SELECT COUNT(*) FROM core.loan_applications")
        print(f"{'APPLY' if args.apply else 'DRY RUN'} | keeping {len(found)} of {total} applications: {', '.join(sorted(found))}\n")

        backup_dir = API / "data" / "backups" / f"pre-purge-{datetime.now():%Y%m%d-%H%M%S}"
        if args.apply and not args.no_backup:
            bak = backup(backup_dir)
            print(f"Backup written: {bak}\n        files: {backup_dir / 'files.zip'}\n")

        for stmt in SETUP:
            cur.execute(stmt)
        cur.execute(f"INSERT #keep_apps SELECT application_id FROM core.loan_applications WHERE application_no IN ({marks})", args.keep)
        cur.execute("INSERT #keep_cases SELECT application_id FROM #keep_apps")
        cur.execute("""INSERT #keep_cases
            SELECT DISTINCT TRY_CAST(JSON_VALUE(a.form_data, '$.case_id') AS uniqueidentifier)
            FROM core.loan_applications a JOIN #keep_apps k ON k.application_id = a.application_id
            WHERE TRY_CAST(JSON_VALUE(a.form_data, '$.case_id') AS uniqueidentifier) IS NOT NULL
              AND TRY_CAST(JSON_VALUE(a.form_data, '$.case_id') AS uniqueidentifier) NOT IN (SELECT case_id FROM #keep_cases)""")
        cur.execute("INSERT #keep_customers SELECT DISTINCT a.customer_id FROM core.loan_applications a JOIN #keep_apps k ON k.application_id = a.application_id")
        cur.execute("""INSERT #keep_customers SELECT c.customer_id FROM core.customers c JOIN ops.users u ON u.email = c.email
                       WHERE u.is_active = 1 AND c.customer_id NOT IN (SELECT customer_id FROM #keep_customers)""")
        cur.execute("INSERT #doomed_apps SELECT application_id FROM core.loan_applications WHERE application_id NOT IN (SELECT application_id FROM #keep_apps)")
        cur.execute("INSERT #doomed_customers SELECT customer_id FROM core.customers WHERE customer_id NOT IN (SELECT customer_id FROM #keep_customers)")
        cur.execute("""INSERT #doomed_cases SELECT case_id FROM fraud.fraud_cases
                       WHERE application_id IN (SELECT application_id FROM #doomed_apps)
                          OR customer_id IN (SELECT customer_id FROM #doomed_customers)""")
        keep_cases = {str(r[0]).lower() for r in cur.execute("SELECT case_id FROM #keep_cases")}
        n_keep_cust = scalar(cur, "SELECT COUNT(*) FROM #keep_customers")
        print(f"keeping {n_keep_cust} customer record(s) (yours + the borrower demo persona) and {len(keep_cases)} case id(s)\n")

        for t in TRIGGERED:
            cur.execute(f"ALTER TABLE {t} DISABLE TRIGGER ALL")
        print(f"{'deleted' if args.apply else 'would delete':<32}rows")
        for label, sql in DELETES:
            cur.execute(sql)
            print(f"  {label:<30}{cur.rowcount:>6}")
        for label, sql in DELETES_AUDIT_GONE:
            cur.execute("DELETE FROM audit.audit_log_details WHERE log_id IN (SELECT log_id FROM audit.audit_log WHERE "
                        + sql.split(" WHERE ", 1)[1] + ")")
            cur.execute(sql)
            print(f"  {label:<30}{cur.rowcount:>6}")

        # kept customers: application counts are stale (they counted the mock applications too)
        cur.execute("""UPDATE c SET application_count = (SELECT COUNT(*) FROM core.loan_applications a WHERE a.customer_id = c.customer_id)
                       FROM core.customers c WHERE c.customer_id IN (SELECT customer_id FROM #keep_customers)""")
        # adopt the kept applications as real data: environment 'prod', and give an unassigned one the default officer
        for table, col in (("core.loan_applications", "application_id"), ("core.uploaded_documents", "application_id")):
            cur.execute(f"UPDATE {table} SET environment_code = 'prod' WHERE {col} IN (SELECT application_id FROM #keep_apps) AND environment_code <> 'prod'")
        cur.execute("UPDATE core.customers SET environment_code = 'prod' WHERE customer_id IN (SELECT customer_id FROM #keep_customers) AND environment_code <> 'prod'")
        cur.execute("UPDATE agent.run SET environment_code = 'prod' WHERE case_id IN (SELECT case_id FROM #keep_cases) AND environment_code <> 'prod'")
        cur.execute("UPDATE pm.event_log SET environment_code = 'prod' WHERE case_id IN (SELECT case_id FROM #keep_cases) AND environment_code <> 'prod'")
        cur.execute("""UPDATE core.loan_applications
                       SET assigned_officer_id = (SELECT TOP 1 user_id FROM ops.users WHERE role_code = 'officer' AND is_active = 1 ORDER BY created_at)
                       WHERE application_id IN (SELECT application_id FROM #keep_apps) AND assigned_officer_id IS NULL""")
        for t in TRIGGERED:
            cur.execute(f"ALTER TABLE {t} ENABLE TRIGGER ALL")

        if not args.apply:
            conn.rollback()
            clean_files(keep_cases, apply=False)
            print("\nDry run only - nothing was changed. Re-run with --apply to delete.")
            return 0
        conn.commit()

    with connect(autocommit=True) as c:
        c.execute("EXEC pm.usp_refresh_all")
        cur = c.cursor()
        print("\nAfter the purge:")
        for label, sql in (("loan applications", "SELECT COUNT(*) FROM core.loan_applications"),
                           ("customers", "SELECT COUNT(*) FROM core.customers"),
                           ("uploaded documents", "SELECT COUNT(*) FROM core.uploaded_documents"),
                           ("process events", "SELECT COUNT(*) FROM pm.event_log"),
                           ("agent runs", "SELECT COUNT(*) FROM agent.run"),
                           ("users (kept)", "SELECT COUNT(*) FROM ops.users")):
            print(f"  {label:<22}{scalar(cur, sql):>6}")
    clean_files(keep_cases, apply=True)
    print("\nDone. Restart the API so its in-memory trace list is empty (launcher\\stop-finsight.bat, then start).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
