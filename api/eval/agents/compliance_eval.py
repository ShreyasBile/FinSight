"""Compliance crew evaluation: KB Ingestion (16), Rule Authoring (17),
Regulatory Q&A (18). Every run uses a freshly seeded throwaway compliance
database (C.fresh_compliance_db), never the live knowledge base.

Gold sets were written by hand from the seeded regulatory text
(app/compliance/regulatory_data.py) BEFORE the agents were run:
  * QA_GOLD — 40 officer questions, each with the one section that answers it
    (paraphrased, not copied, so BM25 cannot win on exact wording);
  * RULE_GOLD — the 9 seeded rules that come from a seeded regulation (the
    other 5 are internal policy with no source text), each with its source
    section(s) and the key terms a correct draft must mention;
  * KB_NEW — 6 regulatory passages that are NOT in the seed corpus, ingested
    through the agent, with 12 questions that only they answer.
"""
from __future__ import annotations

from eval import common as C

QA_GOLD = [
    ("Can a lending service provider's pool account be used to route the disbursal?", "Disbursement and repayment flow"),
    ("Where must loan repayments be credited under the digital lending rules?", "Disbursement and repayment flow"),
    ("Who pays the LSP its fees for sourcing a loan?", "Fees payable to Lending Service Providers"),
    ("Can the LSP charge the borrower directly for its services?", "Fees payable to Lending Service Providers"),
    ("For how long can a borrower exit a digital loan without penalty?", "Cooling-off / look-up period"),
    ("What does the borrower pay back to leave the loan during the look-up period?", "Cooling-off / look-up period"),
    ("Is the processing fee refundable if the borrower exits early?", "Cooling-off / look-up period"),
    ("May a lending app read the borrower's contact list?", "Borrower consent and data minimisation"),
    ("Can a borrower withdraw consent given to a digital lending app?", "Borrower consent and data minimisation"),
    ("Can biometric data be stored by the lending app?", "Borrower consent and data minimisation"),
    ("What is the maximum default loss guarantee cover on a portfolio?", "Default Loss Guarantee (DLG)"),
    ("Which entities can offer a DLG arrangement to a lender?", "Default Loss Guarantee (DLG)"),
    ("What must an app that works with several lenders tell the borrower?", "Multi-lender platform disclosure"),
    ("Can a multi-lender app steer borrowers towards one lender's offer?", "Multi-lender platform disclosure"),
    ("When must the key facts statement be given to the borrower?", "KFS before contract execution"),
    ("Which loans does the KFS requirement apply to and from when?", "KFS before contract execution"),
    ("In what language should the KFS be provided?", "KFS before contract execution"),
    ("What must be included along with the APR in the key facts statement?", "Annual Percentage Rate (APR) disclosure"),
    ("Are insurance charges part of the annual percentage rate?", "Annual Percentage Rate (APR) disclosure"),
    ("Can a lender levy a fee that is not mentioned in the KFS?", "No charges outside the KFS"),
    ("Does the KFS need a proposal number and validity period?", "No charges outside the KFS"),
    ("Can penalties for breaching loan terms be added to the interest rate?", "Penal charges, not penal interest"),
    ("Is interest charged on penal charges?", "Penal charges, not penal interest"),
    ("Can penal charges be used to increase the lender's revenue?", "Reasonableness and disclosure"),
    ("Where must the amount of penal charges be disclosed?", "Reasonableness and disclosure"),
    ("What is the limit on a household's repayments for microfinance loans?", "Repayment capacity"),
    ("Does the household repayment cap include the new loan being applied for?", "Repayment capacity"),
    ("Can a microfinance loan require collateral?", "Collateral-free and no prepayment penalty"),
    ("Is a prepayment penalty allowed on microfinance loans?", "Collateral-free and no prepayment penalty"),
    ("Which documents count as officially valid documents for KYC?", "Customer Due Diligence and OVDs"),
    ("Is PAN mandatory during customer due diligence?", "Customer Due Diligence and OVDs"),
    ("Is a NREGA job card acceptable as an identity document?", "Customer Due Diligence and OVDs"),
    ("What checks apply to a video KYC session?", "Video-based Customer Identification (V-CIP)"),
    ("Who conducts the video customer identification process?", "Video-based Customer Identification (V-CIP)"),
    ("How often must KYC be refreshed for high-risk customers?", "Periodic updation and record keeping"),
    ("How long are KYC records kept after the account is closed?", "Periodic updation and record keeping"),
    ("How frequently must lenders report to credit bureaus now?", "Fortnightly credit information reporting"),
    ("Within how many days must credit information be submitted after the fortnight?", "Fortnightly credit information reporting"),
    ("Does the lender need permission before pulling a borrower's credit report?", "Borrower consent for credit bureau pull"),
    ("Must the borrower be told when negative information is reported to a bureau?", "Borrower consent for credit bureau pull"),
]

