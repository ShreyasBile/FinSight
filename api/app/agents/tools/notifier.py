"""Notification drafts from templates. Nothing is ever sent outside the app:
every row is channel 'in_app' and lands in ops.notifications (only when the
application exists in SQL Server) — there is no email/SMS/WhatsApp code path
in this module at all, and the evaluation checks that no network connection
is attempted while notifications are produced.

Templates are data (TEMPLATES below) and are mirrored into
ops.notification_templates on first use, so the notification pages can show
which template produced a row.
"""
from __future__ import annotations

import json
import re
import string
import uuid

TEMPLATES = {
    "application_submitted.officer": {
        "event": "application_submitted", "role": "officer", "priority": "medium",
        "subject": "New application {application_no}",
        "body": "{applicant} submitted a {product} application ({application_no}). "
                "Documents are processed; the case is ready for review."},
    "application_submitted.borrower": {
        "event": "application_submitted", "role": "user", "priority": "low",
        "subject": "We received your application {application_no}",
        "body": "Thank you, {applicant}. Your {product} application {application_no} was received. "
                "We will notify you here when an officer has reviewed it."},
    "assessment_referred.officer": {
        "event": "assessment_referred", "role": "officer", "priority": "high",
        "subject": "Application {application_no} referred for review",
        "body": "The case assessment referred {application_no}: fraud tier {fraud_tier}, "
                "policy {policy_status}. Review the evidence before continuing."},
    "assessment_referred.head": {
        "event": "assessment_referred", "role": "head", "priority": "high",
        "subject": "High-risk referral: {application_no}",
        "body": "{application_no} was referred with fraud tier {fraud_tier}. "
                "An officer has been notified; your sign-off is required to proceed."},
    "underwriting_completed.officer": {
        "event": "underwriting_completed", "role": "officer", "priority": "medium",
        "subject": "Credit memo draft ready: {application_no}",
        "body": "The underwriting agents drafted a credit memo for {application_no}. "
                "Recommendation: {recommendation}. Next step: {next_step}."},
    "underwriting_completed.head": {
        "event": "underwriting_completed", "role": "head", "priority": "high",
        "subject": "Approval needed: {application_no}",
        "body": "{application_no} requests {amount}, above the officer limit. "
                "Recommendation: {recommendation}. Head approval is required."},
    "offer_drafted.officer": {
        "event": "offer_drafted", "role": "officer", "priority": "medium",
        "subject": "Draft offer for {application_no}",
        "body": "A draft offer was prepared for {application_no}: {amount} at {rate}% for "
                "{tenure} months (EMI {emi}). Review it before it is sent to the borrower."},
    "more_info_requested.borrower": {
        "event": "more_info_requested", "role": "user", "priority": "medium",
        "subject": "More information needed for {application_no}",
        "body": "To continue with {application_no}, please upload: {documents}."},
    "documents_verified.borrower": {
        "event": "documents_verified", "role": "user", "priority": "low",
        "subject": "Documents verified for {application_no}",
        "body": "Your documents for {application_no} have been verified. No action is needed from you."},
    # ── decision workflow (api/app/decisions.py): one event per human action ──
    "documents_requested.borrower": {
        "event": "documents_requested", "role": "user", "priority": "high",
        "subject": "More documents needed for {application_no}",
        "body": "Your officer {actor} asks: \u201c{remarks}\u201d Open the application to upload them."},
    "documents_uploaded.officer": {
        "event": "documents_uploaded", "role": "officer", "priority": "medium",
        "subject": "Documents uploaded: {application_no}",
        "body": "{applicant} uploaded {documents} for {application_no}, as requested. Verify them to continue."},
    "referred_to_head.head": {
        "event": "referred_to_head", "role": "head", "priority": "high",
        "subject": "Approval needed: {application_no}",
        "body": "{actor} referred {application_no} ({applicant}, {amount}) for your decision. Note: {remarks}"},
    "sent_back.officer": {
        "event": "sent_back", "role": "officer", "priority": "high",
        "subject": "{application_no} sent back for review",
        "body": "{actor} sent {application_no} back to you: {remarks}"},
    "offer_made.borrower": {
        "event": "offer_made", "role": "user", "priority": "high",
        "subject": "Your loan offer is ready: {application_no}",
        "body": "You are offered {amount} at {rate}% for {tenure} months (EMI {emi}). "
                "Open the application to accept or decline it. The offer is valid until {valid_until}."},
    "offer_made.officer": {
        "event": "offer_made", "role": "officer", "priority": "medium",
        "subject": "Head approved {application_no}",
        "body": "{actor} approved {application_no}; an offer of {amount} was sent to the borrower."},
    "offer_accepted.officer": {
        "event": "offer_accepted", "role": "officer", "priority": "high",
        "subject": "Offer accepted: {application_no}",
        "body": "{applicant} accepted the offer of {amount} for {application_no}. Next step: sanction the loan."},
    "offer_declined.officer": {
        "event": "offer_declined", "role": "officer", "priority": "medium",
        "subject": "Offer declined: {application_no}",
        "body": "{applicant} declined the offer for {application_no}. The application is closed."},
    "application_rejected.borrower": {
        "event": "application_rejected", "role": "user", "priority": "high",
        "subject": "Update on your application {application_no}",
        "body": "We are unable to approve your {product} application {application_no}. Reason: {remarks}"},
    "loan_sanctioned.borrower": {
        "event": "loan_sanctioned", "role": "user", "priority": "medium",
        "subject": "Loan sanctioned: {application_no}",
        "body": "Your loan of {amount} for {application_no} is sanctioned. Disbursement is the next step."},
    "loan_disbursed.borrower": {
        "event": "loan_disbursed", "role": "user", "priority": "high",
        "subject": "Loan disbursed: {application_no}",
        "body": "{amount} was disbursed to your account for {application_no} (reference {reference})."},
}


