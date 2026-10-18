"""Black Hole mode: the PhysicsEngine black hole lab running inside CAst.

Rendering, the evolving gas disk, camera paths, PNG and movie export are the
lab's own C++ code, built as ``photometry_app/native/cast_blackhole.dll`` from
``native/blackhole``. This module supplies the Qt controls and window around
it. The lab's embedded panorama is replaced by CAst's Milky Way texture.
"""

from __future__ import annotations

import ctypes
import math
import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, QStandardPaths, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QKeySequence,
    QPainter,
    QShortcut,
    QSurfaceFormat,
)
from PySide6.QtOpenGL import QOpenGLWindow
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStyle,
    QStyleOptionSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from photometry_app.app_metadata import application_root_path
from photometry_app.core.blackhole_stars import StarCatalogTextures, download_star_catalog, load_star_catalog_textures
from photometry_app.core.milky_way_assets import MilkyWayAssetResolver

SKY_CREDIT = "Milky Way: ESA Gaia Sky 2020"
LIBRARY_NAME = "cast_blackhole.dll"
SHOT_NAMES = (
    "Current view",
    "Orbit",
    "Push in",
    "Pull back",
    "Above-disk flyby",
    "Rising reveal",
    "Custom A to B",
    "Cinematic slingshot",
)
SHOT_ORBIT, SHOT_FLYBY, SHOT_REVEAL, SHOT_CUSTOM, SHOT_SLINGSHOT = 1, 4, 5, 6, 7
MOVIE_HEIGHTS = (720, 1080, 1440, 2160)

# Rows of the J2000 equatorial-to-galactic rotation; the sky shader applies its transpose.
_EQUATORIAL_TO_GALACTIC = (
    (-0.0548755604, -0.8734370902, -0.4838350155),
    (0.4941094279, -0.4448296300, 0.7469822445),
    (-0.8676661490, -0.1980763734, 0.4559837762),
)


def camera_horizon(spin: float) -> float:
    return 1.0 + math.sqrt(max(0.0, 1.0 - float(spin) * float(spin)))


def camera_minimum(spin: float) -> float:
    return camera_horizon(spin) + 0.02


def kerr_isco(spin: float) -> float:
    """Prograde innermost stable circular orbit in units of GM/c^2."""

    a = float(spin)
    z1 = 1.0 + (1.0 - a * a) ** (1.0 / 3.0) * ((1.0 + a) ** (1.0 / 3.0) + (1.0 - a) ** (1.0 / 3.0))
    z2 = math.sqrt(3.0 * a * a + z1 * z1)
    return 3.0 + z2 - math.sqrt(max(0.0, (3.0 - z1) * (3.0 + z1 + 2.0 * z2)))


def cast_sky_uv(direction: tuple[float, float, float], sky_rotation_deg: float = -65.0) -> tuple[float, float]:
    """Texture UV the sky shader samples for an escaped ray direction.

    The lab's rotation and tilt are kept, so the result is the direction in the
    original panorama's galactic frame. That is converted to RA/Dec and looked
    up in CAst's equatorial Milky Way the way Sky Atlas maps it.
    """

    x, y, z = direction
    angle = math.radians(sky_rotation_deg)
    c, s = math.cos(angle), math.sin(angle)
    x, z = c * x + s * z, -s * x + c * z
    y, z = 0.906307 * y + 0.422618 * z, -0.422618 * y + 0.906307 * z
    galactic = (x, -z, y)
    m = _EQUATORIAL_TO_GALACTIC
    ex = m[0][0] * galactic[0] + m[1][0] * galactic[1] + m[2][0] * galactic[2]
    ey = m[0][1] * galactic[0] + m[1][1] * galactic[1] + m[2][1] * galactic[2]
    ez = m[0][2] * galactic[0] + m[1][2] * galactic[1] + m[2][2] * galactic[2]
    u = (0.5 - math.atan2(ey, ex) / math.tau) % 1.0
    v = 0.5 - math.asin(max(-1.0, min(1.0, ez))) / math.pi
    return u, v


def resolve_cast_milky_way_path() -> Path | None:
    """The single equirectangular Milky Way texture CAst ships (Sky Atlas uses it too)."""

    info = MilkyWayAssetResolver.from_environment(application_root_path()).resolve()
    path = info.runtime_single_texture_path
    if path is not None and path.is_file():
        return path
    return None


def library_path() -> Path:
    return Path(__file__).resolve().parents[1] / "native" / LIBRARY_NAME


def default_export_directory() -> Path:
    pictures = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
    base = Path(pictures) if pictures else Path.home()
    return base / "CAst Black Hole"


def _native_path(path: str | Path) -> bytes:
    # The lab's C++ opens files through narrow (ANSI code page) paths.
    return str(path).encode("mbcs" if sys.platform == "win32" else "utf-8", "replace")


class _Input(ctypes.Structure):
    _fields_ = [
        ("mouseDx", ctypes.c_float),
        ("mouseDy", ctypes.c_float),
        ("wheel", ctypes.c_float),
        ("dragLeft", ctypes.c_int),
        ("dragRight", ctypes.c_int),
        ("dragMiddle", ctypes.c_int),
        ("keyW", ctypes.c_int),
        ("keyA", ctypes.c_int),
        ("keyS", ctypes.c_int),
        ("keyD", ctypes.c_int),
        ("keyQ", ctypes.c_int),
        ("keyE", ctypes.c_int),
        ("shift", ctypes.c_int),
        ("pressSpace", ctypes.c_int),
        ("pressR", ctypes.c_int),
        ("mouse", ctypes.c_int),
        ("keyboard", ctypes.c_int),
    ]


class EngineState(ctypes.Structure):
    _fields_ = [
        ("preparing", ctypes.c_int),
        ("exporting", ctypes.c_int),
        ("buffering", ctypes.c_int),
        ("movieActive", ctypes.c_int),
        ("written", ctypes.c_int),
        ("total", ctypes.c_int),
        ("submitted", ctypes.c_int),
        ("flowReady", ctypes.c_int),
        ("time", ctypes.c_double),
        ("exportElapsed", ctypes.c_double),
    ]


_library: ctypes.CDLL | None = None
_library_error = ""


def load_library() -> ctypes.CDLL | None:
    """The native lab, or None (with ``library_error()``) where it is unavailable."""

    global _library, _library_error
    if _library is not None or _library_error:
        return _library
    if sys.platform != "win32":
        _library_error = "The black hole simulation is available in the Windows build of CAst."
        return None
    path = library_path()
    try:
        lib = ctypes.CDLL(str(path))
    except OSError as exc:
        _library_error = f"The black hole engine could not be loaded ({path.name}): {exc}"
        return None
    handle, text, number = ctypes.c_void_p, ctypes.c_char_p, ctypes.c_double
    buffer = ctypes.c_char_p
    signatures = {
        "bh_last_error": ([buffer, ctypes.c_int], ctypes.c_int),
        "bh_create": ([], handle),
        "bh_destroy": ([handle], None),
        "bh_set_sky": ([handle, ctypes.c_char_p, ctypes.c_int, ctypes.c_int], ctypes.c_int),
        "bh_set_star_catalog": (
            [
                handle,
                ctypes.POINTER(ctypes.c_float),
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_float),
                ctypes.POINTER(ctypes.c_ubyte),
                ctypes.c_int,
                text,
            ],
            ctypes.c_int,
        ),
        "bh_set_ffmpeg": ([text], None),
        "bh_set_export_directory": ([handle, text], None),
        "bh_set_credit": ([handle, ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_int], None),
        "bh_set_overlay": ([handle, ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_int], None),
        "bh_get": ([handle, text, ctypes.POINTER(number)], ctypes.c_int),
        "bh_set": ([handle, text, number], ctypes.c_int),
        "bh_reset_scene": ([handle], None),
        "bh_movie_set_a": ([handle], None),
        "bh_movie_set_b": ([handle], None),
        "bh_movie_request": ([handle], None),
        "bh_movie_stop": ([handle], None),
        "bh_movie_set_destination": ([handle, text], None),
        "bh_movie_status": ([handle, buffer, ctypes.c_int], ctypes.c_int),
        "bh_status": ([handle, buffer, ctypes.c_int], ctypes.c_int),
        "bh_flow_status": ([handle, buffer, ctypes.c_int], ctypes.c_int),
        "bh_state": ([handle, ctypes.POINTER(EngineState)], None),
        "bh_kerr_horizon": ([number], number),
        "bh_kerr_isco": ([number], number),
        "bh_gravitational_seconds": ([number], number),
        "bh_camera_minimum": ([handle], number),
        "bh_camera_horizon": ([handle], number),
        "bh_frame": ([handle, ctypes.POINTER(_Input), number, ctypes.c_int, ctypes.c_int, ctypes.c_uint], ctypes.c_int),
        "bh_save_png": ([handle, text, ctypes.c_int, ctypes.c_int], ctypes.c_int),
    }
    try:
        for name, (arguments, result) in signatures.items():
            function = getattr(lib, name)
            function.argtypes = arguments
            function.restype = result
    except AttributeError as exc:
        _library_error = f"The black hole engine is out of date: {exc}"
        return None
    _library = lib
    return lib


