"""Intake agents on real / public documents: Preprocessing, Extraction, Document Identity, PII Masking,
Validation (protocol: finsight-eval-data/PROTOCOL.md; targets: eval/targets_real.yaml, Group 2).

Datasets
  * MIDV-500 (HF copy: 297 phone frames of an ID card, 5 backgrounds x 2 phones, true card outline as a
    mask). Split by blocks of 10 consecutive frames of a clip, so neighbouring frames share a split.
  * SROIE (ICDAR 2019, real scanned receipts: words + company / date / address / total) and FUNSD (real
    scanned forms: words). Split by image.
  * RVL-CDIP small (real scanned pages, 16 classes) — a pool of WRONG documents.
  * SIDTD genuine ID templates (European mock IDs) — wrong documents for PAN / Aadhaar.
  * PAN cards (synthetic, Indian layout, 1,000 with labels) and Aadhaar cards (synthetic, 48 photos with
    blur / contrast / scale variants, no labels): India's real IDs cannot lawfully be published.
  * Indian bank statements (synthetic data in digital and scanned PDFs, JSON truth incl. transactions).
  * The complete Razorpay IFSC register (every real Indian bank branch code).

Every run goes through the app's own code: run_extract (preprocessing -> extraction -> validation ->
document identity) exactly as an upload, the PII Masking agent, the Validation agent.
Logging and caches hold verdicts and scores only — never a value read from a document.
"""
from __future__ import annotations

import io
import json
import random
import re
import string
from pathlib import Path

from eval import common as C
from eval.real import core as R

MIDV = R.RAW / "midv500" / "data"
SROIE = R.RAW / "sroie" / "data"
FUNSD = R.RAW / "funsd" / "data"
RVL = R.RAW / "rvl_small" / "train"
PAN = R.RAW / "pan_synth"
AADHAAR = R.RAW / "aadhaar_synth"
STMT = R.RAW / "bank_stmt_in" / "train"
IFSC = R.RAW / "ifsc" / "IFSC-list.json"
SIDTD_REALS = R.RAW / "sidtd" / "templates" / "Images" / "reals"


# ── data loading ────────────────────────────────────────────────────────────
def _image_col(schema) -> str:
    for f in schema:
        if str(f.type).startswith("struct") and "bytes" in str(f.type):
            return f.name
    raise KeyError("no image column")


def _parquet(path: Path, keep=None, columns=None):
    """Rows of a parquet file as dicts, one row group at a time (keep(i) selects rows by index)."""
    import pyarrow.parquet as pq
    p = pq.ParquetFile(path)
    i = 0
    for rg in range(p.num_row_groups):
        t = p.read_row_group(rg, columns=columns)
        for j in range(t.num_rows):
            if keep is None or keep(i):
                yield i, {c: t.column(c)[j].as_py() for c in t.column_names}
            i += 1
        del t


def sroie_items(split: str, n: int | None = None, with_image: bool = True):
    f = SROIE / "test-00000-of-00001.parquet"
    import pyarrow.parquet as pq
    schema = pq.ParquetFile(f).schema_arrow
    img = _image_col(schema)
    cols = [c for c in schema.names if c != img] + ([img] if with_image else [])
    out = []
    for i, r in _parquet(f, columns=cols):
        key = f"sroie:{i}"
        if R.split_of(key) != split:
            continue
        out.append({"id": key, "set": "sroie", "i": i, "image": (r.get(img) or {}).get("bytes") if with_image else None,
                    "words": r.get("words") or [], "entities": r.get("entities") or {k: r.get(k) for k in
                                                                                       ("company", "date", "address", "total")}})
    return R.sample(out, n)


def funsd_items(split: str, n: int | None = None):
    out = []
    for name in ("train", "test"):
        f = FUNSD / f"{name}-00000-of-00001.parquet"
        import pyarrow.parquet as pq
        img = _image_col(pq.ParquetFile(f).schema_arrow)
        for i, r in _parquet(f):
            key = f"funsd:{name}:{i}"
            if R.split_of(key) == split:
                out.append({"id": key, "set": "funsd", "image": (r.get(img) or {}).get("bytes"), "words": r.get("words") or []})
    return R.sample(out, n)


def midv_items(split: str):
    import pyarrow.parquet as pq
    out = []
    for f in sorted(MIDV.glob("*.parquet")):
        for i, r in _parquet(f):
            name = Path(r["pixel_values"]["path"]).stem              # e.g. CA01_07
            clip, idx = name.split("_")
            group = f"midv:{clip}:{(int(idx) - 1) // 10}"
            if R.split_of(group) == split:
                out.append({"id": f"midv:{name}", "clip": clip, "condition": clip[0], "phone": clip[1],
                            "image": r["pixel_values"]["bytes"], "mask": r["label"]["bytes"]})
    return out


