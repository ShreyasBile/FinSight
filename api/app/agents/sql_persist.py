"""Write a wizard-submitted application into SQL Server's core tables.

Before this, a borrower's submission only reached storage/<APP>/ and
applications.xlsx, so officer screens backed by SQL and every process-mining
view were blind to it. The Audit/Storage agent calls persist_application()
after the xlsx write succeeds; any failure here is reported, never raised.

Rows are written with environment_code = settings.agents_env ('prod': these are
the real applications). The applicant (core.customers) is always the person on the
documents - matched on PAN, or created from them - so every screen shows the right name.
Who SUBMITTED it is recorded separately in form_data.submitted_by: a borrower's "My
Applications" lists what they submitted, whoever it is for. An officer's submission goes to
that officer's queue; a borrower's or the head's to the default underwriting officer. Blank
contact details on an existing customer are filled from the form (never overwritten).
The full Aadhaar number is NOT stored: UIDAI's rules allow a lender to keep a
masked number only. The customer gets its last four digits (aadhaar_last4, see
docs/mssql/10-aadhaar-last4.sql) and the document's extracted fields keep
"XXXX XXXX 1234" instead of the number.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta

from ..config import settings
from ..db import set_change_context

# wizard product label / id -> ref.loan_product_type.product_code
PRODUCT_CODES = {
    "two wheeler loan": "two_wheeler", "two_wheeler": "two_wheeler",
    "tractor loan": "tractor", "tractor": "tractor",
    "gold loan": "gold", "gold": "gold",
    "personal loan": "personal_loan", "personal": "personal_loan",
    "home loan": "home_loan", "home": "home_loan",
    "msme / business loan": "msme", "msme": "msme",
    "car loan": "car", "car": "car",
    "commercial vehicle loan": "commercial_vehicle", "commercial_vehicle": "commercial_vehicle",
    "loan against property": "lap", "lap": "lap",
    "microfinance / rural loan": "microfinance", "microfinance": "microfinance",
    "working capital loan": "working_capital", "working_capital": "working_capital",
    "used vehicle loan": "used_vehicle", "used_vehicle": "used_vehicle",
}

# FinSight doc types -> ref.document_type codes
DOC_CODES = {"gst_return": "gst_certificate", "gst_return_3b": "gst_certificate", "form16": "itr", "cibil": "other",
             "rc_book": "vehicle_rc", "existing_rc": "vehicle_rc", "insurance": "insurance_certificate",
             "property_doc": "property_document", "title_deed": "property_document",
             "gold_purity": "valuation_report"}
_KNOWN_DOCS = {"aadhaar", "bank_statement", "business_proof", "driving_license", "gst_certificate",
               "insurance_certificate", "itr", "other", "pan", "passport", "photograph",
               "property_document", "salary_slip", "valuation_report", "vehicle_rc", "voter_id"}


def product_code(loan_type: str | None) -> str:
    return PRODUCT_CODES.get((loan_type or "").strip().lower(), "personal_loan")


def doc_code(doc_type: str) -> str:
    c = DOC_CODES.get(doc_type, doc_type)
    return c if c in _KNOWN_DOCS else "other"


def _fields(doc: dict) -> dict:
    out = {}
    for f in doc.get("fields") or []:
        if isinstance(f, dict) and f.get("key"):
            out[f["key"]] = f.get("value")
    if isinstance(doc.get("fields"), dict):
        out.update(doc["fields"])
    return out


_AADHAAR_KEYS = ("aadhaar", "aadhar")


def _aadhaar_last4(value) -> str | None:
    """Last four digits of an Aadhaar number, full ("1234 5678 9012") or already
    masked ("XXXX XXXX 9012"); None if it is neither."""
    s = str(value or "")
    digits = re.sub(r"\D", "", s)
    if len(digits) == 12 or (len(digits) == 4 and "X" in s.upper()):
        return digits[-4:]
    return None


def _mask_ids(fields: dict) -> dict:
    """The extracted fields as they may be stored: any Aadhaar number masked to its last 4."""
    out = dict(fields)
    for k, v in fields.items():
        if any(t in str(k).lower() for t in _AADHAAR_KEYS) and isinstance(v, (str, int)):
            last4 = _aadhaar_last4(v)
            if last4 or len(re.sub(r"\D", "", str(v))) >= 8:
                out[k] = f"XXXX XXXX {last4}" if last4 else "XXXX XXXX XXXX"
    return out


def _dob(value) -> str | None:
    from ..docrules import parse_date_any
    return parse_date_any(str(value or ""))       # 'YYYY-MM-DD' or None


def applicant_from(record: dict) -> dict:
    """Name, DOB, PAN, address from the documents the borrower uploaded."""
    by = {d.get("doc_type"): _fields(d) for d in record.get("documents") or []}
    pan, aad = by.get("pan", {}), by.get("aadhaar", {})
    name = (record.get("applicant_name") or pan.get("full_name") or aad.get("full_name") or "").strip()
    parts = name.split()
    pan_no = re.sub(r"[^A-Z0-9]", "", str(pan.get("pan_number") or "").upper())
    return {"first_name": (parts[0] if parts else "Unknown")[:100],
            "last_name": (" ".join(parts[1:]) if len(parts) > 1 else "-")[:100],
            "dob": _dob(pan.get("dob") or aad.get("dob")) or "1900-01-01",
            "pan": pan_no if re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", pan_no) else None,
            "address": (aad.get("address") or "")[:255] or None,
            "pincode": _pin(aad.get("pincode")),
            "aadhaar_last4": _aadhaar_last4(aad.get("aadhaar_number"))}


SLA_HOURS = 48          # default time an officer has to act on a new application


def _num(v):
    try:
        return float(str(v).replace(",", "")) if v not in (None, "") else None
    except ValueError:
        return None


def _pin(v) -> str | None:
    """A 6-digit Indian PIN code, or None - never a fragment such as "6" from "6-digit"."""
    d = re.sub(r"\D", "", str(v or ""))
    return d if re.fullmatch(r"[1-9]\d{5}", d) else None


def _mobile(v) -> str:
    d = re.sub(r"\D", "", str(v or ""))
    return d[-10:] if len(d) >= 10 else "not captured"


def _owners(cur, actor: dict | None):
    """(officer_id, officer branch) whose queue the application goes to."""
    from ..lending import SESSION_SQL
    role, actor_id = (actor or {}).get("role"), (actor or {}).get("id")
    officer = None
    if role == "officer" and actor_id:
        officer = cur.execute("SELECT user_id, branch_code FROM ops.users WHERE user_id = ? "
                              "AND role_code = 'officer' AND is_active = 1", (actor_id,)).fetchone()
    if officer is None:                                  # borrower / head / unknown: the default officer
        row = cur.execute(SESSION_SQL, ("officer",)).fetchone()
        officer = (row[0], row[4]) if row else None
    return (str(officer[0]) if officer else None), (officer[1] if officer else None)


def _user_id(cur, user_id) -> str | None:
    """user_id if it names an active ops.users row (the audit trail's FK), else None."""
    if not user_id:
        return None
    row = cur.execute("SELECT user_id FROM ops.users WHERE user_id = TRY_CAST(? AS UNIQUEIDENTIFIER) "
                      "AND is_active = 1", (str(user_id),)).fetchone()
    return str(row[0]) if row else None


def persist_application(record: dict, case_id: str | None = None, actor: dict | None = None) -> dict:
    """Insert (or find) customer + application + one row per document.
    Idempotent on application_no. Returns {ok, application_no, application_id,
    documents, created} or {ok: False, error}."""
    if not settings.db_enabled:
        return {"ok": False, "error": "SQL Server disabled (DB_ENABLED=false)"}
    import pyodbc
    app_no = str(record.get("app_id") or "").strip()
    if not app_no:
        return {"ok": False, "error": "record has no app_id"}
    env = settings.agents_env
    a = applicant_from(record)
    loan, appl = record.get("loan") or {}, record.get("applicant") or {}
    with pyodbc.connect(settings.db_connection_string, timeout=5) as conn:
        cur = conn.cursor()
        row = cur.execute("SELECT application_id FROM core.loan_applications WHERE application_no = ?",
                          (app_no,)).fetchone()
        if row:
            return {"ok": True, "application_no": app_no, "application_id": str(row[0]), "created": False,
                    "documents": cur.execute("SELECT COUNT(*) FROM core.uploaded_documents WHERE application_id = ?",
                                             (row[0],)).fetchone()[0]}
        officer_id, branch = _owners(cur, actor)
        monthly = _num(loan.get("monthly_income"))
        cust = None
        if a["pan"]:
            cust = cur.execute("SELECT customer_id FROM core.customers WHERE pan = ?", (a["pan"],)).fetchone()
        if cust:
            customer_id = cust[0]
            # a returning applicant: fill in whatever the record is missing, never overwrite
            cur.execute(
                """UPDATE core.customers SET application_count = application_count + 1, updated_at = SYSUTCDATETIME(),
                       mobile = CASE WHEN mobile IS NULL OR mobile IN ('', 'not captured') THEN ? ELSE mobile END,
                       email = COALESCE(NULLIF(email, ''), ?), city = COALESCE(NULLIF(city, ''), ?),
                       state = COALESCE(NULLIF(state, ''), ?), pincode = CASE WHEN pincode LIKE '[1-9][0-9][0-9][0-9][0-9][0-9]' THEN pincode ELSE COALESCE(?, pincode) END,
                       occupation = COALESCE(NULLIF(occupation, ''), ?), annual_income = COALESCE(annual_income, ?),
                       aadhaar_last4 = COALESCE(aadhaar_last4, ?)
                   WHERE customer_id = ?""",
                (_mobile(appl.get("mobile")), (str(appl.get("email") or "").strip()[:255] or None),
                 (str(appl.get("city") or "").strip()[:100] or None), (str(appl.get("state") or "").strip()[:100] or None),
                 _pin(appl.get("pincode")),
                 (str(loan.get("employment_type") or "").strip()[:100] or None),
                 monthly * 12 if monthly else None, a["aadhaar_last4"], customer_id))
        else:
            # ids are minted here: core tables carry triggers, and SQL Server
            # refuses a bare OUTPUT INSERTED on a table with enabled triggers
            customer_id = str(uuid.uuid4())
            cur.execute(
                """INSERT INTO core.customers (customer_id, first_name, last_name, date_of_birth, mobile, email, pan,
                       address_line1, city, state, pincode, occupation, annual_income,
                       first_application_at, application_count, environment_code, aadhaar_last4)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,SYSUTCDATETIME(),1,?,?)""",
                (customer_id, a["first_name"], a["last_name"], _dob(appl.get("dob")) or a["dob"],
                 _mobile(appl.get("mobile")), (str(appl.get("email") or "").strip()[:255] or None), a["pan"],
                 a["address"], (str(appl.get("city") or "").strip()[:100] or None),
                 (str(appl.get("state") or "").strip()[:100] or None),
                 (_pin(appl.get("pincode")) or a["pincode"]),
                 (str(loan.get("employment_type") or "").strip()[:100] or None),
                 monthly * 12 if monthly else None, env, a["aadhaar_last4"]))
        submitted = (record.get("submitted_at") or datetime.now().isoformat(timespec="seconds"))[:19].replace("T", " ")
        try:
            sla_due = (datetime.fromisoformat(submitted) + timedelta(hours=SLA_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            sla_due = None
        form = {"case_id": case_id, "loan_type": record.get("loan_type"), "loan": loan, "applicant": appl,
                "submitted_by": {"user_id": (actor or {}).get("id"), "role": (actor or {}).get("role")},
                "answers": record.get("form") or {}, "consent": record.get("consent") or None,
                "face_check": {k: (record.get("face_check") or {}).get(k) for k in ("overall", "summary")}}
        application_id = str(uuid.uuid4())
        # the trigger's "Application created" history row names whoever submitted it
        submitter = _user_id(cur, (actor or {}).get("id"))
        set_change_context(cur, submitter, source_system="wizard")
        cur.execute(
            """INSERT INTO core.loan_applications (application_id, application_no, customer_id, loan_product,
                   status_code, loan_amount, tenure_months, purpose, form_data, assigned_officer_id, current_owner_id,
                   branch_code, sla_due_at, applied_at, submitted_at, channel_code, environment_code)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (application_id, app_no, customer_id, product_code(record.get("loan_type")), "submitted",
             _num(loan.get("amount")), int(_num(loan.get("tenure_months")) or 0) or None,
             (str(loan.get("purpose") or "").strip()[:255] or None), json.dumps(form, default=str),
             officer_id, officer_id, branch, sla_due, submitted, submitted, "web", env))
        n_docs = 0
        for d in record.get("documents") or []:
            dt = d.get("doc_type") or "other"
            t = d.get("timings") or {}
            tam = d.get("tampering") or {}
            tier = tam.get("risk_tier") if tam.get("risk_tier") in ("LOW", "MEDIUM", "HIGH") else None
            name = str(d.get("file_name") or d.get("label") or dt)[:255]
            raw = d.get("original_b64") or ""
            sha = hashlib.sha256(raw.encode()).hexdigest() if raw else None
            fields = _mask_ids(_fields(d))
            cur.execute(
                """INSERT INTO core.uploaded_documents (application_id, document_type, file_name, file_path,
                       mime_type, checksum_sha256, ocr_status, verification_status, extracted_data,
                       preprocess_ms, extract_ms, tampering_ms, face_ms, mask_ms, needs_review,
                       tampering_risk_tier, was_preprocessed, provider_used, environment_code, uploaded_by)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (application_id, doc_code(dt), name, f"storage/{app_no}/{dt}_original",
                 d.get("mime"), sha, "verified" if fields else "processing", "pending",
                 json.dumps(fields, default=str) if fields else None,
                 _ms(t.get("preprocess_ms")), _ms(t.get("extract_ms")), _ms(t.get("tampering_ms")),
                 _ms(t.get("face_ms")), _ms(t.get("mask_ms")), 1 if d.get("needs_review") else 0,
                 tier, 1 if t.get("preprocess_ms") else 0, (d.get("provider") or None), env, submitter))
            n_docs += 1
        # The agents read, tamper-checked and face-matched every document while it was uploaded,
        # so the case enters the officer's queue as "Documents Processing" (an automated step).
        set_change_context(cur, None, "documents_processed",
                           "Documents processed by the AI agents at upload", "officer_queue")
        cur.execute("""UPDATE core.loan_applications SET status_code = 'documentsProcessing',
                           current_wait_reason = 'officer_queue', queue_entered_at = SYSUTCDATETIME(),
                           current_owner_since = SYSUTCDATETIME()
                       WHERE application_id = ?""", (application_id,))
        set_change_context(cur)
        try:
            from .tools import notifier
            ctx = {"application_no": app_no, "product": record.get("loan_type") or "loan",
                   "applicant": " ".join(x for x in (a.get("first_name"), a.get("last_name")) if x) or "The applicant"}
            notifier.insert(cur, application_id, notifier.render("application_submitted", ctx))
        except Exception:  # noqa: BLE001 - a notification must not lose the application
            pass
        conn.commit()
    return {"ok": True, "application_no": app_no, "application_id": str(application_id),
            "documents": n_docs, "created": True}


def _ms(v):
    try:
        return int(round(float(v))) if v is not None else None
    except (TypeError, ValueError):
        return None
