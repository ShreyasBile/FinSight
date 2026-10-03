"""Case-level evaluation: Cross-Document Verification, Dedupe, Fraud Fusion,
Policy Check, Audit Storage."""
from __future__ import annotations

import json
import random
import subprocess
import tempfile
from pathlib import Path

from eval import common as C

# Hand-labelled Indian name pairs: the level a careful reviewer would assign.
# match = same person; partial = plausibly same person, needs a look (initials,
# middle name, surname change after marriage); mismatch = different people.
NAME_PAIRS = [
    ("Rahul Sharma", "RAHUL SHARMA", "match"), ("R. Sharma", "Rahul Sharma", "match"),
    ("Sharma Rahul", "Rahul Sharma", "match"), ("Mr. Rahul Kumar Sharma", "Rahul Sharma", "match"),
    ("S. K. Gupta", "Suresh Kumar Gupta", "match"), ("Mohd Irfan Khan", "Irfan Khan", "match"),
    ("Smt. Sunita Devi", "Sunita Devi", "match"), ("Dr Anil Mehta", "ANIL MEHTA", "match"),
    ("Priya Sharma", "Priya Verma", "partial"), ("Neha Gupta", "Neha Agarwal", "partial"),
    ("Kavita Rao", "Kavita Menon", "partial"), ("Anjali Singh", "Anjali Kumari Singh", "match"),
    ("Rahul Sharma", "Amit Verma", "mismatch"), ("Pooja Nair", "Rekha Iyer", "mismatch"),
    ("Suresh Gupta", "Ramesh Gupta", "mismatch"), ("Arjun Patel", "Karan Patel", "mismatch"),
    ("Vikram Singh", "Vikram Singh Chauhan", "match"), ("V. S. Chauhan", "Vikram Singh Chauhan", "match"),
    ("Deepak Kumar", "Deepak Yadav", "partial"), ("Manoj Kumar", "Sanjay Kumar", "mismatch"),
    ("Shreya Iyer", "Shreya Iyer", "match"), ("Ishita Bose", "Ishita Das", "partial"),
    ("Kumari Megha", "Megha", "match"), ("Tarun Joshi", "T Joshi", "match"),
    ("Harish Reddy", "Harish Rao", "partial"), ("Gaurav Mishra", "Saurabh Mishra", "mismatch"),
    ("Nikhil Desai", "Nikhil Kulkarni", "partial"), ("Swati Bhatt", "Swati Bhatt Pandey", "match"),
    ("A. Kumar", "Ajay Kumar", "match"), ("Ajay Kumar", "Anil Kumar", "mismatch"),
]


