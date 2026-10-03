#!/usr/bin/env python3
"""
FinSight OCR — Test tool (no storage)

Edge cases fixed:
  1.  Dark images:       contour detection on 4 brightness-boosted copies
  2.  VLM hallucination: every field cross-validated against raw OCR text
  3.  IQA ordering:      denoise → brightness → contrast → sharpen
  4.  Rotation:          morphological text-line deskew
  5.  EasyOCR speed:     reader cached per language set
  6.  Worse after preprocessing: keep original if preprocessed gives fewer chars

Requirements:
  pip install httpx pillow easyocr opencv-python pypdfium2 docling
  ollama pull qwen2.5vl:7b
"""

import base64, io, json, re, threading, tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

import httpx
from PIL import Image, ImageOps, ImageTk

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL      = "qwen2.5vl:7b"

DOC_LANGUAGES = {
    "aadhaar":        ["en", "hi"],
    "pan":            ["en"],
    "bank_statement": ["en"],
    "salary_slip":    ["en"],
    "itr":            ["en"],
    "gst":            ["en"],
    "cibil":          ["en"],
}

PROMPTS = {
    "aadhaar": """\
Extract all fields from this Aadhaar card image.
Return ONLY valid JSON — no explanation, no markdown.
If you cannot read a field clearly, use null.
{
  "name": "",
  "dob": "DD/MM/YYYY",
  "gender": "",
  "aadhaar_number": "XXXX XXXX XXXX",
  "address": "",
  "pincode": ""
}""",
    "pan": """\
Extract all fields from this PAN card image.
Return ONLY valid JSON — no explanation, no markdown.
If you cannot read a field clearly, use null.
{
  "name": "",
  "father_name": "",
  "dob": "DD/MM/YYYY",
  "pan_number": ""
}""",
    "bank_statement": """\
Extract key financial data from this bank statement.
Return ONLY valid JSON — no explanation, no markdown.
If you cannot read a field clearly, use null.
{
  "account_holder": "",
  "account_number": "",
  "bank_name": "",
  "ifsc": "",
  "period_from": "MM/YYYY",
  "period_to": "MM/YYYY",
  "opening_balance": 0,
  "closing_balance": 0,
  "total_credits": 0,
  "total_debits": 0,
  "average_monthly_balance": 0
}""",
    "salary_slip": """\
Extract all fields from this salary slip.
Return ONLY valid JSON — no explanation, no markdown.
If you cannot read a field clearly, use null.
{
  "employee_name": "",
  "employee_id": "",
  "designation": "",
  "company_name": "",
  "month_year": "",
  "basic_salary": 0,
  "hra": 0,
  "gross_salary": 0,
  "deductions": 0,
  "net_salary": 0,
  "pf_number": ""
}""",
    "itr": """\
Extract key fields from this Income Tax Return document.
Return ONLY valid JSON — no explanation, no markdown.
If you cannot read a field clearly, use null.
{
  "name": "",
  "pan": "",
  "assessment_year": "",
  "gross_total_income": 0,
  "total_income": 0,
  "tax_payable": 0,
  "refund": 0,
  "filing_date": ""
}""",
    "gst": """\
Extract all fields from this GST Return or GST Registration certificate.
Return ONLY valid JSON — no explanation, no markdown.
If you cannot read a field clearly, use null.
{
  "business_name": "",
  "gstin": "",
  "state": "",
  "registration_date": "",
  "total_taxable_value": 0,
  "total_tax_paid": 0,
  "filing_period": ""
}""",
    "cibil": """\
Extract all fields from this CIBIL or credit report.
Return ONLY valid JSON — no explanation, no markdown.
If you cannot read a field clearly, use null.
{
  "name": "",
  "pan": "",
  "cibil_score": 0,
  "report_date": "",
  "total_accounts": 0,
  "active_accounts": 0,
  "overdue_accounts": 0,
  "total_outstanding": 0,
  "enquiries_last_6_months": 0
}""",
}

DOC_LABELS = {
    "aadhaar":        "Aadhaar Card",
    "pan":            "PAN Card",
    "bank_statement": "Bank Statement",
    "salary_slip":    "Salary Slip",
    "itr":            "Income Tax Return (ITR)",
    "gst":            "GST Return / Certificate",
    "cibil":          "CIBIL / Credit Report",
}


# ── Image utilities ────────────────────────────────────────────────────────────

def _pil_to_bgr(img_pil):
    import cv2, numpy as np
    return cv2.cvtColor(np.array(img_pil.convert("RGB")), cv2.COLOR_RGB2BGR)

def _bgr_to_bytes(img) -> bytes:
    import cv2
    _, buf = cv2.imencode(".png", img)
    return buf.tobytes()

def _bytes_to_bgr(image_bytes: bytes):
    import cv2, numpy as np
    arr = np.frombuffer(image_bytes, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)

def _gamma_lut(img, gamma: float):
    import cv2, numpy as np
    table = (((np.arange(256) / 255.0) ** gamma) * 255).astype("uint8")
    return cv2.LUT(img, table)

def _order_points(pts):
    import numpy as np
    rect     = np.zeros((4, 2), dtype=np.float32)
    s, diff  = pts.sum(axis=1), np.diff(pts, axis=1)
    rect[0]  = pts[np.argmin(s)];  rect[2] = pts[np.argmax(s)]
    rect[1]  = pts[np.argmin(diff)]; rect[3] = pts[np.argmax(diff)]
    return rect


# ── Contour warp ───────────────────────────────────────────────────────────────

def _warp_document_robust(img, log_fn=None):
    """Try contour detection on 4 brightness variants × 3 Canny configs."""
    import cv2, numpy as np

    def log(m):
        if log_fn: log_fn(m)

    h, w    = img.shape[:2]
    gray_eq = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))

    variants = [
        ("original",       img),
        ("gamma_0.3",      _gamma_lut(img, 0.30)),
        ("hist_eq",        cv2.merge([gray_eq]*3)),
        ("contrast_boost", cv2.convertScaleAbs(img, alpha=2.5, beta=80)),
    ]

    for v_name, det in variants:
        gray = cv2.cvtColor(det, cv2.COLOR_BGR2GRAY)
        for lo, hi in [(30, 120), (10, 50), (60, 200)]:
            blur  = cv2.GaussianBlur(gray, (5, 5), 0)
            edged = cv2.dilate(cv2.Canny(blur, lo, hi),
                               np.ones((3, 3), np.uint8), iterations=1)
            cnts, _ = cv2.findContours(edged, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:10]:
                peri   = cv2.arcLength(c, True)
                approx = cv2.approxPolyDP(c, 0.02 * peri, True)
                if len(approx) != 4 or cv2.contourArea(approx) <= 0.15 * h * w:
                    continue
                rect           = _order_points(approx.reshape(4, 2).astype(np.float32))
                tl, tr, br, bl = rect
                ow = int(max(np.linalg.norm(br-bl), np.linalg.norm(tr-tl)))
                oh = int(max(np.linalg.norm(tr-br), np.linalg.norm(tl-bl)))
                if ow < 100 or oh < 100:
                    continue
                dst    = np.float32([[0,0],[ow-1,0],[ow-1,oh-1],[0,oh-1]])
                M      = cv2.getPerspectiveTransform(rect, dst)
                warped = cv2.warpPerspective(img, M, (ow, oh), flags=cv2.INTER_CUBIC)
                if oh > ow * 1.4:
                    warped = cv2.rotate(warped, cv2.ROTATE_90_CLOCKWISE)
                    log(f"  ✓ Contour ({v_name}, Canny {lo}/{hi}) → warped+rotated {warped.shape[1]}×{warped.shape[0]}")
                else:
                    log(f"  ✓ Contour ({v_name}, Canny {lo}/{hi}) → {ow}×{oh}")
                return warped

    log("  No document contour found across all variants")
    return None


