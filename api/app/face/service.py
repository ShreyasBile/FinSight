"""Cross-document face verification — one input to the future fraud-detection
module (face match + tampering + field cross-check + field validation).

Fully local and deterministic, no new dependencies:
  * detection  — OpenCV YuNet (bbox + 5 landmarks + confidence). The document is
    tried at 0/90/180/270 degrees and the rotation with the strongest detection
    wins, so a sideways-scanned card is both FOUND and its orientation REPORTED.
  * embedding  — OpenCV SFace (128-d), compared with cosine similarity. The
    officially validated decision threshold for SFace is 0.363 (same person
    when >= 0.363); scores just under it are reported as "uncertain" rather
    than forced into a yes/no.

Like the tampering cascade this is evidence for a human, not an auto-reject:
ID-card photos are small, old, and heavily compressed, so the verdict bands are
deliberately conservative and every raw score is returned for the reviewer.
"""
import base64
import io
import logging
import math
import threading

log = logging.getLogger("finsight.face")

_MODELS_DIR = __file__.rsplit("service.py", 1)[0] + "models"
_DET_MODEL = f"{_MODELS_DIR}/face_detection_yunet_2023mar.onnx"
_REC_MODEL = f"{_MODELS_DIR}/face_recognition_sface_2021dec.onnx"

# SFace official cosine threshold: >= 0.363 means same person. Below that we
# keep an "uncertain" band before calling two faces different — document
# photos are low quality and a hard boundary would overclaim.
_MATCH_T = 0.363
_DIFFERENT_T = 0.25

_DETECT_MAX_DIM = 1280   # detector input cap (long edge)
_MAX_FACES_PER_DOC = 4
_MIN_DET_SCORE = 0.6     # ID-card photos are small; YuNet's default 0.9 misses them

# OpenCV model objects are not thread-safe. Re-entrant because the public
# helpers below take it and are also called from inside analyze_document's own
# critical section. EVERY use of _det/_rec — including the tampering
# photo_region signal, which reuses this detector — must hold it: the review
# step runs /face/verify and per-document /document/integrity concurrently.
_lock = threading.RLock()
_det = None
_rec = None

# public aliases for other modules (photo_region) — never import the privates
DET_MODEL = _DET_MODEL
DETECT_MAX_DIM = _DETECT_MAX_DIM


def _models():
    global _det, _rec
    import cv2
    with _lock:
        if _det is None:
            _det = cv2.FaceDetectorYN.create(_DET_MODEL, "", (320, 320),
                                             _MIN_DET_SCORE, 0.3, 5000)
            _rec = cv2.FaceRecognizerSF.create(_REC_MODEL, "")
    return _det, _rec


def detect_best_rotation(img_bgr):
    """Thread-safe public wrapper of _best_rotation: (k, rows, best_score)."""
    with _lock:
        return _best_rotation(img_bgr)


def rotate(img_bgr, k):
    """Public alias of _rotate (pure function, no model state)."""
    return _rotate(img_bgr, k)


def embed_faces(file_bytes: bytes) -> list[dict]:
    """Faces on one document WITH their 128-d embeddings (numpy arrays) — for
    the dedupe agent's face index. Same detection path as analyze_document."""
    res = analyze_document(file_bytes)
    return [f for f in res.get("faces", []) if f.get("embedding") is not None]


def similarity(emb_a, emb_b) -> float:
    """Thread-safe SFace cosine similarity between two embeddings."""
    with _lock:
        return _similarity(emb_a, emb_b)


