"""
db.py
-----
SQLite schema, connection helper, and one-time seeding of the regulatory
knowledge base and compliance rules.

Design notes:
  * Parameterised queries everywhere (the prototype had an f-string SQL
    injection in the category filter — fixed here).
  * Rules store their condition tree / applies_when / required_fields as
    JSON text and are hydrated back into rules_engine.Rule objects.
  * Seeding is idempotent: it only runs when a table is empty, so restarts
    don't duplicate rows, but a fresh DB comes up fully populated.
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Optional

from .regulatory_data import SEED_DOCUMENTS, SEED_RULES
from .retrieval import chunk_text
from .rules_engine import Rule

# Module-local data dir, matching the tampering module's convention
# (api/app/tampering/data/tampering.db -> api/app/compliance/data/compliance.db).
_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(_DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(_DATA_DIR, "compliance.db")


def get_db(path: Optional[str] = None) -> sqlite3.Connection:
    # Resolve DB_PATH at call time (not as a default-arg binding) so tests can
    # monkeypatch dbm.DB_PATH to point at a throwaway database.
    #
    # check_same_thread=False: FastAPI resolves the `get_conn` generator
    # dependency and runs the endpoint body on separate threadpool threads, so
    # a connection created during dependency setup gets used from a different
    # thread — which SQLite rejects by default ("created in thread X, used in
    # thread Y"). Each request still gets its OWN fresh connection (get_db is
    # called per request) and is never used concurrently, so relaxing the
    # same-thread check is safe; Python's sqlite3 is serialized at the C level.
    conn = sqlite3.connect(path if path is not None else DB_PATH,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            file_hash TEXT UNIQUE,
            category TEXT,
            source_type TEXT,
            effective_date TEXT,
            status TEXT DEFAULT 'active',
            version TEXT DEFAULT '1.0',
            page_count INTEGER DEFAULT 0,
            chunk_count INTEGER DEFAULT 0,
            ingestion_status TEXT DEFAULT 'pending'
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            chunk_index INTEGER,
            section_title TEXT,
            chunk_text TEXT NOT NULL,
            FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id TEXT UNIQUE NOT NULL,
            rule_name TEXT NOT NULL,
            category TEXT,
            rule_type TEXT NOT NULL,
            regulation TEXT,
            regulation_ref TEXT,
            remediation TEXT,
            required_fields TEXT,
            condition_json TEXT NOT NULL,
            applies_when_json TEXT,
            source_document_id INTEGER,
            source_chunk_id INTEGER,
            origin TEXT DEFAULT 'seed',
            is_active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS rule_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            obligation_text TEXT,
            proposed_rule_name TEXT,
            proposed_rule_type TEXT,
            proposed_category TEXT,
            proposed_condition_json TEXT NOT NULL,
            proposed_regulation TEXT,
            proposed_regulation_ref TEXT,
            proposed_remediation TEXT,
            proposed_required_fields TEXT,
            extraction_method TEXT,
            extraction_confidence REAL,
            source_document_id INTEGER,
            source_chunk_id INTEGER,
            needs_field_mapping INTEGER DEFAULT 1,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')),
            reviewed_by TEXT,
            reviewed_at TEXT,
            review_note TEXT,
            approved_rule_id TEXT
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (datetime('now')),
            actor TEXT,
            action TEXT,
            entity_type TEXT,
            entity_id TEXT,
            detail_json TEXT
        );
        """
    )
    conn.commit()


def _hash_title(title: str) -> str:
    import hashlib

    return hashlib.sha256(title.encode()).hexdigest()[:16]


def insert_document(
    conn: sqlite3.Connection,
    title: str,
    category: str,
    source_type: str,
    sections: list[dict],
    effective_date: Optional[str] = None,
) -> int:
    """Insert a document and its chunked sections. Returns the document id.
    Raises sqlite3.IntegrityError on duplicate title."""
    file_hash = _hash_title(title)
    cur = conn.execute(
        """INSERT INTO documents (title, file_hash, category, source_type,
               effective_date, ingestion_status)
           VALUES (?,?,?,?,?, 'completed')""",
        (title, file_hash, category, source_type, effective_date),
    )
    doc_id = cur.lastrowid

    chunk_index = 0
    for section in sections:
        section_title = section.get("section_title", "")
        for piece in chunk_text(section["text"]):
            conn.execute(
                """INSERT INTO chunks (document_id, chunk_index, section_title, chunk_text)
                   VALUES (?,?,?,?)""",
                (doc_id, chunk_index, section_title, piece),
            )
            chunk_index += 1

    conn.execute(
        "UPDATE documents SET chunk_count=?, page_count=? WHERE id=?",
        (chunk_index, len(sections), doc_id),
    )
    conn.commit()
    return doc_id


