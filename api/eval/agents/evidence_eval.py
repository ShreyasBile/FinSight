"""Identity & fraud-evidence crew evaluation: Face Verification, Tampering
Forensics, Authenticity, Tamper Explainer, Tamper Locator."""
from __future__ import annotations

import copy
import io
import json
import random

from eval import common as C

_INTEGRITY_CACHE: dict = {}

# document type of each file in document_tampering/document tests (checked by eye)
_REAL_TYPES = {
    "62988XXXX_DownloadStatement_1783510941_unlocked.pdf": "bank_statement",
    "Screenshot 2026-06-08 113017.png": "aadhaar",
    "WhatsApp Image 2026-06-05 at 4.22.07 PM.jpeg": "aadhaar",
    "WhatsApp Image 2026-06-09 at 3.49.59 PM.jpeg": "aadhaar",
    "WhatsApp Image 2026-07-08 at 5.38.06 PM.jpeg": "vehicle_quotation",
    "pan card shreyas.pdf": "pan",
    "PaySlip_shreyas_bile_July_2026.pdf": "salary_slip",
}


# ── Face Verification (agent 6) ─────────────────────────────────────────────
def _card_jpeg(maker, idt, face, seed, washed=False):
    from PIL import ImageEnhance
    from eval.datasets.synth import forge, render
    im = forge.camera_capture(maker(idt, face=face, seed=seed), random.Random(seed))
    if washed:  # over-exposed ID photo (the real PAN in storage/ is like this)
        im = ImageEnhance.Brightness(im).enhance(1.9)
        im = ImageEnhance.Contrast(im).enhance(0.45)
    return render.to_jpeg(im, 88)


