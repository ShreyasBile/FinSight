"""Face Verification, Liveness (and Dedupe's face index) on real faces.

Face verification — AgeDB-30, CALFW (cross-age), CPLFW (cross-pose), LFW: the standard 6,000-pair
protocols (3,000 same / 3,000 different, 10 folds whose identities do not overlap). Folds 1-3 are
dev, folds 4-10 test. Two conditions:
  raw   the aligned 112x112 crop centred on a neutral canvas — tests the matcher itself
  card  each face pasted into the PHOTO box of a genuine SIDTD ID-card template (box from the
        dataset's own annotations), then photographed-like (perspective, blur, sensor noise, JPEG)
        — what the agent sees in production: the photo on a PAN against the photo on an Aadhaar
Liveness — NUAA (full 640x480 webcam frames, live vs printed-photo attacks; official train list
= dev, official test list = test) as the primary set, because a production selfie is a full frame.
CelebA-Spoof test crops are reported separately: they are tight face crops (face ~94% of the
image) and the anti-spoof models judge the area AROUND the face, so they are a harder,
out-of-distribution check, not the headline number.
"""
from __future__ import annotations

import io
import json
import random
from pathlib import Path

from eval import common as C
from eval.real import core as R

FACES = R.RAW / "faces" / "val"
SETS = {"agedb_30": "agedb_30_ann.txt", "calfw": "calfw_ann.txt", "cplfw": "cplfw_ann.txt", "lfw": "lfw_ann.txt"}
SIDTD = R.RAW / "sidtd" / "templates"
NUAA = R.RAW / "nuaa"


# ── data ────────────────────────────────────────────────────────────────────
def pairs(split: str):
    out = []
    for name, ann in SETS.items():
        for i, line in enumerate((FACES / ann).read_text().splitlines()):
            lab, a, b = line.split()
            fold = i // 600
            if (fold < 3) == (split == "dev"):
                out.append({"id": f"{name}:{i}", "set": name, "label": int(lab), "a": FACES / a, "b": FACES / b})
    return out


def _jpeg(im, q=92) -> bytes:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=q)
    return buf.getvalue()


def raw_image(p: Path) -> bytes:
    from PIL import Image
    face = Image.open(p).convert("RGB")
    canvas = Image.new("RGB", (240, 240), (128, 128, 128))
    canvas.paste(face.resize((132, 132)), (54, 54))
    return _jpeg(canvas)


_TEMPLATES: list | None = None


def _templates():
    """Genuine SIDTD templates with their annotated photo box: [(path, (x, y, w, h)), ...]."""
    global _TEMPLATES
    if _TEMPLATES is None:
        out = []
        for f in sorted((SIDTD / "Annotations" / "reals").glob("*.json")):
            kind = f.stem
            meta = json.loads(f.read_text(encoding="utf-8")).get("_via_img_metadata", {})
            for m in meta.values():
                box = next((r["shape_attributes"] for r in m["regions"]
                            if r["region_attributes"].get("field_name") == "photo"
                            and r["shape_attributes"].get("name") == "rect"), None)
                img = SIDTD / "Images" / "reals" / f"{kind}_{m['filename']}"
                if box and img.exists():
                    out.append((img, (box["x"], box["y"], box["width"], box["height"])))
        _TEMPLATES = out
    return _TEMPLATES


