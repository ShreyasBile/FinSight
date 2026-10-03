"""The loan lifecycle as data (diagram 7, statuses from ref.application_status).

    draft → submitted → documentsProcessing → documentsVerified
      → product branch (gold valuation · field verification · property/legal ·
        home-loan income/document/technical checks · bank/GST analysis ·
        vehicle inspection) → underReview | underwritingReview
      → offerGenerated → accepted → sanctioned → disbursed
    any pre-decision status may also end in rejected / cancelled.

The Workflow Router agent only RECOMMENDS the next step (and who owns it); it
never changes a status — that stays a human action.
"""
from __future__ import annotations

BRANCH = {
    "gold": ["goldValuationScheduled", "goldValued"],
    "tractor": ["fieldVerification"], "microfinance": ["fieldVerification"],
    "lap": ["propertyValuation", "legalVerification"],
    "home_loan": ["propertyValuation", "legalVerification", "incomeVerification", "propertyDocReview",
                  "legalCheck", "technicalValuation"],
    "msme": ["bankGstAnalysis"], "working_capital": ["bankGstAnalysis"],
    "used_vehicle": ["vehicleInspection"],
}
REVIEW = {"msme": "underwritingReview", "working_capital": "underwritingReview"}
OWNER = {
    "draft": "borrower", "submitted": "system", "documentsProcessing": "system (agents)",
    "documentsVerified": "officer", "goldValuationScheduled": "branch (gold appraiser)", "goldValued": "officer",
    "fieldVerification": "field officer", "propertyValuation": "valuer", "legalVerification": "legal team",
    "incomeVerification": "officer", "propertyDocReview": "legal team", "legalCheck": "legal team",
    "technicalValuation": "technical valuer", "bankGstAnalysis": "credit analyst", "vehicleInspection": "field officer",
    "underReview": "officer", "underwritingReview": "credit committee", "offerGenerated": "borrower",
    "accepted": "officer / head", "sanctioned": "operations", "disbursed": "—",
}
TERMINAL = {"disbursed", "rejected", "cancelled", "expired"}
PRE_DECISION = {"draft", "submitted", "documentsProcessing", "documentsVerified", "goldValuationScheduled",
                "goldValued", "fieldVerification", "propertyValuation", "legalVerification", "incomeVerification",
                "propertyDocReview", "legalCheck", "technicalValuation", "bankGstAnalysis", "vehicleInspection",
                "underReview", "underwritingReview"}


def path(product: str, used_vehicle: bool = False) -> list[str]:
    branch = list(BRANCH.get(product, []))
    if product == "commercial_vehicle" and used_vehicle:
        branch = ["vehicleInspection"]
    return (["draft", "submitted", "documentsProcessing", "documentsVerified", *branch,
             REVIEW.get(product, "underReview"), "offerGenerated", "accepted", "sanctioned", "disbursed"])


def next_step(product: str, status: str, evidence: dict | None = None, loan_amount: float | None = None,
              used_vehicle: bool = False) -> dict:
    ev = evidence or {}
    if status in TERMINAL:
        return {"status": status, "next": None, "terminal": True, "owner": None, "blocked_by": [],
                "note": "Terminal status — no further steps."}
    p = path(product, used_vehicle)
    shown = status
    if status in ("underReview", "underwritingReview") and status not in p:
        # diagram 7's review node is "underReview / underwritingReview" for every
        # product; seeded applications use either (e.g. a commercial-vehicle case
        # sitting in underwritingReview), so both mean "the review step"
        status = REVIEW.get(product, "underReview")
    if status not in p:
        return {"status": status, "next": None, "terminal": False, "owner": None, "blocked_by": [],
                "note": f"Status '{status}' is not on the {product} path — needs a human look."}
    nxt = p[p.index(status) + 1]
    blocked = []
    if status == "documentsProcessing":
        if ev.get("documents_missing"):
            blocked.append(f"required documents missing: {', '.join(ev['documents_missing'])}")
        if ev.get("identity_failures"):
            blocked.append(f"wrong document uploaded for: {', '.join(ev['identity_failures'])}")
    if status in ("underReview", "underwritingReview"):
        if ev.get("fraud_tier") == "HIGH":
            blocked.append("fraud risk HIGH — resolve the fraud review before an offer")
        if ev.get("policy_status") == "FAIL":
            blocked.append("a mandatory RBI/policy rule fails")
    head = bool(loan_amount and loan_amount > 500000 and nxt in ("offerGenerated", "sanctioned"))
    return {"status": shown, "next": nxt, "terminal": False, "owner": OWNER.get(nxt),
            "alternatives": (["rejected", "cancelled"] if status in PRE_DECISION else []),
            "blocked_by": blocked, "requires_head_approval": head,
            "note": ("Recommended next step; " + ("blocked until: " + "; ".join(blocked) if blocked else "no blockers found")
                     + (" · amount above ₹5L needs Head approval" if head else ""))}
