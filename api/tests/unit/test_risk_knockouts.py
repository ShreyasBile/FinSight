"""FinSight's rules on top of the vendored risk engine: fabricated documents, a HIGH
fraud tier or a missing bureau score must never come out as "approve"; and when the
income sources disagree, underwriting uses the evidenced (lowest) figure."""
from app.agents.impl.underwriting import finsight_overrides
from app.agents.registry import get_agent
from app.agents.tools.income import triangulate


def doc(tier=None, score=0.0, parsed=None):
    return {"parsed": parsed or {}, "identity_ok": True,
            "tampering": {"risk_tier": tier, "risk_score": score} if tier else {}}


def clean_case(**docs):
    base = {"pan": doc("LOW", 0.1), "aadhaar": doc("LOW", 0.1),
            "salary_slip": doc("LOW", 0.1, {"net_salary": "60000", "gross_salary": "70000"})}
    base.update(docs)
    return {"application_no": "APP-T", "loan": {"loan_type": "Two Wheeler Loan"}, "documents": base}


EXTRA = {"loan_amount": 150000, "tenure_months": 36, "monthly_income": 60000}


def test_no_knockouts_for_clean_documents_with_a_bureau_score():
    ko, cond = finsight_overrides(clean_case(), {"cibil_score": 760})
    assert ko == [] and cond == []


def test_critical_tampering_and_high_fraud_raise_the_score():
    case = clean_case(bank_statement=doc("HIGH", 0.83))
    ko, _ = finsight_overrides(case, {"cibil_score": 760})
    assert ko and ko[0]["adjusted_score"] == 85
    one_moderate, _ = finsight_overrides(clean_case(bank_statement=doc("HIGH", 0.6)), {"cibil_score": 760})
    assert one_moderate[0]["adjusted_score"] == 65
    fraud, _ = finsight_overrides({**clean_case(), "assessment": {"fraud": {"tier": "HIGH"}}}, {"cibil_score": 760})
    assert "fraud risk HIGH" in fraud[0]["reason"]


def test_risk_agent_never_approves_fabricated_documents():
    case = clean_case(salary_slip=doc("HIGH", 0.68, {"net_salary": "1500000"}), bank_statement=doc("HIGH", 0.83))
    r = get_agent("risk_scoring").execute({"case": case, "income": None,
                                           "extra": {**EXTRA, "credit_score": 780}}).payload
    assert r["overall"] >= 85 and r["grade"] == "D"
    assert r["decision_advisory"] == "decline_review_recommended"
    assert r["engine"]["overall"] < r["overall"]                  # the engine's own score is kept for reference


def test_no_bureau_score_turns_an_approval_into_a_referral():
    r = get_agent("risk_scoring").execute({"case": clean_case(), "income": None, "extra": EXTRA}).payload
    assert r["decision_advisory"] != "approve_recommended"
    assert any("CIBIL" in c for c in r["conditions"])
    with_cibil = get_agent("risk_scoring").execute({"case": clean_case(), "income": None,
                                                    "extra": {**EXTRA, "credit_score": 780}}).payload
    # clean + scored: no knock-out raises it — only the real-loan model's (bounded) adjustment applies
    rl = with_cibil.get("real_loan_model") or {}
    expected = max(0.0, min(100.0, with_cibil["engine"]["overall"] + (rl.get("points") or 0)))
    assert abs(with_cibil["overall"] - expected) < 0.01


def test_real_loan_model_is_bounded_monotone_and_two_wheeler_only():
    from app.agents.tools import pd_model
    app = {"loan_amount": 60000.0, "collateral": {"value": 80000.0}}
    cust = {"employment_type": "salaried", "existing_emis": 0.0}
    base = pd_model.score("TWO_WHEELER", app, cust, {"cibil_score": 720.0, "default_count": 0.0}, 30)
    assert base is not None and abs(base["points"]) <= pd_model.MAX_POINTS
    worse_ltv = pd_model.score("TWO_WHEELER", {**app, "collateral": {"value": 64000.0}}, cust,
                               {"cibil_score": 720.0, "default_count": 0.0}, 30)
    worse_cibil = pd_model.score("TWO_WHEELER", app, cust, {"cibil_score": 560.0, "default_count": 0.0}, 30)
    assert worse_ltv["pd"] >= base["pd"] and worse_cibil["pd"] >= base["pd"]
    assert pd_model.score("PERSONAL", app, cust, {}, 30) is None


def test_income_uses_the_evidenced_figure_when_sources_disagree():
    case = {"documents": {"salary_slip": doc(parsed={"net_salary": "1500000"}),
                          "bank_statement": doc(parsed={"total_credits": "10000",
                                                        "statement_period": "01-04-2026 to 30-06-2026"})}}
    r = triangulate(case)
    assert r["mismatches"] and r["declared_monthly"] == 1500000
    assert r["consensus_monthly"] < 5000 and r["consensus_basis"].startswith("lowest")
    agree = triangulate({"documents": {"salary_slip": doc(parsed={"net_salary": "60000"})}})
    assert agree["consensus_monthly"] == 60000 and not agree["mismatches"]