def card_image(p: Path, key: str) -> bytes:
    """The face in the photo box of a genuine ID template, then a phone-photo capture."""
    from PIL import Image
    from eval.datasets.synth import forge
    tpls = _templates()
    rng = random.Random(key)
    tpl, (x, y, w, h) = tpls[rng.randrange(len(tpls))]
    card = Image.open(tpl).convert("RGB")
    face = Image.open(p).convert("RGB")
    fw, fh = face.size                        # crop the square face to the box's aspect ratio
    ar = w / h
    if fw / fh > ar:
        nw = int(fh * ar)
        face = face.crop(((fw - nw) // 2, 0, (fw - nw) // 2 + nw, fh))
    else:
        nh = int(fw / ar)
        face = face.crop((0, (fh - nh) // 2, fw, (fh - nh) // 2 + nh))
    card.paste(face.resize((int(w), int(h))), (int(x), int(y)))
    # Real ID cards repeat the holder's photo as small "ghost" images; these templates still show the
    # original mock person there. Replace every other face on the template with the same test face
    # (faded, as ghost images are), so a card shows one person, as a genuine card does.
    from PIL import ImageEnhance, ImageOps
    for gx, gy, gw, gh in _template_faces(tpl):
        if gx + gw / 2 > x and gx + gw / 2 < x + w and gy + gh / 2 > y and gy + gh / 2 < y + h:
            continue                          # the main photo, already replaced
        pad = 0.35
        bx0, by0 = max(0, int(gx - gw * pad)), max(0, int(gy - gh * pad))
        bx1, by1 = min(card.width, int(gx + gw * (1 + pad))), min(card.height, int(gy + gh * (1 + pad)))
        ghost = ImageOps.grayscale(face).convert("RGB").resize((bx1 - bx0, by1 - by0))
        ghost = ImageEnhance.Contrast(ghost).enhance(0.6)
        card.paste(ghost, (bx0, by0))
    return _jpeg(forge.camera_capture(card, rng), 88)


_FACE_CACHE: dict = {}


def _template_faces(tpl: Path):
    """Face boxes (x, y, w, h) the detector finds on a template, before anything is pasted."""
    if tpl not in _FACE_CACHE:
        import cv2
        import numpy as np
        from app.face import service
        img = cv2.imread(str(tpl))
        k, rows, _ = service.detect_best_rotation(img)
        _FACE_CACHE[tpl] = [tuple(float(v) for v in r[:4]) for r in rows] if k == 0 else []
    return _FACE_CACHE[tpl]


# ── face verification ───────────────────────────────────────────────────────
def _verify(agent, a: bytes, b: bytes) -> dict:
    with C.Timer() as t:
        res = agent.execute({"documents": [("pan", a), ("aadhaar", b)]}).payload["result"]
    pair = res["pairs"][0] if res["pairs"] else None
    # low_quality: the two faces actually compared (not any face on the card: ID cards carry small ghost
    # images that are often low quality while the main photos are fine)
    return {"found": all(d["face_count"] > 0 for d in res["documents"]), "verdict": pair["verdict"] if pair else None,
            "sim": pair["similarity"] if pair else None, "ms": round(t.ms, 1),
            "low_quality": bool(pair and pair.get("low_quality"))}


def _face_metrics(rows) -> dict:
    det = [r for r in rows if r["verdict"]]
    conf = [r for r in det if r["verdict"] in ("match", "different")]
    same = [r for r in conf if r["label"]]
    diff = [r for r in conf if not r["label"]]
    imp = sorted(r["sim"] for r in det if not r["label"])
    thr = imp[int(0.99 * (len(imp) - 1))] if imp else None
    gen = [r["sim"] for r in det if r["label"]]
    return {
        "verdict_accuracy": R.wilson(sum((r["verdict"] == "match") == bool(r["label"]) for r in conf), len(conf)),
        "confident_coverage": R.wilson(len(conf), len(det)),
        "detection_rate": R.wilson(sum(r["found"] for r in rows), len(rows)),
        "false_match_rate": R.wilson(sum(r["verdict"] == "match" for r in diff), len(diff)),
        "false_mismatch_rate": R.wilson(sum(r["verdict"] == "different" for r in same), len(same)),
        "tar_at_far_1pct": R.wilson(sum(s > thr for s in gen), len(gen)) if thr is not None else None,
        "accuracy_at_0363": R.wilson(sum((r["sim"] >= 0.363) == bool(r["label"]) for r in det), len(det)),
        "auc": R.auc_ci(det, score=lambda r: r["sim"]),
        "low_quality_never_mismatch": 1.0 if not any(r["low_quality"] and r["verdict"] == "different" for r in rows) else 0.0,
        "n": len(rows)}


def eval_face(split: str = "test", n: int = 1000, n_card: int = 250) -> dict:
    from app.agents.registry import get_agent
    agent = get_agent("face_verification")
    allp = pairs(split)
    raw_rows, card_rows = [], []
    for name in SETS:
        ps = [p for p in allp if p["set"] == name]
        pick = R.stratified(ps, n, strata=lambda p: p["label"])
        cache = R.ItemCache("face_verification", "raw-v1")
        out = cache.run(pick, lambda p: _verify(agent, raw_image(p["a"]), raw_image(p["b"])), label=f"{name}/raw", every=250)
        raw_rows += [{**o, "label": p["label"], "set": name} for p, o in zip(pick, out)]
        pick_c = pick[:n_card]
        cache_c = R.ItemCache("face_verification", "card-v2")
        out_c = cache_c.run(pick_c, lambda p: _verify(agent, card_image(p["a"], p["id"] + "a"), card_image(p["b"], p["id"] + "b")),
                            label=f"{name}/card", every=125)
        card_rows += [{**o, "label": p["label"], "set": name} for p, o in zip(pick_c, out_c)]
    allrows = raw_rows + card_rows
    m = _face_metrics(allrows)
    metrics = {**m,
               "by_condition": {"raw": _face_metrics(raw_rows), "card": _face_metrics(card_rows)},
               "by_set": {s: _face_metrics([r for r in allrows if r["set"] == s]) for s in SETS},
               "latency": R.latency([r["ms"] for r in allrows])}
    data = {"name": "AgeDB-30, CALFW, CPLFW, LFW (aligned 112x112); SIDTD genuine ID templates for the card condition",
            "split": f"{split} (standard folds {'1-3' if split == 'dev' else '4-10'})", "n_pairs": len(allrows),
            "raw_pairs": len(raw_rows), "card_pairs": len(card_rows),
            "leakage": "SFace (OpenCV zoo) was trained on MS-Celeb-1M; LFW identities may overlap that set, "
                       "AgeDB/CALFW/CPLFW were built as harder tests — results reported per set"}
    return R.save("face_verification", split, metrics, data)


# ── liveness ────────────────────────────────────────────────────────────────
def _acer(rows, t):
    att = [r for r in rows if r["label"] == "attack" and r["score"] is not None]
    live = [r for r in rows if r["label"] == "live" and r["score"] is not None]
    apcer = sum(r["score"] >= t for r in att) / max(1, len(att))
    bpcer = sum(r["score"] < t for r in live) / max(1, len(live))
    return (apcer + bpcer) / 2, apcer, bpcer


def nuaa_items(split: str):
    """Official NUAA lists: *_train_raw.txt = dev, *_test_raw.txt = test."""
    out = []
    for kind, folder, label in (("client", "ClientRaw", "live"), ("imposter", "ImposterRaw", "attack")):
        lst = next(NUAA.rglob(f"{kind}_{'train' if split == 'dev' else 'test'}_raw.txt"), None)
        if lst is None:
            continue
        base = next(NUAA.rglob(folder))
        for line in lst.read_text().split():
            rel = line.replace("\\", "/").split(f"{folder}/", 1)[-1]   # lists may carry the mirror's absolute paths
            p = base / rel
            if p.exists():
                out.append({"id": f"nuaa:{folder}/{rel}", "path": p, "label": label, "subject": rel.split("/")[0]})
    return out


def _live(agent, data: bytes) -> dict:
    with C.Timer() as t:
        r = agent.execute({"data": data}).payload
    res = r.get("result", r) if isinstance(r, dict) else {}
    return {"verdict": res.get("verdict"), "score": res.get("live_score"), "ms": round(t.ms, 1)}


def _liveness_block(rows) -> dict:
    from app.face import liveness
    acer, apcer, bpcer = _acer(rows, liveness.LIVE_T)
    att = [r for r in rows if r["label"] == "attack" and r["score"] is not None]
    live = [r for r in rows if r["label"] == "live" and r["score"] is not None]
    ci = R.bootstrap(lambda s: round(_acer(s, liveness.LIVE_T)[0], 4), rows)["ci95"]
    return {"acer": {"value": round(acer, 4), "ci95": ci, "n": len(rows)},
            "apcer": R.wilson(sum(r["score"] >= liveness.LIVE_T for r in att), len(att)),
            "bpcer": R.wilson(sum(r["score"] < liveness.LIVE_T for r in live), len(live)),
            "auc": R.auc_ci([r for r in rows if r["score"] is not None], score=lambda r: r["score"],
                            label=lambda r: r["label"] == "live"),
            "no_face": sum(r["score"] is None for r in rows), "n_live": len(live), "n_attack": len(att),
            "threshold": liveness.LIVE_T}


def eval_liveness(split: str = "test", n: int = 2000, n_celeba: int = 1000) -> dict:
    import pyarrow.parquet as pq
    from app.agents.registry import get_agent
    agent = get_agent("liveness")
    items = R.stratified(nuaa_items(split), n, strata=lambda x: x["label"])
    cache = R.ItemCache("liveness", "nuaa-v1")
    out = cache.run(items, lambda x: _live(agent, x["path"].read_bytes()), label="NUAA", every=250)
    nuaa = [{**o, "label": x["label"]} for x, o in zip(items, out)]
    # CelebA-Spoof crops: shard 0 = dev, shard 1 = test (each holds both classes)
    shard = sorted((R.RAW / "celeba_spoof" / "data").glob("*.parquet"))[0 if split == "dev" else 1]
    tab = pq.read_table(shard).to_pylist()
    citems = [{"id": f"celeba:{shard.name}:{i}", "i": i, "label": "live" if r["labels"] == 0 else "attack"}
              for i, r in enumerate(tab) if (r.get("cropped_image") or {}).get("bytes")]   # a few rows carry no image
    citems = R.stratified(citems, n_celeba, strata=lambda x: x["label"])
    ccache = R.ItemCache("liveness", "celeba-v1")
    cout = ccache.run(citems, lambda x: _live(agent, tab[x["i"]]["cropped_image"]["bytes"]), label="CelebA-crops", every=250)
    celeba = [{**o, "label": x["label"]} for x, o in zip(citems, cout)]
    m = _liveness_block(nuaa)
    metrics = {**m, "celeba_spoof_crops": _liveness_block(celeba),
               "latency": R.latency([r["ms"] for r in nuaa])}
    data = {"name": "NUAA Photograph Imposter Database (primary, full frames); CelebA-Spoof test crops (secondary)",
            "split": f"{split} (NUAA official {'train' if split == 'dev' else 'test'} list)", "n": len(nuaa),
            "n_celeba": len(celeba), "subjects": sorted({x['subject'] for x in items})}
    return R.save("liveness", split, metrics, data)
