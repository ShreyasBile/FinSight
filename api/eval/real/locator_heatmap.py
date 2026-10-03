"""Development experiment: can the Tamper Locator box pixel-level forgeries from the forensic heatmaps?

The locator only boxes findings that cite a value printed on the page (a figure, an ID). Real forgeries
in Find it again! are caught — when they are — by pixel signals (ELA, noise) that cite nothing, so on the
real test split it boxed 0.6% of flagged forged receipts and hit none. This experiment proposes boxes
from the same blockwise ELA / noise z-maps the signals compute (no new detector, no model), and measures
on the DEVELOPMENT split only: hit rate on forged receipts (box centre inside a true forged region, or
IoU >= 0.5) and the share of genuine receipts that get any box. A setting is adopted only if it boxes
real forgeries clearly more often than it boxes genuine pages.
"""
from __future__ import annotations

import io
import json

import numpy as np

from eval.real import core as R


def _zmap(gray: np.ndarray, b: int, kind: str):
    import cv2
    h, w = gray.shape
    rows, cols = max(h // b, 1), max(w // b, 1)
    if kind == "ela":
        m = np.array([[gray[r * b:(r + 1) * b, c * b:(c + 1) * b].mean() for c in range(cols)] for r in range(rows)],
                     dtype=np.float32)
        med = float(np.median(m))
        scale = max(1.4826 * float(np.median(np.abs(m - med))), 0.15 * med, 0.5)
        return (m - med) / scale
    res = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)
    gx, gy = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    flat = cv2.magnitude(gx, gy) < 30.0
    m = np.full((rows, cols), np.nan, dtype=np.float32)
    for r in range(rows):
        for c in range(cols):
            sl = (slice(r * b, (r + 1) * b), slice(c * b, (c + 1) * b))
            if flat[sl].sum() >= (b * b) // 4:
                m[r, c] = np.median(np.abs(res[sl][flat[sl]]))
    med = float(np.nanmedian(m))
    scale = max(1.4826 * float(np.nanmedian(np.abs(m - med))), 0.1 * med, 1.0)
    z = np.abs((m - med) / scale)
    z[np.isnan(z)] = 0.0
    return z


def zmaps(image) -> dict:
    """{(kind, block): z-map} for one image (ELA computed once)."""
    from app.tampering.signals.ela import compute_ela_image
    grays = {"ela": np.asarray(compute_ela_image(image).convert("L"), dtype=np.float32),
             "noise": np.asarray(image.convert("L"), dtype=np.float32)}
    return {(k, b): _zmap(grays[k], b, k) for k in grays for b in (16, 32, 64)}


def boxes(z: np.ndarray, b: int, z_thr: float, top_k: int) -> list[tuple]:
    """Connected hot blocks -> boxes in image pixels, strongest first."""
    import cv2
    hot = (z > z_thr).astype(np.uint8)
    n, lab = cv2.connectedComponents(hot, connectivity=8)
    out = []
    for i in range(1, n):
        ys, xs = np.where(lab == i)
        out.append((float(z[ys, xs].max()), (xs.min() * b, ys.min() * b, (xs.max() + 1) * b, (ys.max() + 1) * b), len(xs)))
    out.sort(key=lambda t: -t[0])
    return [o[1] for o in out[:top_k] if o[2] <= 0.05 * z.size + 1]      # a "region" covering the page is no box


def _hit(regions, bx) -> bool:
    from eval.real.evidence_real import _iou
    return any(_iou(g, x) >= 0.5 or (g[0] <= (x[0] + x[2]) / 2 <= g[2] and g[1] <= (x[1] + x[3]) / 2 <= g[3])
               for g in regions for x in bx)


GRID = [(k, b, z, t) for k in ("ela", "noise") for b in (16, 32, 64) for z in (3.0, 4.0, 6.0) for t in (1, 3)]


def experiment(split: str = "dev", n_genuine: int = 150) -> dict:
    from PIL import Image
    from eval.real.evidence_real import findit_items
    if split != "dev":
        raise SystemExit("development data only")
    items = findit_items("dev")
    items = [x for x in items if x["path"].exists()]          # the mirror lacks one listed file
    forged = [x for x in items if x["forged"] and x["regions"]]
    genuine = R.sample([x for x in items if not x["forged"]], n_genuine)
    res = {g: {"hit": 0, "boxed_forged": 0, "boxed_genuine": 0} for g in GRID}
    for it in forged + genuine:
        zs = zmaps(Image.open(it["path"]).convert("RGB"))
        for g in GRID:
            bx = boxes(zs[(g[0], g[1])], g[1], g[2], g[3])
            if it["forged"]:
                res[g]["hit"] += _hit(it["regions"], bx)
                res[g]["boxed_forged"] += bool(bx)
            else:
                res[g]["boxed_genuine"] += bool(bx)
    table = sorted(({"kind": g[0], "block": g[1], "z": g[2], "top_k": g[3],
                     "hit_rate": round(v["hit"] / len(forged), 3), "boxed_genuine": round(v["boxed_genuine"] / len(genuine), 3)}
                    for g, v in res.items()), key=lambda r: -(r["hit_rate"] - r["boxed_genuine"]))
    out = {"n_forged": len(forged), "n_genuine": len(genuine), "grid": table}
    (R.RESULTS / "tamper_locator_heatmap_dev.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(table[:8], indent=1))
    return out