def library_error() -> str:
    return _library_error


def _text(function, *arguments) -> str:
    size = function(*arguments, None, 0)
    out = ctypes.create_string_buffer(size + 1)
    function(*arguments, out, size + 1)
    return out.value.decode("utf-8", "replace")


class BlackHoleEngine:
    """One lab session. Every call that touches OpenGL needs its context current."""

    def __init__(self, lib: ctypes.CDLL) -> None:
        self._lib = lib
        self._handle = lib.bh_create()
        if not self._handle:
            raise RuntimeError(self.last_error() or "OpenGL 3.3 is required for the black hole view.")

    @property
    def alive(self) -> bool:
        return bool(self._handle)

    def destroy(self) -> None:
        if self._handle:
            self._lib.bh_destroy(self._handle)
            self._handle = None

    def last_error(self) -> str:
        return _text(self._lib.bh_last_error)

    def get(self, name: str) -> float:
        value = ctypes.c_double()
        if not self._lib.bh_get(self._handle, name.encode(), ctypes.byref(value)):
            raise KeyError(name)
        return value.value

    def set(self, name: str, value: float) -> None:
        if not self._lib.bh_set(self._handle, name.encode(), float(value)):
            raise KeyError(name)

    def set_sky(self, image: QImage) -> None:
        rgb = image.convertToFormat(QImage.Format.Format_RGB888)
        width, height = rgb.width(), rgb.height()
        row = width * 3
        bits = rgb.constBits()
        raw = bytes(bits)[: rgb.sizeInBytes()]
        if rgb.bytesPerLine() != row:
            stride = rgb.bytesPerLine()
            raw = b"".join(raw[y * stride : y * stride + row] for y in range(height))
        if not self._lib.bh_set_sky(self._handle, raw, width, height):
            raise RuntimeError(self.last_error())

    def set_star_catalog(self, catalog: StarCatalogTextures | None) -> None:
        if catalog is None:
            self._lib.bh_set_star_catalog(self._handle, None, 0, None, None, 0, None)
            return
        cells = np.ascontiguousarray(catalog.cells, dtype=np.float32)
        stars = np.ascontiguousarray(catalog.stars, dtype=np.float32)
        colors = np.ascontiguousarray(catalog.colors, dtype=np.uint8)
        floats, bytes_ = ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_ubyte)
        if not self._lib.bh_set_star_catalog(
            self._handle,
            cells.ctypes.data_as(floats),
            int(cells.shape[0]),
            stars.ctypes.data_as(floats),
            colors.ctypes.data_as(bytes_),
            int(stars.shape[0]),
            catalog.note.encode("utf-8"),
        ):
            raise RuntimeError(self.last_error())

    def set_export_directory(self, path: Path) -> None:
        self._lib.bh_set_export_directory(self._handle, _native_path(path))

    def set_ffmpeg(self, path: str) -> None:
        self._lib.bh_set_ffmpeg(_native_path(path) if path else b"")

    def set_credit(self, which: int, image: QImage | None) -> None:
        if image is None:
            self._lib.bh_set_credit(self._handle, which, None, 0, 0)
            return
        data, width, height = _rgba_bytes(image)
        self._lib.bh_set_credit(self._handle, which, data, width, height)

    def set_overlay(self, slot: int, image: QImage | None) -> None:
        if image is None:
            self._lib.bh_set_overlay(self._handle, slot, None, 0, 0)
            return
        data, width, height = _rgba_bytes(image)
        self._lib.bh_set_overlay(self._handle, slot, data, width, height)

    def frame(self, state: _Input, dt: float, width: int, height: int, fbo: int) -> bool:
        return bool(self._lib.bh_frame(self._handle, ctypes.byref(state), dt, width, height, fbo))

    def save_png(self, path: str, width: int, height: int) -> bool:
        return bool(self._lib.bh_save_png(self._handle, _native_path(path), width, height))

    def reset_scene(self) -> None:
        self._lib.bh_reset_scene(self._handle)

    def state(self) -> EngineState:
        out = EngineState()
        self._lib.bh_state(self._handle, ctypes.byref(out))
        return out

    def status(self) -> str:
        return _text(self._lib.bh_status, self._handle)

    def flow_status(self) -> str:
        return _text(self._lib.bh_flow_status, self._handle)

    def movie_status(self) -> str:
        return _text(self._lib.bh_movie_status, self._handle)

    def movie_set_a(self) -> None:
        self._lib.bh_movie_set_a(self._handle)

    def movie_set_b(self) -> None:
        self._lib.bh_movie_set_b(self._handle)

    def movie_request(self) -> None:
        self._lib.bh_movie_request(self._handle)

    def movie_stop(self) -> None:
        self._lib.bh_movie_stop(self._handle)

    def movie_set_destination(self, path: str) -> None:
        self._lib.bh_movie_set_destination(self._handle, _native_path(path) if path else b"")

    def camera_minimum(self) -> float:
        return self._lib.bh_camera_minimum(self._handle)

    def camera_horizon(self) -> float:
        return self._lib.bh_camera_horizon(self._handle)

    def kerr_horizon(self, spin: float) -> float:
        return self._lib.bh_kerr_horizon(spin)

    def kerr_isco(self, spin: float) -> float:
        return self._lib.bh_kerr_isco(spin)

    def gravitational_seconds(self, solar_masses: float) -> float:
        return self._lib.bh_gravitational_seconds(solar_masses)


def _rgba_bytes(image: QImage) -> tuple[bytes, int, int]:
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = rgba.width(), rgba.height()
    row = width * 4
    raw = bytes(rgba.constBits())[: rgba.sizeInBytes()]
    if rgba.bytesPerLine() != row:
        stride = rgba.bytesPerLine()
        raw = b"".join(raw[y * stride : y * stride + row] for y in range(height))
    return raw, width, height


def _lab_font(pixel_size: float) -> QFont:
    font = QFont("Segoe UI")
    font.setPixelSize(max(1, int(round(pixel_size))))
    return font


def credit_badge(pixel_size: float, rounding: float) -> QImage:
    """The lab's sky credit: grey text on a translucent black box (5 x 3 px padding)."""

    font = _lab_font(pixel_size)
    metrics = QFontMetrics(font)
    text_width = metrics.horizontalAdvance(SKY_CREDIT)
    text_height = font.pixelSize()
    image = QImage(text_width + 10, text_height + 6, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 150))
    painter.drawRoundedRect(QRectF(0, 0, image.width(), image.height()), rounding, rounding)
    painter.setFont(font)
    painter.setPen(QColor(190, 195, 205))
    painter.drawText(QRectF(5, 3, text_width + 1, text_height), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, SKY_CREDIT)
    painter.end()
    return image


