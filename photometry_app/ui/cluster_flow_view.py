"""Cluster Flow mode: the PhysicsEngine Cosmic Flow lab (cosmic_view) running inside CAst.

The gas solver, the Cluster flow and Cosmological volume models, the worker
thread, baking, cache replay, the volume renderer and MP4 export are the lab's
own C++ code, built as ``photometry_app/native/cast_cluster_flow.dll`` from
``native/cluster_flow``. This module supplies the Qt panel and OpenGL window
that replace the lab's ImGui panel and GLFW window. Tooltips are the lab's text.
"""

from __future__ import annotations

import ctypes
import sys
from collections import deque
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QPointF, QStandardPaths, Qt, Signal
from PySide6.QtGui import QGuiApplication, QSurfaceFormat
from PySide6.QtOpenGL import QOpenGLWindow
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from photometry_app.ui.blackhole_view import _STYLE, LabCombo, LabSlider, _native_path, _note, _row, _Section
from photometry_app.ui.galaxy_merger_view import _STYLE_EXTRA, LabNumber

LIBRARY_NAME = "cast_cluster_flow.dll"
GAS_GRIDS = (40, 64, 96, 128, 160)
GRAVITY_MESHES = (16, 32, 64, 128)
DISPLAY_FIELDS = ("Gas density", "Gas speed", "Temperature", "Compression / shocks")
ACCURACY = ("Fast / more diffusive", "Balanced", "Accurate", "Fine timesteps", "Custom")
MODELS = ("Cluster flow", "Cosmological volume")
MOVIE_SIZES = ("1280 x 720", "1920 x 1080", "3840 x 2160")


def library_path() -> Path:
    return Path(__file__).resolve().parents[1] / "native" / LIBRARY_NAME


def default_export_directory() -> Path:
    pictures = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
    base = Path(pictures) if pictures else Path.home()
    return base / "CAst Cluster Flow"


_library: ctypes.CDLL | None = None
_library_error = ""


def load_library() -> ctypes.CDLL | None:
    """The native Cosmic Flow lab, or None (with ``library_error()``) where it is unavailable."""

    global _library, _library_error
    if _library is not None or _library_error:
        return _library
    if sys.platform != "win32":
        _library_error = "The Cluster Flow simulation is available in the Windows build of CAst."
        return None
    path = library_path()
    try:
        lib = ctypes.CDLL(str(path))
    except OSError as exc:
        _library_error = f"The Cluster Flow engine could not be loaded ({path.name}): {exc}"
        return None
    handle, text, number, buffer = ctypes.c_void_p, ctypes.c_char_p, ctypes.c_double, ctypes.c_char_p
    signatures = {
        "cf_last_error": ([buffer, ctypes.c_int], ctypes.c_int),
        "cf_set_ffmpeg": ([text], None),
        "cf_help": ([text, buffer, ctypes.c_int], ctypes.c_int),
        "cf_verify_worker": ([], ctypes.c_int),
        "cf_create": ([], handle),
        "cf_destroy": ([handle], None),
        "cf_frame": ([handle, ctypes.c_int, ctypes.c_int, ctypes.c_uint], ctypes.c_int),
        "cf_get": ([handle, text, ctypes.POINTER(number)], ctypes.c_int),
        "cf_set": ([handle, text, number], ctypes.c_int),
        "cf_text": ([handle, text, buffer, ctypes.c_int], ctypes.c_int),
        "cf_action": ([handle, text], ctypes.c_int),
        "cf_bake": ([handle, text], ctypes.c_int),
        "cf_open_cache": ([handle, text], ctypes.c_int),
        "cf_export_mp4": ([handle, text], ctypes.c_int),
        "cf_save_image": ([handle, text], ctypes.c_int),
        "cf_drag": ([handle, number, number, ctypes.c_int, number], None),
        "cf_scroll": ([handle, number], None),
        "cf_render_png": ([handle, text, ctypes.c_int, ctypes.c_int], ctypes.c_int),
    }
    try:
        for name, (arguments, result) in signatures.items():
            function = getattr(lib, name)
            function.argtypes = arguments
            function.restype = result
    except AttributeError as exc:
        _library_error = f"The Cluster Flow engine is out of date: {exc}"
        return None
    _library = lib
    return lib


def library_error() -> str:
    return _library_error


def _read_text(function, *arguments) -> str:
    size = function(*arguments, None, 0)
    out = ctypes.create_string_buffer(size + 1)
    function(*arguments, out, size + 1)
    return out.value.decode("utf-8", "replace")


@lru_cache(maxsize=None)
def lab_help(label: str) -> str:
    """The lab's tooltip for a control label (control_help.hpp), or ""."""

    lib = load_library()
    return _read_text(lib.cf_help, label.encode()) if lib is not None else ""


