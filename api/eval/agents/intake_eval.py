"""Intake crew evaluation: Validation, Preprocessing, Extraction, Document Identity."""
from __future__ import annotations

import io
import json
import random
import string
import time

from eval import common as C


# ── Validation (agent 3) ─────────────────────────────────────────────────────
def eval_validation() -> dict:
    from eval.datasets.synth.identities import make_aadhaar, make_gstin, make_ifsc, make_pan, verhoeff_valid
    from app.agents.registry import get_agent
    rng = random.Random(7)
    cases = []  # (validator, value, expected_flag)
    for _ in range(150):
        pan = make_pan(rng, rng.choice(["Sharma", "Iyer", "Patel"]))
        cases.append(("pan_number", pan, "ok"))
        bad = list(pan)
        i = rng.choice([0, 1, 2, 5, 6, 7, 8, 9])
        bad[i] = rng.choice(string.digits) if i in (0, 1, 2, 9) else rng.choice(string.ascii_uppercase)
        cases.append(("pan_number", "".join(bad), "error"))
        a = make_aadhaar(rng)
        cases.append(("aadhaar_number", f"{a[:4]} {a[4:8]} {a[8:]}", "ok"))
        j = rng.randrange(12)
        mutated = a[:j] + str((int(a[j]) + rng.randint(1, 9)) % 10) + a[j + 1:]
        cases.append(("aadhaar_number", mutated, "error" if not verhoeff_valid(mutated) else "ok"))
        k = rng.randrange(11)
        if a[k] != a[k + 1]:
            swapped = a[:k] + a[k + 1] + a[k] + a[k + 2:]
            cases.append(("aadhaar_number", swapped, "error" if not verhoeff_valid(swapped) else "ok"))
        cases.append(("aadhaar_number", f"XXXX-XXXX-{a[-4:]}", "ok"))          # masked: accepted by design
        ifsc = make_ifsc(rng, rng.choice(["SBIN", "HDFC", "UTIB"]))
        cases.append(("ifsc", ifsc, "ok"))
        cases.append(("ifsc", ifsc[:4] + "1" + ifsc[5:], "error"))             # 5th char must be 0
        g = make_gstin(rng, pan, "Rajasthan")
        cases.append(("gstin", g, "ok"))
        cases.append(("gstin", g[:13] + "Y" + g[14:], "error"))                 # 14th char must be Z
    for v in (300, 725, 900):
        cases.append(("cibil_score", v, "ok"))
    for v in (120, 950):
        cases.append(("cibil_score", v, "warn"))
    agent = get_agent("validation")
    per: dict[str, dict] = {}
    wrong = []
    t0 = time.perf_counter()
    for key, value, expected in cases:
        res = agent.execute({"doc_type": "generic", "parsed": {key: value}}).payload
        got = res["fields"][0]["flag"]
        s = per.setdefault(key, {"n": 0, "correct": 0})
        s["n"] += 1
        s["correct"] += got == expected
        if got != expected:
            wrong.append({"validator": key, "expected": expected, "got": got})
    ms = (time.perf_counter() - t0) * 1000 / len(cases)
    tp = sum(1 for (k, v, e) in cases if e == "error" and agent.execute({"doc_type": "g", "parsed": {k: v}}).payload["fields"][0]["flag"] == "error")
    fn = sum(1 for (_, _, e) in cases if e == "error") - tp
    fp = sum(1 for w in wrong if w["expected"] != "error" and w["got"] == "error")
    metrics = {"accuracy": round(sum(s["correct"] for s in per.values()) / len(cases), 4), "n": len(cases),
               "per_validator": {k: round(s["correct"] / s["n"], 4) for k, s in per.items()},
               "error_detection": C.prf(tp, fp, fn), "ms_per_field": round(ms, 3)}
    return C.save("validation", metrics, failures=wrong[:10])


# ── Preprocessing (agent 1) ─────────────────────────────────────────────────
def _expected_tokens(row) -> list[str]:
    e = row["expected"]
    toks = []
    if row["doc_type"] == "pan":
        toks = [e["pan_number"], *e["full_name"].split(), e["dob"]]
    elif row["doc_type"] == "aadhaar":
        toks = [*e["aadhaar_number"].split(), *e["full_name"].split(), e["dob"]]
    return [C.norm_text(t) for t in toks if t]