RULE_GOLD = {  # rule_id: (source section titles, key terms — any one must appear in the draft)
    "KYC_001": (["Customer Due Diligence and OVDs"], ["officially valid", "ovd", "due diligence"]),
    "KYC_002": (["Customer Due Diligence and OVDs"], ["pan", "form 60"]),
    "DL_KFS_001": (["KFS before contract execution", "Annual Percentage Rate (APR) disclosure"],
                   ["key facts", "kfs", "apr", "annual percentage"]),
    "DL_KFS_002": (["No charges outside the KFS"], ["fee", "charge"]),
    "DL_COOL_001": (["Cooling-off / look-up period"], ["cooling", "look-up", "one day"]),
    "DL_DISB_001": (["Disbursement and repayment flow"], ["disburs", "bank account", "pass-through", "pool account"]),
    "DL_CONSENT_001": (["Borrower consent and data minimisation", "Borrower consent for credit bureau pull"],
                       ["consent"]),
    "PENAL_001": (["Penal charges, not penal interest"], ["penal"]),
    "MFI_FOIR_001": (["Repayment capacity"], ["50", "household income", "repayment obligation"]),
}

KB_NEW = [
    ("RBI Circular – Floating Rate Reset of EMI Based Personal Loans, 2023",
     "At the time of interest rate reset, Regulated Entities shall give borrowers the option to switch to a "
     "fixed rate. Borrowers must also be offered the choice of enhancing the EMI, elongating the tenor, or a "
     "combination of both, and the option to prepay the loan in part or full at any point during the tenor. "
     "All applicable charges for switching must be disclosed in the sanction letter.",
     [("Can a borrower switch from floating to fixed rate when the rate is reset?", 0),
      ("What options must be offered to a floating-rate borrower at reset besides a fixed rate?", 0)]),
    ("RBI Directions – Release of Movable / Immovable Property Documents, 2023",
     "Regulated Entities shall release all original movable and immovable property documents and remove "
     "charges registered with any registry within 30 days after full repayment or settlement of the loan "
     "account. In case of delay attributable to the RE, the borrower shall be compensated at the rate of "
     "Rs 5,000 for each day of delay.",
     [("How soon must property papers be returned after the loan is repaid?", 1),
      ("What compensation is due if the lender delays returning the title deeds?", 1)]),
    ("RBI Master Circular – Wilful Defaulters and Large Defaulters, 2024",
     "A borrower shall be classified as a wilful defaulter only after the lender's identification committee "
     "has examined the evidence and the borrower has been given 15 days to make a written representation. "
     "A review committee headed by the chief executive must confirm the classification before the name is "
     "reported to credit information companies.",
     [("How many days does a borrower get to respond before being named a wilful defaulter?", 2),
      ("Who confirms a wilful defaulter classification before it is reported?", 2)]),
    ("RBI Framework – Compromise Settlements and Technical Write-offs, 2023",
     "Regulated Entities may undertake compromise settlements or technical write-offs only under a board-approved "
     "policy. Borrowers who enter a compromise settlement are subject to a cooling period of at least twelve "
     "months before new credit exposure can be taken on them, and fraud or wilful default accounts need board "
     "approval for any settlement.",
     [("How long must a lender wait before lending again to someone who settled a loan?", 3),
      ("Do settlements of fraud accounts need board approval?", 3)]),
    ("RBI Directions – Gold Loan Loan-to-Value Norms",
     "The loan-to-value ratio for loans against pledge of gold ornaments and jewellery shall not exceed 75 "
     "percent of the value of the gold. The value is computed on the average closing price of 22 carat gold "
     "for the preceding 30 days, and the lender must issue a certificate of the assessed purity and weight.",
     [("What is the maximum loan-to-value for a gold loan?", 4),
      ("How is the value of pledged gold jewellery computed?", 4)]),
    ("RBI Guidelines – Grievance Redressal and Internal Ombudsman",
     "Every Regulated Entity shall acknowledge a customer complaint and resolve it within 30 days. A complaint "
     "that is wholly or partly rejected by the entity must be escalated to its Internal Ombudsman before the "
     "rejection is conveyed, and the customer must be told that they may approach the RBI Ombudsman.",
     [("Within how many days must a lender resolve a customer complaint?", 5),
      ("What must happen before a lender rejects a customer complaint?", 5)]),
]


