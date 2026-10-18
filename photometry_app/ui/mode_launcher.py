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
    QEvent,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImageReader,
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

from photometry_app.app_metadata import APP_DISPLAY_NAME, APP_VERSION, application_root_path
from photometry_app.core.models import AppMode

ModeLauncherTier = Literal["science", "explore"]

SCIENCE_WORKFLOW_LABEL = "Detect & Measure"
VISUALIZATIONS_TOOLS_LABEL = "Explore, Visualize & Process"
SIMULATIONS_LABEL = "Simulations"
EXPLORE_LEARN_LABEL = VISUALIZATIONS_TOOLS_LABEL
LAUNCHER_HEADER_SUBTITLE = "Measure light, detect moving objects, and explore the sky."

_CONTENT_MAX_WIDTH = 1400
_CONTENT_WIDTH_RATIO = 0.82
_PAGE_MARGIN_TOP = 20
_PAGE_MARGIN_BOTTOM = 20
_HEADER_SECTION_GAP = 16
_SECTION_HEADING_GAP = 10
_SECTION_VERTICAL_GAP = 20

_CARD_GAP = 16
_CARD_RADIUS = 6.0
_CARD_INSET = 1.0
_HOVER_ANIMATION_MS = 180
_INFO_PAD_X = 16
_INFO_PAD_TOP = 13
_INFO_PAD_BOTTOM = 14
_TITLE_GAP = 6
_CHEVRON_SLOT = 18

_SCIENCE_MIN_CARD = 240
_EXPLORE_MIN_CARD = 172
_SCIENCE_ASPECT = 16.0 / 9.0
_EXPLORE_ASPECT = 3.0 / 2.0
_SCIENCE_IMAGE_MAX = 420
_EXPLORE_IMAGE_MAX = 280
_SCIENCE_IMAGE_MIN = 108
_EXPLORE_IMAGE_MIN = 84

_PREVIEW_MAX_EDGE = 1280
_DEFAULT_THEME_ACCENT = "#3d8bfd"

_COLOR_BG_TOP = QColor("#07090d")
_COLOR_BG_MID = QColor("#0c1016")
_COLOR_BG_BOTTOM = QColor("#05060a")
_COLOR_CARD = QColor("#151920")
_COLOR_CARD_HOVER = QColor("#1c222c")
_COLOR_TEXT = QColor("#f3f5f8")
_COLOR_TEXT_SECONDARY = QColor("#c4ced8")
_COLOR_TEXT_MUTED = QColor("#aeb8c4")
_COLOR_HEADING = QColor("#e6ebf2")
_COLOR_BORDER = QColor("#3a4150")
_COLOR_HAIRLINE = QColor(255, 255, 255, 22)
_COPPER = QColor("#c9956a")
_COPPER_BRIGHT = QColor("#e0b184")


def launcher_content_width(viewport_width: int) -> int:
    """Use most of the window, leaving balanced side margins."""

    host = max(0, int(viewport_width))
    if host <= 0:
        return 320
    target = int(round(host * _CONTENT_WIDTH_RATIO))
    side_margin = max(36, int(round(host * 0.075)))
    return max(320, min(target, host - side_margin * 2))


def simulation_grid_column_count(width: int, card_count: int) -> int:
    """Use the Explore card size so one Simulations card does not span the page."""

    return launcher_grid_column_count(width, tier="explore", card_count=max(int(card_count), 5))


def launcher_grid_column_count(width: int, *, tier: ModeLauncherTier, card_count: int = 3) -> int:
    """Choose a column count that keeps cards readable instead of squeezing them."""

    if card_count <= 1 or width <= 0:
        return 1
    if tier == "science":
        if card_count >= 3 and width >= _SCIENCE_MIN_CARD * 3 + _CARD_GAP * 2:
            return 3
        if card_count >= 2 and width >= _SCIENCE_MIN_CARD * 2 + _CARD_GAP:
            return min(2, card_count)
        return 1
    for columns, minimum in ((5, _EXPLORE_MIN_CARD), (3, _EXPLORE_MIN_CARD), (2, 168)):
        if card_count >= columns and width >= minimum * columns + _CARD_GAP * (columns - 1):
            return columns
    return 1


def image_band_height(width: int, *, tier: ModeLauncherTier) -> int:
    aspect = _SCIENCE_ASPECT if tier == "science" else _EXPLORE_ASPECT
    natural = width / aspect if aspect else float(width)
    cap = _SCIENCE_IMAGE_MAX if tier == "science" else _EXPLORE_IMAGE_MAX
    floor = _SCIENCE_IMAGE_MIN if tier == "science" else _EXPLORE_IMAGE_MIN
    return int(max(floor, min(cap, round(natural))))


def launcher_accent(theme_accent: str) -> QColor:
    """Copper accent, tinted by a custom theme color when one is set."""

    theme = QColor(str(theme_accent).strip())
    if not theme.isValid() or theme.name().lower() == _DEFAULT_THEME_ACCENT:
        return QColor(_COPPER)
    return QColor(
        int(_COPPER.red() * 0.72 + theme.red() * 0.28),
        int(_COPPER.green() * 0.72 + theme.green() * 0.28),
        int(_COPPER.blue() * 0.72 + theme.blue() * 0.28),
    )


def _build_background_star_field(count: int = 112) -> tuple[tuple[float, float, float, int], ...]:
    rng = random.Random(90817)
    stars: list[tuple[float, float, float, int]] = []
    for _ in range(count):
        stars.append(
            (
                rng.uniform(0.01, 0.99),
                rng.uniform(0.01, 0.99),
                rng.uniform(0.7, 1.7),
                rng.randint(48, 128),
            )
        )
    return tuple(stars)


