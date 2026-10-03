"""Transaction-level fraud patterns in a bank statement (diagram 5's fraud
dashboard patterns), all deterministic and explainable:

  cash_cycling      money in, (almost) the same money out again within 3 days,
                    repeatedly — round-tripping to inflate turnover
  sudden_deposit    a credit several times larger than the usual credits,
                    landing in the last 30 days before the statement ends
  window_dressing   the balance in the final days is far above the period's
                    average (parked funds to look solvent)
  income_mismatch   the payslip's net salary does not arrive as salary credits
  gst_inflation     declared GST turnover far above everything credited
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta


def _d(s: str):
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d %b %Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def scan(transactions: list[dict], payslip_net: float | None = None,
         gst_monthly_turnover: float | None = None) -> dict:
    """Rows parsed from a photo can be misread. A row marked reconciled=False
    (its amount and balances do not add up) never produces a flag on its own
    amount, and conclusions drawn from ABSENCE ("no salary credit at all")
    are only made when at least 90% of rows reconcile."""
    tx = [dict(t, _dt=_d(t["date"])) for t in transactions if _d(t["date"])]
    flags = []
    if len(tx) < 3:
        return {"flags": [], "score": 0.0, "n_transactions": len(tx), "evaluable": False}
    trusted = [t for t in tx if t.get("reconciled", True)]
    complete = len(trusted) >= 0.9 * len(tx)
    credits = [t for t in trusted if t["credit"] > 0]
    debits = [t for t in trusted if t["debit"] > 0]
    end = max(t["_dt"] for t in tx)
    start = min(t["_dt"] for t in tx)
    months = max(1.0, (end - start).days / 30.4)

    # cash cycling
    cycles, cycled = [], set()
    for c in credits:
        if c["credit"] < 10000:
            continue
        for d in debits:
            if id(d) not in cycled and c["_dt"] <= d["_dt"] <= c["_dt"] + timedelta(days=3) \
                    and abs(d["debit"] - c["credit"]) <= 0.02 * c["credit"]:
                cycles.append((c["date"], c["credit"]))
                cycled.update((id(c), id(d)))
                break
    if len(cycles) >= 2:
        flags.append({"pattern": "cash_cycling", "severity": min(1.0, 0.5 + 0.1 * len(cycles)),
                      "detail": f"{len(cycles)} credits were paid back out within 3 days (e.g. Rs {cycles[0][1]:,.0f} on {cycles[0][0]})"})
    else:
        cycled = set()

    # sudden deposit near the end (a credit that was cycled straight back out
    # is cash cycling, not a deposit)
    if len(credits) >= 3:
        med = statistics.median(c["credit"] for c in credits)
        late = [c for c in credits if c["_dt"] >= end - timedelta(days=30) and c["credit"] >= 3 * med
                and c["credit"] >= 50000 and "SALARY" not in c["description"].upper() and id(c) not in cycled]
        if late:
            big = max(late, key=lambda c: c["credit"])
            flags.append({"pattern": "sudden_deposit", "severity": 0.6,
                          "detail": f"Rs {big['credit']:,.0f} credited on {big['date']}, {big['credit'] / med:.1f}x the usual credit"})

    # window dressing: the statement CLOSES far above its typical balance and
    # most of the gap arrived as credits in the final week (a balance that
    # simply grew with savings has no such late inflow)
    bals = [t["balance"] for t in tx]
    med_bal, closing = statistics.median(bals), tx[-1]["balance"]
    late_in = sum(c["credit"] for c in credits if c["_dt"] >= end - timedelta(days=7))
    if med_bal > 0 and closing > 2.0 * med_bal and late_in > 0.5 * (closing - med_bal):
        flags.append({"pattern": "window_dressing", "severity": 0.5,
                      "detail": f"closing balance Rs {closing:,.0f} vs typical Rs {med_bal:,.0f}; "
                                f"Rs {late_in:,.0f} credited in the last 7 days"})

    # salary vs payslip
    sal = [c for c in credits if "SALARY" in c["description"].upper()]
    if payslip_net:
        if not sal and complete and not any("SALARY" in t["description"].upper() for t in tx):
            flags.append({"pattern": "income_mismatch", "severity": 0.6,
                          "detail": f"payslip net Rs {payslip_net:,.0f} but no salary credit in the statement"})
        elif sal:                   # compared only on salary rows that reconcile
            avg = statistics.mean(c["credit"] for c in sal)
            if abs(avg - payslip_net) > 0.10 * payslip_net:
                flags.append({"pattern": "income_mismatch", "severity": min(1.0, abs(avg - payslip_net) / payslip_net),
                              "detail": f"payslip net Rs {payslip_net:,.0f} vs salary credited Rs {avg:,.0f}"})

    # GST inflation
    if gst_monthly_turnover and credits and complete:     # a partial read under-counts credits
        monthly_credits = sum(c["credit"] for c in credits) / months
        if gst_monthly_turnover > 1.5 * monthly_credits:
            flags.append({"pattern": "gst_inflation", "severity": min(1.0, gst_monthly_turnover / monthly_credits / 3),
                          "detail": f"GST turnover Rs {gst_monthly_turnover:,.0f}/month vs Rs {monthly_credits:,.0f}/month credited"})
    score = max((f["severity"] for f in flags), default=0.0)
    return {"flags": flags, "score": round(score, 3), "n_transactions": len(tx), "evaluable": True,
            "months": round(months, 1), "reconciled_share": round(len(trusted) / len(tx), 4)}
