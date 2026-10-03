"""Train the two-wheeler default model the Risk Scoring agent blends with its rule engine.

Why: on 5,000 held-out real L&T loans the agent's ranking reached AUC 0.595 against 0.645 for a
model trained on L&T, and its existing ML overlay (trained on a teaching dataset) scored 0.515 —
no better than chance. This model learns from real Indian two-wheeler loans instead.

Rules it follows (PROTOCOL.md):
  * trained on the L&T DEV split only (30% of loans, by loan id); the test split is never read here
  * only inputs FinSight actually has in production: amount, vehicle price (LTV), age (DOB from
    PAN/Aadhaar), employment type, existing EMIs, and the bureau summary when one is pulled —
    no branch, supplier or employee ids
  * monotonic constraints, so a worse input can never lower the predicted risk
  * blend weight chosen by 5-fold cross-validation on dev

Writes app/agents/models/two_wheeler_pd/v1/{model.joblib, card.json}.
Run:  python -m eval.real.train_two_wheeler_pd
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from eval.real import core as R
from eval.real import risk_real as RR

OUT = Path(__file__).resolve().parents[2] / "app" / "agents" / "models" / "two_wheeler_pd" / "v1"

def _lt_features(rows):
    from app.agents.impl.underwriting import engine_inputs, applicant_age
    from app.agents.tools import pd_model            # the agent's own feature code: no train/serve drift
    X, y = [], []
    for r in rows:
        case, extra = RR.lt_inputs(r)
        app, cust, _docs, bureau, _d = engine_inputs(case, None, extra)
        f = pd_model.features(app, cust, bureau, applicant_age(case))
        X.append([f[k] for k in pd_model.FEATURES])
        y.append(r["label"])
    return np.array(X, dtype=float), np.array(y)


def main():
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import StratifiedKFold
    import joblib
    from app.agents.tools import pd_model
    rows = R.take(RR.lt_rows().to_dict("records"), "dev")
    X, y = _lt_features(rows)
    params = dict(max_iter=400, learning_rate=0.04, max_leaf_nodes=31, min_samples_leaf=80, l2_regularization=1.0,
                  monotonic_cst=list(pd_model.FEATURES.values()), random_state=0)
    # 5-fold CV on dev: out-of-fold predictions, used to measure the model and to pick the blend weight
    oof = np.zeros(len(y))
    for tr, va in StratifiedKFold(5, shuffle=True, random_state=0).split(X, y):
        m = HistGradientBoostingClassifier(**params).fit(X[tr], y[tr])
        oof[va] = m.predict_proba(X[va])[:, 1]
    cv_auc = roc_auc_score(y, oof)
    model = HistGradientBoostingClassifier(**params).fit(X, y)
    base = float(y.mean())

    # blend weight: rule score + w * points, points = SCALE * (logit(p) - logit(base))
    from app.agents.registry import get_agent
    agent = get_agent("risk_scoring")
    probe = R.sample(rows, 4000, salt="blend")
    idx = {r["id"]: i for i, r in enumerate(rows)}
    rule = []
    for r in probe:
        case, extra = RR.lt_inputs(r)
        rule.append(agent.execute({"case": case, "income": None, "extra": {**extra, "_no_real_model": True}}).payload["overall"])
    rule = np.array(rule)
    p = np.clip(oof[[idx[r["id"]] for r in probe]], 1e-4, 1 - 1e-4)
    yp = np.array([r["label"] for r in probe])
    lg = np.log(p / (1 - p)) - math.log(base / (1 - base))
    scale = float(rule.std() / lg.std())                     # model points get the rule score's spread
    grid = {}
    for w in (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0):
        pts = np.clip(w * scale * lg, -pd_model.MAX_POINTS, pd_model.MAX_POINTS)
        grid[w] = round(roc_auc_score(yp, rule + pts), 4)
    # smallest weight within 0.002 AUC of the best: past that point the rule engine stops counting
    # for little gain, and the score should stay explainable by its dimensions
    best = max(grid.values())
    w_best = min(w for w, a in grid.items() if a >= best - 0.002)

    OUT.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, OUT / "model.joblib")
    card = {"name": "two_wheeler_pd", "version": "v1", "trained_on": "L&T Vehicle Loan Default, train.csv, DEV split only "
            f"(seed {R.SEED}, dev fraction {R.DEV_FRACTION}); the test split was not read",
            "rows": int(len(y)), "default_rate": round(base, 4),
            "features": pd_model.FEATURES,
            "estimator": "sklearn HistGradientBoostingClassifier", "params": {k: v for k, v in params.items() if k != "monotonic_cst"},
            "cv5_auc_dev": round(float(cv_auc), 4), "blend": {"weight": w_best, "scale": round(scale, 4), "max_points": pd_model.MAX_POINTS,
                                                              "selection": "smallest weight within 0.002 of the best dev AUC",
                                                              "base_rate": round(base, 4), "dev_auc_by_weight": grid},
            "use": "Risk Scoring agent, TWO_WHEELER product only: overall = rule score + weight * scale * "
                   "(logit(p) - logit(base_rate)), then FinSight's knock-out rules",
            "limits": ["Indian two-wheeler loans from one lender (L&T, ~2018); other products are unchanged",
                       "no income or tenure in the training data — those still come only from the rule engine",
                       "probability is relative risk, not a calibrated PD for a different portfolio"],
            "sklearn": __import__("sklearn").__version__}
    (OUT / "card.json").write_text(json.dumps(card, indent=1), encoding="utf-8")
    print(json.dumps({k: card[k] for k in ("rows", "default_rate", "cv5_auc_dev", "blend")}, indent=1))


if __name__ == "__main__":
    from eval import common as C
    C.setup_env(provider="ollama", db=False)
    main()
