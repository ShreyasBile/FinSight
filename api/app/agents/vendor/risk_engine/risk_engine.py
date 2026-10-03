"""Core scoring pipeline: extract params -> score dimensions -> apply weights -> overrides -> grade -> explain."""

import math
from .weight_matrix import get_weights, DIMENSION_LABELS, DIMENSION_ORDER
from .param_extractors import extract_params

GRADE_MAP = [
    (15, 'A+'), (25, 'A'), (35, 'B+'), (45, 'B'),
    (55, 'C+'), (70, 'C'), (100, 'D'),
]


def _to_grade(score):
    for mx, grade in GRADE_MAP:
        if score <= mx:
            return grade
    return 'D'


def _default_probability(score):
    return 1 / (1 + math.exp(-0.06 * (score - 40)))


def calculate_risk_score(application=None, customer_data=None, documents=None,
                         bureau_data=None, custom_weights=None):
    """Calculate the full risk score for a loan application.

    Args:
        application: dict with productCode, loanAmount, tenureMonths, etc.
        customer_data: dict with name, monthlyIncome, etc.
        documents: list of dicts with type, verified, tamperScore, status
        bureau_data: dict with cibilScore, dpdDays, etc.
        custom_weights: optional per-product weight overrides

    Returns:
        dict with overall, grade, defaultProbability, dimensions, overrides, explanation
    """
    app = application or {}
    cust = customer_data or {}
    docs = documents or []
    bureau = bureau_data or {}
    product_code = app.get('productCode', 'PERSONAL')
    weights = get_weights(product_code, custom_weights)

    params = extract_params(product_code, app, cust, docs, bureau)

    dimensions = {}
    overall_score = 0.0
    overrides = []

    for dim in DIMENSION_ORDER:
        param = params.get(dim, {'value': 50, 'confidence': 0, 'details': ['Data unavailable']})
        weight = weights.get(dim, 0)
        raw_score = param['value']
        weighted_contrib = raw_score * weight
        overall_score += weighted_contrib

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

    # Critical overrides
    override_reasons = []
    for dim in DIMENSION_ORDER:
        dim_score = dimensions[dim]['score']
        if dim_score >= 90:
            override_reasons.append({
                'dimension': dim,
                'reason': f"{DIMENSION_LABELS[dim]} score is {dim_score} (>= 90) — critical threshold exceeded",
                'original_score': overall_score,
                'adjusted_score': max(overall_score, 85),
            })

    if dimensions.get('fraud', {}).get('score', 0) >= 80:
        override_reasons.append({
            'dimension': 'fraud',
            'reason': f"Fraud score is {dimensions['fraud']['score']} — critical fraud risk detected",
            'original_score': overall_score,
            'adjusted_score': max(overall_score, 85),
        })

    cibil = (bureau or {}).get('cibil_score')
    if cibil is not None and cibil < 500:
        override_reasons.append({
            'dimension': 'credit_history',
            'reason': f"CIBIL score {cibil} is below 500",
            'original_score': overall_score,
            'adjusted_score': max(overall_score, 85),
        })

    final_score = overall_score
    for ov in override_reasons:
        if ov['adjusted_score'] > final_score:
            final_score = ov['adjusted_score']
        overrides.append(ov)

    grade = _to_grade(final_score)
    def_prob = _default_probability(final_score)

    explanation = _generate_explanation(product_code, final_score, grade, dimensions, overrides)

    return {
        'overall': round(final_score, 2),
        'grade': grade,
        'defaultProbability': round(def_prob, 4),
        'dimensions': dimensions,
        'overrides': overrides,
        'explanation': explanation,
    }


def _generate_explanation(product_code, score, grade, dimensions, overrides):
    parts = []
    parts.append(f"Risk assessment for {product_code}: overall score {score:.1f}/100 (grade {grade}).")

    high_dims = [(dim, d) for dim, d in dimensions.items() if d['score'] >= 50]
    high_dims.sort(key=lambda x: -x[1]['score'])

    if high_dims:
        top = high_dims[:3]
        risk_str = '; '.join(
            f"{dim.replace('_', ' ')} ({d['score']:.0f}, weight {d['weight'] * 100:.0f}%)"
            for dim, d in top
        )
        parts.append(f"Top risk drivers: {risk_str}.")

    if overrides:
        parts.append('Critical overrides applied:')
        for o in overrides:
            parts.append(f"  - {o['reason']}")

    return ' '.join(parts)
