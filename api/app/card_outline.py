"""Where is the ID card in a phone photo?  (used by preprocess._warp_document_robust for ID documents)

The contour search alone needs one unbroken outline with strong edges. Looking at real phone frames
(MIDV-500 development frames, 2026-10-03) showed where that fails:
  * a pale card on a pale surface has no edge strong enough, but differs in colour;
  * a card washed out by glare on a dark keyboard is one bright blob - trivial for a threshold, yet missed;
  * a card filling 13% of the frame was under the 15% minimum-size rule;
  * the first four-sided contour was taken, right or wrong (9% of frames got a wrong outline).

So candidates are proposed from REGIONS as well as edges - thresholded brightness / colour channels at
three opening sizes (a larger opening separates the card from an object touching it), colour clusters,
"a little unlike the frame's rim" bands, areas of fine print - plus the contour search's own result.
Each rough outline has its sides pulled onto the edges they run along (refine), and ONE score chooses:

    edge support of the four sides  x  ID-card shape (1.59 : 1, near-right angles, opposite sides alike)
    x  the inside differs from what surrounds it (colour, or more print)  x  holds the face photo

Below CONFIDENT no outline is used (the photo is only deskewed): on the development frames every correct
outline scored >= 0.31 and every wrong one <= 0.22. Measured there: card found 49% -> 76% of frames
(table, keyboard and clutter 20 of 20 each), wrong outline used 9% -> 0%.
Not solved: a pale glossy card held in front of a whiteboard (2 of 20 frames).
"""
from __future__ import annotations

import numpy as np

WORK = 1000          # candidates are searched on a frame scaled to this long side
CONFIDENT = 0.30     # below this score an outline is not used


def order(pts):
    """TL, TR, BR, BL."""
    pts = np.asarray(pts, np.float32).reshape(4, 2)
    s, d = pts.sum(1), np.diff(pts, axis=1).ravel()
    return np.float32([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]])


def _quad_of(contour, h, w):
    """A four-cornered outline for a blob: its hull as a 4-gon, else its tight rectangle when it fills it."""
    import cv2
    hull = cv2.convexHull(contour)
    area = cv2.contourArea(hull)
    if not (0.06 * h * w <= area <= 0.80 * h * w):
        return None
    for eps in (0.02, 0.035, 0.05):
        ap = cv2.approxPolyDP(hull, eps * cv2.arcLength(hull, True), True)
        if len(ap) == 4:
            return order(ap)
    box = cv2.minAreaRect(hull)
    bw, bh = box[1]
    if bw * bh > 0 and area / (bw * bh) >= 0.85:
        return order(cv2.boxPoints(box))
    return None


def _channels(img):
    """Grey level plus the channels in which a pale card differs from a pale background (colour, not brightness)."""
    import cv2
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    rim = np.concatenate([lab[:20].reshape(-1, 3), lab[-20:].reshape(-1, 3), lab[:, :20].reshape(-1, 3),
                          lab[:, -20:].reshape(-1, 3)]).astype(np.float32)
    dist = np.linalg.norm(lab.astype(np.float32) - np.median(rim, axis=0), axis=2)        # unlike the frame's rim
    return {"gray": cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), "sat": hsv[..., 1], "a": lab[..., 1], "b": lab[..., 2],
            "rim": np.clip(dist * 4, 0, 255).astype(np.uint8)}


