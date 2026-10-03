"""Read/write API for the lending data the borrower and officer screens show.

Every endpoint here reads SQL Server. The frontend used to carry this content
as hardcoded arrays in src/data/*; those are gone, and these routes replace
them one for one.

Design notes:
  * Queries are written out in full rather than assembled by an ORM. The schema
    in docs/mssql/ is the source of truth and these read paths are shaped by
    what a screen needs, which is rarely what an entity graph would give you.
  * Derived display values (SLA remaining, risk band, fraud level) are computed
    in SQL, next to the data, so the officer queue and any report agree.
  * A database outage returns 503 with a clear message. Document processing
    (/extract, /face/verify, /tampering/*) does not touch this module and keeps
    working regardless.
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, Query

from . import db
from .db import DatabaseUnavailable

log = logging.getLogger("finsight.lending")

router = APIRouter(prefix="/lending", tags=["lending"])


def _guard(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except DatabaseUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


# ── identity ────────────────────────────────────────────────────────────────
# There is no login yet. RolePicker asks for a role and the backend answers
# with the demo person holding it, so every downstream query is driven by a
# real user_id instead of the frontend inventing one.
SESSION_SQL = """
SELECT TOP 1
       u.user_id, u.full_name, u.email, u.role_code, u.branch_code, u.team_name,
       customer_id = c.customer_id
FROM   ops.users u
LEFT JOIN core.customers c ON c.email = u.email
WHERE  u.role_code = ? AND u.is_active = 1
-- every seeded user shares one created_at, so name the demo officer explicitly:
-- without a tie-break "the first officer" differs between queries (and plans)
ORDER BY u.created_at, CASE u.email WHEN 'priya.sharma@finsight.in' THEN 0 ELSE 1 END, u.email
"""


@router.get("/health")
def lending_health():
    return db.health()


@router.get("/session")
def session(role: str = Query(..., pattern="^(user|officer|head|admin)$")):
    row = _guard(db.query_one, SESSION_SQL, (role,))
    if row is None:
        raise HTTPException(
            404, f"No active user with role '{role}'. Run docs/mssql/07-seed-demo-data.sql.")
    return row


@router.get("/products")
def products():
    """Catalogue + limits. Icons, colours and the wizard's dynamic form schema
    stay in the frontend — those are presentation, not data."""
    rows = _guard(db.query, """
        SELECT p.product_code, p.display_name, p.description,
               p.min_amount, p.max_amount, p.min_tenure_months, p.max_tenure_months,
               p.base_interest_rate, p.processing_fee_pct, p.target_tat_hours,
               t.is_secured,
               required_documents =
                   (SELECT d.document_type, dt.display_name, d.is_mandatory, d.display_order
                    FROM ops.loan_product_document_requirements d
                    JOIN ref.document_type dt ON dt.document_type_code = d.document_type
                    WHERE d.product_id = p.product_id
                    ORDER BY d.display_order
                    FOR JSON PATH)
        FROM   ops.loan_products p
        JOIN   ref.loan_product_type t ON t.product_code = p.product_code
        WHERE  p.is_active = 1
        ORDER BY p.display_name
    """)
    return {"products": rows}


# ── shared application projection ───────────────────────────────────────────
# One query shape, two audiences. Keeping it in a single string means the
# borrower's "stage" and the officer's "status" can never drift apart.
APP_SELECT = """
SELECT a.application_id,
       a.application_no,
       a.loan_product,
       product_name        = p.display_name,
       is_secured          = p.is_secured,
       a.status_code,
       status_label        = s.display_name,
       stage_group         = s.stage_group,
       -- a document request puts the ball in the borrower's court whatever the status
       officer_next_action = CASE WHEN a.current_wait_reason = 'awaiting_customer_doc'
                                  THEN N'Awaiting borrower documents' ELSE s.officer_next_action END,
       borrower_next_action= CASE WHEN a.current_wait_reason = 'awaiting_customer_doc'
                                  THEN N'Upload requested documents' ELSE s.borrower_next_action END,
       is_terminal         = s.is_terminal,
       a.loan_amount,
       a.tenure_months,
       a.interest_rate,
       a.branch_code,
       a.applied_at,
       a.submitted_at,
       a.case_started_at,
       a.case_ended_at,
       a.is_case_closed,
       a.last_activity_at,
       a.sla_due_at,
       -- Minutes to (or past) the SLA. Negative means breached; the UI just
       -- formats it, it does not decide it.
       sla_minutes_left    = CASE WHEN a.is_case_closed = 1 THEN NULL   -- a closed case has no clock
                                  ELSE DATEDIFF(MINUTE, SYSUTCDATETIME(), a.sla_due_at) END,
       a.priority,
       -- live, from the history (the stored column is only refreshed by pm.usp_refresh_case_metrics)
       rework_count        = (SELECT COUNT(*) FROM core.application_status_history h
                              WHERE h.application_id = a.application_id AND h.is_rework = 1),
       a.handover_count,
       a.event_count,
       a.automation_ratio,
       a.is_straight_through,
       customer_name       = c.first_name + N' ' + c.last_name,
       a.customer_id,
       c.credit_score,
       officer_name        = o.full_name,
       a.assigned_officer_id,
       a.assigned_head_id,
       head_name           = hd.full_name,
       a.current_wait_reason,
       a.cancellation_reason_code,
       wait_label          = wr.display_name,
       submitted_by_role   = JSON_VALUE(a.form_data, '$.submitted_by.role'),
       risk_score          = CAST(r.risk_score * 100 AS INT),
       risk_level          = r.overall_risk_level,
       -- an open fraud case decides the badge; with none, the Fraud Fusion agent's latest tier
       fraud_level         = COALESCE(f.severity, LOWER(fa.tier), 'unassessed'),
       fraud_category      = f.fraud_category,
       open_offer_amount   = ofr.offered_amount,
       open_offer_status   = ofr.status