def _detect(img_bgr):
    """YuNet detection on one image. Returns Nx15 rows (bbox, 5 landmarks, score)."""
    import cv2
    det, _ = _models()
    h, w = img_bgr.shape[:2]
    scale = min(1.0, _DETECT_MAX_DIM / max(h, w))
    if scale < 1.0:
        small = cv2.resize(img_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    else:
        small = img_bgr
    det.setInputSize((small.shape[1], small.shape[0]))
    _, faces = det.detect(small)
    if faces is None:
        return []
    out = []
    for row in faces:
        row = row.copy()
        row[:14] /= scale  # map bbox+landmarks back to full resolution
        out.append(row)
    return out


def _rotate(img_bgr, k):
    """Rotate image by k*90 degrees clockwise."""
    import cv2
    if k % 4 == 0:
        return img_bgr
    code = {1: cv2.ROTATE_90_CLOCKWISE, 2: cv2.ROTATE_180,
            3: cv2.ROTATE_90_COUNTERCLOCKWISE}[k % 4]
    return cv2.rotate(img_bgr, code)


def _enhance(img_bgr):
    """Contrast recovery for washed-out ID photos (CLAHE + gamma darkening).
    Measured on the real overexposed PAN card: the face goes from undetectable
    at threshold 0.3 to detected at score 0.76."""
    import cv2
    import numpy as np
    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    cl = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(l)
    enh = cv2.cvtColor(cv2.merge((cl, a, b)), cv2.COLOR_LAB2BGR)
    lut = np.array([((i / 255.0) ** 2.5) * 255 for i in range(256)], dtype=np.uint8)
    return cv2.LUT(enh, lut)


def _best_rotation(img_bgr):
    """Try all four orientations; return (k, rows, best_score) for the rotation
    whose strongest face detection wins."""
    best_k, best_rows, best_score = 0, [], 0.0
    for k in range(4):
        rows = _detect(_rotate(img_bgr, k))
        top = max((float(r[14]) for r in rows), default=0.0)
        if top > best_score:
            best_k, best_rows, best_score = k, rows, top
    return best_k, best_rows, best_score


def _thumb(img_bgr, bbox, max_dim=140) -> str | None:
    """Face crop (with margin) as a small JPEG data URI for the review UI."""
    import cv2
    try:
        x, y, w, h = [int(v) for v in bbox]
        H, W = img_bgr.shape[:2]
        mx, my = int(w * 0.25), int(h * 0.25)
        x0, y0 = max(0, x - mx), max(0, y - my)
        x1, y1 = min(W, x + w + mx), min(H, y + h + my)
        crop = img_bgr[y0:y1, x0:x1]
        if crop.size == 0:
            return None
        s = max_dim / max(crop.shape[:2])
        if s < 1.0:
            crop = cv2.resize(crop, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            return None
        return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()
    except Exception:  # noqa: BLE001 - a missing thumbnail is not fatal
        return None


def analyze_document(file_bytes: bytes) -> dict:
    """Find faces in one document (any of the 4 orientations) and embed them.

    Returns {"faces": [{bbox, score, rotation_to_upright, roll_degrees, thumb,
    embedding}], "error": str|None}. `rotation_to_upright` is the clockwise
    rotation (0/90/180/270) that makes the face upright — i.e. how the face is
    oriented inside the document.
    """
    import cv2
    import numpy as np
    from app.imaging import load_image as _load_image  # PDF scan/page or raw image

    try:
        pil = _load_image(file_bytes)
        if pil is None:
            return {"faces": [], "error": "could not read the document image"}
        img = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
    except Exception as exc:  # noqa: BLE001
        return {"faces": [], "error": f"could not read the document image ({type(exc).__name__})"}

    with _lock:
        # pass 1: the document as uploaded; pass 2: contrast-enhanced fallback
        # for washed-out ID photos. A face only found after enhancement is
        # marked low quality — it may confirm a match but never assert a
        # confident mismatch (see verify()).
        enhanced_used = False
        best_k, best_rows, _ = _best_rotation(img)
        if not best_rows:
            eimg = _enhance(img)
            best_k, best_rows, _ = _best_rotation(eimg)
            if best_rows:
                img, enhanced_used = eimg, True
        if not best_rows:
            return {"faces": [], "error": None}

        upright = _rotate(img, best_k)
        _, rec = _models()
        best_rows.sort(key=lambda r: float(r[14]), reverse=True)
        faces = []
        for row in best_rows[:_MAX_FACES_PER_DOC]:
            x, y, w, h = row[:4]
            score = float(row[14])
            # roll: angle of the eye line (landmarks: right eye @4,5 left eye @6,7)
            roll = math.degrees(math.atan2(float(row[7] - row[5]), float(row[6] - row[4])))
            emb = None
            try:
                aligned = rec.alignCrop(upright, row)
                emb = rec.feature(aligned).flatten()
            except Exception:  # noqa: BLE001 - keep the detection even if embedding fails
                log.exception("face: embedding failed for one face")
            faces.append({
                "bbox": [int(x), int(y), int(w), int(h)],
                "score": round(score, 3),
                "rotation_to_upright": (4 - best_k) % 4 * 90,
                "roll_degrees": round(roll, 1),
                "quality": "low" if (enhanced_used or score < 0.7) else "ok",
                "thumb": _thumb(upright, (x, y, w, h)),
                "embedding": emb,
            })
        return {"faces": faces, "error": None}


def _similarity(f1, f2) -> float:
    import cv2
    _, rec = _models()
    return float(rec.match(f1, f2, cv2.FaceRecognizerSF_FR_COSINE))


def verify(documents: list[tuple[str, bytes]], return_embeddings: bool = False) -> dict:
    """Cross-document face verification.

    documents: [(doc_type, file_bytes), ...]
    Returns the JSON-ready structure for /face/verify (see schemas.FaceVerifyResult).
    With return_embeddings=True the result also carries "_embeddings":
    {doc_type: [[128 floats], ...]} for the dedupe agent's face index — the
    caller must pop it before returning the result to a client.
    """
    per_doc = []
    for doc_type, data in documents:
        res = analyze_document(data)
        per_doc.append({"doc_type": doc_type, **res})

    # pairwise: best-matching face pair between each pair of documents that
    # both have at least one embedded face
    with_faces = [d for d in per_doc
                  if any(f.get("embedding") is not None for f in d["faces"])]
    pairs = []
    for i in range(len(with_faces)):
        for j in range(i + 1, len(with_faces)):
            a, b = with_faces[i], with_faces[j]
            best, best_fa, best_fb = None, None, None
            with _lock:
                for fa in a["faces"]:
                    if fa.get("embedding") is None:
                        continue
                    for fb in b["faces"]:
                        if fb.get("embedding") is None:
                            continue
                        s = _similarity(fa["embedding"], fb["embedding"])
                        if best is None or s > best:
                            best, best_fa, best_fb = s, fa, fb
            if best is None:
                continue
            low_quality = best_fa["quality"] == "low" or best_fb["quality"] == "low"
            note = None
            if best >= _MATCH_T:
                verdict = "match"
            elif best < _DIFFERENT_T and not low_quality:
                verdict = "different"
            elif best < _DIFFERENT_T:
                # a face that was barely detectable must not be able to assert
                # a confident mismatch — the low score may be the photo, not
                # the person
                verdict = "uncertain"
                note = ("photo quality on one document is too poor for a confident "
                        "mismatch — a human should compare the faces")
            else:
                verdict = "uncertain"
            pairs.append({"a": a["doc_type"], "b": b["doc_type"],
                          "similarity": round(best, 3), "verdict": verdict,
                          "note": note, "low_quality": low_quality})

    if len(with_faces) < 2:
        overall = "insufficient"
        summary = ("Only one document with a detectable face was provided — nothing to compare."
                   if len(with_faces) == 1 else
                   "No document contained a detectable face.")
    elif any(p["verdict"] == "different" for p in pairs):
        overall = "mismatch"
        bad = [p for p in pairs if p["verdict"] == "different"]
        summary = (f"The face on {bad[0]['a']} does not match the face on {bad[0]['b']} "
                   f"(similarity {bad[0]['similarity']:.2f}) — the documents may belong "
                   "to different people. A human must verify before approval.")
    elif all(p["verdict"] == "match" for p in pairs):
        overall = "match"
        summary = (f"The same person appears on all {len(with_faces)} documents with a "
                   "detectable face.")
    else:
        overall = "uncertain"
        summary = ("Face similarity between some documents is inconclusive (photo quality "
                   "or age difference) — worth a quick human look.")

    # strip embeddings before returning (numpy arrays, not JSON-serializable)
    embeddings: dict[str, list] = {}
    for d in per_doc:
        for f in d["faces"]:
            emb = f.pop("embedding", None)
            if return_embeddings and emb is not None:
                embeddings.setdefault(d["doc_type"], []).append(
                    [round(float(v), 6) for v in emb])
        d["face_count"] = len(d["faces"])

    result = {"documents": per_doc, "pairs": pairs, "overall": overall, "summary": summary}
    if return_embeddings:
        result["_embeddings"] = embeddings
    return result
