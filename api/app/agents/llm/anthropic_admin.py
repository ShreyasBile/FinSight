"""Anthropic model discovery for the Settings picker. The API key is read from
api/.env on the server and never leaves it."""
from __future__ import annotations

import threading
import time

from ...config import settings

# Offered when the Models API cannot be reached. Current generation only.
CURATED = [
    {"name": "claude-opus-5", "display": "Claude Opus 5"},
    {"name": "claude-opus-5-5", "display": "Claude Opus 5.5"},
    {"name": "claude-fable-5-1", "display": "Claude Fable 5.1"},
    {"name": "claude-sonnet-5", "display": "Claude Sonnet 5"},
    {"name": "claude-haiku-4-5", "display": "Claude Haiku 4.5"},
]
DEFAULT_MODEL = "claude-opus-5"

_lock = threading.Lock()
_cache: tuple[float, list] | None = None


def configured() -> bool:
    return bool(settings.anthropic_api_key)


def client():
    import anthropic
    return anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=2)


def list_models() -> list[dict]:
    """Models this key can use (Models API), falling back to a curated list."""
    global _cache
    if not configured():
        return []
    with _lock:
        if _cache and time.time() - _cache[0] < 300:
            return _cache[1]
    out = []
    try:
        for m in client().models.list(limit=50):
            out.append({"name": m.id, "display": getattr(m, "display_name", m.id),
                        "provider": "anthropic", "capabilities": ["vision", "tools", "json"]})
    except Exception:  # noqa: BLE001 - offline / bad key -> curated list
        out = [{**c, "provider": "anthropic", "capabilities": ["vision", "tools", "json"],
                "unverified": True} for c in CURATED]
    with _lock:
        _cache = (time.time(), out)
    return out
