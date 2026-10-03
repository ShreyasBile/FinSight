"""Tampering Forensics, Authenticity, Tamper Locator, Tamper Explainer on real forged documents.

Datasets
  * Find it again! (Univ. La Rochelle, ICDAR 2023) — 988 real scanned receipts, 163 forged by people
    (copy-paste, imitation, pixel edits in Paint / GIMP / Preview...), forged regions boxed. Official
    split: train + val = dev, test = test (218 receipts, 35 forged).
  * SIDTD templates — genuine ID-card / passport templates (MIDV-2020 mock identities) and forgeries
    made with crop-and-replace and inpaint-and-rewrite of one field; split by SOURCE template so a
    forgery never sits in a different split from its original.

Two runs, kept apart on purpose:
  production  what the app does. Receipts go in as "vehicle_quotation" (FinSight's closest document:
              a priced commercial document with a merchant header) with field extraction first, exactly
              like an upload. The authenticity check does not run for that type in production, so it
              does not run here. ID cards go in as "pan" with forensics only: field rules and the
              authenticity prompt are written for Indian PAN/Aadhaar and cannot judge a Finnish ID.
              -> Tampering Forensics, Tamper Locator, Tamper Explainer
  truthful    the authenticity check with the document described as what it is ("retail purchase
              receipt", "identity card or passport issued by a European government"). An evaluation-only
              override: the app has no such document types, and a mislabelled document would be flagged
              for not being a PAN card, which measures nothing. -> Authenticity
"""
from __future__ import annotations

import ast
import copy
import csv
import io
import json
from contextlib import contextmanager
from pathlib import Path

from eval import common as C
from eval.real import core as R

FINDIT = R.RAW / "findit2" / "findit2"
SIDTD = R.RAW / "sidtd" / "templates"

LABELS = {"receipt": ("retail purchase receipt issued by a shop or restaurant",
                      "the shop name and address, date and time, itemised lines with prices, subtotal, tax and "
                      "total, and the payment method"),
          "id_card_eu": ("identity card or passport issued by a European government",
                         "the issuing country and document title, the holder's photograph, name, date of birth, "
                         "dates of issue and expiry, a document number, a signature and printed security patterns")}


# ── data ────────────────────────────────────────────────────────────────────
def findit_items(split: str):
    files = ["train", "val"] if split == "dev" else ["test"]
    out = []
    for f in files:
        for r in csv.DictReader(open(FINDIT / f"{f}.txt", encoding="utf-8")):
            forged = r["forged"] == "1"
            regions, entities, software = [], [], None
            if forged and r["forgery annotations"] not in ("0", ""):
                ann = ast.literal_eval(r["forgery annotations"])
                software = (ann.get("file_attributes") or {}).get("Software used")
                for g in ann.get("regions", []):
                    ra, sa = g["region_attributes"], g["shape_attributes"]
                    if ra.get("Original area") == "no" and sa.get("name") == "rect":
                        regions.append((sa["x"], sa["y"], sa["x"] + sa["width"], sa["y"] + sa["height"]))
                        entities.append(ra.get("Entity type"))
            out.append({"id": f"findit:{r['image']}", "set": "findit", "path": FINDIT / f / r["image"], "forged": forged,
                        "regions": regions, "kind": (software or "none").lower() if forged else "genuine",
                        "entities": entities, "mime": "image/png"})
    return out


def _field_boxes():
    """{template image name: {field: (x0, y0, x1, y1)}} from SIDTD's genuine-template annotations."""
    boxes = {}
    for f in (SIDTD / "Annotations" / "reals").glob("*.json"):
        meta = json.loads(f.read_text(encoding="utf-8")).get("_via_img_metadata", {})
        for m in meta.values():
            d = {}
            for r in m["regions"]:
                sa, name = r["shape_attributes"], r["region_attributes"].get("field_name")
                if sa.get("name") == "rect":
                    d[name] = (sa["x"], sa["y"], sa["x"] + sa["width"], sa["y"] + sa["height"])
                elif "all_points_x" in sa:
                    d[name] = (min(sa["all_points_x"]), min(sa["all_points_y"]), max(sa["all_points_x"]), max(sa["all_points_y"]))
            boxes[f"{f.stem}_{m['filename']}"] = d
    return boxes