FROM   core.loan_applications a
JOIN   core.customers c        ON c.customer_id = a.customer_id
JOIN   ref.loan_product_type p ON p.product_code = a.loan_product
JOIN   ref.application_status s ON s.status_code = a.status_code
LEFT JOIN ops.users o          ON o.user_id = a.assigned_officer_id
LEFT JOIN ops.users hd         ON hd.user_id = a.assigned_head_id
LEFT JOIN ref.wait_reason wr   ON wr.wait_reason_code = a.current_wait_reason
OUTER APPLY (SELECT TOP 1 ra.risk_score, ra.overall_risk_level
             FROM risk.risk_assessments ra
             WHERE ra.application_id = a.application_id
             ORDER BY ra.assessed_at DESC) r
-- Highest-severity live fraud case drives the badge; dismissed cases do not.
OUTER APPLY (SELECT TOP 1 fc.severity, fc.fraud_category
             FROM fraud.fraud_cases fc
             JOIN ref.fraud_severity fs ON fs.severity_code = fc.severity
             WHERE fc.application_id = a.application_id AND fc.status <> 'dismissed'
             ORDER BY fs.sort_order DESC) f
OUTER APPLY (SELECT TOP 1 x.tier
             FROM fraud.fraud_assessments x
             WHERE x.application_no = a.application_no
                OR x.case_id = a.application_id
                OR x.case_id = TRY_CAST(JSON_VALUE(a.form_data, '$.case_id') AS UNIQUEIDENTIFIER)
             ORDER BY x.created_at DESC) fa
OUTER APPLY (SELECT TOP 1 lo.offered_amount, lo.status
             FROM core.loan_offers lo
             WHERE lo.application_id = a.application_id
             ORDER BY lo.created_at DESC) ofr
