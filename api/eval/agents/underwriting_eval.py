"""Underwriting crew evaluation: Income Analysis (20), Risk Scoring (21),
Credit Memo (22), Underwriting Supervisor (23), Offer Generation (24),
Workflow Router (25).

* Income — synthetic applicants whose documents agree, and the same
  applicants with one source altered (inflated payslip, under-declared ITR,
  salary credited below the payslip, Form 16 off). Ground-truth fields are fed
  directly (the extraction agents are measured separately).
* Risk — the sibling project's Indian NBFC loan book (50,000 loans with
  default labels). Its XGBoost was trained on a stratified 80/20 split with
  random_state=42; that exact split is reproduced and ONLY the held-out 20%
  is used, so the ML overlay is scored out of sample. The rule engine never
  saw the data. Monotonicity: worsening one input must never lower the score.
* Supervisor + Memo — 16 evidence bundles covering every branch of the
  underwriting policy, each run through the real Underwriting Crew (local
  model). The expected recommendation per bundle comes from the written
  policy, fixed before the run.
* Offer — EMI against an independent formula over a grid; FOIR cap honoured.
* Router — every status of all 12 product paths against diagram 7
  (docs/finsight-architecture.html), transcribed independently of
  app/agents/tools/workflow.py.
"""
from __future__ import annotations

import random

from eval import common as C


# ── Income ──────────────────────────────────────────────────────────────────
def _income_case(idt, *, slip_x=1.0, itr_x=1.0, bank_x=1.0, f16_x=1.0, noise=0.0):
    rng = random.Random(idt.uid)
    j = lambda v: v * (1 + rng.uniform(-noise, noise))  # noqa: E731
    net, gross = idt.net * slip_x, idt.gross * slip_x
    tx = [dict(t) for t in idt.transactions]
    for t in tx:
        if t["description"].startswith("SALARY"):
            t["credit"] = round(idt.net * bank_x, 2)
    return {"documents": {
        "salary_slip": {"parsed": {"net_salary": round(j(net)), "gross_salary": round(j(gross))}},
        "form16": {"parsed": {"gross_salary": round(j(idt.gross * 12 * f16_x))}},
        "itr": {"parsed": {"gross_total_income": round(j(idt.gross_total_income * itr_x))}},
        "bank_statement": {"parsed": {"statement_period": idt.statement_period or "01/04/2026 to 29/06/2026",
                                      "total_credits": round(sum(t["credit"] for t in tx), 2)},
                           "transactions": tx}}}


def eval_income(n: int = 20) -> dict:
    from app.agents.registry import get_agent
    from eval.datasets.synth.gen_docs import identities
    agent = get_agent("income_analysis")
    scen = [("consistent", {}, False), ("consistent_noise5", {"noise": 0.05}, False),
            ("payslip_inflated_40", {"slip_x": 1.4}, True), ("itr_underdeclared_50", {"itr_x": 0.5}, True),
            ("salary_credit_low_60", {"bank_x": 0.6}, True), ("form16_low_30", {"f16_x": 0.7}, True)]
    tp = fp = fn = tn = 0
    per = {}
    for idt in identities(n, seed=2020):
        for name, kw, expect in scen:
            res = agent.execute({"case": _income_case(idt, **kw)}).payload
            got = bool(res["mismatches"])
            tp += got and expect
            fp += got and not expect
            fn += (not got) and expect
            tn += (not got) and not expect
            s = per.setdefault(name, {"n": 0, "flagged": 0})
            s["n"] += 1
            s["flagged"] += got
    m = C.prf(tp, fp, fn)
    metrics = {"f1": m["f1"], "precision": m["precision"], "recall": m["recall"], "true_negatives": tn,
               "per_scenario_flag_rate": {k: round(v["flagged"] / v["n"], 4) for k, v in per.items()},
               "cases": sum(v["n"] for v in per.values())}
    return C.save("income_analysis", metrics)


# ── Risk ────────────────────────────────────────────────────────────────────
_PURPOSE = {"Home": "home_loan", "Auto": "car", "Personal": "personal_loan", "Education": "education_loan"}


def _risk_inputs(row) -> tuple[dict, dict]:
    case = {"loan": {"loan_type": _PURPOSE.get(row["loan_purpose"], "personal_loan")}, "documents": {}}
    extra = {"loan_amount": float(row["loan_amount"]), "tenure_months": int(row["loan_tenure_months"]),
             "monthly_income": float(row["income"]) / 12,
             "employment_type": "salaried" if row["employment_status"] == "Salaried" else "self_employed",
             "bureau": {"dpd_days": float(row["total_dpd"]), "default_count": float(row["delinquent_months"] > 0),
                        "delinquent_months": float(row["delinquent_months"]),
                        "total_accounts": float(row["number_of_open_accounts"] + row["number_of_closed_accounts"]),
                        "credit_history_years": float(row["total_loan_months"]) / 12,
                        "credit_utilization": float(row["credit_utilization_ratio"])}}
    return case, extra


