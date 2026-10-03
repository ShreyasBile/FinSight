"""
extraction.py
-------------
Turns regulatory document text into DRAFT compliance-rule candidates.

Industry model (three layers): regulatory text -> obligations -> controls.
This module does the middle step — pulling out "obligation" statements and
proposing a checkable control for each — but it NEVER produces a live rule.
Everything it emits is a *candidate* that a human reviewer must approve before
it can fire on a real application (the four-eyes / maker-checker principle).

Two engines, tried in order:
  1. extract_with_llm()      — asks a local Ollama model to read the text and
                               propose structured obligations. Best quality,
                               fully offline, but optional: any failure (Ollama
                               not running, bad JSON, timeout) returns None.
  2. extract_heuristic()     — deterministic fallback. Finds sentences with
                               obligation cues ("shall", "must", "must not",
                               "should", "may not", "not exceed", "at least"…),
                               proposes a severity from the modal verb, and
                               detects a numeric threshold + operator where it
                               can. It deliberately leaves the application-field
                               mapping as a TODO for the reviewer to complete —
                               because deciding which field represents an
                               obligation is a human judgment.

A candidate is a plain dict (see _candidate). Field mapping / severity are
SUGGESTIONS; the reviewer confirms or edits them at approval time.
"""

from __future__ import annotations

import json
import re
from typing import Optional

# LLM calls go through the agent gateway (agent 'rule_authoring'): the model is
# the Settings-page choice, the same config as every other agent. (This module
# used to read OLLAMA_URL/OLLAMA_MODEL from os.environ, which api/.env never
# populated, so rule extraction silently ignored the configured model.)
LLM_TIMEOUT = 60.0

# Modal / obligation cues and the severity they imply.
_MANDATORY_CUES = [
    "shall not", "must not", "shall", "must", "may not", "is required to",
    "are required to", "is prohibited", "prohibited", "not permitted",
    "not be permitted", "required to",
]
_ADVISORY_CUES = ["should", "recommended", "may", "encouraged to", "expected to"]

# Operator cues -> comparison operator (for numeric-threshold obligations).
_LTE_CUES = ["not exceed", "shall not exceed", "must not exceed", "no more than",
             "at most", "maximum of", "up to", "cap of", "capped at", "not more than"]
_GTE_CUES = ["at least", "not less than", "minimum of", "no less than",
             "not be less than"]

_PLACEHOLDER_FIELD = "REVIEW.map_to_application_field"

_SENTENCE_SPLIT = re.compile(r"(?<=[.;])\s+(?=[A-Z0-9])")
_NUMBER = re.compile(
    r"(\d+(?:\.\d+)?)\s*(percent|per cent|%|day|days|month|months|year|years)?",
    re.IGNORECASE,
)
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "seventy five": 75, "fifty": 50,
}


def _candidate(
    *,
    obligation_text: str,
    rule_name: str,
    rule_type: str,
    condition: dict,
    regulation: str,
    regulation_ref: str,
    remediation: str,
    required_fields: list,
    method: str,
    confidence: float,
    source_document_id: Optional[int],
    source_chunk_id: Optional[int],
    needs_field_mapping: bool,
) -> dict:
    return {
        "obligation_text": obligation_text.strip(),
        "proposed_rule_name": rule_name.strip()[:120],
        "proposed_rule_type": rule_type,
        "proposed_condition": condition,
        "proposed_regulation": regulation,
        "proposed_regulation_ref": regulation_ref[:120],
        "proposed_remediation": remediation.strip()[:300],
        "proposed_required_fields": required_fields,
        "extraction_method": method,
        "extraction_confidence": round(confidence, 2),
        "source_document_id": source_document_id,
        "source_chunk_id": source_chunk_id,
        "needs_field_mapping": needs_field_mapping,
    }


# --------------------------------------------------------------------------
# Heuristic (deterministic) extractor
# --------------------------------------------------------------------------
def _severity_for(sentence: str) -> Optional[str]:
    low = sentence.lower()
    for cue in _MANDATORY_CUES:
        if cue in low:
            return "mandatory"
    for cue in _ADVISORY_CUES:
        if re.search(rf"\b{re.escape(cue)}\b", low):
            return "advisory"
    return None


