"""Risk Scoring on real loans.

Datasets
  * L&T Vehicle Loan Default (India, ~233k two-wheeler loans, 21.7% default) — primary.
    Only train.csv carries labels (the hackathon test.csv does not), so train.csv is split by
    loan id into dev (30%) / test (70%) with the protocol's seed.
  * Berka (Czech bank, 682 loans with outcome, full transaction history) — second market check.

What FinSight would know about these applicants, mapped field by field (MAPPING below). What
neither dataset has (L&T: income, tenure; Berka: bureau score) is left out, so the agent defaults
it exactly as it would in production; the result records which inputs were defaulted.

The "trained ceiling" is a gradient-boosting model fitted on the L&T DEV split with every
L&T feature, scored on the same test sample. The agent was never trained on L&T, so the ratio
agent AUC / ceiling AUC says how much of the achievable ranking the agent reaches untrained.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime

from eval import common as C
from eval.real import core as R

LT = R.RAW / "lt_vehicle" / "train.csv"
BERKA = R.RAW / "berka"

MAPPING = {
    "loan_amount": "disbursed_amount",
    "collateral.value": "asset_cost (so LTV = disbursed / asset cost)",
    "credit_score": "PERFORM_CNS.SCORE when >= 300 (0 = no bureau history and 11-18 = 'Not Scored' codes -> not provided)",
    "bureau.default_count": "PRI.OVERDUE.ACCTS + SEC.OVERDUE.ACCTS (accounts currently overdue)",
    "bureau.dpd_days": "30 if DELINQUENT.ACCTS.IN.LAST.SIX.MONTHS > 0 else 0",
    "bureau.credit_utilization": "100 * PRI.CURRENT.BALANCE / PRI.SANCTIONED.AMOUNT",
    "bureau.total_accounts": "PRI.NO.OF.ACCTS + SEC.NO.OF.ACCTS",
    "bureau.credit_history_years": "CREDIT.HISTORY.LENGTH",
    "bureau.enquiry_count": "NO.OF_INQUIRIES",
    "applicant.age": "Date.of.Birth at DisbursalDate",
    "existing_emi": "PRIMARY.INSTAL.AMT + SEC.INSTAL.AMT",
    "employment_type": "Employment.Type",
    "documents": "pan / aadhaar present when PAN_flag / Aadhar_flag = 1",
    "not available (defaulted by the agent)": "monthly income, tenure",
}


def _years(s) -> float:
    m = re.match(r"(\d+)yrs\s+(\d+)mon", str(s or ""))
    return int(m.group(1)) + int(m.group(2)) / 12 if m else 0.0


def lt_rows():
    import pandas as pd
    df = pd.read_csv(LT)
    df["id"] = df["UniqueID"].astype(str)
    df["label"] = df["loan_default"].astype(int)
    return df


def lt_inputs(r) -> tuple[dict, dict]:
    docs = {}
    if r["PAN_flag"] == 1:
        docs["pan"] = {"identity_ok": True}
    if r["Aadhar_flag"] == 1:
        docs["aadhaar"] = {"identity_ok": True}
    sanctioned = float(r["PRI.SANCTIONED.AMOUNT"] or 0)
    bureau = {"default_count": float(r["PRI.OVERDUE.ACCTS"] + r["SEC.OVERDUE.ACCTS"]),
              "dpd_days": 30.0 if r["DELINQUENT.ACCTS.IN.LAST.SIX.MONTHS"] > 0 else 0.0,
              "credit_utilization": round(100 * float(r["PRI.CURRENT.BALANCE"]) / sanctioned, 1) if sanctioned > 0 else 0.0,
              "total_accounts": float(r["PRI.NO.OF.ACCTS"] + r["SEC.NO.OF.ACCTS"]),
              "credit_history_years": round(_years(r["CREDIT.HISTORY.LENGTH"]), 2),
              "enquiry_count": float(r["NO.OF_INQUIRIES"])}
    extra = {"loan_amount": float(r["disbursed_amount"]),
             "collateral": {"type": "vehicle", "value": float(r["asset_cost"])},
             "existing_emi": float(r["PRIMARY.INSTAL.AMT"] + r["SEC.INSTAL.AMT"]),
             "employment_type": "salaried" if r["Employment.Type"] == "Salaried" else "self_employed",
             "bureau": bureau}
    if r["PERFORM_CNS.SCORE"] and r["PERFORM_CNS.SCORE"] >= 300:     # 11-18 are "Not Scored" codes
        extra["credit_score"] = float(r["PERFORM_CNS.SCORE"])
    case = {"loan": {"loan_type": "Two Wheeler Loan"}, "documents": docs, "applicant": {"age": _age(r)}}
    return case, extra


def _age(r) -> float | None:
    """Age at disbursal (FinSight has the DOB from PAN / Aadhaar)."""
    try:
        dob = datetime.strptime(str(r["Date.of.Birth"]), "%d-%m-%y")
        disb = datetime.strptime(str(r["DisbursalDate"]), "%d-%m-%y")
    except ValueError:
        return None
    if dob.year > disb.year - 16:          # two-digit years: 01-01-84 is 1984, not 2084
        dob = dob.replace(year=dob.year - 100)
    return round((disb - dob).days / 365.25, 1)


def _score(agent, case, extra) -> dict:
    with C.Timer() as t:
        p = agent.execute({"case": case, "income": None, "extra": extra}).payload
    return {"overall": p["overall"], "grade": p["grade"], "advisory": p["decision_advisory"],
            "ml_p": (p.get("ml_overlay") or {}).get("model_default_probability"),
            "defaulted": p.get("inputs_defaulted"), "ms": round(t.ms, 1)}


def _features(df):
    import pandas as pd
    X = df.drop(columns=["UniqueID", "id", "label", "loan_default", "Date.of.Birth", "DisbursalDate",
                         "PERFORM_CNS.SCORE.DESCRIPTION", "Employment.Type", "AVERAGE.ACCT.AGE",
                         "CREDIT.HISTORY.LENGTH"]).copy()
    X["acct_age_years"] = df["AVERAGE.ACCT.AGE"].map(_years)
    X["history_years"] = df["CREDIT.HISTORY.LENGTH"].map(_years)
    X["salaried"] = (df["Employment.Type"] == "Salaried").astype(int)
    dob = pd.to_datetime(df["Date.of.Birth"], format="%d-%m-%y", errors="coerce")
    dob = dob.where(dob.dt.year <= 2005, dob - pd.DateOffset(years=100))
    disb = pd.to_datetime(df["DisbursalDate"], format="%d-%m-%y", errors="coerce")
    X["age"] = (disb - dob).dt.days / 365.25
    return X


def trained_ceiling(df, test_ids: set) -> dict:
    """Gradient boosting fitted on the DEV split only; returns its scores on the test sample."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    dev = df[df["id"].map(lambda i: R.split_of(i) == "dev")]
    test = df[df["id"].isin(test_ids)]
    model = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=31,
                                           l2_regularization=1.0, random_state=0)
    model.fit(_features(dev), dev["label"])
    p = model.predict_proba(_features(test))[:, 1]
    return {"scores": dict(zip(test["id"], p.tolist())), "n_train": len(dev),
            "model": "sklearn HistGradientBoostingClassifier(max_iter=300, lr=0.05, leaves=31, l2=1.0), "
                     "all L&T features, fitted on the dev split"}


