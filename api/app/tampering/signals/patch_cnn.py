"""Stage-2 signal: a patch-level forgery detector trained on REAL forged documents.

Why it exists (real-data evaluation, 2026-10-03): the CNN signal was trained on synthetic pages over ELA
maps, and on real documents every pixel signal carried little information (per-signal AUC 0.4-0.67); the
locator, which boxes only values a rule cites, boxed no real forgery. Real forgeries are a few characters
or a field changed in an image editor. This detector was trained on exactly that (patch_model.py): the
development splits of Find it again! (receipts edited by people, the edited boxes marked) and SIDTD
(ID-card templates with fields replaced or repainted).

Per document family - 'page' (receipts, slips, statements) and 'id' (identity cards), the families the
fusion floors use - there are TWO models, each trained on one half of the development documents and
calibrated on the other half, which it never saw:
    statistic   what is read off a page's margin map ('top' = the hottest window, 'excess' = how far it
                stands out from the page's own 99th percentile)
    floor       the value 95% of GENUINE documents stay at or below - for ID cards the higher of flat
                card images and real phone photos of a card, for pages the higher of receipts and forms
    span        how far above the floor a typical caught forgery stands
(Two models, not one trained on everything: two runs of the same recipe give different score scales, so
a floor measured on one model cannot be carried over to another.)

A document is evidence only when BOTH models put it above their own floor. Evidence is 0 otherwise and
0.6-1.0 above; with the fusion's confidence for this signal (0.55) a hit is a MEDIUM - worth a look - and
never HIGH on its own; HIGH needs a second signal. The finding names where ("region [x0, y0, x1, y1]", in
the upload's pixels - up to three separate areas, strongest first) so the locator can draw it. Missing
model files make the signal unavailable (the cascade works without it).
"""
from __future__ import annotations

import json
import os

from app.tampering.signals.base import SignalResult, unavailable
from app.tampering.signals.patch_model import PATCH, areas, build_model, features, fit_size, margins

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "patch_real_v1")

_loaded: dict = {}


def _load(family: str):
    """([(network, {floor, span}), ...], statistic) for a family, or (None, None) without trained models."""
    if family in _loaded:
        return _loaded[family]
    got = (None, None)
    calib_path = os.path.join(MODEL_DIR, f"{family}.json")
    if os.path.isfile(calib_path):
        import torch
        with open(calib_path, encoding="utf-8") as fh:
            calib = json.load(fh)
        nets = []
        for name, c in sorted(calib["models"].items()):
            path = os.path.join(MODEL_DIR, f"{family}_{name}.pt")
            if not os.path.isfile(path):
                nets = []
                break
            net = build_model()
            net.load_state_dict(torch.load(path, map_location="cpu", weights_only=True))
            net.eval()
            nets.append((net, c))
        if nets:
            got = (nets, calib["statistic"])
    _loaded[family] = got
    return got


def evidence(over: float) -> float:
    """`over` = how far above its floor the weaker of the two models puts the page, in spans.
    0 up to the floor; 0.6 just above it, rising to 1.0 one span above."""
    return 0.0 if over <= 0 else 0.6 + 0.4 * min(1.0, over)


def measure(image, doc_type: str = "generic") -> dict | None:
    """What the two models read off the page, or None without trained models:
    over    per model, how far the page stands above that model's own genuine floor, in spans (<= 0: not above)
    boxes   [[x0, y0, x1, y1], ...] in the upload's pixels: the separate areas where the two models, averaged,
            are above their floors - strongest first, at most three; always at least the strongest area"""
    import numpy as np
    from app.tampering.fusion import family_of
    family = family_of(doc_type)
    nets, stat = _load(family)
    if not nets:
        return None
    img, k = fit_size(image.convert("RGB"))
    f = features(img)
    z = []                                   # per model: every window, in spans above that model's floor
    for net, c in nets:
        m = margins(net, f)
        ref = float(np.percentile(m, 99)) if stat == "excess" else 0.0
        z.append((m - ref - float(c["floor"])) / max(float(c["span"]), 1e-6))
    half = PATCH / 2
    boxes = [[int(max(x - half, 0) / k), int(max(y - half, 0) / k), int((x + half) / k), int((y + half) / k)]
             for i, (x, y, v) in enumerate(areas(sum(z) / len(z))) if i == 0 or v > 0]
    return {"family": family, "over": [round(float(m.max()), 3) for m in z], "boxes": boxes}


def verdict(m: dict) -> SignalResult:
    """The signal from a measurement: evidence only when BOTH models put the page above their own floor."""
    score = evidence(min(m["over"]))
    kind = "ID cards" if m["family"] == "id" else "receipts"
    if score > 0:
        where = " and ".join(f"region {b}" for b in m["boxes"])
        finding = (f"Looks edited at {where} - more unlike the rest of the page than anything on 95% of "
                   f"genuine documents (two models trained on real forged {kind} agree)")
    else:
        finding = f"No area stands out as edited (models trained on real forged {kind})"
    return SignalResult("patch_cnn", True, round(score, 4), [finding])


def run(image, doc_type: str = "generic") -> SignalResult:
    m = measure(image, doc_type)
    if m is None:
        from app.tampering.fusion import family_of
        return unavailable("patch_cnn", f"no trained model for the '{family_of(doc_type)}' family")
    return verdict(m)
