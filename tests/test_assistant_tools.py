from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from photometry_app.assistant.file_guard import GuardedFileWriter
from photometry_app.assistant.messages import MessageRole, ToolCall
from photometry_app.assistant.tools import (
    ActionPlan,
    ApprovalDecision,
    ToolAccess,
    ToolContext,
    ToolDefinition,
    ToolFailure,
    ToolRegistry,
    execute_tool_call,
)


FOLDER_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        "mode": {"type": "string", "enum": ["summary", "full"]},
    },
    "required": ["path"],
    "additionalProperties": False,
}


class RecordingApprover:
    def __init__(self, decision: ApprovalDecision) -> None:
        self.decision = decision
        self.plans: list[ActionPlan] = []

    def __call__(self, tool: ToolDefinition, plan: ActionPlan) -> ApprovalDecision:
        self.plans.append(plan)
        return self.decision


def _call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(call_id="call_1", name=name, arguments=arguments)


class ToolDefinitionRulesTest(unittest.TestCase):
    def test_read_tools_cannot_ask_for_approval_and_others_must(self) -> None:
        run = lambda context, arguments, plan: {}
        planner = lambda context, arguments: ActionPlan(title="x", summary="y")
        with self.assertRaisesRegex(ValueError, "must not need approval"):
            ToolDefinition(name="peek", description="", access=ToolAccess.READ, run=run, plan=planner)
        for access in (ToolAccess.SESSION, ToolAccess.CREATE):
            with self.subTest(access=access):
                with self.assertRaisesRegex(ValueError, "must describe its plan"):
                    ToolDefinition(name="act", description="", access=access, run=run)

    def test_names_and_schemas_are_validated(self) -> None:
        run = lambda context, arguments, plan: {}
        with self.assertRaises(ValueError):
            ToolDefinition(name="bad name", description="", access=ToolAccess.READ, run=run)
        with self.assertRaises(ValueError):
            ToolDefinition(name="ok", description="", access=ToolAccess.READ, run=run, parameters={"type": "string"})
        registry = ToolRegistry((ToolDefinition(name="ok", description="d", access=ToolAccess.READ, run=run),))
        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register(ToolDefinition(name="ok", description="d", access=ToolAccess.READ, run=run))
        self.assertEqual([spec.name for spec in registry.specs()], ["ok"])


class ExecuteToolCallTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name).resolve()
        self.source = self.root / "Lights"
        self.source.mkdir()
        (self.source / "frame.fits").write_bytes(b"ORIGINAL")
        self.writer = GuardedFileWriter(protected_roots=[self.source])
        self.context = ToolContext(writer=self.writer)
        self.runs: list[tuple[dict[str, object], ActionPlan | None]] = []

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _read_tool(self) -> ToolDefinition:
        def run(context: ToolContext, arguments: dict[str, object], plan: ActionPlan | None) -> object:
            self.runs.append((arguments, plan))
            return {"files": 3, "path": arguments["path"]}

        return ToolDefinition(name="list_folder", description="List.", access=ToolAccess.READ, run=run, parameters=FOLDER_SCHEMA)

    def _session_tool(self) -> ToolDefinition:
        def plan(context: ToolContext, arguments: dict[str, object]) -> ActionPlan:
            return ActionPlan(title="Leave 2 copies out of the deck", summary="2 exact duplicates", payload=["a.fits", "b.fits"])

        def run(context: ToolContext, arguments: dict[str, object], action_plan: ActionPlan | None) -> object:
            self.runs.append((arguments, action_plan))
            return {"excluded": action_plan.payload}

        return ToolDefinition(name="exclude_from_deck", description="Exclude.", access=ToolAccess.SESSION, run=run, plan=plan)

    def _create_tool(self, *, target: str = "output", proposed: Path | None = None) -> ToolDefinition:
        def plan(context: ToolContext, arguments: dict[str, object]) -> ActionPlan:
            return ActionPlan(title="Save 1 HR diagram", summary="M13", output_folder=proposed)

        def run(context: ToolContext, arguments: dict[str, object], action_plan: ActionPlan | None) -> object:
            self.runs.append((arguments, action_plan))
            folder = action_plan.output_folder if target == "output" else self.source
            path = context.writer.write_text(folder, "frame.fits", "diagram")
            return {"saved": str(path)}

        return ToolDefinition(name="save_hr", description="Save.", access=ToolAccess.CREATE, run=run, plan=plan)

    def test_unknown_tool_lists_the_real_ones(self) -> None:
        registry = ToolRegistry((self._read_tool(),))
        result = execute_tool_call(registry, self.context, _call("delete_files", path="C:/"), None)
        self.assertTrue(result.is_error)
        self.assertIn("no tool named 'delete_files'", result.content)
        self.assertIn("list_folder", result.content)

    def test_bad_arguments_are_rejected_before_running(self) -> None:
        registry = ToolRegistry((self._read_tool(),))
        cases = [
            ({}, "arguments.path is required"),
            ({"path": 4}, "arguments.path must be a string"),
            ({"path": "x", "limit": 0}, "at least 1"),
            ({"path": "x", "limit": True}, "must be an integer"),
            ({"path": "x", "mode": "everything"}, "must be one of"),
            ({"path": "x", "recursive": True}, "not a known argument"),
        ]
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                result = execute_tool_call(registry, self.context, ToolCall("c", "list_folder", arguments), None)
                self.assertTrue(result.is_error)
                self.assertIn(expected, json.loads(result.content)["message"])
        malformed = ToolCall("c", "list_folder", {}, arguments_error="Tool arguments were not valid JSON: x.")
        self.assertIn("not valid JSON", execute_tool_call(registry, self.context, malformed, None).content)
        self.assertEqual(self.runs, [])

    def test_read_tools_run_without_approval(self) -> None:
        registry = ToolRegistry((self._read_tool(),))
        result = execute_tool_call(registry, self.context, _call("list_folder", path="C:/Data", limit=5), None)
        self.assertEqual(result.status, "ok")
        self.assertEqual(json.loads(result.content), {"files": 3, "path": "C:/Data"})
        message = result.to_message()
        self.assertEqual(message.role, MessageRole.TOOL)
        self.assertFalse(message.is_error)

    def test_actions_are_declined_when_no_one_can_approve(self) -> None:
        registry = ToolRegistry((self._session_tool(), self._create_tool(proposed=self.root / "Exports")))
        for name in ("exclude_from_deck", "save_hr"):
            with self.subTest(tool=name):
                result = execute_tool_call(registry, self.context, _call(name), None)
                self.assertEqual(result.status, "declined")
                self.assertIn("Approval is not available", result.content)
        self.assertEqual(self.runs, [])
        self.assertFalse((self.root / "Exports").exists())

    def test_declined_session_action_does_not_run(self) -> None:
        approver = RecordingApprover(ApprovalDecision(approved=False, note="keep the calibrated copies"))
        registry = ToolRegistry((self._session_tool(),))
        result = execute_tool_call(registry, self.context, _call("exclude_from_deck"), approver)
        self.assertEqual(result.status, "declined")
        self.assertIn("keep the calibrated copies", result.content)
        self.assertEqual([plan.title for plan in approver.plans], ["Leave 2 copies out of the deck"])
        self.assertEqual(self.runs, [])

    def test_approved_session_action_receives_its_plan(self) -> None:
        registry = ToolRegistry((self._session_tool(),))
        result = execute_tool_call(registry, self.context, _call("exclude_from_deck"), RecordingApprover(ApprovalDecision(approved=True)))
        self.assertEqual(result.status, "ok")
        self.assertEqual(json.loads(result.content), {"excluded": ["a.fits", "b.fits"]})

    def test_create_action_writes_only_into_the_folder_the_user_chose(self) -> None:
        proposed = self.root / "Proposed"
        chosen = self.root / "Chosen"
        registry = ToolRegistry((self._create_tool(proposed=proposed),))
        approver = RecordingApprover(ApprovalDecision(approved=True, output_folder=chosen))
        result = execute_tool_call(registry, self.context, _call("save_hr"), approver)
        self.assertEqual(result.status, "ok", result.content)
        self.assertEqual(result.created_files, (chosen / "frame.fits",))
        self.assertEqual(json.loads(result.content)["created_files"], [str(chosen / "frame.fits")])
        self.assertFalse(proposed.exists())
        self.assertEqual(self.writer.approved_folders, ())

    def test_create_action_cannot_write_outside_its_approval(self) -> None:
        registry = ToolRegistry((self._create_tool(target="source", proposed=self.root / "Exports"),))
        result = execute_tool_call(registry, self.context, _call("save_hr"), RecordingApprover(ApprovalDecision(approved=True)))
        self.assertTrue(result.is_error)
        self.assertIn("Refused for safety", result.content)
        self.assertEqual((self.source / "frame.fits").read_bytes(), b"ORIGINAL")
        self.assertEqual(result.created_files, ())

    def test_create_action_needs_an_output_folder(self) -> None:
        registry = ToolRegistry((self._create_tool(proposed=None),))
        result = execute_tool_call(registry, self.context, _call("save_hr"), RecordingApprover(ApprovalDecision(approved=True)))
        self.assertTrue(result.is_error)
        self.assertIn("No output folder", result.content)
        self.assertEqual(self.runs, [])

    def test_create_action_refuses_a_source_folder_even_if_approved(self) -> None:
        registry = ToolRegistry((self._create_tool(proposed=self.source),))
        result = execute_tool_call(registry, self.context, _call("save_hr"), RecordingApprover(ApprovalDecision(approved=True)))
        self.assertTrue(result.is_error)
        self.assertIn("outside your source images", result.content)
        self.assertEqual(self.runs, [])

    def test_tool_failures_become_readable_errors(self) -> None:
        def failing(context: ToolContext, arguments: dict[str, object], plan: ActionPlan | None) -> object:
            raise ToolFailure("That folder has no FITS or XISF images.")

        def crashing(context: ToolContext, arguments: dict[str, object], plan: ActionPlan | None) -> object:
            raise KeyError("NAXIS1")

        registry = ToolRegistry(
            (
                ToolDefinition(name="failing", description="", access=ToolAccess.READ, run=failing),
                ToolDefinition(name="crashing", description="", access=ToolAccess.READ, run=crashing),
            )
        )
        failed = execute_tool_call(registry, self.context, _call("failing"), None)
        self.assertEqual(json.loads(failed.content)["message"], "That folder has no FITS or XISF images.")
        crashed = execute_tool_call(registry, self.context, _call("crashing"), None)
        self.assertIn("The crashing tool failed: KeyError", crashed.content)

    def test_cancelled_calls_do_not_run(self) -> None:
        registry = ToolRegistry((self._read_tool(),))
        self.context.cancel_event.set()
        result = execute_tool_call(registry, self.context, _call("list_folder", path="x"), None)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(self.runs, [])

    def test_large_results_are_truncated_with_a_note(self) -> None:
        def big(context: ToolContext, arguments: dict[str, object], plan: ActionPlan | None) -> object:
            return {"rows": ["x" * 100] * 100}

        registry = ToolRegistry((ToolDefinition(name="big", description="", access=ToolAccess.READ, run=big),))
        result = execute_tool_call(registry, self.context, _call("big"), None, max_result_chars=500)
        payload = json.loads(result.content)
        self.assertTrue(payload["truncated"])
        self.assertEqual(len(payload["partial_result"]), 500)


if __name__ == "__main__":
    unittest.main()
