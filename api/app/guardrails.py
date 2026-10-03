"""Validation guardrails for extracted fields.

OCR confidence alone is NOT trustworthy in lending (a hallucinated PAN can still
look valid), so every field is checked structurally: format regexes + real
checksums (Aadhaar Verhoeff, PAN/IFSC/GSTIN structure) + numeric sanity. Each
field gets a flag (ok | warn | error); the doc gets a confidence score and a
needs_review verdict that the UI uses to force human review.
"""

import re

# Human-readable labels (mirrors src/data/fieldLabels.js; falls back to the key)
FIELD_LABELS: dict[str, str] = {
    "pan_number": "PAN Number", "full_name": "Full Name", "dob": "Date of Birth",
    "father_name": "Father's Name", "aadhaar_number": "Aadhaar Number", "gender": "Gender",
    "address": "Address", "pincode": "PIN Code", "employee_name": "Employee Name",
    "employer_name": "Employer Name", "month_year": "Month/Year", "designation": "Designation",
    "basic_pay": "Basic Pay (₹)", "hra": "HRA (₹)", "gross_salary": "Gross Salary (₹)",
    "net_salary": "Net Salary (₹)", "account_holder": "Account Holder", "bank_name": "Bank Name",
    "account_number": "Account Number", "ifsc": "IFSC Code", "statement_period": "Statement Period",
    "opening_balance": "Opening Balance (₹)", "closing_balance": "Closing Balance (₹)",
    "total_credits": "Total Credits (₹)", "total_debits": "Total Debits (₹)",
    "average_balance": "Average Balance (₹)", "document_type": "Document Type",
    "assessment_year": "Assessment Year", "gross_total_income": "Gross Income (₹)",
    "total_tds": "Total TDS (₹)", "gstin": "GSTIN", "legal_name": "Legal Name",
    "turnover": "Turnover (₹)", "cibil_score": "CIBIL Score", "owner_name": "Owner Name",
    "registration_number": "Registration No.", "on_road_price": "On-Road Price (₹)",
}

_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_PIN_RE = re.compile(r"^[0-9]{6}$")
_MIN_DIGITS = {"pincode": 6, "account_number": 6, "mobile": 10, "phone": 10}
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")

# ── Verhoeff checksum (Aadhaar) ───────────────────────────────────────────────
_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def _verhoeff_valid(num: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


def _is_blank(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _to_number(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        cleaned = re.sub(r"[^0-9.\-]", "", v)
        try:
            return float(cleaned) if cleaned else None
        except ValueError:
            return None
    return None


def validate_field(key: str, value) -> str:
    """Return 'ok' | 'warn' | 'error' for a single extracted field."""
    if _is_blank(value):
        return "warn"

    if key == "pan_number":
        return "ok" if _PAN_RE.match(str(value).upper()) else "error"

    if key == "ifsc":
        return "ok" if _IFSC_RE.match(str(value).upper()) else "error"

    if key == "pincode":
        return "ok" if _PIN_RE.match(str(value)) else "warn"

    if key == "gstin":
        return "ok" if _GSTIN_RE.match(str(value).upper()) else "error"

    if key == "aadhaar_number":
        raw = str(value).upper()
        if "X" in raw:  # legitimately masked for privacy — cannot verify, accept
            return "ok"
        digits = re.sub(r"\D", "", raw)
        if len(digits) != 12:
            return "error"
        return "ok" if _verhoeff_valid(digits) else "error"

    if key == "cibil_score":
        n = _to_number(value)
        return "ok" if n is not None and 300 <= n <= 900 else "warn"

    if key in ("net_salary", "gross_salary", "gross_total_income"):
        n = _to_number(value)
        return "warn" if (n is None or n < 1000) else "ok"

    return "ok"


def validate(doc_type: str, parsed: dict | None) -> dict:
    """Validate a parsed document and return field flags + confidence + verdict."""
    if not parsed:
        return {"fields": [], "confidence": 0.0, "needs_review": True}

    fields = []
    ok = 0
    has_error = False
    for key, value in parsed.items():
        # a number field with too few digits to be a reading is a label the model echoed
        # ("6-digit", "PIN Code"): report it as not found rather than as a value
        if key in _MIN_DIGITS and isinstance(value, str) \
                and len(re.sub(r"\D", "", value)) < _MIN_DIGITS[key] and "X" not in value.upper():
            value = None
        flag = validate_field(key, value)
        if flag == "ok":
            ok += 1
        if flag == "error":
            has_error = True
        fields.append({
            "key": key,
            "label": FIELD_LABELS.get(key, key.replace("_", " ").title()),
            "value": value,
            "flag": flag,
        })

    confidence = round(ok / len(fields), 2) if fields else 0.0
    needs_review = has_error or confidence < 0.7
    return {"fields": fields, "confidence": confidence, "needs_review": needs_review}
