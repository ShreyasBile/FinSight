"""Weighted fusion of whatever signals actually ran.

Deliberately rule-based and transparent (a lending decision must be
explainable to an officer and an auditor), with one key mechanic the
reference repo lacked: weights renormalize over AVAILABLE signals only.
If the CNN hasn't been trained yet, or PDF checks don't apply to a JPEG,
their weight is redistributed proportionally — an absent signal is absent,
not "scored zero", so day-one deployments without a model still produce
honest scores.

Weights and tier cutoffs live in config.yaml; when enough labeled verdicts
accumulate, refit them (e.g. logistic regression over per-signal scores) and
update the config — no code change.
"""
from app.tampering.config import CFG
from app.tampering.signals.base import SignalResult


def family_of(doc_type: str | None) -> str:
    """'id' for identity documents (photographed cards), 'page' for everything else (scans, PDFs)."""
    return "id" if (doc_type or "").lower() in CFG.pipeline.always_escalate_doc_types else "page"


def calibrated(name: str, score: float, family: str = "page") -> float:
    """A signal's score above what GENUINE documents of that kind routinely produce, rescaled to [0, 1].

    The pixel signals were scaled on synthetic pages, where a clean page scores near zero. Real captures
    do not: measured on real genuine receipts and ID images (2026-10-01), the CNN scored >= 0.5 on 80%
    of them, and ELA / noise / copy-move reached their maximum on many (repeated digits and logos look
    like copy-move; scans have uneven noise) — 84% of genuine documents were flagged, 17% HIGH.
    config.yaml (fusion.genuine_floor.<family>) holds, per signal, the score that 95% of genuine real
    documents of that family stay at or below; a score up to that floor is no evidence."""
    floors = (getattr(CFG.fusion, "genuine_floor", None) or {}).get(family) or {}
    floor = float(floors.get(name, 0.0) or 0.0)
    if floor <= 0:
        return score
    return 0.0 if score <= floor or floor >= 1 else (score - floor) / (1.0 - floor)


def fuse(signals: list[SignalResult], doc_type: str | None = None) -> dict:
    weights = CFG.fusion.weights
    family = family_of(doc_type)
    raw = {s.name: s.score for s in signals if s.available}
    # fusion works on calibrated scores; the signals' own findings and artifacts are untouched
    available = [SignalResult(s.name, True, calibrated(s.name, s.score, family), s.findings, s.artifacts)
                 for s in signals if s.available]
    signals = [next((a for a in available if a.name == s.name), s) for s in signals]

    total_w = sum(weights.get(s.name, 0.0) for s in available)
    if total_w <= 0:
        return {
            "fused_score": 0.0, "risk_tier": "LOW",
            "contributions": {},
            "explanations": ["No signals available — nothing to score"],
        }

    contributions = {}
    fused = 0.0
    for s in available:
        w = weights.get(s.name, 0.0) / total_w
        contributions[s.name] = {
            "score": round(s.score, 4),
            "normalized_weight": round(w, 4),
            "contribution": round(w * s.score, 4),
        }
        if abs(raw[s.name] - s.score) > 1e-9:          # calibrated: keep what the signal itself reported
            contributions[s.name]["raw_score"] = round(raw[s.name], 4)
        fused += w * s.score

    # Dominant-evidence path: averaging measures corroboration across signals,
    # but a forgery usually trips only the one detector matched to its type —
    # one near-proof finding must not be washed out by five quiet signals.
    conf = getattr(CFG.fusion, "single_signal_confidence", None)
    dominant_name, dominant = None, 0.0
    second_name, second = None, 0.0
    if conf is not None:
        # Signals that are no evidence ALONE (fusion.needs_corroboration): on real documents a lone ELA or
        # noise hit is at least as common on genuine pages as on forged ones, so it enters these two
        # paths only when another signal backs it (that signal's score x confidence >= corroboration_min).
        # It always counts in the weighted average above.
        needs = set(getattr(CFG.fusion, "needs_corroboration", None) or ())
        backing = float(getattr(CFG.fusion, "corroboration_min", 0.0) or 0.0)
        d_of = {s.name: s.score * getattr(conf, s.name, 0.0) for s in available}
        for s in available:
            if s.name in needs and not any(v >= backing for k, v in d_of.items() if k != s.name):
                continue
            d = d_of[s.name]
            if d > dominant:
                second_name, second = dominant_name, dominant
                dominant_name, dominant = s.name, d
            elif d > second:
                second_name, second = s.name, d

    # Corroboration path: when a SECOND independent signal also fires, the
    # evidence is stronger than either signal alone — a fabricated document
    # flagged by both the producer check and the authenticity check should
    # escalate past what one signal's capped confidence allows.
    corro_factor = getattr(CFG.fusion, "corroboration_factor", 0.0)
    corroborated = 0.0
    if corro_factor and dominant > 0 and second > 0:
        corroborated = min(1.0, dominant + corro_factor * second)

    if corroborated > fused and corroborated > dominant:
        fused = corroborated
        contributions["_corroborated_signals"] = {
            "signals": [dominant_name, second_name], "value": round(corroborated, 4)}
    elif dominant > fused:
        fused = dominant
        contributions["_dominant_signal"] = {
            "signal": dominant_name, "value": round(dominant, 4)}

    fused = float(min(max(fused, 0.0), 1.0))

    if fused >= CFG.fusion.tier_high:
        tier = "HIGH"
    elif fused >= CFG.fusion.tier_medium:
        tier = "MEDIUM"
    else:
        tier = "LOW"

    # explanations: every finding from every signal that carries real evidence,
    # plus skip reasons so a reviewer can see what did NOT run and why
    explanations = []
    if "_corroborated_signals" in contributions:
        explanations.append(
            f"Risk driven by two independent signals agreeing: {dominant_name} + "
            f"{second_name} (combined evidence {corroborated:.2f})")
    elif "_dominant_signal" in contributions:
        explanations.append(
            f"Risk driven by a single strong signal: {dominant_name} "
            f"(score x confidence = {dominant:.2f} > corroboration average)")
    for s in signals:
        if s.available and (s.score >= 0.2 or any("FAILED" in f for f in s.findings)):
            for f in s.findings:
                explanations.append(f"[{s.name}] {f}")
        elif not s.available:
            explanations.append(f"[{s.name}] skipped: {s.skip_reason}")
    if not explanations:
        explanations.append("No signal reported significant evidence of tampering")

    return {
        "fused_score": round(fused, 4),
        "risk_tier": tier,
        "contributions": contributions,
        "explanations": explanations,
    }
