"""Evidence for applications that live in SQL Server (the officer queue).

Wizard applications already have a case file (their documents went through
the agents). Seeded/SQL applications have no documents in the case store, so
their case file is built from core.loan_applications + core.customers; the
agents then assess what exists and report the rest as unavailable — honestly,
rather than inventing evidence.
"""
from __future__ import annotations

from fastapi import HTTPException

from ... import db
from .. import case_store

_PRODUCT_LOAN_TYPE = {"personal_loan": "personal_loan", "microfinance": "microfinance_loan",
                      "home_loan": "home_loan", "lap": "loan_against_property", "gold": "gold_loan",
                      "msme": "msme_loan", "working_capital": "working_capital",
                      "two_wheeler": "two_wheeler_loan", "used_vehicle": "used_vehicle_loan",
                      "commercial_vehicle": "commercial_vehicle_loan", "tractor": "tractor_loan",
                      "education_loan": "education_loan"}


def _sql_application(application_no: str) -> dict | None:
    try:
        return db.query_one("""
            SELECT a.application_id, a.application_no, a.loan_product, a.loan_amount, a.tenure_months,
                   a.interest_rate, a.status_code, a.submitted_at, a.form_data,
                   c.first_name, c.last_name, c.date_of_birth, c.pan, c.aadhaar, c.occupation,
                   c.annual_income, c.credit_score, c.city, c.state, c.pincode
            FROM core.loan_applications a JOIN core.customers c ON c.customer_id = a.customer_id
            WHERE a.application_no = ?""", (application_no,))
    except db.DatabaseUnavailable as exc:
        raise HTTPException(503, f"Lending database unavailable: {exc}") from exc


def ensure_case(application_no: str) -> str:
    """Case id for an application: the wizard case if one is linked, else a
    case built from SQL (case id = core.loan_applications.application_id)."""
    cid = case_store.by_application(application_no)
    if cid and case_store.exists(cid):
        return cid
    app = _sql_application(application_no)
    if app is None:
        raise HTTPException(404, f"No application {application_no}")
    cid = str(app["application_id"]).lower()
    income = app.get("annual_income")
    applicant = {"name": f"{app.get('first_name') or ''} {app.get('last_name') or ''}".strip(),
                 "occupation": app.get("occupation"), "credit_score": app.get("credit_score"),
                 "monthly_income": round(income / 12, 2) if income else None,
                 "city": app.get("city"), "state": app.get("state")}
    loan = {"product_code": app["loan_product"],
            "loan_type": _PRODUCT_LOAN_TYPE.get(app["loan_product"], app["loan_product"]),
            "loan_amount": app.get("loan_amount"), "tenure_months": app.get("tenure_months"),
            "interest_rate": app.get("interest_rate"), "status_code": app.get("status_code")}

    consent = None
    try:
        import json
        consent = (json.loads(app["form_data"]) or {}).get("consent") if app.get("form_data") else None
    except (TypeError, ValueError):
        pass

    def _fill(case):
        case["source"] = "sql"
        if consent and not case.get("consent"):       # recorded at submission (form_data.consent)
            case["consent"] = consent
        case["applicant"].update({k: v for k, v in applicant.items() if v is not None})
        case["loan"].update({k: v for k, v in loan.items() if v is not None})
        # the KYC record on file acts as one identity "document" (no image, no
        # tampering/face evidence exists for seeded applications)
        case["documents"].setdefault("kyc_record", {
            "doc_type": "kyc_record", "source": "core.customers",
            "parsed": {"full_name": applicant["name"], "pan_number": app.get("pan"),
                       "aadhaar_number": app.get("aadhaar"),
                       "dob": str(app["date_of_birth"])[:10] if app.get("date_of_birth") else None}})
    case_store.update(cid, _fill)
    case_store.link_application(cid, application_no)
    return cid


def application_case(application_no: str) -> dict:
    cid = case_store.by_application(application_no)
    if not cid:
        raise HTTPException(404, "No agent results for this application yet")
    c = case_store.get(cid)
    return {"case_id": cid, "application_no": application_no, "source": c.get("source"),
            "documents": {dt: {k: d.get(k) for k in ("identity_ok", "validation", "tampering", "provider")}
                          for dt, d in c.get("documents", {}).items()},
            "face": c.get("face"), "liveness": c.get("liveness"), "assessment": c.get("assessment"),
            "underwriting": c.get("underwriting"), "memo": c.get("memo"), "offer": c.get("offer"),
            "workflow": c.get("workflow"), "notifications": c.get("notifications", [])}
