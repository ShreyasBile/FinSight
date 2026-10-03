"""Image preprocessing pipeline for the FinSight OCR service.

Ported from the standalone `test_ocr` tuning tool. Cleans phone-photo scans
before the vision model sees them, which materially improves extraction on
rotated / skewed / dark / noisy uploads:

    Stage 0    EXIF orientation fix
    Stage 0.5  optional background removal (rembg / U2Net)
    Stage 1    resolution normalisation (floor 1000px, cap 4000px)
    Stage 2    document warp (contour detection) -> morphological deskew fallback
               — runs BEFORE orientation now: flattening the card is what most
               improves extraction accuracy AND turns the next step's 12-angle
               OCR sweep into a cheap 2-angle check on the isolated card
    Stage 1.5  orientation: rotate the flattened card upright (angle_cls-OFF OCR
               so upside-down scores measurably worse; typically just 0 vs 180)
    Stage 3    adaptive IQA (denoise -> brightness -> contrast -> sharpen)
    Stage 4    quality gate: revert to original if OCR char-count drops > 30%

Heavy deps (opencv-python-headless, numpy, rapidocr-onnxruntime) are imported
lazily so this module imports cleanly on a minimal install. Call sites gate
on `settings.ocr_preprocess`; when it is off, none of the heavy code runs.

The pipeline is CPU-bound and blocking (ONNXRuntime), so callers must run it
in a threadpool rather than on the event loop.
"""

import io
import logging

from PIL import Image, ImageOps

log = logging.getLogger("finsight.preprocess")

# Per-doc-type OCR languages used only by the auto-orient scoring pass.
# Keys are the backend doc_type ids (see doc_types.py). Aadhaar and land
# records commonly carry Devanagari, so Hindi is added there.
DOC_LANGS: dict[str, tuple] = {
    "aadhaar": ("en", "hi"),
    "land_record": ("en", "hi"),
}


# Full-page documents (A4 statements, payslips, returns). Their photos fill the
# frame, so the page edge is often not a visible contour and the card-oriented
# warp latched onto a box formed by TABLE RULES instead — measured on
# phone photos of synthetic statements: 8 of 18 were cropped and skewed so
# badly that no transaction row could be read (raw photo: 98% of rows).
PAGE_DOC_TYPES = {"bank_statement", "salary_slip", "itr", "form16", "gst_return", "cibil",
                  "property_document", "valuation_report", "business_proof"}


def is_page_doc(doc_type: str | None) -> bool:
    return (doc_type or "") in PAGE_DOC_TYPES


def doc_langs(doc_type: str) -> tuple:
    """OCR languages for the auto-orient pass, per doc type (default English)."""
    return DOC_LANGS.get(doc_type, ("en",))


# ── Logging helpers ────────────────────────────────────────────────────────────
# The default sink is the server logger, which on Windows writes to a cp1252
# console that cannot encode the pipeline's ✓/⚠/→ glyphs. Fold them to ASCII so
# a log line never raises UnicodeEncodeError. Callers that pass their own log_fn
# (e.g. collecting lines for a JSON response) get the original Unicode.
_GLYPHS = {
    "✓": "[ok]", "⚠": "[!]", "→": "->", "…": "...", "×": "x",
    "°": "deg", "σ": "s", "γ": "g",
    "─": "-", "═": "=", "—": "-", "–": "-",  # box-drawing / dashes
}


def _ascii(m: str) -> str:
    for k, v in _GLYPHS.items():
        m = m.replace(k, v)
    return m.encode("ascii", "replace").decode("ascii")


def _default_log(m: str) -> None:
    log.info(_ascii(m).strip())


# ── OCR reader cache ───────────────────────────────────────────────────────────
# RapidOCR (PP-OCR mobile detection+recognition via ONNXRuntime) replaces the
# EasyOCR/PyTorch reader this module used to carry. Same job — cheap OCR for
# orientation scoring and the quality gate below, not the actual field
# extraction (that's the vision-LLM in providers/) — but ONNXRuntime's mobile
# models are several times faster per call on CPU than EasyOCR's CRNN+ResNet,
# and this stage makes up to 12 calls per document (see auto_orient below), so
# the per-call cost matters more here than almost anywhere else in the app.
#
# RapidOCR's bundled default recognizer (ch_PP-OCRv3, English+Chinese+digits)
# has no Devanagari in its dictionary. For doc types that need Hindi (see
# DOC_LANGS below), a second recognizer — the Devanagari PP-OCRv3 mobile
# model (app/ocr_models/hindi/) — runs over the SAME detected text boxes, and
# whichever recognizer reports higher confidence per box wins. This is a
# per-box max, not a sum, so it can only match or beat either model alone —
# confirmed on a synthetic mixed English/Devanagari/digit test where the
# Hindi model actually won on every line including the plain English one
# (its dictionary also covers Latin + digits). Cost: one extra recognition
# pass per document, only for the doc types that request "hi".
_ocr_reader_cache: dict = {}

_HINDI_REC_DIR = __file__.rsplit("preprocess.py", 1)[0] + "ocr_models/hindi"


