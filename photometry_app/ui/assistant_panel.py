from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import partial
import math
from pathlib import Path
import threading

from PySide6.QtCore import QEvent, QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QActionGroup, QFont, QKeyEvent, QPalette
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from photometry_app.assistant.config import model_for_provider, provider_config_from_settings
from photometry_app.assistant.conversation import Conversation, ConversationEvent, EventKind, TurnOutcome, TurnStatus
from photometry_app.assistant.credentials import CredentialStoreError
from photometry_app.assistant.file_guard import GuardedFileWriter
from photometry_app.assistant.prompts import build_system_prompt
from photometry_app.assistant.providers import (
    REASONING_EFFORT_LABELS,
    REASONING_EFFORTS,
    ChatProvider,
    ProviderConfig,
    create_provider,
    normalize_provider_id,
    normalize_reasoning_effort,
    provider_info,
    provider_requires_api_key,
)
from photometry_app.assistant.tools import (
    ACCESS_DESCRIPTIONS,
    ActionPlan,
    ApprovalDecision,
    ToolAccess,
    ToolContext,
    ToolDefinition,
    ToolRegistry,
)
from photometry_app.ui.assistant_settings import ModelListWorker


_EFFORT_TOOLTIP = (
    "Reasoning effort: how long the model thinks before answering. Higher can be more careful "
    "but is slower and uses more tokens. Default lets the provider decide."
)
_WELCOME_TEXT = (
    "Ask how to do something in CAst, or ask me to look at a folder of images. "
    "I can read files and settings, but I never change, move, or delete your files."
)
_TOOL_STATUS_LABELS = {
    "ok": "done",
    "error": "failed",
    "declined": "declined",
    "cancelled": "stopped",
}


@dataclass(slots=True)
class ApprovalRequest:
    tool: ToolDefinition
    plan: ActionPlan
    done: threading.Event = field(default_factory=threading.Event)
    decision: ApprovalDecision = field(default_factory=lambda: ApprovalDecision(approved=False))

    def resolve(self, decision: ApprovalDecision) -> None:
        if self.done.is_set():
            return
        self.decision = decision
        self.done.set()


def assistant_setup_problem(settings: object | None) -> str | None:
    if settings is None:
        return "Open a workspace folder first so CAst can load your settings."
    try:
        config = provider_config_from_settings(settings)
    except CredentialStoreError as exc:
        return str(exc)
    name = provider_info(config.provider_id).display_name
    if not config.model:
        return f"Choose a {name} model in Settings > AI Assistant."
    if provider_requires_api_key(config.provider_id, config.base_url) and not (config.api_key or "").strip():
        return f"Add your {name} API key in Settings > AI Assistant."
    return None


class _ConversationRelay(QObject):
    """Carries conversation callbacks from the worker thread to the panel's thread."""

    event_emitted = Signal(int, object)
    approval_requested = Signal(int, object)


class AssistantTurnWorker(QThread):
    turn_finished = Signal(object, object)
    turn_failed = Signal(object, str)

    def __init__(self, conversation: Conversation, text: str) -> None:
        super().__init__()
        self._conversation = conversation
        self._text = text

    def run(self) -> None:
        try:
            outcome = self._conversation.send(self._text)
        except Exception as exc:
            self.turn_failed.emit(self, f"{type(exc).__name__}: {exc}")
            return
        self.turn_finished.emit(self, outcome)

    def request_cancel(self) -> None:
        self._conversation.cancel()


