from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import json
from typing import Protocol
from urllib.parse import urlsplit

import requests

from photometry_app.assistant.messages import ChatMessage, ChatResponse, ToolSpec


DEFAULT_REQUEST_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_OUTPUT_TOKENS = 4096
# "default" sends nothing, so the provider decides and models without reasoning keep working.
REASONING_EFFORT_DEFAULT = "default"
REASONING_EFFORTS = (REASONING_EFFORT_DEFAULT, "low", "medium", "high")
REASONING_EFFORT_LABELS = {"default": "Default", "low": "Low", "medium": "Medium", "high": "High"}
_LIST_MODELS_TIMEOUT_SECONDS = 20.0
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


class HttpSession(Protocol):
    def post(self, url: str, **kwargs: object) -> requests.Response: ...

    def get(self, url: str, **kwargs: object) -> requests.Response: ...


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    provider_id: str
    model: str
    api_key: str | None
    base_url: str
    timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    reasoning_effort: str = REASONING_EFFORT_DEFAULT

    @property
    def effort(self) -> str | None:
        effort = normalize_reasoning_effort(self.reasoning_effort)
        return None if effort == REASONING_EFFORT_DEFAULT else effort


def normalize_reasoning_effort(value: object) -> str:
    text = str(value or "").strip().casefold()
    return text if text in REASONING_EFFORTS else REASONING_EFFORT_DEFAULT


def is_local_base_url(base_url: str) -> bool:
    try:
        host = (urlsplit(base_url.strip()).hostname or "").casefold()
    except ValueError:
        return False
    return host in _LOCAL_HOSTS


class ChatProvider:
    provider_id = ""
    display_name = ""

    def __init__(self, config: ProviderConfig, session: HttpSession | None = None) -> None:
        self._config = config
        self._session: HttpSession = session if session is not None else requests.Session()

    @property
    def config(self) -> ProviderConfig:
        return self._config

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
    ) -> ChatResponse:
        raise NotImplementedError

    def list_models(self) -> list[str]:
        raise NotImplementedError

    def _require_model(self) -> str:
        model = self._config.model.strip()
        if not model:
            raise ProviderError(f"Choose a {self.display_name} model in Settings > AI Assistant.")
        return model

    def _require_api_key(self) -> str:
        key = (self._config.api_key or "").strip()
        if not key:
            raise ProviderError(f"Add your {self.display_name} API key in Settings > AI Assistant.")
        return key

    def _base_url(self) -> str:
        return self._config.base_url.strip().rstrip("/")

    def _post_json(self, url: str, payload: dict[str, object], headers: dict[str, str]) -> dict[str, object]:
        return self._request_json(
            "post",
            url,
            headers=headers,
            json=payload,
            timeout=self._config.timeout_seconds,
        )

    def _get_json(
        self,
        url: str,
        headers: dict[str, str],
        params: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return self._request_json(
            "get",
            url,
            headers=headers,
            params=params,
            timeout=min(self._config.timeout_seconds, _LIST_MODELS_TIMEOUT_SECONDS),
        )

    def _request_json(self, method: str, url: str, **kwargs: object) -> dict[str, object]:
        host = urlsplit(url).netloc or url
        try:
            response = getattr(self._session, method)(url, **kwargs)
        except requests.Timeout as exc:
            raise ProviderError(f"{self.display_name} did not answer in time ({host}).", retryable=True) from exc
        except requests.RequestException as exc:
            raise ProviderError(f"Could not reach {self.display_name} at {host}. Check the network or server address.", retryable=True) from exc
        payload = _response_json(response)
        if response.status_code >= 400:
            raise self._http_error(response.status_code, payload)
        if not isinstance(payload, dict):
            raise ProviderError(f"{self.display_name} returned a response CAst could not read.")
        return payload

    def _http_error(self, status_code: int, payload: object) -> ProviderError:
        detail = _error_detail(payload)
        name = self.display_name
        if status_code in (401, 403):
            message = f"{name} rejected the API key. Check it in Settings > AI Assistant."
        elif status_code == 404:
            message = f"{name} could not find that model or endpoint. Check the model name and server address."
        elif status_code == 429:
            message = f"{name} is rate-limiting requests or the account is out of quota."
        elif status_code >= 500:
            message = f"{name} had a server error ({status_code}). Try again shortly."
        else:
            message = f"{name} rejected the request ({status_code})."
        if detail:
            message = f"{message} {name} said: {detail}"
        retryable = status_code == 429 or status_code >= 500
        return ProviderError(message, status_code=status_code, retryable=retryable)


def parse_tool_arguments(raw: object) -> tuple[dict[str, object], str]:
    if isinstance(raw, dict):
        return dict(raw), ""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {}, ""
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            return {}, f"Tool arguments were not valid JSON: {exc.msg}."
        if isinstance(parsed, dict):
            return parsed, ""
    return {}, "Tool arguments must be a JSON object."


def optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _response_json(response: requests.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return None


def _error_detail(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, dict):
        detail = error.get("message") or error.get("type") or ""
    elif isinstance(error, str):
        detail = error
    else:
        detail = payload.get("message") or ""
    text = " ".join(str(detail).split())
    return text[:300]
