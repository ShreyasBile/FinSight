"""Compliance crew — agents 16-18 (diagram 8: knowledge base, maker-checker
rule drafts, RAG query). Policy Check (19) lives in case_level.py."""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from ..base import AgentOutcome, FinSightAgent, grounded
from ..registry import implements


@implements("kb_ingestion")
class KbIngestionAgent(FinSightAgent):
    """Stores a regulatory document, chunks it, and rebuilds the BM25 index."""

    def input_digest(self, inputs):
        return {"title": inputs["title"][:80], "chars": len(inputs.get("text", ""))}

    def execute(self, inputs):
        from ...compliance import db as dbm
        from ...compliance.router import rebuild_index
        conn = inputs.get("conn") or dbm.get_db()
        doc_id = self.tool("compliance.insert_document", dbm.insert_document, conn,
                           title=inputs["title"], category=inputs.get("category", "General"),
                           source_type=inputs.get("source_type", "RBI"),
                           sections=[{"section_title": inputs["title"], "text": inputs["text"]}],
                           effective_date=inputs.get("effective_date"))
        self.tool("compliance.rebuild_index", rebuild_index)
        chunks = conn.execute("SELECT chunk_count FROM documents WHERE id=?", (doc_id,)).fetchone()["chunk_count"]
        return AgentOutcome(payload={"id": doc_id, "chunks": chunks}, ai_outcome="info",
                            summary=f"Indexed '{inputs['title'][:60]}' into {chunks} passage(s)",
                            output_summary={"doc_id": doc_id, "chunks": chunks})


@implements("rule_authoring")
class RuleAuthoringAgent(FinSightAgent):
    """Maker side of maker-checker: drafts rule CANDIDATES (LLM per chunk,
    deterministic modal-verb heuristic as fallback). It has no approve or
    reject tool — a human checker decides every draft."""

    def input_digest(self, inputs):
        return {"document_id": inputs.get("document_id"), "chunks": len(inputs["chunks"])}

    def execute(self, inputs):
        from ...compliance.extraction import extract_candidates
        cands, method = self.tool("extraction.extract_candidates", extract_candidates, inputs["chunks"],
                                  document_title=inputs["document_title"],
                                  document_category=inputs.get("document_category", "General"),
                                  prefer_llm=inputs.get("prefer_llm", True))
        mapped = sum(1 for c in cands if not c.get("needs_field_mapping"))
        return AgentOutcome(payload={"candidates": cands, "method": method}, ai_outcome="info",
                            summary=f"{len(cands)} draft rule(s) via {method} · {mapped} with a mapped field "
                                    "· awaiting human review",
                            output_summary={"candidates": len(cands), "method": method, "mapped": mapped})


class _QaAnswer(BaseModel):
    answer: str = Field(description="2-5 sentences answering ONLY from the passages")
    # required (no default): with schema-constrained decoding an optional field
    # is simply left out, and an uncited answer is rejected by the agent
    cited: list[int] = Field(description="numbers of the passages used, e.g. [1, 3]")
    insufficient: bool = Field(default=False, description="true if the passages do not answer the question")


_QA_BACKSTORY = ("You advise loan officers at an Indian NBFC on RBI regulation. You answer ONLY from the "
                 "numbered passages you are given and cite them by number. If the passages do not answer "
                 "the question, you say so. You never add rules, numbers or dates that are not in the passages.")


@implements("regulatory_qa")
class RegulatoryQaAgent(FinSightAgent):
    """Retrieval (BM25) + a cited, grounded answer. The LLM answer is kept only
    if every figure it states appears in the retrieved passages and it cites a
    passage that exists; otherwise the verbatim top passage is returned (the
    pre-agent behaviour), so an unsupported sentence never reaches an officer."""

    def input_digest(self, inputs):
        return {"query_chars": len(inputs["query"]), "top_k": inputs.get("top_k", 5)}

    def execute(self, inputs):
        from ...compliance.router import INDEX
        q, k = inputs["query"], inputs.get("top_k", 5)
        hits = self.tool("bm25.search", INDEX.search, q, top_k=k)
        if not hits:
            return AgentOutcome(payload={"query": q, "answer": "No relevant regulatory provisions found in the knowledge base.",
                                         "confidence": "None", "citations": [], "synthesized": False},
                                ai_outcome="uncertain", summary="No passage retrieved")
        confidence = "High" if hits[0]["score"] >= 4 else "Medium" if hits[0]["score"] >= 1.5 else "Low"
        top = hits[0]
        extractive = (f"Most relevant provision — {top['document_title']}"
                      f"{' · ' + top['section_title'] if top['section_title'] else ''}:\n\n{top['text']}")
        payload = {"query": q, "answer": extractive, "confidence": confidence, "synthesized": False,
                   "citations": [{"document_title": h["document_title"], "section_title": h["section_title"],
                                  "category": h.get("category"), "score": h["score"], "text": h["text"]} for h in hits]}
        outcome = AgentOutcome(payload=payload, ai_outcome="info", confidence=None,
                               summary=f"{len(hits)} passage(s) · top score {top['score']:.1f} ({confidence})",
                               output_summary={"hits": len(hits), "top_score": round(top["score"], 2),
                                               "confidence": confidence})
        if inputs.get("synthesize"):
            self._synthesize(q, hits, payload, outcome)
        return outcome

    def _synthesize(self, q, hits, payload, outcome):
        from ..crewai_compat import kickoff_structured
        passages = "\n\n".join(f"[{i}] {h['document_title']} — {h['section_title'] or ''}\n{h['text']}"
                               for i, h in enumerate(hits, 1))
        prompt = (f"Question from a loan officer: {q}\n\nPassages:\n{passages}\n\n"
                  "Answer the question using only these passages. Cite passage numbers.")
        try:
            ans = kickoff_structured(self.spec, _QA_BACKSTORY, prompt, _QaAnswer, timeout_s=90)
        except Exception as exc:  # noqa: BLE001
            outcome.warnings.append(f"synthesis unavailable ({type(exc).__name__}) — extractive answer shown")
            payload["grounded"] = None
            return
        cited_ok = bool(ans.cited) and all(1 <= c <= len(hits) for c in ans.cited)
        # the model is shown each passage's document and section title, so a
        # regulation's year ("Directions, 2025") is grounded, not invented
        evidence = [f"{h['document_title']} {h.get('section_title') or ''} {h['text']}" for h in hits] + [q]
        num_ok, bad = grounded(ans.answer, evidence)
        if ans.insufficient:
            payload.update(answer="The knowledge base passages retrieved do not answer this question directly. "
                                  "The closest provisions are listed below.", synthesized=True, grounded=True)
            outcome.summary += " · answer: insufficient passages"
        elif cited_ok and num_ok:
            refs = ", ".join(f"[{c}]" for c in sorted(set(ans.cited)))
            payload.update(answer=f"{ans.answer.strip()}\n\nSources: {refs}", synthesized=True, grounded=True,
                           cited=sorted(set(ans.cited)))
            outcome.summary += " · synthesized, grounded"
        else:
            payload["grounded"] = False
            why = "cites no valid passage" if not cited_ok else f"states figures not in the passages {sorted(bad)[:4]}"
            outcome.warnings.append(f"synthesized answer rejected ({why}) — extractive answer shown")
        outcome.output_summary.update(synthesized=payload["synthesized"], grounded=payload.get("grounded"))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()