# ── Morphological deskew ───────────────────────────────────────────────────────

def _morph_deskew(img, log_fn=None):
    """Morphological text-line deskew + Hough fallback."""
    import cv2, numpy as np

    def log(m):
        if log_fn: log_fn(m)

    gray   = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w   = gray.shape
    angles = []

    for thresh in (
        cv2.adaptiveThreshold(cv2.GaussianBlur(gray,(5,5),0), 255,
                              cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                              cv2.THRESH_BINARY_INV, 15, 8),
        cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)[1],
    ):
        kernel  = cv2.getStructuringElement(cv2.MORPH_RECT, (28, 3))
        dilated = cv2.dilate(thresh, kernel, iterations=2)
        cnts, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            if cv2.contourArea(c) < 400: continue
            rect  = cv2.minAreaRect(c)
            bw, bh = rect[1]
            if bw == 0 or bh == 0: continue
            if max(bw,bh)/min(bw,bh) < 2.0: continue
            angle = rect[2]
            if angle < -45: angle += 90
            if abs(angle) < 45: angles.append(angle)

    if len(angles) < 3:
        edges = cv2.Canny(gray, 50, 150, apertureSize=3)
        lines = cv2.HoughLines(edges, 1, np.pi/180, threshold=80)
        if lines is not None:
            for rho, theta in lines[:,0]:
                a = np.degrees(theta) - 90
                if abs(a) < 45: angles.append(float(a))

    if not angles:
        log("  Deskew: no reliable angle — skipping")
        return img

    median = float(np.median(angles))
    if abs(median) < 0.4:
        log(f"  Deskew: {median:.2f}° negligible — skipping")
        return img

    log(f"  Deskew: {median:.2f}° from {len(angles)} samples")
    M = cv2.getRotationMatrix2D((w/2, h/2), median, 1.0)
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


# ── Auto-orientation ──────────────────────────────────────────────────────────

def _enhance_for_orient(img):
    """CLAHE + sharpen + conditional brightness so EasyOCR works on degraded thumbnails."""
    import cv2, numpy as np
    lab     = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l       = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4)).apply(l)
    out     = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)
    if np.mean(cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)) < 80:   # very dark
        out = _gamma_lut(out, 0.4)
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
    return cv2.filter2D(out, -1, kernel)


def auto_orient(img, log_fn=None, langs: tuple = ("en",)):
    """
    Try all 12 multiples of 30° (0 30 60 … 330) on an enhanced thumbnail.
    Score each with (chars × confidence) from EasyOCR.
    Rotate the full-resolution image to whichever wins by > 10%.
    """
    import cv2, numpy as np

    def log(m):
        if log_fn: log_fn(m)

    def _rot(src, angle):
        sh, sw = src.shape[:2]
        M = cv2.getRotationMatrix2D((sw / 2, sh / 2), angle, 1.0)
        return cv2.warpAffine(src, M, (sw, sh),
                              flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REPLICATE)

    h, w  = img.shape[:2]
    scale = min(1.0, 700 / max(h, w))
    small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    enh   = _enhance_for_orient(small)   # enhanced copy — scoring only

    reader = _get_easyocr_reader(langs)

    scores = {}
    for angle in range(0, 360, 30):
        rot     = _rot(enh, angle)
        rgb     = cv2.cvtColor(rot, cv2.COLOR_BGR2RGB)
        results = reader.readtext(rgb, detail=1)
        chars      = sum(len(r[1]) for r in results)
        conf_score = sum(len(r[1]) * float(r[2]) for r in results)
        avg_conf   = conf_score / chars if chars > 0 else 0.0
        scores[angle] = {"chars": chars, "score": conf_score, "avg_conf": avg_conf}
        log(f"  {angle:>3}°  chars={chars:>3}  avg_conf={avg_conf:.2f}  weighted={conf_score:5.1f}")

    best   = max(scores, key=lambda a: scores[a]["score"])
    orig_s = scores[0]["score"]
    best_s = scores[best]["score"]

    should_rotate = best != 0 and (orig_s == 0 or best_s > orig_s * 1.10)

    if should_rotate:
        pct   = f"+{(best_s/orig_s-1)*100:.0f}%" if orig_s > 0 else "0→>0"
        extra = ""
        if scores[best]["chars"] < scores[0]["chars"]:
            extra = (f" (fewer raw chars but avg_conf "
                     f"{scores[best]['avg_conf']:.2f} > {scores[0]['avg_conf']:.2f})")
        log(f"  → Rotating {best}°  weighted {orig_s:.1f}→{best_s:.1f} {pct}{extra}")
        M = cv2.getRotationMatrix2D((w / 2, h / 2), best, 1.0)
        return cv2.warpAffine(img, M, (w, h),
                              flags=cv2.INTER_CUBIC,
                              borderMode=cv2.BORDER_REPLICATE)

    log(f"  → Keeping 0°  (best={best}°  gap={best_s-orig_s:.1f} < 10% threshold)")
    return img


# ── IQA ───────────────────────────────────────────────────────────────────────

def measure_quality(img) -> dict:
    import cv2, numpy as np
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5,5), 0)
    return {
        "brightness": float(np.mean(gray)),
        "contrast":   float(np.std(gray)),
        "sharpness":  float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        "noise":      float(np.std(gray.astype("float32") - blurred.astype("float32"))),
    }


def adaptive_iqa_correct(img, log_fn=None):
    """Single ordered pass: denoise → brightness → contrast → sharpen."""
    import cv2, numpy as np

    def log(m):
        if log_fn: log_fn(m)

    m = measure_quality(img)
    log(f"  Before → brightness={m['brightness']:.1f}  contrast={m['contrast']:.1f}  "
        f"sharpness={m['sharpness']:.1f}  noise={m['noise']:.1f}")

    applied = []

    if m["noise"] > 12:
        img = cv2.fastNlMeansDenoisingColored(img, None, h=10, hColor=10,
                                               templateWindowSize=7, searchWindowSize=21)
        applied.append(f"denoise(σ={m['noise']:.1f})")
        m = measure_quality(img)

    if m["brightness"] > 185:
        gamma = 1.0 + (m["brightness"] - 185) / 70.0
        img   = _gamma_lut(img, gamma)
        applied.append(f"highlight(γ={gamma:.2f})")
    elif m["brightness"] < 65:
        gamma = max(0.30, 1.0 - (65 - m["brightness"]) / 65.0)
        img   = _gamma_lut(img, gamma)
        applied.append(f"gamma-boost(γ={gamma:.2f})")

    m2 = measure_quality(img)
    if m2["contrast"] < 38:
        lab     = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l       = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8,8)).apply(l)
        img     = cv2.cvtColor(cv2.merge((l,a,b)), cv2.COLOR_LAB2BGR)
        applied.append(f"CLAHE(std={m2['contrast']:.1f})")

    m3 = measure_quality(img)
    if m3["sharpness"] < 80 and m3["noise"] < 10:
        kernel = np.array([[0,-1,0],[-1,5,-1],[0,-1,0]], dtype=np.float32)
        img    = cv2.filter2D(img, -1, kernel)
        applied.append(f"sharpen(lap={m3['sharpness']:.1f})")

    m4 = measure_quality(img)
    log(f"  After  → brightness={m4['brightness']:.1f}  contrast={m4['contrast']:.1f}  "
        f"sharpness={m4['sharpness']:.1f}  noise={m4['noise']:.1f}")
    log(f"  Fixes: {', '.join(applied) if applied else 'none needed'}")
    return img