def _corpus(seed: int = 3) -> dict:
    from eval.datasets.synth.identities import expected_fields, make_identity
    rng = random.Random(seed)
    ids = [make_identity(rng, f"P{k}") for k in range(12)]
    validate, identity, cross, dates = [], [], [], []
    for idt in ids:
        for dt in ("pan", "aadhaar", "salary_slip", "bank_statement", "itr", "form16", "gst_return", "cibil"):
            f = expected_fields(idt, dt)
            identity.append([dt, f])
            other = rng.choice(["pan", "aadhaar", "salary_slip", "bank_statement"])
            identity.append([other, f])                               # wrong type asked
            for k, v in f.items():
                validate.append([k, v])
                validate.append([k, str(v)])
            weird = dict(f)
            k = rng.choice(list(weird))
            weird[k] = rng.choice(["", None, "0", "₹ 1,23,456.50", "12abc", True, 0, 3.5, "n/a"])
            identity.append([dt, weird])
        docs = {"pan": {"parsed": expected_fields(idt, "pan"), "label": "PAN Card"},
                "aadhaar": {"parsed": expected_fields(idt, "aadhaar"), "label": "Aadhaar Card"},
                "salary_slip": {"parsed": expected_fields(idt, "salary_slip"), "label": "Salary Slip"}}
        cross.append(docs)
        mix = json.loads(json.dumps(docs))
        mix["salary_slip"]["parsed"]["employee_name"] = rng.choice(["Amit Verma", "Priya " + idt.surname, "R " + idt.surname])
        mix["aadhaar"]["parsed"]["dob"] = rng.choice([idt.dob, "01/01/1990", "1990-01-01", "31.12.1985"])
        mix["aadhaar"]["parsed"]["gender"] = rng.choice(["Male", "Female", "M", "female"])
        mix["land_record"] = {"parsed": {"land_owner": idt.father_name}, "label": "Land Record"}
        cross.append(mix)
        dates += [idt.dob, idt.dob.replace("/", "-"), idt.dob.replace("/", "."), "2026-13-01", "7/3/1990", ""]
    for key, val in [("pincode", "302001"), ("pincode", "012345"), ("employer_tan", "ABCD12345E"),
                     ("registration_number", "MH 12 AB 1234"), ("registration_number", "22 BH 1234 AB"),
                     ("assessment_year", "AY 2024-25"), ("cibil_score", "750"), ("cibil_score", 250),
                     ("net_salary", "0"), ("net_salary", -5), ("account_number", "XXXXXX7890"),
                     ("account_number", "ABCDE1234F"), ("aadhaar_number", "XXXX XXXX 1234"), ("dob", "31/02/1990")]:
        validate.append([key, val])
    return {"validate": validate, "identity": identity, "names": [[a, b] for a, b, _ in NAME_PAIRS],
            "cross": cross, "dates": dates}


def eval_cross_verification() -> dict:
    from app import docrules
    corpus = _corpus()
    tmp = Path(tempfile.mkdtemp()) / "corpus.json"
    tmp.write_text(json.dumps(corpus), encoding="utf-8")
    proc = subprocess.run(["node", str(C.EVAL_DIR / "js_parity.mjs"), str(tmp)], capture_output=True,
                          text=True, encoding="utf-8", cwd=str(C.API_DIR))
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[:500])
    js = json.loads(proc.stdout)
    py = {"validate": [docrules.validate_field(k, v) for k, v in corpus["validate"]],
          "identity": [docrules.check_document_identity(d, p)["ok"] for d, p in corpus["identity"]],
          "names": [docrules.compare_names(a, b)["level"] for a, b in corpus["names"]],
          "cross": [docrules.cross_verify_documents(m) for m in corpus["cross"]],
          "dates": [docrules.parse_date_any(s) for s in corpus["dates"]]}
    diffs, total, same = [], 0, 0
    for k in py:
        for i, (a, b) in enumerate(zip(py[k], js[k])):
            total += 1
            eq = a == b
            same += eq
            if not eq and len(diffs) < 10:
                diffs.append({"kind": k, "i": i, "python": a, "js": b})
    labelled = [(a, b, e, docrules.compare_names(a, b)["level"]) for a, b, e in NAME_PAIRS]
    correct = sum(1 for *_, e, g in labelled if e == g)
    wrong = [{"a": a, "b": b, "expected": e, "got": g} for a, b, e, g in labelled if e != g]
    metrics = {"js_parity": round(same / total, 4), "cases_compared": total,
               "name_pair_accuracy": round(correct / len(labelled), 4), "n_name_pairs": len(labelled),
               "per_expected_level": {lvl: round(sum(1 for *_, e, g in labelled if e == lvl and g == lvl) /
                                                 max(1, sum(1 for *_, e, _g in labelled if e == lvl)), 4)
                                      for lvl in ("match", "partial", "mismatch")}}
    return C.save("cross_verification", metrics, parity_diffs=diffs, name_errors=wrong)


