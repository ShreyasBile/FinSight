"""Per-document flows behind the existing endpoints.

The wizard deliberately calls each stage at its own UX moment (extract at
upload, integrity + face at Review & Submit, mask on hover), so each endpoint
is one traced run. IntakeFlow is a CrewAI Flow (four agents in sequence);
single-agent endpoints run their agent directly inside a traced run. Every
result is also written to the case file (X-Case-Id), so the case-level agents
can use it later without a re-upload.

Response shapes are exactly the pre-agent ones (tests/contract).
"""
from __future__ import annotations

from ...config import settings
from ...doc_types import get_doc_type
from .. import case_store
from ..crewai_compat import flow_classes
from ..registry import get_agent, is_enabled
from ..tracing.ids import document_case, normalize_case_id
from ..tracing.recorder import RECORDER
from ..tracing.redact import digest

_IntakeFlow = None


def _intake_flow_class():
    """Built lazily: importing crewai is slow, and the kill switch path never needs it."""
    global _IntakeFlow
    if _IntakeFlow is not None:
        return _IntakeFlow
    Flow, start, listen, router, and_, or_ = flow_classes()

    class IntakeFlow(Flow):
        """preprocessing → extraction → validation → document identity."""

        @start()
        def preprocess(self):
            s = self.state
            r = get_agent("preprocessing").run({"data": s["data"], "mime": s["mime"],
                                                "doc_type": s["doc_type"], "_doc_case": s["doc_case"]})
            self.results["preprocessing"] = r
            return r.payload

        @listen(preprocess)
        def extract(self, pre):
            s = self.state
            r = get_agent("extraction").run({"data": pre["data"], "mime": pre["mime"],
                                             "doc_type": s["doc_type"], "prompt": s["prompt"],
                                             "_doc_case": s["doc_case"]})
            self.results["extraction"] = r
            return r.payload

        @listen(preprocess)
        def read_page(self, pre):
            # the page's own words, for the identity gate — CPU OCR running in
            # parallel with the GPU-bound VLM call, so it adds no wall time
            # preprocessing already read the page while choosing its orientation
            # (orientation reader, upright angle); a second OCR pass cost 4-5 s
            # per card and outlasted the model call on small ID cards
            reused = pre.get("page_text") or ""
            if len(reused.strip()) >= 20:
                self.results["page_text"] = reused
            else:
                from ..tools.doc_keywords import page_text
                self.results["page_text"] = page_text(pre["data"], pre["mime"])
            if self.state["doc_type"] == "bank_statement":
                # transaction rows for the Fraud Patterns agent (the extraction
                # prompt returns only the statement summary). A born-digital PDF
                # takes ~10 ms; a PHOTO or a SCANNED PDF needs OCR passes that
                # outlast the model call, so run_extract reads those rows after
                # responding (a scan only gives its page count and printed totals here).
                self.results["pre_data"] = pre["data"]
                if self.state["data"][:5] == b"%PDF-":
                    from ..tools.transactions import at_upload
                    self.results["transactions"], self.results["rows_later"] = at_upload(self.state["data"])
            return True

        @listen(and_(extract, read_page))
        def validate(self):
            s = self.state
            parsed = self.results["extraction"].payload.get("parsed")
            if s["doc_type"] == "pan":
                # a ten-character code is where the model slips; the page's own OCR text checks it
                from ..tools.doc_keywords import page_text
                from ..tools.id_crossread import cross_read_pan
                done = cross_read_pan(parsed, self.results.get("page_text"),
                                      lambda: page_text(s["data"], s["mime"]))
                if done:
                    self.results["extraction"].payload["cross_read"] = done
            if s["doc_type"] == "bank_statement" and self.results.get("transactions"):
                # the model cannot add up a statement; totals come from the rows when they are complete
                from ..tools.transactions import apply_summary
                done = apply_summary(parsed, self.results["transactions"])
                if done:
                    self.results["extraction"].payload["totals"] = done
            self.results["validation"] = get_agent("validation").run(
                {"doc_type": s["doc_type"], "parsed": parsed, "_doc_case": s["doc_case"]})
            self.results["doc_identity"] = get_agent("doc_identity").run(
                {"doc_type": s["doc_type"], "parsed": parsed, "_doc_case": s["doc_case"],
                 "page_text": self.results.get("page_text")})
            return True

    _IntakeFlow = IntakeFlow
    return IntakeFlow


