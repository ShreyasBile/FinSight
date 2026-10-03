"""Stage-1 signal: document authenticity assessment via the local vision model.

WHY THIS EXISTS — the forensic signals (ELA/copy-move/noise/CNN) detect EDITS to
a genuine document. They are structurally blind to a document FABRICATED FROM
SCRATCH: an AI- or template-generated payslip/statement that is internally
consistent and carries no edit traces has no "original" to diff against. That is
exactly the gap a modern fake exploits. A vision model instead judges whether the
document LOOKS like a real institutional document or a generated/templated fake —
the one layer that catches semantic fabrication.

Two layers inside this one signal:
  1. DETERMINISTIC placeholder scan — OCR the page and look for verbatim filler
     ("abc bank", sequential account numbers). Verifiable facts, independent of
     the model; alone they score _PLACEHOLDER_SCORE.
  2. Vision-model verdict — the semantic judgement, heavily fenced in:
  * config-gated (settings.tampering_authenticity_enabled) and limited to the
    fraud-prone doc types (income/financial docs) — never runs on everything.
  * NON-BLOCKING, like the whole cascade: it only produces evidence for a human.
  * CAPPED: fusion confidence is set so a lone "likely fabricated" verdict can
    raise a document to MEDIUM (review) but NOT HIGH — auto-condemnation always
    requires corroboration by a second signal.
  * MUST JUSTIFY, AND ONLY IN ITS DOMAIN: reasons about arithmetic or amount
    magnitude are dropped (deterministic checks own those); every number a
    reason cites must be verifiably printed on the page (grounding gate); a
    flag with no surviving reasons is voided.
  * FAIL-SAFE: model/parse/timeout errors degrade to the deterministic scan,
    or `unavailable` — detection never depends on the model being up.

Crucial calibration: a genuine payslip is itself computer-generated, so "looks
computer-generated" must NEVER be the trigger. The prompt asks only for concrete
signs of fabrication-for-deception (implausible/placeholder values, missing
statutory fields a real Indian payslip must carry, template/formatting
incoherence, AI-boilerplate phrasing, fake-looking logos/stamps).
"""
import io
import json
import logging
import re

from app.config import settings
from app.tampering.signals.base import SignalResult, unavailable

log = logging.getLogger("finsight.tampering")

# verdict -> tamper score. "unclear" stays below the stage-1 gate (0.25) so an
# ambiguous-but-probably-fine document is not needlessly escalated; only
# "suspicious"/"likely_fabricated" flag.
_VERDICT_SCORE = {
    "genuine": 0.0,
    "unclear": 0.15,
    "suspicious": 0.55,
    "likely_fabricated": 0.85,
}

_DOC_LABELS = {
    "salary_slip": "monthly salary slip / payslip issued by an employer",
    "payslip": "monthly salary slip / payslip issued by an employer",
    "bank_statement": "bank account statement issued by a bank",
    "itr": "Income Tax Return (ITR) acknowledgement from the Indian Income Tax Dept",
    "form16": "Form 16 TDS certificate issued by an employer",
    "pan": "PAN (Permanent Account Number) card issued by the Indian Income Tax Department",
    "aadhaar": "Aadhaar card issued by UIDAI (Government of India)",
}

# Doc-type-specific "what a real one of these always carries" guidance, used in
# place of the income/financial-doc examples in the prompt below (a PAN card's
# missing-detail red flags are not "employer name / PF lines", they're the
# government's own fixed template elements). ID cards are a STANDARDISED,
# government-designed template — unlike a payslip (varies per employer) or a
# bank statement (varies per bank) — which if anything makes template/branding
# incoherence an even more reliable tell here, not less.
_DOC_EXPECTED_DETAILS = {
    "pan": (
        "the Income Tax Department header and Government of India emblem "
        "(Ashoka Pillar), the exact printed label 'Permanent Account Number "
        "Card', the PAN in the fixed AAAAA9999A format, name, father's name, "
        "date of birth, a signature panel, and a QR code"
    ),
    "aadhaar": (
        "the UIDAI / Government of India branding, the Aadhaar emblem, a "
        "12-digit Aadhaar number, name, date of birth, gender, address, "
        "photograph, and a QR code"
    ),
}
_DEFAULT_EXPECTED_DETAILS = (
    "e.g. an Indian payslip: employer legal name + address, employee ID, "
    "PAN/UAN, PF/ESI or tax lines; a bank statement: bank name/branch, IFSC, "
    "account number, per-transaction rows"
)

