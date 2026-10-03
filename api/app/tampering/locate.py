"""OCR-grounded issue localization — draws WHERE each already-detected finding
is on the document.

IMPORTANT — like explain.py, this does NOT detect anything. Detection is 100%
deterministic (the cascade in pipeline.py). This module takes the concrete
findings that cascade already produced, pulls the specific VALUES they cite
(the numbers and the quoted IDs/formats — e.g. "gross 156,400", PAN
'ABCDE1234F'), runs OCR over the document to find where those exact values are
printed, and draws a numbered box around each. No LLM, no new judgement: a
finding can only be located if the value it names is literally on the page.

Findings with no on-page value (a metadata/producer concern like "made by
jsPDF") simply can't be boxed — they are returned as "not visually locatable",
which is the honest answer: the problem is in the file's data, not on the page.
"""
import base64
import io
import logging
import re

# Shared loaders moved to app/imaging.py; re-exported under the old private
# names so existing importers keep working.
from ..imaging import NUM_RE as _NUM_RE
from ..imaging import load_image as _load_image
from ..imaging import norm_alnum as _norm_alnum
from ..imaging import num_value as _num_value
from ..imaging import ocr_tokens as _ocr_tokens

log = logging.getLogger("finsight.tampering")

# skip the non-findings (same filter explain.py uses), the passed-rule lines,
# the fusion meta-lines, and the authenticity HEADLINE (its concrete reasons
# still get their own numbered rows — the headline itself locates nothing)
_SKIP = ("skipped:", "Risk driven by", "All ", "no applicable rules",
         "AI authenticity check")

# quoted values a finding cites verbatim: PAN 'ABCDE1234F', IFSC 'SBIN0001234'
_QUOTED_RE = re.compile(r"['\"]([^'\"]{3,})['\"]")
# a pixel detector that says WHERE it saw the edit (patch_cnn: "region [x0, y0, x1, y1]", in the
# uploaded image's pixels) - drawn as stated, no OCR needed
_REGION_RE = re.compile(r"region \[(\d+), (\d+), (\d+), (\d+)\]")


def region_boxes(finding: str, scale: float = 1.0) -> list[tuple]:
    """Boxes a finding states in the upload's pixel coordinates, scaled to the rendered image."""
    return [tuple(int(int(v) * scale) for v in m.groups()) for m in _REGION_RE.finditer(finding)]


def _clean_finding(text: str) -> str:
    """Strip the internal [signal] prefix and the FAILED: marker for display."""
    t = text.split("] ", 1)[-1] if text.startswith("[") else text
    if t.startswith("FAILED: "):
        t = t[len("FAILED: "):]
    return t.strip()


def _anchors(finding: str) -> tuple[set[float], set[str]]:
    """The concrete values a finding cites: numeric values (>= 100, so we don't
    box rule counts / small indices) and quoted alphanumeric IDs."""
    nums: set[float] = set()
    for m in _NUM_RE.findall(finding):
        v = _num_value(m)
        if v is not None and v >= 100:
            nums.add(round(v, 2))
    quoted = {q.strip().upper() for q in _QUOTED_RE.findall(finding) if q.strip()}
    return nums, quoted


def _match(nums: set[float], quoted: set[str], tokens: list[dict]) -> list[tuple]:
    """OCR boxes on the page that print one of the finding's cited values."""
    out = []
    for t in tokens:
        hit = False
        if t["value"] is not None and round(t["value"], 2) in nums:
            hit = True
        elif quoted and t["alnum"]:
            ta = t["alnum"]
            for q in quoted:
                qa = _norm_alnum(q)
                if not qa or len(qa) < 4:
                    continue
                # exact match, or the token fully contains the cited value, or the
                # token is a SUBSTANTIAL part of it (>=6 chars) — the length floor
                # stops a short token like an employee id '1234' matching a longer
                # cited account number '123456789'
                if qa == ta or qa in ta or (ta in qa and len(ta) >= 6):
                    hit = True
                    break
        if hit:
            out.append(t["box"])
    return out


