"""Key Facts Statement (RBI: KFS for Loans & Advances, 2024; Digital Lending Directions).

A KFS must reach the borrower before the loan contract and state the APR — the all-in annual cost,
fees included — every fee, the cooling-off period, and how penalties are charged. FinSight builds it
with the draft offer, from the offer's own numbers and the lender's standard terms (config.py: kfs_*).

The policy check judges the digital-lending rules on these FACTS, not on assumptions:
  * before an offer exists there are no loan terms, so those rules do not apply yet (stage gating);
  * once an offer exists, "KFS provided" is true only if the offer carries a KFS and the application
    has reached the offer stage, i.e. the offer went to the borrower.
"""
from __future__ import annotations

from ...config import settings

# application status -> lifecycle stage the compliance rules are written against
_STAGE = {"offerGenerated": "offer", "accepted": "offer", "sanctioned": "sanction", "disbursed": "disbursal"}
OFFER_STAGES = ["offer", "sanction", "disbursal"]


def stage_of(status_code: str | None) -> str:
    """'assessment' until an offer has gone to the borrower."""
    return _STAGE.get(str(status_code or ""), "assessment")


def current_status(case: dict) -> str | None:
    """The application's status NOW (SQL is the system of record; the case file's copy can be stale)."""
    app_no = (case or {}).get("application_no")
    if app_no and settings.db_enabled:
        try:
            from ... import db
            row = db.query_one("SELECT status_code FROM core.loan_applications WHERE application_no = ?", (app_no,))
            if row and row.get("status_code"):
                return row["status_code"]
        except Exception:  # noqa: BLE001 - database down: fall back to the case file
            pass
    return ((case or {}).get("loan") or {}).get("status_code")


def lender_terms() -> dict:
    return {"cooling_off_period_days": settings.kfs_cooling_off_days,
            "disbursal_to_borrower_account": settings.kfs_disbursal_to_borrower_account,
            "penal_charge_is_interest": not settings.kfs_penalty_as_charges}


def apr(amount: float, fees: float, emi: float, months: int) -> float | None:
    """Annual Percentage Rate: the monthly rate at which the EMIs repay what the borrower actually
    receives (amount less upfront fees), times 12. Bisection; None when there is nothing to price."""
    net = amount - fees
    if net <= 0 or emi <= 0 or months <= 0 or emi * months <= net:
        return None
    lo, hi = 0.0, 1.0
    for _ in range(80):
        r = (lo + hi) / 2
        pv = emi * months if r == 0 else emi * (1 - (1 + r) ** -months) / r
        lo, hi = (r, hi) if pv > net else (lo, r)
    return round((lo + hi) / 2 * 12 * 100, 2)


def build_kfs(offer: dict) -> dict | None:
    """The KFS for a draft offer (None when the offer has no amount)."""
    amount, emi, months = offer.get("offered_amount") or 0, offer.get("emi") or 0, offer.get("tenure_months") or 0
    if not amount or not emi or not months:
        return None
    fee = float(offer.get("processing_fee") or 0)
    terms = lender_terms()
    return {
        "loan_amount": amount, "interest_rate": offer.get("interest_rate"), "tenure_months": months, "emi": emi,
        "fees": [{"name": "Processing fee", "amount": round(fee, 2), "when": "deducted at disbursal"}],
        "net_disbursed": round(amount - fee, 2),
        "total_interest": offer.get("total_interest"),
        "total_amount_payable": round(emi * months, 2),
        "apr": apr(amount, fee, emi, months),
        "cooling_off_period_days": terms["cooling_off_period_days"],
        "penalty": ("Late payment attracts a fixed penal charge; it is not added to the interest rate and "
                    "earns no interest" if not terms["penal_charge_is_interest"] else "Penal interest applies"),
        "disbursal": ("Directly to the borrower's own bank account" if terms["disbursal_to_borrower_account"]
                      else "Through a third-party account"),
        "note": "Key Facts Statement — shared with the borrower before the loan contract.",
    }


def loan_terms_facts(case: dict, stage: str) -> dict:
    """loan_terms.* for the policy check, from the offer on file. Empty before the offer stage."""
    offer = (case or {}).get("offer") or {}
    kfs = offer.get("kfs")
    if stage not in OFFER_STAGES:
        return {}
    terms = lender_terms()
    facts = {"cooling_off_period_days": terms["cooling_off_period_days"],
             "disbursal_to_borrower_account": terms["disbursal_to_borrower_account"],
             "penal_charge_is_interest": terms["penal_charge_is_interest"],
             "kfs_provided": bool(kfs), "apr_disclosed": bool(kfs and kfs.get("apr") is not None)}
    if kfs:
        listed = sum(float(f.get("amount") or 0) for f in kfs.get("fees") or [])
        facts["all_fees_in_kfs"] = abs(listed - float(offer.get("processing_fee") or 0)) < 0.01
    return facts