def eval_risk(sample: int = 1500) -> dict:
    import numpy as np
    import pandas as pd
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import train_test_split
    from app.agents.registry import get_agent
    agent = get_agent("risk_scoring")
    src = C.API_DIR.parents[1] / "projects" / "risk analysis 2" / "model" / "indian_dataset" / "merged.csv"
    df = pd.read_csv(src)
    y = df["default"].astype(int).values
    _, test_idx = train_test_split(np.arange(len(df)), test_size=0.2, random_state=42, stratify=y)
    rng = np.random.default_rng(21)
    pick = rng.choice(test_idx, size=min(sample, len(test_idx)), replace=False)
    rule, ml, blend, labels, lat = [], [], [], [], []
    for i in pick:
        row = df.iloc[int(i)]
        case, extra = _risk_inputs(row)
        with C.Timer() as t:
            p = agent.execute({"case": case, "income": None, "extra": extra}).payload
        lat.append(t.ms)
        rule.append(p["overall"])
        ml.append((p.get("ml_overlay") or {}).get("model_default_probability"))
        blend.append((p.get("ml_overlay") or {}).get("defaultProbability"))
        labels.append(int(row["default"]))
    auc_rule = roc_auc_score(labels, rule)
    ok = [k for k, v in enumerate(ml) if v is not None]
    auc_ml = roc_auc_score([labels[k] for k in ok], [ml[k] for k in ok]) if ok else None
    okb = [k for k, v in enumerate(blend) if v is not None]
    auc_blend = roc_auc_score([labels[k] for k in okb], [blend[k] for k in okb]) if okb else None
    # diagnostic: the SAME model and loans, features built the way the sibling
    # project built them for training (indian_nbfc_test.build_features)
    from app.agents.vendor.risk_engine.ml_scorer import get_ml_scorer
    sc = get_ml_scorer("indian")
    tf = []
    for i in pick:
        r = df.iloc[int(i)]
        loan, inc = float(r["loan_amount"]), float(r["income"])
        tf.append({"LOAN": loan, "MORTDUE": 0.0, "VALUE": loan * 1.5, "YOJ": 5.0, "DEROG": 0.0,
                   "DELINQ": float(r["delinquent_months"]), "CLAGE": 60.0, "CLNO": 5.0,
                   "DEBTINC": loan / inc * 100 * 0.1 if inc > 0 else 35.0, "REASON": 1.0, "JOB": 1.0})
    p_train = sc.model.predict_proba(sc.scaler.transform(pd.DataFrame(tf)[sc.features]))[:, 1]
    auc_train_map = roc_auc_score(labels, p_train)
    # monotonicity: make ONE thing worse, the score must not go down
    viol, checks, examples = 0, 0, []
    worsen = [("dpd_days +90", lambda e: e["bureau"].update(dpd_days=e["bureau"]["dpd_days"] + 90)),
              ("defaults +2", lambda e: e["bureau"].update(default_count=e["bureau"]["default_count"] + 2)),
              ("income -40%", lambda e: e.update(monthly_income=e["monthly_income"] * 0.6)),
              ("amount +60%", lambda e: e.update(loan_amount=e["loan_amount"] * 1.6)),
              ("utilisation 95%", lambda e: e["bureau"].update(credit_utilization=95.0))]
    for i in pick[:150]:
        case, extra = _risk_inputs(df.iloc[int(i)])
        base = agent.execute({"case": case, "income": None, "extra": extra}).payload["overall"]
        for name, fn in worsen:
            import copy
            e2 = copy.deepcopy(extra)
            fn(e2)
            s = agent.execute({"case": case, "income": None, "extra": e2}).payload["overall"]
            checks += 1
            if s < base - 1e-6:
                viol += 1
                if len(examples) < 5:
                    examples.append({"change": name, "before": round(base, 2), "after": round(s, 2)})
    metrics = {"auc": round(auc_ml, 4) if auc_ml is not None else None, "auc_rule_engine": round(auc_rule, 4),
               "auc_ml_overlay": round(auc_ml, 4) if auc_ml is not None else None,
               "auc_ml_blended_score": round(auc_blend, 4) if auc_blend is not None else None,
               "auc_ml_training_feature_mapping": round(auc_train_map, 4),
               "monotonicity_violations": viol, "monotonicity_checks": checks,
               "held_out_sample": len(labels), "default_rate": round(float(np.mean(labels)), 4),
               "p50_ms": round(C.latency(lat)["p50_s"] * 1000, 1),
               "note": "No CIBIL score in this dataset: the rule engine's credit-history dimension is scored as "
                       "missing; the ML overlay is the model the sibling project trained on this data's other 80%."}
    return C.save("risk_scoring", metrics, monotonicity_examples=examples)


