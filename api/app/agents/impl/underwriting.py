"""Underwriting crew — agents 20-25 (diagrams 6 and 7)."""
from __future__ import annotations

import math

from ...config import settings
from ..base import AgentOutcome, FinSightAgent, grounded
from ..registry import implements
from ..tools import income as income_tool
from ..tools import workflow as wf

PRODUCT_CODE = {  # FinSight product → vendored risk engine product code
    "personal_loan": "PERSONAL", "personal": "PERSONAL", "two_wheeler": "TWO_WHEELER", "tractor": "TRACTOR",
    "gold": "GOLD", "home_loan": "HOME", "home": "HOME", "msme": "MSME", "lap": "LOAN_AGAINST_PROPERTY",
    "microfinance": "MICROFINANCE", "working_capital": "WORKING_CAPITAL", "used_vehicle": "USED_VEHICLE",
    "commercial_vehicle": "COMMERCIAL_VEHICLE", "education_loan": "PERSONAL", "car": "CAR_NEW",
}
ADVISORY = {"approve": "approve_recommended", "refer": "refer_to_officer", "reject": "decline_review_recommended"}


def product_of(case: dict, extra: dict | None = None) -> str:
    raw = ((extra or {}).get("product_code") or (case.get("loan") or {}).get("product_code")
           or (case.get("loan") or {}).get("loan_type") or "personal_loan")
    key = str(raw).lower().replace(" loan", "").replace(" ", "_")
    for k in PRODUCT_CODE:
        if key.startswith(k):
            return k
    return "personal_loan"


# ── Income Analysis (20) ────────────────────────────────────────────────────
@implements("income_analysis")
class IncomeAnalysisAgent(FinSightAgent):
    """Compares independent income figures (payslip, Form 16, ITR, bank credits)."""

    def execute(self, inputs):
        res = self.tool("income.triangulate", income_tool.triangulate, inputs["case"])
        mm = res["mismatches"]
        outcome = "flag" if mm else ("pass" if res["sources"] >= 2 else "info")
        cons = res["consensus_monthly"]
        return AgentOutcome(payload=res, ai_outcome=outcome, score=res["score"],
                            summary=(f"{len(mm)} income mismatch(es) across {res['sources']} source(s)" if mm else
                                     f"{res['sources']} source(s) agree" if res["sources"] >= 2 else
                                     "Only one income source") + (f" · ~Rs {cons:,.0f}/month" if cons else ""),
                            output_summary={"sources": [e["source"] for e in res["estimates"]],
                                            "mismatches": [(m["a"], m["b"], m["diff_pct"]) for m in mm]})


