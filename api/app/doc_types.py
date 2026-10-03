"""Server-side document-type registry.

Mirrors src/data/docTypes.js so the extraction prompt lives next to the model
instead of being shipped to (and editable in) the browser. Each prompt asks the
model to return strict JSON; the keys here are what the guardrails validate.
"""

DOC_TYPES: dict[str, dict] = {
    "pan": {
        "label": "PAN Card",
        "required": True,
        "prompt": 'Extract PAN card data. Return ONLY JSON: '
                  '{"pan_number":"AAAAA9999A","full_name":"","dob":"DD/MM/YYYY","father_name":"name or null"}',
    },
    "aadhaar": {
        "label": "Aadhaar Card",
        "required": True,
        "prompt": 'Extract Aadhaar card data. Do NOT invent masked digits. Return ONLY JSON: '
                  '{"aadhaar_number":"XXXX-XXXX-NNNN","full_name":"","dob":"DD/MM/YYYY",'
                  '"gender":"Male/Female","address":"","pincode":"6-digit"}',
    },
    "salary_slip": {
        "label": "Salary Slip",
        "required": True,
        "prompt": 'Extract salary slip. Return ONLY JSON: '
                  '{"employee_name":"","employer_name":"","month_year":"Month YYYY","designation":"",'
                  '"basic_pay":0,"hra":0,"gross_salary":0,"net_salary":0}',
    },
    "bank_statement": {
        "label": "Bank Statement",
        "required": True,
        "prompt": 'Extract bank statement summary. Return ONLY JSON: '
                  '{"account_holder":"","bank_name":"","account_number":"XXXX1234","ifsc":"",'
                  '"statement_period":"","opening_balance":0,"closing_balance":0,'
                  '"total_credits":0,"total_debits":0,"average_balance":0}',
    },
    "itr": {
        "label": "ITR / Form 16",
        "required": False,
        "prompt": 'Extract ITR or Form 16. Return ONLY JSON: '
                  '{"document_type":"ITR/Form 16","name":"","assessment_year":"AY YYYY-YY",'
                  '"gross_total_income":0,"total_tds":0}',
    },
    "vehicle_quotation": {
        "label": "Vehicle Quotation",
        "required": False,
        "prompt": 'Extract dealer quotation. Return ONLY JSON: '
                  '{"dealer_name":"","vehicle_make":"","vehicle_model":"","on_road_price":0,"ex_showroom_price":0}',
    },
    "rc_book": {
        "label": "RC Book",
        "required": False,
        "prompt": 'Extract RC Book. Return ONLY JSON: '
                  '{"registration_number":"","owner_name":"","chassis_number":"","engine_number":"",'
                  '"vehicle_make":"","vehicle_model":"","year_of_manufacture":"YYYY","fuel_type":""}',
    },
    "insurance": {
        "label": "Insurance Copy",
        "required": False,
        "prompt": 'Extract insurance policy. Return ONLY JSON: '
                  '{"policy_number":"","insurer":"","insured_name":"","valid_from":"","valid_until":"","sum_insured":0}',
    },
    "property_doc": {
        "label": "Property Document",
        "required": False,
        "prompt": 'Extract property document. Return ONLY JSON: '
                  '{"document_type":"","property_address":"","owner_name":"","property_value":0,"area_sqft":0}',
    },
    "land_record": {
        "label": "Land Record",
        "required": False,
        "prompt": 'Extract land record. Return ONLY JSON: '
                  '{"survey_number":"","village":"","taluk":"","district":"","land_owner":"",'
                  '"area_acres":0.0,"soil_type":""}',
    },
    "gst_return": {
        "label": "GST Return",
        "required": False,
        "prompt": 'Extract GST return. Return ONLY JSON: '
                  '{"gstin":"","legal_name":"","trade_name":"","return_period":"","turnover":0,"tax_paid":0}',
    },
    "financial_statement": {
        "label": "Financial Statement",
        "required": False,
        "prompt": 'Extract financial statement. Return ONLY JSON: '
                  '{"document_type":"Balance Sheet/P&L","entity_name":"","period":"",'
                  '"total_revenue":0,"net_profit":0,"total_assets":0}',
    },
    "form16": {
        "label": "Form 16",
        "required": False,
        "prompt": 'Extract Form 16. Return ONLY JSON: '
                  '{"employee_name":"","employer_name":"","employer_tan":"","assessment_year":"AY YYYY-YY",'
                  '"gross_salary":0,"total_tds":0}',
    },
    "trade_license": {
        "label": "Trade License",
        "required": False,
        "prompt": 'Extract trade license. Return ONLY JSON: '
                  '{"license_number":"","business_name":"","owner_name":"","valid_until":"","business_type":""}',
    },
    "cibil": {
        "label": "CIBIL Report",
        "required": False,
        "prompt": 'Extract CIBIL report. Return ONLY JSON: '
                  '{"name":"","cibil_score":0,"total_accounts":0,"active_accounts":0,'
                  '"settled_accounts":0,"defaults":0}',
    },
    "photo": {
        "label": "Photograph",
        "required": True,
        "prompt": 'Identify if this is a valid passport-size photograph with a clear face visible. '
                  'Return ONLY JSON: {"has_face":true,"is_passport_size":true}',
    },
}


def get_doc_type(doc_id: str) -> dict | None:
    return DOC_TYPES.get(doc_id)
