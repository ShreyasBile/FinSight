"""Liveness agent (29) — passive anti-spoofing on the wizard selfie plus a
match against the ID photo already on the case. Deterministic; advisory:
a failed check asks for a retake or an officer's look, never a rejection."""
from __future__ import annotations

from ..base import AgentOutcome, FinSightAgent
from ..registry import implements

_OUTCOME = {"live_match": "pass", "live_no_reference": "pass", "live_uncertain_match": "uncertain",
            "live_mismatch": "flag", "spoof_suspected": "flag", "no_face": "uncertain",
            "multiple_faces": "uncertain", "unreadable": "uncertain"}
_SUMMARY = {"live_match": "Live selfie, same person as the ID photo",
            "live_no_reference": "Live selfie (no ID photo on the case to compare)",
            "live_uncertain_match": "Live selfie; match with the ID photo inconclusive",
            "live_mismatch": "Live selfie, but a DIFFERENT person from the ID photo",
            "spoof_suspected": "Selfie looks like a printed photo or a screen — retake needed",
            "no_face": "No face in the selfie — retake needed",
            "multiple_faces": "More than one face in the selfie — retake needed",
            "unreadable": "Selfie image could not be read"}


@implements("liveness")
class LivenessAgent(FinSightAgent):
    def input_digest(self, inputs):
        return {"bytes": len(inputs.get("data") or b""), "case": bool(inputs.get("case_id"))}

    def execute(self, inputs):
        from ...face.liveness import check
        refs = inputs.get("id_embeddings")
        if refs is None and inputs.get("case_id"):
            from ..tools.dedupe import faces_for_case
            refs = self.tool("dedupe.faces_for_case", faces_for_case, inputs["case_id"])
        res = self.tool("liveness.check", check, inputs["data"], refs or None)
        v = res["verdict"]
        return AgentOutcome(
            payload=res, ai_outcome=_OUTCOME.get(v, "uncertain"), score=res.get("live_score"),
            confidence=res.get("live_score"), summary=_SUMMARY.get(v, v),
            status="ok" if v not in ("unreadable",) else "degraded",
            output_summary={"verdict": v, "live_score": res.get("live_score"),
                            "similarity": (res.get("match") or {}).get("similarity")})