def sidtd_items(split: str, n_each: int = 200):
    boxes = _field_boxes()
    reals = []
    for p in sorted((SIDTD / "Images" / "reals").glob("*.jpg")):
        reals.append({"id": f"sidtd:{p.name}", "set": "sidtd", "path": p, "forged": False, "group": p.stem,
                      "regions": [], "kind": "genuine", "mime": "image/jpeg"})
    fakes = []
    for p in sorted((SIDTD / "Images" / "fakes").glob("*.jpg")):
        a = json.loads((SIDTD / "Annotations" / "fakes" / f"{p.stem}.json").read_text(encoding="utf-8"))
        src = Path(a["src"]).stem
        box = boxes.get(a["src"], {}).get(a["field"])
        fakes.append({"id": f"sidtd:{p.name}", "set": "sidtd", "path": p, "forged": True, "group": src,
                      "regions": [box] if box else [], "kind": a["ctype"].lower(), "field": a["field"],
                      "mime": "image/jpeg"})
    reals = R.take(reals, split, group=lambda x: x["group"])
    fakes = R.take(fakes, split, group=lambda x: x["group"])
    return R.sample(reals, n_each) + R.stratified(fakes, n_each, strata=lambda x: x["kind"])


# ── running the app's own flows ─────────────────────────────────────────────
@contextmanager
def authenticity_on(flag: bool, extra_types: str = ""):
    from app.config import settings
    old = (settings.tampering_authenticity_enabled, settings.tampering_authenticity_doc_types)
    settings.tampering_authenticity_enabled = flag
    if extra_types:
        settings.tampering_authenticity_doc_types = old[1] + "," + extra_types
    try:
        yield
    finally:
        settings.tampering_authenticity_enabled, settings.tampering_authenticity_doc_types = old


def _summ(t: dict | None, ms: float) -> dict:
    if not t:
        return {"tier": None, "score": None, "ms": round(ms, 1)}
    sig = {s["name"]: (round(s["score"], 4) if s["score"] is not None else None)
           for s in t.get("signals", []) if s.get("available")}
    return {"tier": t["risk_tier"], "score": round(t["risk_score"], 4), "signals": sig,
            "explanations": t.get("explanations", [])[:10], "fast_tracked": t.get("fast_tracked"), "ms": round(ms, 1)}


def production_run(it: dict) -> dict:
    """Exactly the upload path: extract fields, then the integrity cascade."""
    from app.agents.flows import document_flows as flows
    data = it["path"].read_bytes()
    name = it["path"].name
    if it["set"] == "findit":
        doc_type = "vehicle_quotation"
        ex = flows.run_extract(data, it["mime"], doc_type, name)
        parsed = ((ex.get("extraction") or {}).get("parsed")) or {}
        with authenticity_on(True), C.Timer() as t:          # skipped by type in production anyway
            out = flows.run_integrity(data, name, doc_type, parsed)
    else:
        doc_type = "pan"
        with authenticity_on(False), C.Timer() as t:
            out = flows.run_integrity(data, name, doc_type, {})
    return {**_summ(out.get("tampering"), t.ms), "doc_type": doc_type}


def truthful_run(it: dict) -> dict:
    from app.agents.flows import document_flows as flows
    from app.tampering.signals import authenticity as A
    kind = "receipt" if it["set"] == "findit" else "id_card_eu"
    A._DOC_LABELS[kind], A._DOC_EXPECTED_DETAILS[kind] = LABELS[kind]
    data = it["path"].read_bytes()
    with authenticity_on(True, extra_types=kind), C.Timer() as t:
        out = flows.run_integrity(data, it["path"].name, kind, {})
    s = _summ(out.get("tampering"), t.ms)
    s["auth"] = (s.get("signals") or {}).get("authenticity")
    return s