def preparing_box(status: str, scale: float) -> QImage:
    """The lab's "Preparing disk" window: status line over a dim explanation."""

    font = _lab_font(17 * scale)
    metrics = QFontMetrics(font)
    lines = (status, "Building gas history before playback")
    pad_x, pad_y, spacing = 18 * scale, 16 * scale, 8 * scale
    line_height = font.pixelSize()
    width = int(max(metrics.horizontalAdvance(line) for line in lines) + 2 * pad_x)
    height = int(2 * line_height + spacing + 2 * pad_y)
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.fillRect(0, 0, width, height, QColor.fromRgbF(0.035, 0.043, 0.058, 0.9))
    painter.setPen(QColor(110, 110, 128, 128))
    painter.drawRect(0, 0, width - 1, height - 1)
    painter.setFont(font)
    for index, (line, color) in enumerate(zip(lines, (QColor(255, 255, 255), QColor(128, 128, 128)))):
        painter.setPen(color)
        top = pad_y + index * (line_height + spacing)
        painter.drawText(QRectF(pad_x, top, width - pad_x, line_height), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
    painter.end()
    return image


class _Drag:
    __slots__ = ("held", "origin", "dragging")

    def __init__(self) -> None:
        self.held = False
        self.origin = QPointF()
        self.dragging = False


class BlackHoleViewport(QOpenGLWindow):
    """OpenGL 3.3 core window that runs one lab frame per vsync while exposed."""

    failed = Signal(str)
    frameRendered = Signal()
    star_catalog_failed = Signal(str)
    toggleControls = Signal()

    DRAG_THRESHOLD = 6.0

    def __init__(self) -> None:
        super().__init__(QOpenGLWindow.UpdateBehavior.NoPartialUpdate)
        fmt = QSurfaceFormat()
        fmt.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        fmt.setDepthBufferSize(0)
        fmt.setStencilBufferSize(0)
        fmt.setSwapInterval(1)
        self.setFormat(fmt)
        self.engine: BlackHoleEngine | None = None
        self.sky_note = SKY_CREDIT
        self.frame_error = ""
        self._clock = QElapsedTimer()
        self._drags = {
            Qt.MouseButton.LeftButton: _Drag(),
            Qt.MouseButton.RightButton: _Drag(),
            Qt.MouseButton.MiddleButton: _Drag(),
        }
        self._last_mouse: QPointF | None = None
        self._dx = 0.0
        self._dy = 0.0
        self._wheel = 0.0
        self._keys: set[int] = set()
        self._shift = False
        self._press_space = False
        self._press_r = False
        self._credit_ratio = 0.0
        self._preparing_text = ""
        self._frame_times: deque[float] = deque(maxlen=60)
        self._star_catalog: StarCatalogTextures | None = None
        self._star_catalog_uploaded = True
        self.frameSwapped.connect(self._next_frame)

    # Lifecycle -----------------------------------------------------------

    def initializeGL(self) -> None:  # noqa: N802
        lib = load_library()
        if lib is None:
            self.failed.emit(library_error())
            return
        try:
            engine = BlackHoleEngine(lib)
        except Exception as exc:
            self.failed.emit(str(exc).strip() or "OpenGL 3.3 is required for the black hole view.")
            return
        self.engine = engine
        path = resolve_cast_milky_way_path()
        image = QImage(str(path)) if path is not None else QImage()
        if image.isNull():
            self.sky_note = "CAst's Milky Way texture was not found; showing procedural stars."
        else:
            try:
                engine.set_sky(image)
            except Exception as exc:
                self.sky_note = f"Milky Way unavailable: {exc}"
        engine.set_export_directory(default_export_directory())
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

    def set_star_catalog(self, catalog: StarCatalogTextures | None) -> None:
        """Uploaded on the next frame, and again if the GL context is recreated."""

        self._star_catalog = catalog
        self._star_catalog_uploaded = False
        self.requestUpdate()

    # Rendering -----------------------------------------------------------

    def paintGL(self) -> None:  # noqa: N802
        engine = self.engine
        if engine is None or not engine.alive:
            return
        dt = self._clock.restart() / 1000.0 if self._clock.isValid() else 0.0
        if dt > 0:
            self._frame_times.append(dt)
        width, height = self.framebuffer_size()
        if not self._star_catalog_uploaded:
            self._star_catalog_uploaded = True
            try:
                engine.set_star_catalog(self._star_catalog)
            except RuntimeError as exc:
                self.star_catalog_failed.emit(str(exc))
        self._sync_overlays(engine)
        ratio = max(1.0, float(self.devicePixelRatio()))
        state = _Input()
        state.mouseDx = self._dx * ratio
        state.mouseDy = self._dy * ratio
        state.wheel = self._wheel
        state.dragLeft = int(self._drags[Qt.MouseButton.LeftButton].dragging)
        state.dragRight = int(self._drags[Qt.MouseButton.RightButton].dragging)
        state.dragMiddle = int(self._drags[Qt.MouseButton.MiddleButton].dragging)
        keys = self._keys
        state.keyW = int(Qt.Key.Key_W in keys)
        state.keyA = int(Qt.Key.Key_A in keys)
        state.keyS = int(Qt.Key.Key_S in keys)
        state.keyD = int(Qt.Key.Key_D in keys)
        state.keyQ = int(Qt.Key.Key_Q in keys)
        state.keyE = int(Qt.Key.Key_E in keys)
        state.shift = int(self._shift)
        state.pressSpace = int(self._press_space)
        state.pressR = int(self._press_r)
        state.mouse = 1
        state.keyboard = int(self.isActive())
        self._dx = self._dy = self._wheel = 0.0
        self._press_space = self._press_r = False
        if engine.frame(state, dt, width, height, int(self.defaultFramebufferObject())):
            self.frame_error = ""
        else:
            self.frame_error = engine.last_error()
        self.frameRendered.emit()

    def _sync_overlays(self, engine: BlackHoleEngine) -> None:
        ratio = max(1.0, float(self.devicePixelRatio()))
        if ratio != self._credit_ratio:
            badge = credit_badge(12 * ratio, 3 * ratio)
            engine.set_overlay(0, badge)
            engine.set_credit(0, badge)
            self._credit_ratio = ratio
        text = engine.flow_status() if engine.state().preparing else ""
        if text != self._preparing_text:
            engine.set_overlay(1, preparing_box(text, ratio) if text else None)
            self._preparing_text = text

    def save_png(self, path: str) -> None:
        if self.engine is None:
            return
        width, height = self.framebuffer_size()
        self.makeCurrent()
        try:
            self.engine.save_png(path, width, height)
        finally:
            self.doneCurrent()
        self._clock.restart()

    def with_context(self, action) -> None:
        if self.engine is None:
            return
        self.makeCurrent()
        try:
            action(self.engine)
        finally:
            self.doneCurrent()

    # Input ---------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        drag = self._drags.get(event.button())
        if drag is not None:
            drag.held = True
            drag.origin = event.position()
            drag.dragging = False
        self._last_mouse = event.position()
        self.requestActivate()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        drag = self._drags.get(event.button())
        if drag is not None:
            drag.held = False
            drag.dragging = False
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        position = event.position()
        if self._last_mouse is not None:
            delta = position - self._last_mouse
            self._dx += delta.x()
            self._dy += delta.y()
        self._last_mouse = position
        ratio = max(1.0, float(self.devicePixelRatio()))
        for drag in self._drags.values():
            if drag.held and not drag.dragging:
                offset = (position - drag.origin) * ratio
                if math.hypot(offset.x(), offset.y()) >= self.DRAG_THRESHOLD:
                    drag.dragging = True
        event.accept()

    def wheelEvent(self, event) -> None:  # noqa: N802
        self._wheel += event.angleDelta().y() / 120.0
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        self._shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        key = event.key()
        if event.isAutoRepeat():
            event.accept()
            return
        if key == Qt.Key.Key_F1:
            self.toggleControls.emit()
        elif key == Qt.Key.Key_Space:
            self._press_space = True
        elif key == Qt.Key.Key_R:
            self._press_r = True
        self._keys.add(int(key))
        event.accept()

    def keyReleaseEvent(self, event) -> None:  # noqa: N802
        self._shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if not event.isAutoRepeat():
            self._keys.discard(int(event.key()))
        event.accept()

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self._keys.clear()
        self._shift = False
        for drag in self._drags.values():
            drag.held = drag.dragging = False
        super().focusOutEvent(event)


# Controls ------------------------------------------------------------------


class _JumpSlider(QSlider):
    """Clicking the track jumps to that point, as the lab's sliders do."""

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            option = QStyleOptionSlider()
            self.initStyleOption(option)
            handle = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self)
            if not handle.contains(event.position().toPoint()):
                groove = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderGroove, self)
                span = max(1, groove.width() - handle.width())
                offset = int(event.position().x()) - groove.x() - handle.width() // 2
                self.setValue(QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), offset, span))
        super().mousePressEvent(event)


