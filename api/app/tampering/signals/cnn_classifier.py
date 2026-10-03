"""Stage-2 signal: patch-based CNN tamper classifier + localization heatmap.

The model is TRAINED on 128x128 native-resolution ELA crops (see
training/dataset.py for why whole-page resize fails on small edits), so
inference slides the same 128px window across the page's ELA map and scores
every patch in ONE batched forward pass. Page-level evidence comes from the
hottest region; the full patch-probability grid becomes a real localization
heatmap (unlike the reference repo, which scored patches with a model that
had only ever seen whole pages).

Outputs:
- evidence score: hottest patch probability through a calibrated floor
  (chosen from measured clean/tampered distributions on held-out pages —
  scanning ~80 windows inflates every page's max, so only genuinely extreme
  patches count as evidence)
- MC-Dropout uncertainty on the hottest patch (cheap, targeted)
- localization heatmap artifact overlaid on the original document

Loads whatever model the registry marks LIVE; reports unavailable when there
is none — the pipeline works on day one and improves as the loop feeds it.
"""
import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

from app.tampering.config import CFG
from app.tampering.feedback.registry import ModelRegistry
from app.tampering.signals.base import SignalResult, unavailable
from app.tampering.signals.cnn_model import TamperCNN

PATCH = 128
STRIDE = 96
MC_PASSES = 12
UNCERTAINTY_THRESHOLD = 0.15

_cache = {"version": None, "model": None, "calibration": None}

# Fallback calibration if a live model has none stored (e.g. an old model).
# Chosen so a raw max around ~0.96 maps to ~0.5 evidence — deliberately
# conservative; real models should carry their own fitted calibration.
_DEFAULT_CALIBRATION = (9.0, -8.6)


def _load_live_model():
    registry = ModelRegistry()
    live = registry.get_live()
    if live is None:
        return None, None, None
    if _cache["version"] == live["version"]:
        return _cache["model"], live["version"], _cache["calibration"]
    model = TamperCNN()
    state = torch.load(registry.weights_path(live["version"]), map_location="cpu",
                       weights_only=True)
    model.load_state_dict(state)
    model.eval()
    calib = tuple(live.get("cnn_calibration") or _DEFAULT_CALIBRATION)
    _cache.update(version=live["version"], model=model, calibration=calib)
    return model, live["version"], calib


def _patch_grid(ela_arr: np.ndarray):
    """All (x, y) crop origins covering the page, plus the grid shape."""
    h, w = ela_arr.shape[:2]
    xs = list(range(0, max(w - PATCH, 0) + 1, STRIDE)) or [0]
    ys = list(range(0, max(h - PATCH, 0) + 1, STRIDE)) or [0]
    # make sure the right/bottom edges are covered
    if xs[-1] != w - PATCH and w > PATCH:
        xs.append(w - PATCH)
    if ys[-1] != h - PATCH and h > PATCH:
        ys.append(h - PATCH)
    return xs, ys


def score_image(model: TamperCNN, ela_image: Image.Image):
    """Batched sliding-window scoring. Returns (patch_probs, xs, ys) where
    patch_probs has shape (len(ys), len(xs))."""
    arr = np.asarray(ela_image.convert("RGB"), dtype=np.float32) / 255.0
    h, w = arr.shape[:2]
    if h < PATCH or w < PATCH:
        # tiny image: pad to one patch
        pad = np.zeros((max(h, PATCH), max(w, PATCH), 3), dtype=np.float32)
        pad[:h, :w] = arr
        arr = pad
    xs, ys = _patch_grid(arr)
    crops = [arr[y:y + PATCH, x:x + PATCH] for y in ys for x in xs]
    batch = torch.from_numpy(np.stack(crops)).permute(0, 3, 1, 2)
    with torch.no_grad():
        probs = F.softmax(model(batch), dim=1)[:, 1].numpy()
    return probs.reshape(len(ys), len(xs)), xs, ys


def run(ela_image: Image.Image, original: Image.Image) -> SignalResult:
    model, version, calib = _load_live_model()
    if model is None:
        return unavailable(
            "cnn", "no live model in the registry yet — train one (training/train.py) "
                   "and promote it (training/evaluate.py --promote)")

    patch_probs, xs, ys = score_image(model, ela_image)
    flat = np.sort(patch_probs.ravel())[::-1]
    max_p = float(flat[0])
    # PER-MODEL calibrated evidence: evidence = sigmoid(w*max + b), with (w,b)
    # fitted on held-out pages at promotion time (training/calibrate.py) and
    # stored in the registry. This replaced a single hardcoded 0.96 floor,
    # which only suited one model's score range — a conservative model whose
    # forged-max is ~0.64 would score ~0 evidence under that floor. The
    # logistic preserves ranking (unlike a hard clip) while staying a
    # calibrated probability, so fusion gets an honest, model-agnostic signal.
    w, b = calib
    evidence = float(1.0 / (1.0 + np.exp(-(w * max_p + b))))

    # MC-Dropout uncertainty, only on the hottest patch (targeted + cheap)
    iy, ix = np.unravel_index(int(patch_probs.argmax()), patch_probs.shape)
    arr = np.asarray(ela_image.convert("RGB"), dtype=np.float32) / 255.0
    y0, x0 = ys[iy], xs[ix]
    hot = torch.from_numpy(arr[y0:y0 + PATCH, x0:x0 + PATCH].copy()) \
        .permute(2, 0, 1).unsqueeze(0)
    model.train()
    for m in model.modules():
        if isinstance(m, torch.nn.BatchNorm2d):
            m.eval()  # only dropout should be stochastic
    mc = []
    with torch.no_grad():
        for _ in range(MC_PASSES):
            mc.append(float(F.softmax(model(hot), dim=1)[0, 1]))
    model.eval()
    mc_std = float(np.std(mc))

    n_hot = int((patch_probs > 0.9).sum())
    findings = [
        f"Model {version}: {patch_probs.size} patches scanned, hottest {max_p:.3f} "
        f"-> evidence {evidence:.2f} ({n_hot} patches over 0.90)"
    ]
    if mc_std >= UNCERTAINTY_THRESHOLD:
        findings.append(f"High uncertainty on the hottest region (±{mc_std:.2f}) — "
                        "treat automated score with caution, manual review advised")

    # localization heatmap over the original document
    import cv2
    w, h = original.size
    heat = cv2.resize(patch_probs.astype(np.float32), (w, h),
                      interpolation=cv2.INTER_CUBIC)
    heat = np.clip(heat, 0, 1)
    heat_color = cv2.applyColorMap((heat * 255).astype(np.uint8), cv2.COLORMAP_JET)
    base = cv2.cvtColor(np.asarray(original.convert("RGB")), cv2.COLOR_RGB2BGR)
    alpha = CFG.signals.cnn.cam_alpha
    overlay = (base * (1 - alpha) + heat_color * alpha).astype(np.uint8)
    artifact = Image.fromarray(cv2.cvtColor(overlay, cv2.COLOR_BGR2RGB))

    return SignalResult("cnn", True, evidence, findings, {"tamper_localization": artifact})
