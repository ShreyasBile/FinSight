"""Fraud Patterns and Income Analysis on real money flows.

Berka (Czech bank, 1993-98: 4,500 real accounts, 1M real transactions) gives what no synthetic set can:
how genuine accounts actually behave — irregular deposits, lumpy transfers, balances that drift. Each
"statement" is a real 6-month (patterns) or 3-month (income) window of one account, with amounts scaled
by 2.5 (CZK of the 1990s to present-day rupee magnitudes, so FinSight's rupee thresholds mean what they
mean in production) and Berka's Czech operation codes mapped to statement wording (MAP below). A
recurring monthly transfer from the same counter-account (>= 3 months, amounts within 10%) is printed as
"NEFT SALARY CREDIT" — the way an Indian statement shows salary; every other credit keeps its own label.

Fraud Patterns — clean windows (genuine behaviour: every flag on them is a false flag) and windows with
ONE planted pattern each (round-tripping, a late large deposit, window dressing, payslip above the salary
credited, GST turnover above all credits). Accounts are split dev/test by account id.
The statement parser (transactions.parse_best) is scored on the Indian statement set (JSON truth).
IBM AML (synthetic AMLworld, labelled laundering) is reported without a target: its typologies (fan-in,
cycles, scatter-gather) are inter-account graphs, not the single-statement patterns FinSight looks for.

Income Analysis — real salary-like inflows; the payslip is declared consistent (within 3%) or inflated
(1.5-3x). Ground truth = inflated.
"""
from __future__ import annotations

import csv
import json
import random
import re
import statistics
from datetime import datetime, timedelta

from eval import common as C
from eval.real import core as R

BERKA = R.RAW / "berka"
STMT = R.RAW / "bank_stmt_in" / "train"
AML = R.RAW / "ibm_aml"
SCALE = 2.5
MAP = {"VKLAD": "CASH DEPOSIT", "PREVOD Z UCTU": "NEFT TRANSFER FROM ACCOUNT", "VYBER": "CASH WITHDRAWAL",
       "PREVOD NA UCET": "NEFT TRANSFER TO ACCOUNT", "VYBER KARTOU": "CARD WITHDRAWAL",
       "DUCHOD": "PENSION CREDIT", "UROK": "INTEREST CREDIT", "SIPO": "HOUSEHOLD BILL PAYMENT",
       "SLUZBY": "SERVICE CHARGES", "UVER": "LOAN EMI", "POJISTNE": "INSURANCE PREMIUM",
       "SANKC. UROK": "PENAL INTEREST", "": ""}
PATTERNS = ("cash_cycling", "sudden_deposit", "window_dressing", "income_mismatch", "gst_inflation")
# A planted window-dressing inflow IS a large late deposit, so a "sudden_deposit" flag on it is right,
# not a false flag (first scoring, 2026-10-01, counted it as one; corrected the same day).
ALSO_TRUE = {"window_dressing": {"sudden_deposit"}}


def _fp(rs, p) -> int:
    return sum(1 for r in rs if r["truth"] != p and p in r["flags"] and p not in ALSO_TRUE.get(r["truth"], ()))