def candidates(img, chans):
    """Quadrilaterals proposed by thresholded channels (both polarities, three opening sizes - a larger
    opening separates the card from an object touching it) and by edge contours in every channel."""
    import cv2
    h, w = img.shape[:2]
    out = []
    for name, ch in chans.items():
        ch = cv2.GaussianBlur(ch, (5, 5), 0)
        _, bw = cv2.threshold(ch, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        for pol, m0 in (("+", bw), ("-", 255 - bw)):
            for ks in (7, 17, 31):
                k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ks, ks))
                m = cv2.morphologyEx(cv2.morphologyEx(m0, cv2.MORPH_OPEN, k), cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
                cnts, _ = cv2.findContours(m, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
                for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:4]:
                    q = _quad_of(c, h, w)
                    if q is not None:
                        out.append((f"{name}{pol}{ks}", q))
        for lo, hi in ((30, 120), (10, 50), (60, 200)):
            ed = cv2.dilate(cv2.Canny(ch, lo, hi), np.ones((3, 3), np.uint8))
            cnts, _ = cv2.findContours(ed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:8]:
                q = _quad_of(c, h, w)
                if q is not None:
                    out.append((f"{name}:edge{lo}", q))
    # a pale card on a pale background: neither bright nor dark, but its own family of colours
    small = cv2.resize(img, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 15, 1.0)
    for kk in (3, 5):
        cv2.setRNGSeed(7)
        _, lbl, _ = cv2.kmeans(lab, kk, None, crit, 2, cv2.KMEANS_PP_CENTERS)
        lbl = lbl.reshape(small.shape[:2])
        for j in range(kk):
            m0 = cv2.resize((lbl == j).astype(np.uint8) * 255, (w, h), interpolation=cv2.INTER_NEAREST)
            for ks in (9, 21):
                k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ks, ks))
                m = cv2.morphologyEx(cv2.morphologyEx(m0, cv2.MORPH_OPEN, k), cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
                cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:3]:
                    q = _quad_of(c, h, w)
                    if q is not None:
                        out.append((f"k{kk}:{ks}", q))
    # a printed card on a smooth surface (a whiteboard, a sheet of paper): the card is where the fine detail is
    g = cv2.GaussianBlur(chans["gray"], (3, 3), 0)
    fine = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    for t in (10, 18, 30):
        dens = cv2.boxFilter((fine > t).astype(np.float32), -1, (25, 25))
        d8 = np.clip(dens * 255, 0, 255).astype(np.uint8)
        _, m0 = cv2.threshold(d8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        for ks in (15, 31):
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ks, ks))
            m = cv2.morphologyEx(cv2.morphologyEx(m0, cv2.MORPH_CLOSE, np.ones((31, 31), np.uint8)), cv2.MORPH_OPEN, k)
            cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:3]:
                q = _quad_of(c, h, w)
                if q is not None:
                    out.append((f"fine{t}:{ks}", q))
    rim = chans["rim"].astype(np.float32) / 4                      # Lab distance from the rim colour
    for lo_, hi_ in ((4, 30), (6, 45), (10, 60)):
        m0 = ((rim >= lo_) & (rim <= hi_)).astype(np.uint8) * 255
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13))
        m = cv2.morphologyEx(cv2.morphologyEx(m0, cv2.MORPH_OPEN, k), cv2.MORPH_CLOSE, np.ones((41, 41), np.uint8))
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:3]:
            q = _quad_of(c, h, w)
            if q is not None:
                out.append((f"band{lo_}", q))
    return out


