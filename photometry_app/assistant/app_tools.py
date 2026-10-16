"""Tools that look at or steer the running CAst window through a small bridge."""

from __future__ import annotations

import threading
from typing import Protocol

from photometry_app.assistant.program_map import MODES, mode_info
from photometry_app.assistant.tools import ActionPlan, ToolAccess, ToolContext, ToolDefinition, ToolFailure
from photometry_app.core.models import AppMode


class AppBridge(Protocol):
    """Implemented by the main window; every call runs on the UI thread."""

    def state(self, cancel_event: threading.Event) -> dict[str, object]: ...

    def current_mode(self, cancel_event: threading.Event) -> AppMode: ...

    def mode_switch_notes(self, mode: AppMode, cancel_event: threading.Event) -> tuple[str, ...]: ...

    def switch_mode(self, mode: AppMode, cancel_event: threading.Event) -> dict[str, object]: ...


def app_tools(bridge: AppBridge) -> tuple[ToolDefinition, ...]:
    def get_state(context: ToolContext, arguments: dict[str, object], _plan: ActionPlan | None) -> object:
        return bridge.state(context.cancel_event)

    def plan_switch(context: ToolContext, arguments: dict[str, object]) -> ActionPlan:
        info = mode_info(str(arguments["mode"]))
        if info is None:
            raise ToolFailure(f"Unknown mode {arguments['mode']!r}.")
        if bridge.current_mode(context.cancel_event) == info.mode:
            raise ToolFailure(f"CAst is already in {info.name}.")
        return ActionPlan(
            title=f"Switch to {info.name}",
            summary=f"Opens {info.name}. {info.summary}",
            details=bridge.mode_switch_notes(info.mode, context.cancel_event),
            payload=info.mode,
        )

    def switch(context: ToolContext, arguments: dict[str, object], plan: ActionPlan | None) -> object:
        assert plan is not None and isinstance(plan.payload, AppMode)
        return bridge.switch_mode(plan.payload, context.cancel_event)

    return (
        ToolDefinition(
            name="get_cast_state",
            description="What CAst has open right now: the mode, the workspace folder, and whether a job is running.",
            access=ToolAccess.READ,
            run=get_state,
            activity="Checking CAst",
        ),
        ToolDefinition(
            name="switch_mode",
            description="Switch CAst to another mode. The user approves it first. Files are not touched.",
            access=ToolAccess.SESSION,
            run=switch,
            plan=plan_switch,
            parameters={
                "type": "object",
                "properties": {"mode": {"type": "string", "enum": [info.name for info in MODES]}},
                "required": ["mode"],
                "additionalProperties": False,
            },
            activity="Switching mode",
        ),
    )