# ── Risk Scoring (21) ───────────────────────────────────────────────────────
def engine_inputs(case: dict, income: dict | None, extra: dict | None = None) -> tuple[dict, dict, list, dict, list]:
    extra = extra or {}
    loan, appl = case.get("loan") or {}, case.get("applicant") or {}
    docs = case.get("documents") or {}
    defaults = []
    product = PRODUCT_CODE.get(product_of(case, extra), "PERSONAL")
    amount = extra.get("loan_amount") or loan.get("loan_amount")
    if not amount:
        amount, _ = 300000, defaults.append("loan_amount")
    tenure = extra.get("tenure_months") or loan.get("tenure_months")
    if not tenure:
        tenure, _ = 36, defaults.append("tenure_months")
    monthly = (income or {}).get("consensus_monthly") or extra.get("monthly_income") or appl.get("monthly_income")
    if not monthly:
        defaults.append("monthly_income")
    stmt = (docs.get("bank_statement") or {}).get("parsed") or {}
    app = {"productCode": product, "loan_amount": float(amount), "loanAmount": float(amount),
           "tenure_months": int(tenure), "purpose": extra.get("purpose", "")}
    if extra.get("collateral"):
        app["collateral"] = extra["collateral"]
    cust = {"monthly_income": float(monthly or 0), "existing_emis": float(extra.get("existing_emi") or 0),
            "monthly_expenses": float(extra.get("monthly_expenses") or 0),
            "pan": bool(docs.get("pan") or (docs.get("kyc_record") or {}).get("parsed", {}).get("pan_number")),
            "aadhaar": bool(docs.get("aadhaar") or (docs.get("kyc_record") or {}).get("parsed", {}).get("aadhaar_number")),
            "employment_type": "salaried" if docs.get("salary_slip") else (extra.get("employment_type") or ""),
            "city": appl.get("city"), "address": appl.get("city")}
    if extra.get("years_in_job") is not None:
        cust["years_in_job"] = extra["years_in_job"]
    else:
        defaults.append("years_in_job")
    type_map = {"salary_slip": "income_proof", "form16": "income_proof", "itr": "income_proof",
                "property_doc": "property_doc", "bank_statement": "bank_statement", "pan": "pan", "aadhaar": "aadhaar"}
    doc_list = []
    for dt, d in docs.items():
        if dt == "kyc_record":
            continue
        flags = (d.get("validation") or {}).get("flags") or {}
        t = d.get("tampering") or {}
        doc_list.append({"type": type_map.get(dt, dt), "verified": bool(d.get("identity_ok", True)) and "error" not in flags.values(),
                         "tamper_score": round((t.get("risk_score") or 0) * 100, 1),
                         "status": "suspicious" if t.get("risk_tier") == "HIGH" else "ok"})
    cibil = ((docs.get("cibil") or {}).get("parsed") or {}).get("cibil_score") or extra.get("credit_score") or appl.get("credit_score")
    # bureau facts beyond the score (dpd_days, default_count, total_accounts,
    # credit_history_years, credit_utilization) when the caller has them
    bureau = {k: v for k, v in (extra.get("bureau") or {}).items() if v is not None}
    if cibil:
        bureau = {"dpd_days": 0, "default_count": 0, **bureau, "cibil_score": float(cibil)}
    else:
        defaults.append("cibil_score")
    return app, cust, doc_list, bureau, defaults


def applicant_age(case: dict, today=None) -> float | None:
    """Age in years: case.applicant.age if given, else from the DOB on the form or a document."""
    from datetime import date
    from ...docrules import parse_date_any
    appl = case.get("applicant") or {}
    if appl.get("age"):
        return float(appl["age"])
    dob = appl.get("dob") or next((((d.get("parsed") or {}).get("dob")) for d in (case.get("documents") or {}).values()
                                   if isinstance(d, dict) and (d.get("parsed") or {}).get("dob")), None)
    iso = parse_date_any(str(dob or ""))
    if not iso:
        return None
    born = date.fromisoformat(iso)
    years = ((today or date.today()) - born).days / 365.25
    return round(years, 1) if 16 <= years <= 100 else None


STRICTNESS_ORDER = ["approve_recommended", "refer_to_officer", "decline_review_recommended"]


def finsight_overrides(case: dict, bureau: dict) -> tuple[list[dict], list[str]]:
    """FinSight's knock-out rules, applied on top of the vendored engine (which is not
    modified). The engine scores missing data as "average" and gives fraud 8% weight for
    most products, so an application built on fabricated documents with no bureau score
    could come out "grade B, approve". These rules make the evidence the agents found count:

      * fraud tier HIGH, a document tamper score >= 80, or 2+ documents rated HIGH
        -> critical: score raised to at least 85 (the engine's own critical-fraud level)
      * one document rated HIGH (score below 80) -> raised to at least 65 (refer band)
      * no CIBIL score -> never "approve" (credit history unknown); the score is unchanged

    Returns (overrides like the engine's own, conditions for the officer)."""
    docs = {dt: d for dt, d in (case.get("documents") or {}).items() if dt != "kyc_record"}
    high = {dt: round(float((d.get("tampering") or {}).get("risk_score") or 0) * 100, 1)
            for dt, d in docs.items() if (d.get("tampering") or {}).get("risk_tier") == "HIGH"}
    fraud_tier = (((case.get("assessment") or {}).get("fraud") or {}).get("tier") or "").upper()
    out, conditions = [], []
    names = ", ".join(dt.replace("_", " ") for dt in high)
    if fraud_tier == "HIGH":
        out.append({"dimension": "fraud", "reason": "Fraud & Policy assessment rated fraud risk HIGH",
                    "adjusted_score": 85})
    if high and (max(high.values()) >= 80 or len(high) >= 2):
        out.append({"dimension": "fraud", "reason": f"Critical document tampering ({names}; highest "
                                                    f"{max(high.values()):.0f}/100)", "adjusted_score": 85})
    elif high:
        out.append({"dimension": "fraud", "reason": f"Document tampering rated HIGH ({names})", "adjusted_score": 65})
    if high or fraud_tier == "HIGH":
        conditions.append("Verify the flagged documents against originals before any approval")
    if bureau.get("cibil_score") is None:
        conditions.append("No CIBIL score on file — pull a bureau report before approval")
    return out, conditions


