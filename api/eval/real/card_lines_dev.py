"""Development experiment: find an ID card's outline from straight edges (MIDV development frames only).

The contour method needs one unbroken outline; a hand or clutter breaks it. A card's four SIDES are long
straight edges even when a finger covers part of one. This detects line segments, merges collinear ones,
forms quadrilaterals from two near-parallel pairs, and keeps the one whose sides are best covered by
edges and whose shape is an ID card's (sides about 1.59 : 1 under mild perspective).

    python -m eval.real.card_lines_dev
"""
from __future__ import annotations

import io
import itertools
import json
import math

import numpy as np

from eval import common as C
from eval.real import core as R


def _segments(gray):
    import cv2
    lsd = cv2.createLineSegmentDetector()
    lines = lsd.detect(gray)[0]
    if lines is None:
        return np.zeros((0, 4), np.float32)
    return lines.reshape(-1, 4)


def _lines(segs, min_len):
    """Merge near-collinear segments into infinite lines: (theta, rho, support length)."""
    out = []
    for x1, y1, x2, y2 in segs:
        L = math.hypot(x2 - x1, y2 - y1)
        if L < min_len:
            continue
        th = math.atan2(y2 - y1, x2 - x1) % math.pi
        nx, ny = -math.sin(th), math.cos(th)
        rho = nx * x1 + ny * y1
        for ln in out:
            dth = min(abs(ln[0] - th), math.pi - abs(ln[0] - th))
            if dth < math.radians(3) and abs(ln[1] - rho) < 6:
                ln[2] += L
                break
        else:
            out.append([th, rho, L])
    return sorted(out, key=lambda l: -l[2])


def _intersect(a, b):
    t1, r1 = a[0], a[1]
    t2, r2 = b[0], b[1]
    A = np.array([[-math.sin(t1), math.cos(t1)], [-math.sin(t2), math.cos(t2)]])
    if abs(np.linalg.det(A)) < 1e-6:
        return None
    return np.linalg.solve(A, [r1, r2])


def _coverage(edges_dt, p, q, step=3.0):
    n = max(2, int(np.linalg.norm(q - p) / step))
    pts = p + (q - p) * np.linspace(0, 1, n)[:, None]
    h, w = edges_dt.shape
    ok = [edges_dt[int(y), int(x)] <= 2.5 for x, y in pts if 0 <= x < w and 0 <= y < h]
    return (sum(ok) / n) if ok else 0.0


def quad_score(img_bgr, q, max_side=800):
    """The same score find_quad ranks by (edge coverage of the sides x ID-card shape x size), for any quad."""
    import cv2
    h0, w0 = img_bgr.shape[:2]
    s = min(1.0, max_side / max(h0, w0))
    img = cv2.resize(img_bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else img_bgr
    h, w = img.shape[:2]
    gray = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (3, 3), 0)
    med = float(np.median(gray))
    dt = cv2.distanceTransform(255 - cv2.Canny(gray, max(10, 0.5 * med), min(255, 1.3 * med)), cv2.DIST_L2, 3)
    q = np.asarray(q, np.float32) * s
    sides = [np.linalg.norm(q[i] - q[(i + 1) % 4]) for i in range(4)]
    long_, short = (sides[0] + sides[2]) / 2, (sides[1] + sides[3]) / 2
    aspect = max(long_, short) / max(min(long_, short), 1)
    cov = [_coverage(dt, q[i], q[(i + 1) % 4]) for i in range(4)]
    area = cv2.contourArea(q)
    return float(np.mean(cov)) * max(0.0, 1 - abs(aspect - 1.586) / 1.586) * math.sqrt(max(area, 0) / (h * w))