def _detect_threshold(sentence: str) -> Optional[tuple[str, float]]:
    """Return (operator, value) if the sentence expresses a numeric bound."""
    low = sentence.lower()
    op = None
    if any(c in low for c in _LTE_CUES):
        op = "lte"
    elif any(c in low for c in _GTE_CUES):
        op = "gte"
    if op is None:
        return None

    # Prefer a digit; fall back to a spelled-out number.
    m = _NUMBER.search(low)
    value: Optional[float] = None
    unit = None
    if m:
        value = float(m.group(1))
        unit = (m.group(2) or "").lower()
    else:
        for word, num in _WORD_NUMBERS.items():
            if re.search(rf"\b{word}\b", low):
                value, unit = float(num), None
                break
    if value is None:
        return None
    # Normalise percentages to a fraction so it matches ratio-style fields.
    if unit in ("percent", "per cent", "%"):
        value = value / 100.0
    return op, value


def extract_heuristic(
    text: str,
    *,
    document_title: str = "",
    document_category: str = "General",
    source_document_id: Optional[int] = None,
    source_chunk_id: Optional[int] = None,
    section_title: str = "",
) -> list[dict]:
    candidates: list[dict] = []
    for sentence in _SENTENCE_SPLIT.split(text):
        sentence = sentence.strip()
        if len(sentence) < 25:
            continue
        severity = _severity_for(sentence)
        if severity is None:
            continue

        threshold = _detect_threshold(sentence)
        if threshold is not None:
            op, value = threshold
            # We know the operator + bound, but not which field it applies to;
            # leave the field as a placeholder for the reviewer to map.
            condition = {"field": _PLACEHOLDER_FIELD, "op": op, "value": value}
            needs_mapping = True
            confidence = 0.5
        else:
            # Boolean-style obligation: propose an existence/true check the
            # reviewer will point at the right field.
            condition = {"field": _PLACEHOLDER_FIELD, "op": "eq", "value": True}
            needs_mapping = True
            confidence = 0.35

        name = re.sub(r"\s+", " ", sentence)[:80]
        candidates.append(
            _candidate(
                obligation_text=sentence,
                rule_name=name,
                rule_type=severity,
                condition=condition,
                regulation=document_title,
                regulation_ref=section_title or sentence[:60],
                remediation=f"Ensure compliance with: {sentence}",
                required_fields=[],
                method="heuristic",
                confidence=confidence,
                source_document_id=source_document_id,
                source_chunk_id=source_chunk_id,
                needs_field_mapping=needs_mapping,
            )
        )
    return candidates


# --------------------------------------------------------------------------
# LLM extractor (optional, via local Ollama)
# --------------------------------------------------------------------------
_LLM_PROMPT = """You are a compliance analyst. Read the regulatory text below and \
extract every distinct OBLIGATION (a "shall/must/should/may not" requirement). \
For each, return a JSON object with:
  "obligation_text": the exact requirement, quoted from the text,
  "rule_name": a short imperative name (max 12 words),
  "severity": "mandatory" if the text uses shall/must/prohibited/required, else "advisory",
  "suggested_field": a dotted application field this could map to (e.g. \
"loan_terms.cooling_off_period_days"), or null if unclear,
  "operator": one of eq, ne, gte, lte, gt, lt, or null,
  "value": the threshold value (number, true, false, or string), or null,
  "remediation": one sentence on how to comply.
Return ONLY a JSON array. Regulatory text:
---
{text}
---"""


