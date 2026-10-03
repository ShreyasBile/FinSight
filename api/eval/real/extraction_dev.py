"""Development diagnostic for statement extraction (dev split only).

  python -m eval.real.extraction_dev totals [n_scanned]   parser-derived totals / balances vs truth
  python -m eval.real.extraction_dev model [n]            what the reading model returns, and how long it takes
  python -m eval.real.extraction_dev pan [n]              PAN number errors by kind (no values printed)
  python -m eval.real.extraction_dev page1 [n]            the retry path: summary fields from page 1 only
"""
import json
import sys

from eval import common as C


def _close(a, b):
    return a is not None and b is not None and abs(float(a) - float(b)) <= max(1.0, 0.005 * abs(float(b)))


def totals(n_scanned: int = 0):
    C.setup_env(provider="ollama", db=False)
    from app.agents.tools.transactions import parse_best
    from eval.real import intake_real as I
    items = I.statement_items("dev", None)
    items = [i for i in items if i["kind"] == "digital"] + [i for i in items if i["kind"] == "scanned"][:n_scanned]
    agg = {}
    for it in items:
        truth = I._truth_statement(it["truth"])
        got = parse_best(it["path"].read_bytes(), None)
        rows = got.get("transactions") or []
        cr, dr = sum(r["credit"] for r in rows), sum(r["debit"] for r in rows)
        chain = bool(rows) and got.get("opening_balance") is not None
        closes = got.get("closing_matches")
        ok_cr = any(_close(cr, v) for v in truth["total_credits"])
        ok_dr = any(_close(dr, v) for v in truth["total_debits"])
        a = agg.setdefault(it["kind"], {"n": 0, "cr": 0, "dr": 0, "open": 0, "close": 0, "closes": 0,
                                        "closes_and_right": 0, "full": 0, "full_and_right": 0})
        a["n"] += 1
        a["cr"] += ok_cr
        a["dr"] += ok_dr
        a["open"] += _close(got.get("opening_balance"), truth["opening_balance"])
        a["close"] += _close(got.get("closing_balance_printed"), truth["closing_balance"])
        a["closes"] += bool(closes)
        a["closes_and_right"] += bool(closes) and ok_cr and ok_dr
        full = chain and got.get("reconciled_share", 0) >= 0.98
        a["full"] += bool(full)
        a["full_and_right"] += bool(full) and ok_cr and ok_dr
        print(f"{it['kind']} rows={len(rows)} src={got.get('source')} share={got.get('reconciled_share')} "
              f"open={got.get('opening_balance') is not None} closing_printed={got.get('closing_balance_printed') is not None} "
              f"closes={closes} cr_ok={ok_cr} dr_ok={ok_dr}")
    print(json.dumps(agg, indent=1))


