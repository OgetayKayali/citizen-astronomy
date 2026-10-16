from __future__ import annotations

from collections.abc import Sequence
import copy

from photometry_app.assistant.messages import ChatMessage, ChatResponse, MessageRole, StopReason, ToolCall, ToolSpec
from photometry_app.assistant.providers.base import ChatProvider, ProviderError, optional_int


ANTHROPIC_PROVIDER_ID = "anthropic"
ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_API_VERSION = "2023-06-01"

_STOP_REASONS = {
    "end_turn": StopReason.END,
    "stop_sequence": StopReason.END,
    "tool_use": StopReason.TOOL_CALLS,
    "max_tokens": StopReason.MAX_TOKENS,
    "refusal": StopReason.BLOCKED,
}
_THINKING_BUDGETS = {"low": 2048, "medium": 8192, "high": 16384}


class AnthropicProvider(ChatProvider):
    provider_id = ANTHROPIC_PROVIDER_ID
    display_name = "Anthropic"

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
    ) -> ChatResponse:
        payload: dict[str, object] = {
            "model": self._require_model(),
            "max_tokens": int(self._config.max_output_tokens),
            "messages": self._request_messages(messages),
        }
        budget = _THINKING_BUDGETS.get(self._config.effort or "")
        if budget is not None:
            # Thinking tokens count toward max_tokens, which must stay above the budget.
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
            payload["max_tokens"] = budget + int(self._config.max_output_tokens)
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {"name": tool.name, "description": tool.description, "input_schema": tool.parameters}
                for tool in tools
            ]
        data = self._post_json(f"{self._base_url()}/v1/messages", payload, self._headers())
        return self._parse_response(data)

    def list_models(self) -> list[str]:
        data = self._get_json(f"{self._base_url()}/v1/models", self._headers(), params={"limit": 1000})
        rows = data.get("data")
        if not isinstance(rows, list):
            return []
        return [str(row["id"]) for row in rows if isinstance(row, dict) and row.get("id")]

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._require_api_key(),
            "anthropic-version": ANTHROPIC_API_VERSION,
            "content-type": "application/json",
        }

    def _request_messages(self, messages: Sequence[ChatMessage]) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for message in messages:
            if message.role == MessageRole.ASSISTANT:
                role = "assistant"
                blocks = self._assistant_blocks(message)
            elif message.role == MessageRole.TOOL:
                role = "user"
                blocks = [
                    {
                        "type": "tool_result",
                        "tool_use_id": message.tool_call_id,
                        "content": message.text,
                        "is_error": bool(message.is_error),
                    }
                ]
            else:
                role = "user"
                blocks = [{"type": "text", "text": message.text}] if message.text else []
            if not blocks:
                continue
            if rows and rows[-1]["role"] == role:
                rows[-1]["content"].extend(blocks)
            else:
                rows.append({"role": role, "content": list(blocks)})
        return rows

    def _assistant_blocks(self, message: ChatMessage) -> list[dict[str, object]]:
        if message.raw_provider == self.provider_id and isinstance(message.raw_content, list):
            return copy.deepcopy(message.raw_content)
        blocks: list[dict[str, object]] = []
        if message.text:
            blocks.append({"type": "text", "text": message.text})
        for call in message.tool_calls:
            blocks.append({"type": "tool_use", "id": call.call_id, "name": call.name, "input": call.arguments})
        return blocks

    def _parse_response(self, data: dict[str, object]) -> ChatResponse:
        content = data.get("content")
        if not isinstance(content, list):
            raise ProviderError(f"{self.display_name} returned no answer.")
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for index, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                text_parts.append(str(block.get("text") or ""))
            elif kind == "tool_use":
                name = str(block.get("name") or "").strip()
                if not name:
                    continue
                raw_input = block.get("input")
                arguments = dict(raw_input) if isinstance(raw_input, dict) else {}
                arguments_error = "" if isinstance(raw_input, dict) else "Tool arguments must be a JSON object."
                call_id = str(block.get("id") or f"toolu_{index}")
                tool_calls.append(ToolCall(call_id=call_id, name=name, arguments=arguments, arguments_error=arguments_error))
        stop_reason = _STOP_REASONS.get(str(data.get("stop_reason") or ""), StopReason.OTHER)
        if tool_calls:
            stop_reason = StopReason.TOOL_CALLS
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        message = ChatMessage(
            role=MessageRole.ASSISTANT,
            text="".join(text_parts),
            tool_calls=tuple(tool_calls),
            raw_provider=self.provider_id,
            raw_content=copy.deepcopy(content),
        )
        return ChatResponse(
            message=message,
            stop_reason=stop_reason,
            input_tokens=optional_int(usage.get("input_tokens")),
            output_tokens=optional_int(usage.get("output_tokens")),
        )