@implements("risk_scoring")
class RiskScoringAgent(FinSightAgent):
    """Nine-dimension rule-based risk engine (vendored) + XGBoost overlay with
    per-feature contributions. Model caveats are carried with the result."""

    def execute(self, inputs):
        from ..vendor.risk_engine import calculate_ml_risk_score, run_full_pipeline
        case = inputs["case"]
        app, cust, docs, bureau, defaults = engine_inputs(case, inputs.get("income"), inputs.get("extra"))
        full = self.tool("risk_engine.run_full_pipeline", run_full_pipeline, app, cust, docs, bureau)
        risk, decision, pricing = full["risk"], full["decision"], full["pricing"]
        ml = None
        try:
            aligned_scorer()
            ml = self.tool("risk_engine.ml_overlay", calculate_ml_risk_score, app, cust, docs, bureau,
                           model_type="indian")
            _, model_p = aligned_scorer().predict(app, cust, bureau)
            ml = {"overall": ml["overall"], "grade": ml["grade"], "defaultProbability": ml["defaultProbability"],
                  "model_default_probability": round(model_p, 4) if model_p is not None else None,
                  "feature_contributions": _ml_contribs(app, cust, bureau)}
        except Exception as exc:  # noqa: BLE001 - the rule-based score stands on its own
            ml = {"error": f"{type(exc).__name__}: {exc}"}
        contribs = sorted(({"dimension": k, "score": v["score"], "weight": v["weight"],
                            "contribution": round(v["score"] * v["weight"], 2),
                            "factors": [f["details"] for f in v["factors"]][:3]}
                           for k, v in risk["dimensions"].items()), key=lambda x: -x["contribution"])
        # FinSight knock-outs (fabricated documents, HIGH fraud, no bureau score)
        from ..vendor.risk_engine.decision_engine import DEFAULT_THRESHOLDS
        from ..vendor.risk_engine.risk_engine import _default_probability, _to_grade
        engine_overall = risk["overall"]
        engine_advisory = ADVISORY.get(decision["decision"], "refer_to_officer")
        # A model trained on real two-wheeler loans (tools/pd_model.py) adds evidence-based points to
        # the rule score. The engine's own critical overrides stay floors, and since `band` below can
        # only tighten the engine's advisory, the model can turn an approval into a referral but
        # never the reverse.
        real = None
        if not (inputs.get("extra") or {}).get("_no_real_model"):
            try:
                from ..tools import pd_model
                real = self.tool("pd_model.score", pd_model.score, app["productCode"], app, cust, bureau,
                                 applicant_age(case))
            except Exception as exc:  # noqa: BLE001 - the rule score stands on its own
                real = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
        model_overall = engine_overall
        if real and "points" in real:
            model_overall = min(100.0, max(0.0, engine_overall + real["points"]))
            if risk["overrides"]:
                model_overall = max(model_overall, engine_overall)
        knockouts, ko_conditions = finsight_overrides(case, bureau)
        overall = max([model_overall] + [o["adjusted_score"] for o in knockouts])
        th = DEFAULT_THRESHOLDS.get(app["productCode"], DEFAULT_THRESHOLDS["DEFAULT"])
        band = ("decline_review_recommended" if overall >= th["reject_min"] else
                "refer_to_officer" if overall > th["approve_max"] else "approve_recommended")
        advisory = max(engine_advisory, band, key=STRICTNESS_ORDER.index)
        reason = decision["reason"]
        if real and "points" in real and abs(real["points"]) >= 1:
            reason = (f"Rule score {engine_overall:.1f} {'raised' if real['points'] > 0 else 'lowered'} to "
                      f"{model_overall:.1f} by the model trained on real two-wheeler loans "
                      f"(predicted default {real['pd']:.0%})")
        if knockouts and overall > model_overall:
            reason = f"{knockouts[0]['reason']} — risk raised from {model_overall:.1f} to {overall:.1f}"
        if advisory == "approve_recommended" and bureau.get("cibil_score") is None:
            advisory = "refer_to_officer"
            reason = "No CIBIL score — credit history unknown, so the engine's approval is only a referral"
        changed = abs(overall - engine_overall) > 1e-9
        payload = {"overall": round(overall, 2), "grade": _to_grade(overall) if changed else risk["grade"],
                   "default_probability": (_default_probability(overall) if changed
                                           else risk["defaultProbability"]),
                   "real_loan_model": real,
                   "decision_advisory": advisory,
                   "decision_reason": reason,
                   # the engine's "Score 41.9 is in refer range" no longer describes a raised score
                   "conditions": [c for c in decision["conditions"]
                                  if not (overall > engine_overall and str(c).startswith("Score "))] + ko_conditions,
                   "overrides": [o["reason"] for o in risk["overrides"]] + [o["reason"] for o in knockouts],
                   "engine": {"overall": engine_overall, "grade": risk["grade"], "advisory": engine_advisory},
                   "explanation": risk["explanation"],
                   "dimension_contributions": contribs, "pricing": pricing, "ml_overlay": ml,
                   "inputs_defaulted": defaults, "product_code": app["productCode"],
                   "model_notes": ["Rule-based engine: nine weighted dimensions (product weight matrix).",
                                   "Two-wheeler loans: adjusted by a model trained on real Indian two-wheeler "
                                   "loans (L&T; tools/pd_model.py, models/two_wheeler_pd/v1/card.json)."
                                   if real and "points" in real else
                                   "No model trained on real loans exists for this product; the rule score stands.",
                                   "ML overlay: XGBoost trained on a teaching dataset — shown for reference only; "
                                   "on real L&T loans it ranked defaults at chance level (AUC 0.515).",
                                   "Inputs not available were defaulted: " + (", ".join(defaults) or "none")]}
        score = payload["overall"] / 100
        outcome = "fail" if score >= 0.7 else "flag" if score >= 0.4 else "pass"
        return AgentOutcome(payload=payload, ai_outcome=outcome, score=round(score, 4),
                            summary=f"Risk {payload['overall']:.1f}/100 · grade {payload['grade']} · "
                                    f"{payload['decision_advisory'].replace('_', ' ')}"
                                    + (f" · {len(defaults)} input(s) defaulted" if defaults else ""),
                            output_summary={"overall": payload["overall"], "grade": payload["grade"],
                                            "engine_overall": engine_overall, "knockouts": [o["reason"] for o in knockouts],
                                            "real_model_points": (real or {}).get("points"),
                                            "advisory": payload["decision_advisory"], "defaults": defaults,
                                            "top_dimensions": [c["dimension"] for c in contribs[:3]]})


