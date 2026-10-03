"""KB Ingestion, Regulatory Q&A and Rule Authoring on real regulations.

ObliQA (RegNLP, Apache-2.0): 40 real ADGM financial regulations split into numbered passages, and
questions whose gold answers are specific passages (selected with an NLI filter to carry
obligations). Official dev / test files = our dev / test splits.

Everything runs against a FRESH throwaway compliance database seeded like the app's, then the 40
ADGM documents are ingested through FinSight's own KB Ingestion agent — so retrieval is tested on
FinSight's chunking and index, not on ObliQA's passage boundaries. A gold passage counts as
retrieved when its full text (normalised) lies inside a retrieved chunk: passages are separated
by blank lines and the chunker never cuts inside a paragraph, so containment is exact.

Real RBI circulars (raw/rbi/*.pdf) are added as a second set when they exist (the user downloads
them: rbi.org.in blocks scripted downloads).
"""
from __future__ import annotations

import json
import re
import time

from eval import common as C
from eval.real import core as R

OBLIQA = R.RAW / "obliqa"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def corpus():
    names = {int(k): v for k, v in json.loads((OBLIQA / "document_map.json").read_text()).items()}
    docs = {}
    for f in sorted((OBLIQA / "corpus" / "StructuredRegulatoryDocuments").glob("*.json"), key=lambda p: int(p.stem)):
        rows = json.loads(f.read_text(encoding="utf-8"))
        did = int(f.stem)
        docs[did] = {"id": did, "title": f"ADGM {names.get(did, {}).get('short', did)} — {names.get(did, {}).get('file', '')}",
                     "passages": [(r["PassageID"], r["Passage"]) for r in rows if str(r.get("Passage") or "").strip()]}
    return docs


def questions(split: str):
    f = OBLIQA / "ObliQA" / f"ObliQA_{'dev' if split == 'dev' else 'test'}.json"
    return [{"id": q["QuestionID"], "q": q["Question"], "gold": [(p["DocumentID"], p["PassageID"], p["Passage"]) for p in q["Passages"]]}
            for q in json.loads(f.read_text(encoding="utf-8"))]


_KB_READY: dict = {}


def build_kb() -> dict:
    """Fresh seeded compliance DB + the 40 ADGM documents ingested through the KB Ingestion agent."""
    if _KB_READY:
        return _KB_READY
    from app.agents.registry import get_agent
    C.fresh_compliance_db()
    agent = get_agent("kb_ingestion")
    docs = corpus()
    lat, chunks = [], 0
    for d in docs.values():
        text = "\n\n".join(f"{pid} {p}" for pid, p in d["passages"])
        with C.Timer() as t:
            out = agent.run({"title": d["title"], "text": text, "category": "Regulatory", "source_type": "ADGM"}).payload
        lat.append(t.ms)
        chunks += out["chunks"]
    from app.compliance.router import INDEX
    _KB_READY.update(docs=docs, titles={d["title"]: did for did, d in docs.items()}, chunks=chunks,
                     ingest=R.latency(lat), index_chunks=len(INDEX.chunks))
    return _KB_READY


def _hit(gold, cite, titles) -> bool:
    did, _pid, text = gold
    return titles.get(cite["document_title"]) == did and _norm(text) in _norm(cite["text"])


def _retrieve(split: str, n: int, k: int = 10):
    from app.agents.registry import get_agent
    kb = build_kb()
    agent = get_agent("regulatory_qa")
    qs = R.sample(questions(split), n)
    rows = []
    t0 = time.time()
    for i, q in enumerate(qs, 1):
        with C.Timer() as t:
            res = agent.execute({"query": q["q"], "top_k": k}).payload
        cites = res["citations"]
        ranks = [next((r for r, c in enumerate(cites) if _hit(g, c, kb["titles"])), None) for g in q["gold"]]
        doc_ranks = [next((r for r, c in enumerate(cites) if kb["titles"].get(c["document_title"]) == g[0]), None)
                     for g in q["gold"]]
        rows.append({"ranks": ranks, "doc_ranks": doc_ranks, "ms": round(t.ms, 1)})
        if i % 200 == 0:
            print(f"  [ObliQA retrieval] {i}/{len(qs)} ({(time.time() - t0) / i:.2f} s/question)", flush=True)
    return qs, rows, kb