def _font(size: int):
    from PIL import ImageFont
    for name in ("arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


# tier -> box colour (RGB)
_TIER_RGB = {"HIGH": (220, 38, 38), "MEDIUM": (217, 119, 6)}


def _draw(pil_image, located: list[dict], tier: str):
    """Draw a numbered box for every located finding; return (annotated, boxes-drawn)."""
    from PIL import ImageDraw

    im = pil_image.copy()
    draw = ImageDraw.Draw(im)
    W, H = im.size
    lw = max(3, W // 350)
    color = _TIER_RGB.get(tier, _TIER_RGB["MEDIUM"])
    font = _font(max(15, W // 55))
    drawn = 0

    for item in located:
        n = item["n"]
        for (x0, y0, x1, y1) in item["boxes"]:
            pad = lw + 1
            draw.rectangle([x0 - pad, y0 - pad, x1 + pad, y1 + pad], outline=color, width=lw)
            # number badge at the top-left of the box
            label = str(n)
            tb = draw.textbbox((0, 0), label, font=font)
            tw, th = tb[2] - tb[0], tb[3] - tb[1]
            bw, bh = tw + 10, th + 8
            bx, by = x0 - pad, max(0, y0 - pad - bh)
            draw.rectangle([bx, by, bx + bw, by + bh], fill=color)
            draw.text((bx + 5, by + 3), label, fill=(255, 255, 255), font=font)
            drawn += 1
    return im, drawn


def _encode(pil_image, max_dim: int = 1100) -> str:
    w, h = pil_image.size
    if max(w, h) > max_dim:
        s = max_dim / max(w, h)
        pil_image = pil_image.resize((int(w * s), int(h * s)))
    buf = io.BytesIO()
    pil_image.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def locate(tampering: dict, file_bytes: bytes) -> dict | None:
    """Return {annotations, annotated_image|None, located_count, ocr_tokens} or None.

    Never raises. `annotations` is one row per concrete finding:
      {n, text, located: bool}
    Boxes are baked into `annotated_image`; the number on each box matches `n`.
    """
    if tampering.get("risk_tier") not in ("MEDIUM", "HIGH"):
        return None
    findings = [
        _clean_finding(e) for e in tampering.get("explanations", [])
        if not any(sk in e for sk in _SKIP)
    ]
    findings = [f for f in findings if f][:8]
    if not findings:
        return None

    try:
        im = _load_image(file_bytes)
        if im is None:
            return None
        tokens = _ocr_tokens(im)
        try:
            from PIL import Image
            scale = im.size[0] / Image.open(io.BytesIO(file_bytes)).size[0]
        except Exception:  # noqa: BLE001 - a PDF: rendered pages carry no stated regions
            scale = None
    except Exception:  # noqa: BLE001 - localization is best-effort
        log.exception("issue localization: could not OCR the document")
        return None

    annotations = []
    located = []
    for i, finding in enumerate(findings, start=1):
        nums, quoted = _anchors(finding)
        boxes = region_boxes(finding, scale) if scale else []
        if not boxes:
            boxes = _match(nums, quoted, tokens) if (nums or quoted) else []
        annotations.append({"n": i, "text": finding, "located": bool(boxes)})
        if boxes:
            located.append({"n": i, "boxes": boxes})

    # Always return the rendered document as the visual. Draw numbered boxes when
    # findings map to on-page values; when nothing is spatially locatable (e.g. a
    # metadata/authenticity concern) we still show the clean document so the
    # reviewer always sees WHAT was analysed, with a caption explaining there is
    # nothing to box.
    try:
        if located:
            drawn_img, _ = _draw(im, located, tampering["risk_tier"])
            annotated_image = _encode(drawn_img)
        else:
            annotated_image = _encode(im)
    except Exception:  # noqa: BLE001
        log.exception("issue localization: could not render annotated image")
        annotated_image = None

    return {
        "annotations": annotations,
        "annotated_image": annotated_image,
        "located_count": len(located),
        "ocr_tokens": len(tokens),
    }