def training_features(app, cust, bureau):
    """The XGBoost overlay's 11 features built EXACTLY as the sibling project
    built them when it trained the Indian model (indian_nbfc_test.build_features):
    DEBTINC = loan / annual income × 10, DELINQ = months delinquent, and the
    constants it used for the columns its data did not have (YOJ 5, CLAGE 60,
    CLNO 5, VALUE = 1.5 × loan). The vendored scorer builds them differently
    (obligations / monthly income, days past due, real account counts), which
    the model never saw: measured on 1,500 held-out loans the overlay's AUC was
    0.669 with the vendored mapping and 0.771 with this one."""
    app, cust, bureau = app or {}, cust or {}, bureau or {}
    loan = float(app.get("loanAmount") or app.get("loan_amount") or 50000)
    annual = float(cust.get("monthly_income") or 0) * 12
    delinq = bureau.get("delinquent_months")
    if delinq is None:
        delinq = round(float(bureau.get("dpd_days") or 0) / 30)
    return {"LOAN": loan, "MORTDUE": 0.0, "VALUE": loan * 1.5, "YOJ": 5.0, "DEROG": 0.0,
            "DELINQ": float(delinq), "CLAGE": 60.0, "CLNO": 5.0,
            "DEBTINC": loan / annual * 100 * 0.1 if annual > 0 else 35.0, "REASON": 1.0, "JOB": 1.0}