def eval_face(n_pairs: int = 160, n_washed: int = 30) -> dict:
    from eval.datasets.synth.gen_docs import identities, lfw_faces
    from eval.datasets.synth import render
    from app.agents.registry import get_agent
    agent = get_agent("face_verification")
    faces = lfw_faces()
    same = [f for f in faces if f[2]][: n_pairs // 2]
    diff = [f for f in faces if not f[2]][: n_pairs // 2]
    ids = identities(len(same) + len(diff) + n_washed, seed=99)
    rows, lat = [], []
    for k, (fa, fb, label) in enumerate(same + diff):
        idt = ids[k]
        docs = [("pan", _card_jpeg(render.pan_card, idt, fa, k)),
                ("aadhaar", _card_jpeg(render.aadhaar_card, idt, fb, k + 7))]
        with C.Timer() as t:
            res = agent.execute({"documents": docs}).payload["result"]
        lat.append(t.ms)
        found = all(d["face_count"] > 0 for d in res["documents"])
        pair = res["pairs"][0] if res["pairs"] else None
        rows.append({"same": label, "found": found, "verdict": pair["verdict"] if pair else None,
                     "sim": pair["similarity"] if pair else None, "overall": res["overall"],
                     "low_quality": any(f.get("quality") == "low" for d in res["documents"] for f in d["faces"])})
    # washed-out cards: the low-quality rule must hold (never a confident mismatch)
    washed_rows = []
    for k in range(n_washed):
        fa, fb, label = diff[k % len(diff)]
        idt = ids[len(same) + len(diff) + k]
        docs = [("pan", _card_jpeg(render.pan_card, idt, fa, 500 + k, washed=True)),
                ("aadhaar", _card_jpeg(render.aadhaar_card, idt, fb, 900 + k))]
        res = agent.execute({"documents": docs}).payload["result"]
        pair = res["pairs"][0] if res["pairs"] else None
        lowq = any(f.get("quality") == "low" for d in res["documents"] for f in d["faces"])
        washed_rows.append({"low_quality": lowq, "verdict": pair["verdict"] if pair else None})
    detected = [r for r in rows if r["found"] and r["verdict"]]
    confident = [r for r in detected if r["verdict"] in ("match", "different")]
    correct = [r for r in confident if (r["verdict"] == "match") == r["same"]]
    sims_same = [r["sim"] for r in detected if r["same"]]
    sims_diff = [r["sim"] for r in detected if not r["same"]]
    # TAR at FAR = 1%: threshold = 99th percentile of impostor scores
    thr = C.pct(sims_diff, 0.99) if sims_diff else None
    tar = sum(1 for s in sims_same if thr is not None and s > thr) / len(sims_same) if sims_same else None
    acc_0363 = sum(1 for r in detected if (r["sim"] >= 0.363) == r["same"]) / len(detected) if detected else None
    lq_all = [r for r in rows + washed_rows if r["low_quality"]]
    lq_ok = all(r["verdict"] != "different" for r in lq_all)
    lat_s = C.latency(lat)
    metrics = {"verdict_accuracy": round(len(correct) / len(confident), 4) if confident else None,
               "confident_coverage": round(len(confident) / len(detected), 4) if detected else None,
               "detection_rate": round(sum(r["found"] for r in rows) / len(rows), 4),
               "accuracy_at_0363": round(acc_0363, 4) if acc_0363 is not None else None,
               "tar_at_far_1pct": round(tar, 4) if tar is not None else None,
               "auc": C.auc(sims_same, sims_diff),
               "false_match_rate": round(sum(1 for r in confident if r["verdict"] == "match" and not r["same"])
                                         / max(1, sum(1 for r in confident if not r["same"])), 4),
               "false_mismatch_rate": round(sum(1 for r in confident if r["verdict"] == "different" and r["same"])
                                            / max(1, sum(1 for r in confident if r["same"])), 4),
               "low_quality_never_mismatch": 1.0 if lq_ok else 0.0, "low_quality_cases": len(lq_all),
               "washed_out_detected_as_low_quality": round(sum(r["low_quality"] for r in washed_rows) / max(1, len(washed_rows)), 4),
               "n_pairs": len(rows), "p50_s": lat_s["p50_s"], "p95_s": lat_s["p95_s"]}
    return C.save("face_verification", metrics,
                  data="LFW test pairs (public) pasted onto synthetic PAN/Aadhaar cards, camera-simulated")


# ── Tampering Forensics (agent 7) + Authenticity (8) ────────────────────────
def _integrity_runs():
    """Run every tamper_cam document through /document/integrity once
    (authenticity ON, local model). Cached for the authenticity/explainer/
    locator evaluators that reuse the same results."""
    if _INTEGRITY_CACHE:
        return _INTEGRITY_CACHE["rows"]
    cl = C.client()
    rows = []
    for r in [x for x in C.manifest("tamper_cam") if int(x["identity"][2:]) < 8]:
        data = C.read(r["path"])
        with C.Timer() as t:
            resp = cl.post("/document/integrity", data={"doc_type": r["doc_type"],
                                                        "parsed_json": json.dumps(r["ocr_fields"])},
                           files={"file": (r["path"].split("/")[-1], data, r["mime"])})
        t_obj = resp.json().get("tampering") if resp.status_code == 200 else None
        auth = next((s for s in (t_obj or {}).get("signals", []) if s["name"] == "authenticity"), None)
        rows.append({**r, "tampering": t_obj, "ms": t.ms,
                     "auth_score": auth["score"] if auth and auth["available"] else None,
                     "auth_available": bool(auth and auth["available"])})
    # real documents (local manifest; labels by inspection) and the external
    # paired original/tampered set — extracted first, exactly like production
    real_man = C.REAL / "manifest.json"
    ext_man = C.REAL / "external_manifest.json"
    extra = []
    if real_man.exists():
        for r in json.loads(real_man.read_text()):
            if r["label"] in ("genuine", "fabricated"):
                extra.append({**r, "set": "real",
                              "label": {"forged": r["label"] == "fabricated",
                                        "attack": "real_fabricated" if r["label"] == "fabricated" else "none"}})
    if ext_man.exists():
        extra += [{**r, "set": "external"} for r in json.loads(ext_man.read_text())]
    for r in extra:
        data = open(r["path"], "rb").read()
        name = r["path"].replace("\\", "/").split("/")[-1]
        ex = cl.post("/extract", data={"doc_type": r["doc_type"]}, files={"file": (name, data, r["mime"])})
        parsed = ex.json().get("parsed", {}) if ex.status_code == 200 else {}
        with C.Timer() as t:
            resp = cl.post("/document/integrity", data={"doc_type": r["doc_type"], "parsed_json": json.dumps(parsed)},
                           files={"file": (name, data, r["mime"])})
        t_obj = resp.json().get("tampering") if resp.status_code == 200 else None
        auth = next((sg for sg in (t_obj or {}).get("signals", []) if sg["name"] == "authenticity"), None)
        rows.append({**r, "tampering": t_obj, "ms": t.ms, "_bytes": data,
                     "auth_score": auth["score"] if auth and auth["available"] else None,
                     "auth_available": bool(auth and auth["available"])})
    _INTEGRITY_CACHE["rows"] = rows
    return rows


def eval_tampering() -> dict:
    rows = _integrity_runs()
    synth = [r for r in rows if r["set"] == "tamper_cam" and r["tampering"]]
    real = [r for r in rows if r["set"] == "real" and r["tampering"]]
    ext = [r for r in rows if r["set"] == "external" and r["tampering"]]
    forged = [r for r in synth if r["label"]["forged"]]
    genuine = [r for r in synth if not r["label"]["forged"]]
    tier = lambda r: r["tampering"]["risk_tier"]  # noqa: E731
    per_attack = {}
    for r in forged:
        a = r["label"]["attack"]
        s = per_attack.setdefault(a, {"n": 0, "flagged": 0, "high": 0})
        s["n"] += 1; s["flagged"] += tier(r) in ("MEDIUM", "HIGH"); s["high"] += tier(r) == "HIGH"
    per_genuine = {}
    for r in genuine:
        s = per_genuine.setdefault(r["variant"], {"n": 0, "low": 0, "medium": 0, "high": 0})
        s["n"] += 1; s[tier(r).lower()] += 1
    lat = C.latency([r["ms"] for r in synth])
    metrics = {
        "forged_recall": round(sum(tier(r) in ("MEDIUM", "HIGH") for r in forged) / len(forged), 4),
        "forged_high_rate": round(sum(tier(r) == "HIGH" for r in forged) / len(forged), 4),
        "genuine_false_high": round(sum(tier(r) == "HIGH" for r in genuine) / len(genuine), 4),
        "genuine_flagged_any": round(sum(tier(r) != "LOW" for r in genuine) / len(genuine), 4),
        "auc": C.auc([r["tampering"]["risk_score"] for r in forged], [r["tampering"]["risk_score"] for r in genuine]),
        "utility_resave_is_medium": round(sum(tier(r) == "MEDIUM" for r in forged if r["label"]["attack"] == "pdf_utility")
                                          / max(1, sum(1 for r in forged if r["label"]["attack"] == "pdf_utility")), 4),
        "per_attack": {k: {"recall": round(v["flagged"] / v["n"], 4), "high": round(v["high"] / v["n"], 4), "n": v["n"]}
                       for k, v in per_attack.items()},
        "genuine_tiers": per_genuine,
        "real_documents": {
            "genuine": {t: sum(1 for r in real if not r["label"]["forged"] and tier(r) == t) for t in ("LOW", "MEDIUM", "HIGH")},
            "fabricated": {t: sum(1 for r in real if r["label"]["forged"] and tier(r) == t) for t in ("LOW", "MEDIUM", "HIGH")},
            "by_type": [{"doc_type": r["doc_type"], "label": "fabricated" if r["label"]["forged"] else "genuine",
                         "tier": tier(r), "score": r["tampering"]["risk_score"], "note": r.get("note", "")} for r in real]},
        "external_pairs": {
            "forged_recall": round(sum(tier(r) != "LOW" for r in ext if r["label"]["forged"]) /
                                   max(1, sum(1 for r in ext if r["label"]["forged"])), 4),
            "genuine_flagged": round(sum(tier(r) != "LOW" for r in ext if not r["label"]["forged"]) /
                                     max(1, sum(1 for r in ext if not r["label"]["forged"])), 4),
            "auc": C.auc([r["tampering"]["risk_score"] for r in ext if r["label"]["forged"]],
                         [r["tampering"]["risk_score"] for r in ext if not r["label"]["forged"]]),
            "n": len(ext)},
        "n_forged": len(forged), "n_genuine": len(genuine), "p50_s": lat["p50_s"], "p95_s": lat["p95_s"]}
    return C.save("tampering_forensics", metrics,
                  data="120 camera-simulated synthetic documents (8 identities); 14 real documents "
                       "(labelled by inspection); 90 external original/tampered paystub + statement images")


def eval_authenticity() -> dict:
    rows = [r for r in _integrity_runs() if r["tampering"] and r["doc_type"] in ("salary_slip", "bank_statement")
            and r["set"] in ("tamper_cam", "real")]
    fabricated = [r for r in rows if r["label"]["attack"] in ("fabricated", "real_fabricated")]
    genuine = [r for r in rows if not r["label"]["forged"]]
    ran = [r for r in rows if r["auth_available"]]
    flagged = lambda r: (r["auth_score"] or 0) >= 0.5  # noqa: E731
    # cap: whenever authenticity is the ONLY signal that fired, the tier must not be HIGH
    lone, lone_high = 0, 0
    for r in rows:
        sigs = [s for s in r["tampering"]["signals"] if s["available"] and (s["score"] or 0) >= 0.2]
        if len(sigs) == 1 and sigs[0]["name"] == "authenticity":
            lone += 1
            lone_high += r["tampering"]["risk_tier"] == "HIGH"
    metrics = {"fabricated_recall": round(sum(flagged(r) for r in fabricated) / len(fabricated), 4) if fabricated else None,
               "genuine_false_flag": round(sum(flagged(r) for r in genuine) / len(genuine), 4) if genuine else None,
               "cap_respected": 1.0 if lone_high == 0 else round(1 - lone_high / lone, 4),
               "lone_authenticity_cases": lone,
               "ran_rate": round(len(ran) / len(rows), 4) if rows else None,
               "per_format": {fmt: round(sum(flagged(r) for r in fabricated if r["mime"] == mt) /
                                         max(1, sum(1 for r in fabricated if r["mime"] == mt)), 4)
                              for fmt, mt in (("pdf", "application/pdf"), ("photo", "image/jpeg"))},
               "real_fabricated_flagged": sum(flagged(r) for r in fabricated if r["set"] == "real"),
               "real_fabricated_n": sum(1 for r in fabricated if r["set"] == "real"),
               "real_genuine_flagged": sum(flagged(r) for r in genuine if r["set"] == "real"),
               "real_genuine_n": sum(1 for r in genuine if r["set"] == "real"),
               "n_fabricated": len(fabricated), "n_genuine": len(genuine)}
    return C.save("authenticity", metrics, model="ollama qwen2.5vl:7b (local) + deterministic placeholder scan")


def eval_explainer(limit: int = 40) -> dict:
    from app.agents.registry import get_agent
    agent = get_agent("tamper_explainer")
    flagged = [r for r in _integrity_runs() if r["tampering"] and r["tampering"]["risk_tier"] != "LOW"][:limit]
    grounded_ok = produced = unchanged = 0
    dropped_total, lat = 0, []
    for r in flagged:
        before = copy.deepcopy(r["tampering"])
        data = r.get("_bytes") or C.read(r["path"])
        with C.Timer() as t:
            out = agent.execute({"doc_label": r["doc_type"], "doc_type": r["doc_type"],
                                 "tampering": r["tampering"], "data": data})
        lat.append(t.ms)
        unchanged += r["tampering"] == before
        if out.payload:
            produced += 1
            dropped = sum(int(w.split()[1]) for w in out.warnings if w.startswith("dropped"))
            dropped_total += dropped
            grounded_ok += dropped == 0
    lat_s = C.latency(lat)
    metrics = {"grounded_rate": round(grounded_ok / produced, 4) if produced else None,
               "tier_unchanged": round(unchanged / len(flagged), 4) if flagged else None,
               "produced_rate": round(produced / len(flagged), 4) if flagged else None,
               "sentences_dropped": dropped_total, "n": len(flagged),
               "p50_s": lat_s["p50_s"], "p95_s": lat_s["p95_s"]}
    return C.save("tamper_explainer", metrics, model="ollama qwen2.5vl:7b (local), vision on")


def eval_locator() -> dict:
    """Box hit: does a drawn box overlap the region that was actually edited?"""
    from PIL import Image
    from app.imaging import load_image, ocr_tokens
    from app.tampering import locate as L
    rows = [r for r in _integrity_runs() if r.get("label", {}).get("edited_box") and r["tampering"]
            and r["tampering"]["risk_tier"] != "LOW"]
    hits = locatable = 0
    per = {}
    for r in rows:
        data = C.read(r["path"])
        im = load_image(data)
        W0 = Image.open(io.BytesIO(data)).size[0]
        s = im.size[0] / W0
        ex0, ey0, ex1, ey1 = [v * s for v in r["label"]["edited_box"]]
        tokens = ocr_tokens(im)
        boxes = []
        for e in r["tampering"]["explanations"]:
            if any(sk in e for sk in L._SKIP):
                continue
            nums, quoted = L._anchors(L._clean_finding(e))
            if nums or quoted:
                boxes += L._match(nums, quoted, tokens)
        a = r["label"]["attack"]
        st = per.setdefault(a, {"n": 0, "hit": 0, "boxed": 0})
        st["n"] += 1
        if boxes:
            locatable += 1
            st["boxed"] += 1
        best = max((_iou((ex0, ey0, ex1, ey1), b) for b in boxes), default=0.0)
        if best >= 0.5 or any(_inside(b, (ex0, ey0, ex1, ey1)) for b in boxes):
            hits += 1
            st["hit"] += 1
    metrics = {"box_hit_rate": round(hits / len(rows), 4) if rows else None,
               "boxed_rate": round(locatable / len(rows), 4) if rows else None, "n": len(rows),
               "per_attack": {k: {"hit": round(v["hit"] / v["n"], 4), "boxed": round(v["boxed"] / v["n"], 4), "n": v["n"]}
                              for k, v in per.items()}}
    return C.save("tamper_locator", metrics,
                  note="hit = a drawn box overlaps the edited region with IoU >= 0.5 or lies inside it")


def _iou(a, b) -> float:
    ix0, iy0, ix1, iy1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def _inside(b, region) -> bool:
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return region[0] <= cx <= region[2] and region[1] <= cy <= region[3]
