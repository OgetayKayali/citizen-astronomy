from __future__ import annotations

from collections.abc import Sequence
import copy
import json
from urllib.parse import urlsplit

from photometry_app.assistant.messages import ChatMessage, ChatResponse, MessageRole, StopReason, ToolCall, ToolSpec
from photometry_app.assistant.providers.base import (
    ChatProvider,
    ProviderError,
    is_local_base_url,
    optional_int,
    parse_tool_arguments,
)


OPENAI_COMPATIBLE_PROVIDER_ID = "openai_compatible"
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
OPENAI_RESPONSES_RAW_PROVIDER = "openai_responses"
_OPENAI_HOSTS = {"api.openai.com"}

_FINISH_REASONS = {
    "stop": StopReason.END,
    "tool_calls": StopReason.TOOL_CALLS,
    "function_call": StopReason.TOOL_CALLS,
    "length": StopReason.MAX_TOKENS,
    "content_filter": StopReason.BLOCKED,
}
_INCOMPLETE_REASONS = {
    "max_output_tokens": StopReason.MAX_TOKENS,
    "content_filter": StopReason.BLOCKED,
}


def uses_responses_api(base_url: str) -> bool:
    try:
        host = (urlsplit(base_url.strip()).hostname or "").casefold()
    except ValueError:
        return False
    return host in _OPENAI_HOSTS


