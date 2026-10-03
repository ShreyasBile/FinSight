"""/settings/llm — choose the LLM behind the agents, at runtime.

Read by every role (the officer settings page shows the current model);
changed only by the Head Manager. NOTE: FinSight has no login yet — the role
comes from the X-Actor-Role header the UI sends. That is an advisory check, not
real security, and is documented as such.
"""
from __future__ import annotations

import copy

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ..config import settings
from .llm import anthropic_admin, gateway, ollama_admin, privacy, settings_store
from .llm.catalog import HOSTED_PROVIDERS, LLM_AGENTS, PROVIDERS

router = APIRouter(prefix="/settings", tags=["settings"])

PRIVACY_NOTICE = (
    "Documents, PAN and Aadhaar numbers, salary and bank data used by the agents below "
    "will leave this machine and be sent to Anthropic. Image-reading agents send the "
    "document image itself and cannot be redacted; text agents receive a pseudonymised "
    "evidence bundle (names replaced, ID numbers masked to the last 4 digits).")


class ModelChoice(BaseModel):
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    num_ctx: int | None = None
    timeout_s: float | None = None
    keep_alive: str | None = None
    effort: str | None = None


class SettingsUpdate(BaseModel):
    expected_revision: int
    default: ModelChoice | None = None
    lanes: dict[str, ModelChoice | None] | None = None
    agents: dict[str, ModelChoice | None] | None = None
    acknowledge_hosted: bool = False
    redact_text_for_hosted: bool | None = None


class TestRequest(BaseModel):
    provider: str
    model: str
    vision: bool = False


def _require_head(role: str | None):
    if (role or "").lower() != "head":
        raise HTTPException(403, "Only the Head Manager can change AI model settings")


def _view(doc: dict) -> dict:
    snap = settings_store.Snapshot(doc)
    effective = []
    for use in LLM_AGENTS.values():
        r = snap.resolve(use.agent_id)
        effective.append({"agent_id": use.agent_id, "label": use.label, "lane": use.lane,
                          "data_class": use.data_class, "needs": list(use.needs),
                          "purpose": use.purpose, "provider": r.provider, "model": r.model,
                          "source": r.source, "hosted": r.provider in HOSTED_PROVIDERS})
    hosted_any = any(e["hosted"] for e in effective)
    return {
        "revision": doc["revision"], "default": doc["default"], "lanes": doc.get("lanes", {}),
        "agents": doc.get("agents", {}), "effective": effective,
        "hosted_ack": doc.get("hosted_ack"), "hosted_in_use": hosted_any,
        "redact_text_for_hosted": doc.get("redact_text_for_hosted", True),
        "updated_by": doc.get("updated_by"), "updated_at": doc.get("updated_at"),
        "providers": list(PROVIDERS),
        "anthropic": {"configured": anthropic_admin.configured(),
                      "default_model": anthropic_admin.DEFAULT_MODEL},
        "ollama": {"host": settings.ollama_host},
        "hosted_forbidden": settings.finsight_forbid_hosted,
        "privacy": {"notice": PRIVACY_NOTICE, "affected": privacy.affected_agents("anthropic")},
    }


@router.get("/llm")
def get_llm_settings():
    return _view(settings_store.load())


def _merge(target: dict, choice: ModelChoice | None) -> dict | None:
    if choice is None:
        return None
    out = dict(target or {})
    out.update({k: v for k, v in choice.model_dump().items() if v is not None})
    return out


