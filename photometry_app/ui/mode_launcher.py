from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QLinearGradient,
    QMovie,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from photometry_app.app_metadata import APP_DISPLAY_NAME, application_root_path
from photometry_app.core.models import AppMode

ModeLauncherTier = Literal["science", "explore"]

SCIENCE_WORKFLOW_LABEL = "Science Workflows"
VISUALIZATIONS_TOOLS_LABEL = "Visualizations & Tools"
EXPLORE_LEARN_LABEL = VISUALIZATIONS_TOOLS_LABEL

_CONTENT_MAX_WIDTH = 1720
_CARD_RADIUS = 6.0
_CARD_GAP = 20
_CARD_CHROME_PAD = 5.0
_HOVER_ANIMATION_MS = 210

_LABEL_COLUMN_WIDTH_MIN = 300
_LABEL_RAIL_LINE_X = 10
_LABEL_TEXT_INSET = 22
_CONTENT_MARGIN_LEFT = 48
_CONTENT_MARGIN_RIGHT = 72
_CONTENT_MARGIN_TOP = 16
_CONTENT_MARGIN_BOTTOM = 16

_EDITORIAL_SPLIT_GAP = 52
_STACK_LAYOUT_BREAKPOINT = 880
_SECTION_VERTICAL_GAP = 24

_CARD_HEIGHT_MIN = 108

_SCIENCE_HEIGHT_LARGE = 434
_SCIENCE_HEIGHT_MEDIUM = 392
_SCIENCE_HEIGHT_COMPACT = 355
_EXPLORE_HEIGHT_LARGE = _SCIENCE_HEIGHT_LARGE
_EXPLORE_HEIGHT_MEDIUM = _SCIENCE_HEIGHT_MEDIUM
_EXPLORE_HEIGHT_COMPACT = _SCIENCE_HEIGHT_COMPACT

_CELESTIAL_SCALE = 2.0
_CELESTIAL_BRIGHTNESS = 1.48
_CELESTIAL_ROTATION_DEG = -30.0

_BREAKPOINT_SCIENCE_3COL = 820
_BREAKPOINT_SCIENCE_2COL = 520
_BREAKPOINT_EXPLORE_5COL = 480
_BREAKPOINT_EXPLORE_3COL = 660
_BREAKPOINT_EXPLORE_2COL = 420

LayoutDensity = Literal["large", "medium", "compact"]

_AMBIENT_NODES: tuple[tuple[float, float, float], ...] = (
    (420.0, 0.0, 0.32),
    (600.0, 1.4, 0.26),
    (780.0, 2.8, 0.21),
    (960.0, 4.2, 0.17),
)


def launcher_grid_column_count(width: int, *, tier: ModeLauncherTier, card_count: int = 3) -> int:
    if card_count <= 1:
        return 1
    if tier == "science":
        if width >= _BREAKPOINT_SCIENCE_3COL:
            return min(3, card_count)
        if width >= _BREAKPOINT_SCIENCE_2COL:
            return min(2, card_count)
        return 1
    if card_count >= 5 and width >= _BREAKPOINT_EXPLORE_5COL:
        return min(5, card_count)
    if width >= _BREAKPOINT_EXPLORE_3COL:
        return min(3, card_count)
    if width >= _BREAKPOINT_EXPLORE_2COL:
        return min(2, card_count)
    return 1


def _build_background_star_field(count: int = 336) -> tuple[tuple[float, float, float, float], ...]:
    rng = random.Random(90817)
    stars: list[tuple[float, float, float, float]] = []
    for _ in range(count):
        if rng.random() < 0.34:
            x_frac = rng.uniform(0.54, 0.995)
            y_frac = rng.uniform(0.02, 0.52)
        else:
            x_frac = rng.uniform(0.015, 0.985)
            y_frac = rng.uniform(0.015, 0.985)
        size = rng.uniform(0.59, 1.76)
        twinkle_phase = rng.uniform(0.0, math.tau)
        stars.append((x_frac, y_frac, size, twinkle_phase))
    return tuple(stars)


_BACKGROUND_STARS: tuple[tuple[float, float, float, float], ...] = _build_background_star_field()

_NOISE_TEXTURE: QPixmap | None = None


@dataclass(frozen=True, slots=True)
class ModeLauncherEntry:
    mode: AppMode
    title: str
    subtitle: str
    tier: ModeLauncherTier
    image_names: tuple[str, ...]
    gradient_top: str
    gradient_bottom: str
    dim_image: bool = False
    badge: str | None = None


SCIENCE_WORKFLOW_ENTRIES: tuple[ModeLauncherEntry, ...] = (
    ModeLauncherEntry(
        AppMode.DIFFERENTIAL_PHOTOMETRY,
        "Differential Photometry",
        "Measure light curves of variables, exoplanets, and eclipsing binaries.",
        "science",
        image_names=("differential_photometry.jpg", "differential_photometry.png", "differential_photometry.webp"),
        gradient_top="#1a2744",
        gradient_bottom="#0d1528",
    ),
    ModeLauncherEntry(
        AppMode.ASTEROID_COMET_DETECTION,
        "Asteroid / Comet Detection",
        "Detect moving objects and inspect their trajectories in 3D.",
        "science",
        image_names=("asteroid.gif", "asteroid_comet_detection.png", "asteroid_comet_detection.jpg"),
        gradient_top="#1f2430",
        gradient_bottom="#10141d",
    ),
    ModeLauncherEntry(
        AppMode.TRANSIENT_FINDER,
        "Transient Finder",
        "Search for new or fading transients, including supernovae.",
        "science",
        image_names=("Transient.gif", "transient.gif", "transient_finder.png", "transient_finder.jpg"),
        gradient_top="#241a2e",
        gradient_bottom="#100a14",
        dim_image=True,
    ),
)

