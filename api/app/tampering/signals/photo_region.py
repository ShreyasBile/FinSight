"""Stage-2 signal: photo-region consistency (ID documents only).

Purpose-built for the failure mode the other visual signals structurally
miss: a photo pasted in from an external source. ELA and noise both operate
on the WHOLE page — ela.py's block scan can dilute a small, concentrated
anomaly against everything else on the card, and noise.py deliberately
EXCLUDES textured regions like faces from its "flat background" analysis (to
avoid flagging every genuine ID photo, whose natural noise level always
differs from the printed background around it — see noise.py's own
docstring). Neither is a targeted check of "does the photo itself look
consistent with the rest of THIS card."

Method:
  1. Locate the photo using the same face detector as the face-matching
     module (YuNet, tried at all 4 rotations — reused directly, not
     duplicated). An ID document with no detectable face (bank statement,
     salary slip — anything without a photo) makes this signal unavailable,
     not a false "clean."
  2. Expand the detected face box modestly to approximate the printed photo
     box (YuNet returns the FACE; an ID photo usually includes some
     shoulders/background around it).
  3. Compute the same ELA map ela.py already computes, but instead of
     scanning the whole page for ANY anomalous block, compare the mean ELA
     intensity INSIDE the photo box against the rest of the page — a
     direct, two-population comparison rather than a diluted whole-page scan.
  4. Do the same for a noise-floor estimate (Laplacian residual), but
     WITHOUT noise.py's flat-region exclusion — here the point IS to
     characterize the photo's own texture-inclusive noise floor and compare
     it to the card's printed background specifically.

Neither comparison needs EXIF/XMP metadata to work — both read the pixels
themselves, so this still fires when metadata has been stripped (WhatsApp,
a screenshot, a metadata-scrubbing tool), unlike the metadata signal.

Face-detection fallback: the face-matching module's detector (_MIN_DET_SCORE
0.6) is tuned to avoid FALSE MATCHES when asserting two photos show the same
person — a reasonable bar for identity assertions, but it means a genuine,
real ID photo that's small/blurry/rotated/at an angle can score just under
0.6 and never reach this signal at all (silently `unavailable`, not a false
"clean" — but a real coverage gap). Localizing WHERE the photo box is has a
much lower cost of a false positive than asserting identity, so when the
0.6-threshold detector finds nothing, a second pass at a permissive threshold
runs before giving up. It never asserts identity, only "there is probably a
photo around here" for the ELA/noise comparison below.
"""
import cv2
import numpy as np
from PIL import Image

from app.tampering.config import CFG
from app.tampering.signals.base import SignalResult, unavailable
from app.tampering.signals.ela import compute_ela_image

_C = CFG.signals.photo_region

_FALLBACK_MIN_SCORE = 0.3
_fallback_cache = {"det": None}


def _fallback_detector():
    """A second YuNet instance at a lower confidence floor, used only for
    localization (see module docstring) — kept separate from face/service's
    cached detector so its identity-matching threshold is never affected.
    Caller must hold face_service._lock (OpenCV detectors are not thread-safe)."""
    from app.face import service as face_service
    if _fallback_cache["det"] is None:
        _fallback_cache["det"] = cv2.FaceDetectorYN.create(
            face_service.DET_MODEL, "", (320, 320), _FALLBACK_MIN_SCORE, 0.3, 5000)
    return _fallback_cache["det"]


def _detect_permissive(bgr):
    from app.face import service as face_service
    h, w = bgr.shape[:2]
    scale = min(1.0, face_service.DETECT_MAX_DIM / max(h, w))
    small = (cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
             if scale < 1.0 else bgr)
    with face_service._lock:
        det = _fallback_detector()
        det.setInputSize((small.shape[1], small.shape[0]))
        _, faces = det.detect(small)
    if faces is None:
        return []
    out = []
    for row in faces:
        row = row.copy()
        row[:14] /= scale
        out.append(row)
    return out


def _find_photo_box(pil_image: Image.Image):
    """Reuse the face-matching module's detector (best rotation, same
    thresholds already tuned for small/washed-out ID photos); fall back to a
    permissive localization-only pass if it finds nothing. Returns
    (rotated_bgr_image, (x0, y0, x1, y1)) or (None, None) if no face found."""
    from app.face import service as face_service

    rgb = np.array(pil_image.convert("RGB"))
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    k, rows, score = face_service.detect_best_rotation(bgr)
    if not rows or score <= 0:
        best_k, best_rows, best_score = 0, [], 0.0
        for kk in range(4):
            rr = _detect_permissive(face_service.rotate(bgr, kk))
            top = max((float(r[14]) for r in rr), default=0.0)
            if top > best_score:
                best_k, best_rows, best_score = kk, rr, top
        k, rows, score = best_k, best_rows, best_score
    if not rows or score <= 0:
        return None, None
    rotated = face_service.rotate(bgr, k)
    best = max(rows, key=lambda r: float(r[14]))
    x, y, w, h = [float(v) for v in best[:4]]
    # Expand modestly: YuNet returns the FACE, the printed photo box is bigger.
    mx, my = w * 0.6, h * 0.9
    H, W = rotated.shape[:2]
    x0, y0 = max(0, int(x - mx)), max(0, int(y - my))
    x1, y1 = min(W, int(x + w + mx)), min(H, int(y + h + my))
    return rotated, (x0, y0, x1, y1)