@router.put("/llm")
def put_llm_settings(body: SettingsUpdate, x_actor_role: str | None = Header(default=None),
                     x_actor_id: str | None = Header(default=None)):
    _require_head(x_actor_role)
    current = settings_store.load()
    if body.expected_revision != current["revision"]:
        raise HTTPException(409, f"Settings changed since you loaded them (now revision "
                                 f"{current['revision']}) — reload and try again")
    doc = copy.deepcopy(current)
    if body.default is not None:
        doc["default"] = _merge(doc["default"], body.default)
    for lane, choice in (body.lanes or {}).items():
        if lane not in ("vision", "text"):
            raise HTTPException(422, f"Unknown lane '{lane}' (use vision or text)")
        merged = _merge(doc["lanes"].get(lane, {}), choice)
        if merged is None:
            doc["lanes"].pop(lane, None)
        else:
            doc["lanes"][lane] = merged
    for agent_id, choice in (body.agents or {}).items():
        if agent_id not in LLM_AGENTS:
            raise HTTPException(422, f"'{agent_id}' is not an LLM-using agent")
        merged = _merge(doc["agents"].get(agent_id, {}), choice)
        if merged is None:
            doc["agents"].pop(agent_id, None)
        else:
            doc["agents"][agent_id] = merged
    if body.redact_text_for_hosted is not None:
        doc["redact_text_for_hosted"] = body.redact_text_for_hosted

    snap = settings_store.Snapshot(doc)
    resolved = {a: snap.resolve(a) for a in LLM_AGENTS}
    hosted = [a for a, r in resolved.items() if r.provider in HOSTED_PROVIDERS]
    installed = None
    for agent_id, r in resolved.items():
        if r.provider not in PROVIDERS:
            raise HTTPException(422, f"Unknown provider '{r.provider}'")
        if r.provider == "ollama":
            if installed is None:
                try:
                    installed = {m["name"]: m for m in ollama_admin.list_models(force=True)}
                except Exception:  # noqa: BLE001
                    raise HTTPException(422, "Ollama is not reachable, so the model choice "
                                             "cannot be verified — start Ollama and retry")
            if r.model not in installed:
                raise HTTPException(422, f"Model '{r.model}' is not installed in Ollama "
                                         f"(run: ollama pull {r.model})")
            if "vision" in LLM_AGENTS[agent_id].needs and "vision" not in installed[r.model]["capabilities"]:
                raise HTTPException(422, f"{LLM_AGENTS[agent_id].label} reads document images, but "
                                         f"'{r.model}' has no vision capability")
    if hosted:
        if settings.finsight_forbid_hosted:
            raise HTTPException(422, "Hosted providers are disabled in this environment")
        if not anthropic_admin.configured():
            raise HTTPException(422, "Anthropic is not configured on the server (ANTHROPIC_API_KEY)")
        if not (body.acknowledge_hosted or current.get("hosted_ack")):
            raise HTTPException(422, "Acknowledge the privacy notice before sending applicant "
                                     "data to a hosted provider")
        if body.acknowledge_hosted:
            from datetime import datetime, timezone
            doc["hosted_ack"] = {"by": x_actor_id or "head", "agents": hosted,
                                 "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    else:
        doc["hosted_ack"] = None  # back to all-local: a future switch must be re-acknowledged

    saved = settings_store.save(doc, x_actor_id or "head")
    try:
        from .tracing.audit import record_setting_change
        record_setting_change(current, saved, x_actor_id)
    except Exception:  # noqa: BLE001 - audit is best effort
        pass
    return _view(saved)


@router.get("/llm/models")
def list_models(provider: str = "ollama", refresh: bool = False):
    if provider == "ollama":
        try:
            return {"provider": "ollama", "reachable": True,
                    "models": ollama_admin.list_models(force=refresh)}
        except Exception as exc:  # noqa: BLE001
            return {"provider": "ollama", "reachable": False, "models": [],
                    "error": f"{type(exc).__name__}: {exc}"}
    if provider == "anthropic":
        return {"provider": "anthropic", "configured": anthropic_admin.configured(),
                "default_model": anthropic_admin.DEFAULT_MODEL,
                "models": anthropic_admin.list_models()}
    if provider == "mock":
        return {"provider": "mock", "models": [{"name": "mock", "capabilities": ["vision", "json"]}]}
    raise HTTPException(422, f"Unknown provider '{provider}'")


@router.post("/llm/test")
def test_connection(body: TestRequest, x_actor_role: str | None = Header(default=None)):
    _require_head(x_actor_role)
    return gateway.test_call(body.provider, body.model, body.vision)
