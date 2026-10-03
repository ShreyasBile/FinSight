"""Build the RBI rules engine's input from REAL evidence.

The three UI panels that called /compliance/policy/check used to send a
hand-built payload with hardcoded values (kyc_status "complete",
ovd_verified true, every consent true, tamper_score 0). This builder derives
each field from what the agents actually established, and leaves a field OUT
when nothing established it — so the rules engine answers INCONCLUSIVE /
NEEDS_INFO instead of a false PASS.
"""
from __future__ import annotations

from ...docrules import validate_field


def _num(v):
    try:
        return float(str(v).replace(",", "")) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def build_context(case: dict, extra: dict | None = None, assessment: dict | None = None) -> dict:
    extra = extra or {}
    docs = case.get("documents") or {}
    customer: dict = {}
    pan_doc, aad_doc = docs.get("pan"), docs.get("aadhaar")

    def _doc_clean(d):
        if not d:
            return False
        flags = (d.get("validation") or {}).get("flags") or {}
        return d.get("identity_ok", True) and "error" not in flags.values()

    if pan_doc or aad_doc:
        customer["kyc_status"] = "complete" if (_doc_clean(pan_doc) and _doc_clean(aad_doc)) else "incomplete"
        if aad_doc:
            customer["ovd_verified"] = _doc_clean(aad_doc)
    pan_val = (pan_doc or {}).get("parsed", {}).get("pan_number") if pan_doc else None
    if pan_doc is not None:
        customer["pan_available"] = bool(pan_val) and validate_field("pan_number", pan_val) == "ok"

    slip = (docs.get("salary_slip") or {}).get("parsed") or {}
    income = _num(slip.get("net_salary")) or _num(slip.get("gross_salary")) or _num(extra.get("monthly_income"))
    if income:
        customer["monthly_income"] = income
    cibil = _num(((docs.get("cibil") or {}).get("parsed") or {}).get("cibil_score")) or _num(extra.get("credit_score"))
    if cibil:
        customer["credit_score"] = cibil
    for k in ("existing_emi", "proposed_emi", "monthly_household_income"):
        if _num(extra.get(k)) is not None:
            customer[k] = _num(extra.get(k))
    # Consent: what the borrower ticked in the wizard's "Consent & Declaration" step, recorded with
    # the application (case file / form_data). Absent when nothing was recorded -> NEEDS_INFO.
    consent = extra["consent"] if isinstance(extra.get("consent"), dict) else case.get("consent")
    if isinstance(consent, dict) and consent:
        customer["consent"] = {k: bool(consent[k]) for k in ("data_collection", "credit_bureau_pull")
                               if consent.get(k) is not None}

    risk: dict = {}
    tampers = [d["tampering"]["risk_score"] for d in docs.values() if d.get("tampering")]
    if tampers:
        risk["tamper_score"] = round(max(tampers) * 100, 1)      # rules use a 0-100 scale
    if assessment:
        risk["fraud_score"] = round(assessment["fused_score"] * 100, 1)
    face = case.get("face") or {}
    sims = [p["similarity"] for p in face.get("pairs", [])]
    if sims:
        risk["face_match_score"] = round(min(sims), 3)          # SFace scale, rule threshold 0.363

    # Lifecycle stage: the KFS / cooling-off / disbursal / penal-charge rules describe LOAN TERMS, which
    # exist only once an offer has gone to the borrower. During credit assessment they do not apply
    # (rules' applies_when), so a clean application can pass; from the offer stage on they are judged
    # on the Key Facts Statement actually on file (tools/kfs.py), never assumed.
    from . import kfs
    stage = extra.get("stage") or kfs.stage_of(extra.get("status") or kfs.current_status(case))
    loan_terms = kfs.loan_terms_facts(case, stage)
    loan_terms.update({k: v for k, v in (extra.get("loan_terms") or {}).items() if v is not None})
    documents = {dt: {"present": True, "identity_ok": d.get("identity_ok"),
                      "tamper_tier": (d.get("tampering") or {}).get("risk_tier")} for dt, d in docs.items()}
    return {"application_id": case.get("application_no") or case["case_id"],
            "loan_type": extra.get("loan_type") or (case.get("loan") or {}).get("loan_type") or "personal_loan",
            "application": {"stage": stage},
            "customer": customer, "documents": documents, "loan_terms": loan_terms, "risk": risk}


def evaluate(context: dict) -> dict:
    from ...compliance import db as dbm
    from ...compliance.rules_engine import evaluate_application
    conn = dbm.get_db()
    try:
        rules = dbm.load_rules(conn, active_only=True)
    finally:
        conn.close()
    result = evaluate_application(rules, context)
    result["application_id"] = context["application_id"]
    result["loan_type"] = context["loan_type"]
    result["context_used"] = {"customer": sorted(context["customer"]), "risk": context["risk"],
                              "loan_terms": sorted(context["loan_terms"]),
                              "stage": (context.get("application") or {}).get("stage")}
    return result
