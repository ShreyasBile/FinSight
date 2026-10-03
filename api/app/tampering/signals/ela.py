"""Stage-2 signal: Error Level Analysis (camera photos / scanned JPEGs only).

Re-compress the image at a known JPEG quality and diff against the original.
Regions that have been pasted in or edited carry a different compression
history and light up brighter than the rest of the page.

Improvements over the reference repo's ela.py:
- fully in-memory (io.BytesIO) — no 'tempresaved.jpg' litter or race between
  concurrent analyses
- returns BOTH a heuristic scalar score and the raw ELA image (the CNN
  consumes the latter as its input representation)

Scoring: mean intensity of the brightest `top_percentile` pixels, normalized.
A uniformly-compressed genuine photo has a flat, dim ELA map; an edited one
has a bright localized region, which concentrates mass in the top percentile.
"""
import io

import numpy as np
from PIL import Image, ImageChops

from app.tampering.config import CFG
from app.tampering.signals.base import SignalResult, unavailable

_C = CFG.signals.ela


def compute_ela_image(image: Image.Image, quality: int | None = None,
                      return_max_diff: bool = False):
    quality = quality or _C.jpeg_quality
    rgb = image.convert("RGB")
    buf = io.BytesIO()
    rgb.save(buf, "JPEG", quality=quality)
    buf.seek(0)
    resaved = Image.open(buf).convert("RGB")
    diff = ImageChops.difference(rgb, resaved)
    # normalize brightness so the map is visible regardless of absolute levels
    extrema = diff.getextrema()
    max_diff = max(ex[1] for ex in extrema) or 1
    arr = np.asarray(diff, dtype=np.float32) * (255.0 / max_diff)
    ela = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    return (ela, max_diff) if return_max_diff else ela


# Below this raw recompression difference (out of 255), the source is so
# lightly compressed that the normalized ELA map is amplified sensor noise,
# not compression-history evidence — validity condition, not a tuning knob.
MIN_MEANINGFUL_DIFF = 8


def run(image: Image.Image) -> SignalResult:
    ela_img, max_diff = compute_ela_image(image, return_max_diff=True)
    if max_diff < MIN_MEANINGFUL_DIFF:
        return unavailable(
            "ela", f"recompression difference too small (max {max_diff}/255) — "
                   "source is near-lossless, ELA map would be amplified noise")
    gray = np.asarray(ela_img.convert("L"), dtype=np.float32)

    # Blockwise outlier analysis, not global intensity: on ANY text document
    # the brightest ELA pixels are just text edges (high-frequency content
    # always re-compresses differently), so a global percentile only measures
    # "this page has text". What betrays tampering is one REGION whose error
    # level is inconsistent with the rest of the page — a robust z-score
    # outlier among block means.
    b = 64
    h, w = gray.shape
    rows, cols = max(h // b, 1), max(w // b, 1)
    block_means = np.zeros((rows, cols), dtype=np.float32)
    for r in range(rows):
        for c in range(cols):
            block_means[r, c] = gray[r * b:(r + 1) * b, c * b:(c + 1) * b].mean()

    med = float(np.median(block_means))
    mad = float(np.median(np.abs(block_means - med)))
    # same degenerate-MAD guard as noise.py: uniform pages can collapse MAD
    # toward 0, which would turn rounding wobble into astronomical z-scores
    scale = max(1.4826 * mad, 0.15 * med, 0.5)
    z = (block_means - med) / scale
    max_z = float(z.max())          # only brighter-than-page matters for ELA
    n_hot = int((z > 3.0).sum())

    # max_z ~2-3 is normal texture variation; 6+ is a strongly inconsistent
    # region. Ramp between, require at least one clearly hot block.
    score = float(np.clip((max_z - 3.0) / 5.0, 0.0, 1.0)) if n_hot else 0.0

    findings = [
        f"{n_hot} block(s) with anomalous error level (max z={max_z:.1f} vs page median)"
    ]
    if score > 0.4:
        findings.append("A localized region compresses very differently from the rest of the page")

    return SignalResult("ela", True, score, findings, {"ela_map": ela_img})
