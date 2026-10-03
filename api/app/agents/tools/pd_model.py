"""Default-risk model trained on real two-wheeler loans, blended into the Risk Scoring agent.

The rule engine (vendor/risk_engine) is explainable but untrained; on 5,000 held-out real loans
(L&T, India) it ranked defaults at AUC 0.595, and its XGBoost overlay — trained on a teaching
dataset — at 0.515. This model is trained on the L&T loans themselves (dev split only; see
eval/real/train_two_wheeler_pd.py and models/two_wheeler_pd/v1/card.json) using only inputs
FinSight has in production, with monotonic constraints so a worse input never lowers risk.

It does not replace the rule engine: it adds `points` to the rule score,
    points = weight * scale * (logit(p) - logit(base_rate)),
so an applicant who looks like the average borrower moves 0, a riskier one up, a safer one down,
by at most MAX_POINTS.
Weight and scale were chosen by cross-validation on the dev split. Products other than
TWO_WHEELER are unaffected.
"""
from __future__ import annotations

import json
import math
import threading
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parents[1] / "models" / "two_wheeler_pd" / "v1"
PRODUCTS = {"TWO_WHEELER"}
MAX_POINTS = 25.0          # the model moves a rule score by at most one decision band

# feature -> monotonic direction (+1 risk rises with it, -1 falls, 0 free)
FEATURES = {"loan_amount": 1, "ltv": 1, "age": 0, "salaried": 0, "cibil": -1, "has_bureau": 0,
            "bureau_accounts": 0, "overdue_accounts": 1, "recent_delinquency": 1, "utilization": 1,
            "history_years": 0, "existing_emi": 1, "enquiries": 1}

_lock = threading.Lock()
_loaded: dict = {}


def features(app: dict, cust: dict, bureau: dict, age: float | None) -> dict:
    """Model inputs from the risk engine's own inputs (engine_inputs in impl/underwriting.py)."""
    nan = float("nan")
    amount = float(app.get("loan_amount") or 0)
    coll = (app.get("collateral") or {}).get("value")
    b = bureau or {}
    cibil = b.get("cibil_score")
    return {"loan_amount": amount,
            "ltv": round(100 * amount / float(coll), 2) if coll else nan,
            "age": float(age) if age else nan,
            "salaried": 1.0 if str(cust.get("employment_type") or "").lower().startswith("salaried") else 0.0,
            "cibil": float(cibil) if cibil is not None else nan,
            "has_bureau": 1.0 if b else 0.0,
            "bureau_accounts": float(b["total_accounts"]) if "total_accounts" in b else nan,
            "overdue_accounts": float(b["default_count"]) if "default_count" in b else nan,
            "recent_delinquency": (1.0 if (b.get("dpd_days") or 0) > 0 else 0.0) if b else nan,
            "utilization": float(b["credit_utilization"]) if "credit_utilization" in b else nan,
            "history_years": float(b["credit_history_years"]) if "credit_history_years" in b else nan,
            "existing_emi": float(cust.get("existing_emis") or 0),
            "enquiries": float(b["enquiry_count"]) if "enquiry_count" in b else nan}


def _load():
    with _lock:
        if "model" not in _loaded:
            card_p, model_p = MODEL_DIR / "card.json", MODEL_DIR / "model.joblib"
            if not (card_p.exists() and model_p.exists()):
                _loaded.update(model=None, card=None)
            else:
                import joblib
                _loaded.update(model=joblib.load(model_p), card=json.loads(card_p.read_text(encoding="utf-8")))
        return _loaded["model"], _loaded["card"]


def score(product_code: str, app: dict, cust: dict, bureau: dict, age: float | None) -> dict | None:
    """{pd, points, weight, version, inputs_missing} for a supported product, else None."""
    if product_code not in PRODUCTS:
        return None
    model, card = _load()
    if model is None:
        return None
    f = features(app, cust, bureau, age)
    p = float(model.predict_proba([[f[k] for k in FEATURES]])[0][1])
    p = min(max(p, 1e-4), 1 - 1e-4)
    bl = card["blend"]
    base = bl["base_rate"]
    pts = bl["weight"] * bl["scale"] * (math.log(p / (1 - p)) - math.log(base / (1 - base)))
    pts = max(-MAX_POINTS, min(MAX_POINTS, pts))
    return {"pd": round(p, 4), "points": round(pts, 2), "weight": bl["weight"], "version": card["version"],
            "inputs_missing": [k for k, v in f.items() if v != v]}       # NaN != NaN