class LabSlider(QWidget):
    """A labelled float slider with the lab's range, format and log scaling."""

    edited = Signal(float)
    STEPS = 1000

    def __init__(self, label: str, minimum: float, maximum: float, fmt: str, *, log: bool = False, tooltip: str = "", parent=None) -> None:
        super().__init__(parent)
        self._minimum = minimum
        self._maximum = maximum
        self._fmt = fmt
        self._log = log
        self._value = minimum
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self._name = QLabel(label, self)
        self._readout = QLabel(self)
        self._readout.setObjectName("blackHoleValue")
        self._readout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self._name)
        top.addStretch(1)
        top.addWidget(self._readout)
        layout.addLayout(top)
        self._slider = _JumpSlider(Qt.Orientation.Horizontal, self)
        self._slider.setRange(0, self.STEPS)
        self._slider.valueChanged.connect(self._moved)
        layout.addWidget(self._slider)
        if tooltip:
            self.setToolTip(tooltip)
        self._show(minimum)

    @property
    def label(self) -> str:
        return self._name.text()

    def value(self) -> float:
        return self._value

    def set_range(self, minimum: float, maximum: float) -> None:
        if minimum == self._minimum and maximum == self._maximum:
            return
        self._minimum, self._maximum = minimum, maximum
        self._place(self._value)

    def set_value(self, value: float) -> None:
        if self._slider.isSliderDown() and value == self._value:
            return
        if value != self._value:
            self._show(value)
        if not self._slider.isSliderDown():
            self._place(value)

    def _fraction(self, value: float) -> float:
        low, high = self._minimum, self._maximum
        if high <= low:
            return 0.0
        value = min(high, max(low, value))
        if self._log and low > 0:
            return math.log(value / low) / math.log(high / low)
        return (value - low) / (high - low)

    def _from_fraction(self, fraction: float) -> float:
        low, high = self._minimum, self._maximum
        if self._log and low > 0:
            return low * (high / low) ** fraction
        return low + (high - low) * fraction

    def _place(self, value: float) -> None:
        position = int(round(self._fraction(value) * self.STEPS))
        if position != self._slider.value():
            self._slider.blockSignals(True)
            self._slider.setValue(position)
            self._slider.blockSignals(False)

    def _show(self, value: float) -> None:
        self._value = value
        self._readout.setText(self._fmt % value)

    def _moved(self, position: int) -> None:
        value = self._from_fraction(position / self.STEPS)
        self._show(value)
        self.edited.emit(value)


class LabCombo(QWidget):
    edited = Signal(int)

    def __init__(self, label: str, items: tuple[str, ...] | list[str], *, tooltip: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._name = QLabel(label, self)
        self.combo = QComboBox(self)
        self.combo.addItems(list(items))
        self.combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self._name)
        layout.addWidget(self.combo, 1)
        self.combo.currentIndexChanged.connect(self.edited.emit)
        if tooltip:
            self.setToolTip(tooltip)

    def set_index(self, index: int) -> None:
        if index != self.combo.currentIndex():
            self.combo.blockSignals(True)
            self.combo.setCurrentIndex(index)
            self.combo.blockSignals(False)


