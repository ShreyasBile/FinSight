"""Per-product parameter extraction for all 9 risk dimensions."""

import math


def _missing():
    return {'value': 50, 'confidence': 0, 'details': ['Data unavailable']}


def _clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def _extract_identity(product_code, app, cust, docs, bureau):
    if not cust or not docs:
        return _missing()
    details = []

    if not cust.get('pan') or not cust.get('aadhaar'):
        return {'value': 80, 'confidence': 0.6, 'details': ['Missing KYC documents']}

    verified = [d for d in docs if d.get('verified')]
    tampered = [d for d in docs if (d.get('tamper_score') or 0) > 50]
    expired = [d for d in docs if d.get('status') == 'expired']

    score = 10
    total = len(docs) or 1

    if len(verified) < total:
        score += 20 * (1 - len(verified) / total)
        details.append(f"{total - len(verified)} unverified document(s)")

    if tampered:
        score += 30
        details.append(f"{len(tampered)} tampered document(s)")

    if expired:
        score += 15
        details.append(f"{len(expired)} expired document(s)")

    if product_code == 'GOLD':
        if not (app or {}).get('collateral', {}).get('purity'):
            score += 10
            details.append('Gold hallmark/purity not provided')

    if product_code == 'MICROFINANCE':
        if not cust.get('address') and not cust.get('city'):
            score += 15
            details.append('Incomplete address verification')

    confidence = _clamp(0.5 + len(verified) / total * 0.5, 0, 1) if docs else 0.3
    return {'value': _clamp(score), 'confidence': confidence, 'details': details}


def _extract_credit_history(product_code, app, cust, docs, bureau):
    if not bureau or bureau.get('cibil_score') is None:
        return _missing()
    details = []
    cibil = bureau['cibil_score']

    if cibil >= 800:
        score = 5
    elif cibil >= 750:
        score = 15
    elif cibil >= 700:
        score = 30
    elif cibil >= 650:
        score = 50
    elif cibil >= 600:
        score = 65
    elif cibil >= 500:
        score = 80
    else:
        score = 95

    details.append(f"CIBIL score: {cibil}")

    dpd = bureau.get('dpd_days', 0)
    if dpd > 0:
        score += _clamp(dpd / 30 * 5, 0, 15)
        details.append(f"DPD: {dpd} days")

    defaults = bureau.get('default_count', 0)
    if defaults > 0:
        score += _clamp(defaults * 10, 0, 20)
        details.append(f"{defaults} past default(s)")

    util = bureau.get('credit_utilization', 0)
    if util > 80:
        score += 10
        details.append(f"High credit utilization: {util}%")

    return {'value': _clamp(score), 'confidence': 0.8, 'details': details}


def _extract_financial_capacity(product_code, app, cust, docs, bureau):
    if not cust or not app:
        return _missing()
    details = []
    score = 10

    income = cust.get('monthly_income', 0)
    if income <= 0:
        return {'value': 70, 'confidence': 0.3, 'details': ['No income data provided']}

    expenses = cust.get('monthly_expenses', 0)
    existing_emis = cust.get('existing_emis', 0)
    total_obligations = cust.get('total_monthly_obligations', expenses + existing_emis)
    dti = total_obligations / income

    if dti > 0.6:
        score += 35
        details.append(f"High DTI: {dti * 100:.0f}%")
    elif dti > 0.4:
        score += 20
        details.append(f"Moderate DTI: {dti * 100:.0f}%")
    else:
        details.append(f"Healthy DTI: {dti * 100:.0f}%")

    monthly_rate = 0.10 / 12
    tenure = app.get('tenure_months', 12)
    amount = app.get('loan_amount', 0)
    if tenure > 0 and monthly_rate > 0:
        emi = amount * monthly_rate * (1 + monthly_rate) ** tenure / ((1 + monthly_rate) ** tenure - 1)
    else:
        emi = 0
    emi_ratio = emi / income if income > 0 else 1

    if emi_ratio > 0.5:
        score += 25
        details.append(f"EMI/Income ratio high: {emi_ratio * 100:.0f}%")
    elif emi_ratio > 0.3:
        score += 10
        details.append(f"EMI/Income: {emi_ratio * 100:.0f}%")
    else:
        details.append(f"EMI/Income manageable: {emi_ratio * 100:.0f}%")

    years_job = cust.get('years_in_job')
    if years_job is not None and years_job < 1:
        score += 10
        details.append('Employment tenure < 1 year')

    bal_var = cust.get('bank_balance_variance')
    if bal_var is not None and bal_var > 0.5:
        score += 10
        details.append('High bank balance variance')

    unsecured = ['PERSONAL', 'MSME', 'MICROFINANCE', 'WORKING_CAPITAL']
    if product_code in unsecured:
        if dti > 0.4:
            score += 10
        if emi_ratio > 0.35:
            score += 10

    confidence = 0.7 if income > 0 else 0.3
    return {'value': _clamp(score), 'confidence': confidence, 'details': details}


