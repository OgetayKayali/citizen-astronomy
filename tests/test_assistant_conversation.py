from __future__ import annotations

from collections.abc import Callable, Sequence
import json
from pathlib import Path
import tempfile
import unittest

from photometry_app.assistant.conversation import Conversation, EventKind, TurnStatus
from photometry_app.assistant.file_guard import GuardedFileWriter
from photometry_app.assistant.messages import ChatMessage, ChatResponse, MessageRole, StopReason, ToolCall, ToolSpec
from photometry_app.assistant.providers import ProviderError
from photometry_app.assistant.tools import (
    ActionPlan,
    ApprovalDecision,
    ToolAccess,
    ToolContext,
    ToolDefinition,
    ToolRegistry,
)


def _text(text: str, stop: StopReason = StopReason.END) -> ChatResponse:
    return ChatResponse(message=ChatMessage.assistant(text), stop_reason=stop, input_tokens=10, output_tokens=5)


def _calls(*calls: ToolCall, text: str = "") -> ChatResponse:
    return ChatResponse(message=ChatMessage.assistant(text, calls), stop_reason=StopReason.TOOL_CALLS, input_tokens=10, output_tokens=5)


class ScriptedProvider:
    """Plays back replies in order and records what CAst sent each time."""

    display_name = "Scripted"

    def __init__(self, *replies: ChatResponse | Exception | Callable[[], ChatResponse]) -> None:
        self._replies = list(replies)
        self.requests: list[dict[str, object]] = []

    def complete(self, *, system: str, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] = ()) -> ChatResponse:
        self.requests.append({"system": system, "messages": list(messages), "tools": [tool.name for tool in tools]})
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            return reply()
        return reply


class ConversationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name).resolve()
        self.context = ToolContext(writer=GuardedFileWriter(protected_roots=[self.root / "Lights"]))
        self.events = []
        self.tool_runs: list[dict[str, object]] = []

        def count_images(context: ToolContext, arguments: dict[str, object], plan: ActionPlan | None) -> object:
            self.tool_runs.append(arguments)
            return {"folder": arguments["path"], "light_frames": 42}

        def plan_save(context: ToolContext, arguments: dict[str, object]) -> ActionPlan:
            return ActionPlan(title="Save a note", summary="1 file", output_folder=self.root / "Exports")

        def save_note(context: ToolContext, arguments: dict[str, object], plan: ActionPlan | None) -> object:
            path = context.writer.write_text(plan.output_folder, "note.txt", "hi")
            return {"saved": str(path)}

        self.registry = ToolRegistry(
            (
                ToolDefinition(
                    name="count_images",
                    description="Count images.",
                    access=ToolAccess.READ,
                    run=count_images,
                    parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                ),
                ToolDefinition(name="save_note", description="Save.", access=ToolAccess.CREATE, run=save_note, plan=plan_save),
            )
        )

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _conversation(self, provider: ScriptedProvider, **kwargs: object) -> Conversation:
        return Conversation(
            provider,
            self.registry,
            self.context,
            system_prompt="You are CAst's guide.",
            on_event=self.events.append,
            **kwargs,
        )

    def test_plain_answer_needs_one_request(self) -> None:
        provider = ScriptedProvider(_text("Open HR Diagram mode, then click Open."))
        outcome = self._conversation(provider).send("How do I make an HR diagram?")
        self.assertEqual(outcome.status, TurnStatus.COMPLETED)
        self.assertEqual(outcome.text, "Open HR Diagram mode, then click Open.")
        self.assertEqual(provider.requests[0]["system"], "You are CAst's guide.")
        self.assertEqual(provider.requests[0]["tools"], ["count_images", "save_note"])
        self.assertEqual([event.kind for event in self.events], [EventKind.ASSISTANT_TEXT])

    def test_tool_calls_run_and_results_go_back_to_the_model(self) -> None:
        provider = ScriptedProvider(
            _calls(ToolCall("c1", "count_images", {"path": "C:/M13"}), ToolCall("c2", "count_images", {"path": "C:/M92"}), text="Checking both."),
            _text("M13 and M92 each have 42 frames."),
        )
        conversation = self._conversation(provider)
        outcome = conversation.send("Count my frames")
        self.assertEqual(outcome.status, TurnStatus.COMPLETED)
        self.assertEqual(outcome.text, "M13 and M92 each have 42 frames.")
        self.assertEqual(self.tool_runs, [{"path": "C:/M13"}, {"path": "C:/M92"}])
        self.assertEqual(outcome.input_tokens, 20)
        second_request = provider.requests[1]["messages"]
        self.assertEqual([message.role for message in second_request], [MessageRole.USER, MessageRole.ASSISTANT, MessageRole.TOOL, MessageRole.TOOL])
        self.assertEqual(json.loads(second_request[2].text)["folder"], "C:/M13")
        kinds = [event.kind for event in self.events]
        self.assertEqual(
            kinds,
            [EventKind.ASSISTANT_TEXT, EventKind.TOOL_STARTED, EventKind.TOOL_FINISHED, EventKind.TOOL_STARTED, EventKind.TOOL_FINISHED, EventKind.ASSISTANT_TEXT],
        )

    def test_tool_errors_are_returned_to_the_model_instead_of_ending_the_turn(self) -> None:
        provider = ScriptedProvider(_calls(ToolCall("c1", "count_images", {})), _text("I need a folder path."))
        outcome = self._conversation(provider).send("Count")
        self.assertEqual(outcome.status, TurnStatus.COMPLETED)
        self.assertTrue(outcome.tool_results[0].is_error)
        self.assertTrue(provider.requests[1]["messages"][-1].is_error)

    def test_create_actions_need_approval_through_the_loop(self) -> None:
        provider = ScriptedProvider(_calls(ToolCall("c1", "save_note", {})), _text("Nothing was saved."))
        outcome = self._conversation(provider).send("Save a note")
        self.assertEqual(outcome.tool_results[0].status, "declined")
        self.assertFalse((self.root / "Exports").exists())

        approved = []
        provider = ScriptedProvider(_calls(ToolCall("c1", "save_note", {})), _text("Saved."))
        approver = lambda tool, plan: approved.append(plan.title) or ApprovalDecision(approved=True)
        outcome = self._conversation(provider, approver=approver).send("Save a note")
        self.assertEqual(approved, ["Save a note"])
        self.assertEqual(outcome.tool_results[0].created_files, (self.root / "Exports" / "note.txt",))

    def test_step_limit_stops_runaway_tool_loops(self) -> None:
        provider = ScriptedProvider(*[_calls(ToolCall(f"c{index}", "count_images", {"path": "x"})) for index in range(3)])
        outcome = self._conversation(provider, max_steps=3).send("Loop forever")
        self.assertEqual(outcome.status, TurnStatus.STEP_LIMIT)
        self.assertIn("stopped after 3 steps", outcome.text)
        self.assertEqual(len(self.tool_runs), 3)

    def test_provider_errors_end_the_turn_with_a_readable_message(self) -> None:
        provider = ScriptedProvider(ProviderError("Anthropic rejected the API key.", status_code=401))
        outcome = self._conversation(provider).send("hi")
        self.assertEqual(outcome.status, TurnStatus.ERROR)
        self.assertEqual(outcome.error, "Anthropic rejected the API key.")
        self.assertEqual(len(provider.requests), 1)

    def test_retryable_errors_are_retried_once(self) -> None:
        provider = ScriptedProvider(ProviderError("busy", status_code=503, retryable=True), _text("Back online."))
        conversation = self._conversation(provider)
        import photometry_app.assistant.conversation as conversation_module

        previous = conversation_module._RETRY_DELAY_SECONDS
        conversation_module._RETRY_DELAY_SECONDS = 0.0
        try:
            outcome = conversation.send("hi")
        finally:
            conversation_module._RETRY_DELAY_SECONDS = previous
        self.assertEqual(outcome.status, TurnStatus.COMPLETED)
        self.assertEqual(outcome.text, "Back online.")
        self.assertIn(EventKind.RETRYING, [event.kind for event in self.events])

    def test_cancel_during_a_model_reply_discards_its_tool_calls(self) -> None:
        conversation: Conversation | None = None

        def reply_then_cancel() -> ChatResponse:
            conversation.cancel()
            return _calls(ToolCall("c1", "count_images", {"path": "x"}))

        provider = ScriptedProvider(reply_then_cancel, _text("fresh answer"))
        conversation = self._conversation(provider)
        outcome = conversation.send("Count")
        self.assertEqual(outcome.status, TurnStatus.CANCELLED)
        self.assertEqual(self.tool_runs, [])
        self.assertEqual([message.role for message in conversation.history], [MessageRole.USER])

        follow_up = conversation.send("Never mind, how do I blink?")
        self.assertEqual(follow_up.status, TurnStatus.COMPLETED)
        self.assertEqual(follow_up.text, "fresh answer")

    def test_cancel_during_tools_answers_every_pending_call(self) -> None:
        conversation: Conversation | None = None

        def count_and_cancel(context: ToolContext, arguments: dict[str, object], plan: ActionPlan | None) -> object:
            conversation.cancel()
            return {"ok": True}

        registry = ToolRegistry(
            (ToolDefinition(name="count_images", description="", access=ToolAccess.READ, run=count_and_cancel),)
        )
        provider = ScriptedProvider(_calls(ToolCall("c1", "count_images", {}), ToolCall("c2", "count_images", {})))
        conversation = Conversation(provider, registry, self.context, system_prompt="")
        outcome = conversation.send("Count")
        self.assertEqual(outcome.status, TurnStatus.CANCELLED)
        tool_messages = [message for message in conversation.history if message.role == MessageRole.TOOL]
        self.assertEqual([message.tool_call_id for message in tool_messages], ["c1", "c2"])
        self.assertIn("cancelled", tool_messages[1].text)

    def test_old_turns_are_dropped_whole_when_history_is_too_long(self) -> None:
        provider = ScriptedProvider(_text("a" * 900), _text("b" * 900), _text("c"))
        conversation = self._conversation(provider, history_char_budget=1_000)
        conversation.send("first")
        conversation.send("second")
        conversation.send("third")
        sent = provider.requests[2]["messages"]
        self.assertEqual(sent[0].text, "second")
        self.assertEqual([message.role for message in sent], [MessageRole.USER, MessageRole.ASSISTANT, MessageRole.USER])
        self.assertEqual(len(conversation.history), 6)

    def test_cut_off_answers_are_marked(self) -> None:
        provider = ScriptedProvider(_text("Partial", StopReason.MAX_TOKENS))
        outcome = self._conversation(provider).send("Explain everything")
        self.assertTrue(outcome.truncated)
        self.assertEqual(outcome.text, "Partial")


if __name__ == "__main__":
    unittest.main()