class ClusterFlowEngine:
    """One lab session. Creating it, frame and render_png need its OpenGL context current."""

    def __init__(self, lib: ctypes.CDLL) -> None:
        self._lib = lib
        self._handle = lib.cf_create()
        if not self._handle:
            raise RuntimeError(self.last_error() or "OpenGL 3.3 is required for the Cluster Flow view.")

    @property
    def alive(self) -> bool:
        return bool(self._handle)

    def destroy(self) -> None:
        if self._handle:
            self._lib.cf_destroy(self._handle)
            self._handle = None

    def last_error(self) -> str:
        return _read_text(self._lib.cf_last_error)

    def get(self, name: str) -> float:
        value = ctypes.c_double()
        if not self._lib.cf_get(self._handle, name.encode(), ctypes.byref(value)):
            raise KeyError(name)
        return value.value

    def set(self, name: str, value: float) -> None:
        if not self._lib.cf_set(self._handle, name.encode(), float(value)):
            raise KeyError(name)

    def text(self, name: str) -> str:
        return _read_text(self._lib.cf_text, self._handle, name.encode())

    def action(self, name: str) -> None:
        if not self._lib.cf_action(self._handle, name.encode()):
            raise KeyError(name)

    def set_ffmpeg(self, path: str) -> None:
        self._lib.cf_set_ffmpeg(_native_path(path) if path else b"")

    def bake(self, path: str | Path) -> None:
        self._lib.cf_bake(self._handle, _native_path(path))

    def open_cache(self, path: str | Path) -> None:
        self._lib.cf_open_cache(self._handle, _native_path(path))

    def export_mp4(self, path: str | Path) -> None:
        self._lib.cf_export_mp4(self._handle, _native_path(path))

    def save_image(self, path: str | Path) -> None:
        self._lib.cf_save_image(self._handle, _native_path(path))

    def drag(self, dx: float, dy: float, pan: bool, height: float) -> None:
        self._lib.cf_drag(self._handle, float(dx), float(dy), int(pan), float(height))

    def scroll(self, notches: float) -> None:
        self._lib.cf_scroll(self._handle, float(notches))

    def frame(self, width: int, height: int, fbo: int) -> bool:
        return bool(self._lib.cf_frame(self._handle, width, height, fbo))

    def render_png(self, path: str | Path, width: int, height: int) -> None:
        if not self._lib.cf_render_png(self._handle, _native_path(path), int(width), int(height)):
            raise RuntimeError(self.last_error())


class ClusterFlowViewport(QOpenGLWindow):
    """OpenGL 3.3 core window that runs one pass of the lab's loop per vsync while exposed.

    As in the lab: left drag orbits; right or middle drag, or Shift + left drag, pans;
    the wheel zooms; Space pauses; H hides the panel.
    """

    failed = Signal(str)
    frameRendered = Signal()
    toggleControls = Signal()

    def __init__(self) -> None:
        super().__init__(QOpenGLWindow.UpdateBehavior.NoPartialUpdate)
        fmt = QSurfaceFormat()
        fmt.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        fmt.setSwapInterval(1)
        self.setFormat(fmt)
        self.engine: ClusterFlowEngine | None = None
        self.frame_error = ""
        self._clock = QElapsedTimer()
        self._last_mouse: QPointF | None = None
        self._frame_times: deque[float] = deque(maxlen=60)
        self.frameSwapped.connect(self._next_frame)

    def initializeGL(self) -> None:  # noqa: N802
        lib = load_library()
        if lib is None:
            self.failed.emit(library_error())
            return
        try:
            self.engine = ClusterFlowEngine(lib)
        except Exception as exc:
            self.failed.emit(str(exc).strip() or "OpenGL 3.3 is required for the Cluster Flow view.")
            return
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

    def with_context(self, action) -> None:
        if self.engine is None:
            return
        self.makeCurrent()
        try:
            action(self.engine)
        finally:
            self.doneCurrent()

    def paintGL(self) -> None:  # noqa: N802
        engine = self.engine
        if engine is None or not engine.alive:
            return
        dt = self._clock.restart() / 1000.0 if self._clock.isValid() else 0.0
        if dt > 0:
            self._frame_times.append(dt)
        width, height = self.framebuffer_size()
        if engine.frame(width, height, int(self.defaultFramebufferObject())):
            self.frame_error = ""
        else:
            self.frame_error = engine.last_error()
        self.frameRendered.emit()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._last_mouse = event.position()
        self.requestActivate()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        position = event.position()
        last, self._last_mouse = self._last_mouse, position
        engine = self.engine
        buttons = event.buttons()
        if engine is None or last is None:
            return
        delta = position - last
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        left = bool(buttons & Qt.MouseButton.LeftButton)
        pan = bool(buttons & (Qt.MouseButton.RightButton | Qt.MouseButton.MiddleButton)) or (shift and left)
        if pan or left:
            engine.drag(delta.x(), delta.y(), pan, self.height())
        event.accept()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.engine is not None:
            self.engine.scroll(event.angleDelta().y() / 120.0)
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.isAutoRepeat() or self.engine is None:
            event.accept()
            return
        if event.key() == Qt.Key.Key_H:
            self.toggleControls.emit()
        elif event.key() == Qt.Key.Key_Space:
            self.engine.action("playPause")
        event.accept()


