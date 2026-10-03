"""Deterministic document rules — a faithful Python port of
src/utils/docVerification.js (validateField, checkDocumentIdentity,
compareNames, crossVerifyDocuments).

Those checks used to exist ONLY in the browser, so the server could neither
enforce the wrong-document gate nor feed cross-document consistency into fraud
fusion. The port mirrors the JavaScript semantics exactly — including JS
truthiness, parseFloat prefix parsing and String() formatting of numbers —
and tests/unit/test_docrules_parity.py runs the ORIGINAL JavaScript under Node
over a corpus to prove both give identical answers.
"""
from __future__ import annotations

import math
import re

EXPECTED_FIELDS = {
    "pan": ["pan_number", "full_name", "dob", "father_name"],
    "aadhaar": ["aadhaar_number", "full_name", "dob", "gender", "address", "pincode"],
    "salary_slip": ["employee_name", "employer_name", "month_year", "designation", "basic_pay", "hra", "gross_salary", "net_salary"],
    "bank_statement": ["account_holder", "bank_name", "account_number", "ifsc", "statement_period", "opening_balance", "closing_balance", "total_credits", "total_debits", "average_balance"],
    "itr": ["document_type", "name", "assessment_year", "gross_total_income", "total_tds"],
    "vehicle_quotation": ["dealer_name", "vehicle_make", "vehicle_model", "on_road_price", "ex_showroom_price"],
    "rc_book": ["registration_number", "owner_name", "chassis_number", "engine_number", "vehicle_make", "vehicle_model", "year_of_manufacture", "fuel_type"],
    "insurance": ["policy_number", "insurer", "insured_name", "valid_from", "valid_until", "sum_insured"],
    "property_doc": ["document_type", "property_address", "owner_name", "property_value", "area_sqft"],
    "land_record": ["survey_number", "village", "taluk", "district", "land_owner", "area_acres", "soil_type"],
    "gst_return": ["gstin", "legal_name", "trade_name", "return_period", "turnover", "tax_paid"],
    "financial_statement": ["document_type", "entity_name", "period", "total_revenue", "net_profit", "total_assets"],
    "form16": ["employee_name", "employer_name", "employer_tan", "assessment_year", "gross_salary", "total_tds"],
    "trade_license": ["license_number", "business_name", "owner_name", "valid_until", "business_type"],
    "cibil": ["name", "cibil_score", "total_accounts", "active_accounts", "settled_accounts", "defaults"],
    "photo": ["has_face", "is_passport_size"],
}

_RE_PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_RE_IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_RE_GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")
_RE_PIN = re.compile(r"^[1-9][0-9]{5}$")
_RE_TAN = re.compile(r"^[A-Z]{4}[0-9]{5}[A-Z]$")
_RE_VEHICLE = re.compile(r"^([A-Z]{2}[\s-]?\d{1,2}[\s-]?[A-Z]{1,3}[\s-]?\d{4}|\d{2}[\s-]?BH[\s-]?\d{4}[\s-]?[A-Z]{1,2})$", re.I)
_RE_AY = re.compile(r"20\d{2}\s*[-–]\s*\d{2,4}")
_RE_FLOAT_PREFIX = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


# ── JavaScript semantics ─────────────────────────────────────────────────────
def js_str(v) -> str:
    """String(v) as JavaScript renders it."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if math.isnan(v):
            return "NaN"
        if v.is_integer() and abs(v) < 1e21:
            return str(int(v))
        return repr(v)
    if isinstance(v, (list, tuple)):
        return ",".join("" if x is None else js_str(x) for x in v)
    if isinstance(v, dict):
        return "[object Object]"
    return str(v)


def js_truthy(v) -> bool:
    if v is None or v is False:
        return False
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v != 0 and not (isinstance(v, float) and math.isnan(v))
    if isinstance(v, str):
        return v != ""
    return True


def is_blank(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def to_number(v):
    """toNumber(): numbers pass through; strings drop ₹ , and whitespace then
    parseFloat (leading numeric prefix); anything else → None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        cleaned = re.sub(r"[₹,\s]", "", v)
        m = _RE_FLOAT_PREFIX.match(cleaned)
        if not m:
            return None
        n = float(m.group(0))
        return n if math.isfinite(n) else None
    return None


