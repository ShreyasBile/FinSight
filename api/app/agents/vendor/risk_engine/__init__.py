"""Loan Risk Analysis Engine — vendored into FinSight from
D:/novacinternship/projects/risk analysis 2/risk_engine_py (2026-09-25).

Copied (not imported from the sibling path) so FinSight has no absolute-path
dependency; file hashes are pinned in PROVENANCE.sha256 and the pickled models
are verified against them before loading. The Risk Scoring agent reframes the
engine's 'reject' decision as 'decline review recommended' — nothing in
FinSight auto-rejects.
"""

from .weight_matrix import (
    PRODUCT_CODES, PRODUCT_NAMES, SECURED_PRODUCTS,
    DIMENSION_ORDER, DIMENSION_LABELS, DIMENSION_COLORS,
    DEFAULT_WEIGHTS, get_weights,
)
from .param_extractors import extract_params
from .risk_engine import calculate_risk_score
from .decision_engine import decide, DEFAULT_THRESHOLDS, resolve_thresholds
from .pricing_engine import get_rate, BASE_RATES, PREMIUM_FACTORS, MEDIAN_TENURE
from .ml_scorer import calculate_ml_risk_score, get_ml_scorer, MLScorer


def run_full_pipeline(application=None, customer_data=None, documents=None,
                      bureau_data=None, custom_weights=None,
                      decision_thresholds=None, base_rate_override=None):
    """Full pipeline: extract -> score -> decide -> price."""
    product_code = (application or {}).get('productCode')

    risk = calculate_risk_score(
        application=application, customer_data=customer_data,
        documents=documents, bureau_data=bureau_data,
        custom_weights=custom_weights,
    )
    decision = decide(risk['overall'], risk['dimensions'], product_code, decision_thresholds)
    pricing_params = {}
    app = application or {}
    if app.get('tenure_months') is not None:
        pricing_params['tenure_months'] = app['tenure_months']
    if app.get('loan_amount') is not None:
        pricing_params['loan_amount'] = app['loan_amount']
    pricing = get_rate(product_code, risk['overall'], base_rate_override, pricing_params)

    return {'risk': risk, 'decision': decision, 'pricing': pricing}