def find_quad(img_bgr, max_side=800):
    import cv2
    h0, w0 = img_bgr.shape[:2]
    s = min(1.0, max_side / max(h0, w0))
    img = cv2.resize(img_bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else img_bgr
    h, w = img.shape[:2]
    gray = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (3, 3), 0)
    lines = _lines(_segments(gray), 0.06 * min(h, w))[:40]
    if len(lines) < 4:
        return None
    med = float(np.median(gray))
    edges = cv2.Canny(gray, max(10, 0.5 * med), min(255, 1.3 * med))
    dt = cv2.distanceTransform(255 - edges, cv2.DIST_L2, 3)
    pairs = []
    for a, b in itertools.combinations(lines, 2):
        dth = min(abs(a[0] - b[0]), math.pi - abs(a[0] - b[0]))
        if dth < math.radians(15) and abs(a[1] - b[1]) > 0.12 * min(h, w):
            pairs.append((a, b))
    best, best_score = None, 0.0
    for (a, b), (c, d) in itertools.combinations(pairs, 2):
        ang = min(abs(a[0] - c[0]), math.pi - abs(a[0] - c[0]))
        if ang < math.radians(55):
            continue
        pts = [_intersect(a, c), _intersect(c, b), _intersect(b, d), _intersect(d, a)]
        if any(p is None for p in pts):
            continue
        q = np.array(pts, np.float32)
        if (q[:, 0] < -0.02 * w).any() or (q[:, 0] > 1.02 * w).any() or (q[:, 1] < -0.02 * h).any() or (q[:, 1] > 1.02 * h).any():
            continue
        hull = cv2.convexHull(q)
        if len(hull) != 4:
            continue
        area = cv2.contourArea(q)
        if not (0.08 * h * w <= area <= 0.92 * h * w):
            continue
        sides = [np.linalg.norm(q[i] - q[(i + 1) % 4]) for i in range(4)]
        long_, short = (sides[0] + sides[2]) / 2, (sides[1] + sides[3]) / 2
        aspect = max(long_, short) / max(min(long_, short), 1)
        if not (1.3 <= aspect <= 1.95):
            continue
        cov = [_coverage(dt, q[i], q[(i + 1) % 4]) for i in range(4)]
        if min(cov) < 0.2:
            continue
        score = float(np.mean(cov)) * (1 - abs(aspect - 1.586) / 1.586) * math.sqrt(area / (h * w))
        if score > best_score:
            best, best_score = q, score
    if best is None:
        return None
    return best / s, best_score


def _scale_back(q, img):
    """_card_quad works on the cleanup's Stage-1 image (upscaled to >= 1000 px wide / capped at 4000): the
    quad comes back in the frame's own coordinates for scoring."""
    h, w = img.shape[:2]
    s = min(4000 / w, 4000 / h) if (w > 4000 or h > 4000) else (1000 / w if w < 1000 else 1.0)
    return np.asarray(q, np.float32) / s


def main():
    C.setup_env(provider="ollama", db=False)
    import cv2
    from PIL import Image
    from eval.real.intake_real import _card_quad, _quad_iou, midv_items
    by = {}
    for it in midv_items("dev"):
        img = cv2.cvtColor(np.array(Image.open(io.BytesIO(it["image"])).convert("RGB")), cv2.COLOR_RGB2BGR)
        r = find_quad(img)
        iou = _quad_iou(r[0], it["mask"]) if r else 0.0
        contour = _card_quad(it["image"])
        ciou = _quad_iou(contour, it["mask"]) if contour is not None else 0.0
        b = by.setdefault(it["condition"], {"n": 0, "lines": 0, "contour": 0, "either_contour_first": 0,
                                            "best_of_two": 0, "lines_scores": []})
        b["n"] += 1
        b["lines"] += iou >= 0.85
        b["contour"] += ciou >= 0.85
        b["either_contour_first"] += (ciou if contour is not None else iou) >= 0.85
        cs = quad_score(img, contour) if contour is not None else -1
        b["best_of_two"] += (iou if (r and r[1] > cs) else ciou) >= 0.85
        b["lines_scores"].append((round(r[1], 3) if r else None, round(iou, 2)))
    for k, v in sorted(by.items()):
        print(k, {kk: vv for kk, vv in v.items() if kk != "lines_scores"}, v["lines_scores"][:8])
    tot = {k: sum(v[k] for v in by.values()) for k in ("n", "lines", "contour", "either_contour_first", "best_of_two")}
    print(json.dumps(tot))


if __name__ == "__main__":
    main()