class _ChatInput(QPlainTextEdit):
    submitted = Signal()
    stop_requested = Signal()
    _MAX_LINES = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().documentLayout().documentSizeChanged.connect(self._fit_height)
        self._fit_height()

    def _fit_height(self, *_args: object) -> None:
        # Grows with the text like a chat box, then scrolls.
        needed = max(1, math.ceil(self.document().size().height()))
        lines = min(self._MAX_LINES, needed)
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded if needed > self._MAX_LINES else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        margins = self.contentsMargins()
        height = lines * self.fontMetrics().lineSpacing() + 2 * self.document().documentMargin() + margins.top() + margins.bottom() + 2
        self.setFixedHeight(int(math.ceil(height)))

    def event(self, event: QEvent) -> bool:
        # Arrow and paging keys belong to the text box, not to window-wide navigation shortcuts.
        if event.type() == QEvent.Type.ShortcutOverride and isinstance(event, QKeyEvent):
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown, Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_Escape):
                event.accept()
                return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.submitted.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.stop_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


def _choice_button(tooltip: str) -> QToolButton:
    button = QToolButton()
    button.setObjectName("assistantComposerChoice")
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setToolTip(tooltip)
    return button


def _muted_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
    return label


class _ApprovalCard(QFrame):
    decided = Signal(object)

    def __init__(self, request: ApprovalRequest, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._request = request
        self._output_folder = request.plan.output_folder
        self.setObjectName("assistantApprovalCard")
        self.setStyleSheet(
            "QFrame#assistantApprovalCard { border: 1px solid palette(highlight); border-radius: 6px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        title = QLabel(request.plan.title)
        title_font = QFont(title.font())
        title_font.setBold(True)
        title.setFont(title_font)
        title.setWordWrap(True)
        layout.addWidget(title)
        if request.plan.summary:
            summary = QLabel(request.plan.summary)
            summary.setWordWrap(True)
            summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(summary)
        if request.plan.details:
            details = QLabel("\n".join(f"- {line}" for line in request.plan.details))
            details.setWordWrap(True)
            details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(details)
        layout.addWidget(_muted_label(ACCESS_DESCRIPTIONS[request.tool.access]))

        self._decline_button = QPushButton("Decline")
        self._approve_button = QPushButton("Approve")
        self._decline_button.clicked.connect(lambda: self._decide(False))
        self._approve_button.clicked.connect(lambda: self._decide(True))
        self._folder_label: QLabel | None = None
        self._change_folder_button: QPushButton | None = None
        if request.tool.access == ToolAccess.CREATE:
            folder_row = QHBoxLayout()
            self._folder_label = QLabel()
            self._folder_label.setWordWrap(True)
            self._folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._change_folder_button = QPushButton("Change Folder...")
            self._change_folder_button.clicked.connect(self._choose_folder)
            folder_row.addWidget(self._folder_label, stretch=1)
            folder_row.addWidget(self._change_folder_button)
            layout.addLayout(folder_row)
            self._refresh_folder_label()

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self._decline_button)
        buttons.addWidget(self._approve_button)
        layout.addLayout(buttons)
        self._status_label = _muted_label("")
        self._status_label.hide()
        layout.addWidget(self._status_label)
        self._sync_approve_enabled()

    @property
    def output_folder(self) -> Path | None:
        return self._output_folder

    def set_output_folder(self, folder: Path | None) -> None:
        self._output_folder = folder
        self._refresh_folder_label()

    def approve(self) -> None:
        self._decide(True)

    def decline(self, note: str = "") -> None:
        self._decide(False, note)

    def is_pending(self) -> bool:
        return not self._request.done.is_set()

    def stop(self) -> None:
        if self.is_pending():
            self._request.resolve(ApprovalDecision(approved=False, note="Stopped by the user."))
        self._finish("Stopped.")

    def mark_stopped(self) -> None:
        self._finish("Stopped.")

    def _choose_folder(self) -> None:
        start = str(self._output_folder or Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Choose where to save new files", start)
        if selected:
            self.set_output_folder(Path(selected))

    def _refresh_folder_label(self) -> None:
        if self._folder_label is not None:
            folder = self._output_folder
            self._folder_label.setText(f"Save into: {folder}" if folder else "Save into: choose a folder")
        self._sync_approve_enabled()

    def _sync_approve_enabled(self) -> None:
        needs_folder = self._request.tool.access == ToolAccess.CREATE and self._output_folder is None
        self._approve_button.setEnabled(self.is_pending() and not needs_folder)

    def _decide(self, approved: bool, note: str = "") -> None:
        if not self.is_pending():
            return
        if approved and self._request.tool.access == ToolAccess.CREATE and self._output_folder is None:
            return
        decision = ApprovalDecision(approved=approved, output_folder=self._output_folder if approved else None, note=note)
        self._request.resolve(decision)
        self._finish("Approved." if approved else "Declined.")
        self.decided.emit(decision)

    def _finish(self, status: str) -> None:
        for button in (self._approve_button, self._decline_button, self._change_folder_button):
            if button is not None:
                button.setEnabled(False)
        self._status_label.setText(status)
        self._status_label.show()


class _TranscriptView(QScrollArea):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._content = QWidget()
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setSpacing(8)
        self._layout.addStretch(1)
        self.setWidget(self._content)
        self._follow_bottom = True
        scroll_bar = self.verticalScrollBar()
        scroll_bar.valueChanged.connect(self._remember_position)
        scroll_bar.rangeChanged.connect(self._keep_bottom_visible)

    def add_widget(self, widget: QWidget) -> None:
        self._follow_bottom = True
        self._layout.insertWidget(self._layout.count() - 1, widget)

    def clear(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def widgets(self) -> list[QWidget]:
        return [self._layout.itemAt(index).widget() for index in range(self._layout.count() - 1)]

    def _remember_position(self, value: int) -> None:
        self._follow_bottom = value >= self.verticalScrollBar().maximum() - 4

    def _keep_bottom_visible(self, _minimum: int, maximum: int) -> None:
        if self._follow_bottom:
            self.verticalScrollBar().setValue(maximum)


def _bubble(text: str, *, markdown: bool, object_name: str) -> QFrame:
    frame = QFrame()
    frame.setObjectName(object_name)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(10, 6, 10, 6)
    label = QLabel()
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.MarkdownText if markdown else Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    label.setOpenExternalLinks(True)
    label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    label.setText(text)
    layout.addWidget(label)
    return frame


class AssistantPanel(QWidget):
    open_settings_requested = Signal()

    def __init__(
        self,
        *,
        settings_provider: Callable[[], object | None],
        registry_factory: Callable[[], ToolRegistry] = ToolRegistry,
        state_provider: Callable[[], str] = lambda: "",
        protected_roots_provider: Callable[[], Iterable[Path]] = tuple,
        app: object | None = None,
        provider_factory: Callable[[ProviderConfig], ChatProvider] = create_provider,
        on_settings_changed: Callable[[], None] = lambda: None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings_provider = settings_provider
        self._on_settings_changed = on_settings_changed
        self._registry_factory = registry_factory
        self._state_provider = state_provider
        self._protected_roots_provider = protected_roots_provider
        self._app = app
        self._provider_factory = provider_factory
        self._conversation: Conversation | None = None
        self._context: ToolContext | None = None
        self._provider_config: ProviderConfig | None = None
        self._worker: AssistantTurnWorker | None = None
        self._finished_workers: list[AssistantTurnWorker] = []
        self._pending_cards: list[_ApprovalCard] = []
        self._tool_lines: list[tuple[str, QLabel]] = []
        # Bumped by New Chat so late events from the previous conversation are ignored.
        self._generation = 0

        self._relay = _ConversationRelay(self)
        self._relay.event_emitted.connect(self._handle_event)
        self._relay.approval_requested.connect(self._handle_approval_request)

        self.setStyleSheet(
            "QFrame#assistantUserBubble { background-color: palette(alternate-base); border-radius: 6px; }"
            "QFrame#assistantReplyBubble { border: 1px solid palette(mid); border-radius: 6px; }"
            "QFrame#assistantErrorBubble { border: 1px solid palette(bright-text); border-radius: 6px; }"
            "QFrame#assistantComposer { background-color: palette(base); border: 1px solid palette(mid); border-radius: 16px; }"
            "QFrame#assistantComposer QPlainTextEdit { background: transparent; border: none; }"
            "QToolButton#assistantComposerChoice { border: none; border-radius: 6px; padding: 2px 6px; background: transparent; }"
            "QToolButton#assistantComposerChoice:hover { background-color: palette(alternate-base); }"
            "QToolButton#assistantComposerChoice::menu-indicator { image: none; width: 0px; }"
        )

        self._new_chat_button = QPushButton("New Chat")
        self._new_chat_button.setToolTip("Start over. The current conversation is forgotten.")
        self._new_chat_button.clicked.connect(self.new_chat)
        header = QHBoxLayout()
        header.addStretch(1)
        header.addWidget(self._new_chat_button)

        self._setup_banner = QFrame()
        self._setup_banner.setObjectName("assistantSetupBanner")
        self._setup_banner.setStyleSheet("QFrame#assistantSetupBanner { border: 1px solid palette(highlight); border-radius: 6px; }")
        banner_layout = QVBoxLayout(self._setup_banner)
        self._setup_label = QLabel()
        self._setup_label.setWordWrap(True)
        self._setup_button = QPushButton("Open AI Settings")
        self._setup_button.clicked.connect(self.open_settings_requested.emit)
        banner_layout.addWidget(self._setup_label)
        banner_layout.addWidget(self._setup_button, alignment=Qt.AlignmentFlag.AlignLeft)

        self._transcript = _TranscriptView()

        self._status_label = _muted_label("")
        self._status_label.hide()

        self._input = _ChatInput()
        self._input.setPlaceholderText("Ask AI")
        self._input.setToolTip("Enter sends. Shift+Enter starts a new line. Esc stops a running answer.")
        self._input.setTabChangesFocus(True)
        self._input.submitted.connect(self._submit)
        self._input.stop_requested.connect(self.stop)

        self._model_button = _choice_button("Model used for answers. Click to change it or load the model list.")
        self._model_menu = QMenu(self._model_button)
        self._model_menu.aboutToShow.connect(self._rebuild_model_menu)
        self._model_button.setMenu(self._model_menu)
        self._model_lists: dict[str, list[str]] = {}
        self._loading_models_for: str | None = None

        self._effort_button = _choice_button(_EFFORT_TOOLTIP)
        self._effort_menu = QMenu(self._effort_button)
        self._effort_actions = QActionGroup(self._effort_menu)
        self._effort_actions.setExclusive(True)
        for effort in REASONING_EFFORTS:
            action = self._effort_menu.addAction(REASONING_EFFORT_LABELS[effort])
            action.setCheckable(True)
            action.setData(effort)
            self._effort_actions.addAction(action)
            action.triggered.connect(lambda _checked=False, effort=effort: self._choose_effort(effort))
        self._effort_button.setMenu(self._effort_menu)

        self._stop_button = _choice_button("Stop the current answer and any running step (Esc).")
        self._stop_button.setPopupMode(QToolButton.ToolButtonPopupMode.DelayedPopup)
        self._stop_button.setText("Stop")
        self._stop_button.clicked.connect(self.stop)
        self._stop_button.hide()

        self._composer = QFrame()
        self._composer.setObjectName("assistantComposer")
        composer_layout = QVBoxLayout(self._composer)
        composer_layout.setContentsMargins(12, 8, 8, 6)
        composer_layout.setSpacing(2)
        composer_layout.addWidget(self._input)
        choices_row = QHBoxLayout()
        choices_row.setContentsMargins(0, 0, 0, 0)
        choices_row.setSpacing(2)
        choices_row.addStretch(1)
        choices_row.addWidget(self._model_button)
        choices_row.addWidget(self._effort_button)
        choices_row.addWidget(self._stop_button)
        composer_layout.addLayout(choices_row)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(header)
        layout.addWidget(self._setup_banner)
        layout.addWidget(self._transcript, stretch=1)
        layout.addWidget(self._status_label)
        layout.addWidget(self._composer)

        self._add_reply(_WELCOME_TEXT)
        self.refresh_setup_state()

    def is_busy(self) -> bool:
        return self._worker is not None

    def refresh_setup_state(self) -> None:
        problem = assistant_setup_problem(self._settings_provider())
        self._setup_label.setText(problem or "")
        self._setup_banner.setVisible(problem is not None)
        self._refresh_choices()
        self._sync_controls()

    def _refresh_choices(self) -> None:
        settings = self._settings_provider()
        self._model_button.setEnabled(settings is not None)
        self._effort_button.setEnabled(settings is not None)
        provider_id = normalize_provider_id(getattr(settings, "ai_provider", ""))
        model = model_for_provider(getattr(settings, "ai_models", None), provider_id)
        effort = normalize_reasoning_effort(getattr(settings, "ai_reasoning_effort", ""))
        metrics = self._model_button.fontMetrics()
        label = metrics.elidedText(model, Qt.TextElideMode.ElideMiddle, 180) if model else "Choose model"
        self._model_button.setText(f"{label}  \u25be")
        self._model_button.setToolTip(f"{provider_info(provider_id).display_name}: {model or 'no model chosen'}. Click to change.")
        self._effort_button.setText(f"{REASONING_EFFORT_LABELS[effort]}  \u25be")
        for action in self._effort_actions.actions():
            action.setChecked(action.data() == effort)

    def _rebuild_model_menu(self) -> None:
        menu = self._model_menu
        menu.clear()
        settings = self._settings_provider()
        if settings is None:
            return
        provider_id = normalize_provider_id(getattr(settings, "ai_provider", ""))
        current = model_for_provider(getattr(settings, "ai_models", None), provider_id)
        names = list(self._model_lists.get(provider_id, []))
        if current and current not in names:
            names.insert(0, current)
        group = QActionGroup(menu)
        group.setExclusive(True)
        for name in names:
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(name == current)
            group.addAction(action)
            action.triggered.connect(lambda _checked=False, name=name: self._choose_model(name))
        if not names:
            menu.addAction("No model chosen yet").setEnabled(False)
        menu.addSeparator()
        loading = self._loading_models_for == provider_id
        load_action = menu.addAction("Loading models..." if loading else "Load model list")
        load_action.setEnabled(not loading)
        load_action.triggered.connect(self._load_model_list)
        menu.addAction("AI Settings...").triggered.connect(self.open_settings_requested.emit)

    def _choose_model(self, name: str) -> None:
        settings = self._settings_provider()
        if settings is None:
            return
        provider_id = normalize_provider_id(getattr(settings, "ai_provider", ""))
        models = dict(getattr(settings, "ai_models", None) or {})
        models[provider_id] = name
        settings.ai_models = models
        self._settings_changed()

    def _choose_effort(self, effort: str) -> None:
        settings = self._settings_provider()
        if settings is None:
            return
        settings.ai_reasoning_effort = normalize_reasoning_effort(effort)
        self._settings_changed()

    def _settings_changed(self) -> None:
        self._on_settings_changed()
        self.refresh_setup_state()

    def _load_model_list(self) -> None:
        settings = self._settings_provider()
        if settings is None or self._loading_models_for is not None:
            return
        try:
            config = provider_config_from_settings(settings)
        except CredentialStoreError as exc:
            self._add_error(str(exc))
            return
        worker = ModelListWorker(config)
        worker.models_loaded.connect(self._handle_models_loaded)
        worker.load_failed.connect(self._handle_models_failed)
        self._loading_models_for = config.provider_id
        self._set_status("Loading models...")
        worker.start_kept_alive()

    def _handle_models_loaded(self, provider_id: str, models: object) -> None:
        self._loading_models_for = None
        self._set_status("Working..." if self.is_busy() else "")
        names = [str(name) for name in models] if isinstance(models, list) else []
        self._model_lists[provider_id] = names
        if not names:
            self._add_error("The provider returned no models for this key.")
        elif self.isVisible():
            QTimer.singleShot(0, self._model_button.showMenu)

    def _handle_models_failed(self, provider_id: str, message: str) -> None:
        self._loading_models_for = None
        self._set_status("Working..." if self.is_busy() else "")
        self._add_error(message)

    def send_message(self, text: str) -> bool:
        text = text.strip()
        if not text or self.is_busy():
            return False
        settings = self._settings_provider()
        problem = assistant_setup_problem(settings)
        if problem is not None:
            self.refresh_setup_state()
            self._add_error(problem)
            return False
        try:
            conversation = self._prepare_conversation(settings)
        except Exception as exc:
            self._add_error(f"Could not start the assistant: {exc}")
            return False
        self._add_user(text)
        worker = AssistantTurnWorker(conversation, text)
        worker.turn_finished.connect(self._handle_turn_finished)
        worker.turn_failed.connect(self._handle_turn_failed)
        self._worker = worker
        self._set_status("Working...")
        self._sync_controls()
        worker.start()
        return True

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
            self._set_status("Stopping...")
        for card in list(self._pending_cards):
            card.stop()
        self._pending_cards.clear()

    def new_chat(self) -> None:
        if self.is_busy():
            self.stop()
            self._retire_worker(wait_ms=2000)
        self._generation += 1
        self._conversation = None
        self._context = None
        self._provider_config = None
        self._pending_cards.clear()
        self._tool_lines.clear()
        self._transcript.clear()
        self._add_reply(_WELCOME_TEXT)
        self._set_status("")
        self.refresh_setup_state()

    def shutdown(self, wait_ms: int = 2000) -> None:
        self.stop()
        self._retire_worker(wait_ms=wait_ms, terminate=True)

    def _prepare_conversation(self, settings: object) -> Conversation:
        config = provider_config_from_settings(settings)
        state = self._state_provider()
        if self._conversation is None or self._context is None:
            writer = GuardedFileWriter(protected_roots=list(self._protected_roots_provider()))
            self._context = ToolContext(writer=writer, app=self._app)
            generation = self._generation
            self._conversation = Conversation(
                self._provider_factory(config),
                self._registry_factory(),
                self._context,
                system_prompt=build_system_prompt(state),
                approver=partial(self._approve, generation, self._context.cancel_event),
                on_event=partial(self._relay.event_emitted.emit, generation),
            )
            self._provider_config = config
            return self._conversation
        for root in self._protected_roots_provider():
            self._context.writer.protect(root)
        if config != self._provider_config:
            self._conversation.set_provider(self._provider_factory(config))
            self._provider_config = config
        self._conversation.set_system_prompt(build_system_prompt(state))
        return self._conversation

    def _approve(self, generation: int, cancel_event: threading.Event, tool: ToolDefinition, plan: ActionPlan) -> ApprovalDecision:
        # Runs on the worker thread: hand the request to the UI and wait for a button press or Stop.
        request = ApprovalRequest(tool=tool, plan=plan)
        self._relay.approval_requested.emit(generation, request)
        while not request.done.wait(0.1):
            if cancel_event.is_set():
                request.resolve(ApprovalDecision(approved=False, note="Stopped by the user."))
        return request.decision

    def _handle_approval_request(self, generation: int, request: object) -> None:
        if generation != self._generation or not isinstance(request, ApprovalRequest) or request.done.is_set():
            return
        card = _ApprovalCard(request)
        card.decided.connect(lambda _decision, card=card: self._forget_card(card))
        self._pending_cards.append(card)
        self._transcript.add_widget(card)
        self._set_status("Waiting for your approval...")

    def _forget_card(self, card: _ApprovalCard) -> None:
        if card in self._pending_cards:
            self._pending_cards.remove(card)
        if self.is_busy():
            self._set_status("Working...")

    def _handle_event(self, generation: int, event: object) -> None:
        if generation != self._generation or not isinstance(event, ConversationEvent):
            return
        if event.kind == EventKind.ASSISTANT_TEXT:
            self._add_reply(event.text)
        elif event.kind == EventKind.TOOL_STARTED:
            label = _muted_label(f"{self._activity_label(event.tool_name)}...")
            self._tool_lines.append((event.tool_name, label))
            self._transcript.add_widget(label)
        elif event.kind == EventKind.TOOL_FINISHED:
            for index in range(len(self._tool_lines) - 1, -1, -1):
                name, label = self._tool_lines[index]
                if name == event.tool_name:
                    status = _TOOL_STATUS_LABELS.get(event.status, event.status)
                    label.setText(f"{self._activity_label(name)}: {status}")
                    del self._tool_lines[index]
                    break
        elif event.kind == EventKind.RETRYING:
            self._set_status("The provider had a problem. Trying once more...")

    def _activity_label(self, tool_name: str) -> str:
        tool = self._conversation.registry.get(tool_name) if self._conversation is not None else None
        return tool.activity_label if tool is not None else tool_name.replace("_", " ").capitalize()

    def _handle_turn_finished(self, worker: object, outcome: object) -> None:
        if worker is not self._worker:
            return
        self._retire_worker()
        self._set_status("")
        if not isinstance(outcome, TurnOutcome):
            return
        if outcome.status == TurnStatus.ERROR:
            if outcome.text:
                self._add_reply(outcome.text)
            self._add_error(outcome.error)
        elif outcome.status == TurnStatus.CANCELLED:
            self._add_note("Stopped.")
        elif outcome.status == TurnStatus.STEP_LIMIT:
            self._add_note(outcome.text)
        elif outcome.status == TurnStatus.COMPLETED and not outcome.text and not outcome.tool_results:
            self._add_note("The provider returned an empty answer.")
        if outcome.truncated:
            self._add_note("The answer was cut off at the provider's length limit.")
        self._mark_leftover_cards_stopped()
        self._sync_controls()

    def _handle_turn_failed(self, worker: object, message: str) -> None:
        if worker is not self._worker:
            return
        self._retire_worker()
        self._set_status("")
        self._add_error(f"The assistant stopped unexpectedly: {message}")
        self._mark_leftover_cards_stopped()
        self._sync_controls()

    def _mark_leftover_cards_stopped(self) -> None:
        for card in self._pending_cards:
            card.mark_stopped()
        self._pending_cards.clear()

    def _retire_worker(self, *, wait_ms: int = 0, terminate: bool = False) -> None:
        worker = self._worker
        self._worker = None
        if worker is None:
            return
        if wait_ms > 0 and worker.isRunning():
            worker.wait(wait_ms)
            if terminate and worker.isRunning():
                worker.terminate()
                worker.wait(1000)
        # A QThread must stay referenced until it has fully stopped.
        self._finished_workers = [item for item in self._finished_workers if item.isRunning()]
        if worker.isRunning():
            self._finished_workers.append(worker)
            worker.blockSignals(True)

    def _submit(self) -> None:
        text = self._input.toPlainText()
        if self.send_message(text):
            self._input.clear()

    def _sync_controls(self) -> None:
        busy = self.is_busy()
        self._stop_button.setVisible(busy)
        self._new_chat_button.setEnabled(True)

    def _set_status(self, text: str) -> None:
        self._status_label.setText(text)
        self._status_label.setVisible(bool(text))

    def _add_user(self, text: str) -> None:
        self._transcript.add_widget(_bubble(text, markdown=False, object_name="assistantUserBubble"))

    def _add_reply(self, text: str) -> None:
        self._transcript.add_widget(_bubble(text, markdown=True, object_name="assistantReplyBubble"))

    def _add_error(self, text: str) -> None:
        self._transcript.add_widget(_bubble(text, markdown=False, object_name="assistantErrorBubble"))

    def _add_note(self, text: str) -> None:
        self._transcript.add_widget(_muted_label(text))

    def focus_input(self) -> None:
        QTimer.singleShot(0, self._input.setFocus)