N_FINDIT = None        # smoke runs only: limit the receipts (None = the whole official split)


def _items(split: str, n_sidtd: int):
    f = findit_items(split)
    if N_FINDIT:
        f = R.stratified(f, N_FINDIT, strata=lambda x: x["forged"])
    return f + sidtd_items(split, n_sidtd)


def _runs(split: str, n_sidtd: int, which: str):
    items = _items(split, n_sidtd)
    fn, ver = (production_run, "prod-v1") if which == "production" else (truthful_run, "truth-v1")
    cache = R.ItemCache("evidence", f"{ver}-{split}")
    out = cache.run(items, fn, label=which, every=25,
                    on_error={"tier": None, "score": None, "ms": None, "doc_type": None, "explanations": [],
                              "signals": {}, "auth": None})
    rows = [{**it, **o} for it, o in zip(items, out)]
    if which == "production":
        from PIL import Image
        for i, it in enumerate(items, 1):
            patch_signal(it["id"], lambda it=it: Image.open(it["path"]).convert("RGB"),
                         "vehicle_quotation" if it["set"] == "findit" else "pan")
            if i % 100 == 0:
                print(f"  [patch detector] {i}/{len(items)}", flush=True)
        rows = [_refused(r) for r in rows]
    return rows


PATCH = R.ItemCache("evidence", "patch-v3")       # the patch detector's result per document (added 2026-10-03)


def patch_signal(item_id: str, load_image, doc_type: str) -> dict:
    """The new patch detector on one document: it did not exist when the documents were first run, and it
    depends on nothing but the image, so it is computed on its own and fused with the stored signals. The
    cache holds the two models' measurements; nothing is cached while the models are not installed."""
    from app.tampering.signals import patch_cnn
    m = PATCH.get(item_id)
    if m is None:
        m = patch_cnn.measure(load_image(), doc_type)
        if m is None:
            return {"available": False, "score": 0.0, "finding": ""}
        PATCH.put(item_id, m)
    s = patch_cnn.verdict(m)
    return {"available": True, "score": float(s.score), "finding": s.findings[0], "over": m["over"], "boxes": m["boxes"]}


def _refused(r: dict) -> dict:
    """Tier and score from the CURRENT fusion config applied to the per-signal scores measured when the
    document was run. The fusion is a pure function of those scores, so a calibration change is scored
    on the same test documents without re-running a single detector (and without a second look at
    anything but the fused outcome). `tier_as_run` keeps what the fusion said at the time. A signal added
    since (patch_cnn) is read from its own per-document cache and fused in; its finding joins the stored
    explanations when the fusion would list it."""
    if not r.get("tier") or not r.get("signals"):
        return r
    from app.tampering import fusion
    from app.tampering.signals.base import SignalResult
    sigs = [SignalResult(name=k, available=True, score=float(v)) for k, v in r["signals"].items()
            if v is not None and k != "patch_cnn"]
    now = list(sigs)
    m = PATCH.get(r["id"]) if r.get("id") else None
    if m:
        from app.tampering.signals import patch_cnn
        now.append(patch_cnn.verdict(m))
    f = fusion.fuse(now, r.get("doc_type"))
    extra = [e for e in f["explanations"] if e.startswith("[patch_cnn]")]
    if extra:
        r = {**r, "explanations": [e for e in (r.get("explanations") or []) if not e.startswith("[patch_cnn]")] + extra}
    from app.tampering.config import CFG
    floors, CFG.fusion.genuine_floor = CFG.fusion.genuine_floor, {}       # the fusion as originally configured
    needs, CFG.fusion.needs_corroboration = getattr(CFG.fusion, "needs_corroboration", []), []
    try:
        f0 = fusion.fuse(sigs, r.get("doc_type"))
    finally:
        CFG.fusion.genuine_floor, CFG.fusion.needs_corroboration = floors, needs
    return {**r, "tier_as_run": f0["risk_tier"], "score_as_run": f0["fused_score"], "tier": f["risk_tier"], "score": f["fused_score"]}


