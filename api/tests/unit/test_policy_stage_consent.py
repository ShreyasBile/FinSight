"""Three fixes found by the real-data evaluation (2026-10-01):

* a junk rule typed into the form ("on") must be refused, not go live;
* the loan-terms rules (KFS, cooling-off, fund flow, penal charges) apply from the OFFER stage, and the
  borrower's recorded consent reaches the policy check — so a clean application can pass at assessment
  and the supervisor can recommend approval;
* dedupe searches every other applicant with its own strict face threshold, not the 1:1 one.
"""
import sqlite3

import numpy as np
import pytest

from app.agents.impl.underwriting import baseline_recommendation
from app.agents.registry import get_agent
from app.agents.tools import dedupe, kfs, policy_context
from app.compliance import db as dbm
from app.compliance.regulatory_data import SEED_RULES
from app.compliance.rule_validation import rule_problems


# ── rule validation ─────────────────────────────────────────────────────────
JUNK = {"rule_id": "on", "rule_name": "on", "rule_type": "mandatory", "regulation": "",
        "condition": {"field": "customer.kyc_status", "op": "eq", "value": "complete"}}


def test_the_junk_rule_is_refused_and_every_seed_rule_is_accepted():
    assert len(rule_problems(JUNK)) >= 3
    for r in SEED_RULES:
        assert rule_problems(r) == [], r["rule_id"]


@pytest.mark.parametrize("change, hint", [
    ({"rule_id": "kyc 3"}, "Rule ID"), ({"rule_name": "KYC"}, "Rule name"), ({"rule_type": "blocker"}, "Severity"),
    ({"regulation": ""}, "cite"), ({"condition": {"field": "kyc_status", "op": "eq", "value": 1}}, "application field"),
    ({"condition": {"field": "customer.x", "op": "roughly", "value": 1}}, "operator"),
    ({"condition": {"all": []}}, "non-empty"),
])
def test_each_defect_is_named(change, hint):
    good = {"rule_id": "KYC_009", "rule_name": "PAN verified against NSDL", "rule_type": "mandatory",
            "regulation": "Master Direction - KYC, 2016", "condition": {"field": "customer.pan_available", "op": "eq", "value": True}}
    assert rule_problems(good) == []
    assert any(hint in p for p in rule_problems({**good, **change}))