class _RapidOCRReader:
    """Adapts RapidOCR's engine(img) -> (result, elapse) call to the
    .readtext(img, detail=1) -> [(box, text, confidence), ...] shape every
    caller here already expects, so call sites needed no other changes."""

    def __init__(self, engine):
        self._engine = engine

    def readtext(self, img_array, detail: int = 1):
        result, _elapse = self._engine(img_array)
        if not result:
            return []
        return [(box, text, float(score)) for box, text, score in result]


class _BilingualOCRReader:
    """Runs one detection pass, then BOTH recognizers (default + Devanagari)
    over the same crops, keeping whichever is more confident per box. Reuses
    the default engine's own detector/crop/sort methods rather than
    reimplementing them, so it stays correct if RapidOCR's internals change."""

    def __init__(self, engine, hindi_recognizer):
        self._engine = engine
        self._hindi_rec = hindi_recognizer

    def readtext(self, img_array, detail: int = 1):
        dt_boxes, _ = self._engine.text_detector(img_array)
        if dt_boxes is None or len(dt_boxes) < 1:
            return []
        dt_boxes = self._engine.sorted_boxes(dt_boxes)
        crops = self._engine.get_crop_img_list(img_array, dt_boxes)
        default_res, _ = self._engine.text_recognizer(crops)
        hindi_res, _ = self._hindi_rec(crops)
        out = []
        for box, d_r, h_r in zip(dt_boxes, default_res, hindi_res):
            d_text, d_conf = d_r[0], float(d_r[1])
            h_text, h_conf = h_r[0], float(h_r[1])
            text, conf = (h_text, h_conf) if h_conf > d_conf else (d_text, d_conf)
            if conf < self._engine.text_score:
                continue
            out.append((box, text, conf))
        return out


def _load_hindi_recognizer():
    from rapidocr_onnxruntime.ch_ppocr_v3_rec.text_recognize import TextRecognizer
    return TextRecognizer({
        "use_cuda": False,
        "model_path": f"{_HINDI_REC_DIR}/rec.onnx",
        # The HF conversion's own config.json claims input height 32; the
        # traced ONNX graph actually demands 48 (confirmed by the ONNXRuntime
        # shape-mismatch error at 32) — same input height as the default
        # ch_PP-OCRv3 model, so it uses the same rec_img_shape.
        "rec_img_shape": [3, 48, 320],
        "rec_batch_num": 6,
        "keys_path": f"{_HINDI_REC_DIR}/dict.txt",
    })


def _get_ocr_reader(langs: tuple = ("en",)):
    """Singleton OCR reader per language set. Plain RapidOCR for English-only
    callers; the bilingual English+Hindi reader (see above) when "hi" is
    requested — matches DOC_LANGS below (Aadhaar, land records)."""
    from rapidocr_onnxruntime import RapidOCR

    key = tuple(sorted(langs))
    if key not in _ocr_reader_cache:
        engine = RapidOCR()
        if "hi" in key:
            _ocr_reader_cache[key] = _BilingualOCRReader(engine, _load_hindi_recognizer())
        else:
            _ocr_reader_cache[key] = _RapidOCRReader(engine)
    return _ocr_reader_cache[key]


_orient_reader_cache = {"reader": None}


def _get_orient_reader():
    """Reader used ONLY for orientation scoring (auto_orient), with RapidOCR's
    text-angle classifier turned OFF. That classifier silently flips
    upside-down text crops upright before recognition — great for reading, but
    it erases the one signal orientation detection needs: with it ON, an
    inverted card reads almost as well as an upright one (measured 153 vs 174),
    so upright-vs-inverted can't be told apart. With it OFF the inverted card
    reads measurably worse (50 vs 174), giving a decisive vote. English
    recogniser is enough here — orientation only needs enough readable
    Latin/numeric text to score, not full Hindi accuracy (verified: a real
    Aadhaar still orients correctly through its English lines alone)."""
    from rapidocr_onnxruntime import RapidOCR
    if _orient_reader_cache["reader"] is None:
        _orient_reader_cache["reader"] = _RapidOCRReader(RapidOCR(use_angle_cls=False))
    return _orient_reader_cache["reader"]


# ── Image utilities ────────────────────────────────────────────────────────────

def _pil_to_bgr(img_pil):
    import cv2
    import numpy as np

    return cv2.cvtColor(np.array(img_pil.convert("RGB")), cv2.COLOR_RGB2BGR)


def _bgr_to_bytes(img) -> bytes:
    import cv2

    # JPEG, not PNG: measured on a real 2760x1792 photo, lossless PNG output
    # was 8.7MB — sent straight to the Ollama vision model, this alone made
    # the OCR call itself dramatically slower (larger image = far more vision
    # tokens to encode/process), independent of every preprocessing-speed fix.
    # Quality 92 is visually near-lossless for OCR purposes while cutting the
    # file to a fraction of the size.
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return buf.tobytes()