class _IntSlider(LabSlider):
    """The lab's SliderInt: whole values only."""

    def _from_fraction(self, fraction: float) -> float:
        return float(round(super()._from_fraction(fraction)))


class ClusterFlowPanel(QWidget):
    """Cluster Flow mode: the lab's View, Physics, Scene and Bake tabs beside its view."""

    PANEL_WIDTH = 370

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("clusterFlowPanel")
        self._viewport: ClusterFlowViewport | None = None
        self._container: QWidget | None = None
        self._failed = False

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
        self._view_host.setStyleSheet("background: #04060a;")
        host_layout = QVBoxLayout(self._view_host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        self._fallback = QLabel(self._view_host)
        self._fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._fallback.setWordWrap(True)
        self._fallback.setStyleSheet("color: #d9dde4; padding: 24px;")
        self._fallback.hide()
        host_layout.addWidget(self._fallback)
        layout.addWidget(self._view_host, 1)

    # Embedding -------------------------------------------------------------

    @property
    def viewport(self) -> ClusterFlowViewport | None:
        return self._viewport

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._ensure_viewport()
        self._act("resume")
        if self._viewport is not None:
            self._viewport.requestUpdate()

    def hideEvent(self, event) -> None:  # noqa: N802
        # Live evolution runs on all cores; pause it while another mode is showing.
        self._act("suspend")
        super().hideEvent(event)

    def _ensure_viewport(self) -> None:
        if self._viewport is not None or self._failed:
            return
        if load_library() is None:
            self._show_failure(library_error())
            return
        viewport = ClusterFlowViewport()
        viewport.failed.connect(self._show_failure)
        viewport.frameRendered.connect(self._refresh)
        viewport.toggleControls.connect(self._toggle_controls)
        container = QWidget.createWindowContainer(viewport, self._view_host)
        container.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        container.setMinimumSize(320, 240)
        self._view_host.layout().addWidget(container)
        self._viewport = viewport
        self._container = container

    def _show_failure(self, message: str) -> None:
        self._failed = True
        self._fallback.setText(message or "OpenGL 3.3 is required for the Cluster Flow view.")
        self._fallback.show()
        if self._container is not None:
            self._container.hide()
        self._controls.setEnabled(False)

    def _toggle_controls(self) -> None:
        self._scroll.setVisible(not self._scroll.isVisible())

    def _engine(self) -> ClusterFlowEngine | None:
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

    # Construction ----------------------------------------------------------

    def _slider(self, layout, key: str, label: str, low: float, high: float, fmt: str = "%.2f", *, log=False, integer=False, help_key="") -> LabSlider:
        kind = _IntSlider if integer else LabSlider
        slider = kind(label, low, high, fmt, log=log, tooltip=lab_help(help_key or label), parent=self._controls)
        slider.edited.connect(lambda value, k=key: self._set(k, value))
        layout.addWidget(slider)
        self._sliders.append((key, slider))
        return slider

    def _check(self, layout, key: str, label: str) -> QCheckBox:
        box = QCheckBox(label, self._controls)
        box.setToolTip(lab_help(label))
        box.toggled.connect(lambda checked, k=key: self._set(k, 1.0 if checked else 0.0))
        layout.addWidget(box)
        self._checks.append((key, box))
        return box

    def _choice(self, layout, key: str, label: str, items, values=None, help_key="") -> LabCombo:
        combo = LabCombo(label, items, tooltip=lab_help(help_key or label), parent=self._controls)
        values = tuple(values) if values is not None else tuple(range(len(items)))
        combo.edited.connect(lambda index, k=key, v=values: self._set(k, v[index]))
        layout.addWidget(combo)
        self._choices.append((key, combo, values))
        return combo

    def _button(self, label: str, action) -> QPushButton:
        button = QPushButton(label.replace("&", "&&"), self._controls)
        button.setToolTip(lab_help(label))
        button.clicked.connect(action)
        return button

    def _group(self, layout) -> tuple[QWidget, QVBoxLayout]:
        widget = QWidget(self._controls)
        inner = QVBoxLayout(widget)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(6)
        layout.addWidget(widget)
        return widget, inner

    def _section(self, layout, title: str, expanded: bool, *, sub=False) -> _Section:
        section = _Section(title, expanded, self._controls)
        section._toggle.setToolTip(lab_help(title))  # noqa: SLF001
        if sub:
            section._toggle.setObjectName("mergerSubsection")  # noqa: SLF001
        layout.addWidget(section)
        return section

    def _build_controls(self) -> None:
        self._sliders: list[tuple[str, LabSlider]] = []
        self._checks: list[tuple[str, QCheckBox]] = []
        self._choices: list[tuple[str, LabCombo, tuple]] = []
        root = QVBoxLayout(self._controls)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(8)
        parent = self._controls

        title = QLabel("CLUSTER FLOW", parent)
        title.setObjectName("blackHoleTitle")
        root.addWidget(title)
        root.addWidget(_note("FILAMENTS, GAS & FEEDBACK", parent))
        self._run = self._button("Pause", lambda: self._act("playPause"))
        self._restart = self._button("Restart", lambda: self._act("restart"))
        self._transport = _row(self._run, self._restart)
        root.addWidget(self._transport)
        self._header = QLabel(parent)
        self._header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self._header)

        self._build_view(root)
        self._build_physics(root)
        self._build_scene(root)
        self._build_bake(root)

        root.addWidget(
            _note("Left drag: orbit / Right drag: pan\nScroll: zoom / Space: pause / H: hide controls", parent)
        )
        self._timing = _note("", parent)
        root.addWidget(self._timing)
        self._preparing = _note("Preparing the filament network...", parent, dim=False)
        root.addWidget(self._preparing)
        self._status = _note("", parent, dim=False)
        root.addWidget(self._status)
        root.addStretch(1)

    def _build_view(self, root) -> None:
        parent = self._controls
        self._view_section = self._section(root, "View", True)
        v = self._view_section.body_layout
        v.addWidget(_note("Appearance changes apply live", parent))
        motion = self._section(v, "Camera motion", True, sub=True).body_layout
        self._slider(motion, "camera.yaw", "Horizontal angle (degrees)", -180, 180, "%.1f")
        self._slider(motion, "camera.yawSpeed", "Horizontal speed (deg/s)", -90, 90, "%.1f")
        self._check(motion, "camera.rotateYaw", "Rotate horizontally")
        self._slider(motion, "camera.pitch", "Vertical angle (degrees)", -180, 180, "%.1f")
        self._slider(motion, "camera.pitchSpeed", "Vertical speed (deg/s)", -90, 90, "%.1f")
        self._check(motion, "camera.rotatePitch", "Rotate vertically")
        motion.addWidget(_row(self._button("Stop camera motion", lambda: self._act("stopCamera"))))
        motion.addWidget(
            _note(
                "Negative speeds reverse direction. Motion orbits the current focus and continues while physics is "
                "paused. MP4 uses these speeds too.",
                parent,
            )
        )
        self._choice(v, "mode", "Display field", DISPLAY_FIELDS, help_key="##view")
        self._slider(v, "exposure", "Exposure", 0.3, 6.0)
        self._check(v, "showTracers", "Flow tracers")
        self._tracer_brightness = self._slider(v, "tracerOpacity", "Tracer brightness", 0.005, 0.2, "%.3f")
        self._slider(v, "slice", "Slice thickness", 0.025, 1.0)
        self._slice_position = self._slider(v, "slicePosition", "Slice position", -1.0, 1.0)
        self._slider(v, "samples", "Ray samples", 96, 512, "%d", integer=True, help_key="##samples")
        v.addWidget(_row(self._button("Save image...", self._save_image), self._button("Reset camera", lambda: self._act("resetCamera"))))
        explore = self._section(v, "Explore volume", False, sub=True).body_layout
        explore.addWidget(
            _note("Left drag: orbit. Right/middle drag or Shift + left drag: pan. Scroll: zoom into the volume.", parent)
        )
        self._slider(explore, "distance", "Camera distance", 0.06, 7.0, "%.3f", log=True)
        for axis in "XYZ":
            self._slider(explore, f"focus.{axis.lower()}", f"Focus {axis}", -1.0, 1.0, "%.3f")

    def _build_physics(self, root) -> None:
        parent = self._controls
        self._physics_section = self._section(root, "Physics", False)
        p = self._physics_section.body_layout
        p.addWidget(_note("Changes apply live", parent))
        self._slider(p, "speed", "Evolution pace", 0.05, 1.5)
        self._cosmology_physics_note = _note(
            "Live dark matter and gas set gravity. Cosmological gas evolves adiabatically; cluster outbursts are disabled.",
            parent,
        )
        p.addWidget(self._cosmology_physics_note)
        self._cluster_physics, cp = self._group(p)
        self._slider(cp, "gravity", "Dark halo gravity", 0.0, 4.0)
        self._slider(cp, "cooling", "Cooling rate", 0.0, 3.0)
        self._slider(cp, "coolingFloor", "Cooling temperature floor", 0.00001, 0.001, "%.5f", log=True)
        bursts = self._section(cp, "Outbursts", True, sub=True).body_layout
        self._check(bursts, "automatic", "Automatic outbursts")
        self._slider(bursts, "feedback", "Outflow strength", 0.0, 5.0)
        self._slider(bursts, "burstInterval", "Interval", 0.2, 10.0)
        self._slider(bursts, "burstRadius", "Injection radius", 0.04, 0.16, "%.3f")
        self._slider(bursts, "burstHeat", "Heating", 0.0, 0.2, "%.3f")
        self._slider(bursts, "burstKick", "Velocity kick", 0.0, 0.6, "%.3f")
        self._slider(bursts, "collimation", "Jet collimation", 0.0, 1.0)
        bursts.addWidget(_row(self._button("Trigger outburst", lambda: self._act("burst"))))
        solver = self._section(p, "Timestep & solver", True, sub=True).body_layout
        self._choice(solver, "accuracy", "Preset", ACCURACY, help_key="##accuracy")
        self._check(solver, "secondOrder", "Second-order transport")
        self._slider(solver, "courant", "Courant limit", 0.05, 0.3, "%.3f")
        self._slider(solver, "maxDt", "Maximum timestep", 0.001, 0.05, "%.4f", log=True)
        solver.addWidget(
            _note("Smaller timesteps improve time resolution at a higher cost. Grid resolution limits spatial detail.", parent)
        )
        self._solver_note = _note("", parent)
        solver.addWidget(self._solver_note)
        self._last_step = _note("", parent)
        solver.addWidget(self._last_step)

    def _build_scene(self, root) -> None:
        parent = self._controls
        self._scene_section = self._section(root, "Scene", True)
        s = self._scene_section.body_layout
        self._recorded, rec = self._group(s)
        rec.addWidget(_note("Recorded simulation settings. Return to live to configure a new simulation.", parent))
        self._description = QLabel(parent)
        self._description.setWordWrap(True)
        self._description.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._description.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
        rec.addWidget(self._description)

        self._live_scene, sc = self._group(s)
        sc.addWidget(_note("Scene changes take effect when you apply and restart.", parent))
        self._choice(sc, "scene.model", "Model", MODELS, help_key="##model")
        self._cluster_box, cb = self._group(sc)
        self._slider(cb, "scene.clusterBox", "Cluster cube size (code units)", 2.0, 8.0)
        self._cluster_box_note = _note("", parent)
        cb.addWidget(self._cluster_box_note)
        self._coarse_note = _note(
            "Fine filaments are smaller than these cells. Increase Gas grid for more detail; zoom cannot recover "
            "unresolved structure.",
            parent,
        )
        cb.addWidget(self._coarse_note)

        self._cosmology, co = self._group(sc)
        self._slider(co, "scene.boxMpc", "Comoving cube size (Mpc)", 5.0, 500.0, "%.1f", log=True)
        self._cell_note = _note("", parent)
        co.addWidget(self._cell_note)
        self._slider(co, "scene.hubble", "H0 (km/s/Mpc)", 30.0, 100.0)
        self._slider(co, "scene.omegaDM", "Dark matter / Omega_dm", 0.0, 1.0, "%.4f")
        self._slider(co, "scene.omegaB", "Baryons / Omega_b", 0.001, 0.3, "%.4f")
        self._slider(co, "scene.omegaDE", "Dark energy / Omega_de", 0.0, 1.5, "%.4f")
        self._curvature = QLabel(parent)
        co.addWidget(self._curvature)
        self._flat = self._button("Set flat geometry", lambda: self._act("flatGeometry"))
        co.addWidget(_row(self._flat))
        self._flat_note = _note(
            "Flat geometry with nonnegative dark energy requires total matter density at most 1.", parent
        )
        co.addWidget(self._flat_note)
        self._slider(co, "scene.w", "Dark energy w", -1.5, -0.5)
        self._slider(co, "scene.startRedshift", "Starting redshift", 1.0, 49.0, "%.1f")
        self._slider(co, "scene.sigma8", "sigma8", 0.0, 2.0, "%.3f")
        self._slider(co, "scene.spectralIndex", "Spectral index ns", 0.7, 1.3, "%.4f")
        self._choice(
            co, "scene.mesh", "Gravity mesh / DM particles", [f"{n} cubed" for n in GRAVITY_MESHES], GRAVITY_MESHES, help_key="##pm"
        )
        co.addWidget(_row(self._button("TNG-like cosmology preset", lambda: self._act("tngPreset"))))

        self._choice(sc, "scene.grid", "Gas grid", [f"{n} cubed" for n in GAS_GRIDS], GAS_GRIDS, help_key="##grid")
        self._memory_note = _note("", parent)
        sc.addWidget(self._memory_note)
        self._slider(sc, "scene.tracers", "Flow tracer count", 0, 1_000_000, "%d", integer=True, help_key="##tracercount")
        sc.addWidget(
            _note(
                "Visual markers only. Gas accuracy: Gas grid and Physics > Timestep & solver. Gravity resolution: gravity "
                "mesh in cosmological mode; gas-grid sampling of analytic halos in cluster mode.",
                parent,
            )
        )
        self._seed = LabNumber("Random seed", 0, 2_147_483_647, 1, integer=True, tooltip=lab_help("##seed"), parent=parent)
        self._seed.edited.connect(lambda value: self._set("scene.seed", value))
        sc.addWidget(self._seed)
        self._cluster_initial, ci = self._group(sc)
        self._slider(ci, "scene.gasDensity", "Baryonic gas density", 0.1, 4.0, "%.2f x")
        self._slider(ci, "scene.temperature", "Initial temperature", 0.25, 4.0, "%.2f x")
        self._slider(ci, "scene.inflow", "Initial inflow", 0.0, 3.0, "%.2f x")
        self._slider(ci, "scene.turbulence", "Velocity perturbations", 0.0, 3.0, "%.2f x")
        self._slider(ci, "scene.filamentWidth", "Filament width", 0.5, 2.0, "%.2f x")
        self._slider(ci, "scene.haloSoftening", "Dark halo softening", 0.03, 0.15, "%.3f")
        self._cosmology_temperature = self._slider(
            sc, "scene.temperature", "Initial gas temperature / 10,000 K", 0.25, 4.0, "%.2f x"
        )
        sc.addWidget(_row(self._button("Apply scene & restart", lambda: self._act("apply"))))
        self._pending = _note("Scene changes pending", parent, dim=False)
        self._pending.setStyleSheet("color: #ffb34d;")
        sc.addWidget(self._pending)
        about = self._section(sc, "About cosmology", False, sub=True).body_layout
        about.addWidget(
            _note(
                "Cosmological mode uses expanding coordinates, live particle-mesh dark matter, and gas self-gravity. "
                "Initial fluctuations use a smooth CDM spectrum. Radiation, BAO, star formation, and magnetic fields "
                "are omitted.",
                parent,
            )
        )

    def _build_bake(self, root) -> None:
        parent = self._controls
        self._bake_section = self._section(root, "Bake", False)
        b = self._bake_section.body_layout
        b.addWidget(_note("Bake the simulation once, then replay or export without recomputing physics.", parent))
        self._baking, bk = self._group(b)
        self._bake_progress = QProgressBar(parent)
        self._bake_progress.setRange(0, 1000)
        self._bake_progress.setTextVisible(False)
        self._bake_progress.setFixedHeight(8)
        bk.addWidget(self._bake_progress)
        self._bake_count = QLabel(parent)
        bk.addWidget(self._bake_count)
        bk.addWidget(_row(self._button("Cancel bake (keep frames)", lambda: self._act("bake.cancel"))))

        self._bake_idle, idle = self._group(b)
        self._bake_another = self._section(idle, "Bake another simulation", False, sub=True)
        settings = self._bake_another.body_layout
        self._duration = self._slider(settings, "bake.duration", "Evolution duration", 0.1, 30.0)
        self._slider(settings, "bake.frames", "Cached frames", 2, 1200, "%d", integer=True, help_key="##bakeframes")
        self._slider(settings, "bake.grid", "Cached volume grid", 16, 160, "%d cubed", integer=True, help_key="##cachegrid")
        self._slider(settings, "bake.tracers", "Cached tracers", 0, 1_000_000, "%d", integer=True, help_key="##cachetracers")
        self._estimate = _note("", parent)
        settings.addWidget(self._estimate)
        settings.addWidget(_row(self._button("Bake from start...", self._bake)))
        idle.addWidget(_row(self._button("Open cache...", self._open_cache)))

        self._replay, rp = self._group(b)
        self._cache_name = QLabel(parent)
        self._cache_name.setWordWrap(True)
        rp.addWidget(self._cache_name)
        self._cache_frames = QLabel(parent)
        rp.addWidget(self._cache_frames)
        self._replay_controls, rc = self._group(rp)
        self._timeline = self._slider(rc, "playHead", "Cached frame", 0, 1, "%d", integer=True, help_key="##timeline")
        self._slider(rc, "playbackFps", "Playback frames / second", 1, 60, "%.0f")
        self._check(rc, "loop", "Loop replay")
        rc.addWidget(_row(self._button("Return to live", lambda: self._act("returnLive"))))
        self._choice(rc, "movieQuality", "Movie resolution", MOVIE_SIZES, help_key="##moviequality")
        self._slider(rc, "movieFps", "Movie FPS", 1, 60, "%d", integer=True, help_key="##moviefps")
        rc.addWidget(_row(self._button("Export MP4...", self._export_mp4)))
        self._movie, mv = self._group(rp)
        self._movie_progress = QProgressBar(parent)
        self._movie_progress.setRange(0, 1000)
        self._movie_progress.setTextVisible(False)
        self._movie_progress.setFixedHeight(8)
        mv.addWidget(self._movie_progress)
        mv.addWidget(_row(self._button("Stop export (keep video)", lambda: self._act("export.stop"))))

    # Actions ---------------------------------------------------------------

    def _dialog_path(self, name: str) -> str:
        return str(default_export_directory() / name)

    def _save_image(self) -> None:
        engine = self._engine()
        if engine is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save image", self._dialog_path("cosmic-flow.png"), "PNG image (*.png)")
        if path:
            engine.save_image(path)

    def _bake(self) -> None:
        engine = self._engine()
        if engine is None:
            return
        default_export_directory().mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(
            self, "Bake from start", self._dialog_path("cosmic-flow.cflow"), "Cosmic Flow cache (*.cflow)"
        )
        if path:
            engine.bake(path)

    def _open_cache(self) -> None:
        engine = self._engine()
        if engine is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open cache", str(default_export_directory()), "Cosmic Flow cache (*.cflow)"
        )
        if path:
            engine.open_cache(path)

    def _export_mp4(self) -> None:
        engine = self._engine()
        if engine is None:
            return
        name = Path(engine.text("cacheName")).with_suffix(".mp4").name or "cosmic-flow.mp4"
        path, _ = QFileDialog.getSaveFileName(self, "Export MP4", self._dialog_path(name), "MP4 video (*.mp4)")
        if not path:
            return
        try:
            from photometry_app.core.animation_export import resolve_ffmpeg_executable

            engine.set_ffmpeg(resolve_ffmpeg_executable())
        except Exception:
            engine.set_ffmpeg("")
        engine.export_mp4(path)

    # Refresh ---------------------------------------------------------------

    @staticmethod
    def _set_text(label: QLabel, text: str) -> None:
        if label.text() != text:
            label.setText(text)
        label.setVisible(bool(text))

    @staticmethod
    def _set_label(slider: LabSlider, label: str) -> None:
        if slider.label != label:
            slider._name.setText(label)  # noqa: SLF001
            slider.setToolTip(lab_help(label))

    def _refresh(self) -> None:
        engine = self._engine()
        viewport = self._viewport
        if engine is None or viewport is None:
            return
        get = engine.get
        replay = bool(get("replay"))
        exporting = bool(get("exporting"))
        baking = bool(get("bake.busy"))
        cosmology = bool(get("scene.model"))
        playing, running = bool(get("playing")), bool(get("run"))

        self._transport.setEnabled(not exporting and not baking)
        run_text = ("Pause replay" if playing else "Play replay") if replay else ("Pause" if running else "Resume")
        if self._run.text() != run_text:
            self._run.setText(run_text)
            self._run.setToolTip(lab_help(run_text))
        restart_text = "To start" if replay else "Restart"
        if self._restart.text() != restart_text:
            self._restart.setText(restart_text)
            self._restart.setToolTip(lab_help(restart_text))
        if get("shown.cosmology"):
            scale = get("shown.scaleFactor")
            header = (
                f"Elapsed {get('shown.time'):.2f} Gyr | z {1 / scale - 1 if scale else 0:.2f}\n"
                f"Box {get('shown.boxMpc'):.0f} cMpc | a {scale:.3f}"
            )
        else:
            side = get("shown.clusterBox")
            header = f"Time {get('shown.time'):.2f}  |  {int(get('shown.grid'))} cubed\nBox side {side:.2f} | Volume {side ** 3:.2f}"
        self._set_text(self._header, header)

        self._view_section.body.setEnabled(not exporting)
        locked = exporting or baking or replay
        self._physics_section.body.setEnabled(not locked)
        self._scene_section.body.setEnabled(not exporting and not baking)
        self._tracer_brightness.setVisible(bool(get("showTracers")))
        self._slice_position.setVisible(get("slice") < 0.99)

        self._cosmology_physics_note.setVisible(cosmology)
        self._cluster_physics.setEnabled(not cosmology)
        self._solver_note.setText(
            "Gravity detail: Scene > Gravity mesh / DM particles. Gas detail: Scene > Gas grid."
            if cosmology
            else "Cluster halos are analytic. Gas grid resolves their force across the gas; smaller timesteps resolve gas "
            "motion. Gravity strength and softening change the model, not its accuracy."
        )
        self._last_step.setText(f"Last step: {get('shown.lastDt'):.5f}")

        self._recorded.setVisible(replay)
        self._live_scene.setVisible(not replay)
        if replay:
            self._set_text(self._description, engine.text("description").strip())
        else:
            grid = get("scene.grid")
            tracers = get("scene.tracers")
            self._cluster_box.setVisible(not cosmology)
            self._cosmology.setVisible(cosmology)
            self._cluster_initial.setEnabled(not cosmology)
            self._cosmology_temperature.setVisible(cosmology)
            if not cosmology:
                side = get("scene.clusterBox")
                self._cluster_box_note.setText(
                    f"Volume {side ** 3:.2f} | {int(get('scene.clumps'))} clumps\nCell size {side / grid:.4f} code units. "
                    "Larger boxes generate more clumps and filaments."
                )
                self._coarse_note.setVisible(side / grid > 0.04)
            else:
                self._cell_note.setText(
                    f"Cell size {get('scene.boxMpc') / grid:.3f} cMpc. Increasing the box at a fixed grid reduces spatial resolution."
                )
                self._curvature.setText(f"Derived curvature: {get('scene.curvature'):.4f}")
                too_dense = get("scene.omegaDM") + get("scene.omegaB") > 1
                self._flat.setEnabled(not too_dense)
                self._flat_note.setVisible(too_dense)
            cells = grid**3
            pm_cells = get("scene.mesh") ** 3 if cosmology else 0
            memory = (cells * 136.0 + tracers * 144.0 + pm_cells * 80) / (1024.0 * 1024.0)
            self._memory_note.setText(f"{cells / 1e6:.2f} million gas cells. Rough working memory: {memory:.0f} MB.")
            self._seed.set_value(get("scene.seed"))
            self._pending.setVisible(bool(get("pendingScene")))

        self._baking.setVisible(baking)
        self._bake_idle.setVisible(not baking)
        self._bake_idle.setEnabled(not exporting)
        if baking:
            completed, total = int(get("bake.completed")), int(get("bake.total"))
            self._bake_progress.setValue(int(1000 * completed / max(1, total)))
            self._bake_count.setText(f"Cached {completed} / {total}")
        else:
            # The lab shows the bake settings directly until a recording is open.
            toggle = self._bake_another._toggle  # noqa: SLF001
            toggle.setVisible(replay)
            self._bake_another.body.setVisible(not replay or toggle.isChecked())
            self._set_label(self._duration, "Evolution duration (Gyr)" if cosmology else "Evolution duration")
            cache_grid = min(get("bake.grid"), get("scene.grid"))
            cache_tracers = min(get("bake.tracers"), get("scene.tracers"))
            size = (cache_grid**3 * 8 + cache_tracers * 18.0 + 20) * get("bake.frames")
            self._estimate.setText(
                f"Estimated cache: {size / 1e9:.2f} GB. Cache quality affects replay detail, not the simulation grid."
            )
        self._replay.setVisible(replay)
        if replay:
            frames = int(get("replay.frames"))
            self._set_text(self._cache_name, engine.text("cacheName"))
            self._cache_frames.setText(f"{frames} cached frames")
            self._replay_controls.setEnabled(not exporting)
            self._timeline.set_range(0, max(frames - 1, 0))
            self._movie.setVisible(exporting)
            if exporting:
                self._movie_progress.setValue(int(1000 * get("movieFrame") / max(1, frames)))

        self._timing.setText(f"Step {get('shown.stepMs'):.1f} ms | Render {viewport.fps:.0f} fps")
        self._preparing.setVisible(not get("uploaded"))
        self._set_text(self._status, engine.text("status") or viewport.frame_error)
        self._sync_values(engine)

    def _sync_values(self, engine: ClusterFlowEngine) -> None:
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
        for key, combo, values in self._choices:
            if combo.isVisible():
                value = get(key)
                index = min(range(len(values)), key=lambda i: abs(values[i] - value))
                combo.set_index(index)