def aligned_scorer():
    """The Indian ML scorer with its feature builder replaced by
    training_features (instance override; the vendored, hash-pinned code is
    not modified)."""
    from ..vendor.risk_engine.ml_scorer import get_ml_scorer
    sc = get_ml_scorer("indian")
    if not getattr(sc, "_finsight_aligned", False):
        sc._extract_features = training_features
        sc._finsight_aligned = True
    return sc


def _ml_contribs(app, cust, bureau):
    """True per-feature attributions for the tree model (XGBoost pred_contribs)."""
    try:
        import numpy as np
        import pandas as pd
        import xgboost as xgb
        sc = aligned_scorer()
        feats = sc._extract_features(app, cust, bureau)
        X = pd.DataFrame([feats])[sc.features]
        Xs = sc.scaler.transform(X)
        contrib = sc.model.get_booster().predict(xgb.DMatrix(np.asarray(Xs), feature_names=list(sc.features)),
                                                 pred_contribs=True)[0]
        pairs = sorted(zip(sc.features, contrib[:-1]), key=lambda x: -abs(x[1]))
        return [{"feature": f, "contribution": round(float(v), 4), "value": feats[f]} for f, v in pairs[:6]]
    except Exception:  # noqa: BLE001
        return []


# ── Underwriting Supervisor (23) ────────────────────────────────────────────
def evidence_bundle(case: dict, income: dict | None, risk: dict | None, extra: dict | None = None) -> dict:
    """Compact, PII-light evidence for the supervisor and memo writer."""
    extra = extra or {}
    docs = case.get("documents") or {}
    a = case.get("assessment") or {}
    fraud = a.get("fraud") or {}
    loan = case.get("loan") or {}
    return {
        "application": case.get("application_no") or "(draft)",
        "product": product_of(case, extra), "loan_amount": extra.get("loan_amount") or loan.get("loan_amount"),
        "tenure_months": extra.get("tenure_months") or loan.get("tenure_months"),
        "documents": {dt: {"identity_ok": d.get("identity_ok"),
                           "field_errors": [k for k, f in ((d.get("validation") or {}).get("flags") or {}).items() if f == "error"],
                           "tamper_tier": (d.get("tampering") or {}).get("risk_tier"),
                           "tamper_score": (d.get("tampering") or {}).get("risk_score")}
                      for dt, d in docs.items() if dt != "kyc_record"},
        "face_match": (case.get("face") or {}).get("overall"),
        "fraud": {"tier": fraud.get("tier"), "score": fraud.get("fused_score"), "floors": fraud.get("floors"),
                  "driver": fraud.get("dominant_component")} if fraud else None,
        "policy_status": a.get("policy_status"),
        "cross_checks": [c["title"] for c in (a.get("cross_checks") or []) if c["level"] != "ok"],
        "income": {"monthly_estimate": (income or {}).get("consensus_monthly"),
                   "mismatches": [m["detail"] for m in (income or {}).get("mismatches", [])]},
        "risk": {"score": (risk or {}).get("overall"), "grade": (risk or {}).get("grade"),
                 "engine_suggestion": (risk or {}).get("decision_advisory"),
                 "top_dimensions": [d["dimension"] for d in (risk or {}).get("dimension_contributions", [])[:3]]},
    }