def _bytes_to_bgr(image_bytes: bytes):
    import cv2
    import numpy as np

    arr = np.frombuffer(image_bytes, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _gamma_lut(img, gamma: float):
    import cv2
    import numpy as np

    table = (((np.arange(256) / 255.0) ** gamma) * 255).astype("uint8")
    return cv2.LUT(img, table)


def _order_points(pts):
    import numpy as np

    rect = np.zeros((4, 2), dtype=np.float32)
    s, diff = pts.sum(axis=1), np.diff(pts, axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


# ── Contour warp ───────────────────────────────────────────────────────────────

# Real documents are much longer than wide: ID-1 cards (PAN/Aadhaar) are
# 1.586:1, A4 is 1.414:1, US Letter is 1.294:1. A QR code, photo box, or
# signature panel printed ON the document is close to square (~1:1) but is
# still a clean, high-contrast 4-point shape Canny can lock onto instead of
# the true document boundary — warping THAT stretches an internal patch to
# fill the whole output, corrupting the image (garbled/missing text
# downstream, both in OCR and in any preview built from the result). This
# band rejects near-square and extreme-sliver false positives while still
# accepting every common document shape.
_DOC_ASPECT_MIN, _DOC_ASPECT_MAX = 1.15, 2.3


# Rectangle fallback for ID cards (see the contour loop below). Measured on real phone frames of an ID
# card (MIDV-500 development frames, eval/real/card_dev.py, 2026-10-02): card outline found on 32% ->
# 49% of frames (cluttered background 3/20 -> 16/20), wrong outlines 11% -> 9%. Without the frame-edge
# margin the same fallback picked a wrong outline on 29% (cards partly out of the picture).
RECT_FALLBACK = True
RECT_FILL = 0.90
RECT_MARGIN = 0.01        # the fallback rectangle must clear the frame edge by this share of the short side


def _warp_document_robust(img, log_fn=None, page: bool = False):
    """Flatten the document in the frame, or None when no outline is found.

    A page: the contour search below. An ID card: the contour search proposes one outline and
    card_outline.find_card judges it against outlines proposed from regions (brightness, colour, fine
    print) - the best one is used, and none at all unless it looks enough like a card. Measured on real
    phone frames (development split): card found 49% -> 76%, a wrong outline used 9% -> 0%."""
    if page:
        return _contour_warp(img, log_fn, page=True)
    import cv2
    import numpy as np

    def log(m):
        if log_fn:
            log_fn(m)

    first = _contour_warp(img, None, page=False, quad_only=True)
    try:
        from .card_outline import find_card
        found = find_card(img, current=first)
    except Exception as exc:  # noqa: BLE001 - the card search must never break the cleanup
        log(f"  card search failed ({type(exc).__name__}) — contour result used")
        return _contour_warp(img, log_fn, page=False)
    if found is None:
        log("  No outline in the frame looks enough like a card — left as it is")
        return None
    rect = _order_points(found[0].astype(np.float32))
    tl, tr, br, bl = rect
    ow = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    oh = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    if ow < 100 or oh < 100:
        return None
    dst = np.float32([[0, 0], [ow - 1, 0], [ow - 1, oh - 1], [0, oh - 1]])
    warped = cv2.warpPerspective(img, cv2.getPerspectiveTransform(rect, dst), (ow, oh), flags=cv2.INTER_CUBIC)
    if oh > ow * 1.4:
        warped = cv2.rotate(warped, cv2.ROTATE_90_CLOCKWISE)
    log(f"  ✓ Card outline ({found[2]}, score {found[1]:.2f}) → {warped.shape[1]}×{warped.shape[0]}")
    return warped


def _contour_warp(img, log_fn=None, page: bool = False, quad_only: bool = False):
    """Try contour detection on 4 brightness variants x 3 Canny configs.
    `quad_only`: return the outline's four corners instead of the flattened image.

    page=True (a full-page document): the quad must cover at least half the
    frame — anything smaller on a page photo is a table or a text block, not
    the sheet — and a portrait result stays portrait (the 90° turn below is
    for ID cards, which are landscape)."""
    import cv2
    import numpy as np

    def log(m):
        if log_fn:
            log_fn(m)

    h, w = img.shape[:2]
    gray_eq = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))

    variants = [
        ("original", img),
        ("gamma_0.3", _gamma_lut(img, 0.30)),
        ("hist_eq", cv2.merge([gray_eq] * 3)),
        ("contrast_boost", cv2.convertScaleAbs(img, alpha=2.5, beta=80)),
    ]

    for v_name, det in variants:
        gray = cv2.cvtColor(det, cv2.COLOR_BGR2GRAY)
        for lo, hi in [(30, 120), (10, 50), (60, 200)]:
            blur = cv2.GaussianBlur(gray, (5, 5), 0)
            edged = cv2.dilate(cv2.Canny(blur, lo, hi),
                               np.ones((3, 3), np.uint8), iterations=1)
            cnts, _ = cv2.findContours(edged, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:10]:
                peri = cv2.arcLength(c, True)
                approx = cv2.approxPolyDP(c, 0.02 * peri, True)
                if len(approx) != 4 and RECT_FALLBACK and not page:
                    # A card held in a hand or lying among other things: a finger or an object breaks
                    # the outline, so it is not a clean four-sided contour. If the contour's convex
                    # hull still fills the rectangle around it, that rectangle is the card.
                    hull = cv2.convexHull(c)
                    box = cv2.minAreaRect(hull)
                    bw, bh = box[1]
                    pts = cv2.boxPoints(box)
                    m = RECT_MARGIN * min(h, w)
                    inside = (pts[:, 0].min() >= m and pts[:, 1].min() >= m
                              and pts[:, 0].max() <= w - 1 - m and pts[:, 1].max() <= h - 1 - m)
                    # a rectangle running into the frame edge is a card that is partly out of the
                    # picture (or the background itself): there is nothing whole to flatten
                    if bw * bh > 0 and cv2.contourArea(hull) / (bw * bh) >= RECT_FILL and inside:
                        approx = pts.reshape(4, 1, 2).astype(np.int32)
                if len(approx) != 4 or cv2.contourArea(approx) <= (0.5 if page else 0.15) * h * w:
                    continue
                rect = _order_points(approx.reshape(4, 2).astype(np.float32))
                tl, tr, br, bl = rect
                ow = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
                oh = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
                if ow < 100 or oh < 100:
                    continue
                aspect = max(ow, oh) / max(min(ow, oh), 1)
                if not (_DOC_ASPECT_MIN <= aspect <= _DOC_ASPECT_MAX):
                    log(f"  x Contour ({v_name}, Canny {lo}/{hi}) rejected — "
                        f"{ow}x{oh} (aspect {aspect:.2f}) doesn't look like a document")
                    continue
                if quad_only:
                    return rect
                dst = np.float32([[0, 0], [ow - 1, 0], [ow - 1, oh - 1], [0, oh - 1]])
                M = cv2.getPerspectiveTransform(rect, dst)
                warped = cv2.warpPerspective(img, M, (ow, oh), flags=cv2.INTER_CUBIC)
                if oh > ow * 1.4 and not page:
                    warped = cv2.rotate(warped, cv2.ROTATE_90_CLOCKWISE)
                    log(f"  ✓ Contour ({v_name}, Canny {lo}/{hi}) → warped+rotated "
                        f"{warped.shape[1]}×{warped.shape[0]}")
                else:
                    log(f"  ✓ Contour ({v_name}, Canny {lo}/{hi}) → {ow}×{oh}")
                return warped

    log("  No document contour found across all variants")
    return None


