"""Persistence + feedback capture — the part the reference repo didn't have.

Every analysis is recorded in SQLite along with an archived copy of the
document (content-addressed by SHA-256, so re-analyzing the same file never
duplicates storage). Reviewers then attach verdicts (genuine/tampered) to
analyses. The labeled training set for retraining is EXACTLY the set of
archived files that have verdicts — production data labels itself over time.

Tables:
  analyses:  one row per pipeline run (scores, tier, doc_type, file hash)
  verdicts:  one row per human verdict, FK to analyses
"""
import hashlib
import json
import os
import sqlite3
from datetime import datetime

from app.tampering.config import CFG

_SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    file_name TEXT,
    archive_path TEXT,
    doc_type TEXT,
    source_format TEXT,
    fast_tracked INTEGER,
    fused_score REAL,
    risk_tier TEXT,
    signals_json TEXT
);
CREATE TABLE IF NOT EXISTS verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL REFERENCES analyses(id),
    verdict TEXT NOT NULL CHECK (verdict IN ('genuine', 'tampered')),
    reviewer TEXT,
    notes TEXT,
    created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analyses_hash ON analyses(file_hash);
"""


class FeedbackStore:
    def __init__(self, db_path: str | None = None, archive_dir: str | None = None):
        self.db_path = db_path or CFG.storage.db_path
        self.archive_dir = archive_dir or CFG.storage.archive_dir
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        os.makedirs(self.archive_dir, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---------------------------------------------------------- write
    def record_analysis(self, file_bytes: bytes, file_name: str, doc_type: str,
                        source_format: str, report: dict) -> int:
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        ext = os.path.splitext(file_name)[1].lower() or ".bin"
        archive_path = os.path.join(self.archive_dir, file_hash + ext)
        if not os.path.isfile(archive_path):
            with open(archive_path, "wb") as f:
                f.write(file_bytes)

        cur = self._conn.execute(
            "INSERT INTO analyses (created, file_hash, file_name, archive_path, doc_type,"
            " source_format, fast_tracked, fused_score, risk_tier, signals_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), file_hash, file_name,
             archive_path, doc_type, source_format,
             int(report.get("fast_tracked", False)),
             report.get("fused_score"), report.get("risk_tier"),
             json.dumps(report.get("signals", []))))
        self._conn.commit()
        return int(cur.lastrowid)

    def record_verdict(self, analysis_id: int, verdict: str,
                       reviewer: str = "", notes: str = "") -> None:
        if verdict not in ("genuine", "tampered"):
            raise ValueError("verdict must be 'genuine' or 'tampered'")
        self._conn.execute(
            "INSERT INTO verdicts (analysis_id, verdict, reviewer, notes, created)"
            " VALUES (?,?,?,?,?)",
            (analysis_id, verdict, reviewer, notes,
             datetime.now().isoformat(timespec="seconds")))
        self._conn.commit()

    # ---------------------------------------------------------- read
    def get_analysis(self, analysis_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM analyses WHERE id=?", (analysis_id,)).fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self._conn.execute("SELECT * FROM analyses LIMIT 0").description]
        return dict(zip(cols, row))

    def labeled_examples(self) -> list[dict]:
        """Archived files with verdicts — the retraining dataset. If one file
        has multiple verdicts, the most recent wins. group_id is the file hash
        so group-aware splitting keeps re-uploads of the same doc together."""
        rows = self._conn.execute(
            """SELECT a.file_hash, a.archive_path, v.verdict
               FROM verdicts v JOIN analyses a ON a.id = v.analysis_id
               ORDER BY v.created ASC""").fetchall()
        latest = {}
        for file_hash, path, verdict in rows:
            latest[file_hash] = {"group_id": file_hash, "path": path,
                                 "label": 1 if verdict == "tampered" else 0}
        return [ex for ex in latest.values() if os.path.isfile(ex["path"])]

    def stats(self) -> dict:
        n_analyses = self._conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
        n_verdicts = self._conn.execute("SELECT COUNT(*) FROM verdicts").fetchone()[0]
        by_tier = dict(self._conn.execute(
            "SELECT risk_tier, COUNT(*) FROM analyses GROUP BY risk_tier").fetchall())
        by_verdict = dict(self._conn.execute(
            "SELECT verdict, COUNT(*) FROM verdicts GROUP BY verdict").fetchall())
        return {"analyses": n_analyses, "verdicts": n_verdicts,
                "by_tier": by_tier, "by_verdict": by_verdict}

    def close(self):
        self._conn.close()
