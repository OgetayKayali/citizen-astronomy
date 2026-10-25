"""CAst's Qt interface to the original PhysicsEngine Binary Light Lab."""
from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPainterPath, QPen, QShortcut, QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSlider,
    QSplitter, QTabWidget, QVBoxLayout, QWidget)

from photometry_app.core.variable_stars import StellarLabModel, FAMILIES, CONTROLS, EXTRAS
from photometry_app.ui.stellar_surface_gl import StellarSurface
from photometry_app.ui.binary_light_gl import StellarBloom, hdr_scene_shader

CYAN = QColor("#69cbd1")
GOLD = QColor("#d5a677")
MUTED = QColor("#aab5c4")
SCOPE = ("Spherical opaque stars; two-body Kepler motion; blackbody spectra; linear limb darkening; "
         "centered exposure integration. No Roche distortion, irradiation, starspots, mass transfer, dust, "
         "Doppler beaming or light-travel delay. Artistic granulation and stellar glow do not enter the measured flux.")
STYLE = """
QWidget#binaryLightPanel { background: #0c1016; color: #e3e7ed; }
QWidget#binaryLightControls { background: #151920; color: #e3e7ed; }
QWidget#binaryLightControls QLabel { color: #c4ced8; background: transparent; }
QWidget#binaryLightPanel QPushButton, QWidget#binaryLightPanel QComboBox,
QWidget#binaryLightPanel QDoubleSpinBox { background: #242b36; color: #e3e7ed; border: 1px solid #3a4150; border-radius: 4px; padding: 5px; }
QWidget#binaryLightPanel QPushButton:hover { background: #343d4b; border-color: #c9956a; }
QWidget#binaryLightPanel QPushButton:disabled { color: #707785; }
QWidget#binaryLightPanel QTabWidget::pane { border: 0; }
QWidget#binaryLightPanel QTabBar::tab { background: #1c222c; color: #c4ced8; padding: 7px 8px; }
QWidget#binaryLightPanel QTabBar::tab:selected { color: #e0b184; background: #303742; }
"""


class ParameterControl(QWidget):
    """A slider with direct numeric entry, including the lab's logarithmic ranges."""
    edited = Signal(float)

    def __init__(self, label, low, high, value, *, decimals=3, logarithmic=False):
        super().__init__()
        self.low, self.high, self.logarithmic = low, high, logarithmic
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        row = QHBoxLayout()
        self.label = QLabel(label)
        row.addWidget(self.label, 1)
        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(decimals)
        self.spin.setRange(low, high)
        self.spin.setKeyboardTracking(False)
        self.spin.setFixedWidth(110)
        row.addWidget(self.spin)
        layout.addLayout(row)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        layout.addWidget(self.slider)
        self.set_value(value)
        self.spin.valueChanged.connect(self._spin_changed)
        self.slider.valueChanged.connect(self._slide_changed)

    def set_value(self, value):
        value = min(self.high, max(self.low, value))
        fraction = math.log(value/self.low)/math.log(self.high/self.low) if self.logarithmic else (value-self.low)/(self.high-self.low)
        self.spin.blockSignals(True)
        self.slider.blockSignals(True)
        self.spin.setValue(value)
        self.slider.setValue(round(fraction*1000))
        self.spin.blockSignals(False)
        self.slider.blockSignals(False)

    def set_maximum(self, value):
        self.high = value
        self.spin.blockSignals(True)
        self.spin.setMaximum(value)
        self.spin.blockSignals(False)

    def _spin_changed(self, value):
        self.set_value(value)
        self.edited.emit(value)

    def _slide_changed(self, position):
        fraction = position/1000
        value = self.low*(self.high/self.low)**fraction if self.logarithmic else self.low+(self.high-self.low)*fraction
        self.set_value(value)
        self.edited.emit(value)


