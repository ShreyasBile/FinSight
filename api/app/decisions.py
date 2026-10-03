"""Decision workflow: the human steps that move an application after the agents
have processed its documents.

    documentsProcessing ─verify_documents→ documentsVerified ─start_review→ underReview
    underReview ─approve (officer, ≤ ₹5 lakh, no HIGH fraud)→ offerGenerated
    underReview ─refer_to_head→ underwritingReview ─approve (head)→ offerGenerated
                                underwritingReview ─send_back→ underReview
    offerGenerated ─accept_offer (borrower)→ accepted ─sanction→ sanctioned ─disburse→ disbursed
    offerGenerated ─decline_offer→ cancelled
    any status before the offer ─reject→ rejected · ─request_documents→ documentsProcessing

The rules live here, server side, and nowhere else: GET .../actions tells a screen
which buttons this person may press right now (and why the others are disabled),
POST .../actions/{action} re-checks the same rules and performs the step. So the
officer, head and borrower screens cannot drift from each other or from the API.

Every step is ONE status UPDATE; the update trigger writes the status-history row
(actor, remarks, reason code, what the case now waits for - see
docs/mssql/09-decision-workflow.sql) and projects it into pm.event_log, so each
decision is a mineable event. Side rows (the offer, the disbursement, document
verification, in-app notifications) are written in the same transaction.

There is no login yet: the acting person is the actor_id the role picker's
session returned, checked against ops.users and the application.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from . import db
from .db import DatabaseUnavailable

log = logging.getLogger("finsight.decisions")

router = APIRouter(prefix="/lending", tags=["decisions"])

OFFICER_LIMIT = 500000          # above this the Head approves and sanctions (RBI delegation, as in the agents)
HIGH_FRAUD = {"high", "critical"}
OFFER_VALID_DAYS = 30
PRE_OFFER = ("submitted", "documentsProcessing", "documentsVerified", "underReview", "underwritingReview")

# action -> who, from which statuses, to which status, and what the case waits for next
ACTIONS = {
    "verify_documents": dict(
        label="Verify documents", roles=("officer", "head"), frm=("submitted", "documentsProcessing"),
        to="documentsVerified", wait="officer_queue", owner="officer", notify="documents_verified", style="primary",
        hint="Marks the uploaded documents as checked by you."),
    "start_review": dict(
        label="Start underwriting review", roles=("officer", "head"), frm=("documentsVerified",),
        to="underReview", wait="officer_queue", owner="officer", style="primary",
        hint="Moves the case into underwriting, where it is approved, referred or rejected."),
    "approve": dict(
        label="Approve & send offer", roles=("officer", "head"), frm=("underReview", "underwritingReview"),
        to="offerGenerated", wait="awaiting_customer_ack", owner="user", notify="offer_made", style="success",
        offer=True, hint="Sends the loan offer below to the borrower."),
    "refer_to_head": dict(
        label="Refer to Head for approval", roles=("officer",), frm=("underReview",),
        to="underwritingReview", wait="head_approval_queue", owner="head", notify="referred_to_head",
        style="secondary", remarks=True, hint="The Head Manager decides. Say what they should know."),
    "send_back": dict(
        label="Send back to officer", roles=("head",), frm=("underwritingReview",),
        to="underReview", wait="officer_queue", owner="officer", notify="sent_back", style="secondary",
        remarks=True, hint="Returns the case to the officer with your note."),
    "request_documents": dict(
        label="Request more documents", roles=("officer", "head"), frm=PRE_OFFER,
        to="documentsProcessing", wait="awaiting_customer_doc", owner="user", notify="documents_requested",
        style="secondary", remarks=True, hint="The borrower is asked to upload what you list in the remarks."),
    "reject": dict(
        label="Reject application", roles=("officer", "head"), frm=PRE_OFFER,
        to="rejected", wait=None, owner=None, notify="application_rejected", style="danger", remarks=True,
        confirm=True, hint="Closes the application. The remarks are shown to the borrower as the reason."),
    "accept_offer": dict(
        label="Accept offer", roles=("user",), frm=("offerGenerated",),
        to="accepted", wait="officer_queue", owner="officer", notify="offer_accepted", style="success",
        confirm=True, hint="You accept the loan offer; the bank then sanctions the loan."),
    "decline_offer": dict(
        label="Decline offer", roles=("user",), frm=("offerGenerated",),
        to="cancelled", wait=None, owner=None, notify="offer_declined", style="danger", confirm=True,
        hint="Closes the application."),
    "sanction": dict(
        label="Sanction loan", roles=("officer", "head"), frm=("accepted",),
        to="sanctioned", wait="officer_queue", owner="officer", notify="loan_sanctioned", style="primary",
        hint="Issues the sanction; disbursement is the last step."),
    "disburse": dict(
        label="Disburse loan", roles=("officer", "head"), frm=("sanctioned",),
        to="disbursed", wait=None, owner=None, notify="loan_disbursed", style="success", confirm=True,
        hint="Records the payout to the borrower's account and closes the case."),
}
ORDER = list(ACTIONS)


def _guard(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except DatabaseUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


# ── loading ─────────────────────────────────────────────────────────────────
APP_SQL = """
SELECT a.application_id, a.application_no, a.status_code, s.display_name AS status_label, s.is_terminal,
       a.loan_product, p.display_name AS product_name, a.loan_amount, a.tenure_months, a.interest_rate,
       a.assigned_officer_id, a.assigned_head_id, a.current_wait_reason, a.customer_id,
       submitted_by = JSON_VALUE(a.form_data, '$.submitted_by.user_id'),
       submitted_role = JSON_VALUE(a.form_data, '$.submitted_by.role'),
       case_id = JSON_VALUE(a.form_data, '$.case_id'),
       monthly_income = TRY_CAST(JSON_VALUE(a.form_data, '$.loan.monthly_income') AS FLOAT),
       customer_name = c.first_name + N' ' + c.last_name, c.email AS customer_email,
       officer_name = o.full_name, head_name = h.full_name,
       fraud_level = COALESCE(f.severity, LOWER(fa.tier), 'unassessed'),
       risk_score = CAST(r.risk_score * 100 AS INT),
       lp.base_interest_rate, lp.processing_fee_pct, lp.min_amount, lp.max_amount,
       lp.min_tenure_months, lp.max_tenure_months