def test_the_api_refuses_a_junk_rule(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from app.compliance.models import RuleIn
    from app.compliance.router import create_rule
    monkeypatch.setattr(dbm, "DB_PATH", str(tmp_path / "c.db"))
    conn = dbm.get_db()
    dbm.init_db(conn)
    with pytest.raises(HTTPException) as e:
        create_rule(RuleIn(**JUNK), conn)
    assert e.value.status_code == 400 and "cannot go live" in e.value.detail
    assert conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 0


# ── stage gating, consent, KFS ──────────────────────────────────────────────
@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(dbm, "DB_PATH", str(tmp_path / "compliance.db"))
    conn = dbm.get_db()
    dbm.init_db(conn)
    dbm.seed_if_empty(conn)
    conn.close()


def clean_case(**over):
    doc = lambda parsed: {"parsed": parsed, "identity_ok": True, "validation": {"flags": {}},  # noqa: E731
                          "tampering": {"risk_tier": "LOW", "risk_score": 0.08}}
    case = {"case_id": "c1", "application_no": None, "loan": {"loan_type": "two_wheeler_loan"},
            "documents": {"pan": doc({"pan_number": "ABCPE1234F"}), "aadhaar": doc({"aadhaar_number": "XXXX-XXXX-1234"})},
            "face": {"overall": "match", "pairs": [{"a": "pan", "b": "aadhaar", "similarity": 0.61, "verdict": "match"}]},
            "consent": {"data_collection": True, "credit_bureau_pull": True, "accepted_at": "2026-10-01T10:00:00Z"}}
    case.update(over)
    return case


EXTRA = {"credit_score": 760, "monthly_income": 60000, "existing_emi": 0, "proposed_emi": 6000}


def status(case, **extra):
    res = policy_context.evaluate(policy_context.build_context(case, {**EXTRA, **extra}))
    return res["overall_status"], {c["rule_id"]: c["status"] for c in res["checks"]}


def test_a_clean_application_passes_at_assessment(seeded):
    overall, st = status(clean_case())
    assert overall == "PASS", st
    assert all(st[r] == "NOT_APPLICABLE" for r in ("DL_KFS_001", "DL_KFS_002", "DL_COOL_001", "DL_DISB_001", "PENAL_001"))
    assert st["DL_CONSENT_001"] == "PASS"


def test_missing_consent_still_needs_information(seeded):
    overall, st = status(clean_case(consent=None))
    assert overall == "NEEDS_INFO" and st["DL_CONSENT_001"] == "INCONCLUSIVE"
    overall, st = status(clean_case(consent={"data_collection": True, "credit_bureau_pull": False}))
    assert overall == "FAIL" and st["DL_CONSENT_001"] == "FAIL"


def test_offer_stage_is_judged_on_the_kfs_on_file(seeded):
    # an offer stage reached without a Key Facts Statement is a violation, not a pass
    overall, st = status(clean_case(), stage="offer")
    assert overall == "FAIL" and st["DL_KFS_001"] == "FAIL"
    offer = get_agent("offer_generation").execute(
        {"case": clean_case(), "risk": {"overall": 30}, "income": {"consensus_monthly": 60000},
         "extra": {"loan_amount": 90000, "tenure_months": 24}}).payload
    assert offer["kfs"]["apr"] > offer["interest_rate"] and offer["kfs"]["fees"][0]["amount"] == offer["processing_fee"]
    overall, st = status(clean_case(offer=offer), stage="offer")
    assert overall == "PASS", st
    assert all(st[r] == "PASS" for r in ("DL_KFS_001", "DL_KFS_002", "DL_COOL_001", "DL_DISB_001", "PENAL_001"))


def test_apr_is_the_all_in_rate():
    emi = 90000 * 0.01 * 1.01 ** 24 / (1.01 ** 24 - 1)          # 12% a year
    assert abs(kfs.apr(90000, 0, emi, 24) - 12.0) < 0.01          # no fee -> APR equals the rate
    assert kfs.apr(90000, 900, emi, 24) > 12.5                    # a 1% fee raises it
    assert kfs.apr(0, 0, 0, 0) is None
    assert kfs.stage_of("underReview") == "assessment" and kfs.stage_of("offerGenerated") == "offer"


def test_the_supervisor_can_now_recommend_approval(seeded):
    overall, _ = status(clean_case())
    ev = {"policy_status": overall, "fraud": {"tier": "LOW"}, "risk": {"engine_suggestion": "approve_recommended"},
          "documents": {"pan": {"identity_ok": True}}, "income": {"mismatches": []}, "cross_checks": [],
          "face_match": "match", "loan_amount": 90000}
    assert baseline_recommendation(ev)["recommendation"] == "approve_recommended"


def test_startup_stage_gates_an_older_database(tmp_path, monkeypatch):
    from app.compliance.router import _stage_gate_offer_rules
    monkeypatch.setattr(dbm, "DB_PATH", str(tmp_path / "old.db"))
    conn = dbm.get_db()
    dbm.init_db(conn)
    dbm.seed_if_empty(conn)
    conn.execute("UPDATE rules SET applies_when_json=NULL WHERE rule_id LIKE 'DL_%' OR rule_id='PENAL_001'")
    conn.commit()
    _stage_gate_offer_rules(conn)
    gated = [r[0] for r in conn.execute("SELECT rule_id FROM rules WHERE applies_when_json LIKE '%application.stage%'")]
    assert sorted(gated) == ["DL_COOL_001", "DL_DISB_001", "DL_KFS_001", "DL_KFS_002", "PENAL_001"]
    assert conn.execute("SELECT applies_when_json FROM rules WHERE rule_id='DL_CONSENT_001'").fetchone()[0] is None
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='rule_corrected'").fetchone()[0] == 1
    _stage_gate_offer_rules(conn)                                 # idempotent
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='rule_corrected'").fetchone()[0] == 1


# ── dedupe: 1:N face threshold ──────────────────────────────────────────────
def _vec(cos: float, dim: int = 128):
    """A unit vector with the given cosine to e0."""
    v = np.zeros(dim, dtype=np.float32)
    v[0], v[1] = cos, (1 - cos * cos) ** 0.5
    return v.tolist()


def _case(pan):
    return {"documents": {"pan": {"sha256": f"sha-{pan}", "parsed": {"pan_number": pan}}}}


def test_a_lookalike_is_not_a_duplicate_but_the_same_face_is():
    assert dedupe.FACE_MATCH_T < dedupe.FACE_DEDUPE_T
    dedupe.reset_index()
    dedupe.stage_faces("A", {"pan": [_vec(1.0)]})
    dedupe.check("A", _case("ABCPE1111A"))
    # 0.42 passes the 1:1 threshold (0.363) but is a common score between DIFFERENT people in a large index
    dedupe.stage_faces("B", {"pan": [_vec(0.42)]})
    assert dedupe.check("B", _case("ABCPE2222B"), register=False)["hits"] == []
    dedupe.stage_faces("C", {"pan": [_vec(0.72)]})
    hit = dedupe.check("C", _case("ABCPE3333C"), register=False)
    assert hit["floor"] and hit["hits"][0]["kind"] == "same_face_different_id"
    dedupe.reset_index()