# ── Morphological deskew ───────────────────────────────────────────────────────

def _morph_deskew(img, log_fn=None, min_angle: float = 0.4):
    """Morphological text-line deskew + Hough fallback."""
    import cv2
    import numpy as np

    def log(m):
        if log_fn:
            log_fn(m)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    angles = []

    for thresh in (
        cv2.adaptiveThreshold(cv2.GaussianBlur(gray, (5, 5), 0), 255,
                              cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                              cv2.THRESH_BINARY_INV, 15, 8),
        cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1],
    ):
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (28, 3))
        dilated = cv2.dilate(thresh, kernel, iterations=2)
        cnts, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            if cv2.contourArea(c) < 400:
                continue
            rect = cv2.minAreaRect(c)
            bw, bh = rect[1]
            if bw == 0 or bh == 0:
                continue
            if max(bw, bh) / min(bw, bh) < 2.0:
                continue
            angle = rect[2]
            if angle < -45:
                angle += 90
            if abs(angle) < 45:
                angles.append(angle)

    if len(angles) < 3:
        edges = cv2.Canny(gray, 50, 150, apertureSize=3)
        lines = cv2.HoughLines(edges, 1, np.pi / 180, threshold=80)
        if lines is not None:
            for rho, theta in lines[:, 0]:
                a = np.degrees(theta) - 90
                if abs(a) < 45:
                    angles.append(float(a))

    if not angles:
        log("  Deskew: no reliable angle — skipping")
        return img

    median = float(np.median(angles))
    if abs(median) < min_angle:
        log(f"  Deskew: {median:.2f}° negligible — skipping")
        return img

    log(f"  Deskew: {median:.2f}° from {len(angles)} samples")
    return _rotate_expand(img, median)


# ── Auto-orientation ───────────────────────────────────────────────────────────

def _enhance_for_orient(img):
    """CLAHE + sharpen + conditional brightness so EasyOCR works on degraded thumbnails."""
    import cv2
    import numpy as np

    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4)).apply(l)
    out = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)
    if np.mean(cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)) < 80:  # very dark
        out = _gamma_lut(out, 0.4)
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
    return cv2.filter2D(out, -1, kernel)