FROM core.loan_applications a
JOIN core.customers c ON c.customer_id = a.customer_id
JOIN ref.application_status s ON s.status_code = a.status_code
JOIN ref.loan_product_type p ON p.product_code = a.loan_product
LEFT JOIN ops.loan_products lp ON lp.product_code = a.loan_product
LEFT JOIN ops.users o ON o.user_id = a.assigned_officer_id
LEFT JOIN ops.users h ON h.user_id = a.assigned_head_id
OUTER APPLY (SELECT TOP 1 fc.severity FROM fraud.fraud_cases fc
             JOIN ref.fraud_severity fs ON fs.severity_code = fc.severity
             WHERE fc.application_id = a.application_id AND fc.status <> 'dismissed'
             ORDER BY fs.sort_order DESC) f
OUTER APPLY (SELECT TOP 1 x.tier FROM fraud.fraud_assessments x
             WHERE x.application_no = a.application_no OR x.case_id = a.application_id
                OR x.case_id = TRY_CAST(JSON_VALUE(a.form_data, '$.case_id') AS UNIQUEIDENTIFIER)
             ORDER BY x.created_at DESC) fa
OUTER APPLY (SELECT TOP 1 ra.risk_score FROM risk.risk_assessments ra
             WHERE ra.application_id = a.application_id ORDER BY ra.assessed_at DESC) r