def pan_items(split: str, n: int | None = None):
    out = []
    for line in (PAN / "labels.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if R.split_of(f"pan:{r['image']}") == split:
            out.append({"id": f"pan:{r['image']}", "path": PAN / r["image"], "mime": "image/png",
                        "expected": {"pan_number": r["pan"], "full_name": r["name"], "father_name": r["father_name"],
                                     "dob": r["dob"]}})
    return R.sample(out, n)


def statement_items(split: str, n: int | None = None):
    out = []
    for d in sorted(STMT.iterdir()):
        for js in sorted(d.glob("*.json")):
            key = f"stmt:{d.name}:{js.stem}"
            if R.split_of(key) == split:
                out.append({"id": key, "path": js.with_suffix(".pdf"), "truth": js, "mime": "application/pdf",
                            "kind": "scanned" if "Scanned" in d.name else "digital", "layout": d.name})
    return R.sample(out, n)


def rvl_items(per_class: int = 6, split: str = "test"):
    out = []
    for d in sorted(RVL.iterdir()):
        files = [{"id": f"rvl:{d.name}/{p.name}", "path": p, "cls": d.name} for p in sorted(d.glob("*.tif"))]
        out += R.sample(R.take(files, split), per_class)
    return out


def _png(path_or_bytes) -> bytes:
    from PIL import Image
    im = Image.open(io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else path_or_bytes)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


# ── OCR helpers ─────────────────────────────────────────────────────────────
def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _ocr_text(img_bytes: bytes, rotations=(0,), max_dim: int = 1600) -> str:
    from PIL import Image
    from app.imaging import ocr_tokens
    im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    if max(im.size) > max_dim:
        s = max_dim / max(im.size)
        im = im.resize((int(im.width * s), int(im.height * s)))
    return " ".join(" ".join(t["text"] for t in ocr_tokens(im.rotate(a, expand=True))) for a in rotations)


def _word_recall(text: str, words: list[str]) -> float | None:
    """Share of the page's true words (2+ letters/digits) found in the OCR text."""
    ws = [_norm(w) for w in words]
    ws = [w for w in ws if len(w) >= 2]
    if not ws:
        return None
    t = _norm(text)
    return sum(1 for w in ws if w in t) / len(ws)


# ── Preprocessing ───────────────────────────────────────────────────────────
def _card_quad(img_bytes: bytes):
    """The quadrilateral the cleanup's Stage 2 picks on this frame (None = it fell back to deskew),
    in the coordinates of the Stage-1 image, plus that image's scale vs the original."""
    import cv2
    import numpy as np
    from PIL import Image, ImageOps
    from app import preprocess as P
    img = P._pil_to_bgr(ImageOps.exif_transpose(Image.open(io.BytesIO(img_bytes))))
    h, w = img.shape[:2]
    s = 1.0
    if w > 4000 or h > 4000:
        s = min(4000 / w, 4000 / h)
    elif w < 1000:
        s = 1000 / w
    if s != 1.0:
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    seen = []
    orig = P._order_points

    def spy(pts):
        r = orig(pts)
        seen.append(np.array(r))
        return r
    P._order_points = spy
    try:
        warped = P._warp_document_robust(img, page=False)
    finally:
        P._order_points = orig
    return (seen[-1] / s if warped is not None and seen else None)


def _quad_iou(quad, mask_bytes: bytes) -> float:
    import cv2
    import numpy as np
    from PIL import Image
    m = np.array(Image.open(io.BytesIO(mask_bytes)).convert("L")) > 127
    q = np.zeros(m.shape, np.uint8)
    cv2.fillPoly(q, [np.round(quad).astype(np.int32)], 1)
    q = q.astype(bool)
    union = (m | q).sum()
    return float((m & q).sum() / union) if union else 0.0


def eval_preprocessing(split: str = "test", n_scans: int = 100, n_timed: int = 60) -> dict:
    from app.agents.registry import get_agent
    agent = get_agent("preprocessing")
    frames = midv_items(split)
    gcache = R.ItemCache("preprocessing", f"midv-geom-v3-{split}")

    def geom(it):
        quad = _card_quad(it["image"])
        return {"found": quad is not None, "iou": round(_quad_iou(quad, it["mask"]), 4) if quad is not None else 0.0}
    g = gcache.run(frames, geom, label="MIDV outline", every=50)
    rows = [{**{k: it[k] for k in ("condition", "phone")}, **x} for it, x in zip(frames, g)]

    # whole agent on a sample of frames: time and whether the cleanup kept its result
    tcache = R.ItemCache("preprocessing", f"midv-time-v3-{split}")

    def timed(it):
        with C.Timer() as t:
            out = agent.execute({"data": it["image"], "mime": "image/png", "doc_type": "pan"})
        return {"ms": round(t.ms, 1), "status": out.status, "changed": (out.output_summary or {}).get("changed")}
    tm = tcache.run(R.sample(frames, n_timed), timed, label="MIDV timed", every=20)

    # real scans: words readable before vs after cleanup (page documents)
    scans = sroie_items(split, n_scans) + funsd_items(split, 50)
    scache = R.ItemCache("preprocessing", f"scans-v2-{split}")

    def scan(it):
        data = _png(it["image"])
        with C.Timer() as t:
            out = agent.execute({"data": data, "mime": "image/png", "doc_type": "salary_slip"})
        after = out.payload["data"]
        before_r = _word_recall(_ocr_text(data), it["words"])
        after_r = before_r if after == data else _word_recall(_ocr_text(after), it["words"])
        return {"before": before_r, "after": after_r, "ms": round(t.ms, 1), "reverted": after == data}
    s = [dict(x, set=it["set"]) for it, x in zip(scans, scache.run(scans, scan, label="scans", every=20))]
    s = [x for x in s if x.get("before") is not None and x.get("after") is not None]

    mean = lambda xs: round(sum(xs) / len(xs), 4) if xs else None  # noqa: E731
    by_cond = {}
    for r in rows:
        b = by_cond.setdefault(r["condition"], [0, 0])
        b[0] += r["iou"] >= 0.85
        b[1] += 1
    lat = R.latency([x["ms"] for x in tm] + [x["ms"] for x in s])
    metrics = {
        "card_located_rate": R.wilson(sum(r["iou"] >= 0.85 for r in rows), len(rows)),
        "card_found_any": R.wilson(sum(r["found"] for r in rows), len(rows)),
        "mean_iou_when_found": mean([r["iou"] for r in rows if r["found"]]),
        "wrong_outline_rate": R.wilson(sum(r["found"] and r["iou"] < 0.5 for r in rows), len(rows)),
        "by_background": {k: {"located": round(v[0] / v[1], 4), "n": v[1]} for k, v in sorted(by_cond.items())},
        "scan_harm_rate": R.wilson(sum(x["after"] < x["before"] - 0.02 for x in s), len(s)),
        "ocr_recall_before": mean([x["before"] for x in s]), "ocr_recall_after": mean([x["after"] for x in s]),
        "ocr_recall_gain": R.bootstrap(lambda rs: round(sum(x["after"] - x["before"] for x in rs) / len(rs), 4), s),
        "reverted_rate": R.wilson(sum(x["reverted"] for x in s), len(s)),
        "by_scan_set": {k: {"before": mean([x["before"] for x in s if x["set"] == k]),
                            "after": mean([x["after"] for x in s if x["set"] == k]),
                            "n": sum(x["set"] == k for x in s)} for k in ("sroie", "funsd")},
        "p50_s": lat["p50_s"], "p95_s": lat["p95_s"]}
    data = {"name": "MIDV-500 phone frames (outline truth) + SROIE receipts and FUNSD forms (word truth)",
            "split": split, "n_frames": len(rows), "n_timed": len(tm), "n_scans": len(s),
            "backgrounds": {"C": "clutter", "H": "in hand", "K": "keyboard", "P": "partly out of frame", "T": "table"},
            "note": "outline = the quadrilateral Stage 2 of the cleanup selects; found=False means it fell back to deskew"}
    return R.save("preprocessing", split, metrics, data)


# ── Extraction ──────────────────────────────────────────────────────────────
RECEIPT_PROMPT = ('Extract receipt. Return ONLY JSON: '
                  '{"company":"","date":"DD/MM/YYYY","address":"","total":0}')


def any_date(s) -> str | None:
    """ISO date for the formats statements print: 2023-01-05, 05/01/2023, 05-01-23, 05 Jan 2023, 05-Jan-2023."""
    from datetime import datetime
    s = str(s or "").strip()[:20]
    for cut in (len(s), 11, 10, 9, 8):
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d-%b-%Y", "%d %B %Y",
                    "%d/%m/%y", "%d-%m-%y", "%d-%b-%y", "%d %b %y"):
            try:
                return datetime.strptime(s[:cut].strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return None


def truth_rows(t: dict) -> list[dict]:
    """Ground-truth transactions in one shape. The two statement layouts are labelled differently:
    Type 1 has debit / credit / balance columns, Type 2 an amount with a CR/DR marker."""
    out = []
    for x in t.get("transactions") or []:
        if "transaction_amount" in x:
            amt, cr = float(x.get("transaction_amount") or 0), str(x.get("cr_dr") or "").upper().startswith("C")
            out.append({"date": x.get("date"), "credit": amt if cr else 0.0, "debit": 0.0 if cr else amt,
                        "balance": x.get("available_balance"), "failed": bool(x.get("failed"))})
        else:
            out.append({"date": x.get("date"), "credit": float(x.get("credit") or 0), "debit": float(x.get("debit") or 0),
                        "balance": x.get("balance"), "failed": bool(x.get("failed"))})
    return out


def _truth_statement(p: Path) -> dict:
    t = json.loads(p.read_text(encoding="utf-8"))
    tx = truth_rows(t)
    ok = [x for x in tx if not x.get("failed")]
    return {"account_holder": t.get("account_holder"), "bank_name": t.get("bank_name"),
            "account_number": t.get("account_number"), "ifsc": t.get("ifsc_code"),
            "opening_balance": t.get("opening_balance"), "closing_balance": t.get("closing_balance"),
            "statement_period": (t.get("start_date"), t.get("end_date")),
            "total_credits": (sum(x.get("credit") or 0 for x in ok), sum(x.get("credit") or 0 for x in tx)),
            "total_debits": (sum(x.get("debit") or 0 for x in ok), sum(x.get("debit") or 0 for x in tx))}


def _num_ok(exp, got) -> bool:
    g = C.to_num(got)
    return g is not None and exp is not None and abs(g - float(exp)) <= max(1.0, 0.005 * abs(float(exp)))


def _overlap(a, b) -> float:
    ta = set(re.findall(r"[a-z0-9]+", str(a or "").lower()))
    tb = set(re.findall(r"[a-z0-9]+", str(b or "").lower()))
    return len(ta & tb) / len(ta) if ta else 0.0


STMT_PRINTED = ("account_holder", "bank_name", "account_number", "ifsc", "opening_balance", "closing_balance")
STMT_COMPUTED = ("statement_period", "total_credits", "total_debits")


def _score_statement(truth: dict, parsed: dict) -> dict:
    p = parsed or {}
    acct_t, acct_g = re.sub(r"\D", "", str(truth["account_number"] or "")), re.sub(r"\D", "", str(p.get("account_number") or ""))
    period = re.findall(r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}[\s\-]+[A-Za-z]{3,9}[\s\-]+\d{2,4}",
                        str(p.get("statement_period") or ""))
    return {
        "account_holder": _overlap(truth["account_holder"], p.get("account_holder")) >= 0.8,
        "bank_name": _overlap(truth["bank_name"], p.get("bank_name")) >= 0.8,
        "account_number": bool(acct_t) and len(acct_g) >= 4 and acct_t[-4:] == acct_g[-4:]
                          and (len(acct_g) <= 4 or acct_g == acct_t),
        "ifsc": _norm(truth["ifsc"]) == _norm(p.get("ifsc")),
        "opening_balance": _num_ok(truth["opening_balance"], p.get("opening_balance")),
        "closing_balance": _num_ok(truth["closing_balance"], p.get("closing_balance")),
        "statement_period": len(period) >= 2 and any_date(period[0]) == any_date(truth["statement_period"][0])
                            and any_date(period[-1]) == any_date(truth["statement_period"][1]),
        "total_credits": any(_num_ok(v, p.get("total_credits")) for v in truth["total_credits"]),
        "total_debits": any(_num_ok(v, p.get("total_debits")) for v in truth["total_debits"])}