def midv_rows(split: str) -> list[dict]:
    """Genuine phone photos of an ID card (MIDV-500 frames: table, keyboard, hand, clutter, partly out
    of frame; two phones) through the integrity cascade as an ID document. All genuine: every flag is
    a false flag. Same block-of-frames split as the preprocessing test."""
    from eval.real.intake_real import midv_items
    items = midv_items(split)
    cache = R.ItemCache("evidence", f"midv-v1-{split}")

    def one(it):
        from app.agents.flows import document_flows as flows
        with authenticity_on(False), C.Timer() as t:
            out = flows.run_integrity(it["image"], it["id"].split(":")[1] + ".png", "pan", {})
        return {**_summ(out.get("tampering"), t.ms), "doc_type": "pan"}
    out = cache.run(items, one, label="genuine ID photos", every=25,
                    on_error={"tier": None, "score": None, "ms": None, "doc_type": "pan", "explanations": [], "signals": {}})
    from PIL import Image
    for it in items:
        patch_signal(it["id"], lambda it=it: Image.open(io.BytesIO(it["image"])).convert("RGB"), "pan")
    return [_refused({"id": it["id"], "set": "midv", "forged": False, "kind": "genuine", "condition": it["condition"],
                      "regions": [], **o}) for it, o in zip(items, out) if o.get("tier")]


# ── metrics ─────────────────────────────────────────────────────────────────
def _tier_block(rows) -> dict:
    forged = [r for r in rows if r["forged"] and r["tier"]]
    genuine = [r for r in rows if not r["forged"] and r["tier"]]
    per = {}
    for r in forged:
        p = per.setdefault(r["kind"], {"n": 0, "flagged": 0, "high": 0})
        p["n"] += 1
        p["flagged"] += r["tier"] in ("MEDIUM", "HIGH")
        p["high"] += r["tier"] == "HIGH"
    return {"forged_recall": R.wilson(sum(r["tier"] in ("MEDIUM", "HIGH") for r in forged), len(forged)),
            "forged_high_rate": R.wilson(sum(r["tier"] == "HIGH" for r in forged), len(forged)),
            "genuine_false_high": R.wilson(sum(r["tier"] == "HIGH" for r in genuine), len(genuine)),
            "genuine_flagged_any": R.wilson(sum(r["tier"] != "LOW" for r in genuine), len(genuine)),
            "auc": R.auc_ci(forged + genuine, score=lambda r: r["score"], label=lambda r: r["forged"]),
            "per_forgery_kind": {k: {"n": v["n"], "recall": round(v["flagged"] / v["n"], 4),
                                     "high": round(v["high"] / v["n"], 4)} for k, v in per.items()},
            "unreadable": sum(1 for r in rows if not r["tier"]), "failed": sum(1 for r in rows if r.get("error")),
            "n_forged": len(forged), "n_genuine": len(genuine)}


def leakage_check(rows) -> dict:
    """Test documents byte-identical to anything the tampering CNN was trained on."""
    train = Path(r"D:\novacinternship\document_tampering\data")
    return R.leakage({R.sha256_file(r["path"]) for r in rows}, [train / "synthetic", train / "external", train / "archive"])