# ── Full preprocessing pipeline ────────────────────────────────────────────────

def preprocess_image(image_bytes: bytes, log_fn=None, use_vlm: bool = True,
                     use_seg: bool = False, langs: tuple = ("en",)):
    """Returns (preprocessed_bytes, vlm_scores | None)."""
    import cv2, numpy as np

    def log(m):
        if log_fn: log_fn(m)

    log("─── Preprocessing started ───────────────────────────────────")

    # Stage 0 — EXIF + normalise
    log("[Stage 0] EXIF orientation fix")
    img_pil = Image.open(io.BytesIO(image_bytes))
    try:
        img_pil = ImageOps.exif_transpose(img_pil)
        log("  EXIF transpose done (no-op if not needed)")
    except Exception as ex:
        log(f"  EXIF skipped: {ex}")
    img = _pil_to_bgr(img_pil)

    # Stage 0.5 — Background segmentation (optional)
    if use_seg:
        log("[Stage 0.5] Background removal (U2Net / rembg)...")
        image_bytes = segment_document_bg(image_bytes, log_fn=log_fn)
        img = _bytes_to_bgr(image_bytes)
    else:
        log("[Stage 0.5] Background removal skipped (enable with checkbox)")

    # Stage 1 — Resolution
    h, w = img.shape[:2]
    if w > 4000 or h > 4000:
        scale = min(4000/w, 4000/h)
        img   = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        log(f"[Stage 1] Downscaled {w}×{h} → {img.shape[1]}×{img.shape[0]}")
    elif w < 1000:
        scale = 1000/w
        img   = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        log(f"[Stage 1] Upscaled {w}×{h} → {img.shape[1]}×{img.shape[0]}")
    else:
        log(f"[Stage 1] Resolution {w}×{h} OK")

    # Stage 1.5 — Auto-orientation (4 rotations × EasyOCR score)
    log("[Stage 1.5] Auto-orientation check (0° … 330° in 30° steps)...")
    img = auto_orient(img, log_fn=log_fn, langs=langs)

    # Stage 2 — Geometry
    log("[Stage 2] Document contour detection (4 variants × 3 Canny configs)...")
    warped = _warp_document_robust(img, log_fn=log_fn)
    if warped is not None:
        img = warped
    else:
        log("  Falling back to morphological deskew")
        img = _morph_deskew(img, log_fn=log_fn)

    # Stage 3 — IQA
    log("[Stage 3] Adaptive IQA (denoise → brightness → contrast → sharpen)")
    img = adaptive_iqa_correct(img, log_fn=log_fn)

    preprocessed_bytes = _bgr_to_bytes(img)

    # Stage 4 — Pick better result (preprocessed vs original)
    log("[Stage 4] Comparing preprocessed vs original by OCR char count...")
    try:
        import numpy as np
        reader    = _get_easyocr_reader(("en",))
        orig_arr  = np.array(Image.open(io.BytesIO(image_bytes)).convert("RGB"))
        prep_arr  = np.array(Image.open(io.BytesIO(preprocessed_bytes)).convert("RGB"))
        orig_n    = sum(len(r[1]) for r in reader.readtext(orig_arr))
        prep_n    = sum(len(r[1]) for r in reader.readtext(prep_arr))
        log(f"  Chars — original: {orig_n}  preprocessed: {prep_n}")
        if prep_n < orig_n * 0.7:
            log("  ⚠ Preprocessed is worse — reverting to original")
            preprocessed_bytes = image_bytes
        else:
            log("  ✓ Preprocessed is better or equal — keeping")
    except Exception as ex:
        log(f"  Comparison skipped: {ex}")

    # Stage 5 — VLM quality check (optional)
    vlm_scores = None
    if use_vlm:
        log(f"[Stage 5] VLM quality check via {MODEL}  (~20-30 s)...")
        vlm_scores = _vlm_quality_check(preprocessed_bytes)
        log(
            f"  Scores → brightness:{vlm_scores.get('brightness','?')}/10  "
            f"sharpness:{vlm_scores.get('sharpness','?')}/10  "
            f"contrast:{vlm_scores.get('contrast','?')}/10  "
            f"noise:{vlm_scores.get('noise','?')}/10  "
            f"readable:{vlm_scores.get('readable','?')}/10"
        )
        issues = vlm_scores.get("issues", [])
        if issues:
            log(f"  Issues: {', '.join(str(x) for x in issues)}")

        readable = int(vlm_scores.get("readable",    5))
        orient   = int(vlm_scores.get("orientation", 5))
        b        = int(vlm_scores.get("brightness",  5))
        s        = int(vlm_scores.get("sharpness",   5))
        n        = int(vlm_scores.get("noise",        5))

        changed = False

        # Orientation fix first (VLM fallback for any rotation auto_orient missed)
        if orient <= 2:
            img = cv2.rotate(img, cv2.ROTATE_180)
            log(f"  → VLM: orientation={orient}/10 — rotated 180° (upside-down)")
            changed = True
        elif orient == 3:
            img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
            log(f"  → VLM: orientation={orient}/10 — rotated 90° CW (sideways)")
            changed = True
        elif orient == 4:
            img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
            log(f"  → VLM: orientation={orient}/10 — rotated 90° CCW (sideways)")
            changed = True

        if readable < 5:
            log(f"  ⚠ readable={readable}/10 — applying quality recovery")
            if b <= 2:
                img = _gamma_lut(img, 0.35); log("  → Heavy gamma boost"); changed = True
            elif b >= 9:
                img = _gamma_lut(img, 2.5);  log("  → Heavy highlight recovery"); changed = True
            if s <= 2 and n < 6:
                kernel = np.array([[-1,-1,-1],[-1,9,-1],[-1,-1,-1]], dtype=np.float32)
                img    = cv2.filter2D(img, -1, kernel)
                log("  → Aggressive sharpen"); changed = True

        if changed:
            preprocessed_bytes = _bgr_to_bytes(img)
        else:
            log(f"  ✓ readable={readable}/10  orientation={orient}/10 — no recovery needed")
    else:
        log("[Stage 5] VLM quality check skipped")

    log("─── Preprocessing complete ──────────────────────────────────")
    return preprocessed_bytes, vlm_scores


# ── VLM quality check ──────────────────────────────────────────────────────────

_VLM_QUALITY_PROMPT = """\
You are a document image quality inspector.
Rate this document image. Return ONLY valid JSON — no markdown fences.
{
  "brightness": 5,
  "sharpness":  5,
  "contrast":   5,
  "noise":      5,
  "orientation":5,
  "readable":   8,
  "issues":     []
}
Scales (integers 1-10):
  brightness  1=pitch black  5=perfect  10=blown-out white
  sharpness   1=completely blurry  10=razor sharp
  contrast    1=flat grey  10=harsh clipping
  noise       1=clean  10=heavy grain/artefacts
  orientation 1=upside-down  3=rotated 90deg  5=correct
  readable    1=unreadable  10=perfect for OCR
  issues      plain-English list (empty if none)
"""


def _vlm_quality_check(image_bytes: bytes) -> dict:
    try:
        raw    = ask_vision(image_bytes, _VLM_QUALITY_PROMPT)
        result = parse_json(raw)
        return result if result else {"readable": 5, "issues": ["JSON parse error"]}
    except Exception as e:
        return {"readable": 5, "issues": [f"VLM error: {e}"]}


