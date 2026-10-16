from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
import json

from photometry_app.assistant.messages import ChatMessage, ChatResponse, MessageRole, StopReason, ToolCall
from photometry_app.assistant.providers import ChatProvider, ProviderError
from photometry_app.assistant.tools import Approver, ToolContext, ToolRegistry, ToolResult, execute_tool_call


DEFAULT_MAX_STEPS = 12
DEFAULT_HISTORY_CHAR_BUDGET = 240_000
_RETRY_DELAY_SECONDS = 2.0


class EventKind(str, Enum):
    ASSISTANT_TEXT = "assistant_text"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    RETRYING = "retrying"


@dataclass(frozen=True, slots=True)
class ConversationEvent:
    kind: EventKind
    text: str = ""
    tool_name: str = ""
    arguments: dict[str, object] = field(default_factory=dict)
    status: str = ""


class TurnStatus(str, Enum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"
    STEP_LIMIT = "step_limit"


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    status: TurnStatus
    text: str = ""
    error: str = ""
    tool_results: tuple[ToolResult, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    truncated: bool = False


EventSink = Callable[[ConversationEvent], None]


class Conversation:
    """Runs one chat: model replies, tool calls, and tool results, until the model answers in text."""

    def __init__(
        self,
        provider: ChatProvider,
        registry: ToolRegistry,
        context: ToolContext,
        *,
        system_prompt: str,
        approver: Approver | None = None,
        on_event: EventSink | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        history_char_budget: int = DEFAULT_HISTORY_CHAR_BUDGET,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._context = context
        self._system_prompt = system_prompt
        self._approver = approver
        self._on_event = on_event or (lambda _event: None)
        self._max_steps = max(1, int(max_steps))
        self._history_char_budget = max(1_000, int(history_char_budget))
        self._history: list[ChatMessage] = []

    @property
    def history(self) -> tuple[ChatMessage, ...]:
        return tuple(self._history)

    @property
    def provider(self) -> ChatProvider:
        return self._provider

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def set_provider(self, provider: ChatProvider) -> None:
        self._provider = provider

    def set_system_prompt(self, system_prompt: str) -> None:
        self._system_prompt = system_prompt

    def cancel(self) -> None:
        self._context.cancel_event.set()

    def send(self, text: str) -> TurnOutcome:
        self._context.cancel_event.clear()
        self._history.append(ChatMessage.user(text))
        results: list[ToolResult] = []
        input_tokens = 0
        output_tokens = 0
        for _step in range(self._max_steps):
            if self._cancelled():
                return TurnOutcome(TurnStatus.CANCELLED, tool_results=tuple(results), input_tokens=input_tokens, output_tokens=output_tokens)
            try:
                response = self._complete()
            except ProviderError as exc:
                status = TurnStatus.CANCELLED if self._cancelled() else TurnStatus.ERROR
                error = "" if status == TurnStatus.CANCELLED else str(exc)
                return TurnOutcome(status, error=error, tool_results=tuple(results), input_tokens=input_tokens, output_tokens=output_tokens)
            input_tokens += response.input_tokens or 0
            output_tokens += response.output_tokens or 0
            if self._cancelled():
                # Discard the reply: its tool calls would otherwise stay unanswered in the history.
                return TurnOutcome(TurnStatus.CANCELLED, tool_results=tuple(results), input_tokens=input_tokens, output_tokens=output_tokens)

            message = response.message
            self._history.append(message)
            if message.text.strip():
                self._on_event(ConversationEvent(EventKind.ASSISTANT_TEXT, text=message.text))

            if not message.tool_calls:
                return self._finish(response, results, input_tokens, output_tokens)

            for call in message.tool_calls:
                if self._cancelled():
                    result = _cancelled_result(call)
                else:
                    self._on_event(ConversationEvent(EventKind.TOOL_STARTED, tool_name=call.name, arguments=dict(call.arguments)))
                    result = execute_tool_call(self._registry, self._context, call, self._approver)
                    self._on_event(ConversationEvent(EventKind.TOOL_FINISHED, tool_name=call.name, status=result.status))
                results.append(result)
                self._history.append(result.to_message())
            if self._cancelled():
                return TurnOutcome(TurnStatus.CANCELLED, tool_results=tuple(results), input_tokens=input_tokens, output_tokens=output_tokens)

        text = f"I stopped after {self._max_steps} steps without finishing. Ask me to continue, or narrow the request."
        return TurnOutcome(TurnStatus.STEP_LIMIT, text=text, tool_results=tuple(results), input_tokens=input_tokens, output_tokens=output_tokens)

    def _finish(self, response: ChatResponse, results: list[ToolResult], input_tokens: int, output_tokens: int) -> TurnOutcome:
        text = response.message.text.strip()
        if response.stop_reason == StopReason.BLOCKED:
            return TurnOutcome(
                TurnStatus.ERROR,
                text=text,
                error="The provider stopped this answer for content reasons.",
                tool_results=tuple(results),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        return TurnOutcome(
            TurnStatus.COMPLETED,
            text=text,
            tool_results=tuple(results),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            truncated=response.stop_reason == StopReason.MAX_TOKENS,
        )

    def _complete(self) -> ChatResponse:
        messages = self._prompt_messages()
        tools = self._registry.specs()
        try:
            return self._provider.complete(system=self._system_prompt, messages=messages, tools=tools)
        except ProviderError as exc:
            if not exc.retryable:
                raise
            self._on_event(ConversationEvent(EventKind.RETRYING, text=str(exc)))
            if self._context.cancel_event.wait(_RETRY_DELAY_SECONDS):
                raise ProviderError("Stopped by the user.") from exc
            return self._provider.complete(system=self._system_prompt, messages=messages, tools=tools)

    def _prompt_messages(self) -> list[ChatMessage]:
        """Keep whole turns, newest first, within the budget; a turn always starts at a user message."""
        turn_starts = [index for index, message in enumerate(self._history) if message.role == MessageRole.USER]
        if not turn_starts:
            return list(self._history)
        start = turn_starts[-1]
        used = _history_chars(self._history[start:])
        for candidate in reversed(turn_starts[:-1]):
            size = _history_chars(self._history[candidate:start])
            if used + size > self._history_char_budget:
                break
            used += size
            start = candidate
        return list(self._history[start:])

    def _cancelled(self) -> bool:
        return self._context.cancel_event.is_set()


def _history_chars(messages: Sequence[ChatMessage]) -> int:
    total = 0
    for message in messages:
        total += len(message.text)
        for call in message.tool_calls:
            total += len(call.name) + len(json.dumps(call.arguments, default=str))
    return total


def _cancelled_result(call: ToolCall) -> ToolResult:
    return ToolResult(call=call, status="cancelled", content=json.dumps({"status": "cancelled", "message": "Stopped by the user."}))