def _setup():
    C.fresh_compliance_db()


def eval_kb() -> dict:
    from app.agents.registry import get_agent
    from app.compliance.router import INDEX
    _setup()
    agent = get_agent("kb_ingestion")
    titles, lat = [], []
    for title, text, _qs in KB_NEW:
        with C.Timer() as t:
            agent.run({"title": title, "text": text, "category": "Regulatory", "source_type": "RBI"})
        lat.append(t.ms)
        titles.append(title)
    hits = total = 0
    fails = []
    for title, _text, qs in KB_NEW:
        for q, idx in qs:
            total += 1
            res = INDEX.search(q, top_k=5)
            ok = any(h["document_title"] == titles[idx] for h in res)
            hits += ok
            if not ok:
                fails.append({"q": q, "top": [h["document_title"][:50] for h in res[:3]]})
    # the new documents must not push the seeded corpus out
    seed_ok = sum(1 for q, sec in QA_GOLD if any(sec.lower() in (h["section_title"] or "").lower()
                                                   for h in INDEX.search(q, top_k=5)))
    metrics = {"recall_at_5": round(hits / total, 4), "questions": total, "documents_ingested": len(KB_NEW),
               "seed_recall_at_5_after_ingest": round(seed_ok / len(QA_GOLD), 4),
               "ingest_p50_ms": C.latency(lat)["p50_s"] * 1000}
    return C.save("kb_ingestion", metrics, failures=fails)


