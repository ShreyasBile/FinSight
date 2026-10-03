"""Stage-2 signal: blockwise noise-inconsistency analysis.

Every capture device + compression pipeline leaves a characteristic noise
floor across the whole image. Content pasted in from another source (or
locally smoothed/redrawn) carries a DIFFERENT noise level. We estimate local
noise per block via a high-pass residual (Laplacian) and flag blocks whose
noise deviates strongly from the page's typical level.

Complementary to ELA: ELA keys on JPEG compression history, this keys on
sensor/processing noise — a forgery can evade one and still trip the other.
"""
import cv2
import numpy as np
from PIL import Image

from app.tampering.config import CFG
from app.tampering.signals.base import SignalResult, unavailable

_C = CFG.signals.noise


def run(image: Image.Image) -> SignalResult:
    gray = np.asarray(image.convert("L"), dtype=np.float32)
    b = _C.block_size
    h, w = gray.shape
    if h < 2 * b or w < 2 * b:
        return unavailable("noise", "image too small for blockwise noise analysis")

    residual = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)

    # Mask out content edges first: text strokes dominate any raw statistic,
    # turning the map into a text-density detector instead of a noise
    # detector. Estimating only over low-gradient (background/paper) pixels
    # measures the actual noise floor per block.
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    flat_mask = cv2.magnitude(gx, gy) < 30.0

    rows, cols = h // b, w // b
    noise_map = np.full((rows, cols), np.nan, dtype=np.float32)
    for r in range(rows):
        for c in range(cols):
            sl = (slice(r * b, (r + 1) * b), slice(c * b, (c + 1) * b))
            flat = flat_mask[sl]
            if flat.sum() < (b * b) // 4:
                continue  # block is nearly all content — no reliable noise estimate
            noise_map[r, c] = np.median(np.abs(residual[sl][flat]))

    valid = ~np.isnan(noise_map)
    if valid.sum() < 6:
        return unavailable("noise", "too few background blocks for a noise baseline")

    med = float(np.nanmedian(noise_map))
    mad = float(np.nanmedian(np.abs(noise_map - med)))
    # Guard against a degenerate robust scale: block noise medians quantize to
    # integers, so on very uniform pages >50% of blocks can share one value and
    # MAD collapses to 0 — a raw 1e-6 floor then turns ±2 quantization wobble
    # into z≈10^6 and flags half the page. A block must deviate meaningfully
    # in BOTH relative (10% of page level) and absolute terms to be an outlier.
    scale = max(1.4826 * mad, 0.1 * med, 1.0)
    z = (noise_map - med) / scale
    z[~valid] = 0.0

    outliers = np.abs(z) > _C.outlier_sigma
    frac = float(outliers.sum() / valid.sum())
    # a couple of outlier blocks are normal (photos, logos); score ramps as the
    # anomalous area grows, saturating at 15% of the page
    score = float(np.clip(frac / 0.15, 0.0, 1.0)) * min(1.0, np.abs(z).max() / (2 * _C.outlier_sigma))

    findings = [
        f"{int(outliers.sum())}/{int(valid.sum())} background blocks ({frac:.1%}) have anomalous noise levels"
    ]
    if score > 0.4:
        findings.append("Noise texture is inconsistent across the page — possible content from a different source")

    # heatmap artifact
    zn = np.clip(np.abs(z) / (2 * _C.outlier_sigma), 0, 1)
    heat_small = (zn * 255).astype(np.uint8)
    heat = cv2.resize(heat_small, (w, h), interpolation=cv2.INTER_NEAREST)
    heat_color = cv2.applyColorMap(heat, cv2.COLORMAP_JET)
    base = cv2.cvtColor(gray.astype(np.uint8), cv2.COLOR_GRAY2BGR)
    overlay = (base * 0.6 + heat_color * 0.4).astype(np.uint8)
    artifact = Image.fromarray(cv2.cvtColor(overlay, cv2.COLOR_BGR2RGB))

    return SignalResult("noise", True, score, findings, {"noise_map": artifact})