WHERE a.application_no = ?
"""


def _load(application_no: str) -> dict:
    app = _guard(db.query_one, APP_SQL, (application_no,))
    if app is None:
        raise HTTPException(404, f"No application {application_no}")
    return app


def _actor(actor_id: str | None) -> dict:
    if not actor_id:
        raise HTTPException(400, "actor_id is required - who is acting?")
    row = _guard(db.query_one, """SELECT u.user_id, u.full_name, u.role_code, u.email
                                  FROM ops.users u
                                  WHERE u.user_id = TRY_CAST(? AS UNIQUEIDENTIFIER) AND u.is_active = 1""",
                 (actor_id,))
    if row is None:
        raise HTTPException(403, "Unknown or inactive user")
    return row


def _same(a, b) -> bool:
    return bool(a) and bool(b) and str(a).lower() == str(b).lower()


def _is_borrower_of(app: dict, actor: dict) -> bool:
    return actor["role_code"] == "user" and (
        _same(app["submitted_by"], actor["user_id"]) or _same(app["customer_email"], actor["email"]))


def _num(v, default=None):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


# ── rules ───────────────────────────────────────────────────────────────────
def _why_not(action: str, app: dict, actor: dict) -> str | None:
    """None if `actor` may perform `action` on `app` now, else the reason they cannot."""
    spec, role, status = ACTIONS[action], actor["role_code"], app["status_code"]
    if role not in spec["roles"]:
        return "not_your_role"
    if status not in spec["frm"]:
        return "not_now"
    if role == "user" and not _is_borrower_of(app, actor):
        return "Only the borrower who applied can do this."
    if role == "officer" and not _same(app["assigned_officer_id"], actor["user_id"]):
        return f"Assigned to {app['officer_name'] or 'another officer'}."
    amount = _num(app["loan_amount"], 0)
    fraud = (app["fraud_level"] or "none").lower()
    if action == "approve" and fraud == "unassessed":
        return "Run the fraud & policy assessment first (Fraud Report tab)."
    if action == "approve" and role == "officer":
        if status == "underwritingReview":
            return "Waiting for the Head Manager's decision."
        if amount > OFFICER_LIMIT:
            return "Above ₹5 lakh - refer it to the Head for approval."
        if fraud in HIGH_FRAUD:
            return f"Fraud risk {fraud.upper()} - only the Head can approve; refer it."
    if action == "sanction" and role == "officer" and amount > OFFICER_LIMIT:
        return "Above ₹5 lakh - the Head sanctions this loan."
    if action == "verify_documents" and not _guard(db.scalar, "SELECT COUNT(*) FROM core.uploaded_documents "
                                                   "WHERE application_id = ?", (app["application_id"],)):
        return "No documents on file to verify."
    if action == "verify_documents" and app["current_wait_reason"] == "awaiting_customer_doc":
        return "Waiting for the borrower to upload the requested documents."
    return None


def _needs_remarks(action: str, app: dict, actor: dict) -> bool:
    if ACTIONS[action].get("remarks"):
        return True
    # the Head overriding a HIGH fraud flag must say why
    return action == "approve" and (app["fraud_level"] or "").lower() in HIGH_FRAUD


def _agent_offer(app: dict) -> dict | None:
    """The Offer Generation agent's draft for this application, if it has run."""
    try:
        from .agents import case_store
        cid = case_store.by_application(app["application_no"]) or app.get("case_id")
        if cid and case_store.exists(cid):
            return case_store.get(cid).get("offer") or None
    except Exception:  # noqa: BLE001 - the draft is a convenience
        log.warning("could not read the agents' offer for %s", app["application_no"])
    return None


def emi(amount: float, rate: float, months: int) -> float:
    r = rate / 12 / 100
    if amount <= 0 or months <= 0:
        return 0.0
    if r <= 0:
        return round(amount / months, 2)
    f = (1 + r) ** months
    return round(amount * r * f / (f - 1), 2)


def offer_draft(app: dict) -> dict:
    """What the approve form starts from: the agents' risk-priced draft when there is
    one, else the requested amount at the product's base rate."""
    agent = _agent_offer(app) or {}
    amount = _num(agent.get("offered_amount")) or _num(app["loan_amount"], 0)
    tenure = int(_num(agent.get("tenure_months")) or _num(app["tenure_months"]) or 36)
    rate = _num(agent.get("interest_rate")) or _num(app["base_interest_rate"]) or 12.0
    fee_pct = _num(app["processing_fee_pct"], 1.0)
    fee = _num(agent.get("processing_fee"))
    if fee is None:
        fee = round(amount * fee_pct / 100, 2)
    return {"amount": amount, "rate": round(rate, 2), "tenure_months": tenure, "processing_fee": fee,
            "emi": emi(amount, rate, tenure), "source": "agents" if agent.get("offered_amount") else "product",
            "capped_by_foir": bool(agent.get("capped_by_foir")),
            "limits": {"min_amount": _num(app["min_amount"]), "max_amount": _num(app["max_amount"]),
                       "min_tenure": app["min_tenure_months"], "max_tenure": app["max_tenure_months"]}}


