"""Identity & fraud-evidence crew — document-level agents 6-10
(diagrams 3 and 4: face matching, the nine-signal tampering cascade)."""
from __future__ import annotations

import asyncio
import re

from ..base import AgentOutcome, FinSightAgent, grounded
from ..context import note_tool_call
from ..registry import implements
from ..tracing.redact import digest, scrub_text

_TIER_OUTCOME = {"LOW": "pass", "MEDIUM": "flag", "HIGH": "fail"}
_FACE_OUTCOME = {"match": "pass", "mismatch": "fail", "uncertain": "uncertain", "insufficient": "info"}


@implements("face_verification")
class FaceVerificationAgent(FinSightAgent):
    """YuNet detection at four rotations + SFace embeddings; pairwise verdicts.
    A face found only after contrast enhancement may confirm a match but never
    assert a confident mismatch."""

    def input_digest(self, inputs):
        return {"documents": [{"doc_type": d, "sha12": digest(b)} for d, b in inputs["documents"]]}

    def execute(self, inputs):
        from ...face.service import verify
        res = self.tool("face.service.verify", verify, inputs["documents"], return_embeddings=True)
        embeddings = res.pop("_embeddings", {})
        sims = [p["similarity"] for p in res["pairs"]]
        overall = res["overall"]
        return AgentOutcome(
            payload={"result": res, "embeddings": embeddings},
            ai_outcome=_FACE_OUTCOME.get(overall, "info"),
            confidence=min(sims) if sims else None,
            summary=f"{overall} · {len(res['pairs'])} pair(s)"
                    + (f" · weakest similarity {min(sims):.2f}" if sims else ""),
            output_summary={"overall": overall,
                            "faces": {d["doc_type"]: d["face_count"] for d in res["documents"]},
                            "pairs": [{"a": p["a"], "b": p["b"], "similarity": p["similarity"],
                                       "verdict": p["verdict"]} for p in res["pairs"]],
                            "low_quality": [d["doc_type"] for d in res["documents"]
                                            if any(f.get("quality") == "low" for f in d["faces"])]})


@implements("authenticity")
class AuthenticityAgent(FinSightAgent):
    """The one LLM detector: judges whether an income document was fabricated
    from scratch. Fenced: runs only for configured doc types, grounding gate on
    every cited number, arithmetic/magnitude claims dropped, and capped in
    fusion (alone it can reach MEDIUM, never HIGH)."""

    def input_digest(self, inputs):
        return {"doc_type": inputs["doc_type"], "sha12": digest(inputs["data"])}

    def execute(self, inputs):
        from ...tampering.pipeline import run_authenticity
        sig = self.tool("pipeline.run_authenticity", run_authenticity, inputs["data"],
                        inputs["doc_type"], inputs.get("parsed") or {})
        if not sig.available:
            return AgentOutcome(payload=sig, status="skipped", summary=f"Not run: {sig.skip_reason}")
        outcome = "fail" if sig.score >= 0.7 else ("flag" if sig.score >= 0.5 else
                                                   ("uncertain" if sig.score > 0 else "pass"))
        return AgentOutcome(payload=sig, ai_outcome=outcome, score=sig.score,
                            summary=scrub_text(sig.findings[0] if sig.findings else "assessed", 200),
                            output_summary={"score": sig.score, "findings": len(sig.findings)})


@implements("tampering_forensics")
class TamperingForensicsAgent(FinSightAgent):
    """Stage-1 signals → gate → stage-2 pixel forensics → fusion. Each signal is
    recorded as a tool call. Evidence for a human, never an auto-reject."""

    def input_digest(self, inputs):
        return {"doc_type": inputs["doc_type"], "sha12": digest(inputs["data"]),
                "bytes": len(inputs["data"])}

    def execute(self, inputs):
        from ...tampering.service import analyze

        def observer(name, ms, result):
            note_tool_call({"tool": f"signal:{name}", "duration_ms": ms,
                            "status": "ok" if result.available else "skipped",
                            "detail": (f"score {result.score:.2f}" if result.available
                                       else scrub_text(result.skip_reason or "", 120))})

        res = analyze(inputs["data"], inputs.get("filename") or "upload", inputs["doc_type"],
                      inputs.get("parsed") or {}, observer=observer,
                      authenticity_result=inputs.get("authenticity_result"),
                      include_internals=True)
        if res is None:
            return AgentOutcome(payload=None, status="degraded", ai_outcome="uncertain",
                                summary="Tampering analysis could not run",
                                warnings=["cascade returned no result"])
        internals = res.pop("_internals", {})
        fired = [s["name"] for s in res["signals"] if s["available"] and (s["score"] or 0) >= 0.2]
        return AgentOutcome(
            payload={"tampering": res, "internals": internals},
            ai_outcome=_TIER_OUTCOME.get(res["risk_tier"], "info"), score=res["risk_score"],
            summary=f"{res['risk_tier']} ({res['risk_score']:.2f})"
                    + (f" · {', '.join(fired)}" if fired else "") + (" · fast-tracked" if res["fast_tracked"] else ""),
            output_summary={"tier": res["risk_tier"], "score": res["risk_score"],
                            "source_format": res["source_format"], "fast_tracked": res["fast_tracked"],
                            "signals": {s["name"]: s["score"] for s in res["signals"] if s["available"]},
                            "dominant": internals.get("contributions", {}).get("_dominant_signal"),
                            "corroborated": internals.get("contributions", {}).get("_corroborated_signals")})


