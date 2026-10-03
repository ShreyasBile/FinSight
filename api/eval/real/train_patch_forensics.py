"""Train and judge a patch-level forgery detector on REAL forgeries (development data only).

Why: the tampering CNN in the app was trained on synthetic pages over ELA maps. Real receipts in Find it
again! are PNG files (no compression history, so ELA is an edge map) and their forgeries are a few
characters (median box 26 x 35 px) changed in Paint or GIMP; SIDTD's are fields or photos replaced on ID
templates. Per-signal AUC on real development documents was 0.4-0.67 and the locator boxed nothing.

Protocol (development split only - the test splits are not read here):
  two folds per family   receipts: Find it again!'s official TRAIN and VAL files
                         ids:      SIDTD development documents, split by template group
  per fold               round 1 on the other fold; HARD NEGATIVES = genuine text of that fold which
                         round 1 calls forged; round 2 with them; score the held-out fold
  what is measured       on documents the model never saw: ranking (AUC), the share of forged documents
                         above the 95th percentile of genuine ones, and where the hottest area lies
  what is saved          BOTH fold models, each with the floor measured on the documents it never saw (and on
                         genuine documents of another kind: phone photos of ID cards, scanned forms)
                         -> $FINSIGHT_EVAL_DATA/work/patch_models/<family>_A.pt, _B.pt, <family>.json

    python -m eval.real.train_patch_forensics receipts|ids
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path

import numpy as np

from eval import common as C
from eval.real import core as R
from app.tampering.signals.patch_model import PATCH, STRIDE, build_model, features, fit_size, hottest, margins, page_stats

OUT = Path(R.DATA_ROOT) / "work" / "patch_models"
THREADS = int(os.environ.get("PATCH_THREADS", "12"))


# ── documents and patches ───────────────────────────────────────────────────
def doc(it) -> dict:
    from PIL import Image
    src = it["image"] if it.get("image") is not None else Image.open(it["path"]).convert("RGB")
    img, k = fit_size(src)
    return {"f": features(img), "regions": [tuple(float(v) * k for v in b) for b in it["regions"]], "forged": it["forged"]}


def photo_like(img, rng):
    """A genuine flat document as a phone would capture it: on a surface, slightly turned and in perspective,
    blurred, with sensor noise, uneven light and JPEG compression. The detector is trained on flat images;
    without such examples it calls real phone photos of genuine cards 'edited' (46% on development photos)."""
    import cv2
    from PIL import Image
    src = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    k = rng.uniform(0.55, 1.0)
    src = cv2.resize(src, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    h, w = src.shape[:2]
    H, W = int(h * rng.uniform(1.25, 1.9)), int(w * rng.uniform(1.15, 1.6))
    base = np.array([rng.randint(40, 220) for _ in range(3)], np.float32)
    canvas = np.ones((H, W, 3), np.float32) * base
    canvas += cv2.GaussianBlur(np.random.default_rng(rng.randint(0, 1 << 30)).normal(0, 12, (H, W)).astype(np.float32),
                               (0, 0), rng.uniform(1, 6))[..., None]
    ox, oy = rng.uniform(0, W - w), rng.uniform(0, H - h)
    quad = np.float32([[ox, oy], [ox + w, oy], [ox + w, oy + h], [ox, oy + h]])
    quad += np.float32([[rng.uniform(-0.04, 0.04) * w, rng.uniform(-0.04, 0.04) * h] for _ in range(4)])
    M = cv2.getPerspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]), quad)
    warped = cv2.warpPerspective(src, M, (W, H), flags=cv2.INTER_LINEAR).astype(np.float32)
    mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), M, (W, H)).astype(np.float32)[..., None] / 255
    out = canvas * (1 - mask) + warped * mask
    out = out * rng.uniform(0.7, 1.2) + rng.uniform(-20, 20)
    light = np.linspace(rng.uniform(0.75, 1.0), rng.uniform(0.9, 1.1), W)[None, :, None]
    out = np.clip(out * light, 0, 255)
    out = cv2.GaussianBlur(out, (0, 0), rng.uniform(0.4, 1.6))
    out += np.random.default_rng(rng.randint(0, 1 << 30)).normal(0, rng.uniform(1, 5), out.shape)
    out = np.clip(out, 0, 255).astype(np.uint8)
    ok, enc = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, rng.randint(60, 95)])
    out = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    return Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))


def _crop(f, cx, cy):
    _, h, w = f.shape
    x0 = int(min(max(cx - PATCH // 2, 0), max(w - PATCH, 0)))
    y0 = int(min(max(cy - PATCH // 2, 0), max(h - PATCH, 0)))
    p = f[:, y0:y0 + PATCH, x0:x0 + PATCH]
    if p.shape[1:] != (PATCH, PATCH):
        q = np.zeros((3, PATCH, PATCH), np.float32)
        q[:, :p.shape[1], :p.shape[2]] = p
        p = q
    return p


def _inside(cx, cy, boxes, pad=0.0):
    return any(b[0] - pad <= cx <= b[2] + pad and b[1] - pad <= cy <= b[3] + pad for b in boxes)


def sample(d, rng, pos_per_box, neg_genuine, neg_forged):
    f, boxes = d["f"], d["regions"]
    _, h, w = f.shape
    text = np.argwhere(np.abs(f[1]) > 0.15)                    # where the ink is: negatives must be text too
    X, y = [], []
    for b in boxes:
        for _ in range(pos_per_box):
            X.append(_crop(f, rng.uniform(b[0], b[2]), rng.uniform(b[1], b[3])))
            y.append(1)
    for _ in range(neg_forged if d["forged"] else neg_genuine):
        for _try in range(20):
            if len(text) and rng.random() < 0.8:
                cy, cx = text[rng.randrange(len(text))]
            else:
                cx, cy = rng.uniform(0, w), rng.uniform(0, h)
            if not _inside(cx, cy, boxes, pad=PATCH):
                break
        X.append(_crop(f, cx, cy))
        y.append(0)
    return X, y


def patches(items, rng, kw, log):
    t, X, y = time.time(), [], []
    for it in items:
        Xi, yi = sample(doc(it), rng, **kw)
        X += [x.astype(np.float16) for x in Xi]
        y += yi
    log(f"    {len(items)} documents -> {len(y)} patches ({sum(y)} forged) in {time.time() - t:.0f} s")
    return np.stack(X), np.array(y, dtype=np.int64)


def train(X, y, log, epochs=10):
    import torch
    import torch.nn.functional as F
    torch.manual_seed(R.SEED)
    torch.set_num_threads(THREADS)
    net = build_model()
    opt = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=1e-4)
    w = torch.tensor([1.0, max(1.0, float((y == 0).sum()) / max(int((y == 1).sum()), 1))], dtype=torch.float32)
    yt = torch.from_numpy(y)
    n = len(yt)
    t = time.time()
    for ep in range(epochs):
        net.train()
        perm = torch.randperm(n)
        tot = 0.0
        for i in range(0, n, 256):
            idx = perm[i:i + 256]
            xb = torch.from_numpy(X[idx.numpy()].astype(np.float32))
            if torch.rand(1).item() < 0.5:                     # flips: editing traces have no orientation
                xb = torch.flip(xb, dims=[3])
            if torch.rand(1).item() < 0.5:
                xb = torch.flip(xb, dims=[2])
            loss = F.cross_entropy(net(xb), yt[idx], weight=w)
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot += loss.item() * len(idx)
    log(f"    trained {epochs} epochs on {n} patches, last loss {tot / n:.4f} ({time.time() - t:.0f} s)")
    net.eval()
    return net


def hard_negatives(net, items, rng, per_doc=30):
    """Windows of GENUINE documents the model calls forged (margin > 0): the negatives worth learning from."""
    X = []
    for it in items:
        if it["forged"]:
            continue
        d = doc(it)
        m = margins(net, d["f"])
        hot = [tuple(v) for v in np.argwhere(m > 0)]
        rng.shuffle(hot)
        for r, c in hot[:per_doc]:
            X.append(_crop(d["f"], c * STRIDE + PATCH / 2, r * STRIDE + PATCH / 2).astype(np.float16))
    return X


def fit(items, rng, kw, log):
    """Round 1, hard negatives from the same documents, round 2."""
    X, y = patches(items, rng, kw, log)
    net = train(X, y, log)
    hn = hard_negatives(net, items, rng)
    log(f"    {len(hn)} hard negatives")
    if hn:
        X, y = np.concatenate([X, np.stack(hn)]), np.concatenate([y, np.zeros(len(hn), np.int64)])
        net = train(X, y, log)
    return net


def score(net, items) -> list[dict]:
    rows = []
    for it in items:
        d = doc(it)
        m = margins(net, d["f"])
        cx, cy = hottest(m)
        rows.append({**page_stats(m), "forged": d["forged"], "boxed": bool(d["regions"]),
                     "on_edit": _inside(cx, cy, d["regions"], pad=PATCH / 2),        # the area overlaps the edit
                     "centre_in_edit": _inside(cx, cy, d["regions"])})               # the test's own criterion
    return rows


def _summary(rows, stat) -> dict:
    f = [r[stat] for r in rows if r["forged"]]
    g = sorted(r[stat] for r in rows if not r["forged"])
    floor = g[min(len(g) - 1, int(0.95 * len(g)))]
    flagged = [r for r in rows if r["forged"] and r[stat] > floor and r["boxed"]]
    return {"auc": round(C.auc(f, g), 4), "genuine_p95": round(floor, 3), "genuine_max": round(g[-1], 3),
            "forged_above_floor": round(sum(v > floor for v in f) / len(f), 4),
            "genuine_above_floor": round(sum(v > floor for v in g) / len(g), 4),
            "flagged_forged_area_on_edit": round(sum(r["on_edit"] for r in flagged) / max(len(flagged), 1), 4),
            "flagged_forged_centre_in_edit": round(sum(r["centre_in_edit"] for r in flagged) / max(len(flagged), 1), 4),
            "n_forged": len(f), "n_genuine": len(g)}


def _p95(v):
    v = sorted(v)
    return v[min(len(v) - 1, int(0.95 * len(v)))]


def run(family: str, items, fold_of, kw, extra_genuine=None, camera_like: int = 0, log=print):
    """Two models, each trained on one half of the development documents and judged on the other half.
    BOTH are kept for the app, each with the floor measured on documents it never saw: two runs of the
    same recipe give models with different score scales, so a floor cannot be carried over to a third model
    trained on everything."""
    import torch
    rng = random.Random(R.SEED)
    OUT.mkdir(parents=True, exist_ok=True)
    rows, extra_rows = [], []
    for held in (False, True):
        name = "B" if held else "A"                             # model A is judged on fold A, having trained on fold B
        log(f"  model {name}: trained on the other fold, judged on fold {name}")
        tr = [it for it in items if fold_of(it) != held]
        if camera_like:
            from PIL import Image
            gen = [it for it in tr if not it["forged"]]
            for i in range(camera_like):
                g = gen[i % len(gen)]
                tr.append({"image": photo_like(Image.open(g["path"]), rng), "regions": [], "forged": False})
            log(f"    + {camera_like} camera-like renderings of this fold's genuine documents")
        net = fit(tr, rng, kw, log)
        torch.save(net.state_dict(), OUT / f"{family}_{name}.pt")
        rs = [{**r, "model": name} for r in score(net, [it for it in items if fold_of(it) == held])]
        rows += rs
        if extra_genuine:                       # genuine documents of another kind (phone photos, forms): never trained on
            ex = [{"model": name, **page_stats(margins(net, features(fit_size(im)[0])))} for im in extra_genuine]
            extra_rows += ex
    out = calibrate(family, rows, extra_rows)
    log(f"  cross-fitted on development data: {json.dumps(out)}")
    log(f"  saved {OUT / (family + '_A.pt')} and _B.pt with their calibration")
    return out


def calibrate(family: str, rows=None, extra_rows=None) -> dict:
    """Per model: the statistic, its genuine floor and its span, from the held-out scores (given, or as saved
    by the last training run - so the calibration can be redone without training again).
    floor   95% of held-out genuine documents stay at or below it - of BOTH kinds (flat images and phone
            photos for ID cards, receipts and forms for pages): the higher of the two 95th percentiles
    span    how far above the floor a typical caught forgery stands (median of the held-out forged documents
            above the floor); the app's evidence rises from 0.6 at the floor to 1.0 one span above"""
    scores = R.RESULTS / f"patch_forensics_dev_{family}.scores.json"
    if rows is None:
        saved = json.loads(scores.read_text(encoding="utf-8"))
        rows, extra_rows = saved["documents"], saved["other_genuine"]
    stat = max(("top", "excess"), key=lambda s_: _summary(rows, s_)["auc"])
    out = {"family": family, "documents": len(rows), "forged": sum(bool(r["forged"]) for r in rows), "statistic": stat,
           "pooled_auc": {s_: _summary(rows, s_)["auc"] for s_ in ("top", "excess")}, "models": {}}
    for name in sorted({r["model"] for r in rows}):
        rs = [r for r in rows if r["model"] == name]
        g = [r[stat] for r in rs if not r["forged"]]
        f = [r for r in rs if r["forged"]]
        o = [r[stat] for r in (extra_rows or []) if r["model"] == name]
        floor = max(_p95(g), _p95(o)) if o else _p95(g)
        above = sorted(r[stat] - floor for r in f if r[stat] > floor)
        span = above[len(above) // 2] if above else max(max(g + o) - floor, 1e-3)
        hit = [r for r in f if r[stat] > floor and r["boxed"]]
        out["models"][name] = {
            "floor": round(floor, 3), "span": round(max(span, 1e-3), 3),
            "auc": _summary(rs, stat)["auc"], "n_forged": len(f), "n_genuine": len(g),
            "genuine_p95": round(_p95(g), 3), "other_genuine_p95": round(_p95(o), 3) if o else None,
            "forged_above_floor": round(sum(r[stat] > floor for r in f) / len(f), 4),
            "genuine_above_floor": round(sum(v > floor for v in g) / len(g), 4),
            "other_genuine_above_floor": round(sum(v > floor for v in o) / len(o), 4) if o else None,
            "flagged_forged_centre_in_edit": round(sum(r["centre_in_edit"] for r in hit) / max(len(hit), 1), 4),
            "flagged_forged_area_on_edit": round(sum(r["on_edit"] for r in hit) / max(len(hit), 1), 4)}
    scores.write_text(json.dumps(
        {"documents": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()} for r in rows],
         "other_genuine": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()} for r in (extra_rows or [])]}),
        encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{family}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    (R.RESULTS / f"patch_forensics_dev_{family}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def main():
    C.setup_env(provider="ollama", db=False)
    import io
    from PIL import Image
    from eval.real import evidence_real as E
    from eval.real import intake_real as I
    what = sys.argv[1]
    log = lambda m: print(m, flush=True)  # noqa: E731
    if what == "calibrate":
        print(json.dumps(calibrate(sys.argv[2]), indent=1))
        return
    if what == "receipts":
        dev = [it for it in E.findit_items("dev") if it["path"].exists()]     # one listed val file is absent from the copy
        forms = [Image.open(io.BytesIO(I._png(it["image"]))).convert("RGB") for it in I.funsd_items("dev", 40)]
        run("page", dev, lambda it: it["path"].parent.name == "val",
            dict(pos_per_box=24, neg_genuine=40, neg_forged=12), extra_genuine=forms, log=log)
    elif what == "ids":
        dev = E.sidtd_items("dev", 10000)
        photos = [Image.open(io.BytesIO(it["image"])).convert("RGB") for it in I.midv_items("dev")]
        run("id", dev, lambda it: R._u(it["group"], "fold") < 0.5,
            dict(pos_per_box=12, neg_genuine=20, neg_forged=6), extra_genuine=photos, camera_like=220, log=log)


if __name__ == "__main__":
    main()