def eval_regulatory_qa(synth_n: int = 12) -> dict:
    from app.agents.registry import get_agent
    _setup()
    agent = get_agent("regulatory_qa")
    hit1 = hit5 = 0
    fails, lat_r = [], []
    for q, sec in QA_GOLD:
        with C.Timer() as t:
            res = agent.execute({"query": q, "top_k": 5}).payload
        lat_r.append(t.ms)
        secs = [(c["section_title"] or "") for c in res["citations"]]
        r = next((i for i, s in enumerate(secs) if sec.lower() in s.lower()), None)
        hit5 += r is not None
        hit1 += r == 0
        if r is None:
            fails.append({"q": q, "gold": sec, "top": secs[:3]})
    # grounded synthesis on a fixed, evenly spread subset
    step = max(1, len(QA_GOLD) // synth_n)
    subset = QA_GOLD[::step][:synth_n]
    grounded = synthesized = insufficient = cites_gold = attempted = 0
    lat_s, samples = [], []
    for q, sec in subset:
        with C.Timer() as t:
            r = agent.run({"query": q, "top_k": 5, "synthesize": True})
        lat_s.append(t.ms)
        attempted += 1
        p = r.payload
        if p.get("synthesized"):
            synthesized += 1
            grounded += bool(p.get("grounded"))
            if "insufficient" in (p.get("answer") or "").lower() or "do not answer" in (p.get("answer") or "").lower():
                insufficient += 1
            cited = p.get("cited") or []
            cites_gold += any(sec.lower() in (p["citations"][c - 1]["section_title"] or "").lower()
                              for c in cited if 0 < c <= len(p["citations"]))
        samples.append({"q": q, "synthesized": p.get("synthesized"), "grounded": p.get("grounded"),
                        "answer": (p.get("answer") or "")[:260], "warnings": r.warnings[:2]})
    metrics = {"hit_at_5": round(hit5 / len(QA_GOLD), 4), "hit_at_1": round(hit1 / len(QA_GOLD), 4),
               "questions": len(QA_GOLD),
               "grounded_answer_rate": round(grounded / attempted, 4) if attempted else None,
               "synthesis_kept_rate": round(synthesized / attempted, 4) if attempted else None,
               "citation_accuracy": round(cites_gold / synthesized, 4) if synthesized else None,
               "insufficient_answers": insufficient, "synthesis_attempts": attempted,
               "retrieval_p50_ms": round(C.latency(lat_r)["p50_s"] * 1000, 1),
               "synthesis_p50_s": C.latency(lat_s)["p50_s"], "synthesis_p95_s": C.latency(lat_s)["p95_s"]}
    return C.save("regulatory_qa", metrics, failures=fails, samples=samples)


_OPS = {"eq", "ne", "gte", "lte", "gt", "lt"}


def _well_formed(cond) -> bool:
    if not isinstance(cond, dict):
        return False
    if "all" in cond or "any" in cond:
        parts = cond.get("all") or cond.get("any") or []
        return bool(parts) and all(_well_formed(p) for p in parts)
    return isinstance(cond.get("field"), str) and cond.get("op") in _OPS and "value" in cond


def eval_rule_authoring() -> dict:
    from app.agents.registry import get_agent
    from app.compliance import db as dbm
    _setup()
    agent = get_agent("rule_authoring")
    conn = dbm.get_db()
    docs = conn.execute("SELECT id, title, category FROM documents WHERE status='active'").fetchall()
    cands, lat, methods = [], [], {}
    for d in docs:
        chunks = [dict(r) for r in conn.execute(
            "SELECT id AS chunk_id, document_id, section_title, chunk_text AS text FROM chunks "
            "WHERE document_id=? ORDER BY chunk_index", (d["id"],)).fetchall()]
        with C.Timer() as t:
            out = agent.run({"chunks": chunks, "document_title": d["title"],
                             "document_category": d["category"]}).payload
        lat.append(t.ms)
        methods[out["method"]] = methods.get(out["method"], 0) + 1
        sec_of = {c["chunk_id"]: c["section_title"] for c in chunks}
        for c in out["candidates"]:
            c["_section"] = sec_of.get(c.get("source_chunk_id")) or c.get("proposed_regulation_ref") or ""
            cands.append(c)
    recovered, missing = [], []
    for rid, (sections, terms) in RULE_GOLD.items():
        pool = [c for c in cands if any(s.lower() in c["_section"].lower() for s in sections)]
        text = lambda c: " ".join(str(c.get(k) or "") for k in ("proposed_rule_name", "proposed_regulation_ref",  # noqa: E731
                                                              "obligation_text", "proposed_remediation")).lower()
        (recovered if any(any(t in text(c) for t in terms) for c in pool) else missing).append(rid)
    wf = sum(_well_formed(c.get("proposed_condition")) for c in cands)
    mapped = sum(1 for c in cands if not c.get("needs_field_mapping"))
    metrics = {"obligation_recall": round(len(recovered) / len(RULE_GOLD), 4), "gold_rules": len(RULE_GOLD),
               "recovered": recovered, "missing": missing, "candidates": len(cands),
               "valid_condition_rate": round(wf / len(cands), 4) if cands else 0.0,
               "field_mapped_rate": round(mapped / len(cands), 4) if cands else 0.0,
               "methods": methods, "documents": len(docs),
               "p50_s_per_document": C.latency(lat)["p50_s"], "p95_s_per_document": C.latency(lat)["p95_s"]}
    return C.save("rule_authoring", metrics,
                  samples=[{k: c.get(k) for k in ("proposed_rule_name", "proposed_condition", "_section",
                                                  "needs_field_mapping")}
                           for c in cands[:10]])