def _extract(data: bytes, mime: str, doc_type: str, name: str) -> dict:
    from app.agents.flows import document_flows as flows
    # rows of a photo / scanned statement are read in a background thread after the response; these
    # tests score the response, and the threads would pile up behind the next documents
    flows._parse_statement_later = lambda *a, **k: None
    with C.Timer() as t:
        res = flows.run_extract(data, mime, doc_type, name)
    ext = res.get("extraction") or {}
    return {"parsed": ext.get("parsed"), "identity_ok": (res.get("identity") or {}).get("ok"),
            "flags": {f["key"]: f["flag"] for f in (res.get("validation") or {}).get("fields", [])},
            "error": ext.get("error"), "ms": round(t.ms, 1)}


def _pan_one(it) -> dict:
    from app.docrules import compare_names
    r = _extract(it["path"].read_bytes(), "image/png", "pan", it["path"].name)
    p = r["parsed"] or {}
    fields = {k: C.field_match(k, v, p.get(k)) for k, v in it["expected"].items()}
    return {"fields": fields, "json_ok": r["parsed"] is not None, "identity_ok": r["identity_ok"],
            "name_level": compare_names(it["expected"]["full_name"], p.get("full_name"))["level"] if p.get("full_name") else None,
            "pan_valid_flag": r["flags"].get("pan_number"), "ms": r["ms"]}


