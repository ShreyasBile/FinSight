"""/agents — the agent registry, run traces, case files, and case-level flows.

    GET  /agents                         every agent (29) with kind, crew, model, stats
    GET  /agents/{id}                    one agent
    GET  /agents/health                  crewai importable, SQL sink, LLM queue, Ollama
    GET  /agents/stats                   per-agent runs / p50 / p95 / degraded
    GET  /agents/runs?case_id=&agent_id= recent runs (live ring + JSON traces)
    GET  /agents/runs/{run_id}           one run with its steps
    GET  /agents/cases                   recent case files
    GET  /agents/cases/{case_id}         a case file (evidence gathered so far)
    GET  /agents/cases/by-application/{no}
    POST /agents/cases/{case_id}/assess      CaseAssessmentFlow (0 LLM calls)
    POST /agents/cases/{case_id}/underwrite  UnderwritingFlow (async-safe)
    GET  /agents/cases/{case_id}/{fraud|underwriting|memo}
    POST /agents/applications/{no}/assess|underwrite   same, for SQL-resident applications
    GET  /agents/applications/{no}
    POST /agents/policy/check            evidence-backed Policy Check
    GET  /agents/fraud/triage            Fraud Triage proposals
    GET  /agents/process/insights        Process-Mining Analyst
    POST /agents/{id}/run                run one agent on JSON inputs (tests, eval)
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from ..config import settings
from . import case_store
from .llm import gateway
from .registry import IMPLEMENTATIONS, REGISTRY, get_agent, is_enabled
from .tracing.ids import normalize_case_id
from .tracing.recorder import RECORDER
from .tracing.sink_sql import SINK

router = APIRouter(prefix="/agents", tags=["agents"])


def _agent_info(spec, stats: dict) -> dict:
    from . import impl  # noqa: F401 - register implementations
    model = None
    if spec.llm:
        try:
            r = gateway.resolve(spec.id)
            model = {"provider": r.provider, "model": r.model, "source": r.source}
        except Exception:  # noqa: BLE001
            model = None
    return {"id": spec.id, "number": spec.number, "name": spec.name, "crew": spec.crew,
            "kind": spec.kind, "role": spec.role, "goal": spec.goal, "wraps": spec.wraps,
            "activity_code": spec.activity_code, "process": spec.process, "llm": spec.llm,
            "phase": spec.phase, "surface": spec.surface, "side_effects": list(spec.side_effects),
            "implemented": spec.id in IMPLEMENTATIONS, "enabled": is_enabled(spec.id),
            "model": model, "stats": stats.get(spec.id)}


@router.get("")
def list_agents():
    stats = RECORDER.stats()
    return [_agent_info(s, stats) for s in sorted(REGISTRY.values(), key=lambda s: s.number)]


@router.get("/health")
def health():
    from .governor import GATE
    from .llm import ollama_admin
    try:
        from .crewai_compat import crewai
        cv = crewai().__version__
    except Exception as exc:  # noqa: BLE001
        cv = f"unavailable: {exc}"
    from . import impl  # noqa: F401
    return {"agents_enabled": settings.agents_enabled, "crewai": cv,
            "implemented": len(IMPLEMENTATIONS), "registered": len(REGISTRY),
            "sql_sink": SINK.status(), "llm_queue": GATE.snapshot(),
            "ollama_reachable": ollama_admin.reachable(), "llm_calls": gateway.STATS,
            "environment": settings.agents_env}


@router.get("/stats")
def stats():
    return RECORDER.stats()


@router.get("/runs")
def runs(case_id: str | None = None, agent_id: str | None = None, limit: int = Query(50, le=200)):
    cid = normalize_case_id(case_id) if case_id else None
    return RECORDER.runs(case_id=cid, agent_id=agent_id, limit=limit)


@router.get("/runs/{run_id}")
def run_detail(run_id: str):
    r = RECORDER.get(run_id)
    if r is None:
        raise HTTPException(404, "Run not found")
    return r


@router.get("/cases")
def cases(limit: int = 50):
    return case_store.list_cases(limit)


@router.get("/cases/by-application/{application_no}")
def case_by_application(application_no: str):
    cid = case_store.by_application(application_no)
    if not cid:
        raise HTTPException(404, "No agent case file for this application")
    return case_store.get(cid)


@router.get("/cases/{case_id}")
def case_file(case_id: str):
    cid = normalize_case_id(case_id)
    if not case_store.exists(cid):
        raise HTTPException(404, "No evidence has been gathered for this case yet")
    return case_store.get(cid)


# ── case-level flows ─────────────────────────────────────────────────────────
def _actor(role, actor_id):
    return {"role": role, "id": actor_id} if (role or actor_id) else {}


@router.post("/cases/{case_id}/assess")
async def assess(case_id: str, body: dict = Body(default={}),
                 x_actor_role: str | None = Header(default=None), x_actor_id: str | None = Header(default=None)):
    from .flows.case_flows import run_assessment
    cid = normalize_case_id(case_id)
    if not case_store.exists(cid):
        raise HTTPException(404, "No evidence has been gathered for this case yet")
    return await run_in_threadpool(run_assessment, cid, body or {}, _actor(x_actor_role, x_actor_id))


@router.post("/cases/{case_id}/underwrite")
async def underwrite(case_id: str, body: dict = Body(default={}),
                     x_actor_role: str | None = Header(default=None), x_actor_id: str | None = Header(default=None)):
    from .flows.underwriting_flow import run_underwriting
    cid = normalize_case_id(case_id)
    if not case_store.exists(cid):
        raise HTTPException(404, "No evidence has been gathered for this case yet")
    return await run_in_threadpool(run_underwriting, cid, body or {}, _actor(x_actor_role, x_actor_id))


@router.get("/cases/{case_id}/{section}")
def case_section(case_id: str, section: str):
    key = {"fraud": "assessment", "underwriting": "underwriting", "memo": "memo",
           "offer": "offer", "workflow": "workflow", "face": "face"}.get(section)
    if key is None:
        raise HTTPException(404, "Unknown section")
    c = case_store.get(normalize_case_id(case_id))
    if c.get(key) is None:
        raise HTTPException(404, f"No {section} result yet for this case")
    return c[key]


@router.get("/applications/{application_no}")
def application_results(application_no: str):
    from .flows.app_evidence import application_case
    return application_case(application_no)


@router.post("/applications/{application_no}/assess")
async def application_assess(application_no: str, body: dict = Body(default={}),
                             x_actor_role: str | None = Header(default=None),
                             x_actor_id: str | None = Header(default=None)):
    from .flows.app_evidence import ensure_case
    from .flows.case_flows import run_assessment
    cid = await run_in_threadpool(ensure_case, application_no)
    return await run_in_threadpool(run_assessment, cid, body or {}, _actor(x_actor_role, x_actor_id))


@router.post("/applications/{application_no}/underwrite")
async def application_underwrite(application_no: str, body: dict = Body(default={}),
                                 x_actor_role: str | None = Header(default=None),
                                 x_actor_id: str | None = Header(default=None)):
    from .flows.app_evidence import ensure_case
    from .flows.underwriting_flow import run_underwriting
    cid = await run_in_threadpool(ensure_case, application_no)
    return await run_in_threadpool(run_underwriting, cid, body or {}, _actor(x_actor_role, x_actor_id))


@router.post("/policy/check")
async def policy_check(body: dict = Body(...)):
    from .flows.case_flows import run_policy_only
    return await run_in_threadpool(run_policy_only, body)


@router.get("/fraud/triage")
async def fraud_triage(limit: int = 20, narrate: bool = False):
    from .flows.ops_flows import run_triage
    return await run_in_threadpool(run_triage, limit, narrate)


@router.post("/notify/{event}")
async def notify(event: str, body: dict = Body(default={})):
    """Draft (and write, in-app only) the notifications for a lifecycle event.
    body: {case_id? | application_no?, context: {...}, write: bool}."""
    from .tools.notifier import TEMPLATES
    if event not in {t["event"] for t in TEMPLATES.values()}:
        raise HTTPException(404, "Unknown notification event")
    case = {}
    if body.get("case_id"):
        case = case_store.get(body["case_id"])
    elif body.get("application_no"):
        cid = case_store.by_application(body["application_no"])
        case = case_store.get(cid) if cid else {"application_no": body["application_no"]}
    r = await run_in_threadpool(get_agent("notifier").run, {"case": case, "event": event,
                                                            "context": body.get("context") or {},
                                                            "write": bool(body.get("write", True))})
    return {**r.payload, "run_id": r.run_id}


@router.get("/process/insights")
async def process_insights(process_code: str = "loan_application", include_demo: bool = True,
                           narrate: bool = False):
    from .flows.ops_flows import run_process_insights
    return await run_in_threadpool(run_process_insights, process_code, include_demo, narrate)


@router.get("/{agent_id}")
def agent_detail(agent_id: str):
    spec = REGISTRY.get(agent_id)
    if spec is None:
        raise HTTPException(404, "Unknown agent")
    return _agent_info(spec, RECORDER.stats())


@router.post("/{agent_id}/run")
async def run_agent(agent_id: str, body: dict = Body(default={})):
    """Run one agent on JSON inputs (no file bytes). For the eval harness and
    for agents whose inputs are data, not documents."""
    if agent_id not in REGISTRY:
        raise HTTPException(404, "Unknown agent")
    try:
        agent = get_agent(agent_id)
    except KeyError as exc:
        raise HTTPException(501, str(exc))
    inputs = body.get("inputs") or {}
    res = await run_in_threadpool(agent.run, inputs, flow=f"manual:{agent_id}",
                                  case_id=normalize_case_id(body.get("case_id")) if body.get("case_id") else None)
    return {"agent_id": agent_id, "payload": res.payload, "ai_outcome": res.ai_outcome, "score": res.score,
            "confidence": res.confidence, "summary": res.summary, "status": res.status,
            "narrative": res.narrative, "warnings": res.warnings, "run_id": res.run_id}
