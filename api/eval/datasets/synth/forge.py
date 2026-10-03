"""Degradations (for preprocessing/extraction robustness) and forgeries (for the
tampering, authenticity and fraud agents), applied to synthetic renders.

Every function returns the new artefact plus a small metadata dict describing
exactly what was done — that metadata is the ground-truth label.
"""
from __future__ import annotations

import io
import random

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from .render import font


# ── Degradations ─────────────────────────────────────────────────────────────
def rotate(im: Image.Image, quarter_turns: int, skew_deg: float = 0.0) -> tuple[Image.Image, dict]:
    """Rotate by k*90 degrees counter-clockwise plus a small skew.
    Ground truth: the clockwise rotation that makes the document upright."""
    out = im.rotate(90 * quarter_turns, expand=True, fillcolor=(235, 235, 235))
    if skew_deg:
        out = out.rotate(skew_deg, expand=True, fillcolor=(235, 235, 235), resample=Image.BICUBIC)
    return out, {"rotation_ccw": (90 * quarter_turns) % 360, "skew_deg": skew_deg}


def on_desk(im: Image.Image, rng: random.Random, tilt: float = 0.12) -> tuple[Image.Image, dict]:
    """Photograph-like: the card sits on a wooden desk under perspective."""
    w, h = im.size
    W, H = int(w * 1.6), int(h * 1.8)
    desk = np.zeros((H, W, 3), dtype=np.uint8)
    base = np.array([rng.randint(110, 150), rng.randint(80, 105), rng.randint(50, 70)])
    grain = np.random.default_rng(rng.randint(0, 10**6)).normal(0, 9, (H, W, 1))
    stripes = (np.sin(np.linspace(0, 40, W)) * 10)[None, :, None]
    desk = np.clip(base + grain + stripes, 0, 255).astype(np.uint8)
    bg = Image.fromarray(desk)
    ox, oy = (W - w) // 2, (H - h) // 2
    jit = lambda v: int(v * tilt * rng.uniform(-1, 1))  # noqa: E731
    quad = [(ox + jit(w), oy + jit(h)), (ox + w + jit(w), oy + jit(h)),
            (ox + w + jit(w), oy + h + jit(h)), (ox + jit(w), oy + h + jit(h))]
    coeffs = _perspective_coeffs(quad, [(0, 0), (w, 0), (w, h), (0, h)])
    warped = im.transform((W, H), Image.PERSPECTIVE, coeffs, Image.BICUBIC)
    mask = Image.new("L", im.size, 255).transform((W, H), Image.PERSPECTIVE, coeffs, Image.BICUBIC)
    bg.paste(warped, (0, 0), mask)
    return bg, {"perspective_quad": quad}


def _perspective_coeffs(src, dst):
    A, B = [], []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); B.append(u)
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y]); B.append(v)
    return np.linalg.solve(np.array(A, dtype=float), np.array(B, dtype=float)).tolist()


def blur(im: Image.Image, radius: float = 1.6) -> tuple[Image.Image, dict]:
    return im.filter(ImageFilter.GaussianBlur(radius)), {"blur_radius": radius}


def low_light(im: Image.Image, factor: float = 0.45) -> tuple[Image.Image, dict]:
    return ImageEnhance.Brightness(im).enhance(factor), {"brightness": factor}


def glare(im: Image.Image, rng: random.Random) -> tuple[Image.Image, dict]:
    arr = np.asarray(im, dtype=np.float32)
    h, w = arr.shape[:2]
    cy, cx = rng.uniform(0.2, 0.8) * h, rng.uniform(0.2, 0.8) * w
    yy, xx = np.mgrid[0:h, 0:w]
    blob = np.exp(-(((yy - cy) / (h * 0.25)) ** 2 + ((xx - cx) / (w * 0.25)) ** 2))[..., None]
    out = np.clip(arr + blob * 120, 0, 255).astype(np.uint8)
    return Image.fromarray(out), {"glare_center": [round(cx), round(cy)]}