# ── Supervisor + memo (one crew run per bundle) ─────────────────────────────
def _bundle(**kw):
    ev = {"application": "APP-EVAL", "product": "personal_loan", "loan_amount": 300000, "tenure_months": 36,
          "documents": {"pan": {"identity_ok": True, "field_errors": [], "tamper_tier": "LOW", "tamper_score": 0.08},
                        "aadhaar": {"identity_ok": True, "field_errors": [], "tamper_tier": "LOW", "tamper_score": 0.05},
                        "salary_slip": {"identity_ok": True, "field_errors": [], "tamper_tier": "LOW", "tamper_score": 0.1}},
          "face_match": "match", "fraud": {"tier": "LOW", "score": 0.08, "floors": [], "driver": None},
          "policy_status": "PASS", "cross_checks": [],
          "income": {"monthly_estimate": 65820.0, "mismatches": []},
          "risk": {"score": 22.4, "grade": "A", "engine_suggestion": "approve_recommended",
                   "top_dimensions": ["financial_capacity", "credit_history", "loan_structure"]}}
    for k, v in kw.items():
        if k in ("fraud", "risk", "income"):
            ev[k] = {**ev[k], **v}
        elif k == "doc":
            ev["documents"]["salary_slip"].update(v)
        else:
            ev[k] = v
    return ev


SUPERVISOR_CASES = [
    ("clean", {}, "approve_recommended"),
    ("clean_large_amount", {"loan_amount": 1200000}, "approve_recommended"),
    ("clean_home", {"product": "home_loan", "loan_amount": 450000}, "approve_recommended"),
    ("fraud_high", {"fraud": {"tier": "HIGH", "score": 0.74, "floors": ["tamper_high"], "driver": "tampering"}},
     "decline_review_recommended"),
    ("risk_decline", {"risk": {"score": 78.0, "grade": "E", "engine_suggestion": "decline_review_recommended"}},
     "decline_review_recommended"),
    ("wrong_document", {"doc": {"identity_ok": False}}, "request_more_info"),
    ("policy_needs_info", {"policy_status": "NEEDS_INFO"}, "request_more_info"),
    ("fraud_medium", {"fraud": {"tier": "MEDIUM", "score": 0.34, "driver": "cross_check"}}, "refer_to_officer"),
    ("risk_refer", {"risk": {"score": 51.0, "grade": "C", "engine_suggestion": "refer_to_officer"}}, "refer_to_officer"),
    ("policy_fail", {"policy_status": "FAIL"}, "refer_to_officer"),
    ("policy_warn", {"policy_status": "PASS_WITH_WARNINGS"}, "refer_to_officer"),
    ("income_mismatch", {"income": {"mismatches": ["payslip net 65,820 vs salary credited 39,492"]}},
     "refer_to_officer"),
    ("cross_check", {"cross_checks": ["Name differs between PAN and payslip"]}, "refer_to_officer"),
    ("face_uncertain", {"face_match": "uncertain"}, "refer_to_officer"),
    ("face_mismatch", {"face_match": "mismatch"}, "refer_to_officer"),
    ("tamper_medium_only", {"doc": {"tamper_tier": "MEDIUM", "tamper_score": 0.31}}, "approve_recommended"),
]


def _run_uw(ev):
    """One Underwriting Crew run + the two deterministic guards (as in the flow)."""
    from app.agents.crews.underwriting import run_crew
    from app.agents.impl.underwriting import baseline_recommendation
    from app.agents.registry import get_agent
    base = baseline_recommendation(ev)
    rec_llm, memo_llm, warns = run_crew(ev, base)
    rec = get_agent("underwriting_supervisor").execute({"evidence": ev, "llm_recommendation": rec_llm}).payload
    memo = get_agent("credit_memo").execute({"evidence": ev, "recommendation": rec, "llm_memo": memo_llm}).payload
    return base, rec_llm, rec, memo_llm, memo, warns


