"""
ML-powered credit risk scorer using a trained XGBoost model.
Maps our application data to the HMEQ feature space and returns
risk scores and default probabilities.

Model trained on HMEQ dataset (Home Equity, 5960 loans)
AUC-ROC: 0.9485
"""

import os
import json
import warnings
import numpy as np
import pandas as pd
import joblib

warnings.filterwarnings('ignore')

MODEL_DIR = os.path.join(os.path.dirname(__file__), 'model')   # vendored copy (FinSight)

# Pickles execute code when loaded, so each model file must match the hash
# recorded when it was vendored (PROVENANCE.sha256) before joblib touches it.
def _verify(path):
    import hashlib
    prov = os.path.join(os.path.dirname(__file__), 'PROVENANCE.sha256')
    want = {}
    for line in open(prov, encoding='utf-8'):
        h, _, name = line.strip().partition(' *')
        want[name] = h
    rel = 'model/' + os.path.basename(path)
    got = hashlib.sha256(open(path, 'rb').read()).hexdigest()
    if want.get(rel) != got:
        raise RuntimeError(f'model file {rel} does not match its recorded hash — refusing to load')


class MLScorer:
    """Loads a trained XGBoost model and scores loan applications.
    
    Supports two model sources:
      - 'hmeq' (default): US Home Equity dataset (AUC 0.9485)
      - 'indian': Indian NBFC dataset (AUC ~0.7520)
    """

    def __init__(self, model_type='hmeq'):
        self.model = None
        self.scaler = None
        self.features = None
        self._loaded = False
        self.model_type = model_type
        self.model_name = ''

    def load_model(self, model_type=None):
        if model_type:
            self.model_type = model_type

        if self.model_type == 'indian':
            model_path = os.path.join(MODEL_DIR, 'indian_xgb_model.pkl')
            scaler_path = os.path.join(MODEL_DIR, 'indian_scaler.pkl')
            meta_path = os.path.join(MODEL_DIR, 'indian_model_meta.json')
            default_name = 'Indian NBFC XGBoost'
        else:
            model_path = os.path.join(MODEL_DIR, 'xgb_model.pkl')
            scaler_path = os.path.join(MODEL_DIR, 'scaler.pkl')
            meta_path = os.path.join(MODEL_DIR, 'model_meta.json')
            default_name = 'HMEQ (US) XGBoost'

        if not os.path.exists(model_path):
            return False

        _verify(model_path)
        _verify(scaler_path)
        self.model = joblib.load(model_path)
        self.scaler = joblib.load(scaler_path)

        if os.path.exists(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
                self.features = meta.get('features', [
                    'LOAN', 'MORTDUE', 'VALUE', 'YOJ', 'DEROG', 'DELINQ',
                    'CLAGE', 'CLNO', 'DEBTINC', 'REASON', 'JOB',
                ])
            self.model_name = meta.get('dataset', default_name)
        else:
            self.features = [
                'LOAN', 'MORTDUE', 'VALUE', 'YOJ', 'DEROG', 'DELINQ',
                'CLAGE', 'CLNO', 'DEBTINC', 'REASON', 'JOB',
            ]
            self.model_name = default_name

        self._loaded = True
        return True

    @property
    def is_loaded(self):
        return self._loaded

    def predict(self, application, customer_data, bureau_data):
        """Return (risk_score_0_100, default_probability) or (None, None)."""
        if not self._loaded and not self.load_model():
            return None, None

        features = self._extract_features(application, customer_data, bureau_data)
        X = pd.DataFrame([features])

        # Ensure columns match training order
        X = X[self.features] if self.features else X

        X_s = self.scaler.transform(X)
        proba = float(self.model.predict_proba(X_s)[0, 1])
        risk_score = proba * 100

        return risk_score, proba

    def _extract_features(self, app, cust, bureau):
        app = app or {}
        cust = cust or {}
        bureau = bureau or {}

        income = cust.get('monthly_income', 0) or 0
        expenses = cust.get('monthly_expenses', 0) or 0
        emis = cust.get('existing_emis', 0) or 0
        total_obligations = expenses + emis
        dti = (total_obligations / income * 100) if income > 0 else 50.0

        coll = app.get('collateral') or {}

        return {
            'LOAN': float(app.get('loanAmount', 50000) or 50000),
            'MORTDUE': 0.0,
            'VALUE': float(coll.get('value', app.get('loanAmount', 50000) or 50000)),
            'YOJ': float(cust.get('years_in_job', 5) or 5),
            'DEROG': float(bureau.get('default_count', 0) or 0),
            'DELINQ': float(bureau.get('dpd_days', 0) or 0),
            'CLAGE': float(bureau.get('credit_history_years', 5) or 5) * 12,
            'CLNO': float(bureau.get('total_accounts', 5) or 5),
            'DEBTINC': dti,
            'REASON': self._map_reason((app.get('purpose', '') or '')),
            'JOB': self._map_job((cust.get('employment_type', '') or '')),
        }

    def _map_reason(self, purpose):
        p = purpose.lower()
        if 'debt' in p or 'consolid' in p:
            return 0  # 'DebtCon'
        return 1      # 'HomeImp'

    def _map_job(self, emp_type):
        mapping = {
            'salaried': 1,       # 'Office'
            'self_employed': 5,  # 'Self'
            'business': 5,       # 'Self'
            'agriculture': 2,    # 'Other'
            '': 2,               # 'Other'
        }
        return mapping.get(emp_type, 2)

    def score_dimensions(self, application, customer_data, documents, bureau_data):
        """Return 9 dimension params with ML scores overriding financial/credit."""
        from .param_extractors import extract_params

        product_code = (application or {}).get('productCode', 'PERSONAL')
        params = extract_params(product_code, application, customer_data, documents, bureau_data)

        risk_score, proba = self.predict(application, customer_data, bureau_data)

        if risk_score is not None:
            params['financial_capacity'] = {
                'value': min(risk_score * 0.40, 100),
                'confidence': 0.85,
                'details': [
                    f'ML model predicted risk score: {risk_score:.1f}/100',
                    f'Default probability: {proba * 100:.1f}%',
                ],
            }
            params['credit_history'] = {
                'value': min(risk_score * 0.35, 100),
                'confidence': 0.85,
                'details': [
                    f'ML model predicted risk score: {risk_score:.1f}/100',
                ],
            }

        return params


# Singleton for reuse (one per model type)
_scorers = {}


def get_ml_scorer(model_type='hmeq'):
    global _scorers
    if model_type not in _scorers:
        s = MLScorer(model_type)
        s.load_model()
        _scorers[model_type] = s
    return _scorers[model_type]


def calculate_ml_risk_score(application=None, customer_data=None, documents=None,
                            bureau_data=None, custom_weights=None, model_type='hmeq'):
    """Full risk score using ML model for financial/credit dimensions,
    rule-based for operational dimensions.

    Args:
        model_type: 'hmeq' (default) or 'indian'
    
    Returns the same shape as calculate_risk_score().
    """
    from .risk_engine import calculate_risk_score
    from .weight_matrix import get_weights, DIMENSION_ORDER, DIMENSION_LABELS

    import math

    app = application or {}
    cust = customer_data or {}
    docs = documents or []
    bureau = bureau_data or {}
    product_code = app.get('productCode', 'PERSONAL')

    scorer = get_ml_scorer(model_type)
    ml_params = scorer.score_dimensions(app, cust, docs, bureau)

    weights = get_weights(product_code, custom_weights)

    dimensions = {}
    overall_score = 0.0
    overrides = []

    for dim in DIMENSION_ORDER:
        param = ml_params.get(dim, {'value': 50, 'confidence': 0, 'details': ['Data unavailable']})
        weight = weights.get(dim, 0)
        raw_score = param['value']
        overall_score += raw_score * weight

        factors = []
        for d in param['details']:
            lower = d.lower()
            is_neg = any(kw in lower for kw in [
                'high', 'low', 'missing', 'old', 'tamper', 'expired',
                'suspicious', 'violation', 'above', 'inconsistent',
            ])
            factors.append({
                'name': d.split(':')[0].strip(),
                'value': raw_score,
                'impact': 'negative' if is_neg else 'positive',
                'details': d,
            })

        dimensions[dim] = {
            'score': raw_score,
            'weight': weight,
            'contribution': raw_score * weight / 100,
            'confidence': param['confidence'],
            'factors': factors,
        }

    # Critical overrides (same as rule-based)
    for dim in DIMENSION_ORDER:
        dim_score = dimensions[dim]['score']
        if dim_score >= 90:
            overrides.append({
                'dimension': dim,
                'reason': f"{DIMENSION_LABELS[dim]} score is {dim_score:.0f} (>= 90)",
                'original_score': overall_score,
                'adjusted_score': max(overall_score, 85),
            })

    if dimensions.get('fraud', {}).get('score', 0) >= 80:
        overrides.append({
            'dimension': 'fraud',
            'reason': f"Fraud score is {dimensions['fraud']['score']:.0f}",
            'original_score': overall_score,
            'adjusted_score': max(overall_score, 85),
        })

    cibil = (bureau or {}).get('cibil_score')
    if cibil is not None and cibil < 500:
        overrides.append({
            'dimension': 'credit_history',
            'reason': f"CIBIL score {cibil} is below 500",
            'original_score': overall_score,
            'adjusted_score': max(overall_score, 85),
        })

    final_score = overall_score
    for ov in overrides:
        if ov['adjusted_score'] > final_score:
            final_score = ov['adjusted_score']

    GRADE_MAP = [(15, 'A+'), (25, 'A'), (35, 'B+'), (45, 'B'),
                 (55, 'C+'), (70, 'C'), (100, 'D')]
    grade = 'D'
    for mx, g in GRADE_MAP:
        if final_score <= mx:
            grade = g
            break

    def_prob = 1 / (1 + math.exp(-0.06 * (final_score - 40)))

    parts = [f"Risk assessment for {product_code} (ML model): overall score {final_score:.1f}/100 (grade {grade})."]
    high_dims = [(d, v) for d, v in dimensions.items() if v['score'] >= 50]
    high_dims.sort(key=lambda x: -x[1]['score'])
    if high_dims:
        top = high_dims[:3]
        parts.append("Top risk drivers: " + "; ".join(
            f"{d.replace('_', ' ')} ({v['score']:.0f}, weight {v['weight'] * 100:.0f}%)" for d, v in top
        ) + ".")
    if overrides:
        parts.append("Critical overrides applied:")
        for o in overrides:
            parts.append(f"  - {o['reason']}")

    return {
        'overall': round(final_score, 2),
        'grade': grade,
        'defaultProbability': round(def_prob, 4),
        'dimensions': dimensions,
        'overrides': overrides,
        'explanation': ' '.join(parts),
    }