def parse_date_any(s):
    """DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY, YYYY-MM-DD → 'YYYY-MM-DD' or None."""
    if is_blank(s):
        return None
    t = js_str(s).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", t)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$", t)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if 1 <= d <= 31 and 1 <= mo <= 12:
            return f"{y}-{mo:02d}-{d:02d}"
    return None


def _aadhaar_looks_valid(v) -> bool:
    if is_blank(v):
        return False
    s = js_str(v).upper()
    digits = re.sub(r"\D", "", s)
    if len(digits) == 12:
        return True
    return "X" in s and re.search(r"\d{4}\s*$", s.strip()) is not None


def _account_number_looks_valid(v) -> bool:
    if is_blank(v):
        return False
    rest = re.sub(r"[X\s-]", "", js_str(v).upper())
    return re.fullmatch(r"\d{4,}", rest) is not None


DATE_KEYS = {"dob", "valid_from", "valid_until"}
AMOUNT_KEYS = {
    "basic_pay", "hra", "gross_salary", "net_salary", "opening_balance", "closing_balance",
    "total_credits", "total_debits", "average_balance", "gross_total_income", "total_tds",
    "on_road_price", "ex_showroom_price", "sum_insured", "property_value", "area_sqft",
    "area_acres", "turnover", "tax_paid", "total_revenue", "net_profit", "total_assets",
}


def validate_field(key: str, value) -> str:
    """'ok' | 'warn' | 'error' | 'missing' (the browser's field check)."""
    if is_blank(value):
        return "missing"
    s = js_str(value).strip()
    if key == "pan_number":
        return "ok" if _RE_PAN.match(s.upper()) else "error"
    if key == "aadhaar_number":
        return "ok" if _aadhaar_looks_valid(s) else "error"
    if key == "account_number":
        return "ok" if _account_number_looks_valid(s) else "error"
    if key == "ifsc":
        return "ok" if _RE_IFSC.match(s.upper()) else "error"
    if key == "gstin":
        return "ok" if _RE_GSTIN.match(s.upper()) else "error"
    if key == "pincode":
        return "ok" if _RE_PIN.match(s) else "error"
    if key == "employer_tan":
        return "ok" if _RE_TAN.match(s.upper()) else "warn"
    if key == "registration_number":
        return "ok" if _RE_VEHICLE.match(s) else "warn"
    if key == "assessment_year":
        return "ok" if _RE_AY.search(s) else "warn"
    if key == "cibil_score":
        n = to_number(s)
        return "ok" if n is not None and 300 <= n <= 900 else "error"
    if key in DATE_KEYS:
        return "ok" if parse_date_any(s) else "warn"
    if key in AMOUNT_KEYS:
        n = to_number(s)
        if n is None:
            return "warn"
        return "ok" if n > 0 else "warn"
    return "ok"


def _pos(v) -> bool:
    n = to_number(v)
    return (n if n is not None else 0) > 0


def _js_or(*vals):
    for v in vals:
        if js_truthy(v):
            return v
    return vals[-1] if vals else None