def insert_rule(conn: sqlite3.Connection, rule: dict, origin: str = "seed") -> None:
    conn.execute(
        """INSERT INTO rules (rule_id, rule_name, category, rule_type,
               regulation, regulation_ref, remediation, required_fields,
               condition_json, applies_when_json,
               source_document_id, source_chunk_id, origin)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            rule["rule_id"],
            rule["rule_name"],
            rule.get("category", "general"),
            rule["rule_type"],
            rule.get("regulation", ""),
            rule.get("regulation_ref", ""),
            rule.get("remediation", ""),
            json.dumps(rule.get("required_fields", [])),
            json.dumps(rule["condition"]),
            json.dumps(rule["applies_when"]) if rule.get("applies_when") else None,
            rule.get("source_document_id"),
            rule.get("source_chunk_id"),
            origin,
        ),
    )
    conn.commit()


def audit(conn: sqlite3.Connection, actor: str, action: str,
          entity_type: str, entity_id: str, detail: Optional[dict] = None) -> None:
    conn.execute(
        "INSERT INTO audit_log (actor, action, entity_type, entity_id, detail_json) VALUES (?,?,?,?,?)",
        (actor, action, entity_type, str(entity_id), json.dumps(detail or {})),
    )
    conn.commit()


def insert_candidates(conn: sqlite3.Connection, candidates: list[dict]) -> list[int]:
    """Insert extracted draft candidates (status='pending'). Returns new ids."""
    ids = []
    for c in candidates:
        cur = conn.execute(
            """INSERT INTO rule_candidates (
                   obligation_text, proposed_rule_name, proposed_rule_type,
                   proposed_category, proposed_condition_json, proposed_regulation,
                   proposed_regulation_ref, proposed_remediation,
                   proposed_required_fields, extraction_method, extraction_confidence,
                   source_document_id, source_chunk_id, needs_field_mapping)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                c.get("obligation_text", ""),
                c.get("proposed_rule_name", ""),
                c.get("proposed_rule_type", "advisory"),
                c.get("proposed_category", "General"),
                json.dumps(c["proposed_condition"]),
                c.get("proposed_regulation", ""),
                c.get("proposed_regulation_ref", ""),
                c.get("proposed_remediation", ""),
                json.dumps(c.get("proposed_required_fields", [])),
                c.get("extraction_method", "heuristic"),
                c.get("extraction_confidence", 0.0),
                c.get("source_document_id"),
                c.get("source_chunk_id"),
                1 if c.get("needs_field_mapping", True) else 0,
            ),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