def run(image: Image.Image, doc_type: str = "generic") -> SignalResult:
    if doc_type not in _C.applicable_doc_types:
        return unavailable("photo_region", f"doc_type='{doc_type}' has no ID photo to check")

    rotated_bgr, box = _find_photo_box(image)
    if rotated_bgr is None:
        return unavailable("photo_region", "no detectable photo/face on this document")
    x0, y0, x1, y1 = box
    H, W = rotated_bgr.shape[:2]
    if (x1 - x0) < 24 or (y1 - y0) < 24:
        return unavailable("photo_region", "detected photo region too small to analyze")

    rotated_pil = Image.fromarray(cv2.cvtColor(rotated_bgr, cv2.COLOR_BGR2RGB))
    photo_mask = np.zeros((H, W), dtype=bool)
    photo_mask[y0:y1, x0:x1] = True

    # Compare against a RING immediately surrounding the photo, not "the rest
    # of the page" — a busier card (QR code, emblems, multiple text blocks all
    # contributing their own baseline noise) dilutes a whole-page comparison
    # exactly the way ela.py's own page-wide scan does; this stays localized
    # to "does the photo match its own immediate surroundings," which is
    # comparable regardless of how much unrelated content sits elsewhere on
    # the card.
    bw, bh = x1 - x0, y1 - y0
    rx0, ry0 = max(0, x0 - bw // 2), max(0, y0 - bh // 2)
    rx1, ry1 = min(W, x1 + bw // 2), min(H, y1 + bh // 2)
    ring_mask = np.zeros((H, W), dtype=bool)
    ring_mask[ry0:ry1, rx0:rx1] = True
    ring_mask &= ~photo_mask

    # ---- ELA: photo box vs. its immediate surrounding ring ----
    ela_img, max_diff = compute_ela_image(rotated_pil, return_max_diff=True)
    ela_gray = np.asarray(ela_img.convert("L"), dtype=np.float32)
    ela_inside = float(ela_gray[photo_mask].mean())
    ela_ring_vals = ela_gray[ring_mask]
    if ela_ring_vals.size < 50:  # ring too thin (photo fills most of the card) — fall back to whole page
        ela_ring_vals = ela_gray[~photo_mask]
    ela_ring_med = float(np.median(ela_ring_vals))
    ela_ring_mad = float(np.median(np.abs(ela_ring_vals - ela_ring_med)))
    ela_scale = max(1.4826 * ela_ring_mad, 0.15 * ela_ring_med, 0.5)
    ela_z = (ela_inside - ela_ring_med) / ela_scale

    # ---- Noise: photo box vs. its immediate surrounding ring's background floor ----
    gray = cv2.cvtColor(rotated_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    residual = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)
    noise_inside = float(np.median(np.abs(residual[photo_mask])))
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    flat_mask = cv2.magnitude(gx, gy) < 30.0
    ring_flat = flat_mask & ring_mask
    if ring_flat.sum() < 100:
        ring_flat = flat_mask & ~photo_mask  # fall back to whole page
    if ring_flat.sum() < 50:
        noise_z = 0.0
    else:
        bg_vals = np.abs(residual[ring_flat])
        bg_med = float(np.median(bg_vals))
        bg_mad = float(np.median(np.abs(bg_vals - bg_med)))
        noise_scale = max(1.4826 * bg_mad, 0.1 * bg_med, 1.0)
        noise_z = (noise_inside - bg_med) / noise_scale

    # Ramp each z-score into [0,1], same shape as ela.py/noise.py's own
    # scoring; take the stronger of the two independent readings.
    ela_score = float(np.clip((ela_z - 2.0) / 6.0, 0.0, 1.0)) if max_diff >= 8 else 0.0
    noise_score = float(np.clip((abs(noise_z) - 2.0) / 6.0, 0.0, 1.0))
    score = max(ela_score, noise_score)

    findings = [
        f"Photo region ELA level {ela_z:+.1f} sigma vs. its immediate surroundings "
        f"(photo={ela_inside:.1f}, surrounding card={ela_ring_med:.1f})",
        f"Photo region noise level {noise_z:+.1f} sigma vs. its immediate surroundings",
    ]
    if score > 0.4:
        findings.append(
            "The printed photo's compression/noise signature is inconsistent with the rest "
            "of the document — consistent with a photo pasted in from a different source")

    # Visual evidence: the compared region, boxed on the ELA map itself, so a
    # reviewer can see exactly what was measured against what.
    overlay = cv2.cvtColor(ela_gray.astype(np.uint8), cv2.COLOR_GRAY2BGR)
    cv2.rectangle(overlay, (x0, y0), (x1, y1), (0, 0, 255), 3)
    artifact = Image.fromarray(cv2.cvtColor(overlay, cv2.COLOR_BGR2RGB))

    return SignalResult("photo_region", True, score, findings, {"photo_region_map": artifact})
