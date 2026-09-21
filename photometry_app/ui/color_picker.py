"""Themed popover color picker for CAst annotation swatches."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QIcon,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

PRESET_COLORS: tuple[str, ...] = (
    "#f8fafc",
    "#94a3b8",
    "#38bdf8",
    "#3d8bfd",
    "#22c55e",
    "#eab308",
    "#f97316",
    "#ef4444",
    "#a855f7",
    "#111827",
)

_RECENT_COLOR_LIMIT = 8
_recent_colors: list[str] = []

_NONE_TOKEN = "none"


def normalize_hex_color(value: str | None, *, default: str | None = None) -> str | None:
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text or text in {_NONE_TOKEN, "transparent"}:
        return None
    if not text.startswith("#"):
        text = f"#{text}"
    if len(text) == 4 and all(char in "0123456789abcdef" for char in text[1:]):
        text = f"#{text[1] * 2}{text[2] * 2}{text[3] * 2}"
    color = QColor(text)
    if not color.isValid():
        return default
    return color.name().lower()


def recent_colors() -> tuple[str, ...]:
    return tuple(_recent_colors)


def remember_recent_color(value: str | None) -> None:
    color = normalize_hex_color(value)
    if color is None:
        return
    if color in _recent_colors:
        _recent_colors.remove(color)
    _recent_colors.insert(0, color)
    del _recent_colors[_RECENT_COLOR_LIMIT:]


def clear_recent_colors() -> None:
    _recent_colors.clear()


def checkerboard_pixmap(size: int, *, light: str = "#64748b", dark: str = "#1e293b") -> QPixmap:
    pixmap = QPixmap(max(8, int(size)), max(8, int(size)))
    tile = 4
    light_color = QColor(light)
    dark_color = QColor(dark)
    painter = QPainter(pixmap)
    for y in range(0, pixmap.height(), tile):
        for x in range(0, pixmap.width(), tile):
            painter.fillRect(x, y, tile, tile, light_color if ((x // tile) + (y // tile)) % 2 == 0 else dark_color)
    painter.end()
    return pixmap


def apply_swatch_button(
    button: QPushButton,
    color: str | None,
    *,
    border: str = "rgba(15, 23, 42, 0.45)",
    none_border: str | None = None,
) -> None:
    resolved = normalize_hex_color(color)
    button.setText("")
    button.setIconSize(QSize(14, 14))
    if resolved is None:
        button.setIcon(QIcon(checkerboard_pixmap(18)))
        button.setStyleSheet(
            "QPushButton {"
            "background-color: transparent;"
            f"border: 1px dashed {none_border or border};"
            "border-radius: 6px;"
            "padding: 0;"
            "}"
        )
        return
    button.setIcon(QIcon())
    button.setStyleSheet(
        "QPushButton {"
        f"background-color: {resolved};"
        f"border: 1px solid {border};"
        "border-radius: 6px;"
        "padding: 0;"
        "}"
    )


def _hsv_from_color(color: QColor, fallback_hue: float) -> tuple[float, float, float]:
    hue, saturation, value, _alpha = color.getHsvF()
    if hue < 0:
        hue = fallback_hue
    return float(hue), float(saturation), float(value)


class _SaturationValueSquare(QWidget):
    valuesChanged = Signal(float, float)
    interactionFinished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hue = 0.55
        self._saturation = 0.7
        self._value = 0.85
        self.setFixedSize(220, 132)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_hsv(self, hue: float, saturation: float, value: float) -> None:
        self._hue = max(0.0, min(1.0, float(hue)))
        self._saturation = max(0.0, min(1.0, float(saturation)))
        self._value = max(0.0, min(1.0, float(value)))
        self.update()

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 8.0, 8.0)
        painter.setClipPath(path)
        hue_color = QColor.fromHsvF(self._hue, 1.0, 1.0)
        horizontal = QLinearGradient(rect.topLeft(), rect.topRight())
        horizontal.setColorAt(0.0, QColor("#ffffff"))
        horizontal.setColorAt(1.0, hue_color)
        painter.fillRect(rect, horizontal)
        vertical = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        vertical.setColorAt(0.0, QColor(0, 0, 0, 0))
        vertical.setColorAt(1.0, QColor(0, 0, 0, 255))
        painter.fillRect(rect, vertical)
        marker = QPoint(
            int(round(rect.left() + self._saturation * rect.width())),
            int(round(rect.top() + (1.0 - self._value) * rect.height())),
        )
        painter.setClipping(False)
        painter.setPen(QPen(QColor("#0f172a"), 3.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(marker, 6, 6)
        painter.setPen(QPen(QColor("#f8fafc"), 1.4))
        painter.drawEllipse(marker, 6, 6)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._emit_from_position(event.position().x(), event.position().y())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._emit_from_position(event.position().x(), event.position().y())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.interactionFinished.emit()

    def _emit_from_position(self, x: float, y: float) -> None:
        width = max(1.0, float(self.width() - 1))
        height = max(1.0, float(self.height() - 1))
        saturation = max(0.0, min(1.0, float(x) / width))
        value = max(0.0, min(1.0, 1.0 - (float(y) / height)))
        self.set_hsv(self._hue, saturation, value)
        self.valuesChanged.emit(saturation, value)


class _HueBar(QWidget):
    hueChanged = Signal(float)
    interactionFinished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hue = 0.55
        self.setFixedSize(220, 14)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_hue(self, hue: float) -> None:
        self._hue = max(0.0, min(1.0, float(hue)))
        self.update()

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 7.0, 7.0)
        painter.setClipPath(path)
        gradient = QLinearGradient(rect.topLeft(), rect.topRight())
        for stop in range(7):
            fraction = stop / 6.0
            gradient.setColorAt(fraction, QColor.fromHsvF(fraction, 1.0, 1.0))
        painter.fillRect(rect, gradient)
        marker_x = rect.left() + self._hue * rect.width()
        painter.setClipping(False)
        painter.setPen(QPen(QColor("#f8fafc"), 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(marker_x - 4.0, rect.top() - 1.0, 8.0, rect.height() + 2.0), 3.0, 3.0)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._emit_from_position(event.position().x())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._emit_from_position(event.position().x())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.interactionFinished.emit()

    def _emit_from_position(self, x: float) -> None:
        hue = max(0.0, min(1.0, float(x) / max(1.0, float(self.width() - 1))))
        self.set_hue(hue)
        self.hueChanged.emit(hue)


class ColorPickerPopup(QFrame):
    colorChanged = Signal(object)
    colorCommitted = Signal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        allow_none: bool = False,
        theme_colors: dict[str, str] | None = None,
        presets: Sequence[str] | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("castColorPickerPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._allow_none = bool(allow_none)
        self._none_selected = False
        self._hue = 0.55
        self._saturation = 0.72
        self._value = 0.85
        self._updating = False
        self._presets = tuple(color for color in (normalize_hex_color(item) for item in (presets or PRESET_COLORS)) if color)
        colors = theme_colors or {}
        panel = colors.get("panel_bg", "#1e293b")
        border = colors.get("gridline", "#334155")
        text = colors.get("text", "#f8fafc")
        accent = colors.get("accent", "#3d8bfd")
        field_bg = QColor(panel).lighter(112).name().lower()
        self._theme_border = border
        self.setStyleSheet(
            "QFrame#castColorPickerPopup {"
            f"background-color: {panel};"
            f"border: 1px solid {border};"
            "border-radius: 10px;"
            "}"
            "QLabel { background: transparent; border: none; }"
            f"QLabel#castColorPickerCaption {{ color: {text}; font-size: 10px; font-weight: 700; letter-spacing: 0.06em; }}"
            "QLineEdit#castColorPickerHex {"
            f"background-color: {field_bg};"
            f"color: {text};"
            f"border: 1px solid {border};"
            "border-radius: 6px;"
            "padding: 4px 8px;"
            f"selection-background-color: {accent};"
            "}"
        )

        self._square = _SaturationValueSquare(self)
        self._hue_bar = _HueBar(self)
        self._hex_input = QLineEdit(self)
        self._hex_input.setObjectName("castColorPickerHex")
        self._hex_input.setMaxLength(7)
        self._preview = QLabel(self)
        self._preview.setFixedSize(28, 28)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._none_button: QPushButton | None = None
        self._preset_buttons: list[QPushButton] = []
        self._recent_buttons: list[QPushButton] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        root.addWidget(self._square, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addWidget(self._hue_bar, 0, Qt.AlignmentFlag.AlignHCenter)

        hex_row = QHBoxLayout()
        hex_row.setSpacing(8)
        hex_label = QLabel("HEX")
        hex_label.setObjectName("castColorPickerCaption")
        hex_row.addWidget(hex_label)
        hex_row.addWidget(self._hex_input, 1)
        hex_row.addWidget(self._preview)
        root.addLayout(hex_row)

        swatch_caption = QLabel("SWATCHES")
        swatch_caption.setObjectName("castColorPickerCaption")
        root.addWidget(swatch_caption)
        swatch_grid = QGridLayout()
        swatch_grid.setHorizontalSpacing(6)
        swatch_grid.setVerticalSpacing(6)
        column = 0
        if self._allow_none:
            self._none_button = self._make_swatch_button(None)
            self._none_button.setToolTip("No fill")
            swatch_grid.addWidget(self._none_button, 0, 0)
            column = 1
        for index, color in enumerate(self._presets):
            button = self._make_swatch_button(color)
            self._preset_buttons.append(button)
            swatch_grid.addWidget(button, (column + index) // 8, (column + index) % 8)
        root.addLayout(swatch_grid)

        recent_caption = QLabel("RECENT")
        recent_caption.setObjectName("castColorPickerCaption")
        root.addWidget(recent_caption)
        recent_row = QHBoxLayout()
        recent_row.setSpacing(6)
        for _index in range(_RECENT_COLOR_LIMIT):
            button = self._make_swatch_button("#1e293b")
            button.setEnabled(False)
            self._recent_buttons.append(button)
            recent_row.addWidget(button)
        recent_row.addStretch(1)
        root.addLayout(recent_row)

        self._square.valuesChanged.connect(self._handle_sv_changed)
        self._square.interactionFinished.connect(self._commit_current_color)
        self._hue_bar.hueChanged.connect(self._handle_hue_changed)
        self._hue_bar.interactionFinished.connect(self._commit_current_color)
        self._hex_input.editingFinished.connect(self._handle_hex_finished)
        self._refresh_recent_buttons()
        self._sync_from_hsv(commit=False, emit_change=False)

    def current_color(self) -> str | None:
        if self._none_selected:
            return None
        return QColor.fromHsvF(self._hue, self._saturation, self._value).name().lower()

    def set_color(self, color: str | None) -> None:
        resolved = normalize_hex_color(color)
        self._updating = True
        try:
            if resolved is None and self._allow_none:
                self._none_selected = True
            else:
                self._none_selected = False
                qcolor = QColor(resolved or "#38bdf8")
                self._hue, self._saturation, self._value = _hsv_from_color(qcolor, self._hue)
            self._sync_from_hsv(commit=False, emit_change=False)
        finally:
            self._updating = False

    def popup_below(self, anchor: QWidget) -> None:
        self.adjustSize()
        origin = anchor.mapToGlobal(QPoint(0, anchor.height() + 6))
        screen = anchor.screen().availableGeometry() if anchor.screen() is not None else QRect(origin, QSize(800, 600))
        x = min(max(screen.left() + 8, origin.x()), screen.right() - self.sizeHint().width() - 8)
        y = origin.y()
        if y + self.sizeHint().height() > screen.bottom():
            y = max(screen.top() + 8, anchor.mapToGlobal(QPoint(0, 0)).y() - self.sizeHint().height() - 6)
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self._hex_input.setFocus(Qt.FocusReason.PopupFocusReason)

    def _make_swatch_button(self, color: str | None) -> QPushButton:
        button = QPushButton(self)
        button.setFixedSize(22, 22)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        apply_swatch_button(button, color, border=self._theme_border)
        button.setProperty("swatchColor", color)
        button.clicked.connect(lambda _checked=False, swatch=color: self._select_swatch(swatch))
        return button

    def _refresh_recent_buttons(self) -> None:
        colors = recent_colors()
        for index, button in enumerate(self._recent_buttons):
            if index < len(colors):
                button.setEnabled(True)
                button.setProperty("swatchColor", colors[index])
                apply_swatch_button(button, colors[index], border=self._theme_border)
                try:
                    button.clicked.disconnect()
                except TypeError:
                    pass
                button.clicked.connect(lambda _checked=False, swatch=colors[index]: self._select_swatch(swatch))
            else:
                button.setEnabled(False)
                apply_swatch_button(button, "#1e293b", border=self._theme_border)

    def _handle_sv_changed(self, saturation: float, value: float) -> None:
        if self._updating:
            return
        self._none_selected = False
        self._saturation = saturation
        self._value = value
        self._sync_from_hsv(commit=False, emit_change=True)

    def _handle_hue_changed(self, hue: float) -> None:
        if self._updating:
            return
        self._none_selected = False
        self._hue = hue
        self._sync_from_hsv(commit=False, emit_change=True)

    def _handle_hex_finished(self) -> None:
        if self._updating:
            return
        raw = self._hex_input.text().strip()
        if not raw:
            if self._allow_none:
                self.set_color(None)
                self._emit_color(commit=True)
            else:
                self._sync_from_hsv(commit=False, emit_change=False)
            return
        parsed = normalize_hex_color(raw, default=self.current_color() if not self._none_selected else None)
        self.set_color(parsed)
        self._emit_color(commit=True)

    def _select_swatch(self, color: str | None) -> None:
        self.set_color(color)
        self._emit_color(commit=True)

    def _sync_from_hsv(self, *, commit: bool, emit_change: bool) -> None:
        self._square.set_hsv(self._hue, self._saturation, self._value)
        self._hue_bar.set_hue(self._hue)
        color = self.current_color()
        self._updating = True
        try:
            if color is None:
                self._hex_input.setText("")
                self._preview.setPixmap(checkerboard_pixmap(28))
                self._preview.setStyleSheet(
                    f"background-color: transparent; border: 1px dashed {self._theme_border}; border-radius: 6px;"
                )
            else:
                self._hex_input.setText(color.upper())
                self._preview.setPixmap(QPixmap())
                self._preview.setStyleSheet(
                    f"background-color: {color}; border: 1px solid {self._theme_border}; border-radius: 6px;"
                )
        finally:
            self._updating = False
        if emit_change:
            self._emit_color(commit=commit)

    def _commit_current_color(self) -> None:
        self._emit_color(commit=True)

    def _emit_color(self, *, commit: bool) -> None:
        color = self.current_color()
        self.colorChanged.emit(color)
        if commit:
            remember_recent_color(color)
            self._refresh_recent_buttons()
            self.colorCommitted.emit(color)