def baseline_recommendation(ev: dict) -> dict:
    """The deterministic floor the supervisor may only tighten."""
    reasons = []
    fraud = ev.get("fraud") or {}
    risk = ev.get("risk") or {}
    if fraud.get("tier") == "HIGH":
        reasons.append("fraud risk HIGH")
    if risk.get("engine_suggestion") == "decline_review_recommended":
        reasons.append("risk engine suggests a decline review")
    if reasons:
        rec = "decline_review_recommended"
    else:
        docs_missing = [d for d, v in (ev.get("documents") or {}).items() if v.get("identity_ok") is False]
        if ev.get("policy_status") == "NEEDS_INFO" or docs_missing:
            rec = "request_more_info"
            reasons.append("policy check needs information" if ev.get("policy_status") == "NEEDS_INFO"
                           else f"wrong document for {', '.join(docs_missing)}")
        elif (fraud.get("tier") == "MEDIUM" or risk.get("engine_suggestion") == "refer_to_officer"
              or ev.get("policy_status") in ("FAIL", "PASS_WITH_WARNINGS") or (ev.get("income") or {}).get("mismatches")
              or ev.get("cross_checks") or ev.get("face_match") in ("mismatch", "uncertain")):
            rec = "refer_to_officer"
            for cond, why in ((fraud.get("tier") == "MEDIUM", "fraud risk MEDIUM"),
                              (risk.get("engine_suggestion") == "refer_to_officer", "risk in the refer band"),
                              (ev.get("policy_status") in ("FAIL", "PASS_WITH_WARNINGS"), f"policy {ev.get('policy_status')}"),
                              (bool((ev.get("income") or {}).get("mismatches")), "income sources disagree"),
                              (bool(ev.get("cross_checks")), "cross-document inconsistency"),
                              (ev.get("face_match") in ("mismatch", "uncertain"), f"face match {ev.get('face_match')}")):
                if cond:
                    reasons.append(why)
        else:
            rec = "approve_recommended"
            reasons.append("no adverse evidence found")
    amt = ev.get("loan_amount") or 0
    return {"recommendation": rec, "reasons": reasons, "requires_head": bool(amt and float(amt) > 500000)}


def enforce_floor(baseline: dict, llm_rec: str | None) -> tuple[str, bool]:
    from ..crews.underwriting import STRICTNESS
    base = baseline["recommendation"]
    if llm_rec not in STRICTNESS:
        return base, False
    if STRICTNESS[llm_rec] < STRICTNESS[base]:
        return base, True          # tried to loosen → baseline kept
    return llm_rec, False


@implements("underwriting_supervisor")
class UnderwritingSupervisorAgent(FinSightAgent):
    """Recommends a next step. Deterministic baseline (floor) + LLM that may
    only tighten it. Advisory: a person decides; > Rs 5 lakh needs the Head."""

    def execute(self, inputs):
        ev = inputs["evidence"]
        base = baseline_recommendation(ev)
        llm = inputs.get("llm_recommendation")          # from the Underwriting Crew
        final, loosen_blocked = enforce_floor(base, llm.recommendation if llm else None)
        warnings = ["LLM recommendation was looser than the rule baseline — baseline kept"] if loosen_blocked else []
        rationale = None
        if llm:
            ok, bad = grounded(llm.rationale + " " + " ".join(llm.drivers), ev)
            if ok:
                rationale = llm.rationale
            else:
                warnings.append(f"rationale dropped: cites figures not in the evidence {sorted(bad)[:4]}")
        payload = {"recommendation": final, "baseline": base["recommendation"], "baseline_reasons": base["reasons"],
                   "requires_head": base["requires_head"], "llm_recommendation": llm.recommendation if llm else None,
                   "loosening_blocked": loosen_blocked, "drivers": (llm.drivers if llm and rationale else base["reasons"]),
                   "rationale": rationale or ("; ".join(base["reasons"]).capitalize() + "."),
                   "caveats": (llm.caveats if llm and rationale else []), "grounded": rationale is not None or not llm,
                   "advisory": True}
        o = {"approve_recommended": "pass", "request_more_info": "uncertain", "refer_to_officer": "flag",
             "decline_review_recommended": "fail"}[final]
        return AgentOutcome(payload=payload, ai_outcome=o, warnings=warnings,
                            summary=f"{final.replace('_', ' ')} (baseline {base['recommendation'].replace('_', ' ')})"
                                    + (" · Head approval needed" if base["requires_head"] else ""),
                            output_summary={"final": final, "baseline": base["recommendation"],
                                            "llm": payload["llm_recommendation"], "loosening_blocked": loosen_blocked})


