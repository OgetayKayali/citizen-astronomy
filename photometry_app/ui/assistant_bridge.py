"""Lets assistant tools, which run on a worker thread, read and steer the main window on the UI thread."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
import threading
from typing import TYPE_CHECKING, TypeVar

from PySide6.QtCore import QObject, Qt, QThread, Signal

from photometry_app.app_metadata import application_install_path
from photometry_app.assistant.program_map import mode_info
from photometry_app.assistant.tools import ToolCancelled, ToolFailure
from photometry_app.core.models import AppMode
from photometry_app.core.observation_deck import ObservationDeckLibrary

if TYPE_CHECKING:
    from photometry_app.ui.astro_tools_panel import ObservationDeckWorkspace
    from photometry_app.ui.main_window import MainWindow


T = TypeVar("T")


@dataclass(slots=True)
class _UiCall:
    function: Callable[[], object]
    done: threading.Event = field(default_factory=threading.Event)
    cancelled: bool = False
    result: object = None
    error: BaseException | None = None


class UiInvoker(QObject):
    _requested = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._requested.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def call(self, function: Callable[[], T], cancel_event: threading.Event | None = None) -> T:
        if QThread.currentThread() is self.thread():
            return function()
        request = _UiCall(function)
        self._requested.emit(request)
        while not request.done.wait(0.1):
            if cancel_event is not None and cancel_event.is_set():
                request.cancelled = True
                raise ToolCancelled("Stopped by the user.")
        if request.error is not None:
            raise request.error
        return request.result  # type: ignore[return-value]

    def _run(self, request: object) -> None:
        if not isinstance(request, _UiCall):
            return
        if request.cancelled:
            request.done.set()
            return
        try:
            request.result = request.function()
        except BaseException as exc:
            request.error = exc
        finally:
            request.done.set()


class MainWindowAssistantBridge:
    def __init__(self, window: "MainWindow") -> None:
        self._window = window
        self._invoker = UiInvoker(window)

    def state(self, cancel_event: threading.Event) -> dict[str, object]:
        return self._invoker.call(self._state, cancel_event)

    def current_mode(self, cancel_event: threading.Event) -> AppMode:
        return self._invoker.call(self._window._current_app_mode, cancel_event)

    def mode_switch_notes(self, mode: AppMode, cancel_event: threading.Event) -> tuple[str, ...]:
        return self._invoker.call(lambda: self._mode_switch_notes(mode), cancel_event)

    def switch_mode(self, mode: AppMode, cancel_event: threading.Event) -> dict[str, object]:
        def switch() -> dict[str, object]:
            self._window._handle_app_mode_changed(mode)
            return self._state()

        return self._invoker.call(switch, cancel_event)

    def deck_library(self, cancel_event: threading.Event) -> ObservationDeckLibrary | None:
        return self._invoker.call(lambda: self._deck().library, cancel_event)

    def deck_busy(self, cancel_event: threading.Event) -> bool:
        return self._invoker.call(lambda: self._deck().is_busy(), cancel_event)

    def open_deck(self, cancel_event: threading.Event) -> None:
        def open_mode() -> None:
            if self._window._current_app_mode() != AppMode.ASTRO_TOOLS:
                self._window._handle_app_mode_changed(AppMode.ASTRO_TOOLS)
            self._deck()

        self._invoker.call(open_mode, cancel_event)

    def add_deck_folder(self, folder: Path, cancel_event: threading.Event) -> None:
        def add() -> None:
            deck = self._deck()
            if deck.is_busy():
                raise ToolFailure("The Observation Deck is busy with another scan.")
            deck.add_master_folder(folder)

        self._invoker.call(add, cancel_event)

    def set_deck_exclusions(self, paths: tuple[Path, ...], excluded: bool, cancel_event: threading.Event) -> int:
        return self._invoker.call(lambda: self._deck().set_images_excluded(paths, excluded=excluded), cancel_event)

    def protected_roots(self) -> list[Path]:
        roots: list[Path] = []
        for text in (self._window._root_path_input.text(), self._window._hr_source_image_input.text()):
            if text.strip():
                path = Path(text.strip()).expanduser()
                roots.append(path if path.is_dir() else path.parent)
        panel = getattr(self._window, "_astro_tools_panel_widget", None)
        library = panel.observation_map_tool.library if panel is not None else None
        if library is not None:
            roots.extend(library.root_paths)
        return roots

    def _deck(self) -> "ObservationDeckWorkspace":
        panel = getattr(self._window, "_astro_tools_panel_widget", None)
        if panel is None:
            panel = self._window._ensure_astro_tools_panel()
        if panel is None:
            raise ToolFailure("The Observation Deck is not available in this window.")
        return panel.observation_map_tool

    @staticmethod
    def catalog_cache_dir() -> Path | None:
        try:
            return application_install_path()
        except Exception:
            return None

    def _state(self) -> dict[str, object]:
        window = self._window
        mode = window._current_app_mode()
        info = mode_info(mode)
        settings = window._settings
        state: dict[str, object] = {
            "mode": info.name if info is not None else str(mode),
            "workspace_folder": window._root_path_input.text().strip(),
            "running_jobs": self._running_jobs(),
            "keeps_results_when_switching_modes": bool(getattr(settings, "keep_mode_state_on_switch", False)),
        }
        if mode == AppMode.HR_DIAGRAM and window._hr_source_image_input.text().strip():
            state["hr_source_image"] = window._hr_source_image_input.text().strip()
        return state

    def _running_jobs(self) -> list[str]:
        running: list[str] = []
        for attr_name in self._window._background_worker_attr_names():
            worker = getattr(self._window, attr_name, None)
            is_running = getattr(worker, "isRunning", None)
            if callable(is_running) and is_running():
                running.append(attr_name.strip("_").removesuffix("_worker").replace("_", " "))
        return running

    def _mode_switch_notes(self, mode: AppMode) -> tuple[str, ...]:
        window = self._window
        current = mode_info(window._current_app_mode())
        target = mode_info(mode)
        notes: list[str] = []
        if current is not None and not bool(getattr(window._settings, "keep_mode_state_on_switch", False)):
            notes.append(f"Anything loaded in {current.name} is cleared when you leave it.")
        running = self._running_jobs()
        if running:
            notes.append(f"Running now: {', '.join(running)}.")
        if target is not None:
            notes.append(f"{target.name} opens {target.opens}.")
        return tuple(notes)