def _ocr_recall(img_bytes: bytes, tokens: list[str]) -> float:
    from PIL import Image
    from app.imaging import ocr_tokens
    im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h = im.size
    if max(w, h) > 1600:
        s = 1600 / max(w, h)
        im = im.resize((int(w * s), int(h * s)))
    text = C.norm_text(" ".join(t["text"] for t in ocr_tokens(im)))
    return sum(1 for t in tokens if t and t in text) / len(tokens) if tokens else 0.0


def eval_preprocessing(n_ids: int = 5) -> dict:
    from app.preprocess import doc_langs, preprocess_image
    rows = [r for r in C.manifest("degraded") if int(r["identity"][2:]) < n_ids]
    per_variant: dict[str, list] = {}
    before, after, lat, reverted, stage_ms = [], [], [], 0, {}
    for r in rows:
        data = C.read(r["path"])
        toks = _expected_tokens(r)
        marks = []
        t0 = time.perf_counter()
        out = preprocess_image(data, langs=doc_langs(r["doc_type"]),
                               log_fn=lambda m: marks.append((time.perf_counter(), m)))
        ms = (time.perf_counter() - t0) * 1000
        lat.append(ms)
        for (ta, ma), (tb, _) in zip(marks, marks[1:]):
            if ma.startswith("[Stage"):
                key = ma.split("]")[0] + "]"
                stage_ms.setdefault(key, []).append((tb - ta) * 1000)
        if out == data:
            reverted += 1
        rb, ra = _ocr_recall(data, toks), _ocr_recall(out, toks)
        before.append(rb)
        after.append(ra)
        per_variant.setdefault(r["variant"].split("_")[0] if r["variant"].startswith("rot") else r["variant"], []).append((rb, ra))
    mean = lambda xs: round(sum(xs) / len(xs), 4) if xs else None  # noqa: E731
    lat_s = C.latency(lat)
    metrics = {"field_text_recall_after": mean(after), "field_text_recall_before": mean(before),
               "recall_gain": round(mean(after) - mean(before), 4), "n_images": len(rows),
               "reverted": reverted, "p50_s": lat_s["p50_s"], "p95_s": lat_s["p95_s"],
               "per_variant": {k: {"before": mean([b for b, _ in v]), "after": mean([a for _, a in v]), "n": len(v)}
                               for k, v in per_variant.items()},
               "stage_p50_ms": {k: round(C.pct(v, 0.5)) for k, v in sorted(stage_ms.items())}}
    return C.save("preprocessing", metrics)


# ── Extraction (agent 2) ────────────────────────────────────────────────────
EXTRACT_VARIANTS = ["pan", "aadhaar", "salary_slip", "bank_statement", "itr", "form16", "gst_return",
                    "salary_slip_photo"]


def _score_fields(expected: dict, parsed: dict | None):
    parsed = parsed or {}
    res = {k: C.field_match(k, v, parsed.get(k)) for k, v in expected.items()}
    return res