_SYSTEM = (
    "You are a document-fraud reviewer for an Indian NBFC (lending) platform. You "
    "are shown ONE document image and must judge whether it looks like a GENUINE "
    "document issued by a real institution, or a FABRICATED one made to deceive a "
    "lender (AI-generated, a fill-in template, or hand-built in a design tool). "
    "CRITICAL: real payslips, statements and certificates are normally "
    "computer-generated PDFs — being computer-generated is NOT fraud and is NOT a "
    "reason to flag. Only raise concern for CONCRETE, SPECIFIC signs of "
    "fabrication. Never guess; if it looks like an ordinary document with "
    "plausible, specific details, answer 'genuine'. You are one input among "
    "deterministic checks and never make the final decision."
)


def _fields_block(ocr_fields: dict | None) -> str:
    """Compact OCR-extracted values for the prompt, so the model cites EXACT
    values instead of re-reading numbers off the image (vision models routinely
    misread Indian digit grouping — '1,00,000.00' became '1,00,00,000.00' in
    testing, a 100x error shown to the user)."""
    if not ocr_fields:
        return ""
    flat = {}
    for k, v in list(ocr_fields.items())[:20]:
        if isinstance(v, dict) and "value" in v:
            v = v["value"]
        if v is None or isinstance(v, (list, dict)) or str(v).strip() == "":
            continue
        flat[k] = str(v)[:60]
    if not flat:
        return ""
    return (
        "\nFor reference, OCR already extracted these fields from this document. "
        "When you cite any number or name from the document, use these EXACT "
        "values — do not re-read them from the image:\n"
        + json.dumps(flat, ensure_ascii=False) + "\n"
    )


def _prompt(doc_type: str, ocr_fields: dict | None = None) -> str:
    label = _DOC_LABELS.get(doc_type, f"'{doc_type}' document")
    expected = _DOC_EXPECTED_DETAILS.get(doc_type, _DEFAULT_EXPECTED_DETAILS)
    return (
        f"This should be a {label}.\n"
        f"{_fields_block(ocr_fields)}\n"
        "Look for concrete signs it was FABRICATED to deceive a lender, such as:\n"
        f"- Missing details a real one of these documents almost always carries ({expected}).\n"
        "- Placeholder / dummy / obviously-fake values (e.g. 'ABC Company', "
        "'123456', 'John Doe', 'abc bank').\n"
        "- Text that reads like AI-generated or template boilerplate rather than a "
        "real employer's wording; inconsistent fonts, spacing or alignment; a "
        "logo/stamp that looks generated or is absent where one is expected.\n"
        "- Dates that are impossible (e.g. issued before the period it covers).\n\n"
        "Do NOT flag merely because the document is digital, clean, or simple.\n"
        "Do NOT make claims about arithmetic (whether totals, gross, deductions or "
        "net add up) — a separate deterministic system verifies all arithmetic "
        "exactly, and your visual reading of amounts is not reliable enough.\n"
        "Do NOT judge whether amounts are high, low, round or 'realistic' — amount "
        "plausibility is not your job. Focus on placeholders, missing statutory "
        "details, branding/layout problems, and wording.\n\n"
        "Answer as strict JSON only:\n"
        '{\n'
        '  "verdict": "genuine" | "unclear" | "suspicious" | "likely_fabricated",\n'
        '  "reasons": ["each a short, concrete, specific observation that justifies '
        'the verdict — cite what you actually see"],\n'
        '  "genuine_markers": ["specific real-looking details you did see, if any"]\n'
        '}\n'
        "Rules: use 'suspicious' or 'likely_fabricated' ONLY with concrete reasons. "
        "If unsure or the document simply looks ordinary, use 'genuine' or "
        "'unclear'. Keep reasons short and specific."
    )


# Reasons the model is NOT allowed to make, dropped unconditionally:
#  * arithmetic claims — arithmetic is owned by the deterministic business_rules
#    signal (exact numbers, zero false positives). Audit finding: the model
#    asserted "gross and deductions do not add up" and "net is higher than
#    gross" on a slip whose math is consistent — no cited numbers, so numeric
#    grounding alone could not catch them.
#  * amount-magnitude opinions ("salary is unusually high/unrealistic") — the
#    model's sense of a plausible Indian salary is unreliable, and the one
#    hallucination the user caught (Rs 1 lakh read as Rs 1 crore) was exactly
#    this class of claim.
_ARITH_CLAIM_RE = re.compile(
    r"add(s|ed)?\s+up|arithmetic|calculat|miscalc|do(es)?\s+not\s+(sum|total|match)"
    r"|(net|gross)[^.]{0,60}(exceed|higher\s+than|greater\s+than|more\s+than|lower\s+than|less\s+than)[^.]{0,30}(gross|net|earnings)",
    re.IGNORECASE)
