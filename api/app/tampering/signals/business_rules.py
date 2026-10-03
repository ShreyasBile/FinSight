"""Stage-1 signal: business-rule validation of the OCR-extracted fields.

Runs on the OCR JSON (the same shape FinSight's OCR backend produces: a flat
dict of field -> value, or field -> {"value": ...}), NOT on pixels. A forger
who edits one number on a payslip rarely fixes every number that depends on
it — arithmetic consistency is cheap, deterministic, and brutally effective.

All rules are pure functions; no ML, no network, no LLM. Same trust model as
FinSight's docVerification.js, and designed to merge with it later.

Score: fraction of applicable rules that FAILED, weighted by severity
(hard arithmetic breaks count double vs. soft format issues).
"""
import re
from datetime import datetime

from app.tampering.config import CFG
from app.tampering.signals.base import SignalResult, unavailable

_TOL = CFG.signals.business_rules.amount_tolerance

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")
PIN_RE = re.compile(r"^[1-9][0-9]{5}$")

# Unmistakable filler words in a name/institution field — fabricated documents
# are frequently populated with these. Deliberately narrow (word-boundary,
# short list) so a real name can't false-positive.
PLACEHOLDER_RE = re.compile(
    r"\b(?:abc|xyz|test|dummy|sample|asdf|qwerty|lorem|john doe|jane doe)\b", re.I)

# Verhoeff checksum tables (Aadhaar's last digit is a Verhoeff check digit)
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


def verhoeff_valid(number: str) -> bool:
    c = 0
    for i, digit in enumerate(reversed(number)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(digit)]]
    return c == 0


def _unwrap(fields: dict) -> dict:
    """Accept both {field: value} and FinSight's {field: {value, flag, ...}}."""
    out = {}
    for k, v in fields.items():
        out[k] = v.get("value") if isinstance(v, dict) and "value" in v else v
    return out


def _num(value) -> float | None:
    if value is None:
        return None
    s = re.sub(r"[₹,\s]|INR|Rs\.?", "", str(value), flags=re.IGNORECASE)
    try:
        return float(s)
    except ValueError:
        return None