_BACKGROUND_STARS: tuple[tuple[float, float, float, int], ...] = _build_background_star_field()


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
    detail: str = ""
    crop_focus: tuple[float, float] = (0.5, 0.5)


SCIENCE_WORKFLOW_ENTRIES: tuple[ModeLauncherEntry, ...] = (
    ModeLauncherEntry(
        AppMode.DIFFERENTIAL_PHOTOMETRY,
        "Differential Photometry",
        "Measure light curves of variables, exoplanets, and eclipsing binaries.",
        "science",
        image_names=("differential_photometry.jpg", "differential_photometry.png", "differential_photometry.webp"),
        gradient_top="#1a2744",
        gradient_bottom="#0d1528",
        detail="Compare a target star with nearby reference stars and build a light curve from your images.",
    ),
    ModeLauncherEntry(
        AppMode.ASTEROID_COMET_DETECTION,
        "Asteroid / Comet Detection",
        "Detect moving objects and inspect their trajectories in 3D.",
        "science",
        image_names=("asteroid.gif", "asteroid_comet_detection.png", "asteroid_comet_detection.jpg"),
        gradient_top="#1f2430",
        gradient_bottom="#10141d",
        detail="Find objects that move between frames and inspect the measured track in 3D.",
    ),
    ModeLauncherEntry(
        AppMode.TRANSIENT_FINDER,
        "Transient Finder",
        "Search for new or fading transients, including supernovae.",
        "science",
        image_names=("Transient.gif", "transient.gif", "transient_finder.png", "transient_finder.jpg"),
        gradient_top="#241a2e",
        gradient_bottom="#100a14",
        detail="Compare a sequence for sources that appear or fade, including supernova candidates.",
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
        detail="Pan a full-sky chart of stars, nebulae, and deep-sky catalogs.",
    ),
    ModeLauncherEntry(
        AppMode.SKY_EXPLORER,
        "Sky Explorer",
        "Identify objects and create customizable sky annotations.",
        "explore",
        image_names=("Sky_Explorer.png", "sky_explorer.png", "sky_explorer.jpg", "sky_explorer.webp"),
        gradient_top="#30182a",
        gradient_bottom="#140b12",
        detail="Identify objects on a plate-solved frame and draw annotations you can export.",
    ),
    ModeLauncherEntry(
        AppMode.HR_DIAGRAM,
        "HR Diagram",
        "Explore stellar color, luminosity, clusters, and evolution.",
        "explore",
        image_names=("hr_diagram.png", "hr_diagram.jpg", "hr_diagram.webp"),
        gradient_top="#172238",
        gradient_bottom="#0b111c",
        detail="Compare stellar color and luminosity to see the main sequence, clusters, and giants.",
    ),
    ModeLauncherEntry(
        AppMode.DISTANCE_MAP,
        "Distance Map",
        "Map catalog stars in 3D using their measured distances.",
        "explore",
        image_names=("distance_map.png", "distance_map.jpg", "distance_map.webp"),
        gradient_top="#142238",
        gradient_bottom="#08111f",
        detail="Place catalog stars in three dimensions using published distances.",
        crop_focus=(0.5, 0.36),
    ),
    ModeLauncherEntry(
        AppMode.ASTROSTACK,
        "Deep Stack",
        "Stack, align, and export cumulative deep-sky animations from solved frames.",
        "explore",
        image_names=("astrostack.gif", "deep_stack.png", "deep_stack.jpg", "astrostack.png", "astrostack.jpg", "astrostack.webp"),
        gradient_top="#1a2438",
        gradient_bottom="#0a1018",
        detail="Align solved frames, combine them into a stack, and export the cumulative result as an animation.",
        crop_focus=(0.5, 0.62),
    ),
)

SIMULATION_ENTRIES: tuple[ModeLauncherEntry, ...] = (
    ModeLauncherEntry(
        AppMode.BLACK_HOLE,
        "Black Hole",
        "Trace light around a spinning or nonspinning black hole.",
        "explore",
        image_names=("black_hole.jpg", "black_hole.png", "black_hole.webp"),
        gradient_top="#2a160f",
        gradient_bottom="#100804",
        detail="Orbit a Kerr or Schwarzschild black hole, watch its gas disk evolve against CAst's lensed Milky Way, and export movies.",
    ),
    ModeLauncherEntry(
        AppMode.GALAXY_MERGER,
        "Galaxy Merger",
        "Collide two disk galaxies with gravity, gas, and new stars.",
        "explore",
        image_names=("galaxy_merger.jpg", "galaxy_merger.png", "galaxy_merger.webp"),
        gradient_top="#1c1830",
        gradient_bottom="#090812",
        detail="Run an N-body encounter of two galaxies, scrub through the recording, and view it as starlight and dust, a density volume, or particles.",
    ),
    ModeLauncherEntry(
        AppMode.CLUSTER_FLOW,
        "Cluster Flow",
        "Evolve gas filaments in a galaxy cluster or an expanding universe.",
        "explore",
        image_names=("cluster_flow.jpg", "cluster_flow.png", "cluster_flow.webp"),
        gradient_top="#0d2230",
        gradient_bottom="#050c12",
        detail="Watch gas fall along filaments, heat in outbursts, or grow from cosmological fluctuations, then bake a recording and export an MP4.",
    ),
)