_MAGNITUDE_CLAIM_RE = re.compile(
    r"(unusually|too|suspiciously)\s+(high|low|large|small|round)|unrealistic|implausible",
    re.IGNORECASE)

# Deterministic page-text placeholder scan — the backstop that does NOT depend
# on the model's judgement. Fabricated documents are routinely populated with
# filler ("abc bank", account "123456789"); these are read straight off the
# page with OCR and are verifiable facts, not opinions. Kept narrow (word
# boundaries, institution-word context) so genuine documents can't match.
_PLACEHOLDER_PAGE_RE = re.compile(
    r"\b(?:abc|xyz|test|dummy|sample|fake)\s+(?:bank|company|corp|pvt|ltd|finance|enterprises|industries)\b"
    r"|\b(?:john|jane)\s+doe\b|\blorem\s+ipsum\b",
    re.IGNORECASE)
# score when the page provably prints placeholder values: strong deterministic
# evidence (MEDIUM alone; HIGH only with corroboration from another signal)
_PLACEHOLDER_SCORE = 0.75


def _page_tokens(image) -> list | None:
    """RapidOCR word tokens for the page, or None if OCR failed. Read ONCE per
    document and reused by both the placeholder scan and numeric grounding."""
    from app import imaging
    try:
        return imaging.ocr_tokens(image)
    except Exception:  # noqa: BLE001
        log.exception("authenticity: page OCR failed")
        return None


def _placeholder_scan(tokens: list | None) -> list[str]:
    """Verbatim placeholder values printed on the page. Deterministic."""
    if not tokens:
        return []
    findings = []
    joined = " ".join(t["text"] for t in tokens)
    m = _PLACEHOLDER_PAGE_RE.search(joined)
    if m:
        findings.append(
            f"The document prints '{m.group(0)}' — a placeholder, not a real institution or person")
    for t in tokens:
        text = t["text"].strip()
        if "." in text or "%" in text:  # decimals/rates are amounts, not IDs
            continue
        digits = re.sub(r"\D", "", text)
        if len(digits) >= 6 and (
                digits in "1234567890123456789012"
                or digits in "9876543210987654321098"
                or len(set(digits)) == 1):
            findings.append(
                f"The document prints '{text}' — a sequential/repeated placeholder number")
    return findings[:4]


def _numeric_claims(text: str) -> set:
    """Money/ID numbers (>=100) a reason cites — the values that MUST exist on
    the document for the reason to be trusted. Small numbers (days, counts) are
    ignored as noise."""
    from app import imaging
    out = set()
    for m in imaging.NUM_RE.findall(text):
        v = imaging.num_value(m)
        if v is not None and v >= 100:
            out.add(round(v, 2))
    return out


def _document_values(tokens: list | None, ocr_fields: dict | None) -> set | None:
    """Every number actually present in the document: OCR word tokens from the
    page plus the extracted field values. None = page OCR failed (verification
    impossible — caller keeps reasons rather than dropping valid ones)."""
    from app import imaging
    if tokens is None:
        return None
    texts = [t["text"] for t in tokens]
    if ocr_fields:
        for v in ocr_fields.values():
            if isinstance(v, dict):
                v = v.get("value")
            if v is not None:
                texts.append(str(v))
    vals: set = set()
    for text in texts:
        for m in imaging.NUM_RE.findall(text):
            n = imaging.num_value(m)
            if n is not None:
                vals.add(round(n, 2))
    return vals


def _verify_reasons(reasons: list[str], tokens: list | None,
                    ocr_fields: dict | None) -> list[str]:
    """Deterministic grounding gate for the model's reasons: forbidden claim
    classes (arithmetic, amount magnitude) are dropped outright, and any number
    a reason cites must actually be printed on the document (or in the OCR
    fields). Measured case: the model read basic salary Rs 1,00,000.00 as
    Rs 1,00,00,000.00 and called it implausible — that reason must never reach
    a reviewer."""
    allowed = []
    for r in reasons:
        if _ARITH_CLAIM_RE.search(r):
            log.info("authenticity: dropped arithmetic claim: %s", r)
        elif _MAGNITUDE_CLAIM_RE.search(r):
            log.info("authenticity: dropped amount-magnitude opinion: %s", r)
        else:
            allowed.append(r)
    reasons = allowed
    if not any(_numeric_claims(r) for r in reasons):
        return reasons
    doc_vals = _document_values(tokens, ocr_fields)
    if doc_vals is None:
        return reasons  # cannot verify — keep rather than discard valid evidence
    kept = []
    for r in reasons:
        claims = _numeric_claims(r)
        if claims and not claims.issubset(doc_vals):
            log.info("authenticity: dropped ungrounded reason (cites %s not on page): %s",
                     sorted(claims - doc_vals), r)
            continue
        kept.append(r)
    return kept