_UW_CACHE: dict = {}


def _uw_runs():
    if not _UW_CACHE:
        for name, kw, expected in SUPERVISOR_CASES:
            ev = _bundle(**kw)
            with C.Timer() as t:
                out = _run_uw(ev)
            _UW_CACHE[name] = (ev, expected, out, t.ms)
    return _UW_CACHE


def eval_supervisor() -> dict:
    from app.agents.crews.underwriting import STRICTNESS
    runs = _uw_runs()
    agree = never_loosen = llm_answered = blocked = over_tight = 0
    rows = []
    for name, (ev, expected, (base, rec_llm, rec, _m, _memo, warns), ms) in runs.items():
        final = rec["recommendation"]
        agree += final == expected
        never_loosen += STRICTNESS[final] >= STRICTNESS[base["recommendation"]]
        llm_answered += rec_llm is not None
        blocked += bool(rec["loosening_blocked"])
        over_tight += STRICTNESS[final] > STRICTNESS[expected]
        rows.append({"case": name, "expected": expected, "baseline": base["recommendation"],
                     "llm": rec["llm_recommendation"], "final": final, "loosening_blocked": rec["loosening_blocked"],
                     "requires_head": rec["requires_head"], "s": round(ms / 1000, 1), "warnings": warns[:2]})
    n = len(runs)
    lat = C.latency([v[3] for v in runs.values()])
    metrics = {"agreement": round(agree / n, 4), "never_loosens": round(never_loosen / n, 4),
               "baseline_agreement": round(sum(r["baseline"] == r["expected"] for r in rows) / n, 4),
               "llm_answered": llm_answered, "loosening_attempts_blocked": blocked,
               "tightened_beyond_policy": over_tight, "cases": n,
               "crew_p50_s": lat["p50_s"], "crew_p95_s": lat["p95_s"]}
    return C.save("underwriting_supervisor", metrics, cases=rows)


def eval_memo() -> dict:
    from app.agents.base import grounded
    from app.agents.crews.underwriting import MEMO_SECTIONS
    runs = _uw_runs()
    memos_llm = fully_grounded_llm = complete = sections_total = sections_ok = 0
    head_condition_ok = head_cases = final_grounded = 0
    rows = []
    for name, (ev, _exp, (_b, _r, rec, memo_llm, memo, _w), _ms) in runs.items():
        final = memo["memo"]
        complete += bool(memo["complete"])
        # the FINAL memo must be grounded end to end (gate + template fallback)
        final_grounded += all(grounded(final[s], [ev, rec])[0] for s in MEMO_SECTIONS)
        if memo_llm is not None:
            memos_llm += 1
            data = memo_llm.model_dump()
            ok = [s for s in MEMO_SECTIONS if data.get(s) and grounded(data[s], [ev, rec])[0]]
            sections_total += len(MEMO_SECTIONS)
            sections_ok += len(ok)
            fully_grounded_llm += len(ok) == len(MEMO_SECTIONS)
        if rec["requires_head"]:
            head_cases += 1
            head_condition_ok += any("Head" in c for c in final.get("conditions", []))
        rows.append({"case": name, "source": memo["source"], "replaced": memo["sections_replaced"]})
    n = len(runs)
    metrics = {"grounded_rate": round(fully_grounded_llm / memos_llm, 4) if memos_llm else None,
               "section_grounded_rate": round(sections_ok / sections_total, 4) if sections_total else None,
               "section_completeness": round(complete / n, 4), "final_memo_grounded": round(final_grounded / n, 4),
               "llm_memos": memos_llm, "memos": n,
               "head_condition_present": f"{head_condition_ok}/{head_cases}"}
    return C.save("credit_memo", metrics, memos=rows)


# ── Offer ───────────────────────────────────────────────────────────────────
def _emi_ref(p, annual, n):
    r = annual / 1200
    return p * r * (1 + r) ** n / ((1 + r) ** n - 1)


