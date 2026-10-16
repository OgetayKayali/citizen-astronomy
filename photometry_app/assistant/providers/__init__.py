from __future__ import annotations

from dataclasses import dataclass

from photometry_app.assistant.providers.anthropic import (
    ANTHROPIC_DEFAULT_BASE_URL,
    ANTHROPIC_PROVIDER_ID,
    AnthropicProvider,
)
from photometry_app.assistant.providers.base import (
    REASONING_EFFORT_DEFAULT,
    REASONING_EFFORT_LABELS,
    REASONING_EFFORTS,
    ChatProvider,
    HttpSession,
    ProviderConfig,
    ProviderError,
    is_local_base_url,
    normalize_reasoning_effort,
)
from photometry_app.assistant.providers.gemini import GEMINI_DEFAULT_BASE_URL, GEMINI_PROVIDER_ID, GeminiProvider
from photometry_app.assistant.providers.openai_compatible import (
    OPENAI_COMPATIBLE_PROVIDER_ID,
    OPENAI_DEFAULT_BASE_URL,
    OpenAICompatibleProvider,
)


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    provider_id: str
    display_name: str
    default_base_url: str
    base_url_editable: bool
    model_placeholder: str


PROVIDER_INFOS: tuple[ProviderInfo, ...] = (
    ProviderInfo(
        provider_id=OPENAI_COMPATIBLE_PROVIDER_ID,
        display_name="OpenAI-compatible (OpenAI, Ollama, LM Studio, ...)",
        default_base_url=OPENAI_DEFAULT_BASE_URL,
        base_url_editable=True,
        model_placeholder="Model name, e.g. from Load Models",
    ),
    ProviderInfo(
        provider_id=ANTHROPIC_PROVIDER_ID,
        display_name="Anthropic (Claude)",
        default_base_url=ANTHROPIC_DEFAULT_BASE_URL,
        base_url_editable=False,
        model_placeholder="Claude model name, e.g. from Load Models",
    ),
    ProviderInfo(
        provider_id=GEMINI_PROVIDER_ID,
        display_name="Google Gemini",
        default_base_url=GEMINI_DEFAULT_BASE_URL,
        base_url_editable=False,
        model_placeholder="Gemini model name, e.g. from Load Models",
    ),
)

PROVIDER_IDS: tuple[str, ...] = tuple(info.provider_id for info in PROVIDER_INFOS)
DEFAULT_PROVIDER_ID = OPENAI_COMPATIBLE_PROVIDER_ID

_PROVIDER_CLASSES: dict[str, type[ChatProvider]] = {
    OPENAI_COMPATIBLE_PROVIDER_ID: OpenAICompatibleProvider,
    ANTHROPIC_PROVIDER_ID: AnthropicProvider,
    GEMINI_PROVIDER_ID: GeminiProvider,
}


def provider_info(provider_id: str) -> ProviderInfo:
    for info in PROVIDER_INFOS:
        if info.provider_id == provider_id:
            return info
    raise ProviderError(f"Unknown AI provider: {provider_id!r}.")


def normalize_provider_id(value: object) -> str:
    text = str(value or "").strip().casefold()
    return text if text in PROVIDER_IDS else DEFAULT_PROVIDER_ID


def provider_requires_api_key(provider_id: str, base_url: str) -> bool:
    if provider_id == OPENAI_COMPATIBLE_PROVIDER_ID:
        return not is_local_base_url(base_url or OPENAI_DEFAULT_BASE_URL)
    return True


def create_provider(config: ProviderConfig, session: HttpSession | None = None) -> ChatProvider:
    provider_class = _PROVIDER_CLASSES.get(config.provider_id)
    if provider_class is None:
        raise ProviderError(f"Unknown AI provider: {config.provider_id!r}.")
    return provider_class(config, session=session)


__all__ = [
    "DEFAULT_PROVIDER_ID",
    "PROVIDER_IDS",
    "PROVIDER_INFOS",
    "REASONING_EFFORTS",
    "REASONING_EFFORT_DEFAULT",
    "REASONING_EFFORT_LABELS",
    "ChatProvider",
    "HttpSession",
    "ProviderConfig",
    "ProviderError",
    "ProviderInfo",
    "create_provider",
    "is_local_base_url",
    "normalize_provider_id",
    "normalize_reasoning_effort",
    "provider_info",
    "provider_requires_api_key",
]
