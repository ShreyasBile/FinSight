"""FinSight agent layer (CrewAI).

Every FinSight feature is a separate agent (see registry.py). Agents are
TOOL-FIRST: the agent's deterministic tool (an existing FinSight module) runs
directly; the CrewAI Agent/LLM is only used for judgement or narration, with a
grounding gate on whatever it writes. No LLM ever sets a score or a verdict.

Importing this package never imports crewai (it is heavy, ~2 s); the
crewai_compat module is the only place that does, lazily.
"""
import os

# CrewAI/OpenTelemetry/chromadb telemetry OFF before anything can import them:
# this service handles applicants' financial PII.
for _k, _v in {
    "CREWAI_DISABLE_TELEMETRY": "true",
    "CREWAI_TRACING_ENABLED": "false",
    "OTEL_SDK_DISABLED": "true",
    "ANONYMIZED_TELEMETRY": "False",
    # crewai's console formatter otherwise calls PyPI to check for a newer
    # version — an outbound request this service must not make
    "CREWAI_DISABLE_VERSION_CHECK": "true",
}.items():
    os.environ.setdefault(_k, _v)