def _offer_from(body: dict, app: dict) -> dict:
    d = offer_draft(app)
    o = body.get("offer") or {}
    amount = _num(o.get("amount"), d["amount"])
    rate = _num(o.get("rate"), d["rate"])
    tenure = int(_num(o.get("tenure_months"), d["tenure_months"]))
    lim = d["limits"]
    if not amount or amount <= 0:
        raise HTTPException(422, "The offer amount must be more than zero.")
    if lim["max_amount"] and amount > lim["max_amount"]:
        raise HTTPException(422, f"The offer is above the product maximum (₹{lim['max_amount']:,.0f}).")
    if _num(app["loan_amount"]) and amount > _num(app["loan_amount"]):
        raise HTTPException(422, "The offer cannot exceed the amount the borrower asked for.")
    if not 1 <= rate <= 36:
        raise HTTPException(422, "The interest rate must be between 1% and 36%.")
    if (lim["min_tenure"] and tenure < lim["min_tenure"]) or (lim["max_tenure"] and tenure > lim["max_tenure"]):
        raise HTTPException(422, f"The tenure must be {lim['min_tenure']}-{lim['max_tenure']} months.")
    fee = _num(o.get("processing_fee"))
    if fee is None:
        fee = d["processing_fee"] if amount == d["amount"] else round(amount * _num(app["processing_fee_pct"], 1.0) / 100, 2)
    return {"amount": round(amount, 2), "rate": round(rate, 3), "tenure_months": tenure,
            "processing_fee": round(fee, 2), "emi": emi(amount, rate, tenure)}


def _listing(app: dict, actor: dict) -> dict:
    actions = []
    for name in ORDER:
        why = _why_not(name, app, actor)
        if why in ("not_your_role", "not_now"):
            continue                                   # not offered at all on this screen, in this status
        spec = ACTIONS[name]
        actions.append({"action": name, "label": spec["label"], "style": spec["style"], "hint": spec["hint"],
                        "enabled": why is None, "reason": why, "remarks_required": _needs_remarks(name, app, actor),
                        "confirm": bool(spec.get("confirm")), "offer": bool(spec.get("offer"))})
    waiting = None
    if not actions and not app["is_terminal"]:
        waiting = {"underwritingReview": f"Waiting for the Head Manager's decision"
                                         f"{' (' + app['head_name'] + ')' if app['head_name'] else ''}.",
                   "offerGenerated": "Waiting for the borrower to accept the offer.",
                   }.get(app["status_code"])
        if app["current_wait_reason"] == "awaiting_customer_doc":
            waiting = "Waiting for the borrower to upload the requested documents."
    return {"application_no": app["application_no"], "status_code": app["status_code"],
            "status_label": app["status_label"], "role": actor["role_code"], "actions": actions,
            "waiting_for": waiting,
            "offer_draft": offer_draft(app) if any(a["offer"] and a["enabled"] for a in actions) else None,
            "officer_limit": OFFICER_LIMIT}


@router.get("/applications/{application_no}/actions")
def available_actions(application_no: str, actor_id: str = Query(...)):
    return _listing(_load(application_no), _actor(actor_id))


# ── performing an action ────────────────────────────────────────────────────
# Decisions the status history cannot express on its own (a request that leaves the
# status unchanged, the Head's role in a step) also go to pm.event_log as their own
# activity, with the remarks in the attributes.
PM_ACTIVITY = {"request_documents": "DOC_REQUEST_AGAIN", "refer_to_head": "HEAD_APPROVAL_REQ",
               "send_back": "HEAD_RETURN"}


def _log_pm(app: dict, actor: dict, activity: str, attributes: dict):
    """After the commit, on its own connection: usp_log_event runs with XACT_ABORT, so
    inside the decision's transaction any hiccup in it would roll the decision back."""
    try:
        with db.connection() as conn:
            db.log_event(conn, "loan_application", str(app["application_id"]), activity,
                         resource_id=str(actor["user_id"]),
                         resource_type="customer" if actor["role_code"] == "user" else "human",
                         source_system="decision_api", attributes=json.dumps(attributes, default=str))
            conn.commit()
    except Exception:  # noqa: BLE001 - telemetry must never break the decision
        log.warning("could not log %s for %s", activity, app["application_no"])


