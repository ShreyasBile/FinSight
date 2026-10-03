"""12x9 weight matrix for loan risk analysis."""

PRODUCT_CODES = {
    'TWO_WHEELER': 'TWO_WHEELER',
    'TRACTOR': 'TRACTOR',
    'GOLD': 'GOLD',
    'PERSONAL': 'PERSONAL',
    'HOME': 'HOME',
    'MSME': 'MSME',
    'CAR_NEW': 'CAR_NEW',
    'COMMERCIAL_VEHICLE': 'COMMERCIAL_VEHICLE',
    'LOAN_AGAINST_PROPERTY': 'LOAN_AGAINST_PROPERTY',
    'MICROFINANCE': 'MICROFINANCE',
    'WORKING_CAPITAL': 'WORKING_CAPITAL',
    'USED_VEHICLE': 'USED_VEHICLE',
}

PRODUCT_NAMES = {
    'TWO_WHEELER': 'Two Wheeler Loan',
    'TRACTOR': 'Tractor Loan',
    'GOLD': 'Gold Loan',
    'PERSONAL': 'Personal Loan',
    'HOME': 'Home Loan',
    'MSME': 'MSME / Business Loan',
    'CAR_NEW': 'Car Loan (New)',
    'COMMERCIAL_VEHICLE': 'Commercial Vehicle Loan',
    'LOAN_AGAINST_PROPERTY': 'Loan Against Property',
    'MICROFINANCE': 'Microfinance / Rural Loan',
    'WORKING_CAPITAL': 'Working Capital Loan',
    'USED_VEHICLE': 'Used Vehicle Loan',
}

SECURED_PRODUCTS = {
    'TWO_WHEELER', 'TRACTOR', 'GOLD', 'HOME', 'CAR_NEW',
    'COMMERCIAL_VEHICLE', 'LOAN_AGAINST_PROPERTY', 'USED_VEHICLE',
}

DIMENSION_ORDER = [
    'identity', 'credit_history', 'financial_capacity', 'collateral',
    'loan_structure', 'fraud', 'product_segment', 'external_macro', 'compliance',
]

DIMENSION_LABELS = {
    'identity': 'Identity Risk',
    'credit_history': 'Credit History Risk',
    'financial_capacity': 'Financial Capacity Risk',
    'collateral': 'Collateral Risk',
    'loan_structure': 'Loan Structure Risk',
    'fraud': 'Fraud Risk',
    'product_segment': 'Product/Segment Risk',
    'external_macro': 'External/Macro Risk',
    'compliance': 'Compliance Risk',
}

DIMENSION_COLORS = {
    'identity': '#6366f1',
    'credit_history': '#f59e0b',
    'financial_capacity': '#ef4444',
    'collateral': '#8b5cf6',
    'loan_structure': '#3b82f6',
    'fraud': '#dc2626',
    'product_segment': '#10b981',
    'external_macro': '#6b7280',
    'compliance': '#f97316',
}

DEFAULT_WEIGHTS = {
    'TWO_WHEELER': {
        'identity': 0.12, 'credit_history': 0.15, 'financial_capacity': 0.15,
        'collateral': 0.25, 'loan_structure': 0.10, 'fraud': 0.08,
        'product_segment': 0.05, 'external_macro': 0.05, 'compliance': 0.05,
    },
    'TRACTOR': {
        'identity': 0.12, 'credit_history': 0.15, 'financial_capacity': 0.15,
        'collateral': 0.25, 'loan_structure': 0.10, 'fraud': 0.08,
        'product_segment': 0.05, 'external_macro': 0.05, 'compliance': 0.05,
    },
    'GOLD': {
        'identity': 0.15, 'credit_history': 0.10, 'financial_capacity': 0.10,
        'collateral': 0.38, 'loan_structure': 0.07, 'fraud': 0.08,
        'product_segment': 0.05, 'external_macro': 0.03, 'compliance': 0.04,
    },
    'PERSONAL': {
        'identity': 0.12, 'credit_history': 0.22, 'financial_capacity': 0.30,
        'collateral': 0.00, 'loan_structure': 0.12, 'fraud': 0.10,
        'product_segment': 0.06, 'external_macro': 0.04, 'compliance': 0.04,
    },
    'HOME': {
        'identity': 0.12, 'credit_history': 0.15, 'financial_capacity': 0.15,
        'collateral': 0.25, 'loan_structure': 0.10, 'fraud': 0.08,
        'product_segment': 0.05, 'external_macro': 0.05, 'compliance': 0.05,
    },
    'MSME': {
        'identity': 0.10, 'credit_history': 0.20, 'financial_capacity': 0.25,
        'collateral': 0.00, 'loan_structure': 0.10, 'fraud': 0.10,
        'product_segment': 0.18, 'external_macro': 0.04, 'compliance': 0.03,
    },
    'CAR_NEW': {
        'identity': 0.12, 'credit_history': 0.15, 'financial_capacity': 0.15,
        'collateral': 0.25, 'loan_structure': 0.10, 'fraud': 0.08,
        'product_segment': 0.05, 'external_macro': 0.05, 'compliance': 0.05,
    },
    'COMMERCIAL_VEHICLE': {
        'identity': 0.12, 'credit_history': 0.15, 'financial_capacity': 0.15,
        'collateral': 0.25, 'loan_structure': 0.10, 'fraud': 0.08,
        'product_segment': 0.05, 'external_macro': 0.05, 'compliance': 0.05,
    },
    'LOAN_AGAINST_PROPERTY': {
        'identity': 0.12, 'credit_history': 0.15, 'financial_capacity': 0.15,
        'collateral': 0.25, 'loan_structure': 0.10, 'fraud': 0.08,
        'product_segment': 0.05, 'external_macro': 0.05, 'compliance': 0.05,
    },
    'MICROFINANCE': {
        'identity': 0.20, 'credit_history': 0.18, 'financial_capacity': 0.28,
        'collateral': 0.00, 'loan_structure': 0.08, 'fraud': 0.08,
        'product_segment': 0.05, 'external_macro': 0.03, 'compliance': 0.10,
    },
    'WORKING_CAPITAL': {
        'identity': 0.10, 'credit_history': 0.20, 'financial_capacity': 0.25,
        'collateral': 0.00, 'loan_structure': 0.10, 'fraud': 0.10,
        'product_segment': 0.18, 'external_macro': 0.04, 'compliance': 0.03,
    },
    'USED_VEHICLE': {
        'identity': 0.12, 'credit_history': 0.15, 'financial_capacity': 0.15,
        'collateral': 0.25, 'loan_structure': 0.10, 'fraud': 0.08,
        'product_segment': 0.05, 'external_macro': 0.05, 'compliance': 0.05,
    },
}


def get_weights(product_code, custom_weights=None):
    """Retrieve weights for a product, optionally merged with custom overrides."""
    base = dict(DEFAULT_WEIGHTS.get(product_code, {}))
    if not base:
        raise ValueError(f"Unknown product code: {product_code}")
    if not custom_weights:
        return base

    merged = {**base, **custom_weights}
    total = sum(merged.get(d, 0) for d in DIMENSION_ORDER)
    if total <= 0:
        return base
    if abs(total - 1.0) > 0.001:
        for d in DIMENSION_ORDER:
            merged[d] = (merged.get(d, 0) or 0) / total
    return merged