def eval_extraction(n_ids: int = 6, degraded_ids: int = 3) -> dict:
    cl = C.client()
    rows = [r for r in C.manifest("clean") if int(r["identity"][2:]) < n_ids and r["variant"] in EXTRACT_VARIANTS]
    field_hits, field_n, docs_ok, json_ok, lat, per_type, per_field_miss = 0, 0, 0, 0, [], {}, {}
    failures = []
    for r in rows:
        data = C.read(r["path"])
        with C.Timer() as t:
            resp = cl.post("/extract", data={"doc_type": r["doc_type"]},
                           files={"file": (r["path"].split("/")[-1], data, r["mime"])})
        lat.append(t.ms)
        body = resp.json() if resp.status_code == 200 else {}
        parsed = body.get("parsed") if resp.status_code == 200 else None
        json_ok += bool(parsed)
        m = _score_fields(r["expected"], parsed)
        hits = sum(m.values())
        field_hits += hits
        field_n += len(m)
        docs_ok += all(m.values())
        s = per_type.setdefault(r["variant"], {"fields": 0, "hits": 0, "docs": 0, "all_ok": 0})
        s["fields"] += len(m); s["hits"] += hits; s["docs"] += 1; s["all_ok"] += all(m.values())
        for k, ok in m.items():
            if not ok:
                per_field_miss[f"{r['doc_type']}.{k}"] = per_field_miss.get(f"{r['doc_type']}.{k}", 0) + 1
        if not all(m.values()) and len(failures) < 12:
            failures.append({"doc": r["variant"], "missed": [k for k, ok in m.items() if not ok],
                             "status": resp.status_code})
    # degraded ID cards (robustness)
    drows = [r for r in C.manifest("degraded") if int(r["identity"][2:]) < degraded_ids]
    d_hits = d_n = 0
    per_deg = {}
    for r in drows:
        data = C.read(r["path"])
        resp = cl.post("/extract", data={"doc_type": r["doc_type"]}, files={"file": ("x.jpg", data, "image/jpeg")})
        parsed = resp.json().get("parsed") if resp.status_code == 200 else None
        m = _score_fields(r["expected"], parsed)
        d_hits += sum(m.values()); d_n += len(m)
        key = r["variant"].rstrip("0123456789") if r["variant"].startswith("rot") else r["variant"]
        pd = per_deg.setdefault(key, [0, 0]); pd[0] += sum(m.values()); pd[1] += len(m)
    lat_s = C.latency(lat)
    metrics = {"field_accuracy": round(field_hits / field_n, 4), "doc_all_correct": round(docs_ok / len(rows), 4),
               "json_ok_rate": round(json_ok / len(rows), 4), "n_docs": len(rows), "n_fields": field_n,
               "degraded_field_accuracy": round(d_hits / d_n, 4) if d_n else None, "n_degraded": len(drows),
               "per_doc_type": {k: {"field_accuracy": round(v["hits"] / v["fields"], 4),
                                    "all_correct": round(v["all_ok"] / v["docs"], 4), "n": v["docs"]}
                                for k, v in per_type.items()},
               "per_degradation": {k: round(v[0] / v[1], 4) for k, v in per_deg.items()},
               "most_missed_fields": dict(sorted(per_field_miss.items(), key=lambda x: -x[1])[:10]),
               "p50_s": lat_s["p50_s"], "p95_s": lat_s["p95_s"]}
    return C.save("extraction", metrics, failures=failures, model="ollama qwen2.5vl:7b (local)")


# ── Document Identity (agent 4) ─────────────────────────────────────────────
WRONG_PAIRS = [("pan", "aadhaar"), ("aadhaar", "pan"), ("salary_slip", "bank_statement"),
               ("bank_statement", "salary_slip"), ("pan", "salary_slip"), ("itr", "aadhaar"),
               ("gst_return", "pan"), ("form16", "bank_statement")]


def eval_doc_identity(n_ids: int = 3) -> dict:
    """Upload the WRONG document for a requested type through the real
    extraction model, then see whether the gate catches it; also count how
    often a correct upload is wrongly blocked."""
    cl = C.client()
    man = {(r["identity"], r["variant"]): r for r in C.manifest("clean")}
    caught = total_wrong = blocked = total_right = 0
    detail = []
    for i in range(n_ids):
        uid = f"ID{i:03d}"
        for asked, actual in WRONG_PAIRS:
            r = man[(uid, actual)]
            resp = cl.post("/extract", data={"doc_type": asked},
                           files={"file": (r["path"].split("/")[-1], C.read(r["path"]), r["mime"])})
            ok = (resp.json().get("document_identity") or {}).get("ok", True) if resp.status_code == 200 else False
            total_wrong += 1
            caught += not ok
            detail.append({"asked": asked, "uploaded": actual, "caught": not ok})
        for dt in ["pan", "aadhaar", "salary_slip", "bank_statement", "itr", "form16", "gst_return"]:
            r = man[(uid, dt)]
            resp = cl.post("/extract", data={"doc_type": dt},
                           files={"file": (r["path"].split("/")[-1], C.read(r["path"]), r["mime"])})
            ok = (resp.json().get("document_identity") or {}).get("ok", True) if resp.status_code == 200 else False
            total_right += 1
            blocked += not ok
    metrics = {"wrong_doc_recall": round(caught / total_wrong, 4), "false_block_rate": round(blocked / total_right, 4),
               "n_wrong": total_wrong, "n_right": total_right,
               "missed": [d for d in detail if not d["caught"]][:10]}
    return C.save("doc_identity", metrics)