def _perform(app: dict, actor: dict, action: str, body: dict) -> dict:
    spec = ACTIONS[action]
    remarks = str(body.get("remarks") or "").strip()[:1000]
    if _needs_remarks(action, app, actor) and len(remarks) < 3:
        raise HTTPException(422, "Please add remarks - they are recorded with this decision.")
    offer = _offer_from(body, app) if spec.get("offer") else None
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    app_id, status_from = app["application_id"], app["status_code"]
    head_id = None
    extra: dict = {}
    with db.connection() as conn:
        cur = conn.cursor()
        try:
            if action == "refer_to_head":
                from .lending import SESSION_SQL
                row = cur.execute(SESSION_SQL, ("head",)).fetchone()
                head_id = str(row[0]) if row else None
            owner = {"officer": app["assigned_officer_id"], "head": head_id or app["assigned_head_id"],
                     "user": None}.get(spec["owner"])

            # side rows first, in the same transaction as the status change
            if action == "verify_documents":
                cur.execute("""UPDATE core.uploaded_documents
                               SET verification_status = 'verified', verified_at = SYSUTCDATETIME(),
                                   updated_at = SYSUTCDATETIME()
                               WHERE application_id = ? AND verification_status IN ('pending','processing','resubmitted')""",
                            (app_id,))
                extra["documents_verified"] = cur.rowcount
            elif action == "approve":
                prev = cur.execute("SELECT TOP 1 offer_id, revision_no FROM core.loan_offers WHERE application_id = ? "
                                   "ORDER BY created_at DESC", (app_id,)).fetchone()
                cur.execute("UPDATE core.loan_offers SET status = 'expired' WHERE application_id = ? AND status = 'pending'",
                            (app_id,))
                offer_id = str(uuid.uuid4())
                terms = json.dumps({"approved_by": actor["full_name"], "approved_role": actor["role_code"],
                                    "remarks": remarks or None,
                                    "conditions": ["Offer valid for 30 days", "Hypothecation of the vehicle to FinSight"]
                                    if app["loan_product"] in ("two_wheeler", "commercial_vehicle", "tractor") else
                                    ["Offer valid for 30 days"]})
                cur.execute("""INSERT INTO core.loan_offers (offer_id, application_id, offered_amount, interest_rate,
                                   tenure_months, processing_fee, emi_amount, offer_valid_until, terms_conditions, status,
                                   generated_by_type, generated_by, sent_at, revision_no, revision_of_offer_id)
                               VALUES (?,?,?,?,?,?,?,?,?,'pending','human',?,SYSUTCDATETIME(),?,?)""",
                            (offer_id, app_id, offer["amount"], offer["rate"], offer["tenure_months"],
                             offer["processing_fee"], offer["emi"], now + timedelta(days=OFFER_VALID_DAYS), terms,
                             actor["user_id"], (prev[1] + 1) if prev else 1, prev[0] if prev else None))
                extra["offer_id"] = offer_id
            elif action in ("accept_offer", "decline_offer"):
                o = cur.execute("SELECT TOP 1 offer_id, offer_valid_until FROM core.loan_offers WHERE application_id = ? "
                                "AND status = 'pending' ORDER BY created_at DESC", (app_id,)).fetchone()
                if not o:
                    raise HTTPException(409, "There is no open offer on this application.")
                if action == "accept_offer" and o[1] and o[1] < now:
                    raise HTTPException(409, "This offer has expired.")
                cur.execute("""UPDATE core.loan_offers SET status = ?, decided_at = SYSUTCDATETIME(),
                                   accepted_at = CASE WHEN ? = 'accepted' THEN SYSUTCDATETIME() ELSE accepted_at END,
                                   first_viewed_at = ISNULL(first_viewed_at, SYSUTCDATETIME()),
                                   customer_decision_sec = DATEDIFF(SECOND, sent_at, SYSUTCDATETIME())
                               WHERE offer_id = ?""",
                            ("accepted" if action == "accept_offer" else "rejected",
                             "accepted" if action == "accept_offer" else "rejected", o[0]))
            elif action == "disburse":
                o = cur.execute("SELECT TOP 1 offer_id, offered_amount FROM core.loan_offers WHERE application_id = ? "
                                "AND status = 'accepted' ORDER BY created_at DESC", (app_id,)).fetchone()
                if not o:
                    raise HTTPException(409, "No accepted offer to disburse.")
                ref = f"FSD{now:%y%m%d}{app['application_no'][-6:]}"
                cur.execute("""INSERT INTO core.disbursements (offer_id, application_id, disbursed_amount,
                                   disbursement_method, reference_number, settlement_account, initiated_at,
                                   settled_at, settlement_lag_sec)
                               VALUES (?,?,?,?,?,?,SYSUTCDATETIME(),SYSUTCDATETIME(),0)""",
                            (o[0], app_id, o[1], "NEFT", ref, "Borrower's account on file"))
                extra.update(reference=ref, disbursed_amount=float(o[1]))

            # the status change itself: the trigger writes history + event log from this context
            # change_reason holds only what a person wrote; the action itself is the reason_code
            db.set_change_context(cur, str(actor["user_id"]), action, remarks or None, spec["wait"],
                                  source_system="decision_api")
            cur.execute(
                """UPDATE core.loan_applications
                   SET status_code = ?, current_wait_reason = ?, current_owner_id = ?,
                       current_owner_since = SYSUTCDATETIME(),
                       assigned_head_id = COALESCE(?, assigned_head_id),
                       interest_rate = COALESCE(?, interest_rate),
                       completed_at = CASE WHEN ? IN ('disbursed','rejected','cancelled') THEN SYSUTCDATETIME()
                                           ELSE completed_at END,
                       cancellation_reason_code = CASE WHEN ? IN ('rejected','cancelled') THEN ? ELSE cancellation_reason_code END
                   WHERE application_id = ? AND status_code = ?""",
                (spec["to"], spec["wait"], owner, head_id, offer["rate"] if offer else None,
                 spec["to"], spec["to"], (remarks or spec["label"])[:60], app_id, status_from))
            if cur.rowcount != 1:
                raise HTTPException(409, "The application changed while you were looking at it - reload and try again.")
            db.set_change_context(cur)

            # in-app notifications for whoever acts next
            if spec.get("notify"):
                from .agents.tools import notifier
                ctx = {"application_no": app["application_no"], "applicant": app["customer_name"],
                       "product": app["product_name"], "actor": actor["full_name"], "remarks": remarks or "—",
                       "documents": remarks or "the documents listed by your officer",
                       "amount": (offer or {}).get("amount") or extra.get("disbursed_amount") or app["loan_amount"],
                       "rate": (offer or {}).get("rate"), "tenure": (offer or {}).get("tenure_months"),
                       "emi": (offer or {}).get("emi"), "reference": extra.get("reference"),
                       "valid_until": (now + timedelta(days=OFFER_VALID_DAYS)).strftime("%d %b %Y")}
                if action in ("accept_offer", "decline_offer", "sanction"):
                    ctx["amount"] = cur.execute("SELECT TOP 1 offered_amount FROM core.loan_offers WHERE application_id = ? "
                                                "ORDER BY created_at DESC", (app_id,)).fetchone()[0]
                drafts = notifier.render(spec["notify"], ctx)
                if action == "approve" and actor["role_code"] != "head":
                    drafts = [d for d in drafts if d.get("role") != "officer"]   # the officer knows - they did it
                extra["notified"] = notifier.insert(cur, app_id, drafts)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            try:
                db.set_change_context(cur)
            except Exception:  # noqa: BLE001 - the connection may already be broken
                pass
    activity = PM_ACTIVITY.get(action) or ("HEAD_APPROVE" if action == "approve" and actor["role_code"] == "head" else None)
    if activity:
        _log_pm(app, actor, activity, {"remarks": remarks or None, "from_status": status_from, "to_status": spec["to"],
                                       "fraud_level": app["fraud_level"],
                                       "human_overrode_ai": action == "approve" and (app["fraud_level"] or "") in HIGH_FRAUD})
    return {"ok": True, "action": action, "from": status_from, "to": spec["to"], **extra}


