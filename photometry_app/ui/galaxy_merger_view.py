"""Galaxy Merger mode: the PhysicsEngine galaxy merger (physics_view) running inside CAst.

N-body gravity, gas dynamics, star formation, the optical renderer, recordings,
looks and PNG/MP4 export are the viewer's own C++ code, built as
``photometry_app/native/cast_merger.dll`` from ``native/merger``. This module
supplies the Qt panel and OpenGL window that replace the viewer's ImGui panel
and GLFW window.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QElapsedTimer, QPointF, QRegularExpression, QStandardPaths, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QKeySequence,
    QRegularExpressionValidator,
    QShortcut,
    QSurfaceFormat,
)
from PySide6.QtOpenGL import QOpenGLWindow
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from photometry_app.core.blackhole_stars import CatalogStars, download_star_catalog, load_catalog_stars
from photometry_app.ui.blackhole_view import (
    _STYLE,
    LabCombo,
    LabSlider,
    _native_path,
    _note,
    _row,
    _Section,
    resolve_cast_milky_way_path,
)

LIBRARY_NAME = "cast_merger.dll"
EXPORT_SIZES = ((1280, 720), (1920, 1080), (2560, 1440), (3840, 2160))
COMPOSITIONS = (
    ("Face-on disk", "face"),
    ("Edge-on disk", "edge"),
    ("First passage", "passage"),
    ("After passage", "tails"),
)

_STYLE_EXTRA = """
#blackHoleControls QDoubleSpinBox, #blackHoleControls QSpinBox, #blackHoleControls QLineEdit, #blackHoleControls QComboBox {
    background: #11151c; color: #d9dde4; border: 1px solid #262d38; border-radius: 3px; padding: 2px 4px; }
#blackHoleControls QDoubleSpinBox:disabled, #blackHoleControls QSpinBox:disabled, #blackHoleControls QLineEdit:disabled {
    color: #6f747c; }
#blackHoleControls QProgressBar { background: #171c24; border: none; border-radius: 3px; height: 8px; color: transparent; }
#blackHoleControls QProgressBar::chunk { background: #d98c40; border-radius: 3px; }
#blackHoleControls QRadioButton { color: #d9dde4; }
#mergerSubsection { text-align: left; padding: 3px 4px; font-weight: 600; color: #d9dde4;
    background: #1d222b; border: none; border-radius: 3px; }