def eval_offer() -> dict:
    from app.agents.registry import get_agent
    agent = get_agent("offer_generation")
    exact = total = foir_ok = foir_n = capped = 0
    fails = []
    for product in ("personal_loan", "home_loan", "two_wheeler", "gold", "msme", "car"):
        for amount in (50000, 300000, 750000, 2500000):
            for tenure in (12, 36, 60, 120):
                for score in (15, 45, 75):
                    for income in (None, 40000, 150000):
                        out = agent.execute({"case": {"loan": {"loan_type": product}},
                                             "risk": {"overall": score},
                                             "income": {"consensus_monthly": income} if income else {},
                                             "extra": {"loan_amount": amount, "tenure_months": tenure}}).payload
                        total += 1
                        ref = round(_emi_ref(out["offered_amount"], out["interest_rate"], tenure), 2) \
                            if out["offered_amount"] else 0.0
                        ok = abs(ref - out["emi"]) <= 0.01
                        exact += ok
                        if not ok and len(fails) < 5:
                            fails.append({"product": product, "amount": amount, "emi": out["emi"], "ref": ref})
                        if income:
                            foir_n += 1
                            foir_ok += out["emi"] <= income * 0.5 + 0.01
                        capped += out["capped_by_foir"]
    metrics = {"emi_exact": round(exact / total, 4), "offers": total,
               "foir_cap_respected": round(foir_ok / foir_n, 4) if foir_n else None, "capped_offers": capped}
    return C.save("offer_generation", metrics, failures=fails)


# ── Router (diagram 7, transcribed) ─────────────────────────────────────────
_REV = {"underReview", "underwritingReview"}
DIAGRAM7 = {  # product: branch between documentsVerified and review
    "gold": ["goldValuationScheduled", "goldValued"],
    "tractor": ["fieldVerification"], "microfinance": ["fieldVerification"],
    "lap": ["propertyValuation", "legalVerification"],
    "home_loan": ["propertyValuation", "legalVerification", "incomeVerification", "propertyDocReview",
                  "legalCheck", "technicalValuation"],
    "msme": ["bankGstAnalysis"], "working_capital": ["bankGstAnalysis"],
    "used_vehicle": ["vehicleInspection"], "commercial_vehicle": [],   # inspection only if used
    "personal_loan": [], "two_wheeler": [], "car": [],
}


def _expected(product, status):
    """Set of next statuses diagram 7 allows (None = terminal)."""
    branch = DIAGRAM7[product]
    review = {"underwritingReview"} if product in ("msme", "working_capital") else _REV
    seq = ["draft", "submitted", "documentsProcessing", "documentsVerified", *branch]
    if status in seq:
        i = seq.index(status)
        return {seq[i + 1]} if i + 1 < len(seq) else review
    if status in _REV:
        return {"offerGenerated"}
    return {"offerGenerated": {"accepted", "sanctioned"}, "accepted": {"sanctioned", "disbursed"},
            "sanctioned": {"disbursed"}}.get(status)


def eval_router() -> dict:
    from app.agents.registry import get_agent
    agent = get_agent("workflow_router")
    ok = total = 0
    fails = []
    for product in DIAGRAM7:
        branch = DIAGRAM7[product]
        review = "underwritingReview" if product in ("msme", "working_capital") else "underReview"
        statuses = ["draft", "submitted", "documentsProcessing", "documentsVerified", *branch, review,
                    "offerGenerated", "accepted", "sanctioned"]
        for st in statuses:
            out = agent.execute({"case": {"loan": {"loan_type": product}}, "status": st}).payload
            exp = _expected(product, st)
            good = out.get("next") in exp if exp else out.get("next") is None
            total += 1
            ok += good
            if not good:
                fails.append({"product": product, "status": st, "got": out.get("next"), "expected": sorted(exp or [])})
    # guard behaviour
    guards = []
    o = agent.execute({"case": {"loan": {"loan_type": "personal_loan"}, "documents": {"pan": {"identity_ok": False}}},
                       "status": "documentsProcessing"}).payload
    guards.append(("wrong document blocks documentsVerified", bool(o["blocked_by"])))
    o = agent.execute({"case": {"loan": {"loan_type": "personal_loan"}, "assessment": {"fraud": {"tier": "HIGH"}}},
                       "status": "underReview"}).payload
    guards.append(("fraud HIGH blocks the offer", bool(o["blocked_by"])))
    o = agent.execute({"case": {"loan": {"loan_type": "home_loan"}}, "status": "underReview",
                       "extra": {"loan_amount": 900000}}).payload
    guards.append(("amount above Rs 5 lakh needs the Head", bool(o.get("requires_head_approval"))))
    for st in ("disbursed", "rejected", "cancelled"):
        o = agent.execute({"case": {"loan": {"loan_type": "gold"}}, "status": st}).payload
        guards.append((f"{st} is terminal", o.get("next") is None))
    metrics = {"accuracy": round(ok / total, 4), "transitions": total, "products": len(DIAGRAM7),
               "guards_passed": f"{sum(g for _, g in guards)}/{len(guards)}"}
    return C.save("workflow_router", metrics, failures=fails, guards=[{"check": c, "ok": g} for c, g in guards])