def _case(case_id_raw: str | None, data: bytes) -> str:
    return normalize_case_id(case_id_raw) or _synthetic(data)


def _synthetic(data: bytes) -> str:
    from ..tracing.ids import synthetic_case
    return synthetic_case(data)


def _actor(actor: dict | None) -> dict:
    return actor or {}


# ── /extract ────────────────────────────────────────────────────────────────
def run_extract(data: bytes, mime: str, doc_type: str, filename: str | None,
                case_id_raw: str | None = None, actor: dict | None = None) -> dict:
    """Returns {preprocess, extraction, validation, identity, run_id}."""
    cfg = get_doc_type(doc_type)
    case_id = _case(case_id_raw, data)
    sha = digest(data, 64)
    doc_case = document_case(case_id, doc_type, sha)

    if not settings.agents_enabled:  # kill switch: same tools, no flow, no tracing
        pre = get_agent("preprocessing").execute({"data": data, "mime": mime, "doc_type": doc_type}).payload
        ext = get_agent("extraction").execute({"data": pre["data"], "mime": pre["mime"],
                                               "doc_type": doc_type, "prompt": cfg["prompt"]}).payload
        from ..tools.doc_keywords import page_text
        reused = pre.get("page_text") or ""
        text = reused if len(reused.strip()) >= 20 else page_text(pre["data"], pre["mime"])
        if doc_type == "pan":                    # the same deterministic corrections as the flow
            from ..tools.id_crossread import cross_read_pan
            cross_read_pan(ext["parsed"], text, lambda: page_text(data, mime))
        if doc_type == "bank_statement" and data[:5] == b"%PDF-":
            from ..tools.transactions import apply_summary, at_upload
            apply_summary(ext["parsed"], at_upload(data)[0])
        val = get_agent("validation").execute({"doc_type": doc_type, "parsed": ext["parsed"]}).payload
        idn = get_agent("doc_identity").execute({"doc_type": doc_type, "parsed": ext["parsed"],
                                                 "page_text": text}).payload
        return {"preprocess": pre, "extraction": ext, "validation": val, "identity": idn, "run_id": None}

    with RECORDER.run("extract", case_id, actor=_actor(actor)) as run:
        flow = _intake_flow_class()()
        flow.results = {}
        flow.kickoff(inputs={"data": data, "mime": mime, "doc_type": doc_type,
                             "prompt": cfg["prompt"], "doc_case": doc_case})
        res = flow.results
        ext = res["extraction"].payload
        val = res["validation"].payload
        idn = res["doc_identity"].payload
        txn = res.get("transactions") or {}
        case_store.put_document(
            case_id, doc_type, sha256=sha, mime=mime, file_name=filename, doc_case=doc_case,
            parsed=ext.get("parsed"), provider=ext.get("provider"),
            validation={"confidence": val["confidence"], "needs_review": val["needs_review"],
                        "flags": {f["key"]: f["flag"] for f in val["fields"]}},
            identity_ok=idn["ok"],
            transactions=txn.get("transactions") or None, transactions_source=txn.get("source"),
            timings={"preprocess_ms": res["preprocessing"].payload.get("ms"), "extract_ms": ext.get("ms")})
        if doc_type == "bank_statement" and (data[:5] != b"%PDF-" or res.get("rows_later")):
            import threading
            threading.Thread(target=_parse_statement_later, args=(case_id, data, res.get("pre_data")),
                             name="statement-rows", daemon=True).start()
        return {"preprocess": res["preprocessing"].payload, "extraction": ext, "validation": val,
                "identity": idn, "run_id": run.run_id, "case_id": case_id}


def _parse_statement_later(case_id: str, data: bytes, pre_data: bytes | None) -> None:
    """Photo and scanned-PDF statements: read the transaction rows off the request
    path (two OCR passes on a photo measured 23 s median while other work shared the
    CPU; six scanned pages four minutes) and add them to the case file for the Fraud
    Patterns agent. Never raises."""
    try:
        from ..tools.transactions import parse_best
        txn = parse_best(data, pre_data)
        case_store.put_document(case_id, "bank_statement", transactions=txn.get("transactions") or None,
                                transactions_source=txn.get("source"))
        from ..tools.transactions import apply_summary

        def _fn(case):                           # same rule as the request path (transactions.apply_summary)
            apply_summary((case["documents"].get("bank_statement") or {}).get("parsed"), txn)
        case_store.update(case_id, _fn)
    except Exception:  # noqa: BLE001 - enrichment only
        pass


