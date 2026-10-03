"""Fraud-evidence evaluation, part 2: Fraud Patterns (14) and Fraud Triage (15).

Fraud Patterns — 54 synthetic statements (datasets/synth/out/patterns): six
scenarios (clean, cash cycling, sudden deposit, window dressing, income
mismatch, GST inflation), each a born-digital PDF and, for every other one, a
phone photo of the printout. Two things are measured separately so a failure
can be attributed: (a) the transaction parser against the true rows, and
(b) pattern detection end to end (parsed rows) and on the true rows (rules
only).

Fraud Triage — a planted population in a throwaway dedupe index: clean
applicants, repeat applicants (same PAN + same face — a customer, not fraud),
two 3-member rings (one face under three PANs; one bank account under three
PANs), two linked pairs (one PAN with two different faces; one file under two
PANs) and case-level assessments of each tier. Faces are real SFace
embeddings of public LFW photographs (same person = two different photos).
"""
from __future__ import annotations

import io
import uuid

from eval import common as C

PATTERNS = ("cash_cycling", "sudden_deposit", "window_dressing", "income_mismatch", "gst_inflation")


def _row_key(t):
    return (t["date"], round(t["debit"], 2), round(t["credit"], 2), round(t["balance"], 2))


def eval_patterns() -> dict:
    from app.agents.registry import get_agent
    from app.agents.tools.transactions import parse_best
    agent = get_agent("fraud_patterns")
    rows = C.manifest("patterns")
    counts = {p: {"tp": 0, "fp": 0, "fn": 0} for p in PATTERNS}
    counts_true = {p: {"tp": 0, "fp": 0, "fn": 0} for p in PATTERNS}
    parse_ok = parse_total = 0
    per_variant, lat, fails = {}, {}, []
    clean_docs = clean_flagged = 0
    pre_agent = get_agent("preprocessing")
    for r in rows:
        lab = r["label"]
        truth = lab["transactions"]
        data = C.read(r["path"])
        pre = None
        if r["mime"].startswith("image/"):     # as in intake: the preprocessed page first, then the original
            pre = pre_agent.execute({"data": data, "mime": r["mime"], "doc_type": "bank_statement"}).payload["data"]
        with C.Timer() as t:
            parsed = parse_best(data, pre)
        lat.setdefault(r["variant"], []).append(t.ms)
        got = {_row_key(x) for x in parsed["transactions"]}
        ok = sum(1 for x in truth if _row_key(x) in got)
        parse_ok += ok
        parse_total += len(truth)
        v = per_variant.setdefault(r["variant"], {"rows": 0, "ok": 0, "docs": 0, "docs_exact": 0})
        v["rows"] += len(truth)
        v["ok"] += ok
        v["docs"] += 1
        v["docs_exact"] += ok == len(truth) and len(parsed["transactions"]) == len(truth)
        expected = set(lab["patterns"])
        extra = {"payslip_net": lab["payslip_net"], "gst_monthly_turnover": lab["gst_monthly_turnover"]}
        for source, tx, cnt in (("parsed", parsed["transactions"], counts), ("true", truth, counts_true)):
            out = agent.execute({"case": {}, "extra": {**extra, "transactions": tx}}).payload
            predicted = {f["pattern"] for f in out.get("flags", [])}
            for p in PATTERNS:
                cnt[p]["tp"] += p in predicted and p in expected
                cnt[p]["fp"] += p in predicted and p not in expected
                cnt[p]["fn"] += p not in predicted and p in expected
            if source == "parsed":
                if lab["scenario"] == "clean":
                    clean_docs += 1
                    clean_flagged += bool(predicted)
                if predicted != expected:
                    fails.append({"doc": r["id"], "expected": sorted(expected), "predicted": sorted(predicted),
                                  "rows_parsed": f"{ok}/{len(truth)}"})
        v["reconciled"] = v.get("reconciled", 0) + parsed.get("reconciled_share", 0)
    per_pattern = {p: C.prf(**counts[p]) for p in PATTERNS}
    per_pattern_true = {p: C.prf(**counts_true[p]) for p in PATTERNS}
    macro = round(sum(v["f1"] for v in per_pattern.values()) / len(PATTERNS), 4)
    macro_true = round(sum(v["f1"] for v in per_pattern_true.values()) / len(PATTERNS), 4)
    metrics = {
        "macro_f1": macro, "macro_f1_on_true_rows": macro_true,
        "parse_accuracy": round(parse_ok / parse_total, 4), "n_statements": len(rows),
        "per_pattern": per_pattern, "per_pattern_true_rows": per_pattern_true,
        "clean_false_flag_rate": round(clean_flagged / clean_docs, 4) if clean_docs else None,
        "per_variant": {k: {"row_accuracy": round(v["ok"] / v["rows"], 4),
                            "docs_parsed_exactly": round(v["docs_exact"] / v["docs"], 4), "docs": v["docs"],
                            "mean_reconciled_share": round(v["reconciled"] / v["docs"], 4),
                            **{f"parse_{q}": C.latency(lat[k])[f"{q}_s"] for q in ("p50", "p95")}}
                        for k, v in per_variant.items()},
    }
    return C.save("fraud_patterns", metrics, failures=fails[:12])


# ── Fraud Triage ────────────────────────────────────────────────────────────
def _embed(img) -> list | None:
    """SFace embedding of one LFW photo (upscaled onto a canvas for YuNet)."""
    from PIL import Image
    from app.face.service import embed_faces
    im = img.resize((img.width * 3, img.height * 3))
    canvas = Image.new("RGB", (im.width + 200, im.height + 200), (235, 235, 235))
    canvas.paste(im, (100, 100))
    buf = io.BytesIO()
    canvas.save(buf, "JPEG", quality=92)
    faces = embed_faces(buf.getvalue())
    return [float(x) for x in faces[0]["embedding"].ravel()] if faces else None


