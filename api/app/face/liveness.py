"""Passive liveness for the wizard's selfie step.

Two questions, both answered locally without any model service:

1. Is this a live face, or a photo of a printout / a screen (a presentation
   attack)? — MiniFASNetV2 (crop scale 2.7) + MiniFASNetV1SE (crop scale 4.0)
   from minivision-ai/Silent-Face-Anti-Spoofing (Apache-2.0), run exactly as
   upstream: YuNet face box → scaled crop → 80×80 BGR → softmax; the two
   models' probabilities are averaged and class 1 is "real".
2. Is it the same person as on the ID card? — SFace similarity against the ID
   photo embeddings already on the case, with the same thresholds Face
   Verification uses (>= 0.363 same person; < 0.25 different; between =
   uncertain). A low-quality face never asserts a mismatch.

The result is advisory: a failed check asks for a retake or an officer's
look — it never rejects an application.
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import numpy as np

_DIR = Path(__file__).resolve().parent / "models" / "antispoof"
_MODELS = {  # file: (scale, constructor, sha256)
    "2.7_80x80_MiniFASNetV2.pth": (2.7, "MiniFASNetV2",
                                   "a5eb02e1843f19b5386b953cc4c9f011c3f985d0ee2bb9819eea9a142099bec0"),
    "4_0_0_80x80_MiniFASNetV1SE.pth": (4.0, "MiniFASNetV1SE",
                                       "84ee1d37d96894d5e82de5a57df044ef80a58be2b218b5ed7cdfd875ec2f5990"),
}
LIVE_T = 0.5             # mean "real" probability; calibrated in eval/agents/ops_eval.eval_liveness
_lock = threading.Lock()
_nets: list | None = None


def _load():
    global _nets
    with _lock:
        if _nets is not None:
            return _nets
        import torch
        from . import minifasnet
        nets = []
        for fname, (scale, ctor, sha) in _MODELS.items():
            path = _DIR / fname
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != sha:
                raise RuntimeError(f"anti-spoof weights {fname} failed the integrity check")
            state = torch.load(path, map_location="cpu", weights_only=True)
            state = {k[7:] if k.startswith("module.") else k: v for k, v in state.items()}
            net = getattr(minifasnet, ctor)(conv6_kernel=(5, 5))
            net.load_state_dict(state)
            net.eval()
            nets.append((scale, net))
        _nets = nets
        return nets


def _crop(img, bbox, scale: float, out: int = 80):
    """Upstream CropImage: a box `scale` times the face, centred on it and
    shifted (not clipped) to stay inside the image."""
    import cv2
    h_img, w_img = img.shape[:2]
    x, y, w, h = bbox
    scale = min((h_img - 1) / h, (w_img - 1) / w, scale)
    nw, nh = w * scale, h * scale
    cx, cy = x + w / 2, y + h / 2
    x0, y0 = cx - nw / 2, cy - nh / 2
    x1, y1 = cx + nw / 2, cy + nh / 2
    if x0 < 0:
        x1, x0 = x1 - x0, 0
    if y0 < 0:
        y1, y0 = y1 - y0, 0
    if x1 > w_img - 1:
        x0, x1 = x0 - (x1 - w_img + 1), w_img - 1
    if y1 > h_img - 1:
        y0, y1 = y0 - (y1 - h_img + 1), h_img - 1
    patch = img[int(y0):int(y1) + 1, int(x0):int(x1) + 1]
    return cv2.resize(patch, (out, out))


def real_score(img_bgr, bbox) -> dict:
    import torch
    import torch.nn.functional as F
    probs = []
    for scale, net in _load():
        patch = _crop(img_bgr, bbox, scale)
        t = torch.from_numpy(patch.transpose(2, 0, 1).copy()).float().unsqueeze(0)   # raw 0-255 BGR, as upstream
        with torch.no_grad():
            p = F.softmax(net(t), dim=1)[0].numpy()
        probs.append(p)
    mean = np.mean(probs, axis=0)
    return {"real": float(mean[1]), "print": float(mean[0]), "replay": float(mean[2]),
            "per_model": [round(float(p[1]), 4) for p in probs]}


def check(selfie: bytes, id_embeddings: list | None = None) -> dict:
    """{verdict, live, live_score, faces, match, quality, notes}. verdict ∈
    live_match | live_uncertain_match | live_no_reference | live_mismatch |
    spoof_suspected | no_face | multiple_faces | unreadable."""
    import cv2
    from app.imaging import load_image
    from . import service
    try:
        pil = load_image(selfie)
        img = cv2.cvtColor(np.array(pil.convert("RGB")), cv2.COLOR_RGB2BGR)
    except Exception as exc:  # noqa: BLE001
        return {"verdict": "unreadable", "live": None, "notes": [f"could not read the image ({type(exc).__name__})"]}
    k, rows, _ = service.detect_best_rotation(img)
    if not rows:
        return {"verdict": "no_face", "live": None, "faces": 0,
                "notes": ["No face found — ask for a retake in good light, face centred."]}
    upright = service.rotate(img, k)
    rows = sorted(rows, key=lambda r: float(r[14]), reverse=True)
    big = [r for r in rows if r[2] * r[3] >= 0.25 * rows[0][2] * rows[0][3]]
    if len(big) > 1:
        return {"verdict": "multiple_faces", "live": None, "faces": len(big),
                "notes": ["More than one face in the selfie — only the applicant should be in frame."]}
    row = rows[0]
    bbox = [int(v) for v in row[:4]]
    det_score = float(row[14])
    face_frac = (bbox[2] * bbox[3]) / float(upright.shape[0] * upright.shape[1])
    blur = float(cv2.Laplacian(cv2.cvtColor(upright, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
    low_quality = det_score < 0.7 or face_frac < 0.02 or blur < 20
    spoof = real_score(upright, bbox)
    live = spoof["real"] >= LIVE_T
    match = None
    if id_embeddings:
        emb = None
        faces = service.analyze_document(selfie).get("faces") or []
        if faces and faces[0].get("embedding") is not None:
            emb = faces[0]["embedding"]
        if emb is not None:
            sims = [service.similarity(np.asarray(emb, dtype=np.float32).reshape(1, -1),
                                       np.asarray(e, dtype=np.float32).reshape(1, -1)) for e in id_embeddings]
            best = max(sims)
            if best >= service._MATCH_T:
                mv = "match"
            elif best < service._DIFFERENT_T and not low_quality:
                mv = "mismatch"
            else:
                mv = "uncertain"
            match = {"verdict": mv, "similarity": round(best, 3), "references": len(id_embeddings)}
    if not live:
        verdict = "spoof_suspected"
    elif match is None:
        verdict = "live_no_reference"
    else:
        verdict = {"match": "live_match", "mismatch": "live_mismatch", "uncertain": "live_uncertain_match"}[match["verdict"]]
    notes = []
    if not live:
        notes.append("The selfie looks like a photo of a printout or a screen — ask for a live retake.")
    if low_quality:
        notes.append("Low image quality (small, blurred or faint face) — a mismatch is not asserted.")
    return {"verdict": verdict, "live": live, "live_score": round(spoof["real"], 4),
            "attack_scores": {"print": round(spoof["print"], 4), "replay": round(spoof["replay"], 4)},
            "per_model_real": spoof["per_model"], "faces": 1, "match": match,
            "quality": {"detector_score": round(det_score, 3), "face_fraction": round(face_frac, 4),
                        "sharpness": round(blur, 1), "low": low_quality},
            "notes": notes}
