import copy
import json

from .base import OCRProvider

# Canned, schema-valid responses so the full app flow works with no model installed.
# Values are crafted to PASS the guardrails (valid PAN/IFSC/GSTIN, masked Aadhaar).
_CANNED: dict[str, dict] = {
    "pan": {"pan_number": "ABCPK1234D", "full_name": "Rajesh Kumar", "dob": "15/03/1986", "father_name": "Suresh Kumar"},
    "aadhaar": {"aadhaar_number": "XXXX-XXXX-4521", "full_name": "Rajesh Kumar", "dob": "15/03/1986", "gender": "Male", "address": "12, Transport Nagar, Jaipur", "pincode": "302001"},
    "salary_slip": {"employee_name": "Rajesh Kumar", "employer_name": "ABC Transport Co.", "month_year": "May 2024", "designation": "Manager", "basic_pay": 48000, "hra": 19200, "gross_salary": 96400, "net_salary": 75000},
    "bank_statement": {"account_holder": "Rajesh Kumar", "bank_name": "HDFC Bank", "account_number": "XXXX7890", "ifsc": "HDFC0001234", "statement_period": "Jan–Jun 2024", "opening_balance": 142000, "closing_balance": 182400, "total_credits": 540000, "total_debits": 499600, "average_balance": 182400},
    "itr": {"document_type": "ITR", "name": "Rajesh Kumar", "assessment_year": "AY 2024-25", "gross_total_income": 1260000, "total_tds": 92000},
    "gst_return": {"gstin": "08ABCPK1234D1Z5", "legal_name": "Verma Trades", "trade_name": "Verma Trades", "return_period": "May 2024", "turnover": 3500000, "tax_paid": 210000},
    "cibil": {"name": "Rajesh Kumar", "cibil_score": 718, "total_accounts": 6, "active_accounts": 2, "settled_accounts": 0, "defaults": 0},
    "photo": {"has_face": True, "is_passport_size": True},
}

_DEFAULT = {"document_verified": "Yes", "status": "Authentic"}


class MockProvider(OCRProvider):
    name = "mock"

    async def extract(self, file_bytes, mime, doc_type, prompt):
        # a fresh copy every call: the flow corrects fields in place (statement totals, the PAN number),
        # and a shared dict would carry one upload's corrections into the next
        parsed = copy.deepcopy(_CANNED.get(doc_type, _DEFAULT))
        return json.dumps(parsed, ensure_ascii=False), parsed
