"""
retrieval.py
------------
A small, dependency-free lexical retrieval engine (Okapi BM25) plus a
paragraph-aware chunker.

The prototype's /api/rag/query was a stub that ignored the query and just
counted documents. This replaces it with genuine ranked retrieval over the
text of the ingested regulatory chunks, so a query like "cooling off period"
actually returns the passages that discuss it, with document + section
citations.

Why BM25 and not vector embeddings? This project is meant to run fully
offline and self-contained, with no external embedding API and no heavy ML
dependency (that keeps it isolated from the main FinSight app, as requested).
BM25 is the same ranking family used by Elasticsearch/Lucene and is strong
for keyword-heavy regulatory text where the vocabulary is precise ("KFS",
"APR", "penal charges", "cooling-off"). It is honestly a *lexical* matcher,
not a semantic one — documented as such rather than dressed up as embeddings.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

# A compact English stopword list — enough to stop common words from
# dominating BM25 scores without needing an NLP library.
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "have", "in", "is", "it", "its", "of", "on", "or", "that", "the", "to",
    "was", "were", "will", "with", "shall", "any", "all", "such", "may",
    "this", "these", "those", "which", "their", "they", "there", "than",
    "not", "no", "if", "but", "into", "under", "over", "per", "within",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, drop stopwords and 1-char tokens."""
    return [
        t for t in _TOKEN_RE.findall(text.lower())
        if t not in _STOPWORDS and len(t) > 1
    ]


def chunk_text(text: str, max_chars: int = 700) -> list[str]:
    """Split text into chunks on blank lines (paragraphs), packing
    consecutive paragraphs together until max_chars, so a chunk stays a
    coherent unit rather than an arbitrary character window."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        if not buf:
            buf = para
        elif len(buf) + len(para) + 2 <= max_chars:
            buf = f"{buf}\n\n{para}"
        else:
            chunks.append(buf)
            buf = para
    if buf:
        chunks.append(buf)
    # A single very long paragraph still becomes its own chunk; that's fine
    # for BM25 (length normalization handles it).
    return chunks or ([text.strip()] if text.strip() else [])


@dataclass
class IndexedChunk:
    chunk_id: int
    document_id: int
    document_title: str
    category: str
    section_title: str
    text: str
    tokens: list[str]


class BM25Index:
    """In-memory Okapi BM25 index rebuilt from the chunk table on demand."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.chunks: list[IndexedChunk] = []
        self.doc_freq: Counter[str] = Counter()
        self.avg_len: float = 0.0

    def build(self, records: Iterable[dict]) -> None:
        """records: dicts with keys chunk_id, document_id, document_title,
        category, section_title, text."""
        self.chunks = []
        self.doc_freq = Counter()
        total_len = 0
        for r in records:
            tokens = tokenize(r["text"])
            self.chunks.append(
                IndexedChunk(
                    chunk_id=r["chunk_id"],
                    document_id=r["document_id"],
                    document_title=r["document_title"],
                    category=r.get("category", ""),
                    section_title=r.get("section_title", "") or "",
                    text=r["text"],
                    tokens=tokens,
                )
            )
            total_len += len(tokens)
            for term in set(tokens):
                self.doc_freq[term] += 1
        n = len(self.chunks)
        self.avg_len = (total_len / n) if n else 0.0

    def _idf(self, term: str) -> float:
        n = len(self.chunks)
        df = self.doc_freq.get(term, 0)
        # BM25 idf with +1 floor so common terms never go negative.
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        if not self.chunks:
            return []
        q_terms = tokenize(query)
        if not q_terms:
            return []

        scored: list[tuple[float, IndexedChunk]] = []
        for chunk in self.chunks:
            tf = Counter(chunk.tokens)
            dl = len(chunk.tokens)
            score = 0.0
            for term in q_terms:
                if term not in tf:
                    continue
                freq = tf[term]
                idf = self._idf(term)
                denom = freq + self.k1 * (
                    1 - self.b + self.b * (dl / self.avg_len if self.avg_len else 0)
                )
                score += idf * (freq * (self.k1 + 1)) / denom
            if score > 0:
                scored.append((score, chunk))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, chunk in scored[:top_k]:
            results.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "document_id": chunk.document_id,
                    "document_title": chunk.document_title,
                    "category": chunk.category,
                    "section_title": chunk.section_title,
                    "text": chunk.text,
                    "score": round(score, 4),
                }
            )
        return results