def _stmt_one(it) -> dict:
    r = _extract(it["path"].read_bytes(), "application/pdf", "bank_statement", it["path"].name)
    sc = _score_statement(_truth_statement(it["truth"]), r["parsed"] or {})
    return {"fields": sc, "json_ok": r["parsed"] is not None, "identity_ok": r["identity_ok"], "ms": r["ms"]}


def _receipt_one(it) -> dict:
    from app.agents.registry import get_agent
    from app.docrules import parse_date_any
    data = _png(it["image"])
    with C.Timer() as t:
        pre = get_agent("preprocessing").execute({"data": data, "mime": "image/png", "doc_type": "salary_slip"}).payload
        ext = get_agent("extraction").execute({"data": pre["data"], "mime": pre["mime"], "doc_type": "receipt",
                                               "prompt": RECEIPT_PROMPT}).payload
    p, e = ext.get("parsed") or {}, it["entities"]
    fields = {"company": _overlap(e.get("company"), p.get("company")) >= 0.8,
              "date": bool(p.get("date")) and parse_date_any(str(e.get("date"))) is not None
                      and parse_date_any(str(e.get("date"))) == parse_date_any(str(p.get("date"))),
              "address": _overlap(e.get("address"), p.get("address")) >= 0.8,
              "total": _num_ok(C.to_num(e.get("total")), p.get("total"))}
    return {"fields": fields, "json_ok": ext.get("parsed") is not None, "ms": round(t.ms, 1)}


def _field_block(rows, keys=None) -> dict:
    flat = [ok for r in rows for k, ok in r["fields"].items() if keys is None or k in keys]
    docs = [all(ok for k, ok in r["fields"].items() if keys is None or k in keys) for r in rows]
    per = {}
    for r in rows:
        for k, ok in r["fields"].items():
            p = per.setdefault(k, [0, 0])
            p[0] += ok
            p[1] += 1
    return {"field_accuracy": R.wilson(sum(flat), len(flat)), "doc_all_correct": R.wilson(sum(docs), len(docs)),
            "per_field": {k: round(v[0] / v[1], 4) for k, v in per.items()}, "n_docs": len(rows)}


ON_ERROR_FIELDS = {"fields": {}, "json_ok": False, "identity_ok": None, "ms": None}


EXTRACTION_VER = "v2"      # v1 = the code before the 2026-10-02 fixes (kept as extraction.<split>.baseline.json)


def extraction_cached(kind: str, split: str, item_id: str) -> dict | None:
    """An item's cached extraction result: the current version, else the first run's (same documents;
    used by the document-identity and cross-verification tests, which reuse what extraction read)."""
    for ver in (EXTRACTION_VER, "v1"):
        g = R.ItemCache("extraction", f"{kind}-{ver}-{split}").get(item_id)
        if g:
            return g
    return None