def _people(n_same_pairs: int, n_singles: int):
    """[(emb_a, emb_b)] two photos of one person, and [emb] for further people —
    every entry a DIFFERENT named LFW person. (A first version drew "unrelated"
    applicants from LFW's pair lists, where the same public figure recurs; the
    agent then correctly linked them, which the evaluation scored as false
    rings.)"""
    from eval.agents.ops_eval import _lfw_people
    people = _lfw_people(n_same_pairs + n_singles + 10)
    pairs, singles = [], []
    for _name, a, b in people:
        if len(pairs) < n_same_pairs:
            ea, eb = _embed(a), _embed(b)
            if ea and eb:
                pairs.append((ea, eb))
        elif len(singles) < n_singles:
            e = _embed(a)
            if e:
                singles.append(e)
    return pairs, singles


def _pan(k: int) -> str:
    return f"ABCP{chr(65 + k % 26)}{1000 + k:04d}{chr(65 + (k * 7) % 26)}"


def eval_triage() -> dict:
    from app.agents.registry import get_agent
    from app.agents.tools import dedupe
    dedupe.reset_index()
    pairs, singles = _people(6, 24)
    cases, truth_rings, truth_cases, already_open = [], [], [], set()
    k = [0]

    def new_case(pan=None, account=None, face=None, sha=None, tier=None, floors=(), app=None):
        cid = str(uuid.uuid4())
        docs = {"pan": {"parsed": {"pan_number": pan or _pan(1000 + k[0])}}}
        if account:
            docs["bank_statement"] = {"parsed": {"account_number": account}}
        if sha:
            docs["salary_slip"] = {"sha256": sha, "parsed": {}}
        k[0] += 1
        case = {"case_id": cid, "application_no": app or f"APP-TRI-{k[0]:03d}", "documents": docs}
        if tier:
            case["assessment"] = {"fraud": {"tier": tier, "floors": list(floors), "fused_score": 0.7 if tier == "HIGH" else 0.4,
                                            "components": {}}}
        dedupe.check(cid, case, register=True)
        if face:
            dedupe.stage_faces(cid, {"pan": [face]})
        cases.append(case)
        return cid

    s = iter(singles)
    # clean applicants
    for _ in range(8):
        new_case(face=next(s))
    # repeat applicants: same PAN, two photos of the same person -> NOT fraud
    for p in range(2):
        pan = _pan(p)
        new_case(pan=pan, face=pairs[p][0])
        new_case(pan=pan, face=pairs[p][1])
    # ring 1: one face under three PANs
    r1 = [new_case(pan=_pan(10), face=pairs[2][0]), new_case(pan=_pan(11), face=pairs[2][1]),
          new_case(pan=_pan(12), face=pairs[2][0])]
    truth_rings.append(set(r1))
    # ring 2: one bank account under three PANs (different people)
    r2 = [new_case(pan=_pan(20 + j), account="50100234567891", face=next(s)) for j in range(3)]
    truth_rings.append(set(r2))
    # pair 1: one PAN, two different people's faces
    p1 = [new_case(pan=_pan(30), face=next(s)), new_case(pan=_pan(30), face=next(s))]
    truth_rings.append(set(p1))
    # pair 2: the same salary-slip file under two PANs
    p2 = [new_case(pan=_pan(40), sha="ab" * 32, face=next(s)), new_case(pan=_pan(41), sha="ab" * 32, face=next(s))]
    truth_rings.append(set(p2))
    # case-level assessments
    for _ in range(3):
        truth_cases.append(new_case(tier="HIGH", face=next(s)))
    for fl in (["face_mismatch_confident"], ["tamper_high"]):
        truth_cases.append(new_case(tier="MEDIUM", floors=fl, face=next(s)))
    for _ in range(3):
        new_case(tier="MEDIUM", face=next(s))           # medium, no floor -> watch, not a case
    opened = new_case(tier="HIGH", face=next(s), app="APP-TRI-OPEN")
    already_open.add("APP-TRI-OPEN")

    with C.Timer() as t:
        res = get_agent("fraud_triage").execute({"cases": cases, "limit": 100,
                                                 "already_open": already_open}).payload
    props = res["proposals"]
    ring_props = [set(p["case_ids"]) for p in props if p["kind"] == "ring"]
    case_props = [p["case_ids"][0] for p in props if p["kind"] == "open_case"]
    tp_r = sum(1 for g in ring_props if g in truth_rings)
    tp_c = sum(1 for c in case_props if c in truth_cases)
    repeat_proposed = sum(1 for g in ring_props if g not in truth_rings)
    precision = (tp_r + tp_c) / len(props) if props else 0.0
    recall = (tp_r + tp_c) / (len(truth_rings) + len(truth_cases))
    metrics = {"proposal_precision": round(precision, 4), "proposal_recall": round(recall, 4),
               "proposals": len(props), "ring_proposals": len(ring_props), "rings_found": tp_r,
               "rings_planted": len(truth_rings), "case_proposals": len(case_props), "cases_found": tp_c,
               "cases_planted": len(truth_cases), "false_ring_proposals": repeat_proposed,
               "already_open_reproposed": int(opened in case_props), "links_found": res["links"],
               "latency_ms": round(t.ms), "population": len(cases), "same_person_pairs_embedded": len(pairs)}
    dedupe.reset_index()
    return C.save("fraud_triage", metrics)