def eval_tampering(split: str = "test", n: int = 200) -> dict:
    rows = _runs(split, n, "production")
    lat = R.latency([r["ms"] for r in rows if r["ms"]])
    as_run = [{**r, "tier": r.get("tier_as_run", r["tier"]), "score": r.get("score_as_run", r["score"])} for r in rows]
    photos = midv_rows(split)
    photo_block = lambda rs, t="tier": {"flagged_any": R.wilson(sum(r[t] != "LOW" for r in rs), len(rs)),  # noqa: E731
                                        "false_high": R.wilson(sum(r[t] == "HIGH" for r in rs), len(rs)), "n": len(rs)}
    from app.tampering.config import CFG
    metrics = {**_tier_block(rows),
               "by_dataset": {s: _tier_block([r for r in rows if r["set"] == s]) for s in ("findit", "sidtd")},
               "as_originally_configured": {**{k: v for k, v in _tier_block(as_run).items() if k != "per_forgery_kind"},
                                            "by_dataset": {s: {k: v for k, v in _tier_block([r for r in as_run if r["set"] == s]).items()
                                                               if k in ("forged_recall", "genuine_false_high", "genuine_flagged_any", "auc")}
                                                           for s in ("findit", "sidtd")}},
               "genuine_id_photos": {**photo_block(photos),
                                     "as_originally_configured": photo_block(
                                         [{**r, "tier_as_run": r.get("tier_as_run", r["tier"])} for r in photos], "tier_as_run")},
               "tier_changed_by_calibration": sum(1 for r in rows if r.get("tier_as_run") and r["tier"] != r["tier_as_run"]),
               "genuine_floor": dict(getattr(CFG.fusion, "genuine_floor", {}) or {}),
               "latency": lat}
    data = {"name": "Find it again! (official test) + SIDTD templates (test groups); MIDV-500 genuine ID photos "
                    "as an extra genuine set", "split": split,
            "n": len(rows), "findit": sum(r["set"] == "findit" for r in rows), "sidtd": sum(r["set"] == "sidtd" for r in rows),
            "path": "production: receipts as vehicle_quotation with extraction; ID cards as pan, forensics only",
            "leakage": leakage_check(rows)}
    return R.save("tampering_forensics", split, metrics, data)


def eval_authenticity(split: str = "test", n: int = 100) -> dict:
    rows = _runs(split, n, "truthful")
    ran = [r for r in rows if r.get("auth") is not None]

    def block(rs):
        f = [r for r in rs if r["forged"] and r.get("auth") is not None]
        g = [r for r in rs if not r["forged"] and r.get("auth") is not None]
        return {"forged_recall": R.wilson(sum(r["auth"] >= 0.5 for r in f), len(f)),
                "genuine_false_flag": R.wilson(sum(r["auth"] >= 0.5 for r in g), len(g)),
                "auc": R.auc_ci(f + g, score=lambda r: r["auth"], label=lambda r: r["forged"])}
    lone = [r for r in rows if r["tier"] and [k for k, v in (r.get("signals") or {}).items() if (v or 0) >= 0.2] == ["authenticity"]]
    metrics = {**block(rows), "by_dataset": {s: block([r for r in rows if r["set"] == s]) for s in ("findit", "sidtd")},
               "cap_respected": R.wilson(sum(r["tier"] != "HIGH" for r in lone), len(lone))["value"] if lone else 1.0,
               "lone_authenticity_cases": len(lone), "ran_rate": R.wilson(len(ran), len(rows)),
               "latency": R.latency([r["ms"] for r in rows if r["ms"]])}
    data = {"name": "Find it again! (official test) + SIDTD templates (test groups)", "split": split, "n": len(rows),
            "path": "truthful: documents described as what they are (evaluation-only label override)",
            "labels": {k: v[0] for k, v in LABELS.items()}}
    return R.save("authenticity", split, metrics, data)


def _iou(a, b) -> float:
    ix0, iy0, ix1, iy1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def _boxes(r) -> tuple[list, float]:
    """The locator's own boxes (same functions as /tampering/locate), in ORIGINAL pixel coordinates."""
    from PIL import Image
    from app.tampering import locate as L
    data = r["path"].read_bytes()
    im = L._load_image(data)
    if im is None:
        return [], 1.0
    s = im.size[0] / Image.open(io.BytesIO(data)).size[0]
    tokens = L._ocr_tokens(im)
    out = []
    for e in r.get("explanations") or []:
        if any(sk in e for sk in L._SKIP):
            continue
        f = L._clean_finding(e)
        stated = L.region_boxes(f)                      # a region a detector states: already in the upload's pixels
        if stated:
            out += stated
            continue
        nums, quoted = L._anchors(f)
        if nums or quoted:
            out += [tuple(v / s for v in b) for b in L._match(nums, quoted, tokens)]
    return out, s


