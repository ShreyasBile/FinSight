"""Plain-language explainer for tampering findings, via the local Qwen model.

IMPORTANT — this does NOT detect anything. Detection is deterministic (the
nine-signal cascade in pipeline.py). This module only TRANSLATES the findings
that cascade already produced into simple, non-technical language a borrower
or a non-technical officer can understand. The LLM is given the concrete
findings and asked to rephrase them; when vision is on it also sees the page,
but only to say WHERE each listed finding is. It never makes a tampering
judgement and cannot change the risk tier.

Runs only for MEDIUM/HIGH documents (a clean document needs no explanation),
and is fully isolated: any failure returns None and the raw findings are
still shown. Provider/model = the Settings choice for 'tamper_explainer'.
"""
import io
import json
import logging

from ..config import settings

log = logging.getLogger("finsight.tampering")

_SYSTEM = (
    "You explain document-check results to ordinary people applying for a loan "
    "in India. You are given the EXACT problems an automated checker already "
    "found in a document. Your only job is to restate those problems in very "
    "simple, calm, everyday language — the kind a person with no technical or "
    "banking background would understand. Do NOT invent new problems, do NOT "
    "decide if the document is fake, and do NOT add problems that are not in "
    "the list. Only explain what is given. All money amounts are in Indian "
    "Rupees — always write amounts with the rupee sign (for example: Rs 85,000) "
    "and NEVER use dollar signs. When numbers are supposed to add up, state the "
    "arithmetic plainly: what the numbers are, what they should total, and what "
    "the document claims instead."
)


def _prompt(doc_label: str, tier: str, findings: list[str], has_image: bool = False) -> str:
    bullet = "\n".join(f"- {f}" for f in findings)
    seeing = (
        "You are also shown the actual document image. Use it ONLY to say WHERE "
        "on the document each listed problem is (for example 'the net salary "
        "figure near the bottom' or 'the second row of the table'), so your "
        "explanation is concrete. You must NOT look for any new problems in the "
        "image and must NOT change the findings — only explain the ones listed. "
        if has_image else ""
    )
    return (
        f"Document being checked: {doc_label}\n"
        f"Overall check result: {'needs a closer look' if tier == 'MEDIUM' else 'has clear concerns'}\n\n"
        f"The automated checker found these specific issues:\n{bullet}\n\n"
        f"{seeing}"
        "Write the explanation as JSON with exactly this shape:\n"
        '{\n'
        '  "summary": "one or two short sentences, plain language, saying what the '
        'overall concern is and that a person will review it — reassuring, not accusing",\n'
        '  "issues": ["each specific problem rewritten in one simple sentence a '
        'non-technical person understands — say what looks off, where on the document '
        'it is, and in plain words why that matters"]\n'
        '}\n'
        "Rules: no jargon (no words like ELA, checksum, pixel, metadata, keypoint). "
        "If a number does not add up, say which numbers and what they should be. "
        "Keep every sentence short. Do not accuse the person of fraud — say the "
        "document 'needs checking' or 'does not match'."
    )


def _findings_for_llm(tampering: dict) -> list[str]:
    """The concrete, concerning findings — skip 'not applicable' noise and the
    internal fusion-mechanics line."""
    out = []
    for e in tampering.get("explanations", []):
        if "skipped:" in e or e.startswith("Risk driven by a single"):
            continue
        # strip the [signal] prefix; the LLM should not see internal names
        out.append(e.split("] ", 1)[-1] if e.startswith("[") else e)
    return out[:8]


def _points_at_the_page(tampering: dict) -> bool:
    """Is there anything for the vision model to point at? Only a finding that cites a value printed
    on the page (a figure, a quoted ID) has a location. Pixel-level findings (noise, compression,
    duplicated patches, an editor tag) have none — and shown the page with nothing to locate, the
    small local model went looking for problems of its own (it compared dates of birth with expiry
    dates, cash with change) in about one explanation in six on real documents (2026-10-02). The
    grounding gate removed those sentences; not showing the page stops them being written."""
    from .locate import _SKIP, _anchors, _clean_finding
    for e in tampering.get("explanations", []):
        if any(sk in e for sk in _SKIP):
            continue
        nums, quoted = _anchors(_clean_finding(e))
        # only a rule finding cites what is printed on the page; the numbers in a pixel finding are
        # block and keypoint counts, and the quoted word in a metadata finding is a tool name ('gimp')
        if (nums or quoted) and e.startswith("[business_rules]"):
            return True
    return False


def _image_for_vision(file_bytes: bytes) -> bytes | None:
    """Downscaled JPEG of the document (page 1 for PDFs) for the vision call.
    Kept small so the grounding image doesn't blow up the context or latency."""
    try:
        from PIL import Image
        if file_bytes[:5] == b"%PDF-":
            from ..utils import render_pdf_to_images
            pages = render_pdf_to_images(file_bytes, max_pages=1)
            if not pages:
                return None
            im = Image.open(io.BytesIO(pages[0]))
        else:
            im = Image.open(io.BytesIO(file_bytes))
        im = im.convert("RGB")
        w, h = im.size
        if max(w, h) > 1100:
            s = 1100 / max(w, h)
            im = im.resize((int(w * s), int(h * s)))
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=85)
        return buf.getvalue()
    except Exception:  # noqa: BLE001 - fall back to text-only explanation
        return None


async def explain(doc_label: str, tampering: dict,
                  image_bytes: bytes | None = None) -> dict | None:
    """Return {"summary": str, "issues": [str]} or None. Never raises.

    If image_bytes is given and vision is enabled, Qwen SEES the document so it
    can say where each already-detected issue is — grounded strictly by the
    findings, never free-form detection."""
    if not settings.tampering_explain_enabled:
        return None
    if tampering.get("risk_tier") not in ("MEDIUM", "HIGH"):
        return None
    findings = _findings_for_llm(tampering)
    if not findings:
        return None

    from ..agents.llm import gateway

    image = None
    if settings.tampering_explain_vision and image_bytes and _points_at_the_page(tampering):
        try:
            if gateway.supports_vision(gateway.resolve("tamper_explainer")):
                image = _image_for_vision(image_bytes)
        except Exception:  # noqa: BLE001 - fall back to a text-only explanation
            image = None

    try:
        res = await gateway.achat(
            "tamper_explainer", system=_SYSTEM,
            user=_prompt(doc_label, tampering["risk_tier"], findings, has_image=bool(image)),
            images=[image] if image else None, timeout=settings.tampering_explain_timeout)
        parsed = res.parsed if isinstance(res.parsed, dict) else json.loads(res.text)
        summary = str(parsed.get("summary", "")).strip()
        issues = [str(i).strip() for i in parsed.get("issues", []) if str(i).strip()]
        if not summary and not issues:
            return None
        return {"summary": summary, "issues": issues[:8]}
    except Exception:  # noqa: BLE001 - explanation is best-effort, findings still show
        log.exception("tampering explainer failed — omitting plain summary")
        return None
