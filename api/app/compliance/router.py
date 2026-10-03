"""
router.py
---------
RBI Compliance endpoints, mounted into the main FinSight API under /compliance.

This is the RBI Compliance Checker's backend (knowledge base + BM25 retrieval,
safe no-eval rules engine, maker-checker rule extraction workflow, audit log)
integrated as a FinSight module. Role expectations, enforced by the frontend:
  * Head Manager  — knowledge-base management, rule creation, draft review.
  * Officer+Head  — per-applicant compliance check, regulatory Q&A (chatbot).

Call startup() once at app boot: it creates/seeds the SQLite DB and builds the
in-memory BM25 index.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from . import db as dbm
from .extraction import extract_candidates
from .models import (
    ApproveCandidate,
    DocumentIn,
    ExtractRequest,
    LoanApplication,
    RagQuery,
    RejectCandidate,
    RuleIn,
)
from .retrieval import BM25Index
from .rules_engine import evaluate_application

router = APIRouter(prefix="/compliance", tags=["compliance"])

# A single in-memory BM25 index, rebuilt whenever the KB changes.
INDEX = BM25Index()


def rebuild_index() -> None:
    conn = dbm.get_db()
    try:
        INDEX.build(dbm.load_chunks_for_index(conn))
    finally:
        conn.close()


def startup() -> None:
    """Initialise + seed the compliance DB and build the retrieval index."""
    conn = dbm.get_db()
    try:
        dbm.init_db(conn)
        dbm.seed_if_empty(conn)
        _correct_face_threshold(conn)
        _stage_gate_offer_rules(conn)
    finally:
        conn.close()
    rebuild_index()


def _correct_face_threshold(conn) -> None:
    """One-time, audited correction of the seeded FACE_001 rule: its 0.62
    threshold was on a different model's scale; FinSight's face module uses
    SFace, whose validated same-person threshold is 0.363. A database seeded
    before this fix is corrected in place (and logged), never silently."""
    import json
    row = conn.execute("SELECT condition_json FROM rules WHERE rule_id='FACE_001'").fetchone()
    if not row:
        return
    cond = json.loads(row["condition_json"])
    if cond.get("value") == 0.62:
        cond["value"] = 0.363
        conn.execute("UPDATE rules SET condition_json=?, regulation_ref=? WHERE rule_id='FACE_001'",
                     (json.dumps(cond), "Face match score >= 0.363 (SFace cosine)"))
        conn.commit()
        dbm.audit(conn, "system:agents-migration", "rule_corrected", "rule", "FACE_001",
                  {"field": "risk.face_match_score", "from": 0.62, "to": 0.363,
                   "reason": "threshold aligned to the SFace model used by the face module"})


def _stage_gate_offer_rules(conn) -> None:
    """One-time, audited correction of the seeded loan-terms rules (KFS, cooling-off, fund flow, penal
    charges): they were evaluated at credit assessment, before any offer or loan terms exist, so every
    application ended in NEEDS_INFO and no recommendation could ever be 'approve'. They now apply from
    the offer stage on (regulatory_data.FROM_OFFER_STAGE). Only untouched seed rules are changed."""
    import json
    from .regulatory_data import FROM_OFFER_STAGE, OFFER_STAGE_RULES
    changed = []
    for rid in OFFER_STAGE_RULES:
        row = conn.execute("SELECT applies_when_json, origin FROM rules WHERE rule_id=?", (rid,)).fetchone()
        if row and row["origin"] == "seed" and not row["applies_when_json"]:
            conn.execute("UPDATE rules SET applies_when_json=? WHERE rule_id=?", (json.dumps(FROM_OFFER_STAGE), rid))
            changed.append(rid)
    if changed:
        conn.commit()
        dbm.audit(conn, "system:agents-migration", "rule_corrected", "rule", ",".join(changed),
                  {"applies_when": FROM_OFFER_STAGE,
                   "reason": "loan-terms rules apply once an offer exists, not during credit assessment"})


def get_conn():
    conn = dbm.get_db()
    try:
        yield conn
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Knowledge base
# --------------------------------------------------------------------------
@router.post("/kb/documents")
def ingest_document(doc: DocumentIn, conn: sqlite3.Connection = Depends(get_conn)):
    """Ingest a regulatory document (title + full text); chunked and indexed.
    Runs as the KB Ingestion agent."""
    from ..agents.registry import get_agent
    try:
        res = get_agent("kb_ingestion").run(
            {"title": doc.title, "category": doc.category, "source_type": doc.source_type,
             "text": doc.text, "effective_date": doc.effective_date, "conn": conn}, flow="kb_ingest")
    except sqlite3.IntegrityError:
        raise HTTPException(400, "A document with this title already exists.")
    return {"id": res.payload["id"], "title": doc.title, "status": "completed", "chunks": res.payload["chunks"]}


@router.get("/kb/documents")
def list_documents(category: str = "", conn: sqlite3.Connection = Depends(get_conn)):
    if category:
        rows = conn.execute(
            "SELECT * FROM documents WHERE category=? ORDER BY id DESC", (category,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM documents ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


@router.get("/kb/documents/{doc_id}/chunks")
def document_chunks(doc_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    rows = conn.execute(
        "SELECT id, chunk_index, section_title, chunk_text FROM chunks WHERE document_id=? ORDER BY chunk_index",
        (doc_id,),
    ).fetchall()
    return [dict(r) for r in rows]


@router.delete("/kb/documents/{doc_id}")
def delete_document(doc_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    conn.execute("UPDATE documents SET status='removed' WHERE id=?", (doc_id,))
    conn.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
    conn.commit()
    rebuild_index()
    return {"message": "Removed", "id": doc_id}


# --------------------------------------------------------------------------
# RAG query (BM25 retrieval over regulatory text) — powers the chatbot
# --------------------------------------------------------------------------
@router.post("/rag/query")
def rag_query(q: RagQuery):
    """Regulatory Q&A agent: BM25 retrieval, plus (synthesize=true) a cited
    answer that is kept only if fully grounded in the retrieved passages."""
    from ..agents.registry import get_agent
    res = get_agent("regulatory_qa").run({"query": q.query, "top_k": q.top_k, "synthesize": q.synthesize},
                                         flow="regulatory_qa")
    return res.payload


def _rag_query_extractive(q: RagQuery):  # pre-agent implementation, kept for reference/kill switch
    hits = INDEX.search(q.query, top_k=q.top_k)
    if not hits:
        return {
            "query": q.query,
            "answer": "No relevant regulatory provisions found in the knowledge base.",
            "confidence": "None",
            "citations": [],
        }
    confidence = "High" if hits[0]["score"] >= 4 else "Medium" if hits[0]["score"] >= 1.5 else "Low"
    top = hits[0]
    answer = (
        f"Most relevant provision — {top['document_title']}"
        f"{' · ' + top['section_title'] if top['section_title'] else ''}:\n\n"
        f"{top['text']}"
    )
    return {
        "query": q.query,
        "answer": answer,
        "confidence": confidence,
        "citations": [
            {
                "document_title": h["document_title"],
                "section_title": h["section_title"],
                "category": h["category"],
                "score": h["score"],
                "text": h["text"],
            }
            for h in hits
        ],
    }


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------
@router.get("/rules")
def list_rules(conn: sqlite3.Connection = Depends(get_conn)):
    import json

    rows = conn.execute("SELECT * FROM rules WHERE is_active=1 ORDER BY rule_id").fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["condition"] = json.loads(d.pop("condition_json"))
        d["applies_when"] = json.loads(d["applies_when_json"]) if d.get("applies_when_json") else None
        d.pop("applies_when_json", None)
        d["required_fields"] = json.loads(d["required_fields"]) if d.get("required_fields") else []
        result.append(d)
    return result


@router.post("/rules")
def create_rule(rule: RuleIn, conn: sqlite3.Connection = Depends(get_conn)):
    from .rule_validation import rule_problems
    problems = rule_problems(rule.model_dump())
    if problems:
        raise HTTPException(400, "This rule cannot go live: " + "; ".join(problems) + ".")
    try:
        dbm.insert_rule(conn, rule.model_dump(), origin="manual")
    except sqlite3.IntegrityError:
        raise HTTPException(400, "A rule with this rule_id already exists.")
    dbm.audit(conn, "head_manager", "rule_created", "rule", rule.rule_id, {"origin": "manual"})
    return {"rule_id": rule.rule_id, "status": "created"}


@router.delete("/rules/{rule_id}")
def deactivate_rule(rule_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    conn.execute("UPDATE rules SET is_active=0 WHERE rule_id=?", (rule_id,))
    conn.commit()
    dbm.audit(conn, "head_manager", "rule_deactivated", "rule", rule_id, {})
    return {"message": "Deactivated", "rule_id": rule_id}


# --------------------------------------------------------------------------
# Rule extraction + maker-checker review workflow
# --------------------------------------------------------------------------
@router.post("/kb/documents/{doc_id}/extract")
def extract_rules(doc_id: int, req: ExtractRequest = ExtractRequest(),
                  conn: sqlite3.Connection = Depends(get_conn)):
    """Extract DRAFT rule candidates from a document. Candidates go into the
    review queue — they do NOT become active rules until a reviewer approves."""
    doc = conn.execute("SELECT * FROM documents WHERE id=? AND status='active'", (doc_id,)).fetchone()
    if not doc:
        raise HTTPException(404, "Document not found.")
    chunk_rows = conn.execute(
        "SELECT id AS chunk_id, document_id, section_title, chunk_text AS text FROM chunks WHERE document_id=? ORDER BY chunk_index",
        (doc_id,),
    ).fetchall()
    chunks = [dict(r) for r in chunk_rows]
    if not chunks:
        return {"candidates": 0, "method": "none", "message": "Document has no text chunks."}

    from ..agents.registry import get_agent
    res = get_agent("rule_authoring").run(
        {"chunks": chunks, "document_title": doc["title"], "document_category": doc["category"],
         "prefer_llm": req.prefer_llm, "document_id": doc_id}, flow="rule_authoring")
    candidates, method = res.payload["candidates"], res.payload["method"]
    for c in candidates:
        c.setdefault("proposed_category", doc["category"])
    ids = dbm.insert_candidates(conn, candidates)
    dbm.audit(conn, f"extractor:{method}", "extract", "document", doc_id,
              {"candidates": len(ids), "method": method})
    return {"candidates": len(ids), "method": method, "candidate_ids": ids}


@router.get("/rules/candidates")
def list_candidates(status: str = "pending", conn: sqlite3.Connection = Depends(get_conn)):
    return dbm.list_candidates(conn, status=status)


@router.post("/rules/candidates/{candidate_id}/approve")
def approve_candidate(candidate_id: int, body: ApproveCandidate,
                      conn: sqlite3.Connection = Depends(get_conn)):
    overrides = {k: v for k, v in body.model_dump().items()
                 if k != "reviewer" and v is not None}
    try:
        rule_id = dbm.approve_candidate(conn, candidate_id, overrides, body.reviewer)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "approved", "rule_id": rule_id, "candidate_id": candidate_id}


@router.post("/rules/candidates/{candidate_id}/reject")
def reject_candidate(candidate_id: int, body: RejectCandidate,
                     conn: sqlite3.Connection = Depends(get_conn)):
    try:
        dbm.reject_candidate(conn, candidate_id, body.reviewer, body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "rejected", "candidate_id": candidate_id}


@router.get("/audit")
def audit_log(limit: int = 100, conn: sqlite3.Connection = Depends(get_conn)):
    return dbm.list_audit(conn, limit=limit)


# --------------------------------------------------------------------------
# Compliance check — used by the Customer 360 "RBI Compliance" tab
# --------------------------------------------------------------------------
@router.post("/policy/check")
def check_application(application: LoanApplication, conn: sqlite3.Connection = Depends(get_conn)):
    rules = dbm.load_rules(conn, active_only=True)
    context = application.model_dump()
    result = evaluate_application(rules, context)
    result["application_id"] = application.application_id
    result["loan_type"] = application.loan_type
    return result


# --------------------------------------------------------------------------
# Stats
# --------------------------------------------------------------------------
@router.get("/stats")
def stats(conn: sqlite3.Connection = Depends(get_conn)):
    total_docs = conn.execute("SELECT COUNT(*) FROM documents WHERE status='active'").fetchone()[0]
    total_chunks = conn.execute(
        "SELECT COUNT(*) FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.status='active'"
    ).fetchone()[0]
    active_rules = conn.execute("SELECT COUNT(*) FROM rules WHERE is_active=1").fetchone()[0]
    mandatory = conn.execute(
        "SELECT COUNT(*) FROM rules WHERE is_active=1 AND rule_type='mandatory'"
    ).fetchone()[0]
    pending_candidates = conn.execute(
        "SELECT COUNT(*) FROM rule_candidates WHERE status='pending'"
    ).fetchone()[0]
    by_category = conn.execute(
        "SELECT category, COUNT(*) c FROM documents WHERE status='active' GROUP BY category"
    ).fetchall()
    return {
        "active_documents": total_docs,
        "total_chunks": total_chunks,
        "active_rules": active_rules,
        "mandatory_rules": mandatory,
        "pending_candidates": pending_candidates,
        "documents_by_category": {r["category"]: r["c"] for r in by_category},
    }