def eval_extraction(split: str = "test", n_pan: int = 150, n_receipts: int = 100, n_stmt: int | None = 60) -> dict:
    """EVAL_EXTRACTION_VERSION=v1 re-reads the cached first run (nothing is re-run) and saves it as the baseline."""
    import os
    ver = os.environ.get("EVAL_EXTRACTION_VERSION", EXTRACTION_VER)
    pans = pan_items(split, n_pan)
    stmts = statement_items(split, n_stmt)
    receipts = sroie_items(split, n_receipts)
    pr = R.ItemCache("extraction", f"pan-{ver}-{split}").run(pans, _pan_one, label="PAN", every=25, on_error=ON_ERROR_FIELDS)
    sr = R.ItemCache("extraction", f"stmt-{ver}-{split}").run(stmts, _stmt_one, label="statements", every=10,
                                                              on_error=ON_ERROR_FIELDS)
    for it, r in zip(stmts, sr):
        r["kind"] = it["kind"]
    rr = R.ItemCache("extraction", f"receipt-{ver}-{split}").run(receipts, _receipt_one, label="receipts", every=25,
                                                                 on_error=ON_ERROR_FIELDS)
    # an item whose run failed counts as all fields wrong (it is in the denominator)
    for rows, keys in ((pr, ("pan_number", "full_name", "father_name", "dob")), (sr, STMT_PRINTED + STMT_COMPUTED),
                       (rr, ("company", "date", "address", "total"))):
        for r in rows:
            if not r["fields"]:
                r["fields"] = {k: False for k in keys}
    printed = [{"fields": r["fields"]} for r in pr] + \
              [{"fields": {k: r["fields"][k] for k in STMT_PRINTED}} for r in sr]
    allrows = pr + sr
    lat = R.latency([r["ms"] for r in pr + sr + rr if r.get("ms")])
    metrics = {**_field_block(printed),
               "receipt_field_accuracy": _field_block(rr)["field_accuracy"],
               "json_ok_rate": R.wilson(sum(r["json_ok"] for r in allrows + rr), len(allrows + rr)),
               "scanned_statement_field_accuracy": _field_block(
                   [{"fields": {k: r["fields"][k] for k in STMT_PRINTED}} for r in sr if r["kind"] == "scanned"])["field_accuracy"],
               "by_set": {"pan_cards": _field_block(pr),
                          "statements_printed": _field_block([{"fields": {k: r["fields"][k] for k in STMT_PRINTED}} for r in sr]),
                          "statements_computed": _field_block([{"fields": {k: r["fields"][k] for k in STMT_COMPUTED}} for r in sr]),
                          "statements_digital": _field_block([{"fields": {k: r["fields"][k] for k in STMT_PRINTED}}
                                                              for r in sr if r["kind"] == "digital"]),
                          "receipts_sroie": _field_block(rr)},
               "failed_runs": sum(1 for r in pr + sr + rr if r.get("error")),
               "latency": lat}
    data = {"name": "PAN cards (synthetic, labelled) + Indian bank statements (synthetic data, digital/scanned PDFs) "
                    "+ SROIE real receipts (evaluation-only receipt prompt)",
            "split": split, "n_pan": len(pr), "n_statements": len(sr), "n_receipts": len(rr),
            "matching": "numbers within 0.5% (min Rs 1); dates by value; names, bank, company and address by >= 80% "
                        "token overlap; account number by last 4 digits (the prompt asks for a masked number); "
                        "totals accept the sum with or without failed transactions"}
    data["code_version"] = ver
    return R.save("extraction", split if ver != "v1" else f"{split}.baseline", metrics, data,
                  model="ollama qwen2.5vl:7b (local)")


# ── Document Identity ───────────────────────────────────────────────────────
ASKED = ("pan", "aadhaar", "salary_slip", "bank_statement")


def _pick(key: str, options) -> str:
    return options[int(R._u(key, "asked") * len(options))]


def wrong_uploads(split: str = "test"):
    """(document, the type it is uploaded AS) — every one of them is the wrong document."""
    out = []
    for it in rvl_items(6, split):
        out.append({"id": it["id"], "src": "rvl:" + it["cls"], "load": lambda it=it: (_png(it["path"]), "image/png"),
                    "asked": _pick(it["id"], ASKED)})
    for it in sroie_items(split, 30):
        out.append({"id": it["id"], "src": "sroie_receipt", "load": lambda it=it: (_png(it["image"]), "image/png"),
                    "asked": _pick(it["id"], ("salary_slip", "bank_statement"))})
    reals = [{"id": f"sidtd:{p.name}", "path": p} for p in sorted(SIDTD_REALS.glob("*.jpg"))]
    for it in R.sample(R.take(reals, split, group=lambda x: x["path"].stem), 30):
        out.append({"id": it["id"], "src": "sidtd_foreign_id", "load": lambda it=it: (it["path"].read_bytes(), "image/jpeg"),
                    "asked": _pick(it["id"], ("pan", "aadhaar"))})
    for it in pan_items(split, None)[-20:]:                  # not the cards the extraction run uses
        out.append({"id": it["id"], "src": "pan_card", "load": lambda it=it: (it["path"].read_bytes(), "image/png"),
                    "asked": _pick(it["id"], ("aadhaar", "bank_statement", "salary_slip"))})
    for it in statement_items(split, None)[-20:]:
        out.append({"id": it["id"], "src": "bank_statement", "load": lambda it=it: (it["path"].read_bytes(), "application/pdf"),
                    "asked": _pick(it["id"], ("salary_slip", "pan", "aadhaar"))})
    return out


def aadhaar_items():
    return [{"id": f"aadhaar:{p.name}", "path": p, "variant": p.stem.split("front_")[-1]}
            for p in sorted(AADHAAR.glob("*.jpg"))]