@router.post("/applications/{application_no}/actions/{action}")
async def perform_action(application_no: str, action: str, body: dict = Body(default={})):
    if action not in ACTIONS:
        raise HTTPException(404, f"Unknown action '{action}'")
    app = await run_in_threadpool(_load, application_no)
    actor = await run_in_threadpool(_actor, body.get("actor_id"))
    why = await run_in_threadpool(_why_not, action, app, actor)
    if why == "not_your_role":
        raise HTTPException(403, f"A {actor['role_code']} cannot '{ACTIONS[action]['label']}'.")
    if why == "not_now":
        raise HTTPException(409, f"'{ACTIONS[action]['label']}' is not possible while the application is "
                                 f"{app['status_label']}.")
    if why:
        raise HTTPException(409, why)
    result = await run_in_threadpool(_perform, app, actor, action, body)
    fresh = await run_in_threadpool(_load, application_no)
    return {**result, "status_code": fresh["status_code"], "status_label": fresh["status_label"]}


# ── documents: viewing and re-uploading ─────────────────────────────────────
STORAGE_DIR = Path(__file__).resolve().parents[2] / "storage"


def _doc_files(app_no: str, file_path: str) -> tuple[Path | None, Path | None]:
    """(original, masked) files on disk for one uploaded_documents.file_path."""
    key = Path(str(file_path)).name
    key = key[:-len("_original")] if key.endswith("_original") else key
    folder = STORAGE_DIR / re.sub(r"[^A-Za-z0-9._-]", "_", app_no)
    originals = sorted(folder.glob(f"{key}_original.*")) if folder.is_dir() else []
    masked = folder / f"{key}_masked.jpg"
    return (originals[0] if originals else None), (masked if masked.is_file() else None)


