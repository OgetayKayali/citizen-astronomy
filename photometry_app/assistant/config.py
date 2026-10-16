from __future__ import annotations

from collections.abc import Mapping

from photometry_app.assistant.credentials import load_api_key
from photometry_app.assistant.providers import ProviderConfig, normalize_provider_id, normalize_reasoning_effort, provider_info


def resolve_base_url(provider_id: str, custom_base_url: str = "") -> str:
    info = provider_info(normalize_provider_id(provider_id))
    custom = custom_base_url.strip()
    if info.base_url_editable and custom:
        return custom.rstrip("/")
    return info.default_base_url


def model_for_provider(models: Mapping[str, str] | None, provider_id: str) -> str:
    if not models:
        return ""
    return str(models.get(provider_id, "") or "").strip()


def provider_config_from_settings(settings: object, *, api_key: str | None = None) -> ProviderConfig:
    provider_id = normalize_provider_id(getattr(settings, "ai_provider", ""))
    key = api_key if api_key is not None else load_api_key(provider_id)
    return ProviderConfig(
        provider_id=provider_id,
        model=model_for_provider(getattr(settings, "ai_models", None), provider_id),
        api_key=key,
        base_url=resolve_base_url(provider_id, str(getattr(settings, "ai_openai_compatible_base_url", "") or "")),
        reasoning_effort=normalize_reasoning_effort(getattr(settings, "ai_reasoning_effort", "")),
    )