def _first(ranks):
    r = [x for x in ranks if x is not None]
    return min(r) if r else None


def eval_regulatory_qa(split: str = "test", n: int = 1000, n_synth: int = 30) -> dict:
    from app.agents.registry import get_agent
    qs, rows, kb = _retrieve(split, n)
    best = [_first(r["ranks"]) for r in rows]
    passages = [x for r in rows for x in r["ranks"]]
    mrr = lambda rs: round(sum(1 / (b + 1) for b in rs if b is not None) / len(rs), 4)  # noqa: E731
    metrics = {"obliqa_recall_at_10": R.wilson(sum(b is not None for b in best), len(best)),
               "obliqa_recall_at_5": R.wilson(sum(b is not None and b < 5 for b in best), len(best)),
               "obliqa_hit_at_1": R.wilson(sum(b == 0 for b in best), len(best)),
               "obliqa_mrr_at_10": R.bootstrap(lambda s: mrr(s), best),
               "obliqa_passage_recall_at_10": R.wilson(sum(x is not None for x in passages), len(passages)),
               "retrieval_latency": R.latency([r["ms"] for r in rows]),
               "index_chunks": kb["index_chunks"]}
    # grounded synthesis (local model) on a fixed subset
    agent = get_agent("regulatory_qa")
    cache = R.ItemCache("regulatory_qa", f"synth-v1-{split}")

    def synth(q):
        r = agent.run({"query": q["q"], "top_k": 5, "synthesize": True})
        p = r.payload
        cited = p.get("cited") or []
        gold_cited = any(_hit(g, p["citations"][c - 1], kb["titles"]) for g in q["gold"] for c in cited
                         if 0 < c <= len(p["citations"]))
        return {"synthesized": bool(p.get("synthesized")), "grounded": bool(p.get("grounded")), "cites_gold": gold_cited}
    sq = cache.run(R.sample(qs, n_synth, salt="synth"), synth, label="synthesis", every=10)
    done = [s for s in sq if s["synthesized"]]
    metrics.update({"grounded_answer_rate": R.wilson(sum(s["grounded"] for s in done), len(done)),
                    "synthesis_kept_rate": R.wilson(len(done), len(sq)),
                    "citation_accuracy": R.wilson(sum(s["cites_gold"] for s in done), len(done))})
    data = {"name": "ObliQA (40 ADGM regulations; official test file)", "split": split, "n_questions": len(rows),
            "n_synthesis": len(sq), "criterion": "a gold passage's full text lies inside a retrieved chunk of the same document"}
    return R.save("regulatory_qa", split, metrics, data)


def eval_kb(split: str = "test", n: int = 1000) -> dict:
    qs, rows, kb = _retrieve(split, n)
    # chunking fidelity: every gold passage survives ingestion whole, in one chunk
    from app.compliance import db as dbm
    conn = dbm.get_db()
    by_title = {}
    for r in conn.execute("SELECT d.title, c.chunk_text FROM chunks c JOIN documents d ON d.id = c.document_id"):
        by_title.setdefault(r["title"], []).append(_norm(r["chunk_text"]))
    inv = {did: t for t, did in kb["titles"].items()}
    gold = {(g[0], g[1]): g[2] for q in qs for g in q["gold"]}
    intact = sum(any(_norm(text) in ch for ch in by_title.get(inv.get(did), [])) for (did, _pid), text in gold.items())
    doc5 = [_first([d for d in r["doc_ranks"] if d is not None and d < 5]) for r in rows]
    sec5 = [_first([x for x in r["ranks"] if x is not None and x < 5]) for r in rows]
    metrics = {"recall_at_5": R.wilson(sum(d is not None for d in doc5), len(doc5)),
               "section_recall_at_5": R.wilson(sum(s is not None for s in sec5), len(sec5)),
               "passage_integrity": R.wilson(intact, len(gold)),
               "documents_ingested": len(kb["docs"]), "chunks": kb["chunks"], "ingest_latency": kb["ingest"],
               "retrieval_latency": R.latency([r["ms"] for r in rows])}
    data = {"name": "ObliQA corpus (40 ADGM regulations) ingested through the KB Ingestion agent", "split": split,
            "n_questions": len(rows), "gold_passages": len(gold),
            "recall_at_5": "the gold passage's DOCUMENT among the top-5 chunks",
            "section_recall_at_5": "the gold passage ITSELF inside a top-5 chunk"}
    return R.save("kb_ingestion", split, metrics, data)