def _rotate_expand(img, angle: float):
    """Rotate the full image by `angle` degrees WITHOUT cropping — the output
    canvas grows to fit the rotated content instead of clipping it.

    cv2.warpAffine's naive form keeps the output the same (w, h) as the input,
    which is only correct for a 0deg/180deg rotation. A 90deg (or 270deg)
    rotation swaps width and height, so forcing it back into the original
    (w, h) box necessarily clips real content off one axis while
    BORDER_REPLICATE smears edge pixels into the newly-empty space on the
    other — exactly the missing-edge-text + gradient-smear corruption this
    was causing on real (usually 90deg-rotated) phone photos."""
    import cv2
    import numpy as np

    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(M[0, 0]), abs(M[0, 1])
    new_w = int((h * sin) + (w * cos))
    new_h = int((h * cos) + (w * sin))
    M[0, 2] += (new_w / 2) - (w / 2)
    M[1, 2] += (new_h / 2) - (h / 2)
    return cv2.warpAffine(img, M, (new_w, new_h),
                          flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def auto_orient(img, log_fn=None, langs: tuple = ("en",), candidate_angles=None, info: dict | None = None):
    """Rotate the image so its text is upright, scoring candidate angles by OCR.

    candidate_angles: when the caller already knows the geometry (e.g. the card
    was warped flat to a landscape rectangle, so only upright-vs-inverted
    remains), pass the exact angles to test — e.g. (0, 180) — and the expensive
    coarse-to-fine cardinal+30deg sweep is skipped. When None, the full
    cardinals-first (then 30deg steps if ambiguous) sweep runs.

    `langs` is accepted for call-site compatibility but orientation always uses
    the dedicated angle_cls-OFF reader (see _get_orient_reader) regardless.
    """
    import cv2

    def log(m):
        if log_fn:
            log_fn(m)

    def _rot(src, angle):
        sh, sw = src.shape[:2]
        M = cv2.getRotationMatrix2D((sw / 2, sh / 2), angle, 1.0)
        return cv2.warpAffine(src, M, (sw, sh),
                              flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REPLICATE)

    h, w = img.shape[:2]
    # This used to cap at 450px, sized for EasyOCR's cost curve (900px=3.5s,
    # 700px=2.85s, 450px=~1.8s per call). That budget no longer applies now
    # that RapidOCR does this scoring (see _get_ocr_reader) — measured on a
    # real Aadhaar card, RapidOCR's default detector (limit_type='min',
    # limit_side_len=736) upsamples anything below ~736px on its short side
    # back up internally before running, so a 450px thumbnail was RE-SCALED
    # UP by the detector anyway, at real cost, while still handing the
    # recognizer a blurrier starting image. A 1000px thumbnail measured no
    # slower per call and gave a materially more reliable orientation vote:
    # at 450px the correctly-oriented candidate lost to a wrong one because
    # Aadhaar's own printed security pattern (a rotated "आधार" watermark,
    # guilloche background) is real, competing Devanagari-readable content at
    # the WRONG angle, and 450px was too coarse to let the correct angle's
    # actual name/number text out-read that watermark; 1000px resolved it
    # correctly and by a clearer margin in that test.
    scale = min(1.0, 1000 / max(h, w))
    small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    enh = _enhance_for_orient(small)  # enhanced copy — scoring only

    reader = _get_orient_reader()  # angle_cls OFF — see _get_orient_reader

    def _score_angle(angle):
        rot = _rot(enh, angle)
        rgb = cv2.cvtColor(rot, cv2.COLOR_BGR2RGB)
        results = reader.readtext(rgb, detail=1)
        chars = sum(len(r[1]) for r in results)
        # Score only LANDSCAPE-shaped boxes (width >= height in page
        # coordinates) — not chars*confidence over every box. RapidOCR's
        # detector finds text as an arbitrarily-oriented quad and internally
        # rotates each crop to horizontal before recognition (standard PP-OCR
        # behavior), so it can read a text line reasonably well even when the
        # WHOLE PAGE is rotated 90° wrong — that reading only becomes a TALL,
        # narrow box in page coordinates instead of a wide one. Measured on a
        # real Aadhaar card: at the wrong page rotation, RapidOCR still read
        # the name/number/heading correctly (chars*confidence scored HIGHER
        # than the correct rotation), because every one of those boxes came
        # back portrait-shaped (e.g. 456h x 57w) while at the correct
        # rotation the same lines came back landscape (e.g. 46h x 450w). Box
        # shape in page coordinates is what actually reveals page rotation;
        # content-reading confidence does not, once the recognizer is this
        # rotation-tolerant. Verified this leaves already-correct doc types
        # (PAN) unchanged while fixing Aadhaar.
        landscape_score = 0.0
        landscape_chars = 0
        for box, text, conf in results:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            bw, bh = max(xs) - min(xs), max(ys) - min(ys)
            if bw >= bh:
                landscape_score += len(text) * float(conf)
                landscape_chars += len(text)
        avg_conf = landscape_score / landscape_chars if landscape_chars > 0 else 0.0
        log(f"  {angle:>3}°  chars={chars:>3}  landscape_weighted={landscape_score:5.1f}")
        return {"chars": chars, "score": landscape_score, "avg_conf": avg_conf,
                "text": " ".join(r[1] for r in results)}

    if candidate_angles is not None:
        # Restricted set — the caller already flattened the card, so the search
        # space is tiny (typically just 0 vs 180). Always include 0 as the
        # reference the should_rotate threshold below compares against.
        angles = tuple(dict.fromkeys((0, *candidate_angles)))
        scores = {a: _score_angle(a) for a in angles}
    else:
        # Coarse-to-fine: measured cost is dominated by this stage's OCR calls
        # (12x on a full 12-angle sweep). Real phone photos are overwhelmingly
        # mis-rotated in 90deg multiples (held sideways/upside-down) — fine skew
        # is handled separately by the warp/deskew stage, not this one. So score
        # the 4 cardinals first; only pay for the other 8 (e.g. a WhatsApp
        # re-save with EXIF stripped, genuinely photographed at an odd angle)
        # when the cardinals don't give a clear answer. Cuts the common case ~3x
        # without losing coverage on the ambiguous case this stage exists for.
        cardinals = (0, 90, 180, 270)
        scores = {a: _score_angle(a) for a in cardinals}
        ranked = sorted(cardinals, key=lambda a: scores[a]["score"], reverse=True)
        top, second = scores[ranked[0]]["score"], scores[ranked[1]]["score"]
        confident = top > 0 and (second == 0 or top >= 2 * second)

        if not confident:
            log("  Cardinals ambiguous — checking the remaining 30° steps too")
            for angle in range(30, 360, 30):
                if angle in scores:
                    continue
                scores[angle] = _score_angle(angle)

    best = max(scores, key=lambda a: scores[a]["score"])
    orig_s = scores[0]["score"]
    best_s = scores[best]["score"]

    should_rotate = best != 0 and (orig_s == 0 or best_s > orig_s * 1.10)
    if info is not None:
        # the words read at the chosen angle = the page's own text; the
        # Document Identity check reuses it instead of a second OCR pass
        # (measured 4-5 s per card on CPU)
        info["page_text"] = scores[best if should_rotate else 0]["text"].lower()

    if should_rotate:
        pct = f"+{(best_s / orig_s - 1) * 100:.0f}%" if orig_s > 0 else "0→>0"
        extra = ""
        if scores[best]["chars"] < scores[0]["chars"]:
            extra = (f" (fewer raw chars but avg_conf "
                     f"{scores[best]['avg_conf']:.2f} > {scores[0]['avg_conf']:.2f})")
        log(f"  → Rotating {best}°  weighted {orig_s:.1f}→{best_s:.1f} {pct}{extra}")
        return _rotate_expand(img, best)

    log(f"  → Keeping 0°  (best={best}°  gap={best_s - orig_s:.1f} < 10% threshold)")
    return img


# ── IQA ────────────────────────────────────────────────────────────────────────

def measure_quality(img) -> dict:
    import cv2
    import numpy as np

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    return {
        "brightness": float(np.mean(gray)),
        "contrast": float(np.std(gray)),
        "sharpness": float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        "noise": float(np.std(gray.astype("float32") - blurred.astype("float32"))),
    }


def paper_like(img) -> dict | None:
    """A page of dark ink on light paper that is already well exposed: most of the image is bright
    background and the ink is clearly darker. Returns {"background_noise": ...} or None.

    Why this matters: the repairs below were tuned on photographs, where a mean brightness above 185
    means over-exposure, a low overall contrast means a washed-out image and a high high-frequency
    residual means sensor noise. On a clean SCAN all three are simply what paper looks like — the page
    is mostly white (mean ~240, low overall spread) and its sharp text edges look like "noise". The
    repairs then darkened the page, equalised it and blurred the text: on real scanned receipts and
    forms, 31% read worse after cleanup than before (real-data evaluation, 2026-10-02)."""
    import cv2
    import numpy as np
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    p25, p50 = np.percentile(gray, 25), np.percentile(gray, 50)
    ink = float((gray < 128).mean())
    # bright paper, EVENLY bright over most of the frame (a scan or a flat, well-lit page: a photo on a
    # desk has shadows and a background, so its lower quartile falls well below its median), and some
    # dark ink on it (a receipt can be under 1% ink, so percentiles of the ink side say nothing)
    if not (p50 >= 190 and p50 - p25 <= 12 and ink >= 0.0005):
        return None
    background = gray >= p50 - 25                  # the paper itself, where real noise would show
    residual = gray.astype("float32") - cv2.GaussianBlur(gray, (5, 5), 0).astype("float32")
    return {"background_noise": float(np.std(residual[background])) if background.any() else 0.0}


def adaptive_iqa_correct(img, log_fn=None):
    """Single ordered pass: denoise → brightness → contrast → sharpen."""
    import cv2
    import numpy as np

    def log(m):
        if log_fn:
            log_fn(m)

    m = measure_quality(img)
    log(f"  Before → brightness={m['brightness']:.1f}  contrast={m['contrast']:.1f}  "
        f"sharpness={m['sharpness']:.1f}  noise={m['noise']:.1f}")

    applied = []

    paper = paper_like(img)
    if paper is not None:
        # a well-exposed page: bright paper is not over-exposure and text edges are not noise.
        # Only a genuinely noisy background (a grainy photocopy) is denoised; nothing else is touched.
        if paper["background_noise"] > 8:
            img = cv2.fastNlMeansDenoisingColored(img, None, h=7, hColor=7,
                                                  templateWindowSize=7, searchWindowSize=21)
            applied.append(f"denoise(paper σ={paper['background_noise']:.1f})")
        log(f"  Clean page (dark ink on light paper) — {'fixes: ' + applied[0] if applied else 'no repair needed'}")
        return img

    if m["noise"] > 12:
        img = cv2.fastNlMeansDenoisingColored(img, None, h=10, hColor=10,
                                              templateWindowSize=7, searchWindowSize=21)
        applied.append(f"denoise(σ={m['noise']:.1f})")
        m = measure_quality(img)

    if m["brightness"] > 185:
        gamma = 1.0 + (m["brightness"] - 185) / 70.0
        img = _gamma_lut(img, gamma)
        applied.append(f"highlight(γ={gamma:.2f})")
    elif m["brightness"] < 65:
        gamma = max(0.30, 1.0 - (65 - m["brightness"]) / 65.0)
        img = _gamma_lut(img, gamma)
        applied.append(f"gamma-boost(γ={gamma:.2f})")

    m2 = measure_quality(img)
    if m2["contrast"] < 38:
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(l)
        img = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)
        applied.append(f"CLAHE(std={m2['contrast']:.1f})")

    m3 = measure_quality(img)
    if m3["sharpness"] < 80 and m3["noise"] < 10:
        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
        img = cv2.filter2D(img, -1, kernel)
        applied.append(f"sharpen(lap={m3['sharpness']:.1f})")

    m4 = measure_quality(img)
    log(f"  After  → brightness={m4['brightness']:.1f}  contrast={m4['contrast']:.1f}  "
        f"sharpness={m4['sharpness']:.1f}  noise={m4['noise']:.1f}")
    log(f"  Fixes: {', '.join(applied) if applied else 'none needed'}")
    return img