def score(img, grad, q, faces=None, lab=None):
    """How much a quadrilateral looks like an ID card lying in the frame: (score, parts)."""
    import cv2
    h, w = img.shape[:2]
    sides = [np.linalg.norm(q[i] - q[(i + 1) % 4]) for i in range(4)]
    a, b = (sides[0] + sides[2]) / 2, (sides[1] + sides[3]) / 2
    aspect = max(a, b) / max(min(a, b), 1)
    if not (1.25 <= aspect <= 2.0) or min(sides) < 0.08 * min(h, w):
        return 0.0, {}
    opp = min(sides[0], sides[2]) / max(sides[0], sides[2]) * min(sides[1], sides[3]) / max(sides[1], sides[3])
    for i in range(4):                                         # corners of a card in mild perspective: 90 +- 25 degrees
        u, v = q[i - 1] - q[i], q[(i + 1) % 4] - q[i]
        cosang = float(np.dot(u, v) / max(np.linalg.norm(u) * np.linalg.norm(v), 1e-6))
        if abs(cosang) > 0.42:
            return 0.0, {}
    # edge support: along each side, how often there is a clear gradient within 3 px
    thr = np.percentile(grad, 85)
    sup = []
    for i in range(4):
        p, r = q[i], q[(i + 1) % 4]
        n = max(8, int(np.linalg.norm(r - p) / 4))
        pts = p + (r - p) * np.linspace(0.05, 0.95, n)[:, None]
        nrm = np.array([-(r - p)[1], (r - p)[0]]) / max(np.linalg.norm(r - p), 1)
        hit = on = 0
        for pt in pts:
            if not (3 <= pt[0] < w - 3 and 3 <= pt[1] < h - 3):
                continue                                     # a side running along the frame edge has no outside
            on += 1
            vals = [grad[int(pt[1] + nrm[1] * t), int(pt[0] + nrm[0] * t)] for t in (-3, -1, 0, 1, 3)
                    if 0 <= int(pt[1] + nrm[1] * t) < h and 0 <= int(pt[0] + nrm[0] * t) < w]
            hit += max(vals) >= thr
        sup.append(hit / on if on >= n * 0.3 else None)
    seen = [s for s in sup if s is not None]
    if len(seen) < 3:
        return 0.0, {}
    edge = float(np.mean(seen)) * (min(seen) ** 0.5)
    # inside against a ring outside: a card is a different surface from what it lies on
    inside = np.zeros((h, w), np.uint8)
    cv2.fillPoly(inside, [np.round(q).astype(np.int32)], 1)
    ring = cv2.dilate(inside, np.ones((31, 31), np.uint8)) - inside
    if lab is None:
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    if ring.sum() < 50 or inside.sum() < 50:
        return 0.0, {}
    contrast = float(np.linalg.norm(np.median(lab[inside == 1], axis=0) - np.median(lab[ring == 1], axis=0)))
    shape = 1.0 - min(abs(aspect - 1.586) / 0.6, 1.0) * 0.6
    busy = float((grad[inside == 1] >= thr).mean())            # a card is printed: its inside is busy
    busy_out = float((grad[ring == 1] >= thr).mean())
    # a card is a different surface from what it lies on: in colour, or in how much is printed on it.
    # (A rectangle PRINTED on the sheet under the card has four fine sides and the right shape - and is
    # the same paper inside and out.)
    # An area EMPTIER than its surroundings (blank paper inside a printed frame, a patch of table) needs a
    # much stronger colour difference to count than one that is at least as busy.
    colour = min(contrast / (25.0 if busy >= busy_out else 60.0), 1.0)
    differs = max(colour, min(max(busy - busy_out, 0.0) / 0.10, 1.0))
    s = edge * shape * opp * differs
    if faces:                                                  # the frame shows a face: the card is around it
        poly = np.round(q).astype(np.int32)
        if not any(cv2.pointPolygonTest(poly, (float(x), float(y)), False) >= 0 for x, y in faces):
            s *= 0.5
    return float(s), {"edge": round(edge, 2), "aspect": round(aspect, 2), "opp": round(opp, 2),
                      "contrast": round(contrast, 1), "busy": round(busy, 2), "busy_out": round(busy_out, 2)}