# ── Credit Memo (22) ────────────────────────────────────────────────────────
def template_memo(ev: dict, rec: dict) -> dict:
    """Deterministic memo from the evidence — always grounded; used when the
    LLM memo is unavailable or fails the grounding gate."""
    inc, risk, fraud = ev.get("income") or {}, ev.get("risk") or {}, ev.get("fraud") or {}
    amt = ev.get("loan_amount")
    return {
        "executive_summary": (f"Application {ev['application']} for a {ev['product'].replace('_', ' ')} "
                              f"{('of Rs ' + format(float(amt), ',.0f')) if amt else ''}. Recommendation: "
                              f"{rec['recommendation'].replace('_', ' ')}."),
        "applicant_profile": f"Documents on file: {', '.join(ev.get('documents', {}).keys()) or 'none'}. "
                             f"Face match: {ev.get('face_match') or 'not run'}.",
        "income_assessment": (f"Estimated monthly income Rs {inc['monthly_estimate']:,.0f}. " if inc.get("monthly_estimate") else
                              "Income could not be estimated from the documents. ")
                             + ("Mismatches: " + "; ".join(inc.get("mismatches", [])) if inc.get("mismatches") else
                                "Income sources are consistent."),
        "risk_assessment": (f"Risk score {risk.get('score')} (grade {risk.get('grade')}); main drivers: "
                            f"{', '.join(risk.get('top_dimensions') or []) or 'n/a'}."),
        "fraud_and_compliance": (f"Fraud assessment {fraud.get('tier')} ({fraud.get('score')}). " if fraud else
                                 "Fraud assessment not run. ") + f"Policy check: {ev.get('policy_status') or 'not run'}.",
        "recommendation": rec["rationale"],
        "conditions": ["Head Manager approval required (amount above Rs 5 lakh)"] if rec.get("requires_head") else [],
    }


@implements("credit_memo")
class CreditMemoAgent(FinSightAgent):
    """Writes the credit memo (LLM via the Underwriting Crew). Every figure must
    exist in the evidence; failing sections fall back to the deterministic
    template, so the memo never states an unsupported number."""

    def execute(self, inputs):
        from ..crews.underwriting import MEMO_SECTIONS
        ev, rec = inputs["evidence"], inputs["recommendation"]
        tmpl = template_memo(ev, rec)
        llm = inputs.get("llm_memo")
        memo, source, dropped = dict(tmpl), "template", []
        if llm:
            data = llm.model_dump()
            source = "llm"
            for sec in MEMO_SECTIONS:
                text = data.get(sec) or ""
                if text and grounded(text, [ev, rec])[0]:
                    memo[sec] = text
                else:
                    dropped.append(sec)
            conds = [c for c in data.get("conditions", []) if grounded(c, [ev, rec])[0]]
            memo["conditions"] = list(dict.fromkeys(tmpl["conditions"] + conds))
            if len(dropped) == len(MEMO_SECTIONS):
                source = "template"
        complete = all(memo.get(s) for s in MEMO_SECTIONS)
        warnings = [f"{len(dropped)} section(s) replaced by the template (ungrounded or empty): {', '.join(dropped)}"] if dropped else []
        return AgentOutcome(payload={"memo": memo, "source": source, "sections_replaced": dropped,
                                     "complete": complete, "status": "draft"},
                            ai_outcome="info", warnings=warnings,
                            summary=f"Memo drafted ({source}{', ' + str(len(dropped)) + ' section(s) templated' if dropped and source == 'llm' else ''})",
                            output_summary={"source": source, "replaced": dropped, "complete": complete})


# ── Offer Generation (24) ───────────────────────────────────────────────────
def emi(principal: float, annual_rate: float, months: int) -> float:
    r = annual_rate / 12 / 100
    if r <= 0 or months <= 0:
        return 0.0
    f = (1 + r) ** months
    return principal * r * f / (f - 1)