def camera_capture(im: Image.Image, rng: random.Random) -> Image.Image:
    """Make a clean render behave like a phone photo of a printed document:
    slight perspective, optical resampling, luminance-correlated sensor noise,
    vignetting and camera JPEG. Without this, genuine synthetic images carry
    perfectly flat colours and pixel-identical repeated glyphs that no real
    photo has — which the pixel forensics (correctly) find strange."""
    w, h = im.size
    j = lambda v: v * rng.uniform(-0.012, 0.012)  # noqa: E731
    quad = [(j(w), j(h)), (w + j(w), j(h)), (w + j(w), h + j(h)), (j(w), h + j(h))]
    coeffs = _perspective_coeffs(quad, [(0, 0), (w, 0), (w, h), (0, h)])
    out = im.transform((w, h), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=(236, 236, 232))
    s = rng.uniform(0.72, 0.85)
    out = out.resize((int(w * s), int(h * s)), Image.BILINEAR).resize((w, h), Image.BICUBIC)
    arr = np.asarray(out, dtype=np.float32)
    g = np.random.default_rng(rng.randint(0, 10**6))
    lum_noise = g.normal(0, rng.uniform(3.0, 5.5), arr.shape[:2])[..., None]
    chroma = g.normal(0, 1.5, arr.shape)
    yy, xx = np.mgrid[0:h, 0:w]
    vign = 1 - 0.18 * (((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2)
    arr = np.clip(arr * vign[..., None] + lum_noise + chroma, 0, 255).astype(np.uint8)
    return recompress(Image.fromarray(arr).filter(ImageFilter.GaussianBlur(0.5)), rng.randint(84, 92))


def recompress(im: Image.Image, quality: int) -> Image.Image:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=quality)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


# ── Image forgeries ──────────────────────────────────────────────────────────
def copy_move(im: Image.Image, src_box: tuple, dst_xy: tuple) -> tuple[Image.Image, dict]:
    """Duplicate a region inside the same image (classic clone-stamp edit)."""
    out = im.copy()
    patch = im.crop(src_box)
    out.paste(patch, dst_xy)
    x0, y0, x1, y1 = src_box
    dst = (dst_xy[0], dst_xy[1], dst_xy[0] + (x1 - x0), dst_xy[1] + (y1 - y0))
    return out, {"forgery": "copy_move", "edited_box": list(dst), "source_box": list(src_box)}


def splice_text(im: Image.Image, box: tuple, new_text: str, size: int,
                bold: bool = True, first_quality: int = 70) -> tuple[Image.Image, dict]:
    """Edit printed text: erase a box with a flat fill and type new text in a
    slightly different font — then the whole image is saved again, so the
    edited region carries a different compression history (what ELA/CNN read)."""
    base = recompress(im, first_quality)            # the 'original' scan's JPEG history
    d = ImageDraw.Draw(base)
    x0, y0, x1, y1 = box
    arr = np.asarray(base)
    ring = np.concatenate([arr[max(0, y0 - 4):y0, x0:x1].reshape(-1, 3),
                           arr[y1:y1 + 4, x0:x1].reshape(-1, 3)])
    fill = tuple(int(v) for v in np.median(ring, axis=0)) if ring.size else (250, 250, 247)
    d.rectangle(box, fill=fill)
    d.text((x0 + 2, y0), new_text, font=font(size, bold), fill=(12, 12, 12))
    return base, {"forgery": "splice_text", "edited_box": list(box), "new_text": new_text}


def photo_swap(card: Image.Image, photo_box: tuple, new_face: Image.Image,
               face_quality: int = 35) -> tuple[Image.Image, dict]:
    """Paste a photo that comes from a different source (heavily recompressed,
    different noise) into the ID card's photo box."""
    x0, y0, x1, y1 = photo_box
    face = recompress(new_face.convert("RGB").resize((x1 - x0, y1 - y0)), face_quality)
    face = face.filter(ImageFilter.GaussianBlur(0.8))
    out = card.copy()
    out.paste(face, (x0, y0))
    return out, {"forgery": "photo_swap", "edited_box": list(photo_box)}


# ── Presentation attacks (liveness) ─────────────────────────────────────────
def print_attack(face: Image.Image, rng: random.Random) -> Image.Image:
    """A face photo PRINTED on paper and held up to the camera: printer
    halftone, washed-out ink gamut, a white paper margin, the sheet slightly
    tilted in front of a background, uneven light, then a phone capture."""
    w, h = face.size
    im = ImageEnhance.Color(face).enhance(rng.uniform(0.6, 0.85))
    im = ImageEnhance.Contrast(im).enhance(rng.uniform(0.75, 0.9))
    arr = np.asarray(im, dtype=np.float32)
    yy, xx = np.mgrid[0:h, 0:w]
    period = rng.choice([3, 4])
    dots = (np.sin(xx * 2 * np.pi / period) * np.sin(yy * 2 * np.pi / period) + 1) / 2
    arr = arr * (1 - 0.10 * dots[..., None]) + 12
    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    m = int(0.08 * w)
    paper = Image.new("RGB", (w + 2 * m, h + 2 * m), (246, 244, 238))
    paper.paste(im, (m, m))
    paper = paper.rotate(rng.uniform(-6, 6), expand=True, fillcolor=(90, 84, 78))
    bg = Image.new("RGB", (int(paper.width * 1.25), int(paper.height * 1.25)),
                   tuple(rng.randint(60, 140) for _ in range(3)))
    bg.paste(paper, ((bg.width - paper.width) // 2, (bg.height - paper.height) // 2))
    arr = np.asarray(bg, dtype=np.float32)
    H, W = arr.shape[:2]
    gy, gx = np.mgrid[0:H, 0:W]
    light = 1 + 0.18 * ((gx / W) - 0.5) * rng.choice([-1, 1])
    arr = np.clip(arr * light[..., None], 0, 255).astype(np.uint8)
    return camera_capture(Image.fromarray(arr), rng)


def screen_attack(face: Image.Image, rng: random.Random) -> Image.Image:
    """A face photo shown on a phone/monitor SCREEN and re-captured: moiré from
    the pixel grid beating against the camera sensor, RGB sub-pixel stripes,
    boosted display gamma, a black bezel and a specular glare band."""
    w, h = face.size
    im = ImageEnhance.Brightness(face).enhance(rng.uniform(1.05, 1.2))
    arr = np.asarray(im, dtype=np.float32)
    yy, xx = np.mgrid[0:h, 0:w]
    ang = rng.uniform(0, np.pi)
    f = rng.uniform(0.18, 0.35)
    moire = np.sin(2 * np.pi * f * (xx * np.cos(ang) + yy * np.sin(ang)))
    arr = arr * (1 + 0.07 * moire[..., None])
    sub = np.zeros_like(arr)
    for c in range(3):
        sub[:, c::3, c] = 10
    arr = arr + sub - 5
    t = (xx + yy) / (w + h)
    band = np.exp(-((t - rng.uniform(0.3, 0.7)) ** 2) / 0.004)
    arr = arr + 70 * band[..., None]
    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    b = int(0.06 * w)
    frame = Image.new("RGB", (w + 2 * b, h + 2 * b), (12, 12, 14))
    frame.paste(im, (b, b))
    bg = Image.new("RGB", (int(frame.width * 1.2), int(frame.height * 1.2)),
                   tuple(rng.randint(40, 110) for _ in range(3)))
    bg.paste(frame.rotate(rng.uniform(-5, 5), expand=True, fillcolor=bg.getpixel((0, 0))),
             (int(frame.width * 0.08), int(frame.height * 0.08)))
    return camera_capture(bg, rng)
