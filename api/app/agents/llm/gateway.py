"""The single door every FinSight LLM call goes through.

Before the agent layer, five modules each built their own httpx call and all
of them were hard-wired to Ollama, so choosing Anthropic only changed field
extraction. Now extraction, authenticity, the tamper explainer, rule authoring
and every agent narration call `chat()` with their agent id, and the gateway:

  1. resolves provider + model for that agent from the Settings snapshot;
  2. applies the privacy guard (hosted provider => Head Manager acknowledgment);
  3. queues on the LLMGate (priority + per-provider concurrency);
  4. calls Ollama (/api/chat), Anthropic (official SDK) or the mock provider;
  5. records the call (latency, queue wait, tokens) on the active agent step.

Payloads are unchanged from the original modules for Ollama (format=json,
temperature 0, num_ctx), so switching nothing in Settings changes nothing.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import asdict, dataclass

import httpx

from ...config import settings
from ...utils import parse_json_blob, render_pdf_to_images
from ..context import current_snapshot, note_llm_call
from ..governor import GATE, QueueTimeout
from . import anthropic_admin, ollama_admin, privacy
from .catalog import LLM_AGENTS
from .settings_store import ResolvedLLM, Snapshot, snapshot as take_snapshot

log = logging.getLogger("finsight.llm")


class LLMError(RuntimeError):
    """Base class — callers catch this (or Exception) and degrade."""


class LLMUnavailable(LLMError):
    """Provider unreachable, timed out, rate-limited, or queue wait exceeded."""


class LLMRejected(LLMError):
    """Provider refused the request (bad request, auth, refusal, no vision...)."""


@dataclass
class GatewayResult:
    text: str
    parsed: dict | list | None
    provider: str
    model: str
    latency_ms: float
    queue_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("text", None)
        d.pop("parsed", None)
        return d


# agent_id -> fn(kwargs) -> str ; used when the resolved provider is "mock"
MOCK_RESPONDERS: dict = {}

STATS: dict[str, dict] = {}


def _stat(agent_id: str, ok: bool, ms: float):
    s = STATS.setdefault(agent_id, {"calls": 0, "errors": 0, "total_ms": 0.0})
    s["calls"] += 1
    s["errors"] += 0 if ok else 1
    s["total_ms"] += ms


def active_snapshot() -> Snapshot:
    snap = current_snapshot.get()
    return snap if snap is not None else take_snapshot()


def resolve(agent_id: str) -> ResolvedLLM:
    return active_snapshot().resolve(agent_id)


def supports_vision(res: ResolvedLLM) -> bool:
    if res.provider in ("anthropic", "mock"):
        return True
    return "vision" in ollama_admin.capabilities(res.model)


def _mime_of(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:5] == b"%PDF-":
        return "application/pdf"
    return "image/jpeg"


# ── public API ──────────────────────────────────────────────────────────────
def chat(agent_id: str, *, user: str, system: str | None = None,
         images: list[bytes] | None = None, documents: list[tuple[bytes, str]] | None = None,
         json_mode: bool = True, schema: dict | None = None, timeout: float | None = None,
         max_tokens: int = 4096, mock_hint: str | None = None,
         resolved: ResolvedLLM | None = None) -> GatewayResult:
    """One LLM call for `agent_id`. Raises LLMError subclasses or PrivacyBlocked."""
    snap = active_snapshot()
    res = resolved or snap.resolve(agent_id)
    privacy.check(agent_id, res.provider, snap.hosted_acknowledged)
    use = LLM_AGENTS.get(agent_id)
    priority = use.priority if use else 3
    max_wait = use.max_wait_s if use else 120.0
    timeout = timeout or res.timeout_s

    t_start = time.perf_counter()
    record = {"agent": agent_id, "provider": res.provider, "model": res.model,
              "purpose": (use.purpose if use else ""), "ok": False}
    try:
        with GATE.slot(res.provider, priority, max_wait) as queue_ms:
            t0 = time.perf_counter()
            if res.provider == "ollama":
                text, tin, tout = _ollama(res, user, system, images, documents, json_mode, schema, timeout)
            elif res.provider == "anthropic":
                text, tin, tout = _anthropic(res, user, system, images, documents, json_mode, schema,
                                             timeout, max_tokens)
            elif res.provider == "mock":
                text, tin, tout = _mock(agent_id, user=user, system=system, images=images,
                                        documents=documents, mock_hint=mock_hint), None, None
            else:
                raise LLMRejected(f"unknown provider '{res.provider}'")
            latency = (time.perf_counter() - t0) * 1000
    except QueueTimeout as exc:
        record.update(error=f"queue: {exc}", latency_ms=round((time.perf_counter() - t_start) * 1000, 1))
        note_llm_call(record)
        _stat(agent_id, False, record["latency_ms"])
        raise LLMUnavailable(str(exc)) from exc
    except (LLMError, privacy.PrivacyBlocked) as exc:
        record.update(error=str(exc)[:300], latency_ms=round((time.perf_counter() - t_start) * 1000, 1))
        note_llm_call(record)
        _stat(agent_id, False, record["latency_ms"])
        raise

    parsed = None
    if json_mode:
        parsed = _parse_json(text)
    result = GatewayResult(text=text, parsed=parsed, provider=res.provider, model=res.model,
                           latency_ms=round(latency, 1), queue_ms=round(queue_ms, 1),
                           input_tokens=tin, output_tokens=tout)
    record.update(ok=True, latency_ms=result.latency_ms, queue_wait_ms=result.queue_ms,
                  prompt_tokens=tin, completion_tokens=tout, parsed=parsed is not None or not json_mode)
    note_llm_call(record)
    _stat(agent_id, True, result.latency_ms)
    return result


async def achat(agent_id: str, **kw) -> GatewayResult:
    """Async wrapper: runs the blocking call in a worker thread (the context,
    and so the active agent step, is copied into it)."""
    return await asyncio.to_thread(chat, agent_id, **kw)


def _parse_json(text: str):
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    return parse_json_blob(text)


# ── Ollama ──────────────────────────────────────────────────────────────────
_VISION_PATCH = 28          # Qwen2.5-VL / Qwen3-VL: one visual token per 28×28 px
_TEXT_RESERVE = 1800        # prompt + JSON answer


def fit_images(pics: list[bytes], num_ctx: int) -> tuple[list[bytes], dict | None]:
    """Scale images down (all by the same factor) so their visual tokens fit
    the context window. Sent at full resolution, a 3-page statement PDF
    rendered to ~10,400 prompt tokens against num_ctx 8,192 and Ollama
    rejected the request — extraction failed outright (measured on a real
    statement). Returns (images, note-or-None)."""
    import io
    import math
    from PIL import Image
    if not pics:
        return pics, None
    ims = []
    for p in pics:
        try:
            ims.append(Image.open(io.BytesIO(p)))
        except Exception:  # noqa: BLE001 - not an image we can measure; leave as is
            return pics, None
    tokens = sum(math.ceil(w / _VISION_PATCH) * math.ceil(h / _VISION_PATCH) for w, h in (i.size for i in ims))
    budget = max(1024, num_ctx - _TEXT_RESERVE)
    if tokens <= budget:
        return pics, None
    scale = math.sqrt(budget / tokens) * 0.95
    out = []
    for im in ims:
        w, h = im.size
        small = im.convert("RGB").resize((max(_VISION_PATCH, int(w * scale)), max(_VISION_PATCH, int(h * scale))),
                                         Image.LANCZOS)
        buf = io.BytesIO()
        small.save(buf, "JPEG", quality=90)
        out.append(buf.getvalue())
    return out, {"visual_tokens_before": tokens, "budget": budget, "scale": round(scale, 3)}


def _ollama(res: ResolvedLLM, user, system, images, documents, json_mode, schema, timeout):
    pics: list[bytes] = list(images or [])
    for data, mime in documents or []:
        if (mime or _mime_of(data)) == "application/pdf":
            pics.extend(render_pdf_to_images(data, max_pages=settings.pdf_max_pages))
        else:
            pics.append(data)
    pics, fitted = fit_images(pics, res.num_ctx)
    if fitted:
        log.info("images scaled to fit num_ctx %s: %s", res.num_ctx, fitted)
    msg = {"role": "user", "content": user}
    if pics:
        msg["images"] = [base64.b64encode(p).decode("ascii") for p in pics]
    messages = ([{"role": "system", "content": system}] if system else []) + [msg]
    payload = {
        "model": res.model, "stream": False, "messages": messages,
        "options": {"temperature": res.temperature, "num_ctx": res.num_ctx},
        "keep_alive": res.keep_alive,
    }
    if json_mode:
        payload["format"] = schema if schema else "json"
    if "thinking" in ollama_admin.capabilities(res.model):
        payload["think"] = False  # reasoning traces add latency and are never shown
    try:
        resp = ollama_admin.client().post(f"{ollama_admin.host()}/api/chat", json=payload, timeout=timeout)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
        raise LLMUnavailable(f"Ollama unreachable/timed out: {type(exc).__name__}") from exc
    if resp.status_code >= 400:
        # Ollama's error body ("image too large", "model not found") is far more
        # useful than a bare status — surface it.
        raise LLMRejected(f"Ollama rejected the request: {resp.text[:300]}")
    data = resp.json()
    text = (data.get("message") or {}).get("content", "") or ""
    return text, data.get("prompt_eval_count"), data.get("eval_count")


# ── Anthropic (official SDK) ────────────────────────────────────────────────
_FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5-1")


def _anthropic(res: ResolvedLLM, user, system, images, documents, json_mode, schema, timeout, max_tokens):
    if not anthropic_admin.configured():
        raise LLMRejected("ANTHROPIC_API_KEY is not set on the server")
    import anthropic

    content = []
    for data, mime in documents or []:
        mime = mime or _mime_of(data)
        b64 = base64.b64encode(data).decode("ascii")
        if mime == "application/pdf":
            content.append({"type": "document",
                            "source": {"type": "base64", "media_type": "application/pdf", "data": b64}})
        else:
            content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}})
    for data in images or []:
        content.append({"type": "image", "source": {"type": "base64", "media_type": _mime_of(data),
                                                    "data": base64.b64encode(data).decode("ascii")}})
    prompt = user
    output_config: dict = {"effort": res.effort} if res.effort else {}
    # Anthropic structured outputs need a closed schema (additionalProperties
    # false); a plain Pydantic schema falls back to JSON mode + the prompt
    if json_mode and schema and schema.get("additionalProperties") is False:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    elif json_mode:
        prompt = user + "\n\nRespond with only the JSON object, no other text."
    content.append({"type": "text", "text": prompt})

    kwargs = {"model": res.model, "max_tokens": max(max_tokens, 16000),
              "messages": [{"role": "user", "content": content}]}
    if system:
        kwargs["system"] = system
    extra_body = {"output_config": output_config} if output_config else {}

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=timeout, max_retries=2)
    try:
        if res.model.startswith(_FALLBACK_MODELS):
            # server-side refusal fallback: a policy decline is re-run on a
            # fallback model inside the same call instead of failing the step
            try:
                msg = client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"],
                    extra_body={**extra_body, "fallbacks": "default"}, **kwargs)
            except anthropic.BadRequestError as exc:
                if "fallback" not in str(exc).lower():
                    raise
                msg = client.messages.create(extra_body=extra_body, **kwargs)
        else:
            msg = client.messages.create(extra_body=extra_body, **kwargs)
    except (anthropic.APIConnectionError, anthropic.APITimeoutError, anthropic.RateLimitError,
            anthropic.InternalServerError) as exc:
        raise LLMUnavailable(f"Anthropic unavailable: {type(exc).__name__}") from exc
    except anthropic.APIStatusError as exc:
        raise LLMRejected(f"Anthropic API error {exc.status_code}: {getattr(exc, 'message', exc)}") from exc

    if getattr(msg, "stop_reason", None) == "refusal":
        raise LLMRejected("the model declined this request")
    text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
    usage = getattr(msg, "usage", None)
    return text, getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)


# ── Mock (dev/tests) ────────────────────────────────────────────────────────
def _mock(agent_id: str, **kw) -> str:
    fn = MOCK_RESPONDERS.get(agent_id)
    if fn is not None:
        out = fn(kw)
        return out if isinstance(out, str) else json.dumps(out)
    if agent_id == "extraction":
        from ...providers.mock import _CANNED, _DEFAULT
        return json.dumps(_CANNED.get(kw.get("mock_hint") or "", dict(_DEFAULT)), ensure_ascii=False)
    if agent_id == "authenticity":
        return json.dumps({"verdict": "unclear", "reasons": [], "genuine_markers": []})
    return "{}"


def test_call(provider: str, model: str, vision: bool = False) -> dict:
    """Settings 'Test connection': a tiny SYNTHETIC request — never a document."""
    res = ResolvedLLM(provider=provider, model=model, temperature=0.0, num_ctx=2048,
                      timeout_s=60.0, keep_alive="10m", effort="low", source="test", revision=-1)
    images = None
    if vision:
        from PIL import Image, ImageDraw
        import io
        im = Image.new("RGB", (160, 60), "white")
        ImageDraw.Draw(im).text((10, 20), "TEST 42", fill="black")
        buf = io.BytesIO(); im.save(buf, "PNG"); images = [buf.getvalue()]
    t0 = time.perf_counter()
    try:
        prompt = ('Read the text in the image and reply as JSON {"text": "..."}.' if vision
                  else 'Reply as JSON: {"status": "ok"}')
        if provider == "ollama":
            text, _, _ = _ollama(res, prompt, None, images, None, True, None, 60)
        elif provider == "anthropic":
            if settings.finsight_forbid_hosted:
                raise privacy.PrivacyBlocked("hosted calls are forbidden in this environment")
            text, _, _ = _anthropic(res, prompt, None, images, None, True, None, 60, 64)
        else:
            text = '{"status": "ok"}'
        parsed = _parse_json(text)
        return {"ok": parsed is not None, "latency_ms": round((time.perf_counter() - t0) * 1000),
                "reply": (text or "")[:120], "capability_ok": (not vision) or bool(parsed)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "latency_ms": round((time.perf_counter() - t0) * 1000),
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