# ── Dedupe (agent 12) ────────────────────────────────────────────────────────
def eval_dedupe(n_base: int = 30) -> dict:
    import hashlib
    from eval.datasets.synth.gen_docs import identities, lfw_faces
    from eval.datasets.synth import render
    from app.agents.tools import dedupe
    from app.face.service import embed_faces
    from eval.datasets.synth.identities import expected_fields
    dedupe.reset_index()
    faces = lfw_faces()
    ids = identities(n_base + 20, seed=41)
    diff_pairs = [f for f in faces if not f[2]]
    same_pairs = [f for f in faces if f[2]]

    def case_for(idt, face, name=None, pan=None, reuse_sha=None, tag=""):
        pan_f = expected_fields(idt, "pan")
        if name:
            pan_f["full_name"] = name
        if pan:
            pan_f["pan_number"] = pan
        card = render.to_jpeg(render.pan_card(idt, face=face, seed=hash(tag) % 997), 90)
        sha = reuse_sha or hashlib.sha256(card).hexdigest()
        embs = [f["embedding"].tolist() for f in embed_faces(card)]
        return {"case_id": f"case-{tag}", "documents": {"pan": {"sha256": sha, "parsed": pan_f}}}, embs, sha

    base = []
    for k in range(n_base):
        c, embs, sha = case_for(ids[k], diff_pairs[k][0], tag=f"b{k}")
        dedupe.stage_faces(c["case_id"], {"pan": embs})
        dedupe.check(c["case_id"], c, register=True)
        base.append((ids[k], c, sha, diff_pairs[k][0]))
    probes = []  # (expected_kind or None, case, embeddings)
    for k in range(5):
        idt, c0, sha, face = base[k]
        c, embs, _ = case_for(ids[n_base + k], diff_pairs[20 + k][1], reuse_sha=sha, tag=f"reuse{k}")
        probes.append(("same_file_reused", c, embs))
    for k in range(5, 10):   # same PAN, different face
        idt, c0, sha, face = base[k]
        c, embs, _ = case_for(ids[n_base + k], diff_pairs[40 + k][1], pan=c0["documents"]["pan"]["parsed"]["pan_number"], tag=f"idtheft{k}")
        probes.append(("same_id_different_face", c, embs))
    for k in range(10, 15):  # same face (same LFW person), different PAN
        a, b, _ = same_pairs[k]
        c_old, embs_old, _ = case_for(ids[k], a, tag=f"sf_old{k}")
        dedupe.stage_faces(c_old["case_id"], {"pan": embs_old})
        dedupe.check(c_old["case_id"], c_old, register=True)
        c, embs, _ = case_for(ids[n_base + k], b, tag=f"sf_new{k}")
        probes.append(("same_face_different_id", c, embs))
    for k in range(15, 20):  # genuinely new applicants
        c, embs, _ = case_for(ids[n_base + k], diff_pairs[80 + k][1], tag=f"new{k}")
        probes.append((None, c, embs))
    tp = fp = fn = 0
    kind_ok = 0
    details = []
    for expected, c, embs in probes:
        dedupe.stage_faces(c["case_id"], {"pan": embs})
        res = dedupe.check(c["case_id"], c, register=False)
        got = res["hits"][0]["kind"] if res["hits"] else None
        strong = bool(res["hits"]) and res["hits"][0]["score"] >= 0.95
        if expected:
            if strong:
                tp += 1
                kind_ok += got == expected
            else:
                fn += 1
        elif strong:
            fp += 1
        details.append({"expected": expected, "got": got, "score": res["score"]})
    pr = C.prf(tp, fp, fn)
    metrics = {"recall": pr["recall"], "precision": pr["precision"], "f1": pr["f1"],
               "kind_accuracy": round(kind_ok / max(1, tp), 4), "n_probes": len(probes), "index_size": n_base + 5,
               "per_kind": {k: round(sum(1 for d in details if d["expected"] == k and d["got"] == k) /
                                     max(1, sum(1 for d in details if d["expected"] == k)), 4)
                            for k in ("same_file_reused", "same_id_different_face", "same_face_different_id")},
               "clean_false_alarms": fp}
    dedupe.reset_index()
    return C.save("dedupe", metrics, details=details)


