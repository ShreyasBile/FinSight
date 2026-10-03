"""The only module that imports crewai.

GatewayLLM makes every CrewAI Agent's LLM call go through FinSight's own
gateway (app/agents/llm/gateway.py), so CrewAI agents inherit — for free — the
Settings-page model choice, the privacy guard for hosted providers, the LLM
priority queue, per-step tracing, reasoning-off for thinking models, and the
offline mock provider used in tests. CrewAI supplies the agent (role, goal,
backstory), the task framing and structured output; FinSight supplies the
transport and the guardrails.

Agents are TOOL-FIRST: CrewAI is used for one structured narration/judgement
call per step, never for LLM-driven tool selection on a hot path (small local
models are unreliable at tool calling, and every extra round trip is seconds).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel

log = logging.getLogger("finsight.agents.crewai")

_crewai = None


def crewai():
    """Lazy import (crewai takes ~2 s to import)."""
    global _crewai
    if _crewai is None:
        from .. import agents  # noqa: F401 - ensures telemetry env defaults are set first
        import crewai as _c
        _silence_console()
        _crewai = _c
    return _crewai


def _silence_console():
    """CrewAI renders Rich panels to stdout for every agent/flow event (flow
    panels even with verbose=False). In an API server that is noise, costs
    time, and crashes on Windows' cp1252 console with emoji titles. FinSight
    records its own traces, so CrewAI's console output is switched off."""
    try:
        from crewai.events.utils.console_formatter import ConsoleFormatter
        ConsoleFormatter.print = lambda self, *a, **k: None  # type: ignore[method-assign]
        ConsoleFormatter.print_panel = lambda self, *a, **k: None  # type: ignore[method-assign]
    except Exception:  # noqa: BLE001 - cosmetic; never block agent startup
        log.debug("could not silence crewai console output")


def _messages_to_prompt(messages) -> tuple[str | None, str]:
    if isinstance(messages, str):
        return None, messages
    system_parts, user_parts = [], []
    for m in messages or []:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "user")
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
        if isinstance(content, list):
            content = "\n".join(str(c.get("text", "")) if isinstance(c, dict) else str(c) for c in content)
        (system_parts if role == "system" else user_parts).append(
            content if role != "assistant" else f"(previous answer)\n{content}")
    return ("\n\n".join(system_parts) or None), "\n\n".join(user_parts)


def _gateway_llm_class():
    from crewai.llms.base_llm import BaseLLM

    class GatewayLLM(BaseLLM):
        """CrewAI LLM backed by the FinSight gateway for one agent id."""
        agent_id: str = "generic"
        timeout_s: float = 120.0
        last_result: Any = None

        def call(self, messages, tools=None, callbacks=None, available_functions=None,
                 from_task=None, from_agent=None, response_model=None, **kwargs):
            from .context import current_step, steps_by_agent
            from .llm import gateway
            # inside a Crew, attribute this call to THIS agent's own step
            mapped = (steps_by_agent.get() or {}).get(self.agent_id)
            token = current_step.set(mapped) if mapped is not None else None
            try:
                return self._call(messages, response_model)
            finally:
                if token is not None:
                    current_step.reset(token)

        def _call(self, messages, response_model):
            from .llm import gateway
            system, user = _messages_to_prompt(messages)
            schema = None
            if response_model is not None and isinstance(response_model, type) \
                    and issubclass(response_model, BaseModel):
                schema = response_model.model_json_schema()
                user = (user + "\n\nReturn ONLY a JSON object matching this schema:\n"
                        + json.dumps(schema))
            # the schema is also sent as Ollama's `format`, so decoding is
            # constrained to it: with plain JSON mode qwen2.5vl echoed the schema
            # itself back ({"properties": …, "type": "object"}) on every
            # Regulatory Q&A synthesis in the evaluation
            res = gateway.chat(self.agent_id, system=system, user=user, json_mode=schema is not None,
                               schema=schema, timeout=self.timeout_s)
            self.last_result = res
            if schema is not None:
                data = res.parsed if isinstance(res.parsed, dict) else None
                if data is None:
                    raise RuntimeError("model did not return valid JSON")
                return response_model.model_validate(data)
            return res.text

        def supports_function_calling(self) -> bool:
            return False

        def supports_stop_words(self) -> bool:
            return False

        def get_context_window_size(self) -> int:
            return 8192

    return GatewayLLM


_GW = None


def gateway_llm(agent_id: str, timeout_s: float = 120.0):
    global _GW
    crewai()
    if _GW is None:
        _GW = _gateway_llm_class()
    from .llm.gateway import resolve
    r = resolve(agent_id)
    return _GW(model=f"{r.provider}:{r.model}", agent_id=agent_id, timeout_s=timeout_s,
               temperature=r.temperature)


def build_agent(spec, backstory: str, agent_id: str | None = None, timeout_s: float = 120.0):
    """A CrewAI Agent for one FinSight agent spec, on the gateway LLM."""
    c = crewai()
    return c.Agent(role=spec.role, goal=spec.goal, backstory=backstory,
                   llm=gateway_llm(agent_id or spec.id, timeout_s), verbose=False,
                   allow_delegation=False, max_iter=2)


def kickoff_structured(spec, backstory: str, prompt: str, response_model: type[BaseModel],
                       agent_id: str | None = None, timeout_s: float = 120.0):
    """One structured CrewAI call → a validated `response_model` instance."""
    agent = build_agent(spec, backstory, agent_id, timeout_s)
    out = agent.kickoff(prompt, response_format=response_model)
    parsed = getattr(out, "pydantic", None)
    if isinstance(parsed, response_model):
        return parsed
    raw = getattr(out, "raw", None) or str(out)
    from ..utils import parse_json_blob
    data = parse_json_blob(raw)
    if data is None:
        raise RuntimeError("CrewAI agent returned no structured output")
    return response_model.model_validate(data)


def flow_classes():
    """(Flow, start, listen, router, and_, or_) — for app/agents/flows."""
    crewai()
    from crewai.flow.flow import Flow, and_, listen, or_, router, start
    return Flow, start, listen, router, and_, or_


def crew_classes():
    c = crewai()
    return c.Crew, c.Task, c.Process
