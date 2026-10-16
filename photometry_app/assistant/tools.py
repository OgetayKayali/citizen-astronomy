from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
import json
from pathlib import Path
import re
import threading

from photometry_app.assistant.file_guard import FileGuardError, GuardedFileWriter
from photometry_app.assistant.messages import ChatMessage, ToolCall, ToolSpec
from photometry_app.assistant.schema import validate_arguments


DEFAULT_MAX_RESULT_CHARS = 24_000
_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


class ToolAccess(str, Enum):
    READ = "read"
    SESSION = "session"
    CREATE = "create"


ACCESS_DESCRIPTIONS = {
    ToolAccess.READ: "Reads files or CAst state. Changes nothing.",
    ToolAccess.SESSION: "Changes what CAst shows or indexes. Files on disk are not touched.",
    ToolAccess.CREATE: "Writes new files into an output folder you approve. Existing files are never replaced.",
}


class ToolFailure(RuntimeError):
    """A tool could not do what was asked; the message is shown to the model and the user."""


class ToolCancelled(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ActionPlan:
    title: str
    summary: str
    details: tuple[str, ...] = ()
    output_folder: Path | None = None
    payload: object = None


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    approved: bool
    output_folder: Path | None = None
    note: str = ""


@dataclass(slots=True)
class ToolContext:
    writer: GuardedFileWriter
    app: object | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    progress: Callable[[str], None] = lambda _message: None

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise ToolCancelled("Stopped by the user.")


ToolRunner = Callable[[ToolContext, dict[str, object], ActionPlan | None], object]
ToolPlanner = Callable[[ToolContext, dict[str, object]], ActionPlan]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    access: ToolAccess
    run: ToolRunner
    parameters: Mapping[str, object] = field(default_factory=lambda: {"type": "object", "properties": {}})
    plan: ToolPlanner | None = None
    activity: str = ""

    @property
    def activity_label(self) -> str:
        return self.activity or self.name.replace("_", " ").capitalize()

    def __post_init__(self) -> None:
        if not _TOOL_NAME_PATTERN.match(self.name):
            raise ValueError(f"Invalid tool name {self.name!r}.")
        if self.parameters.get("type") != "object":
            raise ValueError(f"Tool {self.name!r} parameters must be a JSON object schema.")
        if self.access == ToolAccess.READ and self.plan is not None:
            raise ValueError(f"Read tool {self.name!r} must not need approval.")
        if self.access != ToolAccess.READ and self.plan is None:
            raise ValueError(f"{self.access.value.title()} tool {self.name!r} must describe its plan for approval.")

    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, parameters=dict(self.parameters))


Approver = Callable[[ToolDefinition, ActionPlan], ApprovalDecision]


class ToolRegistry:
    def __init__(self, tools: tuple[ToolDefinition, ...] = ()) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name!r} is already registered.")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def specs(self) -> list[ToolSpec]:
        return [tool.spec() for tool in self._tools.values()]


@dataclass(frozen=True, slots=True)
class ToolResult:
    call: ToolCall
    status: str
    content: str
    created_files: tuple[Path, ...] = ()

    @property
    def is_error(self) -> bool:
        return self.status == "error"

    def to_message(self) -> ChatMessage:
        return ChatMessage.tool_result(self.call, self.content, is_error=self.is_error)


def execute_tool_call(
    registry: ToolRegistry,
    context: ToolContext,
    call: ToolCall,
    approver: Approver | None,
    *,
    max_result_chars: int = DEFAULT_MAX_RESULT_CHARS,
) -> ToolResult:
    tool = registry.get(call.name)
    if tool is None:
        known = ", ".join(registry.names()) or "none"
        return _error(call, f"There is no tool named {call.name!r}. Available tools: {known}.")
    if call.arguments_error:
        return _error(call, call.arguments_error)
    errors = validate_arguments(tool.parameters, call.arguments)
    if errors:
        return _error(call, "Invalid arguments: " + " ".join(errors))

    arguments = dict(call.arguments)
    created_before = len(context.writer.created_files)
    try:
        context.check_cancelled()
        if tool.access == ToolAccess.READ:
            value = tool.run(context, arguments, None)
        else:
            plan = tool.plan(context, arguments)
            context.check_cancelled()
            decision = _ask_approval(approver, tool, plan)
            if not decision.approved:
                note = f" The user said: {decision.note}" if decision.note else ""
                return _result(call, "declined", {"status": "declined", "message": f"The user declined: {plan.title}.{note}"}, max_result_chars)
            if tool.access == ToolAccess.CREATE:
                folder = decision.output_folder or plan.output_folder
                if folder is None:
                    return _error(call, "No output folder was chosen, so nothing was written.")
                with context.writer.approved(folder) as approved_folder:
                    value = tool.run(context, arguments, replace(plan, output_folder=approved_folder))
            else:
                value = tool.run(context, arguments, plan)
    except ToolCancelled as exc:
        return _result(call, "cancelled", {"status": "cancelled", "message": str(exc)}, max_result_chars, context, created_before)
    except FileGuardError as exc:
        return _error(call, f"Refused for safety: {exc}", context, created_before)
    except ToolFailure as exc:
        return _error(call, str(exc), context, created_before)
    except Exception as exc:
        return _error(call, f"The {tool.name} tool failed: {type(exc).__name__}: {exc}", context, created_before)
    return _result(call, "ok", value, max_result_chars, context, created_before)


def _ask_approval(approver: Approver | None, tool: ToolDefinition, plan: ActionPlan) -> ApprovalDecision:
    if approver is None:
        return ApprovalDecision(approved=False, note="Approval is not available here, so the action was not run.")
    decision = approver(tool, plan)
    return decision if isinstance(decision, ApprovalDecision) else ApprovalDecision(approved=False)


def _new_files(context: ToolContext | None, created_before: int) -> tuple[Path, ...]:
    if context is None:
        return ()
    return context.writer.created_files[created_before:]


def _error(call: ToolCall, message: str, context: ToolContext | None = None, created_before: int = 0) -> ToolResult:
    payload: dict[str, object] = {"status": "error", "message": message}
    created = _new_files(context, created_before)
    if created:
        payload["created_files"] = [str(path) for path in created]
    return ToolResult(call=call, status="error", content=json.dumps(payload), created_files=created)


def _result(
    call: ToolCall,
    status: str,
    value: object,
    max_result_chars: int,
    context: ToolContext | None = None,
    created_before: int = 0,
) -> ToolResult:
    created = _new_files(context, created_before)
    if created and isinstance(value, dict) and "created_files" not in value:
        value = {**value, "created_files": [str(path) for path in created]}
    content = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    if len(content) > max_result_chars:
        content = json.dumps(
            {
                "status": status,
                "truncated": True,
                "note": f"The result was {len(content)} characters and was cut to fit. Ask for a narrower result if needed.",
                "partial_result": content[:max_result_chars],
            },
            ensure_ascii=False,
        )
    return ToolResult(call=call, status=status, content=content, created_files=created)