EXPLORE_LEARN_ENTRIES: tuple[ModeLauncherEntry, ...] = (
    ModeLauncherEntry(
        AppMode.SKY_VIEW,
        "Sky Atlas",
        "Explore the sky and objects with an interactive all-sky map.",
        "explore",
        image_names=("sky_atlas.jpg", "sky_atlas.png", "sky_atlas.webp", "sky_view.png", "sky_view.jpg", "sky_view.webp"),
        gradient_top="#121f35",
        gradient_bottom="#070c16",
    ),
    ModeLauncherEntry(
        AppMode.SKY_EXPLORER,
        "Sky Explorer",
        "Identify objects and create customizable sky annotations.",
        "explore",
        image_names=("Sky_Explorer.png", "sky_explorer.png", "sky_explorer.jpg", "sky_explorer.webp"),
        gradient_top="#30182a",
        gradient_bottom="#140b12",
        dim_image=True,
    ),
    ModeLauncherEntry(
        AppMode.HR_DIAGRAM,
        "HR Diagram",
        "Explore stellar color, luminosity, clusters, and evolution.",
        "explore",
        image_names=("hr_diagram.png", "hr_diagram.jpg", "hr_diagram.webp"),
        gradient_top="#172238",
        gradient_bottom="#0b111c",
    ),
    ModeLauncherEntry(
        AppMode.DISTANCE_MAP,
        "Distance Map",
        "Map catalog stars in 3D using their measured distances.",
        "explore",
        image_names=("distance_map.png", "distance_map.jpg", "distance_map.webp"),
        gradient_top="#142238",
        gradient_bottom="#08111f",
    ),
    ModeLauncherEntry(
        AppMode.ASTROSTACK,
        "Deep Stack",
        "Stack, align, and export cumulative deep-sky animations from solved frames.",
        "explore",
        image_names=("astrostack.gif", "deep_stack.png", "deep_stack.jpg", "astrostack.png", "astrostack.jpg", "astrostack.webp"),
        gradient_top="#1a2438",
        gradient_bottom="#0a1018",
    ),
)

MODE_LAUNCHER_ENTRIES: tuple[ModeLauncherEntry, ...] = SCIENCE_WORKFLOW_ENTRIES + EXPLORE_LEARN_ENTRIES


def _noise_texture() -> QPixmap:
    global _NOISE_TEXTURE
    if _NOISE_TEXTURE is not None:
        return _NOISE_TEXTURE
    rng = random.Random(42)
    size = 256
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    for y in range(size):
        for x in range(size):
            value = rng.randint(0, 255)
            alpha = rng.randint(8, 22)
            image.setPixelColor(x, y, QColor(value, value, value, alpha))
    _NOISE_TEXTURE = QPixmap.fromImage(image)
    return _NOISE_TEXTURE


def _resolve_mode_launcher_image(image_names: tuple[str, ...]) -> Path | None:
    assets_dir = application_root_path() / "assets" / "mode_launcher"
    if not assets_dir.is_dir():
        return None
    available_by_name = {path.name.casefold(): path for path in assets_dir.iterdir() if path.is_file()}
    for image_name in image_names:
        candidate = assets_dir / image_name
        if candidate.is_file():
            return candidate
        matched = available_by_name.get(image_name.casefold())
        if matched is not None:
            return matched
    return None