GRADES = ["A+", "A", "B+", "B", "C+", "C", "D"]


def eval_risk(split: str = "test", n: int = 5000) -> dict:
    import pandas as pd
    from app.agents.registry import get_agent
    agent = get_agent("risk_scoring")
    df = lt_rows()
    rows = R.take(df.to_dict("records"), split)
    pick = R.sample(rows, n)
    cache = R.ItemCache("risk_scoring", "lt-v3")
    out = cache.run(pick, lambda r: _score(agent, *lt_inputs(r)), label="L&T", every=500)
    for r, o in zip(pick, out):
        o["label"], o["id"] = r["label"], r["id"]
    ceil = trained_ceiling(df, {r["id"] for r in pick})
    for o in out:
        o["ceil"] = ceil["scores"][o["id"]]

    auc_agent = R.auc_ci(out, score=lambda o: o["overall"])
    auc_ml = R.auc_ci([o for o in out if o["ml_p"] is not None], score=lambda o: o["ml_p"])
    auc_ceiling = R.auc_ci(out, score=lambda o: o["ceil"])

    def ratio(s):
        a = C.auc([o["overall"] for o in s if o["label"]], [o["overall"] for o in s if not o["label"]])
        c = C.auc([o["ceil"] for o in s if o["label"]], [o["ceil"] for o in s if not o["label"]])
        return round((a - 0.5) / (c - 0.5), 4) if a and c and c > 0.5 else None
    # the ratio is taken over the part above chance: 0.5 AUC is no skill, so (a-.5)/(c-.5)
    rel = R.bootstrap(ratio, out)

    by_grade = {}
    for o in out:
        g = by_grade.setdefault(o["grade"], {"n": 0, "defaults": 0})
        g["n"] += 1
        g["defaults"] += o["label"]
    rates = [(g, by_grade[g]["defaults"] / by_grade[g]["n"]) for g in GRADES if by_grade.get(g, {}).get("n", 0) >= 30]
    monotone = all(b[1] >= a[1] - 0.005 for a, b in zip(rates, rates[1:]))
    advis = {}
    for o in out:
        a = advis.setdefault(o["advisory"], {"n": 0, "defaults": 0})
        a["n"] += 1
        a["defaults"] += o["label"]

    # monotonicity: make ONE input worse; the score must not go down
    worsen = [("CIBIL -100", lambda e: e.update(credit_score=max(300.0, e["credit_score"] - 100)) if "credit_score" in e else None),
              ("2 more overdue accounts", lambda e: e["bureau"].update(default_count=e["bureau"]["default_count"] + 2)),
              ("recent delinquency", lambda e: e["bureau"].update(dpd_days=max(30.0, e["bureau"]["dpd_days"]))),
              ("asset worth 15% less", lambda e: e["collateral"].update(value=e["collateral"]["value"] * 0.85)),
              ("existing EMI +50%", lambda e: e.update(existing_emi=e["existing_emi"] * 1.5 + 500))]
    viol = checks = 0
    examples = []
    for r in pick[:200]:
        case, extra = lt_inputs(r)
        base = agent.execute({"case": case, "income": None, "extra": extra}).payload["overall"]
        for name, fn in worsen:
            e2 = copy.deepcopy(extra)
            if fn(e2) is None and name == "CIBIL -100" and "credit_score" not in extra:
                continue
            s = agent.execute({"case": case, "income": None, "extra": e2}).payload["overall"]
            checks += 1
            if s < base - 1e-6:
                viol += 1
                if len(examples) < 5:
                    examples.append({"change": name, "before": round(base, 2), "after": round(s, 2)})

    berka = eval_berka(agent, split)
    defaulted = pd.Series([",".join(o["defaulted"] or []) for o in out]).value_counts().head(3).to_dict()
    metrics = {
        "auc_vs_trained_ceiling": rel,
        "auc": auc_agent, "auc_trained_ceiling": auc_ceiling, "auc_ml_overlay": auc_ml,
        "grade_default_rate_monotone": 1.0 if monotone else 0.0,
        "default_rate_by_grade": {g: {"n": by_grade[g]["n"], "default_rate": round(by_grade[g]["defaults"] / by_grade[g]["n"], 4)}
                                  for g in GRADES if g in by_grade},
        "default_rate_by_advisory": {a: {"n": v["n"], "default_rate": round(v["defaults"] / v["n"], 4)} for a, v in advis.items()},
        "monotonicity_violations": viol, "monotonicity_checks": checks, "monotonicity_examples": examples,
        "base_default_rate": round(sum(o["label"] for o in out) / len(out), 4),
        "inputs_defaulted_top": defaulted,
        "berka": berka,
        "latency": R.latency([o["ms"] for o in out]),
    }
    data = {"name": "L&T Vehicle Loan Default (train.csv, labelled) + Berka", "source": "Kaggle; see DATASETS.md",
            "split": split, "n": len(out), "population": len(rows), "mapping": MAPPING,
            "ceiling_model": {k: v for k, v in ceil.items() if k != "scores"},
            "leakage": "the agent's ML overlay was trained on a different dataset (projects/risk analysis 2); "
                       "no L&T or Berka row was used to build or tune the agent"}
    return R.save("risk_scoring", split, metrics, data)