class BinaryScene(QOpenGLWidget):
    failed = Signal(str)

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.program = self.vao = None
        self.bloom = None
        self.surface = None
        self.error = ""
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        fmt.setDepthBufferSize(0)
        fmt.setStencilBufferSize(0)
        self.setFormat(fmt)
        self.setMinimumSize(300, 220)
        self.overlay = BinaryOverlay(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.overlay.setGeometry(self.rect())

    def initializeGL(self):
        from OpenGL import GL
        from OpenGL.GL.shaders import compileProgram, compileShader
        self.context().aboutToBeDestroyed.connect(self.cleanup)
        try:
            lib = self.panel.model.lib
            self.program = compileProgram(compileShader(lib.bl_shader(0).decode(), GL.GL_VERTEX_SHADER),
                                          compileShader(hdr_scene_shader(lib.bl_shader(1).decode()), GL.GL_FRAGMENT_SHADER))
            self.vao = GL.glGenVertexArrays(1)
            self.bloom = StellarBloom(lib.bl_shader(0).decode())
            self.surface = StellarSurface(lib.bl_shader(1).decode())
        except Exception as exc:
            self.error = f"OpenGL 3.3 stellar renderer unavailable: {exc}"
            self.failed.emit(self.error)

    def cleanup(self):
        from OpenGL import GL
        self.makeCurrent()
        if self.surface:
            self.surface.close()
            self.surface = None
        if self.bloom:
            self.bloom.close()
            self.bloom = None
        if self.program:
            GL.glDeleteProgram(self.program)
            self.program = None
        if self.vao:
            GL.glDeleteVertexArrays(1, [self.vao])
            self.vao = None
        self.doneCurrent()

    def paintGL(self):
        from OpenGL import GL
        p = self.panel
        m = p.model
        GL.glClearColor(.002, .003, .009, 1)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        if self.program and self.bloom:
            ratio = self.devicePixelRatioF()
            w, h = round(self.width()*ratio), round(self.height()*ratio)
            GL.glDisable(GL.GL_BLEND)
            GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glDisable(GL.GL_SCISSOR_TEST)
            try:
                self.bloom.begin(w, h)
            except Exception as exc:
                GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.defaultFramebufferObject())
                self.error = str(exc)
                self.failed.emit(self.error)
                self.overlay.update()
                return
            GL.glUseProgram(self.program)
            GL.glBindVertexArray(self.vao)
            def scalar(name, value):
                GL.glUniform1f(GL.glGetUniformLocation(self.program, name), float(value))
            def vector(name, values):
                GL.glUniform3f(GL.glGetUniformLocation(self.program, name), *map(float, values))
            g = m.geometry(m.cycles)
            for j, suffix in enumerate(("a", "b")):
                vector(f"center{j}", g[j*3:j*3+3] if not m.family else [1e8, 1e8, 0])
                for uniform, field in (("radius", "radius"), ("temp", "temperature"), ("limb", "limb")):
                    scalar(f"{uniform}{j}", m.get(f"{field}_{suffix}"))
                vector(f"tint{j}", p.tints[j])
            extent = max(m.get("separation")*(1+m.get("eccentricity"))*.68,
                         max(m.get("radius_a"), m.get("radius_b"))*1.4)/p.look["zoom"]
            if m.family and not m.invalid:
                extent = m.info()[0]/p.look["zoom"]
            scalar("extent", extent)
            scalar("time", m.cycles*8)
            for name in ("exposure", "glow", "detail", "sky"):
                scalar(name, p.look[name])
            GL.glUniform2f(GL.glGetUniformLocation(self.program, "viewport"), w, h)
            GL.glUniform1i(GL.glGetUniformLocation(self.program, "customTint"), int(p.custom_tint.isChecked()))
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
            if m.family and not m.invalid:
                self.surface.draw(m, extent, w/h, p.look, p.tints, p.custom_tint.isChecked())
                GL.glBindVertexArray(self.vao)
            self.bloom.finish(self.defaultFramebufferObject(), p.look["glow"], p.look["exposure"], ratio)
            GL.glBindVertexArray(0)
            GL.glUseProgram(0)
        self.overlay.update()


