from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class StopReason(str, Enum):
    END = "end"
    TOOL_CALLS = "tool_calls"
    MAX_TOKENS = "max_tokens"
    BLOCKED = "blocked"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, object] = field(default_factory=lambda: {"type": "object", "properties": {}})


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, object]
    arguments_error: str = ""


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: MessageRole
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str = ""
    tool_name: str = ""
    is_error: bool = False
    # Provider-native assistant content, replayed verbatim to the same provider so opaque
    # fields such as Gemini thought signatures survive multi-step tool use.
    raw_provider: str = ""
    raw_content: object = None

    @classmethod
    def user(cls, text: str) -> "ChatMessage":
        return cls(role=MessageRole.USER, text=text)

    @classmethod
    def assistant(cls, text: str = "", tool_calls: tuple[ToolCall, ...] = ()) -> "ChatMessage":
        return cls(role=MessageRole.ASSISTANT, text=text, tool_calls=tuple(tool_calls))

    @classmethod
    def tool_result(cls, call: ToolCall, content: str, *, is_error: bool = False) -> "ChatMessage":
        return cls(
            role=MessageRole.TOOL,
            text=content,
            tool_call_id=call.call_id,
            tool_name=call.name,
            is_error=is_error,
        )


@dataclass(frozen=True, slots=True)
class ChatResponse:
    message: ChatMessage
    stop_reason: StopReason
    input_tokens: int | None = None
    output_tokens: int | None = None
