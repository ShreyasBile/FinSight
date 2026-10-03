import json
import logging
import time
from functools import partial

from fastapi import FastAPI, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .doc_types import DOC_TYPES, get_doc_type
from .guardrails import validate
from .schemas import (ApplicationStoreRequest, ApplicationStoreResult, DocTypeInfo,
                      ExtractResponse, FaceVerifyResult, FieldResult,
                      IntegrityResult, LocateResult, MaskResult, PlainExplanation,
                      TamperingResult)
from .agents.flows import document_flows as flows
from .utils import guess_mime
from .compliance import router as compliance_router, startup as compliance_startup
from .lending import router as lending_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("finsight.ocr")

app = FastAPI(title="FinSight OCR Service", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_methods=["*"],
    allow_headers=["*"],
    # the UI reads the agent run id to show that run in the Agent Activity panel, and a
    # download's file name (credit memo export)
    expose_headers=["X-Finsight-Run-Id", "X-Finsight-Case-Id", "Content-Disposition"],
)


def _actor(role: str | None, actor_id: str | None) -> dict:
    return {"role": role, "id": actor_id} if (role or actor_id) else {}


def _stamp(response: Response, out: dict):
    if out.get("run_id"):
        response.headers["X-Finsight-Run-Id"] = out["run_id"]
    if out.get("case_id"):
        response.headers["X-Finsight-Case-Id"] = out["case_id"]

# RBI Compliance module (/compliance/*): seeds its own SQLite DB and builds the
# BM25 index once at boot — cheap (a few dozen chunks), safe to do at import.
app.include_router(compliance_router)
compliance_startup()

# Lending data (/lending/*): everything the borrower and officer screens read.
# No startup work — the router opens a connection per request and returns 503
# if SQL Server is unreachable, so document processing is unaffected by a
# database outage.
app.include_router(lending_router)
# Decisions (/lending/applications/{no}/actions, document files, re-uploads): the
# officer / head / borrower steps that move an application through its lifecycle.
from .decisions import router as decisions_router  # noqa: E402

app.include_router(decisions_router)
# Credit memo as a PDF / Word document (/lending/applications/{no}/memo).
from .memo_export import router as memo_router  # noqa: E402

app.include_router(memo_router)

# LLM selection (/settings/llm): the head-only Settings page chooses which
# provider/model every LLM-using agent runs on, at runtime.
from .agents.settings_api import router as settings_router  # noqa: E402
from .agents.api import router as agents_router  # noqa: E402

app.include_router(settings_router)
# Agents (/agents/*): registry, run traces, case files, case-level flows.
app.include_router(agents_router)


@app.on_event("startup")
def _warm_agents():
    """Import crewai and build the flow classes in the background (~3 s cold),
    so the first upload does not pay for it; start the SQL event sink."""
    import threading

    def _warm():
        try:
            from .agents import impl  # noqa: F401
            from .agents.flows.document_flows import _intake_flow_class
            _intake_flow_class()
            from .agents.tracing.sink_sql import SINK
            if settings.agents_sql_sink and settings.db_enabled:
                SINK.start()
        except Exception:  # noqa: BLE001 - warm-up is an optimisation only
            log.exception("agent warm-up failed (agents will initialise on first use)")

    threading.Thread(target=_warm, name="finsight-agent-warmup", daemon=True).start()


@app.get("/health")
def health():
    from . import db as _db
    return {"status": "ok", "provider": settings.ocr_provider,
            "database": _db.health()}


@app.get("/doc-types", response_model=list[DocTypeInfo])
def doc_types():
    return [DocTypeInfo(id=k, label=v["label"], required=v["required"]) for k, v in DOC_TYPES.items()]