class _Section(QWidget):
    def __init__(self, title: str, expanded: bool, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._toggle = QToolButton(self)
        self._toggle.setObjectName("blackHoleSection")
        self._toggle.setText(title.replace("&", "&&"))
        self._toggle.setCheckable(True)
        self._toggle.setChecked(expanded)
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._toggle.toggled.connect(self._sync)
        layout.addWidget(self._toggle)
        self.body = QWidget(self)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(4, 0, 0, 6)
        self.body_layout.setSpacing(8)
        layout.addWidget(self.body)
        self._sync(expanded)

    def add(self, widget: QWidget) -> QWidget:
        self.body_layout.addWidget(widget)
        return widget

    def _sync(self, expanded: bool) -> None:
        self._toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.body.setVisible(expanded)


def _note(text: str, parent: QWidget, *, dim: bool = True) -> QLabel:
    label = QLabel(text, parent)
    label.setWordWrap(True)
    if dim:
        label.setObjectName("blackHoleDim")
    return label


def _row(*widgets: QWidget) -> QWidget:
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    for widget in widgets:
        layout.addWidget(widget)
    layout.addStretch(1)
    return holder


_STYLE = """
#blackHoleControls { background: #090b0f; }
#blackHoleControls QLabel { color: #d9dde4; }
#blackHoleTitle { color: #ffb861; font-weight: 600; letter-spacing: 1px; }
#blackHoleDim, #blackHoleValue { color: #8a8f99; }
#blackHoleSection { text-align: left; padding: 5px 4px; font-weight: 600; color: #e7e9ee;
    background: #30291f; border: none; border-radius: 3px; }
#blackHoleControls QSlider::groove:horizontal { height: 4px; background: #171c24; border-radius: 2px; }
#blackHoleControls QSlider::handle:horizontal { background: #d98c40; width: 12px; margin: -5px 0; border-radius: 3px; }
#blackHoleControls QSlider::handle:horizontal:disabled { background: #4a3a2a; }
#blackHoleControls QPushButton { background: #403020; color: #f0e6d8; border: none; border-radius: 4px; padding: 5px 10px; }
#blackHoleControls QPushButton:hover { background: #664824; }
#blackHoleControls QPushButton:disabled { background: #221c16; color: #6f6a64; }
#blackHoleControls QCheckBox::indicator:checked { background: #ffb85e; border: 1px solid #ffb85e; }
"""


class BlackHolePanel(QWidget):
    """Black Hole mode: the lab's control panel beside its view."""

    PANEL_WIDTH = 340

    _star_catalog_loaded = Signal(object, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("blackHolePanel")
        self._viewport: BlackHoleViewport | None = None
        self._container: QWidget | None = None
        self._failed = False
        self._suppress = False
        self._catalog_state = "idle"
        self._catalog_textures: StarCatalogTextures | None = None
        self._catalog_error = ""
        self._star_catalog_loaded.connect(self._finish_star_catalog)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._controls = QWidget(self)
        self._controls.setObjectName("blackHoleControls")
        self._controls.setStyleSheet(_STYLE)
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
    def viewport(self) -> BlackHoleViewport | None:
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
        viewport = BlackHoleViewport()
        viewport.failed.connect(self._show_failure)
        viewport.frameRendered.connect(self._refresh)
        viewport.toggleControls.connect(self._toggle_controls)
        viewport.star_catalog_failed.connect(self._star_catalog_upload_failed)
        if self._catalog_textures is not None:
            viewport.set_star_catalog(self._catalog_textures)
        container = QWidget.createWindowContainer(viewport, self._view_host)
        container.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        container.setMinimumSize(320, 240)
        self._view_host.layout().addWidget(container)
        self._viewport = viewport
        self._container = container

    def _show_failure(self, message: str) -> None:
        self._failed = True
        self._fallback.setText(message or "OpenGL 3.3 is required for the black hole view.")
        self._fallback.show()
        if self._container is not None:
            self._container.hide()
        self._controls.setEnabled(False)

    def _toggle_controls(self) -> None:
        self._scroll.setVisible(not self._scroll.isVisible())

    # Catalog stars -----------------------------------------------------------

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
            textures = load_star_catalog_textures()
        except Exception as exc:  # noqa: BLE001 - reported in the panel
            self._star_catalog_loaded.emit(None, str(exc) or type(exc).__name__)
            return
        error = "the download did not finish; check the internet connection" if download and textures is None else ""
        self._star_catalog_loaded.emit(textures, error)

    def _finish_star_catalog(self, textures: StarCatalogTextures | None, error: str) -> None:
        self._catalog_textures = textures
        self._catalog_error = error
        self._catalog_state = "ready" if textures is not None else "failed" if error else "missing"
        if self._viewport is not None:
            self._viewport.set_star_catalog(textures)

    def _star_catalog_upload_failed(self, message: str) -> None:
        self._catalog_textures = None
        self._catalog_error = message
        self._catalog_state = "failed"

    def _catalog_message(self) -> str:
        state = self._catalog_state
        if state == "ready" and self._catalog_textures is not None:
            return f"{self._catalog_textures.note}."
        if state == "loading":
            return "Loading catalog stars..."
        if state == "downloading":
            return "Downloading catalog stars. Random stars are shown until it finishes."
        if state == "missing":
            return "No star catalog is downloaded yet. Random stars are shown instead."
        if state == "failed":
            return f"Could not load catalog stars ({self._catalog_error}). Random stars are shown instead."
        return ""

    def _engine(self) -> BlackHoleEngine | None:
        viewport = self._viewport
        if viewport is None or viewport.engine is None or not viewport.engine.alive:
            return None
        return viewport.engine

    def _set(self, name: str, value: float) -> None:
        engine = self._engine()
        if engine is not None and not self._suppress:
            engine.set(name, value)

    def _binder(self, name: str, factor: float = 1.0):
        return lambda value: self._set(name, value / factor)

    # Construction ----------------------------------------------------------

    def _slider(self, section_layout, key: str, label: str, low: float, high: float, fmt: str, *, log=False, tooltip="", factor=1.0) -> LabSlider:
        slider = LabSlider(label, low, high, fmt, log=log, tooltip=tooltip, parent=self._controls)
        slider.edited.connect(self._binder(key, factor))
        section_layout.addWidget(slider)
        self._sliders[key] = (slider, factor)
        return slider

    def _check(self, section_layout, key: str, label: str, tooltip: str = "") -> QCheckBox:
        box = QCheckBox(label, self._controls)
        if tooltip:
            box.setToolTip(tooltip)
        box.toggled.connect(lambda checked, k=key: self._set(k, 1.0 if checked else 0.0))
        section_layout.addWidget(box)
        self._checks[key] = box
        return box

    def _combo(self, section_layout, key: str, label: str, items, tooltip: str = "") -> LabCombo:
        combo = LabCombo(label, items, tooltip=tooltip, parent=self._controls)
        combo.edited.connect(lambda index, k=key: self._set(k, index))
        section_layout.addWidget(combo)
        self._combos[key] = combo
        return combo

    def _button(self, label: str, action, tooltip: str = "") -> QPushButton:
        button = QPushButton(label, self._controls)
        if tooltip:
            button.setToolTip(tooltip)
        button.clicked.connect(action)
        return button

    def _build_controls(self) -> None:
        self._sliders: dict[str, tuple[LabSlider, float]] = {}
        self._checks: dict[str, QCheckBox] = {}
        self._combos: dict[str, LabCombo] = {}
        root = QVBoxLayout(self._controls)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(8)
        parent = self._controls

        self._scene_group = QWidget(parent)
        scene = QVBoxLayout(self._scene_group)
        scene.setContentsMargins(0, 0, 0, 0)
        scene.setSpacing(8)
        root.addWidget(self._scene_group)

        title = QLabel("BLACK HOLE", parent)
        title.setObjectName("blackHoleTitle")
        scene.addWidget(title)
        scene.addWidget(_note("KERR / SCHWARZSCHILD LIGHT LAB", parent))
        self._combo(scene, "metric", "Spacetime", ("Schwarzschild", "Kerr (rotating)"))
        self._spin = self._slider(
            scene, "spin", "Spin", 0.0, 0.9, "a* = %.2f",
            tooltip="Dimensionless spin, prograde disk. Zero spin can be compared with the independent Schwarzschild solver. Near-extremal spins are excluded.",
        )
        self._cinematic_spin = self._button("Cinematic 0.6", self._cinematic_spin_clicked)
        self._cinematic_spin_row = _row(self._cinematic_spin)
        scene.addWidget(self._cinematic_spin_row)
        self._radii = _note("", parent)
        scene.addWidget(self._radii)
        self._play = self._button("Pause", lambda: self._toggle("play"))
        self._play.setFixedWidth(85)
        scene.addWidget(_row(self._play, self._button("Reset scene", self._reset_scene)))
        self._check(
            scene, "physical", "Physical appearance",
            "Includes gravitational redshift, gas-velocity Doppler shifts and g^4 bolometric brightness. Emission and RGB colors remain illustrative.",
        )
        self._cinematic_note = _note("Cinematic color / balanced brightness", parent)
        scene.addWidget(self._cinematic_note)
        self._status = _note("", parent, dim=False)
        scene.addWidget(self._status)

        camera = _Section("Camera", False, parent)
        scene.addWidget(camera)
        c = camera.body_layout
        self._distance = self._slider(c, "distance", "Distance", 2.02, 200.0, "%.3f rg", log=True)
        self._clearance = LabSlider(
            "Horizon clearance", 0.02, 198.0, "%.3f rg", log=True,
            tooltip="Coordinate distance above the event horizon. A 0.02 rg margin keeps the exterior ray solver outside its capture boundary.",
            parent=parent,
        )
        self._clearance.edited.connect(self._clearance_edited)
        c.addWidget(self._clearance)
        c.addWidget(_row(self._button("Near horizon", self._near_horizon), self._button("Look outward", self._look_outward)))
        self._slider(
            c, "inclination", "Inclination", 1.0, 179.0, "%.1f deg",
            tooltip="Measured from the disk axis: 90 degrees is edge-on. Volumetric gas stays visible at grazing angles.",
        )
        self._slider(c, "yaw", "Azimuth", -180.0, 180.0, "%.1f deg")
        self._slider(c, "fov", "Field of view", 15.0, 80.0, "%.0f deg")
        c.addWidget(_note("Aim & framing", parent))
        aim_tip = "Aim independently of camera position. Right-drag to look; the black hole can move off-screen."
        self._slider(c, "pan", "Pan", -180.0, 180.0, "%.1f deg", tooltip=aim_tip)
        self._slider(c, "tilt", "Tilt", -85.0, 85.0, "%.1f deg", tooltip=aim_tip)
        self._slider(c, "roll", "Roll", -180.0, 180.0, "%.1f deg", tooltip=aim_tip)
        c.addWidget(_row(self._button("Center aim / level", self._center_aim)))
        self._slider(
            c, "cameraSmoothing", "Smoothing", 0.0, 1.0, "%.2f s",
            tooltip="Smooths mouse, keyboard and slider camera movement. Zero gives immediate response. Movie paths use their own exact timing.",
        )
        self._slider(c, "moveSpeed", "Move speed", 1.0, 60.0, "%.0f rg/s")
        c.addWidget(_note("Right-drag: look. Middle-drag: truck. WASD: move, Q/E: down/up. Hold Shift for faster movement.", parent, dim=False))
        self._check(c, "orbit", "Orbit camera")
        self._orbit_speed = self._slider(c, "orbitSpeed", "Orbit speed", -30.0, 30.0, "%.1f deg/s")

        effects = _Section("Visual effects", True, parent)
        scene.addWidget(effects)
        e = effects.body_layout
        self._milky_way = self._check(
            e, "milkyWay", "Milky Way background",
            "CAst's all-sky Milky Way, the texture Sky Atlas uses. Switching off restores procedural stars. Grid and black backgrounds override it.",
        )
        self._milky_way.toggled.connect(lambda checked: checked and self._set("background", 0))
        self._sky_controls = QWidget(parent)
        sc = QVBoxLayout(self._sky_controls)
        sc.setContentsMargins(0, 0, 0, 0)
        sc.setSpacing(8)
        e.addWidget(self._sky_controls)
        self._slider(sc, "skyBrightness", "Sky brightness", 0.0, 2.0, "%.2f")
        self._slider(sc, "skyRotation", "Sky rotation", -180.0, 180.0, "%.0f deg")
        self._slider(
            sc, "skyContrast", "Sky contrast", 0.3, 3.0, "%.2f",
            tooltip="Steepens or flattens the Milky Way's brightness curve. Above 1, dust lanes darken and the bright band stands out.",
        )
        self._slider(
            sc, "skySaturation", "Sky saturation", 0.0, 3.0, "%.2f",
            tooltip="0 is greyscale; 1 is the original panorama colors; higher values intensify them.",
        )
        self._slider(
            sc, "skyBlackPoint", "Sky black level", 0.0, 0.5, "%.2f",
            tooltip="Clips the darkest part of the panorama to black, removing faint haze from empty sky.",
        )
        self._slider(
            sc, "skyWarmth", "Sky warmth", -1.0, 1.0, "%+.2f",
            tooltip="Shifts the Milky Way toward blue (negative) or gold (positive) without changing its brightness.",
        )
        self._check(
            sc, "stars", "Star overlay",
            "Adds sharp, colored stars over the Milky Way. They are lensed by the black hole with the rest of the sky. Decorative: positions are random, not a star catalog.",
        )
        self._star_controls = QWidget(parent)
        st = QVBoxLayout(self._star_controls)
        st.setContentsMargins(0, 0, 0, 0)
        st.setSpacing(8)
        sc.addWidget(self._star_controls)
        self._check(
            st, "starCatalog", "Real catalog stars",
            "Real Hipparcos (and Tycho-2, if downloaded) stars from Sky Atlas's catalog cache, at their true positions behind the Milky Way, colored by their measured B-V color. Brightness is compressed for display.",
        )
        self._catalog_controls = QWidget(parent)
        cat = QVBoxLayout(self._catalog_controls)
        cat.setContentsMargins(0, 0, 0, 0)
        cat.setSpacing(8)
        st.addWidget(self._catalog_controls)
        self._slider(
            cat, "starMagnitudeLimit", "Faintest star", 3.0, 9.5, "V %.1f",
            tooltip="Catalog stars fainter than this magnitude are hidden. About 6.5 matches a dark naked-eye sky.",
        )
        self._catalog_note = _note("", parent)
        cat.addWidget(self._catalog_note)
        self._catalog_download = self._button("Download star catalog", lambda: self._start_star_catalog(download=True))
        self._catalog_download.setToolTip(
            "Downloads Hipparcos stars to V 9.5 into Sky Atlas's catalog folder, the same files Sky Atlas uses."
        )
        cat.addWidget(_row(self._catalog_download))
        self._slider(st, "starBrightness", "Star brightness", 0.0, 4.0, "%.2f")
        self._random_star_controls = QWidget(parent)
        rs = QVBoxLayout(self._random_star_controls)
        rs.setContentsMargins(0, 0, 0, 0)
        rs.setSpacing(8)
        st.addWidget(self._random_star_controls)
        self._slider(
            rs, "starDensity", "Star density", 0.0, 3.0, "%.2f",
            tooltip="How many stars appear. Brightness per star is unchanged.",
        )
        self._slider(
            rs, "starFollow", "Follow Milky Way", 0.0, 1.0, "%.2f",
            tooltip="0 spreads stars evenly; 1 crowds them into the bright galactic band and thins them in dark sky.",
        )
        self._slider(
            st, "starSize", "Star size", 0.5, 3.0, "%.2fx",
            tooltip="Scales every star. Stars never shrink below about half a screen pixel.",
        )
        self._slider(
            st, "starColor", "Star color", 0.0, 1.0, "%.2f",
            tooltip="0 makes every star white; 1 gives the full range of star colors.",
        )
        self._sky_credit = _note(SKY_CREDIT, parent)
        self._sky_credit.setToolTip("Adapted by rotation, brightness, filtering and lensing.")
        e.addWidget(self._sky_credit)
        self._cinematic_effects = QWidget(parent)
        ce = QVBoxLayout(self._cinematic_effects)
        ce.setContentsMargins(0, 0, 0, 0)
        ce.setSpacing(8)
        e.addWidget(self._cinematic_effects)
        self._space_blend = self._slider(
            ce, "spaceBlend", "Space blending", 0.0, 1.0, "%.2f",
            tooltip="Lets the lensed star field show through dim gas, so the outskirts blend into space. Bright gas stays opaque. 0 restores density-based absorption. Cinematic volumetric emission with a star background only.",
        )
        self._check(ce, "cinematicGrade", "Golden inner disk", "Bright golden-yellow inner gas, fading through amber to dim orange outskirts.")
        self._check(
            ce, "particles", "Fine orbiting particles",
            "Tiny hot glints in the disk's own color ride the orbiting gas and flare like satellite flares: a quick brighten, then a fade. Some last much longer than others. Purely decorative; gravitational lensing still applies.",
        )
        self._particle_controls = QWidget(parent)
        pc = QVBoxLayout(self._particle_controls)
        pc.setContentsMargins(0, 0, 0, 0)
        pc.setSpacing(8)
        ce.addWidget(self._particle_controls)
        self._slider(
            pc, "particleStrength", "Particle amount", 0.0, 3.0, "%.2f",
            tooltip="How many glints flare at once. 1 is the default; 3 lights about three times as many. Brightness is unchanged.",
        )
        self._slider(
            pc, "particleBrightness", "Particle brightness", 0.0, 4.0, "%.2f",
            tooltip="Peak brightness of each glint relative to the gas it rides in.",
        )
        self._slider(
            pc, "particleSize", "Particle size", 0.5, 3.0, "%.2fx",
            tooltip="Scales every glint. Glints never shrink below about half a screen pixel, so very small values mainly dim them.",
        )
        self._slider(
            pc, "particleSizeVariety", "Size variety", 0.0, 2.0, "%.2f",
            tooltip="0 makes all glints the same size; higher values mix tiny specks with larger ones.",
        )
        self._slider(
            pc, "particleLifetime", "Flare duration", 0.25, 4.0, "%.2fx", log=True,
            tooltip="Scales how long each flare lasts. Durations follow simulation time, so the playback rate also changes them on screen.",
        )
        self._slider(
            pc, "particleWhiteness", "Particle whiteness", 0.0, 1.0, "%.2f",
            tooltip="0 keeps each glint the color of the surrounding gas; 1 makes glints white-hot.",
        )
        self._effects_note = _note("Disk effects are active in cinematic emission view.", parent, dim=False)
        e.addWidget(self._effects_note)

        disk = _Section("Disk & time", False, parent)
        scene.addWidget(disk)
        d = disk.body_layout
        show_disk = QCheckBox("Show disk", parent)
        show_disk.toggled.connect(lambda checked: self._set("disk", 1.0 if checked else 0.0))
        self._checks["disk"] = show_disk
        d.addWidget(_row(show_disk, _note("Prograde", parent)))
        self._check(
            d, "fluid", "Evolving fluid",
            "Effective-potential gas dynamics with pressure, viscous transport and cooling. Not GRMHD. Changing flow parameters rebuilds 512 tg of history in the background.",
        )
        self._fluid_group = QWidget(parent)
        fg = QVBoxLayout(self._fluid_group)
        fg.setContentsMargins(0, 0, 0, 0)
        fg.setSpacing(8)
        d.addWidget(self._fluid_group)
        self._combo(
            fg, "flowBackend", "Fluid solver", ("Auto (prefer GPU)", "GPU", "CPU reference"),
            "GPU evolves the same gas model in 32-bit floating point. CPU retains the double-precision reference. Changing solver rebuilds history.",
        )
        self._combo(fg, "flowView", "Disk view", ("Emission", "Surface density", "Temperature", "Flow tracers"))
        self._detail_contrast = self._slider(
            fg, "detailContrast", "Detail contrast", 1.0, 2.5, "%.2f",
            tooltip="Emphasizes existing gas brightness variations around each ring's mean. 1 keeps their original contrast. Cinematic appearance only.",
        )
        self._check(
            fg, "volume", "Volumetric disk",
            "Integrates emission and absorption through a reconstructed gas layer. The underlying fluid simulation is still two-dimensional.",
        )
        self._thickness = self._slider(
            fg, "thickness", "Thickness", 0.002, 0.15, "H/r %.3f", log=True,
            tooltip="Vertical scale of the gas layer; 0.002 is a very thin disk. The outer layer tapers in height as well as brightness. Changes appearance without rebuilding the flow.",
        )
        self._opacity = self._slider(
            fg, "opacity", "Opacity", 0.0, 20.0, "%.2f",
            tooltip="Grey absorption strength. Dense foreground gas obscures material behind it. Zero makes the layer transparent.",
        )
        self._slider(fg, "viscosity", "Viscosity", 0.0, 0.3, "alpha %.3f")
        self._slider(
            fg, "stirring", "Stirring", 0.0, 1.0, "%.2f",
            tooltip="Prescribed disturbances representing unresolved forcing, not self-generated magnetic turbulence. Zero disables stirring.",
        )
        self._grid = LabCombo(
            "Fluid grid", ("32 x 64", "64 x 128", "128 x 256", "256 x 512"),
            tooltip="Higher grids resolve finer gas structures but need longer to prepare. 64 x 128 is useful for quick previews.",
            parent=parent,
        )
        self._grid.edited.connect(lambda index: self._set("flowGrid", 32 << index))
        fg.addWidget(self._grid)
        self._slider(d, "outer", "Outer radius", 8.0, 40.0, "%.1f rg")
        self._slider(
            d, "outerFade", "Outer fade", 5.0, 50.0, "%.0f%%", factor=100.0,
            tooltip="Tapers height, emitted light and opacity over this fraction of the disk span, so the outskirts narrow smoothly into space.",
        )
        self._temperature = self._slider(
            d, "temperature", "Temperature", 2000.0, 30000.0, "%.0f K",
            tooltip="Illustrative emission temperature scale. Fluid temperature color follows local radiative flux; luminosity is not calibrated.",
        )
        self._slider(
            d, "mass", "Mass", 1.0, 10000.0, "%.1f M solar", log=True,
            tooltip="Million solar masses. Changes the conversion between physical seconds and gravitational time units.",
        )
        self._slider(d, "speed", "Playback rate", 0.0, 10000.0, "%.0f s/s")
        self._check(
            d, "delays", "Light-travel delays",
            "Samples gas history at each ray's emission time. History spans 512 tg at 4 tg spacing; older emission times use the oldest available state.",
        )
        self._elapsed = _note("", parent)
        d.addWidget(self._elapsed)

        quality = _Section("Appearance & quality", False, parent)
        scene.addWidget(quality)
        q = quality.body_layout
        self._combo(q, "background", "Background", ("Stars", "Lensing grid", "Black"))
        self._slider(q, "exposure", "Exposure", 0.1, 5.0, "%.3f")
        self._slider(q, "bloom", "Bloom", 0.0, 1.0, "%.3f")
        self._combo(q, "quality", "Ray accuracy", ("Preview", "Standard", "Fine"))
        self._slider(q, "scale", "Render scale", 0.3, 1.0, "%.2f")
        self._samples = QCheckBox("4 samples per pixel", parent)
        self._samples.toggled.connect(lambda checked: self._set("samples", 4 if checked else 1))
        q.addWidget(self._samples)

        save = self._button(
            "Save PNG...", self._save_png,
            "Exports the current scene viewport without the controls, plus a text file with scene parameters.",
        )
        save.setMinimumHeight(30)
        scene.addWidget(save)

        self._build_movie(root, parent)
        self._fps = _note("", parent)
        root.addWidget(self._fps)
        root.addWidget(_note("Left drag: orbit / Right drag: look\nMiddle drag: truck / Wheel: distance\nWASD + Q/E: move / Shift: faster\nSpace: pause / F1: hide controls", parent, dim=False))
        root.addStretch(1)

    def _build_movie(self, root: QVBoxLayout, parent: QWidget) -> None:
        movie = _Section("Video export", True, parent)
        root.addWidget(movie)
        self._movie_form = QWidget(parent)
        m = QVBoxLayout(self._movie_form)
        m.setContentsMargins(0, 0, 0, 0)
        m.setSpacing(8)
        movie.add(self._movie_form)
        self._combo(m, "movie.shot", "Camera preset", SHOT_NAMES)
        self._custom_group = QWidget(parent)
        cg = QVBoxLayout(self._custom_group)
        cg.setContentsMargins(0, 0, 0, 0)
        cg.addWidget(self._button("Set A from camera", lambda: self._movie_call("movie_set_a")))
        cg.addWidget(self._button("Set B from camera", lambda: self._movie_call("movie_set_b")))
        self._custom_a = _note("", parent)
        self._custom_b = _note("", parent)
        cg.addWidget(self._custom_a)
        cg.addWidget(self._custom_b)
        cg.addWidget(_note("Endpoints include pan, tilt and roll. Turn preview off to position the camera.", parent, dim=False))
        m.addWidget(self._custom_group)
        self._flight_group = QWidget(parent)
        fl = QVBoxLayout(self._flight_group)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(8)
        self._flight_note = _note("", parent, dim=False)
        fl.addWidget(self._flight_note)
        self._slider(fl, "movie.altitude", "Flight altitude", 0.02, 40.0, "%.2f rg", log=True)
        self._impact = self._slider(fl, "movie.impact", "Side clearance", 2.02, 100.0, "%.2f rg", log=True)
        self._check(fl, "movie.left", "Pass on left")
        self._slider(fl, "movie.bank", "Bank angle", 0.0, 45.0, "%.0f deg")
        self._slider(fl, "movie.framing", "Off-center framing", 0.0, 1.0, "%.2f")
        self._arc = self._slider(fl, "movie.arc", "Turn arc", 30.0, 270.0, "%.0f deg")
        m.addWidget(self._flight_group)
        self._still_note = _note("Uses your current camera and scene appearance.", parent, dim=False)
        m.addWidget(self._still_note)
        self._sweep = self._slider(m, "movie.sweep", "Sweep", -360.0, 360.0, "%.0f deg")
        self._travel = self._slider(m, "movie.travel", "Travel", 0.1, 2.0, "%.2fx")
        self._check(m, "movie.ease", "Ease in / out")
        self._check(m, "movie.preview", "Preview shot")
        self._preview_play = self._check(m, "movie.previewPlay", "Play preview")
        self._preview_play.toggled.connect(self._preview_play_toggled)
        self._preview_position = self._slider(m, "movie.previewPosition", "Shot position", 0.0, 1.0, "%.2f")
        self._combo(m, "movie.resolution", "Resolution", ("720p", "1080p", "1440p", "4K UHD"))
        self._combo(m, "movie.rate", "Frame rate", ("24 fps", "30 fps", "60 fps"))
        self._slider(m, "movie.duration", "Duration", 1.0, 120.0, "%.0f seconds")
        self._check(m, "movie.freeze", "Freeze disk animation")
        self._combo(m, "movie.format", "Format", ("MP4 (H.264)", "PNG sequence"))
        output_row = QWidget(parent)
        ol = QHBoxLayout(output_row)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.addWidget(QLabel("Output path", output_row))
        self._destination = QLineEdit(output_row)
        self._destination.textChanged.connect(lambda text: self._movie_call("movie_set_destination", text))
        ol.addWidget(self._destination, 1)
        m.addWidget(output_row)
        m.addWidget(_row(self._button("Browse output...", self._browse_output)))
        m.addWidget(_note(
            f"Full-resolution export uses the selected ray accuracy and samples. MP4 uses CAst's bundled FFmpeg. "
            f"Blank path saves under {default_export_directory()}.",
            parent, dim=False,
        ))
        export = self._button("Export video / frames", self._export_movie)
        m.addWidget(export)

        self._movie_progress = QWidget(parent)
        mp = QVBoxLayout(self._movie_progress)
        mp.setContentsMargins(0, 0, 0, 0)
        self._progress = QProgressBar(self._movie_progress)
        self._progress.setTextVisible(False)
        mp.addWidget(self._progress)
        self._progress_text = _note("", parent, dim=False)
        mp.addWidget(self._progress_text)
        self._finalizing = _note("Finalizing file...", parent, dim=False)
        mp.addWidget(self._finalizing)
        mp.addWidget(_row(self._button("Stop export (keep partial)", lambda: self._movie_call("movie_stop"))))
        movie.add(self._movie_progress)
        self._movie_status = _note("", parent, dim=False)
        movie.add(self._movie_status)

    # Actions ---------------------------------------------------------------

    def _movie_call(self, method: str, *arguments) -> None:
        engine = self._engine()
        if engine is not None and not self._suppress:
            getattr(engine, method)(*arguments)

    def _toggle(self, key: str) -> None:
        engine = self._engine()
        if engine is not None:
            engine.set(key, 0.0 if engine.get(key) else 1.0)

    def _cinematic_spin_clicked(self) -> None:
        self._set("spin", 0.6)
        self._set("physical", 0)

    def _reset_scene(self) -> None:
        engine = self._engine()
        if engine is not None:
            engine.reset_scene()

    def _clearance_edited(self, clearance: float) -> None:
        engine = self._engine()
        if engine is not None:
            engine.set("distance", engine.camera_horizon() + clearance)

    def _near_horizon(self) -> None:
        engine = self._engine()
        if engine is not None:
            engine.set("distance", engine.camera_horizon() + 0.1)

    def _look_outward(self) -> None:
        self._set("pan", 180)
        self._set("tilt", 0)
        self._set("roll", 0)

    def _center_aim(self) -> None:
        for key in ("pan", "tilt", "roll"):
            self._set(key, 0)

    def _preview_play_toggled(self, checked: bool) -> None:
        engine = self._engine()
        if engine is not None and checked and engine.get("movie.previewPosition") >= 1:
            engine.set("movie.previewPosition", 0)

    def _save_png(self) -> None:
        viewport = self._viewport
        if viewport is None or viewport.engine is None:
            return
        directory = default_export_directory()
        path, _ = QFileDialog.getSaveFileName(self, "Save PNG", str(directory / "blackhole.png"), "PNG image (*.png);;All files (*.*)")
        if path:
            viewport.save_png(path)

    def _browse_output(self) -> None:
        engine = self._engine()
        if engine is None:
            return
        directory = default_export_directory()
        if int(engine.get("movie.format")) == 1:
            chosen = QFileDialog.getExistingDirectory(self, "Export frames", str(directory))
            if chosen:
                # Folder picker chooses a parent; use a fresh child for frames.
                chosen = str(Path(chosen) / f"blackhole-{time.time_ns()}")
        else:
            chosen, _ = QFileDialog.getSaveFileName(self, "Export movie", str(directory / "blackhole.mp4"), "MP4 movie (*.mp4);;All files (*.*)")
        if chosen:
            self._destination.setText(chosen)

    def _export_movie(self) -> None:
        engine = self._engine()
        if engine is None:
            return
        if int(engine.get("movie.format")) == 0:
            try:
                from photometry_app.core.animation_export import resolve_ffmpeg_executable

                engine.set_ffmpeg(resolve_ffmpeg_executable())
            except Exception:
                engine.set_ffmpeg("")
        height = MOVIE_HEIGHTS[int(engine.get("movie.resolution"))]
        engine.set_credit(1, credit_badge(max(12.0, height / 90.0), 0.0))
        engine.movie_request()

    # Refresh from the engine --------------------------------------------------

    def _refresh(self) -> None:
        engine = self._engine()
        viewport = self._viewport
        if engine is None or viewport is None:
            return
        get = engine.get
        self._suppress = True
        try:
            for key, (slider, factor) in self._sliders.items():
                slider.set_value(get(key) * factor)
            for key, box in self._checks.items():
                checked = bool(get(key))
                if box.isChecked() != checked:
                    box.blockSignals(True)
                    box.setChecked(checked)
                    box.blockSignals(False)
            for key, combo in self._combos.items():
                combo.set_index(int(get(key)))
            grid = int(get("flowGrid"))
            self._grid.set_index(3 if grid == 256 else 2 if grid == 128 else 1 if grid == 64 else 0)
            samples = get("samples") == 4
            if self._samples.isChecked() != samples:
                self._samples.blockSignals(True)
                self._samples.setChecked(samples)
                self._samples.blockSignals(False)
            self._sync_layout(engine, viewport)
        finally:
            self._suppress = False

    def _sync_layout(self, engine: BlackHoleEngine, viewport: BlackHoleViewport) -> None:
        get = engine.get
        state = engine.state()
        metric = int(get("metric"))
        spin = get("spin") if metric == 1 else 0.0
        physical = bool(get("physical"))
        fluid = bool(get("fluid"))
        volume = bool(get("volume"))
        flow_view = int(get("flowView"))
        background = int(get("background"))
        shot = int(get("movie.shot"))
        horizon = engine.camera_horizon()
        minimum = engine.camera_minimum()
        active = bool(state.movieActive)

        self._scene_group.setEnabled(not active)
        self._spin.setVisible(metric == 1)
        self._cinematic_spin_row.setVisible(metric == 1)
        self._set_text(self._radii, f"Horizon {engine.kerr_horizon(spin):.3f} rg | ISCO {engine.kerr_isco(spin):.3f} rg")
        self._set_text(self._play, "Pause" if get("play") else "Play")
        self._cinematic_note.setVisible(not physical)
        status = viewport.frame_error or engine.status()
        self._set_text(self._status, status)
        self._status.setVisible(bool(status))

        self._distance.set_range(minimum, 200.0)
        self._clearance.set_range(0.02, 200.0 - horizon)
        self._clearance.set_value(get("distance") - horizon)
        self._orbit_speed.setVisible(bool(get("orbit")))

        milky_way = bool(get("milkyWay"))
        for widget in (self._sky_controls, self._sky_credit):
            widget.setVisible(milky_way)
        stars = bool(get("stars"))
        self._star_controls.setVisible(stars)
        catalog = stars and bool(get("starCatalog"))
        if catalog and self._catalog_state == "idle":
            self._start_star_catalog()
        catalog_ready = catalog and self._catalog_state == "ready"
        self._catalog_controls.setVisible(catalog)
        self._random_star_controls.setVisible(not catalog_ready)
        self._set_text(self._catalog_note, self._catalog_message())
        self._catalog_download.setVisible(self._catalog_state in ("missing", "failed"))
        self._set_text(self._sky_credit, viewport.sky_note)
        effects_off = physical or (fluid and flow_view != 0)
        self._cinematic_effects.setEnabled(not effects_off)
        self._space_blend.setEnabled(fluid and volume and background == 0)
        self._particle_controls.setVisible(bool(get("particles")))
        self._effects_note.setVisible(effects_off)

        self._fluid_group.setVisible(fluid)
        self._detail_contrast.setVisible(not physical)
        self._thickness.setVisible(volume)
        self._opacity.setVisible(volume)
        self._temperature.setVisible(physical)
        elapsed = state.time
        hours = elapsed * engine.gravitational_seconds(get("mass") * 1e6) / 3600.0
        self._set_text(self._elapsed, f"Elapsed: {elapsed:.1f} tg / {hours:.2f} hours")

        self._movie_form.setEnabled(not active)
        self._custom_group.setVisible(shot == SHOT_CUSTOM)
        if shot == SHOT_CUSTOM:
            self._set_text(self._custom_a, "A: %.0f rg / %.0f deg / %.0f yaw" % (get("movie.start.distance"), get("movie.start.inclination"), get("movie.start.yaw")))
            self._set_text(self._custom_b, "B: %.0f rg / %.0f deg / %.0f yaw" % (get("movie.end.distance"), get("movie.end.inclination"), get("movie.end.yaw")))
        flight = shot in (SHOT_FLYBY, SHOT_SLINGSHOT)
        self._flight_group.setVisible(flight)
        if flight:
            self._set_text(
                self._flight_note,
                "Pass above the disk plane and alongside the hole, with drifting off-center framing."
                if shot == SHOT_FLYBY
                else "Approach, bank around the hole, then climb out. A cinematic maneuver inspired by Interstellar.",
            )
        self._impact.set_range(minimum, 100.0)
        self._arc.setVisible(shot == SHOT_SLINGSHOT)
        self._still_note.setVisible(shot not in (SHOT_CUSTOM, SHOT_FLYBY, SHOT_SLINGSHOT))
        self._sweep.setVisible(shot in (SHOT_ORBIT, SHOT_REVEAL))
        self._travel.setVisible(2 <= shot <= 5 or shot == SHOT_SLINGSHOT)
        preview = bool(get("movie.preview"))
        self._preview_play.setVisible(preview)
        self._preview_position.setVisible(preview)

        self._movie_progress.setVisible(active)
        if active:
            total = max(1, state.total)
            self._progress.setRange(0, total)
            self._progress.setValue(state.written)
            self._set_text(self._progress_text, f"{state.written} / {state.total} frames | {state.exportElapsed:.0f}s elapsed")
            self._finalizing.setVisible(state.submitted >= state.total)
        movie_status = engine.movie_status()
        self._set_text(self._movie_status, movie_status)
        self._movie_status.setVisible(bool(movie_status))
        fps = viewport.fps
        if fps > 0:
            self._set_text(self._fps, f"{fps:.1f} FPS | {1000.0 / fps:.1f} ms/frame")

    @staticmethod
    def _set_text(widget, text: str) -> None:
        if widget.text() != text:
            widget.setText(text)
