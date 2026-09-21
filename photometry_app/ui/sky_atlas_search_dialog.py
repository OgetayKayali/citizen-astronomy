from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from photometry_app.core.sky_atlas import SkyAtlasObject, format_sky_atlas_search_result_title


class _SearchResultBridge(QObject):
    completed = Signal(int, str, object)


class SkyAtlasSearchDialog(QDialog):
    """Centered Sky Atlas search palette with ranked results from every catalog."""

    objectChosen = Signal(object)

    def __init__(
        self,
        search_callback: Callable[[str], tuple[SkyAtlasObject, ...]],
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._search_callback = search_callback
        self._results: tuple[SkyAtlasObject, ...] = ()
        self._applied_query = ""
        self._search_token = 0
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(90)
        self._debounce.timeout.connect(self._dispatch_search)
        self._bridge = _SearchResultBridge(self)
        self._bridge.completed.connect(self._on_search_completed, Qt.ConnectionType.QueuedConnection)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sky-atlas-search")
        self.setObjectName("skyAtlasSearchDialog")
        self.setWindowTitle("Search the Sky")
        self.setModal(True)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(620, 560)

        shell = QFrame(self)
        shell.setObjectName("skyAtlasSearchShell")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(shell)

        layout = QVBoxLayout(shell)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        eyebrow = QLabel("SKY ATLAS")
        eyebrow.setObjectName("skyAtlasSearchEyebrow")
        title = QLabel("Search the sky")
        title.setObjectName("skyAtlasSearchTitle")
        title_font = QFont(title.font())
        title_font.setPointSize(18)
        title_font.setWeight(QFont.Weight.DemiBold)
        title.setFont(title_font)
        subtitle = QLabel("Every catalog is included, even layers you have turned off.")
        subtitle.setObjectName("skyAtlasSearchSubtitle")
        subtitle.setWordWrap(True)

        self._search_input = QLineEdit()
        self._search_input.setObjectName("skyAtlasSearchInput")
        self._search_input.setPlaceholderText("Type a name, catalog ID, or alias…")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self._on_query_changed)
        self._search_input.returnPressed.connect(self._accept_current)

        self._status_label = QLabel("Start typing to search stars, nebulae, galaxies, and more.")
        self._status_label.setObjectName("skyAtlasSearchStatus")
        self._status_label.setWordWrap(True)

        self._results_list = QListWidget()
        self._results_list.setObjectName("skyAtlasSearchResults")
        self._results_list.setUniformItemSizes(True)
        self._results_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._results_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._results_list.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._apply_results_palette()
        self._results_list.itemActivated.connect(self._accept_item)
        self._results_list.itemClicked.connect(self._accept_item)

        hint = QLabel("Enter to go there  ·  Esc to close")
        hint.setObjectName("skyAtlasSearchHint")

        layout.addWidget(eyebrow)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(self._search_input)
        layout.addWidget(self._status_label)
        layout.addWidget(self._results_list, stretch=1)
        layout.addWidget(hint)

        self.setStyleSheet(
            """
            QFrame#skyAtlasSearchShell {
                background-color: rgba(6, 10, 22, 246);
                border: 1px solid rgba(186, 210, 255, 48);
                border-radius: 22px;
            }
            QLabel#skyAtlasSearchEyebrow {
                color: rgba(186, 230, 253, 0.78);
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 2px;
            }
            QLabel#skyAtlasSearchTitle {
                color: #f8fbff;
            }
            QLabel#skyAtlasSearchSubtitle,
            QLabel#skyAtlasSearchStatus,
            QLabel#skyAtlasSearchHint {
                color: rgba(191, 219, 254, 0.72);
            }
            QLabel#skyAtlasSearchHint {
                font-size: 11px;
            }
            QLineEdit#skyAtlasSearchInput {
                background-color: rgba(15, 23, 42, 230);
                border: 1px solid rgba(125, 211, 252, 70);
                border-radius: 12px;
                color: #f8fafc;
                font-size: 15px;
                min-height: 40px;
                padding: 8px 14px;
                selection-background-color: #38bdf8;
                selection-color: #082f49;
            }
            QLineEdit#skyAtlasSearchInput:focus {
                border: 1px solid rgba(125, 211, 252, 180);
            }
            QListWidget#skyAtlasSearchResults {
                background-color: rgba(8, 13, 28, 180);
                border: 1px solid rgba(148, 163, 184, 36);
                border-radius: 14px;
                color: #e2e8f0;
                outline: none;
                padding: 6px;
                selection-background-color: #0c4a6e;
                selection-color: #f8fbff;
            }
            QListWidget#skyAtlasSearchResults::item {
                border: 1px solid transparent;
                border-radius: 10px;
                color: #e2e8f0;
                min-height: 52px;
                padding: 8px 10px;
            }
            QListWidget#skyAtlasSearchResults::item:hover {
                background-color: rgba(148, 163, 184, 28);
                color: #f8fbff;
            }
            QListWidget#skyAtlasSearchResults::item:selected,
            QListWidget#skyAtlasSearchResults::item:selected:active,
            QListWidget#skyAtlasSearchResults::item:selected:!active,
            QListWidget#skyAtlasSearchResults::item:selected:hover {
                background-color: #0c4a6e;
                border: 1px solid rgba(125, 211, 252, 170);
                color: #f8fbff;
            }
            """
        )

    def _apply_results_palette(self) -> None:
        palette = self._results_list.palette()
        ink = QColor("#0c4a6e")
        text = QColor("#f8fbff")
        muted = QColor("#e2e8f0")
        palette.setColor(QPalette.ColorRole.Text, muted)
        palette.setColor(QPalette.ColorRole.HighlightedText, text)
        palette.setColor(QPalette.ColorRole.Highlight, ink)
        palette.setColor(QPalette.ColorGroup.Inactive, QPalette.ColorRole.HighlightedText, text)
        palette.setColor(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Highlight, ink)
        self._results_list.setPalette(palette)

    @property
    def search_input(self) -> QLineEdit:
        return self._search_input

    @property
    def results(self) -> tuple[SkyAtlasObject, ...]:
        return self._results

    def selected_object(self) -> SkyAtlasObject | None:
        item = self._results_list.currentItem()
        if item is None:
            return self._results[0] if self._results else None
        payload = item.data(Qt.ItemDataRole.UserRole)
        return payload if isinstance(payload, SkyAtlasObject) else None

    def set_query(self, query: str) -> None:
        self._debounce.stop()
        self._search_input.blockSignals(True)
        self._search_input.setText(query)
        self._search_input.blockSignals(False)
        stripped = str(query or "").strip()
        self._search_token += 1
        if not stripped:
            self._show_empty_prompt()
            return
        self._apply_results(stripped, tuple(self._search_callback(stripped)))

    def center_on_widget(self, widget: QWidget) -> None:
        host = widget.window() if widget is not None else None
        frame = host.frameGeometry() if host is not None else widget.frameGeometry()
        dialog_size = self.frameGeometry().size()
        if dialog_size.width() < 200 or dialog_size.height() < 200:
            dialog_size = self.size()
        top_left = frame.center() - self.rect().center()
        self.move(top_left)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            self.center_on_widget(parent)
        self._search_input.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self._search_input.selectAll()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self._search_input.hasFocus():
            if self._results_list.count() > 0:
                self._results_list.setFocus(Qt.FocusReason.TabFocusReason)
                if self._results_list.currentRow() < 0:
                    self._results_list.setCurrentRow(0)
            return
        super().keyPressEvent(event)

    def _show_empty_prompt(self) -> None:
        self._results = ()
        self._applied_query = ""
        self._results_list.clear()
        self._status_label.setText("Start typing to search stars, nebulae, galaxies, and more.")

    def _on_query_changed(self, query: str) -> None:
        stripped = str(query or "").strip()
        self._debounce.stop()
        if not stripped:
            self._search_token += 1
            self._show_empty_prompt()
            return
        self._results = ()
        self._applied_query = ""
        self._results_list.clear()
        self._status_label.setText("Searching…")
        self._debounce.start()

    def _dispatch_search(self) -> None:
        query = str(self._search_input.text() or "").strip()
        if not query:
            self._show_empty_prompt()
            return
        self._search_token += 1
        token = self._search_token
        callback = self._search_callback
        bridge = self._bridge

        def _run() -> None:
            try:
                results = tuple(callback(query))
            except Exception:
                results = ()
            bridge.completed.emit(token, query, results)

        self._executor.submit(_run)

    def _on_search_completed(self, token: int, query: str, results: object) -> None:
        if token != self._search_token:
            return
        if str(query or "").strip() != str(self._search_input.text() or "").strip():
            return
        payload = results if isinstance(results, tuple) else ()
        self._apply_results(str(query), payload)

    def _apply_results(self, query: str, results: tuple[SkyAtlasObject, ...]) -> None:
        self._applied_query = str(query or "").strip()
        self._results = tuple(results)
        self._results_list.clear()
        if not self._results:
            self._status_label.setText(f"No objects match “{self._applied_query}”.")
            return
        count = len(self._results)
        self._status_label.setText(f"{count} match{'es' if count != 1 else ''}  ·  most relevant first")
        for sky_object in self._results:
            item = QListWidgetItem(self._format_result_text(sky_object))
            item.setData(Qt.ItemDataRole.UserRole, sky_object)
            self._results_list.addItem(item)
        self._results_list.setCurrentRow(0)

    def _format_result_text(self, sky_object: SkyAtlasObject) -> str:
        title = format_sky_atlas_search_result_title(sky_object)
        details: list[str] = []
        catalog = str(sky_object.catalog or "").strip()
        if catalog:
            details.append(catalog)
        if sky_object.magnitude is not None:
            details.append(f"{float(sky_object.magnitude):.2f} mag")
        constellation = str(sky_object.constellation or "").strip()
        if constellation:
            details.append(constellation)
        if not details:
            return title
        return f"{title}\n{' · '.join(details)}"

    def _accept_item(self, item: QListWidgetItem | None = None) -> None:
        payload = None if item is None else item.data(Qt.ItemDataRole.UserRole)
        sky_object = payload if isinstance(payload, SkyAtlasObject) else self.selected_object()
        if sky_object is None:
            return
        self.objectChosen.emit(sky_object)
        self.accept()

    def _accept_current(self) -> None:
        query = str(self._search_input.text() or "").strip()
        if query and self._applied_query != query:
            self._debounce.stop()
            self._search_token += 1
            try:
                self._apply_results(query, tuple(self._search_callback(query)))
            except Exception:
                self._apply_results(query, ())
        self._accept_item(self._results_list.currentItem())

    def done(self, result: int) -> None:  # type: ignore[override]
        self._debounce.stop()
        self._search_token += 1
        super().done(result)
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self._executor.shutdown(wait=False)