MODE_LAUNCHER_ENTRIES: tuple[ModeLauncherEntry, ...] = SCIENCE_WORKFLOW_ENTRIES + EXPLORE_LEARN_ENTRIES + SIMULATION_ENTRIES


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


def _bounded_image_size(size: QSize, max_edge: int) -> QSize:
    if not size.isValid() or max(size.width(), size.height()) <= max_edge:
        return size
    bounded = QSize(size)
    bounded.scale(max_edge, max_edge, Qt.AspectRatioMode.KeepAspectRatio)
    return bounded


def _load_preview_master(path: Path, *, max_edge: int = _PREVIEW_MAX_EDGE) -> QPixmap:
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    scaled = _bounded_image_size(reader.size(), max_edge)
    if scaled.isValid() and scaled != reader.size():
        reader.setScaledSize(scaled)
    image = reader.read()
    if image.isNull():
        return QPixmap()
    return QPixmap.fromImage(image)


def _cover_source_rect(source: QPixmap, target_w: int, target_h: int, focus: tuple[float, float]) -> QRectF:
    sw = float(source.width())
    sh = float(source.height())
    if sw <= 0.0 or sh <= 0.0 or target_w <= 0 or target_h <= 0:
        return QRectF()
    target_aspect = target_w / float(target_h)
    source_aspect = sw / sh
    if source_aspect > target_aspect:
        crop_h = sh
        crop_w = sh * target_aspect
    else:
        crop_w = sw
        crop_h = sw / target_aspect
    focus_x = min(1.0, max(0.0, float(focus[0])))
    focus_y = min(1.0, max(0.0, float(focus[1])))
    left = min(max(0.0, focus_x * sw - crop_w / 2.0), max(0.0, sw - crop_w))
    top = min(max(0.0, focus_y * sh - crop_h / 2.0), max(0.0, sh - crop_h))
    return QRectF(left, top, crop_w, crop_h)


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


def _mix_color(start: QColor, end: QColor, amount: float) -> QColor:
    blend = max(0.0, min(1.0, amount))
    return QColor(
        int(start.red() + (end.red() - start.red()) * blend),
        int(start.green() + (end.green() - start.green()) * blend),
        int(start.blue() + (end.blue() - start.blue()) * blend),
        int(start.alpha() + (end.alpha() - start.alpha()) * blend),
    )