# ── Document segmentation (background removal) ─────────────────────────────────

def segment_document_bg(image_bytes: bytes, log_fn=None) -> bytes:
    """
    Remove background outside the document using rembg (U2Net ONNX model).
    The non-document area is replaced with white so OCR is not distracted by
    table surfaces, hands, wallets, or other objects in the photo.

    Requires:  pip install rembg onnxruntime
    Model is downloaded automatically on first use (~170 MB, cached in ~/.u2net/).
    """
    def log(m):
        if log_fn:
            log_fn(m)

    try:
        from rembg import remove as rembg_remove
    except ImportError:
        log("  ⚠ rembg not installed — run: pip install rembg onnxruntime")
        return image_bytes

    try:
        import numpy as np

        inp = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        log(f"  Running U2Net segmentation on {inp.width}×{inp.height} image...")
        out = rembg_remove(inp)  # returns RGBA; alpha = document mask

        # How much of the image was masked (0=nothing found, 1=whole image)
        alpha = np.array(out)[:, :, 3]
        coverage = float(alpha.mean()) / 255.0
        log(f"  Mask coverage: {coverage * 100:.1f}%")

        if coverage < 0.05:
            log("  ⚠ Mask too empty — segmentation skipped (keeping original)")
            return image_bytes
        if coverage > 0.97:
            log("  ⚠ Mask covers whole image — no background detected, keeping original")
            return image_bytes

        # Composite the masked image over solid white
        bg = Image.new("RGBA", out.size, (255, 255, 255, 255))
        bg.paste(out, mask=out.split()[3])
        result = bg.convert("RGB")

        buf = io.BytesIO()
        result.save(buf, format="PNG")
        log(f"  ✓ Background removed — {coverage * 100:.1f}% foreground preserved")
        return buf.getvalue()

    except Exception as e:
        log(f"  ⚠ Segmentation error: {e}")
        return image_bytes


