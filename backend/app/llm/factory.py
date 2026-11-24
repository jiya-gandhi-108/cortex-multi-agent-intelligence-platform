from __future__ import annotations

from app.core.config import LLMProvider, Settings
from app.llm.base import LLMClient, LLMConfigurationError

_cache: dict[tuple[LLMProvider, int], LLMClient] = {}


def get_llm_client(settings: Settings, provider: LLMProvider | str | None = None) -> LLMClient:
    """Return a process-wide client per provider, so rate governors are shared
    across every concurrent research run."""
    chosen = LLMProvider(provider) if provider else settings.default_llm_provider
    key = (chosen, id(settings))
    if key in _cache:
        return _cache[key]
    if not settings.provider_configured(chosen):
        raise LLMConfigurationError(
            f"{chosen.value} is not configured: set {chosen.value.upper()}_API_KEY"
        )
    client: LLMClient
    if chosen is LLMProvider.GROQ:
        from app.llm.groq_client import GroqClient

        client = GroqClient(settings)
    else:
        from app.llm.gemini_client import GeminiClient

        client = GeminiClient(settings)
    _cache[key] = client
    return client
