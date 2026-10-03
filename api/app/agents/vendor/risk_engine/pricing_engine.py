"""Risk-adjusted interest rate pricing with EMI and total interest calculation."""

BASE_RATES = {
    'TWO_WHEELER': 12, 'TRACTOR': 11, 'GOLD': 9, 'PERSONAL': 15,
    'HOME': 8.5, 'MSME': 14, 'CAR_NEW': 10, 'COMMERCIAL_VEHICLE': 12,
    'LOAN_AGAINST_PROPERTY': 10.5, 'MICROFINANCE': 16,
    'WORKING_CAPITAL': 14.5, 'USED_VEHICLE': 13,
}

MEDIAN_TENURE = {
    'TWO_WHEELER': 36, 'TRACTOR': 48, 'GOLD': 12, 'PERSONAL': 36,
    'HOME': 240, 'MSME': 60, 'CAR_NEW': 60, 'COMMERCIAL_VEHICLE': 60,
    'LOAN_AGAINST_PROPERTY': 120, 'MICROFINANCE': 18,
    'WORKING_CAPITAL': 6, 'USED_VEHICLE': 48,
}

PREMIUM_FACTORS = {
    'TWO_WHEELER': 0.04, 'TRACTOR': 0.04, 'GOLD': 0.03, 'PERSONAL': 0.06,
    'HOME': 0.035, 'MSME': 0.05, 'CAR_NEW': 0.04, 'COMMERCIAL_VEHICLE': 0.045,
    'LOAN_AGAINST_PROPERTY': 0.035, 'MICROFINANCE': 0.05,
    'WORKING_CAPITAL': 0.05, 'USED_VEHICLE': 0.045,
}


def _calculate_emi(principal, annual_rate, tenure_months):
    """Calculate EMI using the standard formula."""
    if annual_rate <= 0 or tenure_months <= 0:
        return 0
    monthly_rate = annual_rate / 12 / 100
    factor = (1 + monthly_rate) ** tenure_months
    return principal * monthly_rate * factor / (factor - 1)


def get_rate(product_code, risk_score, base_rate=None, params=None):
    """Calculate the risk-adjusted interest rate.

    Args:
        product_code: product identifier
        risk_score: composite risk score (0-100)
        base_rate: override base rate; uses default if None
        params: optional dict with tenure_months, loan_amount, premium_factor

    Returns:
        dict with baseRate, riskPremium, tenureAdjustment, finalRate,
              effectiveMonthlyEMI, totalInterestPayable
    """
    params = params or {}
    default_base = BASE_RATES.get(product_code, 12)
    effective_base = base_rate if base_rate is not None else default_base
    premium_factor = params.get('premium_factor', PREMIUM_FACTORS.get(product_code, 0.04))
    tenure_months = params.get('tenure_months', 12)
    loan_amount = params.get('loan_amount', 100000)

    # Risk premium from band adjustment
    if risk_score < 25:
        band_adjustment = -0.5
    elif risk_score < 55:
        band_adjustment = 0 + (risk_score - 25) * (1.5 / 30)
    elif risk_score < 75:
        band_adjustment = 1.5 + (risk_score - 55) * (1.5 / 20)
    else:
        band_adjustment = 3.0 + (risk_score - 75) * (2.0 / 25)

    # Tenure adjustment: +0.1% per year above product median
    median_months = MEDIAN_TENURE.get(product_code, 36)
    years_above = max(0, (tenure_months - median_months) / 12)
    tenure_adjustment = years_above * 0.1

    final_rate = max(0, effective_base + band_adjustment + tenure_adjustment)

    effective_monthly_emi = _calculate_emi(loan_amount, final_rate, tenure_months)
    total_payment = effective_monthly_emi * tenure_months
    total_interest = total_payment - loan_amount

    return {
        'baseRate': effective_base,
        'riskPremium': round(band_adjustment, 2),
        'tenureAdjustment': round(tenure_adjustment, 2),
        'finalRate': round(final_rate, 2),
        'effectiveMonthlyEMI': round(effective_monthly_emi, 2),
        'totalInterestPayable': round(total_interest, 2),
    }