def _inr(v) -> str:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    s = f"{int(round(n)):d}"
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = re.sub(r"(\d)(?=(\d{2})+$)", r"\1,", head)
        s = f"{head},{tail}"
    return f"Rs {s}"


def variables(template: dict) -> list[str]:
    return sorted({f for _, f, _, _ in string.Formatter().parse(template["subject"] + template["body"]) if f})


def render(event: str, context: dict) -> list[dict]:
    """Drafts for every template of `event`. A template whose variables are not
    all known is skipped (never sent half-filled) and reported as such."""
    out = []
    for name, t in TEMPLATES.items():
        if t["event"] != event:
            continue
        if t["role"] == "head" and event == "underwriting_completed" and not context.get("requires_head"):
            continue
        if t["role"] == "head" and event == "assessment_referred" and context.get("fraud_tier") != "HIGH":
            continue
        ctx = dict(context)
        for k in ("amount", "emi"):
            if ctx.get(k) is not None:
                ctx[k] = _inr(ctx[k])
        missing = [v for v in variables(t) if ctx.get(v) in (None, "")]
        if missing:
            out.append({"template": name, "skipped": True, "missing": missing})
            continue
        out.append({"template": name, "role": t["role"], "channel": "in_app", "priority": t["priority"],
                    "title": t["subject"].format(**ctx)[:200], "body": t["body"].format(**ctx)})
    return out


def _recipients(cur, application_id) -> dict:
    """user_id per role for one application: its officer and head, and the borrower
    who submitted it (or whose customer record it is). A role with nobody identified
    is left out - a notification is never sent to an arbitrary borrower."""
    from ...lending import SESSION_SQL
    ids = {}
    if application_id is not None:
        r = cur.execute("""SELECT a.assigned_officer_id, a.assigned_head_id,
                                  JSON_VALUE(a.form_data, '$.submitted_by.user_id'),
                                  JSON_VALUE(a.form_data, '$.submitted_by.role'), c.email
                           FROM core.loan_applications a
                           JOIN core.customers c ON c.customer_id = a.customer_id
                           WHERE a.application_id = ?""", (application_id,)).fetchone()
        if r:
            ids["officer"], ids["head"] = r[0], r[1]
            u = None
            if r[3] == "user" and r[2]:
                u = cur.execute("SELECT user_id FROM ops.users WHERE CAST(user_id AS NVARCHAR(40)) = ? AND is_active = 1",
                                (str(r[2]),)).fetchone()
            if not u and r[4]:
                u = cur.execute("SELECT user_id FROM ops.users WHERE email = ? AND role_code = 'user' AND is_active = 1",
                                (r[4],)).fetchone()
            ids["user"] = u[0] if u else None
    for role in ("officer", "head"):
        if not ids.get(role):
            r = cur.execute(SESSION_SQL, (role,)).fetchone()
            ids[role] = r[0] if r else None
    return ids


def _ensure_templates(cur) -> dict:
    ids = {}
    for name, t in TEMPLATES.items():
        r = cur.execute("SELECT template_id FROM ops.notification_templates WHERE template_name = ?",
                        (name,)).fetchone()
        if not r:
            tid = str(uuid.uuid4())      # minted here: no OUTPUT INSERTED on tables with triggers
            cur.execute("""INSERT INTO ops.notification_templates (template_id, template_name, channel,
                               subject_template, body_template, variables) VALUES (?,?,?,?,?,?)""",
                        (tid, name, "in_app", t["subject"], t["body"], json.dumps(variables(t))))
            r = (tid,)
        ids[name] = r[0]
    return ids


def insert(cur, application_id, drafts: list[dict]) -> int:
    """Insert rendered drafts for one application on an open cursor (the caller's
    transaction). Returns how many rows were written."""
    ready = [d for d in drafts if not d.get("skipped")]
    if not ready:
        return 0
    tmpl = _ensure_templates(cur)
    who = _recipients(cur, application_id)
    n = 0
    for d in ready:
        uid = who.get(d["role"])
        if uid is None:
            d["unwritten"] = f"no {d['role']} identified for this application"
            continue
        cur.execute("""INSERT INTO ops.notifications (user_id, title, body, channel, priority, reference_type,
                           reference_id, application_id, template_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (uid, d["title"], d["body"], "in_app", d["priority"], "loan_application",
                     application_id, application_id, tmpl[d["template"]]))
        n += 1
    return n


def write(drafts: list[dict], application_no: str | None) -> dict:
    """Insert the drafts into ops.notifications (in-app). Only for applications
    that exist in SQL Server; otherwise the drafts are returned unwritten."""
    from ...config import settings
    ready = [d for d in drafts if not d.get("skipped")]
    if not ready:
        return {"written": 0, "reason": "nothing to write"}
    if not (settings.db_enabled and application_no):
        return {"written": 0, "reason": "no SQL application to attach to"}
    import pyodbc
    with pyodbc.connect(settings.db_connection_string, timeout=5) as conn:
        cur = conn.cursor()
        row = cur.execute("SELECT application_id FROM core.loan_applications WHERE application_no = ?",
                          (application_no,)).fetchone()
        if not row:
            return {"written": 0, "reason": "application not in SQL Server"}
        n = insert(cur, row[0], ready)
        conn.commit()
    return {"written": n}