# ── Fraud Fusion (agent 13) ─────────────────────────────────────────────────
def expert_tier(c: dict, floors: list) -> str:
    """The labelling policy (written before calibration): strong signals are a
    HIGH tamper score, a confident face mismatch, an identity conflict and a
    cross-document name/DOB error; moderate signals are a MEDIUM tamper score,
    failed field checksums and an inconclusive face."""
    strong = sum([
        (c["tampering"] or 0) >= 0.6, "face_mismatch_confident" in floors,
        (c["dedupe"] or 0) >= 0.95, (c["cross_check"] or 0) >= 0.9])
    moderate = sum([0.3 <= (c["tampering"] or 0) < 0.6, (c["validation"] or 0) >= 0.3,
                    c["face"] == 0.20, (c["patterns"] or 0) >= 0.5])
    if strong >= 2 or (strong == 1 and moderate >= 2):
        return "HIGH"
    if strong == 1 or moderate >= 1:
        return "MEDIUM"
    return "LOW"


def _scenarios(n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        face_state = rng.choices(["none", "match", "uncertain", "mismatch"], [2, 5, 2, 1])[0]
        face = {"none": None, "match": 0.0, "uncertain": 0.20, "mismatch": round(rng.uniform(0.7, 1.0), 3)}[face_state]
        tamper = rng.choices([None, round(rng.uniform(0, 0.29), 3), round(rng.uniform(0.3, 0.59), 3),
                              round(rng.uniform(0.6, 1.0), 3)], [1, 6, 2, 1])[0]
        c = {"tampering": tamper, "face": face,
             "cross_check": rng.choices([None, 0.0, 0.3, 0.9], [1, 6, 2, 1])[0],
             "validation": rng.choices([None, 0.0, round(rng.uniform(0.1, 0.29), 3), round(rng.uniform(0.3, 1.0), 3)], [1, 6, 2, 1])[0],
             "dedupe": rng.choices([0.0, 0.35, 0.95, 1.0], [8, 2, 1, 0.5])[0],
             "patterns": rng.choices([None, 0.0, round(rng.uniform(0.5, 0.9), 3)], [5, 3, 1])[0]}
        floors = []
        if face_state == "mismatch":
            floors.append("face_mismatch_confident")
        if c["dedupe"] >= 0.95:
            floors.append("exact_duplicate_identity")
        if (c["tampering"] or 0) >= 0.6:
            floors.append("tamper_high")
        out.append({"c": c, "floors": floors, "expected": expert_tier(c, floors)})
    return out


def eval_fraud_fusion() -> dict:
    import itertools
    from app.agents.tools import fraud_fusion as FF
    calib, held = _scenarios(400, 1), _scenarios(400, 2)

    def run(doc, r):
        c = dict(r["c"])
        if c["face"] == 0.20:                      # 'uncertain' marker -> this doc's mapping
            c["face"] = doc.get("face_uncertain", 0.20)
        return FF.fuse(c, r["floors"], doc)

    def acc(doc, rows):
        return sum(run(doc, r)["tier"] == r["expected"] for r in rows) / len(rows)

    v1 = dict(FF.V1)              # the formula as first designed: weighted sum + floors, no dominant path
    v1_held = acc(v1, held)
    # Stage 1 — weighted sum + floors only (the formula as designed): weights
    # and the MEDIUM threshold, calibration split only.
    best_ws = (acc(v1, calib), v1)
    for wt, wc, wv, wf in itertools.product([0.25, 0.30, 0.35], [0.20, 0.25, 0.30], [0.10, 0.15, 0.20], [0.15, 0.20]):
        for med in (0.15, 0.20, 0.25, 0.30):
            doc = {"weights_version": "v1c", "tiers": {"medium": med, "high": 0.60},
                   "weights": {"tampering": wt, "face": wf, "cross_check": wc, "validation": wv,
                               "dedupe": 0.10, "patterns": 0.10}}
            a = acc(doc, calib)
            if a > best_ws[0] + 1e-9:
                best_ws = (a, doc)
    ws_held = acc(best_ws[1], held)
    # Stage 2 — add the dominant + corroboration path (same mechanics as the
    # tampering fusion), calibration split only.
    base = best_ws[1]
    best = (best_ws[0], base)
    grid = itertools.product([0.20, 0.35], [0.50, 0.59], [0.60, 0.80], [0.40, 0.60], [0.60, 0.80],
                             [0.60, 0.70], [0.10, 0.25, 0.50], [0.20, 0.30])
    for fu, ct, cf, cc, cv, cp, corro, med in grid:
        doc = {**base, "weights_version": "v2", "face_uncertain": fu, "tiers": {"medium": med, "high": 0.60},
               "confidence": {"tampering": ct, "face": cf, "cross_check": cc, "validation": cv,
                              "dedupe": 0.50, "patterns": cp},
               "corroboration_factor": corro}
        a = acc(doc, calib)
        if a > best[0] + 1e-9:
            best = (a, doc)
    v2 = best[1]
    v2_held = acc(v2, held)
    rng = random.Random(9)
    mono_ok = mono_n = 0
    for r in held[:200]:
        for k in r["c"]:
            if r["c"][k] is None or (k == "face" and r["c"][k] == 0.20):  # 'uncertain' is a category
                continue
            up = dict(r); up["c"] = dict(r["c"]); up["c"][k] = min(1.0, r["c"][k] + rng.uniform(0.05, 0.4))
            mono_n += 1
            mono_ok += run(v2, up)["fused_score"] >= run(v2, r)["fused_score"] - 1e-9
    confusion = {}
    for r in held:
        got = run(v2, r)["tier"]
        confusion.setdefault(r["expected"], {}).setdefault(got, 0)
        confusion[r["expected"]][got] += 1
    metrics = {"tier_accuracy_heldout": round(v2_held, 4), "tier_accuracy_heldout_v1": round(v1_held, 4),
               "tier_accuracy_heldout_weighted_only_calibrated": round(ws_held, 4),
               "calibration_accuracy": round(best[0], 4), "monotonicity": round(mono_ok / mono_n, 4),
               "selected": {k: v2.get(k) for k in ("weights", "tiers", "confidence", "corroboration_factor",
                                                    "face_uncertain")},
               "confusion_heldout": confusion, "n_calibration": len(calib), "n_heldout": len(held)}
    (C.RESULTS / "fraud_weights_v2.json").write_text(json.dumps(v2, indent=1))
    return C.save("fraud_fusion", metrics,
                  note="Labels come from a written expert policy (eval/agents/case_eval.py::expert_tier), "
                       "not from real fraud outcomes; calibration used only the calibration split.")


# ── Policy Check (agent 19) ─────────────────────────────────────────────────
COMPLIANT = {
    "application_id": "EVAL-1", "loan_type": "personal_loan",
    "customer": {"kyc_status": "complete", "ovd_verified": True, "pan_available": True,
                 "consent": {"data_collection": True, "credit_bureau_pull": True},
                 "existing_emi": 5000, "proposed_emi": 12000, "monthly_income": 60000, "credit_score": 720},
    "documents": {},
    "loan_terms": {"kfs_provided": True, "apr_disclosed": True, "all_fees_in_kfs": True, "cooling_off_period_days": 3,
                   "disbursal_to_borrower_account": True, "penal_charge_is_interest": False},
    "risk": {"tamper_score": 10, "fraud_score": 12, "face_match_score": 0.55},
}
VIOLATIONS = [
    ("KYC_001", ("customer", "kyc_status", "incomplete")), ("KYC_002", ("customer", "pan_available", False)),
    ("DL_KFS_001", ("loan_terms", "kfs_provided", False)), ("DL_KFS_002", ("loan_terms", "all_fees_in_kfs", False)),
    ("DL_COOL_001", ("loan_terms", "cooling_off_period_days", 0)),
    ("DL_DISB_001", ("loan_terms", "disbursal_to_borrower_account", False)),
    ("DL_CONSENT_001", ("customer", "consent", {"data_collection": False, "credit_bureau_pull": True})),
    ("PENAL_001", ("loan_terms", "penal_charge_is_interest", True)),
    ("POL_FOIR_001", ("customer", "proposed_emi", 40000)), ("POL_CREDIT_001", ("customer", "credit_score", 610)),
    ("RISK_TAMPER_001", ("risk", "tamper_score", 72)), ("RISK_FRAUD_001", ("risk", "fraud_score", 65)),
    ("FACE_001", ("risk", "face_match_score", 0.20)),
]


def eval_policy() -> dict:
    import copy
    from app.agents.registry import get_agent
    C.fresh_compliance_db()
    agent = get_agent("policy_check")
    base = agent.execute({"context": copy.deepcopy(COMPLIANT)}).payload
    detected, rows = 0, []
    for rule_id, (sec, key, val) in VIOLATIONS:
        ctx = copy.deepcopy(COMPLIANT)
        ctx[sec][key] = val
        res = agent.execute({"context": ctx}).payload
        st = {c["rule_id"]: c["status"] for c in res["checks"]}
        others_clean = all(s in ("PASS", "NOT_APPLICABLE") for r, s in st.items() if r != rule_id)
        ok = st.get(rule_id) in ("FAIL", "WARNING") and others_clean
        detected += ok
        rows.append({"rule": rule_id, "status": st.get(rule_id), "isolated": others_clean})
    # missing evidence must never PASS: drop each field instead of violating it
    missing_ok = 0
    for rule_id, (sec, key, _) in VIOLATIONS:
        ctx = copy.deepcopy(COMPLIANT)
        ctx[sec].pop(key, None)
        st = {c["rule_id"]: c["status"] for c in agent.execute({"context": ctx}).payload["checks"]}
        missing_ok += st.get(rule_id) in ("INCONCLUSIVE", "NOT_APPLICABLE")
    # evidence builder: a PAN with a failed checksum must not count as KYC complete
    from app.agents.tools.policy_context import build_context
    case = {"case_id": "c", "documents": {"pan": {"parsed": {"pan_number": "ABCD1234"},
                                                  "validation": {"flags": {"pan_number": "error"}}, "identity_ok": False},
                                          "aadhaar": {"parsed": {}, "validation": {"flags": {}}, "identity_ok": True}}}
    ctx = build_context(case)
    builder_ok = ctx["customer"].get("kyc_status") == "incomplete" and ctx["customer"].get("pan_available") is False
    metrics = {"rule_accuracy": round(detected / len(VIOLATIONS), 4), "baseline_status": base["overall_status"],
               "missing_field_never_passes": round(missing_ok / len(VIOLATIONS), 4),
               "evidence_builder_ok": 1.0 if builder_ok else 0.0, "n_rules": len(VIOLATIONS)}
    return C.save("policy_check", metrics, per_rule=rows)


# ── Audit Storage (agent 27) ────────────────────────────────────────────────
def eval_audit_storage(n: int = 5) -> dict:
    import base64
    import openpyxl
    from app import storage
    from app.agents.registry import get_agent
    from eval.datasets.synth.gen_docs import identities
    from eval.datasets.synth.identities import expected_fields
    tmp = Path(tempfile.mkdtemp(prefix="finsight-store-"))
    storage.STORAGE_DIR, storage.XLSX_PATH = tmp, tmp / "applications.xlsx"
    agent = get_agent("audit_storage")
    ok_files = ok_rows = total_docs = masked_ok = mgr_full = 0
    for k, idt in enumerate(identities(n, seed=77)):
        docs = []
        for dt, rel in (("pan", f"clean/ID00{k}/pan.jpg"), ("aadhaar", f"clean/ID00{k}/aadhaar.jpg")):
            raw = C.read(rel)
            docs.append({"doc_type": dt, "label": dt, "file_name": rel, "mime": "image/jpeg",
                         "fields": expected_fields(idt, dt),
                         "original_b64": "data:image/jpeg;base64," + base64.b64encode(raw).decode(),
                         "masked_b64": "data:image/jpeg;base64," + base64.b64encode(raw[:100]).decode(),
                         "timings": {"extract_ms": 1000}})
        rec = {"app_id": f"APP-EVAL-{k}", "applicant_name": idt.full_name, "loan_type": "Personal Loan",
               "documents": docs}
        res = agent.execute({"record": rec, "persist_sql": False}).payload["store"]
        for d in docs:
            total_docs += 1
            p = tmp / rec["app_id"] / f"{d['doc_type']}_original.jpg"
            ok_files += p.exists() and p.read_bytes() == base64.b64decode(d["original_b64"].split(",")[1])
        ok_rows += res.get("rows") == len(docs)
    wb = openpyxl.load_workbook(storage.XLSX_PATH)
    for row_m, row_o in zip(wb["Manager"].iter_rows(min_row=2, values_only=True),
                            wb["Officer"].iter_rows(min_row=2, values_only=True)):
        fields_o, fields_m = str(row_o[7]), str(row_m[7])
        import re
        masked_ok += not re.search(r"[A-Z]{5}\d{4}[A-Z]|\d{4} \d{4} \d{4}", fields_o)
        mgr_full += bool(re.search(r"[A-Z]{5}\d{4}[A-Z]|\d{4} \d{4} \d{4}", fields_m))
    # SQL write-back (core.customers / loan_applications / uploaded_documents)
    # on throwaway EVAL- applications, read back, then removed
    from app.agents.sql_persist import persist_application
    from app.config import settings
    from eval.agents.ops_eval import _cleanup
    sql_ok = sql_idempotent = 0
    sql_error = None
    import pyodbc
    for k, idt in enumerate(identities(3, seed=78)):
        app_no = f"EVAL-STORE-{k:04d}"
        _cleanup(app_no)
        rec = {"app_id": app_no, "applicant_name": idt.full_name, "loan_type": "Home Loan",
               "documents": [{"doc_type": dt, "fields": expected_fields(idt, dt), "mime": "image/jpeg",
                              "timings": {"extract_ms": 900, "preprocess_ms": 400}}
                             for dt in ("pan", "aadhaar", "salary_slip")]}
        rec["documents"][0]["fields"]["pan_number"] = f"ZZEVA{k:04d}Z"      # never collide with seeded PANs
        try:
            first = persist_application(rec, None)
            again = persist_application(rec, None)
            with pyodbc.connect(settings.db_connection_string, timeout=5) as conn:
                row = conn.execute("""SELECT a.loan_product, a.status_code, a.environment_code, c.pan,
                                             (SELECT COUNT(*) FROM core.uploaded_documents d WHERE d.application_id = a.application_id)
                                      FROM core.loan_applications a JOIN core.customers c ON c.customer_id = a.customer_id
                                      WHERE a.application_no = ?""", (app_no,)).fetchone()
            sql_ok += bool(first.get("ok") and row and row[0] == "home_loan" and row[1] == "submitted"
                           and row[2] == "demo" and row[4] == 3)
            sql_idempotent += bool(again.get("ok") and not again.get("created"))
        except Exception as exc:  # noqa: BLE001
            sql_error = f"{type(exc).__name__}: {str(exc)[:160]}"
        finally:
            _cleanup(app_no)
    metrics = {"roundtrip_integrity": round(ok_files / total_docs, 4), "rows_ok": round(ok_rows / n, 4),
               "officer_masking": round(masked_ok / total_docs, 4), "manager_full_values": round(mgr_full / total_docs, 4),
               "n_applications": n, "n_documents": total_docs,
               "sql_write_success": round(sql_ok / 3, 4), "sql_write_idempotent": round(sql_idempotent / 3, 4),
               "sql_error": sql_error}
    return C.save("audit_storage", metrics)
