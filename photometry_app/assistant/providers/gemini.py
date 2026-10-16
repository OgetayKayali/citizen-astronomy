from __future__ import annotations

from collections.abc import Sequence
import copy
import json

from photometry_app.assistant.messages import ChatMessage, ChatResponse, MessageRole, StopReason, ToolCall, ToolSpec
from photometry_app.assistant.providers.base import ChatProvider, ProviderError, optional_int


GEMINI_PROVIDER_ID = "gemini"
GEMINI_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
_GEMINI_API_VERSION = "v1beta"

# Gemini function parameters accept an OpenAPI subset; other JSON Schema keywords are rejected.
_GEMINI_SCHEMA_KEYS = {
    "type",
    "format",
    "title",
    "description",
    "nullable",
    "enum",
    "properties",
    "required",
    "items",
    "minItems",
    "maxItems",
    "minimum",
    "maximum",
    "anyOf",
    "propertyOrdering",
}

_FINISH_REASONS = {
    "STOP": StopReason.END,
    "MAX_TOKENS": StopReason.MAX_TOKENS,
    "SAFETY": StopReason.BLOCKED,
    "RECITATION": StopReason.BLOCKED,
    "BLOCKLIST": StopReason.BLOCKED,
    "PROHIBITED_CONTENT": StopReason.BLOCKED,
    "SPII": StopReason.BLOCKED,
}
_THINKING_BUDGETS = {"low": 1024, "medium": 8192, "high": 24576}


class GeminiProvider(ChatProvider):
    provider_id = GEMINI_PROVIDER_ID
    display_name = "Google Gemini"

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
    ) -> ChatResponse:
        model_name = self._require_model()
        model = _model_resource_name(model_name)
        generation_config: dict[str, object] = {"maxOutputTokens": int(self._config.max_output_tokens)}
        effort = self._config.effort
        if effort:
            # Gemini 2.5 takes a thinking token budget; later models take a named level.
            budget = _THINKING_BUDGETS[effort]
            if "gemini-2.5" in model_name.casefold():
                generation_config["thinkingConfig"] = {"thinkingBudget": budget}
            else:
                generation_config["thinkingConfig"] = {"thinkingLevel": effort}
            generation_config["maxOutputTokens"] = budget + int(self._config.max_output_tokens)
        payload: dict[str, object] = {
            "contents": self._request_contents(messages),
            "generationConfig": generation_config,
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": gemini_schema(tool.parameters),
                        }
                        for tool in tools
                    ]
                }
            ]
        url = f"{self._base_url()}/{_GEMINI_API_VERSION}/{model}:generateContent"
        data = self._post_json(url, payload, self._headers())
        return self._parse_response(data)

    def list_models(self) -> list[str]:
        data = self._get_json(
            f"{self._base_url()}/{_GEMINI_API_VERSION}/models",
            self._headers(),
            params={"pageSize": 1000},
        )
        rows = data.get("models")
        if not isinstance(rows, list):
            return []
        names: list[str] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("name"):
                continue
            methods = row.get("supportedGenerationMethods")
            if isinstance(methods, list) and "generateContent" not in methods:
                continue
            names.append(str(row["name"]).removeprefix("models/"))
        return names

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._require_api_key(), "Content-Type": "application/json"}

    def _request_contents(self, messages: Sequence[ChatMessage]) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for message in messages:
            if message.role == MessageRole.ASSISTANT:
                role = "model"
                parts = self._model_parts(message)
            elif message.role == MessageRole.TOOL:
                role = "user"
                parts = [
                    {
                        "functionResponse": {
                            "name": message.tool_name,
                            "response": _function_response_payload(message.text, message.is_error),
                        }
                    }
                ]
            else:
                role = "user"
                parts = [{"text": message.text}] if message.text else []
            if not parts:
                continue
            if rows and rows[-1]["role"] == role:
                rows[-1]["parts"].extend(parts)
            else:
                rows.append({"role": role, "parts": list(parts)})
        return rows

    def _model_parts(self, message: ChatMessage) -> list[dict[str, object]]:
        if message.raw_provider == self.provider_id and isinstance(message.raw_content, list):
            return copy.deepcopy(message.raw_content)
        parts: list[dict[str, object]] = []
        if message.text:
            parts.append({"text": message.text})
        for call in message.tool_calls:
            parts.append({"functionCall": {"name": call.name, "args": call.arguments}})
        return parts

    def _parse_response(self, data: dict[str, object]) -> ChatResponse:
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            feedback = data.get("promptFeedback") if isinstance(data.get("promptFeedback"), dict) else {}
            reason = str(feedback.get("blockReason") or "").strip()
            if reason:
                raise ProviderError(f"{self.display_name} blocked the request ({reason}).")
            raise ProviderError(f"{self.display_name} returned no answer.")
        candidate = candidates[0] if isinstance(candidates[0], dict) else {}
        content = candidate.get("content") if isinstance(candidate.get("content"), dict) else {}
        parts = content.get("parts") if isinstance(content.get("parts"), list) else []
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for index, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            if isinstance(part.get("functionCall"), dict):
                function_call = part["functionCall"]
                name = str(function_call.get("name") or "").strip()
                if not name:
                    continue
                raw_args = function_call.get("args")
                arguments = dict(raw_args) if isinstance(raw_args, dict) else {}
                arguments_error = "" if raw_args is None or isinstance(raw_args, dict) else "Tool arguments must be a JSON object."
                call_id = str(function_call.get("id") or f"gemini_call_{index}")
                tool_calls.append(ToolCall(call_id=call_id, name=name, arguments=arguments, arguments_error=arguments_error))
            elif "text" in part and not part.get("thought"):
                text_parts.append(str(part.get("text") or ""))
        stop_reason = _FINISH_REASONS.get(str(candidate.get("finishReason") or ""), StopReason.OTHER)
        if tool_calls:
            stop_reason = StopReason.TOOL_CALLS
        usage = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), dict) else {}
        message = ChatMessage(
            role=MessageRole.ASSISTANT,
            text="".join(text_parts),
            tool_calls=tuple(tool_calls),
            raw_provider=self.provider_id,
            raw_content=copy.deepcopy(parts),
        )
        return ChatResponse(
            message=message,
            stop_reason=stop_reason,
            input_tokens=optional_int(usage.get("promptTokenCount")),
            output_tokens=optional_int(usage.get("candidatesTokenCount")),
        )


def gemini_schema(schema: object) -> object:
    if isinstance(schema, list):
        return [gemini_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    cleaned: dict[str, object] = {}
    for key, value in schema.items():
        if key not in _GEMINI_SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            cleaned[key] = {name: gemini_schema(child) for name, child in value.items()}
        elif key in {"items", "anyOf"}:
            cleaned[key] = gemini_schema(value)
        else:
            cleaned[key] = value
    return cleaned


def _model_resource_name(model: str) -> str:
    name = model.strip()
    return name if name.startswith("models/") else f"models/{name}"


def _function_response_payload(text: str, is_error: bool) -> dict[str, object]:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    payload: dict[str, object] = parsed if isinstance(parsed, dict) else {"content": text}
    if is_error:
        return {"error": payload}
    return payload