WHERE  a.environment_code = 'prod'
"""


@router.get("/applications")
def applications(
    customer_id: str | None = None,
    submitted_by: str | None = None,
    officer_id: str | None = None,
    include_closed: bool = True,
    search: str | None = None,
):
    """Borrower list (customer_id) or officer queue (officer_id).

    Passing neither returns every application — used by the head's views.
    """
    sql = APP_SELECT
    params: list = []
    if customer_id and submitted_by:
        # a borrower's "My Applications": applications for them, and applications they submitted
        sql += " AND (a.customer_id = ? OR JSON_VALUE(a.form_data, '$.submitted_by.user_id') = ?)"
        params += [customer_id, submitted_by]
    elif customer_id:
        sql += " AND a.customer_id = ?"
        params.append(customer_id)
    elif submitted_by:
        sql += " AND JSON_VALUE(a.form_data, '$.submitted_by.user_id') = ?"
        params.append(submitted_by)
    if officer_id:
        sql += " AND a.assigned_officer_id = ?"
        params.append(officer_id)
    if not include_closed:
        sql += " AND a.is_case_closed = 0"
    if search:
        sql += (" AND (a.application_no LIKE ? OR"
                " (c.first_name + N' ' + c.last_name) LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]
    sql += " ORDER BY a.applied_at DESC"

    rows = _guard(db.query, sql, params)
    return {"applications": rows, "count": len(rows)}


@router.get("/applications/{application_no}")
def application_detail(application_no: str):
    sql = APP_SELECT + " AND a.application_no = ?"
    app = _guard(db.query_one, sql, (application_no,))
    if app is None:
        raise HTTPException(404, f"No application {application_no}")

    app_id = app["application_id"]

    # Full customer record for the applicant-profile panel. The list projection
    # only carries name + credit score; the detail view needs KYC identity and
    # income fields, which live on core.customers.
    app["customer"] = _guard(db.query_one, """
        SELECT c.customer_id, c.first_name, c.last_name, c.date_of_birth,
               c.mobile, c.email, c.pan, c.occupation,
               -- only the last four digits exist (UIDAI masking); shown as XXXX-XXXX-1234
               aadhaar = CASE WHEN c.aadhaar_last4 IS NOT NULL THEN N'XXXXXXXX' + c.aadhaar_last4 END,
               c.annual_income, c.credit_score,
               c.address_line1, c.address_line2, c.city, c.state, c.pincode,
               c.application_count, c.first_application_at
        FROM   core.customers c
        JOIN   core.loan_applications a ON a.customer_id = c.customer_id
        WHERE  a.application_id = ?
    """, (app_id,))

    app["timeline"] = _guard(db.query, """
        SELECT h.event_seq, h.from_status, h.to_status,
               from_label = sf.display_name, to_label = st.display_name,
               stage_group = st.stage_group,
               h.created_at, h.duration_in_previous_sec, h.is_automated,
               h.is_rework, h.is_backward_transition,
               h.change_reason, h.reason_code,
               actor = u.full_name, actor_role = u.role_code
        FROM   core.application_status_history h
        JOIN   ref.application_status st ON st.status_code = h.to_status
        LEFT JOIN ref.application_status sf ON sf.status_code = h.from_status
        LEFT JOIN ops.users u ON u.user_id = h.changed_by
        WHERE  h.application_id = ?
        ORDER BY h.event_seq
    """, (app_id,))

    app["documents"] = _guard(db.query, """
        SELECT d.document_id, d.document_type, doc_label = dt.display_name, d.file_path,
               d.file_name, d.ocr_status, d.verification_status, d.uploaded_at,
               d.ocr_confidence, d.needs_review, d.tampering_risk_tier,
               d.attempt_no, d.preprocess_ms, d.extract_ms, d.tampering_ms,
               d.face_ms, d.mask_ms
        FROM   core.uploaded_documents d
        JOIN   ref.document_type dt ON dt.document_type_code = d.document_type
        WHERE  d.application_id = ?
        ORDER BY d.uploaded_at
    """, (app_id,))

    for d in app["documents"]:
        d["doc_label"] = _doc_label(d)
        d.pop("file_path", None)

    app["fraud_cases"] = _guard(db.query, """
        SELECT c.case_id, c.severity, c.status, c.fraud_category, c.description,
               c.ai_confidence, c.detection_method, c.created_at, c.sla_breached,
               signals = (SELECT s.signal_name, s.signal_score, s.signal_category,
                                 s.component_code, s.compute_ms, s.model_version
                          FROM fraud.fraud_signals s WHERE s.case_id = c.case_id
                          FOR JSON PATH)
        FROM   fraud.fraud_cases c
        WHERE  c.application_id = ?
        ORDER BY c.created_at DESC
    """, (app_id,))

    app["offers"] = _guard(db.query, """
        SELECT offer_id, offered_amount, interest_rate, tenure_months, emi_amount,
               processing_fee, status, offer_valid_until, sent_at, decided_at,
               customer_decision_sec, revision_no, terms_conditions,
               approved_by = u.full_name
        FROM   core.loan_offers o
        LEFT JOIN ops.users u ON u.user_id = o.generated_by
        WHERE  o.application_id = ?
        ORDER BY o.created_at DESC
    """, (app_id,))

    app["disbursements"] = _guard(db.query, """
        SELECT disbursed_amount, disbursed_at, disbursement_method, reference_number, settlement_account
        FROM   core.disbursements WHERE application_id = ?
        ORDER BY disbursed_at DESC
    """, (app_id,))

    # What the borrower was last asked to upload - open while the case waits on them.
    app["document_request"] = None
    if app.get("current_wait_reason") == "awaiting_customer_doc":
        app["document_request"] = _guard(db.query_one, """
            SELECT TOP 1 remarks = JSON_VALUE(e.attributes, '$.remarks'), requested_at = e.occurred_at,
                   requested_by = e.resource_name
            FROM   pm.event_log e
            JOIN   pm.activity act ON act.activity_id = e.activity_id
            WHERE  e.process_code = 'loan_application' AND e.case_id = ?
              AND  act.activity_code = 'DOC_REQUEST_AGAIN'
            ORDER BY e.occurred_at DESC, e.event_seq DESC
        """, (app_id,))

    return app


def _doc_label(d: dict) -> str:
    """The database keeps a fixed set of document types; the wizard's others (dealer
    quotation, land records, MOA...) are stored as 'other'. Name those after the key
    they were stored under, e.g. storage/APP-1/vehicle_quotation_original -> Vehicle Quotation."""
    label = d.get("doc_label") or "Document"
    if d.get("document_type") != "other":
        return label
    key = re.sub(r"(_a\d+)?_original$", "", str(d.get("file_path") or "").replace("\\", "/").rsplit("/", 1)[-1])
    return key.replace("_", " ").title() if key and key != "other" else label


@router.get("/notifications")
def notifications(user_id: str, limit: int = 30):
    rows = _guard(db.query, """
        SELECT TOP (?) n.notification_id, n.title, n.body, n.priority, n.is_read,
               n.sent_at, n.read_at, n.reference_type,
               application_no = a.application_no,
               minutes_ago = DATEDIFF(MINUTE, n.sent_at, SYSUTCDATETIME())
        FROM   ops.notifications n
        LEFT JOIN core.loan_applications a ON a.application_id = n.application_id
        WHERE  n.user_id = ?
        ORDER BY n.sent_at DESC
    """, (limit, user_id))
    unread = sum(1 for r in rows if not r["is_read"])
    return {"notifications": rows, "unread": unread}


@router.get("/fraud/cases")
def fraud_cases(officer_id: str | None = None):
    sql = """
        SELECT c.case_id, c.severity, c.status, c.fraud_category, c.description,
               c.ai_confidence, score = CAST(c.ai_confidence * 100 AS INT),
               c.detection_method, c.created_at, c.sla_breached,
               c.time_to_detect_sec, c.time_to_resolve_sec,
               application_no = a.application_no,
               customer_name  = cu.first_name + N' ' + cu.last_name,
               branch         = a.branch_code,
               officer_name   = o.full_name,
               c.assigned_to
        FROM   fraud.fraud_cases c
        LEFT JOIN core.loan_applications a ON a.application_id = c.application_id
        LEFT JOIN core.customers cu ON cu.customer_id = c.customer_id
        LEFT JOIN ops.users o ON o.user_id = c.assigned_to
        WHERE  c.environment_code = 'prod'
    """
    params: list = []
    if officer_id:
        sql += " AND c.assigned_to = ?"
        params.append(officer_id)
    sql += " ORDER BY c.ai_confidence DESC"
    rows = _guard(db.query, sql, params)
    return {"cases": rows, "count": len(rows)}


@router.get("/policy-tickets")
def policy_tickets(officer_id: str | None = None):
    sql = """
        SELECT t.ticket_no, t.issue, t.policy_ref, t.priority, t.status,
               t.created_at, t.resolved_at, t.resolution, t.resolution_sec,
               raised_by_name = u.full_name,
               application_no = a.application_no
        FROM   ops.policy_tickets t
        JOIN   ops.users u ON u.user_id = t.raised_by
        LEFT JOIN core.loan_applications a ON a.application_id = t.application_id
    """
    params: list = []
    if officer_id:
        sql += " WHERE t.raised_by = ?"
        params.append(officer_id)
    sql += " ORDER BY t.created_at DESC"
    return {"tickets": _guard(db.query, sql, params)}


@router.get("/reports")
def reports(officer_id: str | None = None):
    sql = """
        SELECT r.report_no, r.report_type, r.status, r.generated_at, r.sent_at,
               r.generation_ms, r.regenerated_count,
               application_no = a.application_no,
               customer_name  = c.first_name + N' ' + c.last_name,
               generated_by_name = u.full_name
        FROM   ops.generated_reports r
        JOIN   core.loan_applications a ON a.application_id = r.application_id
        JOIN   core.customers c ON c.customer_id = a.customer_id
        LEFT JOIN ops.users u ON u.user_id = r.generated_by
    """
    params: list = []
    if officer_id:
        # theirs: what they generated, and what the agents drafted on their applications
        sql += " WHERE r.generated_by = ? OR a.assigned_officer_id = ?"
        params += [officer_id, officer_id]
    sql += " ORDER BY r.generated_at DESC"
    return {"reports": _guard(db.query, sql, params)}


@router.get("/officer/stats")
def officer_stats(officer_id: str):
    """The numbers on the officer's queue header and performance card — counted
    in SQL rather than by the browser, so they match the underlying rows even
    when the list is paged or filtered."""
    queue = _guard(db.query_one, """
        SELECT total        = COUNT(*),
               high_risk    = SUM(CASE WHEN r.risk_score >= 0.70 THEN 1 ELSE 0 END),
               pending      = SUM(CASE WHEN a.status_code IN
                                    ('underReview','underwritingReview') THEN 1 ELSE 0 END),
               sla_breached = SUM(CASE WHEN a.sla_due_at < SYSUTCDATETIME()
                                        AND a.is_case_closed = 0 THEN 1 ELSE 0 END),
               approved     = SUM(CASE WHEN a.status_code IN
                                    ('accepted','sanctioned','disbursed') THEN 1 ELSE 0 END),
               rejected     = SUM(CASE WHEN a.status_code = 'rejected' THEN 1 ELSE 0 END),
               awaiting_docs= SUM(CASE WHEN a.status_code = 'documentsProcessing' THEN 1 ELSE 0 END)
        FROM   core.loan_applications a
        OUTER APPLY (SELECT TOP 1 risk_score FROM risk.risk_assessments ra
                     WHERE ra.application_id = a.application_id
                     ORDER BY assessed_at DESC) r
        WHERE  a.assigned_officer_id = ? AND a.environment_code = 'prod'
    """, (officer_id,))

    perf = _guard(db.query_one, """
        SELECT avg_hours = AVG(CAST(a.case_duration_sec AS FLOAT)) / 3600.0,
               fraud_found = (SELECT COUNT(*) FROM fraud.fraud_cases f
                              WHERE f.assigned_to = ? AND f.status IN ('confirmed','escalated')),
               tickets_raised = (SELECT COUNT(*) FROM ops.policy_tickets t WHERE t.raised_by = ?)
        FROM   core.loan_applications a
        WHERE  a.assigned_officer_id = ? AND a.case_duration_sec IS NOT NULL
    """, (officer_id, officer_id, officer_id))

    # Branch leaderboard, so "rank in branch" is a fact rather than a constant.
    leaderboard = _guard(db.query, """
        SELECT officer_id = u.user_id, u.full_name, u.branch_code,
               apps = COUNT(a.application_id)
        FROM   ops.users u
        LEFT JOIN core.loan_applications a
               ON a.assigned_officer_id = u.user_id AND a.environment_code = 'prod'
        WHERE  u.role_code = 'officer'
        GROUP BY u.user_id, u.full_name, u.branch_code
        ORDER BY apps DESC
    """)
    return {"queue": queue, "performance": perf, "leaderboard": leaderboard}


@router.get("/borrower/stats")
def borrower_stats(customer_id: str, submitted_by: str | None = None):
    row = _guard(db.query_one, """
        SELECT total    = COUNT(*),
               active   = SUM(CASE WHEN s.is_terminal = 0 THEN 1 ELSE 0 END),
               -- the borrower's move: finish a draft, answer a document request, decide on an offer
               awaiting = SUM(CASE WHEN a.status_code IN ('draft','offerGenerated')
                                     OR a.current_wait_reason = 'awaiting_customer_doc'
                              THEN 1 ELSE 0 END),
               offers   = SUM(CASE WHEN a.status_code = 'offerGenerated' THEN 1 ELSE 0 END)
        FROM   core.loan_applications a
        JOIN   ref.application_status s ON s.status_code = a.status_code
        WHERE  (a.customer_id = ? OR JSON_VALUE(a.form_data, '$.submitted_by.user_id') = ?)
          AND  a.environment_code = 'prod'
    """, (customer_id, submitted_by or ""))
    return row or {"total": 0, "active": 0, "awaiting": 0, "offers": 0}


# ── head-manager overview ────────────────────────────────────────────────────
_LATEST_RISK = """OUTER APPLY (SELECT TOP 1 ra.risk_score FROM risk.risk_assessments ra
                             WHERE ra.application_id = a.application_id
                             ORDER BY ra.assessed_at DESC) r"""
_APPROVED = "('accepted','sanctioned','disbursed')"
_IN_REVIEW = "('underReview','underwritingReview')"


@router.get("/overview")
def overview():
    """Portfolio-level numbers behind the head manager's dashboard, portfolio-risk,
    fraud and relationship pages — counted in SQL over every stored application, so
    they can never disagree with the application lists. Nothing here is a constant:
    with no applications every number is 0 and every list is empty."""
    totals = _guard(db.query_one, f"""
        SELECT applications     = COUNT(*),
               active           = ISNULL(SUM(CASE WHEN a.is_case_closed = 0 THEN 1 ELSE 0 END), 0),
               customers        = COUNT(DISTINCT a.customer_id),
               requested_amount = ISNULL(SUM(a.loan_amount), 0),
               approved_amount  = ISNULL(SUM(CASE WHEN a.status_code IN {_APPROVED} THEN a.loan_amount END), 0),
               approved         = ISNULL(SUM(CASE WHEN a.status_code IN {_APPROVED} THEN 1 ELSE 0 END), 0),
               rejected         = ISNULL(SUM(CASE WHEN a.status_code = 'rejected' THEN 1 ELSE 0 END), 0),
               pending_review   = ISNULL(SUM(CASE WHEN a.status_code IN {_IN_REVIEW} THEN 1 ELSE 0 END), 0),
               new_applications = ISNULL(SUM(CASE WHEN a.status_code IN ('draft','submitted') THEN 1 ELSE 0 END), 0),
               needs_head       = ISNULL(SUM(CASE WHEN a.status_code = 'underwritingReview' OR (a.loan_amount > 500000 AND a.is_case_closed = 0) THEN 1 ELSE 0 END), 0),
               sla_breached     = ISNULL(SUM(CASE WHEN a.sla_due_at < SYSUTCDATETIME() AND a.is_case_closed = 0 THEN 1 ELSE 0 END), 0),
               scored           = ISNULL(SUM(CASE WHEN r.risk_score IS NOT NULL THEN 1 ELSE 0 END), 0),
               avg_risk         = AVG(CAST(r.risk_score * 100 AS FLOAT)),
               high_risk        = ISNULL(SUM(CASE WHEN r.risk_score >= 0.70 THEN 1 ELSE 0 END), 0)
        FROM   core.loan_applications a {_LATEST_RISK}
        WHERE  a.environment_code = 'prod'""")

    by_status = _guard(db.query, """
        SELECT a.status_code, label = s.display_name, stage_group = s.stage_group, n = COUNT(*)
        FROM   core.loan_applications a
        JOIN   ref.application_status s ON s.status_code = a.status_code
        WHERE  a.environment_code = 'prod'
        GROUP BY a.status_code, s.display_name, s.stage_group
        ORDER BY COUNT(*) DESC, s.display_name""")

    by_product = _guard(db.query, f"""
        SELECT a.loan_product, name = p.display_name, n = COUNT(*),
               amount = ISNULL(SUM(a.loan_amount), 0),
               avg_risk = AVG(CAST(r.risk_score * 100 AS FLOAT)),
               high_risk = ISNULL(SUM(CASE WHEN r.risk_score >= 0.70 THEN 1 ELSE 0 END), 0)
        FROM   core.loan_applications a
        JOIN   ref.loan_product_type p ON p.product_code = a.loan_product {_LATEST_RISK}
        WHERE  a.environment_code = 'prod'
        GROUP BY a.loan_product, p.display_name
        ORDER BY COUNT(*) DESC, p.display_name""")

    # same bands as the RiskBadge component: <40 low, <65 medium, <80 high, else very high
    risk_bands = _guard(db.query, f"""
        SELECT band, n = COUNT(*) FROM (
            SELECT band = CASE WHEN r.risk_score IS NULL THEN 'unscored'
                               WHEN r.risk_score < 0.40 THEN 'low'
                               WHEN r.risk_score < 0.65 THEN 'medium'
                               WHEN r.risk_score < 0.80 THEN 'high' ELSE 'very_high' END
            FROM core.loan_applications a {_LATEST_RISK}
            WHERE a.environment_code = 'prod') x
        GROUP BY band""")

    by_branch = _guard(db.query, f"""
        SELECT branch = ISNULL(a.branch_code, N'—'), n = COUNT(*),
               amount = ISNULL(SUM(a.loan_amount), 0),
               avg_risk = AVG(CAST(r.risk_score * 100 AS FLOAT))
        FROM   core.loan_applications a {_LATEST_RISK}
        WHERE  a.environment_code = 'prod'
        GROUP BY ISNULL(a.branch_code, N'—')
        ORDER BY COUNT(*) DESC""")

    officers = _guard(db.query, f"""
        SELECT u.user_id, u.full_name, u.branch_code,
               assigned     = COUNT(a.application_id),
               open_cases   = ISNULL(SUM(CASE WHEN a.is_case_closed = 0 THEN 1 ELSE 0 END), 0),
               pending      = ISNULL(SUM(CASE WHEN a.status_code IN {_IN_REVIEW} THEN 1 ELSE 0 END), 0),
               sla_breached = ISNULL(SUM(CASE WHEN a.sla_due_at < SYSUTCDATETIME() AND a.is_case_closed = 0 THEN 1 ELSE 0 END), 0)
        FROM   ops.users u
        LEFT JOIN core.loan_applications a
               ON a.assigned_officer_id = u.user_id AND a.environment_code = 'prod'
        WHERE  u.role_code = 'officer' AND u.is_active = 1
        GROUP BY u.user_id, u.full_name, u.branch_code
        ORDER BY COUNT(a.application_id) DESC, u.full_name""")

    fraud_where = "c.environment_code = 'prod' AND c.status <> 'dismissed'"
    fraud = {
        "cases": _guard(db.query_one, f"SELECT n = COUNT(*) FROM fraud.fraud_cases c WHERE {fraud_where}")["n"],
        "by_severity": _guard(db.query, f"""SELECT c.severity, n = COUNT(*) FROM fraud.fraud_cases c
                                            WHERE {fraud_where} GROUP BY c.severity"""),
        "by_category": _guard(db.query, f"""SELECT category = ISNULL(c.fraud_category, N'Uncategorised'), n = COUNT(*)
                                            FROM fraud.fraud_cases c WHERE {fraud_where}
                                            GROUP BY ISNULL(c.fraud_category, N'Uncategorised') ORDER BY COUNT(*) DESC"""),
        "by_status": _guard(db.query, f"""SELECT c.status, n = COUNT(*) FROM fraud.fraud_cases c
                                          WHERE {fraud_where} GROUP BY c.status"""),
        "top_signals": _guard(db.query, f"""SELECT TOP 6 s.signal_name, n = COUNT(*)
                                            FROM fraud.fraud_signals s JOIN fraud.fraud_cases c ON c.case_id = s.case_id
                                            WHERE {fraud_where} GROUP BY s.signal_name ORDER BY COUNT(*) DESC"""),
    }

    documents = _guard(db.query_one, """
        SELECT documents = COUNT(*),
               needs_review = ISNULL(SUM(CASE WHEN d.needs_review = 1 THEN 1 ELSE 0 END), 0),
               tamper_flagged = ISNULL(SUM(CASE WHEN d.tampering_risk_tier IN ('MEDIUM','HIGH') THEN 1 ELSE 0 END), 0),
               tamper_high = ISNULL(SUM(CASE WHEN d.tampering_risk_tier = 'HIGH' THEN 1 ELSE 0 END), 0)
        FROM   core.uploaded_documents d WHERE d.environment_code = 'prod'""")

    return {"totals": totals, "by_status": by_status, "by_product": by_product, "risk_bands": risk_bands,
            "by_branch": by_branch, "officers": officers, "fraud": fraud, "documents": documents}


@router.get("/users")
def users():
    """The people in ops.users (read-only): who can sign in as which role."""
    return {"users": _guard(db.query, """
        SELECT u.user_id, u.full_name, u.email, u.role_code, u.branch_code, u.team_name, u.is_active
        FROM   ops.users u
        ORDER BY CASE u.role_code WHEN 'head' THEN 0 WHEN 'officer' THEN 1 ELSE 2 END, u.full_name""")}


@router.get("/audit")
def audit(limit: int = Query(100, ge=1, le=500)):
    """The most recent rows of audit.audit_log — written by database triggers, so every change to an
    application, customer, offer, fraud case, risk assessment or user appears here."""
    return {"entries": _guard(db.query, """
        SELECT TOP (?) l.log_id, l.table_name, l.operation, l.entity_id, l.occurred_at, l.source_system,
               l.changed_columns, changed_by = u.full_name, application_no = a.application_no
        FROM   audit.audit_log l
        LEFT JOIN ops.users u ON u.user_id = l.changed_by
        LEFT JOIN core.loan_applications a ON a.application_id = l.application_id
        ORDER BY l.occurred_at DESC, l.log_id DESC""", (limit,))}