def _draw_cover_pixmap(painter: QPainter, pixmap: QPixmap, target_rect: QRectF) -> None:
    if pixmap.isNull():
        return
    scaled = pixmap.scaled(
        target_rect.size().toSize(),
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    offset_x = target_rect.left() + (target_rect.width() - scaled.width()) / 2.0
    offset_y = target_rect.top() + (target_rect.height() - scaled.height()) / 2.0
    painter.drawPixmap(int(offset_x), int(offset_y), scaled)


def _paint_toy_sun_accent(painter: QPainter, center: QPointF, phase: float, *, bright: float) -> None:
    pulse = 0.94 + 0.06 * math.sin(phase * 0.65)
    glow_mix = 0.68 + 0.32 * math.sin(phase * 1.05)
    core_radius = 29.0 * pulse
    corona = QRadialGradient(center, core_radius * 2.6)
    corona.setColorAt(0.0, QColor(255, 196, 88, int(16 * bright * glow_mix)))
    corona.setColorAt(0.45, QColor(255, 168, 58, int(9 * bright * glow_mix)))
    corona.setColorAt(1.0, QColor(0, 0, 0, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(corona)
    painter.drawEllipse(center, core_radius * (2.1 + 0.08 * glow_mix), core_radius * (2.1 + 0.08 * glow_mix))

    disc = QRadialGradient(center, core_radius)
    disc.setColorAt(0.0, QColor(255, 232, 150, int(58 * bright * glow_mix)))
    disc.setColorAt(0.52, QColor(255, 196, 88, int(44 * bright * glow_mix)))
    disc.setColorAt(0.86, QColor(244, 158, 52, int(30 * bright * glow_mix)))
    disc.setColorAt(1.0, QColor(220, 128, 36, int(20 * bright * glow_mix)))
    painter.setBrush(disc)
    painter.drawEllipse(center, core_radius, core_radius)

    rim = QPen(QColor(255, 228, 160, int(40 * bright * glow_mix)), 1.1)
    painter.setPen(rim)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(center, core_radius, core_radius)


def _paint_twinkle_star(
    painter: QPainter,
    center: QPointF,
    arm: float,
    alpha: int,
) -> None:
    if alpha < 4:
        return
    color = QColor(220, 236, 255, alpha)
    painter.setPen(QPen(color, max(0.55, arm * 0.32)))
    painter.drawLine(QPointF(center.x() - arm, center.y()), QPointF(center.x() + arm, center.y()))
    painter.drawLine(QPointF(center.x(), center.y() - arm), QPointF(center.x(), center.y() + arm))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(center, arm * 0.24, arm * 0.24)


def _paint_twinkling_stars(painter: QPainter, rect: QRectF, phase: float) -> None:
    for x_frac, y_frac, size, twinkle_phase in _BACKGROUND_STARS:
        twinkle = 0.62 + 0.38 * math.sin(phase * 1.85 + twinkle_phase)
        star_x = rect.left() + rect.width() * x_frac
        star_y = rect.top() + rect.height() * y_frac
        arm = size * 2.17
        star_alpha = int(30 + 26 * twinkle)
        _paint_twinkle_star(painter, QPointF(star_x, star_y), arm, star_alpha)


def _orbit_point(focus: QPointF, radius: float, angle_rad: float, *, flatten: float = 0.54) -> QPointF:
    return QPointF(
        focus.x() + radius * math.cos(angle_rad),
        focus.y() + radius * flatten * math.sin(angle_rad),
    )


def _paint_reticle(painter: QPainter, focus: QPointF, phase: float) -> None:
    scale = _CELESTIAL_SCALE
    bright = _CELESTIAL_BRIGHTNESS
    reticle_pen = QPen(QColor(120, 168, 210, int(38 * bright)))
    reticle_pen.setWidthF(1.0)
    painter.setPen(reticle_pen)
    span = (280.0 + math.sin(phase * 0.7) * 18.0) * scale
    painter.drawLine(QPointF(focus.x() - span, focus.y()), QPointF(focus.x() + span, focus.y()))
    painter.drawLine(QPointF(focus.x(), focus.y() - span * 0.55), QPointF(focus.x(), focus.y() + span * 0.55))


def _paint_celestial_motif(painter: QPainter, rect: QRectF, focus: QPointF, phase: float) -> None:
    scale = _CELESTIAL_SCALE
    bright = _CELESTIAL_BRIGHTNESS
    painter.save()
    painter.translate(focus.x(), focus.y())
    painter.rotate(_CELESTIAL_ROTATION_DEG)
    origin = QPointF(0.0, 0.0)

    grid_spacing = 56.0 * scale
    max_grid_dist = min(rect.width(), rect.height()) * 0.58 * scale
    drift_x = math.sin(phase * 0.35) * 12.0 * scale
    drift_y = math.cos(phase * 0.28) * 8.0 * scale
    grid_origin = QPointF(origin.x() + drift_x, origin.y() + drift_y)

    x = grid_origin.x() - max_grid_dist
    while x <= grid_origin.x() + max_grid_dist:
        dist = abs(x - grid_origin.x()) / max_grid_dist
        alpha = int(46 * bright * max(0.0, 1.0 - dist * dist))
        if alpha > 3:
            painter.setPen(QPen(QColor(118, 154, 192, alpha), 1.0))
            painter.drawLine(QPointF(x, grid_origin.y() - max_grid_dist), QPointF(x, grid_origin.y() + max_grid_dist))
        x += grid_spacing
    y = grid_origin.y() - max_grid_dist
    while y <= grid_origin.y() + max_grid_dist:
        dist = abs(y - grid_origin.y()) / max_grid_dist
        alpha = int(38 * bright * max(0.0, 1.0 - dist * dist))
        if alpha > 3:
            painter.setPen(QPen(QColor(118, 154, 192, alpha), 1.0))
            painter.drawLine(QPointF(grid_origin.x() - max_grid_dist, y), QPointF(grid_origin.x() + max_grid_dist, y))
        y += grid_spacing

    orbit_pen = QPen(QColor(156, 192, 228, int(52 * bright)))
    orbit_pen.setWidthF(1.35)
    painter.setPen(orbit_pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for index, radius in enumerate((160.0, 250.0, 340.0, 440.0, 560.0)):
        if index == 0:
            continue
        wobble = math.sin(phase * 0.45 + index * 0.6) * 6.0 * scale
        active_radius = radius * scale + wobble
        orbit_pen.setColor(QColor(156, 192, 228, int((58 - index * 6) * bright)))
        painter.setPen(orbit_pen)
        arc_rect = QRectF(
            origin.x() - active_radius,
            origin.y() - active_radius * 0.54,
            active_radius * 2.0,
            active_radius * 1.08,
        )
        start = int((205 + math.sin(phase + index) * 4) * 16)
        painter.drawArc(arc_rect, start, 130 * 16)
        painter.drawArc(arc_rect, start + 180 * 16, 130 * 16)

    for radius, node_phase, speed in _AMBIENT_NODES:
        angle = phase * speed + node_phase
        node = _orbit_point(origin, radius, angle)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(210, 232, 255, int(72 * bright)))
        painter.drawEllipse(node, 1.4 * scale, 1.4 * scale)

    scan_y = origin.y() + math.sin(phase * 0.9) * max_grid_dist * 0.42
    scan = QLinearGradient(QPointF(origin.x() - max_grid_dist, scan_y), QPointF(origin.x() + max_grid_dist, scan_y))
    scan.setColorAt(0.0, QColor(140, 190, 230, 0))
    scan.setColorAt(0.48, QColor(140, 190, 230, int(22 * bright)))
    scan.setColorAt(0.52, QColor(140, 190, 230, int(22 * bright)))
    scan.setColorAt(1.0, QColor(140, 190, 230, 0))
    painter.fillRect(QRectF(origin.x() - max_grid_dist, scan_y - 1.0, max_grid_dist * 2.0, 2.0 * scale), scan)

    _paint_reticle(painter, origin, phase)
    painter.restore()


def _paint_launcher_background(
    painter: QPainter,
    rect: QRectF,
    *,
    focus: QPointF | None = None,
    phase: float = 0.0,
    card_regions: tuple[QRectF, ...] = (),
) -> None:
    focus_point = focus or QPointF(rect.left() + rect.width() * 0.14, rect.top() + rect.height() * 0.84)
    bright = _CELESTIAL_BRIGHTNESS

    base = QLinearGradient(rect.topLeft(), rect.bottomRight())
    base.setColorAt(0.0, QColor("#020409"))
    base.setColorAt(0.4, QColor("#060c15"))
    base.setColorAt(1.0, QColor("#010103"))
    painter.fillRect(rect, base)

    _paint_celestial_motif(painter, rect, focus_point, phase)
    _paint_toy_sun_accent(painter, focus_point, phase, bright=bright)

    if card_regions:
        combined = card_regions[0]
        for region in card_regions[1:]:
            combined = combined.united(region)
        pad_x = 36.0
        pad_y = 28.0
        scrim_rect = combined.adjusted(-pad_x, -pad_y, pad_x, pad_y)
        scrim = QRadialGradient(scrim_rect.center(), max(scrim_rect.width(), scrim_rect.height()) * 0.58)
        scrim.setColorAt(0.0, QColor(4, 8, 16, 88))
        scrim.setColorAt(0.45, QColor(4, 8, 16, 52))
        scrim.setColorAt(0.78, QColor(2, 5, 10, 22))
        scrim.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(scrim_rect, scrim)
        band = QLinearGradient(QPointF(scrim_rect.left(), scrim_rect.top()), QPointF(scrim_rect.left(), scrim_rect.bottom()))
        band.setColorAt(0.0, QColor(0, 0, 0, 0))
        band.setColorAt(0.12, QColor(2, 5, 10, 36))
        band.setColorAt(0.88, QColor(2, 5, 10, 36))
        band.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(scrim_rect, band)

    corner_haze_right = QRadialGradient(QPointF(rect.right(), rect.top()), rect.width() * 0.4)
    corner_haze_right.setColorAt(0.0, QColor(78, 48, 112, 28))
    corner_haze_right.setColorAt(1.0, QColor(0, 0, 0, 0))
    painter.fillRect(rect, corner_haze_right)

    _paint_twinkling_stars(painter, rect, phase)

    vignette = QRadialGradient(rect.center(), max(rect.width(), rect.height()) * 0.74)
    vignette.setColorAt(0.0, QColor(0, 0, 0, 0))
    vignette.setColorAt(0.55, QColor(0, 0, 0, 32))
    vignette.setColorAt(1.0, QColor(0, 0, 0, 200))
    painter.fillRect(rect, vignette)

    noise = _noise_texture()
    painter.setOpacity(0.32)
    painter.drawTiledPixmap(rect.toRect(), noise)
    painter.setOpacity(1.0)


class ModeLauncherCard(QPushButton):
    def __init__(
        self,
        entry: ModeLauncherEntry,
        *,
        accent_color: str = "#3d8bfd",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._entry = entry
        self._accent_color = QColor(accent_color)
        self._hover_progress = 0.0
        self._pressed = False
        self._card_height = 200
        self._layout_density: LayoutDensity = "large"
        self._hover_animation = QPropertyAnimation(self, b"hoverProgress")
        self._hover_animation.setDuration(_HOVER_ANIMATION_MS)
        self._hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._image_path = _resolve_mode_launcher_image(entry.image_names)
        self._movie: QMovie | None = None
        self._pixmap: QPixmap | None = None
        if self._image_path is not None:
            if self._image_path.suffix.casefold() == ".gif":
                self._movie = QMovie(str(self._image_path))
                self._movie.setCacheMode(QMovie.CacheMode.CacheAll)
                self._movie.frameChanged.connect(self.update)
            else:
                pixmap = QPixmap(str(self._image_path))
                if not pixmap.isNull():
                    self._pixmap = pixmap
        if self._movie is not None:
            self._movie.start()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFlat(True)
        self.setToolTip("")
        self.setAccessibleName(f"{entry.title}. {entry.subtitle}")

    def get_hover_progress(self) -> float:
        return self._hover_progress

    def set_hover_progress(self, value: float) -> None:
        self._hover_progress = max(0.0, min(1.0, float(value)))
        self.update()

    hoverProgress = Property(float, get_hover_progress, set_hover_progress)

    @property
    def entry(self) -> ModeLauncherEntry:
        return self._entry

    def set_layout_metrics(self, *, height: int, density: LayoutDensity) -> None:
        changed = False
        if self._card_height != height:
            self._card_height = height
            self.setMinimumHeight(height)
            self.setMaximumHeight(height)
            changed = True
        if self._layout_density != density:
            self._layout_density = density
            changed = True
        if changed:
            self.update()

    def _animate_hover(self, target: float) -> None:
        self._hover_animation.stop()
        self._hover_animation.setStartValue(self._hover_progress)
        self._hover_animation.setEndValue(target)
        self._hover_animation.start()

    def _title_font(self) -> QFont:
        title_font = QFont(self.font())
        card_width = max(1, self.width())
        if self._layout_density == "large" or card_width >= 340:
            point_size = 24
        elif self._layout_density == "medium" or card_width >= 260:
            point_size = 21
        elif card_width >= 220:
            point_size = 19
        else:
            point_size = 18
        if self._entry.tier == "science":
            title_font.setWeight(QFont.Weight.Black)
            title_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.4)
        else:
            title_font.setWeight(QFont.Weight.Black)
            title_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.4)
        title_font.setPointSize(point_size)
        return title_font

    def _subtitle_font(self) -> QFont:
        subtitle_font = QFont(self.font())
        point_size = 12 if self._layout_density != "compact" else 11
        subtitle_font.setPointSize(point_size)
        subtitle_font.setWeight(QFont.Weight.Medium)
        subtitle_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.35)
        return subtitle_font

    def _current_background_pixmap(self) -> QPixmap | None:
        if self._movie is not None:
            frame = self._movie.currentPixmap()
            if not frame.isNull():
                return frame
        return self._pixmap

    def enterEvent(self, event) -> None:  # noqa: N802
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._animate_hover(0.0)
        self._pressed = False
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)

    def _content_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(
            _CARD_CHROME_PAD,
            _CARD_CHROME_PAD,
            -_CARD_CHROME_PAD,
            -_CARD_CHROME_PAD,
        )

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        hover = self._hover_progress
        content_rect = self._content_rect()
        card_path = QPainterPath()
        card_path.addRoundedRect(content_rect, _CARD_RADIUS, _CARD_RADIUS)

        painter.setClipPath(card_path)
        background = self._current_background_pixmap()
        if background is not None and not background.isNull():
            _draw_cover_pixmap(painter, background, content_rect)
            dim_alpha = 42 if self._entry.tier == "science" else 36
            if self._entry.dim_image:
                dim_alpha += 38
            dim_alpha = max(6, int(dim_alpha - 36 * hover - (14 if self.hasFocus() else 0)))
            painter.fillPath(card_path, QColor(6, 10, 18, dim_alpha))
            if hover > 0.01:
                painter.fillPath(card_path, QColor(255, 255, 255, int(38 * hover)))
        else:
            gradient = QLinearGradient(content_rect.topLeft(), content_rect.bottomRight())
            gradient.setColorAt(0.0, QColor(self._entry.gradient_top))
            gradient.setColorAt(1.0, QColor(self._entry.gradient_bottom))
            painter.fillPath(card_path, gradient)

        scrim = QLinearGradient(QPointF(content_rect.left(), content_rect.top()), QPointF(content_rect.left(), content_rect.bottom()))
        scrim.setColorAt(0.0, QColor(0, 0, 0, 0))
        scrim.setColorAt(0.38, QColor(0, 0, 0, 28))
        scrim.setColorAt(0.62, QColor(0, 0, 0, 118))
        scrim.setColorAt(0.82, QColor(0, 0, 0, 168))
        scrim.setColorAt(1.0, QColor(0, 0, 0, 198))
        painter.fillPath(card_path, scrim)

        painter.setClipping(False)
        border_mix = hover
        if self.hasFocus():
            border_color = self._accent_color
            border_width = 2.0
        elif border_mix > 0.01:
            border_color = QColor(self._accent_color)
            border_color.setAlpha(int(90 + 165 * border_mix))
            border_width = 1.25 + border_mix
        else:
            border_color = QColor(255, 255, 255, 68)
            border_width = 1.0

        border_pen = QPen(border_color, border_width)
        border_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        border_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(border_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(card_path)

        if border_mix > 0.04 or self.hasFocus():
            glow = QColor(self._accent_color)
            glow.setAlpha(int(24 + 48 * border_mix + (18 if self.hasFocus() else 0)))
            glow_pen = QPen(glow, border_width + 2.0 * border_mix)
            glow_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(glow_pen)
            painter.drawPath(card_path)

        text_inset = 28.0
        text_left = content_rect.left() + text_inset
        text_right = content_rect.right() - text_inset
        bottom_line_gap = 22.0

        title_font = self._title_font()
        subtitle_font = self._subtitle_font()
        title_metrics = QFontMetrics(title_font)
        subtitle_metrics = QFontMetrics(subtitle_font)
        title_line_count = 2 if title_metrics.horizontalAdvance(self._entry.title) > (text_right - text_left) * 0.95 else 1
        title_block_height = title_metrics.height() * title_line_count + 4.0
        subtitle_line_height = subtitle_metrics.lineSpacing() * 1.08
        subtitle_lines = 2.7
        subtitle_block_height = subtitle_line_height * subtitle_lines
        subtitle_rect = QRectF(
            text_left,
            content_rect.bottom() - bottom_line_gap - subtitle_block_height,
            text_right - text_left,
            subtitle_block_height,
        )
        title_rect = QRectF(
            text_left,
            subtitle_rect.top() - title_block_height - 2.0,
            text_right - text_left,
            title_block_height,
        )

        painter.setPen(QColor(255, 255, 255))
        painter.setFont(title_font)
        painter.drawText(
            title_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom | Qt.TextFlag.TextWordWrap),
            self._entry.title,
        )
        subtitle_color = QColor(228, 236, 246, 224)
        painter.setPen(subtitle_color)
        painter.setFont(subtitle_font)
        painter.drawText(
            subtitle_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom | Qt.TextFlag.TextWordWrap),
            self._entry.subtitle,
        )

        if self._pressed:
            painter.setClipPath(card_path)
            painter.fillPath(card_path, QColor(255, 255, 255, 18))
            painter.setClipping(False)