def _paint_corner_orbits(painter: QPainter, rect: QRectF) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    focus = QPointF(rect.left() + rect.width() * 0.04, rect.bottom() - rect.height() * 0.02)
    radii = (rect.width() * 0.16, rect.width() * 0.26, rect.width() * 0.37)
    for index, radius in enumerate(radii):
        pen = QPen(QColor(168, 186, 206, 64 - index * 12))
        pen.setWidthF(1.15)
        painter.setPen(pen)
        arc = QRectF(focus.x() - radius, focus.y() - radius * 0.38, radius * 2.0, radius * 0.76)
        painter.drawArc(arc, 18 * 16, 68 * 16)

    glow = QRadialGradient(focus, 86.0)
    glow.setColorAt(0.0, QColor(201, 149, 106, 34))
    glow.setColorAt(1.0, QColor(0, 0, 0, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(glow)
    painter.drawEllipse(focus, 72.0, 36.0)

    body_center = QPointF(focus.x() + radii[1] * 0.62, focus.y() - radii[1] * 0.16)
    painter.setBrush(QColor(196, 208, 222, 78))
    painter.drawEllipse(body_center, 6.5, 6.5)

    far = QPointF(rect.right() - rect.width() * 0.02, rect.top() + rect.height() * 0.08)
    far_radius = rect.width() * 0.22
    pen = QPen(QColor(168, 186, 206, 46))
    pen.setWidthF(1.0)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    far_arc = QRectF(far.x() - far_radius, far.y() - far_radius * 0.36, far_radius * 2.0, far_radius * 0.72)
    painter.drawArc(far_arc, 200 * 16, 62 * 16)
    painter.restore()


def _paint_launcher_background(painter: QPainter, rect: QRectF) -> None:
    base = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    base.setColorAt(0.0, _COLOR_BG_TOP)
    base.setColorAt(0.55, _COLOR_BG_MID)
    base.setColorAt(1.0, _COLOR_BG_BOTTOM)
    painter.fillRect(rect, base)

    _paint_corner_orbits(painter, rect)

    painter.setPen(Qt.PenStyle.NoPen)
    for x_frac, y_frac, size, alpha in _BACKGROUND_STARS:
        color = QColor(214, 224, 236, alpha)
        painter.setBrush(color)
        center = QPointF(rect.left() + rect.width() * x_frac, rect.top() + rect.height() * y_frac)
        painter.drawEllipse(center, size, size)

    vignette = QRadialGradient(rect.center(), max(rect.width(), rect.height()) * 0.72)
    vignette.setColorAt(0.0, QColor(0, 0, 0, 0))
    vignette.setColorAt(0.72, QColor(0, 0, 0, 18))
    vignette.setColorAt(1.0, QColor(0, 0, 0, 72))
    painter.fillRect(rect, vignette)


def _column_widths(total: int, columns: int) -> list[int]:
    if columns <= 0:
        return []
    inner = max(columns, total - _CARD_GAP * (columns - 1))
    base = inner // columns
    extra = inner - base * columns
    return [base + (1 if index < extra else 0) for index in range(columns)]


def _px_font(base: QFont, pixel_size: int, weight: QFont.Weight) -> QFont:
    font = QFont(base)
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.0)
    return font


class _AccentBar(QWidget):
    def __init__(self, color: QColor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self.setFixedSize(3, 16)

    def set_color(self, color: QColor) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawRoundedRect(QRectF(self.rect()), 1.0, 1.0)


class _SectionHeading(QWidget):
    def __init__(self, text: str, object_name: str, accent: QColor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(object_name + "Row")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self._bar = _AccentBar(accent, self)
        self._label = QLabel(text, self)
        self._label.setObjectName(object_name)
        layout.addWidget(self._bar, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch(1)

    def set_accent(self, accent: QColor) -> None:
        self._bar.set_color(accent)

    def set_font(self, font: QFont) -> None:  # noqa: A003
        self._label.setFont(font)
        super().setFont(font)


class ModeLauncherCard(QPushButton):
    def __init__(
        self,
        entry: ModeLauncherEntry,
        *,
        accent_color: str = _DEFAULT_THEME_ACCENT,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._entry = entry
        self._accent = launcher_accent(accent_color)
        self._hover_progress = 0.0
        self._pressed = False
        self._image_height = image_band_height(360 if entry.tier == "science" else 240, tier=entry.tier)
        self._footer_height = 76
        self._fast_cover = False
        self._cover = QPixmap()
        self._cover_key: tuple[int, int, int, int] | None = None
        self._animations_enabled = False
        self._hover_animation = QPropertyAnimation(self, b"hoverProgress", self)
        self._hover_animation.setDuration(_HOVER_ANIMATION_MS)
        self._hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._refine_timer = QTimer(self)
        self._refine_timer.setSingleShot(True)
        self._refine_timer.setInterval(110)
        self._refine_timer.timeout.connect(self._refine_cover)
        self._image_path = _resolve_mode_launcher_image(entry.image_names)
        self._master = QPixmap()
        self._movie: QMovie | None = None
        if self._image_path is not None:
            self._master = _load_preview_master(self._image_path)
            if self._image_path.suffix.casefold() == ".gif":
                movie = QMovie(str(self._image_path), parent=self)
                movie.setCacheMode(QMovie.CacheMode.CacheNone)
                if not self._master.isNull():
                    movie.setScaledSize(self._master.size())
                movie.frameChanged.connect(self._on_movie_frame)
                self._movie = movie
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAutoDefault(False)
        self.setDefault(False)
        self.setFlat(True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("QPushButton { background: transparent; border: none; padding: 0px; margin: 0px; }")
        self.setAccessibleName(entry.title)
        description = entry.detail or entry.subtitle
        self.setAccessibleDescription(description)
        if entry.detail:
            self.setToolTip(entry.detail)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def get_hover_progress(self) -> float:
        return self._hover_progress

    def set_hover_progress(self, value: float) -> None:
        self._hover_progress = max(0.0, min(1.0, float(value)))
        self.update()

    hoverProgress = Property(float, get_hover_progress, set_hover_progress)

    @property
    def entry(self) -> ModeLauncherEntry:
        return self._entry

    def image_band_height(self) -> int:
        return self._image_height

    def footer_band_height(self) -> int:
        return self._footer_height

    def set_accent_color(self, accent_color: str) -> None:
        self._accent = launcher_accent(accent_color)
        self.update()

    def set_animations_enabled(self, enabled: bool) -> None:
        self._animations_enabled = enabled
        if self._movie is None:
            return
        if enabled:
            if self._movie.state() != QMovie.MovieState.Running:
                self._movie.start()
        elif self._movie.state() == QMovie.MovieState.Running:
            self._movie.stop()

    def set_band_heights(self, *, image_height: int, footer_height: int) -> None:
        image_height = max(1, int(image_height))
        footer_height = max(1, int(footer_height))
        if image_height == self._image_height and footer_height == self._footer_height:
            return
        self._image_height = image_height
        self._footer_height = footer_height
        self._cover_key = None
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        if event.size() != event.oldSize():
            self._fast_cover = True
            self._cover_key = None
            self._refine_timer.start()
        super().resizeEvent(event)

    def footer_height_for(self, width: int) -> int:
        text_width = max(48, int(width) - 2 * int(math.ceil(_CARD_INSET)) - 2 * _INFO_PAD_X)
        title_width = max(40, text_width - _CHEVRON_SLOT)
        title_rect = QFontMetrics(self._title_font()).boundingRect(
            0,
            0,
            title_width,
            10_000,
            int(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap),
            self._entry.title,
        )
        body_rect = QFontMetrics(self._body_font()).boundingRect(
            0,
            0,
            text_width,
            10_000,
            int(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap),
            self._entry.subtitle,
        )
        return _INFO_PAD_TOP + title_rect.height() + _TITLE_GAP + body_rect.height() + _INFO_PAD_BOTTOM + 2

    def _title_font(self) -> QFont:
        size = 18 if self._entry.tier == "science" else 16
        return _px_font(self.font(), size, QFont.Weight.DemiBold)

    def _body_font(self) -> QFont:
        return _px_font(self.font(), 13, QFont.Weight.Medium)

    def _animate_hover(self, target: float) -> None:
        self._hover_animation.stop()
        self._hover_animation.setStartValue(self._hover_progress)
        self._hover_animation.setEndValue(target)
        self._hover_animation.start()

    def _current_source(self) -> QPixmap:
        if self._movie is not None and self._animations_enabled:
            frame = self._movie.currentPixmap()
            if not frame.isNull():
                return frame
        return self._master

    def _on_movie_frame(self, _frame: int) -> None:
        self._cover_key = None
        self.update()

    def _refine_cover(self) -> None:
        self._fast_cover = False
        self._cover_key = None
        self.update()

    def _image_pixel_size(self) -> tuple[int, int, float]:
        rect = self._image_rect()
        dpr = max(1.0, float(self.devicePixelRatioF()))
        return max(1, round(rect.width() * dpr)), max(1, round(rect.height() * dpr)), dpr

    def _ensure_cover(self) -> QPixmap:
        source = self._current_source()
        if source.isNull():
            return QPixmap()
        target_w, target_h, dpr = self._image_pixel_size()
        frame_id = self._movie.currentFrameNumber() if self._movie is not None and self._animations_enabled else 0
        key = (frame_id, target_w, target_h, 1 if self._fast_cover else 0)
        if key == self._cover_key and not self._cover.isNull():
            return self._cover
        cover = QPixmap(target_w, target_h)
        cover.fill(_COLOR_CARD)
        painter = QPainter(cover)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, not self._fast_cover)
        source_rect = _cover_source_rect(source, target_w, target_h, self._entry.crop_focus)
        painter.drawPixmap(QRectF(0, 0, float(target_w), float(target_h)), source, source_rect)
        painter.end()
        cover.setDevicePixelRatio(dpr)
        self._cover = cover
        self._cover_key = key
        return cover

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

    def focusInEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().focusOutEvent(event)

    def _content_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(_CARD_INSET, _CARD_INSET, -_CARD_INSET, -_CARD_INSET)

    def _image_rect(self) -> QRectF:
        content = self._content_rect()
        return QRectF(content.left(), content.top(), content.width(), min(self._image_height, content.height()))

    def _footer_rect(self) -> QRectF:
        content = self._content_rect()
        image = self._image_rect()
        top = image.bottom()
        return QRectF(content.left(), top, content.width(), max(0.0, content.bottom() - top))

    def _paint_chevron(self, painter: QPainter, origin: QPointF, color: QColor) -> None:
        shift = 3.0 * self._hover_progress
        x = origin.x() + shift
        y = origin.y()
        path = QPainterPath()
        path.moveTo(x, y - 4.0)
        path.lineTo(x + 5.0, y)
        path.lineTo(x, y + 4.0)
        pen = QPen(color, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        hover = self._hover_progress
        focused = self.hasFocus()
        content_rect = self._content_rect()
        if content_rect.width() <= 2.0 or content_rect.height() <= 2.0:
            painter.end()
            return
        card_path = QPainterPath()
        card_path.addRoundedRect(content_rect, _CARD_RADIUS, _CARD_RADIUS)
        image_rect = self._image_rect()
        footer_rect = self._footer_rect()

        painter.setClipPath(card_path)
        cover = self._ensure_cover()
        if not cover.isNull():
            painter.drawPixmap(image_rect, cover, QRectF(0, 0, cover.width(), cover.height()))
            fade = QLinearGradient(QPointF(image_rect.left(), image_rect.bottom() - 12.0), QPointF(image_rect.left(), image_rect.bottom()))
            fade.setColorAt(0.0, QColor(_COLOR_CARD.red(), _COLOR_CARD.green(), _COLOR_CARD.blue(), 0))
            fade.setColorAt(1.0, QColor(_COLOR_CARD.red(), _COLOR_CARD.green(), _COLOR_CARD.blue(), 46))
            painter.fillRect(QRectF(image_rect.left(), image_rect.bottom() - 12.0, image_rect.width(), 12.0), fade)
        else:
            gradient = QLinearGradient(image_rect.topLeft(), image_rect.bottomRight())
            gradient.setColorAt(0.0, QColor(self._entry.gradient_top))
            gradient.setColorAt(1.0, QColor(self._entry.gradient_bottom))
            painter.fillRect(image_rect, gradient)

        footer_color = _mix_color(_COLOR_CARD, _COLOR_CARD_HOVER, hover)
        painter.fillRect(footer_rect, footer_color)
        painter.setPen(QPen(_COLOR_HAIRLINE, 1.0))
        painter.drawLine(QPointF(footer_rect.left(), footer_rect.top()), QPointF(footer_rect.right(), footer_rect.top()))

        text_left = footer_rect.left() + _INFO_PAD_X
        text_width = max(40.0, footer_rect.width() - _INFO_PAD_X * 2)
        title_width = max(32.0, text_width - _CHEVRON_SLOT)
        title_font = self._title_font()
        body_font = self._body_font()
        title_metrics = QFontMetrics(title_font)
        title_bound = title_metrics.boundingRect(
            0,
            0,
            int(title_width),
            10_000,
            int(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap),
            self._entry.title,
        )
        title_rect = QRectF(text_left, footer_rect.top() + _INFO_PAD_TOP, title_width, title_bound.height())
        body_top = title_rect.bottom() + _TITLE_GAP
        body_rect = QRectF(text_left, body_top, text_width, max(0.0, footer_rect.bottom() - _INFO_PAD_BOTTOM - body_top))
        painter.setPen(_COLOR_TEXT)
        painter.setFont(title_font)
        painter.drawText(
            title_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
            self._entry.title,
        )
        painter.setPen(_COLOR_TEXT_SECONDARY)
        painter.setFont(body_font)
        painter.drawText(
            body_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
            self._entry.subtitle,
        )
        chevron_color = _mix_color(QColor(_COPPER.red(), _COPPER.green(), _COPPER.blue(), 150), self._accent, max(hover, 1.0 if focused else 0.0))
        self._paint_chevron(
            painter,
            QPointF(footer_rect.right() - _INFO_PAD_X - 6.0, title_rect.center().y()),
            chevron_color,
        )

        if self._pressed:
            painter.fillPath(card_path, QColor(0, 0, 0, 28))
        painter.setClipping(False)

        if focused:
            border_color = self._accent
            border_width = 2.0
        else:
            border_color = _mix_color(_COLOR_BORDER, self._accent, hover)
            border_width = 1.0 + 0.35 * hover
        border_pen = QPen(border_color, border_width)
        border_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(border_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(card_path)
        painter.end()


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
        self._columns = 0
        self._applying_sizes = False
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(_CARD_GAP)
        self._grid.setVerticalSpacing(_CARD_GAP)
        self._orphan_host: QWidget | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)

    def sync_to_width(self, width: int, *, image_height: int | None = None, column_count: int | None = None) -> None:
        width = max(1, int(width))
        columns = column_count if column_count is not None else launcher_grid_column_count(width, tier=self._tier, card_count=len(self._cards))
        if columns != self._columns:
            self._rebuild(columns)
        self._apply_sizes(width, columns, image_height=image_height)

    def _clear_grid(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(self)
        self._orphan_host = None

    def _rebuild(self, columns: int) -> None:
        self._clear_grid()
        self._columns = columns
        count = len(self._cards)
        if count == 3 and columns == 2:
            self._grid.addWidget(self._cards[0], 0, 0)
            self._grid.addWidget(self._cards[1], 0, 1)
            self._orphan_host = QWidget(self)
            orphan_layout = QHBoxLayout(self._orphan_host)
            orphan_layout.setContentsMargins(0, 0, 0, 0)
            orphan_layout.addStretch(1)
            orphan_layout.addWidget(self._cards[2], 0, Qt.AlignmentFlag.AlignHCenter)
            orphan_layout.addStretch(1)
            self._grid.addWidget(self._orphan_host, 1, 0, 1, 2)
            return
        for index, card in enumerate(self._cards):
            self._grid.addWidget(card, index // columns, index % columns)
        for column in range(columns):
            self._grid.setColumnStretch(column, 1 if column == columns - 1 and len(self._cards) < columns else 0)
        for card in self._cards:
            self._grid.setAlignment(card, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

    def _apply_sizes(self, width: int, columns: int, *, image_height: int | None = None) -> None:
        widths = _column_widths(width, columns)
        if not widths:
            return
        card_width = widths[0]
        footer_height = max(card.footer_height_for(card_width) for card in self._cards)
        if image_height is None:
            image_height = image_band_height(card_width, tier=self._tier)
        card_height = image_height + footer_height + 2 * int(math.ceil(_CARD_INSET))
        self._applying_sizes = True
        try:
            for card in self._cards:
                card.set_band_heights(image_height=image_height, footer_height=footer_height)
                card.setFixedSize(card_width, card_height)
            rows = math.ceil(len(self._cards) / columns)
            if len(self._cards) == 3 and columns == 2:
                rows = 2
            total_height = rows * card_height + max(0, rows - 1) * _CARD_GAP
            self.setFixedHeight(total_height)
        finally:
            self._applying_sizes = False


class ModeLauncherWidget(QWidget):
    mode_selected = Signal(AppMode)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("modeLauncher")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._accent_color = _DEFAULT_THEME_ACCENT
        self._accent = launcher_accent(self._accent_color)
        self._cards_by_mode: dict[AppMode, ModeLauncherCard] = {}
        self._scroll_area: QScrollArea | None = None
        self._align_root: QWidget | None = None
        self._align_layout: QVBoxLayout | None = None
        self._header: QWidget | None = None
        self._content_column: QWidget | None = None
        self._syncing_width = False
        self._background = QPixmap()
        self._background_key: tuple[int, int, int] | None = None

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        scroll = QScrollArea(self)
        self._scroll_area = scroll
        scroll.setObjectName("modeLauncherScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.viewport().setAutoFillBackground(False)
        scroll.viewport().installEventFilter(self)
        outer_layout.addWidget(scroll, 1)

        align_root = QWidget()
        self._align_root = align_root
        align_root.setObjectName("modeLauncherAlignRoot")
        align_root.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        scroll.setWidget(align_root)

        align_layout = QVBoxLayout(align_root)
        self._align_layout = align_layout
        align_layout.setContentsMargins(0, _PAGE_MARGIN_TOP, 0, _PAGE_MARGIN_BOTTOM)
        align_layout.setSpacing(0)
        align_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

        column = QWidget(align_root)
        column.setObjectName("modeLauncherColumn")
        self._content_column = column
        column.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(0)

        header = QWidget(column)
        self._header = header
        header.setObjectName("modeLauncherHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(16)
        title = QLabel(APP_DISPLAY_NAME, header)
        title.setObjectName("modeLauncherHeaderTitle")
        title.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        version = QLabel(APP_VERSION, header)
        version.setObjectName("modeLauncherVersion")
        version.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        title_row.addWidget(title, 0, Qt.AlignmentFlag.AlignBaseline)
        title_row.addStretch(1)
        title_row.addWidget(version, 0, Qt.AlignmentFlag.AlignBaseline)
        subtitle = QLabel(LAUNCHER_HEADER_SUBTITLE, header)
        subtitle.setObjectName("modeLauncherHeaderSubtitle")
        subtitle.setWordWrap(True)
        subtitle.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        header_layout.addLayout(title_row)
        header_layout.addWidget(subtitle)

        science_cards = self._build_cards(SCIENCE_WORKFLOW_ENTRIES)
        explore_cards = self._build_cards(EXPLORE_LEARN_ENTRIES)
        simulation_cards = self._build_cards(SIMULATION_ENTRIES)
        self._science_heading = _SectionHeading(SCIENCE_WORKFLOW_LABEL, "modeLauncherSectionScience", self._accent, column)
        self._explore_heading = _SectionHeading(VISUALIZATIONS_TOOLS_LABEL, "modeLauncherSectionExplore", self._accent, column)
        self._simulation_heading = _SectionHeading(SIMULATIONS_LABEL, "modeLauncherSectionSimulations", self._accent, column)
        self._science_row = _LauncherCardGrid(science_cards, tier="science", parent=column)
        self._explore_row = _LauncherCardGrid(explore_cards, tier="explore", parent=column)
        self._simulation_row = _LauncherCardGrid(simulation_cards, tier="explore", parent=column)
        self._headings = (self._science_heading, self._explore_heading, self._simulation_heading)

        column_layout.addWidget(header)
        column_layout.addSpacing(_HEADER_SECTION_GAP)
        column_layout.addWidget(self._science_heading)
        column_layout.addSpacing(_SECTION_HEADING_GAP)
        column_layout.addWidget(self._science_row)
        column_layout.addSpacing(_SECTION_VERTICAL_GAP)
        column_layout.addWidget(self._explore_heading)
        column_layout.addSpacing(_SECTION_HEADING_GAP)
        column_layout.addWidget(self._explore_row)
        column_layout.addSpacing(_SECTION_VERTICAL_GAP)
        column_layout.addWidget(self._simulation_heading)
        column_layout.addSpacing(_SECTION_HEADING_GAP)
        column_layout.addWidget(self._simulation_row)

        align_layout.addWidget(column, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        align_layout.addStretch(1)

        self._apply_chrome()
        self._sync_layout()
        QTimer.singleShot(0, self._sync_layout)

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
        self._accent = launcher_accent(normalized)
        for card in self._cards_by_mode.values():
            card.set_accent_color(normalized)
        for heading in self._headings:
            heading.set_accent(self._accent)
        self._apply_chrome()

    def clear_card_focus(self) -> None:
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        for card in self._cards_by_mode.values():
            card.clearFocus()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if (
            self._scroll_area is not None
            and watched is self._scroll_area.viewport()
            and event.type() == QEvent.Type.Resize
        ):
            self._sync_layout()
        return super().eventFilter(watched, event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_layout)
        QTimer.singleShot(60, self._start_card_animations)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._set_card_animations(False)
        super().hideEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_layout()
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        background = self._ensure_background()
        if not background.isNull():
            painter.drawPixmap(0, 0, background)
        painter.end()

    def _host_size(self) -> tuple[int, int]:
        viewport = self._scroll_area.viewport() if self._scroll_area is not None else None
        viewport_width = viewport.width() if viewport is not None else 0
        viewport_height = viewport.height() if viewport is not None else 0
        # The scroll viewport can still be the old size when the window has already grown.
        width = max(self.width(), viewport_width)
        height = max(self.height(), viewport_height)
        return max(1, width), max(0, height)

    def _chrome_height(self) -> int:
        header_height = self._header.sizeHint().height() if self._header is not None else 48
        science_height = self._science_heading.sizeHint().height()
        explore_height = self._explore_heading.sizeHint().height()
        simulation_height = self._simulation_heading.sizeHint().height()
        return (
            header_height
            + science_height
            + explore_height
            + simulation_height
            + _HEADER_SECTION_GAP
            + _SECTION_HEADING_GAP * 3
            + _SECTION_VERTICAL_GAP * 2
            + _PAGE_MARGIN_TOP
            + _PAGE_MARGIN_BOTTOM
        )

    def _fitted_image_heights(
        self,
        *,
        science_width: int,
        explore_width: int,
        science_footer: int,
        explore_footer: int,
        simulation_footer: int,
        viewport_height: int,
    ) -> tuple[int, int]:
        science = image_band_height(science_width, tier="science")
        explore = image_band_height(explore_width, tier="explore")
        if viewport_height <= 0:
            return science, explore
        inset = 2 * int(math.ceil(_CARD_INSET))

        def occupied(science_image: int, explore_image: int) -> int:
            return (
                self._chrome_height()
                + science_image
                + science_footer
                + inset
                + explore_image
                + explore_footer
                + inset
                + explore_image
                + simulation_footer
                + inset
            )

        overflow = occupied(science, explore) - viewport_height
        if overflow > 0:
            science_floor = _SCIENCE_IMAGE_MIN
            explore_floor = _EXPLORE_IMAGE_MIN
            while overflow > 0 and explore > explore_floor:
                explore -= 1
                overflow -= 2
            while overflow > 0 and science > max(science_floor, explore + 48):
                science -= 1
                overflow -= 1
            while overflow > 0 and science > science_floor:
                science -= 1
                overflow -= 1
            if science < explore + 48 and explore > explore_floor:
                needed = explore + 48 - science
                transfer = min(needed, (explore - explore_floor) * 2)
                science += transfer
                explore -= transfer // 2
            return science, explore
        extra = int(viewport_height * 0.82) - occupied(science, explore)
        if extra <= 12:
            return science, explore
        science_cap = max(science, int(science_width / 1.60))
        explore_cap = max(explore, int(explore_width / 1.35))
        science_room = max(0, science_cap - science)
        explore_room = max(0, explore_cap - explore)
        science_gain = min(science_room, int(round(extra * 0.58)))
        explore_gain = min(explore_room, max(0, extra - science_gain) // 2)
        return science + science_gain, explore + explore_gain

    def _sync_layout(self) -> None:
        if (
            self._syncing_width
            or self._content_column is None
            or self._scroll_area is None
            or self._align_root is None
            or self._align_layout is None
            or self._header is None
        ):
            return
        host_width, host_height = self._host_size()
        self._syncing_width = True
        try:
            column_width = launcher_content_width(host_width)
            if self._content_column.width() != column_width:
                self._content_column.setFixedWidth(column_width)
            science_columns = launcher_grid_column_count(
                column_width,
                tier="science",
                card_count=len(SCIENCE_WORKFLOW_ENTRIES),
            )
            explore_columns = launcher_grid_column_count(
                column_width,
                tier="explore",
                card_count=len(EXPLORE_LEARN_ENTRIES),
            )
            simulation_columns = simulation_grid_column_count(column_width, len(SIMULATION_ENTRIES))
            science_width = _column_widths(column_width, science_columns)[0]
            explore_width = _column_widths(column_width, explore_columns)[0]
            simulation_width = _column_widths(column_width, simulation_columns)[0]
            science_footer = max(card.footer_height_for(science_width) for card in self._science_row._cards)  # noqa: SLF001
            explore_footer = max(card.footer_height_for(explore_width) for card in self._explore_row._cards)  # noqa: SLF001
            simulation_footer = max(card.footer_height_for(simulation_width) for card in self._simulation_row._cards)  # noqa: SLF001
            science_image, explore_image = self._fitted_image_heights(
                science_width=science_width,
                explore_width=explore_width,
                science_footer=science_footer,
                explore_footer=explore_footer,
                simulation_footer=simulation_footer,
                viewport_height=host_height,
            )
            if host_height > 0:
                self._align_root.setMinimumHeight(host_height)
            self._science_row.sync_to_width(column_width, image_height=science_image)
            self._explore_row.sync_to_width(column_width, image_height=explore_image)
            self._simulation_row.sync_to_width(
                column_width,
                image_height=explore_image,
                column_count=simulation_columns,
            )
            content_height = (
                self._header.sizeHint().height()
                + self._science_heading.sizeHint().height()
                + self._explore_heading.sizeHint().height()
                + self._simulation_heading.sizeHint().height()
                + _HEADER_SECTION_GAP
                + _SECTION_HEADING_GAP * 3
                + _SECTION_VERTICAL_GAP * 2
                + self._science_row.height()
                + self._explore_row.height()
                + self._simulation_row.height()
            )
            slack = max(_PAGE_MARGIN_TOP + _PAGE_MARGIN_BOTTOM, host_height - content_height)
            top_margin = int(slack * 0.42)
            bottom_margin = slack - top_margin
            self._align_layout.setContentsMargins(0, top_margin, 0, bottom_margin)
        finally:
            self._syncing_width = False

    def _start_card_animations(self) -> None:
        if self.isVisible():
            self._set_card_animations(True)

    def _set_card_animations(self, enabled: bool) -> None:
        for card in self._cards_by_mode.values():
            card.set_animations_enabled(enabled)

    def _ensure_background(self) -> QPixmap:
        width = max(1, self.width())
        height = max(1, self.height())
        dpr = max(1.0, float(self.devicePixelRatioF()))
        key = (width, height, round(dpr * 100))
        if key == self._background_key and not self._background.isNull():
            return self._background
        pixmap = QPixmap(max(1, round(width * dpr)), max(1, round(height * dpr)))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(_COLOR_BG_TOP)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        _paint_launcher_background(painter, QRectF(0, 0, width, height))
        painter.end()
        self._background = pixmap
        self._background_key = key
        return pixmap

    def _emit_mode(self, mode: AppMode) -> None:
        self.mode_selected.emit(mode)

    def _apply_chrome(self) -> None:
        title_font = _px_font(self.font(), 26, QFont.Weight.DemiBold)
        subtitle_font = _px_font(self.font(), 13, QFont.Weight.Normal)
        version_font = _px_font(self.font(), 13, QFont.Weight.Medium)
        heading_font = _px_font(self.font(), 18, QFont.Weight.DemiBold)
        title = self.findChild(QLabel, "modeLauncherHeaderTitle")
        subtitle = self.findChild(QLabel, "modeLauncherHeaderSubtitle")
        version = self.findChild(QLabel, "modeLauncherVersion")
        if title is not None:
            title.setFont(title_font)
        if subtitle is not None:
            subtitle.setFont(subtitle_font)
        if version is not None:
            version.setFont(version_font)
        for heading in getattr(self, "_headings", ()):
            heading.set_font(heading_font)
        self.setStyleSheet(
            "QWidget#modeLauncher { background: transparent; }"
            "QWidget#modeLauncherAlignRoot, QWidget#modeLauncherColumn, QWidget#modeLauncherHeader { background: transparent; }"
            "QScrollArea#modeLauncherScroll, QScrollArea#modeLauncherScroll > QWidget > QWidget { background: transparent; border: none; }"
            "QLabel#modeLauncherHeaderTitle { color: #f3f5f8; background: transparent; }"
            "QLabel#modeLauncherHeaderSubtitle { color: #b7c2ce; background: transparent; }"
            "QLabel#modeLauncherVersion { color: #b7c2ce; background: transparent; }"
            "QLabel#modeLauncherSectionScience, QLabel#modeLauncherSectionExplore, QLabel#modeLauncherSectionSimulations { color: #e6ebf2; background: transparent; }"
        )