def list_candidates(conn: sqlite3.Connection, status: str = "pending") -> list[dict]:
    if status == "all":
        rows = conn.execute("SELECT * FROM rule_candidates ORDER BY id DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM rule_candidates WHERE status=? ORDER BY id DESC", (status,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["proposed_condition"] = json.loads(d.pop("proposed_condition_json"))
        d["proposed_required_fields"] = json.loads(d["proposed_required_fields"]) if d.get("proposed_required_fields") else []
        d["needs_field_mapping"] = bool(d["needs_field_mapping"])
        out.append(d)
    return out


def get_candidate(conn: sqlite3.Connection, candidate_id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM rule_candidates WHERE id=?", (candidate_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["proposed_condition"] = json.loads(d.pop("proposed_condition_json"))
    d["proposed_required_fields"] = json.loads(d["proposed_required_fields"]) if d.get("proposed_required_fields") else []
    return d


def _condition_has_placeholder(node: dict) -> bool:
    """True if any leaf still references the extractor's placeholder field —
    i.e. the reviewer has not yet mapped it to a real application field."""
    from .extraction import _PLACEHOLDER_FIELD

    if not isinstance(node, dict):
        return False
    for group in ("all", "any"):
        if group in node:
            return any(_condition_has_placeholder(c) for c in node[group])
    if "not" in node:
        return _condition_has_placeholder(node["not"])
    return node.get("field") == _PLACEHOLDER_FIELD


def approve_candidate(
    conn: sqlite3.Connection,
    candidate_id: int,
    overrides: dict,
    reviewer: str,
) -> str:
    """Approve a draft candidate into a live rule (the 'checker' step).

    overrides may supply reviewer-edited fields: rule_id, rule_name, rule_type,
    category, condition, regulation, regulation_ref, remediation, required_fields.
    Raises ValueError if the candidate is missing/not pending, if the condition
    still has an unmapped placeholder field, or if the rule_id already exists.
    Returns the created rule_id.
    """
    cand = get_candidate(conn, candidate_id)
    if cand is None:
        raise ValueError("Candidate not found.")
    if cand["status"] != "pending":
        raise ValueError(f"Candidate already {cand['status']}.")

    condition = overrides.get("condition", cand["proposed_condition"])
    if _condition_has_placeholder(condition):
        raise ValueError(
            "Condition still references the placeholder field; map it to a real "
            "application field before approving."
        )

    rule_id = overrides.get("rule_id") or f"EXT_{candidate_id:04d}"
    exists = conn.execute("SELECT 1 FROM rules WHERE rule_id=?", (rule_id,)).fetchone()
    if exists:
        raise ValueError(f"Rule id '{rule_id}' already exists; choose another.")

    rule = {
        "rule_id": rule_id,
        "rule_name": overrides.get("rule_name", cand["proposed_rule_name"]),
        "category": overrides.get("category", cand.get("proposed_category") or "General"),
        "rule_type": overrides.get("rule_type", cand["proposed_rule_type"]),
        "regulation": overrides.get("regulation", cand.get("proposed_regulation") or ""),
        "regulation_ref": overrides.get("regulation_ref", cand.get("proposed_regulation_ref") or ""),
        "remediation": overrides.get("remediation", cand.get("proposed_remediation") or ""),
        "required_fields": overrides.get("required_fields", cand.get("proposed_required_fields") or []),
        "condition": condition,
        "applies_when": overrides.get("applies_when"),
        "source_document_id": cand.get("source_document_id"),
        "source_chunk_id": cand.get("source_chunk_id"),
    }
    from .rule_validation import rule_problems
    problems = rule_problems(rule)
    if problems:
        raise ValueError("This rule cannot go live: " + "; ".join(problems) + ".")
    insert_rule(conn, rule, origin="extracted")

    conn.execute(
        """UPDATE rule_candidates
           SET status='approved', reviewed_by=?, reviewed_at=datetime('now'),
               approved_rule_id=?
           WHERE id=?""",
        (reviewer, rule_id, candidate_id),
    )
    conn.commit()
    audit(conn, reviewer, "candidate_approved", "rule_candidate", candidate_id,
          {"rule_id": rule_id})
    audit(conn, reviewer, "rule_created", "rule", rule_id,
          {"origin": "extracted", "from_candidate": candidate_id})
    return rule_id


def reject_candidate(conn: sqlite3.Connection, candidate_id: int,
                     reviewer: str, note: str = "") -> None:
    cand = get_candidate(conn, candidate_id)
    if cand is None:
        raise ValueError("Candidate not found.")
    if cand["status"] != "pending":
        raise ValueError(f"Candidate already {cand['status']}.")
    conn.execute(
        """UPDATE rule_candidates
           SET status='rejected', reviewed_by=?, reviewed_at=datetime('now'), review_note=?
           WHERE id=?""",
        (reviewer, note, candidate_id),
    )
    conn.commit()
    audit(conn, reviewer, "candidate_rejected", "rule_candidate", candidate_id, {"note": note})


def list_audit(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = json.loads(d.pop("detail_json")) if d.get("detail_json") else {}
        out.append(d)
    return out


def seed_if_empty(conn: sqlite3.Connection) -> None:
    """Populate documents+chunks and rules from regulatory_data if empty."""
    doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    if doc_count == 0:
        for doc in SEED_DOCUMENTS:
            insert_document(
                conn,
                title=doc["title"],
                category=doc["category"],
                source_type=doc["source_type"],
                sections=doc["sections"],
                effective_date=doc.get("effective_date"),
            )

    rule_count = conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
    if rule_count == 0:
        for rule in SEED_RULES:
            insert_rule(conn, rule)


def load_rules(conn: sqlite3.Connection, active_only: bool = True) -> list[Rule]:
    q = "SELECT * FROM rules"
    if active_only:
        q += " WHERE is_active=1"
    rows = conn.execute(q).fetchall()
    rules = []
    for r in rows:
        rules.append(
            Rule(
                rule_id=r["rule_id"],
                rule_name=r["rule_name"],
                category=r["category"],
                rule_type=r["rule_type"],
                condition=json.loads(r["condition_json"]),
                regulation=r["regulation"] or "",
                regulation_ref=r["regulation_ref"] or "",
                remediation=r["remediation"] or "",
                required_fields=json.loads(r["required_fields"]) if r["required_fields"] else [],
                applies_when=json.loads(r["applies_when_json"]) if r["applies_when_json"] else None,
                is_active=bool(r["is_active"]),
            )
        )
    return rules


def load_chunks_for_index(conn: sqlite3.Connection) -> list[dict]:
    """Return active-document chunks joined with document metadata, shaped for
    BM25Index.build()."""
    rows = conn.execute(
        """SELECT c.id AS chunk_id, c.document_id, c.section_title, c.chunk_text,
                  d.title AS document_title, d.category
           FROM chunks c
           JOIN documents d ON d.id = c.document_id
           WHERE d.status = 'active'"""
    ).fetchall()
    return [
        {
            "chunk_id": r["chunk_id"],
            "document_id": r["document_id"],
            "document_title": r["document_title"],
            "category": r["category"],
            "section_title": r["section_title"],
            "text": r["chunk_text"],
        }
        for r in rows
    ]
