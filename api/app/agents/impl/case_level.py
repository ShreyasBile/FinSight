"""Case-level agents 11-13 and 19 (diagram 5: four evidence producers, one
fused score; diagram 8: live policy check). They work from the case file the
document agents filled — no re-upload, no LLM."""
from __future__ import annotations

from ..base import AgentOutcome, FinSightAgent
from ..registry import implements
from ..tools import dedupe, fraud_fusion, policy_context

_DOC_LABEL = {"pan": "PAN Card", "aadhaar": "Aadhaar Card", "salary_slip": "Salary Slip",
              "bank_statement": "Bank Statement", "itr": "ITR / Form 16", "form16": "Form 16",
              "gst_return": "GST Return", "cibil": "CIBIL Report", "rc_book": "RC Book",
              "insurance": "Insurance Copy", "property_doc": "Property Document",
              "land_record": "Land Record", "vehicle_quotation": "Vehicle Quotation"}


@implements("cross_verification")
class CrossVerificationAgent(FinSightAgent):
    """Indian-aware consistency across documents: names (initials, honorifics,
    surname-first order, surname change after marriage), DOB, gender. Ownership
    documents (land/property) in a family member's name are a 'look', not an error."""

    def execute(self, inputs):
        from ...docrules import cross_verify_documents
        case = inputs["case"]
        docs_map = {dt: {"parsed": d.get("parsed"), "label": _DOC_LABEL.get(dt, dt)}
                    for dt, d in (case.get("documents") or {}).items() if d.get("parsed")}
        checks = self.tool("docrules.cross_verify_documents", cross_verify_documents, docs_map)
        levels = [c["level"] for c in checks]
        outcome = "fail" if "error" in levels else ("flag" if "warn" in levels else
                                                    ("pass" if checks else "info"))
        conf = round(levels.count("ok") / len(levels), 3) if levels else None
        return AgentOutcome(payload=checks, ai_outcome=outcome, confidence=conf,
                            summary=(f"{len(checks)} check(s): " + ", ".join(
                                f"{c['level']}" for c in checks)) if checks else "Fewer than two documents to compare",
                            output_summary={"checks": [{"level": c["level"], "title": c["title"]} for c in checks],
                                            "documents": sorted(docs_map)})


@implements("dedupe")
class DedupeAgent(FinSightAgent):
    """Same file / PAN / Aadhaar / account / face in another application.
    Keys are HMAC digests; face embeddings stay in the local index."""

    def execute(self, inputs):
        res = self.tool("dedupe.check", dedupe.check, inputs["case_id"], inputs["case"],
                        register=inputs.get("register", True))
        hits = res["hits"]
        outcome = "fail" if res["floor"] else ("flag" if hits else "pass")
        return AgentOutcome(payload=res, ai_outcome=outcome, score=res["score"],
                            summary=(f"{len(hits)} match(es) in other applications — strongest: {hits[0]['kind']}"
                                     if hits else f"No duplicates ({', '.join(res['keys_checked']) or 'no keys'}"
                                                  f"{', ' + str(res['faces_checked']) + ' face(s)' if res['faces_checked'] else ''})"),
                            output_summary={"hits": [{"kind": h["kind"], "keys": h["keys"], "score": h["score"],
                                                      "face_similarity": h["face_similarity"]} for h in hits],
                                            "keys_checked": res["keys_checked"], "faces_checked": res["faces_checked"]})


@implements("fraud_fusion")
class FraudFusionAgent(FinSightAgent):
    """Weighted sum over available components + dominant floors. Advisory."""

    def execute(self, inputs):
        case = inputs["case"]
        docs = case.get("documents") or {}
        tamper, tamper_ev, tamper_high = fraud_fusion.tampering_component(docs)
        face, face_ev = fraud_fusion.face_component(case.get("face"))
        cross, cross_ev = fraud_fusion.cross_check_component(inputs.get("cross_checks"))
        valid, valid_ev = fraud_fusion.validation_component(docs)
        dd = inputs.get("dedupe") or {}
        dd_score = dd.get("score") if dd else None
        dd_ev = [f"{h['kind']} ({', '.join(h['keys']) or 'face'})" for h in dd.get("hits", [])] or \
                (["no duplicates found"] if dd else ["not checked"])
        pat = inputs.get("patterns") or {}
        pat_score = pat.get("score") if pat else None
        pat_ev = [f"{f['pattern']}: {f['detail']}" for f in pat.get("flags", [])] or \
                 (["no suspicious statement patterns"] if pat else ["no transaction-level statement"])

        floors = []
        if (case.get("face") or {}).get("overall") == "mismatch":
            floors.append("face_mismatch_confident")
        if dd.get("floor"):
            floors.append("exact_duplicate_identity")
        if tamper_high:
            floors.append("tamper_high")

        comps = {"tampering": tamper, "face": face, "cross_check": cross, "validation": valid,
                 "dedupe": dd_score, "patterns": pat_score}
        fused = self.tool("fraud_fusion.fuse", fraud_fusion.fuse, comps, floors)
        evidence = {"tampering": tamper_ev, "face": face_ev, "cross_check": cross_ev,
                    "validation": valid_ev, "dedupe": dd_ev, "patterns": pat_ev}
        assessment = {**fused, "components": {k: {"score": comps[k], "available": comps[k] is not None,
                                                  "weight": fraud_fusion.load_weights()["weights"][k],
                                                  "evidence": evidence[k]} for k in fraud_fusion.COMPONENTS}}
        return AgentOutcome(
            payload=assessment, ai_outcome={"LOW": "pass", "MEDIUM": "flag", "HIGH": "fail"}[fused["tier"]],
            score=fused["fused_score"],
            summary=f"{fused['tier']} ({fused['fused_score']:.2f}) · driven by {fused['dominant_component'] or '—'}"
                    + (f" · floors: {', '.join(floors)}" if floors else ""),
            output_summary={"tier": fused["tier"], "fused": fused["fused_score"], "floors": floors,
                            "components": {k: comps[k] for k in fraud_fusion.COMPONENTS},
                            "weights_version": fused["weights_version"]})


@implements("policy_check")
class PolicyCheckAgent(FinSightAgent):
    """Every active RBI/internal rule against evidence the agents established;
    fields nobody established stay out, so the answer is NEEDS_INFO, not a
    false PASS. auto_approval_allowed is informational — a person decides."""

    def execute(self, inputs):
        ctx = inputs.get("context") or policy_context.build_context(
            inputs["case"], inputs.get("extra"), inputs.get("assessment"))
        res = self.tool("rules_engine.evaluate_application", policy_context.evaluate, ctx)
        status = res["overall_status"]
        outcome = {"PASS": "pass", "PASS_WITH_WARNINGS": "flag", "FAIL": "fail",
                   "NEEDS_INFO": "uncertain"}.get(status, "info")
        sm = res.get("summary", {})
        return AgentOutcome(payload=res, ai_outcome=outcome,
                            summary=f"{status} · {sm.get('passed', 0)} pass, {sm.get('failed', 0)} fail, "
                                    f"{sm.get('inconclusive', 0)} need info",
                            output_summary={"overall_status": status, "summary": sm,
                                            "failed": [c["rule_id"] for c in res.get("checks", []) if c["status"] == "FAIL"]})
