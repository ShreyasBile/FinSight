"""Patch-level forgery detector trained on REAL forged documents: network, input channels, page scoring.

Shared by the tampering signal (signals/patch_cnn.py) and by the training / evaluation script
(eval/real/train_patch_forensics.py), so the two cannot drift apart.

What it is: a small CNN over 48-px patches with three input channels that carry editing traces (grey
level, a high-pass residual, the ELA map), trained on patches around the boxed forged regions of real
forgeries against patches of genuine text - with a second round on the genuine patches the first round
got wrong. A page is scored in one convolutional pass. Scores are logit MARGINS (forged minus genuine),
not probabilities: a probability saturates at 1.0 on many patches of a genuine page and every such
page then ties.
"""
from __future__ import annotations

import io

import numpy as np

PATCH = 48
STRIDE = 16
MAX_PIXELS = 2_500_000          # a larger scan is scored at this size (35-megapixel scans exist)


def features(img) -> np.ndarray:
    """(3, H, W) float32: grey level, high-pass residual, ELA (JPEG q90 difference, normalised)."""
    import cv2
    from PIL import Image, ImageChops
    rgb = img.convert("RGB")
    g = np.asarray(rgb.convert("L"), dtype=np.float32)
    hp = g - cv2.GaussianBlur(g, (5, 5), 0)
    buf = io.BytesIO()
    rgb.save(buf, "JPEG", quality=90)
    buf.seek(0)
    ela = np.asarray(ImageChops.difference(rgb, Image.open(buf).convert("RGB")).convert("L"), dtype=np.float32)
    ela = ela / max(float(ela.max()), 1.0)
    return np.stack([g / 255.0, np.clip(hp / 32.0, -1, 1), ela]).astype(np.float32)


def build_model():
    import torch.nn as nn

    def block(a, b):
        return nn.Sequential(nn.Conv2d(a, b, 3, padding=1), nn.BatchNorm2d(b), nn.ReLU(inplace=True),
                             nn.Conv2d(b, b, 3, padding=1), nn.BatchNorm2d(b), nn.ReLU(inplace=True), nn.MaxPool2d(2))
    return nn.Sequential(block(3, 24), block(24, 48), block(48, 96), nn.AdaptiveAvgPool2d(1), nn.Flatten(),
                         nn.Dropout(0.3), nn.Linear(96, 2))


def fit_size(img):
    """(image to score, factor applied): large scans are scored at MAX_PIXELS."""
    from PIL import Image
    k = 1.0
    if img.width * img.height > MAX_PIXELS:
        k = (MAX_PIXELS / (img.width * img.height)) ** 0.5
        img = img.resize((max(PATCH, int(img.width * k)), max(PATCH, int(img.height * k))), Image.LANCZOS)
    return img, k


def margins(net, f: np.ndarray) -> np.ndarray:
    """Forged-minus-genuine logit of every 48-px window (stride 16), in one pass: the convolutional layers
    run on the whole page and each window's average becomes a 6 x 6 average pool on the 1/8-scale map."""
    import torch
    import torch.nn.functional as F
    _, h, w = f.shape
    if h < PATCH or w < PATCH:
        pad = np.zeros((3, max(h, PATCH), max(w, PATCH)), np.float32)
        pad[:, :h, :w] = f
        f = pad
    with torch.no_grad():
        fm = net[:3](torch.from_numpy(np.ascontiguousarray(f[None])))
        pooled = F.avg_pool2d(fm, kernel_size=PATCH // 8, stride=STRIDE // 8)
        z = net[6](pooled.permute(0, 2, 3, 1))[0]
        return (z[:, :, 1] - z[:, :, 0]).numpy()


def page_stats(m: np.ndarray) -> dict:
    """What a page's margin map says: its hottest window, and how far that stands out from the page's own
    99th percentile (a busy genuine page is 'odd' in many places; an edit is odd in one)."""
    v = np.sort(m.ravel())
    return {"top": float(v[-1]), "excess": float(v[-1] - np.percentile(v, 99))}


def _centre(m: np.ndarray, r: int, c: int) -> tuple[float, float]:
    """Centre (x, y), in scored-image pixels, of the area around window (r, c): the margin-weighted centroid
    of its 3 x 3 neighbourhood (finer than the 16-px window grid)."""
    r0, r1, c0, c1 = max(r - 1, 0), min(r + 2, m.shape[0]), max(c - 1, 0), min(c + 2, m.shape[1])
    sub = m[r0:r1, c0:c1]
    wgt = np.clip(sub - (m[r, c] - 3.0), 0, None)               # only windows within 3 logits of the peak
    ys, xs = np.mgrid[r0:r1, c0:c1]
    return (float((xs * wgt).sum() / wgt.sum()) * STRIDE + PATCH / 2,
            float((ys * wgt).sum() / wgt.sum()) * STRIDE + PATCH / 2)


def hottest(m: np.ndarray) -> tuple[float, float]:
    """Centre (x, y), in scored-image pixels, of the hottest area of a margin map."""
    return _centre(m, *np.unravel_index(int(m.argmax()), m.shape))


def areas(m: np.ndarray, limit: int = 3, apart: int = 3) -> list[tuple[float, float, float]]:
    """The separate hot areas of a map, strongest first: [(x, y, value)], at most `limit`. Two peaks closer
    than `apart` windows (48 px) are one area. On development ID cards the edit was under the strongest
    area on 56% of flagged forgeries and under one of the three strongest on 71%."""
    w, out = m.astype(np.float32).copy(), []
    for _ in range(limit):
        r, c = np.unravel_index(int(w.argmax()), w.shape)
        if not np.isfinite(w[r, c]):
            break
        out.append((*_centre(m, r, c), float(m[r, c])))
        w[max(r - apart, 0):r + apart + 1, max(c - apart, 0):c + apart + 1] = -np.inf
    return out