def model(n: int = 16):
    """The whole statement flow on dev statements: fields, time, and whether the tampering rule
    "opening + credits - debits = closing" fails on these GENUINE statements - with the reading model's
    own totals (before) and with the totals policy of transactions.apply_summary (after)."""
    C.setup_env(provider="ollama", db=False)
    from app.agents.tools import transactions as T
    from app.tampering.signals import business_rules
    from eval.real import core as R
    from eval.real import intake_real as I
    items = I.statement_items("dev", None)
    half = n // 2
    items = [i for i in items if i["kind"] == "digital"][:half] + [i for i in items if i["kind"] == "scanned"][:n - half]
    seen = {}
    real = T.apply_summary

    def spy(parsed, txn):
        seen["before"] = dict(parsed) if isinstance(parsed, dict) else None
        return real(parsed, txn)
    T.apply_summary = spy
    cache = R.ItemCache("extraction", "stmt-flow-v3-dev")

    def rule_fails(parsed):
        r = business_rules.run(parsed or {}, "bank_statement")
        return any("Bank summary" in f and f.startswith("FAILED") for f in (r.findings or []))

    def one(it):
        seen.clear()
        truth = I._truth_statement(it["truth"])
        r = I._extract(it["path"].read_bytes(), "application/pdf", "bank_statement", it["path"].name)
        sc = I._score_statement(truth, r["parsed"] or {})
        return {"kind": it["kind"], "s": round(r["ms"] / 1000), "json": r["parsed"] is not None,
                "wrong": [k for k, v in sc.items() if not v],
                "rule_fails_before": rule_fails(seen.get("before")), "rule_fails_after": rule_fails(r["parsed"]),
                "totals_after": [(r["parsed"] or {}).get(k) is not None for k in ("total_credits", "total_debits")]}
    rows = cache.run(items, one, label="statement flow", every=4)
    for r in rows:
        print(r)
    for kind in ("digital", "scanned"):
        rs = [r for r in rows if r.get("kind") == kind]
        if rs:
            print(kind, {"n": len(rs), "rule_fails_before": sum(r["rule_fails_before"] for r in rs),
                         "rule_fails_after": sum(r["rule_fails_after"] for r in rs),
                         "printed_fields_all_right": sum(not [w for w in r["wrong"] if w in I.STMT_PRINTED] for r in rs),
                         "totals_right": sum("total_credits" not in r["wrong"] and "total_debits" not in r["wrong"] for r in rs),
                         "median_s": sorted(r["s"] for r in rs)[len(rs) // 2]})


def page1(n: int = 10):
    """The retry path: the reading model sees page 1 only. Are the summary fields still right?"""
    import asyncio
    C.setup_env(provider="ollama", db=False)
    from app.agents.impl.intake import _first_page
    from app.doc_types import get_doc_type
    from app.providers import get_provider
    from eval.real import intake_real as I
    items = I.statement_items("dev", None)
    half = n // 2
    items = [i for i in items if i["kind"] == "digital"][:half] + [i for i in items if i["kind"] == "scanned"][:n - half]
    prompt, agg = get_doc_type("bank_statement")["prompt"], {}
    for it in items:
        truth = I._truth_statement(it["truth"])
        with C.Timer() as t:
            _raw, parsed = asyncio.run(get_provider().extract(_first_page(it["path"].read_bytes()), "image/png",
                                                              "bank_statement", prompt))
        sc = I._score_statement(truth, parsed or {})
        for k in I.STMT_PRINTED + ("statement_period",):
            a = agg.setdefault(k, [0, 0])
            a[0] += sc[k]
            a[1] += 1
        print(f"{it['kind']} {round(t.ms / 1000)}s json={parsed is not None} wrong={[k for k in I.STMT_PRINTED if not sc[k]]}",
              flush=True)
    print({k: f"{v[0]}/{v[1]}" for k, v in agg.items()})


def panocr(n: int = 150):
    """OCR-only read of the PAN number on dev cards (no model call): how often does the page's OCR text
    hold exactly one PAN-shaped token, and is it the right one?"""
    import re
    C.setup_env(provider="ollama", db=False)
    from app.agents.registry import get_agent
    from app.agents.tools.doc_keywords import page_text
    from eval.real import intake_real as I
    pan_re = re.compile(r"[A-Z]{5}[0-9]{4}[A-Z]")
    agg = {"n": 0, "none": 0, "one": 0, "one_right": 0, "several": 0, "several_has_truth": 0}
    for it in I.pan_items("dev", n):
        truth = it["expected"]["pan_number"].upper()
        pre = get_agent("preprocessing").execute({"data": it["path"].read_bytes(), "mime": "image/png", "doc_type": "pan"}).payload
        text = pre.get("page_text") or ""
        if len(text.strip()) < 20:
            text = page_text(pre["data"], pre["mime"])
        cands = set(pan_re.findall(text.upper()))
        agg["n"] += 1
        agg["none"] += not cands
        agg["one"] += len(cands) == 1
        agg["one_right"] += cands == {truth}
        agg["several"] += len(cands) > 1
        agg["several_has_truth"] += len(cands) > 1 and truth in cands
    print(json.dumps(agg))


def firstlast(n: int = 10):
    """First + last page as the first attempt: summary fields and time, on dev statements."""
    import asyncio
    C.setup_env(provider="ollama", db=False)
    from app.agents.impl.intake import _summary_pages
    from app.doc_types import get_doc_type
    from app.providers import get_provider
    from eval.real import intake_real as I
    items = I.statement_items("dev", None)
    half = n // 2
    items = [i for i in items if i["kind"] == "digital"][:half] + [i for i in items if i["kind"] == "scanned"][:n - half]
    prompt, agg = get_doc_type("bank_statement")["prompt"], {}
    for it in items:
        truth = I._truth_statement(it["truth"])
        data = it["path"].read_bytes()
        two = _summary_pages(data)
        with C.Timer() as t:
            _raw, parsed = asyncio.run(get_provider().extract(two or data, "application/pdf", "bank_statement", prompt))
        sc = I._score_statement(truth, parsed or {})
        for k in I.STMT_PRINTED + ("statement_period",):
            a = agg.setdefault(k, [0, 0])
            a[0] += sc[k]
            a[1] += 1
        print(f"{it['kind']} two_pages={two is not None} {round(t.ms / 1000)}s json={parsed is not None} "
              f"wrong={[k for k in I.STMT_PRINTED if not sc[k]]}", flush=True)
    print({k: f"{v[0]}/{v[1]}" for k, v in agg.items()})


def panraw(n: int = 60):
    """Does the cleanup help the reading model on a flat card image? The model reads the UPLOAD as it came
    (no preprocessing); compared with its cached read of the cleaned image (pan-readers-dev)."""
    import asyncio
    import re
    C.setup_env(provider="ollama", db=False)
    from app.doc_types import get_doc_type
    from app.providers import get_provider
    from eval.real import core as R
    from eval.real import intake_real as I
    cleaned = R.ItemCache("extraction", "pan-readers-dev")
    cache = R.ItemCache("extraction", "pan-raw-dev")
    prompt = get_doc_type("pan")["prompt"]

    def one(it):
        _raw, parsed = asyncio.run(get_provider().extract(it["path"].read_bytes(), "image/png", "pan", prompt))
        p = parsed or {}
        return {"model_raw": re.sub(r"[^A-Z0-9]", "", str(p.get("pan_number") or "").upper()),
                "fields": {k: C.field_match(k, v, p.get(k)) for k, v in it["expected"].items()}}
    items = [it for it in I.pan_items("dev", 150) if cleaned.get(it["id"])][:n]
    rows = cache.run(items, one, label="PAN raw", every=10)
    both = [(cleaned.get(it["id"]), r) for it, r in zip(items, rows) if r.get("model_raw") is not None]
    print(json.dumps({"n": len(both), "cleaned_right": sum(c["model"] == c["truth"] for c, _ in both),
                      "raw_right": sum(r["model_raw"] == c["truth"] for c, r in both),
                      "raw_only_right": sum(r["model_raw"] == c["truth"] != c["model"] for c, r in both),
                      "cleaned_only_right": sum(c["model"] == c["truth"] != r["model_raw"] for c, r in both),
                      "raw_other_fields": {k: sum(r["fields"][k] for _, r in both) for k in ("full_name", "father_name", "dob")}}, indent=1))


def pan(n: int = 150):
    """Three readers of the PAN number on dev cards (the cards are synthetic): the reading model, OCR of the
    cleaned image (the text the flow already has), OCR of the upload as it came. Which rule is right most
    often? Per-card reads are cached, so the rules can be re-scored without calling the model again."""
    import re
    C.setup_env(provider="ollama", db=False)
    from app.agents.flows import document_flows as flows
    from app.agents.tools.doc_keywords import page_text
    from eval.real import core as R
    from eval.real import intake_real as I
    pan_re = re.compile(r"[A-Z]{5}[0-9]{4}[A-Z]")
    cache = R.ItemCache("extraction", "pan-readers-dev")
    from app.agents.tools import id_crossread
    id_crossread.cross_read_pan = lambda parsed, text: None       # the model's own read is what is measured here

    def one(it):
        data = it["path"].read_bytes()
        res = flows.run_extract(data, "image/png", "pan", it["path"].name)
        ext = res.get("extraction") or {}
        model = (ext.get("parsed") or {}).get("pan_number")
        pre = res.get("preprocess") or {}
        text = pre.get("page_text") or ""
        if len(text.strip()) < 20:
            text = page_text(pre.get("data") or data, pre.get("mime") or "image/png")
        return {"truth": it["expected"]["pan_number"].upper(),
                "model": re.sub(r"[^A-Z0-9]", "", str(model or "").upper()) if model is not None else None,
                "ocr_clean": sorted(set(pan_re.findall(text.upper()))),
                "ocr_upload": sorted(set(pan_re.findall(page_text(data, "image/png").upper())))}
    rows = cache.run(I.pan_items("dev", n), one, label="PAN readers", every=10)
    rows = [r for r in rows if r.get("truth") and r.get("model") is not None]

    def single(c):
        return c[0] if len(c) == 1 else None

    def replace_on_single(r):
        return single(r["ocr_clean"]) or r["model"]

    def vote(r):
        a, b, m = single(r["ocr_clean"]), single(r["ocr_upload"]), r["model"]
        if a and (a == m or a == b):
            return a
        if b and b == m:
            return b
        return m

    def vote_else_ocr(r):
        a, b, m = single(r["ocr_clean"]), single(r["ocr_upload"]), r["model"]
        if a and b and a == b:
            return a
        return m

    n = len(rows)
    out = {"n": n}
    for name, rule in (("model", lambda r: r["model"]), ("replace_when_one_ocr_candidate", replace_on_single),
                       ("two_of_three", vote), ("ocr_only_when_both_ocr_reads_agree", vote_else_ocr)):
        out[name] = sum(rule(r) == r["truth"] for r in rows)
    dis = [r for r in rows if single(r["ocr_clean"]) and single(r["ocr_clean"]) != r["model"]]
    out["disagree"] = {"n": len(dis), "ocr_right": sum(single(r["ocr_clean"]) == r["truth"] for r in dis),
                       "model_right": sum(r["model"] == r["truth"] for r in dis),
                       "ocr_upload_backs_ocr": sum(single(r["ocr_upload"]) == single(r["ocr_clean"]) for r in dis),
                       "ocr_upload_backs_model": sum(single(r["ocr_upload"]) == r["model"] for r in dis)}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "totals"
    arg = int(sys.argv[2]) if len(sys.argv) > 2 else None
    {"totals": totals, "model": model, "pan": pan, "page1": page1, "panocr": panocr, "panraw": panraw, "firstlast": firstlast}[what](*([arg] if arg is not None else []))
