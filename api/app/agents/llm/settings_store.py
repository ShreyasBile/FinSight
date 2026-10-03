"""Runtime LLM selection, set from the head-only Settings page.

Persisted as api/data/llm_settings.json (atomic replace) so it works with the
database down and survives restarts. Resolution for one agent:

    per-agent override  →  lane (vision | text)  →  default  →  environment

Every agent run takes a snapshot() first, so a Settings change in the middle
of a run can never mix two models inside one run. The Anthropic API key is
never stored here — it stays in api/.env and is never returned to a client.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from ...config import settings
from .catalog import LLM_AGENTS, PROVIDERS

_lock = threading.Lock()
_cache: dict | None = None
_cache_mtime: float | None = None

_MODEL_KEYS = ("provider", "model", "temperature", "num_ctx", "timeout_s", "keep_alive", "effort")


@dataclass(frozen=True)
class ResolvedLLM:
    provider: str
    model: str
    temperature: float
    num_ctx: int
    timeout_s: float
    keep_alive: str
    effort: str
    source: str            # agent | lane | default | env — where the choice came from
    revision: int

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def signature(self) -> str:
        return f"{self.provider}:{self.model}"


def _path():
    return settings.data_dir / "llm_settings.json"


def _env_default() -> dict:
    provider = (settings.ocr_provider or "ollama").lower()
    if provider not in PROVIDERS:
        provider = "ollama"
    model = settings.anthropic_model if provider == "anthropic" else settings.ollama_model
    if provider == "mock":
        model = "mock"
    return {"provider": provider, "model": model, "temperature": 0.0,
            "num_ctx": settings.ollama_num_ctx, "timeout_s": 120.0,
            "keep_alive": "10m", "effort": "low"}


def _blank() -> dict:
    return {"revision": 0, "default": _env_default(), "lanes": {}, "agents": {},
            "hosted_ack": None, "redact_text_for_hosted": True,
            "updated_by": None, "updated_at": None}


def load() -> dict:
    """Current settings document (cached; re-read if the file changed)."""
    global _cache, _cache_mtime
    p = _path()
    with _lock:
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = None
        if _cache is not None and mtime == _cache_mtime:
            return copy.deepcopy(_cache)
        doc = _blank()
        if mtime is not None:
            try:
                stored = json.loads(p.read_text(encoding="utf-8"))
                doc.update({k: v for k, v in stored.items() if k in doc})
                doc["default"] = {**_env_default(), **(stored.get("default") or {})}
            except (OSError, ValueError):
                pass  # unreadable file -> environment defaults, never a crash
        _cache, _cache_mtime = doc, mtime
        return copy.deepcopy(doc)


def save(doc: dict, actor: str | None) -> dict:
    """Atomically persist a new revision (caller validated it)."""
    global _cache, _cache_mtime
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = copy.deepcopy(doc)
    doc["revision"] = int(doc.get("revision", 0)) + 1
    doc["updated_by"] = actor
    doc["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    with _lock:
        _cache, _cache_mtime = doc, p.stat().st_mtime
    return copy.deepcopy(doc)


def lane_of(agent_id: str) -> str:
    use = LLM_AGENTS.get(agent_id)
    return use.lane if use else "text"


def resolve(agent_id: str, doc: dict | None = None) -> ResolvedLLM:
    doc = doc or load()
    base = dict(doc["default"])
    source = "default"
    lane = doc.get("lanes", {}).get(lane_of(agent_id)) or {}
    if lane.get("provider") or lane.get("model"):
        base.update({k: v for k, v in lane.items() if k in _MODEL_KEYS and v not in (None, "")})
        source = "lane"
    override = doc.get("agents", {}).get(agent_id) or {}
    if override.get("provider") or override.get("model"):
        base.update({k: v for k, v in override.items() if k in _MODEL_KEYS and v not in (None, "")})
        source = "agent"
    if doc.get("revision", 0) == 0 and source == "default":
        source = "env"
    provider = str(base.get("provider") or "ollama").lower()
    model = str(base.get("model") or "")
    if not model:
        model = {"anthropic": settings.anthropic_model, "mock": "mock"}.get(provider, settings.ollama_model)
    return ResolvedLLM(
        provider=provider, model=model,
        temperature=float(base.get("temperature", 0.0) or 0.0),
        num_ctx=int(base.get("num_ctx") or settings.ollama_num_ctx),
        timeout_s=float(base.get("timeout_s") or 120.0),
        keep_alive=str(base.get("keep_alive") or "10m"),
        effort=str(base.get("effort") or "low"),
        source=source, revision=int(doc.get("revision", 0)))


class Snapshot:
    """A frozen view of the settings for one run."""

    def __init__(self, doc: dict | None = None):
        self.doc = doc or load()
        self.revision = int(self.doc.get("revision", 0))

    def resolve(self, agent_id: str) -> ResolvedLLM:
        return resolve(agent_id, self.doc)

    @property
    def hosted_acknowledged(self) -> bool:
        return bool(self.doc.get("hosted_ack"))

    @property
    def redact_text_for_hosted(self) -> bool:
        return bool(self.doc.get("redact_text_for_hosted", True))


def snapshot() -> Snapshot:
    return Snapshot()