def _date(value):
    if not value:
        return None
    s = str(value).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def run(ocr_fields: dict | None, doc_type: str = "generic") -> SignalResult:
    if not ocr_fields:
        return unavailable("business_rules", "no OCR fields supplied")

    f = _unwrap(ocr_fields)
    # Each entry: (passed: bool|None, severity: int, description). None = rule
    # not applicable to this document (missing inputs) — skipped, not failed.
    checks = []

    # ---- format & checksum rules (any doc type that carries the field) ----
    pan = f.get("pan") or f.get("pan_number")
    if pan:
        ok = bool(PAN_RE.match(str(pan).strip().upper()))
        checks.append((ok, 1, f"PAN format '{pan}'"))

    aadhaar = f.get("aadhaar_number") or f.get("aadhaar")
    if aadhaar:
        digits = re.sub(r"\D", "", str(aadhaar))
        if len(digits) == 12:  # masked Aadhaar (XXXX XXXX 1234) can't be checksummed
            checks.append((verhoeff_valid(digits), 2,
                           f"Aadhaar Verhoeff checksum for '{aadhaar}'"))

    ifsc = f.get("ifsc") or f.get("ifsc_code")
    if ifsc:
        checks.append((bool(IFSC_RE.match(str(ifsc).strip().upper())), 1,
                       f"IFSC format '{ifsc}'"))

    gstin = f.get("gstin")
    if gstin:
        checks.append((bool(GSTIN_RE.match(str(gstin).strip().upper())), 1,
                       f"GSTIN format '{gstin}'"))

    pincode = f.get("pincode") or f.get("pin_code")
    if pincode:
        digits = re.sub(r"\D", "", str(pincode))
        if digits:  # only when OCR actually read digits (masked/blank -> skip)
            checks.append((bool(PIN_RE.match(digits)), 1, f"PIN code format '{pincode}'"))

    # ---- placeholder / dummy value detection ----
    # A fabricated document is often populated with filler ('abc bank',
    # account '123456789'). Only unmistakable patterns fire, and only as
    # failures — absence of placeholders proves nothing, so no passed-check
    # is recorded.
    for key, val in f.items():
        if not isinstance(val, str) or not val.strip():
            continue
        kl = key.lower()
        if any(t in kl for t in ("name", "bank", "employer", "company", "issuer")):
            if PLACEHOLDER_RE.search(val):
                checks.append((False, 2,
                               f"{key} '{val}' looks like a placeholder, not a real name"))
        if "account" in kl or "acct" in kl:
            digits = re.sub(r"\D", "", val)
            if len(digits) >= 6 and (
                    digits in "1234567890123456789012"
                    or digits in "9876543210987654321098"
                    or len(set(digits)) == 1):
                checks.append((False, 2,
                               f"{key} '{val}' is a sequential/repeated placeholder number"))

    # ---- salary arithmetic ----
    # FinSight's salary OCR emits basic_pay/hra/gross_salary/net_salary but NOT
    # an itemised total_deductions. So we run whichever checks the present
    # fields allow: the classic gross-deductions=net when deductions IS given,
    # plus two invariants that hold with FinSight's actual fields —
    # net<=gross and (basic+hra)<=gross. A forger who inflates net past gross,
    # or whose components don't fit under gross, trips these.
    gross = _num(f.get("gross_salary"))
    deductions = _num(f.get("total_deductions"))
    net = _num(f.get("net_salary"))
    basic = _num(f.get("basic_pay"))
    hra = _num(f.get("hra"))
    if gross is not None and deductions is not None and net is not None:
        expected = gross - deductions
        checks.append((abs(expected - net) <= _TOL, 2,
                       f"Payslip arithmetic: gross {gross:,.0f} minus deductions "
                       f"{deductions:,.0f} = {expected:,.0f}, but the slip shows net "
                       f"salary {net:,.0f} (off by {abs(expected - net):,.0f})"))
    if gross is not None and net is not None:
        checks.append((net <= gross + _TOL, 2,
                       f"Net salary {net:,.0f} is higher than gross salary "
                       f"{gross:,.0f} — net pay can never exceed gross pay"))
    if gross is not None and basic is not None and hra is not None:
        checks.append((basic + hra <= gross + _TOL, 1,
                       f"Basic {basic:,.0f} + HRA {hra:,.0f} should not exceed gross {gross:,.0f}"))

    # ---- bank statement reconciliation ----
    opening, closing = _num(f.get("opening_balance")), _num(f.get("closing_balance"))
    credits, debits = _num(f.get("total_credits")), _num(f.get("total_debits"))
    txns = f.get("transactions")
    if isinstance(txns, list) and txns and opening is not None:
        # per-transaction running balance (when row-level data is available)
        balance = opening
        broke_at = None
        for i, t in enumerate(txns):
            amt = _num(t.get("amount") if isinstance(t, dict) else None)
            bal = _num(t.get("balance") if isinstance(t, dict) else None)
            if amt is None or bal is None:
                continue
            balance += amt
            if abs(balance - bal) > _TOL:
                broke_at = i + 1
                break
        checks.append((broke_at is None, 2,
                       "Bank statement running balance reconciles"
                       if broke_at is None else
                       f"Bank statement running balance breaks at transaction {broke_at}"))
        if closing is not None and broke_at is None:
            checks.append((abs(balance - closing) <= _TOL, 2,
                           f"Final running balance {balance:,.2f} matches closing balance {closing:,.2f}"))
    elif (opening is not None and closing is not None
          and credits is not None and debits is not None
          # a ZERO credit or debit total on a real statement is almost always an
          # OCR extraction gap, not reality — reconciling against it manufactures
          # a false failure on a genuine document, so require both to be present
          and credits > 0 and debits > 0):
        # summary reconciliation — FinSight's OCR emits these totals even when
        # it doesn't extract per-row transactions
        expected = opening + credits - debits
        checks.append((abs(expected - closing) <= max(_TOL, 0.01 * abs(closing)), 2,
                       f"Bank summary: opening {opening:,.0f} + credits {credits:,.0f} "
                       f"- debits {debits:,.0f} = {expected:,.0f} should equal closing {closing:,.0f}"))

    # ---- CIBIL score range ----
    cibil = _num(f.get("cibil_score"))
    if cibil is not None and cibil > 0:
        checks.append((300 <= cibil <= 900, 2, f"CIBIL score {cibil:.0f} in valid range 300-900"))

    # ---- vehicle pricing sanity ----
    on_road, ex_show = _num(f.get("on_road_price")), _num(f.get("ex_showroom_price"))
    if on_road is not None and ex_show is not None and on_road > 0 and ex_show > 0:
        checks.append((on_road >= ex_show - _TOL, 1,
                       f"On-road price {on_road:,.0f} should be >= ex-showroom {ex_show:,.0f}"))

    # ---- date rules ----
    dob = _date(f.get("dob") or f.get("date_of_birth"))
    if dob:
        checks.append((dob < datetime.now(), 2, f"DOB '{f.get('dob') or f.get('date_of_birth')}' is in the past"))

    start, end = _date(f.get("period_start") or f.get("statement_from")), _date(f.get("period_end") or f.get("statement_to"))
    if start and end:
        checks.append((start <= end, 2, "Statement period start precedes end"))

    issue = _date(f.get("issue_date") or f.get("date_of_issue"))
    if issue:
        checks.append((issue <= datetime.now(), 2, "Issue date is not in the future"))

    if not checks:
        return unavailable("business_rules",
                           f"no applicable rules for doc_type='{doc_type}' with supplied fields")

    findings = []
    failed_weight = passed_weight = 0
    for ok, severity, desc in checks:
        if ok:
            passed_weight += severity
        else:
            failed_weight += severity
            findings.append(f"FAILED: {desc}")
    if not findings:
        findings.append(f"All {len(checks)} applicable rules passed")

    score = failed_weight / (failed_weight + passed_weight)
    return SignalResult("business_rules", True, score, findings)