def eval_doc_identity(split: str = "test") -> dict:
    wrong = wrong_uploads(split)
    cache = R.ItemCache("doc_identity", f"wrong-v2-{split}")      # v2: the code after the 2026-10-02 extraction fixes

    def one(it):
        data, mime = it["load"]()
        r = _extract(data, mime, it["asked"], it["id"].split(":")[-1])
        return {"caught": r["identity_ok"] is False, "json_ok": r["parsed"] is not None, "ms": r["ms"]}
    on_err = {"caught": None, "json_ok": False, "ms": None}
    # documents offered as a bank statement are re-run under v3: the statement-phrase rule (2026-10-02 evening)
    as_stmt = [it for it in wrong if it["asked"] == "bank_statement"]
    done = dict(zip((it["id"] for it in wrong if it["asked"] != "bank_statement"),
                    cache.run([it for it in wrong if it["asked"] != "bank_statement"], one, label="wrong uploads", every=25,
                              on_error=on_err)))
    done.update(zip((it["id"] for it in as_stmt),
                    R.ItemCache("doc_identity", f"wrong-v3-{split}").run(as_stmt, one, label="wrong uploads as statement",
                                                                           every=10, on_error=on_err)))
    res = [done[it["id"]] for it in wrong]
    rows = [{**x, "src": it["src"].split(":")[0], "cls": it["src"], "asked": it["asked"]} for it, x in zip(wrong, res)]
    ran = [r for r in rows if r["caught"] is not None]

    # right uploads: the extraction run's own PAN cards / statements (same split) + the Aadhaar set
    right = []
    for it in pan_items(split, 150):
        g = extraction_cached("pan", split, it["id"])
        if g and g.get("identity_ok") is not None:
            right.append({"src": "pan_card", "blocked": not g["identity_ok"]})
    # statements under their own type, run again with the statement-phrase rule in place (the same 60 the
    # extraction test reads)
    stm = [it for it in statement_items(split, 60)]
    sres = R.ItemCache("doc_identity", f"stmt-right-v3-{split}").run(
        stm, lambda it: {"blocked": _extract(it["path"].read_bytes(), "application/pdf", "bank_statement",
                                             it["path"].name)["identity_ok"] is False},
        label="statements as statements", every=15, on_error={"blocked": None})
    right += [{"src": "bank_statement", "blocked": x["blocked"]} for x in sres if x["blocked"] is not None]
    acache = R.ItemCache("doc_identity", "aadhaar-right-v1")
    aad = aadhaar_items()
    ares = acache.run(aad, lambda it: {"blocked": _extract(it["path"].read_bytes(), "image/jpeg", "aadhaar",
                                                           it["path"].name)["identity_ok"] is False},
                      label="aadhaar as aadhaar", every=12, on_error={"blocked": None})
    right += [{"src": "aadhaar_card", "blocked": x["blocked"]} for x in ares if x["blocked"] is not None]

    def by(key, rs, val):
        out = {}
        for r in rs:
            b = out.setdefault(r[key], [0, 0])
            b[0] += bool(r[val])
            b[1] += 1
        return {k: {"rate": round(v[0] / v[1], 4), "n": v[1]} for k, v in sorted(out.items())}
    metrics = {"wrong_doc_recall": R.wilson(sum(r["caught"] for r in ran), len(ran)),
               "false_block_rate": R.wilson(sum(r["blocked"] for r in right), len(right)),
               "caught_by_source": by("src", ran, "caught"), "caught_by_rvl_class": by("cls", [r for r in ran if r["src"] == "rvl"], "caught"),
               "caught_by_asked_type": by("asked", ran, "caught"), "blocked_by_source": by("src", right, "blocked"),
               "failed_runs": len(rows) - len(ran)}
    data = {"name": "wrong: RVL-CDIP (16 classes), SROIE receipts, SIDTD foreign IDs, PAN cards and statements under "
                    "another type; right: PAN cards, statements, synthetic Aadhaar cards under their own type",
            "split": split, "n_wrong": len(ran), "n_right": len(right)}
    return R.save("doc_identity", split, metrics, data)


# ── PII Masking ─────────────────────────────────────────────────────────────
def _font(px: int, mono: bool = False):
    from PIL import ImageFont
    for name in (("cour.ttf", "consola.ttf") if mono else ("arial.ttf", "calibri.ttf")):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default()


def _gen_ids(key: str) -> tuple[str, str]:
    from eval.datasets.synth.identities import make_aadhaar, make_pan
    rng = random.Random(key)
    return make_pan(rng, rng.choice(string.ascii_uppercase)), make_aadhaar(rng)


LABELS_PAN = ("PAN: {}", "PAN No. {}", "Permanent Account Number {}", "PAN {}")
LABELS_AAD = ("Aadhaar No: {}", "Aadhaar {}", "UID: {}", "Aadhaar Number {}")