class BinaryOverlay(QWidget):
    """Qt-painted labels above the GL surface, independent of driver paint state."""
    def __init__(self, scene):
        super().__init__(scene)
        self.scene = scene
        self.panel = scene.panel
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    def paintEvent(self, event):
        p = self.panel
        m = p.model
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(MUTED)
        painter.drawText(22, 28, "OBSERVER VIEW  /  +Z TOWARD YOU")
        painter.setPen(CYAN)
        painter.drawText(22, 51, (f"PHASE  {m.cycles % 1:.3f}    |    " if m.periodic else "") + f"TIME  {m.cycles*m.period:.5g} days")
        if self.scene.error:
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.scene.error)
        elif m.invalid:
            painter.drawText(22, self.height()-25, "INVALID SYSTEM / photometry paused")
        else:
            measurement = m.observe(m.cycles)
            painter.drawText(22, self.height()-44, f"Flux {measurement[0]:.5f}   |   Δmag {-2.5*math.log10(measurement[0]/m.comparison):+.4f}   |   {measurement[2]:.3e} W/m²")
            g = m.geometry(m.cycles)
            state = "OUT OF ECLIPSE" if g[7] >= m.get("radius_a")+m.get("radius_b") else ("B IN FRONT OF A" if g[8] == 1 else "A IN FRONT OF B")
            painter.setPen(MUTED)
            painter.drawText(22, self.height()-22, m.name if m.family else f"{state}  /  projected separation {g[7]:.2f} R☉")
        if not m.family and p.orbit.isChecked() and self.width() >= 850 and self.height() >= 550:
            self._inset(painter)
        painter.end()

    def _inset(self, painter):
        m = self.panel.model
        cx, cy = self.width()-116, 142
        scale = 72/(m.get("separation")*(1+m.get("eccentricity")))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(8, 15, 25, 220))
        painter.drawRoundedRect(QRectF(cx-95, cy-88, 190, 195), 10, 10)
        painter.setPen(MUTED)
        painter.drawText(cx-77, cy-65, "ORBIT / FACE-ON")
        points = [m.geometry(k/120, True) for k in range(121)]
        for j, color in enumerate((CYAN, GOLD)):
            path = QPainterPath()
            for k, point in enumerate(points):
                x, y = cx+point[j*3]*scale, cy-point[j*3+1]*scale
                path.moveTo(x, y) if k == 0 else path.lineTo(x, y)
            painter.setPen(QColor("#3d5367"))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
            point = m.geometry(m.cycles, True)
            painter.setBrush(color)
            radius = max(3, m.get("radius_a" if j == 0 else "radius_b")*scale)
            painter.drawEllipse(QPointF(cx+point[j*3]*scale, cy-point[j*3+1]*scale), radius, radius)
        painter.setPen(MUTED)
        painter.drawText(cx-53, cy+92, "BARYCENTER")
        painter.drawLine(cx-4, cy, cx+4, cy)
        painter.drawLine(cx, cy-4, cx, cy+4)


class LightCurvePlot(QWidget):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.setMinimumHeight(190)

    def paintEvent(self, event):
        p = self.panel
        m = p.model
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0c1016"))
        rect = QRectF(62, 14, max(1, self.width()-86), max(1, self.height()-60))
        start, stop = p.prediction_range
        def abscissa(cycle):
            return cycle % 1 if m.periodic else (cycle-start)/(stop-start)
        magnitude = p.graph_mode.currentIndex() == 1
        def ordinate(flux):
            return -2.5*np.log10(np.asarray(flux)/m.comparison) if magnitude else np.asarray(flux)
        data = list(p.prediction_curve)
        data.extend(s[1] for s in m.samples if m.periodic or start <= s[0] <= stop)
        values = ordinate(data) if data else np.array([0, 1])
        low, high = float(np.min(values)), float(np.max(values))
        pad = max(.02, (high-low)*.12)
        low, high = low-pad, high+pad
        if not magnitude:
            low = max(0., low)
        def point(phase, flux):
            f = (float(ordinate(flux))-low)/(high-low)
            return QPointF(rect.left()+phase*rect.width(), rect.top()+(f if magnitude else 1-f)*rect.height())
        for k in range(5):
            y = rect.top()+rect.height()*k/4
            painter.setPen(QColor("#243343"))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(MUTED)
            value = low+(high-low)*k/4 if magnitude else high-(high-low)*k/4
            painter.drawText(QRectF(0, y-10, 54, 20), Qt.AlignmentFlag.AlignRight, f"{value:.3g}" if abs(value) >= 100 else f"{value:.3f}")
        for k in range(11):
            x = rect.left()+rect.width()*k/10
            painter.setPen(QColor("#192736"))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            if k % 2 == 0:
                painter.setPen(MUTED)
                painter.drawText(QPointF(x-8, rect.bottom()+19), f"{k/10:.1f}" if m.periodic else f"{(start+(stop-start)*k/10)*m.period:.3g}")
        painter.save()
        painter.setClipRect(rect)
        if p.prediction.isChecked() and len(p.prediction_curve):
            path = QPainterPath()
            for k, flux in enumerate(p.prediction_curve):
                at = point(k/max(1,len(p.prediction_curve)-1), flux)
                path.moveTo(at) if k == 0 else path.lineTo(at)
            painter.setPen(QPen(QColor("#6b8497"), 1.4))
            painter.drawPath(path)
        painter.setPen(QPen(CYAN, 2.2))
        painter.setBrush(CYAN)
        last = None
        for cycle, flux, _ in m.samples:
            at = point(abscissa(cycle), flux)
            if m.noise_ppm > 0:
                painter.drawEllipse(at, 1.7, 1.7)
            elif last is not None and (not m.periodic or math.floor(last[0]) == math.floor(cycle)):
                painter.drawLine(last[1], at)
            last = (cycle, at)
        x = rect.left()+abscissa(m.cycles)*rect.width()
        painter.setPen(GOLD)
        painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        if not m.invalid:
            painter.setBrush(GOLD)
            painter.drawEllipse(point(abscissa(m.cycles), m.observe(m.cycles)[0]), 4, 4)
        if not m.samples:
            painter.setPen(MUTED)
            message = "Press Play to record the curve as the model evolves."
            box = QRectF(rect.left()+10, rect.top()+7, painter.fontMetrics().horizontalAdvance(message)+12, 23)
            painter.fillRect(box, QColor(12, 16, 22, 230))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, message)
        painter.restore()
        painter.setPen(MUTED)
        painter.drawText(QRectF(rect.left(), rect.bottom()+26, rect.width(), 25), Qt.AlignmentFlag.AlignCenter, "Phase / one cycle" if m.periodic else "Elapsed time / days (unfolded)")
        painter.end()


class BinaryLightPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("binaryLightPanel")
        self.setStyleSheet(STYLE)
        self.model = None
        self.scene = None
        self.playing = False
        self.prediction_curve = np.array([])
        self.prediction_range = (0, 1)
        self._curve_generation = 0
        self._curve_future = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stellar-curve")
        self.destroyed.connect(lambda: self._executor.shutdown(wait=False, cancel_futures=True))
        self.controls = {}
        self.look = dict(zoom=1., exposure=1.5, glow=1., detail=1., sky=.65)
        self.tints = [[.82, .92, 1.], [1., .6, .25]]
        self._last_tick = time.monotonic()
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.rebuild_timer = QTimer(self)
        self.rebuild_timer.setSingleShot(True)
        self.rebuild_timer.setInterval(100)
        self.rebuild_timer.timeout.connect(self._rebuild_curve)

    def showEvent(self, event):
        super().showEvent(event)
        if self.model is None and self._layout.count() == 0:
            try:
                self.model = StellarLabModel()
                self._build()
            except RuntimeError as exc:
                message = QLabel(str(exc))
                message.setWordWrap(True)
                self._layout.addWidget(message)
                return
        self._last_tick = time.monotonic()
        if self.model is not None:
            self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def _label(self, text, layout):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(label)
        return label

    def _control(self, layout, key, title, low, high, value, **kwargs):
        control = ParameterControl(title, low, high, value, **kwargs)
        layout.addWidget(control)
        self.controls[key] = control
        control.edited.connect(lambda v, k=key: self._edit(k, v))
        return control

    def _build(self):
        m = self.model
        side = QWidget()
        side.setObjectName("binaryLightControls")
        form = QVBoxLayout(side)
        form.setContentsMargins(16, 16, 16, 16)
        title = self._label("Binary Light Lab", form)
        title.setStyleSheet("font-size: 22px; font-weight: 600; color: #e0b184;")
        self._label("From stellar geometry to measured light.", form)
        self.presets = QComboBox()
        for i in range(6):
            self.presets.addItem(m.lib.bl_preset_text(i, 0).decode())
        for family, entry in FAMILIES.items():
            self.presets.addItem(entry[0])
        self.presets.addItem("Custom model")
        form.addWidget(self.presets)
        self.description = self._label(m.lib.bl_preset_text(0, 1).decode(), form)
        self.presets.currentIndexChanged.connect(self._preset)
        row = QHBoxLayout()
        self.play = QPushButton("Play")
        self.play.clicked.connect(self._toggle_play)
        row.addWidget(self.play)
        restart = QPushButton("Restart")
        restart.clicked.connect(self._restart)
        row.addWidget(restart)
        form.addLayout(row)
        self.period_label = self._label("", form)
        self._control(form, "speed", "Cycles / second", .01, .5, .12, decimals=2)
        self._control(form, "phase", "Seek phase", 0, 1, 0, decimals=3)
        self.warning = self._label("", form)
        self.warning.setStyleSheet("color: #e0b184;")
        tabs = self.tabs = QTabWidget()
        form.addWidget(tabs)
        pages = {}
        for name in ("Stars", "Model", "Observe", "Library", "Look"):
            page = QWidget()
            pages[name] = QVBoxLayout(page)
            pages[name].setContentsMargins(0, 10, 0, 0)
            tabs.addTab(page, name)
        self.star_titles = {}
        for suffix, label in (("a", "PRIMARY / A"), ("b", "SECONDARY / B")):
            self.star_titles[suffix] = self._label(label, pages["Stars"])
            for key, title, low, high, decimals, log in (
                ("radius", "Radius / R☉", .001, 1500, 3, True), ("temperature", "Temperature / K", 1500, 200000, 0, True),
                ("mass", "Mass / M☉", .01, 100, 3, True), ("limb", "Limb darkening", 0, 1, 2, False)):
                self._control(pages["Stars"], key+"_"+suffix, title, low, high, m.get(key+"_"+suffix), decimals=decimals, logarithmic=log)
            setattr(self, "luminosity_"+suffix, self._label("", pages["Stars"]))
        self.star_help = self._label("Radii are physical, with no display enlargement. Temperature sets band brightness; mass sets orbital motion. Linear limb coefficients are user supplied.", pages["Stars"])
        for key, title, low, high, decimals, log in (
            ("inclination", "Inclination / deg", 0, 90, 2, False), ("separation", "Separation / R☉", .1, 2000, 3, True),
            ("period", "Period / days", .001, 5000, 4, True), ("eccentricity", "Eccentricity", 0, .9, 3, False),
            ("omega", "Periastron / deg", -180, 180, 1, False), ("node", "Sky rotation / deg", -180, 180, 1, False)):
            self._control(pages["Model"], key, title, low, high, m.period if key == "period" else m.get(key), decimals=decimals, logarithmic=log)
        self.orbit_help = self._label("90° = edge-on; 0° = face-on. Separation is the relative orbit’s semi-major axis. Editing period changes separation; editing mass changes period.", pages["Model"])
        self.orbit_info = self._label("", pages["Model"])
        self.phase_help = self._label("Phase zero is periastron, or the reference epoch for a circular orbit; it need not be an eclipse. Sky rotation changes position angle, not photometry. R / Roche lobe above about 0.7 suggests tidal distortion; this estimate at periastron is only a diagnostic for eccentric systems.", pages["Model"])
        for key, (label, low, high, decimals, logarithmic) in CONTROLS.items():
            self._control(pages["Model"], key, label, low, high, self.model.get(key), decimals=decimals, logarithmic=logarithmic)
        self.scope_label = self._label("", pages["Model"])
        self.band = QComboBox()
        self.band.addItems(["Bolometric", "Blue / 390–490 nm", "Visual / 500–600 nm", "Red / 600–750 nm"])
        self.band.setCurrentIndex(2)
        pages["Observe"].addWidget(self.band)
        self.band.currentIndexChanged.connect(lambda v: self._edit("band", v))
        self._label("Ideal rectangular energy bands; blackbody spectra. Not calibrated Johnson BVR filters.", pages["Observe"])
        for key, title, low, high, value, decimals, log in (
            ("distance", "Distance / pc", 1, 100000, 100, 1, True), ("third_light", "Third light", 0, .95, 0, 3, False),
            ("exposure", "Exposure / s", 0, m.period*86400*.05, 0, 1, False),
            ("noise", "Noise / ppm", 0, 10000, 0, 0, False), ("comparison", "Comparison flux", .1, 10, 1, 2, True)):
            self._control(pages["Observe"], key, title, low, high, value, decimals=decimals, logarithmic=log)
        self._label("Third light is constant contamination as a fraction of the total uneclipsed light. Comparison flux is relative to the uneclipsed target: Δmag = −2.5 log₁₀(target / comparison). Noise is illustrative Gaussian scatter in normalized flux.", pages["Observe"])
        self.export_model = QPushButton("Export model CSV…")
        self.export_observed = QPushButton("Export observations…")
        self.save_system = QPushButton("Save system…")
        self.load_system = QPushButton("Load system…")
        for button, action in ((self.export_model, "model"), (self.export_observed, "observed"), (self.save_system, "save"), (self.load_system, "load")):
            pages["Observe"].addWidget(button)
            button.clicked.connect(lambda checked=False, a=action: self._file(a))
        self._label("VARIABLE-STAR ATLAS", pages["Library"])
        self._label("Select an example to load its surface model, observing controls and light curve. These are educational models with editable assumptions.", pages["Library"])
        self.library_buttons = {}
        for family, entry in {0: ("EA · detached binaries", "Spherical Keplerian binaries; six original examples."), **FAMILIES}.items():
            button = QPushButton("Load  " + entry[0])
            button.clicked.connect(lambda checked=False, f=family: self.presets.setCurrentIndex(0 if f == 0 else f+5))
            pages["Library"].addWidget(button)
            self.library_buttons[family] = button
            self._label(entry[1], pages["Library"])
        self._label("Models use blackbody bands and approximate surface physics. Each model's scope and limitations appear in the Model tab and exported metadata.", pages["Library"])
        for key, title, low, high in (("zoom", "Zoom", .5, 3), ("exposure", "Display exposure", .3, 3), ("glow", "Stellar glow", 0, 3), ("detail", "Granulation", 0, 1), ("sky", "Star field", 0, 2)):
            self._control(pages["Look"], "look_"+key, title, low, high, self.look[key], decimals=2)
        self.orbit = QCheckBox("Orbital diagram")
        self.orbit.setChecked(True)
        self.custom_tint = QCheckBox("Custom stellar colors")
        pages["Look"].addWidget(self.orbit)
        pages["Look"].addWidget(self.custom_tint)
        for j, name in enumerate(("Primary color…", "Secondary color…")):
            button = QPushButton(name)
            button.setEnabled(False)
            self.custom_tint.toggled.connect(button.setEnabled)
            button.clicked.connect(lambda checked=False, index=j: self._color(index))
            pages["Look"].addWidget(button)
        self._label("Stellar glow adds soft camera bloom from visible starlight and fades during eclipses. These display controls do not change the light curve. Default colors follow temperature; the observer view retains the physical radius ratio.", pages["Look"])
        for layout in pages.values():
            layout.addStretch()
        self.status = self._label("", form)
        form.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(side)
        scroll.setFixedWidth(390)
        self._layout.addWidget(scroll)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.scene = BinaryScene(self)
        self.scene.failed.connect(self.status.setText)
        splitter.addWidget(self.scene)
        graph = QWidget()
        graph_layout = QVBoxLayout(graph)
        graph_layout.setContentsMargins(12, 8, 12, 4)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("LIVE LIGHT CURVE"))
        self.graph_mode = QComboBox()
        self.graph_mode.addItems(["Relative flux", "Differential mag"])
        bar.addWidget(self.graph_mode)
        self.prediction = QCheckBox("Show prediction")
        bar.addWidget(self.prediction)
        self.sample_count = QLabel("0 samples")
        bar.addWidget(self.sample_count)
        bar.addStretch()
        graph_layout.addLayout(bar)
        self.plot = LightCurvePlot(self)
        graph_layout.addWidget(self.plot, 1)
        splitter.addWidget(graph)
        splitter.setSizes([650, 300])
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 3)
        right_layout.addWidget(splitter)
        self._layout.addWidget(right, 1)
        self.shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self.shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.shortcut.activated.connect(self._toggle_play)
        self._changed()

    def _preset(self, index):
        if index >= 22:
            return
        if index < 6:
            self.model.set_preset(index)
            self.description.setText(self.model.lib.bl_preset_text(index, 1).decode())
        else:
            self.model.select_family(index-5)
            self.description.setText(FAMILIES[self.model.family][1])
        self._sync()
        self._changed()

    def _configure_family(self):
        m = self.model
        active = FAMILIES[m.family][3] if m.family else ()
        for key in CONTROLS:
            self.controls[key].setVisible(key in active)
        for suffix in ('a', 'b'):
            visible = suffix == 'a' or m.binary
            self.star_titles[suffix].setVisible(visible)
            getattr(self, 'luminosity_'+suffix).setVisible(visible)
            for field in ('radius','temperature','mass','limb'):
                self.controls[field+'_'+suffix].setVisible(visible)
                self.controls[field+'_'+suffix].setEnabled(not (m.family in (1,2) and field == 'radius'))
        for key in ('separation','eccentricity','omega','node','inclination'):
            visible = m.binary or key in ('inclination','node') and m.family in (9,10,13)
            if key == 'eccentricity':visible = m.family == 0
            if key == 'omega':visible = m.family in (0,1,2)
            self.controls[key].setVisible(visible)
        self.controls['omega'].label.setText('Periastron / deg' if not m.family else 'Orbital epoch / deg')
        self.controls['mass_a'].label.setText('Mass / M☉' if m.binary else 'Reference mass / M☉')
        self.orbit_help.setVisible(m.family == 0)
        self.phase_help.setVisible(m.family == 0)
        self.orbit_info.setVisible(m.family in (0,1,2))
        self.orbit.setEnabled(m.family == 0)
        self.controls['period'].label.setText('Period / days' if m.periodic else 'Time window / days' if m.family >= 14 else 'Primary period / days')
        self.controls['phase'].label.setText('Seek phase' if m.periodic else 'Seek time / days')
        self.controls['phase'].set_maximum(1 if m.periodic else 5*m.period)
        self.controls['phase'].spin.setDecimals(3 if m.periodic else 5)
        self.controls['speed'].label.setText('Cycles / second' if m.periodic else 'Time windows / second')
        self.scope_label.setText(m.scope if m.family else SCOPE)
        self.star_help.setText('Roche geometry sets the stellar sizes. Temperatures are polar reference values; the Model tab controls filling, gravity darkening and irradiation.' if m.family in (1,2) else 'Physical radii are shown to scale. Temperature sets the blackbody band brightness. Pulsation and event controls are in the Model tab.' if m.family else 'Radii are physical. Temperature sets band brightness; mass sets orbital motion.')

    def _sync(self):
        m = self.model
        self._configure_family()
        for key, control in self.controls.items():
            if key in ("speed", "phase", "noise", "comparison") or key.startswith("look_"):
                continue
            if key == "exposure":
                control.set_maximum(m.period*86400*.05)
            control.set_value(m.period if key == "period" else m.get(key))
        self.controls["noise"].set_value(m.noise_ppm)
        self.controls["comparison"].set_value(m.comparison)
        self.band.blockSignals(True)
        self.band.setCurrentIndex(int(m.get("band")))
        self.band.blockSignals(False)

    def _edit(self, key, value):
        m = self.model
        if key.startswith("look_"):
            self.look[key[5:]] = value
            return
        if key == "speed":
            return
        if key == "phase":
            self.playing = False
            self.play.setText("Play")
            m.restart(value if m.periodic else value/m.period)
            if not m.periodic:self._rebuild_curve()
            return
        if key == "period":
            m.set_period(value)
        elif key == "noise":
            m.noise_ppm = value
        elif key == "comparison":
            m.comparison = value
        else:
            m.set(key, value)
        self.presets.setCurrentIndex(22)
        self.description.setText("Custom · " + m.name)
        self._sync()
        self._changed()

    def _changed(self):
        m = self.model
        m.restart()
        self._curve_generation += 1
        self.prediction_range = (0, 1)
        self.prediction_curve = np.array([])
        error = m.invalid
        self.play.setEnabled(not error)
        self.export_model.setEnabled(not error)
        self.save_system.setEnabled(not error)
        self.controls["phase"].setEnabled(not error)
        if error:
            self.playing = False
            self.play.setText("Play")
        tidal = not m.family and (m.roche(0) > .7 or m.roche(1) > .7)
        self.warning.setText(error or ("Tidal distortion may be significant. This spherical prediction omits ellipsoidal variation." if tidal else ""))
        self.period_label.setText(f"{'Period' if m.periodic else 'Time scale'}  {m.period:.5g} days")
        for suffix in ("a", "b"):
            luminosity = m.get("radius_"+suffix)**2*(m.get("temperature_"+suffix)/5772)**4
            getattr(self, "luminosity_"+suffix).setText(f"Bolometric luminosity: {luminosity:.2f} L☉")
        self.orbit_info.setText(f"Periastron separation: {m.get('separation')*(1-m.get('eccentricity')):.3f} R☉\nR / approximate Roche lobe: {m.roche(0):.2f}, {m.roche(1):.2f}")
        if m.family in (1, 2) and not error:
            info = m.info()
            self.orbit_info.setText(f"Volume-equivalent radii: {info[3]:.3f}, {info[4]:.3f} R☉\nCircular, synchronous Roche surfaces")
            for suffix, radius in zip(("a", "b"), info[3:]):
                self.controls["radius_"+suffix].set_value(radius)
                getattr(self, "luminosity_"+suffix).setText("Surface brightness includes gravity darkening")
        self._configure_family()
        self.rebuild_timer.start()

    def _rebuild_curve(self):
        if self.model.invalid:
            return
        m = self.model
        span = (0, 1) if m.periodic else (max(0, math.floor(m.cycles)-1), max(1, math.floor(m.cycles)+1))
        if span != self.prediction_range:
            self.prediction_curve = np.array([])
        self.prediction_range = span
        if not m.family:
            self.prediction_curve = m.curve()[:, 0]
        else:
            self._curve_generation += 1
            if self._curve_future:
                self._curve_future.cancel()
            snapshot = m.snapshot()
            self._curve_future = self._executor.submit(lambda: snapshot.curve(301, *span)[:, 0])
            self._pending_generation = self._curve_generation
            self.status.setText('Calculating surface light curve…')
        self.plot.update()

    def _toggle_play(self):
        # Text entry retains Space rather than starting a recording.
        from PySide6.QtWidgets import QApplication
        if isinstance(QApplication.focusWidget(), QLineEdit):
            return
        if not self.model.invalid:
            self.playing = not self.playing
            self.play.setText("Pause" if self.playing else "Play")

    def _restart(self):
        self.model.restart()
        if not self.model.periodic:
            self.prediction_curve = np.array([])
            self._rebuild_curve()

    def _tick(self):
        now = time.monotonic()
        dt, self._last_tick = now-self._last_tick, now
        if self.playing:
            self.model.advance(dt, self.controls["speed"].spin.value())
        phase_control = self.controls["phase"]
        if not self.model.periodic and self.model.cycles*self.model.period > phase_control.high:
            phase_control.set_maximum((math.ceil(self.model.cycles)+1)*self.model.period)
        if not phase_control.spin.hasFocus() and not phase_control.spin.lineEdit().hasFocus():
            phase_control.set_value(self.model.cycles % 1 if self.model.periodic else self.model.cycles*self.model.period)
        if self._curve_future and self._curve_future.done():
            if self._pending_generation == self._curve_generation:
                try:
                    self.prediction_curve = self._curve_future.result()
                    self.status.setText('')
                except Exception as exc:
                    self.status.setText(str(exc))
            self._curve_future = None
        if not self.model.periodic and self.model.cycles >= self.prediction_range[1]:
            self._rebuild_curve()
        self.sample_count.setText(f"{len(self.model.samples):,} samples")
        self.export_observed.setEnabled(bool(self.model.samples) and not self.model.invalid)
        self.scene.update()
        self.plot.update()

    def _color(self, index):
        color = QColorDialog.getColor(QColor.fromRgbF(*self.tints[index]), self, "Stellar display color")
        if color.isValid():
            self.tints[index] = [color.redF(), color.greenF(), color.blueF()]

    def _file(self, action):
        system = action in ("save", "load")
        name = "stellar-model.starlab" if system else "stellar-light-curve.csv"
        filter_text = "CAst Stellar Lab (*.starlab *.binary)" if system else "CSV (*.csv)"
        dialog = QFileDialog.getOpenFileName if action == "load" else QFileDialog.getSaveFileName
        path, _ = dialog(self, {"save": "Save system", "load": "Load system", "model": "Export model CSV", "observed": "Export model and observations"}[action], name, filter_text)
        if not path:
            return
        try:
            if action == "load":
                self.model.load(path)
                self.presets.setCurrentIndex(22)
                self.description.setText("Loaded · " + self.model.name)
                self._sync()
                self._changed()
            elif action == "save":
                self.model.save(path)
            else:
                path = self.model.export(path, action == "observed")
            self.status.setText(f"{'Loaded' if action == 'load' else 'Saved'} {path}")
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))