# ── Berka ───────────────────────────────────────────────────────────────────
def _d(yymmdd) -> datetime:
    s = str(int(yymmdd))
    return datetime(1900 + int(s[:2]), int(s[2:4]), int(s[4:6]))


def berka_rows():
    import pandas as pd
    loans = pd.read_csv(BERKA / "loan.csv", sep=";")
    trans = pd.read_csv(BERKA / "trans.csv", sep=";", low_memory=False, usecols=["account_id", "date", "type", "amount", "balance"])
    trans = trans[trans["account_id"].isin(set(loans["account_id"]))].copy()
    trans["dt"] = pd.to_datetime(trans["date"].astype(int).astype(str), format="%y%m%d")
    by_acct = {a: g for a, g in trans.groupby("account_id")}
    rows = []
    for _, l in loans.iterrows():
        ld = pd.Timestamp(_d(l["date"]))
        g = by_acct.get(l["account_id"])
        t = g[(g["dt"] < ld) & (g["dt"] >= ld - pd.DateOffset(years=1))] if g is not None else g
        if t is None:
            t = trans.iloc[0:0]
        months = max(1, len({(d.year, d.month) for d in t["dt"]}))
        credits = t[t["type"] == "PRIJEM"]["amount"].sum()
        bal = t["balance"]
        rows.append({"id": str(l["loan_id"]), "label": int(l["status"] in ("B", "D")), "amount": float(l["amount"]),
                     "duration": int(l["duration"]), "payments": float(l["payments"]),
                     "monthly_income": round(credits / months, 2) if len(t) else 0.0,
                     "balance_cv": round(float(bal.std() / bal.mean()), 4) if len(bal) > 2 and bal.mean() > 0 else None,
                     "min_balance": float(bal.min()) if len(bal) else None})
    return rows


def eval_berka(agent, split: str) -> dict:
    rows = R.take(berka_rows(), split)
    cache = R.ItemCache("risk_scoring", "berka-v2")

    def one(r):
        extra = {"loan_amount": r["amount"], "tenure_months": r["duration"], "monthly_income": r["monthly_income"]}
        return _score(agent, {"loan": {"loan_type": "Personal Loan"}, "documents": {}}, extra)
    out = cache.run(rows, one, label="Berka", every=200)
    for r, o in zip(rows, out):
        o["label"] = r["label"]
    return {"auc": R.auc_ci(out, score=lambda o: o["overall"]),
            "auc_ml_overlay": R.auc_ci([o for o in out if o["ml_p"] is not None], score=lambda o: o["ml_p"]),
            "n": len(out), "defaults": sum(o["label"] for o in out),
            "note": "income = average monthly credits in the 12 months before the loan; amounts in CZK (1990s), "
                    "so absolute thresholds are not comparable — ranking (AUC) is"}
