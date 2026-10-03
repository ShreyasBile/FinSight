"""Income triangulation across the documents an applicant supplied.

Each source gives an independent monthly figure; they are compared pairwise
with tolerances that reflect what each document actually measures:

  payslip gross    vs Form 16 gross / 12     15 %  (same employer, same year)
  payslip gross    vs ITR total income / 12  under-declaration only: ITR may
                                             include other income, but an ITR
                                             much LOWER than the payslip is the
                                             classic inflated-payslip signal
  payslip net      vs bank salary credits    10 %  (the salary should land)
  payslip net      vs ALL bank credits       declared net above every rupee
                                             that came in (+10 %)
Figures are compared, never judged for plausibility ("is 2 lakh a month
realistic?" is not a question this agent answers).
"""
from __future__ import annotations

import re
from datetime import datetime


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:\.\d+)?", re.sub(r"[₹,\s]|Rs\.?", "", str(v)))
    return float(m.group(0)) if m else None


def _months(period: str | None) -> float | None:
    if not period:
        return None
    ds = re.findall(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", str(period))
    if len(ds) >= 2:
        try:
            a = datetime(int(ds[0][2]), int(ds[0][1]), int(ds[0][0]))
            b = datetime(int(ds[-1][2]), int(ds[-1][1]), int(ds[-1][0]))
            return max(1.0, round((b - a).days / 30.4, 1))
        except ValueError:
            return None
    return None


def triangulate(case: dict) -> dict:
    docs = case.get("documents") or {}
    p = lambda dt: (docs.get(dt) or {}).get("parsed") or {}  # noqa: E731
    slip, f16, itr, stmt, gst = p("salary_slip"), p("form16"), p("itr"), p("bank_statement"), p("gst_return")
    est = []

    def add(source, monthly, basis):
        if monthly and monthly > 0:
            est.append({"source": source, "monthly": round(monthly, 2), "basis": basis})

    net, gross = _num(slip.get("net_salary")), _num(slip.get("gross_salary"))
    add("payslip_net", net, "net salary on the payslip")
    add("payslip_gross", gross, "gross salary on the payslip")
    add("form16_gross", (_num(f16.get("gross_salary")) or 0) / 12, "Form 16 gross salary / 12")
    add("itr_income", (_num(itr.get("gross_total_income")) or 0) / 12, "ITR gross total income / 12")
    months = _months(stmt.get("statement_period"))
    txns = (docs.get("bank_statement") or {}).get("transactions") or []
    sal = [t for t in txns if "SALARY" in str(t.get("description", "")).upper() and (t.get("credit") or 0) > 0]
    if sal:
        add("bank_salary_credits", sum(t["credit"] for t in sal) / len(sal), f"average of {len(sal)} salary credit(s)")
    credits = _num(stmt.get("total_credits"))
    if credits and months:
        add("bank_all_credits", credits / months, f"all credits / {months} month(s)")
    add("gst_turnover", (_num(gst.get("turnover")) or 0), "monthly GST turnover (business income, not salary)")

    by = {e["source"]: e["monthly"] for e in est}
    mism = []

    def pct(a, b):
        return abs(a - b) / max(a, b) * 100

    if "payslip_gross" in by and "form16_gross" in by and pct(by["payslip_gross"], by["form16_gross"]) > 15:
        mism.append({"a": "payslip_gross", "b": "form16_gross", "diff_pct": round(pct(by["payslip_gross"], by["form16_gross"]), 1),
                     "detail": f"payslip gross {by['payslip_gross']:,.0f}/month vs Form 16 {by['form16_gross']:,.0f}/month"})
    if "payslip_gross" in by and "itr_income" in by and by["itr_income"] < 0.75 * by["payslip_gross"]:
        mism.append({"a": "payslip_gross", "b": "itr_income", "diff_pct": round(pct(by["payslip_gross"], by["itr_income"]), 1),
                     "detail": f"income declared to tax ({by['itr_income']:,.0f}/month) is well below the payslip ({by['payslip_gross']:,.0f}/month)"})
    if "payslip_net" in by and "bank_salary_credits" in by and pct(by["payslip_net"], by["bank_salary_credits"]) > 10:
        mism.append({"a": "payslip_net", "b": "bank_salary_credits", "diff_pct": round(pct(by["payslip_net"], by["bank_salary_credits"]), 1),
                     "detail": f"payslip net {by['payslip_net']:,.0f} vs salary credited {by['bank_salary_credits']:,.0f}"})
    if "payslip_net" in by and "bank_all_credits" in by and by["payslip_net"] > 1.10 * by["bank_all_credits"]:
        mism.append({"a": "payslip_net", "b": "bank_all_credits", "diff_pct": round(pct(by["payslip_net"], by["bank_all_credits"]), 1),
                     "detail": f"declared net {by['payslip_net']:,.0f}/month exceeds everything credited to the account ({by['bank_all_credits']:,.0f}/month)"})
    salaried = [by[k] for k in ("payslip_net", "bank_salary_credits") if k in by]
    declared = sorted(salaried)[len(salaried) // 2] if salaried else (by.get("form16_gross") or by.get("itr_income"))
    consensus, basis = declared, "declared income (sources agree)" if len(est) >= 2 else "only source on file"
    if mism:
        # The sources disagree: lend on what is actually evidenced — the lowest figure involved —
        # never on the payslip's claim (a fabricated slip otherwise makes any loan "affordable").
        involved = [by[k] for m in mism for k in (m["a"], m["b"]) if k in by]
        consensus, basis = min(involved), "lowest of the disagreeing sources"
    score = min(1.0, max((m["diff_pct"] / 50 for m in mism), default=0.0))
    return {"estimates": est, "consensus_monthly": round(consensus, 2) if consensus else None,
            "declared_monthly": round(declared, 2) if declared else None, "consensus_basis": basis,
            "mismatches": mism, "score": round(score, 3), "sources": len(est)}