def refine(grad, q, reach: int = 14):
    """Pull each side of a rough quadrilateral onto the edge it runs along: sample across the side, take
    the strongest edge point on each normal, fit a line through those points (trimmed), and intersect the
    four fitted lines. A side with too little edge under it stays where it was."""
    import cv2
    h, w = grad.shape
    thr = np.percentile(grad, 85)
    lines = []
    for i in range(4):
        p, r = q[i], q[(i + 1) % 4]
        d = (r - p) / max(np.linalg.norm(r - p), 1e-6)
        nrm = np.array([-d[1], d[0]])
        pts = []
        for t in np.linspace(0.08, 0.92, 40):
            base = p + (r - p) * t
            best, bv = None, thr
            for o in range(-reach, reach + 1):
                x, y = base + nrm * o
                if 0 <= int(x) < w and 0 <= int(y) < h and grad[int(y), int(x)] > bv:
                    best, bv = (x, y), grad[int(y), int(x)]
            if best is not None:
                pts.append(best)
        if len(pts) >= 12:
            arr = np.float32(pts)
            for _ in range(2):                                 # fit, drop the worst fifth, fit again
                vx, vy, x0, y0 = cv2.fitLine(arr, cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
                dist = np.abs((arr[:, 0] - x0) * vy - (arr[:, 1] - y0) * vx)
                arr = arr[dist <= np.percentile(dist, 80)] if len(arr) > 12 else arr
            ang = abs(float(vx * d[0] + vy * d[1]))
            if ang > 0.97:                                     # still along the side (within ~14 degrees)
                lines.append((np.array([x0, y0]), np.array([vx, vy])))
                continue
        lines.append((p.astype(np.float64), d.astype(np.float64)))
    out = []
    for i in range(4):
        (p1, d1), (p2, d2) = lines[i - 1], lines[i]
        A = np.array([d1, -d2]).T
        if abs(np.linalg.det(A)) < 1e-6:
            return q
        t = np.linalg.solve(A, p2 - p1)
        out.append(p1 + d1 * t[0])
    out = np.float32(out)
    if np.abs(out - q).max() > 3 * reach:                      # a corner ran away: keep the rough quad
        return q
    return out


def face_centres(img) -> list[tuple[float, float]]:
    """Centres of the faces the app's own detector finds in the frame, at any of the four rotations, in
    the frame's coordinates. An identity card is the rectangle around a face photo."""
    try:
        from app.face import service as F
    except Exception:  # noqa: BLE001 - no detector: the cue is simply not used
        return []
    out = []
    try:
        with F._lock:
            for k in range(4):
                rot = np.ascontiguousarray(np.rot90(img, k))
                for row in F._detect(rot):
                    x, y = float(row[0] + row[2] / 2), float(row[1] + row[3] / 2)   # in the rotated frame
                    ch = rot.shape[0]
                    for _ in range(k):                                     # undo one quarter-turn at a time
                        x, y = (ch - 1) - y, x
                        ch = rot.shape[1] if ch == rot.shape[0] else rot.shape[0]
                    out.append((x, y))
    except Exception:  # noqa: BLE001 - detector models missing or failing: no cue
        return []
    return out


def find_card(img_bgr, current=None):
    """(quad TL-TR-BR-BL in the image's own pixels, score, source), or None when nothing in the frame
    looks enough like a card. `current` is the contour search's own quad (one more candidate)."""
    import cv2
    h0, w0 = img_bgr.shape[:2]
    s = min(1.0, WORK / max(h0, w0))
    img = cv2.resize(img_bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else img_bgr
    chans = _channels(img)
    grad = np.zeros(img.shape[:2], np.float32)
    for ch in chans.values():                    # an edge in any channel, each on its own scale
        c = cv2.GaussianBlur(ch, (3, 3), 0)
        g = cv2.magnitude(cv2.Sobel(c, cv2.CV_32F, 1, 0), cv2.Sobel(c, cv2.CV_32F, 0, 1))
        grad = np.maximum(grad, g / max(float(np.percentile(g, 95)), 1.0))
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    cands = candidates(img, chans)
    if current is not None:
        cands.append(("current", order(np.asarray(current, np.float32) * s)))
    faces = face_centres(img)
    best, seen = None, set()
    for name, q in cands:
        key = tuple(np.round(q.ravel() / 12).astype(int))      # the same outline proposed twice is judged once
        if key in seen:
            continue
        seen.add(key)
        # a region gives a ROUGH outline; it is judged after its sides have been pulled onto the edges
        for nm, qq in ((name, q), (name + "+r", refine(grad, q))):
            sc, _parts = score(img, grad, qq, faces, lab)
            if sc > (best[1] if best else 0.0):
                best = (qq, sc, nm)
    if best is None or best[1] < CONFIDENT:
        return None
    return np.clip(best[0], [0, 0], [img.shape[1] - 1, img.shape[0] - 1]) / s, float(best[1]), best[2]
