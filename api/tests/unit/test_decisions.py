"""Decision workflow rules (api/app/decisions.py) — who may do what, when. Pure
rule checks: the database is never touched (the one document count is stubbed)."""
import pytest
from fastapi import HTTPException

from app import decisions as d

OFFICER = {"user_id": "O-1", "full_name": "Priya Sharma", "role_code": "officer", "email": "priya@x"}
OTHER_OFFICER = {**OFFICER, "user_id": "O-2", "full_name": "Amit Kumar"}
HEAD = {"user_id": "H-1", "full_name": "Anil Verma", "role_code": "head", "email": "anil@x"}
BORROWER = {"user_id": "U-1", "full_name": "Rahul Verma", "role_code": "user", "email": "rahul@x"}
STRANGER = {**BORROWER, "user_id": "U-2", "email": "someone@x"}


def app(**kw):
    base = {"application_id": "A-1", "application_no": "APP-1", "status_code": "underReview",
            "status_label": "Under Review", "is_terminal": False, "loan_product": "two_wheeler",
            "product_name": "Two Wheeler Loan", "loan_amount": 168730, "tenure_months": 36, "interest_rate": None,
            "assigned_officer_id": "O-1", "assigned_head_id": None, "current_wait_reason": "officer_queue",
            "customer_id": "C-1", "submitted_by": "U-1", "submitted_role": "user", "case_id": None,
            "monthly_income": 50000, "customer_name": "Test Applicant", "customer_email": "applicant@x",
            "officer_name": "Priya Sharma", "head_name": None, "fraud_level": "low", "risk_score": 30,
            "base_interest_rate": 11.5, "processing_fee_pct": 1.0, "min_amount": 5000, "max_amount": 500000,
            "min_tenure_months": 6, "max_tenure_months": 60}
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def _one_document(monkeypatch):
    monkeypatch.setattr(d.db, "scalar", lambda *a, **k: 1)
    monkeypatch.setattr(d, "_agent_offer", lambda a: None)


def test_officer_approves_within_limit_when_fraud_is_low():
    assert d._why_not("approve", app(), OFFICER) is None


@pytest.mark.parametrize("kw,why", [
    ({"fraud_level": "unassessed"}, "Run the fraud & policy assessment first"),
    ({"fraud_level": "high"}, "only the Head can approve"),
    ({"loan_amount": 750000}, "Above ₹5 lakh"),
    ({"status_code": "underwritingReview"}, "Waiting for the Head"),
])
def test_officer_cannot_approve(kw, why):
    assert why in d._why_not("approve", app(**kw), OFFICER)


def test_head_may_approve_high_fraud_but_must_say_why():
    a = app(status_code="underwritingReview", fraud_level="high")
    assert d._why_not("approve", a, HEAD) is None
    assert d._needs_remarks("approve", a, HEAD)
    assert not d._needs_remarks("approve", app(), OFFICER)


def test_only_the_assigned_officer_acts():
    assert "Assigned to Priya Sharma" in d._why_not("reject", app(), OTHER_OFFICER)


def test_roles_and_statuses_gate_the_listing():
    assert d._why_not("send_back", app(), OFFICER) == "not_your_role"        # a Head action
    assert d._why_not("refer_to_head", app(), HEAD) == "not_your_role"       # an officer action
    assert d._why_not("accept_offer", app(), BORROWER) == "not_now"          # no offer yet
    assert d._why_not("reject", app(status_code="offerGenerated"), OFFICER) == "not_now"
    assert d._why_not("disburse", app(status_code="accepted"), OFFICER) == "not_now"


def test_borrower_actions_only_for_their_own_application():
    a = app(status_code="offerGenerated")
    assert d._why_not("accept_offer", a, BORROWER) is None
    assert d._why_not("decline_offer", a, BORROWER) is None
    assert "Only the borrower" in d._why_not("accept_offer", a, STRANGER)
    # the applicant's own login (matched on e-mail) counts too
    assert d._why_not("accept_offer", app(status_code="offerGenerated", submitted_by="O-1",
                                           customer_email="rahul@x"), BORROWER) is None


def test_large_loans_are_sanctioned_by_the_head():
    a = app(status_code="accepted", loan_amount=600000)
    assert "Head sanctions" in d._why_not("sanction", a, OFFICER)
    assert d._why_not("sanction", a, HEAD) is None


def test_verify_waits_for_requested_documents():
    a = app(status_code="documentsProcessing", current_wait_reason="awaiting_customer_doc")
    assert "Waiting for the borrower" in d._why_not("verify_documents", a, OFFICER)


def test_listing_hides_what_is_not_possible_and_explains_what_is_blocked():
    listing = d._listing(app(fraud_level="high"), OFFICER)
    names = {x["action"]: x for x in listing["actions"]}
    assert set(names) == {"approve", "refer_to_head", "request_documents", "reject"}
    assert names["approve"]["enabled"] is False and "Head" in names["approve"]["reason"]
    assert names["refer_to_head"]["remarks_required"] and names["reject"]["confirm"]
    assert listing["offer_draft"] is None                      # nothing enabled can send an offer


def test_offer_is_validated_against_the_product_and_the_request():
    a = app()
    ok = d._offer_from({"offer": {"amount": 150000, "rate": 12.5, "tenure_months": 36}}, a)
    assert ok["emi"] == d.emi(150000, 12.5, 36) and ok["processing_fee"] == 1500.0
    for bad in ({"amount": 0}, {"amount": 900000}, {"amount": 200000},     # zero, > product max, > requested
                {"rate": 40}, {"tenure_months": 72}):
        with pytest.raises(HTTPException):
            d._offer_from({"offer": bad}, a)


def test_emi_matches_the_standard_formula():
    assert d.emi(168730, 12.58, 36) == pytest.approx(5651, abs=1)
    assert d.emi(12000, 0, 12) == 1000.0