def _extract_collateral(product_code, app, cust, docs, bureau):
    unsecured = ['PERSONAL', 'MSME', 'MICROFINANCE', 'WORKING_CAPITAL']
    if product_code in unsecured:
        return {'value': 0, 'confidence': 1, 'details': ['No collateral required (unsecured product)']}

    coll = (app or {}).get('collateral')
    if not coll:
        return _missing()
    details = []
    score = 10

    coll_value = coll.get('value', 0)
    loan_amount = app.get('loan_amount', 0)
    ltv = loan_amount / coll_value if coll_value > 0 else 1

    if ltv > 0.9:
        score += 40
        details.append(f"High LTV: {ltv * 100:.0f}%")
    elif ltv > 0.75:
        score += 25
        details.append(f"Elevated LTV: {ltv * 100:.0f}%")
    elif ltv > 0.6:
        score += 10
        details.append(f"Moderate LTV: {ltv * 100:.0f}%")
    else:
        details.append(f"Low LTV: {ltv * 100:.0f}%")

    if product_code == 'GOLD':
        if not coll.get('purity'):
            score += 15
            details.append('Gold purity not assessed')
        else:
            details.append(f"Gold purity: {coll['purity']}")
        if not coll.get('weight_grams'):
            score += 10
            details.append('Gold weight not provided')
        else:
            details.append(f"Gold weight: {coll['weight_grams']}g")
        score = max(0, score - 5)
        details.append('Gold is highly liquid collateral')

    if product_code in ('HOME', 'LOAN_AGAINST_PROPERTY'):
        pt = coll.get('property_type')
        if pt:
            details.append(f"Property type: {pt}")
        loc = coll.get('location')
        if loc:
            if loc in ('tier3', 'rural'):
                score += 10
                details.append('Lower-tier location')
            else:
                details.append(f"Location: {loc}")

    vehicle_codes = {'TWO_WHEELER', 'CAR_NEW', 'COMMERCIAL_VEHICLE', 'USED_VEHICLE', 'TRACTOR'}
    if product_code in vehicle_codes:
        age = coll.get('vehicle_age_years')
        if age is not None:
            if age > 10:
                score += 20
                details.append(f"Old vehicle: {age} years")
            elif age > 5:
                score += 10
                details.append(f"Vehicle age: {age} years")
            else:
                details.append(f"Vehicle age: {age} years (new)")

    confidence = 0.75 if coll_value > 0 else 0.4
    return {'value': _clamp(score), 'confidence': confidence, 'details': details}


def _extract_loan_structure(product_code, app, cust, docs, bureau):
    if not app or not cust:
        return _missing()
    details = []
    score = 10

    amount = app.get('loan_amount', 0)
    tenure = app.get('tenure_months', 0)
    income = cust.get('monthly_income', 0)

    if income > 0:
        ratio = amount / income
        if ratio > 60:
            score += 30
            details.append(f"Loan is {ratio:.0f}x monthly income")
        elif ratio > 36:
            score += 15
            details.append(f"Loan is {ratio:.0f}x monthly income")
        else:
            details.append(f"Loan-to-income ratio: {ratio:.0f}x")

    product_tenure = {
        'TWO_WHEELER': (12, 60), 'TRACTOR': (12, 84),
        'GOLD': (3, 36), 'PERSONAL': (6, 60),
        'HOME': (60, 360), 'MSME': (12, 120),
        'CAR_NEW': (12, 84), 'COMMERCIAL_VEHICLE': (12, 96),
        'LOAN_AGAINST_PROPERTY': (12, 240),
        'MICROFINANCE': (6, 36), 'WORKING_CAPITAL': (3, 12),
        'USED_VEHICLE': (12, 72),
    }
    tmin, tmax = product_tenure.get(product_code, (0, 0))
    if tmin and tenure > 0:
        if tenure < tmin:
            score += 15
            details.append(f"Tenure below product minimum ({tmin}m)")
        elif tenure > tmax:
            score += 15
            details.append(f"Tenure above product maximum ({tmax}m)")
        else:
            details.append(f"Tenure within product range ({tmin}-{tmax}m)")

    return {'value': _clamp(score), 'confidence': 0.6, 'details': details}


def _extract_fraud(product_code, app, cust, docs, bureau):
    if not docs:
        return _missing()
    details = []
    score = 5

    tamper_scores = [d.get('tamper_score', 0) for d in docs]
    max_tamper = max(tamper_scores) if tamper_scores else 0
    avg_tamper = sum(tamper_scores) / len(tamper_scores) if tamper_scores else 0

    if max_tamper > 80:
        score += 40
        details.append(f"Critical tampering detected (score: {max_tamper})")
    elif max_tamper > 50:
        score += 25
        details.append(f"Significant tampering (score: {max_tamper})")
    elif avg_tamper > 20:
        score += 10
        details.append(f"Minor tampering signs (avg: {avg_tamper:.0f})")

    suspicious = [d for d in docs if d.get('status') == 'suspicious']
    if suspicious:
        score += 20
        details.append(f"{len(suspicious)} suspicious document(s)")

    income = (cust or {}).get('monthly_income', 0)
    loan_amt = (app or {}).get('loan_amount', 0)
    if income > 0 and loan_amt > 0 and loan_amt / income > 100:
        score += 20
        details.append('Loan amount inconsistent with income profile')

    confidence = 0.7 if docs else 0.3
    return {'value': _clamp(score), 'confidence': confidence, 'details': details}


