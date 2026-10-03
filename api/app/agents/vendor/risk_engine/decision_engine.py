"""Approve / refer / reject decision logic."""

DEFAULT_THRESHOLDS = {
    'DEFAULT': {'approve_max': 40, 'reject_min': 70},
    'GOLD': {'approve_max': 50, 'reject_min': 70},
    'PERSONAL': {'approve_max': 30, 'reject_min': 70},
    'MICROFINANCE': {'approve_max': 40, 'reject_min': 60},
    'HOME': {'approve_max': 45, 'reject_min': 70},
    'LOAN_AGAINST_PROPERTY': {'approve_max': 45, 'reject_min': 70},
    'TWO_WHEELER': {'approve_max': 40, 'reject_min': 70},
    'TRACTOR': {'approve_max': 40, 'reject_min': 70},
    'CAR_NEW': {'approve_max': 40, 'reject_min': 70},
    'COMMERCIAL_VEHICLE': {'approve_max': 40, 'reject_min': 70},
    'MSME': {'approve_max': 35, 'reject_min': 65},
    'WORKING_CAPITAL': {'approve_max': 35, 'reject_min': 65},
    'USED_VEHICLE': {'approve_max': 40, 'reject_min': 70},
}


def decide(risk_score, dimensions, product_code, thresholds=None):
    """Determine the loan decision based on risk assessment.

    Args:
        risk_score: composite risk score (0-100)
        dimensions: dict of dimension results with 'score' key
        product_code: product identifier
        thresholds: optional dict with approve_max and/or reject_min

    Returns:
        dict with decision, reason, conditions, aiConfidence
    """
    base = dict(DEFAULT_THRESHOLDS.get(product_code, DEFAULT_THRESHOLDS['DEFAULT']))
    if thresholds:
        base.update(thresholds)
    approve_max = base['approve_max']
    reject_min = base['reject_min']

    conditions = []

    # Override — unconditional rejection
    fraud_score = (dimensions.get('fraud', {}) or {}).get('score', 0)
    if fraud_score >= 80:
        return {
            'decision': 'reject',
            'reason': 'Critical fraud risk detected — application rejected',
            'conditions': ['Fraud score exceeds critical threshold'],
            'aiConfidence': 0.95,
        }

    for dim, data in dimensions.items():
        data = data or {}
        if data.get('score', 0) >= 90:
            return {
                'decision': 'reject',
                'reason': f"{dim.replace('_', ' ')} score ({data['score']}) exceeds critical threshold — application rejected",
                'conditions': [f"{dim.replace('_', ' ')} \u2265 90"],
                'aiConfidence': 0.95,
            }

    # Standard decision
    if risk_score >= reject_min:
        return {
            'decision': 'reject',
            'reason': f"Risk score {risk_score:.1f} exceeds rejection threshold ({reject_min})",
            'conditions': ['High overall risk score'],
            'aiConfidence': 0.9,
        }

    if risk_score <= approve_max:
        return {
            'decision': 'approve',
            'reason': f"Risk score {risk_score:.1f} is within approval threshold (\u2264 {approve_max})",
            'conditions': [],
            'aiConfidence': 0.85,
        }

    elevated = [
        dim.replace('_', ' ') for dim, data in dimensions.items()
        if (data or {}).get('score', 0) >= 50
    ]
    if elevated:
        conditions.append(f"Elevated risk in: {', '.join(elevated)}")
    conditions.append(f"Score {risk_score:.1f} is in refer range ({approve_max}-{reject_min})")

    return {
        'decision': 'refer',
        'reason': f"Risk score {risk_score:.1f} requires manual review ({approve_max} < score < {reject_min})",
        'conditions': conditions,
        'aiConfidence': 0.75,
    }


def resolve_thresholds(product_code, custom=None):
    """Resolve product-specific thresholds with optional custom overrides."""
    base = dict(DEFAULT_THRESHOLDS.get(product_code, DEFAULT_THRESHOLDS['DEFAULT']))
    if custom:
        base.update(custom)
    return base