def _well_formed(cond) -> bool:
    from eval.agents.compliance_eval import _well_formed as wf
    return wf(cond)


def eval_rule_authoring(split: str = "test", max_chunks: int = 160) -> dict:
    """Gold obligations = passages that ObliQA questions (train+dev+test) are built on, inside documents
    chosen by a fixed rule (hash order, test-split documents, until max_chunks chunks)."""
    from app.agents.registry import get_agent
    from app.compliance import db as dbm
    kb = build_kb()
    agent = get_agent("rule_authoring")
    conn = dbm.get_db()
    gold = {}
    for s in ("train", "dev", "test"):
        for q in json.loads((OBLIQA / "ObliQA" / f"ObliQA_{s}.json").read_text(encoding="utf-8")):
            for p in q["Passages"]:
                gold.setdefault(p["DocumentID"], {})[p["PassageID"]] = p["Passage"]
    candidates_docs = []
    for title, did in kb["titles"].items():
        n_chunks = conn.execute("SELECT chunk_count FROM documents WHERE title=?", (title,)).fetchone()[0]
        if did in gold and R.split_of(f"doc{did}") == split and 20 <= n_chunks <= 120:
            candidates_docs.append((R._u(f"doc{did}", "rules"), did, title, n_chunks))
    chosen, total = [], 0
    for _, did, title, nc in sorted(candidates_docs):
        if total + nc > max_chunks:
            continue
        chosen.append((did, title))
        total += nc
    cache = R.ItemCache("rule_authoring", f"obliqa-v1-{split}")
    recovered = gold_n = cands_n = cands_on_gold = valid = 0
    per_doc = []
    for did, title in chosen:
        d = conn.execute("SELECT id, category FROM documents WHERE title=?", (title,)).fetchone()
        chunks = [dict(r) for r in conn.execute(
            "SELECT id AS chunk_id, document_id, section_title, chunk_text AS text FROM chunks WHERE document_id=? "
            "ORDER BY chunk_index", (d["id"],)).fetchall()]
        out = cache.get(title) or cache.put(title, _author(agent, chunks, title, d["category"]))
        text_of = {c["chunk_id"]: _norm(c["text"]) for c in chunks}
        cand_chunks = [text_of.get(c.get("source_chunk_id"), "") for c in out["candidates"]]
        g = gold[did]
        rec = sum(any(_norm(t) in ch for ch in cand_chunks) for t in g.values())
        on_gold = sum(any(_norm(t) in ch for t in g.values()) for ch in cand_chunks)
        recovered += rec
        gold_n += len(g)
        cands_n += len(cand_chunks)
        cands_on_gold += on_gold
        valid += sum(_well_formed(c.get("proposed_condition")) for c in out["candidates"])
        per_doc.append({"document": title, "chunks": len(chunks), "gold_obligations": len(g), "recovered": rec,
                        "candidates": len(cand_chunks), "seconds": out["seconds"], "method": out["method"]})
    metrics = {"obligation_recall": R.wilson(recovered, gold_n), "precision": R.wilson(cands_on_gold, cands_n),
               "valid_condition_rate": R.wilson(valid, cands_n), "per_document": per_doc}
    data = {"name": "ObliQA obligation passages in ADGM documents (third-party labels)", "split": split,
            "documents": [t for _, t in chosen], "chunks": total,
            "gold": "passages any ObliQA question (train, dev or test) is built on",
            "recovered": "a candidate rule was drafted from a chunk containing the obligation passage"}
    return R.save("rule_authoring", split, metrics, data)


def _author(agent, chunks, title, category) -> dict:
    t = time.time()
    out = agent.run({"chunks": chunks, "document_title": title, "document_category": category}).payload
    return {"candidates": [{k: c.get(k) for k in ("source_chunk_id", "proposed_rule_name", "proposed_condition",
                                                  "needs_field_mapping")} for c in out["candidates"]],
            "method": out["method"], "seconds": round(time.time() - t, 1)}