@router.get("/documents/{document_id}/file")
def document_file(document_id: str, actor_id: str = Query(...), variant: str = Query("auto")):
    """The stored document for a reviewer. An officer only ever gets the masked copy
    (PAN / Aadhaar numbers redacted, like the Officer sheet of the audit workbook);
    the Head and the borrower who applied may open the original."""
    actor = _actor(actor_id)
    doc = _guard(db.query_one, """SELECT d.file_path, d.mime_type, d.file_name, a.application_no,
                                         a.assigned_officer_id, JSON_VALUE(a.form_data, '$.submitted_by.user_id') AS submitted_by,
                                         c.email AS customer_email
                                  FROM core.uploaded_documents d
                                  JOIN core.loan_applications a ON a.application_id = d.application_id
                                  JOIN core.customers c ON c.customer_id = a.customer_id
                                  WHERE d.document_id = TRY_CAST(? AS UNIQUEIDENTIFIER)""", (document_id,))
    if doc is None:
        raise HTTPException(404, "No such document")
    role = actor["role_code"]
    if role == "user" and not (_same(doc["submitted_by"], actor["user_id"]) or _same(doc["customer_email"], actor["email"])):
        raise HTTPException(403, "Not your application")
    original, masked = _doc_files(doc["application_no"], doc["file_path"])
    if role == "officer" or variant == "masked":
        if masked is None:
            from .mask_jobs import pending
            raise HTTPException(404, "The masked copy is still being prepared - try again in a minute."
                                if pending(doc["application_no"]) else "No masked copy of this document is available.")
        return FileResponse(masked, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
    if original is None:
        raise HTTPException(404, "The stored file is missing.")
    return FileResponse(original, media_type=doc["mime_type"] or None, filename=doc["file_name"],
                        content_disposition_type="inline", headers={"Cache-Control": "no-store"})


@router.post("/applications/{application_no}/documents")
async def upload_requested_document(application_no: str, actor_id: str = Form(...), doc_type: str = Form(...),
                                    file: UploadFile = File(...)):
    """The borrower answers "Request more documents": the file is stored next to the
    originals as attempt N, recorded in core.uploaded_documents as a resubmission,
    masked in the background, and the officer is notified. The status stays
    Documents Processing until the officer verifies again."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "File too large (15 MB max)")
    app = await run_in_threadpool(_load, application_no)
    actor = await run_in_threadpool(_actor, actor_id)
    if not _is_borrower_of(app, actor) and actor["role_code"] not in ("officer", "head"):
        raise HTTPException(403, "Only the borrower who applied can upload documents here.")
    if app["status_code"] not in PRE_OFFER:
        raise HTTPException(409, f"Documents cannot be added while the application is {app['status_label']}.")
    return await run_in_threadpool(_store_upload, app, actor, re.sub(r"[^a-z0-9_]", "", doc_type.lower()) or "other",
                                   file.filename or "document", file.content_type or "", data)


def _store_upload(app: dict, actor: dict, doc_type: str, file_name: str, mime: str, data: bytes) -> dict:
    import base64
    import hashlib

    from .agents.sql_persist import doc_code
    from .agents.tools import notifier
    from .mask_jobs import schedule
    from .storage import store_application
    app_no, app_id = app["application_no"], app["application_id"]
    attempt = int(_guard(db.scalar, "SELECT ISNULL(MAX(attempt_no), 0) + 1 FROM core.uploaded_documents "
                                    "WHERE application_id = ? AND document_type = ?",
                         (app_id, doc_code(doc_type))) or 1)
    attempt = max(attempt, 2)
    key = f"{doc_type}_a{attempt}"
    name = _guard(db.scalar, "SELECT display_name FROM ref.document_type WHERE document_type_code = ?",
                  (doc_code(doc_type),)) if doc_code(doc_type) != "other" else None
    label = f"{name or doc_type.replace('_', ' ').title()} (upload {attempt})"
    stored = store_application({"app_id": app_no, "applicant_name": app["customer_name"],
                                "loan_type": app["product_name"],
                                "submitted_at": datetime.now().isoformat(timespec="seconds"),
                                "documents": [{"doc_type": key, "label": label, "file_name": file_name, "mime": mime,
                                               "uploaded_at": datetime.now().isoformat(timespec="seconds"),
                                               "fields": {}, "original_b64": base64.b64encode(data).decode(),
                                               "masked_b64": None, "timings": {}}]})
    if not stored.get("ok"):
        raise HTTPException(500, f"Could not store the file: {stored.get('error')}")
    with db.connection() as conn:
        cur = conn.cursor()
        try:
            prev = cur.execute("SELECT TOP 1 document_id FROM core.uploaded_documents WHERE application_id = ? "
                               "AND document_type = ? ORDER BY uploaded_at DESC", (app_id, doc_code(doc_type))).fetchone()
            cur.execute("""INSERT INTO core.uploaded_documents (application_id, document_type, file_name, file_path,
                               file_size_bytes, mime_type, checksum_sha256, ocr_status, verification_status,
                               uploaded_by, attempt_no, resubmission_of_document_id, environment_code)
                           VALUES (?,?,?,?,?,?,?,'pending','resubmitted',?,?,?,'prod')""",
                        (app_id, doc_code(doc_type), file_name[:255], f"storage/{app_no}/{key}_original", len(data),
                         mime[:100] or None, hashlib.sha256(data).hexdigest(), str(actor["user_id"]), attempt,
                         prev[0] if prev else None))
            # the case is back with the officer (no status change - they verify it again)
            cur.execute("""UPDATE core.loan_applications SET current_wait_reason = 'officer_queue',
                               current_owner_id = assigned_officer_id, current_owner_since = SYSUTCDATETIME(),
                               last_activity_at = SYSUTCDATETIME()
                           WHERE application_id = ? AND current_wait_reason = 'awaiting_customer_doc'""", (app_id,))
            n = notifier.insert(cur, app_id, notifier.render("documents_uploaded", {
                "application_no": app_no, "applicant": app["customer_name"],
                "documents": label.lower()}))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    schedule(app_no, key, doc_type, label, data, {}, app.get("case_id"),
             {"role": actor["role_code"], "id": str(actor["user_id"])})
    _log_pm(app, actor, "DOC_RESUBMIT", {"document": label, "attempt_no": attempt, "file_name": file_name})
    return {"ok": True, "document": label, "attempt_no": attempt, "notified": n}


# ── notifications: read state ───────────────────────────────────────────────
@router.post("/notifications/{notification_id}/read")
def mark_read(notification_id: str, body: dict = Body(default={})):
    n = _guard(db.execute, """UPDATE ops.notifications SET is_read = 1, read_at = ISNULL(read_at, SYSUTCDATETIME())
                              WHERE notification_id = TRY_CAST(? AS UNIQUEIDENTIFIER)
                                AND user_id = TRY_CAST(? AS UNIQUEIDENTIFIER)""",
               (notification_id, body.get("user_id")))
    return {"ok": n == 1}


@router.post("/notifications/read-all")
def mark_all_read(body: dict = Body(default={})):
    n = _guard(db.execute, """UPDATE ops.notifications SET is_read = 1, read_at = ISNULL(read_at, SYSUTCDATETIME())
                              WHERE user_id = TRY_CAST(? AS UNIQUEIDENTIFIER) AND is_read = 0""",
               (body.get("user_id"),))
    return {"ok": True, "marked": n}
