"""Privacy crew evaluation: PII Masking.

A mask is judged by reading it back: the masked render is OCR'd and must not
contain the full PAN / Aadhaar number anywhere (a leak), while the last four
characters should still be readable. Real documents are checked for leaks of
ANY PAN/Aadhaar-shaped number (no ground truth needed) and reported only as
counts.
"""
from __future__ import annotations

import base64
import io
import re

from eval import common as C

_PAN = re.compile(r"[A-Z]{5}\d{4}[A-Z]")
_AAD = re.compile(r"\d{12}")


def _ocr_text(data_uri: str) -> str:
    """Read the masked render back at all four rotations (a sideways card must
    not pass the leak check just because OCR cannot read sideways text)."""
    from PIL import Image
    from app.imaging import ocr_tokens
    raw = base64.b64decode(data_uri.split(",", 1)[1])
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    return " ".join(" ".join(t["text"] for t in ocr_tokens(im.rotate(a, expand=True))) for a in (0, 90, 180, 270))


_AAD_TOKEN = re.compile(r"^\d{4}\s?\d{4}\s?\d{4}$")


def _own_numbers(data: bytes) -> set[str]:
    """The document's OWN PAN / Aadhaar numbers, read from the unmasked original
    (any rotation). Searching the masked readback for 'anything PAN/Aadhaar
    shaped' raised false leaks: the mask's own X's run into the next digits
    ('XXXXX1297' + a letter matches the PAN pattern), and an e-Aadhaar's
    16-digit VID and enrolment number — deliberately left visible — contain
    12-digit substrings."""
    from app.imaging import load_image, ocr_tokens
    pil = load_image(data).convert("RGB")
    own = set()
    for a in (0, 90, 180, 270):
        for t in ocr_tokens(pil.rotate(a, expand=True)):
            own.update(_PAN.findall(t["alnum"]))
            if _AAD_TOKEN.match(t["text"].strip()):
                own.add(re.sub(r"\D", "", t["text"]))
    return own


def _full_number(row) -> str:
    e = row["expected"]
    return C.norm_text(e.get("pan_number") or e.get("aadhaar_number"))


def eval_masking(n_ids: int = 6, degraded_ids: int = 3) -> dict:
    from app.agents.registry import get_agent
    agent = get_agent("pii_masking")
    rows = [r for r in C.manifest("clean") if int(r["identity"][2:]) < n_ids and r["variant"] in ("pan", "aadhaar")]
    rows += [r for r in C.manifest("degraded") if int(r["identity"][2:]) < degraded_ids]
    leaks, last4, over, lat, per = 0, 0, 0, [], {}
    fails = []
    for r in rows:
        full = _full_number(r)
        with C.Timer() as t:
            res = agent.execute({"data": C.read(r["path"]), "doc_type": r["doc_type"], "parsed": {}}).payload
        lat.append(t.ms)
        key = r["variant"] if r["set"] == "degraded" else "clean"
        key = "rot" if key.startswith("rot") else key
        s = per.setdefault(key, {"n": 0, "leak": 0, "last4": 0})
        s["n"] += 1
        if res is None:
            leaks += 1
            s["leak"] += 1
            fails.append({"doc": r["id"], "why": "no masked render"})
            continue
        text = C.norm_text(_ocr_text(res["masked_image"]))
        leaked = full in text
        kept = full[-4:] in text
        leaks += leaked
        last4 += kept
        s["leak"] += leaked
        s["last4"] += kept
        over += max(0, res["masked_count"] - 1)
        if leaked:
            fails.append({"doc": r["id"], "why": "full number still readable",
                          "masked_count": res["masked_count"]})
    n = len(rows)
    # real documents (local only; counts only, no values)
    real_checked = real_leaks = 0
    real_detail = []
    for p in sorted((C.API_DIR.parent / "storage").glob("APP-*/*_original.*")):
        if not any(k in p.name for k in ("pan", "aadhaar")):
            continue
        data = p.read_bytes()
        own = _own_numbers(data)
        res = agent.execute({"data": data, "doc_type": p.name.split("_")[0], "parsed": {}}).payload
        real_checked += 1
        if res is None:
            real_leaks += 1
            continue
        text = C.norm_text(_ocr_text(res["masked_image"]))
        leak = any(n in text for n in own)
        kept = all(n[-4:] in text for n in own) if own else None
        real_leaks += leak
        real_detail.append({"file": f"{p.parent.name}/{p.name.split('_original')[0]}",
                            "masked_regions": res["masked_count"], "own_numbers_found": len(own),
                            "leak": leak, "last4_kept": kept})
    lat_s = C.latency(lat)
    metrics = {"pii_recall": round(1 - leaks / n, 4), "last4_kept": round(last4 / n, 4), "n": n,
               "leaks": leaks, "extra_regions_masked": over,
               "per_condition": {k: {"n": v["n"], "leak_free": round(1 - v["leak"] / v["n"], 4),
                                     "last4_kept": round(v["last4"] / v["n"], 4)} for k, v in per.items()},
               "real_documents_checked": real_checked, "real_documents_leaking": real_leaks,
               "p50_s": lat_s["p50_s"], "p95_s": lat_s["p95_s"]}
    return C.save("pii_masking", metrics, failures=fails[:10], real_documents=real_detail)