def extract_with_llm(
    text: str,
    *,
    document_title: str = "",
    document_category: str = "General",
    source_document_id: Optional[int] = None,
    source_chunk_id: Optional[int] = None,
    section_title: str = "",
) -> Optional[list[dict]]:
    """Ask a local Ollama model to propose obligations. Returns None on any
    failure so the caller can fall back to the heuristic extractor."""
    try:
        from ..agents.llm.gateway import chat
        res = chat("rule_authoring", user=_LLM_PROMPT.format(text=text[:6000]), timeout=LLM_TIMEOUT)
        parsed = res.parsed if res.parsed is not None else json.loads(res.text)
    except Exception:
        return None

    # Ollama's json format may wrap the array in an object; normalise.
    if isinstance(parsed, dict):
        for v in parsed.values():
            if isinstance(v, list):
                parsed = v
                break
        else:
            parsed = [parsed]
    if not isinstance(parsed, list):
        return None

    candidates: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict) or not item.get("obligation_text"):
            continue
        severity = "mandatory" if str(item.get("severity", "")).lower().startswith("mand") else "advisory"
        field = item.get("suggested_field")
        op = item.get("operator")
        value = item.get("value")
        if field and op and value is not None:
            condition = {"field": field, "op": op, "value": value}
            needs_mapping = False
            confidence = 0.7
        else:
            condition = {"field": _PLACEHOLDER_FIELD, "op": "eq", "value": True}
            needs_mapping = True
            confidence = 0.55
        candidates.append(
            _candidate(
                obligation_text=str(item["obligation_text"]),
                rule_name=str(item.get("rule_name") or item["obligation_text"][:80]),
                rule_type=severity,
                condition=condition,
                regulation=document_title,
                regulation_ref=section_title or str(item["obligation_text"])[:60],
                remediation=str(item.get("remediation") or ""),
                required_fields=[field] if (field and not needs_mapping) else [],
                method="llm",
                confidence=confidence,
                source_document_id=source_document_id,
                source_chunk_id=source_chunk_id,
                needs_field_mapping=needs_mapping,
            )
        )
    return candidates or None


# --------------------------------------------------------------------------
# Orchestration + validation
# --------------------------------------------------------------------------
_VALID_OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in",
              "contains", "exists", "not_exists"}


def validate_condition(node: dict) -> bool:
    """Light structural check that a proposed condition is well-formed before
    it's stored as a candidate. (Actual safe evaluation happens only after
    approval, in the rules engine.)"""
    if not isinstance(node, dict):
        return False
    for group in ("all", "any"):
        if group in node:
            return isinstance(node[group], list) and all(validate_condition(c) for c in node[group])
    if "not" in node:
        return validate_condition(node["not"])
    if "field" in node or "expr" in node:
        return node.get("op") in _VALID_OPS
    return False


def extract_candidates(
    chunks: list[dict],
    *,
    document_title: str,
    document_category: str,
    prefer_llm: bool = True,
) -> tuple[list[dict], str]:
    """Run extraction over a document's chunks, one chunk (section) at a time.

    chunks: dicts with keys chunk_id, document_id, section_title, text.

    Extracting per chunk — rather than in a single whole-document call — is what
    gives good coverage: a small local model reliably returns the obligation(s)
    in one short section, whereas a whole-document call tends to collapse to a
    single result. Each chunk independently tries the LLM and falls back to the
    heuristic if the LLM is unavailable or yields nothing, so a document with N
    obligation-bearing sections produces ~N drafts, each linked to its source
    chunk.

    Returns (candidates, method_used); method_used is "llm", "heuristic", or
    "llm+heuristic" depending on which engines actually contributed.
    """
    candidates: list[dict] = []
    used_llm = False
    used_heuristic = False

    for c in chunks:
        chunk_cands: Optional[list[dict]] = None
        if prefer_llm:
            chunk_cands = extract_with_llm(
                c["text"],
                document_title=document_title,
                document_category=document_category,
                source_document_id=c["document_id"],
                source_chunk_id=c["chunk_id"],
                section_title=c.get("section_title", ""),
            )
        if chunk_cands:
            used_llm = True
            candidates.extend(chunk_cands)
        else:
            heur = extract_heuristic(
                c["text"],
                document_title=document_title,
                document_category=document_category,
                source_document_id=c["document_id"],
                source_chunk_id=c["chunk_id"],
                section_title=c.get("section_title", ""),
            )
            if heur:
                used_heuristic = True
                candidates.extend(heur)

    method_used = (
        "llm+heuristic" if used_llm and used_heuristic
        else "llm" if used_llm
        else "heuristic"
    )

    # Drop anything that somehow produced a malformed condition.
    candidates = [c for c in candidates if validate_condition(c["proposed_condition"])]
    return candidates, method_used