@app.post("/extract", response_model=ExtractResponse)
async def extract(response: Response, doc_type: str = Form(...), file: UploadFile = File(...),
                  x_case_id: str | None = Header(default=None),
                  x_actor_role: str | None = Header(default=None),
                  x_actor_id: str | None = Header(default=None)):
    cfg = get_doc_type(doc_type)
    if cfg is None:
        raise HTTPException(400, f"Unknown doc_type '{doc_type}'")

    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > settings.max_file_mb * 1024 * 1024:
        raise HTTPException(413, f"File exceeds {settings.max_file_mb} MB limit")

    mime = guess_mime(file.filename, file.content_type)

    # Preprocess (warp + orient) BEFORE the VLM — this is required for extraction
    # ACCURACY, not cosmetics. On a rotated/skewed real card the VLM bleeds text
    # between stacked fields (measured: a sideways PAN's father-name leaked into
    # the name field, and one name gained a garbled extra word, until the card
    # was warped flat and turned upright). The warp-first pipeline makes this
    # cheap (~6-9s — flattening is ~0.02s of OpenCV and collapses orientation to
    # a 2-angle check; see preprocess.py), so it stays on the upload path.
    # The tampering cascade (the genuinely slow, 20-80s part) is what moved to
    # POST /document/integrity for the Review & Submit step.
    #
    # Runs as the Intake crew: Preprocessing -> Extraction -> Validation ->
    # Document Identity agents (app/agents/flows/document_flows.py).
    try:
        out = await run_in_threadpool(flows.run_extract, data, mime, doc_type, file.filename,
                                      x_case_id, _actor(x_actor_role, x_actor_id))
    except Exception as exc:  # noqa: BLE001 - surface a clean error to the client
        log.exception("extraction failed")
        raise HTTPException(502, f"Extraction failed: {exc}") from exc
    _stamp(response, out)

    pre, ext, check = out["preprocess"], out["extraction"], out["validation"]
    timings: dict[str, float] = {}
    if pre.get("ms") is not None:
        timings["preprocess_ms"] = pre["ms"]
    timings["extract_ms"] = ext["ms"]
    parsed = ext["parsed"]

    return ExtractResponse(
        doc_type=doc_type,
        provider=ext["provider"],
        parsed=parsed or {},
        raw=ext["raw"],
        fields=[FieldResult(**f) for f in check["fields"]],
        confidence=check["confidence"],
        needs_review=check["needs_review"],
        tampering=None,
        cleaned_preview=pre.get("cleaned_preview"),
        timings=timings,
        document_identity=out["identity"],
    )


