"""Stage-1 signal: copy-move forgery detection (classical keypoints, no ML).

Catches the forgery class that ELA and CNNs are structurally weak against:
a region copied from the SAME image and pasted elsewhere (e.g. duplicating a
transaction row, cloning a stamp, repeating a digit). Because the pasted pixels
share the source image's compression history, ELA sees nothing unusual — but
keypoint matching finds near-identical patches at two locations.

Method: ORB keypoints → brute-force match against themselves → Lowe ratio
test → drop near-self matches → cluster surviving matches by displacement
vector. Many matches agreeing on one displacement = a copied region.
Also works on rasterized PDF pages (duplication survives rendering).
"""
import re

import cv2
import numpy as np
from PIL import Image

from app.tampering.config import CFG
from app.tampering.signals.base import SignalResult, unavailable

_C = CFG.signals.copy_move


# On these document types, content legitimately repeats: a person shares their
# surname with their father (Name + Father's Name both end in the same word),
# and cards carry repeated emblems/headers/security patterns. A small
# pixel-identical duplication is almost always one of those — NOT forgery — so
# copy_move requires a much larger duplicated region here to flag anything.
# (Geometry cannot tell a shared surname from a copied word; document type can.)
_ID_DOC_TYPES = {
    "pan", "pan_card", "aadhaar", "aadhaar_card", "aadhar", "voter_id",
    "voter_id_card", "passport", "driving_license", "dl", "ration_card",
}
# For ID docs, a duplication must cover at least this fraction of the page to
# count as evidence (a swapped photo is ~4%+; a shared surname is ~0.3%).
_ID_MIN_AREA_FRAC = 0.015


_ID_NUMBER_FIELD_KEYS = ("pan_number", "pan", "aadhaar_number", "aadhaar", "aadhar")


def _id_number_values(ocr_fields: dict | None) -> list[str]:
    if not ocr_fields:
        return []
    out = []
    for key in _ID_NUMBER_FIELD_KEYS:
        v = ocr_fields.get(key)
        if isinstance(v, dict):
            v = v.get("value")
        if v:
            alnum = re.sub(r"[^A-Z0-9]", "", str(v).upper())
            if len(alnum) >= 6:
                out.append(alnum)
    return out


def _duplicated_id_number(image: Image.Image, box, ocr_fields: dict | None) -> bool:
    """Does the verified small duplicate sit on top of the document's own
    unique ID number (PAN/Aadhaar)? A shared surname between Name and
    Father's Name is genuinely ambiguous at the pixel level (see the
    size_ok/_ID_MIN_AREA_FRAC carve-out below) — but the SAME unique
    identifier printed twice on one card has no such innocent explanation,
    so this overrides that carve-out specifically for the ID-number field.
    Only runs OCR when there is already a verified pixel-identical small
    match (the rare path), so the common clean case pays no extra cost."""
    if box is None:
        return False
    id_values = _id_number_values(ocr_fields)
    if not id_values:
        return False
    x1, y1, x2, y2, sx1, sy1, sx2, sy2 = box
    try:
        from app.imaging import ocr_tokens as _ocr_tokens
        tokens = _ocr_tokens(image.convert("RGB"))
    except Exception:  # noqa: BLE001 - best-effort refinement, never fatal
        return False

    def hits(bx0, by0, bx1, by1):
        for t in tokens:
            tx0, ty0, tx1, ty1 = t["box"]
            if tx1 < bx0 or tx0 > bx1 or ty1 < by0 or ty0 > by1:
                continue
            ta = t["alnum"]
            if len(ta) >= 4 and any(ta in v or v in ta for v in id_values):
                return True
        return False

    return hits(x1, y1, x2, y2) or hits(sx1, sy1, sx2, sy2)


