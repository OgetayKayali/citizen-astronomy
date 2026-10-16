from __future__ import annotations

import json
import os
import threading
import time
import unittest

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from photometry_app.assistant.app_tools import app_tools
from photometry_app.assistant.file_guard import GuardedFileWriter
from photometry_app.assistant.messages import ToolCall
from photometry_app.assistant.tools import ApprovalDecision, ToolCancelled, ToolContext, ToolRegistry, execute_tool_call
from photometry_app.core.models import AppMode
from photometry_app.ui.assistant_bridge import UiInvoker

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class FakeBridge:
    def __init__(self) -> None:
        self.mode = AppMode.SKY_EXPLORER
        self.switched: list[AppMode] = []

    def state(self, cancel_event: threading.Event) -> dict[str, object]:
        return {"mode": self.mode.value, "workspace_folder": "D:/Astro"}

    def current_mode(self, cancel_event: threading.Event) -> AppMode:
        return self.mode

    def mode_switch_notes(self, mode: AppMode, cancel_event: threading.Event) -> tuple[str, ...]:
        return ("Anything loaded in Sky Explorer is cleared when you leave it.",)

    def switch_mode(self, mode: AppMode, cancel_event: threading.Event) -> dict[str, object]:
        self.switched.append(mode)
        self.mode = mode
        return self.state(cancel_event)


class AppToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = FakeBridge()
        self.registry = ToolRegistry(app_tools(self.bridge))
        self.context = ToolContext(writer=GuardedFileWriter())
        self.plans: list[object] = []

    def _run(self, name: str, arguments: dict[str, object], approve: bool | None = True) -> tuple[str, dict[str, object]]:
        def approver(tool, plan):
            self.plans.append(plan)
            return ApprovalDecision(approved=bool(approve))

        result = execute_tool_call(self.registry, self.context, ToolCall("c1", name, arguments), approver if approve is not None else None)
        return result.status, json.loads(result.content)

    def test_state_is_read_without_approval(self) -> None:
        status, payload = self._run("get_cast_state", {}, approve=None)
        self.assertEqual((status, payload["mode"]), ("ok", "sky_explorer"))

    def test_switch_mode_asks_first_and_warns_about_cleared_results(self) -> None:
        status, payload = self._run("switch_mode", {"mode": "HR Diagram"})
        self.assertEqual(status, "ok")
        self.assertEqual(self.bridge.switched, [AppMode.HR_DIAGRAM])
        plan = self.plans[0]
        self.assertEqual(plan.title, "Switch to HR Diagram")
        self.assertIn("cleared when you leave it", plan.details[0])

    def test_declined_or_unapprovable_switch_does_nothing(self) -> None:
        status, _payload = self._run("switch_mode", {"mode": "HR Diagram"}, approve=False)
        self.assertEqual(status, "declined")
        status, _payload = self._run("switch_mode", {"mode": "HR Diagram"}, approve=None)
        self.assertEqual(status, "declined")
        self.assertEqual(self.bridge.switched, [])

    def test_switching_to_the_open_mode_or_an_unknown_mode_fails(self) -> None:
        status, payload = self._run("switch_mode", {"mode": "Sky Explorer"})
        self.assertEqual(status, "error")
        self.assertIn("already in Sky Explorer", payload["message"])
        status, payload = self._run("switch_mode", {"mode": "Spectroscopy"})
        self.assertEqual(status, "error")
        self.assertEqual(self.plans, [])


class _Caller(QThread):
    def __init__(self, invoker: UiInvoker, function, cancel_event=None) -> None:
        super().__init__()
        self._invoker = invoker
        self._function = function
        self._cancel_event = cancel_event
        self.result: object = None
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.result = self._invoker.call(self._function, self._cancel_event)
        except BaseException as exc:
            self.error = exc


class UiInvokerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _wait(self, thread: QThread) -> None:
        deadline = time.monotonic() + 5
        while thread.isRunning() and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
        thread.wait(1000)

    def test_worker_calls_run_on_the_ui_thread(self) -> None:
        invoker = UiInvoker()
        ui_thread = QThread.currentThread()
        caller = _Caller(invoker, lambda: QThread.currentThread() is ui_thread)
        caller.start()
        self._wait(caller)
        self.assertIs(caller.result, True)

    def test_errors_come_back_to_the_worker(self) -> None:
        invoker = UiInvoker()
        caller = _Caller(invoker, lambda: 1 / 0)
        caller.start()
        self._wait(caller)
        self.assertIsInstance(caller.error, ZeroDivisionError)

    def test_stop_ends_the_wait(self) -> None:
        invoker = UiInvoker()
        cancel = threading.Event()
        cancel.set()
        ran: list[bool] = []
        caller = _Caller(invoker, lambda: ran.append(True), cancel)
        caller.start()
        caller.wait(2000)
        QApplication.processEvents()
        self.assertIsInstance(caller.error, ToolCancelled)
        self.assertEqual(ran, [])

    def test_ui_thread_calls_run_directly(self) -> None:
        self.assertEqual(UiInvoker().call(lambda: 7), 7)


if __name__ == "__main__":
    unittest.main()