# ── Full preprocessing pipeline ────────────────────────────────────────────────

def preprocess_image(image_bytes: bytes, *, segment: bool = False,
                     langs: tuple = ("en",), log_fn=None, page: bool = False,
                     info: dict | None = None) -> bytes:
    """Clean a document image for OCR. Returns PNG bytes (original on any failure).

    Blocking / CPU-bound (EasyOCR, OpenCV) — run this in a threadpool, not on the
    event loop. `segment=True` additionally runs rembg background removal (needs
    the optional rembg + onnxruntime install).
    """
    import cv2

    if log_fn is None:
        log_fn = _default_log

    def log(m):
        log_fn(m)

    log("─── Preprocessing started ───────────────────────────────────")

    # Stage 0 — EXIF + decode
    log("[Stage 0] EXIF orientation fix")
    img_pil = Image.open(io.BytesIO(image_bytes))
    try:
        img_pil = ImageOps.exif_transpose(img_pil)
        log("  EXIF transpose done (no-op if not needed)")
    except Exception as ex:
        log(f"  EXIF skipped: {ex}")
    img = _pil_to_bgr(img_pil)

    # Stage 0.5 — Background segmentation (optional)
    if segment:
        log("[Stage 0.5] Background removal (U2Net / rembg)...")
        image_bytes = segment_document_bg(image_bytes, log_fn=log_fn)
        img = _bytes_to_bgr(image_bytes)
    else:
        log("[Stage 0.5] Background removal skipped")

    # Stage 1 — Resolution
    h, w = img.shape[:2]
    if w > 4000 or h > 4000:
        scale = min(4000 / w, 4000 / h)
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        log(f"[Stage 1] Downscaled {w}×{h} → {img.shape[1]}×{img.shape[0]}")
    elif w < 1000:
        scale = 1000 / w
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        log(f"[Stage 1] Upscaled {w}×{h} → {img.shape[1]}×{img.shape[0]}")
    else:
        log(f"[Stage 1] Resolution {w}×{h} OK")
    stage1 = img            # to tell later whether geometry changed anything

    # Stage 2 (GEOMETRY FIRST) — isolate & flatten the card. This is the single
    # biggest win for VLM extraction accuracy: on a flat, cropped card the model
    # stops bleeding text between adjacent stacked fields (measured on a real
    # sideways PAN, "father's name" leaked into "name" until the card was warped
    # flat). It is also nearly free (~0.02s, pure OpenCV edge detection). Doing
    # it BEFORE orientation is what makes orientation cheap: a warped ID card is
    # a clean landscape rectangle, so orientation collapses from a 12-angle OCR
    # sweep over the whole cluttered photo to a 2-angle (upright vs upside-down)
    # check on the isolated card.
    # A flat, evenly lit page (a scan) has no perspective to correct: "flattening" it only crops and
    # resamples it. (Measured on real scans, 2026-10-02: the outline step fired on 6 of 18 and each
    # time changed nothing but the pixels.)
    scan = paper_like(img) if page else None
    if scan is not None:
        log("[Stage 2] Flat, evenly lit page (a scan) — no outline to flatten")
        warped = None
    else:
        log("[Stage 2] Document contour detection (4 variants × 3 Canny configs)...")
        warped = _warp_document_robust(img, log_fn=log_fn, page=page)
    if warped is not None:
        img = warped
        gh, gw = img.shape[:2]
        # Warp maps the card to a canonical rectangle: landscape ⇒ only upright
        # vs inverted remain (0/180); a rare portrait result ⇒ pay for all four.
        # A warped PAGE is portrait by construction ⇒ also just 0/180.
        orient_angles = (0, 180) if (gw >= gh or page) else (0, 90, 180, 270)
    else:
        log("  Falling back to morphological deskew")
        # on a scan a tilt under ~1° does not hinder reading, and rotating resamples every pixel
        img = _morph_deskew(img, log_fn=log_fn, min_angle=0.8 if scan is not None else 0.4)
        orient_angles = None  # geometry unknown — full cardinals-first sweep

    # Stage 1.5 (ORIENTATION, after warp) — get the flattened card upright.
    log(f"[Stage 1.5] Orientation check (angles={orient_angles or 'full sweep'})...")
    img = auto_orient(img, log_fn=log_fn, langs=langs, candidate_angles=orient_angles, info=info)

    # Do no harm: a page that is already a clean, upright scan (dark ink on light paper, no warp, no
    # deskew, no rotation, no noisy background) has nothing to repair — and every step from here on is
    # slightly lossy for it (resampling, re-encoding). Measured on real scanned receipts and forms
    # (2026-10-02): 31% read worse after cleanup than before, 0% were caught by the Stage-4 guard.
    # Such a page goes to the reading model exactly as it was uploaded.
    if page and img is stage1 and w <= 4000 and h <= 4000:
        if scan is not None and scan["background_noise"] <= 8:
            log("[Stage 3] Clean, upright scan — nothing to repair; original kept")
            log("─── Preprocessing complete ──────────────────────────────────")
            return image_bytes

    # Stage 3 — IQA
    log("[Stage 3] Adaptive IQA (denoise → brightness → contrast → sharpen)")
    img = adaptive_iqa_correct(img, log_fn=log_fn)

    preprocessed_bytes = _bgr_to_bytes(img)

    # Stage 4 — Keep whichever reads better (preprocessed vs original)
    #
    # This only needs a RELATIVE signal (did preprocessing help or hurt?), not
    # full-accuracy OCR — but it was running EasyOCR on the FULL RESOLUTION
    # image (up to 4000px) twice. Measured: 35s of a ~42s preprocessing total
    # on a 2500x3300 photo, i.e. the actual bottleneck users were hitting.
    # Downscaling both images to the same size before comparing keeps the
    # comparison fair (both lose equal detail) while cutting this stage to
    # a couple of seconds — same decision logic, same revert-on-regression
    # safety net, just measured on thumbnails instead of full-res.
    log("[Stage 4] Comparing preprocessed vs original by OCR char count...")
    try:
        import cv2
        import numpy as np

        reader = _get_ocr_reader(("en",))

        # a full page is unreadable at 450px either way (so a bad warp could
        # never lose the comparison); pages are compared at 1000px
        def _small(arr, max_dim=1000 if page else 450):
            h, w = arr.shape[:2]
            if max(h, w) <= max_dim:
                return arr
            scale = max_dim / max(h, w)
            return cv2.resize(arr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

        orig_arr = _small(np.array(Image.open(io.BytesIO(image_bytes)).convert("RGB")))
        prep_arr = _small(np.array(Image.open(io.BytesIO(preprocessed_bytes)).convert("RGB")))
        orig_n = sum(len(r[1]) for r in reader.readtext(orig_arr))
        prep_n = sum(len(r[1]) for r in reader.readtext(prep_arr))
        log(f"  Chars (450px compare) — original: {orig_n}  preprocessed: {prep_n}")
        if prep_n < orig_n * 0.7:
            log("  ⚠ Preprocessed is worse — reverting to original")
            preprocessed_bytes = image_bytes
        else:
            log("  ✓ Preprocessed is better or equal — keeping")
    except Exception as ex:
        log(f"  Comparison skipped: {ex}")

    log("─── Preprocessing complete ──────────────────────────────────")
    return preprocessed_bytes