def run(image: Image.Image, doc_type: str = "generic",
       ocr_fields: dict | None = None) -> SignalResult:
    gray = np.array(image.convert("L"))
    if min(gray.shape) < 64:
        return unavailable("copy_move", "image too small for keypoint analysis")
    is_id_doc = doc_type in _ID_DOC_TYPES
    img_area = float(gray.shape[0] * gray.shape[1])

    orb = cv2.ORB_create(nfeatures=_C.orb_features)
    keypoints, descriptors = orb.detectAndCompute(gray, None)
    if descriptors is None or len(keypoints) < 20:
        return unavailable("copy_move", "not enough texture for keypoints")

    # Match descriptors against themselves; k=3 so each keypoint can skip its
    # own trivial self-match and still apply the ratio test to two neighbours.
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    matches = matcher.knnMatch(descriptors, descriptors, k=3)

    candidate_pairs = []
    for group in matches:
        # group[0] is the keypoint matched to itself (distance 0) — skip it.
        others = [m for m in group if m.trainIdx != m.queryIdx]
        if len(others) < 2:
            if len(others) == 1:
                others.append(None)
            else:
                continue
        best, second = others[0], others[1]
        if second is not None and best.distance >= _C.match_ratio * second.distance:
            continue  # not distinctive enough (Lowe ratio test)
        p1 = np.array(keypoints[best.queryIdx].pt)
        p2 = np.array(keypoints[best.trainIdx].pt)
        if np.linalg.norm(p1 - p2) < _C.min_displacement_px:
            continue  # same neighbourhood, not a copy elsewhere
        candidate_pairs.append((p1, p2))

    if not candidate_pairs:
        return SignalResult("copy_move", True, 0.0, ["No duplicated regions found"])

    # Cluster by displacement vector (quantized). A real copy-move produces many
    # pairs with the SAME displacement; random texture matches scatter.
    bins = {}
    for p1, p2 in candidate_pairs:
        d = p2 - p1
        key = (round(d[0] / 8), round(d[1] / 8))
        # fold both directions into one canonical key, and ORIENT the pair to
        # match it — so every p1 in a cluster lies on the same (source) side.
        # Mixing sides would double the source bbox and wreck the density test.
        canon = min(key, (-key[0], -key[1]))
        if canon != key:
            p1, p2 = p2, p1
        bins.setdefault(canon, []).append((p1, p2))

    # Merge each bin with its 3x3 neighbours: keypoint localization jitter
    # (JPEG noise, subpixel rounding) spreads one physical displacement across
    # adjacent bins, which would fragment a real cluster below threshold.
    def merged_pairs(center):
        out = []
        for ddx in (-1, 0, 1):
            for ddy in (-1, 0, 1):
                out.extend(bins.get((center[0] + ddx, center[1] + ddy), []))
        return out

    # Evaluate EVERY displacement cluster big enough to matter, and keep the
    # largest one that also passes a spatial-density check. Two failure modes
    # this avoids: (a) repeated document text (same word at the same column on
    # many rows) forms large but page-scattered clusters — high count, low
    # density, not a forgery; (b) naively taking only the biggest cluster lets
    # such a text cluster mask a real (smaller but dense) copied region.
    def cluster_stats(pairs):
        src_pts = np.array([p1 for p1, _ in pairs])
        bw = max(float(src_pts[:, 0].max() - src_pts[:, 0].min()), 32.0)
        bh = max(float(src_pts[:, 1].max() - src_pts[:, 1].min()), 32.0)
        return len(pairs) / (bw * bh)

    MIN_DENSITY = 4e-4
    candidates = [(key, merged_pairs(key)) for key in bins
                  if len(merged_pairs(key)) >= _C.min_cluster_matches]
    dense = [(key, pairs) for key, pairs in candidates
             if cluster_stats(pairs) >= MIN_DENSITY]

    if not dense:
        if candidates:
            n_big = max(len(p) for _, p in candidates)
            return SignalResult(
                "copy_move", True, 0.0,
                [f"{n_big} same-displacement matches but scattered across the page "
                 "— repeated text/layout, not a copied region"])
        n_best = max((len(p) for p in bins.values()), default=0)
        return SignalResult("copy_move", True, 0.0,
                            [f"Only {n_best} weakly-consistent matches — below evidence threshold"])

    # PIXEL VERIFICATION — the step that separates a real copy-move from
    # repeated document text once and for all. Keypoint clusters only say
    # "similar features, consistent shift"; measured on clean statements the
    # same-font repeated tokens can reach cluster sizes that overlap real
    # forgeries. But a real copy-paste is PIXEL-IDENTICAL (bar recompression):
    # aligning the source region with its displaced twin and correlating
    # (NCC) reads ~0.95+ for duplication and ~0.5-0.9 for lookalike text.
    #
    # Critical detail (found via deep testing — see logs.md): the region must
    # be built from INLIER points only, not every point in the (3x3-merged)
    # bin. A handful of stray matches from a neighboring quantization bin can
    # sit far outside the true duplicated patch; bounding-boxing ALL of them
    # inflates a true 148x49px patch into e.g. 439x98px, diluting NCC from
    # ~1.0 to ~0.30 even though the duplication is perfect. Filtering to
    # points within a tight tolerance of the median displacement before
    # building the box fixed every case measured (0.30/0.56 -> 0.9999+).
    # Floor out only DEGENERATE matches (a handful of points in a near-zero
    # area — noise, not evidence). Measured against 20 real forgeries and one
    # known false positive: a single repeated short label/token (e.g. "CHQ
    # DEP" appearing on two rows of a bank statement) CAN legitimately be
    # pixel-identical at a size that overlaps real forged-region dimensions
    # (measured false positive: 187x28px; real forgeries measured 16-253px
    # wide, 16-60px tall) — geometry alone cannot separate the two cases.
    # That ambiguity is real, not a bug: it's why a verified match's fusion
    # confidence is 0.50, not 1.0 (config: single_signal_confidence.copy_move)
    # — capping it at MEDIUM tier for human review rather than auto-flagging
    # HIGH. Telling "forger copied a row" from "the same label repeats" needs
    # semantic content awareness (does the matched text look like a
    # categorical label vs. a value that should never repeat) which is
    # business_rules' job, not this pixel-only signal's.
    MIN_VERIFIED_W, MIN_VERIFIED_H = 20, 14

    def size_ok(pw, ph):
        # ID docs: judge by AREA FRACTION (a shared surname is a tiny fraction
        # of the card); other docs: the absolute small-token floor.
        if is_id_doc:
            return (pw * ph) / img_area >= _ID_MIN_AREA_FRAC
        return pw >= MIN_VERIFIED_W and ph >= MIN_VERIFIED_H

    def verify(pairs):
        disps = np.array([p2 - p1 for p1, p2 in pairs])
        disp = np.median(disps, axis=0)
        deviation = np.linalg.norm(disps - disp, axis=1)
        inliers = deviation <= 4.0
        if inliers.sum() < 2:
            return 0.0, 0, 0, None
        src_pts = np.array([p1 for (p1, _), keep in zip(pairs, inliers) if keep])
        disp = np.median(disps[inliers], axis=0)

        x1 = max(int(src_pts[:, 0].min()) - 8, 0)
        y1 = max(int(src_pts[:, 1].min()) - 8, 0)
        x2 = min(int(src_pts[:, 0].max()) + 8, gray.shape[1])
        y2 = min(int(src_pts[:, 1].max()) + 8, gray.shape[0])
        pw, ph = x2 - x1, y2 - y1
        sx1, sy1 = x1 + round(disp[0]), y1 + round(disp[1])
        sx2, sy2 = sx1 + pw, sy1 + ph
        if not (0 <= sx1 and sx2 <= gray.shape[1] and 0 <= sy1 and sy2 <= gray.shape[0]
                and pw >= 16 and ph >= 16):
            return 0.0, pw, ph, None
        region_a = gray[y1:y2, x1:x2].astype(np.float32)
        region_b = gray[sy1:sy2, sx1:sx2].astype(np.float32)
        if region_a.std() <= 1.0 or region_b.std() <= 1.0:
            return 0.0, pw, ph, None
        ncc_val = float(cv2.matchTemplate(region_a, region_b, cv2.TM_CCOEFF_NORMED)[0, 0])
        return ncc_val, pw, ph, (x1, y1, x2, y2, sx1, sy1, sx2, sy2)

    # Verify every dense candidate, largest first, and prefer the first bin
    # that verifies AND is large enough — a big repeated-text cluster must
    # not mask a smaller but pixel-identical real duplication in another bin.
    best_key, best_pairs, ncc, best_w, best_h, best_box = None, None, 0.0, 0, 0, None
    for key, pairs in sorted(dense, key=lambda kv: -len(kv[1])):
        this_ncc, pw, ph, box = verify(pairs)
        if best_key is None:            # largest bin = fallback if none verify
            best_key, best_pairs, ncc, best_w, best_h, best_box = key, pairs, this_ncc, pw, ph, box
        if this_ncc >= 0.92 and size_ok(pw, ph):
            best_key, best_pairs, ncc, best_w, best_h, best_box = key, pairs, this_ncc, pw, ph, box
            break

    n = len(best_pairs)
    dx, dy = best_key[0] * 8, best_key[1] * 8
    big_enough = size_ok(best_w, best_h)

    if ncc >= 0.92 and big_enough:
        # verified pixel-identical duplication — near-proof
        score = max(min(1.0, n / _C.saturation_matches), ncc)
        findings = [
            f"VERIFIED duplication: {n} keypoint pairs at one displacement "
            f"(~{dx:+d}, {dy:+d}) px and the two regions are pixel-identical "
            f"(correlation {ncc:.2f}) — content was copied within this document"
        ]
    elif ncc >= 0.92 and is_id_doc and _duplicated_id_number(image, best_box, ocr_fields):
        # Too small to pass the shared-surname area floor, but the duplicated
        # pixels sit on the document's own unique ID number, not a name field.
        # A shared surname has an innocent explanation; the same PAN/Aadhaar
        # number appearing twice on one card does not — override the carve-out.
        score = max(min(1.0, n / _C.saturation_matches), ncc)
        findings = [
            f"VERIFIED duplication of the document's own ID number: {n} keypoint "
            f"pairs at displacement (~{dx:+d}, {dy:+d}) px are pixel-identical "
            f"(correlation {ncc:.2f}) — the unique identifier appears twice, which "
            "(unlike a shared surname) has no legitimate explanation"
        ]
    elif ncc >= 0.92:
        # pixel-identical but too small to be forgery evidence — on an ID card
        # this is almost certainly a shared surname (Name + Father's Name) or a
        # repeated emblem/header; elsewhere, a repeated short label/token
        score = min(0.15, n / _C.saturation_matches)
        reason = ("a repeated name or design element (normal on ID cards, e.g. a "
                  "shared surname), not a copied region"
                  if is_id_doc else
                  f"only {best_w}x{best_h}px — too small to be more than a repeated short label/token")
        findings = [
            f"{n} keypoint pairs at displacement (~{dx:+d}, {dy:+d}) px are pixel-identical "
            f"(correlation {ncc:.2f}) but the matched region is {reason}"
        ]
    else:
        # dense cluster but the regions differ at pixel level — lookalike
        # text/layout, keep only weak residual suspicion
        score = min(0.15, n / _C.saturation_matches)
        findings = [
            f"{n} keypoint pairs share a displacement (~{dx:+d}, {dy:+d}) px but the "
            f"regions are not pixel-identical (correlation {ncc:.2f}) — "
            "likely repeated text/layout rather than a copied region"
        ]

    # Visual evidence: lines connecting each matched pair.
    overlay = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    for p1, p2 in best_pairs:
        pt1, pt2 = tuple(map(int, p1)), tuple(map(int, p2))
        cv2.line(overlay, pt1, pt2, (0, 0, 255), 1, cv2.LINE_AA)
        cv2.circle(overlay, pt1, 4, (0, 255, 0), 1, cv2.LINE_AA)
        cv2.circle(overlay, pt2, 4, (255, 0, 0), 1, cv2.LINE_AA)
    artifact = Image.fromarray(cv2.cvtColor(overlay, cv2.COLOR_BGR2RGB))

    return SignalResult("copy_move", True, score, findings, {"match_overlay": artifact})