def eval_locator(split: str = "test", n: int = 200) -> dict:
    rows = [r for r in _runs(split, n, "production") if r["tier"] in ("MEDIUM", "HIGH")]
    cache = R.ItemCache("tamper_locator", f"v2-{split}")        # v2: findings now include the patch detector's regions (2026-10-04)
    res = cache.run(rows, lambda r: {"boxes": _boxes(r)[0]}, label="locate", every=25)
    hit = overlap = boxed = 0
    forged = [(r, x["boxes"]) for r, x in zip(rows, res) if r["forged"] and r["regions"]]
    genuine = [(r, x["boxes"]) for r, x in zip(rows, res) if not r["forged"]]
    per = {}
    for r, bx in forged:
        h = any(_iou(g, b) >= 0.5 or (g[0] <= (b[0] + b[2]) / 2 <= g[2] and g[1] <= (b[1] + b[3]) / 2 <= g[3])
                for g in r["regions"] for b in bx)
        o = any(_iou(g, b) > 0 for g in r["regions"] for b in bx)
        hit += h
        overlap += o
        boxed += bool(bx)
        p = per.setdefault(r["set"], {"n": 0, "hit": 0, "boxed": 0})
        p["n"] += 1
        p["hit"] += h
        p["boxed"] += bool(bx)
    metrics = {"box_hit_rate": R.wilson(hit, len(forged)), "any_overlap_rate": R.wilson(overlap, len(forged)),
               "boxed_rate": R.wilson(boxed, len(forged)),
               "false_box_rate": R.wilson(sum(bool(b) for _, b in genuine), len(genuine)),
               "by_dataset": {k: {"n": v["n"], "hit": round(v["hit"] / v["n"], 4), "boxed": round(v["boxed"] / v["n"], 4)}
                              for k, v in per.items()}}
    data = {"name": "flagged (MEDIUM/HIGH) documents of the production run", "split": split,
            "n_forged_flagged": len(forged), "n_genuine_flagged": len(genuine),
            "criterion": "hit = a drawn box has IoU >= 0.5 with a forged region or its centre lies inside it"}
    return R.save("tamper_locator", split, metrics, data)


def eval_explainer(split: str = "test", n: int = 200, limit: int = 60) -> dict:
    from app.agents.registry import get_agent
    agent = get_agent("tamper_explainer")
    rows = [r for r in _runs(split, n, "production") if r["tier"] in ("MEDIUM", "HIGH")]
    rows = R.sample(rows, limit)
    cache = R.ItemCache("tamper_explainer", f"v3-{split}")      # v2: page image only when a finding points at it (2026-10-02); v3: patch-detector findings (2026-10-04)

    def one(r):
        t = {"risk_tier": r["tier"], "risk_score": r["score"], "explanations": r["explanations"],
             "signals": [{"name": k, "score": v, "available": True} for k, v in (r.get("signals") or {}).items()]}
        before = copy.deepcopy(t)
        with C.Timer() as tm:
            out = agent.execute({"doc_label": r["doc_type"], "doc_type": r["doc_type"], "tampering": t,
                                 "data": r["path"].read_bytes()})
        dropped = sum(int(w.split()[1]) for w in out.warnings if w.startswith("dropped"))
        return {"produced": bool(out.payload), "dropped": dropped, "unchanged": t == before, "ms": round(tm.ms, 1)}
    res = cache.run(rows, one, label="explain", every=10)
    produced = [x for x in res if x["produced"]]
    metrics = {"grounded_rate": R.wilson(sum(x["dropped"] == 0 for x in produced), len(produced)),
               "tier_unchanged": R.wilson(sum(x["unchanged"] for x in res), len(res)),
               "produced_rate": R.wilson(len(produced), len(res)),
               "sentences_dropped": sum(x["dropped"] for x in produced),
               "latency": R.latency([x["ms"] for x in res])}
    data = {"name": "flagged documents of the production run (Find it again! + SIDTD)", "split": split, "n": len(res)}
    return R.save("tamper_explainer", split, metrics, data)