# ── Document segmentation (background removal) ────────────────────────────────

def segment_document_bg(image_bytes: bytes, log_fn=None) -> bytes:
    """
    Remove background outside the document using rembg (U2Net ONNX model).
    The non-document area is replaced with white so OCR is not distracted by
    table surfaces, hands, wallets, or other objects in the photo.

    Requires:  pip install rembg onnxruntime
    Model is downloaded automatically on first use (~170 MB, cached in ~/.u2net/).
    """
    def log(m):
        if log_fn: log_fn(m)

    try:
        from rembg import remove as rembg_remove
    except ImportError:
        log("  ⚠ rembg not installed — run: pip install rembg onnxruntime")
        return image_bytes

    try:
        inp = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        log(f"  Running U2Net segmentation on {inp.width}×{inp.height} image...")
        out = rembg_remove(inp)          # returns RGBA; alpha = document mask

        # Check how much of the image was masked (0=nothing found, 1=whole image)
        import numpy as np
        alpha = np.array(out)[:, :, 3]
        coverage = float(alpha.mean()) / 255.0
        log(f"  Mask coverage: {coverage*100:.1f}%")

        if coverage < 0.05:
            log("  ⚠ Mask too empty — segmentation skipped (keeping original)")
            return image_bytes
        if coverage > 0.97:
            log("  ⚠ Mask covers whole image — no background detected, keeping original")
            return image_bytes

        # Composite the masked image over solid white
        bg     = Image.new("RGBA", out.size, (255, 255, 255, 255))
        bg.paste(out, mask=out.split()[3])
        result = bg.convert("RGB")

        buf = io.BytesIO()
        result.save(buf, format="PNG")
        log(f"  ✓ Background removed — {coverage*100:.1f}% foreground preserved")
        return buf.getvalue()

    except Exception as e:
        log(f"  ⚠ Segmentation error: {e}")
        return image_bytes


# ── Cross-validation ───────────────────────────────────────────────────────────

def cross_validate_fields(fields: dict, raw_ocr: str) -> tuple:
    """Check every VLM field against raw OCR. Returns (annotated, summary, reliability%)."""
    if not raw_ocr.strip():
        return ({k: {"value": v, "status": "no_ocr"} for k,v in fields.items()},
                "⚠ No OCR text — cannot validate", 0)

    ocr_lower  = raw_ocr.lower()
    ocr_squash = re.sub(r"\s+", "", ocr_lower)
    ocr_digits = re.sub(r"\D", "", raw_ocr)

    id_fields   = {"aadhaar_number","pan_number","gstin","account_number","pf_number","employee_id","cibil_score"}
    date_fields = {"dob","filing_date","registration_date","report_date","period_from","period_to"}

    annotated = {}
    confirmed = partial_c = unverified = 0

    for key, value in fields.items():
        val_str = str(value).strip() if value is not None else ""

        if not val_str or val_str in ("0","null","None","N/A",""):
            annotated[key] = {"value": value, "status": "empty"}; continue

        if re.sub(r"[\sX\-*]", "", val_str.upper()) == "":
            annotated[key] = {"value": value, "status": "masked"}; continue

        if len(val_str.replace(" ","")) <= 2:
            annotated[key] = {"value": value, "status": "short"}; continue

        val_lower  = val_str.lower()
        val_squash = re.sub(r"\s+","", val_lower)
        val_digits = re.sub(r"\D","", val_str)

        if key in date_fields:
            if val_digits and len(val_digits) >= 6 and val_digits in ocr_digits:
                annotated[key] = {"value": value, "status": "date_ok"}; confirmed += 1
            else:
                annotated[key] = {"value": value, "status": "unverified"}; unverified += 1
            continue

        if key in id_fields:
            if val_digits and len(val_digits) >= 4 and val_digits in ocr_digits:
                annotated[key] = {"value": value, "status": "numeric_ok"}; confirmed += 1
            else:
                annotated[key] = {"value": value, "status": "unverified"}; unverified += 1
            continue

        if val_squash in ocr_squash:
            annotated[key] = {"value": value, "status": "confirmed"}; confirmed += 1; continue

        words   = [w for w in re.findall(r"[a-z0-9]{3,}", val_lower)]
        if not words:
            annotated[key] = {"value": value, "status": "short"}; continue
        matched = [w for w in words if w in ocr_lower]
        ratio   = len(matched) / len(words)
        if ratio >= 0.60:
            annotated[key] = {"value": value, "status": "partial",
                              "detail": f"{len(matched)}/{len(words)} words"}
            partial_c += 1
        else:
            annotated[key] = {"value": value, "status": "unverified",
                              "detail": f"{len(matched)}/{len(words)} words"}
            unverified += 1

    checkable   = confirmed + partial_c + unverified
    reliability = int(((confirmed + 0.5*partial_c) / checkable)*100) if checkable else 100

    if checkable == 0:
        summary = "✓ No checkable fields"
    elif unverified == 0:
        summary = f"✓ All {checkable} fields verified  (reliability {reliability}%)"
    elif reliability < 40:
        summary = (f"⚠  HIGH HALLUCINATION RISK — {unverified}/{checkable} fields not in OCR  "
                   f"(reliability {reliability}%)  — re-upload a clearer image")
    elif reliability < 70:
        summary = f"⚠  {unverified} field(s) unverified — manual review needed  (reliability {reliability}%)"
    else:
        summary = f"✓ {confirmed} confirmed, {partial_c} partial, {unverified} unverified  (reliability {reliability}%)"

    return annotated, summary, reliability


# ── EasyOCR cache ──────────────────────────────────────────────────────────────

_easyocr_cache: dict = {}

def _get_easyocr_reader(langs: tuple):
    import easyocr
    key = tuple(sorted(langs))
    if key not in _easyocr_cache:
        _easyocr_cache[key] = easyocr.Reader(list(key), verbose=False)
    return _easyocr_cache[key]


# ── OCR / LLM helpers ─────────────────────────────────────────────────────────

def to_b64(data: bytes) -> str:
    return base64.b64encode(data).decode()

def parse_json(raw: str) -> dict:
    raw = re.sub(r"```(?:json)?","",raw).strip("`").strip()
    m   = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try: return json.loads(m.group())
        except: pass
    return {}

def is_digital_pdf(file_bytes: bytes) -> bool:
    try:
        import pypdfium2 as pdfium
        return len(pdfium.PdfDocument(file_bytes)[0].get_textpage().get_text_range().strip()) > 100
    except: return False

def pdf_to_images(file_bytes: bytes, max_pages: int = 3) -> list:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(file_bytes)
    out = []
    for i in range(min(len(pdf), max_pages)):
        buf = io.BytesIO()
        pdf[i].render(scale=2.0).to_pil().save(buf, format="PNG")
        out.append(buf.getvalue())
    return out

def extract_with_docling(file_path: str) -> str:
    from docling.document_converter import DocumentConverter
    return DocumentConverter().convert(file_path).document.export_to_markdown()

def ocr_with_easyocr(image_bytes: bytes, langs: tuple = ("en",)) -> str:
    import numpy as np
    reader = _get_easyocr_reader(langs)
    arr    = np.array(Image.open(io.BytesIO(image_bytes)).convert("RGB"))
    return "\n".join(r[1] for r in reader.readtext(arr))