class _LauncherCardGrid(QWidget):
    def __init__(
        self,
        cards: list[ModeLauncherCard],
        *,
        tier: ModeLauncherTier,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._cards = cards
        self._tier = tier
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(_CARD_GAP)
        self._grid.setVerticalSpacing(_CARD_GAP)
        self._orphan_host: QWidget | None = None
        self._uniform_card_height: int | None = None
        self._uniform_density: LayoutDensity | None = None
        for card in cards:
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._reflow()

    def set_uniform_card_height(self, height: int, *, density: LayoutDensity) -> None:
        normalized_height = max(_CARD_HEIGHT_MIN, int(height))
        if self._uniform_card_height == normalized_height and self._uniform_density == density:
            return
        self._uniform_card_height = normalized_height
        self._uniform_density = density
        self._reflow()

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._reflow()
        super().resizeEvent(event)

    def _columns_for_width(self, width: int) -> int:
        return launcher_grid_column_count(width, tier=self._tier, card_count=len(self._cards))

    def _density_for_columns(self, columns: int) -> LayoutDensity:
        if columns >= 5:
            return "compact"
        if columns >= 3:
            return "large"
        if columns == 2:
            return "medium"
        return "compact"

    def _card_height_for_density(self, density: LayoutDensity) -> int:
        if density == "large":
            return _SCIENCE_HEIGHT_LARGE
        if density == "medium":
            return _SCIENCE_HEIGHT_MEDIUM
        return _SCIENCE_HEIGHT_COMPACT

    def _clear_grid(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(self)
        self._orphan_host = None

    def _reflow(self) -> None:
        self._clear_grid()
        width = max(1, self.width())
        columns = self._columns_for_width(width)
        if self._uniform_card_height is not None:
            card_height = self._uniform_card_height
            density = self._uniform_density or self._density_for_columns(columns)
        else:
            density = self._density_for_columns(columns)
            card_height = self._card_height_for_density(density)
        for card in self._cards:
            card.set_layout_metrics(height=card_height, density=density)

        count = len(self._cards)
        if count == 3 and columns == 2:
            self._grid.addWidget(self._cards[0], 0, 0)
            self._grid.addWidget(self._cards[1], 0, 1)
            self._grid.setColumnStretch(0, 1)
            self._grid.setColumnStretch(1, 1)
            self._orphan_host = QWidget(self)
            orphan_layout = QHBoxLayout(self._orphan_host)
            orphan_layout.setContentsMargins(0, 0, 0, 0)
            orphan_layout.setSpacing(0)
            orphan_layout.addStretch(1)
            orphan_layout.addWidget(self._cards[2], 0)
            orphan_layout.addStretch(1)
            single_column_width = max(1, (width - _CARD_GAP) // 2)
            self._cards[2].setMaximumWidth(single_column_width)
            self._grid.addWidget(self._orphan_host, 1, 0, 1, 2)
            total_height = card_height * 2 + _CARD_GAP
        else:
            for card in self._cards:
                card.setMaximumWidth(16777215)
            rows = (count + columns - 1) // columns
            for index, card in enumerate(self._cards):
                row = index // columns
                column = index % columns
                self._grid.addWidget(card, row, column)
                self._grid.setColumnStretch(column, 1)
            total_height = rows * card_height + max(0, rows - 1) * _CARD_GAP

        self.setMinimumHeight(total_height)
        self.setMaximumHeight(total_height)


def _science_editorial_accent(theme_accent: str) -> QColor:
    theme = QColor(theme_accent)
    warm = QColor("#e8a04c")
    if theme.lightness() < 40:
        return warm
    return QColor(
        int(warm.red() * 0.72 + theme.red() * 0.28),
        int(warm.green() * 0.72 + theme.green() * 0.28),
        int(warm.blue() * 0.72 + theme.blue() * 0.28),
    )


def _explore_editorial_accent(theme_accent: str) -> QColor:
    theme = QColor(theme_accent)
    cool = QColor("#94aac4")
    return QColor(
        int(cool.red() * 0.78 + theme.red() * 0.22),
        int(cool.green() * 0.78 + theme.green() * 0.22),
        int(cool.blue() * 0.78 + theme.blue() * 0.22),
    )


def _editorial_title_metrics(tier: ModeLauncherTier) -> tuple[tuple[str, ...], tuple[int, ...], tuple[int, int]]:
    if tier == "science":
        return (("SCIENCE", "WORKFLOWS"), (42, 38), (2, 4))
    return (("VISUALIZATIONS", "& TOOLS"), (32, 28), (3, 4))


def _measure_editorial_line_width(line: str, *, tier: ModeLauncherTier, line_index: int, line_sizes: tuple[int, ...]) -> int:
    font = QFont()
    font.setPointSize(line_sizes[min(line_index, len(line_sizes) - 1)])
    font.setWeight(QFont.Weight.Black if tier == "science" else QFont.Weight.Bold)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.8 if tier == "science" else 2.0)
    return QFontMetrics(font).horizontalAdvance(line)


def _editorial_title_column_width(_tier: ModeLauncherTier) -> int:
    max_text_width = _LABEL_COLUMN_WIDTH_MIN - _LABEL_TEXT_INSET - 14
    for tier in ("science", "explore"):
        lines, line_sizes, _ = _editorial_title_metrics(tier)
        for index, line in enumerate(lines):
            max_text_width = max(max_text_width, _measure_editorial_line_width(line, tier=tier, line_index=index, line_sizes=line_sizes))
    return max_text_width + _LABEL_TEXT_INSET + 14


class _EditorialCategoryTitle(QWidget):
    def __init__(
        self,
        lines: tuple[str, ...],
        *,
        tier: ModeLauncherTier,
        accent_color: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._lines = lines
        self._tier = tier
        self._theme_accent = accent_color
        self._ambient_phase = 0.0
        self._column_width = _editorial_title_column_width(tier)
        self.setObjectName("modeLauncherEditorialScience" if tier == "science" else "modeLauncherEditorialExplore")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.setFixedWidth(self._column_width)

    def set_accent_color(self, accent_color: str) -> None:
        self._theme_accent = accent_color
        self.update()

    def set_ambient_phase(self, phase: float) -> None:
        self._ambient_phase = phase
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        _, line_sizes, (line_spacing, top_pad) = _editorial_title_metrics(self._tier)
        total_height = top_pad
        for index, _line in enumerate(self._lines):
            font = QFont(self.font())
            font.setPointSize(line_sizes[min(index, len(line_sizes) - 1)])
            font.setWeight(QFont.Weight.Black if self._tier == "science" else QFont.Weight.Bold)
            total_height += QFontMetrics(font).height() + (line_spacing if index < len(self._lines) - 1 else 0)
        total_height += 48 if self._tier == "science" else 36
        return QSize(self._column_width, total_height)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._column_width, 120 if self._tier == "science" else 96)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        text_left = rect.left() + _LABEL_TEXT_INSET
        accent = _science_editorial_accent(self._theme_accent) if self._tier == "science" else _explore_editorial_accent(
            self._theme_accent
        )

        rail_pen = QPen(accent)
        rail_pen.setWidthF(2.0 if self._tier == "science" else 1.5)
        rail_color = QColor(accent)
        rail_color.setAlpha(185 if self._tier == "science" else 110)
        rail_pen.setColor(rail_color)
        painter.setPen(rail_pen)
        rail_top = rect.top() + 6.0
        rail_bottom = rect.bottom() - 6.0
        painter.drawLine(QPointF(rect.left() + _LABEL_RAIL_LINE_X, rail_top), QPointF(rect.left() + _LABEL_RAIL_LINE_X, rail_bottom))

        _, line_sizes, (line_spacing, top_pad) = _editorial_title_metrics(self._tier)

        y = rect.top() + top_pad
        for index, line in enumerate(self._lines):
            font = QFont(self.font())
            font.setPointSize(line_sizes[min(index, len(line_sizes) - 1)])
            font.setWeight(QFont.Weight.Black if self._tier == "science" else QFont.Weight.Bold)
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.8 if self._tier == "science" else 2.0)
            painter.setFont(font)
            text_color = QColor(accent)
            if self._tier == "explore":
                text_color.setAlpha(210)
            painter.setPen(text_color)
            metrics = QFontMetrics(font)
            painter.drawText(QPointF(text_left, y + metrics.ascent()), line)
            y += metrics.height() + line_spacing

        accent_line = QPen(accent)
        accent_line.setWidthF(2.0 if self._tier == "science" else 1.5)
        line_color = QColor(accent)
        line_color.setAlpha(170 if self._tier == "science" else 100)
        accent_line.setColor(line_color)
        painter.setPen(accent_line)
        line_y = y + 10.0
        line_width = min(self._column_width - _LABEL_TEXT_INSET - 4, 118.0 if self._tier == "science" else 86.0)
        painter.drawLine(QPointF(text_left, line_y), QPointF(text_left + line_width, line_y))

        orbit_center = QPointF(text_left + line_width * 0.55, line_y + 28.0)
        orbit_radius = 22.0 if self._tier == "science" else 16.0
        wobble = math.sin(self._ambient_phase * 0.55) * 2.0
        orbit_rect = QRectF(
            orbit_center.x() - orbit_radius - wobble,
            orbit_center.y() - orbit_radius * 0.52,
            (orbit_radius + wobble) * 2.0,
            orbit_radius * 1.04,
        )
        orbit_pen = QPen(accent)
        orbit_pen.setWidthF(1.0)
        orbit_color = QColor(accent)
        orbit_color.setAlpha(72 if self._tier == "science" else 48)
        orbit_pen.setColor(orbit_color)
        painter.setPen(orbit_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        start = int((210 + math.sin(self._ambient_phase) * 6) * 16)
        painter.drawArc(orbit_rect, start, 118 * 16)

        node_angle = self._ambient_phase * 0.65 + (0.8 if self._tier == "science" else 2.1)
        node = _orbit_point(orbit_center, orbit_radius * 0.88, node_angle, flatten=0.52)
        node_color = QColor(accent)
        node_color.setAlpha(130 if self._tier == "science" else 85)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(node_color)
        painter.drawEllipse(node, 2.5, 2.5)


class _EditorialSplitSection(QWidget):
    def __init__(
        self,
        *,
        tier: ModeLauncherTier,
        title: _EditorialCategoryTitle,
        cards: _LauncherCardGrid,
        accent_color: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tier = tier
        self._title = title
        self._cards = cards
        self._accent_color = accent_color
        self._stacked = False
        self.setObjectName("modeLauncherScienceSplit" if tier == "science" else "modeLauncherExploreSplit")
        self._split_host = QWidget(self)
        self._stack_host = QWidget(self)
        self._split_layout = QHBoxLayout(self._split_host)
        self._split_layout.setContentsMargins(0, 0, 0, 0)
        self._split_layout.setSpacing(_EDITORIAL_SPLIT_GAP)
        self._stack_layout = QVBoxLayout(self._stack_host)
        self._stack_layout.setContentsMargins(0, 0, 0, 0)
        self._stack_layout.setSpacing(16)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)
        self._outer.addWidget(self._split_host)
        self._reflow_layout(force=True)

    def set_accent_color(self, accent_color: str) -> None:
        self._accent_color = accent_color
        self.update()

    def set_ambient_phase(self, phase: float) -> None:
        self._title.set_ambient_phase(phase)

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._reflow_layout()
        super().resizeEvent(event)

    def _reflow_layout(self, *, force: bool = False) -> None:
        width = self.width()
        stacked = width > 120 and width < _STACK_LAYOUT_BREAKPOINT
        if not force and stacked == self._stacked:
            return
        self._stacked = stacked
        while self._split_layout.count():
            item = self._split_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(self)
        while self._stack_layout.count():
            item = self._stack_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(self)
        if stacked:
            self._title.setFixedWidth(self._title._column_width)  # noqa: SLF001
            self._stack_layout.addWidget(self._title)
            self._stack_layout.addWidget(self._cards)
            self._outer.removeWidget(self._split_host)
            self._split_host.hide()
            if self._outer.indexOf(self._stack_host) < 0:
                self._outer.addWidget(self._stack_host)
            self._stack_host.show()
        else:
            self._title.setFixedWidth(self._title._column_width)  # noqa: SLF001
            self._split_layout.addWidget(self._title, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            self._split_layout.addWidget(self._cards, 1)
            self._outer.removeWidget(self._stack_host)
            self._stack_host.hide()
            if self._outer.indexOf(self._split_host) < 0:
                self._outer.addWidget(self._split_host)
            self._split_host.show()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cards_rect = QRectF(self._cards.geometry())
        if cards_rect.width() <= 1.0:
            return
        if self._tier == "science":
            glow = QRadialGradient(cards_rect.center(), max(cards_rect.width(), cards_rect.height()) * 0.58)
            accent = _science_editorial_accent(self._accent_color)
            glow.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), 28))
            glow.setColorAt(0.55, QColor(accent.red(), accent.green(), accent.blue(), 10))
            glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        else:
            glow = QRadialGradient(cards_rect.center(), max(cards_rect.width(), cards_rect.height()) * 0.52)
            glow.setColorAt(0.0, QColor(120, 150, 190, 16))
            glow.setColorAt(0.6, QColor(80, 110, 150, 6))
            glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(cards_rect.adjusted(-20.0, -10.0, 20.0, 10.0), glow)