def _extract_product_segment(product_code, app, cust, docs, bureau):
    details = []
    score = 30

    sector_risk = {
        'AGRICULTURE': 40, 'MANUFACTURING': 35, 'CONSTRUCTION': 45, 'RETAIL': 30,
        'TECHNOLOGY': 20, 'HEALTHCARE': 25, 'EDUCATION': 25, 'HOSPITALITY': 40,
        'TRANSPORT': 35, 'REAL_ESTATE': 40, 'FINANCIAL_SERVICES': 30,
    }

    business = (app or {}).get('business', {})
    industry = business.get('industry')
    if industry:
        risk = sector_risk.get(industry.upper(), 30)
        score = risk
        details.append(f"Industry: {industry} (risk: {risk})")
        yrs = business.get('years_in_operation')
        if yrs is not None and yrs < 2:
            score += 10
            details.append('Business < 2 years old')
    else:
        details.append('Industry not specified, using default risk')

    segment_risk_map = {
        'MICROFINANCE': 10, 'MSME': 15, 'WORKING_CAPITAL': 15,
        'GOLD': 5, 'PERSONAL': 20, 'HOME': 10,
        'TWO_WHEELER': 15, 'TRACTOR': 10, 'CAR_NEW': 15,
        'COMMERCIAL_VEHICLE': 20, 'LOAN_AGAINST_PROPERTY': 10, 'USED_VEHICLE': 20,
    }
    sr = segment_risk_map.get(product_code)
    if sr is not None:
        score = (score + sr) / 2
        details.append('Product segment adjustment applied')

    return {'value': _clamp(score), 'confidence': 0.5, 'details': details}


def _extract_external_macro(product_code, app, cust, docs, bureau):
    return {'value': 50, 'confidence': 0, 'details': ['No macro-economic data provided — using neutral baseline']}


def _extract_compliance(product_code, app, cust, docs, bureau):
    if not docs:
        return _missing()
    details = []
    score = 10

    required_docs = {
        'TWO_WHEELER': ['pan', 'aadhaar', 'income_proof'],
        'TRACTOR': ['pan', 'aadhaar', 'income_proof'],
        'GOLD': ['pan', 'aadhaar'],
        'PERSONAL': ['pan', 'aadhaar', 'income_proof', 'bank_statement'],
        'HOME': ['pan', 'aadhaar', 'income_proof', 'property_doc', 'bank_statement'],
        'MSME': ['pan', 'aadhaar', 'income_proof', 'bank_statement'],
        'CAR_NEW': ['pan', 'aadhaar', 'income_proof'],
        'COMMERCIAL_VEHICLE': ['pan', 'aadhaar', 'income_proof'],
        'LOAN_AGAINST_PROPERTY': ['pan', 'aadhaar', 'income_proof', 'property_doc'],
        'MICROFINANCE': ['pan', 'aadhaar'],
        'WORKING_CAPITAL': ['pan', 'aadhaar', 'income_proof', 'bank_statement'],
        'USED_VEHICLE': ['pan', 'aadhaar', 'income_proof'],
    }

    needed = required_docs.get(product_code, ['pan', 'aadhaar'])
    present = {d.get('type') for d in docs}
    missing = [t for t in needed if t not in present]

    if missing:
        score += len(missing) * 15
        details.append(f"Missing documents: {', '.join(missing)}")

    loan_amt = (app or {}).get('loan_amount', 0)
    income = (cust or {}).get('monthly_income', 0)
    if loan_amt > 0 and income > 0:
        ratio = loan_amt / income
        unsecured = ['PERSONAL', 'MSME', 'MICROFINANCE', 'WORKING_CAPITAL']
        if product_code in unsecured and ratio > 48:
            score += 20
            details.append('Loan amount exceeds policy limit for unsecured product')

    confidence = 0.7 if docs else 0.3
    return {'value': _clamp(score), 'confidence': confidence, 'details': details}


_EXTRACTORS = {
    'identity': _extract_identity,
    'credit_history': _extract_credit_history,
    'financial_capacity': _extract_financial_capacity,
    'collateral': _extract_collateral,
    'loan_structure': _extract_loan_structure,
    'fraud': _extract_fraud,
    'product_segment': _extract_product_segment,
    'external_macro': _extract_external_macro,
    'compliance': _extract_compliance,
}


def extract_params(product_code, application, customer_data, documents, bureau_data):
    """Extract all 9 risk dimension parameters."""
    result = {}
    for dim, fn in _EXTRACTORS.items():
        result[dim] = fn(product_code, application, customer_data, documents, bureau_data)
    return result