"""


def library_path() -> Path:
    return Path(__file__).resolve().parents[1] / "native" / LIBRARY_NAME


def examples_directory() -> Path:
    return Path(__file__).resolve().parents[1] / "native" / "galaxy_merger"


def default_export_directory() -> Path:
    pictures = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
    base = Path(pictures) if pictures else Path.home()
    return base / "CAst Galaxy Merger"


_library: ctypes.CDLL | None = None
_library_error = ""


def load_library() -> ctypes.CDLL | None:
    """The native merger, or None (with ``library_error()``) where it is unavailable."""

    global _library, _library_error
    if _library is not None or _library_error:
        return _library
    if sys.platform != "win32":
        _library_error = "The galaxy merger simulation is available in the Windows build of CAst."
        return None
    path = library_path()
    try:
        lib = ctypes.CDLL(str(path))
    except OSError as exc:
        _library_error = f"The galaxy merger engine could not be loaded ({path.name}): {exc}"
        return None
    handle, text, number, buffer = ctypes.c_void_p, ctypes.c_char_p, ctypes.c_double, ctypes.c_char_p
    signatures = {
        "mg_last_error": ([buffer, ctypes.c_int], ctypes.c_int),
        "mg_set_examples_directory": ([text], None),
        "mg_set_ffmpeg": ([text], None),
        "mg_create": ([], handle),
        "mg_destroy": ([handle], None),
        "mg_frame": ([handle, number, ctypes.c_int, ctypes.c_int, ctypes.c_uint], ctypes.c_int),
        "mg_get": ([handle, text, ctypes.POINTER(number)], ctypes.c_int),
        "mg_set": ([handle, text, number], ctypes.c_int),
        "mg_text": ([handle, text, buffer, ctypes.c_int], ctypes.c_int),
        "mg_set_export_path": ([handle, text], ctypes.c_int),
        "mg_set_seed": ([handle, ctypes.c_int, text], ctypes.c_int),
        "mg_set_milky_way": ([handle, ctypes.c_char_p, ctypes.c_int, ctypes.c_int], ctypes.c_int),
        "mg_set_star_catalog": (
            [handle, ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int],
            ctypes.c_int,
        ),
        "mg_action": ([handle, text], ctypes.c_int),
        "mg_load_builtin": ([handle, ctypes.c_int], ctypes.c_int),
        "mg_load_file": ([handle, text], ctypes.c_int),
        "mg_save_recording": ([handle, text], ctypes.c_int),
        "mg_save_look": ([handle, text], ctypes.c_int),
        "mg_load_look": ([handle, text], ctypes.c_int),
        "mg_color_group": ([handle, ctypes.c_int, ctypes.POINTER(ctypes.c_float)], ctypes.c_int),
        "mg_set_color_group": ([handle, ctypes.c_int, ctypes.c_float, ctypes.c_float, ctypes.c_float], ctypes.c_int),
        "mg_drag": ([handle, number, number, ctypes.c_int], None),
        "mg_scroll": ([handle, number], None),
        "mg_render_png": ([handle, text, ctypes.c_int, ctypes.c_int], ctypes.c_int),
    }
    try:
        for name, (arguments, result) in signatures.items():
            function = getattr(lib, name)
            function.argtypes = arguments
            function.restype = result
    except AttributeError as exc:
        _library_error = f"The galaxy merger engine is out of date: {exc}"
        return None
    _library = lib
    return lib


def library_error() -> str:
    return _library_error


class MergerEngine:
    """One viewer session. Every call that touches OpenGL needs its context current."""

    def __init__(self, lib: ctypes.CDLL) -> None:
        self._lib = lib
        lib.mg_set_examples_directory(_native_path(examples_directory()))
        self._handle = lib.mg_create()
        if not self._handle:
            raise RuntimeError(self.last_error() or "OpenGL 3.3 is required for the galaxy merger view.")

    @property
    def alive(self) -> bool:
        return bool(self._handle)

    def destroy(self) -> None:
        if self._handle:
            self._lib.mg_destroy(self._handle)
            self._handle = None

    def last_error(self) -> str:
        size = self._lib.mg_last_error(None, 0)
        out = ctypes.create_string_buffer(size + 1)
        self._lib.mg_last_error(out, size + 1)
        return out.value.decode("utf-8", "replace")

    def get(self, name: str) -> float:
        value = ctypes.c_double()
        if not self._lib.mg_get(self._handle, name.encode(), ctypes.byref(value)):
            raise KeyError(name)
        return value.value

    def set(self, name: str, value: float) -> None:
        if not self._lib.mg_set(self._handle, name.encode(), float(value)):
            raise KeyError(name)

    def text(self, name: str) -> str:
        key = name.encode()
        size = self._lib.mg_text(self._handle, key, None, 0)
        out = ctypes.create_string_buffer(size + 1)
        self._lib.mg_text(self._handle, key, out, size + 1)
        return out.value.decode("utf-8", "replace")

    def action(self, name: str) -> None:
        if not self._lib.mg_action(self._handle, name.encode()):
            raise KeyError(name)

    def set_export_path(self, path: str | Path) -> None:
        self._lib.mg_set_export_path(self._handle, _native_path(path))

    def set_ffmpeg(self, path: str) -> None:
        self._lib.mg_set_ffmpeg(_native_path(path) if path else b"")

    def set_seed(self, galaxy: int, digits: str) -> None:
        self._lib.mg_set_seed(self._handle, int(galaxy), digits.encode())

    def set_milky_way(self, image: QImage) -> None:
        rgb = image.convertToFormat(QImage.Format.Format_RGB888)
        width, height = rgb.width(), rgb.height()
        row, stride = width * 3, rgb.bytesPerLine()
        raw = bytes(rgb.constBits())[: rgb.sizeInBytes()]
        if stride != row:
            raw = b"".join(raw[y * stride : y * stride + row] for y in range(height))
        if not self._lib.mg_set_milky_way(self._handle, raw, width, height):
            raise RuntimeError(self.last_error())

    def set_star_catalog(self, catalog: CatalogStars | None) -> None:
        if catalog is None:
            self._lib.mg_set_star_catalog(self._handle, None, None, 0)
            return
        stars = np.ascontiguousarray(catalog.stars, dtype=np.float32)
        colors = np.ascontiguousarray(catalog.colors, dtype=np.uint8)
        if not self._lib.mg_set_star_catalog(
            self._handle,
            stars.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            colors.ctypes.data_as(ctypes.POINTER(ctypes.c_ubyte)),
            int(stars.shape[0]),
        ):
            raise RuntimeError(self.last_error())

    def load_builtin(self, index: int) -> None:
        self._lib.mg_load_builtin(self._handle, int(index))

    def load_file(self, path: str | Path) -> None:
        self._lib.mg_load_file(self._handle, _native_path(path))

    def save_recording(self, path: str | Path) -> None:
        self._lib.mg_save_recording(self._handle, _native_path(path))

    def save_look(self, path: str | Path) -> None:
        self._lib.mg_save_look(self._handle, _native_path(path))

    def load_look(self, path: str | Path) -> None:
        self._lib.mg_load_look(self._handle, _native_path(path))

    def color_group(self, index: int) -> tuple[float, float, float] | None:
        rgb = (ctypes.c_float * 3)()
        if not self._lib.mg_color_group(self._handle, int(index), rgb):
            return None
        return float(rgb[0]), float(rgb[1]), float(rgb[2])

    def set_color_group(self, index: int, rgb: tuple[float, float, float]) -> None:
        self._lib.mg_set_color_group(self._handle, int(index), *(float(c) for c in rgb))

    def drag(self, dx: float, dy: float, pan: bool) -> None:
        self._lib.mg_drag(self._handle, float(dx), float(dy), int(pan))

    def scroll(self, notches: float) -> None:
        self._lib.mg_scroll(self._handle, float(notches))

    def frame(self, dt: float, width: int, height: int, fbo: int) -> bool:
        return bool(self._lib.mg_frame(self._handle, dt, width, height, fbo))

    def render_png(self, path: str | Path, width: int, height: int) -> None:
        if not self._lib.mg_render_png(self._handle, _native_path(path), int(width), int(height)):
            raise RuntimeError(self.last_error())


class MergerViewport(QOpenGLWindow):
    """OpenGL 3.3 core window that runs one viewer frame per vsync while exposed.

    Middle drag orbits and Shift + middle drag pans, as in the viewer; left and
    right drag do the same for mice and touchpads without a middle button.
    """

    failed = Signal(str)
    frameRendered = Signal()
    toggleControls = Signal()
    skyFailed = Signal(str)

    def __init__(self) -> None:
        super().__init__(QOpenGLWindow.UpdateBehavior.NoPartialUpdate)
        self._milky_way: QImage | None = None
        self._star_catalog: CatalogStars | None = None
        self._milky_way_uploaded = True
        self._star_catalog_uploaded = True
        fmt = QSurfaceFormat()
        fmt.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        fmt.setDepthBufferSize(24)
        fmt.setStencilBufferSize(8)
        fmt.setSwapInterval(1)
        self.setFormat(fmt)
        self.engine: MergerEngine | None = None
        self.frame_error = ""
        self._clock = QElapsedTimer()
        self._last_mouse: QPointF | None = None
        self._buttons = Qt.MouseButton.NoButton
        self._frame_times: deque[float] = deque(maxlen=60)
        self.frameSwapped.connect(self._next_frame)

    def initializeGL(self) -> None:  # noqa: N802
        lib = load_library()
        if lib is None:
            self.failed.emit(library_error())
            return
        try:
            engine = MergerEngine(lib)
        except Exception as exc:
            self.failed.emit(str(exc).strip() or "OpenGL 3.3 is required for the galaxy merger view.")
            return
        engine.set_export_path(default_export_directory())
        self.engine = engine
        self._milky_way_uploaded = self._milky_way is None
        self._star_catalog_uploaded = self._star_catalog is None
        context = self.context()
        if context is not None:
            context.aboutToBeDestroyed.connect(self._teardown)
        application = QGuiApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self._teardown)
        self._clock.start()

    def _teardown(self) -> None:
        if self.engine is None:
            return
        self.makeCurrent()
        self.engine.destroy()
        self.engine = None
        self.doneCurrent()

    def _next_frame(self) -> None:
        if self.isExposed():
            self.update()

    def framebuffer_size(self) -> tuple[int, int]:
        ratio = max(1.0, float(self.devicePixelRatio()))
        return max(1, int(round(self.width() * ratio))), max(1, int(round(self.height() * ratio)))

    @property
    def fps(self) -> float:
        if not self._frame_times:
            return 0.0
        average = sum(self._frame_times) / len(self._frame_times)
        return 1.0 / average if average > 0 else 0.0

    def restart_clock(self) -> None:
        self._clock.restart()

    def with_context(self, action) -> None:
        if self.engine is None:
            return
        self.makeCurrent()
        try:
            action(self.engine)
        finally:
            self.doneCurrent()

    def set_milky_way(self, image: QImage) -> None:
        """Uploaded on the next frame, and again if the GL context is recreated."""

        self._milky_way = image
        self._milky_way_uploaded = False
        self.requestUpdate()

    def set_star_catalog(self, catalog: CatalogStars | None) -> None:
        self._star_catalog = catalog
        self._star_catalog_uploaded = False
        self.requestUpdate()

    def _upload_sky(self, engine: MergerEngine) -> None:
        if not self._milky_way_uploaded:
            self._milky_way_uploaded = True
            try:
                engine.set_milky_way(self._milky_way)
            except Exception as exc:  # noqa: BLE001 - reported in the panel
                self._milky_way = None
                self.skyFailed.emit(f"Milky Way unavailable: {exc}")
        if not self._star_catalog_uploaded:
            self._star_catalog_uploaded = True
            try:
                engine.set_star_catalog(self._star_catalog)
            except Exception as exc:  # noqa: BLE001 - reported in the panel
                self._star_catalog = None
                self.skyFailed.emit(f"Catalog stars unavailable: {exc}")

    def paintGL(self) -> None:  # noqa: N802
        engine = self.engine
        if engine is None or not engine.alive:
            return
        self._upload_sky(engine)
        dt = self._clock.restart() / 1000.0 if self._clock.isValid() else 0.0
        if dt > 0:
            self._frame_times.append(dt)
        width, height = self.framebuffer_size()
        if engine.frame(dt, width, height, int(self.defaultFramebufferObject())):
            self.frame_error = ""
        else:
            self.frame_error = engine.last_error()
        self.frameRendered.emit()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._buttons = event.buttons()
        self._last_mouse = event.position()
        self.requestActivate()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._buttons = event.buttons()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        position = event.position()
        last, self._last_mouse = self._last_mouse, position
        engine = self.engine
        buttons = event.buttons()
        if engine is None or last is None or not buttons & (
            Qt.MouseButton.MiddleButton | Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton
        ):
            return
        ratio = max(1.0, float(self.devicePixelRatio()))
        delta = (position - last) * ratio
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        pan = shift or (bool(buttons & Qt.MouseButton.RightButton) and not buttons & Qt.MouseButton.MiddleButton)
        engine.drag(delta.x(), delta.y(), pan)
        event.accept()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.engine is not None:
            self.engine.scroll(event.angleDelta().y() / 120.0)
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.isAutoRepeat() or self.engine is None:
            event.accept()
            return
        key = event.key()
        if key == Qt.Key.Key_F1:
            self.toggleControls.emit()
        elif key == Qt.Key.Key_Space:
            self.engine.action("playPause")
        elif key == Qt.Key.Key_F:
            self.engine.action("frame")
        event.accept()


# Controls ------------------------------------------------------------------


class _Number(QDoubleSpinBox):
    """ImGui's DragScalar in a spin box: free typing, %.5g display, no wheel steal."""

    def __init__(self, minimum: float, maximum: float, step: float, fmt: str = "%.5g", parent=None) -> None:
        super().__init__(parent)
        self._fmt = fmt
        self.setDecimals(12)
        self.setRange(minimum, maximum)
        self.setSingleStep(step)
        self.setKeyboardTracking(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def textFromValue(self, value: float) -> str:  # noqa: N802
        return self._fmt % value

    def valueFromText(self, text: str) -> float:  # noqa: N802
        try:
            return float(text)
        except ValueError:
            return self.value()

    def validate(self, text: str, position: int):
        from PySide6.QtGui import QValidator

        stripped = text.strip()
        if stripped in ("", "-", "+", ".", "-.", "+.") or stripped.lower().endswith(("e", "e-", "e+")):
            return QValidator.State.Intermediate, text, position
        try:
            float(stripped)
        except ValueError:
            return QValidator.State.Invalid, text, position
        return QValidator.State.Acceptable, text, position

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class _Integer(QSpinBox):
    def __init__(self, minimum: int, maximum: int, parent=None) -> None:
        super().__init__(parent)
        self.setRange(minimum, maximum)
        self.setKeyboardTracking(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class _Field(QWidget):
    """Label on the left, editors on the right; values arrive as floats."""

    edited = Signal(float)

    def __init__(self, label: str, editors: list[QWidget], *, tooltip: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        name = QLabel(label, self)
        name.setWordWrap(True)
        name.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        layout.addWidget(name, 3)
        holder = QHBoxLayout()
        holder.setSpacing(3)
        for editor in editors:
            holder.addWidget(editor)
        layout.addLayout(holder, 2)
        self.editors = editors
        if tooltip:
            self.setToolTip(tooltip)

    @staticmethod
    def _show(editor, value) -> None:
        if editor.hasFocus() or editor.value() == value:
            return
        editor.blockSignals(True)
        editor.setValue(value)
        editor.blockSignals(False)


class LabNumber(_Field):
    def __init__(self, label: str, minimum: float, maximum: float, step: float, *, integer=False, tooltip="", parent=None) -> None:
        editor = _Integer(int(minimum), int(maximum)) if integer else _Number(minimum, maximum, step * 10)
        super().__init__(label, [editor], tooltip=tooltip, parent=parent)
        self._integer = integer
        editor.valueChanged.connect(lambda value: self.edited.emit(float(value)))

    def set_value(self, value: float) -> None:
        self._show(self.editors[0], int(round(value)) if self._integer else value)


class LabVector(_Field):
    edited_axis = Signal(int, float)

    def __init__(self, label: str, step: float, *, parent=None) -> None:
        editors = [_Number(-1e9, 1e9, step * 10, "%.4g") for _ in range(3)]
        super().__init__(label, editors, parent=parent)
        for axis, editor in enumerate(editors):
            editor.valueChanged.connect(lambda value, a=axis: self.edited_axis.emit(a, float(value)))

    def set_values(self, values: tuple[float, float, float]) -> None:
        for editor, value in zip(self.editors, values):
            self._show(editor, value)


class LabSeed(QWidget):
    """A galaxy's 64-bit realization seed, edited as exact digits."""

    edited = Signal(str)

    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(label, self), 2)
        self.editor = QLineEdit(self)
        self.editor.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{1,20}"), self.editor))
        self.editor.editingFinished.connect(self._commit)
        layout.addWidget(self.editor, 3)
        self._value = ""

    def _commit(self) -> None:
        text = self.editor.text()
        if text and int(text) < 2**64 and text != self._value:
            self.edited.emit(str(int(text)))

    def set_value(self, text: str) -> None:
        self._value = text
        if not self.editor.hasFocus() and self.editor.text() != text:
            self.editor.setText(text)


class LabColor(QWidget):
    """ImGui's ColorEdit3 as a swatch that opens CAst's color dialog."""

    edited = Signal(float, float, float)

    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.name = QLabel(label, self)
        self._swatch = QPushButton(self)
        self._swatch.setFixedSize(46, 20)
        self._swatch.clicked.connect(self._choose)
        layout.addWidget(self.name, 1)
        layout.addWidget(self._swatch)
        self._rgb = (-1.0, -1.0, -1.0)

    def set_rgb(self, rgb: tuple[float, float, float]) -> None:
        if rgb == self._rgb:
            return
        self._rgb = rgb
        color = QColor.fromRgbF(*(min(1.0, max(0.0, c)) for c in rgb))
        self._swatch.setStyleSheet(f"background: {color.name()}; border: 1px solid #3a4250; border-radius: 3px;")

    def _choose(self) -> None:
        start = QColor.fromRgbF(*(min(1.0, max(0.0, c)) for c in self._rgb))
        color = QColorDialog.getColor(start, self, self.name.text())
        if color.isValid():
            rgb = (color.redF(), color.greenF(), color.blueF())
            self.set_rgb(rgb)
            self.edited.emit(*rgb)


@dataclass(frozen=True)
class _Parameter:
    key: str
    label: str
    step: float = 0.01
    minimum: float = 0.0
    maximum: float = 1e6
    kind: str = "real"  # real, int, flag, vector, arms, seed
    tooltip: str = ""


# draw_parameters, in the viewer's order and with its drag speeds and limits.
GALAXY_PARAMETERS: tuple[_Parameter, ...] = (
    _Parameter("particles", "Particles", 1, 4, 20000, "int"),
    _Parameter("haloMass", "Halo mass", 0.01, 1e-6, 1e6),
    _Parameter("haloScale", "Halo scale", 0.01, 1e-4, 1e4),
    _Parameter("haloCutoff", "Halo cutoff", 0.05, 0.0, 1e4),
    _Parameter("diskMass", "Disk mass", 0.01, 1e-6, 1e6),
    _Parameter("diskScale", "Disk scale", 0.01, 1e-4, 1e4),
    _Parameter("diskHeight", "Disk height", 0.001, 1e-4, 10.0),
    _Parameter("dispersionRatio", "Dispersion ratio", 0.01, 0.1, 10.0),
    _Parameter(
        "targetQ", "Target stellar Q (0 = ratio)", 0.01, 0.0, 5.0,
        tooltip="Sets radial random motion independently of height. An initial target, not a stability guarantee.",
    ),
    _Parameter("diskParticleFraction", "Disk particle fraction (0 = equal mass)", 0.01, 0.0, 0.9),
    _Parameter(
        "spiralStrength", "Initial spiral strength", 0.01, 0.0, 0.85,
        tooltip="Seeds a density pattern in stars and gas. Gravity evolves it freely after initialization.",
    ),
    _Parameter("spiralArms", "Initial arms", kind="arms"),
    _Parameter("spiralPitch", "Spiral pitch (degrees)", 0.1, 5.0, 60.0),
    _Parameter("spiralPhase", "Spiral phase (degrees)", 0.5, -360.0, 360.0),
    _Parameter(
        "jeansVertical", "Height-dependent vertical support", kind="flag",
        tooltip="Experimental Jeans support. Requires isolation validation for your chosen galaxy.",
    ),
    _Parameter("populationVariation", "Stellar population variety", 0.01, 0.0, 1.0),
    _Parameter("initialYoungFraction", "Initial young mass fraction (peak)", 0.005, 0.0, 1.0),
    _Parameter("initialYoungAge", "Initial age spread", 0.1, 0.0, 1000.0),
    _Parameter("youngArmContrast", "Young population arm contrast", 0.01, 0.0, 1.0),
    _Parameter("bulgeMass", "Bulge mass", 0.01, 0.0, 1e6),
    _Parameter("bulgeScale", "Bulge scale", 0.01, 0.0, 1e4),
    _Parameter("gasMass", "Gas mass", 0.01, 0.0, 1e6),
    _Parameter("gasParticles", "Gas particles", 1, 0, 20000, "int"),
    _Parameter("gasHeight", "Gas height", 0.001, 0.0, 10.0),
    _Parameter("hotGasMass", "Hot gas mass", 0.01, 0.0, 1e6),
    _Parameter("hotGasParticles", "Hot gas particles", 1, 0, 20000, "int"),
    _Parameter("hotEmission", "Hot emission", 0.01, 0.0, 20.0),
    _Parameter("soundSpeed", "Sound speed", 0.001, 1e-4, 5.0),
    _Parameter("coolingTime", "Cooling time", 0.01, 0.0, 1e4),
    _Parameter("starFormationTime", "Star-formation time", 0.01, 1e-3, 1e4),
    _Parameter(
        "continuousStarFormation", "Continuous star formation", kind="flag",
        tooltip="Converts a fraction of gas each step. Stars share their gas carrier's orbit; total mass is conserved.",
    ),
    _Parameter("starFormationDensity", "Star-formation density (0 = auto)", 0.001, 0.0, 1e4),
    _Parameter("starFormationTemperature", "Formation temperature / cold floor", 0.01, 1.0, 100.0),
    _Parameter("starFormationConverging", "Require converging gas", kind="flag"),
    _Parameter("feedbackEnergy", "Feedback energy", 0.01, 0.0, 100.0),
    _Parameter("feedbackTime", "Feedback time", 0.01, 0.0, 1e4),
    _Parameter("inclination", "Inclination", 0.2, -180.0, 180.0),
    _Parameter("node", "Disk node", 0.2, -180.0, 180.0),
    _Parameter("reverseSpin", "Reverse spin", kind="flag"),
    _Parameter("center", "Center", 0.02, kind="vector"),
    _Parameter("velocity", "Velocity", 0.005, kind="vector"),
    _Parameter("seed", "Seed", kind="seed"),
    _Parameter("displayRadius", "Display radius", 0.005, 0.01, 20.0),
    _Parameter("emission", "Disk emission", 0.01, 0.0, 20.0),
    _Parameter("haloEmission", "Halo emission", 0.01, 0.0, 20.0),
    _Parameter("bulgeEmission", "Bulge emission", 0.01, 0.0, 20.0),
    _Parameter("gasEmission", "Gas emission", 0.01, 0.0, 20.0),
    _Parameter("youngStarEmission", "Young-star emission", 0.01, 0.0, 20.0),
)
BODY_PARAMETERS: tuple[_Parameter, ...] = (
    _Parameter("mass", "Mass", 0.01, 1e-8, 1e6),
    _Parameter("position", "Position", 0.01, kind="vector"),
    _Parameter("velocity", "Velocity", 0.01, kind="vector"),
    _Parameter("displayRadius", "Display radius", 0.001, 0.001, 10.0),
    _Parameter("emission", "Emission", 0.01, 0.0, 20.0),
)


class GalaxyMergerPanel(QWidget):
    """Galaxy Merger mode: the viewer's Physics panel beside its view."""

    PANEL_WIDTH = 380
    _milky_way_loaded = Signal(object, str)
    _star_catalog_loaded = Signal(object, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("galaxyMergerPanel")
        self._viewport: MergerViewport | None = None
        self._container: QWidget | None = None
        self._failed = False
        self._color_labels: tuple[str, ...] = ()
        # Background sky: idle, loading, ready, missing or failed (catalog also downloading).
        self._milky_way_state = "idle"
        self._milky_way_image: QImage | None = None
        self._milky_way_error = ""
        self._catalog_state = "idle"
        self._catalog: CatalogStars | None = None
        self._catalog_error = ""
        self._milky_way_loaded.connect(self._finish_milky_way)
        self._star_catalog_loaded.connect(self._finish_star_catalog)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._controls = QWidget(self)
        self._controls.setObjectName("blackHoleControls")
        self._controls.setStyleSheet(_STYLE + _STYLE_EXTRA)
        self._build_controls()
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidget(self._controls)
        self._scroll.setFixedWidth(self.PANEL_WIDTH)
        layout.addWidget(self._scroll)

        self._view_host = QWidget(self)
        self._view_host.setStyleSheet("background: #090b0f;")
        host_layout = QVBoxLayout(self._view_host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        self._fallback = QLabel(self._view_host)
        self._fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._fallback.setWordWrap(True)
        self._fallback.setStyleSheet("color: #d9dde4; padding: 24px;")
        self._fallback.hide()
        host_layout.addWidget(self._fallback)
        layout.addWidget(self._view_host, 1)

        shortcut = QShortcut(QKeySequence(Qt.Key.Key_F1), self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self._toggle_controls)

    # Embedding -------------------------------------------------------------

    @property
    def viewport(self) -> MergerViewport | None:
        return self._viewport

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._ensure_viewport()
        if self._viewport is not None:
            self._viewport.restart_clock()
            self._viewport.requestUpdate()

    def _ensure_viewport(self) -> None:
        if self._viewport is not None or self._failed:
            return
        if load_library() is None:
            self._show_failure(library_error())
            return
        viewport = MergerViewport()
        viewport.failed.connect(self._show_failure)
        viewport.frameRendered.connect(self._refresh)
        viewport.toggleControls.connect(self._toggle_controls)
        viewport.skyFailed.connect(self._sky_failed)
        if self._milky_way_image is not None:
            viewport.set_milky_way(self._milky_way_image)
        if self._catalog is not None:
            viewport.set_star_catalog(self._catalog)
        container = QWidget.createWindowContainer(viewport, self._view_host)
        container.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        container.setMinimumSize(320, 240)
        self._view_host.layout().addWidget(container)
        self._viewport = viewport
        self._container = container

    def _show_failure(self, message: str) -> None:
        self._failed = True
        self._fallback.setText(message or "OpenGL 3.3 is required for the galaxy merger view.")
        self._fallback.show()
        if self._container is not None:
            self._container.hide()
        self._controls.setEnabled(False)

    def _toggle_controls(self) -> None:
        self._scroll.setVisible(not self._scroll.isVisible())

    def _engine(self) -> MergerEngine | None:
        viewport = self._viewport
        if viewport is None or viewport.engine is None or not viewport.engine.alive:
            return None
        return viewport.engine

    def _set(self, name: str, value: float) -> None:
        engine = self._engine()
        if engine is not None:
            engine.set(name, value)

    def _act(self, name: str) -> None:
        engine = self._engine()
        if engine is not None:
            engine.action(name)

    # Background sky ----------------------------------------------------------

    def _start_milky_way(self) -> None:
        if self._milky_way_state != "idle":
            return
        self._milky_way_state = "loading"
        threading.Thread(target=self._load_milky_way, daemon=True).start()

    def _load_milky_way(self) -> None:
        path = resolve_cast_milky_way_path()
        if path is None:
            self._milky_way_loaded.emit(None, "")
            return
        image = QImage(str(path))
        if image.isNull():
            self._milky_way_loaded.emit(None, f"could not read {path.name}")
            return
        if image.width() > 8192:
            image = image.scaled(
                8192, 4096, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        self._milky_way_loaded.emit(image.convertToFormat(QImage.Format.Format_RGB888), "")

    def _finish_milky_way(self, image: QImage | None, error: str) -> None:
        self._milky_way_image = image
        self._milky_way_error = error
        self._milky_way_state = "ready" if image is not None else "failed" if error else "missing"
        if image is not None and self._viewport is not None:
            self._viewport.set_milky_way(image)

    def _start_star_catalog(self, *, download: bool = False) -> None:
        if self._catalog_state in ("loading", "downloading"):
            return
        self._catalog_state = "downloading" if download else "loading"
        self._catalog_error = ""
        threading.Thread(target=self._load_star_catalog, args=(download,), daemon=True).start()

    def _load_star_catalog(self, download: bool) -> None:
        try:
            if download:
                download_star_catalog()
            catalog = load_catalog_stars()
        except Exception as exc:  # noqa: BLE001 - reported in the panel
            self._star_catalog_loaded.emit(None, str(exc) or type(exc).__name__)
            return
        error = "the download did not finish; check the internet connection" if download and catalog is None else ""
        self._star_catalog_loaded.emit(catalog, error)

    def _finish_star_catalog(self, catalog: CatalogStars | None, error: str) -> None:
        self._catalog = catalog
        self._catalog_error = error
        self._catalog_state = "ready" if catalog is not None else "failed" if error else "missing"
        if self._viewport is not None:
            self._viewport.set_star_catalog(catalog)

    def _sky_failed(self, message: str) -> None:
        if message.startswith("Milky Way"):
            self._milky_way_image = None
            self._milky_way_state, self._milky_way_error = "failed", message
        else:
            self._catalog = None
            self._catalog_state, self._catalog_error = "failed", message

    def _milky_way_message(self) -> str:
        state = self._milky_way_state
        if state == "loading":
            return "Loading CAst's Milky Way..."
        if state == "ready":
            return "The Milky Way Sky Atlas uses (ESA Gaia Sky 2020)."
        if state == "missing":
            return "CAst's Milky Way texture was not found."
        if state == "failed":
            return f"Could not load the Milky Way ({self._milky_way_error})."
        return ""

    def _catalog_message(self) -> str:
        state = self._catalog_state
        if state == "ready" and self._catalog is not None:
            return f"{self._catalog.note}."
        if state == "loading":
            return "Loading catalog stars..."
        if state == "downloading":
            return "Downloading catalog stars. Random stars are shown until it finishes."
        if state == "missing":
            return "No star catalog is downloaded yet. Random stars are shown instead."
        if state == "failed":
            return f"Could not load catalog stars ({self._catalog_error}). Random stars are shown instead."
        return ""

    # Construction ----------------------------------------------------------

    def _slider(self, layout, key: str, label: str, low: float, high: float, fmt: str = "%.3f", *, log=False, tooltip="") -> LabSlider:
        slider = LabSlider(label, low, high, fmt, log=log, tooltip=tooltip, parent=self._controls)
        slider.edited.connect(lambda value, k=key: self._set(k, value))
        layout.addWidget(slider)
        self._sliders.append((key, slider))
        return slider

    def _check(self, layout, key: str, label: str, tooltip: str = "") -> QCheckBox:
        box = QCheckBox(label, self._controls)
        if tooltip:
            box.setToolTip(tooltip)
        box.toggled.connect(lambda checked, k=key: self._set(k, 1.0 if checked else 0.0))
        layout.addWidget(box)
        self._checks.append((key, box))
        return box

    def _combo(self, layout, key: str, label: str, items, tooltip: str = "") -> LabCombo:
        combo = LabCombo(label, items, tooltip=tooltip, parent=self._controls)
        combo.edited.connect(lambda index, k=key: self._set(k, index))
        layout.addWidget(combo)
        self._combos.append((key, combo))
        return combo

    def _button(self, label: str, action, tooltip: str = "") -> QPushButton:
        button = QPushButton(label, self._controls)
        if tooltip:
            button.setToolTip(tooltip)
        button.clicked.connect(action)
        return button

    def _number(self, layout, key: str, label: str, step: float, low: float, high: float, *, integer=False, tooltip="") -> LabNumber:
        field = LabNumber(label, low, high, step, integer=integer, tooltip=tooltip, parent=self._controls)
        field.edited.connect(lambda value, k=key: self._set(k, value))
        layout.addWidget(field)
        self._numbers.append((key, field))
        return field

    def _vector(self, layout, key: str, label: str, step: float) -> LabVector:
        field = LabVector(label, step, parent=self._controls)
        field.edited_axis.connect(lambda axis, value, k=key: self._set(f"{k}.{'xyz'[axis]}", value))
        layout.addWidget(field)
        self._vectors.append((key, field))
        return field

    def _parameter(self, layout, prefix: str, spec: _Parameter) -> None:
        key = prefix + spec.key
        if spec.kind in ("real", "int"):
            self._number(layout, key, spec.label, spec.step, spec.minimum, spec.maximum, integer=spec.kind == "int", tooltip=spec.tooltip)
        elif spec.kind == "flag":
            self._check(layout, key, spec.label, spec.tooltip)
        elif spec.kind == "vector":
            self._vector(layout, key, spec.label, spec.step)
        elif spec.kind == "arms":
            combo = LabCombo(spec.label, ("Two", "Four"), parent=self._controls)
            combo.edited.connect(lambda index, k=key: self._set(k, 4 if index else 2))
            layout.addWidget(combo)
            self._arms.append((key, combo))
        elif spec.kind == "seed":
            seed = LabSeed(spec.label, self._controls)
            index = int(prefix[1])
            seed.edited.connect(lambda digits, i=index: self.viewport.engine and self.viewport.engine.set_seed(i, digits))
            layout.addWidget(seed)
            self._seeds.append((index, seed))

    def _subsection(self, layout, title: str, expanded: bool) -> _Section:
        section = _Section(title, expanded, self._controls)
        section._toggle.setObjectName("mergerSubsection")  # noqa: SLF001
        layout.addWidget(section)
        return section

    def _build_controls(self) -> None:
        self._sliders: list[tuple[str, LabSlider]] = []
        self._checks: list[tuple[str, QCheckBox]] = []
        self._combos: list[tuple[str, LabCombo]] = []
        self._numbers: list[tuple[str, LabNumber]] = []
        self._vectors: list[tuple[str, LabVector]] = []
        self._arms: list[tuple[str, LabCombo]] = []
        self._seeds: list[tuple[int, LabSeed]] = []
        root = QVBoxLayout(self._controls)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(8)
        parent = self._controls

        title = QLabel("GALAXY MERGER", parent)
        title.setObjectName("blackHoleTitle")
        root.addWidget(title)
        root.addWidget(_note("N-BODY GRAVITY, GAS AND STARLIGHT", parent))

        self._file_group = QWidget(parent)
        files = QVBoxLayout(self._file_group)
        files.setContentsMargins(0, 0, 0, 0)
        files.setSpacing(8)
        root.addWidget(self._file_group)
        simulation_row = QHBoxLayout()
        simulation_row.addWidget(QLabel("Simulation", parent))
        self._simulation = QComboBox(parent)
        self._simulation.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._simulation.setPlaceholderText("Choose a simulation")
        self._simulation.activated.connect(self._simulation_chosen)
        simulation_row.addWidget(self._simulation, 1)
        files.addLayout(simulation_row)
        files.addWidget(
            _row(
                self._button("Load scene", self._load_scene),
                self._button("Load recording", self._load_recording),
                self._button("Save recording", self._save_recording),
            )
        )
        self._status = _note("", parent, dim=False)
        root.addWidget(self._status)
        self._error = _note("", parent, dim=False)
        self._error.setStyleSheet("color: #ff8a7a;")
        root.addWidget(self._error)
        self._progress = QProgressBar(parent)
        self._progress.setRange(0, 1000)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(8)
        root.addWidget(self._progress)
        self._run = self._button("Run", lambda: self._act("run"))
        self._stop = self._button("Stop", lambda: self._act("stop"))
        self._continue = self._button("Continue", lambda: self._act("continue"))
        self._run_row = _row(self._run, self._stop, self._continue)
        root.addWidget(self._run_row)

        self._build_playback(root)
        self._build_parameters(root)
        self._build_appearance(root)
        self._build_background(root)
        self._build_export(root)
        root.addWidget(
            _note(
                "Middle or left drag orbits. Shift + drag or right drag pans. Scroll zooms. "
                "Space pauses. F frames. F1 hides this panel.",
                parent,
            )
        )
        root.addStretch(1)

    def _build_playback(self, root) -> None:
        parent = self._controls
        section = _Section("Playback", True, parent)
        root.addWidget(section)
        p = section.body_layout
        p.addWidget(_note("Point masses. Sphere size and star size are display only.", parent))
        self._no_recording = _note("No recording yet. Choose a simulation and press Run.", parent)
        p.addWidget(self._no_recording)
        self._playback = QWidget(parent)
        pb = QVBoxLayout(self._playback)
        pb.setContentsMargins(0, 0, 0, 0)
        pb.setSpacing(6)
        p.addWidget(self._playback)
        self._diagnostics = QLabel(parent)
        self._diagnostics.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        pb.addWidget(self._diagnostics)
        self._play = self._button("Pause", lambda: self._act("playPause"))
        self._play.setFixedWidth(85)
        pb.addWidget(_row(self._play, self._button("Frame", lambda: self._act("frame"))))
        self._time = self._slider(pb, "playhead", "Time", 0.0, 1.0)
        self._speed = self._slider(pb, "speed", "Speed", 0.0, 1.0)

    def _build_parameters(self, root) -> None:
        parent = self._controls
        section = _Section("Parameters", True, parent)
        root.addWidget(section)
        p = section.body_layout
        self._recording_note = _note(
            "This is a saved recording. Choose a simulation to set parameters and run a new one.", parent
        )
        p.addWidget(self._recording_note)
        self._parameters = QWidget(parent)
        params = QVBoxLayout(self._parameters)
        params.setContentsMargins(0, 0, 0, 0)
        params.setSpacing(6)
        p.addWidget(self._parameters)
        params.addWidget(_note("Gravity is a direct pair sum. Gas adds pressure, cooling, and star formation.", parent))
        params.addWidget(QLabel("Resolution", parent))
        self._quality = QButtonGroup(self)
        self._quality.setExclusive(False)
        quality_row = QWidget(parent)
        qr = QHBoxLayout(quality_row)
        qr.setContentsMargins(0, 0, 0, 0)
        for index, name in enumerate(("Minimal", "Optimal", "Fine", "Ultra")):
            radio = QRadioButton(name, quality_row)
            radio.setAutoExclusive(False)
            self._quality.addButton(radio, index)
            qr.addWidget(radio)
        qr.addStretch(1)
        self._quality.idClicked.connect(lambda index: self._act(f"quality.{index}"))
        params.addWidget(quality_row)
        self._quality_detail = _note("", parent)
        params.addWidget(self._quality_detail)
        self._number(params, "dt", "dt", 0.0001, 1e-6, 1.0)
        self._number(params, "steps", "Steps", 1, 1, 100000, integer=True)
        self._number(params, "sampleEvery", "Sample every", 1, 1, 1000, integer=True)
        self._gravity_fields = QWidget(parent)
        gf = QVBoxLayout(self._gravity_fields)
        gf.setContentsMargins(0, 0, 0, 0)
        gf.setSpacing(6)
        params.addWidget(self._gravity_fields)
        self._number(gf, "G", "G", 0.001, 1e-8, 100.0)
        self._number(gf, "softening", "Softening", 0.001, 0.0, 5.0)
        self._body_sections: list[_Section] = []
        for index in range(8):
            body = self._subsection(params, f"Body {index + 1}", False)
            for spec in BODY_PARAMETERS:
                self._parameter(body.body_layout, f"b{index}.", spec)
            self._body_sections.append(body)
        self._galaxy_sections: list[_Section] = []
        for index in range(2):
            galaxy = self._subsection(params, f"Galaxy {index + 1}", False)
            for spec in GALAXY_PARAMETERS:
                self._parameter(galaxy.body_layout, f"g{index}.", spec)
            self._galaxy_sections.append(galaxy)

    def _build_appearance(self, root) -> None:
        parent = self._controls
        section = _Section("Appearance", True, parent)
        root.addWidget(section)
        a = section.body_layout
        self._check(a, "showAxes", "Axes")
        self._picture_group = QWidget(parent)
        pg = QVBoxLayout(self._picture_group)
        pg.setContentsMargins(0, 0, 0, 0)
        pg.setSpacing(6)
        a.addWidget(self._picture_group)
        self._combo(pg, "picture", "Picture", ("Particles", "Illustrative volume", "Optical"))
        pg.addWidget(
            _note(
                "Optical draws stellar light and foreground dust. Illustrative volume uses a stylized density palette. "
                "Appearance does not change the simulation.",
                parent,
            )
        )

        self._field_group = QWidget(parent)
        fg = QVBoxLayout(self._field_group)
        fg.setContentsMargins(0, 0, 0, 0)
        fg.setSpacing(6)
        a.addWidget(self._field_group)
        self._combo(fg, "fieldResolution", "Resolution", ("Soft", "Coarse", "Standard", "Fine"))
        self._slider(fg, "fieldGain", "Field brightness", 0.2, 4.0)
        self._slider(fg, "fieldContrast", "Field contrast", 0.0, 1.0)
        self._slider(fg, "fieldHeat", "Field heat", 0.0, 1.0)
        fg.addWidget(
            _note(
                "Resolution controls the reconstructed galaxy density field. Brightness, contrast and heat affect its appearance only.",
                parent,
            )
        )

        self._optical_group = QWidget(parent)
        og = QVBoxLayout(self._optical_group)
        og.setContentsMargins(0, 0, 0, 0)
        og.setSpacing(6)
        a.addWidget(self._optical_group)
        self._refined_note = _note(
            "Refined starlight: massless tracers follow the recorded gravity field. Their light does not add gravitational mass.",
            parent,
        )
        og.addWidget(self._refined_note)
        og.addWidget(_row(self._button("Frame current light", lambda: self._act("compose.wide"))))
        for first, second in (COMPOSITIONS[0:2], COMPOSITIONS[2:4]):
            og.addWidget(
                _row(
                    self._button(first[0], lambda _=False, s=first[1]: self._act(f"compose.{s}")),
                    self._button(second[0], lambda _=False, s=second[1]: self._act(f"compose.{s}")),
                )
            )
        og.addWidget(
            _note(
                "Disk views select the first galaxy. Compositions stay fixed during playback; After passage is a suggested tail view.",
                parent,
            )
        )
        self._slider(og, "exposure", "Exposure", 0.5, 30.0)
        self._slider(og, "bloom", "Bloom", 0.0, 0.6)
        self._slider(og, "opticalSmoothing", "Structure smoothing", 0.5, 1.8)
        self._slider(og, "stellarDetail", "Fine stellar structure", 0.0, 1.0)
        self._slider(og, "armDefinition", "Arm and stream definition", 0.0, 1.0)
        self._slider(og, "dustStructure", "Dust lane structure", 0.0, 1.0)
        self._slider(og, "dust", "Dust", 0.0, 5.0)
        self._slider(og, "reddening", "Reddening", 0.0, 1.0)
        self._slider(og, "gasGlow", "Nebula emission", 0.0, 3.0)
        populations = self._subsection(og, "Stellar populations and emission lines", False)
        sp = populations.body_layout
        sp.addWidget(
            _note(
                "Age clocks are in simulation units. Blue stellar light lasts longer than ionizing light. "
                "Emission lines are illustrative proxies.",
                parent,
            )
        )
        self._slider(sp, "youngLifetime", "Blue-light lifetime", 0.1, 100.0, "%.2f", log=True)
        self._slider(sp, "ionizingLifetime", "Ionizing lifetime", 0.01, 20.0, "%.3f", log=True)
        self._slider(sp, "oiiiLifetime", "OIII source lifetime", 0.01, 20.0, "%.3f", log=True)
        self._slider(sp, "youngBoost", "Young-light boost", 0.0, 10.0)
        self._slider(sp, "associationSize", "Association size", 0.05, 2.0)
        self._slider(sp, "nebulaRadius", "Ionizing reach", 0.05, 5.0)
        self._slider(sp, "ionizingMassScale", "Ionizing mass scale", 0.000001, 0.1, "%.6f", log=True)
        self._slider(sp, "nebulaDensityScale", "Emission density scale", 0.00001, 1.0, "%.5f", log=True)
        self._slider(sp, "halphaStrength", "H-alpha strength", 0.0, 50.0)
        self._slider(sp, "oiiiStrength", "OIII strength", 0.0, 50.0)
        self._slider(sp, "nebulaSize", "Nebula size", 0.05, 2.0)
        self._palettes: list[tuple[str, LabColor]] = []
        for key, label in (("halpha", "H-alpha palette"), ("oiii", "OIII palette")):
            color = LabColor(label, parent)
            color.edited.connect(lambda r, g, b, k=key: self._set_rgb(k, (r, g, b)))
            sp.addWidget(color)
            self._palettes.append((key, color))
        sp.addWidget(
            _row(
                self._button("Broadband look", lambda: self._act("look.broadband")),
                self._button("Emission emphasis", lambda: self._act("look.emission")),
            )
        )
        self._slider(og, "stellarTexture", "Stellar texture", 0.0, 0.3)
        og.addWidget(_row(self._button("Save look", self._save_look), self._button("Load look", self._load_look)))
        self._check(og, "showHalo", "Show dynamical halo")
        self._check(og, "showHotGas", "Show hot atmosphere")
        og.addWidget(
            _note(
                "Lifetime is in simulation units. Halo and atmosphere light are illustrative overlays. Dust absorbs stars "
                "behind it; young clusters fade toward warm starlight.",
                parent,
            )
        )

        self._particle_group = QWidget(parent)
        ptg = QVBoxLayout(self._particle_group)
        ptg.setContentsMargins(0, 0, 0, 0)
        ptg.setSpacing(6)
        a.addWidget(self._particle_group)
        self._slider(ptg, "pointScale", "Point scale", 0.2, 8.0)
        self._slider(ptg, "lightSmoothing", "Light smoothing", 0.0, 8.0)
        self._slider(ptg, "soften", "Soften", 0.0, 28.0)
        self._slider(ptg, "softenBrightness", "Soften brightness", 0.0, 8.0)
        self._slider(ptg, "dust", "Dust", 0.0, 5.0)
        self._slider(ptg, "reddening", "Reddening", 0.0, 1.0)
        self._slider(ptg, "gasGlow", "Gas glow", 0.0, 3.0)
        ptg.addWidget(
            _note(
                "Smoothing reaches toward the next particle, so crowded regions stay small. Soften is the width of the smooth "
                "galaxy underneath those details. Soften brightness lifts only that smooth layer, so a wide one stays visible "
                "on the dark background. Nearby gas adds into one cloud, and a neck appears only where the clouds overlap. "
                "Dust darkens that smooth body along the cloud. Reddening takes the blue out first and returns a little of "
                "the blocked light as brown.",
                parent,
            )
        )
        self._slider(ptg, "trail", "Trail", 0.02, 1.0)
        self._slider(ptg, "keyLight", "Key light", 0.0, 3.0)
        self._slider(ptg, "fillLight", "Fill light", 0.0, 3.0)

        self._colors_note = _note("Colors appear when particles are on screen.", parent)
        a.addWidget(self._colors_note)
        self._colors_holder = QWidget(parent)
        self._colors_layout = QVBoxLayout(self._colors_holder)
        self._colors_layout.setContentsMargins(0, 0, 0, 0)
        self._colors_layout.setSpacing(4)
        a.addWidget(self._colors_holder)
        self._group_colors: list[LabColor] = []

    def _build_background(self, root) -> None:
        parent = self._controls
        section = _Section("Background", False, parent)
        root.addWidget(section)
        b = section.body_layout
        b.addWidget(
            _note(
                "A CAst backdrop, not part of the simulation: the merger has no real place in the sky. "
                "It appears in the view, Save frame, and exports.",
                parent,
            )
        )
        self._check(b, "sky.milkyWay", "Milky Way")
        self._milky_way_controls = QWidget(parent)
        mw = QVBoxLayout(self._milky_way_controls)
        mw.setContentsMargins(0, 0, 0, 0)
        mw.setSpacing(6)
        b.addWidget(self._milky_way_controls)
        self._milky_way_note = _note("", parent)
        mw.addWidget(self._milky_way_note)
        self._slider(mw, "sky.brightness", "Sky brightness", 0.0, 3.0, "%.2f")
        self._slider(mw, "sky.blackLevel", "Sky black level", 0.0, 0.5, "%.2f")
        self._slider(mw, "sky.saturation", "Sky saturation", 0.0, 2.0, "%.2f")

        self._check(b, "sky.stars", "Stars")
        self._star_controls = QWidget(parent)
        st = QVBoxLayout(self._star_controls)
        st.setContentsMargins(0, 0, 0, 0)
        st.setSpacing(6)
        b.addWidget(self._star_controls)
        self._check(st, "sky.catalog", "Real catalog stars")
        self._catalog_note = _note("", parent)
        st.addWidget(self._catalog_note)
        self._catalog_download = self._button("Download star catalog", lambda: self._start_star_catalog(download=True))
        st.addWidget(_row(self._catalog_download))
        self._slider(st, "sky.starBrightness", "Star brightness", 0.0, 12.0, "%.2f")
        self._slider(st, "sky.starSize", "Star size", 0.5, 3.0, "%.2f")
        self._slider(st, "sky.faintest", "Faintest star", 3.0, 9.5, "V %.1f")

        self._sky_orientation = QWidget(parent)
        so = QVBoxLayout(self._sky_orientation)
        so.setContentsMargins(0, 0, 0, 0)
        so.setSpacing(6)
        b.addWidget(self._sky_orientation)
        self._slider(so, "sky.rotation", "Sky rotation", -180.0, 180.0, "%.0f°")
        self._slider(so, "sky.tilt", "Sky tilt", -90.0, 90.0, "%.0f°")
        so.addWidget(
            _note(
                "Rotation and tilt turn the backdrop. The defaults put the southern Milky Way behind the opening view; "
                "at zero, the band lies in the merger's orbital plane. Star brightness is compressed for display: "
                "1.585x per magnitude instead of 2.512x.",
                parent,
            )
        )

    def _build_export(self, root) -> None:
        parent = self._controls
        section = _Section("Export", False, parent)
        root.addWidget(section)
        e = section.body_layout
        e.addWidget(
            _note(
                "Renders the view, without this panel. Camera, colors, lights, trails, and axes are the ones on screen.",
                parent,
            )
        )
        self._export_status = _note("", parent, dim=False)
        e.addWidget(self._export_status)
        self._export_error = _note("", parent, dim=False)
        self._export_error.setStyleSheet("color: #ff8a7a;")
        e.addWidget(self._export_error)
        self._export_stop = self._button("Stop export", lambda: self._act("export.stop"))
        self._export_start = self._button("Export", self._start_export)
        self._export_continue = self._button("Continue", lambda: self._start_export(resume=True))
        self._export_still = self._button("Save frame", lambda: self._act("export.still"))
        e.addWidget(_row(self._export_stop, self._export_start, self._export_continue, self._export_still))
        self._export_settings = QWidget(parent)
        es = QVBoxLayout(self._export_settings)
        es.setContentsMargins(0, 0, 0, 0)
        es.setSpacing(6)
        e.addWidget(self._export_settings)
        self._export_preset = LabCombo(
            "Preset", ["Custom"] + [f"{w} x {h}" for w, h in EXPORT_SIZES], parent=parent
        )
        self._export_preset.edited.connect(self._export_preset_chosen)
        es.addWidget(self._export_preset)
        self._number(es, "export.width", "Width", 1, 16, 8192, integer=True)
        self._number(es, "export.height", "Height", 1, 16, 8192, integer=True)
        self._number(es, "export.fps", "FPS", 1, 1, 120, integer=True)
        self._combo(es, "export.format", "Format", ("PNG sequence", "MP4 (ffmpeg)"))
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Output", parent))
        self._export_path = QLineEdit(parent)
        self._export_path.editingFinished.connect(self._export_path_edited)
        output_row.addWidget(self._export_path, 1)
        output_row.addWidget(self._button("Browse", self._browse_export))
        es.addLayout(output_row)
        self._number(es, "export.start", "From", 0.01, -1e9, 1e9)
        self._number(es, "export.end", "To", 0.01, -1e9, 1e9)
        self._export_summary = _note("", parent)
        es.addWidget(self._export_summary)
        es.addWidget(_note("PNG sequence can continue after Stop. MP4 is closed as a finished file.", parent))

    # Actions ---------------------------------------------------------------

    def _set_rgb(self, key: str, rgb: tuple[float, float, float]) -> None:
        for channel, value in zip("rgb", rgb):
            self._set(f"{key}.{channel}", value)

    def _simulation_chosen(self, index: int) -> None:
        engine = self._engine()
        if engine is not None and index != int(engine.get("preset")):
            engine.load_builtin(index)

    def _dialog_directory(self) -> str:
        return str(default_export_directory())

    def _load_scene(self) -> None:
        engine = self._engine()
        if engine is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Load scene", str(examples_directory()), "Scenes (*.scene)")
        if path:
            engine.load_file(path)

    def _load_recording(self) -> None:
        engine = self._engine()
        if engine is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Load recording", self._dialog_directory(), "Recordings (*.traj)")
        if path:
            engine.load_file(path)

    def _save_recording(self) -> None:
        engine = self._engine()
        if engine is None:
            return
        if not engine.get("hasSamples"):
            engine.save_recording("")
            return
        name = engine.text("name") or "recording"
        suggested = str(Path(self._dialog_directory()) / f"{name}.traj")
        path, _ = QFileDialog.getSaveFileName(self, "Save recording", suggested, "Recordings (*.traj)")
        if path:
            default_export_directory().mkdir(parents=True, exist_ok=True)
            engine.save_recording(path)

    def _save_look(self) -> None:
        engine = self._engine()
        if engine is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save look", str(Path(self._dialog_directory()) / "galaxy.look"), "Looks (*.look)")
        if path:
            engine.save_look(path)

    def _load_look(self) -> None:
        engine = self._engine()
        if engine is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Load look", str(examples_directory()), "Looks (*.look)")
        if path:
            engine.load_look(path)

    def _export_preset_chosen(self, index: int) -> None:
        if index > 0:
            width, height = EXPORT_SIZES[index - 1]
            self._set("export.width", width)
            self._set("export.height", height)

    def _export_path_edited(self) -> None:
        engine = self._engine()
        if engine is not None and self._export_path.text() != engine.text("exportPath"):
            engine.set_export_path(self._export_path.text())

    def _browse_export(self) -> None:
        engine = self._engine()
        if engine is None:
            return
        current = engine.text("exportPath")
        if int(engine.get("export.format")) == 0:
            path = QFileDialog.getExistingDirectory(self, "PNG sequence folder", current)
        else:
            path, _ = QFileDialog.getSaveFileName(self, "MP4 file", engine.text("exportTarget"), "MP4 video (*.mp4)")
        if path:
            engine.set_export_path(path)

    def _start_export(self, resume: bool = False) -> None:
        engine = self._engine()
        if engine is None:
            return
        if int(engine.get("export.format")) == 1 and not resume:
            try:
                from photometry_app.core.animation_export import resolve_ffmpeg_executable

                engine.set_ffmpeg(resolve_ffmpeg_executable())
            except Exception:
                engine.set_ffmpeg("")
        engine.action("export.continue" if resume else "export.start")

    # Refresh ---------------------------------------------------------------

    @staticmethod
    def _set_text(label: QLabel, text: str) -> None:
        if label.text() != text:
            label.setText(text)
        label.setVisible(bool(text))

    def _refresh(self) -> None:
        engine = self._engine()
        viewport = self._viewport
        if engine is None or viewport is None:
            return
        get = engine.get
        busy = bool(get("busy"))
        editable = bool(get("editable"))
        has_samples = bool(get("hasSamples"))

        if self._simulation.count() == 0:
            self._simulation.addItems([engine.text(f"simulation{i}") for i in range(int(get("simulations")))])
        preset = int(get("preset"))
        if self._simulation.currentIndex() != preset:
            self._simulation.setCurrentIndex(preset)
        name = engine.text("name") or "Choose a simulation"
        if self._simulation.placeholderText() != name:
            self._simulation.setPlaceholderText(name)
        self._file_group.setEnabled(not busy)
        self._set_text(self._status, engine.text("status") or viewport.frame_error)
        self._set_text(self._error, engine.text("error"))
        steps = get("steps")
        self._progress.setVisible(busy and steps > 0)
        if busy and steps > 0:
            self._progress.setValue(int(1000 * get("step") / steps))
        self._run_row.setVisible(editable)
        self._stop.setVisible(busy)
        self._run.setVisible(not busy)
        self._continue.setVisible(not busy and bool(get("resumable")))

        self._no_recording.setVisible(not has_samples)
        self._playback.setVisible(has_samples)
        if has_samples:
            self._diagnostics.setText(
                f"t = {get('playhead'):.6g}\nenergy = {get('energy'):.8g}\n"
                f"|P| = {get('momentum'):.3g}\n|L| = {get('angularMomentum'):.6g}"
            )
            play_text = "Pause" if get("playing") else "Play"
            if self._play.text() != play_text:
                self._play.setText(play_text)
            begin, end, speed = get("timeBegin"), get("timeEnd"), get("speed")
            self._time.set_range(begin, end)
            self._speed.set_range(0.0, max((end - begin) / 2.0, max(speed * 4.0, 1e-3)))

        self._recording_note.setVisible(not editable)
        self._parameters.setVisible(editable)
        self._parameters.setEnabled(not busy)
        if editable:
            quality = int(get("quality"))
            for button in self._quality.buttons():
                button.setChecked(self._quality.id(button) == quality)
            self._set_text(self._quality_detail, engine.text("qualityDetail"))
            self._gravity_fields.setVisible(bool(get("gravity")))
            for sections, count, prefix in (
                (self._body_sections, int(get("bodies")), "body"),
                (self._galaxy_sections, int(get("galaxies")), "galaxy"),
            ):
                for index, section in enumerate(sections):
                    section.setVisible(index < count)
                    title = engine.text(f"{prefix}{index}").replace("&", "&&") if index < count else ""
                    if index < count and section._toggle.text() != title:  # noqa: SLF001
                        section._toggle.setText(title)  # noqa: SLF001

        picture = int(get("picture"))
        particle_scene = bool(get("particleScene"))
        field_view = particle_scene and picture == 1
        self._picture_group.setVisible(particle_scene)
        self._field_group.setVisible(field_view)
        self._optical_group.setVisible(not field_view and picture == 2)
        self._particle_group.setVisible(not field_view and picture != 2)
        self._refined_note.setVisible(bool(get("refinedLight")))
        for key, color in self._palettes:
            color.set_rgb((get(f"{key}.r"), get(f"{key}.g"), get(f"{key}.b")))
        self._sync_color_groups(engine, field_view)

        milky_way, stars, catalog = bool(get("sky.milkyWay")), bool(get("sky.stars")), bool(get("sky.catalog"))
        if milky_way and self._milky_way_state == "idle":
            self._start_milky_way()
        if stars and catalog and self._catalog_state == "idle":
            self._start_star_catalog()
        self._milky_way_controls.setVisible(milky_way)
        self._star_controls.setVisible(stars)
        self._sky_orientation.setVisible(milky_way or stars)
        if milky_way:
            self._set_text(self._milky_way_note, self._milky_way_message())
        if stars:
            self._set_text(self._catalog_note, self._catalog_message() if catalog else "")
            self._catalog_download.setVisible(catalog and self._catalog_state in ("missing", "failed"))

        active = bool(get("export.active"))
        self._set_text(self._export_status, engine.text("exportStatus"))
        self._set_text(self._export_error, engine.text("exportError"))
        self._export_stop.setVisible(active)
        self._export_start.setVisible(not active)
        self._export_still.setVisible(not active)
        self._export_continue.setVisible(not active and bool(get("export.resumable")))
        self._export_settings.setEnabled(not active)
        if self._export_settings.isVisible():
            size = (int(get("export.width")), int(get("export.height")))
            self._export_preset.set_index(EXPORT_SIZES.index(size) + 1 if size in EXPORT_SIZES else 0)
            path = engine.text("exportPath")
            if not self._export_path.hasFocus() and self._export_path.text() != path:
                self._export_path.setText(path)
            frames = int(get("export.frames"))
            target = engine.text("exportTarget")
            self._export_summary.setText(
                f"{frames} PNG frames in {target}" if int(get("export.format")) == 0 else f"{frames} frames to {target}"
            )

        self._sync_values(engine)

    def _sync_values(self, engine: MergerEngine) -> None:
        get = engine.get
        for key, slider in self._sliders:
            if slider.isVisible():
                slider.set_value(get(key))
        for key, box in self._checks:
            if box.isVisible():
                checked = bool(get(key))
                if box.isChecked() != checked:
                    box.blockSignals(True)
                    box.setChecked(checked)
                    box.blockSignals(False)
        for key, combo in self._combos:
            if combo.isVisible():
                combo.set_index(int(get(key)))
        for key, field in self._numbers:
            if field.isVisible():
                field.set_value(get(key))
        for key, field in self._vectors:
            if field.isVisible():
                field.set_values((get(f"{key}.x"), get(f"{key}.y"), get(f"{key}.z")))
        for key, combo in self._arms:
            if combo.isVisible():
                combo.set_index(1 if get(key) == 4 else 0)
        for index, seed in self._seeds:
            if seed.isVisible():
                seed.set_value(engine.text(f"seed{index}"))

    def _sync_color_groups(self, engine: MergerEngine, field_view: bool) -> None:
        count = int(engine.get("colorGroups"))
        labels = tuple(engine.text(f"colorGroup{i}") for i in range(count))
        if labels != self._color_labels:
            for widget in self._group_colors:
                widget.deleteLater()
            self._group_colors = []
            for index, label in enumerate(labels):
                color = LabColor(label, self._controls)
                color.edited.connect(lambda r, g, b, i=index: self._paint_group(i, (r, g, b)))
                self._colors_layout.addWidget(color)
                self._group_colors.append(color)
            self._color_labels = labels
        self._colors_note.setVisible(not field_view and count == 0)
        if self._colors_holder.isVisible():
            for index, widget in enumerate(self._group_colors):
                rgb = engine.color_group(index)
                if rgb is not None:
                    widget.set_rgb(rgb)

    def _paint_group(self, index: int, rgb: tuple[float, float, float]) -> None:
        engine = self._engine()
        if engine is not None:
            engine.set_color_group(index, rgb)