class ModeLauncherWidget(QWidget):
    mode_selected = Signal(AppMode)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("modeLauncher")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._accent_color = "#3d8bfd"
        self._cards_by_mode: dict[AppMode, ModeLauncherCard] = {}
        self._content_column: QWidget | None = None
        self._scroll_area: QScrollArea | None = None
        self._align_root: QWidget | None = None
        self._ambient_phase = 0.0
        self._ambient_timer = QTimer(self)
        self._ambient_timer.setInterval(40)
        self._ambient_timer.timeout.connect(self._advance_ambient)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        scroll = QScrollArea(self)
        self._scroll_area = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        outer_layout.addWidget(scroll, 1)

        align_root = QWidget()
        self._align_root = align_root
        align_root.setObjectName("modeLauncherAlignRoot")
        align_root.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        align_root.setStyleSheet("QWidget#modeLauncherAlignRoot { background: transparent; }")
        scroll.setWidget(align_root)

        align_layout = QVBoxLayout(align_root)
        align_layout.setContentsMargins(
            _CONTENT_MARGIN_LEFT,
            _CONTENT_MARGIN_TOP,
            _CONTENT_MARGIN_RIGHT,
            _CONTENT_MARGIN_BOTTOM,
        )
        align_layout.setSpacing(0)

        content_row = QHBoxLayout()
        content_row.setSpacing(0)

        column = QWidget()
        column.setObjectName("modeLauncherColumn")
        self._content_column = column
        column.setMinimumWidth(0)
        column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        column.setStyleSheet("QWidget#modeLauncherColumn { background: transparent; }")

        root_layout = QVBoxLayout(column)
        root_layout.setContentsMargins(0, 0, 4, 0)
        root_layout.setSpacing(0)

        identity = QLabel(APP_DISPLAY_NAME)
        identity.setObjectName("modeLauncherIdentity")
        identity.setAlignment(Qt.AlignmentFlag.AlignLeft)

        science_cards = self._build_cards(SCIENCE_WORKFLOW_ENTRIES)
        explore_cards = self._build_cards(EXPLORE_LEARN_ENTRIES)

        self._science_title = _EditorialCategoryTitle(
            ("SCIENCE", "WORKFLOWS"),
            tier="science",
            accent_color=self._accent_color,
            parent=column,
        )
        self._explore_title = _EditorialCategoryTitle(
            ("VISUALIZATIONS", "& TOOLS"),
            tier="explore",
            accent_color=self._accent_color,
            parent=column,
        )
        self._science_row = _LauncherCardGrid(science_cards, tier="science", parent=column)
        self._explore_row = _LauncherCardGrid(explore_cards, tier="explore", parent=column)

        self._science_section = _EditorialSplitSection(
            tier="science",
            title=self._science_title,
            cards=self._science_row,
            accent_color=self._accent_color,
            parent=column,
        )
        self._explore_section = _EditorialSplitSection(
            tier="explore",
            title=self._explore_title,
            cards=self._explore_row,
            accent_color=self._accent_color,
            parent=column,
        )

        root_layout.addWidget(identity)
        root_layout.addSpacing(12)
        root_layout.addWidget(self._science_section, 1)
        root_layout.addSpacing(_SECTION_VERTICAL_GAP)
        root_layout.addWidget(self._explore_section, 1)

        content_row.addWidget(column, 1)
        align_layout.addLayout(content_row, 1)

        self._editorial_titles = (self._science_title, self._explore_title)
        self._split_sections = (self._science_section, self._explore_section)
        self._apply_chrome()
        QTimer.singleShot(0, self._science_section._reflow_layout)  # noqa: SLF001
        QTimer.singleShot(0, self._explore_section._reflow_layout)  # noqa: SLF001
        QTimer.singleShot(0, self._fit_cards_to_viewport)

    def _build_cards(self, entries: tuple[ModeLauncherEntry, ...]) -> list[ModeLauncherCard]:
        cards: list[ModeLauncherCard] = []
        for entry in entries:
            card = ModeLauncherCard(entry, accent_color=self._accent_color, parent=self)
            card.clicked.connect(lambda checked=False, mode=entry.mode: self._emit_mode(mode))
            self._cards_by_mode[entry.mode] = card
            cards.append(card)
        return cards

    def set_accent_color(self, accent_color: str) -> None:
        normalized = str(accent_color).strip().lower()
        if normalized == self._accent_color:
            return
        self._accent_color = normalized
        for card in self._cards_by_mode.values():
            card._accent_color = QColor(normalized)  # noqa: SLF001
            card.update()
        for title in self._editorial_titles:
            title.set_accent_color(normalized)
        for section in self._split_sections:
            section.set_accent_color(normalized)
        self._apply_chrome()

    def clear_card_focus(self) -> None:
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        for card in self._cards_by_mode.values():
            card.clearFocus()

    def _background_focus(self) -> QPointF:
        rect = self.rect()
        return QPointF(rect.left() + rect.width() * 0.14, rect.top() + rect.height() * 0.84)

    def _card_background_regions(self) -> tuple[QRectF, ...]:
        regions: list[QRectF] = []
        for section in self._split_sections:
            cards = section._cards  # noqa: SLF001
            mapped = cards.mapTo(self, cards.rect().topLeft())
            regions.append(QRectF(mapped.x(), mapped.y(), cards.width(), cards.height()))
        return tuple(regions)

    def _advance_ambient(self) -> None:
        self._ambient_phase += 0.018
        for title in self._editorial_titles:
            title.set_ambient_phase(self._ambient_phase)
        for section in self._split_sections:
            section.set_ambient_phase(self._ambient_phase)
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        self._ambient_timer.start()
        QTimer.singleShot(0, self._fit_cards_to_viewport)
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._ambient_timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._fit_cards_to_viewport()
        self.update()
        super().resizeEvent(event)

    def _fit_cards_to_viewport(self) -> None:
        if self._scroll_area is None or self._align_root is None:
            return
        viewport_height = self._scroll_area.viewport().height()
        if viewport_height <= 0:
            return
        self._align_root.setMinimumHeight(viewport_height)

        science_title_height = self._science_title.sizeHint().height()
        explore_title_height = self._explore_title.sizeHint().height()
        science_width = max(1, self._science_row.width())
        explore_width = max(1, self._explore_row.width())
        science_columns = launcher_grid_column_count(
            science_width,
            tier="science",
            card_count=len(SCIENCE_WORKFLOW_ENTRIES),
        )
        explore_columns = launcher_grid_column_count(
            explore_width,
            tier="explore",
            card_count=len(EXPLORE_LEARN_ENTRIES),
        )
        science_rows = math.ceil(len(SCIENCE_WORKFLOW_ENTRIES) / science_columns)
        explore_rows = math.ceil(len(EXPLORE_LEARN_ENTRIES) / explore_columns)

        identity_label = self.findChild(QLabel, "modeLauncherIdentity")
        identity_height = identity_label.sizeHint().height() if identity_label is not None else 28
        fixed_overhead = _CONTENT_MARGIN_TOP + _CONTENT_MARGIN_BOTTOM + 12 + _SECTION_VERTICAL_GAP + identity_height

        def section_height(card_height: int, *, title_height: int, rows: int) -> int:
            grid_height = rows * card_height + max(0, rows - 1) * _CARD_GAP
            return max(title_height, grid_height)

        def total_height(card_height: int) -> int:
            return fixed_overhead + section_height(
                card_height,
                title_height=science_title_height,
                rows=science_rows,
            ) + section_height(
                card_height,
                title_height=explore_title_height,
                rows=explore_rows,
            )

        low = _CARD_HEIGHT_MIN
        row_count = max(1, science_rows + explore_rows)
        high = max(_SCIENCE_HEIGHT_LARGE, (viewport_height - fixed_overhead) // row_count)
        best_height = low
        while low <= high:
            candidate = (low + high) // 2
            if total_height(candidate) <= viewport_height:
                best_height = candidate
                low = candidate + 1
            else:
                high = candidate - 1

        density: LayoutDensity
        if best_height >= _SCIENCE_HEIGHT_MEDIUM:
            density = "large"
        elif best_height >= _SCIENCE_HEIGHT_COMPACT - 24:
            density = "medium"
        else:
            density = "compact"
        self._science_row.set_uniform_card_height(best_height, density=density)
        self._explore_row.set_uniform_card_height(best_height, density=density)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        _paint_launcher_background(
            painter,
            QRectF(self.rect()),
            focus=self._background_focus(),
            phase=self._ambient_phase,
            card_regions=self._card_background_regions(),
        )
        super().paintEvent(event)

    def _emit_mode(self, mode: AppMode) -> None:
        self.mode_selected.emit(mode)

    def _apply_chrome(self) -> None:
        self.setStyleSheet(
            "QWidget#modeLauncher { background: transparent; }"
            "QLabel#modeLauncherIdentity { color: rgba(196, 210, 228, 0.82); font-size: 14px; font-weight: 600; letter-spacing: 0.75px; }"
        )
