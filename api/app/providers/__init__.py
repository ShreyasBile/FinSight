from .anthropic import AnthropicProvider
from .base import OCRProvider
from .mock import MockProvider
from .ollama import OllamaProvider

_PROVIDERS = {
    "mock": MockProvider,
    "ollama": OllamaProvider,
    "anthropic": AnthropicProvider,
}


def get_provider() -> OCRProvider:
    """The extraction provider currently selected for the Extraction agent.

    Resolved per call (not cached) so a change on the Settings page takes
    effect on the very next upload — no restart. With no Settings saved yet,
    this is OCR_PROVIDER from api/.env, exactly as before."""
    from ..agents.llm.gateway import resolve
    name = resolve("extraction").provider
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise ValueError(f"Unknown extraction provider '{name}'. Use one of: {', '.join(_PROVIDERS)}")
    return cls()