def _image_jpeg(image, max_dim: int = 1100) -> bytes | None:
    try:
        im = image.convert("RGB")
        w, h = im.size
        if max(w, h) > max_dim:
            s = max_dim / max(w, h)
            im = im.resize((int(w * s), int(h * s)))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None


def skip_reason(doc_type: str) -> str | None:
    """Why the check will not run for this doc type (None = it will run).
    Checked BEFORE the caller renders a page image, so a disabled or
    out-of-scope check costs nothing."""
    if not getattr(settings, "tampering_authenticity_enabled", False):
        return "authenticity check disabled"
    allowed = {d.strip() for d in getattr(settings, "tampering_authenticity_doc_types", "").split(",") if d.strip()}
    if allowed and doc_type not in allowed:
        return f"not run for doc_type='{doc_type}'"
    return None


def run(image, doc_type: str = "generic", ocr_fields: dict | None = None) -> SignalResult:
    reason = skip_reason(doc_type)
    if reason:
        return unavailable("authenticity", reason)

    if image is None:
        return unavailable("authenticity", "no document image to assess")

    # Deterministic backstop FIRST — placeholder values read straight off the
    # page flag the document even if the vision model is down or unhelpful.
    tokens = _page_tokens(image)
    det_findings = _placeholder_scan(tokens)

    # ---- vision model verdict (best-effort) ----
    # Routed through the agent gateway: the model/provider is whatever the
    # Settings page selected for the 'authenticity' agent (local Ollama by
    # default); the privacy guard and the LLM queue apply.
    verdict, reasons = None, []
    jpeg = _image_jpeg(image)
    if jpeg is not None:
        timeout = getattr(settings, "tampering_authenticity_timeout", 60)
        try:
            from app.agents.llm.gateway import chat
            res = chat("authenticity", system=_SYSTEM, user=_prompt(doc_type, ocr_fields),
                       images=[jpeg], timeout=timeout)
            parsed = res.parsed if isinstance(res.parsed, dict) else json.loads(res.text)
            v = str(parsed.get("verdict", "")).strip().lower().replace(" ", "_")
            if v in _VERDICT_SCORE:
                verdict = v
                reasons = [str(r).strip() for r in parsed.get("reasons", []) if str(r).strip()]
        except Exception as exc:  # noqa: BLE001 - never let the model break detection
            log.warning("authenticity: model unavailable (%s) — deterministic scan only",
                        type(exc).__name__)

    model_score = _VERDICT_SCORE.get(verdict, 0.0) if verdict else 0.0
    if model_score >= 0.5 and reasons:
        # Grounding gate: forbidden claim classes dropped; every cited number
        # must actually be printed on the document (or in the OCR fields).
        reasons = _verify_reasons(reasons, tokens, ocr_fields)
    if model_score >= 0.5 and not reasons:
        # A flag with no surviving justification is worthless and risky (this
        # is the one probabilistic verdict) — void it rather than flag a
        # document on a bare verdict. The deterministic scan still counts.
        log.info("authenticity: model flag had no verifiable reasons — voided")
        verdict, model_score = None, 0.0

    score = max(model_score, _PLACEHOLDER_SCORE if det_findings else 0.0)
    if verdict is None and not det_findings:
        return unavailable("authenticity", "model unavailable/unusable and no deterministic evidence")

    # ---- assemble findings ----
    if score >= 0.5:
        head = ("Authenticity check found strong signs this document was fabricated"
                if score >= 0.7 else
                "Authenticity check found signs this document may be fabricated")
        findings = [head] + det_findings + [r for r in reasons if r not in det_findings]
        findings = findings[:7]
    elif verdict == "unclear":
        findings = ["AI authenticity check could not confirm this document is genuine"]
    else:
        findings = ["The document reads as a genuine institutional document (AI authenticity check)"]

    return SignalResult("authenticity", True, score, findings)