@app.post("/document/integrity", response_model=IntegrityResult)
async def document_integrity(
    response: Response,
    doc_type: str = Form(...),
    file: UploadFile = File(...),
    parsed_json: str = Form("{}"),
    x_case_id: str | None = Header(default=None),
    x_actor_role: str | None = Header(default=None),
    x_actor_id: str | None = Header(default=None),
):
    """Deferred tampering analysis for the Review & Submit page. Runs the
    forensic cascade on the ORIGINAL bytes (preprocessing rewrites pixels and
    would destroy the compression/noise traces the signals read), once per
    document when the borrower reaches Review & Submit rather than blocking
    every upload — it's the slow (20-80s) part of document handling.

    The cleaned/auto-oriented render is NOT produced here — /extract already
    returns it (it preprocesses for extraction anyway), and the frontend reuses
    that for the hover preview and PII masking. `parsed_json` is /extract's
    parsed fields, so business rules can corroborate the extracted values.
    """
    cfg = get_doc_type(doc_type)
    if cfg is None:
        raise HTTPException(400, f"Unknown doc_type '{doc_type}'")

    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    try:
        parsed = json.loads(parsed_json) if parsed_json else {}
    except json.JSONDecodeError:
        parsed = {}

    tampering = None
    timings: dict[str, float] = {}
    if settings.tampering_enabled:
        try:
            t0 = time.perf_counter()
            # Authenticity agent -> Tampering Forensics agent (nine signals as tool calls)
            out = await run_in_threadpool(flows.run_integrity, data, file.filename or "upload",
                                          doc_type, parsed or {}, x_case_id,
                                          _actor(x_actor_role, x_actor_id))
            tampering = out["tampering"]
            _stamp(response, out)
            if tampering is not None:
                timings["tampering_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        except Exception:  # noqa: BLE001 - endpoint must not 500 on a signal error
            log.exception("tampering analysis errored — omitting from response")

    return IntegrityResult(
        tampering=TamperingResult(**tampering) if tampering else None,
        cleaned_preview=None,  # provided by /extract; see docstring
        timings=timings,
    )


@app.post("/tampering/explain", response_model=PlainExplanation)
async def tampering_explain(
    response: Response,
    doc_type: str = Form(...),
    tampering_json: str = Form(...),
    file: UploadFile = File(...),
    x_case_id: str | None = Header(default=None),
    x_actor_role: str | None = Header(default=None),
    x_actor_id: str | None = Header(default=None),
):
    """On-demand plain-language + visually-grounded explanation for a MEDIUM/HIGH
    document, TOGGLEABLE from the review UI. Separated from /extract so a plain
    upload never pays this call's latency (~3-4s) unless the user actually asks
    to see it — /extract already returns the raw findings for free; this just
    translates them (TRANSLATION ONLY, see tampering/explain.py — detection is
    unchanged and 100% deterministic, this cannot alter the risk tier).

    `tampering_json` is the exact `tampering` object /extract already returned
    for this document (the frontend already has it — no need to recompute the
    cascade). Re-uploading the file lets Qwen see the actual document so it can
    say WHERE each already-found issue is, not just restate it in text.
    """
    cfg = get_doc_type(doc_type)
    if cfg is None:
        raise HTTPException(400, f"Unknown doc_type '{doc_type}'")
    try:
        tampering = json.loads(tampering_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"tampering_json is not valid JSON: {exc}")
    if tampering.get("risk_tier") not in ("MEDIUM", "HIGH"):
        raise HTTPException(400, "Explanation is only available for MEDIUM/HIGH documents")

    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")

    out = await run_in_threadpool(flows.run_explain, doc_type, tampering, data, x_case_id,
                                  _actor(x_actor_role, x_actor_id))
    _stamp(response, out)
    explanation = out["explanation"]
    if explanation is None:
        raise HTTPException(502, "Explanation unavailable — the local model may be busy or unreachable")
    return PlainExplanation(**explanation)


@app.post("/tampering/locate", response_model=LocateResult)
async def tampering_locate(
    response: Response,
    doc_type: str = Form(...),
    tampering_json: str = Form(...),
    file: UploadFile = File(...),
    x_case_id: str | None = Header(default=None),
    x_actor_role: str | None = Header(default=None),
    x_actor_id: str | None = Header(default=None),
):
    """On-demand OCR-grounded localization: draws numbered boxes on the document
    showing WHERE each already-detected finding is (the specific figure/ID it
    cites, found on the page by OCR). No detection, no LLM — a finding is only
    boxed if the value it names is literally printed on the document; metadata
    concerns (e.g. producer software) come back marked "not visually locatable".

    Shown on the Review & Submit page for MEDIUM/HIGH documents. `tampering_json`
    is the exact object /extract returned, so the cascade is never re-run.
    """
    cfg = get_doc_type(doc_type)
    if cfg is None:
        raise HTTPException(400, f"Unknown doc_type '{doc_type}'")
    try:
        tampering = json.loads(tampering_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"tampering_json is not valid JSON: {exc}")
    if tampering.get("risk_tier") not in ("MEDIUM", "HIGH"):
        raise HTTPException(400, "Localization is only available for MEDIUM/HIGH documents")

    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")

    # OCR-bound (RapidOCR) — keep it off the event loop. Tamper Locator agent.
    out = await run_in_threadpool(flows.run_locate, tampering, data, doc_type, x_case_id,
                                  _actor(x_actor_role, x_actor_id))
    _stamp(response, out)
    result = out["located"]
    if result is None:
        raise HTTPException(502, "Could not analyse the document for issue locations")
    return LocateResult(**result)


@app.post("/face/verify", response_model=FaceVerifyResult)
async def face_verify(response: Response, doc_types: str = Form(...),
                      files: list[UploadFile] = File(...),
    x_case_id: str | None = Header(default=None),
    x_actor_role: str | None = Header(default=None),
    x_actor_id: str | None = Header(default=None),
                      ):
    """Cross-document face verification for the Review & Submit page: finds the
    face on every submitted document (any orientation), embeds it, and reports
    whether the same person appears across all of them. One input to the fraud
    -detection module; evidence for a human, never an auto-reject.

    `doc_types` is comma-separated, in the same order as `files`.
    """
    types = [t.strip() for t in doc_types.split(",") if t.strip()]
    if len(types) != len(files):
        raise HTTPException(400, f"doc_types has {len(types)} entries but {len(files)} files were sent")
    if len(files) < 1:
        raise HTTPException(400, "No files provided")

    documents = []
    for dt, f in zip(types, files):
        data = await f.read()
        if data:
            documents.append((dt, data))
    if not documents:
        raise HTTPException(400, "All files were empty")

    # CPU-bound (OpenCV detection + embedding) — off the event loop.
    t0 = time.perf_counter()
    out = await run_in_threadpool(flows.run_face, documents, x_case_id, _actor(x_actor_role, x_actor_id))
    _stamp(response, out)
    result = out["result"]
    result["timings"] = {"face_ms": round((time.perf_counter() - t0) * 1000, 1)}
    return FaceVerifyResult(**result)


@app.post("/liveness/check")
async def liveness_check(response: Response, file: UploadFile = File(...),
                         x_case_id: str | None = Header(default=None),
                         x_actor_role: str | None = Header(default=None),
                         x_actor_id: str | None = Header(default=None)):
    """Wizard selfie step: is it a live person (not a printed photo or a
    screen), and the same person as the ID photo on this case (X-Case-Id)?
    Advisory — a failed check asks for a retake; the selfie is not stored."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty selfie")
    t0 = time.perf_counter()
    out = await run_in_threadpool(flows.run_liveness, data, x_case_id, _actor(x_actor_role, x_actor_id))
    _stamp(response, out)
    return {**out["liveness"], "timings": {"liveness_ms": round((time.perf_counter() - t0) * 1000, 1)}}


@app.post("/document/mask", response_model=MaskResult)
async def document_mask(
    response: Response,
    file: UploadFile = File(...),
    parsed_json: str = Form("{}"),
    doc_type: str = Form("generic"),
    x_case_id: str | None = Header(default=None),
    x_actor_role: str | None = Header(default=None),
    x_actor_id: str | None = Header(default=None),
):
    """Return a privacy-masked render of the document for the hover preview:
    only the PAN number and the Aadhaar number are redacted (last 4 kept
    visible) — no other number is touched. OCR-bound, so run off the event
    loop. A document with neither a PAN nor an Aadhaar number comes back
    re-rendered unchanged.
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    try:
        parsed = json.loads(parsed_json) if parsed_json else {}
    except json.JSONDecodeError:
        parsed = {}

    t0 = time.perf_counter()
    out = await run_in_threadpool(flows.run_mask, data, parsed, doc_type, x_case_id,
                                  _actor(x_actor_role, x_actor_id))
    _stamp(response, out)
    result = out["masked"]
    if result is None:
        raise HTTPException(502, "Could not render a masked preview for this document")
    result["timings"] = {"mask_ms": round((time.perf_counter() - t0) * 1000, 1)}
    return MaskResult(**result)


@app.post("/application/store", response_model=ApplicationStoreResult)
async def application_store(req: ApplicationStoreRequest, response: Response,
    x_case_id: str | None = Header(default=None),
    x_actor_role: str | None = Header(default=None),
    x_actor_id: str | None = Header(default=None),
                            ):
    """Persist a submitted application to the audit store: writes the original +
    masked files under storage/<app_id>/ and appends one row per document to
    storage/applications.xlsx, in both a Manager (full PII) and an Officer (PII
    masked) sheet. See storage.py. Never fails the submission — a storage error
    comes back as {ok: false, error}."""
    record = req.model_dump()
    if not record.get("case_id") and x_case_id:
        record["case_id"] = x_case_id
    out = await run_in_threadpool(flows.run_store, record, _actor(x_actor_role, x_actor_id))
    _stamp(response, out)
    sql = out.get("sql") or {}
    from .mask_jobs import pending
    return ApplicationStoreResult(**out["store"], application_no=sql.get("application_no"),
                                  sql_ok=sql.get("ok") if sql else None, sql_error=sql.get("error"),
                                  masking_in_background=pending(str(record.get("app_id") or "")))