# ── /document/integrity ─────────────────────────────────────────────────────
def run_integrity(data: bytes, filename: str, doc_type: str, parsed: dict,
                  case_id_raw: str | None = None, actor: dict | None = None) -> dict:
    """Authenticity agent first (its verdict is injected into the cascade so the
    vision check runs exactly once), then Tampering Forensics."""
    case_id = _case(case_id_raw, data)
    sha = digest(data, 64)
    doc_case = document_case(case_id, doc_type, sha)
    base = {"data": data, "filename": filename, "doc_type": doc_type, "parsed": parsed, "_doc_case": doc_case}

    if not settings.agents_enabled:
        out = get_agent("tampering_forensics").execute(base).payload
        return {"tampering": out and out["tampering"], "run_id": None}

    with RECORDER.run("integrity", case_id, actor=_actor(actor)) as run:
        auth = get_agent("authenticity").run(base) if is_enabled("authenticity") else None
        inject = auth.payload if auth is not None else None
        r = get_agent("tampering_forensics").run({**base, "authenticity_result": inject})
        tampering = r.payload and r.payload["tampering"]
        if tampering:
            case_store.put_document(case_id, doc_type, sha256=sha, doc_case=doc_case, tampering={
                "risk_tier": tampering["risk_tier"], "risk_score": tampering["risk_score"],
                "source_format": tampering["source_format"], "fast_tracked": tampering["fast_tracked"],
                "signals": {s["name"]: s["score"] for s in tampering["signals"] if s["available"]},
                "explanations": tampering["explanations"][:12],
                "authenticity": (inject.score if inject is not None and inject.available else None)})
        return {"tampering": tampering, "run_id": run.run_id, "case_id": case_id}


# ── /tampering/explain, /tampering/locate ───────────────────────────────────
def run_explain(doc_type: str, tampering: dict, data: bytes, case_id_raw: str | None = None,
                actor: dict | None = None) -> dict:
    cfg = get_doc_type(doc_type)
    case_id = _case(case_id_raw, data)
    inp = {"doc_label": cfg.get("label", doc_type), "doc_type": doc_type, "tampering": tampering,
           "data": data, "_doc_case": document_case(case_id, doc_type, digest(data, 64))}
    if not settings.agents_enabled:
        return {"explanation": get_agent("tamper_explainer").execute(inp).payload, "run_id": None}
    with RECORDER.run("explain", case_id, actor=_actor(actor)) as run:
        r = get_agent("tamper_explainer").run(inp)
        return {"explanation": r.payload, "run_id": run.run_id}


def run_locate(tampering: dict, data: bytes, doc_type: str, case_id_raw: str | None = None,
               actor: dict | None = None) -> dict:
    case_id = _case(case_id_raw, data)
    inp = {"tampering": tampering, "data": data, "doc_type": doc_type,
           "_doc_case": document_case(case_id, doc_type, digest(data, 64))}
    if not settings.agents_enabled:
        return {"located": get_agent("tamper_locator").execute(inp).payload, "run_id": None}
    with RECORDER.run("locate", case_id, actor=_actor(actor)) as run:
        r = get_agent("tamper_locator").run(inp)
        return {"located": r.payload, "run_id": run.run_id}


# ── /document/mask ──────────────────────────────────────────────────────────
def run_mask(data: bytes, parsed: dict, doc_type: str, case_id_raw: str | None = None,
             actor: dict | None = None) -> dict:
    case_id = _case(case_id_raw, data)
    inp = {"data": data, "parsed": parsed, "doc_type": doc_type,
           "_doc_case": document_case(case_id, doc_type, digest(data, 64))}
    if not settings.agents_enabled:
        return {"masked": get_agent("pii_masking").execute(inp).payload, "run_id": None}
    with RECORDER.run("mask", case_id, actor=_actor(actor)) as run:
        r = get_agent("pii_masking").run(inp)
        return {"masked": r.payload, "run_id": run.run_id}