@implements("offer_generation")
class OfferGenerationAgent(FinSightAgent):
    """Draft, risk-priced offer (vendored pricing engine) capped so the EMI
    keeps FOIR within 50%. A draft for the officer — never sent automatically."""

    def execute(self, inputs):
        from ..vendor.risk_engine.pricing_engine import get_rate
        risk, inc, extra = inputs.get("risk") or {}, inputs.get("income") or {}, inputs.get("extra") or {}
        case = inputs["case"]
        loan = case.get("loan") or {}
        product = PRODUCT_CODE.get(product_of(case, extra), "PERSONAL")
        requested = float(extra.get("loan_amount") or loan.get("loan_amount") or 0)
        tenure = int(extra.get("tenure_months") or loan.get("tenure_months") or 36)
        if not requested:
            return AgentOutcome(payload=None, status="skipped", summary="No requested amount to price")
        score = float(risk.get("overall") or 50)
        price = self.tool("pricing_engine.get_rate", get_rate, product, score, None,
                          {"tenure_months": tenure, "loan_amount": requested})
        rate = price["finalRate"]
        monthly = inc.get("consensus_monthly")
        max_emi = monthly * 0.5 - float(extra.get("existing_emi") or 0) if monthly else None
        amount = requested
        capped = False
        if max_emi is not None and max_emi > 0 and emi(requested, rate, tenure) > max_emi:
            r = rate / 12 / 100
            f = (1 + r) ** tenure
            amount = math.floor(max_emi * (f - 1) / (r * f) / 1000) * 1000
            capped = True
        if max_emi is not None and max_emi <= 0:
            amount, capped = 0, True
        e = round(emi(amount, rate, tenure), 2)
        payload = {"status": "draft", "requested_amount": requested, "offered_amount": amount, "capped_by_foir": capped,
                   "interest_rate": rate, "tenure_months": tenure, "emi": e,
                   "total_interest": round(e * tenure - amount, 2),
                   "processing_fee": round(amount * settings.kfs_processing_fee_pct / 100, 2),
                   "pricing": price, "note": "Draft offer — an officer reviews and sends it."}
        # the Key Facts Statement that must reach the borrower with the offer (APR, every fee, cooling-off)
        from ..tools.kfs import build_kfs
        payload["kfs"] = build_kfs(payload)
        return AgentOutcome(payload=payload, ai_outcome="info",
                            summary=f"Draft: Rs {amount:,.0f} at {rate}% for {tenure} months, EMI Rs {e:,.0f}"
                                    + (" (capped to keep FOIR <= 50%)" if capped else ""),
                            output_summary={"offered": amount, "rate": rate, "emi": e, "capped": capped})


# ── Workflow Router (25) ────────────────────────────────────────────────────
@implements("workflow_router")
class WorkflowRouterAgent(FinSightAgent):
    """Recommends the next lifecycle step for the product (diagram 7)."""

    def execute(self, inputs):
        case = inputs["case"]
        extra = inputs.get("extra") or {}
        product = product_of(case, extra)
        status = inputs.get("status") or (case.get("loan") or {}).get("status_code") or "documentsProcessing"
        a = case.get("assessment") or {}
        docs = case.get("documents") or {}
        evidence = {"fraud_tier": (a.get("fraud") or {}).get("tier"), "policy_status": a.get("policy_status"),
                    "identity_failures": [d for d, v in docs.items() if v.get("identity_ok") is False],
                    "documents_missing": [d for d in ("pan", "aadhaar") if d not in docs and "kyc_record" not in docs]}
        amount = extra.get("loan_amount") or (case.get("loan") or {}).get("loan_amount")
        res = self.tool("workflow.next_step", wf.next_step, product, status, evidence,
                        float(amount) if amount else None, bool(extra.get("used_vehicle")))
        return AgentOutcome(payload={**res, "product": product}, ai_outcome="flag" if res.get("blocked_by") else "info",
                            summary=(f"{status} → {res['next']}" if res.get("next") else res["note"])
                                    + (f" · blocked: {len(res['blocked_by'])}" if res.get("blocked_by") else ""),
                            output_summary={"product": product, "status": status, "next": res.get("next"),
                                            "blocked": res.get("blocked_by")})