class OpenAICompatibleProvider(ChatProvider):
    """OpenAI's Responses API for api.openai.com; Chat Completions for other compatible servers."""

    provider_id = OPENAI_COMPATIBLE_PROVIDER_ID
    display_name = "OpenAI-compatible server"

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._use_responses = uses_responses_api(self._config.base_url)
        self._request_encrypted_reasoning = True

    @property
    def uses_responses(self) -> bool:
        return self._use_responses

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
    ) -> ChatResponse:
        if self._use_responses:
            return self._complete_responses(system, messages, tools)
        try:
            return self._complete_chat(system, messages, tools)
        except ProviderError as exc:
            # Some models only take tools through the Responses API and say so in a 400.
            if exc.status_code != 400 or "/responses" not in str(exc):
                raise
        self._use_responses = True
        return self._complete_responses(system, messages, tools)

    def list_models(self) -> list[str]:
        data = self._get_json(f"{self._base_url()}/models", self._headers())
        rows = data.get("data")
        if not isinstance(rows, list):
            return []
        return sorted({str(row["id"]) for row in rows if isinstance(row, dict) and row.get("id")}, key=str.casefold)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = (self._config.api_key or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        elif not is_local_base_url(self._base_url()):
            self._require_api_key()
        return headers

    # Chat Completions

    def _complete_chat(self, system: str, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]) -> ChatResponse:
        payload: dict[str, object] = {
            "model": self._require_model(),
            "messages": self._chat_messages(system, messages),
        }
        if self._config.effort:
            payload["reasoning_effort"] = self._config.effort
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {"name": tool.name, "description": tool.description, "parameters": tool.parameters},
                }
                for tool in tools
            ]
        data = self._post_json(f"{self._base_url()}/chat/completions", payload, self._headers())
        return self._parse_chat_response(data)

    def _chat_messages(self, system: str, messages: Sequence[ChatMessage]) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        if system:
            rows.append({"role": "system", "content": system})
        for message in messages:
            if message.role == MessageRole.USER:
                rows.append({"role": "user", "content": message.text})
            elif message.role == MessageRole.ASSISTANT:
                row: dict[str, object] = {"role": "assistant", "content": message.text or None}
                if message.tool_calls:
                    row["tool_calls"] = [
                        {
                            "id": call.call_id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                        }
                        for call in message.tool_calls
                    ]
                rows.append(row)
            elif message.role == MessageRole.TOOL:
                rows.append({"role": "tool", "tool_call_id": message.tool_call_id, "content": message.text})
        return rows

    def _parse_chat_response(self, data: dict[str, object]) -> ChatResponse:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderError(f"{self.display_name} returned no answer.")
        choice = choices[0]
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        text = _message_text(message.get("content"))
        tool_calls: list[ToolCall] = []
        raw_calls = message.get("tool_calls")
        if isinstance(raw_calls, list):
            for index, raw_call in enumerate(raw_calls):
                if not isinstance(raw_call, dict):
                    continue
                function = raw_call.get("function") if isinstance(raw_call.get("function"), dict) else {}
                name = str(function.get("name") or "").strip()
                if not name:
                    continue
                arguments, arguments_error = parse_tool_arguments(function.get("arguments"))
                call_id = str(raw_call.get("id") or f"call_{index}")
                tool_calls.append(ToolCall(call_id=call_id, name=name, arguments=arguments, arguments_error=arguments_error))
        stop_reason = _FINISH_REASONS.get(str(choice.get("finish_reason") or ""), StopReason.OTHER)
        if tool_calls:
            stop_reason = StopReason.TOOL_CALLS
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return ChatResponse(
            message=ChatMessage.assistant(text, tuple(tool_calls)),
            stop_reason=stop_reason,
            input_tokens=optional_int(usage.get("prompt_tokens")),
            output_tokens=optional_int(usage.get("completion_tokens")),
        )

    # Responses API

    def _complete_responses(self, system: str, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]) -> ChatResponse:
        payload: dict[str, object] = {
            "model": self._require_model(),
            "input": self._responses_input(messages),
            # Nothing is kept on OpenAI's side for later retrieval; reasoning comes back encrypted so it can be replayed.
            "store": False,
        }
        if system:
            payload["instructions"] = system
        if self._config.effort:
            payload["reasoning"] = {"effort": self._config.effort}
        if tools:
            payload["tools"] = [
                {"type": "function", "name": tool.name, "description": tool.description, "parameters": tool.parameters}
                for tool in tools
            ]
        url = f"{self._base_url()}/responses"
        if self._request_encrypted_reasoning:
            try:
                data = self._post_json(url, {**payload, "include": ["reasoning.encrypted_content"]}, self._headers())
                return self._parse_responses_response(data)
            except ProviderError as exc:
                if exc.status_code != 400 or not any(word in str(exc).casefold() for word in ("include", "encrypted", "reasoning")):
                    raise
            self._request_encrypted_reasoning = False
        data = self._post_json(url, payload, self._headers())
        return self._parse_responses_response(data)

    def _responses_input(self, messages: Sequence[ChatMessage]) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for message in messages:
            if message.role == MessageRole.USER:
                items.append({"role": "user", "content": message.text})
            elif message.role == MessageRole.ASSISTANT:
                if message.raw_provider == OPENAI_RESPONSES_RAW_PROVIDER and isinstance(message.raw_content, list):
                    items.extend(copy.deepcopy(message.raw_content))
                    continue
                if message.text:
                    items.append({"role": "assistant", "content": message.text})
                for call in message.tool_calls:
                    items.append(
                        {"type": "function_call", "call_id": call.call_id, "name": call.name, "arguments": json.dumps(call.arguments)}
                    )
            elif message.role == MessageRole.TOOL:
                items.append({"type": "function_call_output", "call_id": message.tool_call_id, "output": message.text})
        return items

    def _parse_responses_response(self, data: dict[str, object]) -> ChatResponse:
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            raise ProviderError(f"{self.display_name} could not answer: {error['message']}")
        output = data.get("output")
        if not isinstance(output, list):
            raise ProviderError(f"{self.display_name} returned no answer.")
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        replay: list[dict[str, object]] = []
        for index, item in enumerate(output):
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind == "message":
                for part in item.get("content") or ():
                    if isinstance(part, dict) and part.get("type") == "output_text":
                        text_parts.append(str(part.get("text") or ""))
                replay.append(item)
            elif kind == "function_call":
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                arguments, arguments_error = parse_tool_arguments(item.get("arguments"))
                call_id = str(item.get("call_id") or f"call_{index}")
                tool_calls.append(ToolCall(call_id=call_id, name=name, arguments=arguments, arguments_error=arguments_error))
                replay.append(item)
            elif kind == "reasoning":
                replay.append(item)
        if any(item.get("type") == "reasoning" and not item.get("encrypted_content") for item in replay):
            # Unencrypted reasoning cannot be sent back when store is off, and item ids would then point at nothing.
            replay = [{key: value for key, value in item.items() if key != "id"} for item in replay if item.get("type") != "reasoning"]
        stop_reason = StopReason.END
        if tool_calls:
            stop_reason = StopReason.TOOL_CALLS
        elif data.get("status") == "incomplete":
            details = data.get("incomplete_details") if isinstance(data.get("incomplete_details"), dict) else {}
            stop_reason = _INCOMPLETE_REASONS.get(str(details.get("reason") or ""), StopReason.OTHER)
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return ChatResponse(
            message=ChatMessage(
                role=MessageRole.ASSISTANT,
                text="".join(text_parts),
                tool_calls=tuple(tool_calls),
                raw_provider=OPENAI_RESPONSES_RAW_PROVIDER,
                raw_content=copy.deepcopy(replay),
            ),
            stop_reason=stop_reason,
            input_tokens=optional_int(usage.get("input_tokens")),
            output_tokens=optional_int(usage.get("output_tokens")),
        )


def _message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [str(part.get("text") or "") for part in content if isinstance(part, dict) and part.get("type") == "text"]
        return "".join(parts)
    return ""