@implements("tamper_explainer")
class TamperExplainerAgent(FinSightAgent):
    """Translation only: restates the cascade's findings in plain language (and,
    with vision, says where on the page they are). It never sees the tier as
    something to decide and cannot change it; any sentence that cites a figure
    not present in the findings is dropped by the grounding gate."""

    def input_digest(self, inputs):
        t = inputs["tampering"]
        return {"doc_type": inputs.get("doc_type"), "tier": t.get("risk_tier"), "sha12": digest(inputs["data"])}

    def execute(self, inputs):
        from ...tampering.explain import explain
        tampering = inputs["tampering"]
        res = asyncio.run(explain(inputs["doc_label"], tampering, inputs["data"]))
        if res is None:
            return AgentOutcome(payload=None, status="degraded", ai_outcome="uncertain",
                                summary="Explanation unavailable (model busy, unreachable or disabled)")
        evidence = {"findings": tampering.get("explanations", []), "signals": tampering.get("signals", [])}
        kept, dropped = [], []
        for issue in res.get("issues", []):
            (kept if grounded(issue, evidence)[0] else dropped).append(issue)
        summary = res.get("summary", "")
        if summary and not grounded(summary, evidence)[0]:
            summary = ("Some details on this document need checking. A reviewer will look at the "
                       "points listed below.")
            dropped.append("(summary)")
        warnings = [f"dropped {len(dropped)} ungrounded sentence(s)"] if dropped else []
        payload = {"summary": summary, "issues": kept or res.get("issues", [])[:0]}
        if not payload["issues"] and not payload["summary"]:
            return AgentOutcome(payload=None, status="degraded", summary="Explanation failed grounding")
        return AgentOutcome(payload=payload, ai_outcome="info", warnings=warnings,
                            summary=f"{len(kept)} plain-language point(s)",
                            output_summary={"issues": len(kept), "dropped": len(dropped),
                                            "tier_unchanged": True})


@implements("tamper_locator")
class TamperLocatorAgent(FinSightAgent):
    """Boxes on the page every figure/ID a finding cites (OCR-grounded; a
    finding with no on-page value is honestly reported as not locatable)."""

    def input_digest(self, inputs):
        return {"tier": inputs["tampering"].get("risk_tier"), "sha12": digest(inputs["data"])}

    def execute(self, inputs):
        from ...tampering.locate import locate
        res = self.tool("tampering.locate", locate, inputs["tampering"], inputs["data"])
        if res is None:
            return AgentOutcome(payload=None, status="degraded", summary="Could not locate issues")
        n = len(res["annotations"])
        return AgentOutcome(payload=res, ai_outcome="info",
                            summary=f"{res['located_count']}/{n} finding(s) boxed on the page",
                            output_summary={"located": res["located_count"], "findings": n,
                                            "ocr_tokens": res["ocr_tokens"]})


@implements("pii_masking")
class PiiMaskingAgent(FinSightAgent):
    """PAN + Aadhaar only, found by pattern on the page (not trusted from the
    extracted fields), last four characters kept, drawn in the card's own ink."""

    def input_digest(self, inputs):
        return {"doc_type": inputs.get("doc_type"), "sha12": digest(inputs["data"])}

    def execute(self, inputs):
        from ...mask import mask_document
        res = self.tool("mask.mask_document", mask_document, inputs["data"], inputs.get("parsed") or {},
                        inputs.get("doc_type") or "generic")
        if res is None:
            return AgentOutcome(payload=None, status="degraded", summary="Could not render a masked preview")
        if res.get("withheld"):
            return AgentOutcome(payload=res, ai_outcome="flag",
                                summary="ID number not located on the card - preview withheld, redact by hand",
                                output_summary={"masked_count": 0, "labels": [], "withheld": True})
        labels = ", ".join(res["labels"]) or "nothing to mask"
        return AgentOutcome(payload=res, ai_outcome="info",
                            summary=f"Masked {res['masked_count']} region(s) · {labels}",
                            output_summary={"masked_count": res["masked_count"], "labels": res["labels"]})


def _clean(s: str) -> str:
    return re.sub(r"^\[[^\]]+\]\s*", "", s)