def ask_vision(image_bytes: bytes, prompt: str) -> str:
    r = httpx.post(OLLAMA_URL, json={
        "model": MODEL,
        "messages": [{"role":"user","content":prompt,"images":[to_b64(image_bytes)]}],
        "stream": False, "options": {"temperature": 0},
    }, timeout=180)
    r.raise_for_status()
    return r.json()["message"]["content"]

def ask_text(text: str, prompt: str) -> str:
    r = httpx.post(OLLAMA_URL, json={
        "model": MODEL,
        "messages": [{"role":"user","content":f"{prompt}\n\nDocument text:\n{text}"}],
        "stream": False, "options": {"temperature": 0},
    }, timeout=180)
    r.raise_for_status()
    return r.json()["message"]["content"]


# ── Degradation simulator ──────────────────────────────────────────────────────

def apply_degradation(image_bytes: bytes, params: dict) -> bytes:
    import cv2, numpy as np
    img = _bytes_to_bgr(image_bytes)
    if img is None: return image_bytes
    h, w = img.shape[:2]

    if a := float(params.get("rotation", 0)):
        M = cv2.getRotationMatrix2D((w/2,h/2), -a, 1.0)
        img = cv2.warpAffine(img, M, (w,h), borderMode=cv2.BORDER_REPLICATE)
    if s := int(params.get("perspective", 0)):
        src = np.float32([[0,0],[w,0],[w,h],[0,h]])
        dst = np.float32([[s,s],[w-s,0],[w,h],[0,h-s]])
        img = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src,dst), (w,h), borderMode=cv2.BORDER_REPLICATE)
    if b := int(params.get("blur", 0)):
        img = cv2.GaussianBlur(img, (b*2+1,b*2+1), 0)
    if m := int(params.get("motion_blur", 0)):
        if m > 1:
            k = np.zeros((m,m)); k[m//2,:] = np.ones(m)/m
            img = cv2.filter2D(img, -1, k)
    if n := float(params.get("noise", 0)):
        img = np.clip(img.astype(np.int16) + np.random.normal(0,n,img.shape).astype(np.int16),0,255).astype(np.uint8)
    if br := float(params.get("brightness", 0)):
        img = cv2.convertScaleAbs(img, beta=br)
    if ct := float(params.get("contrast", 1.0)):
        if ct != 1.0: img = cv2.convertScaleAbs(img, alpha=ct)
    if oe := float(params.get("overexpose", 0)):
        img = np.clip(img.astype(np.float32)*(1+oe/100),0,255).astype(np.uint8)
    if sh := float(params.get("shadow", 0)):
        mask = np.ones((h,w), np.float32)
        fade = max(1,int(w*0.55))
        mask[:,:fade] = np.linspace(1-sh/200.0, 1.0, fade)
        img = np.clip(img*np.stack([mask]*3,axis=-1),0,255).astype(np.uint8)
    if q := int(params.get("jpeg_quality", 95)):
        if q < 95:
            _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
            img    = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if cr := int(params.get("crop_edges", 0)):
        img = cv2.resize(img[cr:h-cr, cr:w-cr], (w,h), interpolation=cv2.INTER_LINEAR)

    _, out = cv2.imencode(".png", img)
    return out.tobytes()


# ── Slider widget ─────────────────────────────────────────────────────────────

class SliderRow(tk.Frame):
    def __init__(self, parent, label, from_, to, default, fmt="{:.0f}", on_change=None, **kw):
        super().__init__(parent, bg="#13131F", **kw)
        self._cb     = on_change
        self.enabled = tk.BooleanVar(value=False)
        self.value   = tk.DoubleVar(value=default)
        row = tk.Frame(self, bg="#13131F"); row.pack(fill="x")
        tk.Checkbutton(row, variable=self.enabled, bg="#13131F", fg="#E2E8F0",
                       selectcolor="#2D2D44", activebackground="#13131F",
                       command=self._fire).pack(side="left")
        tk.Label(row, text=label, fg="#CBD5E1", bg="#13131F",
                 font=("Inter",9), anchor="w").pack(side="left", fill="x", expand=True)
        self._lbl = tk.Label(row, text=fmt.format(default), fg="#A5B4FC",
                             bg="#13131F", font=("DM Mono",9), width=6)
        self._lbl.pack(side="right")
        ttk.Scale(self, from_=from_, to=to, variable=self.value, orient="horizontal",
                  command=lambda v, f=fmt: (
                      self._lbl.config(text=f.format(float(v))),
                      self.enabled.set(True), self._fire(),
                  )).pack(fill="x", padx=6, pady=(0,8))

    def _fire(self):
        if self._cb: self._cb()

    def active_value(self):
        return self.value.get() if self.enabled.get() else None


# ── App ───────────────────────────────────────────────────────────────────────

class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("FinSight AI — OCR Test Tool")
        self.geometry("1520x920")
        self.minsize(1100, 700)
        self.configure(bg="#1E1E2E")

        self._file_bytes     = None
        self._file_path      = None
        self._orig_bytes     = None
        self._degraded_bytes = None
        self._cleaned_bytes  = None
        self._photos         = {}
        self._deg_job        = None
        self._last_raw_text  = ""
        self._last_route     = ""
        self._last_vlm       = None
        self._proc_log_lines = []

        self._build_ui()

    def _build_ui(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TScale",              background="#13131F", troughcolor="#2D2D44", sliderlength=14)
        s.configure("TCombobox",           fieldbackground="#2D2D44", background="#2D2D44", foreground="#E2E8F0")
        s.configure("Vertical.TScrollbar", background="#2D2D44", troughcolor="#13131F", arrowcolor="#64748B")
        s.configure("TNotebook",           background="#1E1E2E", borderwidth=0)
        s.configure("TNotebook.Tab",       background="#13131F", foreground="#94A3B8",
                    padding=[10,4], font=("Inter",9))
        s.map("TNotebook.Tab",
              background=[("selected","#1E1E2E")], foreground=[("selected","#A5B4FC")])

        # Toolbar
        tb = tk.Frame(self, bg="#13131F", pady=10, padx=14)
        tb.pack(fill="x", side="top")

        tk.Label(tb, text="FinSight OCR", font=("Inter",13,"bold"),
                 fg="#A5B4FC", bg="#13131F").pack(side="left", padx=(0,18))
        tk.Label(tb, text="Doc type:", fg="#94A3B8",
                 bg="#13131F", font=("Inter",10)).pack(side="left")
        self.doc_var = tk.StringVar(value=DOC_LABELS["aadhaar"])
        ttk.Combobox(tb, textvariable=self.doc_var, width=22,
                     values=list(DOC_LABELS.values()),
                     state="readonly", font=("Inter",10)).pack(side="left", padx=(4,16))

        self.mode_var = tk.StringVar(value="full")
        for txt, val in [("Full pipeline","full"), ("OCR only","ocr")]:
            tk.Radiobutton(tb, text=txt, variable=self.mode_var, value=val,
                           bg="#13131F", fg="#E2E8F0", selectcolor="#2D2D44",
                           activebackground="#13131F",
                           font=("Inter",10)).pack(side="left", padx=3)

        tk.Frame(tb, width=8, bg="#13131F").pack(side="left")
        self.vlm_pp_var = tk.BooleanVar(value=True)
        tk.Checkbutton(tb, text="VLM quality check", variable=self.vlm_pp_var,
                       bg="#13131F", fg="#FBBF24", selectcolor="#2D2D44",
                       activebackground="#13131F", font=("Inter",10)).pack(side="left", padx=(0,8))
        self.seg_var = tk.BooleanVar(value=False)
        tk.Checkbutton(tb, text="Remove bg (rembg)", variable=self.seg_var,
                       bg="#13131F", fg="#86EFAC", selectcolor="#2D2D44",
                       activebackground="#13131F", font=("Inter",10)).pack(side="left", padx=(0,16))

        self.upload_btn = self._btn(tb, "📂  Upload",  self._upload, "#4F46E5", "#4338CA")
        self.upload_btn.pack(side="left", padx=(0,6))
        self.run_btn = self._btn(tb, "▶  Process", self._run, "#0D9488", "#0F766E", state="disabled")
        self.run_btn.pack(side="left")
        self.file_lbl = tk.Label(tb, text="No file selected", fg="#475569",
                                 bg="#13131F", font=("Inter",9))
        self.file_lbl.pack(side="left", padx=14)

        # Main layout
        main = tk.Frame(self, bg="#1E1E2E")
        main.pack(fill="both", expand=True, padx=10, pady=8)
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, minsize=300, weight=0)
        main.rowconfigure(0, weight=3)
        main.rowconfigure(1, weight=2)

        left = tk.Frame(main, bg="#1E1E2E")
        left.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0,8))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=3)
        left.rowconfigure(1, weight=2)

        # Image panels
        imgs = tk.Frame(left, bg="#1E1E2E")
        imgs.grid(row=0, column=0, sticky="nsew", pady=(0,6))
        imgs.rowconfigure(0, weight=1)
        for c in range(3): imgs.columnconfigure(c, weight=1)
        self.orig_panel  = self._img_card(imgs, "Original",         0)
        self.deg_panel   = self._img_card(imgs, "Simulated Issues", 1)
        self.clean_panel = self._img_card(imgs, "Preprocessed",     2)

        # Notebook — 3 tabs
        nb = ttk.Notebook(left)
        nb.grid(row=1, column=0, sticky="nsew")
        self._nb = nb

        for bg, attr, title in [
            ("#0F172A", "raw_txt",  "  Raw OCR Text  "),
            ("#0A1F18", "json_txt", "  Extraction + Validation  "),
            ("#0D0D1A", "log_txt",  "  Processing Log  "),
        ]:
            f = tk.Frame(nb, bg=bg)
            nb.add(f, text=title)
            f.rowconfigure(0, weight=1); f.columnconfigure(0, weight=1)
            w = scrolledtext.ScrolledText(
                f, wrap="word", font=("Consolas",9), bg=bg, fg="#E2E8F0",
                insertbackground="white", relief="flat", padx=8, pady=8, state="disabled")
            w.tag_config("txt", foreground="#E2E8F0")
            w.grid(row=0, column=0, sticky="nsew")
            setattr(self, attr, w)

        # Colour tags for validation panel
        self.json_txt.tag_config("hdr",       foreground="#A5B4FC", font=("DM Mono",9,"bold"))
        self.json_txt.tag_config("divider",    foreground="#2D3748")
        self.json_txt.tag_config("confirmed",  foreground="#34D399")
        self.json_txt.tag_config("date_ok",    foreground="#34D399")
        self.json_txt.tag_config("numeric_ok", foreground="#34D399")
        self.json_txt.tag_config("partial",    foreground="#FBBF24")
        self.json_txt.tag_config("unverified", foreground="#F87171")
        self.json_txt.tag_config("masked",     foreground="#94A3B8")
        self.json_txt.tag_config("empty",      foreground="#475569")
        self.json_txt.tag_config("short",      foreground="#64748B")
        self.json_txt.tag_config("val",        foreground="#E2E8F0")
        self.json_txt.tag_config("key_col",    foreground="#94A3B8")
        self.json_txt.tag_config("warn",       foreground="#FCA5A5", font=("DM Mono",9,"bold"))
        self.json_txt.tag_config("ok_sum",     foreground="#6EE7B7", font=("DM Mono",9,"bold"))

        self._build_sidebar(main)

        # Status bar
        sb = tk.Frame(self, bg="#0D0D1A", pady=5)
        sb.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="Ready — upload a document to begin.")
        tk.Label(sb, textvariable=self.status_var, fg="#94A3B8", bg="#0D0D1A",
                 font=("Inter",9), anchor="w").pack(side="left", padx=12)
        self.progress = ttk.Progressbar(sb, mode="indeterminate", length=120)
        self.progress.pack(side="right", padx=12)

    def _build_sidebar(self, parent):
        sb = tk.Frame(parent, bg="#13131F")
        sb.grid(row=0, column=1, rowspan=2, sticky="nsew")
        sb.columnconfigure(0, weight=1); sb.rowconfigure(1, weight=1)

        hdr = tk.Frame(sb, bg="#0D0D1A", pady=9, padx=10)
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew")
        tk.Label(hdr, text="⚠  Simulate Upload Issues", fg="#FBBF24",
                 bg="#0D0D1A", font=("Inter",10,"bold")).pack(side="left")

        canvas = tk.Canvas(sb, bg="#13131F", highlightthickness=0, bd=0)
        vsb    = ttk.Scrollbar(sb, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.grid(row=1, column=0, sticky="nsew")
        vsb.grid(row=1, column=1, sticky="ns")

        inner = tk.Frame(canvas, bg="#13131F", padx=12)
        cw    = canvas.create_window((0,0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(cw, width=e.width))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-1*(e.delta//120),"units"))

        cb = self._on_degrade

        def sec(t):
            tk.Frame(inner, height=1, bg="#1E293B").pack(fill="x", pady=(10,0))
            tk.Label(inner, text=f"  {t}", fg="#64748B", bg="#13131F",
                     font=("Inter",8,"bold"), anchor="w").pack(fill="x", pady=(2,4))

        sec("ORIENTATION")
        self.c_rotate  = SliderRow(inner,"Rotation angle (°)",   -180,180, 0, on_change=cb); self.c_rotate.pack(fill="x")
        self.c_persp   = SliderRow(inner,"Perspective skew (px)",   0,120, 0, on_change=cb); self.c_persp.pack(fill="x")
        sec("FOCUS & MOTION")
        self.c_blur    = SliderRow(inner,"Gaussian blur (radius)",   0, 20, 0, on_change=cb); self.c_blur.pack(fill="x")
        self.c_motion  = SliderRow(inner,"Motion blur (length px)",  0, 50, 0, on_change=cb); self.c_motion.pack(fill="x")
        sec("EXPOSURE")
        self.c_bright  = SliderRow(inner,"Brightness offset",     -150,150, 0, on_change=cb); self.c_bright.pack(fill="x")
        self.c_contrast= SliderRow(inner,"Contrast scale",         0.1,3.0,1.0,fmt="{:.2f}",on_change=cb); self.c_contrast.pack(fill="x")
        self.c_overexp = SliderRow(inner,"Overexposure (%)",          0,200, 0, on_change=cb); self.c_overexp.pack(fill="x")
        self.c_shadow  = SliderRow(inner,"Shadow intensity",          0,150, 0, on_change=cb); self.c_shadow.pack(fill="x")
        sec("NOISE & COMPRESSION")
        self.c_noise   = SliderRow(inner,"Random noise (σ)",          0, 80, 0, on_change=cb); self.c_noise.pack(fill="x")
        self.c_jpeg    = SliderRow(inner,"JPEG quality (low=worse)",  5, 95,95, on_change=cb); self.c_jpeg.pack(fill="x")
        sec("FRAMING")
        self.c_crop    = SliderRow(inner,"Crop edges (px)",            0, 80, 0, on_change=cb); self.c_crop.pack(fill="x")

        tk.Frame(inner, height=1, bg="#1E293B").pack(fill="x", pady=(12,0))
        self._btn(inner,"↺  Reset All Effects",self._reset_degrade,"#334155","#1E293B").pack(fill="x",pady=8,padx=2)
        tk.Label(inner, text="Move any slider to apply.\nSimulated image updates in real time.\nProcess uses simulated image.",
                 fg="#475569",bg="#13131F",font=("Inter",8),justify="left",wraplength=250).pack(fill="x",pady=(0,10))

    # ── Widget factories ──────────────────────────────────────────────────────

    def _btn(self, parent, text, cmd, bg, abg, state="normal"):
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg="white",
                         relief="flat", font=("Inter",10,"bold"), padx=12, pady=6,
                         cursor="hand2", activebackground=abg, activeforeground="white",
                         state=state)

    def _img_card(self, parent, title, col):
        pad = (0,4) if col < 2 else (4,0)
        card = tk.Frame(parent, bg="#13131F")
        card.grid(row=0, column=col, sticky="nsew", padx=pad, pady=2)
        card.rowconfigure(1, weight=1); card.columnconfigure(0, weight=1)
        tk.Label(card, text=title, fg="#A5B4FC", bg="#13131F",
                 font=("Inter",9,"bold"), pady=5).grid(row=0, column=0)
        lbl = tk.Label(card, text="—", bg="#0D0D1A", fg="#334155", font=("Inter",9))
        lbl.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0,6))
        return lbl

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _doc_key(self):
        return next((k for k,v in DOC_LABELS.items() if v == self.doc_var.get()), "aadhaar")

    def _get_degrade_params(self):
        mapping = {
            "rotation":    self.c_rotate,   "perspective": self.c_persp,
            "blur":        self.c_blur,      "motion_blur": self.c_motion,
            "brightness":  self.c_bright,    "contrast":    self.c_contrast,
            "overexpose":  self.c_overexp,   "shadow":      self.c_shadow,
            "noise":       self.c_noise,     "jpeg_quality":self.c_jpeg,
            "crop_edges":  self.c_crop,
        }
        return {k: v for k, ctrl in mapping.items() if (v := ctrl.active_value()) is not None}

    def _show_img(self, panel, img_bytes, caption=""):
        if not img_bytes: return
        try:
            img = Image.open(io.BytesIO(img_bytes))
            panel.update_idletasks()
            pw = max(panel.winfo_width()-16, 180)
            ph = max(panel.winfo_height()-16, 120)
            img.thumbnail((pw, ph), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            panel.config(image=photo, text=caption, compound="bottom",
                         fg="#64748B", bg="#0D0D1A", font=("Inter",8))
            self._photos[id(panel)] = photo
        except Exception as e:
            panel.config(text=f"Cannot display:\n{e}", image="")

    def _clear_panel(self, panel, msg="—"):
        panel.config(image="", text=msg, bg="#0D0D1A", fg="#334155")
        self._photos.pop(id(panel), None)

    def _set_text(self, w, text):
        w.config(state="normal")
        w.delete("1.0", "end")
        w.insert("end", text, "txt")
        w.config(state="disabled")

    def _set_json_output(self, validated, summary, reliability, route, raw_fields):
        w   = self.json_txt
        div = "─" * 50
        w.config(state="normal"); w.delete("1.0","end")

        w.insert("end", f"{div}\n", "divider")
        w.insert("end", f" EXTRACTION RESULT\n", "hdr")
        w.insert("end", f" Route: {route}\n", "key_col")
        w.insert("end", f"{div}\n\n", "divider")

        tag = "warn" if reliability < 50 else "ok_sum"
        w.insert("end", f" {summary}\n\n", tag)

        w.insert("end", " FIELD VALIDATION\n", "hdr")
        w.insert("end", f"{div}\n", "divider")

        icons = {
            "confirmed": ("✓","confirmed"), "date_ok": ("✓","date_ok"),
            "numeric_ok":("✓","numeric_ok"), "partial": ("~","partial"),
            "unverified":("⚠","unverified"), "masked":  ("■","masked"),
            "empty":     ("∅","empty"),      "short":   ("—","short"),
            "no_ocr":    ("?","short"),
        }
        for key, info in validated.items():
            if isinstance(info, dict):
                val, status, detail = info.get("value",""), info.get("status","empty"), info.get("detail","")
            else:
                val, status, detail = info, "empty", ""
            icon, tag = icons.get(status, ("?","short"))
            val_str   = str(val)[:38] + ("..." if len(str(val)) > 38 else "")
            det_str   = f"  ({detail})" if detail else ""
            w.insert("end", f" {key:<22}", "key_col")
            w.insert("end", f"{icon} {status.upper():<14}", tag)
            w.insert("end", f'"{val_str}"{det_str}\n', "val")

        w.insert("end", f"\n{div}\n", "divider")
        w.insert("end", " RAW JSON\n", "hdr")
        w.insert("end", f"{div}\n", "divider")
        clean = {k: (v.get("value") if isinstance(v,dict) else v) for k,v in validated.items()}
        w.insert("end", json.dumps(clean, indent=2, ensure_ascii=False) + "\n", "val")
        w.config(state="disabled")

    def _log(self, msg):
        def _append():
            ts   = datetime.now().strftime("%H:%M:%S")
            line = f"[{ts}] {msg}"
            self.log_txt.config(state="normal")
            self.log_txt.insert("end", line+"\n", "txt")
            self.log_txt.see("end")
            self.log_txt.config(state="disabled")
            self._proc_log_lines.append(line)
        self.after(0, _append)

    def _clear_log(self):
        self._proc_log_lines = []
        self.log_txt.config(state="normal"); self.log_txt.delete("1.0","end"); self.log_txt.config(state="disabled")

    def _status(self, msg):
        self.after(0, lambda: self.status_var.set(msg))

    # ── Actions ───────────────────────────────────────────────────────────────

    def _upload(self):
        path = filedialog.askopenfilename(
            title="Select document",
            filetypes=[("All supported","*.pdf *.jpg *.jpeg *.png *.tiff *.bmp *.webp"),
                       ("PDF","*.pdf"),("Images","*.jpg *.jpeg *.png *.tiff *.bmp *.webp")])
        if not path: return

        self._file_path  = path
        self._file_bytes = Path(path).read_bytes()
        self.file_lbl.config(text=Path(path).name, fg="#94A3B8")

        suffix = Path(path).suffix.lower()
        if suffix == ".pdf":
            pages = pdf_to_images(self._file_bytes, max_pages=1)
            self._orig_bytes = pages[0] if pages else None
        else:
            self._orig_bytes = self._file_bytes

        self._degraded_bytes = self._orig_bytes
        self._cleaned_bytes  = None

        self._show_img(self.orig_panel, self._orig_bytes)
        self._show_img(self.deg_panel,  self._orig_bytes, "No effects applied")
        self._clear_panel(self.clean_panel, "Click ▶ Process")
        self._set_text(self.raw_txt, ""); self._set_text(self.json_txt, "")
        self._clear_log()
        self.run_btn.config(state="normal")
        self._status("File loaded. Optionally simulate issues, then ▶ Process.")

    def _on_degrade(self):
        if not self._orig_bytes: return
        if self._deg_job is not None: self.after_cancel(self._deg_job)
        self._deg_job = self.after(80, self._apply_degrade_bg)

    def _apply_degrade_bg(self):
        self._deg_job = None
        params, orig  = self._get_degrade_params(), self._orig_bytes
        def worker():
            result  = apply_degradation(orig, params) if params else orig
            caption = ("Active: "+", ".join(k.replace("_"," ") for k in params)
                       if params else "No effects applied")
            self._degraded_bytes = result
            self.after(0, lambda: self._show_img(self.deg_panel, result, caption))
        threading.Thread(target=worker, daemon=True).start()

    def _reset_degrade(self):
        for ctrl in [self.c_rotate, self.c_persp, self.c_blur, self.c_motion,
                     self.c_bright, self.c_contrast, self.c_overexp, self.c_shadow,
                     self.c_noise, self.c_jpeg, self.c_crop]:
            ctrl.enabled.set(False)
        self._on_degrade()

    def _run(self):
        if not self._file_bytes:
            messagebox.showwarning("No file","Upload a document first."); return
        self.run_btn.config(state="disabled"); self.upload_btn.config(state="disabled")
        self.progress.start(12); self._clear_log(); self._nb.select(2)
        threading.Thread(target=self._process, daemon=True).start()

    def _process(self):
        try:
            path        = Path(self._file_path)
            suffix      = path.suffix.lower()
            doc_key     = self._doc_key()
            prompt      = PROMPTS[doc_key]
            langs       = tuple(DOC_LANGUAGES.get(doc_key, ["en"]))
            input_bytes = self._degraded_bytes or self._file_bytes
            use_vlm_pp  = self.vlm_pp_var.get() and self.mode_var.get() == "full"
            use_seg     = self.seg_var.get()

            self._log("══════════════════════════════════════════════")
            self._log(f"File: {path.name}  |  {self.doc_var.get()}")
            self._log(f"Mode: {self.mode_var.get().upper()}  |  "
                      f"VLM: {'ON' if use_vlm_pp else 'OFF'}  |  "
                      f"Segment bg: {'ON' if use_seg else 'OFF'}")
            self._log("══════════════════════════════════════════════")

            # Preprocess
            self._status("Preprocessing image...")
            if suffix == ".pdf" and is_digital_pdf(self._file_bytes):
                pages = pdf_to_images(self._file_bytes, 1)
                self._cleaned_bytes = pages[0] if pages else None
                vlm = None
                self._log("Digital PDF — image preprocessing skipped")
                self.after(0, lambda: self._show_img(self.clean_panel, self._cleaned_bytes, "digital PDF"))
            else:
                self._cleaned_bytes, vlm = preprocess_image(
                    input_bytes, log_fn=self._log, use_vlm=use_vlm_pp,
                    use_seg=use_seg, langs=langs)
                cap = f"readable {vlm.get('readable','?')}/10" if vlm else ("seg+preprocessed" if use_seg else "preprocessed")
                self.after(0, lambda c=cap: self._show_img(self.clean_panel, self._cleaned_bytes, c))

            # OCR
            self._log("─── OCR extraction ─────────────────────────")
            self._status("Running EasyOCR...")

            if suffix == ".pdf":
                if is_digital_pdf(self._file_bytes):
                    self._log("Route: Digital PDF → Docling")
                    raw_text, route = extract_with_docling(str(path)), "Digital PDF → Docling"
                else:
                    pages    = pdf_to_images(input_bytes)
                    cleaned  = [preprocess_image(p, use_vlm=False, langs=langs)[0] for p in pages]
                    raw_text = "\n\n--- PAGE BREAK ---\n\n".join(ocr_with_easyocr(p, langs) for p in cleaned)
                    route    = f"Scanned PDF → EasyOCR ({len(pages)} page/s)"
                    self._log(f"Route: {route}")
            else:
                src       = self._cleaned_bytes or input_bytes
                raw_text  = ocr_with_easyocr(src, langs)
                self._log(f"Preprocessed OCR: {len(raw_text)} chars")
                if len(raw_text.strip()) < 50:
                    self._log(f"⚠ Only {len(raw_text.strip())} chars — retrying on raw input")
                    raw_alt = ocr_with_easyocr(input_bytes, langs)
                    self._log(f"Raw input OCR: {len(raw_alt)} chars")
                    if len(raw_alt) > len(raw_text):
                        raw_text = raw_alt
                        route    = "Image → EasyOCR (fallback: raw)"
                        self._log("Using raw input result")
                    else:
                        route = "Image → preprocess → EasyOCR"
                else:
                    route = "Image → preprocess → EasyOCR"

            self._log(f"OCR complete — {len(raw_text)} chars  |  Route: {route}")
            self._last_raw_text = raw_text
            self._last_route    = route

            self.after(0, lambda t=f"[Route]  {route}\n[Chars]  {len(raw_text)}\n{'─'*44}\n\n"+raw_text:
                       self._set_text(self.raw_txt, t))

            if self.mode_var.get() == "ocr":
                self._log("OCR-only mode — LLM skipped")
                self.after(0, lambda: self._set_text(
                    self.json_txt, "OCR-only mode — switch to Full pipeline for JSON extraction."))
                self._status(f"Done (OCR only) — {len(raw_text)} chars")
                return

            # LLM
            self._log(f"─── LLM extraction ({MODEL}) ─────────────")
            self._log("Calling Ollama (~30-60 s)...")
            self._status(f"Calling {MODEL}...")

            is_img = suffix in {".jpg",".jpeg",".png",".tiff",".bmp",".webp"}
            if is_img:
                self._log("Strategy: vision (image → VLM)")
                raw_resp = ask_vision(self._cleaned_bytes or input_bytes, prompt)
            elif suffix == ".pdf":
                if is_digital_pdf(self._file_bytes):
                    self._log("Strategy: text (Docling → LLM)")
                    raw_resp = ask_text(raw_text, prompt)
                else:
                    pages    = pdf_to_images(input_bytes)
                    cleaned  = preprocess_image(pages[0], use_vlm=False, langs=langs)[0]
                    self._log("Strategy: vision (PDF page → VLM)")
                    raw_resp = ask_vision(cleaned, prompt)
            else:
                raw_resp = ask_text(raw_text, prompt)

            fields = parse_json(raw_resp)
            if not fields:
                self._log("⚠ JSON parse failed — retrying")
                raw_resp2 = ask_text(raw_resp, "Extract JSON only from the above. Return ONLY valid JSON.")
                fields    = parse_json(raw_resp2)

            self._log(f"LLM done — {len(fields)} fields")

            # Cross-validate
            self._log("─── Cross-validating vs OCR text ───────────")
            validated, summary, reliability = cross_validate_fields(fields, raw_text)
            self._log(summary)
            for k, info in validated.items():
                st  = info.get("status","?") if isinstance(info,dict) else "?"
                val = info.get("value","")   if isinstance(info,dict) else info
                self._log(f"  {k:<22} [{st.upper()}]  {val!r}")

            self.after(0, lambda v=validated, s=summary, r=reliability, ro=route, f=fields:
                       self._set_json_output(v, s, r, ro, f))
            self._status(f"Done — {len(fields)} fields  |  Reliability: {reliability}%")

        except Exception as e:
            import traceback
            self._log(f"✗ ERROR: {e}")
            self._log(traceback.format_exc())
            self.after(0, lambda err=str(e): self._set_text(self.json_txt, f"Error:\n{err}"))
            self._status(f"Error: {e}")
        finally:
            self.after(0, self._done)

    def _done(self):
        self.progress.stop()
        self.run_btn.config(state="normal")
        self.upload_btn.config(state="normal")


if __name__ == "__main__":
    App().mainloop()