# ── Berka statements ────────────────────────────────────────────────────────
def _berka_by_account() -> dict:
    out: dict = {}
    with open(BERKA / "trans.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter=";"):
            d = r["date"]
            out.setdefault(r["account_id"], []).append(
                (datetime(1900 + int(d[:2]), int(d[2:4]), int(d[4:6])), r["type"], r["operation"], float(r["amount"]),
                 float(r["balance"]), r["k_symbol"].strip(), (r["bank"] or "") + (r["account"] or "")))
    for v in out.values():
        v.sort(key=lambda t: t[0])
    return out


def _salary_partners(txs) -> set:
    """Counter-accounts that pay in every month (>= 3 months) with amounts within 10% of their median."""
    by: dict = {}
    for dt, typ, op, amt, _, ks, partner in txs:
        if typ == "PRIJEM" and op == "PREVOD Z UCTU" and partner and ks not in ("DUCHOD", "UROK"):
            by.setdefault(partner, []).append((dt.strftime("%Y-%m"), amt))
    out = set()
    for p, xs in by.items():
        months = {m for m, _ in xs}
        med = statistics.median(a for _, a in xs)
        if len(months) >= 3 and all(abs(a - med) <= 0.10 * med for _, a in xs):
            out.add(p)
    return out


def berka_windows(split: str, months: int, n: int, min_tx: int = 15):
    """[(account, [statement rows], salary_monthly or None)] — one window per account, deterministic."""
    accounts = _berka_by_account()
    keys = R.sample([{"id": a} for a in accounts if R.split_of(f"berka:{a}") == split], None, salt=f"win{months}")
    out = []
    for k in keys:
        a = k["id"]
        txs = accounts[a]
        if len(txs) < min_tx * 2:
            continue
        rng = random.Random(f"{a}-{months}")
        first, last = txs[0][0], txs[-1][0]
        span = (last - first).days - months * 31
        if span <= 0:
            continue
        start = first + timedelta(days=rng.randint(0, span))
        start = start.replace(day=1)
        end = (start + timedelta(days=months * 31)).replace(day=1) - timedelta(days=1)
        win = [t for t in txs if start <= t[0] <= end]
        if len(win) < min_tx:
            continue
        sal = _salary_partners(win)
        rows, opening = [], None
        for dt, typ, op, amt, bal, ks, partner in win:
            credit = amt * SCALE if typ == "PRIJEM" else 0.0
            debit = amt * SCALE if typ != "PRIJEM" else 0.0
            if opening is None:
                opening = bal * SCALE - credit + debit
            desc = "NEFT SALARY CREDIT" if partner in sal and typ == "PRIJEM" else (MAP.get(ks) or MAP.get(op, op))
            rows.append({"date": dt.strftime("%d/%m/%Y"), "description": desc, "credit": round(credit, 2),
                         "debit": round(debit, 2), "balance": round(bal * SCALE, 2), "reconciled": True})
        sal_rows = [r["credit"] for r in rows if r["description"] == "NEFT SALARY CREDIT"]
        out.append({"id": f"berka:{a}:{start:%Y%m}", "account": a, "rows": rows, "opening": opening,
                    "start": start, "end": end, "salary": statistics.mean(sal_rows) if sal_rows else None})
        if len(out) >= n:
            break
    return out


def _rebalance(rows, opening):
    rows.sort(key=lambda r: datetime.strptime(r["date"], "%d/%m/%Y"))
    bal = opening
    for r in rows:
        bal = round(bal + r["credit"] - r["debit"], 2)
        r["balance"] = bal
    return rows


def plant(w: dict, pattern: str, rng: random.Random) -> tuple[list, float | None, float | None]:
    """(rows, payslip_net, gst_monthly_turnover) with one pattern planted into a real window."""
    rows = [dict(r) for r in w["rows"]]
    end = w["end"]
    credits = [r["credit"] for r in rows if r["credit"] > 0] or [10000.0]
    med_credit = statistics.median(credits)
    months = max(1.0, (w["end"] - w["start"]).days / 30.4)
    payslip = round(w["salary"] * rng.uniform(0.97, 1.03), 0) if w["salary"] else None
    gst = None
    if pattern == "cash_cycling":
        for _ in range(3):
            x = round(rng.uniform(30000, 200000), 0)
            d = w["start"] + timedelta(days=rng.randint(5, max(6, (end - w["start"]).days - 5)))
            rows.append({"date": d.strftime("%d/%m/%Y"), "description": "CASH DEPOSIT", "credit": x, "debit": 0.0,
                         "balance": 0.0, "reconciled": True})
            back = d + timedelta(days=rng.randint(0, 3))
            rows.append({"date": back.strftime("%d/%m/%Y"), "description": "NEFT TRANSFER TO ACCOUNT", "credit": 0.0,
                         "debit": round(x * rng.uniform(0.99, 1.0), 0), "balance": 0.0, "reconciled": True})
    elif pattern == "sudden_deposit":
        x = round(max(60000.0, med_credit * rng.uniform(5, 12)), 0)
        d = end - timedelta(days=rng.randint(3, 25))
        rows.append({"date": d.strftime("%d/%m/%Y"), "description": "CASH DEPOSIT", "credit": x, "debit": 0.0,
                     "balance": 0.0, "reconciled": True})
    elif pattern == "window_dressing":
        med_bal = statistics.median(r["balance"] for r in rows)
        x = round(max(med_bal, 20000.0) * rng.uniform(2.5, 4.0), 0)
        for part in (0.5, 0.5):
            d = end - timedelta(days=rng.randint(0, 6))
            rows.append({"date": d.strftime("%d/%m/%Y"), "description": "NEFT TRANSFER FROM ACCOUNT",
                         "credit": round(x * part, 0), "debit": 0.0, "balance": 0.0, "reconciled": True})
    elif pattern == "income_mismatch":
        base = w["salary"] or (sum(credits) / months)
        payslip = round(base * rng.uniform(1.6, 3.0), 0)
    elif pattern == "gst_inflation":
        gst = round(sum(credits) / months * rng.uniform(2.0, 4.0), 0)
    return _rebalance(rows, w["opening"]), payslip, gst


def _clean(w, rng):
    """A clean window, with the documents a genuine applicant would also supply."""
    payslip = round(w["salary"] * rng.uniform(0.97, 1.03), 0) if w["salary"] else None
    credits = [r["credit"] for r in w["rows"] if r["credit"] > 0]
    months = max(1.0, (w["end"] - w["start"]).days / 30.4)
    gst = round(sum(credits) / months * rng.uniform(0.5, 0.9), 0) if credits and rng.random() < 0.3 else None
    return [dict(r) for r in w["rows"]], payslip, gst


def _f1(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return round(2 * p * r / (p + r), 4) if p + r else 0.0


def eval_fraud_patterns(split: str = "test", n_clean: int = 300, n_each: int = 60) -> dict:
    from app.agents.tools.fraud_patterns import scan
    wins = berka_windows(split, 6, n_clean + n_each * len(PATTERNS))
    rng = random.Random(f"{R.SEED}-patterns-{split}")
    items = []
    for i, w in enumerate(wins):
        if i < n_clean:
            items.append((w, None, *_clean(w, rng)))
        else:
            pat = PATTERNS[(i - n_clean) % len(PATTERNS)]
            items.append((w, pat, *plant(w, pat, rng)))
    res = []
    for w, pat, rows, payslip, gst in items:
        out = scan(rows, payslip_net=payslip, gst_monthly_turnover=gst)
        res.append({"truth": pat, "flags": [f["pattern"] for f in out["flags"]], "score": out["score"]})
    per = {}
    for p in PATTERNS:
        tp = sum(1 for r in res if r["truth"] == p and p in r["flags"])
        fp = _fp(res, p)
        fn = sum(1 for r in res if r["truth"] == p and p not in r["flags"])
        per[p] = {"f1": _f1(tp, fp, fn), "recall": round(tp / max(1, tp + fn), 4), "false_flags": fp}
    clean = [r for r in res if r["truth"] is None]
    clean_kinds = {}
    for r in clean:
        for f in r["flags"]:
            clean_kinds[f] = clean_kinds.get(f, 0) + 1
    parse = statement_parse(split)
    aml = aml_check()
    metrics = {"macro_f1": round(sum(v["f1"] for v in per.values()) / len(per), 4), "per_pattern": per,
               "macro_f1_ci95": R.bootstrap(lambda rs: _macro(rs), res)["ci95"],
               "genuine_flag_rate": R.wilson(sum(bool(r["flags"]) for r in clean), len(clean)),
               "genuine_flags_by_pattern": clean_kinds, "parse_accuracy": parse["row_recall"],
               "parse": parse, "aml_ibm": aml,
               "auc_planted_vs_clean": R.auc_ci(res, score=lambda r: r["score"], label=lambda r: r["truth"] is not None)}
    data = {"name": "Berka real accounts (6-month windows, clean + one planted pattern each); Indian statement "
                    "set for the parser; IBM AML HI-Small (informational)",
            "split": split, "n_windows": len(res), "n_clean": len(clean), "planted_each": n_each,
            "scale": f"amounts x{SCALE} (CZK 1990s -> rupee magnitudes)", "label_map": MAP}
    return R.save("fraud_patterns", split, metrics, data)


def _macro(rs):
    f = []
    for p in PATTERNS:
        tp = sum(1 for r in rs if r["truth"] == p and p in r["flags"])
        fp = _fp(rs, p)
        fn = sum(1 for r in rs if r["truth"] == p and p not in r["flags"])
        f.append(_f1(tp, fp, fn))
    return round(sum(f) / len(f), 4)


# ── statement parser (Indian statement set) ─────────────────────────────────
def statement_parse(split: str, n_scanned: int = 40) -> dict:
    from app.agents.tools.transactions import parse_best
    from eval.real.intake_real import statement_items
    items = statement_items(split, None)
    items = [i for i in items if i["kind"] == "digital"] + [i for i in items if i["kind"] == "scanned"][:n_scanned]
    cache = R.ItemCache("fraud_patterns", f"parse-v2-{split}")      # v2: parser rewritten 2026-10-01

    def one(it):
        from eval.real.intake_real import truth_rows
        truth = truth_rows(json.loads(it["truth"].read_text(encoding="utf-8")))
        with C.Timer() as t:
            got = parse_best(it["path"].read_bytes(), None)
        rows = got.get("transactions") or []
        key = lambda d, a, b: (_iso(d), round(float(a or 0), 0), round(float(b or 0), 0))  # noqa: E731
        want = {}
        for x in truth:
            k = key(x["date"], (x.get("credit") or 0) or (x.get("debit") or 0), x.get("balance") or 0)
            want.setdefault(k, []).append((x.get("credit") or 0) > 0)
        hit = side = 0
        for r in rows:
            k = key(r["date"], r["credit"] or r["debit"], r["balance"])
            if want.get(k):
                side += want[k].pop() == (r["credit"] > 0)
                hit += 1
        return {"truth_rows": len(truth), "parsed_rows": len(rows), "matched": hit, "side_ok": side,
                "reconciled": sum(1 for r in rows if r.get("reconciled")), "source": got.get("source"),
                "ms": round(t.ms, 1)}
    res = cache.run(items, one, label="parse statements", every=10, on_error={"truth_rows": 0, "parsed_rows": 0, "matched": 0})
    for it, r in zip(items, res):
        r["kind"] = it["kind"]

    def blk(rs):
        tr, pr, m = sum(r["truth_rows"] for r in rs), sum(r["parsed_rows"] for r in rs), sum(r["matched"] for r in rs)
        return {"row_recall": round(m / tr, 4) if tr else None, "row_precision": round(m / pr, 4) if pr else None,
                "side_accuracy": round(sum(r.get("side_ok", 0) for r in rs) / m, 4) if m else None,
                "statements": len(rs), "truth_rows": tr}
    return {**blk(res), "digital": blk([r for r in res if r["kind"] == "digital"]),
            "scanned": blk([r for r in res if r["kind"] == "scanned"]),
            "latency": R.latency([r["ms"] for r in res if r.get("ms")])}


def _iso(s):
    from eval.real.intake_real import any_date
    return any_date(s)


# ── IBM AML (informational) ─────────────────────────────────────────────────
def aml_check(n_laundering: int = 150, n_clean: int = 300, fx: float = 83.0) -> dict:
    """Accounts that pass laundered money through (receive and send within a CYCLE / SCATTER-GATHER /
    GATHER-SCATTER / STACK attempt) vs accounts with no laundering transaction, as single statements."""
    from app.agents.tools.fraud_patterns import scan
    through, kind = set(), None
    seen_in, seen_out = {}, {}
    for line in (AML / "HI-Small_Patterns.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("BEGIN"):
            kind = line.split("-", 1)[1].split(":")[0].strip()
            seen_in, seen_out = {}, {}
            continue
        if line.startswith("END") or not line.strip():
            continue
        if kind in ("CYCLE", "SCATTER-GATHER", "GATHER-SCATTER", "STACK"):
            p = line.split(",")
            seen_out[p[2]] = 1
            seen_in[p[4]] = 1
            through.update(a for a in seen_in if a in seen_out)
    rng = random.Random(R.SEED)
    launder = sorted(through)
    rng.shuffle(launder)
    launder = set(launder[:n_laundering])
    rows: dict = {a: [] for a in launder}
    dirty, clean_pool = set(), {}
    with open(AML / "HI-Small_Trans.csv", encoding="utf-8") as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            ts, src, dst, amt_r, cur_r, amt_p, cur_p, lab = r[0], r[2], r[4], r[5], r[6], r[7], r[8], r[10]
            if lab == "1":
                dirty.update((src, dst))
            for acct, credit in ((dst, True), (src, False)):
                if src == dst:
                    continue
                cur = cur_r if credit else cur_p
                if cur != "US Dollar":
                    continue
                amt = float(amt_r if credit else amt_p) * fx
                if acct in rows:
                    if len(rows[acct]) < 400:
                        rows[acct].append((ts, credit, amt))
                elif len(clean_pool) < 20000 or acct in clean_pool:
                    lst = clean_pool.setdefault(acct, [])
                    if len(lst) < 400:
                        lst.append((ts, credit, amt))
    clean = [a for a, v in clean_pool.items() if a not in dirty and len(v) >= 8]
    rng.shuffle(clean)
    out = []
    for label, accts, src in ((1, launder, rows), (0, clean[:n_clean], clean_pool)):
        for a in accts:
            txs = sorted(src[a])
            if len(txs) < 3:
                continue
            bal, st = 100000.0, []
            for ts, credit, amt in txs:
                bal += amt if credit else -amt
                st.append({"date": datetime.strptime(ts, "%Y/%m/%d %H:%M").strftime("%d/%m/%Y"),
                           "description": "NEFT TRANSFER FROM ACCOUNT" if credit else "NEFT TRANSFER TO ACCOUNT",
                           "credit": round(amt, 2) if credit else 0.0, "debit": 0.0 if credit else round(amt, 2),
                           "balance": round(bal, 2), "reconciled": True})
            s = scan(st)
            out.append({"label": label, "score": s["score"], "flags": [f["pattern"] for f in s["flags"]]})
    lau = [r for r in out if r["label"]]
    cl = [r for r in out if not r["label"]]
    return {"auc": R.auc_ci(out, score=lambda r: r["score"], label=lambda r: r["label"]),
            "laundering_flagged": R.wilson(sum(bool(r["flags"]) for r in lau), len(lau)),
            "clean_flagged": R.wilson(sum(bool(r["flags"]) for r in cl), len(cl)),
            "note": "USD transactions only, x83 to rupees; opening balance Rs 1 lakh (AML data carries no balances)"}


# ── income analysis ─────────────────────────────────────────────────────────
def eval_income_analysis(split: str = "test", n: int = 400) -> dict:
    from app.agents.registry import get_agent
    agent = get_agent("income_analysis")
    wins = berka_windows(split, 3, n * 3)
    rng = random.Random(f"{R.SEED}-income-{split}")
    res = []
    for w in wins:
        credits = sum(r["credit"] for r in w["rows"])
        if credits <= 0:
            continue
        months = max(1.0, (w["end"] - w["start"]).days / 30.4)
        base = w["salary"] or credits / months
        inflated = rng.random() < 0.5
        declared = round(base * (rng.uniform(1.5, 3.0) if inflated else rng.uniform(0.97, 1.03)), 0)
        case = {"documents": {
            "salary_slip": {"parsed": {"net_salary": str(declared), "gross_salary": str(round(declared * 1.15, 0))}},
            "bank_statement": {"parsed": {"total_credits": str(round(credits, 2)),
                                          "statement_period": f"{w['start']:%d/%m/%Y} to {w['end']:%d/%m/%Y}"},
                               "transactions": w["rows"]}}}
        out = agent.execute({"case": case}).payload
        res.append({"truth": inflated, "flag": bool(out.get("mismatches")), "salary_rows": bool(w["salary"]),
                    "consensus_ok": (out.get("consensus_monthly") or 0) <= declared + 1})
        if len(res) >= n:
            break
    tp = sum(1 for r in res if r["truth"] and r["flag"])
    fp = sum(1 for r in res if not r["truth"] and r["flag"])
    fn = sum(1 for r in res if r["truth"] and not r["flag"])
    cons = [r for r in res if not r["truth"]]
    metrics = {"f1": _f1(tp, fp, fn), "f1_ci95": R.bootstrap(lambda rs: _f1(
                   sum(1 for r in rs if r["truth"] and r["flag"]), sum(1 for r in rs if not r["truth"] and r["flag"]),
                   sum(1 for r in rs if r["truth"] and not r["flag"])), res)["ci95"],
               "recall": R.wilson(tp, tp + fn), "false_mismatch_rate": R.wilson(fp, len(cons)),
               "by_evidence": {k: {"f1": _f1(sum(1 for r in rs if r["truth"] and r["flag"]),
                                             sum(1 for r in rs if not r["truth"] and r["flag"]),
                                             sum(1 for r in rs if r["truth"] and not r["flag"])), "n": len(rs)}
                               for k, rs in (("salary_credits_on_statement", [r for r in res if r["salary_rows"]]),
                                             ("all_credits_only", [r for r in res if not r["salary_rows"]]))},
               "lends_on_evidenced_figure": R.wilson(sum(r["consensus_ok"] for r in res if r["flag"]), sum(r["flag"] for r in res)),
               "n": len(res)}
    data = {"name": "Berka real accounts, 3-month windows; payslip declared consistent (+-3%) or inflated (1.5-3x)",
            "split": split, "scale": f"amounts x{SCALE}"}
    return R.save("income_analysis", split, metrics, data)