# ── /face/verify ────────────────────────────────────────────────────────────
def run_face(documents: list[tuple[str, bytes]], case_id_raw: str | None = None,
             actor: dict | None = None) -> dict:
    case_id = normalize_case_id(case_id_raw) or _synthetic(b"".join(b[:4096] for _, b in documents))
    inp = {"documents": documents}
    if not settings.agents_enabled:
        return {"result": get_agent("face_verification").execute(inp).payload["result"], "run_id": None}
    with RECORDER.run("face", case_id, actor=_actor(actor)) as run:
        r = get_agent("face_verification").run(inp)
        result, embeddings = r.payload["result"], r.payload["embeddings"]
        case_store.set_section(case_id, "face", {
            "overall": result["overall"], "summary": result["summary"],
            "pairs": result["pairs"],
            "documents": [{"doc_type": d["doc_type"], "face_count": d["face_count"],
                           "quality": [f.get("quality") for f in d["faces"]]} for d in result["documents"]]})
        if embeddings:
            try:
                from ..tools import dedupe
                dedupe.stage_faces(case_id, embeddings)
            except ImportError:
                pass
        return {"result": result, "run_id": run.run_id, "case_id": case_id}


# ── /liveness/check ─────────────────────────────────────────────────────────
def run_liveness(data: bytes, case_id_raw: str | None = None, actor: dict | None = None) -> dict:
    """Selfie → anti-spoof + match against the case's ID photo (staged by the
    Face Verification step). The selfie itself is not stored."""
    case_id = _case(case_id_raw, data)
    inp = {"data": data, "case_id": case_id if case_id_raw else None}
    if not settings.agents_enabled:
        return {"liveness": get_agent("liveness").execute(inp).payload, "run_id": None}
    with RECORDER.run("liveness", case_id, actor=_actor(actor)) as run:
        r = get_agent("liveness").run(inp)
        res = r.payload
        if case_id_raw:
            case_store.set_section(case_id, "liveness", {
                k: res.get(k) for k in ("verdict", "live", "live_score", "match", "quality", "notes")})
        return {"liveness": res, "run_id": run.run_id, "case_id": case_id}


# ── /application/store ──────────────────────────────────────────────────────
def run_store(record: dict, actor: dict | None = None) -> dict:
    case_id = normalize_case_id(record.get("case_id")) if record.get("case_id") else None
    inp = {"record": record, "case_id": case_id, "actor": actor or {}}
    if not settings.agents_enabled:
        store = get_agent("audit_storage").execute({**inp, "persist_sql": False}).payload["store"]
        _mask_later(record, store, case_id, actor)
        return {"store": store, "run_id": None}
    with RECORDER.run("store", case_id, case_key=record.get("app_id"), actor=_actor(actor)) as run:
        r = get_agent("audit_storage").run(inp)
        if case_id:
            case_store.link_application(case_id, record.get("app_id"),
                                        applicant={"name": record.get("applicant_name")},
                                        loan={"loan_type": record.get("loan_type"),
                                              "submitted_at": record.get("submitted_at"),
                                              **{k: v for k, v in (record.get("loan") or {}).items() if v not in (None, "")}})
            if record.get("consent"):       # evidence for the policy check (DL_CONSENT_001)
                case_store.set_section(case_id, "consent", record["consent"])
        _mask_later(record, r.payload["store"], case_id, actor)
        return {"store": r.payload["store"], "sql": r.payload.get("sql"), "run_id": run.run_id}


def _mask_later(record: dict, store: dict, case_id: str | None, actor: dict | None):
    """Documents submitted without a masked render get one in the background
    (mask_jobs.py) - the submission never waits for OCR-bound masking."""
    if not (store or {}).get("ok"):
        return
    from ...mask_jobs import schedule
    from ...storage import _decode_data
    for d in record.get("documents") or []:
        if d.get("masked_b64") or not d.get("original_b64"):
            continue
        data = _decode_data(d["original_b64"])
        if data:
            dt = str(d.get("doc_type") or "document")
            schedule(str(record.get("app_id")), dt, dt, str(d.get("label") or dt), data,
                     d.get("fields") if isinstance(d.get("fields"), dict) else {}, case_id, actor)