_IDENTITY_RULES = {
    "pan": lambda p: validate_field("pan_number", p.get("pan_number")) == "ok",
    "aadhaar": lambda p: _aadhaar_looks_valid(p.get("aadhaar_number")),
    "salary_slip": lambda p: (not is_blank(p.get("employee_name"))
                              and any(_pos(p.get(k)) for k in ("net_salary", "gross_salary", "basic_pay"))),
    "bank_statement": lambda p: (_account_number_looks_valid(p.get("account_number"))
                                 or validate_field("ifsc", p.get("ifsc")) == "ok"
                                 or any(to_number(p.get(k)) is not None and to_number(p.get(k)) != 0
                                        for k in ("opening_balance", "closing_balance", "average_balance"))),
    "itr": lambda p: validate_field("assessment_year", p.get("assessment_year")) == "ok" or _pos(p.get("gross_total_income")),
    "vehicle_quotation": lambda p: (_pos(p.get("on_road_price")) or _pos(p.get("ex_showroom_price"))
                                    or (not is_blank(p.get("dealer_name")) and not is_blank(p.get("vehicle_make")))),
    "rc_book": lambda p: (not is_blank(p.get("registration_number"))
                          and not is_blank(_js_or(p.get("chassis_number"), p.get("engine_number"), p.get("owner_name")))),
    "insurance": lambda p: not is_blank(p.get("policy_number")),
    "property_doc": lambda p: not is_blank(p.get("owner_name")) or not is_blank(p.get("property_address")),
    "land_record": lambda p: not is_blank(p.get("survey_number")) or not is_blank(p.get("village")),
    "gst_return": lambda p: validate_field("gstin", p.get("gstin")) == "ok",
    "financial_statement": lambda p: not is_blank(p.get("entity_name")) or _pos(p.get("total_revenue")),
    "form16": lambda p: (validate_field("employer_tan", p.get("employer_tan")) == "ok"
                         or validate_field("assessment_year", p.get("assessment_year")) == "ok"
                         or _pos(p.get("gross_salary"))),
    "trade_license": lambda p: not is_blank(p.get("license_number")) or not is_blank(p.get("business_name")),
    "cibil": lambda p: validate_field("cibil_score", p.get("cibil_score")) == "ok",
    "photo": lambda p: p.get("has_face") is True or p.get("has_face") == "true",
}


def check_document_identity(doc_id: str, parsed) -> dict:
    rule = _IDENTITY_RULES.get(doc_id)
    if rule is None:
        return {"ok": True, "rule": None}
    if not parsed or not isinstance(parsed, dict):
        return {"ok": False, "rule": doc_id}
    return {"ok": bool(rule(parsed)), "rule": doc_id}


# ── Indian-aware name comparison ────────────────────────────────────────────
HONORIFICS = {"MR", "MRS", "MS", "MISS", "MASTER", "DR", "SHRI", "SRI", "SMT", "SH",
              "KUM", "KUMARI", "MOHD", "MD"}


def name_tokens(s) -> list[str]:
    if is_blank(s):
        return []
    t = re.sub(r"[.,]", " ", js_str(s).upper())
    return [x for x in re.split(r"\s+", t) if x and x not in HONORIFICS]


def _tokens_subsume(shorter, longer):
    """The positions in `longer` that the words of `shorter` account for (an initial stands for a word),
    or None when some word of `shorter` is not in `longer`."""
    pool = list(enumerate(longer))
    used = []
    for t in shorter:
        idx = next((i for i, (_, p) in enumerate(pool)
                    if p == t or (len(t) == 1 and p.startswith(t)) or (len(p) == 1 and t.startswith(p))), -1)
        if idx == -1:
            return None
        used.append(pool.pop(idx)[0])
    return used


PARTIAL_NOTE = ("Part of the name differs between documents — this is common after marriage "
                "or with initials. Our team will verify.")


def compare_names(a, b) -> dict:
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return {"level": "match"}
    shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    used = _tokens_subsume(shorter, longer)
    if used is not None:
        # Every word of the shorter name is in the longer one. A dropped MIDDLE name is the same person;
        # a dropped FIRST name is not: "Suresh Sharma" against "Rahul Suresh Sharma" is the father's
        # document (given name + father's name + surname is the Western-India convention). Found by
        # the real-data evaluation: 150 of 150 such pairs were judged the same person.
        if len(shorter) < len(longer) and 0 not in used:
            return {"level": "partial", "note": PARTIAL_NOTE}
        return {"level": "match"}
    # a shared SURNAME alone (last token of both) is not a partial match —
    # "Suresh Gupta" vs "Ramesh Gupta" are different people (same fix as the JS)
    common = [t for t in ta if len(t) > 1 and t in tb and not (t == ta[-1] and t == tb[-1])]
    if common:
        return {"level": "partial", "note": PARTIAL_NOTE}
    return {"level": "mismatch"}


