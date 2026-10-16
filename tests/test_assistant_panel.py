from __future__ import annotations

from collections.abc import Callable, Sequence
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QLabel

from photometry_app.assistant.credentials import API_KEY_ENV_VAR, api_key_account, set_credential_store
from photometry_app.assistant.messages import ChatMessage, ChatResponse, StopReason, ToolCall, ToolSpec
from photometry_app.assistant.tools import ActionPlan, ToolAccess, ToolContext, ToolDefinition, ToolRegistry
from photometry_app.ui.assistant_panel import AssistantPanel, _ApprovalCard, assistant_setup_problem

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class MemoryCredentialStore:
    def __init__(self) -> None:
        self.secrets: dict[str, str] = {}

    def get(self, account: str) -> str | None:
        return self.secrets.get(account)

    def set(self, account: str, secret: str) -> None:
        self.secrets[account] = secret

    def delete(self, account: str) -> None:
        self.secrets.pop(account, None)


class ScriptedProvider:
    display_name = "Scripted"

    def __init__(self, replies: list[ChatResponse]) -> None:
        self._replies = replies
        self.requests: list[list[ChatMessage]] = []

    def complete(self, *, system: str, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] = ()) -> ChatResponse:
        self.requests.append(list(messages))
        return self._replies.pop(0)


def _text(text: str) -> ChatResponse:
    return ChatResponse(message=ChatMessage.assistant(text), stop_reason=StopReason.END)


def _calls(*calls: ToolCall) -> ChatResponse:
    return ChatResponse(message=ChatMessage.assistant("", calls), stop_reason=StopReason.TOOL_CALLS)


def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    QApplication.processEvents()
    return predicate()


class AssistantPanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name).resolve()
        self._previous_env_key = os.environ.pop(API_KEY_ENV_VAR, None)
        self.store = MemoryCredentialStore()
        self.store.secrets[api_key_account("anthropic")] = "sk-test"
        set_credential_store(self.store)
        self.settings = SimpleNamespace(
            ai_provider="anthropic", ai_models={"anthropic": "claude-test"}, ai_openai_compatible_base_url="", ai_reasoning_effort="default"
        )
        self.saved_settings = 0
        self.configs: list[object] = []
        self.providers: list[ScriptedProvider] = []
        self.replies: list[ChatResponse] = []
        self.runs: list[str] = []
        self.panel: AssistantPanel | None = None

    def tearDown(self) -> None:
        if self.panel is not None:
            self.panel.shutdown()
            self.panel.deleteLater()
        set_credential_store(None)
        if self._previous_env_key is not None:
            os.environ[API_KEY_ENV_VAR] = self._previous_env_key
        self._temp.cleanup()

    def _registry(self) -> ToolRegistry:
        def count(context: ToolContext, arguments: dict[str, object], plan: ActionPlan | None) -> object:
            self.runs.append("count")
            return {"light_frames": 42}

        def plan_mode(context: ToolContext, arguments: dict[str, object]) -> ActionPlan:
            return ActionPlan(title="Switch to HR Diagram", summary="Opens the HR Diagram mode.", details=("Nothing is loaded yet.",))

        def switch_mode(context: ToolContext, arguments: dict[str, object], plan: ActionPlan | None) -> object:
            self.runs.append("switch")
            return {"mode": "HR Diagram"}

        def plan_save(context: ToolContext, arguments: dict[str, object]) -> ActionPlan:
            return ActionPlan(title="Save a summary", summary="1 new text file", output_folder=self.root / "Proposed")

        def save(context: ToolContext, arguments: dict[str, object], plan: ActionPlan | None) -> object:
            path = context.writer.write_text(plan.output_folder, "summary.txt", "42 frames")
            return {"saved": str(path)}

        return ToolRegistry(
            (
                ToolDefinition(name="count_images", description="", access=ToolAccess.READ, run=count, activity="Counting images"),
                ToolDefinition(name="switch_mode", description="", access=ToolAccess.SESSION, run=switch_mode, plan=plan_mode),
                ToolDefinition(name="save_summary", description="", access=ToolAccess.CREATE, run=save, plan=plan_save),
            )
        )

    def _provider_factory(self, config: object) -> ScriptedProvider:
        self.configs.append(config)
        provider = ScriptedProvider(self.replies)
        self.providers.append(provider)
        return provider

    def _make_panel(self) -> AssistantPanel:
        self.panel = AssistantPanel(
            settings_provider=lambda: self.settings,
            registry_factory=self._registry,
            state_provider=lambda: "- Open mode: Sky Explorer",
            protected_roots_provider=lambda: [self.root / "Lights"],
            provider_factory=self._provider_factory,
            on_settings_changed=self._count_save,
        )
        self.panel.resize(420, 600)
        return self.panel

    def _count_save(self) -> None:
        self.saved_settings += 1

    def _texts(self) -> list[str]:
        texts: list[str] = []
        for widget in self.panel._transcript.widgets():
            if isinstance(widget, QLabel):
                texts.append(widget.text())
            else:
                texts.extend(label.text() for label in widget.findChildren(QLabel))
        return texts

    def _cards(self) -> list[_ApprovalCard]:
        return [widget for widget in self.panel._transcript.widgets() if isinstance(widget, _ApprovalCard)]

    def test_setup_banner_explains_what_is_missing(self) -> None:
        self.assertIsNone(assistant_setup_problem(self.settings))
        self.assertIn("Open a workspace", assistant_setup_problem(None))
        missing_model = SimpleNamespace(ai_provider="gemini", ai_models={}, ai_openai_compatible_base_url="")
        self.assertIn("Choose a Google Gemini model", assistant_setup_problem(missing_model))
        missing_key = SimpleNamespace(ai_provider="gemini", ai_models={"gemini": "g"}, ai_openai_compatible_base_url="")
        self.assertIn("Add your Google Gemini API key", assistant_setup_problem(missing_key))
        local = SimpleNamespace(ai_provider="openai_compatible", ai_models={"openai_compatible": "llama"}, ai_openai_compatible_base_url="http://localhost:11434/v1")
        self.assertIsNone(assistant_setup_problem(local))

        self.settings = missing_key
        panel = self._make_panel()
        self.assertFalse(panel._setup_banner.isHidden())
        self.assertFalse(panel.send_message("hello"))
        self.assertEqual(self.providers, [])

    def test_question_gets_an_answer_in_the_transcript(self) -> None:
        panel = self._make_panel()
        self.assertTrue(panel._setup_banner.isHidden())
        self.replies.append(_text("Open **HR Diagram** mode, then click Open."))
        self.assertTrue(panel.send_message("How do I make an HR diagram?"))
        self.assertTrue(panel.is_busy())
        self.assertTrue(_wait_until(lambda: not panel.is_busy()))
        texts = self._texts()
        self.assertIn("How do I make an HR diagram?", texts)
        self.assertIn("Open **HR Diagram** mode, then click Open.", texts)
        self.assertTrue(panel._stop_button.isHidden())

    def test_tool_activity_is_shown_with_its_result(self) -> None:
        panel = self._make_panel()
        self.replies.extend([_calls(ToolCall("c1", "count_images", {})), _text("There are 42 frames.")])
        panel.send_message("Count my frames")
        self.assertTrue(_wait_until(lambda: not panel.is_busy()))
        texts = self._texts()
        self.assertIn("Counting images: done", texts)
        self.assertIn("There are 42 frames.", texts)
        self.assertEqual(self.runs, ["count"])

    def test_session_action_waits_for_approve(self) -> None:
        panel = self._make_panel()
        self.replies.extend([_calls(ToolCall("c1", "switch_mode", {})), _text("HR Diagram is open.")])
        panel.send_message("Take me to HR Diagram")
        self.assertTrue(_wait_until(lambda: bool(self._cards())))
        card = self._cards()[0]
        self.assertIn("Switch to HR Diagram", self._texts())
        self.assertEqual(self.runs, [])
        self.assertTrue(panel.is_busy())
        card.approve()
        self.assertTrue(_wait_until(lambda: not panel.is_busy()))
        self.assertEqual(self.runs, ["switch"])
        self.assertIn("Approved.", self._texts())

    def test_declined_action_does_not_run(self) -> None:
        panel = self._make_panel()
        self.replies.extend([_calls(ToolCall("c1", "switch_mode", {})), _text("Okay, staying here.")])
        panel.send_message("Take me to HR Diagram")
        self.assertTrue(_wait_until(lambda: bool(self._cards())))
        self._cards()[0].decline()
        self.assertTrue(_wait_until(lambda: not panel.is_busy()))
        self.assertEqual(self.runs, [])
        tool_message = self.providers[0].requests[1][-1]
        self.assertEqual(json.loads(tool_message.text)["status"], "declined")

    def test_create_action_saves_into_the_folder_the_user_picked(self) -> None:
        panel = self._make_panel()
        self.replies.extend([_calls(ToolCall("c1", "save_summary", {})), _text("Saved.")])
        panel.send_message("Save a summary")
        self.assertTrue(_wait_until(lambda: bool(self._cards())))
        card = self._cards()[0]
        self.assertIn(f"Save into: {self.root / 'Proposed'}", self._texts())
        chosen = self.root / "Chosen"
        card.set_output_folder(chosen)
        card.approve()
        self.assertTrue(_wait_until(lambda: not panel.is_busy()))
        self.assertEqual((chosen / "summary.txt").read_text(encoding="utf-8"), "42 frames")
        self.assertFalse((self.root / "Proposed").exists())

    def test_create_action_cannot_be_approved_without_a_folder(self) -> None:
        panel = self._make_panel()
        self.replies.extend([_calls(ToolCall("c1", "save_summary", {})), _text("Nothing saved.")])
        panel.send_message("Save a summary")
        self.assertTrue(_wait_until(lambda: bool(self._cards())))
        card = self._cards()[0]
        card.set_output_folder(None)
        self.assertFalse(card._approve_button.isEnabled())
        card.approve()
        self.assertTrue(card.is_pending())
        panel.stop()
        self.assertTrue(_wait_until(lambda: not panel.is_busy()))

    def test_stop_while_waiting_for_approval_ends_the_turn(self) -> None:
        panel = self._make_panel()
        self.replies.extend([_calls(ToolCall("c1", "switch_mode", {}))])
        panel.send_message("Take me to HR Diagram")
        self.assertTrue(_wait_until(lambda: bool(self._cards())))
        panel.stop()
        self.assertTrue(_wait_until(lambda: not panel.is_busy()))
        self.assertEqual(self.runs, [])
        self.assertFalse(self._cards()[0].is_pending())
        self.assertIn("Stopped.", self._texts())

    def test_new_chat_forgets_the_conversation(self) -> None:
        panel = self._make_panel()
        self.replies.extend([_text("First answer."), _text("Second answer.")])
        panel.send_message("first")
        self.assertTrue(_wait_until(lambda: not panel.is_busy()))
        panel.new_chat()
        QApplication.processEvents()
        self.assertNotIn("First answer.", self._texts())
        panel.send_message("second")
        self.assertTrue(_wait_until(lambda: not panel.is_busy()))
        self.assertEqual(len(self.providers), 2)
        self.assertEqual([message.text for message in self.providers[1].requests[0]], ["second"])

    def test_follow_up_keeps_history_and_switches_provider_after_settings_change(self) -> None:
        panel = self._make_panel()
        self.replies.extend([_text("One."), _text("Two.")])
        panel.send_message("first")
        self.assertTrue(_wait_until(lambda: not panel.is_busy()))
        self.settings.ai_models = {"anthropic": "claude-other"}
        panel.send_message("second")
        self.assertTrue(_wait_until(lambda: not panel.is_busy()))
        self.assertEqual(len(self.providers), 2)
        self.assertEqual([message.text for message in self.providers[1].requests[0]], ["first", "One.", "second"])

    def test_source_folders_are_protected_for_the_whole_chat(self) -> None:
        panel = self._make_panel()
        self.replies.append(_text("ok"))
        panel.send_message("hi")
        self.assertTrue(_wait_until(lambda: not panel.is_busy()))
        self.assertTrue(panel._context.writer.is_protected(self.root / "Lights" / "M13"))

    def test_enter_sends_and_shift_enter_adds_a_line(self) -> None:
        panel = self._make_panel()
        self.replies.append(_text("ok"))
        panel._input.setPlainText("line one")
        shift_enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier, "\r")
        panel._input.keyPressEvent(shift_enter)
        self.assertIn("\n", panel._input.toPlainText())
        enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "\r")
        panel._input.keyPressEvent(enter)
        self.assertEqual(panel._input.toPlainText(), "")
        self.assertTrue(_wait_until(lambda: not panel.is_busy()))

    def test_composer_shows_model_and_effort_without_a_send_button(self) -> None:
        panel = self._make_panel()
        self.assertEqual(panel._input.placeholderText(), "Ask AI")
        self.assertFalse(hasattr(panel, "_send_button"))
        self.assertTrue(panel._model_button.text().startswith("claude-test"))
        self.assertTrue(panel._effort_button.text().startswith("Default"))
        self.assertTrue(panel._stop_button.isHidden())

    def test_choosing_effort_is_saved_and_used_for_the_next_answer(self) -> None:
        panel = self._make_panel()
        high = next(action for action in panel._effort_actions.actions() if action.data() == "high")
        high.trigger()
        self.assertEqual(self.settings.ai_reasoning_effort, "high")
        self.assertEqual(self.saved_settings, 1)
        self.assertTrue(panel._effort_button.text().startswith("High"))
        self.assertTrue(high.isChecked())
        self.replies.append(_text("ok"))
        panel.send_message("hi")
        self.assertTrue(_wait_until(lambda: not panel.is_busy()))
        self.assertEqual(self.configs[-1].reasoning_effort, "high")

    def test_model_menu_lists_loaded_models_and_switches(self) -> None:
        panel = self._make_panel()
        panel._rebuild_model_menu()
        texts = [action.text() for action in panel._model_menu.actions() if action.text()]
        self.assertEqual(texts, ["claude-test", "Load model list", "AI Settings..."])
        panel._handle_models_loaded("anthropic", ["claude-a", "claude-b"])
        panel._rebuild_model_menu()
        actions = {action.text(): action for action in panel._model_menu.actions() if action.text()}
        self.assertTrue(actions["claude-test"].isChecked())
        self.assertIn("claude-b", actions)
        actions["claude-b"].trigger()
        self.assertEqual(self.settings.ai_models, {"anthropic": "claude-b"})
        self.assertEqual(self.saved_settings, 1)
        self.assertTrue(panel._model_button.text().startswith("claude-b"))

    def test_model_list_failure_is_shown(self) -> None:
        panel = self._make_panel()
        panel._handle_models_failed("anthropic", "Anthropic rejected the API key.")
        self.assertIn("Anthropic rejected the API key.", self._texts())

    def test_escape_stops_a_running_answer(self) -> None:
        panel = self._make_panel()
        self.replies.extend([_calls(ToolCall("c1", "switch_mode", {}))])
        panel.send_message("Take me to HR Diagram")
        self.assertTrue(_wait_until(lambda: bool(self._cards())))
        self.assertFalse(panel._stop_button.isHidden())
        panel._input.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        self.assertTrue(_wait_until(lambda: not panel.is_busy()))
        self.assertTrue(panel._stop_button.isHidden())

    def test_chat_box_grows_with_its_text_up_to_a_limit(self) -> None:
        panel = self._make_panel()
        one_line = panel._input.height()
        panel._input.setPlainText("a\nb\nc")
        QApplication.processEvents()
        three_lines = panel._input.height()
        self.assertGreater(three_lines, one_line)
        panel._input.setPlainText("\n".join("x" * 20))
        QApplication.processEvents()
        self.assertLess(panel._input.height(), one_line * 8)

    def test_arrow_keys_stay_in_the_chat_box(self) -> None:
        panel = self._make_panel()
        override = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier)
        panel._input.event(override)
        self.assertTrue(override.isAccepted())


if __name__ == "__main__":
    unittest.main()
