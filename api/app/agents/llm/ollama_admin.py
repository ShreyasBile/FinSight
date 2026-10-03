"""Local Ollama model discovery (for the Settings model picker and for
capability checks such as "does this model accept images?")."""
from __future__ import annotations

import threading
import time

import httpx

from ...config import settings

_lock = threading.Lock()
_models_cache: tuple[float, list] | None = None
_caps_cache: dict[str, list[str]] = {}
_TTL = 60.0


def _host() -> str:
    return host()


def host() -> str:
    """The Ollama base URL with 'localhost' pinned to IPv4. On Windows,
    'localhost' resolves to ::1 first while Ollama listens on 127.0.0.1 only;
    a synchronous client then waits ~1 s per IPv6 attempt before falling back
    (measured: 2.07 s of every model call spent in connect)."""
    h = settings.ollama_host.rstrip("/")
    return h.replace("://localhost", "://127.0.0.1")


_client: httpx.Client | None = None


def client() -> httpx.Client:
    """One pooled client for every Ollama call (keep-alive: no reconnect per call)."""
    global _client
    with _lock:
        if _client is None:
            _client = httpx.Client(timeout=None, limits=httpx.Limits(max_keepalive_connections=8))
        return _client


def capabilities(model: str) -> list[str]:
    """['completion', 'vision', 'tools', 'thinking', ...] from /api/show (cached)."""
    with _lock:
        if model in _caps_cache:
            return _caps_cache[model]
    try:
        r = client().post(f"{_host()}/api/show", json={"model": model}, timeout=10)
        caps = list(r.json().get("capabilities") or []) if r.status_code < 400 else []
    except Exception:  # noqa: BLE001 - unknown capabilities, not an error
        return []
    with _lock:
        _caps_cache[model] = caps
    return caps


def list_models(force: bool = False) -> list[dict]:
    """Installed models with size/quantisation/capabilities/loaded flag."""
    global _models_cache
    now = time.time()
    with _lock:
        if not force and _models_cache and now - _models_cache[0] < _TTL:
            return _models_cache[1]
    tags = client().get(f"{_host()}/api/tags", timeout=10).json().get("models", [])
    try:
        loaded = {m.get("name") for m in client().get(f"{_host()}/api/ps", timeout=5).json().get("models", [])}
    except Exception:  # noqa: BLE001
        loaded = set()
    out = []
    for m in tags:
        name = m.get("name")
        det = m.get("details") or {}
        out.append({
            "name": name, "provider": "ollama",
            "params": det.get("parameter_size"), "quant": det.get("quantization_level"),
            "size_gb": round((m.get("size") or 0) / 1e9, 2),
            "capabilities": capabilities(name), "loaded": name in loaded,
        })
    with _lock:
        _models_cache = (now, out)
    return out


def reachable() -> bool:
    try:
        return client().get(f"{_host()}/api/version", timeout=3).status_code < 400
    except Exception:  # noqa: BLE001
        return False