def _print_ids(page_bytes: bytes, key: str) -> tuple[bytes, str, str]:
    """A real scanned page with an applicant block (PAN + Aadhaar) printed in a white band added under it,
    in the page's own scale — as a payslip, statement header or form carries them."""
    from PIL import Image, ImageDraw
    rng = random.Random(key)
    pan, aad = _gen_ids(key)
    page = Image.open(io.BytesIO(page_bytes)).convert("RGB")
    w, h = page.size
    px = max(14, int(w / 45))
    band = Image.new("RGB", (w, px * 5), (255, 255, 255))
    d = ImageDraw.Draw(band)
    aad_txt = rng.choice((f"{aad[:4]} {aad[4:8]} {aad[8:]}", aad))
    mono = rng.random() < 0.4
    d.text((px, px // 2), rng.choice(LABELS_PAN).format(pan), fill=(20, 20, 20), font=_font(px, mono))
    d.text((px, px * 2 + px // 2), rng.choice(LABELS_AAD).format(aad_txt), fill=(20, 20, 20), font=_font(px, mono))
    out = Image.new("RGB", (w, h + band.height), (255, 255, 255))
    out.paste(page, (0, 0))
    out.paste(band, (0, h))
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue(), pan, aad


_FOLD = str.maketrans({"o": "0", "d": "0", "q": "0", "i": "1", "l": "1", "s": "5", "b": "8", "z": "2", "g": "6"})


def _fold(s: str) -> str:
    """Look-alike characters read as one (O/0, I/1, S/5, B/8, Z/2): a number OCR reads back with one
    of these swapped is still plainly readable to a person, so it still counts as exposed."""
    return _norm(s).translate(_FOLD)


def _mask_check(agent, data: bytes, doc_type: str, own: list[str], other_words: list[str], parsed: dict | None = None) -> dict:
    import base64
    with C.Timer() as t:
        res = agent.execute({"data": data, "doc_type": doc_type, "parsed": parsed or {}}).payload
    if res is None:
        return {"rendered": False, "leak": True, "last4": False, "ms": round(t.ms, 1)}
    masked = base64.b64decode(res["masked_image"].split(",", 1)[1])
    text = _norm(_ocr_text(masked, rotations=(0, 90, 180, 270)))
    folded = text.translate(_FOLD)
    out = {"rendered": True, "leak": any(_norm(n) in text for n in own),
           "leak_lookalike": any(_fold(n) in folded for n in own),
           "last4": all(_fold(n)[-4:] in folded for n in own) if own else None,
           "masked_regions": res["masked_count"], "withheld": bool(res.get("withheld")), "ms": round(t.ms, 1)}
    if other_words:
        before = _norm(_ocr_text(data))
        readable = [w for w in other_words if w in before]
        out["others_readable_before"] = len(readable)
        out["others_intact"] = sum(1 for w in readable if w in text)
    return out


def exposed(r: dict) -> bool:
    """The ID is still there for a reader: it reads back (exactly or with a look-alike character), or
    nothing at all was masked on a document that carries one. (The first scoring, 2026-10-01, asked
    only for an exact read-back, and so passed cards whose small print OCR misread by one character
    although no mask had been drawn.) A card the app WITHHELD (blurred whole, because its number was not
    located) counts as not exposed only if the number really does not read back; it is reported
    separately as `withheld` and never counts as "last four kept"."""
    return bool(r.get("leak") or r.get("leak_lookalike") or not (r.get("masked_regions") or r.get("withheld")))


def _own_numbers(data: bytes) -> list[str]:
    """Aadhaar / PAN numbers OCR reads off the unmasked original (any rotation) — for the unlabelled set."""
    from app.imaging import load_image, ocr_tokens
    pil = load_image(data).convert("RGB")
    own = set()
    for a in (0, 90, 180, 270):
        for t in ocr_tokens(pil.rotate(a, expand=True)):
            own.update(re.findall(r"[A-Z]{5}\d{4}[A-Z]", t["alnum"]))
            if re.match(r"^\d{4}\s?\d{4}\s?\d{4}$", t["text"].strip()):
                own.add(re.sub(r"\D", "", t["text"]))
    return sorted(own)


def eval_pii_masking(split: str = "test", n_pan: int = 100, n_pages: int = 90) -> dict:
    """EVAL_PII_VERSION=v1 re-scores the cached run of the masking code as it was before 2026-10-01
    (nothing is re-run); the default version runs the current code."""
    import os
    from app.agents.registry import get_agent
    ver = os.environ.get("EVAL_PII_VERSION", "v7")
    agent = get_agent("pii_masking")
    on_err = {"rendered": False, "leak": True, "last4": False, "ms": None}
    pans = pan_items(split, n_pan)
    pr = R.ItemCache("pii_masking", f"pan-{ver}-{split}").run(
        pans, lambda it: _mask_check(agent, it["path"].read_bytes(), "pan", [it["expected"]["pan_number"]], []),
        label="PAN cards", every=25, on_error=on_err)
    px = []
    if ver != "v1":      # as in production: the masking step also receives the number the extraction read
        px = R.ItemCache("pii_masking", f"panx-{ver}-{split}").run(
            pans, lambda it: _mask_check(agent, it["path"].read_bytes(), "pan", [it["expected"]["pan_number"]], [],
                                         parsed={"pan_number": it["expected"]["pan_number"]}),
            label="PAN cards + extracted value", every=25, on_error=on_err)

    # Indian IDs printed onto real scanned pages; the page's other numbers must survive
    pages = [dict(it, kind="sroie") for it in sroie_items(split, n_pages // 3)] + \
            [dict(it, kind="funsd") for it in funsd_items(split, n_pages // 3)]
    rvl = [it for it in rvl_items(3, split) if it["cls"] in ("form", "invoice", "letter", "memo", "budget", "questionnaire",
                                                      "specification", "scientific_report", "news_article", "email")]
    pages += [{"id": it["id"], "image": it["path"].read_bytes(), "words": None, "kind": "rvl"} for it in rvl]

    def page_one(it):
        data, pan, aad = _print_ids(_png(it["image"]), it["id"])
        nums = [_norm(w) for w in (it["words"] or []) if sum(ch.isdigit() for ch in str(w)) >= 4]
        dt = _pick(it["id"], ("salary_slip", "bank_statement", "itr", "form16"))
        return _mask_check(agent, data, dt, [pan, aad], sorted(set(n for n in nums if len(n) >= 4)))
    gr = R.ItemCache("pii_masking", f"pages-{ver}-{split}").run(pages, page_one, label="printed on real pages", every=15,
                                                              on_error=on_err)

    aad = aadhaar_items()

    def aad_one(it):
        data = it["path"].read_bytes()
        own = _own_numbers(data)
        r = _mask_check(agent, data, "aadhaar", own, [])
        r["own_found"] = len(own)
        return r
    ar = R.ItemCache("pii_masking", "aadhaar-v1" if ver == "v1" else f"aadhaar-{ver}").run(
        aad, aad_one, label="aadhaar variants", every=12, on_error=on_err)

    def block(rows):
        return {"not_exposed": R.wilson(sum(not exposed(r) for r in rows), len(rows)),
                "last4_kept": R.wilson(sum(bool(r.get("last4")) and not exposed(r) for r in rows), len(rows)),
                "nothing_masked": sum(1 for r in rows if not r.get("masked_regions") and not r.get("withheld")),
                "withheld": sum(1 for r in rows if r.get("withheld")), "n": len(rows)}
    labelled = pr + gr
    others_before = sum(r.get("others_readable_before", 0) for r in gr)
    metrics = {"pii_recall": R.wilson(sum(not exposed(r) for r in labelled), len(labelled)),
               "last4_kept": R.wilson(sum(bool(r.get("last4")) and not exposed(r) for r in labelled), len(labelled)),
               "other_numbers_intact": R.wilson(sum(r.get("others_intact", 0) for r in gr), others_before),
               "withheld_rate": R.wilson(sum(bool(r.get("withheld")) for r in labelled), len(labelled)),
               "by_set": {"pan_cards": block(pr), "pan_cards_with_extracted_value": block(px) if px else None,
                          "ids_on_real_pages": block(gr),
                          "ids_on_real_pages_by_source": {k: block([r for it, r in zip(pages, gr) if it["kind"] == k])
                                                          for k in ("sroie", "funsd", "rvl")},
                          "aadhaar_unlabelled": {**block([r for r in ar if r.get("own_found")]),
                                                 "own_number_found_by_ocr": sum(1 for r in ar if r.get("own_found")),
                                                 "n_images": len(ar)}},
               "not_rendered": sum(not r["rendered"] for r in labelled + ar),
               "latency": R.latency([r["ms"] for r in labelled + ar if r.get("ms")])}
    data = {"name": "PAN cards (synthetic, labelled); generated PAN + Aadhaar printed under real SROIE / FUNSD / RVL-CDIP "
                    "pages; synthetic Aadhaar photos (numbers found by OCR of the original)",
            "split": split, "n_pan": len(pr), "n_pages": len(gr), "n_aadhaar": len(ar), "code_version": ver,
            "check": "the masked render is read back by OCR at 4 rotations; an ID is EXPOSED when it reads back "
                     "(exactly or with a look-alike character) or when nothing was masked; last4_kept counts "
                     "documents that are not exposed AND still show the last four characters"}
    return R.save("pii_masking", split if ver != "v1" else f"{split}.baseline", metrics, data)


# ── Validation ──────────────────────────────────────────────────────────────
def eval_validation(split: str = "test") -> dict:
    from eval.datasets.synth.identities import make_aadhaar, make_gstin, verhoeff_valid
    from app.agents.registry import get_agent
    agent = get_agent("validation")
    flag = lambda k, v: agent.execute({"doc_type": "generic", "parsed": {k: v}}).payload["fields"][0]["flag"]  # noqa: E731
    codes = json.loads(IFSC.read_text(encoding="utf-8"))
    rejected = [c for c in codes if flag("ifsc", c) != "ok"]
    accepted = len(codes) - len(rejected)
    rng = random.Random(R.SEED)
    cases = []                                             # (validator, value, expected, family)
    for c in R.sample([{"id": c} for c in codes], 1500, salt=split):
        c = c["id"]
        cases.append(("ifsc", c, "ok", "ifsc_real"))
        cases.append(("ifsc", c.lower(), "ok", "ifsc_lowercase"))
        cases.append(("ifsc", c[:4] + rng.choice("123456789O") + c[5:], "error", "ifsc_5th_not_zero"))
        cases.append(("ifsc", c[:-1], "error", "ifsc_short"))
        cases.append(("ifsc", c + rng.choice(string.digits), "error", "ifsc_long"))
        cases.append(("ifsc", rng.choice(string.digits) + c[1:], "error", "ifsc_digit_in_bank_code"))
    for it in pan_items(split, 500):
        p = it["expected"]["pan_number"]
        cases.append(("pan_number", p, "ok", "pan_real_format"))
        i = rng.choice([0, 1, 2, 3, 4, 9])
        cases.append(("pan_number", p[:i] + rng.choice(string.digits) + p[i + 1:], "error", "pan_letter_to_digit"))
        j = rng.choice([5, 6, 7, 8])
        cases.append(("pan_number", p[:j] + rng.choice(string.ascii_uppercase) + p[j + 1:], "error", "pan_digit_to_letter"))
        cases.append(("gstin", make_gstin(rng, p, "Maharashtra"), "ok", "gstin"))
        g = make_gstin(rng, p, "Karnataka")
        cases.append(("gstin", g[:13] + "Y" + g[14:], "error", "gstin_14th_not_z"))
    for _ in range(500):
        a = make_aadhaar(rng)
        cases.append(("aadhaar_number", f"{a[:4]} {a[4:8]} {a[8:]}", "ok", "aadhaar_valid"))
        j = rng.randrange(12)
        m = a[:j] + str((int(a[j]) + rng.randint(1, 9)) % 10) + a[j + 1:]
        cases.append(("aadhaar_number", m, "error" if not verhoeff_valid(m) else "ok", "aadhaar_one_digit"))
        k = rng.randrange(11)
        if a[k] != a[k + 1]:
            s = a[:k] + a[k + 1] + a[k] + a[k + 2:]
            cases.append(("aadhaar_number", s, "error" if not verhoeff_valid(s) else "ok", "aadhaar_transposition"))
    # IFSC codes printed on the Indian statement set: what an applicant's statement actually carries
    stmt_ifsc = [json.loads(p.read_text(encoding="utf-8")).get("ifsc_code") for p in STMT.glob("*/*.json")]
    stmt_ok = sum(flag("ifsc", v) == "ok" for v in stmt_ifsc if v)
    real_set = set(codes)
    stmt_registered = sum(1 for v in stmt_ifsc if v and v.upper() in real_set)
    per, wrong = {}, []
    for k, v, e, fam in cases:
        got = flag(k, v)
        b = per.setdefault(fam, [0, 0])
        b[0] += got == e
        b[1] += 1
        if got != e and len(wrong) < 10:
            wrong.append({"family": fam, "expected": e, "got": got})
    n_ok = sum(b[0] for b in per.values())
    metrics = {"accuracy": R.wilson(n_ok, len(cases)), "real_ifsc_accepted": R.wilson(accepted, len(codes)),
               "per_family": {k: round(v[0] / v[1], 4) for k, v in sorted(per.items())},
               "statement_ifsc_format_ok": R.wilson(stmt_ok, len([v for v in stmt_ifsc if v])),
               "statement_ifsc_in_real_register": R.wilson(stmt_registered, len([v for v in stmt_ifsc if v])),
               "n_cases": len(cases), "n_real_ifsc": len(codes)}
    data = {"name": "Razorpay IFSC register (all real codes) + mutations; PAN formats from the PAN set; generated "
                    "Aadhaar (Verhoeff) and GSTIN + mutations; IFSCs printed on the Indian statement set",
            "split": split, "note": "the validator checks FORMAT (and the Aadhaar checksum); whether a branch exists "
                                    "is not checked — statement_ifsc_in_real_register shows what that would add"}
    return R.save("validation", split, metrics, data, failures=wrong, real_ifsc_rejected=rejected[:20])