STRICT_NAME_KEYS = ["full_name", "employee_name", "account_holder", "insured_name", "name"]
OWNERSHIP_NAME_KEYS = ["owner_name", "land_owner"]
PERSON_NAME_KEYS = STRICT_NAME_KEYS + OWNERSHIP_NAME_KEYS
_RANK = {"match": 0, "partial": 1, "mismatch": 2}


def cross_verify_documents(docs_map: dict) -> list[dict]:
    """docs_map: {doc_id: {parsed, label}} → [{level, title, detail}]."""
    checks: list[dict] = []
    names, dobs, genders = [], [], []
    for entry in docs_map.values():
        parsed, label = (entry or {}).get("parsed"), (entry or {}).get("label")
        if not parsed:
            continue
        for k in PERSON_NAME_KEYS:
            if not is_blank(parsed.get(k)):
                names.append({"label": label, "value": js_str(parsed[k]).strip(),
                              "ownership": k in OWNERSHIP_NAME_KEYS})
                break
        if not is_blank(parsed.get("dob")):
            dobs.append({"label": label, "value": js_str(parsed["dob"]).strip()})
        if not is_blank(parsed.get("gender")):
            genders.append({"label": label, "value": js_str(parsed["gender"]).strip()})

    if len(names) >= 2:
        worst, detail, any_ownership = {"level": "match"}, "", False
        for i in range(1, len(names)):
            r = compare_names(names[0]["value"], names[i]["value"])
            involves = names[0]["ownership"] or names[i]["ownership"]
            if r["level"] in ("mismatch", "partial"):
                if _RANK[r["level"]] > _RANK[worst["level"]] or worst["level"] == "match":
                    worst = r
                    detail = (f'"{names[0]["value"]}" ({js_str(names[0]["label"])}) vs '
                              f'"{names[i]["value"]}" ({js_str(names[i]["label"])})')
                    any_ownership = involves
        if worst["level"] == "match":
            checks.append({"level": "ok", "title": "Name matches across documents", "detail": ""})
        elif worst["level"] == "partial":
            checks.append({"level": "warn", "title": "Names are similar but not identical",
                           "detail": f"{detail}. {worst['note']}"})
        elif any_ownership:
            checks.append({"level": "warn", "title": "A document lists a different name for ownership",
                           "detail": f"{detail}. This is common when land or property is in a family "
                                     "member's name — our team will confirm the relationship."})
        else:
            checks.append({"level": "error", "title": "Names don't match between documents",
                           "detail": f"{detail}. Please check you uploaded your own documents."})

    if len(dobs) >= 2:
        known = [{**d, "iso": parse_date_any(d["value"])} for d in dobs]
        known = [d for d in known if d["iso"]]
        uniq = list(dict.fromkeys(d["iso"] for d in known))
        if len(uniq) <= 1:
            checks.append({"level": "ok", "title": "Date of birth matches across documents", "detail": ""})
        else:
            checks.append({"level": "error", "title": "Date of birth differs between documents",
                           "detail": " vs ".join(f"{d['value']} ({js_str(d['label'])})" for d in known)})

    if len(genders) >= 2:
        uniq = list(dict.fromkeys(g["value"][0].upper() for g in genders))
        if len(uniq) > 1:
            checks.append({"level": "error", "title": "Gender differs between documents",
                           "detail": " vs ".join(f"{g['value']} ({js_str(g['label'])})" for g in genders)})
    return checks
