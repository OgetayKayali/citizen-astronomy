from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import math
import re
import shutil
from pathlib import Path
from time import perf_counter
from typing import Callable, Mapping, Sequence
from zoneinfo import available_timezones

from astropy.io import fits
from matplotlib.collections import LineCollection
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
import numpy as np
from PySide6.QtCore import QEvent, QItemSelectionModel, QPoint, QRect, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QDoubleValidator, QFont, QImage, QPainter, QPalette, QPen, QVector3D
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QColorDialog,
    QDoubleSpinBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QFormLayout,
    QFontComboBox,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QMenu,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QSlider,
    QSplitter,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QStyle,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from photometry_app.core.animation_export import (
    StreamingGifWriter,
    StreamingMp4Writer,
    resolve_astrostack_stack_export_frame_indices,
)
from photometry_app.core.catalog_filters import VARIABLE_STAR_DESIGNATION_LABELS, classify_variable_star_designation
from photometry_app.core.calibration import CalibrationPipelineRequest
from photometry_app.core.discovery import MissedKnownMovingObject, MovingObjectCandidate, MovingObjectDiscoveryResult, MovingObjectRecoveryResult, RecoveredKnownMovingObject, _estimate_discovery_motion_range, candidate_discovery_method_label
from photometry_app.core.hr_motion_groups import HrMotionGroupSettings, hr_motion_group_preset_description
from photometry_app.core.models import CatalogStar, FileScanResult, ObjectScanSummary, ObservationMetadata, PhotometryApertureMode, VariableSelectionPreview, VariableStarDesignationFamily, VariableStarLimitMode, WcsStatus
from photometry_app.core.plotting import AnnotatedImageDisplay, AnnotatedImageRenderSettings
from photometry_app.core.sky_explorer import SKY_EXPLORER_LAYER_ORDER, sky_explorer_object_type_group_definitions
from photometry_app.core.settings import AppSettings, _coerce_hex_color, default_custom_theme_colors, default_settings_config_path, resolve_shared_parallel_workers, setup_pixel_scale_arcsec_per_pixel
from photometry_app.core.sky_atlas import SkyAtlasObject, load_local_sky_atlas_objects
from photometry_app.ui.constellation_overlay import ConstellationDataLoader
from photometry_app.core.solar_system import HeliocentricReferenceBody, KnownObjectComparisonTrack, KnownObjectHeliocentricContext, SolarSystemDetection, SolarSystemFrameMeasurement, load_cached_major_planet_heliocentric_paths, parse_observation_time
from photometry_app.core.synthetic_tracking import SyntheticTrackingResult, format_synthetic_tracking_summary, measure_synthetic_tracking_peak
from photometry_app.ui.image_view import AnnotatedImageView, ImageOverlay, MotionVectorOverlay
from photometry_app.ui.moving_object_label_dialog import MovingObjectQuickLabelDialog
from photometry_app.ui.workers import AsteroidOrbitContextTarget, AsteroidOrbitContextWorker

try:
    import pyqtgraph as pg
except Exception:
    pg = None

try:
    import pyqtgraph.opengl as gl
except Exception:
    gl = None


# Background stars sit on a sphere far outside the trajectory data so they always
# read as a distant backdrop, and wheel zoom is clamped so the camera can never
# leave that sphere (or dive uselessly close to the origin).
_KNOWN_OBJECT_3D_STARFIELD_MIN_RADIUS_AU = 50.0
_KNOWN_OBJECT_3D_STARFIELD_EXTENT_FACTOR = 40.0
_KNOWN_OBJECT_3D_MIN_CAMERA_DISTANCE_AU = 0.02
_KNOWN_OBJECT_3D_MAX_ZOOM_OUT_EXTENT_FACTOR = 14.0
# Soft-edge fade used when the span has no padded observation core (Custom / empty).
_KNOWN_OBJECT_3D_PATH_EDGE_FADE_FRACTION = 0.18
_KNOWN_OBJECT_3D_ECLIPTIC_OBLIQUITY_DEG = 23.4392911
_KNOWN_OBJECT_SKY_TRACK_MIN_FIELD_RADIUS_DEG = 6.0
_KNOWN_OBJECT_SKY_TRACK_MAX_FIELD_RADIUS_DEG = 180.0
_KNOWN_OBJECT_SKY_TRACK_STAR_PADDING_DEG = 12.0
_KNOWN_OBJECT_SKY_TRACK_ADAPTIVE_ERROR_DEG = 0.01
_KNOWN_OBJECT_SKY_TRACK_ADAPTIVE_MAX_DEPTH = 8
_KNOWN_OBJECT_SKY_TRACK_BASE_SUBDIVISIONS = 4
_KNOWN_OBJECT_SKY_TRACK_DENSITY_LIMITS = {
    "sparse": 1.2,
    "medium": 2.5,
    "dense": 6.0,
}
_KNOWN_OBJECT_SKY_TRACK_BAYER_LETTER_BY_GREEK = {
    "alpha": "a",
    "beta": "b",
    "gamma": "g",
    "delta": "d",
    "epsilon": "e",
    "zeta": "z",
    "eta": "n",
    "theta": "th",
    "iota": "i",
    "kappa": "k",
    "lambda": "l",
    "mu": "m",
    "nu": "nu",
    "xi": "x",
    "omicron": "o",
    "pi": "p",
    "rho": "r",
    "sigma": "s",
    "tau": "t",
    "upsilon": "u",
    "phi": "ph",
    "chi": "ch",
    "psi": "ps",
    "omega": "w",
}
_KNOWN_OBJECT_3D_PANEL_ORDER_DEFAULT = ("topdown", "sky_track", "magnitude", "distance")
_KNOWN_OBJECT_3D_PANEL_KEYS = ("topdown", "sky_track", "magnitude", "distance", "data")
_KNOWN_OBJECT_3D_PANEL_LABELS = {
    "topdown": "Heliocentric top-down",
    "sky_track": "Sky Track",
    "magnitude": "Magnitude",
    "distance": "Distance",
    "data": "Data",
}
_KNOWN_OBJECT_3D_PANEL_DEFAULT_SIZES = {
    "topdown": 240,
    "sky_track": 200,
    "magnitude": 180,
    "distance": 180,
    "data": 180,
}

if gl is not None:

    class _KnownObjectOrbitGLViewWidget(gl.GLViewWidget):
        """GLViewWidget that clamps wheel-zoom camera distance and keeps the far
        clip plane beyond the background starfield."""

        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self._camera_distance_minimum = _KNOWN_OBJECT_3D_MIN_CAMERA_DISTANCE_AU
            self._camera_distance_maximum = 1.0e9
            self._minimum_far_clip = 0.0

        def set_camera_distance_limits(self, minimum: float, maximum: float, *, minimum_far_clip: float) -> None:
            self._camera_distance_minimum = float(minimum)
            self._camera_distance_maximum = float(max(minimum, maximum))
            self._minimum_far_clip = float(minimum_far_clip)
            self._clamp_camera_distance()

        def _clamp_camera_distance(self) -> None:
            distance = float(self.opts.get("distance", 0.0) or 0.0)
            clamped = min(self._camera_distance_maximum, max(self._camera_distance_minimum, distance))
            if clamped != distance:
                self.opts["distance"] = clamped
                self.update()

        def wheelEvent(self, ev) -> None:
            super().wheelEvent(ev)
            self._clamp_camera_distance()

        def projectionMatrix(self, region, viewport):
            matrix = super().projectionMatrix(region, viewport)
            distance = float(self.opts.get("distance", 0.0) or 0.0)
            if distance <= 0.0:
                return matrix
            # pyqtgraph uses farClip = distance * 1000, which would cull the
            # distant starfield when the camera is zoomed in close.
            near_clip = distance * 0.001
            far_clip = distance * 1000.0
            if self._minimum_far_clip <= far_clip:
                return matrix
            far_clip = self._minimum_far_clip
            depth_range = far_clip - near_clip
            depth_row = matrix.row(2)
            depth_row.setZ(-(far_clip + near_clip) / depth_range)
            depth_row.setW(-2.0 * far_clip * near_clip / depth_range)
            matrix.setRow(2, depth_row)
            return matrix

else:
    _KnownObjectOrbitGLViewWidget = None


_ASTEROID_BLINK_INTERVAL_OPTIONS_MS: tuple[int, ...] = (50, 100, 200, 350, 500, 1000, 2000)


@dataclass(frozen=True, slots=True)
class AstrostackGifExportOptions:
    frame_duration_ms: int = 50
    loop_count: int | None = 0
    scale_percent: int = 100
    fast_mode: bool = True
    export_format: str = "gif"


_ASTROSTACK_EXPORT_FORMAT_LABELS: dict[str, str] = {
    "gif": "GIF",
    "mp4": "MP4",
}


class AstrostackGifExportDialog(QDialog):
    def __init__(
        self,
        *,
        frame_count: int,
        initial_options: AstrostackGifExportOptions | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Deep Stack Export")
        self.setMinimumWidth(360)
        self._frame_count = max(1, int(frame_count))
        self._syncing_frame_controls = False
        options = initial_options or AstrostackGifExportOptions()

        self._fast_mode_input = QCheckBox("Fast mode", self)
        self._fast_mode_input.setChecked(bool(options.fast_mode))
        self._fast_mode_input.setToolTip(
            "Export evenly spaced frames (up to 60) instead of one frame per cumulative stack. "
            "All frames are still aligned and stacked; only export rendering is subsampled."
        )

        self._format_input = QComboBox(self)
        for format_key, format_label in _ASTROSTACK_EXPORT_FORMAT_LABELS.items():
            self._format_input.addItem(format_label, format_key)
        normalized_format = str(options.export_format or "gif").strip().lower()
        format_index = self._format_input.findData(normalized_format if normalized_format in _ASTROSTACK_EXPORT_FORMAT_LABELS else "gif")
        self._format_input.setCurrentIndex(0 if format_index < 0 else format_index)

        self._frame_rate_input = QDoubleSpinBox(self)
        self._frame_rate_input.setRange(0.2, 50.0)
        self._frame_rate_input.setDecimals(2)
        self._frame_rate_input.setSingleStep(1.0)
        self._frame_rate_input.setSuffix(" fps")

        self._frame_duration_input = QSpinBox(self)
        self._frame_duration_input.setRange(20, 5000)
        self._frame_duration_input.setSingleStep(10)
        self._frame_duration_input.setSuffix(" ms")

        self._scale_input = QSpinBox(self)
        self._scale_input.setRange(10, 100)
        self._scale_input.setSingleStep(5)
        self._scale_input.setSuffix(" %")
        self._scale_input.setValue(max(10, min(100, int(options.scale_percent))))
        self._scale_input.setToolTip(
            "Output image size as a percentage of the stacked frame. "
            "The image and all overlay layers are scaled together to preserve layout alignment."
        )

        self._loop_forever_input = QCheckBox("Loop forever", self)
        self._loop_forever_input.setChecked(options.loop_count == 0)

        self._playback_widget = QWidget(self)
        playback_layout = QHBoxLayout(self._playback_widget)
        playback_layout.setContentsMargins(0, 0, 0, 0)
        playback_layout.addWidget(self._loop_forever_input)

        self._summary_label = QLabel(self)
        self._summary_label.setWordWrap(True)

        self._frame_duration_input.setValue(max(20, min(5000, int(options.frame_duration_ms))))
        self._sync_frame_rate_from_duration()
        self._update_summary()

        self._frame_duration_input.valueChanged.connect(self._handle_frame_duration_changed)
        self._frame_rate_input.valueChanged.connect(self._handle_frame_rate_changed)
        self._scale_input.valueChanged.connect(lambda _value: self._update_summary())
        self._loop_forever_input.toggled.connect(lambda _checked: self._update_summary())
        self._fast_mode_input.toggled.connect(lambda _checked: self._update_summary())
        self._format_input.currentIndexChanged.connect(self._handle_export_format_changed)

        form_layout = QFormLayout()
        form_layout.addRow("Processing", self._fast_mode_input)
        form_layout.addRow("Format", self._format_input)
        form_layout.addRow("Frame Rate", self._frame_rate_input)
        form_layout.addRow("Frame Time", self._frame_duration_input)
        form_layout.addRow("Output size", self._scale_input)
        form_layout.addRow("Playback", self._playback_widget)
        self._sync_export_format_dependent_controls()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            ok_button.setText("Export")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form_layout)
        layout.addWidget(self._summary_label)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def selected_options(self) -> AstrostackGifExportOptions:
        export_format = str(self._format_input.currentData() or "gif").strip().lower()
        if export_format not in _ASTROSTACK_EXPORT_FORMAT_LABELS:
            export_format = "gif"
        return AstrostackGifExportOptions(
            frame_duration_ms=int(self._frame_duration_input.value()),
            loop_count=0 if self._loop_forever_input.isChecked() else None,
            scale_percent=int(self._scale_input.value()),
            fast_mode=self._fast_mode_input.isChecked(),
            export_format=export_format,
        )

    def _selected_export_format(self) -> str:
        export_format = str(self._format_input.currentData() or "gif").strip().lower()
        return export_format if export_format in _ASTROSTACK_EXPORT_FORMAT_LABELS else "gif"

    def _sync_export_format_dependent_controls(self) -> None:
        is_gif = self._selected_export_format() == "gif"
        self._playback_widget.setVisible(is_gif)

    def _handle_export_format_changed(self, _index: int) -> None:
        self._sync_export_format_dependent_controls()
        self._update_summary()

    def _handle_frame_duration_changed(self, _value: int) -> None:
        if self._syncing_frame_controls:
            return
        self._sync_frame_rate_from_duration()
        self._update_summary()

    def _handle_frame_rate_changed(self, _value: float) -> None:
        if self._syncing_frame_controls:
            return
        self._syncing_frame_controls = True
        try:
            duration_ms = int(round(1000.0 / max(0.2, float(self._frame_rate_input.value()))))
            self._frame_duration_input.setValue(max(20, min(5000, duration_ms)))
        finally:
            self._syncing_frame_controls = False
        self._update_summary()

    def _sync_frame_rate_from_duration(self) -> None:
        self._syncing_frame_controls = True
        try:
            fps = 1000.0 / max(20, int(self._frame_duration_input.value()))
            self._frame_rate_input.setValue(max(0.2, min(50.0, fps)))
        finally:
            self._syncing_frame_controls = False

    def _update_summary(self) -> None:
        export_frame_count = len(
            resolve_astrostack_stack_export_frame_indices(
                self._frame_count,
                fast_mode=self._fast_mode_input.isChecked(),
            )
        )
        duration_seconds = (export_frame_count * int(self._frame_duration_input.value())) / 1000.0
        format_label = _ASTROSTACK_EXPORT_FORMAT_LABELS.get(self._selected_export_format(), "GIF")
        loop_text = "loops forever" if self._loop_forever_input.isChecked() else "plays once"
        subsample_text = ""
        if self._fast_mode_input.isChecked() and export_frame_count < self._frame_count:
            subsample_text = (
                f" Fast mode exports {export_frame_count} evenly spaced {format_label} frames from the "
                f"{self._frame_count} cumulative stacks."
            )
        elif not self._fast_mode_input.isChecked():
            subsample_text = f" Full quality exports every cumulative stack as a {format_label} frame."
        playback_text = f", {loop_text}" if self._selected_export_format() == "gif" else ""
        self._summary_label.setText(
            f"{self._frame_count} stack frame(s), {export_frame_count} {format_label} frame(s), "
            f"{duration_seconds:.2f} s per pass, {self._scale_input.value()}% output size{playback_text}."
            f"{subsample_text}"
        )


@dataclass(frozen=True, slots=True)
class SkyExplorerComparisonAnimationOptions:
    duration_seconds: float = 5.0
    frame_rate_fps: float = 30.0
    smooth_motion: bool = True
    ping_pong: bool = False
    loop_forever: bool = True
    output_scale_percent: int = 100


class SkyExplorerComparisonAnimationExportDialog(QDialog):
    def __init__(
        self,
        *,
        initial_options: SkyExplorerComparisonAnimationOptions | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export Comparison Animation")
        self.setMinimumWidth(360)
        options = initial_options or SkyExplorerComparisonAnimationOptions()

        self._duration_input = QDoubleSpinBox(self)
        self._duration_input.setRange(0.5, 120.0)
        self._duration_input.setDecimals(1)
        self._duration_input.setSingleStep(0.5)
        self._duration_input.setSuffix(" s")
        self._duration_input.setValue(max(0.5, float(options.duration_seconds)))

        self._frame_rate_input = QDoubleSpinBox(self)
        self._frame_rate_input.setRange(5.0, 120.0)
        self._frame_rate_input.setDecimals(1)
        self._frame_rate_input.setSingleStep(1.0)
        self._frame_rate_input.setSuffix(" fps")
        self._frame_rate_input.setValue(max(5.0, float(options.frame_rate_fps)))
        self._frame_rate_input.setToolTip(
            "Target playback frame rate. Smooth motion may add extra frames so the divider does not jump."
        )

        self._smooth_motion_input = QCheckBox("Smooth divider motion", self)
        self._smooth_motion_input.setChecked(bool(options.smooth_motion))
        self._smooth_motion_input.setToolTip(
            "Add extra frames when needed so the divider moves at most 2 screen pixels per frame."
        )

        self._ping_pong_input = QCheckBox("Return divider to the left", self)
        self._ping_pong_input.setChecked(bool(options.ping_pong))
        self._ping_pong_input.setToolTip(
            "After the divider reaches the right edge, animate it back to the left edge."
        )

        self._loop_forever_input = QCheckBox("Loop forever (GIF only)", self)
        self._loop_forever_input.setChecked(bool(options.loop_forever))
        self._loop_forever_input.setToolTip(
            "Applies to GIF exports. MP4 videos always play once."
        )

        self._output_scale_input = QSpinBox(self)
        self._output_scale_input.setRange(10, 100)
        self._output_scale_input.setSingleStep(5)
        self._output_scale_input.setSuffix(" %")
        self._output_scale_input.setValue(max(10, min(100, int(options.output_scale_percent))))
        self._output_scale_input.setToolTip(
            "Output size as a percentage of the current view. Lower values reduce GIF file size. "
            "MP4 exports also honor this setting."
        )

        self._summary_label = QLabel(self)
        self._summary_label.setWordWrap(True)

        self._duration_input.valueChanged.connect(lambda _value: self._update_summary())
        self._frame_rate_input.valueChanged.connect(lambda _value: self._update_summary())
        self._smooth_motion_input.toggled.connect(lambda _checked: self._update_summary())
        self._ping_pong_input.toggled.connect(lambda _checked: self._update_summary())
        self._loop_forever_input.toggled.connect(lambda _checked: self._update_summary())
        self._output_scale_input.valueChanged.connect(lambda _value: self._update_summary())

        form_layout = QFormLayout()
        form_layout.addRow("Duration", self._duration_input)
        form_layout.addRow("Frame rate", self._frame_rate_input)
        form_layout.addRow("Output size", self._output_scale_input)
        form_layout.addRow("Motion quality", self._smooth_motion_input)
        form_layout.addRow("Divider motion", self._ping_pong_input)
        form_layout.addRow("Playback", self._loop_forever_input)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            ok_button.setText("Continue")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form_layout)
        layout.addWidget(self._summary_label)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self._update_summary()

    def selected_options(self) -> SkyExplorerComparisonAnimationOptions:
        return SkyExplorerComparisonAnimationOptions(
            duration_seconds=float(self._duration_input.value()),
            frame_rate_fps=float(self._frame_rate_input.value()),
            smooth_motion=self._smooth_motion_input.isChecked(),
            ping_pong=self._ping_pong_input.isChecked(),
            loop_forever=self._loop_forever_input.isChecked(),
            output_scale_percent=int(self._output_scale_input.value()),
        )

    def _update_summary(self) -> None:
        from photometry_app.core.animation_export import (
            resolve_sky_explorer_comparison_animation_timing,
            sky_explorer_comparison_split_fractions,
        )

        options = self.selected_options()
        divider_travel_pixels = 1200.0 * 0.96 if options.smooth_motion else None
        mp4_frame_count, mp4_frame_duration_ms = resolve_sky_explorer_comparison_animation_timing(
            options.duration_seconds,
            fps=options.frame_rate_fps,
            ping_pong=options.ping_pong,
            divider_travel_pixels=divider_travel_pixels,
            smooth_motion=options.smooth_motion,
            gif_mode=False,
        )
        gif_frame_count, gif_frame_duration_ms = resolve_sky_explorer_comparison_animation_timing(
            options.duration_seconds,
            fps=options.frame_rate_fps,
            ping_pong=options.ping_pong,
            divider_travel_pixels=divider_travel_pixels,
            smooth_motion=options.smooth_motion,
            gif_mode=True,
        )
        mp4_fractions = sky_explorer_comparison_split_fractions(
            frame_count=mp4_frame_count,
            ping_pong=options.ping_pong,
        )
        gif_fractions = sky_explorer_comparison_split_fractions(
            frame_count=gif_frame_count,
            ping_pong=options.ping_pong,
        )
        motion_text = "left to right and back" if options.ping_pong else "left to right"
        loop_text = "loops forever for GIF" if options.loop_forever else "plays once for GIF"
        scale_text = (
            f"Output size is {options.output_scale_percent}% of the current view."
            if options.output_scale_percent != 100
            else ""
        )
        gif_note = ""
        if gif_frame_count < mp4_frame_count:
            gif_note = (
                f" GIF uses {len(gif_fractions)} frame(s) at {gif_frame_duration_ms} ms each "
                f"because many viewers enforce a 20 ms minimum per GIF frame."
            )
        mp4_playback_seconds = len(mp4_fractions) * mp4_frame_duration_ms / 1000.0
        self._summary_label.setText(
            f"MP4 exports {len(mp4_fractions)} frame(s) at {options.frame_rate_fps:.1f} fps over "
            f"{options.duration_seconds:.1f} s ({mp4_frame_duration_ms} ms per frame, "
            f"{mp4_playback_seconds:.1f} s playback). GIF exports use the same timing target with "
            f"{len(gif_fractions)} frame(s) at {gif_frame_duration_ms} ms per frame while the divider "
            f"moves {motion_text}. {loop_text}; MP4 always plays once.{gif_note} {scale_text}".strip()
        )


_SKY_EXPLORER_SETTINGS_LAYER_FIELDS: tuple[tuple[str, str, str], ...] = (
    (
        "deep_sky",
        "SIMBAD Deep Sky",
        "Query deep-sky objects such as galaxies, nebulae, and clusters from the main Sky Explorer deep-sky search path.",
    ),
    (
        "general_objects",
        "SIMBAD General Objects",
        "Query SIMBAD for named non-stellar general objects that fall outside the narrower deep-sky categories.",
    ),
    (
        "solar_system",
        "Solar System Objects",
        "Search nearby known asteroids and comets using the image observation time and solved field footprint.",
    ),
    (
        "variable_stars",
        "VSX Variable Stars",
        "Query the AAVSO VSX catalog for variable stars in the solved field.",
    ),
    (
        "gaia_stars",
        "Gaia DR3 Stars",
        "Query Gaia DR3 for stellar sources in the solved field.",
    ),
    (
        "exoplanets",
        "NASA Exoplanet Hosts",
        "Query the NASA Exoplanet Archive host-star list for the solved field.",
    ),
)


if pg is not None:
    class _UtcDateAxisItem(pg.AxisItem):
        def __init__(self, orientation: str = "bottom") -> None:
            super().__init__(orientation=orientation)

        def tickStrings(self, values: list[float], scale: float, spacing: float) -> list[str]:
            labels: list[str] = []
            for value in values:
                try:
                    timestamp = datetime.fromtimestamp(float(value), tz=UTC)
                except Exception:
                    labels.append("")
                    continue
                if spacing >= 86400.0 * 365.0:
                    labels.append(timestamp.strftime("%Y"))
                elif spacing >= 86400.0 * 28.0:
                    labels.append(timestamp.strftime("%Y-%m"))
                elif spacing >= 86400.0:
                    labels.append(timestamp.strftime("%m-%d"))
                else:
                    labels.append(timestamp.strftime("%m-%d\n%H:%M"))
            return labels


_AAVSO_FILTER_OPTIONS = [
    "U",
    "B",
    "V",
    "R",
    "I",
    "J",
    "H",
    "K",
    "TG",
    "TB",
    "TR",
    "CV",
    "CR",
    "SZ",
    "SU",
    "SG",
    "SR",
    "SI",
    "STU",
    "STV",
    "STB",
    "STY",
    "STHBW",
    "STHBN",
    "MA",
    "MB",
    "MI",
    "ZS",
    "Y",
    "HA",
    "HAC",
    "O",
]


def _observation_timezone_options() -> list[str]:
    common = [
        "UTC",
        "America/New_York",
        "America/Chicago",
        "America/Denver",
        "America/Los_Angeles",
        "Europe/London",
        "Europe/Berlin",
        "Asia/Tokyo",
        "Australia/Sydney",
    ]
    try:
        discovered = sorted(available_timezones())
    except Exception:
        discovered = []
    ordered: list[str] = []
    for item in [*common, *discovered]:
        if item and item not in ordered:
            ordered.append(item)
    return ordered


_OBSERVATION_TIMEZONE_OPTIONS = _observation_timezone_options()

_KNOWN_OBJECT_3D_SPAN_OPTIONS = (
    ("local", "Local", 45.0, 61),
    ("90d", "+/-90d", 90.0, 91),
    ("180d", "+/-180d", 180.0, 121),
    ("1y", "1y", 365.25, 181),
    ("5y", "5y", 365.25 * 5.0, 361),
    ("custom", "Custom", None, None),
)

_KNOWN_OBJECT_3D_BODY_STYLES = {
    "mercury": {"line": (0.85, 0.72, 0.58), "glow": (0.74, 0.63, 0.49), "hex": "#d9b894"},
    "venus": {"line": (0.92, 0.84, 0.55), "glow": (0.80, 0.70, 0.36), "hex": "#ebd68c"},
    "mars": {"line": (0.96, 0.46, 0.34), "glow": (0.78, 0.31, 0.22), "hex": "#f57656"},
    "jupiter": {"line": (0.88, 0.70, 0.52), "glow": (0.69, 0.53, 0.39), "hex": "#dfb386"},
    "saturn": {"line": (0.90, 0.79, 0.50), "glow": (0.73, 0.62, 0.35), "hex": "#e6ca80"},
    "uranus": {"line": (0.56, 0.87, 0.96), "glow": (0.39, 0.71, 0.79), "hex": "#90def5"},
    "neptune": {"line": (0.36, 0.58, 1.00), "glow": (0.26, 0.43, 0.80), "hex": "#5c93ff"},
}

_KNOWN_OBJECT_3D_OBJECT_STYLE = {"line": (1.0, 0.70, 0.25), "glow": (1.0, 0.62, 0.10), "hex": "#ffb340"}
_KNOWN_OBJECT_3D_COMET_STYLE = {"line": (0.38, 0.90, 0.95), "glow": (0.22, 0.76, 0.84), "hex": "#61e5f2"}


@dataclass(frozen=True, slots=True)
class LightCurveFilterSettings:
    hide_excluded: bool = False
    max_error_enabled: bool = False
    max_error_magnitude: float = 0.10
    outlier_filter_enabled: bool = False
    min_magnitude: float | None = None
    max_magnitude: float | None = None

    def normalized(self) -> "LightCurveFilterSettings":
        minimum = self.min_magnitude
        maximum = self.max_magnitude
        if self.outlier_filter_enabled and minimum is not None and maximum is not None and minimum > maximum:
            minimum, maximum = maximum, minimum
        return replace(
            self,
            max_error_magnitude=min(5.0, max(0.0, float(self.max_error_magnitude))),
            min_magnitude=None if minimum is None else min(30.0, max(-5.0, float(minimum))),
            max_magnitude=None if maximum is None else min(30.0, max(-5.0, float(maximum))),
        )

    def has_active_filters(self) -> bool:
        normalized = self.normalized()
        return bool(
            normalized.hide_excluded
            or normalized.max_error_enabled
            or normalized.outlier_filter_enabled
        )


class LightCurveFilterDialog(QDialog):
    def __init__(self, settings: LightCurveFilterSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        normalized = settings.normalized()
        self.setWindowTitle("Filtering Settings")
        self.resize(420, 220)

        self._hide_excluded_input = QCheckBox("Hide excluded measurements")
        self._hide_excluded_input.setChecked(normalized.hide_excluded)

        self._max_error_enabled_input = QCheckBox("Exclude points with error above")
        self._max_error_enabled_input.setChecked(normalized.max_error_enabled)
        self._max_error_enabled_input.stateChanged.connect(self._update_enabled_inputs)
        self._max_error_input = QDoubleSpinBox()
        self._max_error_input.setDecimals(2)
        self._max_error_input.setRange(0.0, 5.0)
        self._max_error_input.setSingleStep(0.01)
        self._max_error_input.setSuffix(" mag")
        self._max_error_input.setValue(normalized.max_error_magnitude)

        self._outlier_enabled_input = QCheckBox("Exclude magnitude outliers")
        self._outlier_enabled_input.setChecked(normalized.outlier_filter_enabled)
        self._outlier_enabled_input.stateChanged.connect(self._update_enabled_inputs)
        self._min_magnitude_input = QDoubleSpinBox()
        self._min_magnitude_input.setDecimals(2)
        self._min_magnitude_input.setRange(-5.0, 30.0)
        self._min_magnitude_input.setSingleStep(0.05)
        self._min_magnitude_input.setSuffix(" mag")
        self._min_magnitude_input.setValue(normalized.min_magnitude if normalized.min_magnitude is not None else 10.0)
        self._max_magnitude_input = QDoubleSpinBox()
        self._max_magnitude_input.setDecimals(2)
        self._max_magnitude_input.setRange(-5.0, 30.0)
        self._max_magnitude_input.setSingleStep(0.05)
        self._max_magnitude_input.setSuffix(" mag")
        self._max_magnitude_input.setValue(normalized.max_magnitude if normalized.max_magnitude is not None else 15.0)

        form_layout = QFormLayout()
        form_layout.addRow(self._hide_excluded_input)
        form_layout.addRow(self._max_error_enabled_input, self._max_error_input)

        outlier_group = QGridLayout()
        outlier_group.addWidget(self._outlier_enabled_input, 0, 0, 1, 2)
        outlier_group.addWidget(QLabel("Min magnitude"), 1, 0)
        outlier_group.addWidget(self._min_magnitude_input, 1, 1)
        outlier_group.addWidget(QLabel("Max magnitude"), 2, 0)
        outlier_group.addWidget(self._max_magnitude_input, 2, 1)
        outlier_container = QWidget()
        outlier_container.setLayout(outlier_group)
        form_layout.addRow(outlier_container)

        button_row = QHBoxLayout()
        reset_button = QPushButton("Reset")
        reset_button.clicked.connect(self._reset_inputs)
        button_row.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.accept)
        button_row.addWidget(reset_button)
        button_row.addWidget(cancel_button)
        button_row.addWidget(ok_button)

        layout = QVBoxLayout()
        layout.addLayout(form_layout)
        layout.addStretch(1)
        layout.addLayout(button_row)
        self.setLayout(layout)
        self._update_enabled_inputs()

    def build_settings(self) -> LightCurveFilterSettings:
        minimum = float(self._min_magnitude_input.value()) if self._outlier_enabled_input.isChecked() else None
        maximum = float(self._max_magnitude_input.value()) if self._outlier_enabled_input.isChecked() else None
        return LightCurveFilterSettings(
            hide_excluded=self._hide_excluded_input.isChecked(),
            max_error_enabled=self._max_error_enabled_input.isChecked(),
            max_error_magnitude=float(self._max_error_input.value()),
            outlier_filter_enabled=self._outlier_enabled_input.isChecked(),
            min_magnitude=minimum,
            max_magnitude=maximum,
        ).normalized()

    def _update_enabled_inputs(self) -> None:
        self._max_error_input.setEnabled(self._max_error_enabled_input.isChecked())
        outlier_enabled = self._outlier_enabled_input.isChecked()
        self._min_magnitude_input.setEnabled(outlier_enabled)
        self._max_magnitude_input.setEnabled(outlier_enabled)


@dataclass(frozen=True, slots=True)
class ResultsViewFilterSettings:
    min_snr_enabled: bool = False
    min_snr: float = 10.0
    min_var_score_enabled: bool = False
    min_var_score: float = 5.0
    ml_label: str = ""
    magnitude_filter_enabled: bool = False
    min_magnitude: float | None = None
    max_magnitude: float | None = None

    def normalized(self) -> "ResultsViewFilterSettings":
        minimum = self.min_magnitude
        maximum = self.max_magnitude
        if self.magnitude_filter_enabled and minimum is not None and maximum is not None and minimum > maximum:
            minimum, maximum = maximum, minimum
        return replace(
            self,
            min_snr=max(0.0, float(self.min_snr)),
            min_var_score=max(0.0, float(self.min_var_score)),
            ml_label=str(self.ml_label or "").strip(),
            min_magnitude=None if minimum is None else min(30.0, max(-5.0, float(minimum))),
            max_magnitude=None if maximum is None else min(30.0, max(-5.0, float(maximum))),
        )

    def has_active_filters(self) -> bool:
        normalized = self.normalized()
        return bool(
            normalized.min_snr_enabled
            or normalized.min_var_score_enabled
            or normalized.ml_label
            or normalized.magnitude_filter_enabled
        )


class ResultsViewFilterDialog(QDialog):
    def __init__(
        self,
        settings: ResultsViewFilterSettings,
        *,
        ml_label_options: Sequence[tuple[str, str]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        normalized = settings.normalized()
        self.setWindowTitle("Source/File Results Filters")
        self.resize(440, 260)

        self._min_snr_enabled_input = QCheckBox("Only show rows with SNR above")
        self._min_snr_enabled_input.setChecked(normalized.min_snr_enabled)
        self._min_snr_enabled_input.stateChanged.connect(self._update_enabled_inputs)
        self._min_snr_input = QDoubleSpinBox()
        self._min_snr_input.setDecimals(1)
        self._min_snr_input.setRange(0.0, 1000000.0)
        self._min_snr_input.setSingleStep(1.0)
        self._min_snr_input.setValue(normalized.min_snr)

        self._min_var_score_enabled_input = QCheckBox("Only show rows with Var Score above")
        self._min_var_score_enabled_input.setChecked(normalized.min_var_score_enabled)
        self._min_var_score_enabled_input.stateChanged.connect(self._update_enabled_inputs)
        self._min_var_score_input = QDoubleSpinBox()
        self._min_var_score_input.setDecimals(1)
        self._min_var_score_input.setRange(0.0, 1000000.0)
        self._min_var_score_input.setSingleStep(0.5)
        self._min_var_score_input.setValue(normalized.min_var_score)

        self._ml_label_input = QComboBox()
        self._ml_label_input.addItem("All ML labels", "")
        seen_labels: set[str] = set()
        for label_text, label_value in ml_label_options:
            if not label_value or label_value in seen_labels:
                continue
            seen_labels.add(label_value)
            self._ml_label_input.addItem(label_text, label_value)
        selected_label_index = self._ml_label_input.findData(normalized.ml_label)
        if selected_label_index >= 0:
            self._ml_label_input.setCurrentIndex(selected_label_index)

        self._magnitude_enabled_input = QCheckBox("Only show catalog magnitudes between")
        self._magnitude_enabled_input.setChecked(normalized.magnitude_filter_enabled)
        self._magnitude_enabled_input.stateChanged.connect(self._update_enabled_inputs)
        self._min_magnitude_input = QDoubleSpinBox()
        self._min_magnitude_input.setDecimals(2)
        self._min_magnitude_input.setRange(-5.0, 30.0)
        self._min_magnitude_input.setSingleStep(0.1)
        self._min_magnitude_input.setSuffix(" mag")
        self._min_magnitude_input.setValue(normalized.min_magnitude if normalized.min_magnitude is not None else 10.0)
        self._max_magnitude_input = QDoubleSpinBox()
        self._max_magnitude_input.setDecimals(2)
        self._max_magnitude_input.setRange(-5.0, 30.0)
        self._max_magnitude_input.setSingleStep(0.1)
        self._max_magnitude_input.setSuffix(" mag")
        self._max_magnitude_input.setValue(normalized.max_magnitude if normalized.max_magnitude is not None else 15.0)

        form_layout = QFormLayout()
        form_layout.addRow(self._min_snr_enabled_input, self._min_snr_input)
        form_layout.addRow(self._min_var_score_enabled_input, self._min_var_score_input)
        form_layout.addRow("ML label", self._ml_label_input)

        magnitude_layout = QGridLayout()
        magnitude_layout.setContentsMargins(0, 0, 0, 0)
        magnitude_layout.addWidget(self._magnitude_enabled_input, 0, 0, 1, 2)
        magnitude_layout.addWidget(QLabel("Min magnitude"), 1, 0)
        magnitude_layout.addWidget(self._min_magnitude_input, 1, 1)
        magnitude_layout.addWidget(QLabel("Max magnitude"), 2, 0)
        magnitude_layout.addWidget(self._max_magnitude_input, 2, 1)
        magnitude_container = QWidget()
        magnitude_container.setLayout(magnitude_layout)
        form_layout.addRow(magnitude_container)

        button_row = QHBoxLayout()
        reset_button = QPushButton("Reset")
        reset_button.clicked.connect(self._reset_inputs)
        button_row.addWidget(reset_button)
        button_row.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        button_row.addWidget(buttons)

        layout = QVBoxLayout()
        layout.addLayout(form_layout)
        layout.addStretch(1)
        layout.addLayout(button_row)
        self.setLayout(layout)

        self._update_enabled_inputs()

    def build_settings(self) -> ResultsViewFilterSettings:
        return ResultsViewFilterSettings(
            min_snr_enabled=self._min_snr_enabled_input.isChecked(),
            min_snr=float(self._min_snr_input.value()),
            min_var_score_enabled=self._min_var_score_enabled_input.isChecked(),
            min_var_score=float(self._min_var_score_input.value()),
            ml_label=str(self._ml_label_input.currentData() or ""),
            magnitude_filter_enabled=self._magnitude_enabled_input.isChecked(),
            min_magnitude=float(self._min_magnitude_input.value()) if self._magnitude_enabled_input.isChecked() else None,
            max_magnitude=float(self._max_magnitude_input.value()) if self._magnitude_enabled_input.isChecked() else None,
        ).normalized()

    def _reset_inputs(self) -> None:
        self._min_snr_enabled_input.setChecked(False)
        self._min_snr_input.setValue(10.0)
        self._min_var_score_enabled_input.setChecked(False)
        self._min_var_score_input.setValue(5.0)
        self._ml_label_input.setCurrentIndex(0)
        self._magnitude_enabled_input.setChecked(False)
        self._min_magnitude_input.setValue(10.0)
        self._max_magnitude_input.setValue(15.0)
        self._update_enabled_inputs()

    def _update_enabled_inputs(self) -> None:
        self._min_snr_input.setEnabled(self._min_snr_enabled_input.isChecked())
        self._min_var_score_input.setEnabled(self._min_var_score_enabled_input.isChecked())
        magnitude_enabled = self._magnitude_enabled_input.isChecked()
        self._min_magnitude_input.setEnabled(magnitude_enabled)
        self._max_magnitude_input.setEnabled(magnitude_enabled)

    def _reset_inputs(self) -> None:
        self._hide_excluded_input.setChecked(False)
        self._max_error_enabled_input.setChecked(False)
        self._max_error_input.setValue(0.10)
        self._outlier_enabled_input.setChecked(False)
        self._min_magnitude_input.setValue(10.0)
        self._max_magnitude_input.setValue(15.0)
        self._update_enabled_inputs()


@dataclass(frozen=True, slots=True)
class KnownObjectOrbit3DSearchEntry:
    target: AsteroidOrbitContextTarget
    angular_distance_deg: float | None = None
    is_in_image: bool = False


@dataclass(frozen=True, slots=True)
class KnownObjectOrbit3DPlannerRequest:
    identifier: str
    start_time: datetime
    end_time: datetime


class _KnownObjectOrbit3DSearchWorker(QThread):
    search_updated = Signal(object)
    search_status_updated = Signal(str)
    search_completed = Signal(object)
    search_failed = Signal(str)

    def __init__(self, operation: Callable[[Callable[[object], None], Callable[[str], None]], object], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._operation = operation

    def run(self) -> None:
        try:
            result = self._operation(self.search_updated.emit, self.search_status_updated.emit)
        except Exception as exc:
            self.search_failed.emit(str(exc))
            return
        self.search_completed.emit(result)


class _KnownObjectOrbit3DSearchResultsDialog(QDialog):
    def __init__(self, *, title: str, description_text: str, parent: QWidget | None = None, action_button_text: str = "Add Selected", initial_status_text: str = "Adjust the search options, run the search, then choose which objects to add.") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(860, 520)
        self._entries: tuple[KnownObjectOrbit3DSearchEntry, ...] = ()
        self._worker: _KnownObjectOrbit3DSearchWorker | None = None

        description = QLabel(description_text, self)
        description.setWordWrap(True)
        self._status_label = QLabel(initial_status_text, self)
        self._status_label.setWordWrap(True)

        self._table = QTableWidget(0, 6, self)
        self._table.setHorizontalHeaderLabels(["Name", "Type", "Pred Mag", "Distance", "In Image", "Status"])
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.itemSelectionChanged.connect(self._sync_add_button_enabled)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column_index in (1, 2, 3, 4, 5):
            header.setSectionResizeMode(column_index, QHeaderView.ResizeMode.ResizeToContents)

        self._count_label = QLabel("0 objects", self)
        cancel_button = QPushButton("Cancel", self)
        cancel_button.setAutoDefault(False)
        cancel_button.setDefault(False)
        cancel_button.clicked.connect(self.reject)
        self._add_button = QPushButton(action_button_text, self)
        self._add_button.setAutoDefault(False)
        self._add_button.setDefault(False)
        self._add_button.clicked.connect(self.accept)

        self._buttons_row = QHBoxLayout()
        self._buttons_row.addWidget(self._count_label)
        self._buttons_row.addStretch(1)
        self._buttons_row.addWidget(cancel_button)
        self._buttons_row.addWidget(self._add_button)

        self._body_layout = QVBoxLayout()
        self._body_layout.addWidget(description)
        self._body_layout.addWidget(self._status_label)
        self._body_layout.addWidget(self._table, stretch=1)
        self._body_layout.addLayout(self._buttons_row)
        self.setLayout(self._body_layout)
        self._sync_add_button_enabled()

    def selected_entries(self) -> tuple[KnownObjectOrbit3DSearchEntry, ...]:
        selected_rows = sorted({index.row() for index in self._table.selectionModel().selectedRows()})
        return tuple(self._entries[row_index] for row_index in selected_rows if 0 <= row_index < len(self._entries))

    def _set_entries(self, entries: tuple[KnownObjectOrbit3DSearchEntry, ...], *, empty_message: str, preserve_status: bool = False) -> None:
        self._entries = tuple(entries)
        self._table.setUpdatesEnabled(False)
        self._table.blockSignals(True)
        try:
            self._table.setRowCount(len(self._entries))
            for row_index, entry in enumerate(self._entries):
                detection = entry.target.detection
                values = (
                    detection.name or detection.designation or f"Target {row_index + 1}",
                    detection.object_type or "Unknown",
                    "--" if detection.predicted_magnitude is None else f"{float(detection.predicted_magnitude):.1f}",
                    "--" if entry.angular_distance_deg is None else f"{float(entry.angular_distance_deg):.2f} deg",
                    "Yes" if entry.is_in_image else "No",
                    detection.status or "Predicted nearby",
                )
                for column_index, value in enumerate(values):
                    self._table.setItem(row_index, column_index, QTableWidgetItem(value))
        finally:
            self._table.blockSignals(False)
            self._table.setUpdatesEnabled(True)
        count_text = f"{len(self._entries)} object"
        if len(self._entries) != 1:
            count_text += "s"
        self._count_label.setText(count_text)
        if not preserve_status:
            self._status_label.setText(empty_message if not self._entries else f"Found {len(self._entries)} object(s). Select the rows to add.")
        self._sync_add_button_enabled()

    def _begin_search(self, operation: Callable[[Callable[[object], None], Callable[[str], None]], object], *, loading_text: str) -> None:
        if self._worker is not None:
            return
        self._set_entries((), empty_message="", preserve_status=True)
        self._set_search_controls_enabled(False)
        self._status_label.setText(loading_text)
        self._worker = _KnownObjectOrbit3DSearchWorker(operation, self)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.search_updated.connect(self._handle_search_updated)
        self._worker.search_status_updated.connect(self._handle_search_status_updated)
        self._worker.search_completed.connect(self._handle_search_completed)
        self._worker.search_failed.connect(self._handle_search_failed)
        self._worker.start()

    def _set_search_controls_enabled(self, enabled: bool) -> None:
        self._table.setEnabled(enabled)
        self._add_button.setEnabled(enabled and bool(self._table.selectionModel().selectedRows()))

    def _handle_search_completed(self, result: object) -> None:
        self._worker = None
        self._set_search_controls_enabled(True)
        self._handle_search_result(result)

    def _handle_search_failed(self, message: str) -> None:
        self._worker = None
        self._set_search_controls_enabled(True)
        self._status_label.setText(message or "Search failed.")

    def _handle_search_updated(self, result: object) -> None:
        return

    def _handle_search_status_updated(self, message: str) -> None:
        if message:
            self._status_label.setText(message)

    def _handle_search_result(self, result: object) -> None:
        raise NotImplementedError

    def _sync_add_button_enabled(self) -> None:
        self._add_button.setEnabled(self._worker is None and bool(self._table.selectionModel().selectedRows()))


class KnownObjectOrbit3DNearbySearchDialog(_KnownObjectOrbit3DSearchResultsDialog):
    def __init__(
        self,
        *,
        default_radius_deg: float,
        default_magnitude_limit: float,
        search_callback: Callable[[float, float], tuple[KnownObjectOrbit3DSearchEntry, ...]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            title="Nearby Field Search",
            description_text="Search a wider nearby field around the current image, review the found objects, then select which ones to add into the shared 3D scene.",
            parent=parent,
        )
        self._search_callback = search_callback

        self._radius_input = QDoubleSpinBox(self)
        self._radius_input.setRange(0.1, 10.0)
        self._radius_input.setDecimals(1)
        self._radius_input.setSingleStep(0.5)
        self._radius_input.setSuffix(" deg")
        self._radius_input.setValue(float(default_radius_deg))

        self._magnitude_input = QDoubleSpinBox(self)
        self._magnitude_input.setRange(-5.0, 30.0)
        self._magnitude_input.setDecimals(1)
        self._magnitude_input.setSingleStep(0.5)
        self._magnitude_input.setSuffix(" mag")
        self._magnitude_input.setValue(float(default_magnitude_limit))

        self._search_button = QPushButton("Search Nearby", self)
        self._search_button.setAutoDefault(False)
        self._search_button.setDefault(False)
        self._search_button.clicked.connect(self._start_search)

        controls_row = QHBoxLayout()
        controls_row.addWidget(QLabel("Radius", self))
        controls_row.addWidget(self._radius_input)
        controls_row.addSpacing(8)
        controls_row.addWidget(QLabel("Max predicted mag", self))
        controls_row.addWidget(self._magnitude_input)
        controls_row.addSpacing(8)
        controls_row.addWidget(self._search_button)
        controls_row.addStretch(1)
        self._body_layout.insertLayout(2, controls_row)

    def _start_search(self) -> None:
        radius_deg = float(self._radius_input.value())
        magnitude_limit = float(self._magnitude_input.value())
        self._begin_search(
            lambda _emit_results, _emit_status: self._search_callback(radius_deg, magnitude_limit),
            loading_text="Searching nearby asteroid/comet predictions...",
        )

    def _set_search_controls_enabled(self, enabled: bool) -> None:
        super()._set_search_controls_enabled(enabled)
        self._radius_input.setEnabled(enabled)
        self._magnitude_input.setEnabled(enabled)
        self._search_button.setEnabled(enabled)

    def _handle_search_result(self, result: object) -> None:
        entries = tuple(result) if isinstance(result, (tuple, list)) else ()
        self._set_entries(entries, empty_message="No nearby off-image asteroid/comet targets matched the current search.")


class KnownObjectOrbit3DExactLookupDialog(_KnownObjectOrbit3DSearchResultsDialog):
    def __init__(
        self,
        *,
        lookup_callback: Callable[[str], tuple[KnownObjectOrbit3DSearchEntry, ...]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            title="Object Lookup",
            description_text="Search the small-body database by keyword, name, or designation, review the matching objects, then add the ones you want into the current 3D scene.",
            parent=parent,
        )
        self._lookup_callback = lookup_callback

        self._identifier_input = QLineEdit(self)
        self._identifier_input.setPlaceholderText("Examples: Pallas, 2 Pallas, 154P, ATLAS, 3I")
        self._identifier_input.returnPressed.connect(self._start_search)

        self._lookup_button = QPushButton("Lookup", self)
        self._lookup_button.setAutoDefault(False)
        self._lookup_button.setDefault(False)
        self._lookup_button.clicked.connect(self._start_search)

        controls_row = QHBoxLayout()
        controls_row.addWidget(QLabel("Name", self))
        controls_row.addWidget(self._identifier_input, stretch=1)
        controls_row.addWidget(self._lookup_button)
        self._body_layout.insertLayout(2, controls_row)

    def _start_search(self) -> None:
        identifier = self._identifier_input.text().strip()
        if not identifier:
            self._status_label.setText("Enter a keyword, asteroid/comet name, or designation to search.")
            return
        self._begin_search(
            lambda _emit_results, _emit_status: self._lookup_callback(identifier),
            loading_text=f"Searching asteroid/comet matches for {identifier}...",
        )

    def _set_search_controls_enabled(self, enabled: bool) -> None:
        super()._set_search_controls_enabled(enabled)
        self._identifier_input.setEnabled(enabled)
        self._lookup_button.setEnabled(enabled)

    def _handle_search_result(self, result: object) -> None:
        entries = tuple(result) if isinstance(result, (tuple, list)) else ()
        self._set_entries(entries, empty_message="No asteroid/comet matches were found for that search.")


class KnownObjectOrbit3DPlannerDialog(_KnownObjectOrbit3DSearchResultsDialog):
    def __init__(
        self,
        *,
        default_start_time: datetime,
        default_end_time: datetime,
        search_callback: Callable[[KnownObjectOrbit3DPlannerRequest], tuple[KnownObjectOrbit3DSearchEntry, ...]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            title="Plan Object",
            description_text="Search globally for a named asteroid or comet over the selected date range, then open a heliocentric 3D view for future observation planning.",
            parent=parent,
            action_button_text="Open Selected",
            initial_status_text="Enter an object name, choose the start and end dates, then open the selected object(s).",
        )
        self._search_callback = search_callback

        self._identifier_input = QLineEdit(self)
        self._identifier_input.setPlaceholderText("Examples: Pallas, 2 Pallas, 154P, ATLAS, or 3I")
        self._identifier_input.returnPressed.connect(self._start_search)

        self._start_time_input = QLineEdit(self)
        self._start_time_input.setPlaceholderText("YYYY-MM-DD")
        self._start_time_input.setText(self._format_date(default_start_time))
        self._start_time_input.returnPressed.connect(self._start_search)

        self._end_time_input = QLineEdit(self)
        self._end_time_input.setPlaceholderText("YYYY-MM-DD")
        self._end_time_input.setText(self._format_date(default_end_time))
        self._end_time_input.returnPressed.connect(self._start_search)

        self._search_button = QPushButton("Search", self)
        self._search_button.setAutoDefault(False)
        self._search_button.setDefault(False)
        self._search_button.clicked.connect(self._start_search)

        controls_form = QFormLayout()
        controls_form.addRow("Object", self._identifier_input)
        controls_form.addRow("Start Date", self._start_time_input)
        controls_form.addRow("End Date", self._end_time_input)

        controls_row = QHBoxLayout()
        controls_row.addLayout(controls_form, stretch=1)
        controls_row.addWidget(self._search_button)
        self._body_layout.insertLayout(2, controls_row)

    @staticmethod
    def _format_date(value: datetime) -> str:
        return value.astimezone(UTC).strftime("%Y-%m-%d")

    @staticmethod
    def _parse_midnight_utc_date(value: str, *, label: str) -> datetime:
        try:
            parsed_date = datetime.strptime(value.strip(), "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"{label} must use YYYY-MM-DD.") from exc
        return parsed_date.replace(tzinfo=UTC)

    def _build_request(self) -> KnownObjectOrbit3DPlannerRequest | None:
        identifier = self._identifier_input.text().strip()
        if not identifier:
            self._status_label.setText("Enter an asteroid or comet name/designation before searching.")
            return None
        try:
            start_time = self._parse_midnight_utc_date(self._start_time_input.text(), label="Start Date")
            end_time = self._parse_midnight_utc_date(self._end_time_input.text(), label="End Date")
        except ValueError as exc:
            self._status_label.setText(str(exc))
            return None
        if end_time <= start_time:
            self._status_label.setText("End Date must be later than Start Date.")
            return None
        return KnownObjectOrbit3DPlannerRequest(
            identifier=identifier,
            start_time=start_time,
            end_time=end_time,
        )

    def _start_search(self) -> None:
        request = self._build_request()
        if request is None:
            return
        self._begin_search(
            lambda emit_results, emit_status: self._search_callback(
                request,
                partial_results_callback=emit_results,
                progress_callback=emit_status,
            ),
            loading_text=f"Searching planning matches for {request.identifier}...",
        )

    def _set_search_controls_enabled(self, enabled: bool) -> None:
        super()._set_search_controls_enabled(enabled)
        self._identifier_input.setEnabled(enabled)
        self._start_time_input.setEnabled(enabled)
        self._end_time_input.setEnabled(enabled)
        self._search_button.setEnabled(enabled)

    def _handle_search_updated(self, result: object) -> None:
        entries = tuple(result) if isinstance(result, (tuple, list)) else ()
        self._set_entries(entries, empty_message="No asteroid/comet planning matches were found for that search.", preserve_status=True)

    def _handle_search_result(self, result: object) -> None:
        entries = tuple(result) if isinstance(result, (tuple, list)) else ()
        self._set_entries(entries, empty_message="No asteroid/comet planning matches were found for that search.")


class HrMotionGroupDialog(QDialog):
    def __init__(self, settings: HrMotionGroupSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        normalized = settings.normalized()
        self.setWindowTitle("Motion Group Detection")
        self.resize(470, 360)
        self._updating_inputs = False

        description = QLabel(
            "Keep Find Motion Group simple by choosing a preset here. Expert controls stay optional and only affect future runs until you change them again."
        )
        description.setWordWrap(True)

        self._preset_input = QComboBox()
        self._preset_input.addItem("Default", "default")
        self._preset_input.addItem("Tight", "tight")
        self._preset_input.addItem("Loose", "loose")
        self._preset_input.addItem("Parallax Priority", "parallax")
        self._preset_input.addItem("Custom", "custom")
        self._preset_input.currentIndexChanged.connect(self._handle_preset_changed)

        self._preset_description = QLabel()
        self._preset_description.setWordWrap(True)

        self._auto_filter_input = QCheckBox("Turn on Only Group automatically after detection")

        general_form = QFormLayout()
        general_form.addRow("Preset", self._preset_input)

        self._expert_toggle_button = QPushButton("Show Expert Controls")
        self._expert_toggle_button.setCheckable(True)
        self._expert_toggle_button.toggled.connect(self._set_expert_controls_visible)

        expert_note = QLabel("Changing any expert setting switches the preset to Custom.")
        expert_note.setWordWrap(True)

        self._method_input = QComboBox()
        self._method_input.addItem("Auto (recommended)", "auto")
        self._method_input.addItem("Lightweight", "lightweight")
        self._method_input.addItem("Sklearn DBSCAN", "sklearn")
        self._method_input.currentIndexChanged.connect(self._mark_custom_from_expert_change)

        self._strictness_input = QDoubleSpinBox()
        self._strictness_input.setRange(0.4, 2.5)
        self._strictness_input.setSingleStep(0.1)
        self._strictness_input.setDecimals(2)
        self._strictness_input.valueChanged.connect(self._mark_custom_from_expert_change)

        self._parallax_mode_input = QComboBox()
        self._parallax_mode_input.addItem("Auto", "auto")
        self._parallax_mode_input.addItem("Require parallax", "always")
        self._parallax_mode_input.addItem("Proper motion only", "never")
        self._parallax_mode_input.currentIndexChanged.connect(self._mark_custom_from_expert_change)

        self._refine_hr_input = QCheckBox("Apply HR consistency cleanup")
        self._refine_hr_input.toggled.connect(self._mark_custom_from_expert_change)

        expert_form = QFormLayout()
        expert_form.addRow("Backend", self._method_input)
        expert_form.addRow("Strictness", self._strictness_input)
        expert_form.addRow("Parallax", self._parallax_mode_input)
        expert_form.addRow(self._refine_hr_input)

        self._expert_container = QWidget()
        expert_layout = QVBoxLayout()
        expert_layout.setContentsMargins(0, 0, 0, 0)
        expert_layout.addWidget(expert_note)
        expert_layout.addLayout(expert_form)
        self._expert_container.setLayout(expert_layout)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.accept)
        button_row.addWidget(cancel_button)
        button_row.addWidget(ok_button)

        layout = QVBoxLayout()
        layout.addWidget(description)
        layout.addLayout(general_form)
        layout.addWidget(self._preset_description)
        layout.addWidget(self._auto_filter_input)
        layout.addWidget(self._expert_toggle_button)
        layout.addWidget(self._expert_container)
        layout.addStretch(1)
        layout.addLayout(button_row)
        self.setLayout(layout)

        self._apply_settings(normalized)
        self._expert_toggle_button.setChecked(normalized.preset == "custom")

    def build_settings(self) -> HrMotionGroupSettings:
        return HrMotionGroupSettings(
            preset=str(self._preset_input.currentData() or "default"),
            method=str(self._method_input.currentData() or "auto"),
            strictness=float(self._strictness_input.value()),
            parallax_mode=str(self._parallax_mode_input.currentData() or "auto"),
            refine_hr_consistency=self._refine_hr_input.isChecked(),
            auto_filter=self._auto_filter_input.isChecked(),
        ).normalized()

    def _apply_settings(self, settings: HrMotionGroupSettings) -> None:
        self._updating_inputs = True
        preset_index = self._preset_input.findData(settings.preset)
        self._preset_input.setCurrentIndex(0 if preset_index < 0 else preset_index)
        method_index = self._method_input.findData(settings.method)
        self._method_input.setCurrentIndex(0 if method_index < 0 else method_index)
        self._strictness_input.setValue(settings.strictness)
        parallax_index = self._parallax_mode_input.findData(settings.parallax_mode)
        self._parallax_mode_input.setCurrentIndex(0 if parallax_index < 0 else parallax_index)
        self._refine_hr_input.setChecked(settings.refine_hr_consistency)
        self._auto_filter_input.setChecked(settings.auto_filter)
        self._updating_inputs = False
        self._update_preset_description()

    def _handle_preset_changed(self) -> None:
        if self._updating_inputs:
            return
        preset = str(self._preset_input.currentData() or "default")
        if preset != "custom":
            preset_settings = HrMotionGroupSettings(preset=preset, auto_filter=self._auto_filter_input.isChecked()).normalized()
            self._apply_settings(preset_settings)
        else:
            self._update_preset_description()
            self._expert_toggle_button.setChecked(True)

    def _mark_custom_from_expert_change(self) -> None:
        if self._updating_inputs:
            return
        if str(self._preset_input.currentData() or "default") != "custom":
            self._updating_inputs = True
            custom_index = self._preset_input.findData("custom")
            self._preset_input.setCurrentIndex(0 if custom_index < 0 else custom_index)
            self._updating_inputs = False
        self._update_preset_description()

    def _update_preset_description(self) -> None:
        preset = str(self._preset_input.currentData() or "default")
        self._preset_description.setText(hr_motion_group_preset_description(preset))

    def _set_expert_controls_visible(self, visible: bool) -> None:
        self._expert_container.setVisible(visible)
        self._expert_toggle_button.setText("Hide Expert Controls" if visible else "Show Expert Controls")


class ScanResultsSummaryDialog(QDialog):
    def __init__(self, summaries: list[ObjectScanSummary], selected_object_name: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._summaries = list(summaries)
        self.setWindowTitle("Loaded Results")
        self.resize(760, 520)
        self._object_table = QTableWidget(len(summaries), 5)
        self._object_table.setHorizontalHeaderLabels(["Object", "Files", "Solved", "Needs Solve", "Invalid"])
        self._object_table.horizontalHeader().setStretchLastSection(False)
        self._object_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._object_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._object_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._object_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._object_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._object_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._object_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._object_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._object_table.setAlternatingRowColors(True)
        self._object_table.itemDoubleClicked.connect(lambda *_args: self.accept())
        self._object_table.itemSelectionChanged.connect(self._update_note_preview)

        total_files = sum(len(summary.files) for summary in summaries)
        description = QLabel(
            f"Loaded {total_files} FITS file(s) across {len(summaries)} object folder(s). "
            "Choose the active object for Generate and file inspection."
        )
        description.setWordWrap(True)

        note_preview_label = QLabel("Object Notes")
        self._object_note_preview = QPlainTextEdit()
        self._object_note_preview.setReadOnly(True)
        self._object_note_preview.setPlaceholderText("Select an object row to review solved and unsolved file notes.")
        self._object_note_preview.setMinimumHeight(140)

        selected_row = 0
        for row_index, summary in enumerate(summaries):
            values = [
                summary.object_name,
                str(len(summary.files)),
                str(summary.solved_count),
                str(summary.unsolved_count),
                str(summary.invalid_count),
            ]
            for column_index, value in enumerate(values):
                self._object_table.setItem(row_index, column_index, QTableWidgetItem(value))
            if selected_object_name and summary.object_name == selected_object_name:
                selected_row = row_index

        if summaries:
            self._object_table.selectRow(selected_row)
        self._update_note_preview()

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)
        use_selected_button = QPushButton("Use Selected Object")
        use_selected_button.setEnabled(bool(summaries))
        use_selected_button.clicked.connect(self.accept)
        button_row.addWidget(close_button)
        button_row.addWidget(use_selected_button)

        layout = QVBoxLayout()
        layout.addWidget(description)
        layout.addWidget(self._object_table, stretch=1)
        layout.addWidget(note_preview_label)
        layout.addWidget(self._object_note_preview)
        layout.addLayout(button_row)
        self.setLayout(layout)

    def selected_object_name(self) -> str | None:
        current_row = self._object_table.currentRow()
        if current_row < 0:
            return None
        item = self._object_table.item(current_row, 0)
        return item.text() if item is not None else None

    def _update_note_preview(self) -> None:
        current_row = self._object_table.currentRow()
        if current_row < 0 or current_row >= len(self._summaries):
            self._object_note_preview.clear()
            return
        self._object_note_preview.setPlainText(self._build_note_preview(self._summaries[current_row]))

    @staticmethod
    def _build_note_preview(summary: ObjectScanSummary) -> str:
        total_files = len(summary.files)
        lines = [
            f"{summary.object_name}: {summary.solved_count} solved, {summary.unsolved_count} needing solve, {summary.invalid_count} invalid across {total_files} file(s)."
        ]
        if summary.unsolved_count == 0 and summary.invalid_count == 0:
            lines.append("All loaded files are ready for Generate.")
        else:
            unsolved_files = [item for item in summary.files if item.wcs_status == WcsStatus.UNSOLVED]
            invalid_files = [item for item in summary.files if item.wcs_status == WcsStatus.INVALID]
            if unsolved_files:
                lines.append(f"Needs solve: {ScanResultsSummaryDialog._preview_file_names(unsolved_files)}")
            if invalid_files:
                lines.append(f"Invalid: {ScanResultsSummaryDialog._preview_file_names(invalid_files)}")

        reason_counts = Counter(reason for item in summary.files for reason in item.reasons if reason)
        if reason_counts:
            common_notes = ", ".join(f"{reason} ({count})" for reason, count in reason_counts.most_common(3))
            lines.append(f"Common notes: {common_notes}")
        else:
            lines.append("No file notes were recorded for this object.")
        return "\n".join(lines)

    @staticmethod
    def _preview_file_names(results: list[FileScanResult]) -> str:
        names = [item.path.name for item in results[:3]]
        remaining = len(results) - len(names)
        if remaining > 0:
            names.append(f"+{remaining} more")
        return ", ".join(names)


@dataclass(frozen=True, slots=True)
class AsteroidSequenceGroupSummary:
    key: str
    label: str
    filter_name: str
    exposure_text: str
    frame_count: int


class AsteroidSequenceGroupDialog(QDialog):
    def __init__(self, groups: list[AsteroidSequenceGroupSummary], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._groups = list(groups)
        self.setWindowTitle("Select Asteroid/Comet Groups")
        self.resize(760, 460)

        description = QLabel(
            "The selected folder contains multiple filter and/or exposure-time groups. Choose one or more groups to load into Asteroid/Comet Detection."
        )
        description.setWordWrap(True)

        self._group_table = QTableWidget(len(groups), 4)
        self._group_table.setHorizontalHeaderLabels(["Group", "Filter", "Exposure", "Frames"])
        self._group_table.horizontalHeader().setStretchLastSection(False)
        self._group_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._group_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._group_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._group_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._group_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._group_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._group_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._group_table.setAlternatingRowColors(True)
        self._group_table.itemDoubleClicked.connect(lambda *_args: self.accept())

        for row_index, group in enumerate(groups):
            items = [
                QTableWidgetItem(group.label),
                QTableWidgetItem(group.filter_name),
                QTableWidgetItem(group.exposure_text),
                QTableWidgetItem(str(group.frame_count)),
            ]
            for item in items:
                item.setData(Qt.ItemDataRole.UserRole, group.key)
            for column_index, item in enumerate(items):
                self._group_table.setItem(row_index, column_index, item)

        if groups:
            self._group_table.selectRow(0)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        load_button = QPushButton("Load Selected Groups")
        load_button.setEnabled(bool(groups))
        load_button.clicked.connect(self.accept)
        button_row.addWidget(cancel_button)
        button_row.addWidget(load_button)

        layout = QVBoxLayout()
        layout.addWidget(description)
        layout.addWidget(self._group_table, stretch=1)
        layout.addLayout(button_row)
        self.setLayout(layout)

    def selected_group_keys(self) -> list[str]:
        keys: list[str] = []
        for item in self._group_table.selectedItems():
            if item.column() != 0:
                continue
            key = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(key, str) and key not in keys:
                keys.append(key)
        return keys


class SyntheticTrackingPreviewDialog(QDialog):
    def __init__(
        self,
        *,
        detection_name: str,
        display: AnnotatedImageDisplay,
        result: SyntheticTrackingResult,
        known_object_overlays: list[ImageOverlay] | None = None,
        render_settings: AnnotatedImageRenderSettings | None = None,
        show_markers: bool = True,
        show_labels: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Synthetic Tracking - {detection_name}")
        self.resize(860, 820)
        self._detection_name = detection_name
        self._auto_result = replace(result)
        self._result = replace(result)
        self._known_object_overlays = list(known_object_overlays or [])
        self._render_settings = render_settings

        self._summary_label = QLabel(result.summary_text)
        self._summary_label.setWordWrap(True)

        self._backend_status_label = QLabel(result.compute_backend_summary or "")
        self._backend_status_label.setWordWrap(True)
        self._backend_status_label.setVisible(bool(result.compute_backend_summary))

        self._warmup_status_label = QLabel(result.gpu_warmup_summary or "")
        self._warmup_status_label.setWordWrap(True)
        self._warmup_status_label.setVisible(bool(result.gpu_warmup_summary))

        self._manual_help_label = QLabel(
            "Ctrl+click the object in the preview to override Measured Peak and recompute offset, SNR, peak value, and local flux."
        )
        self._manual_help_label.setWordWrap(True)

        self._image_view = AnnotatedImageView(self)
        self._image_view.imagePressed.connect(self._handle_image_pressed)
        self._display = display

        self._details_output = QPlainTextEdit()
        self._details_output.setReadOnly(True)

        self._overlay_visibility_actions: dict[tuple[str, str], QAction] = {}
        self._overlay_menu_button = QToolButton(self)
        self._overlay_menu_button.setText("Overlays")
        self._overlay_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._overlay_menu = QMenu(self._overlay_menu_button)
        self._overlay_menu_button.setMenu(self._overlay_menu)
        self._create_overlay_visibility_actions(show_markers=show_markers, show_labels=show_labels)

        self._reset_manual_peak_button = QPushButton("Reset to Auto")
        self._reset_manual_peak_button.clicked.connect(self._reset_manual_peak)

        self._export_image_button = QPushButton("Export Image...")
        self._export_image_button.clicked.connect(self._export_image)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)

        button_row = QHBoxLayout()
        button_row.addWidget(self._overlay_menu_button)
        button_row.addWidget(self._reset_manual_peak_button)
        button_row.addStretch(1)
        button_row.addWidget(self._export_image_button)
        button_row.addWidget(close_button)

        self._refresh_preview(reset_view=True)

        layout = QVBoxLayout()
        layout.addWidget(self._summary_label)
        layout.addWidget(self._backend_status_label)
        layout.addWidget(self._warmup_status_label)
        layout.addWidget(self._manual_help_label)
        layout.addWidget(self._image_view, stretch=1)
        layout.addWidget(self._details_output)
        layout.addLayout(button_row)
        self.setLayout(layout)

    def _create_overlay_visibility_actions(self, *, show_markers: bool, show_labels: bool) -> None:
        self._overlay_menu.clear()
        self._overlay_visibility_actions.clear()
        sections: list[tuple[str, str, bool, bool]] = [
            ("Predicted center", "predicted", show_markers, show_labels),
            ("Auto peak", "auto", show_markers, show_labels),
            ("Measured peak", "measured", show_markers, show_labels),
        ]
        if self._known_object_overlays:
            sections.append(("Known objects", "known", False, False))
        for index, (label, overlay_key, default_marker, default_label) in enumerate(sections):
            if index > 0:
                self._overlay_menu.addSeparator()
            for visibility_key, checked in (("marker", default_marker), ("label", default_label)):
                action = QAction(f"{label} {visibility_key.title()}", self._overlay_menu)
                action.setCheckable(True)
                action.setChecked(checked)
                action.toggled.connect(self._handle_overlay_visibility_changed)
                self._overlay_menu.addAction(action)
                self._overlay_visibility_actions[(overlay_key, visibility_key)] = action

    def _overlay_visibility_checked(self, overlay_key: str, visibility_key: str) -> bool:
        action = self._overlay_visibility_actions.get((overlay_key, visibility_key))
        return True if action is None else action.isChecked()

    def _sync_overlay_visibility_action_enabled_states(self) -> None:
        for action in self._overlay_visibility_actions.values():
            action.setEnabled(True)

    def _format_motion_text(self, result: SyntheticTrackingResult) -> str:
        motion_text = f"{self._format_optional_float(result.motion_px_per_hour, 2)} px/h"
        if result.motion_arcsec_per_hour is not None:
            motion_text += f" ({self._format_optional_float(result.motion_arcsec_per_hour, 2)} arcsec/h)"
        return motion_text

    def _detail_text(self, result: SyntheticTrackingResult) -> str:
        detail_lines = [
            f"Measurement mode: {'Manual override' if self._has_manual_override() else 'Auto'}",
            f"Used frames: {result.used_frame_count}",
            f"Skipped frames: {result.skipped_frame_count}",
            f"Stacking motion: {self._format_motion_text(result)}",
            f"Stacking angle: {self._format_optional_float(result.motion_angle_deg, 1)} deg",
            f"Stacked SNR: {self._format_optional_float(result.local_snr, 2)}",
            f"Center offset: {self._format_optional_float(result.match_offset_px, 2)} px",
            f"Peak value: {self._format_optional_float(result.local_peak_value, 1)}",
            f"Local flux: {self._format_optional_float(result.local_flux, 1)}",
            "",
            "Frames:",
        ]
        for contribution in result.frame_contributions:
            timestamp_text = contribution.observation_time.isoformat() if contribution.observation_time is not None else "-"
            if contribution.used:
                detail_lines.append(
                    f"{contribution.source_path.name} | {timestamp_text} | predicted x={self._format_optional_float(contribution.predicted_x, 2)}, y={self._format_optional_float(contribution.predicted_y, 2)}"
                )
            else:
                detail_lines.append(
                    f"{contribution.source_path.name} | {timestamp_text} | skipped: {contribution.reason or '-'}"
                )
        return "\n".join(detail_lines)

    def _format_optional_float(self, value: float | None, decimals: int) -> str:
        if value is None:
            return "-"
        return f"{float(value):.{decimals}f}"

    def _has_manual_override(self) -> bool:
        return (
            self._result.measured_x != self._auto_result.measured_x
            or self._result.measured_y != self._auto_result.measured_y
            or self._result.local_snr != self._auto_result.local_snr
            or self._result.match_offset_px != self._auto_result.match_offset_px
        )

    def _build_overlays(self) -> list[ImageOverlay]:
        overlays: list[ImageOverlay] = []
        if not self._result.full_frame_mode:
            overlays.append(
                ImageOverlay(
                    source_id="synthetic:center",
                    name="Predicted center",
                    x=self._result.center_x,
                    y=self._result.center_y,
                    aperture_radius=5.0,
                    annulus_inner_radius=5.0,
                    annulus_outer_radius=5.0,
                    color="#22c55e",
                    show_annulus=False,
                    show_marker=self._overlay_visibility_checked("predicted", "marker"),
                    show_label=self._overlay_visibility_checked("predicted", "label"),
                    marker_style="cross",
                )
            )
        if self._has_manual_override() and self._auto_result.measured_x is not None and self._auto_result.measured_y is not None:
            overlays.append(
                ImageOverlay(
                    source_id="synthetic:auto_peak",
                    name="Auto peak",
                    x=self._auto_result.measured_x,
                    y=self._auto_result.measured_y,
                    aperture_radius=3.0,
                    annulus_inner_radius=3.0,
                    annulus_outer_radius=3.0,
                    color="#38bdf8",
                    show_annulus=False,
                    show_marker=self._overlay_visibility_checked("auto", "marker"),
                    show_label=self._overlay_visibility_checked("auto", "label"),
                    marker_style="circle",
                    show_center_dot=False,
                )
            )
        if self._result.measured_x is not None and self._result.measured_y is not None:
            overlays.append(
                ImageOverlay(
                    source_id="synthetic:peak",
                    name="Manual peak" if self._has_manual_override() else "Measured peak",
                    x=self._result.measured_x,
                    y=self._result.measured_y,
                    aperture_radius=3.0,
                    annulus_inner_radius=3.0,
                    annulus_outer_radius=3.0,
                    color="#f59e0b" if self._has_manual_override() else "#38bdf8",
                    show_annulus=False,
                    show_marker=self._overlay_visibility_checked("measured", "marker"),
                    show_label=self._overlay_visibility_checked("measured", "label"),
                    marker_style="circle",
                    show_center_dot=False,
                )
            )
        if self._known_object_overlays:
            overlays.extend(
                replace(
                    overlay,
                    show_marker=self._overlay_visibility_checked("known", "marker"),
                    show_label=self._overlay_visibility_checked("known", "label"),
                )
                for overlay in self._known_object_overlays
            )
        return overlays

    def _handle_overlay_visibility_changed(self, _checked: bool) -> None:
        self._refresh_preview(reset_view=False)

    def _refresh_preview(self, *, reset_view: bool) -> None:
        self._summary_label.setText(self._result.summary_text)
        self._backend_status_label.setText(self._result.compute_backend_summary or "")
        self._backend_status_label.setVisible(bool(self._result.compute_backend_summary))
        self._warmup_status_label.setText(self._result.gpu_warmup_summary or "")
        self._warmup_status_label.setVisible(bool(self._result.gpu_warmup_summary))
        self._details_output.setPlainText(self._detail_text(self._result))
        self._reset_manual_peak_button.setEnabled(self._has_manual_override())
        self._sync_overlay_visibility_action_enabled_states()
        self._image_view.set_content(
            self._display,
            overlays=self._build_overlays(),
            grid_overlays=[],
            editor_enabled=False,
            reset_view=reset_view,
            render_settings=self._render_settings,
        )
        if reset_view and not self._result.full_frame_mode:
            self._image_view.focus_on(self._result.center_x, self._result.center_y, minimum_zoom_scale=4.0)

    def _handle_image_pressed(self, image_x: float, image_y: float, button: object, modifiers: object) -> None:
        modifier_flags = int(getattr(modifiers, "value", 0))
        control_flag = int(getattr(Qt.KeyboardModifier.ControlModifier, "value", 0))
        if button != Qt.MouseButton.LeftButton or not bool(modifier_flags & control_flag):
            return
        measured_x, measured_y, match_offset_px, local_snr, local_peak_value, local_flux = measure_synthetic_tracking_peak(
            self._result.stacked_data,
            self._result.center_x,
            self._result.center_y,
            anchor_x=float(image_x),
            anchor_y=float(image_y),
            search_radius=2,
        )
        self._result = replace(
            self._result,
            measured_x=measured_x,
            measured_y=measured_y,
            match_offset_px=match_offset_px,
            local_snr=local_snr,
            local_peak_value=local_peak_value,
            local_flux=local_flux,
            summary_text=format_synthetic_tracking_summary(
                used_frame_count=self._result.used_frame_count,
                total_frame_count=len(self._result.frame_contributions),
                local_snr=local_snr,
                match_offset_px=match_offset_px,
                motion_px_per_hour=self._result.motion_px_per_hour,
                motion_arcsec_per_hour=self._result.motion_arcsec_per_hour,
                motion_angle_deg=self._result.motion_angle_deg,
            ),
        )
        self._refresh_preview(reset_view=False)

    def _reset_manual_peak(self) -> None:
        self._result = replace(self._auto_result)
        self._refresh_preview(reset_view=False)

    def _export_image(self) -> None:
        suggested_path = self._result.reference_path.with_name(f"{self._result.reference_path.stem}_synthetic_tracking_preview.png")
        selected, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Synthetic Tracking Preview",
            str(suggested_path),
            "PNG Files (*.png);;JPEG Files (*.jpg *.jpeg);;BMP Files (*.bmp);;Raw FITS Files (*.fit *.fits)",
        )
        if not selected:
            return
        output_path = Path(selected).expanduser()
        selected_filter_text = str(selected_filter or "")
        raw_fits_export = "fits" in selected_filter_text.lower() or output_path.suffix.lower() in {".fit", ".fits"}
        if raw_fits_export:
            if output_path.suffix.lower() not in {".fit", ".fits"}:
                output_path = output_path.with_suffix(".fits")
        elif output_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp"}:
            output_path = output_path.with_suffix(".png")
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Export Synthetic Tracking Preview", str(exc))
            return
        if raw_fits_export:
            self._export_raw_stack(output_path)
            return
        exported_image = self._image_view.capture_full_resolution_image()
        if exported_image is None:
            QMessageBox.warning(self, "Export Synthetic Tracking Preview", "Could not render the full-resolution image.")
            return
        if exported_image.save(str(output_path)):
            QMessageBox.information(self, "Export Synthetic Tracking Preview", f"Saved preview image to {output_path}.")
            return
        QMessageBox.warning(self, "Export Synthetic Tracking Preview", "Could not save the preview image.")

    def _export_raw_stack(self, output_path: Path) -> None:
        linear_stack = self._result.linear_stacked_data if self._result.linear_stacked_data is not None else self._result.stacked_data
        header = fits.Header()
        header["IMAGETYP"] = ("SYNTH_TRK", "Synthetic tracking raw stack")
        header["SRCFILE"] = (self._result.reference_path.name[:68], "Reference frame used for the stack")
        header["FULLFRM"] = (bool(self._result.full_frame_mode), "True when the stack used full-frame mode")
        header["CROPRAD"] = (int(self._result.crop_radius), "Synthetic tracking crop radius in pixels")
        header["USEDFRMS"] = (int(self._result.used_frame_count), "Frames used in the synthetic stack")
        header["SKIPFRMS"] = (int(self._result.skipped_frame_count), "Frames skipped while stacking")
        if self._result.motion_px_per_hour is not None:
            header["MOTPXPH"] = (float(self._result.motion_px_per_hour), "Tracking motion in pixels per hour")
        if self._result.motion_arcsec_per_hour is not None:
            header["MOTARCPH"] = (float(self._result.motion_arcsec_per_hour), "Tracking motion in arcsec per hour")
        if self._result.motion_angle_deg is not None:
            header["MOTANG"] = (float(self._result.motion_angle_deg), "Tracking angle in image-plane degrees")
        if self._result.center_x is not None:
            header["CTRX"] = (float(self._result.center_x), "Predicted stack center X in pixels")
        if self._result.center_y is not None:
            header["CTRY"] = (float(self._result.center_y), "Predicted stack center Y in pixels")
        try:
            fits.PrimaryHDU(data=np.asarray(linear_stack, dtype=np.float32), header=header).writeto(output_path, overwrite=True)
        except Exception as exc:
            QMessageBox.warning(self, "Export Synthetic Tracking Preview", f"Could not save the raw stack: {exc}")
            return
        QMessageBox.information(
            self,
            "Export Synthetic Tracking Preview",
            f"Saved linear raw synthetic-tracking stack to {output_path}. Display stretch, preview background normalization, and curve adjustments were not applied.",
        )


@dataclass(frozen=True, slots=True)
class AdvancedSyntheticTrackingOptions:
    full_frame_mode: bool
    crop_radius: int
    motion_px_per_hour: float
    motion_arcsec_per_hour: float | None
    motion_angle_deg: float
    integration_mode: str
    weight_mode: str
    rejection_mode: str


class AdvancedSyntheticTrackingDialog(QDialog):
    def __init__(
        self,
        *,
        detection_name: str,
        default_motion_px_per_hour: float,
        default_motion_arcsec_per_hour: float | None,
        default_motion_angle_deg: float,
        default_crop_radius: int,
        default_integration_mode: str,
        default_weight_mode: str,
        default_rejection_mode: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Advanced Synthetic Track - {detection_name}")
        self.resize(480, 280)
        self._motion_sync_in_progress = False
        self._pixel_scale_arcsec_per_pixel = (
            None
            if default_motion_arcsec_per_hour is None or abs(float(default_motion_px_per_hour)) <= 1.0e-9
            else float(default_motion_arcsec_per_hour) / abs(float(default_motion_px_per_hour))
        )

        self._description_label = QLabel(self)
        self._description_label.setWordWrap(True)

        self._stack_scope_input = QComboBox(self)
        self._stack_scope_input.addItem("Object-centered crop", "crop")
        self._stack_scope_input.addItem("Full image (manual motion)", "full_frame")
        self._stack_scope_input.currentIndexChanged.connect(self._sync_stack_scope_controls)

        self._crop_radius_label = QLabel("Track Crop Radius", self)
        self._crop_radius_input = QSpinBox(self)
        self._crop_radius_input.setRange(4, 65535)
        self._crop_radius_input.setSuffix(" px")
        self._crop_radius_input.setValue(int(default_crop_radius))

        self._motion_px_label = QLabel("Motion (px/h)", self)

        self._motion_px_input = QDoubleSpinBox(self)
        self._motion_px_input.setRange(-100_000.0, 100_000.0)
        self._motion_px_input.setDecimals(3)
        self._motion_px_input.setSuffix(" px/h")
        self._motion_px_input.setValue(float(default_motion_px_per_hour))
        self._motion_px_input.valueChanged.connect(self._sync_arcsec_from_px)

        self._motion_arcsec_label = QLabel("Motion (arcsec/h)", self)
        self._motion_arcsec_input = QDoubleSpinBox(self)
        self._motion_arcsec_input.setRange(-1_000_000.0, 1_000_000.0)
        self._motion_arcsec_input.setDecimals(3)
        self._motion_arcsec_input.setSuffix(" arcsec/h")
        if default_motion_arcsec_per_hour is None:
            self._motion_arcsec_input.setValue(0.0)
            self._motion_arcsec_input.setEnabled(False)
            self._motion_arcsec_input.setToolTip("Arcsec/hour is unavailable until the current image has a usable pixel scale.")
        else:
            self._motion_arcsec_input.setValue(float(default_motion_arcsec_per_hour))
            self._motion_arcsec_input.valueChanged.connect(self._sync_px_from_arcsec)

        self._motion_angle_label = QLabel("Angle", self)
        self._motion_angle_input = QDoubleSpinBox(self)
        self._motion_angle_input.setRange(-360.0, 360.0)
        self._motion_angle_input.setDecimals(2)
        self._motion_angle_input.setSuffix(" deg")
        self._motion_angle_input.setValue(float(default_motion_angle_deg))

        self._integration_mode_label = QLabel("Integration", self)
        self._integration_mode_input = QComboBox(self)
        self._integration_mode_input.addItem("Average", "average")
        self._integration_mode_input.addItem("Mean", "mean")
        self._integration_mode_input.addItem("Min", "min")
        self._integration_mode_input.addItem("Max", "max")
        integration_mode_index = self._integration_mode_input.findData(default_integration_mode)
        self._integration_mode_input.setCurrentIndex(integration_mode_index if integration_mode_index >= 0 else 0)

        self._weight_mode_label = QLabel("Weights", self)
        self._weight_mode_input = QComboBox(self)
        self._weight_mode_input.addItem("PSF signal weight", "psf_signal_weight")
        self._weight_mode_input.addItem("PSF SNR", "psf_snr")
        self._weight_mode_input.addItem("SNR", "snr")
        self._weight_mode_input.addItem("Average signal strength", "average_signal_strength")
        weight_mode_index = self._weight_mode_input.findData(default_weight_mode)
        self._weight_mode_input.setCurrentIndex(weight_mode_index if weight_mode_index >= 0 else 0)

        self._rejection_mode_label = QLabel("Rejection", self)
        self._rejection_mode_input = QComboBox(self)
        self._rejection_mode_input.addItem("No rejection", "no_rejection")
        self._rejection_mode_input.addItem("Min/Max", "min_max")
        self._rejection_mode_input.addItem("Sigma clipping", "sigma_clipping")
        self._rejection_mode_input.addItem("Winsorized sigma clipping", "winsorized_sigma_clipping")
        self._rejection_mode_input.addItem("Averaged sigma clipping", "averaged_sigma_clipping")
        rejection_mode_index = self._rejection_mode_input.findData(default_rejection_mode)
        self._rejection_mode_input.setCurrentIndex(rejection_mode_index if rejection_mode_index >= 0 else 0)
        self._rejection_mode_input.currentIndexChanged.connect(self._sync_stack_scope_controls)

        form_layout = QFormLayout()
        form_layout.addRow("Stack Scope", self._stack_scope_input)
        form_layout.addRow(self._crop_radius_label, self._crop_radius_input)
        form_layout.addRow(self._motion_px_label, self._motion_px_input)
        form_layout.addRow(self._motion_arcsec_label, self._motion_arcsec_input)
        form_layout.addRow(self._motion_angle_label, self._motion_angle_input)
        form_layout.addRow(self._integration_mode_label, self._integration_mode_input)
        form_layout.addRow(self._weight_mode_label, self._weight_mode_input)
        form_layout.addRow(self._rejection_mode_label, self._rejection_mode_input)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok, self)
        button_box.button(QDialogButtonBox.StandardButton.Ok).setText("Run Synthetic Track")
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(self._description_label)
        layout.addLayout(form_layout)
        layout.addWidget(button_box)
        self.setLayout(layout)
        self._sync_stack_scope_controls()

    def _sync_stack_scope_controls(self) -> None:
        full_frame_mode = str(self._stack_scope_input.currentData() or "crop") == "full_frame"
        self._set_row_visibility(self._crop_radius_label, self._crop_radius_input, not full_frame_mode)
        self._set_row_visibility(self._motion_px_label, self._motion_px_input, full_frame_mode)
        self._set_row_visibility(self._motion_arcsec_label, self._motion_arcsec_input, full_frame_mode)
        self._set_row_visibility(self._motion_angle_label, self._motion_angle_input, full_frame_mode)
        if full_frame_mode:
            description = (
                "Build a single full-image synthetic-track stack using the manual motion below. This uses much more memory than object-centered crop mode, especially with many large frames."
            )
            if str(self._rejection_mode_input.currentData() or "no_rejection") != "no_rejection":
                description += " GPU acceleration currently applies only to full-frame No rejection stacks, so this selection will use CPU fallback."
            else:
                description += " If CuPy is installed, full-frame No rejection stacks can use GPU acceleration."
            self._description_label.setText(description)
        else:
            self._description_label.setText(
                "Build an object-centered synthetic-track stack around the predicted target positions. Use the crop radius below to control how much surrounding image area is included."
            )


    @staticmethod
    def _set_row_visibility(label: QWidget, widget: QWidget, visible: bool) -> None:
        label.setVisible(visible)
        widget.setVisible(visible)

    def _sync_arcsec_from_px(self, value: float) -> None:
        if self._motion_sync_in_progress or self._pixel_scale_arcsec_per_pixel is None or not self._motion_arcsec_input.isEnabled():
            return
        self._motion_sync_in_progress = True
        try:
            self._motion_arcsec_input.setValue(float(value) * self._pixel_scale_arcsec_per_pixel)
        finally:
            self._motion_sync_in_progress = False

    def _sync_px_from_arcsec(self, value: float) -> None:
        if self._motion_sync_in_progress or self._pixel_scale_arcsec_per_pixel is None or abs(self._pixel_scale_arcsec_per_pixel) <= 1.0e-12:
            return
        self._motion_sync_in_progress = True
        try:
            self._motion_px_input.setValue(float(value) / self._pixel_scale_arcsec_per_pixel)
        finally:
            self._motion_sync_in_progress = False

    def build_options(self) -> AdvancedSyntheticTrackingOptions:
        full_frame_mode = str(self._stack_scope_input.currentData() or "crop") == "full_frame"
        return AdvancedSyntheticTrackingOptions(
            full_frame_mode=full_frame_mode,
            crop_radius=int(self._crop_radius_input.value()),
            motion_px_per_hour=(float(self._motion_px_input.value()) if full_frame_mode else 0.0),
            motion_arcsec_per_hour=(
                float(self._motion_arcsec_input.value())
                if full_frame_mode and self._motion_arcsec_input.isEnabled()
                else None
            ),
            motion_angle_deg=(float(self._motion_angle_input.value()) if full_frame_mode else 0.0),
            integration_mode=str(self._integration_mode_input.currentData() or "average"),
            weight_mode=str(self._weight_mode_input.currentData() or "psf_signal_weight"),
            rejection_mode=str(self._rejection_mode_input.currentData() or "no_rejection"),
        )


class MovingObjectTrajectoryDialog(QDialog):
    def __init__(
        self,
        *,
        object_label: str,
        candidate: MovingObjectCandidate,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Trajectory - {object_label}")
        self.resize(1120, 900)
        self._candidate = candidate

        self.setStyleSheet(
            "QDialog { background-color: #060816; color: #e7eefc; }"
            "QLabel { color: #e7eefc; }"
            "QPushButton { background-color: #10182d; color: #f3f7ff; border: 1px solid #2d436f; padding: 5px 12px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #182341; }"
            "QTableWidget { background-color: #09101f; alternate-background-color: #0d1527; color: #edf4ff; gridline-color: #24314f; selection-background-color: #23406d; }"
            "QHeaderView::section { background-color: #101a31; color: #cfe0ff; border: 0px; padding: 4px; }"
        )

        summary_label = QLabel(self._summary_text(object_label, candidate), self)
        summary_label.setWordWrap(True)
        summary_label.setStyleSheet(
            "background-color: rgba(14, 22, 38, 0.94);"
            "border: 1px solid #213355;"
            "border-radius: 6px;"
            "padding: 8px 10px;"
            "color: #edf4ff;"
        )

        self._figure = Figure(figsize=(8.8, 6.8), tight_layout=True)
        self._canvas = FigureCanvasQTAgg(self._figure)
        self._toolbar = NavigationToolbar2QT(self._canvas, self)

        self._table = QTableWidget(len(candidate.frame_detections), 8, self)
        self._table.setHorizontalHeaderLabels(["Frame", "UTC", "x", "y", "RA", "Dec", "Residual", "Peak"])
        table_header = self._table.horizontalHeader()
        for column_index, width in ((0, 70), (1, 220), (2, 80), (3, 80), (4, 120), (5, 120), (6, 80), (7, 80)):
            table_header.setSectionResizeMode(column_index, QHeaderView.ResizeMode.Interactive)
            self._table.setColumnWidth(column_index, width)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)

        close_button = QPushButton("Close", self)
        close_button.clicked.connect(self.close)

        layout = QVBoxLayout()
        layout.addWidget(summary_label)
        layout.addWidget(self._toolbar)
        layout.addWidget(self._canvas, stretch=1)
        layout.addWidget(self._table)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)
        self.setLayout(layout)

        self._populate_table()
        self._draw_plots()

    def _summary_text(self, object_label: str, candidate: MovingObjectCandidate) -> str:
        motion_text = f"{candidate.motion_px_per_hour:.2f} px/h"
        if candidate.motion_arcsec_per_hour is not None:
            motion_text += f" | {candidate.motion_arcsec_per_hour:.2f} arcsec/h"
        return (
            f"{object_label} | {len(candidate.frame_detections)} detection(s) | Motion: {motion_text} | "
            f"Deflection RMS: {candidate.fit_rms_px:.2f} px | Max deflection: {candidate.max_deflection_px:.2f} px | Avg residual score: {candidate.average_snr:.2f}"
        )

    def _populate_table(self) -> None:
        for row_index, detection in enumerate(self._candidate.frame_detections):
            items = [
                QTableWidgetItem(f"F{detection.frame_index + 1}"),
                QTableWidgetItem(detection.observation_time.isoformat()),
                QTableWidgetItem(f"{detection.x:.2f}"),
                QTableWidgetItem(f"{detection.y:.2f}"),
                QTableWidgetItem(self._format_optional_float(detection.ra_deg, precision=6)),
                QTableWidgetItem(self._format_optional_float(detection.dec_deg, precision=6)),
                QTableWidgetItem(f"{detection.local_snr:.2f}"),
                QTableWidgetItem(f"{detection.peak_value:.1f}"),
            ]
            for column_index, item in enumerate(items):
                self._table.setItem(row_index, column_index, item)

    def _draw_plots(self) -> None:
        self._figure.clear()
        self._figure.patch.set_facecolor("#050914")
        axes = self._figure.subplots(2, 2)
        ax_image = axes[0][0]
        ax_sky = axes[0][1]
        ax_time = axes[1][0]
        ax_snr = axes[1][1]

        for ax in (ax_image, ax_sky, ax_time, ax_snr):
            KnownObjectOrbit3DDialog._apply_space_theme(ax)

        detections = list(self._candidate.frame_detections)
        if not detections:
            ax_image.text(0.5, 0.5, "No detections available.", ha="center", va="center", transform=ax_image.transAxes)
            ax_sky.axis("off")
            ax_time.axis("off")
            ax_snr.axis("off")
            self._canvas.draw_idle()
            return

        first_time = detections[0].observation_time
        elapsed_minutes = [(detection.observation_time - first_time).total_seconds() / 60.0 for detection in detections]
        x_values = [detection.x for detection in detections]
        y_values = [detection.y for detection in detections]
        snr_values = [detection.local_snr for detection in detections]
        frame_labels = [f"F{detection.frame_index + 1}" for detection in detections]

        ax_image.plot(x_values, y_values, marker="o", color="#f59e0b", linewidth=1.8, label="Measured image path")
        ax_image.plot(
            [self._candidate.start_x, self._candidate.end_x],
            [self._candidate.start_y, self._candidate.end_y],
            linestyle="--",
            color="#7dd3fc",
            linewidth=1.4,
            alpha=0.9,
            label="Linear-motion fit",
        )
        for index in self._endpoint_indices(len(frame_labels)):
            x_value = x_values[index]
            y_value = y_values[index]
            label = frame_labels[index]
            ax_image.annotate(label, (x_value, y_value), textcoords="offset points", xytext=(5, 5), fontsize=8, color="#eef5ff")
        ax_image.set_title("Image-plane trajectory")
        ax_image.set_xlabel("x (px)")
        ax_image.set_ylabel("y (px)")
        ax_image.set_aspect("equal", adjustable="datalim")
        self._set_equal_span_limits(ax_image, x_values, y_values, invert_y=True)
        image_legend = ax_image.legend(loc="best")
        KnownObjectOrbit3DDialog._style_space_legend(image_legend)

        sky_points = [
            (detection.ra_deg, detection.dec_deg, label)
            for detection, label in zip(detections, frame_labels)
            if detection.ra_deg is not None and detection.dec_deg is not None
        ]
        if sky_points:
            reference_ra_deg, reference_dec_deg, _reference_label = sky_points[0]
            cos_dec = math.cos(math.radians(reference_dec_deg))
            ra_offsets_arcsec = [((ra_deg - reference_ra_deg) * cos_dec * 3600.0) for ra_deg, _dec_deg, _label in sky_points]
            dec_offsets_arcsec = [((dec_deg - reference_dec_deg) * 3600.0) for _ra_deg, dec_deg, _label in sky_points]
            sky_labels = [label for _ra_deg, _dec_deg, label in sky_points]
            ax_sky.plot(ra_offsets_arcsec, dec_offsets_arcsec, marker="o", color="#38bdf8", linewidth=1.8, label="Measured sky path")
            for index in self._endpoint_indices(len(sky_labels)):
                ax_sky.annotate(
                    sky_labels[index],
                    (ra_offsets_arcsec[index], dec_offsets_arcsec[index]),
                    textcoords="offset points",
                    xytext=(5, 5),
                    fontsize=8,
                    color="#eef5ff",
                )
            ax_sky.set_title("Sky-plane path")
            ax_sky.set_xlabel("dRA cos(Dec) (arcsec)")
            ax_sky.set_ylabel("dDec (arcsec)")
            ax_sky.set_aspect("equal", adjustable="datalim")
            self._set_equal_span_limits(ax_sky, ra_offsets_arcsec, dec_offsets_arcsec)
            sky_legend = ax_sky.legend(loc="best")
            KnownObjectOrbit3DDialog._style_space_legend(sky_legend)
        else:
            ax_sky.set_title("Sky-plane path")
            ax_sky.text(0.5, 0.5, "Sky coordinates unavailable for this tracklet.", ha="center", va="center", transform=ax_sky.transAxes)
            ax_sky.set_xticks([])
            ax_sky.set_yticks([])

        ax_time.plot(elapsed_minutes, x_values, marker="o", color="#f59e0b", linewidth=1.6, label="x")
        ax_time.plot(elapsed_minutes, y_values, marker="o", color="#8b5cf6", linewidth=1.6, label="y")
        ax_time.set_title("Pixel position vs time")
        ax_time.set_xlabel("Elapsed time (min)")
        ax_time.set_ylabel("Position (px)")
        time_legend = ax_time.legend(loc="best")
        KnownObjectOrbit3DDialog._style_space_legend(time_legend)

        ax_snr.plot(elapsed_minutes, snr_values, marker="o", color="#10b981", linewidth=1.6)
        if np.isfinite(np.asarray(snr_values, dtype=float)).any():
            ax_snr.axhline(float(np.nanmedian(snr_values)), color="#6ee7b7", linestyle="--", linewidth=1.0, alpha=0.8)
        ax_snr.set_title("Residual score vs time")
        ax_snr.set_xlabel("Elapsed time (min)")
        ax_snr.set_ylabel("Residual score")

        for ax in (ax_image, ax_sky, ax_time, ax_snr):
            KnownObjectOrbit3DDialog._finalize_space_axes(ax)

        self._canvas.draw_idle()

    @staticmethod
    def _endpoint_indices(count: int) -> tuple[int, ...]:
        if count <= 0:
            return ()
        if count == 1:
            return (0,)
        return (0, count - 1)

    @staticmethod
    def _set_equal_span_limits(ax, x_values: list[float], y_values: list[float], *, invert_y: bool = False) -> None:
        finite_points = [
            (float(x_value), float(y_value))
            for x_value, y_value in zip(x_values, y_values)
            if np.isfinite(x_value) and np.isfinite(y_value)
        ]
        if not finite_points:
            return
        x_min = min(point[0] for point in finite_points)
        x_max = max(point[0] for point in finite_points)
        y_min = min(point[1] for point in finite_points)
        y_max = max(point[1] for point in finite_points)
        span = max(x_max - x_min, y_max - y_min, 1.0)
        half_span = (0.5 * span) + max(0.5, span * 0.08)
        x_center = 0.5 * (x_min + x_max)
        y_center = 0.5 * (y_min + y_max)
        ax.set_xlim(x_center - half_span, x_center + half_span)
        if invert_y:
            ax.set_ylim(y_center + half_span, y_center - half_span)
        else:
            ax.set_ylim(y_center - half_span, y_center + half_span)

    @staticmethod
    def _format_optional_float(value: float | None, *, precision: int) -> str:
        return "-" if value is None else f"{value:.{precision}f}"


class KnownObjectTrajectoryDialog(QDialog):
    def __init__(
        self,
        *,
        detection: SolarSystemDetection,
        frame_measurements: tuple[SolarSystemFrameMeasurement, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        object_label = detection.name or detection.designation or "Known Object"
        self.setWindowTitle(f"Trajectory - {object_label}")
        self.resize(1120, 900)
        self._detection = detection
        self._frame_measurements = frame_measurements

        summary_label = QLabel(self._summary_text(), self)
        summary_label.setWordWrap(True)

        self._figure = Figure(figsize=(8.8, 6.8), tight_layout=True)
        self._canvas = FigureCanvasQTAgg(self._figure)
        self._toolbar = NavigationToolbar2QT(self._canvas, self)

        self._table = QTableWidget(len(frame_measurements), 10, self)
        self._table.setHorizontalHeaderLabels(["Frame", "UTC", "Pred x", "Pred y", "Meas x", "Meas y", "RA", "Dec", "Offset", "SNR"])
        table_header = self._table.horizontalHeader()
        for column_index, width in ((0, 70), (1, 220), (2, 80), (3, 80), (4, 80), (5, 80), (6, 120), (7, 120), (8, 80), (9, 80)):
            table_header.setSectionResizeMode(column_index, QHeaderView.ResizeMode.Interactive)
            self._table.setColumnWidth(column_index, width)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        close_button = QPushButton("Close", self)
        close_button.clicked.connect(self.close)

        layout = QVBoxLayout()
        layout.addWidget(summary_label)
        layout.addWidget(self._toolbar)
        layout.addWidget(self._canvas, stretch=1)
        layout.addWidget(self._table)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)
        self.setLayout(layout)

        self._populate_table()
        self._draw_plots()

    def _summary_text(self) -> str:
        object_label = self._detection.name or self._detection.designation or "Known Object"
        predicted_motion = "-" if self._detection.motion_rate_arcsec_per_hour is None else f"{self._detection.motion_rate_arcsec_per_hour:.2f} arcsec/h"
        matched_count = sum(1 for measurement in self._frame_measurements if measurement.measured_x is not None and measurement.measured_y is not None)
        return (
            f"{object_label} | Frames: {len(self._frame_measurements)} | Measured matches: {matched_count} | "
            f"Pred Mag: {self._format_optional_float(self._detection.predicted_magnitude, precision=2)} | Motion: {predicted_motion}"
        )

    def _populate_table(self) -> None:
        for row_index, measurement in enumerate(self._frame_measurements):
            items = [
                QTableWidgetItem(f"F{row_index + 1}"),
                QTableWidgetItem(measurement.observation_time.isoformat()),
                QTableWidgetItem(f"{measurement.predicted_x:.2f}"),
                QTableWidgetItem(f"{measurement.predicted_y:.2f}"),
                QTableWidgetItem(self._format_optional_float(measurement.measured_x, precision=2)),
                QTableWidgetItem(self._format_optional_float(measurement.measured_y, precision=2)),
                QTableWidgetItem(f"{measurement.predicted_ra_deg:.6f}"),
                QTableWidgetItem(f"{measurement.predicted_dec_deg:.6f}"),
                QTableWidgetItem(self._format_optional_float(measurement.match_offset_px, precision=2)),
                QTableWidgetItem(self._format_optional_float(measurement.local_snr, precision=2)),
            ]
            for column_index, item in enumerate(items):
                self._table.setItem(row_index, column_index, item)

    def _draw_plots(self) -> None:
        self._figure.clear()
        self._figure.patch.set_facecolor("#050914")
        axes = self._figure.subplots(2, 2)
        ax_image = axes[0][0]
        ax_sky = axes[0][1]
        ax_offset = axes[1][0]
        ax_snr = axes[1][1]

        for ax in (ax_image, ax_sky, ax_offset, ax_snr):
            KnownObjectOrbit3DDialog._apply_space_theme(ax)

        if not self._frame_measurements:
            ax_image.text(0.5, 0.5, "No frame measurements available.", ha="center", va="center", transform=ax_image.transAxes)
            ax_sky.axis("off")
            ax_offset.axis("off")
            ax_snr.axis("off")
            self._canvas.draw_idle()
            return

        first_time = self._frame_measurements[0].observation_time
        elapsed_minutes = [(measurement.observation_time - first_time).total_seconds() / 60.0 for measurement in self._frame_measurements]
        frame_labels = [f"F{index + 1}" for index in range(len(self._frame_measurements))]

        predicted_x = [measurement.predicted_x for measurement in self._frame_measurements]
        predicted_y = [measurement.predicted_y for measurement in self._frame_measurements]
        ax_image.plot(predicted_x, predicted_y, marker="o", color="#38bdf8", linewidth=1.8, label="Predicted")
        measured_points = [
            (index, float(measurement.measured_x), float(measurement.measured_y), label, measurement.match_offset_px)
            for index, (measurement, label) in enumerate(zip(self._frame_measurements, frame_labels))
            if measurement.measured_x is not None and measurement.measured_y is not None
        ]
        if measured_points:
            ax_image.plot(
                [point[1] for point in measured_points],
                [point[2] for point in measured_points],
                marker="o",
                color="#f59e0b",
                linewidth=1.8,
                label="Measured",
            )
            ax_image.errorbar(
                [point[1] for point in measured_points],
                [point[2] for point in measured_points],
                xerr=[0.0 if point[4] is None else float(point[4]) for point in measured_points],
                yerr=[0.0 if point[4] is None else float(point[4]) for point in measured_points],
                fmt="none",
                ecolor="#fcd34d",
                alpha=0.52,
                elinewidth=1.0,
                capsize=2.5,
            )
            first_residual = True
            for point in measured_points:
                predicted_index = point[0]
                ax_image.plot(
                    [predicted_x[predicted_index], point[1]],
                    [predicted_y[predicted_index], point[2]],
                    color="#fb7185",
                    linewidth=1.0,
                    alpha=0.82,
                    label="Residual" if first_residual else None,
                )
                first_residual = False
        for index in self._endpoint_indices(len(frame_labels)):
            ax_image.annotate(
                frame_labels[index],
                (predicted_x[index], predicted_y[index]),
                textcoords="offset points",
                xytext=(5, 5),
                fontsize=8,
                color="#eef5ff",
            )
        ax_image.set_title("Image-plane trajectory")
        ax_image.set_xlabel("x (px)")
        ax_image.set_ylabel("y (px)")
        ax_image.set_aspect("equal", adjustable="datalim")
        self._set_equal_span_limits(
            ax_image,
            predicted_x + [point[1] for point in measured_points],
            predicted_y + [point[2] for point in measured_points],
            invert_y=True,
        )
        image_legend = ax_image.legend(loc="best")
        KnownObjectOrbit3DDialog._style_space_legend(image_legend)

        reference_ra_deg = self._frame_measurements[0].predicted_ra_deg
        reference_dec_deg = self._frame_measurements[0].predicted_dec_deg
        cos_dec = math.cos(math.radians(reference_dec_deg))
        ra_offsets_arcsec = [((measurement.predicted_ra_deg - reference_ra_deg) * cos_dec * 3600.0) for measurement in self._frame_measurements]
        dec_offsets_arcsec = [((measurement.predicted_dec_deg - reference_dec_deg) * 3600.0) for measurement in self._frame_measurements]
        ax_sky.plot(ra_offsets_arcsec, dec_offsets_arcsec, marker="o", color="#38bdf8", linewidth=1.8, label="Predicted sky path")
        measured_sky_points = [
            (
                index,
                ((measurement.measured_ra_deg - reference_ra_deg) * cos_dec * 3600.0),
                ((measurement.measured_dec_deg - reference_dec_deg) * 3600.0),
            )
            for index, measurement in enumerate(self._frame_measurements)
            if measurement.measured_ra_deg is not None and measurement.measured_dec_deg is not None
        ]
        if measured_sky_points:
            ax_sky.scatter(
                [point[1] for point in measured_sky_points],
                [point[2] for point in measured_sky_points],
                color="#f59e0b",
                s=34,
                zorder=3,
                label="Measured sky centroid",
            )
            first_sky_residual = True
            for point in measured_sky_points:
                predicted_index = point[0]
                ax_sky.plot(
                    [ra_offsets_arcsec[predicted_index], point[1]],
                    [dec_offsets_arcsec[predicted_index], point[2]],
                    color="#fb7185",
                    linewidth=1.0,
                    alpha=0.82,
                    label="Sky residual" if first_sky_residual else None,
                )
                first_sky_residual = False
        for index in self._endpoint_indices(len(frame_labels)):
            ax_sky.annotate(
                frame_labels[index],
                (ra_offsets_arcsec[index], dec_offsets_arcsec[index]),
                textcoords="offset points",
                xytext=(5, 5),
                fontsize=8,
                color="#eef5ff",
            )
        ax_sky.set_title("Predicted sky-plane path")
        ax_sky.set_xlabel("dRA cos(Dec) (arcsec)")
        ax_sky.set_ylabel("dDec (arcsec)")
        ax_sky.set_aspect("equal", adjustable="datalim")
        self._set_equal_span_limits(
            ax_sky,
            ra_offsets_arcsec + [point[1] for point in measured_sky_points],
            dec_offsets_arcsec + [point[2] for point in measured_sky_points],
        )
        sky_legend = ax_sky.legend(loc="best")
        KnownObjectOrbit3DDialog._style_space_legend(sky_legend)

        offset_values = [measurement.match_offset_px for measurement in self._frame_measurements]
        plotted_offset_values = [np.nan if value is None else float(value) for value in offset_values]
        ax_offset.plot(elapsed_minutes, plotted_offset_values, marker="o", color="#8b5cf6", linewidth=1.6)
        if np.isfinite(np.asarray(plotted_offset_values, dtype=float)).any():
            ax_offset.axhline(float(np.nanmedian(plotted_offset_values)), color="#c4b5fd", linestyle="--", linewidth=1.0, alpha=0.8)
        ax_offset.set_title("Match offset vs time")
        ax_offset.set_xlabel("Elapsed time (min)")
        ax_offset.set_ylabel("Offset (px)")

        snr_values = [measurement.local_snr for measurement in self._frame_measurements]
        plotted_snr_values = [np.nan if value is None else float(value) for value in snr_values]
        ax_snr.plot(elapsed_minutes, plotted_snr_values, marker="o", color="#10b981", linewidth=1.6)
        if np.isfinite(np.asarray(plotted_snr_values, dtype=float)).any():
            ax_snr.axhline(float(np.nanmedian(plotted_snr_values)), color="#6ee7b7", linestyle="--", linewidth=1.0, alpha=0.8)
        ax_snr.set_title("Detection SNR vs time")
        ax_snr.set_xlabel("Elapsed time (min)")
        ax_snr.set_ylabel("Local SNR")

        for ax in (ax_image, ax_sky, ax_offset, ax_snr):
            KnownObjectOrbit3DDialog._finalize_space_axes(ax)

        self._canvas.draw_idle()

    @staticmethod
    def _endpoint_indices(count: int) -> tuple[int, ...]:
        if count <= 0:
            return ()
        if count == 1:
            return (0,)
        return (0, count - 1)

    @staticmethod
    def _set_equal_span_limits(ax, x_values: list[float], y_values: list[float], *, invert_y: bool = False) -> None:
        finite_points = [
            (float(x_value), float(y_value))
            for x_value, y_value in zip(x_values, y_values)
            if np.isfinite(x_value) and np.isfinite(y_value)
        ]
        if not finite_points:
            return
        x_min = min(point[0] for point in finite_points)
        x_max = max(point[0] for point in finite_points)
        y_min = min(point[1] for point in finite_points)
        y_max = max(point[1] for point in finite_points)
        span = max(x_max - x_min, y_max - y_min, 1.0)
        half_span = (0.5 * span) + max(0.5, span * 0.08)
        x_center = 0.5 * (x_min + x_max)
        y_center = 0.5 * (y_min + y_max)
        ax.set_xlim(x_center - half_span, x_center + half_span)
        if invert_y:
            ax.set_ylim(y_center + half_span, y_center - half_span)
        else:
            ax.set_ylim(y_center - half_span, y_center + half_span)

    @staticmethod
    def _format_optional_float(value: float | None, *, precision: int) -> str:
        return "-" if value is None else f"{value:.{precision}f}"


@dataclass(frozen=True, slots=True)
class KnownObjectOrbit3DSaveExportPlan:
    export_format: str
    include_info_panel: bool
    is_animation: bool
    frame_count: int
    frame_duration_ms: int
    total_duration_seconds: float


class KnownObjectOrbit3DSaveDialog(QDialog):
    _STILL_FORMAT_OPTIONS: tuple[tuple[str, str], ...] = (
        ("png", "PNG image (*.png)"),
        ("jpg", "JPG image (*.jpg)"),
    )
    _ANIMATION_FORMAT_OPTIONS: tuple[tuple[str, str], ...] = (
        ("gif", "GIF animation (*.gif)"),
        ("mp4", "MP4 video (*.mp4)"),
    )
    _ANIMATION_FPS: dict[str, float] = {"gif": 15.0, "mp4": 30.0}
    _MAX_ANIMATION_FRAMES = 1800
    _MIN_ANIMATION_FRAMES = 2

    def __init__(
        self,
        *,
        animation_window_seconds: float,
        speed_seconds_per_second: float,
        speed_label: str,
        capture_size_provider: Callable[[bool], tuple[int, int]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Save Trajectory View")
        self.setMinimumWidth(420)
        self._animation_window_seconds = max(0.0, float(animation_window_seconds))
        self._speed_seconds_per_second = max(1.0, float(speed_seconds_per_second))
        self._speed_label = str(speed_label).strip()
        self._capture_size_provider = capture_size_provider

        capture_group = QGroupBox("Capture area", self)
        self._view_only_radio = QRadioButton("Trajectory view only", capture_group)
        self._with_panel_radio = QRadioButton("Trajectory view + info panel", capture_group)
        self._view_only_radio.setChecked(True)
        capture_layout = QVBoxLayout()
        capture_layout.addWidget(self._view_only_radio)
        capture_layout.addWidget(self._with_panel_radio)
        capture_group.setLayout(capture_layout)

        self._format_combo = QComboBox(self)
        for format_key, format_label in self._STILL_FORMAT_OPTIONS:
            self._format_combo.addItem(format_label, format_key)
        if self._animation_window_seconds > 0.0:
            for format_key, format_label in self._ANIMATION_FORMAT_OPTIONS:
                self._format_combo.addItem(format_label, format_key)
        format_row = QHBoxLayout()
        format_row.addWidget(QLabel("Format", self))
        format_row.addWidget(self._format_combo, stretch=1)

        self._details_label = QLabel(self)
        self._details_label.setWordWrap(True)
        self._details_label.setStyleSheet(
            "background-color: rgba(14, 22, 38, 0.6);"
            "border: 1px solid #213355;"
            "border-radius: 4px;"
            "padding: 6px 8px;"
        )

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(capture_group)
        layout.addLayout(format_row)
        layout.addWidget(self._details_label)
        layout.addWidget(button_box)
        self.setLayout(layout)

        self._view_only_radio.toggled.connect(self._refresh_details)
        self._format_combo.currentIndexChanged.connect(self._refresh_details)
        self._refresh_details()

    def selected_format(self) -> str:
        return str(self._format_combo.currentData() or "png")

    def include_info_panel(self) -> bool:
        return self._with_panel_radio.isChecked()

    def export_plan(self) -> KnownObjectOrbit3DSaveExportPlan:
        export_format = self.selected_format()
        include_info_panel = self.include_info_panel()
        if export_format not in self._ANIMATION_FPS:
            return KnownObjectOrbit3DSaveExportPlan(
                export_format=export_format,
                include_info_panel=include_info_panel,
                is_animation=False,
                frame_count=1,
                frame_duration_ms=0,
                total_duration_seconds=0.0,
            )
        duration_seconds = self._animation_window_seconds / self._speed_seconds_per_second
        frames_per_second = self._ANIMATION_FPS[export_format]
        frame_count = int(round(duration_seconds * frames_per_second))
        frame_count = min(self._MAX_ANIMATION_FRAMES, max(self._MIN_ANIMATION_FRAMES, frame_count))
        frame_duration_ms = max(1, int(round(duration_seconds * 1000.0 / frame_count)))
        return KnownObjectOrbit3DSaveExportPlan(
            export_format=export_format,
            include_info_panel=include_info_panel,
            is_animation=True,
            frame_count=frame_count,
            frame_duration_ms=frame_duration_ms,
            total_duration_seconds=duration_seconds,
        )

    @staticmethod
    def _format_file_size(size_bytes: float) -> str:
        if size_bytes >= 1024.0 * 1024.0:
            return f"{size_bytes / (1024.0 * 1024.0):.1f} MB"
        return f"{max(1.0, size_bytes / 1024.0):.0f} KB"

    @staticmethod
    def _format_duration(duration_seconds: float) -> str:
        total_seconds = max(0, int(round(duration_seconds)))
        minutes, seconds = divmod(total_seconds, 60)
        if minutes > 0:
            return f"{minutes} min {seconds} s"
        return f"{seconds} s"

    def _estimated_file_size_bytes(self, plan: KnownObjectOrbit3DSaveExportPlan, width: int, height: int) -> float:
        pixel_count = float(max(1, width) * max(1, height))
        if plan.export_format == "png":
            return pixel_count * 3.0 * 0.35
        if plan.export_format == "jpg":
            return pixel_count * 3.0 * 0.10
        if plan.export_format == "gif":
            return pixel_count * plan.frame_count * 0.15
        return pixel_count * plan.frame_count * 0.0125

    def _refresh_details(self) -> None:
        plan = self.export_plan()
        width, height = self._capture_size_provider(plan.include_info_panel)
        size_estimate = self._format_file_size(self._estimated_file_size_bytes(plan, width, height))
        if not plan.is_animation:
            self._details_label.setText(
                f"Image size: {width} x {height} px\nEstimated file size: ~{size_estimate}"
            )
            return
        effective_fps = plan.frame_count / plan.total_duration_seconds if plan.total_duration_seconds > 0 else 0.0
        lines = [
            f"One full pass of the current timeline at {self._speed_label}.",
            f"Video length: {self._format_duration(plan.total_duration_seconds)} "
            f"({plan.frame_count} frames at ~{effective_fps:.0f} fps).",
            f"Frame size: {width} x {height} px.",
            f"Estimated file size: ~{size_estimate}.",
        ]
        expected_frames = int(round(plan.total_duration_seconds * self._ANIMATION_FPS[plan.export_format]))
        if expected_frames > self._MAX_ANIMATION_FRAMES:
            lines.append(
                f"Frame count is capped at {self._MAX_ANIMATION_FRAMES}; the frame rate was reduced to keep the full pass."
            )
        self._details_label.setText("\n".join(lines))


class KnownObjectOrbit3DDialog(QDialog):
    def __init__(
        self,
        *,
        detection: SolarSystemDetection | None = None,
        frame_measurements: tuple[SolarSystemFrameMeasurement, ...] = (),
        context: KnownObjectHeliocentricContext,
        targets: tuple[AsteroidOrbitContextTarget, ...] | None = None,
        available_targets: tuple[AsteroidOrbitContextTarget, ...] | None = None,
        search_nearby_targets: Callable[[float, float], tuple[KnownObjectOrbit3DSearchEntry, ...]] | None = None,
        lookup_exact_target: Callable[[str], tuple[KnownObjectOrbit3DSearchEntry, ...]] | None = None,
        default_nearby_search_radius_deg: float = 3.0,
        default_nearby_magnitude_limit: float = 18.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        object_label = (
            (detection.name or detection.designation)
            if detection is not None
            else None
        ) or context.object_label or "Trajectory View"
        self.setWindowTitle(f"3D View - {object_label}")
        self.resize(1180, 920)
        self._detection = detection
        self._frame_measurements = tuple(frame_measurements)
        self._context = context
        if targets is not None:
            visible_targets = tuple(targets)
        elif detection is not None:
            visible_targets = (
                AsteroidOrbitContextTarget(detection=detection, frame_measurements=self._frame_measurements),
            )
        else:
            visible_targets = ()
        self._context_targets = tuple(visible_targets)
        self._available_targets = self._normalize_available_targets(self._context_targets, available_targets)
        self._search_nearby_targets = search_nearby_targets
        self._lookup_exact_target = lookup_exact_target
        self._default_nearby_search_radius_deg = float(default_nearby_search_radius_deg)
        self._default_nearby_magnitude_limit = float(default_nearby_magnitude_limit)
        self._playback_timer = QTimer(self)
        self._playback_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._playback_timer.timeout.connect(self._advance_playback)
        self._playback_last_tick_seconds: float | None = None
        self._playback_updating = False
        self._playback_index = 0
        self._playback_time = context.reference_time
        self._observation_reset_time = self._default_observation_reset_time()
        self._context_reload_worker = None
        self._gl_view = None
        self._gl_panel_container = None
        self._gl_panel_layout = None
        self._gl_scene_items: list[object] = []
        self._object_current_item = None
        self._earth_current_item = None
        self._connector_item = None
        self._observed_current_item = None
        self._comparison_current_items: list[object | None] = []
        self._additional_body_current_items: dict[str, object] = {}
        self._gl_label_items: dict[str, object] = {}
        self._gl_label_offset_au = 0.08
        self._active_span_key = "local"
        self._custom_span_start = context.window_start.astimezone(UTC)
        self._custom_span_end = context.window_end.astimezone(UTC)
        current_keys = {self._target_visibility_key(target.detection) for target in visible_targets}
        self._object_visibility_states = {
            self._target_visibility_key(target.detection): self._target_visibility_key(target.detection) in current_keys
            for target in self._available_targets
        }
        self._object_visibility_actions: dict[str, QAction] = {}
        self._pending_visibility_states: dict[str, bool] | None = None
        self._refresh_context_arrays()
        self.setStyleSheet(
            "QDialog { background-color: #060816; color: #e7eefc; }"
            "QLabel { color: #e7eefc; }"
            "QPushButton { background-color: #10182d; color: #f3f7ff; border: 1px solid #2d436f; padding: 5px 12px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #182341; }"
            "QTableWidget { background-color: #09101f; alternate-background-color: #0d1527; color: #edf4ff; gridline-color: #24314f; selection-background-color: #23406d; }"
            "QHeaderView::section { background-color: #101a31; color: #cfe0ff; border: 0px; padding: 4px; }"
        )

        self._summary_label = QLabel(self._summary_text(), self)
        self._summary_label.setWordWrap(True)
        self._summary_label.setStyleSheet(
            "background-color: rgba(14, 22, 38, 0.94);"
            "border: 1px solid #213355;"
            "border-radius: 6px;"
            "padding: 8px 10px;"
            "color: #edf4ff;"
        )

        self._periods_panel = QFrame(self)
        self._periods_panel.setVisible(False)
        self._periods_panel.setMaximumWidth(360)
        self._periods_panel.setStyleSheet(
            "background-color: rgba(10, 17, 31, 0.92);"
            "border: 1px solid #213355;"
            "border-radius: 6px;"
            "padding: 6px 10px;"
            "color: #dce8ff;"
        )
        self._periods_panel_layout = QVBoxLayout()
        self._periods_panel_layout.setContentsMargins(10, 8, 10, 8)
        self._periods_panel_layout.setSpacing(6)
        self._periods_panel.setLayout(self._periods_panel_layout)

        self._span_combo = QComboBox(self)
        for key, label, padding_days, sample_count in _KNOWN_OBJECT_3D_SPAN_OPTIONS:
            self._span_combo.addItem(label, (key, padding_days, sample_count))
        self._sync_span_combo_to_context()
        self._span_combo.currentIndexChanged.connect(self._handle_span_changed)

        self._custom_span_start_input = QLineEdit(self)
        self._custom_span_start_input.setPlaceholderText("YYYY-MM-DD")
        self._custom_span_start_input.setFixedWidth(110)
        self._custom_span_start_input.setToolTip("Custom span start date (UTC).")
        self._custom_span_end_input = QLineEdit(self)
        self._custom_span_end_input.setPlaceholderText("YYYY-MM-DD")
        self._custom_span_end_input.setFixedWidth(110)
        self._custom_span_end_input.setToolTip("Custom span end date (UTC).")
        self._custom_span_apply_button = QPushButton("Apply", self)
        self._custom_span_apply_button.setToolTip("Reload the Trajectory View for the custom start/end dates.")
        self._custom_span_apply_button.clicked.connect(self._handle_custom_span_apply)
        self._custom_span_start_label = QLabel("From", self)
        self._custom_span_end_label = QLabel("To", self)
        self._sync_custom_span_inputs_to_state()
        self._set_custom_span_controls_visible(False)

        self._show_planets_checkbox = QCheckBox("Planets", self)
        self._show_planets_checkbox.toggled.connect(self._handle_planets_toggled)
        self._sync_planets_checkbox_to_context()

        self._show_periods_checkbox = QCheckBox("Periods", self)
        self._show_periods_checkbox.toggled.connect(self._handle_show_periods_toggled)

        self._show_labels_checkbox = QCheckBox("Show Labels", self)
        self._show_labels_checkbox.setChecked(True)
        self._show_labels_checkbox.toggled.connect(self._handle_label_style_changed)

        self._show_sample_points_checkbox = QCheckBox("Show Sample Points", self)
        self._show_sample_points_checkbox.setChecked(False)
        self._show_sample_points_checkbox.toggled.connect(self._handle_sample_points_toggled)

        self._label_font_combo = QFontComboBox(self)
        self._label_font_combo.setCurrentFont(QFont("Segoe UI"))
        self._label_font_combo.currentFontChanged.connect(self._handle_label_style_changed)

        self._label_size_spin = QSpinBox(self)
        self._label_size_spin.setRange(6, 24)
        self._label_size_spin.setValue(9)
        self._label_size_spin.setSuffix(" pt")
        self._label_size_spin.valueChanged.connect(self._handle_label_style_changed)

        self._label_bold_checkbox = QCheckBox("Bold", self)
        self._label_bold_checkbox.toggled.connect(self._handle_label_style_changed)

        self._label_italic_checkbox = QCheckBox("Italic", self)
        self._label_italic_checkbox.toggled.connect(self._handle_label_style_changed)

        self._asteroid_color_hex = str(_KNOWN_OBJECT_3D_OBJECT_STYLE["hex"])
        self._comet_color_hex = str(_KNOWN_OBJECT_3D_COMET_STYLE["hex"])
        self._asteroid_color_button = QPushButton("Asteroids", self)
        self._asteroid_color_button.clicked.connect(self._handle_asteroid_color_button_clicked)
        self._comet_color_button = QPushButton("Comets", self)
        self._comet_color_button.clicked.connect(self._handle_comet_color_button_clicked)
        self._sync_object_color_button_styles()

        self._camera_mode_combo = QComboBox(self)
        self._camera_mode_combo.addItem("Orbit Overview", "overview")
        self._camera_mode_combo.addItem("Top-Down", "topdown")
        self._camera_mode_combo.addItem("Side View", "side")
        self._camera_mode_combo.addItem("Object Follow", "object-follow")
        self._camera_mode_combo.addItem("Earth Follow", "earth-follow")
        self._camera_mode_combo.currentIndexChanged.connect(self._handle_camera_mode_changed)

        self._play_button = QPushButton(self)
        self._play_button.setCheckable(True)
        self._play_button.setFixedSize(44, 30)
        self._play_button.setIconSize(QSize(18, 18))
        self._play_button.setToolTip("Play timeline")
        self._play_button.setStyleSheet(
            "QPushButton { background-color: #173869; color: #f6fbff; border: 1px solid #5a8ff0; padding: 0px; border-radius: 5px; }"
            "QPushButton:hover { background-color: #214b8c; }"
            "QPushButton:checked { background-color: #2a5db7; border-color: #9fc0ff; }"
        )
        self._play_button.toggled.connect(self._handle_play_toggled)
        self._sync_play_button_icon(False)

        self._reset_time_button = QToolButton(self)
        self._reset_time_button.setFixedSize(30, 30)
        self._reset_time_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self._reset_time_button.setIconSize(QSize(16, 16))
        self._reset_time_button.setToolTip("Return to the observation time")
        self._reset_time_button.setStyleSheet(
            "QToolButton { background-color: #10182d; color: #f6fbff; border: 1px solid #2d436f; border-radius: 5px; }"
            "QToolButton:hover { background-color: #182341; }"
        )
        self._reset_time_button.clicked.connect(self._handle_reset_time_clicked)

        self._settings_button = QToolButton(self)
        self._settings_button.setText("⚙")
        self._settings_button.setToolTip("Display settings")
        self._settings_button.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")
        self._settings_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._settings_menu = QMenu(self._settings_button)

        settings_panel = QWidget(self._settings_menu)
        settings_panel.setStyleSheet("background-color: #0d1424; color: #edf4ff;")
        settings_panel_layout = QVBoxLayout()
        settings_panel_layout.setContentsMargins(10, 8, 10, 8)
        settings_panel_layout.setSpacing(8)
        settings_panel_layout.addWidget(self._show_labels_checkbox)
        settings_panel_layout.addWidget(self._show_sample_points_checkbox)
        settings_font_row = QHBoxLayout()
        settings_font_row.setContentsMargins(0, 0, 0, 0)
        settings_font_row.addWidget(QLabel("Font", settings_panel))
        settings_font_row.addWidget(self._label_font_combo, stretch=1)
        settings_panel_layout.addLayout(settings_font_row)
        settings_style_row = QHBoxLayout()
        settings_style_row.setContentsMargins(0, 0, 0, 0)
        settings_style_row.addWidget(QLabel("Size", settings_panel))
        settings_style_row.addWidget(self._label_size_spin)
        settings_style_row.addSpacing(8)
        settings_style_row.addWidget(self._label_bold_checkbox)
        settings_style_row.addWidget(self._label_italic_checkbox)
        settings_style_row.addStretch(1)
        settings_panel_layout.addLayout(settings_style_row)
        settings_color_row = QHBoxLayout()
        settings_color_row.setContentsMargins(0, 0, 0, 0)
        settings_color_row.addWidget(QLabel("Colors", settings_panel))
        settings_color_row.addWidget(self._asteroid_color_button)
        settings_color_row.addWidget(self._comet_color_button)
        settings_color_row.addStretch(1)
        settings_panel_layout.addLayout(settings_color_row)

        settings_panel_layout.addWidget(QLabel("Sky Track", settings_panel))
        self._sky_track_bayer_checkbox = QCheckBox("Bayer designations (a Ori, z Oph, …)", settings_panel)
        self._sky_track_bayer_checkbox.setChecked(False)
        self._sky_track_bayer_checkbox.setToolTip("Also label stars using Bayer-style short names from catalog aliases.")
        self._sky_track_bayer_checkbox.toggled.connect(self._handle_sky_track_display_settings_changed)
        settings_panel_layout.addWidget(self._sky_track_bayer_checkbox)

        sky_density_row = QHBoxLayout()
        sky_density_row.setContentsMargins(0, 0, 0, 0)
        sky_density_row.addWidget(QLabel("Star density", settings_panel))
        self._sky_track_density_combo = QComboBox(settings_panel)
        self._sky_track_density_combo.addItem("Sparse", "sparse")
        self._sky_track_density_combo.addItem("Medium", "medium")
        self._sky_track_density_combo.addItem("Dense", "dense")
        self._sky_track_density_combo.setCurrentIndex(1)
        self._sky_track_density_combo.setToolTip("Controls how many background stars are drawn by magnitude limit.")
        self._sky_track_density_combo.currentIndexChanged.connect(self._handle_sky_track_display_settings_changed)
        sky_density_row.addWidget(self._sky_track_density_combo, stretch=1)
        settings_panel_layout.addLayout(sky_density_row)

        sky_extent_row = QHBoxLayout()
        sky_extent_row.setContentsMargins(0, 0, 0, 0)
        sky_extent_row.addWidget(QLabel("Star draw radius", settings_panel))
        self._sky_track_extent_spin = QDoubleSpinBox(settings_panel)
        self._sky_track_extent_spin.setRange(30.0, 180.0)
        self._sky_track_extent_spin.setSingleStep(10.0)
        self._sky_track_extent_spin.setDecimals(0)
        self._sky_track_extent_spin.setValue(180.0)
        self._sky_track_extent_spin.setSuffix("°")
        self._sky_track_extent_spin.setToolTip("Angular radius around the trajectory center used for stars and constellation lines. 180° draws the entire sky.")
        self._sky_track_extent_spin.valueChanged.connect(self._handle_sky_track_display_settings_changed)
        sky_extent_row.addWidget(self._sky_track_extent_spin)
        sky_extent_row.addStretch(1)
        settings_panel_layout.addLayout(sky_extent_row)

        self._sky_track_constellations_checkbox = QCheckBox("Constellation lines", settings_panel)
        self._sky_track_constellations_checkbox.setChecked(True)
        self._sky_track_constellations_checkbox.setToolTip("Draw stick-figure constellation lines across the Sky Track field.")
        self._sky_track_constellations_checkbox.toggled.connect(self._handle_sky_track_display_settings_changed)
        settings_panel_layout.addWidget(self._sky_track_constellations_checkbox)
        sky_view_buttons = QHBoxLayout()
        sky_view_buttons.setContentsMargins(0, 0, 0, 0)
        self._sky_track_fit_button = QPushButton("Fit Trajectory", settings_panel)
        self._sky_track_fit_button.setToolTip("Return Sky Track to the complete visible trajectory.")
        self._sky_track_fit_button.clicked.connect(self._apply_sky_track_view_fit)
        self._sky_track_all_sky_button = QPushButton("Entire Sky", settings_panel)
        self._sky_track_all_sky_button.setToolTip("Zoom Sky Track out to the complete celestial sphere.")
        self._sky_track_all_sky_button.clicked.connect(self._apply_sky_track_entire_sky_fit)
        sky_view_buttons.addWidget(self._sky_track_fit_button)
        sky_view_buttons.addWidget(self._sky_track_all_sky_button)
        sky_view_buttons.addStretch(1)
        settings_panel_layout.addLayout(sky_view_buttons)

        settings_panel_layout.addWidget(QLabel("Info panels", settings_panel))
        self._panel_order_list = QListWidget(settings_panel)
        self._panel_order_list.setMinimumHeight(110)
        self._panel_order_list.setMaximumHeight(140)
        self._panel_order_list.setStyleSheet(
            "QListWidget { background-color: #10182d; color: #edf4ff; border: 1px solid #2d436f; }"
            "QListWidget::item:selected { background-color: #23406d; }"
        )
        settings_panel_layout.addWidget(self._panel_order_list)
        panel_order_buttons = QHBoxLayout()
        panel_order_buttons.setContentsMargins(0, 0, 0, 0)
        self._panel_move_up_button = QPushButton("Move Up", settings_panel)
        self._panel_move_up_button.clicked.connect(self._handle_panel_move_up)
        self._panel_move_down_button = QPushButton("Move Down", settings_panel)
        self._panel_move_down_button.clicked.connect(self._handle_panel_move_down)
        self._panel_remove_button = QPushButton("Remove", settings_panel)
        self._panel_remove_button.setToolTip("Hide the selected panel.")
        self._panel_remove_button.clicked.connect(self._handle_panel_remove)
        self._panel_reset_layout_button = QPushButton("Reset Layout", settings_panel)
        self._panel_reset_layout_button.setToolTip("Restore the default visible panels, order, and relative heights.")
        self._panel_reset_layout_button.clicked.connect(self._handle_panel_layout_reset)
        panel_order_buttons.addWidget(self._panel_move_up_button)
        panel_order_buttons.addWidget(self._panel_move_down_button)
        panel_order_buttons.addWidget(self._panel_remove_button)
        panel_order_buttons.addWidget(self._panel_reset_layout_button)
        panel_order_buttons.addStretch(1)
        settings_panel_layout.addLayout(panel_order_buttons)
        panel_add_row = QHBoxLayout()
        panel_add_row.setContentsMargins(0, 0, 0, 0)
        panel_add_row.addWidget(QLabel("Add", settings_panel))
        self._panel_add_combo = QComboBox(settings_panel)
        self._panel_add_combo.setMinimumWidth(160)
        self._panel_add_button = QPushButton("Add Panel", settings_panel)
        self._panel_add_button.clicked.connect(self._handle_panel_add)
        panel_add_row.addWidget(self._panel_add_combo, stretch=1)
        panel_add_row.addWidget(self._panel_add_button)
        settings_panel_layout.addLayout(panel_add_row)

        settings_panel.setLayout(settings_panel_layout)

        settings_action = QWidgetAction(self._settings_menu)
        settings_action.setDefaultWidget(settings_panel)
        self._settings_menu.addAction(settings_action)
        self._settings_button.setMenu(self._settings_menu)

        self._object_menu_button = QToolButton(self)
        self._object_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._object_menu = QMenu(self._object_menu_button)
        self._object_menu_button.setMenu(self._object_menu)
        self._rebuild_object_toggle_controls()

        self._object_lookup_button = QPushButton("Lookup", self)
        self._object_lookup_button.setToolTip("Search asteroid/comet names, designations, or keywords and add the selected matches to the current 3D scene.")
        self._object_lookup_button.clicked.connect(self._handle_exact_lookup_requested)
        self._object_lookup_button.setVisible(self._lookup_exact_target is not None)

        self._save_view_button = QPushButton("Save", self)
        self._save_view_button.setToolTip(
            "Save the trajectory view (optionally including the info panel) as a still image (PNG/JPG) "
            "or as an animation (GIF/MP4) covering one full pass of the timeline at the selected speed."
        )
        self._save_view_button.clicked.connect(self._handle_save_view_requested)

        self._speed_combo = QComboBox(self)
        self._speed_combo.addItem("1 h/s", 3600.0)
        self._speed_combo.addItem("6 h/s", 21600.0)
        self._speed_combo.addItem("1 d/s", 86400.0)
        self._speed_combo.addItem("7 d/s", 604800.0)
        self._speed_combo.addItem("30 d/s", 2592000.0)
        self._speed_combo.setCurrentIndex(2)
        self._speed_combo.setFixedHeight(30)
        self._speed_combo.currentIndexChanged.connect(self._update_playback_timer_interval)

        self._time_input = QLineEdit(self)
        self._time_input.setPlaceholderText("YYYY-MM-DD HH:MM:SS UTC")
        self._time_input.setFixedWidth(210)
        self._time_input.setFixedHeight(30)
        self._time_input.editingFinished.connect(self._handle_time_input_editing_finished)

        self._frame_slider = QSlider(Qt.Orientation.Horizontal, self)
        self._frame_slider.setRange(0, 0)
        self._frame_slider.setMaximumWidth(260)
        self._frame_slider.valueChanged.connect(self._handle_slider_changed)

        self._frame_label = QLabel("0/0", self)
        self._frame_label.setFixedWidth(72)
        self._frame_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._frame_label.setStyleSheet("color: #cfe0ff; font-weight: 600;")

        controls_row = QHBoxLayout()
        controls_row.setContentsMargins(0, 0, 0, 0)
        controls_row.setSpacing(8)
        controls_row.addWidget(QLabel("Span", self), 0, Qt.AlignmentFlag.AlignVCenter)
        controls_row.addWidget(self._span_combo, 0, Qt.AlignmentFlag.AlignVCenter)
        controls_row.addWidget(self._custom_span_start_label, 0, Qt.AlignmentFlag.AlignVCenter)
        controls_row.addWidget(self._custom_span_start_input, 0, Qt.AlignmentFlag.AlignVCenter)
        controls_row.addWidget(self._custom_span_end_label, 0, Qt.AlignmentFlag.AlignVCenter)
        controls_row.addWidget(self._custom_span_end_input, 0, Qt.AlignmentFlag.AlignVCenter)
        controls_row.addWidget(self._custom_span_apply_button, 0, Qt.AlignmentFlag.AlignVCenter)
        controls_row.addSpacing(8)
        controls_row.addWidget(self._show_planets_checkbox, 0, Qt.AlignmentFlag.AlignVCenter)
        controls_row.addWidget(self._show_periods_checkbox, 0, Qt.AlignmentFlag.AlignVCenter)
        controls_row.addSpacing(8)
        controls_row.addWidget(QLabel("Camera", self), 0, Qt.AlignmentFlag.AlignVCenter)
        controls_row.addWidget(self._camera_mode_combo, 0, Qt.AlignmentFlag.AlignVCenter)
        controls_row.addSpacing(8)
        controls_row.addWidget(self._settings_button, 0, Qt.AlignmentFlag.AlignVCenter)
        controls_row.addWidget(self._object_menu_button, 0, Qt.AlignmentFlag.AlignVCenter)
        controls_row.addWidget(self._object_lookup_button, 0, Qt.AlignmentFlag.AlignVCenter)
        controls_row.addWidget(self._save_view_button, 0, Qt.AlignmentFlag.AlignVCenter)
        controls_row.addStretch(1)
        animation_separator = QFrame(self)
        animation_separator.setFrameShape(QFrame.Shape.VLine)
        animation_separator.setStyleSheet("color: #2b3b5f;")
        controls_row.addWidget(animation_separator, 0, Qt.AlignmentFlag.AlignVCenter)
        controls_row.addSpacing(8)
        playback_controls_row = QHBoxLayout()
        playback_controls_row.setContentsMargins(0, 0, 0, 0)
        playback_controls_row.setSpacing(8)
        playback_controls_row.addWidget(QLabel("UTC", self), 0, Qt.AlignmentFlag.AlignVCenter)
        playback_controls_row.addWidget(self._time_input, 0, Qt.AlignmentFlag.AlignVCenter)
        playback_controls_row.addWidget(self._reset_time_button, 0, Qt.AlignmentFlag.AlignVCenter)
        playback_controls_row.addWidget(self._play_button, 0, Qt.AlignmentFlag.AlignVCenter)
        playback_controls_row.addWidget(QLabel("Speed", self), 0, Qt.AlignmentFlag.AlignVCenter)
        playback_controls_row.addWidget(self._speed_combo, 0, Qt.AlignmentFlag.AlignVCenter)
        playback_controls_row.addSpacing(8)
        playback_controls_row.addWidget(QLabel("Time", self), 0, Qt.AlignmentFlag.AlignVCenter)
        playback_controls_row.addWidget(self._frame_slider, 0, Qt.AlignmentFlag.AlignVCenter)
        playback_controls_row.addWidget(QLabel("Sample", self), 0, Qt.AlignmentFlag.AlignVCenter)
        playback_controls_row.addWidget(self._frame_label, 0, Qt.AlignmentFlag.AlignVCenter)
        controls_row.addLayout(playback_controls_row)

        self._plot_hover_label = QLabel("Hover a plot to inspect values.", self)
        self._plot_hover_label.setWordWrap(True)
        self._plot_hover_label.setMinimumHeight(24)
        self._plot_hover_label.setVisible(False)
        self._plot_hover_label.setStyleSheet(
            "background-color: rgba(10, 17, 31, 0.92);"
            "border: 1px solid #213355;"
            "border-radius: 4px;"
            "padding: 4px 8px;"
            "color: #dce8ff;"
        )
        self._distance_hover_series: list[tuple[str, np.ndarray, np.ndarray, str, str]] = []
        self._magnitude_hover_series: list[tuple[str, np.ndarray, np.ndarray, str, str]] = []
        self._distance_hover_artists: dict[str, object] = {}
        self._magnitude_hover_artists: dict[str, object] = {}
        self._distance_playback_item = None
        self._magnitude_playback_item = None
        self._time_series_plot_refreshing = False
        self._topdown_playback_primary_marker = None
        self._topdown_playback_earth_marker = None
        self._topdown_playback_body_markers: dict[str, object] = {}
        self._topdown_playback_text_items: dict[str, object] = {}

        if pg is None:
            raise RuntimeError("pyqtgraph is required to display the orbit time-series plots.")
        self._topdown_plot = self._create_topdown_plot_widget()
        self._distance_plot = self._create_time_series_plot_widget("Distance over window", "Distance (AU)")
        self._magnitude_plot = self._create_time_series_plot_widget("Literature magnitude over window", "Mag", invert_y=True)
        self._sky_track_plot = self._create_sky_track_plot_widget()
        self._sky_track_playback_item = None
        self._sky_track_text_item = None
        self._sky_track_projected_series: list[dict[str, object]] = []
        self._sky_track_hover_items: dict[str, object] = {}
        self._topdown_plot.viewport().installEventFilter(self)
        self._magnitude_plot.setXLink(self._distance_plot)
        self._distance_plot.viewport().installEventFilter(self)
        self._magnitude_plot.viewport().installEventFilter(self)
        self._topdown_mouse_proxy = pg.SignalProxy(
            self._topdown_plot.scene().sigMouseMoved,
            rateLimit=60,
            slot=self._handle_topdown_plot_mouse_moved,
        )
        self._distance_mouse_proxy = pg.SignalProxy(
            self._distance_plot.scene().sigMouseMoved,
            rateLimit=60,
            slot=self._handle_distance_plot_mouse_moved,
        )
        self._distance_plot.scene().sigMouseClicked.connect(self._handle_distance_plot_mouse_clicked)
        self._magnitude_mouse_proxy = pg.SignalProxy(
            self._magnitude_plot.scene().sigMouseMoved,
            rateLimit=60,
            slot=self._handle_magnitude_plot_mouse_moved,
        )
        self._magnitude_plot.scene().sigMouseClicked.connect(self._handle_magnitude_plot_mouse_clicked)
        self._sky_track_plot.scene().sigMouseClicked.connect(self._handle_sky_track_plot_mouse_clicked)

        left_panel = self._build_gl_panel()
        self._periods_panel.setParent(left_panel)
        self._periods_panel.hide()
        self._position_periods_panel()

        right_panel = QWidget(self)
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_panel.setLayout(right_layout)
        self._info_panel = right_panel

        visual_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        visual_splitter.addWidget(left_panel)
        visual_splitter.addWidget(right_panel)
        visual_splitter.setChildrenCollapsible(False)
        visual_splitter.setStretchFactor(0, 5)
        visual_splitter.setStretchFactor(1, 2)
        visual_splitter.setSizes([1180, 520])
        self._visual_splitter = visual_splitter

        self._table = QTableWidget(len(frame_measurements), 10, self)
        self._table.setHorizontalHeaderLabels(["Frame", "UTC", "Obj X", "Obj Y", "Obj Z", "Earth X", "Earth Y", "Earth Z", "Sun Dist", "Earth Dist"])
        table_header = self._table.horizontalHeader()
        for column_index, width in ((0, 70), (1, 210), (2, 78), (3, 78), (4, 78), (5, 82), (6, 82), (7, 82), (8, 82), (9, 82)):
            table_header.setSectionResizeMode(column_index, QHeaderView.ResizeMode.Interactive)
            self._table.setColumnWidth(column_index, width)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.itemSelectionChanged.connect(self._handle_table_selection_changed)
        self._table.setAlternatingRowColors(True)
        self._table.setMinimumHeight(120)

        self._topdown_plot.setMinimumHeight(120)
        self._distance_plot.setMinimumHeight(120)
        self._magnitude_plot.setMinimumHeight(120)
        self._sky_track_plot.setMinimumHeight(120)

        self._info_panel_widgets = {
            "topdown": self._topdown_plot,
            "sky_track": self._sky_track_plot,
            "magnitude": self._magnitude_plot,
            "distance": self._distance_plot,
            "data": self._table,
        }
        self._info_panel_order = list(_KNOWN_OBJECT_3D_PANEL_ORDER_DEFAULT)
        self._sky_track_fit_bounds: tuple[float, float, float, float] | None = None
        self._right_splitter = QSplitter(Qt.Orientation.Vertical, self)
        self._right_splitter.setChildrenCollapsible(False)
        self._apply_info_panel_layout(reset_sizes=True)
        self._sync_panel_order_list()
        self._sky_track_plot.viewport().installEventFilter(self)

        close_button = QPushButton("Close", self)
        close_button.clicked.connect(self.close)

        right_layout.addWidget(self._right_splitter, stretch=1)

        layout = QVBoxLayout()
        layout.addWidget(self._summary_label)
        layout.addLayout(controls_row)
        layout.addWidget(visual_splitter, stretch=1)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)
        self.setLayout(layout)

        self._populate_table()
        self._update_periods_label()
        self._draw_plots()
        self._update_playback_timer_interval()
        self._sync_playback_controls_to_context(preferred_time=self._context.reference_time, update_camera=True)
        QTimer.singleShot(0, self._refresh_gl_after_show)

    def _refresh_context_arrays(self) -> None:
        self._object_path_points = self._sample_points(self._context.object_path_samples)
        self._earth_path_points = self._sample_points(self._context.earth_path_samples)
        self._observed_object_points = self._sample_points(self._context.observation_object_samples)
        self._observed_earth_points = self._sample_points(self._context.observation_earth_samples)
        self._comparison_path_points = [self._sample_points(track.path_samples) for track in self._comparison_tracks()]
        self._comparison_observed_points = [self._sample_points(track.observation_samples) for track in self._comparison_tracks()]
        self._additional_body_points = {
            body.key: self._sample_points(body.path_samples)
            for body in self._context.additional_bodies
        }
        self._timeline_times = tuple(sample.observation_time for sample in self._timeline_samples())
        self._timeline_timestamps = np.array([sample_time.timestamp() for sample_time in self._timeline_times], dtype=float) if self._timeline_times else np.zeros(0, dtype=float)
        self._sky_track_ra_deg, self._sky_track_dec_deg, self._sky_track_times = self._sky_track_radec_for_samples(
            self._context.object_path_samples,
            self._context.earth_path_samples,
        )
        self._sky_track_observation_ra_deg, self._sky_track_observation_dec_deg, self._sky_track_observation_times = self._sky_track_radec_for_samples(
            self._context.observation_object_samples,
            self._context.observation_earth_samples,
        )
        self._sky_track_series = self._build_sky_track_series()

    def matches_request(
        self,
        detection: SolarSystemDetection | None,
        frame_measurements: tuple[SolarSystemFrameMeasurement, ...],
    ) -> bool:
        if detection is None or self._detection is None:
            return False
        if (self._detection.designation or "") != (detection.designation or ""):
            return False
        if (self._detection.name or "") != (detection.name or ""):
            return False
        if len(self._frame_measurements) != len(frame_measurements):
            return False
        return all(
            existing.source_path == incoming.source_path and existing.observation_time == incoming.observation_time
            for existing, incoming in zip(self._frame_measurements, frame_measurements, strict=True)
        )

    def update_view_context(
        self,
        *,
        detection: SolarSystemDetection | None,
        frame_measurements: tuple[SolarSystemFrameMeasurement, ...],
        context: KnownObjectHeliocentricContext,
        targets: tuple[AsteroidOrbitContextTarget, ...] | None = None,
        available_targets: tuple[AsteroidOrbitContextTarget, ...] | None = None,
    ) -> None:
        previous_playback_time = self._current_playback_time()
        self._playback_timer.stop()
        self._context_reload_worker = None
        self._detection = detection
        self._frame_measurements = tuple(frame_measurements)
        self._context = context
        if targets is not None:
            visible_targets = tuple(targets)
        elif detection is not None:
            visible_targets = (
                AsteroidOrbitContextTarget(detection=detection, frame_measurements=self._frame_measurements),
            )
        else:
            visible_targets = ()
        self._context_targets = tuple(visible_targets)
        self._available_targets = self._normalize_available_targets(self._context_targets, available_targets or self._available_targets)
        visible_keys = {self._target_visibility_key(target.detection) for target in visible_targets}
        self._object_visibility_states = {
            self._target_visibility_key(target.detection): self._object_visibility_states.get(self._target_visibility_key(target.detection), self._target_visibility_key(target.detection) in visible_keys)
            for target in self._available_targets
        }
        self._custom_span_start = context.window_start.astimezone(UTC)
        self._custom_span_end = context.window_end.astimezone(UTC)
        self._sync_primary_target_state()
        self._observation_reset_time = self._default_observation_reset_time()
        self._refresh_context_arrays()
        self._rebuild_object_toggle_controls()
        self._sync_span_combo_to_context()
        self._sync_custom_span_inputs_to_state()
        self._sync_planets_checkbox_to_context()
        self._table.setRowCount(len(self._frame_measurements))
        self._play_button.blockSignals(True)
        self._play_button.setChecked(False)
        self._sync_play_button_icon(False)
        self._play_button.blockSignals(False)
        self._populate_table()
        self._update_periods_label()
        self._draw_plots()
        self._set_context_loading(False)
        self._sync_playback_controls_to_context(preferred_time=previous_playback_time, update_camera=True)
        QTimer.singleShot(0, self._refresh_gl_after_show)

    def update_available_targets(
        self,
        *,
        available_targets: tuple[AsteroidOrbitContextTarget, ...],
    ) -> None:
        self._available_targets = self._normalize_available_targets(self._context_targets, available_targets)
        self._rebuild_object_toggle_controls()

    def _apply_selected_search_entries(self, selected_entries: tuple[KnownObjectOrbit3DSearchEntry, ...]) -> None:
        if not selected_entries:
            return
        previous_states = dict(self._object_visibility_states)
        self._available_targets = self._normalize_available_targets(
            self._context_targets,
            tuple(self._available_targets) + tuple(entry.target for entry in selected_entries),
        )
        for target in self._available_targets:
            key = self._target_visibility_key(target.detection)
            self._object_visibility_states[key] = previous_states.get(key, key in self._current_target_keys())
        for entry in selected_entries:
            self._object_visibility_states[self._target_visibility_key(entry.target.detection)] = True
        desired_targets = self._desired_context_targets()
        desired_keys = {self._target_visibility_key(target.detection) for target in desired_targets}
        self._rebuild_object_toggle_controls()
        if desired_keys.issubset(self._current_target_keys()):
            self._draw_plots()
            self._update_plot_playback_markers()
            QTimer.singleShot(0, self._refresh_gl_after_show)
            return
        self._pending_visibility_states = previous_states
        self._start_context_reload_for_current_span(targets=desired_targets)

    def _handle_nearby_search_requested(self) -> None:
        if self._search_nearby_targets is None:
            return
        dialog = KnownObjectOrbit3DNearbySearchDialog(
            default_radius_deg=self._default_nearby_search_radius_deg,
            default_magnitude_limit=self._default_nearby_magnitude_limit,
            search_callback=self._search_nearby_targets,
            parent=self,
        )
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return
        self._apply_selected_search_entries(dialog.selected_entries())

    def _handle_exact_lookup_requested(self) -> None:
        if self._lookup_exact_target is None:
            return
        dialog = KnownObjectOrbit3DExactLookupDialog(lookup_callback=self._lookup_exact_target, parent=self)
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return
        self._apply_selected_search_entries(dialog.selected_entries())

    def _capture_gl_view_image(self) -> QImage | None:
        if self._gl_view is None:
            return None
        image: QImage | None = None
        read_qimage = getattr(self._gl_view, "readQImage", None)
        if callable(read_qimage):
            image = read_qimage()
        elif hasattr(self._gl_view, "grabFramebuffer"):
            image = self._gl_view.grabFramebuffer()
        else:
            image = self._gl_view.grab().toImage()
        if image is None or image.isNull():
            return None
        return image

    @staticmethod
    def _normalize_export_qimage(image: QImage) -> QImage:
        normalized = image.convertToFormat(QImage.Format.Format_RGB888)
        if normalized.isNull():
            normalized = image.copy()
        if float(normalized.devicePixelRatio() or 1.0) != 1.0:
            normalized = normalized.copy()
            normalized.setDevicePixelRatio(1.0)
        return normalized

    @staticmethod
    def _compose_side_by_side_export_image(left_image: QImage, right_image: QImage) -> QImage:
        height = max(left_image.height(), right_image.height())
        left_scaled = left_image
        right_scaled = right_image
        if left_image.height() != height:
            left_scaled = left_image.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)
        if right_image.height() != height:
            right_scaled = right_image.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)
        composed = QImage(
            max(1, left_scaled.width() + right_scaled.width()),
            max(1, height),
            QImage.Format.Format_RGB888,
        )
        composed.fill(QColor("#060816"))
        painter = QPainter(composed)
        try:
            painter.drawImage(0, 0, left_scaled)
            painter.drawImage(left_scaled.width(), 0, right_scaled)
        finally:
            painter.end()
        return composed

    def _capture_info_panel_image(self) -> QImage | None:
        info_panel = getattr(self, "_info_panel", None)
        if info_panel is None and self._visual_splitter is not None:
            info_panel = self._visual_splitter.widget(1)
        if info_panel is None:
            return None
        # Offscreen render avoids depending on what is currently covering the
        # panel on-screen (e.g. a progress dialog), so animation export does not
        # need to hide/show windows each frame.
        ratio = max(1.0, float(info_panel.devicePixelRatio() or 1.0))
        width = max(1, int(round(info_panel.width() * ratio)))
        height = max(1, int(round(info_panel.height() * ratio)))
        if width <= 1 or height <= 1:
            return None
        image = QImage(width, height, QImage.Format.Format_RGB888)
        image.setDevicePixelRatio(ratio)
        image.fill(QColor("#09101f"))
        painter = QPainter(image)
        try:
            info_panel.render(
                painter,
                QPoint(0, 0),
                renderFlags=(
                    QWidget.RenderFlag.DrawWindowBackground
                    | QWidget.RenderFlag.DrawChildren
                ),
            )
        finally:
            painter.end()
        if image.isNull():
            return None
        return image

    def _capture_export_image(self, include_info_panel: bool) -> QImage | None:
        gl_image = self._capture_gl_view_image()
        if gl_image is None or gl_image.isNull():
            if self._gl_panel_container is not None:
                gl_image = self._gl_panel_container.grab().toImage()
            if gl_image is None or gl_image.isNull():
                return None
        gl_image = self._normalize_export_qimage(gl_image)
        if not include_info_panel:
            return gl_image

        # Grab the GL framebuffer and info panel separately, then stitch them.
        # A single splitter.grab() is unreliable with OpenGL children.
        info_image = self._capture_info_panel_image()
        if info_image is None or info_image.isNull():
            raise ValueError(
                "The info panel could not be captured. Try saving again after the "
                "3D view is fully visible, or choose Trajectory view only."
            )
        info_image = self._normalize_export_qimage(info_image)
        return self._compose_side_by_side_export_image(gl_image, info_image)

    def _export_capture_size(self, include_info_panel: bool) -> tuple[int, int]:
        if include_info_panel:
            left_widget = self._gl_view if self._gl_view is not None else self._gl_panel_container
            right_widget = getattr(self, "_info_panel", None)
            if right_widget is None and self._visual_splitter is not None:
                right_widget = self._visual_splitter.widget(1)
            if left_widget is None or right_widget is None:
                return (0, 0)
            left_ratio = float(left_widget.devicePixelRatio() or 1.0)
            right_ratio = float(right_widget.devicePixelRatio() or 1.0)
            left_width = max(1, int(round(left_widget.width() * left_ratio)))
            left_height = max(1, int(round(left_widget.height() * left_ratio)))
            right_width = max(1, int(round(right_widget.width() * right_ratio)))
            right_height = max(1, int(round(right_widget.height() * right_ratio)))
            return (left_width + right_width, max(left_height, right_height))
        widget = self._gl_view if self._gl_view is not None else self._gl_panel_container
        if widget is None:
            return (0, 0)
        ratio = float(widget.devicePixelRatio() or 1.0)
        return (max(1, int(round(widget.width() * ratio))), max(1, int(round(widget.height() * ratio))))

    def _default_export_file_stem(self) -> str:
        object_label = self._detection.name or self._detection.designation or self._context.object_label or "trajectory"
        sanitized = re.sub(r"[^\w\-]+", "_", str(object_label)).strip("_") or "trajectory"
        return f"{sanitized}_trajectory"

    def _handle_save_view_requested(self) -> None:
        if self._play_button.isChecked():
            self._play_button.setChecked(False)
        window_start, window_end = self._playback_window_bounds()
        window_seconds = max(0.0, (window_end - window_start).total_seconds())
        options_dialog = KnownObjectOrbit3DSaveDialog(
            animation_window_seconds=window_seconds,
            speed_seconds_per_second=float(self._speed_combo.currentData() or 86400.0),
            speed_label=self._speed_combo.currentText(),
            capture_size_provider=self._export_capture_size,
            parent=self,
        )
        if options_dialog.exec() != int(QDialog.DialogCode.Accepted):
            return
        plan = options_dialog.export_plan()
        filter_labels = {
            "png": "PNG image (*.png)",
            "jpg": "JPG image (*.jpg *.jpeg)",
            "gif": "GIF animation (*.gif)",
            "mp4": "MP4 video (*.mp4)",
        }
        default_name = f"{self._default_export_file_stem()}.{plan.export_format}"
        path_text, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save Trajectory View",
            default_name,
            filter_labels[plan.export_format],
        )
        if not path_text:
            return
        output_path = Path(path_text)
        allowed_suffixes = {".jpg", ".jpeg"} if plan.export_format == "jpg" else {f".{plan.export_format}"}
        if output_path.suffix.lower() not in allowed_suffixes:
            output_path = output_path.with_suffix(f".{plan.export_format}")
        if plan.is_animation:
            self._export_trajectory_animation(output_path, plan)
        else:
            self._export_trajectory_still(output_path, plan)

    def _export_trajectory_still(self, output_path: Path, plan: KnownObjectOrbit3DSaveExportPlan) -> None:
        try:
            image = self._capture_export_image(plan.include_info_panel)
        except ValueError as exc:
            QMessageBox.warning(self, "Save Failed", str(exc))
            return
        if image is None or image.isNull():
            QMessageBox.warning(self, "Save Failed", "The trajectory view could not be captured.")
            return
        if plan.export_format == "jpg":
            saved = image.save(str(output_path), "JPG", 95)
        else:
            saved = image.save(str(output_path))
        if not saved:
            QMessageBox.warning(self, "Save Failed", f"The image could not be written to {output_path}.")
            return
        QMessageBox.information(
            self,
            "View Saved",
            f"Saved {image.width()} x {image.height()} px image to:\n{output_path}",
        )

    def _export_trajectory_animation(self, output_path: Path, plan: KnownObjectOrbit3DSaveExportPlan) -> None:
        window_start, window_end = self._playback_window_bounds()
        window_seconds = max(0.0, (window_end - window_start).total_seconds())
        if window_seconds <= 0.0 or plan.frame_count < 2:
            QMessageBox.warning(self, "Save Failed", "The current timeline is too short to export an animation.")
            return
        original_time = self._current_playback_time()
        update_camera = self._camera_mode_requires_tracking()
        writer_factory = StreamingGifWriter if plan.export_format == "gif" else StreamingMp4Writer
        progress = QProgressDialog("Rendering trajectory animation...", "Cancel", 0, plan.frame_count, self)
        progress.setWindowModality(Qt.WindowModality.NonModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        cancelled = False
        try:
            with writer_factory(output_path, frame_duration_ms=plan.frame_duration_ms) as writer:
                for frame_index in range(plan.frame_count):
                    frame_time = window_start + timedelta(seconds=window_seconds * frame_index / plan.frame_count)
                    self._set_playback_time(frame_time, update_camera=update_camera)
                    QApplication.processEvents()
                    image = self._capture_export_image(plan.include_info_panel)
                    if image is None or image.isNull():
                        raise ValueError("The trajectory view could not be captured.")
                    writer.append_qimage(image)
                    progress.setValue(frame_index + 1)
                    if progress.wasCanceled():
                        cancelled = True
                        break
        except ValueError as exc:
            progress.close()
            self._set_playback_time(original_time, update_camera=update_camera)
            output_path.unlink(missing_ok=True)
            QMessageBox.warning(self, "Animation Export Failed", str(exc))
            return
        finally:
            progress.close()
            self._set_playback_time(original_time, update_camera=update_camera)
        if cancelled:
            output_path.unlink(missing_ok=True)
            return
        file_size_text = ""
        try:
            file_size_mb = output_path.stat().st_size / (1024.0 * 1024.0)
            file_size_text = f"\nFile size: {file_size_mb:.1f} MB"
        except OSError:
            pass
        QMessageBox.information(
            self,
            "Animation Saved",
            f"Saved {plan.frame_count} frames "
            f"({KnownObjectOrbit3DSaveDialog._format_duration(plan.total_duration_seconds)}) to:\n"
            f"{output_path}{file_size_text}",
        )

    def _sync_span_combo_to_context(self) -> None:
        if getattr(self, "_active_span_key", "local") == "custom":
            custom_index = next(
                (
                    index
                    for index in range(self._span_combo.count())
                    if isinstance(self._span_combo.itemData(index), tuple) and self._span_combo.itemData(index)[0] == "custom"
                ),
                0,
            )
            self._span_combo.blockSignals(True)
            self._span_combo.setCurrentIndex(custom_index)
            self._span_combo.blockSignals(False)
            self._set_custom_span_controls_visible(True)
            return
        target_padding_days = float(getattr(self._context, "arc_padding_days", 45.0))
        best_index = 0
        best_delta = float("inf")
        for index, (key, _label, padding_days, _sample_count) in enumerate(_KNOWN_OBJECT_3D_SPAN_OPTIONS):
            if key == "custom" or padding_days is None:
                continue
            delta = abs(float(padding_days) - target_padding_days)
            if delta < best_delta:
                best_delta = delta
                best_index = index
                self._active_span_key = key
        self._span_combo.blockSignals(True)
        self._span_combo.setCurrentIndex(best_index)
        self._span_combo.blockSignals(False)
        self._set_custom_span_controls_visible(False)

    def _sync_planets_checkbox_to_context(self) -> None:
        checked = bool(getattr(self._context, "include_major_planets", False))
        self._show_planets_checkbox.blockSignals(True)
        self._show_planets_checkbox.setChecked(checked)
        self._show_planets_checkbox.blockSignals(False)

    def _build_gl_panel(self) -> QWidget:
        container = QWidget(self)
        container_layout = QVBoxLayout()
        container_layout.setContentsMargins(0, 0, 0, 0)
        self._gl_panel_container = container
        self._gl_panel_layout = container_layout
        container.installEventFilter(self)
        if gl is None:
            fallback = QLabel(
                "GPU 3D view is unavailable because pyqtgraph.opengl or OpenGL could not be imported. The scientific side plots are still available.",
                container,
            )
            fallback.setWordWrap(True)
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fallback.setStyleSheet(
                "background-color: #08101d; border: 1px solid #213355; border-radius: 6px; padding: 16px; color: #edf4ff;"
            )
            container_layout.addWidget(fallback, stretch=1)
            container.setLayout(container_layout)
            return container

        container.setLayout(container_layout)
        return container

    @staticmethod
    def _reset_gl_shader_program_caches() -> None:
        if gl is None:
            return
        # pyqtgraph caches compiled shader program IDs globally, but those IDs
        # are bound to the OpenGL context that created them.
        for item_name in ("GLScatterPlotItem", "GLLinePlotItem"):
            item_class = getattr(gl, item_name, None)
            if item_class is not None and hasattr(item_class, "_shaderProgram"):
                setattr(item_class, "_shaderProgram", None)

    def _recreate_gl_view(self) -> None:
        if gl is None or self._gl_panel_container is None or self._gl_panel_layout is None:
            return
        self._clear_gl_scene()
        if self._gl_view is not None:
            self._gl_panel_layout.removeWidget(self._gl_view)
            self._gl_view.deleteLater()
            self._gl_view = None
        self._reset_gl_shader_program_caches()
        self._gl_view = _KnownObjectOrbitGLViewWidget(self._gl_panel_container)
        self._gl_view.setBackgroundColor(QColor("#040713"))
        self._gl_view.setMinimumHeight(440)
        self._gl_view.opts["fov"] = 58
        self._gl_view.opts["distance"] = 6.0
        self._gl_panel_layout.addWidget(self._gl_view, stretch=1)
        self._gl_panel_layout.activate()
        self._gl_view.updateGeometry()
        self._gl_view.show()
        self._position_periods_panel()
        self._periods_panel.raise_()
        self._rebuild_gl_scene()

    def _clear_gl_scene(self) -> None:
        if self._gl_view is None:
            return
        for item in self._gl_scene_items:
            try:
                self._gl_view.removeItem(item)
            except Exception:
                continue
        self._gl_scene_items = []
        self._object_current_item = None
        self._earth_current_item = None
        self._connector_item = None
        self._observed_current_item = None
        self._comparison_current_items = []
        self._additional_body_current_items = {}
        self._gl_label_items = {}

    def _scene_points(self) -> np.ndarray:
        point_groups = [self._earth_path_points, self._observed_earth_points, *self._additional_body_points.values()]
        if self._is_target_visible(0):
            point_groups.extend([self._object_path_points, self._observed_object_points])
        for comparison_index, comparison_points in enumerate(self._comparison_path_points, start=1):
            if self._is_target_visible(comparison_index):
                point_groups.append(comparison_points)
        for comparison_index, comparison_points in enumerate(self._comparison_observed_points, start=1):
            if self._is_target_visible(comparison_index):
                point_groups.append(comparison_points)
        point_groups = [points for points in point_groups if points.size]
        if not point_groups:
            return np.zeros((1, 3), dtype=float)
        return np.vstack(point_groups)

    def _rebuild_gl_scene(self) -> None:
        if self._gl_view is None:
            return
        self._clear_gl_scene()
        show_sample_points = self._show_sample_points_checkbox.isChecked()
        scene_points = self._scene_points()
        scene_extent = max(1.0, float(np.max(np.linalg.norm(scene_points, axis=1)))) if scene_points.size else 1.0
        focus_points = self._trajectory_focus_points()
        focus_extent = max(0.5, float(np.max(np.linalg.norm(focus_points, axis=1)))) if focus_points.size else 0.5
        self._gl_label_offset_au = min(0.08, max(0.02, focus_extent * 0.015))
        self._apply_gl_camera_distance_limits(scene_extent)
        self._add_gl_starfield(scene_extent)
        primary_style = self._primary_target_style()
        if self._is_target_visible(0):
            self._add_gl_path(
                self._context.object_path_samples,
                self._object_path_points,
                color=primary_style["line"],
                glow_color=primary_style["glow"],
                peak_alpha=0.98,
                base_alpha=0.20,
                glow_peak_alpha=0.22,
                glow_base_alpha=0.04,
            )
        self._add_gl_path(
            self._context.earth_path_samples,
            self._earth_path_points,
            color=(0.35, 0.78, 1.0),
            glow_color=(0.22, 0.74, 1.0),
            peak_alpha=0.94,
            base_alpha=0.18,
            glow_peak_alpha=0.18,
            glow_base_alpha=0.03,
        )
        for comparison_points_index, track in enumerate(self._comparison_tracks()):
            target_index = comparison_points_index + 1
            if not self._is_target_visible(target_index):
                self._comparison_current_items.append(None)
                continue
            style = self._comparison_track_style(comparison_points_index)
            comparison_points = self._comparison_path_points[comparison_points_index] if comparison_points_index < len(self._comparison_path_points) else np.zeros((0, 3), dtype=float)
            self._add_gl_path(
                track.path_samples,
                comparison_points,
                color=style["line"],
                glow_color=style["glow"],
                peak_alpha=0.92,
                base_alpha=0.14,
                glow_peak_alpha=0.17,
                glow_base_alpha=0.03,
            )
            comparison_current_item = gl.GLScatterPlotItem(
                pos=np.zeros((1, 3), dtype=float),
                color=np.array([[style["line"][0], style["line"][1], style["line"][2], 0.96]], dtype=float),
                size=11.0,
                pxMode=True,
            )
            self._comparison_current_items.append(comparison_current_item)
            self._gl_scene_items.append(comparison_current_item)
            self._gl_view.addItem(comparison_current_item)
        for body in self._context.additional_bodies:
            style = self._body_style(body.key)
            body_points = self._additional_body_points.get(body.key, np.zeros((0, 3), dtype=float))
            self._add_gl_path(
                body.path_samples,
                body_points,
                color=style["line"],
                glow_color=style["glow"],
                peak_alpha=0.86,
                base_alpha=0.10,
                glow_peak_alpha=0.16,
                glow_base_alpha=0.02,
            )
            current_item = gl.GLScatterPlotItem(
                pos=np.zeros((1, 3), dtype=float),
                color=np.array([[style["line"][0], style["line"][1], style["line"][2], 0.95]], dtype=float),
                size=8.5,
                pxMode=True,
            )
            self._additional_body_current_items[body.key] = current_item
            self._gl_scene_items.append(current_item)
            self._gl_view.addItem(current_item)
        if show_sample_points and self._is_target_visible(0):
            self._add_gl_observed_points(self._observed_object_points, color=(1.0, 0.45, 0.45, 0.92), size=8.0)
        for comparison_points_index, observed_points in enumerate(self._comparison_observed_points):
            target_index = comparison_points_index + 1
            if not show_sample_points or not self._is_target_visible(target_index):
                continue
            style = self._comparison_track_style(comparison_points_index)
            self._add_gl_observed_points(
                observed_points,
                color=(style["line"][0], style["line"][1], style["line"][2], 0.80),
                size=6.4,
            )
        self._add_gl_sun()

        self._object_current_item = None if not self._is_target_visible(0) else gl.GLScatterPlotItem(
            pos=np.zeros((1, 3), dtype=float),
            color=np.array([[primary_style["line"][0], primary_style["line"][1], primary_style["line"][2], 1.0]], dtype=float),
            size=18.0,
            pxMode=True,
        )
        self._earth_current_item = gl.GLScatterPlotItem(
            pos=np.zeros((1, 3), dtype=float),
            color=np.array([[0.40, 0.84, 1.0, 1.0]], dtype=float),
            size=14.0,
            pxMode=True,
        )
        self._observed_current_item = None if (not show_sample_points or not self._is_target_visible(0)) else gl.GLScatterPlotItem(
            pos=np.zeros((1, 3), dtype=float),
            color=np.array([[1.0, 0.40, 0.40, 1.0]], dtype=float),
            size=12.0,
            pxMode=True,
        )
        self._connector_item = None if not self._is_target_visible(0) else gl.GLLinePlotItem(
            pos=np.zeros((2, 3), dtype=float),
            color=(0.88, 0.92, 1.0, 0.28),
            width=1.3,
            antialias=True,
        )
        for scene_item in (self._object_current_item, self._earth_current_item, self._observed_current_item, self._connector_item):
            if scene_item is None:
                continue
            self._gl_scene_items.append(scene_item)
            self._gl_view.addItem(scene_item)
        self._add_gl_label("sun", "Sun", (0.0, 0.0, 0.0), "#facc15")
        self._add_gl_label("earth", "Earth", (0.0, 0.0, 0.0), "#90def5")
        if self._is_target_visible(0):
            self._add_gl_label("object-primary", self._primary_target_label(), (0.0, 0.0, 0.0), str(primary_style["hex"]))
        for comparison_points_index, track in enumerate(self._comparison_tracks()):
            target_index = comparison_points_index + 1
            if not self._is_target_visible(target_index):
                continue
            comparison_style = self._comparison_track_style(comparison_points_index)
            self._add_gl_label(
                f"object-{comparison_points_index}",
                track.object_label,
                (0.0, 0.0, 0.0),
                str(comparison_style["hex"]),
            )
        for body in self._context.additional_bodies:
            style = self._body_style(body.key)
            self._add_gl_label(f"planet-{body.key}", body.label, (0.0, 0.0, 0.0), str(style["hex"]))

    def _refresh_gl_after_show(self) -> None:
        if gl is None or not self.isVisible():
            return
        if self._gl_panel_container is not None and (self._gl_panel_container.width() < 8 or self._gl_panel_container.height() < 8):
            QTimer.singleShot(60, self._refresh_gl_after_show)
            return
        if self._gl_view is None or not self._gl_scene_items or self._gl_view.width() < 8 or self._gl_view.height() < 8:
            self._recreate_gl_view()
        else:
            self._rebuild_gl_scene()
        self._update_gl_playback_state()
        self._position_periods_panel()
        self._periods_panel.raise_()
        if self._gl_view is not None:
            self._gl_view.update()

    def _position_periods_panel(self) -> None:
        if self._gl_panel_container is None:
            return
        available_width = max(0, self._gl_panel_container.width() - 24)
        if available_width <= 0:
            return
        self._periods_panel.setMaximumWidth(min(360, available_width))
        self._periods_panel.adjustSize()
        width = min(self._periods_panel.sizeHint().width(), self._periods_panel.maximumWidth())
        height = self._periods_panel.sizeHint().height()
        self._periods_panel.resize(width, height)
        self._periods_panel.move(12, 12)

    def _summary_text(self) -> str:
        object_label = self._primary_target_label()
        span_label = self._span_combo.currentText() if hasattr(self, "_span_combo") else "Local"
        planets_label = "On" if getattr(self._context, "include_major_planets", False) else "Off"
        target_count = sum(1 for checked in self._object_visibility_states.values() if checked)
        if target_count <= 0:
            target_summary = "No objects"
            horizons_label = "Earth only"
        elif target_count == 1:
            target_summary = object_label
            horizons_label = self._context.resolved_target_name
        else:
            target_summary = f"{object_label} + {target_count - 1} more"
            horizons_label = self._context.resolved_target_name
        return (
            f"{target_summary} | Targets: {target_count} | Horizons target: {horizons_label} | Frames: {len(self._frame_measurements)} | "
            f"Span: {span_label} | Planets: {planets_label} | Window: {self._context.window_start.date().isoformat()} to {self._context.window_end.date().isoformat()}"
        )

    def _comparison_tracks(self) -> tuple[KnownObjectComparisonTrack, ...]:
        return tuple(getattr(self._context, "comparison_tracks", ()))

    def _info_panel_widget_for_key(self, panel_key: str) -> QWidget | None:
        return getattr(self, "_info_panel_widgets", {}).get(panel_key)

    def _current_info_panel_sizes(self) -> dict[str, int]:
        sizes = list(self._right_splitter.sizes()) if hasattr(self, "_right_splitter") else []
        mapped: dict[str, int] = {}
        for index, panel_key in enumerate(getattr(self, "_info_panel_order", [])):
            if index < len(sizes):
                mapped[panel_key] = max(1, int(sizes[index]))
        return mapped

    def _apply_info_panel_layout(self, *, reset_sizes: bool = False) -> None:
        if not hasattr(self, "_right_splitter") or not hasattr(self, "_info_panel_widgets"):
            return
        previous_sizes = {} if reset_sizes else self._current_info_panel_sizes()
        while self._right_splitter.count():
            widget = self._right_splitter.widget(0)
            if widget is not None:
                widget.setParent(None)
        ordered_keys = [key for key in self._info_panel_order if key in self._info_panel_widgets]
        if not ordered_keys:
            ordered_keys = list(_KNOWN_OBJECT_3D_PANEL_ORDER_DEFAULT)
            self._info_panel_order = list(ordered_keys)
        for index, panel_key in enumerate(ordered_keys):
            widget = self._info_panel_widgets[panel_key]
            self._right_splitter.addWidget(widget)
            self._right_splitter.setStretchFactor(index, 1)
            widget.show()
        for panel_key, widget in self._info_panel_widgets.items():
            if panel_key not in ordered_keys:
                widget.hide()
                widget.setParent(self)
        self._info_panel_order = ordered_keys
        if reset_sizes:
            sizes = [_KNOWN_OBJECT_3D_PANEL_DEFAULT_SIZES.get(key, 180) for key in ordered_keys]
        else:
            sizes = [
                previous_sizes.get(key, _KNOWN_OBJECT_3D_PANEL_DEFAULT_SIZES.get(key, 180))
                for key in ordered_keys
            ]
        self._right_splitter.setSizes(sizes)
        if "sky_track" in ordered_keys:
            QTimer.singleShot(0, self._apply_sky_track_view_fit)

    def _hidden_info_panel_keys(self) -> list[str]:
        visible = set(getattr(self, "_info_panel_order", ()))
        return [key for key in _KNOWN_OBJECT_3D_PANEL_KEYS if key not in visible]

    def _sync_panel_add_combo(self) -> None:
        if not hasattr(self, "_panel_add_combo"):
            return
        hidden_keys = self._hidden_info_panel_keys()
        self._panel_add_combo.blockSignals(True)
        self._panel_add_combo.clear()
        for panel_key in hidden_keys:
            self._panel_add_combo.addItem(_KNOWN_OBJECT_3D_PANEL_LABELS.get(panel_key, panel_key), panel_key)
        self._panel_add_combo.blockSignals(False)
        has_hidden = bool(hidden_keys)
        self._panel_add_combo.setEnabled(has_hidden)
        if hasattr(self, "_panel_add_button"):
            self._panel_add_button.setEnabled(has_hidden)

    def _sync_panel_order_list(self) -> None:
        if not hasattr(self, "_panel_order_list"):
            return
        selected_key = None
        current_item = self._panel_order_list.currentItem()
        if current_item is not None:
            selected_key = current_item.data(Qt.ItemDataRole.UserRole)
        self._panel_order_list.clear()
        for panel_key in self._info_panel_order:
            item = QListWidgetItem(_KNOWN_OBJECT_3D_PANEL_LABELS.get(panel_key, panel_key))
            item.setData(Qt.ItemDataRole.UserRole, panel_key)
            self._panel_order_list.addItem(item)
            if panel_key == selected_key:
                self._panel_order_list.setCurrentItem(item)
        if self._panel_order_list.currentRow() < 0 and self._panel_order_list.count() > 0:
            self._panel_order_list.setCurrentRow(0)
        self._sync_panel_add_combo()

    def _move_info_panel(self, delta: int) -> None:
        current_row = self._panel_order_list.currentRow() if hasattr(self, "_panel_order_list") else -1
        if current_row < 0:
            return
        target_row = current_row + int(delta)
        if target_row < 0 or target_row >= len(self._info_panel_order):
            return
        order = list(self._info_panel_order)
        order[current_row], order[target_row] = order[target_row], order[current_row]
        self._info_panel_order = order
        self._apply_info_panel_layout(reset_sizes=False)
        self._sync_panel_order_list()
        if hasattr(self, "_panel_order_list"):
            self._panel_order_list.setCurrentRow(target_row)

    def _handle_panel_move_up(self) -> None:
        self._move_info_panel(-1)

    def _handle_panel_move_down(self) -> None:
        self._move_info_panel(1)

    def _handle_panel_remove(self) -> None:
        if not hasattr(self, "_panel_order_list"):
            return
        current_row = self._panel_order_list.currentRow()
        if current_row < 0 or current_row >= len(self._info_panel_order):
            return
        if len(self._info_panel_order) <= 1:
            QMessageBox.information(self, "3D view", "Keep at least one info panel visible.")
            return
        order = list(self._info_panel_order)
        order.pop(current_row)
        self._info_panel_order = order
        self._apply_info_panel_layout(reset_sizes=False)
        self._sync_panel_order_list()
        if self._panel_order_list.count() > 0:
            self._panel_order_list.setCurrentRow(min(current_row, self._panel_order_list.count() - 1))

    def _handle_panel_add(self) -> None:
        if not hasattr(self, "_panel_add_combo"):
            return
        panel_key = self._panel_add_combo.currentData()
        if not panel_key or panel_key in self._info_panel_order:
            return
        self._info_panel_order = list(self._info_panel_order) + [str(panel_key)]
        self._apply_info_panel_layout(reset_sizes=False)
        self._sync_panel_order_list()
        if hasattr(self, "_panel_order_list"):
            self._panel_order_list.setCurrentRow(self._panel_order_list.count() - 1)

    def _handle_panel_layout_reset(self) -> None:
        self._info_panel_order = list(_KNOWN_OBJECT_3D_PANEL_ORDER_DEFAULT)
        self._apply_info_panel_layout(reset_sizes=True)
        self._sync_panel_order_list()

    def _build_sky_track_series(self) -> list[dict[str, object]]:
        series: list[dict[str, object]] = []
        if self._context.object_path_samples:
            series.append(
                {
                    "target_index": 0,
                    "label": self._primary_target_label(),
                    "ra_deg": self._sky_track_ra_deg,
                    "dec_deg": self._sky_track_dec_deg,
                    "times": self._sky_track_times,
                    "observation_ra_deg": self._sky_track_observation_ra_deg,
                    "observation_dec_deg": self._sky_track_observation_dec_deg,
                    "observation_times": self._sky_track_observation_times,
                    "object_samples": tuple(self._context.object_path_samples),
                    "earth_samples": tuple(self._context.earth_path_samples),
                }
            )
        earth_path = self._context.earth_path_samples
        earth_obs = self._context.observation_earth_samples
        for comparison_index, track in enumerate(self._comparison_tracks()):
            target_index = comparison_index + 1
            ra_deg, dec_deg, times = self._sky_track_radec_for_samples(track.path_samples, earth_path)
            observation_ra_deg, observation_dec_deg, observation_times = self._sky_track_radec_for_samples(
                track.observation_samples,
                earth_obs,
            )
            if target_index < len(self._context_targets):
                detection = self._context_targets[target_index].detection
                label = detection.name or detection.designation or track.object_label
            else:
                label = track.object_label
            series.append(
                {
                    "target_index": target_index,
                    "label": label,
                    "ra_deg": ra_deg,
                    "dec_deg": dec_deg,
                    "times": times,
                    "observation_ra_deg": observation_ra_deg,
                    "observation_dec_deg": observation_dec_deg,
                    "observation_times": observation_times,
                    "object_samples": tuple(track.path_samples),
                    "earth_samples": tuple(earth_path),
                }
            )
        return series

    def _visible_sky_track_series(self) -> list[dict[str, object]]:
        return [
            entry
            for entry in getattr(self, "_sky_track_series", [])
            if self._is_target_visible(int(entry["target_index"]))
            and np.asarray(entry["ra_deg"]).size > 0
            and np.asarray(entry["dec_deg"]).size > 0
        ]

    def _sky_track_style_for_target(self, target_index: int) -> dict[str, object]:
        if target_index <= 0:
            return self._primary_target_style()
        return self._comparison_track_style(target_index - 1)

    @staticmethod
    def _target_visibility_key(detection: SolarSystemDetection) -> str:
        designation = (detection.designation or "").strip()
        name = (detection.name or "").strip()
        if designation and name:
            return f"designation:{designation}|name:{name}"
        if designation:
            return f"designation:{designation}"
        if name:
            return f"name:{name}"
        return f"coords:{detection.predicted_ra_deg:.6f}:{detection.predicted_dec_deg:.6f}"

    @staticmethod
    def _normalize_available_targets(
        current_targets: tuple[AsteroidOrbitContextTarget, ...],
        available_targets: tuple[AsteroidOrbitContextTarget, ...] | None,
    ) -> tuple[AsteroidOrbitContextTarget, ...]:
        ordered_targets: list[AsteroidOrbitContextTarget] = []
        seen_keys: set[str] = set()
        for target in tuple(available_targets or ()) + tuple(current_targets):
            key = KnownObjectOrbit3DDialog._target_visibility_key(target.detection)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            ordered_targets.append(target)
        return tuple(ordered_targets)

    def _current_target_keys(self) -> set[str]:
        return {self._target_visibility_key(target.detection) for target in self._context_targets}

    def _desired_context_targets(self) -> tuple[AsteroidOrbitContextTarget, ...]:
        return tuple(
            target
            for target in self._available_targets
            if self._object_visibility_states.get(self._target_visibility_key(target.detection), False)
        )

    def _target_visibility_entries(self) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        desired_targets = self._desired_context_targets()
        detail_key = self._target_visibility_key(desired_targets[0].detection) if desired_targets else None
        for index, target in enumerate(self._available_targets):
            key = self._target_visibility_key(target.detection)
            label = target.detection.name or target.detection.designation or f"Target {index + 1}"
            if key == detail_key:
                label = f"{label} (detail)"
            entries.append((key, label))
        return entries

    def _is_target_visible(self, target_index: int) -> bool:
        if target_index < 0 or target_index >= len(self._context_targets):
            return False
        key = self._target_visibility_key(self._context_targets[target_index].detection)
        return bool(self._object_visibility_states.get(key, True))

    def _sync_object_visibility_states(self) -> None:
        previous_states = dict(self._object_visibility_states)
        self._object_visibility_states = {}
        current_keys = self._current_target_keys()
        for key, _label in self._target_visibility_entries():
            self._object_visibility_states[key] = previous_states.get(key, key in current_keys)

    def _rebuild_object_toggle_controls(self) -> None:
        self._sync_object_visibility_states()
        self._object_menu.clear()
        self._object_visibility_actions = {}
        for key, label in self._target_visibility_entries():
            action = QAction(label, self._object_menu)
            action.setCheckable(True)
            action.setChecked(bool(self._object_visibility_states.get(key, False)))
            action.toggled.connect(lambda checked, visibility_key=key: self._handle_object_visibility_toggled(visibility_key, checked))
            self._object_visibility_actions[key] = action
            self._object_menu.addAction(action)
        if self._search_nearby_targets is not None:
            if self._object_visibility_actions:
                self._object_menu.addSeparator()
            add_action = self._object_menu.addAction("Nearby Search...")
            add_action.triggered.connect(self._handle_nearby_search_requested)
        selected_count = sum(1 for checked in self._object_visibility_states.values() if checked)
        total_count = len(self._available_targets)
        self._object_menu_button.setText(f"Objects ({selected_count}/{total_count})")
        self._object_menu_button.setEnabled(total_count > 0 or self._search_nearby_targets is not None)

    def _current_label_font(self) -> QFont:
        font = QFont(self._label_font_combo.currentFont())
        font.setPointSize(int(self._label_size_spin.value()))
        font.setBold(self._label_bold_checkbox.isChecked())
        font.setItalic(self._label_italic_checkbox.isChecked())
        return font

    def _label_text_style(self) -> dict[str, object]:
        return {
            "textcoords": "offset points",
            "xytext": (6, 6),
            "fontsize": int(self._label_size_spin.value()),
            "color": "#f8fbff",
            "fontweight": "bold" if self._label_bold_checkbox.isChecked() else "normal",
            "fontstyle": "italic" if self._label_italic_checkbox.isChecked() else "normal",
            "fontfamily": self._label_font_combo.currentFont().family(),
        }

    def _label_offset_position(self, position: tuple[float, float, float]) -> tuple[float, float, float]:
        return (
            float(position[0]) + self._gl_label_offset_au,
            float(position[1]) + self._gl_label_offset_au,
            float(position[2]),
        )

    def _trajectory_focus_points(self) -> np.ndarray:
        point_groups: list[np.ndarray] = []
        if self._is_target_visible(0):
            point_groups.extend([self._object_path_points, self._observed_object_points])
        for comparison_index, comparison_points in enumerate(self._comparison_path_points, start=1):
            if self._is_target_visible(comparison_index):
                point_groups.append(comparison_points)
        for comparison_index, comparison_points in enumerate(self._comparison_observed_points, start=1):
            if self._is_target_visible(comparison_index):
                point_groups.append(comparison_points)
        point_groups = [points for points in point_groups if points.size]
        if not point_groups:
            return np.zeros((1, 3), dtype=float)
        return np.vstack(point_groups)

    def _trajectory_focus_bounds(self) -> tuple[float, float, float, float]:
        focus_points = self._trajectory_focus_points()
        if not focus_points.size:
            return (-1.0, 1.0, -1.0, 1.0)
        x_values = focus_points[:, 0]
        y_values = focus_points[:, 1]
        center_x = float(np.mean((float(np.min(x_values)), float(np.max(x_values)))))
        center_y = float(np.mean((float(np.min(y_values)), float(np.max(y_values)))))
        half_span = max(
            0.35,
            float(np.max(x_values) - np.min(x_values)) * 0.5,
            float(np.max(y_values) - np.min(y_values)) * 0.5,
        )
        margin = max(0.08, half_span * 0.18)
        bounded_half_span = half_span + margin
        return (
            center_x - bounded_half_span,
            center_x + bounded_half_span,
            center_y - bounded_half_span,
            center_y + bounded_half_span,
        )

    def _point_in_xy_focus_bounds(self, x_value: float, y_value: float) -> bool:
        bounds = getattr(self, "_xy_focus_bounds", None)
        if bounds is None:
            return True
        x_min, x_max, y_min, y_max = bounds
        return x_min <= float(x_value) <= x_max and y_min <= float(y_value) <= y_max

    def _additional_bodies(self) -> tuple[HeliocentricReferenceBody, ...]:
        return tuple(self._context.additional_bodies)

    def _object_magnitude_samples(self):
        return tuple(getattr(self._context, "object_magnitude_samples", ()))

    @staticmethod
    def _radec_unit_vector(ra_deg: float, dec_deg: float) -> np.ndarray:
        ra_rad = math.radians(float(ra_deg))
        dec_rad = math.radians(float(dec_deg))
        cos_dec = math.cos(dec_rad)
        return np.array([
            cos_dec * math.cos(ra_rad),
            cos_dec * math.sin(ra_rad),
            math.sin(dec_rad),
        ], dtype=float)

    @staticmethod
    def _unit_vector_to_radec(vector: np.ndarray) -> tuple[float, float] | None:
        norm = float(np.linalg.norm(vector))
        if norm <= 0.0 or not np.isfinite(norm):
            return None
        unit = vector / norm
        ra_deg = math.degrees(math.atan2(float(unit[1]), float(unit[0]))) % 360.0
        dec_deg = math.degrees(math.asin(max(-1.0, min(1.0, float(unit[2])))))
        return (ra_deg, dec_deg)

    @staticmethod
    def _ecliptic_vector_to_equatorial(vector: np.ndarray) -> np.ndarray:
        obliquity_rad = math.radians(_KNOWN_OBJECT_3D_ECLIPTIC_OBLIQUITY_DEG)
        cos_eps = math.cos(obliquity_rad)
        sin_eps = math.sin(obliquity_rad)
        return np.array([
            float(vector[0]),
            float(vector[1]) * cos_eps - float(vector[2]) * sin_eps,
            float(vector[1]) * sin_eps + float(vector[2]) * cos_eps,
        ], dtype=float)

    @classmethod
    def _geocentric_radec_from_positions(cls, object_position: np.ndarray, earth_position: np.ndarray) -> tuple[float, float] | None:
        geocentric_vector = np.array(object_position, dtype=float) - np.array(earth_position, dtype=float)
        return cls._unit_vector_to_radec(cls._ecliptic_vector_to_equatorial(geocentric_vector))

    @classmethod
    def _sky_track_radec_for_samples(cls, object_samples, earth_samples) -> tuple[np.ndarray, np.ndarray, tuple[datetime, ...]]:
        ra_values: list[float] = []
        dec_values: list[float] = []
        times: list[datetime] = []
        for object_sample, earth_sample in zip(object_samples, earth_samples):
            radec = cls._geocentric_radec_from_positions(
                np.array([object_sample.x_au, object_sample.y_au, object_sample.z_au], dtype=float),
                np.array([earth_sample.x_au, earth_sample.y_au, earth_sample.z_au], dtype=float),
            )
            if radec is None:
                continue
            ra_deg, dec_deg = radec
            ra_values.append(float(ra_deg))
            dec_values.append(float(dec_deg))
            times.append(object_sample.observation_time)
        return (np.array(ra_values, dtype=float), np.array(dec_values, dtype=float), tuple(times))

    @staticmethod
    def _state_sample_position(sample) -> np.ndarray:
        return np.array([sample.x_au, sample.y_au, sample.z_au], dtype=float)

    @staticmethod
    def _state_sample_velocity(sample) -> np.ndarray:
        return np.array(
            [
                getattr(sample, "vx_au_per_day", 0.0),
                getattr(sample, "vy_au_per_day", 0.0),
                getattr(sample, "vz_au_per_day", 0.0),
            ],
            dtype=float,
        )

    @classmethod
    def _interpolate_state_vector_position(cls, samples, observation_time: datetime) -> np.ndarray | None:
        """Interpolate a state-vector series with position and velocity continuity."""
        samples = tuple(samples)
        if not samples:
            return None
        if len(samples) == 1:
            return cls._state_sample_position(samples[0])
        timestamps = np.array([sample.observation_time.timestamp() for sample in samples], dtype=float)
        target_timestamp = float(observation_time.timestamp())
        if target_timestamp <= float(timestamps[0]):
            return cls._state_sample_position(samples[0])
        if target_timestamp >= float(timestamps[-1]):
            return cls._state_sample_position(samples[-1])
        upper_index = int(np.searchsorted(timestamps, target_timestamp, side="right"))
        lower_index = max(0, upper_index - 1)
        upper_index = min(len(samples) - 1, upper_index)
        lower_sample = samples[lower_index]
        upper_sample = samples[upper_index]
        interval_seconds = float(timestamps[upper_index] - timestamps[lower_index])
        if interval_seconds <= 0.0:
            return cls._state_sample_position(lower_sample)
        fraction = min(1.0, max(0.0, (target_timestamp - float(timestamps[lower_index])) / interval_seconds))
        lower_position = cls._state_sample_position(lower_sample)
        upper_position = cls._state_sample_position(upper_sample)
        lower_velocity = cls._state_sample_velocity(lower_sample)
        upper_velocity = cls._state_sample_velocity(upper_sample)
        if not np.all(np.isfinite(np.concatenate((lower_position, upper_position, lower_velocity, upper_velocity)))):
            return lower_position + ((upper_position - lower_position) * fraction)
        interval_days = interval_seconds / 86400.0
        fraction_squared = fraction * fraction
        fraction_cubed = fraction_squared * fraction
        lower_position_weight = (2.0 * fraction_cubed) - (3.0 * fraction_squared) + 1.0
        lower_velocity_weight = fraction_cubed - (2.0 * fraction_squared) + fraction
        upper_position_weight = (-2.0 * fraction_cubed) + (3.0 * fraction_squared)
        upper_velocity_weight = fraction_cubed - fraction_squared
        return (
            (lower_position_weight * lower_position)
            + (lower_velocity_weight * interval_days * lower_velocity)
            + (upper_position_weight * upper_position)
            + (upper_velocity_weight * interval_days * upper_velocity)
        )

    @classmethod
    def _sky_track_radec_at_time(cls, entry: Mapping[str, object], observation_time: datetime) -> tuple[float, float] | None:
        object_position = cls._interpolate_state_vector_position(entry.get("object_samples", ()), observation_time)
        earth_position = cls._interpolate_state_vector_position(entry.get("earth_samples", ()), observation_time)
        if object_position is None or earth_position is None:
            return None
        return cls._geocentric_radec_from_positions(object_position, earth_position)

    @staticmethod
    def _angular_separation_deg(ra_a_deg: float, dec_a_deg: float, ra_b_deg: float, dec_b_deg: float) -> float:
        vector_a = KnownObjectOrbit3DDialog._radec_unit_vector(ra_a_deg, dec_a_deg)
        vector_b = KnownObjectOrbit3DDialog._radec_unit_vector(ra_b_deg, dec_b_deg)
        dot = max(-1.0, min(1.0, float(np.dot(vector_a, vector_b))))
        return math.degrees(math.acos(dot))

    @staticmethod
    def _sky_track_projection_center(ra_values: np.ndarray, dec_values: np.ndarray) -> tuple[float, float]:
        if ra_values.size == 0 or dec_values.size == 0:
            return (0.0, 0.0)
        vectors = np.array([
            KnownObjectOrbit3DDialog._radec_unit_vector(float(ra_deg), float(dec_deg))
            for ra_deg, dec_deg in zip(ra_values, dec_values)
        ], dtype=float)
        mean_vector = np.mean(vectors, axis=0)
        if float(np.linalg.norm(mean_vector)) <= 1.0e-6:
            mid_index = int(len(vectors) // 2)
            mean_vector = vectors[mid_index]
        radec = KnownObjectOrbit3DDialog._unit_vector_to_radec(mean_vector)
        return radec if radec is not None else (float(ra_values[0]), float(dec_values[0]))

    @staticmethod
    def _project_sky_radec(
        ra_values: np.ndarray,
        dec_values: np.ndarray,
        center_ra_deg: float,
        center_dec_deg: float,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Project sky coordinates with a trajectory-centered azimuthal equidistant map."""
        if ra_values.size == 0 or dec_values.size == 0:
            return (np.zeros(0, dtype=float), np.zeros(0, dtype=float), np.zeros(0, dtype=bool))
        center_ra_rad = math.radians(float(center_ra_deg))
        center_dec_rad = math.radians(float(center_dec_deg))
        ra_radians = np.radians(np.asarray(ra_values, dtype=float))
        dec_radians = np.radians(np.asarray(dec_values, dtype=float))
        delta_ra = ((ra_radians - center_ra_rad + math.pi) % (2.0 * math.pi)) - math.pi
        sin_center_dec = math.sin(center_dec_rad)
        cos_center_dec = math.cos(center_dec_rad)
        sin_dec = np.sin(dec_radians)
        cos_dec = np.cos(dec_radians)
        cos_distance = np.clip(
            (sin_center_dec * sin_dec) + (cos_center_dec * cos_dec * np.cos(delta_ra)),
            -1.0,
            1.0,
        )
        angular_distance = np.arccos(cos_distance)
        sin_distance = np.sin(angular_distance)
        scale = np.ones_like(angular_distance)
        ordinary = np.abs(sin_distance) > 1.0e-10
        scale[ordinary] = angular_distance[ordinary] / sin_distance[ordinary]
        x_radians = scale * cos_dec * np.sin(delta_ra)
        y_radians = scale * (
            (cos_center_dec * sin_dec)
            - (sin_center_dec * cos_dec * np.cos(delta_ra))
        )
        x_values = np.degrees(x_radians)
        y_values = np.degrees(y_radians)
        valid = (
            np.isfinite(x_values)
            & np.isfinite(y_values)
            & np.isfinite(angular_distance)
            & (angular_distance < math.pi - 1.0e-8)
        )
        return (x_values.astype(float), y_values.astype(float), valid)

    @staticmethod
    def _point_to_segment_distance(
        point: tuple[float, float],
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> float:
        point_vector = np.array(point, dtype=float)
        start_vector = np.array(start, dtype=float)
        segment_vector = np.array(end, dtype=float) - start_vector
        denominator = float(np.dot(segment_vector, segment_vector))
        if denominator <= 1.0e-18:
            return float(np.linalg.norm(point_vector - start_vector))
        fraction = max(0.0, min(1.0, float(np.dot(point_vector - start_vector, segment_vector)) / denominator))
        return float(np.linalg.norm(point_vector - (start_vector + (fraction * segment_vector))))

    @classmethod
    def _projected_sky_track_point(
        cls,
        entry: Mapping[str, object],
        observation_time: datetime,
        center_ra_deg: float,
        center_dec_deg: float,
    ) -> tuple[float, float] | None:
        radec = cls._sky_track_radec_at_time(entry, observation_time)
        if radec is None:
            return None
        x_values, y_values, valid = cls._project_sky_radec(
            np.array([radec[0]], dtype=float),
            np.array([radec[1]], dtype=float),
            center_ra_deg,
            center_dec_deg,
        )
        if x_values.size == 0 or y_values.size == 0 or not bool(valid[0]):
            return None
        return (float(x_values[0]), float(y_values[0]))

    @classmethod
    def _adaptive_projected_sky_track(
        cls,
        entry: Mapping[str, object],
        center_ra_deg: float,
        center_dec_deg: float,
    ) -> tuple[np.ndarray, np.ndarray, tuple[datetime, ...]]:
        base_times = tuple(entry.get("times", ()))
        if not base_times:
            return (np.zeros(0, dtype=float), np.zeros(0, dtype=float), ())
        cache: dict[float, tuple[float, float] | None] = {}

        def evaluate(observation_time: datetime) -> tuple[float, float] | None:
            timestamp = float(observation_time.timestamp())
            if timestamp not in cache:
                cache[timestamp] = cls._projected_sky_track_point(
                    entry,
                    observation_time,
                    center_ra_deg,
                    center_dec_deg,
                )
            return cache[timestamp]

        first_point = evaluate(base_times[0])
        if first_point is None:
            return (np.zeros(0, dtype=float), np.zeros(0, dtype=float), ())
        samples: list[tuple[datetime, float, float]] = [(base_times[0], first_point[0], first_point[1])]

        def append_interval(
            start_time: datetime,
            start_point: tuple[float, float],
            end_time: datetime,
            end_point: tuple[float, float],
            depth: int,
        ) -> None:
            midpoint_time = start_time + ((end_time - start_time) / 2)
            midpoint = evaluate(midpoint_time)
            if midpoint is None:
                samples.append((end_time, end_point[0], end_point[1]))
                return
            deviation = cls._point_to_segment_distance(midpoint, start_point, end_point)
            if deviation > _KNOWN_OBJECT_SKY_TRACK_ADAPTIVE_ERROR_DEG and depth < _KNOWN_OBJECT_SKY_TRACK_ADAPTIVE_MAX_DEPTH:
                append_interval(start_time, start_point, midpoint_time, midpoint, depth + 1)
                append_interval(midpoint_time, midpoint, end_time, end_point, depth + 1)
                return
            samples.append((end_time, end_point[0], end_point[1]))

        for interval_start, interval_end in zip(base_times, base_times[1:]):
            interval_duration = interval_end - interval_start
            subdivision_times = [
                interval_start + (interval_duration * (index / _KNOWN_OBJECT_SKY_TRACK_BASE_SUBDIVISIONS))
                for index in range(_KNOWN_OBJECT_SKY_TRACK_BASE_SUBDIVISIONS + 1)
            ]
            for start_time, end_time in zip(subdivision_times, subdivision_times[1:]):
                start_point = evaluate(start_time)
                end_point = evaluate(end_time)
                if start_point is None or end_point is None:
                    continue
                if samples[-1][0] != start_time:
                    samples.append((start_time, start_point[0], start_point[1]))
                append_interval(start_time, start_point, end_time, end_point, 0)
        if not samples:
            return (np.zeros(0, dtype=float), np.zeros(0, dtype=float), ())
        return (
            np.array([sample[1] for sample in samples], dtype=float),
            np.array([sample[2] for sample in samples], dtype=float),
            tuple(sample[0] for sample in samples),
        )

    def _create_sky_track_plot_widget(self):
        plot_widget = pg.PlotWidget(background="#08101d")
        plot_widget.setMinimumHeight(170)
        plot_item = plot_widget.getPlotItem()
        plot_item.showGrid(x=True, y=True, alpha=0.18)
        plot_item.setMenuEnabled(False)
        plot_item.setLabel("bottom", "East offset (deg)", color="#edf4ff")
        plot_item.setLabel("left", "North offset (deg)", color="#edf4ff")
        plot_item.setTitle("Sky Track", color="#f6fbff")
        plot_item.getViewBox().setMouseEnabled(x=True, y=True)
        plot_item.getViewBox().setAspectLocked(lock=True)
        for axis_name in ("bottom", "left"):
            axis = plot_item.getAxis(axis_name)
            axis.setTextPen(pg.mkPen("#dbe7ff"))
            axis.setPen(pg.mkPen("#425a82"))
        return plot_widget

    def _reset_sky_track_plot(self):
        plot_item = self._sky_track_plot.getPlotItem()
        legend = getattr(plot_item, "legend", None)
        if legend is not None and legend.scene() is not None:
            legend.scene().removeItem(legend)
            plot_item.legend = None
        plot_item.clear()
        plot_item.showGrid(x=True, y=True, alpha=0.18)
        plot_item.setLabel("bottom", "East offset (deg)", color="#edf4ff")
        plot_item.setLabel("left", "North offset (deg)", color="#edf4ff")
        plot_item.getViewBox().setMouseEnabled(x=True, y=True)
        plot_item.getViewBox().setAspectLocked(lock=True)
        for axis_name in ("bottom", "left"):
            axis = plot_item.getAxis(axis_name)
            axis.setTextPen(pg.mkPen("#dbe7ff"))
            axis.setPen(pg.mkPen("#425a82"))
        return plot_item

    def _create_topdown_plot_widget(self):
        plot_widget = pg.PlotWidget(background="#08101d")
        plot_widget.setMinimumHeight(220)
        plot_item = plot_widget.getPlotItem()
        plot_item.showGrid(x=True, y=True, alpha=0.22)
        plot_item.setMenuEnabled(False)
        plot_item.setLabel("bottom", "X (AU)", color="#edf4ff")
        plot_item.setLabel("left", "Y (AU)", color="#edf4ff")
        plot_item.setTitle("Heliocentric top-down view", color="#f6fbff")
        plot_item.getViewBox().setMouseEnabled(x=True, y=True)
        plot_item.getViewBox().setAspectLocked(lock=False)
        for axis_name in ("bottom", "left"):
            axis = plot_item.getAxis(axis_name)
            axis.setTextPen(pg.mkPen("#dbe7ff"))
            axis.setPen(pg.mkPen("#425a82"))
        plot_item.addLegend(
            offset=(8, 8),
            labelTextColor="#eef5ff",
            brush=pg.mkBrush(13, 20, 36, 235),
            pen=pg.mkPen("#314669"),
        )
        return plot_widget

    def _reset_topdown_plot(self):
        plot_item = self._topdown_plot.getPlotItem()
        legend = getattr(plot_item, "legend", None)
        if legend is not None and legend.scene() is not None:
            legend.scene().removeItem(legend)
            plot_item.legend = None
        plot_item.clear()
        plot_item.showGrid(x=True, y=True, alpha=0.22)
        plot_item.setTitle("Heliocentric top-down view", color="#f6fbff")
        plot_item.setLabel("bottom", "X (AU)", color="#edf4ff")
        plot_item.setLabel("left", "Y (AU)", color="#edf4ff")
        plot_item.getViewBox().setMouseEnabled(x=True, y=True)
        plot_item.getViewBox().setAspectLocked(lock=False)
        for axis_name in ("bottom", "left"):
            axis = plot_item.getAxis(axis_name)
            axis.setTextPen(pg.mkPen("#dbe7ff"))
            axis.setPen(pg.mkPen("#425a82"))
        plot_item.addLegend(
            offset=(8, 8),
            labelTextColor="#eef5ff",
            brush=pg.mkBrush(13, 20, 36, 235),
            pen=pg.mkPen("#314669"),
        )
        return plot_item

    @staticmethod
    def _magnitude_series_points(samples) -> tuple[list[datetime], list[float]]:
        valid_samples = [sample for sample in samples if getattr(sample, "literature_magnitude", None) is not None]
        return (
            [sample.observation_time for sample in valid_samples],
            [float(sample.literature_magnitude) for sample in valid_samples],
        )

    @staticmethod
    def _nearest_magnitude_sample(samples, observation_time: datetime):
        if not samples:
            return None
        return min(samples, key=lambda sample: abs((sample.observation_time - observation_time).total_seconds()))

    def _create_time_series_plot_widget(self, title: str, y_label: str, *, invert_y: bool = False):
        axis_item = _UtcDateAxisItem(orientation="bottom")
        plot_widget = pg.PlotWidget(axisItems={"bottom": axis_item}, background="#08101d")
        plot_widget.setMinimumHeight(170)
        plot_item = plot_widget.getPlotItem()
        plot_item.showGrid(x=True, y=True, alpha=0.22)
        plot_item.setMenuEnabled(False)
        plot_item.setLabel("bottom", "", color="#edf4ff")
        plot_item.setLabel("left", y_label, color="#edf4ff")
        plot_item.setTitle(title, color="#f6fbff")
        plot_item.getViewBox().setMouseEnabled(x=True, y=True)
        plot_item.getViewBox().invertY(invert_y)
        for axis_name in ("bottom", "left"):
            axis = plot_item.getAxis(axis_name)
            axis.setTextPen(pg.mkPen("#dbe7ff"))
            axis.setPen(pg.mkPen("#425a82"))
            if hasattr(axis, "enableAutoSIPrefix"):
                axis.enableAutoSIPrefix(False)
        return plot_widget

    @staticmethod
    def _time_series_plot_title(plot_kind: str) -> str:
        return "Distance" if plot_kind == "distance" else "Literature magnitude"

    def eventFilter(self, watched: object, event: object) -> bool:
        if watched is self._gl_panel_container and isinstance(event, QEvent) and event.type() == QEvent.Type.Resize:
            self._position_periods_panel()
        if (
            hasattr(self, "_sky_track_plot")
            and watched is self._sky_track_plot.viewport()
            and isinstance(event, QEvent)
            and event.type() == QEvent.Type.Resize
        ):
            self._apply_sky_track_view_fit()
        if isinstance(event, QEvent) and event.type() in {QEvent.Type.Leave, QEvent.Type.Hide}:
            if watched is self._topdown_plot.viewport():
                self._handle_plot_hover_leave(None)
            elif watched is self._distance_plot.viewport():
                self._clear_time_series_hover("distance")
            elif watched is self._magnitude_plot.viewport():
                self._clear_time_series_hover("magnitude")
        return super().eventFilter(watched, event)

    @staticmethod
    def _datetime_to_time_axis_value(observation_time: datetime) -> float:
        if observation_time.tzinfo is None:
            observation_time = observation_time.replace(tzinfo=UTC)
        else:
            observation_time = observation_time.astimezone(UTC)
        return float(observation_time.timestamp())

    @staticmethod
    def _time_axis_value_to_datetime(x_value: float) -> datetime:
        return datetime.fromtimestamp(float(x_value), tz=UTC)

    @staticmethod
    def _time_axis_series(samples, values, label: str, unit: str, color: str) -> tuple[str, np.ndarray, np.ndarray, str, str] | None:
        if not samples or not values:
            return None
        x_values = np.array([KnownObjectOrbit3DDialog._datetime_to_time_axis_value(sample_time) for sample_time in samples], dtype=float)
        y_values = np.array([float(value) for value in values], dtype=float)
        if x_values.size == 0 or y_values.size == 0 or x_values.size != y_values.size:
            return None
        return (label, x_values, y_values, unit, color)

    @staticmethod
    def _format_hover_datetime(x_value: float) -> str:
        hover_time = KnownObjectOrbit3DDialog._time_axis_value_to_datetime(x_value)
        return hover_time.strftime("%Y-%m-%d")

    @staticmethod
    def _format_hover_datetime_compact(x_value: float) -> str:
        hover_time = KnownObjectOrbit3DDialog._time_axis_value_to_datetime(x_value)
        return hover_time.strftime("%Y-%m-%d")

    def _set_plot_hover_text(self, text: str) -> None:
        self._plot_hover_label.setText(text)

    def _create_time_series_hover_artists(self, plot_widget) -> dict[str, object]:
        plot_item = plot_widget.getPlotItem()
        guide_line = pg.InfiniteLine(
            angle=90,
            movable=False,
            pen=pg.mkPen("#dce8ff", width=1.0, style=Qt.PenStyle.DashLine),
        )
        guide_line.hide()
        plot_item.addItem(guide_line, ignoreBounds=True)
        marker_item = pg.ScatterPlotItem(pxMode=True)
        marker_item.hide()
        plot_item.addItem(marker_item, ignoreBounds=True)
        x_annotation = pg.TextItem(anchor=(0.5, 0.0), color="#dce8ff", border=pg.mkPen("#213355"), fill=pg.mkBrush(9, 18, 35, 240))
        x_annotation.hide()
        plot_item.addItem(x_annotation, ignoreBounds=True)
        return {
            "line": guide_line,
            "marker_item": marker_item,
            "annotations": [],
            "x_annotation": x_annotation,
            "x_text": "",
            "series_texts": [],
        }

    def _create_time_series_playback_item(self, plot_widget):
        playback_item = pg.ScatterPlotItem(pxMode=True)
        playback_item.hide()
        plot_widget.getPlotItem().addItem(playback_item, ignoreBounds=True)
        return playback_item

    @staticmethod
    def _interpolated_time_series_value(x_value: float, x_values: np.ndarray, y_values: np.ndarray) -> float:
        if x_values.size <= 1:
            return float(y_values[0])
        return float(np.interp(float(x_value), x_values, y_values, left=y_values[0], right=y_values[-1]))

    def _ensure_time_series_hover_artist_capacity(self, plot_widget, artist_bundle: dict[str, object], count: int) -> None:
        annotations = artist_bundle["annotations"]
        plot_item = plot_widget.getPlotItem()
        while len(annotations) < count:
            annotation = pg.TextItem(anchor=(0.0, 0.5), color="#f8fbff", border=pg.mkPen("#213355"), fill=pg.mkBrush(9, 18, 35, 240))
            annotation.hide()
            plot_item.addItem(annotation, ignoreBounds=True)
            annotations.append(annotation)

    def _hide_time_series_hover_artists(self, artist_bundle: dict[str, object]) -> None:
        if not artist_bundle:
            return
        line = artist_bundle.get("line")
        if line is not None:
            line.hide()
        marker_item = artist_bundle.get("marker_item")
        if marker_item is not None:
            marker_item.hide()
            marker_item.setData([], [])
        x_annotation = artist_bundle.get("x_annotation")
        if x_annotation is not None:
            x_annotation.hide()
            x_annotation.setText("")
        artist_bundle["x_text"] = ""
        artist_bundle["series_texts"] = []
        for annotation in artist_bundle.get("annotations", []):
            annotation.hide()
            annotation.setText("")

    def _time_series_bottom_y(self, plot_widget) -> float:
        y_min, y_max = plot_widget.getPlotItem().getViewBox().viewRange()[1]
        inverted = bool(plot_widget.getPlotItem().getViewBox().state.get("yInverted", False))
        return float(max(y_min, y_max) if inverted else min(y_min, y_max))

    def _time_series_top_y(self, plot_widget) -> float:
        y_min, y_max = plot_widget.getPlotItem().getViewBox().viewRange()[1]
        inverted = bool(plot_widget.getPlotItem().getViewBox().state.get("yInverted", False))
        return float(min(y_min, y_max) if inverted else max(y_min, y_max))

    def _update_time_series_hover_artists(
        self,
        plot_widget,
        x_value: float,
        series_entries: list[tuple[str, np.ndarray, np.ndarray, str, str]],
        artist_bundle: dict[str, object],
    ) -> None:
        if not series_entries or not artist_bundle:
            self._hide_time_series_hover_artists(artist_bundle)
            return
        plot_item = plot_widget.getPlotItem()
        guide_line = artist_bundle["line"]
        guide_line.setPos(float(x_value))
        guide_line.show()
        x_annotation = artist_bundle.get("x_annotation")
        x_label_text = self._format_hover_datetime_compact(x_value)
        if x_annotation is not None:
            x_annotation.setText(x_label_text)
            x_annotation.setPos(float(x_value), self._time_series_bottom_y(plot_widget))
            x_annotation.show()
        artist_bundle["x_text"] = x_label_text
        self._ensure_time_series_hover_artist_capacity(plot_widget, artist_bundle, len(series_entries))
        annotations = artist_bundle["annotations"]
        x_min, x_max = plot_item.getViewBox().viewRange()[0]
        y_min, y_max = plot_item.getViewBox().viewRange()[1]
        x_offset = max(abs(float(x_max) - float(x_min)) * 0.018, 120.0)
        y_offset = max(abs(float(y_max) - float(y_min)) * 0.04, 0.04)
        marker_x_values: list[float] = []
        marker_y_values: list[float] = []
        marker_brushes: list[object] = []
        marker_pens: list[object] = []
        series_texts: list[str] = []
        for index, (label, x_values, y_values, unit, color) in enumerate(series_entries):
            value = self._interpolated_time_series_value(x_value, x_values, y_values)
            marker_x_values.append(float(x_value))
            marker_y_values.append(value)
            marker_brushes.append(pg.mkBrush(color))
            marker_pens.append(pg.mkPen("#f8fbff", width=1.0))
            annotation_text = f"{x_label_text} | {label}: {value:.3f} {unit}"
            series_texts.append(annotation_text)
            annotation = annotations[index]
            annotation.setText(annotation_text, color="#f8fbff")
            if float(x_value) <= ((float(x_min) + float(x_max)) * 0.5):
                annotation.setAnchor((0.0, 0.5))
                annotation.setPos(float(x_value) + x_offset, value + ((index - ((len(series_entries) - 1) / 2.0)) * y_offset))
            else:
                annotation.setAnchor((1.0, 0.5))
                annotation.setPos(float(x_value) - x_offset, value + ((index - ((len(series_entries) - 1) / 2.0)) * y_offset))
            annotation.show()
        marker_item = artist_bundle.get("marker_item")
        if marker_item is not None:
            marker_item.setData(
                x=marker_x_values,
                y=marker_y_values,
                size=[8] * len(marker_x_values),
                pen=marker_pens,
                brush=marker_brushes,
            )
            marker_item.show()
        artist_bundle["series_texts"] = series_texts
        for annotation in annotations[len(series_entries):]:
            annotation.hide()
            annotation.setText("")

    def _clear_time_series_hover(self, plot_kind: str | None = None) -> None:
        if self._time_series_plot_refreshing:
            return
        if plot_kind in {None, "distance"}:
            self._hide_time_series_hover_artists(self._distance_hover_artists)
        if plot_kind in {None, "magnitude"}:
            self._hide_time_series_hover_artists(self._magnitude_hover_artists)
        distance_visible = bool(self._distance_hover_artists.get("line") and self._distance_hover_artists["line"].isVisible())
        magnitude_visible = bool(self._magnitude_hover_artists.get("line") and self._magnitude_hover_artists["line"].isVisible())
        if not distance_visible and not magnitude_visible:
            self._set_plot_hover_text("Hover a plot to inspect values.")

    def _set_time_series_hover(self, plot_kind: str, x_value: float | None) -> None:
        if self._time_series_plot_refreshing:
            return
        if plot_kind == "distance":
            target_plot = self._distance_plot
            series_entries = self._distance_hover_series
            artist_bundle = self._distance_hover_artists
            other_bundle = self._magnitude_hover_artists
        else:
            target_plot = self._magnitude_plot
            series_entries = self._magnitude_hover_series
            artist_bundle = self._magnitude_hover_artists
            other_bundle = self._distance_hover_artists
        self._hide_time_series_hover_artists(other_bundle)
        if x_value is None:
            self._hide_time_series_hover_artists(artist_bundle)
            self._set_plot_hover_text("Hover a plot to inspect values.")
            return
        self._update_time_series_hover_artists(target_plot, float(x_value), series_entries, artist_bundle)
        self._set_plot_hover_text(self._format_time_series_hover(self._time_series_plot_title(plot_kind), float(x_value), series_entries))

    def _handle_distance_plot_mouse_moved(self, event) -> None:
        if self._time_series_plot_refreshing:
            return
        if not event:
            return
        scene_position = event[0]
        if not self._distance_plot.getPlotItem().getViewBox().sceneBoundingRect().contains(scene_position):
            self._clear_time_series_hover("distance")
            return
        mouse_point = self._distance_plot.getPlotItem().getViewBox().mapSceneToView(scene_position)
        self._set_time_series_hover("distance", float(mouse_point.x()))

    def _handle_magnitude_plot_mouse_moved(self, event) -> None:
        if self._time_series_plot_refreshing:
            return
        if not event:
            return
        scene_position = event[0]
        if not self._magnitude_plot.getPlotItem().getViewBox().sceneBoundingRect().contains(scene_position):
            self._clear_time_series_hover("magnitude")
            return
        mouse_point = self._magnitude_plot.getPlotItem().getViewBox().mapSceneToView(scene_position)
        self._set_time_series_hover("magnitude", float(mouse_point.x()))

    def _jump_playback_to_time_series_x(self, plot_kind: str, x_value: float) -> None:
        bounded_time = self._clamp_playback_time(self._time_axis_value_to_datetime(float(x_value)))
        bounded_x_value = self._datetime_to_time_axis_value(bounded_time)
        self._set_time_series_hover(plot_kind, bounded_x_value)
        self._set_playback_time(bounded_time, update_camera=self._camera_mode_requires_tracking())

    def _handle_distance_plot_mouse_clicked(self, event) -> None:
        if self._time_series_plot_refreshing or event is None:
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        view_box = self._distance_plot.getPlotItem().getViewBox()
        scene_position = event.scenePos()
        if not view_box.sceneBoundingRect().contains(scene_position):
            return
        mouse_point = view_box.mapSceneToView(scene_position)
        self._jump_playback_to_time_series_x("distance", float(mouse_point.x()))
        event.accept()

    def _handle_magnitude_plot_mouse_clicked(self, event) -> None:
        if self._time_series_plot_refreshing or event is None:
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        view_box = self._magnitude_plot.getPlotItem().getViewBox()
        scene_position = event.scenePos()
        if not view_box.sceneBoundingRect().contains(scene_position):
            return
        mouse_point = view_box.mapSceneToView(scene_position)
        self._jump_playback_to_time_series_x("magnitude", float(mouse_point.x()))
        event.accept()

    def _handle_sky_track_plot_mouse_clicked(self, event) -> None:
        if event is None or event.button() != Qt.MouseButton.LeftButton:
            return
        view_box = self._sky_track_plot.getPlotItem().getViewBox()
        scene_position = event.scenePos()
        if not view_box.sceneBoundingRect().contains(scene_position):
            return
        x_values = getattr(self, "_sky_track_projected_x", np.zeros(0, dtype=float))
        y_values = getattr(self, "_sky_track_projected_y", np.zeros(0, dtype=float))
        track_times = tuple(getattr(self, "_sky_track_projected_times", ()))
        if x_values.size == 0 or y_values.size == 0 or not track_times:
            return
        mouse_point = view_box.mapSceneToView(scene_position)
        distances = np.hypot(x_values - float(mouse_point.x()), y_values - float(mouse_point.y()))
        nearest_index = int(np.argmin(distances))
        if nearest_index < 0 or nearest_index >= len(track_times):
            return
        self._set_playback_time(track_times[nearest_index], update_camera=self._camera_mode_requires_tracking())
        event.accept()

    def _handle_topdown_plot_mouse_moved(self, event) -> None:
        if not event:
            return
        scene_position = event[0]
        view_box = self._topdown_plot.getPlotItem().getViewBox()
        if not view_box.sceneBoundingRect().contains(scene_position):
            self._handle_plot_hover_leave(None)
            return
        mouse_point = view_box.mapSceneToView(scene_position)
        self._set_plot_hover_text(f"Heliocentric top-down | X {float(mouse_point.x()):.3f} AU | Y {float(mouse_point.y()):.3f} AU")

    def _handle_plot_hover_leave(self, _event) -> None:
        self._set_plot_hover_text("Hover a plot to inspect values.")

    def _handle_plot_hover(self, event) -> None:
        if event.inaxes is None or event.xdata is None or event.ydata is None:
            self._set_plot_hover_text("Hover a plot to inspect values.")
            return
        if hasattr(self, "_ax_xy") and event.inaxes is self._ax_xy:
            self._set_plot_hover_text(f"Heliocentric top-down | X {event.xdata:.3f} AU | Y {event.ydata:.3f} AU")
            return
        self._set_plot_hover_text("Hover a plot to inspect values.")

    def _format_time_series_hover(
        self,
        title: str,
        x_value: float,
        series_entries: list[tuple[str, np.ndarray, np.ndarray, str, str]],
    ) -> str:
        if not series_entries:
            return f"{title} | No data at cursor"
        formatted_date = self._format_hover_datetime(x_value)
        fragments = [f"{title} | {formatted_date}"]
        for label, x_values, y_values, unit, _color in series_entries:
            if x_values.size == 0 or y_values.size == 0:
                continue
            value = self._interpolated_time_series_value(x_value, x_values, y_values)
            fragments.append(f"{label}: {value:.3f} {unit}")
        return " | ".join(fragments)

    def _current_playback_time(self) -> datetime:
        if hasattr(self, "_playback_time"):
            return self._clamp_playback_time(self._playback_time)
        if self._timeline_times:
            return self._timeline_times[0]
        return self._context.reference_time

    def _nearest_body_sample(self, body: HeliocentricReferenceBody, observation_time: datetime) -> tuple[float, float, float] | None:
        interpolated_position = self._interpolate_position(body.path_samples, observation_time)
        if interpolated_position is None:
            return None
        return (float(interpolated_position[0]), float(interpolated_position[1]), float(interpolated_position[2]))

    @staticmethod
    def _format_orbital_period(period_days: float | None) -> str:
        if period_days is None or not math.isfinite(period_days) or period_days <= 0.0:
            return "open trajectory"
        if period_days >= 320.0:
            return f"{period_days / 365.25:.2f} y"
        return f"{period_days:.1f} d"

    def _period_entries(self) -> list[tuple[str, str, str]]:
        entries_with_order: list[tuple[str, str, str, float | None]] = []
        if self._context_targets and self._is_target_visible(0):
            entries_with_order.append(
                (
                    self._primary_target_label(),
                    self._format_orbital_period(self._context.object_orbital_period_days),
                    str(self._primary_target_style()["hex"]),
                    self._context.object_orbital_period_days,
                )
            )
        entries_with_order.append(
            (
                "Earth",
                self._format_orbital_period(self._context.earth_orbital_period_days),
                str(self._body_style("earth")["hex"]),
                self._context.earth_orbital_period_days,
            )
        )
        for comparison_index, track in enumerate(self._comparison_tracks(), start=1):
            if not self._is_target_visible(comparison_index):
                continue
            entries_with_order.append(
                (
                    track.object_label,
                    self._format_orbital_period(track.orbital_period_days),
                    str(self._comparison_track_style(comparison_index - 1)["hex"]),
                    track.orbital_period_days,
                )
            )
        for body in self._additional_bodies():
            entries_with_order.append((body.label, self._format_orbital_period(body.orbital_period_days), str(self._body_style(body.key)["hex"]), body.orbital_period_days))
        entries_with_order.sort(
            key=lambda entry: (
                entry[3] is None or not math.isfinite(float(entry[3])) or float(entry[3]) <= 0.0,
                float(entry[3]) if entry[3] is not None and math.isfinite(float(entry[3])) and float(entry[3]) > 0.0 else float("inf"),
                entry[0].lower(),
            )
        )
        return [(label, period_text, color_hex) for label, period_text, color_hex, _period_days in entries_with_order]

    def _update_periods_label(self) -> None:
        while self._periods_panel_layout.count():
            item = self._periods_panel_layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                while child_layout.count():
                    child_item = child_layout.takeAt(0)
                    child_widget = child_item.widget()
                    if child_widget is not None:
                        child_widget.deleteLater()
        title_label = QLabel("Orbital Periods", self._periods_panel)
        title_label.setStyleSheet("color: #eef5ff; font-weight: 600;")
        self._periods_panel_layout.addWidget(title_label)
        for label_text, period_text, color_hex in self._period_entries():
            row_widget = QWidget(self._periods_panel)
            row_layout = QHBoxLayout()
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            swatch = QFrame(row_widget)
            swatch.setFixedSize(10, 10)
            swatch.setStyleSheet(f"background-color: {color_hex}; border-radius: 5px; border: 1px solid rgba(255, 255, 255, 0.18);")
            name_label = QLabel(label_text, row_widget)
            name_label.setStyleSheet("color: #edf4ff;")
            value_label = QLabel(period_text, row_widget)
            value_label.setStyleSheet("color: #c6d9ff;")
            row_layout.addWidget(swatch)
            row_layout.addWidget(name_label, stretch=1)
            row_layout.addWidget(value_label)
            row_widget.setLayout(row_layout)
            self._periods_panel_layout.addWidget(row_widget)
        self._position_periods_panel()
        self._periods_panel.setVisible(self._show_periods_checkbox.isChecked())
        if self._periods_panel.isVisible():
            self._periods_panel.raise_()

    def _sync_play_button_icon(self, is_checked: bool) -> None:
        icon_key = QStyle.StandardPixmap.SP_MediaPause if is_checked else QStyle.StandardPixmap.SP_MediaPlay
        self._play_button.setIcon(self.style().standardIcon(icon_key))
        self._play_button.setToolTip("Pause timeline" if is_checked else "Play timeline")

    @staticmethod
    def _body_style(body_key: str) -> dict[str, object]:
        return _KNOWN_OBJECT_3D_BODY_STYLES.get(body_key, {"line": (0.80, 0.80, 0.80), "glow": (0.55, 0.55, 0.55), "hex": "#d0d0d0"})

    def _comparison_track_style(self, index: int) -> dict[str, object]:
        target_index = index + 1
        if target_index < len(self._context_targets):
            return self._target_style_for_detection(self._context_targets[target_index].detection)
        return self._target_style_for_detection(None)

    @staticmethod
    def _style_from_hex(color_hex: str) -> dict[str, object]:
        color = QColor(color_hex)
        if not color.isValid():
            color = QColor(_KNOWN_OBJECT_3D_OBJECT_STYLE["hex"])
        red, green, blue, _alpha = color.getRgbF()
        glow = (
            min(1.0, (red * 0.78) + 0.10),
            min(1.0, (green * 0.78) + 0.10),
            min(1.0, (blue * 0.78) + 0.10),
        )
        return {"line": (red, green, blue), "glow": glow, "hex": color.name().lower()}

    def _target_style_for_detection(self, detection: SolarSystemDetection | None) -> dict[str, object]:
        object_type = "" if detection is None or detection.object_type is None else str(detection.object_type).strip().lower()
        if "comet" in object_type:
            return self._style_from_hex(self._comet_color_hex)
        return self._style_from_hex(self._asteroid_color_hex)

    def _primary_target(self) -> AsteroidOrbitContextTarget | None:
        return self._context_targets[0] if self._context_targets else None

    def _primary_target_detection(self) -> SolarSystemDetection | None:
        primary_target = self._primary_target()
        if primary_target is not None:
            return primary_target.detection
        return self._detection

    def _primary_target_label(self) -> str:
        detection = self._primary_target_detection()
        if detection is not None:
            return detection.name or detection.designation or self._context.object_label or "Known Object"
        return self._context.object_label or "Trajectory View"

    def _sync_primary_target_state(self) -> None:
        primary_target = self._primary_target()
        if primary_target is not None:
            self._detection = primary_target.detection
            self._frame_measurements = tuple(primary_target.frame_measurements)
        self.setWindowTitle(f"3D View - {self._primary_target_label()}")

    def _primary_target_style(self) -> dict[str, object]:
        return self._target_style_for_detection(self._primary_target_detection())

    def _sync_object_color_button_styles(self) -> None:
        for button, color_hex in ((self._asteroid_color_button, self._asteroid_color_hex), (self._comet_color_button, self._comet_color_hex)):
            button.setStyleSheet(
                "background-color: #10182d;"
                "color: #f3f7ff;"
                "border: 1px solid #2d436f;"
                "padding: 5px 12px;"
                "border-radius: 4px;"
                f"border-left: 16px solid {color_hex};"
            )

    def _pick_object_type_color(self, title: str, initial_hex: str) -> str | None:
        selected = QColorDialog.getColor(QColor(initial_hex), self, title)
        if not selected.isValid():
            return None
        return selected.name().lower()

    def _handle_asteroid_color_button_clicked(self) -> None:
        selected = self._pick_object_type_color("Choose Asteroid Color", self._asteroid_color_hex)
        if selected is None:
            return
        self._asteroid_color_hex = selected
        self._sync_object_color_button_styles()
        self._handle_label_style_changed()
        self._update_periods_label()

    def _handle_comet_color_button_clicked(self) -> None:
        selected = self._pick_object_type_color("Choose Comet Color", self._comet_color_hex)
        if selected is None:
            return
        self._comet_color_hex = selected
        self._sync_object_color_button_styles()
        self._handle_label_style_changed()
        self._update_periods_label()

    def _add_gl_label(self, key: str, text: str, position: tuple[float, float, float], color: str) -> None:
        if self._gl_view is None or gl is None or not self._show_labels_checkbox.isChecked():
            return
        label_class = getattr(gl, "GLTextItem", None)
        if label_class is None:
            return
        label_item = label_class(
            pos=np.array(self._label_offset_position(position), dtype=float),
            text=text,
            color=QColor(color),
            font=self._current_label_font(),
        )
        self._gl_label_items[key] = label_item
        self._gl_scene_items.append(label_item)
        self._gl_view.addItem(label_item)

    def _set_gl_label_position(self, key: str, position: tuple[float, float, float]) -> None:
        label_item = self._gl_label_items.get(key)
        if label_item is None:
            return
        try:
            label_item.setData(pos=np.array(self._label_offset_position(position), dtype=float), font=self._current_label_font())
        except Exception:
            return

    def _populate_table(self) -> None:
        self._table.clearContents()
        object_samples = tuple(self._context.observation_object_samples)
        earth_samples = tuple(self._context.observation_earth_samples)
        row_count = min(len(self._frame_measurements), len(object_samples), len(earth_samples))
        self._table.setRowCount(row_count)
        for row_index in range(row_count):
            measurement = self._frame_measurements[row_index]
            object_sample = object_samples[row_index]
            earth_sample = earth_samples[row_index]
            sun_distance_au = self._vector_norm(object_sample.x_au, object_sample.y_au, object_sample.z_au)
            earth_distance_au = self._vector_norm(
                object_sample.x_au - earth_sample.x_au,
                object_sample.y_au - earth_sample.y_au,
                object_sample.z_au - earth_sample.z_au,
            )
            items = [
                QTableWidgetItem(f"F{row_index + 1}"),
                QTableWidgetItem(measurement.observation_time.isoformat()),
                QTableWidgetItem(f"{object_sample.x_au:.3f}"),
                QTableWidgetItem(f"{object_sample.y_au:.3f}"),
                QTableWidgetItem(f"{object_sample.z_au:.3f}"),
                QTableWidgetItem(f"{earth_sample.x_au:.3f}"),
                QTableWidgetItem(f"{earth_sample.y_au:.3f}"),
                QTableWidgetItem(f"{earth_sample.z_au:.3f}"),
                QTableWidgetItem(f"{sun_distance_au:.3f}"),
                QTableWidgetItem(f"{earth_distance_au:.3f}"),
            ]
            for column_index, item in enumerate(items):
                self._table.setItem(row_index, column_index, item)

    def _draw_plots(self) -> None:
        self._draw_topdown_plot()
        self._draw_time_series_plots()
        self._draw_sky_track_plot()

    def _draw_sky_track_plot(self) -> None:
        plot_item = self._reset_sky_track_plot()
        self._sky_track_playback_item = None
        self._sky_track_text_item = None
        self._sky_track_projected_series = []
        self._sky_track_projected_x = np.zeros(0, dtype=float)
        self._sky_track_projected_y = np.zeros(0, dtype=float)
        self._sky_track_projected_times: tuple[datetime, ...] = ()
        self._sky_track_projection_center_deg = None
        visible_series = self._visible_sky_track_series()
        if not visible_series:
            empty_item = pg.TextItem("No sky-track context available.", anchor=(0.5, 0.5), color="#dbe7ff")
            empty_item.setPos(0.0, 0.0)
            plot_item.addItem(empty_item)
            plot_item.getViewBox().setRange(xRange=(-1.0, 1.0), yRange=(-1.0, 1.0), padding=0.0)
            return

        combined_ra = np.concatenate([np.asarray(entry["ra_deg"], dtype=float) for entry in visible_series])
        combined_dec = np.concatenate([np.asarray(entry["dec_deg"], dtype=float) for entry in visible_series])
        center_ra_deg, center_dec_deg = self._sky_track_projection_center(combined_ra, combined_dec)
        self._sky_track_projection_center_deg = (center_ra_deg, center_dec_deg)

        projected_series: list[dict[str, object]] = []
        all_x: list[np.ndarray] = []
        all_y: list[np.ndarray] = []
        all_times: list[datetime] = []
        track_radius = _KNOWN_OBJECT_SKY_TRACK_MIN_FIELD_RADIUS_DEG
        for entry in visible_series:
            path_x, path_y, path_times = self._adaptive_projected_sky_track(
                entry,
                center_ra_deg,
                center_dec_deg,
            )
            obs_ra = np.asarray(entry["observation_ra_deg"], dtype=float)
            obs_dec = np.asarray(entry["observation_dec_deg"], dtype=float)
            obs_x = np.zeros(0, dtype=float)
            obs_y = np.zeros(0, dtype=float)
            if obs_ra.size and obs_dec.size:
                obs_x_all, obs_y_all, obs_valid = self._project_sky_radec(obs_ra, obs_dec, center_ra_deg, center_dec_deg)
                obs_x = obs_x_all[obs_valid]
                obs_y = obs_y_all[obs_valid]
            if path_x.size:
                track_radius = max(track_radius, float(np.nanmax(np.hypot(path_x, path_y))) * 1.25)
                all_x.append(path_x)
                all_y.append(path_y)
                all_times.extend(path_times)
            if obs_x.size:
                track_radius = max(track_radius, float(np.nanmax(np.hypot(obs_x, obs_y))) * 1.25)
            style = self._sky_track_style_for_target(int(entry["target_index"]))
            projected_series.append(
                {
                    "target_index": int(entry["target_index"]),
                    "label": str(entry["label"]),
                    "color_hex": str(style["hex"]),
                    "projected_x": path_x,
                    "projected_y": path_y,
                    "projected_times": path_times,
                    "observation_x": obs_x,
                    "observation_y": obs_y,
                    "source_entry": entry,
                }
            )

        if not all_x:
            empty_item = pg.TextItem("Sky track is too wide for this compact view.", anchor=(0.5, 0.5), color="#dbe7ff")
            empty_item.setPos(0.0, 0.0)
            plot_item.addItem(empty_item)
            plot_item.getViewBox().setRange(xRange=(-1.0, 1.0), yRange=(-1.0, 1.0), padding=0.0)
            return

        track_radius = min(_KNOWN_OBJECT_SKY_TRACK_MAX_FIELD_RADIUS_DEG, track_radius)
        self._sky_track_projected_series = projected_series
        self._sky_track_projected_x = np.concatenate(all_x)
        self._sky_track_projected_y = np.concatenate(all_y)
        self._sky_track_projected_times = tuple(all_times)

        if len(projected_series) > 1:
            plot_item.addLegend(
                offset=(8, 8),
                labelTextColor="#eef5ff",
                brush=pg.mkBrush(13, 20, 36, 235),
                pen=pg.mkPen("#314669"),
            )

        combined_x = np.concatenate(all_x)
        combined_y = np.concatenate(all_y)
        for projected in projected_series:
            obs_x = np.asarray(projected["observation_x"], dtype=float)
            obs_y = np.asarray(projected["observation_y"], dtype=float)
            if obs_x.size:
                combined_x = np.concatenate([combined_x, obs_x])
                combined_y = np.concatenate([combined_y, obs_y])
        x_min = float(np.nanmin(combined_x))
        x_max = float(np.nanmax(combined_x))
        y_min = float(np.nanmin(combined_y))
        y_max = float(np.nanmax(combined_y))
        margin = max(0.35, 0.08 * track_radius)
        fit_bounds = (x_min - margin, x_max + margin, y_min - margin, y_max + margin)
        self._sky_track_fit_bounds = fit_bounds
        star_field_radius = self._sky_track_star_draw_radius_deg()

        self._draw_sky_track_all_sky_boundary(plot_item)
        if self._sky_track_constellations_enabled():
            self._draw_sky_track_constellation_lines(plot_item, center_ra_deg, center_dec_deg, star_field_radius)
        self._draw_sky_track_stars(plot_item, center_ra_deg, center_dec_deg, star_field_radius)
        for projected in projected_series:
            path_x = np.asarray(projected["projected_x"], dtype=float)
            path_y = np.asarray(projected["projected_y"], dtype=float)
            color_hex = str(projected["color_hex"])
            pen_width = 2.4 if int(projected["target_index"]) == 0 else 1.8
            if path_x.size:
                plot_item.plot(
                    path_x,
                    path_y,
                    pen=pg.mkPen(color_hex, width=pen_width),
                    name=str(projected["label"]),
                )
            obs_x = np.asarray(projected["observation_x"], dtype=float)
            obs_y = np.asarray(projected["observation_y"], dtype=float)
            if obs_x.size:
                plot_item.addItem(
                    pg.ScatterPlotItem(
                        x=obs_x,
                        y=obs_y,
                        size=8 if int(projected["target_index"]) == 0 else 7,
                        pen=pg.mkPen("#f8fbff", width=0.8),
                        brush=pg.mkBrush(color_hex),
                        pxMode=True,
                    )
                )

        self._sky_track_playback_item = pg.ScatterPlotItem(
            x=[],
            y=[],
            size=13,
            pen=pg.mkPen("#ffffff", width=1.2),
            brush=pg.mkBrush("#ffef9a"),
            pxMode=True,
        )
        plot_item.addItem(self._sky_track_playback_item)
        self._sky_track_text_item = pg.TextItem("", anchor=(0.0, 1.0), color="#f8fbff")
        plot_item.addItem(self._sky_track_text_item)
        center_text = f"Center RA {self._format_ra_hours(center_ra_deg)}, Dec {center_dec_deg:+.1f} deg"
        plot_item.setTitle(f"Sky Track - {center_text}", color="#f6fbff")
        self._apply_sky_track_view_fit()
        QTimer.singleShot(0, self._apply_sky_track_view_fit)

    def _sky_track_widget_aspect(self) -> float:
        if not hasattr(self, "_sky_track_plot"):
            return 1.6
        view_box = self._sky_track_plot.getPlotItem().getViewBox()
        width = float(max(1.0, view_box.width()))
        height = float(max(1.0, view_box.height()))
        return max(0.35, min(8.0, width / height))

    def _apply_sky_track_view_fit(self) -> None:
        bounds = getattr(self, "_sky_track_fit_bounds", None)
        if bounds is None or not hasattr(self, "_sky_track_plot"):
            return
        self._set_sky_track_view_bounds(bounds, padding=0.02)

    def _apply_sky_track_entire_sky_fit(self) -> None:
        if not hasattr(self, "_sky_track_plot"):
            return
        sky_radius = _KNOWN_OBJECT_SKY_TRACK_MAX_FIELD_RADIUS_DEG
        self._set_sky_track_view_bounds(
            (-sky_radius, sky_radius, -sky_radius, sky_radius),
            padding=0.01,
        )

    def _set_sky_track_view_bounds(
        self,
        bounds: tuple[float, float, float, float],
        *,
        padding: float,
    ) -> None:
        x_min, x_max, y_min, y_max = bounds
        x_span = max(_KNOWN_OBJECT_SKY_TRACK_MIN_FIELD_RADIUS_DEG * 0.5, float(x_max) - float(x_min))
        y_span = max(_KNOWN_OBJECT_SKY_TRACK_MIN_FIELD_RADIUS_DEG * 0.5, float(y_max) - float(y_min))
        center_x = 0.5 * (float(x_min) + float(x_max))
        center_y = 0.5 * (float(y_min) + float(y_max))
        widget_aspect = self._sky_track_widget_aspect()
        data_aspect = x_span / y_span
        if widget_aspect >= data_aspect:
            target_y_span = y_span
            target_x_span = target_y_span * widget_aspect
        else:
            target_x_span = x_span
            target_y_span = target_x_span / widget_aspect
        plot_item = self._sky_track_plot.getPlotItem()
        plot_item.getViewBox().setRange(
            xRange=(center_x - (0.5 * target_x_span), center_x + (0.5 * target_x_span)),
            yRange=(center_y - (0.5 * target_y_span), center_y + (0.5 * target_y_span)),
            padding=padding,
        )

    @staticmethod
    def _draw_sky_track_all_sky_boundary(plot_item) -> None:
        angles = np.linspace(0.0, 2.0 * math.pi, 361, dtype=float)
        radius = _KNOWN_OBJECT_SKY_TRACK_MAX_FIELD_RADIUS_DEG
        plot_item.plot(
            radius * np.cos(angles),
            radius * np.sin(angles),
            pen=pg.mkPen(QColor(115, 139, 178, 105), width=1.0),
        )

    def _draw_sky_track_stars(self, plot_item, center_ra_deg: float, center_dec_deg: float, field_radius_deg: float) -> None:
        magnitude_limit = self._sky_track_magnitude_limit()
        star_objects = [
            sky_object
            for sky_object in load_local_sky_atlas_objects()
            if str(getattr(sky_object, "object_type", "")).casefold() == "star"
            and getattr(sky_object, "magnitude", None) is not None
            and float(sky_object.magnitude) <= magnitude_limit
        ]
        if self._sky_track_density_key() == "dense":
            star_objects = self._augment_sky_track_stars_with_constellation_endpoints(star_objects)
        if not star_objects:
            return
        visible_stars = [
            sky_object
            for sky_object in star_objects
            if self._angular_separation_deg(
                float(getattr(sky_object, "ra_deg", 0.0)),
                float(getattr(sky_object, "dec_deg", 0.0)),
                center_ra_deg,
                center_dec_deg,
            )
            <= field_radius_deg + _KNOWN_OBJECT_SKY_TRACK_STAR_PADDING_DEG
        ]
        if not visible_stars:
            return
        star_ra = np.array([float(star.ra_deg) for star in visible_stars], dtype=float)
        star_dec = np.array([float(star.dec_deg) for star in visible_stars], dtype=float)
        star_x_all, star_y_all, star_valid = self._project_sky_radec(star_ra, star_dec, center_ra_deg, center_dec_deg)
        points: list[dict[str, object]] = []
        for sky_object, x_value, y_value, is_valid in zip(visible_stars, star_x_all, star_y_all, star_valid):
            if not is_valid:
                continue
            magnitude = float(sky_object.magnitude) if sky_object.magnitude is not None else magnitude_limit
            if abs(float(x_value)) > field_radius_deg * 1.25 or abs(float(y_value)) > field_radius_deg * 1.25:
                continue
            size = max(2.5, min(10.0, 8.0 - (magnitude * 1.1)))
            color = QColor(str(getattr(sky_object, "color", "#edf4ff")))
            color.setAlpha(150)
            points.append(
                {
                    "pos": (float(x_value), float(y_value)),
                    "size": size,
                    "pen": None,
                    "brush": pg.mkBrush(color),
                    "data": sky_object.name,
                }
            )
        if points:
            plot_item.addItem(pg.ScatterPlotItem(spots=points, pxMode=True))

        include_bayer = self._sky_track_bayer_labels_enabled()
        label_candidates: list[tuple[float, str, float, float]] = []
        for star, x_value, y_value, is_valid in zip(visible_stars, star_x_all, star_y_all, star_valid):
            if not is_valid or abs(float(x_value)) > field_radius_deg or abs(float(y_value)) > field_radius_deg:
                continue
            magnitude = float(star.magnitude) if star.magnitude is not None else magnitude_limit
            if magnitude <= 2.2 and self._sky_track_is_proper_star_name(star.name):
                label_candidates.append((magnitude, str(star.name), float(x_value), float(y_value)))
            elif include_bayer:
                bayer_label = self._sky_track_bayer_label_for_object(star)
                if bayer_label and magnitude <= min(3.5, magnitude_limit):
                    label_candidates.append((magnitude + 0.05, bayer_label, float(x_value), float(y_value)))
        label_candidates = sorted(label_candidates, key=lambda item: item[0])[:36 if include_bayer else 24]
        label_offset = min(0.8, max(0.12, field_radius_deg * 0.004))
        for _magnitude, label_text, x_value, y_value in label_candidates:
            label = pg.TextItem(label_text, anchor=(0.0, 1.0), color="#dbe7ff")
            label.setPos(x_value + label_offset, y_value - label_offset)
            plot_item.addItem(label)

    def _draw_sky_track_constellation_lines(
        self,
        plot_item,
        center_ra_deg: float,
        center_dec_deg: float,
        field_radius_deg: float,
    ) -> None:
        try:
            segments = self._sky_track_constellation_segments()
        except Exception:
            return
        if not segments:
            return
        line_x: list[float] = []
        line_y: list[float] = []
        for segment in segments:
            start_sep = self._angular_separation_deg(
                float(segment.start_ra_deg),
                float(segment.start_dec_deg),
                center_ra_deg,
                center_dec_deg,
            )
            end_sep = self._angular_separation_deg(
                float(segment.end_ra_deg),
                float(segment.end_dec_deg),
                center_ra_deg,
                center_dec_deg,
            )
            if min(start_sep, end_sep) > field_radius_deg + _KNOWN_OBJECT_SKY_TRACK_STAR_PADDING_DEG:
                continue
            segment_ra, segment_dec = self._great_circle_radec_samples(
                np.asarray(segment.start_unit_vector, dtype=float),
                np.asarray(segment.end_unit_vector, dtype=float),
            )
            x_values, y_values, valid = self._project_sky_radec(
                segment_ra,
                segment_dec,
                center_ra_deg,
                center_dec_deg,
            )
            previous_point: tuple[float, float] | None = None
            for x_value, y_value, is_valid in zip(x_values, y_values, valid):
                point = (float(x_value), float(y_value))
                within_radius = math.hypot(*point) <= field_radius_deg + _KNOWN_OBJECT_SKY_TRACK_STAR_PADDING_DEG
                if not bool(is_valid) or not within_radius:
                    if line_x and not math.isnan(line_x[-1]):
                        line_x.append(float("nan"))
                        line_y.append(float("nan"))
                    previous_point = None
                    continue
                if previous_point is not None and math.hypot(point[0] - previous_point[0], point[1] - previous_point[1]) > 35.0:
                    line_x.append(float("nan"))
                    line_y.append(float("nan"))
                line_x.append(point[0])
                line_y.append(point[1])
                previous_point = point
            if line_x and not math.isnan(line_x[-1]):
                line_x.append(float("nan"))
                line_y.append(float("nan"))
        if line_x:
            plot_item.plot(
                np.asarray(line_x, dtype=float),
                np.asarray(line_y, dtype=float),
                connect="finite",
                pen=pg.mkPen(QColor(180, 198, 230, 110), width=1.0),
            )

    @classmethod
    def _great_circle_radec_samples(
        cls,
        start_vector: np.ndarray,
        end_vector: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        start_norm = float(np.linalg.norm(start_vector))
        end_norm = float(np.linalg.norm(end_vector))
        if start_norm <= 0.0 or end_norm <= 0.0:
            return (np.zeros(0, dtype=float), np.zeros(0, dtype=float))
        start_unit = start_vector / start_norm
        end_unit = end_vector / end_norm
        dot = max(-1.0, min(1.0, float(np.dot(start_unit, end_unit))))
        angular_distance = math.acos(dot)
        sample_count = max(2, int(math.ceil(math.degrees(angular_distance) / 4.0)) + 1)
        fractions = np.linspace(0.0, 1.0, sample_count, dtype=float)
        if angular_distance <= 1.0e-9:
            vectors = np.repeat(start_unit[np.newaxis, :], sample_count, axis=0)
        else:
            sin_distance = math.sin(angular_distance)
            vectors = np.array(
                [
                    (
                        (math.sin((1.0 - fraction) * angular_distance) / sin_distance) * start_unit
                        + (math.sin(fraction * angular_distance) / sin_distance) * end_unit
                    )
                    for fraction in fractions
                ],
                dtype=float,
            )
        radec = [cls._unit_vector_to_radec(vector) for vector in vectors]
        valid_radec = [entry if entry is not None else (float("nan"), float("nan")) for entry in radec]
        return (
            np.array([entry[0] for entry in valid_radec], dtype=float),
            np.array([entry[1] for entry in valid_radec], dtype=float),
        )

    def _handle_sky_track_display_settings_changed(self, *_args) -> None:
        if not hasattr(self, "_sky_track_plot"):
            return
        self._draw_sky_track_plot()
        self._update_plot_playback_markers()

    def _sky_track_density_key(self) -> str:
        if not hasattr(self, "_sky_track_density_combo"):
            return "medium"
        value = self._sky_track_density_combo.currentData()
        return str(value or "medium")

    def _sky_track_magnitude_limit(self) -> float:
        return float(_KNOWN_OBJECT_SKY_TRACK_DENSITY_LIMITS.get(self._sky_track_density_key(), 2.5))

    def _sky_track_star_draw_radius_deg(self) -> float:
        if not hasattr(self, "_sky_track_extent_spin"):
            return _KNOWN_OBJECT_SKY_TRACK_MAX_FIELD_RADIUS_DEG
        return min(
            _KNOWN_OBJECT_SKY_TRACK_MAX_FIELD_RADIUS_DEG,
            max(1.0, float(self._sky_track_extent_spin.value())),
        )

    def _sky_track_bayer_labels_enabled(self) -> bool:
        return bool(getattr(self, "_sky_track_bayer_checkbox", None) and self._sky_track_bayer_checkbox.isChecked())

    def _sky_track_constellations_enabled(self) -> bool:
        return bool(getattr(self, "_sky_track_constellations_checkbox", None) and self._sky_track_constellations_checkbox.isChecked())

    def _sky_track_constellation_loader(self) -> ConstellationDataLoader:
        loader = getattr(self, "_sky_track_constellation_data_loader", None)
        if loader is None:
            loader = ConstellationDataLoader()
            self._sky_track_constellation_data_loader = loader
        return loader

    def _sky_track_constellation_segments(self):
        return self._sky_track_constellation_loader().load().line_segments

    def _sky_track_constellation_abbreviation_map(self) -> dict[str, str]:
        cached = getattr(self, "_sky_track_constellation_abbrev_map", None)
        if cached is not None:
            return cached
        mapping: dict[str, str] = {}
        try:
            labels = self._sky_track_constellation_loader().load().labels
        except Exception:
            labels = ()
        for label in labels:
            abbreviation = str(label.abbreviation or label.constellation_id).strip()
            if not abbreviation:
                continue
            for candidate in (label.name, label.abbreviation, label.constellation_id):
                key = re.sub(r"\s+", " ", str(candidate or "").replace("\u2005", " ")).strip().casefold()
                if key:
                    mapping[key] = abbreviation
            # Common genitive/stem forms used in Bayer aliases ("Orionis", "Ophiuchi").
            stem = re.sub(r"(is|ae|i|us|um)$", "", abbreviation.casefold())
            if stem:
                mapping[stem] = abbreviation
        # Extra stems for common Bayer aliases not covered by abbreviation alone.
        mapping.update(
            {
                "orionis": "Ori",
                "ophiuchi": "Oph",
                "canis majoris": "CMa",
                "canis minoris": "CMi",
                "ursa majoris": "UMa",
                "ursa minoris": "UMi",
                "bootis": "Boo",
                "boötis": "Boo",
                "tauri": "Tau",
                "scorpii": "Sco",
                "virginis": "Vir",
                "leonis": "Leo",
                "geminorum": "Gem",
                "aquilae": "Aql",
                "lyrae": "Lyr",
                "cygni": "Cyg",
                "eridani": "Eri",
                "centauri": "Cen",
                "crucis": "Cru",
                "carinae": "Car",
                "aurigae": "Aur",
                "andromedae": "And",
                "cassiopeiae": "Cas",
                "draconis": "Dra",
                "herculis": "Her",
                "pegasi": "Peg",
                "perseii": "Per",
                "persei": "Per",
                "piscis austrini": "PsA",
            }
        )
        self._sky_track_constellation_abbrev_map = mapping
        return mapping

    @staticmethod
    def _sky_track_is_proper_star_name(name: str) -> bool:
        normalized = str(name or "").strip()
        if not normalized:
            return False
        if re.fullmatch(r"[a-z]{1,2}\s+[A-Z][A-Za-z]{1,2}", normalized):
            return False
        if normalized.casefold().startswith(("hip ", "hd ", "hr ", "sao ", "tyc ")):
            return False
        return any(character.isalpha() for character in normalized)

    @classmethod
    def _sky_track_bayer_label_from_alias(cls, alias: str, abbreviation_map: dict[str, str]) -> str | None:
        normalized = re.sub(r"\s+", " ", str(alias or "").replace("\u2005", " ").strip())
        if not normalized:
            return None
        match = re.fullmatch(
            r"(?i)(alpha|beta|gamma|delta|epsilon|zeta|eta|theta|iota|kappa|lambda|mu|nu|xi|omicron|pi|rho|sigma|tau|upsilon|phi|chi|psi|omega)\s+(.+)",
            normalized,
        )
        if match is None:
            return None
        greek = match.group(1).casefold()
        constellation_key = match.group(2).strip().casefold()
        letter = _KNOWN_OBJECT_SKY_TRACK_BAYER_LETTER_BY_GREEK.get(greek)
        abbreviation = abbreviation_map.get(constellation_key)
        if letter is None or not abbreviation:
            return None
        return f"{letter} {abbreviation}"

    def _sky_track_bayer_label_for_object(self, sky_object) -> str | None:
        abbreviation_map = self._sky_track_constellation_abbreviation_map()
        for alias in (sky_object.name, *tuple(getattr(sky_object, "aliases", ()))):
            label = self._sky_track_bayer_label_from_alias(str(alias), abbreviation_map)
            if label:
                return label
        return None

    def _augment_sky_track_stars_with_constellation_endpoints(self, star_objects: list) -> list:
        existing = {
            (round(float(star.ra_deg), 3), round(float(star.dec_deg), 3))
            for star in star_objects
        }
        augmented = list(star_objects)
        try:
            segments = self._sky_track_constellation_segments()
        except Exception:
            return augmented

        for segment in segments:
            for ra_deg, dec_deg in (
                (float(segment.start_ra_deg), float(segment.start_dec_deg)),
                (float(segment.end_ra_deg), float(segment.end_dec_deg)),
            ):
                key = (round(ra_deg % 360.0, 3), round(dec_deg, 3))
                if key in existing:
                    continue
                existing.add(key)
                augmented.append(
                    SkyAtlasObject(
                        name="",
                        object_type="Star",
                        ra_deg=ra_deg % 360.0,
                        dec_deg=dec_deg,
                        magnitude=4.8,
                        catalog="Constellation",
                        aliases=(),
                        color="#c9d7ef",
                        constellation=str(segment.constellation_id),
                        label_visible=False,
                        searchable=False,
                        selectable=False,
                    )
                )
        return augmented

    @staticmethod
    def _format_ra_hours(ra_deg: float) -> str:
        total_hours = (float(ra_deg) % 360.0) / 15.0
        hours = int(total_hours)
        minutes = int(round((total_hours - hours) * 60.0))
        if minutes >= 60:
            hours = (hours + 1) % 24
            minutes = 0
        return f"{hours:02d}h {minutes:02d}m"

    def _draw_topdown_plot(self) -> None:
        plot_item = self._reset_topdown_plot()
        self._topdown_playback_primary_marker = None
        self._topdown_playback_earth_marker = None
        self._topdown_playback_body_markers = {}
        self._topdown_playback_text_items = {}

        object_path = self._context.object_path_samples
        earth_path = self._context.earth_path_samples
        if not object_path or not earth_path:
            empty_item = pg.TextItem("No top-down context available.", anchor=(0.5, 0.5), color="#dbe7ff")
            empty_item.setPos(0.5, 0.5)
            plot_item.addItem(empty_item)
            plot_item.getViewBox().setRange(xRange=(0.0, 1.0), yRange=(0.0, 1.0), padding=0.0)
            return

        comparison_tracks = self._comparison_tracks()
        primary_style = self._primary_target_style()
        primary_label = self._primary_target_label()
        show_sample_points = self._show_sample_points_checkbox.isChecked()

        observed_object = self._context.observation_object_samples
        observed_earth = self._context.observation_earth_samples
        observed_labels = [f"F{index + 1}" for index in range(len(observed_object))]

        focus_x_min, focus_x_max, focus_y_min, focus_y_max = self._trajectory_focus_bounds()
        self._xy_focus_bounds = (focus_x_min, focus_x_max, focus_y_min, focus_y_max)
        x_span = max(0.1, focus_x_max - focus_x_min)
        y_span = max(0.1, focus_y_max - focus_y_min)
        rng = np.random.default_rng(20260411)
        star_count = 60
        star_item = pg.ScatterPlotItem(
            x=rng.uniform(focus_x_min - (0.12 * x_span), focus_x_max + (0.12 * x_span), star_count),
            y=rng.uniform(focus_y_min - (0.12 * y_span), focus_y_max + (0.12 * y_span), star_count),
            size=rng.uniform(2.0, 8.0, star_count),
            pen=None,
            brush=pg.mkBrush(219, 231, 255, 80),
            pxMode=True,
        )
        plot_item.addItem(star_item)
        if self._is_target_visible(0):
            primary_x = np.array([sample.x_au for sample in self._context.object_path_samples], dtype=float)
            primary_y = np.array([sample.y_au for sample in self._context.object_path_samples], dtype=float)
            plot_item.plot(
                primary_x,
                primary_y,
                pen=pg.mkPen(QColor.fromRgbF(primary_style["glow"][0], primary_style["glow"][1], primary_style["glow"][2], 0.24), width=4.8),
            )
            plot_item.plot(primary_x, primary_y, pen=pg.mkPen(str(primary_style["hex"]), width=2.0), name=primary_label)
        earth_x = np.array([sample.x_au for sample in self._context.earth_path_samples], dtype=float)
        earth_y = np.array([sample.y_au for sample in self._context.earth_path_samples], dtype=float)
        plot_item.plot(
            earth_x,
            earth_y,
            pen=pg.mkPen(QColor(56, 189, 248, 60), width=4.4),
        )
        plot_item.plot(earth_x, earth_y, pen=pg.mkPen("#58c7ff", width=1.9), name="Earth")
        for comparison_index, track in enumerate(comparison_tracks, start=1):
            if not self._is_target_visible(comparison_index):
                continue
            style = self._comparison_track_style(comparison_index - 1)
            comparison_x = np.array([sample.x_au for sample in track.path_samples], dtype=float)
            comparison_y = np.array([sample.y_au for sample in track.path_samples], dtype=float)
            plot_item.plot(
                comparison_x,
                comparison_y,
                pen=pg.mkPen(QColor.fromRgbF(style["glow"][0], style["glow"][1], style["glow"][2], 0.17), width=4.0),
            )
            plot_item.plot(comparison_x, comparison_y, pen=pg.mkPen(style["hex"], width=1.7), name=track.object_label)
        plot_item.addItem(
            pg.ScatterPlotItem(x=[0.0], y=[0.0], size=22, pen=None, brush=pg.mkBrush(253, 224, 71, 46), pxMode=True)
        )
        plot_item.addItem(
            pg.ScatterPlotItem(x=[0.0], y=[0.0], size=10, pen=None, brush=pg.mkBrush("#facc15"), pxMode=True)
        )
        if show_sample_points and observed_object and self._is_target_visible(0):
            observed_object_x = [sample.x_au for sample in observed_object]
            observed_object_y = [sample.y_au for sample in observed_object]
            plot_item.addItem(
                pg.ScatterPlotItem(
                    x=observed_object_x,
                    y=observed_object_y,
                    size=8,
                    pen=pg.mkPen("#ffe8a3", width=0.8),
                    brush=pg.mkBrush("#ff6b6b"),
                    pxMode=True,
                )
            )
            for label, x_value, y_value in zip(observed_labels, observed_object_x, observed_object_y):
                text_item = pg.TextItem(label, anchor=(0.0, 1.0), color="#f8fbff")
                text_item.setPos(float(x_value) + 0.01, float(y_value) + 0.01)
                text_item.setFont(QFont("Segoe UI", 8))
                plot_item.addItem(text_item)
        for comparison_index, track in enumerate(comparison_tracks, start=1):
            if not self._is_target_visible(comparison_index):
                continue
            if not show_sample_points or not track.observation_samples:
                continue
            style = self._comparison_track_style(comparison_index - 1)
            track_observed_x = [sample.x_au for sample in track.observation_samples]
            track_observed_y = [sample.y_au for sample in track.observation_samples]
            plot_item.addItem(
                pg.ScatterPlotItem(
                    x=track_observed_x,
                    y=track_observed_y,
                    size=6,
                    pen=pg.mkPen("#f8fbff", width=0.5),
                    brush=pg.mkBrush(style["hex"]),
                    pxMode=True,
                )
            )
        plot_item.getViewBox().setXRange(focus_x_min, focus_x_max, padding=0.0)
        plot_item.getViewBox().setYRange(focus_y_min, focus_y_max, padding=0.0)
        self._topdown_playback_primary_marker = pg.ScatterPlotItem(
            size=12,
            pen=pg.mkPen(str(primary_style["hex"]), width=1.1),
            brush=pg.mkBrush("#fff5c2"),
            pxMode=True,
        )
        plot_item.addItem(self._topdown_playback_primary_marker)
        self._topdown_playback_earth_marker = pg.ScatterPlotItem(
            size=10,
            pen=pg.mkPen("#58c7ff", width=1.0),
            brush=pg.mkBrush("#d6f6ff"),
            pxMode=True,
        )
        plot_item.addItem(self._topdown_playback_earth_marker)
        for body in self._additional_bodies():
            marker_item = pg.ScatterPlotItem(
                size=7,
                pen=None,
                brush=pg.mkBrush(self._body_style(body.key)["hex"]),
                pxMode=True,
            )
            self._topdown_playback_body_markers[body.key] = marker_item
            plot_item.addItem(marker_item)
        if self._show_labels_checkbox.isChecked():
            for key in ("sun", "earth", "object-primary"):
                text_item = pg.TextItem("", anchor=(0.0, 1.0), color="#f8fbff")
                text_item.setFont(self._current_label_font())
                text_item.hide()
                self._topdown_playback_text_items[key] = text_item
                plot_item.addItem(text_item)
            for comparison_index, track in enumerate(comparison_tracks, start=1):
                if not self._is_target_visible(comparison_index):
                    continue
                text_item = pg.TextItem("", anchor=(0.0, 1.0), color="#f8fbff")
                text_item.setFont(self._current_label_font())
                text_item.hide()
                self._topdown_playback_text_items[f"object-{comparison_index}"] = text_item
                plot_item.addItem(text_item)
            for body in self._additional_bodies():
                text_item = pg.TextItem("", anchor=(0.0, 1.0), color="#f8fbff")
                text_item.setFont(self._current_label_font())
                text_item.hide()
                self._topdown_playback_text_items[f"planet-{body.key}"] = text_item
                plot_item.addItem(text_item)
        self._update_topdown_playback_artists()

    @staticmethod
    def _set_topdown_marker_position(marker_item, x_value: float | None, y_value: float | None) -> None:
        if marker_item is None:
            return
        if x_value is None or y_value is None:
            marker_item.hide()
            marker_item.setData([], [])
            return
        marker_item.setData(x=[float(x_value)], y=[float(y_value)])
        marker_item.show()

    def _topdown_label_offset_position(self, x_value: float, y_value: float) -> tuple[float, float]:
        bounds = getattr(self, "_xy_focus_bounds", None)
        if bounds is None:
            return (float(x_value), float(y_value))
        x_min, x_max, y_min, y_max = bounds
        return (
            float(x_value) + max((float(x_max) - float(x_min)) * 0.015, 0.02),
            float(y_value) + max((float(y_max) - float(y_min)) * 0.015, 0.02),
        )

    def _set_topdown_label_position(self, key: str, text: str | None, x_value: float | None, y_value: float | None) -> None:
        label_item = self._topdown_playback_text_items.get(key)
        if label_item is None:
            return
        if text is None or x_value is None or y_value is None:
            label_item.hide()
            return
        offset_x, offset_y = self._topdown_label_offset_position(float(x_value), float(y_value))
        label_item.setText(text, color="#f8fbff")
        label_item.setFont(self._current_label_font())
        label_item.setPos(offset_x, offset_y)
        label_item.show()

    def _update_topdown_playback_artists(
        self,
        object_position: np.ndarray | None = None,
        earth_position: np.ndarray | None = None,
    ) -> None:
        if not hasattr(self, "_topdown_plot"):
            return
        if object_position is None:
            object_position = self._interpolate_position(self._context.object_path_samples, self._current_playback_time())
        if earth_position is None:
            earth_position = self._interpolate_position(self._context.earth_path_samples, self._current_playback_time())
        if object_position is None or earth_position is None:
            self._set_topdown_marker_position(self._topdown_playback_primary_marker, None, None)
            self._set_topdown_marker_position(self._topdown_playback_earth_marker, None, None)
            for body_key, marker_item in self._topdown_playback_body_markers.items():
                self._set_topdown_marker_position(marker_item, None, None)
                self._set_topdown_label_position(f"planet-{body_key}", None, None, None)
            for key in tuple(self._topdown_playback_text_items):
                if not key.startswith("planet-"):
                    self._set_topdown_label_position(key, None, None, None)
            return
        object_x = float(object_position[0])
        object_y = float(object_position[1])
        earth_x = float(earth_position[0])
        earth_y = float(earth_position[1])
        show_labels = self._show_labels_checkbox.isChecked()
        show_sun_label = show_labels and self._point_in_xy_focus_bounds(0.0, 0.0)
        show_primary = self._is_target_visible(0)
        show_earth = self._point_in_xy_focus_bounds(earth_x, earth_y)
        self._set_topdown_marker_position(
            self._topdown_playback_primary_marker,
            object_x if show_primary else None,
            object_y if show_primary else None,
        )
        self._set_topdown_marker_position(
            self._topdown_playback_earth_marker,
            earth_x if show_earth else None,
            earth_y if show_earth else None,
        )
        self._set_topdown_label_position("sun", "Sun" if show_sun_label else None, 0.0, 0.0)
        self._set_topdown_label_position(
            "object-primary",
            self._primary_target_label() if show_labels and show_primary else None,
            object_x,
            object_y,
        )
        self._set_topdown_label_position("earth", "Earth" if show_labels and show_earth else None, earth_x, earth_y)
        playback_time = self._current_playback_time()
        for comparison_index, track in enumerate(self._comparison_tracks(), start=1):
            comparison_position = self._interpolate_position(track.path_samples, playback_time)
            if comparison_position is None:
                self._set_topdown_label_position(f"object-{comparison_index}", None, None, None)
                continue
            comparison_x = float(comparison_position[0])
            comparison_y = float(comparison_position[1])
            comparison_visible = show_labels and self._is_target_visible(comparison_index)
            self._set_topdown_label_position(
                f"object-{comparison_index}",
                track.object_label if comparison_visible else None,
                comparison_x,
                comparison_y,
            )
        for body in self._additional_bodies():
            nearest_position = self._nearest_body_sample(body, playback_time)
            marker_item = self._topdown_playback_body_markers.get(body.key)
            if nearest_position is None or not self._point_in_xy_focus_bounds(nearest_position[0], nearest_position[1]):
                self._set_topdown_marker_position(marker_item, None, None)
                self._set_topdown_label_position(f"planet-{body.key}", None, None, None)
                continue
            self._set_topdown_marker_position(marker_item, nearest_position[0], nearest_position[1])
            self._set_topdown_label_position(
                f"planet-{body.key}",
                body.label if show_labels else None,
                nearest_position[0],
                nearest_position[1],
            )

    def _reset_time_series_plot(self, plot_widget, title: str, y_label: str, *, invert_y: bool = False):
        plot_item = plot_widget.getPlotItem()
        legend = getattr(plot_item, "legend", None)
        if legend is not None and legend.scene() is not None:
            legend.scene().removeItem(legend)
            plot_item.legend = None
        plot_item.clear()
        plot_item.showGrid(x=True, y=True, alpha=0.22)
        plot_item.setTitle(title, color="#f6fbff")
        plot_item.setLabel("bottom", "UTC", color="#edf4ff")
        plot_item.setLabel("left", y_label, color="#edf4ff")
        plot_item.getViewBox().setMouseEnabled(x=True, y=True)
        plot_item.getViewBox().invertY(invert_y)
        for axis_name in ("bottom", "left"):
            axis = plot_item.getAxis(axis_name)
            axis.setTextPen(pg.mkPen("#dbe7ff"))
            axis.setPen(pg.mkPen("#425a82"))
        plot_item.addLegend(
            offset=(8, 8),
            labelTextColor="#eef5ff",
            brush=pg.mkBrush(13, 20, 36, 235),
            pen=pg.mkPen("#314669"),
        )
        return plot_item

    def _show_time_series_empty_message(self, plot_widget, message: str, *, x_range: tuple[float, float] | None = None) -> None:
        plot_item = plot_widget.getPlotItem()
        text_item = pg.TextItem(message, anchor=(0.5, 0.5), color="#dbe7ff")
        plot_item.addItem(text_item)
        if x_range is None:
            text_item.setPos(0.5, 0.5)
            plot_item.getViewBox().setRange(xRange=(0.0, 1.0), yRange=(0.0, 1.0), padding=0.0)
            return
        x_min, x_max = x_range
        text_item.setPos((float(x_min) + float(x_max)) * 0.5, 0.5)
        plot_item.getViewBox().setRange(xRange=(float(x_min), float(x_max)), yRange=(0.0, 1.0), padding=0.0)

    def _set_time_series_ranges(self, plot_widget, x_arrays: list[np.ndarray], y_arrays: list[np.ndarray]) -> None:
        if not x_arrays or not y_arrays:
            return
        x_min = min(float(np.min(values)) for values in x_arrays if values.size)
        x_max = max(float(np.max(values)) for values in x_arrays if values.size)
        y_min = min(float(np.min(values)) for values in y_arrays if values.size)
        y_max = max(float(np.max(values)) for values in y_arrays if values.size)
        if math.isclose(x_min, x_max, rel_tol=0.0, abs_tol=1e-9):
            x_min -= 60.0
            x_max += 60.0
        if math.isclose(y_min, y_max, rel_tol=0.0, abs_tol=1e-9):
            padding = max(0.05, abs(y_min) * 0.05)
            y_min -= padding
            y_max += padding
        else:
            padding = max(0.03, (y_max - y_min) * 0.08)
            y_min -= padding
            y_max += padding
        plot_widget.getPlotItem().getViewBox().setXRange(x_min, x_max, padding=0.02)
        plot_widget.getPlotItem().getViewBox().setYRange(y_min, y_max, padding=0.0)

    def _draw_time_series_plots(self) -> None:
        self._time_series_plot_refreshing = True
        try:
            self._hide_time_series_hover_artists(self._distance_hover_artists)
            self._hide_time_series_hover_artists(self._magnitude_hover_artists)
            self._distance_hover_artists = {}
            self._magnitude_hover_artists = {}
            if self._distance_playback_item is not None:
                self._distance_playback_item.hide()
                self._distance_playback_item = None
            if self._magnitude_playback_item is not None:
                self._magnitude_playback_item.hide()
                self._magnitude_playback_item = None

            self._distance_hover_series = []
            self._magnitude_hover_series = []

            distance_plot_item = self._reset_time_series_plot(self._distance_plot, "Distance over window", "Distance (AU)")
            magnitude_plot_item = self._reset_time_series_plot(self._magnitude_plot, "Literature magnitude over window", "Mag", invert_y=True)

            object_path = self._context.object_path_samples
            earth_path = self._context.earth_path_samples
            if not object_path or not earth_path:
                self._show_time_series_empty_message(self._distance_plot, "No distance context available.")
                self._show_time_series_empty_message(self._magnitude_plot, "No Horizons literature magnitude series available for the visible objects.")
                self._distance_hover_artists = self._create_time_series_hover_artists(self._distance_plot)
                self._magnitude_hover_artists = self._create_time_series_hover_artists(self._magnitude_plot)
                self._distance_playback_item = self._create_time_series_playback_item(self._distance_plot)
                self._magnitude_playback_item = self._create_time_series_playback_item(self._magnitude_plot)
                return

            comparison_tracks = self._comparison_tracks()
            observed_object = self._context.observation_object_samples
            observed_earth = self._context.observation_earth_samples
            show_sample_points = self._show_sample_points_checkbox.isChecked()

            distance_x_arrays: list[np.ndarray] = []
            distance_y_arrays: list[np.ndarray] = []
            magnitude_x_arrays: list[np.ndarray] = []
            magnitude_y_arrays: list[np.ndarray] = []
            primary_style = self._primary_target_style()
            primary_label = self._primary_target_label()

            time_axis = [sample.observation_time for sample in object_path]
            object_sun_distance = [self._vector_norm(sample.x_au, sample.y_au, sample.z_au) for sample in object_path]
            object_earth_distance = [
                self._vector_norm(
                    object_sample.x_au - earth_sample.x_au,
                    object_sample.y_au - earth_sample.y_au,
                    object_sample.z_au - earth_sample.z_au,
                )
                for object_sample, earth_sample in zip(object_path, earth_path, strict=True)
            ]
            object_time_values = np.array([self._datetime_to_time_axis_value(sample_time) for sample_time in time_axis], dtype=float)
            primary_line_color = QColor(str(primary_style["hex"]))
            primary_glow_color = QColor(primary_line_color)
            primary_glow_color.setAlpha(40)
            distance_plot_item.plot(object_time_values, object_sun_distance, pen=pg.mkPen(primary_glow_color, width=5.0))
            if self._is_target_visible(0):
                distance_plot_item.plot(object_time_values, object_sun_distance, pen=pg.mkPen(str(primary_style["hex"]), width=2.0), name="Object-Sun")
                distance_plot_item.plot(object_time_values, object_earth_distance, pen=pg.mkPen(QColor(139, 92, 246, 36), width=5.0))
                distance_plot_item.plot(object_time_values, object_earth_distance, pen=pg.mkPen("#8b5cf6", width=2.0), name="Object-Earth")
                object_sun_series = self._time_axis_series(time_axis, object_sun_distance, "Object-Sun", "AU", str(primary_style["hex"]))
                object_earth_series = self._time_axis_series(time_axis, object_earth_distance, "Object-Earth", "AU", "#8b5cf6")
                if object_sun_series is not None:
                    self._distance_hover_series.append(object_sun_series)
                    distance_x_arrays.append(object_sun_series[1])
                    distance_y_arrays.append(object_sun_series[2])
                if object_earth_series is not None:
                    self._distance_hover_series.append(object_earth_series)
                    distance_x_arrays.append(object_earth_series[1])
                    distance_y_arrays.append(object_earth_series[2])
            for comparison_index, track in enumerate(comparison_tracks, start=1):
                if not self._is_target_visible(comparison_index):
                    continue
                style = self._comparison_track_style(comparison_index - 1)
                comparison_time_axis = [sample.observation_time for sample in track.path_samples]
                comparison_time_values = np.array([self._datetime_to_time_axis_value(sample_time) for sample_time in comparison_time_axis], dtype=float)
                comparison_sun_distance = [self._vector_norm(sample.x_au, sample.y_au, sample.z_au) for sample in track.path_samples]
                distance_plot_item.plot(comparison_time_values, comparison_sun_distance, pen=pg.mkPen(style["hex"], width=1.8), name=f"{track.object_label}-Sun")
                comparison_series = self._time_axis_series(comparison_time_axis, comparison_sun_distance, f"{track.object_label}-Sun", "AU", style["hex"])
                if comparison_series is not None:
                    self._distance_hover_series.append(comparison_series)
                    distance_x_arrays.append(comparison_series[1])
                    distance_y_arrays.append(comparison_series[2])
            if show_sample_points and observed_object and observed_earth and self._is_target_visible(0):
                observed_times = np.array([self._datetime_to_time_axis_value(sample.observation_time) for sample in observed_object], dtype=float)
                observed_sun_distance = [self._vector_norm(sample.x_au, sample.y_au, sample.z_au) for sample in observed_object]
                observed_earth_distance = [
                    self._vector_norm(
                        object_sample.x_au - earth_sample.x_au,
                        object_sample.y_au - earth_sample.y_au,
                        object_sample.z_au - earth_sample.z_au,
                    )
                    for object_sample, earth_sample in zip(observed_object, observed_earth, strict=True)
                ]
                distance_plot_item.addItem(
                    pg.ScatterPlotItem(
                        x=observed_times,
                        y=observed_sun_distance,
                        size=7,
                        pen=pg.mkPen("#ffe8a3", width=0.9),
                        brush=pg.mkBrush("#ff6b6b"),
                    )
                )
                distance_plot_item.addItem(
                    pg.ScatterPlotItem(
                        x=observed_times,
                        y=observed_earth_distance,
                        size=7,
                        pen=pg.mkPen("#d8ffe4", width=0.9),
                        brush=pg.mkBrush("#22c55e"),
                    )
                )
                distance_x_arrays.append(observed_times)
                distance_y_arrays.append(np.array(observed_sun_distance, dtype=float))
                distance_y_arrays.append(np.array(observed_earth_distance, dtype=float))
            for comparison_index, track in enumerate(comparison_tracks, start=1):
                if not show_sample_points or not self._is_target_visible(comparison_index) or not track.observation_samples:
                    continue
                style = self._comparison_track_style(comparison_index - 1)
                comparison_observed_times = np.array([self._datetime_to_time_axis_value(sample.observation_time) for sample in track.observation_samples], dtype=float)
                comparison_observed_sun_distance = [self._vector_norm(sample.x_au, sample.y_au, sample.z_au) for sample in track.observation_samples]
                distance_plot_item.addItem(
                    pg.ScatterPlotItem(
                        x=comparison_observed_times,
                        y=comparison_observed_sun_distance,
                        size=5,
                        pen=pg.mkPen("#f8fbff", width=0.6),
                        brush=pg.mkBrush(style["hex"]),
                    )
                )
                distance_x_arrays.append(comparison_observed_times)
                distance_y_arrays.append(np.array(comparison_observed_sun_distance, dtype=float))
            if distance_x_arrays and distance_y_arrays:
                self._set_time_series_ranges(self._distance_plot, distance_x_arrays, distance_y_arrays)
            else:
                self._show_time_series_empty_message(self._distance_plot, "No distance context available.")

            magnitude_has_series = False
            if self._is_target_visible(0):
                object_magnitude_times, object_magnitudes = self._magnitude_series_points(self._object_magnitude_samples())
                if object_magnitudes:
                    object_magnitude_values = np.array([self._datetime_to_time_axis_value(sample_time) for sample_time in object_magnitude_times], dtype=float)
                    magnitude_plot_item.plot(object_magnitude_values, object_magnitudes, pen=pg.mkPen(str(primary_style["hex"]), width=2.0), name=primary_label)
                    object_magnitude_series = self._time_axis_series(object_magnitude_times, object_magnitudes, primary_label, "mag", str(primary_style["hex"]))
                    if object_magnitude_series is not None:
                        self._magnitude_hover_series.append(object_magnitude_series)
                        magnitude_x_arrays.append(object_magnitude_series[1])
                        magnitude_y_arrays.append(object_magnitude_series[2])
                    magnitude_has_series = True
            for comparison_index, track in enumerate(comparison_tracks, start=1):
                if not self._is_target_visible(comparison_index):
                    continue
                comparison_times, comparison_magnitudes = self._magnitude_series_points(getattr(track, "magnitude_samples", ()))
                if not comparison_magnitudes:
                    continue
                style = self._comparison_track_style(comparison_index - 1)
                comparison_time_values = np.array([self._datetime_to_time_axis_value(sample_time) for sample_time in comparison_times], dtype=float)
                magnitude_plot_item.plot(comparison_time_values, comparison_magnitudes, pen=pg.mkPen(style["hex"], width=1.8), name=track.object_label)
                comparison_magnitude_series = self._time_axis_series(comparison_times, comparison_magnitudes, track.object_label, "mag", style["hex"])
                if comparison_magnitude_series is not None:
                    self._magnitude_hover_series.append(comparison_magnitude_series)
                    magnitude_x_arrays.append(comparison_magnitude_series[1])
                    magnitude_y_arrays.append(comparison_magnitude_series[2])
                magnitude_has_series = True
            if magnitude_has_series and magnitude_x_arrays and magnitude_y_arrays:
                self._set_time_series_ranges(self._magnitude_plot, magnitude_x_arrays, magnitude_y_arrays)
            else:
                linked_x_range = None
                if distance_x_arrays:
                    distance_view = self._distance_plot.getPlotItem().getViewBox().viewRange()[0]
                    linked_x_range = (float(distance_view[0]), float(distance_view[1]))
                self._show_time_series_empty_message(
                    self._magnitude_plot,
                    "No Horizons literature magnitude series available for the visible objects.",
                    x_range=linked_x_range,
                )

            self._distance_hover_artists = self._create_time_series_hover_artists(self._distance_plot)
            self._magnitude_hover_artists = self._create_time_series_hover_artists(self._magnitude_plot)
            self._distance_playback_item = self._create_time_series_playback_item(self._distance_plot)
            self._magnitude_playback_item = self._create_time_series_playback_item(self._magnitude_plot)
        finally:
            self._time_series_plot_refreshing = False

    def _update_time_series_playback_markers(self, object_position: np.ndarray, earth_position: np.ndarray) -> None:
        playback_time = self._current_playback_time()
        if self._distance_playback_item is not None:
            if self._is_target_visible(0):
                x_value = self._datetime_to_time_axis_value(playback_time)
                sun_distance = self._vector_norm(float(object_position[0]), float(object_position[1]), float(object_position[2]))
                earth_distance = self._vector_norm(
                    float(object_position[0] - earth_position[0]),
                    float(object_position[1] - earth_position[1]),
                    float(object_position[2] - earth_position[2]),
                )
                self._distance_playback_item.setData(
                    x=[x_value, x_value],
                    y=[sun_distance, earth_distance],
                    size=[10, 9],
                    pen=[pg.mkPen("#ff7a59", width=1.0), pg.mkPen("#2bd27d", width=1.0)],
                    brush=[pg.mkBrush("#fff5c2"), pg.mkBrush("#c6ffe0")],
                )
                self._distance_playback_item.show()
            else:
                self._distance_playback_item.hide()
                self._distance_playback_item.setData([], [])
        if self._magnitude_playback_item is not None:
            x_values: list[float] = []
            y_values: list[float] = []
            sizes: list[float] = []
            pens: list[object] = []
            brushes: list[object] = []
            if self._is_target_visible(0):
                object_magnitude = self._interpolate_magnitude(self._object_magnitude_samples(), playback_time)
                if object_magnitude is not None:
                    x_values.append(self._datetime_to_time_axis_value(playback_time))
                    y_values.append(float(object_magnitude))
                    sizes.append(10)
                    pens.append(pg.mkPen("#ff7a59", width=1.0))
                    brushes.append(pg.mkBrush("#fff5c2"))
            for comparison_index, track in enumerate(self._comparison_tracks(), start=1):
                if not self._is_target_visible(comparison_index):
                    continue
                comparison_magnitude = self._interpolate_magnitude(getattr(track, "magnitude_samples", ()), playback_time)
                if comparison_magnitude is None:
                    continue
                style = self._comparison_track_style(comparison_index - 1)
                x_values.append(self._datetime_to_time_axis_value(playback_time))
                y_values.append(float(comparison_magnitude))
                sizes.append(7)
                pens.append(pg.mkPen("#f8fbff", width=0.7))
                brushes.append(pg.mkBrush(style["hex"]))
            if x_values:
                self._magnitude_playback_item.setData(x=x_values, y=y_values, size=sizes, pen=pens, brush=brushes)
                self._magnitude_playback_item.show()
            else:
                self._magnitude_playback_item.hide()
                self._magnitude_playback_item.setData([], [])

    def _update_sky_track_playback_marker(self, object_position: np.ndarray | None, earth_position: np.ndarray | None) -> None:
        if self._sky_track_playback_item is None:
            return
        center = getattr(self, "_sky_track_projection_center_deg", None)
        if center is None or earth_position is None:
            self._sky_track_playback_item.hide()
            self._sky_track_playback_item.setData([], [])
            if self._sky_track_text_item is not None:
                self._sky_track_text_item.hide()
            return

        playback_time = self._current_playback_time()
        marker_x: list[float] = []
        marker_y: list[float] = []
        marker_brushes: list[object] = []
        marker_sizes: list[int] = []
        label_anchor: tuple[float, float, float, float] | None = None
        for projected in getattr(self, "_sky_track_projected_series", []):
            target_index = int(projected["target_index"])
            source_entry = projected.get("source_entry")
            if not isinstance(source_entry, Mapping):
                continue
            radec = self._sky_track_radec_at_time(source_entry, playback_time)
            if radec is None:
                continue
            ra_deg, dec_deg = radec
            x_values, y_values, valid = self._project_sky_radec(
                np.array([ra_deg], dtype=float),
                np.array([dec_deg], dtype=float),
                float(center[0]),
                float(center[1]),
            )
            if x_values.size == 0 or y_values.size == 0 or not bool(valid[0]):
                continue
            x_value = float(x_values[0])
            y_value = float(y_values[0])
            marker_x.append(x_value)
            marker_y.append(y_value)
            marker_brushes.append(pg.mkBrush(str(projected["color_hex"])))
            marker_sizes.append(14 if target_index == 0 else 11)
            if label_anchor is None or target_index == 0:
                label_anchor = (x_value, y_value, ra_deg, dec_deg)

        if not marker_x:
            self._sky_track_playback_item.hide()
            self._sky_track_playback_item.setData([], [])
            if self._sky_track_text_item is not None:
                self._sky_track_text_item.hide()
            return

        self._sky_track_playback_item.setData(
            x=marker_x,
            y=marker_y,
            brush=marker_brushes,
            size=marker_sizes,
            pen=pg.mkPen("#ffffff", width=1.2),
        )
        self._sky_track_playback_item.show()
        if self._sky_track_text_item is not None and label_anchor is not None:
            x_value, y_value, ra_deg, dec_deg = label_anchor
            self._sky_track_text_item.setText(
                f"{self._format_playback_time_text(playback_time)}\n"
                f"RA {self._format_ra_hours(ra_deg)}  Dec {dec_deg:+.2f} deg"
            )
            self._sky_track_text_item.setPos(x_value + 0.15, y_value - 0.15)
            self._sky_track_text_item.show()

    def _update_plot_playback_markers(self) -> None:
        if not hasattr(self, "_topdown_plot"):
            return
        playback_time = self._current_playback_time()
        object_position = self._interpolate_position(self._context.object_path_samples, playback_time)
        earth_position = self._interpolate_position(self._context.earth_path_samples, playback_time)
        if object_position is None or earth_position is None:
            self._update_topdown_playback_artists(None, None)
            if self._distance_playback_item is not None:
                self._distance_playback_item.hide()
                self._distance_playback_item.setData([], [])
            if self._magnitude_playback_item is not None:
                self._magnitude_playback_item.hide()
                self._magnitude_playback_item.setData([], [])
            self._update_sky_track_playback_marker(None, None)
            return
        self._update_topdown_playback_artists(object_position, earth_position)
        self._update_time_series_playback_markers(object_position, earth_position)
        self._update_sky_track_playback_marker(object_position, earth_position)

    def _update_gl_playback_state(self) -> None:
        if (
            self._gl_view is None
            or self._earth_current_item is None
        ):
            return
        playback_time = self._current_playback_time()
        object_position = self._interpolate_position(self._context.object_path_samples, playback_time)
        earth_position = self._interpolate_position(self._context.earth_path_samples, playback_time)
        if object_position is None or earth_position is None:
            return
        if self._object_current_item is not None:
            self._object_current_item.setData(pos=np.array([object_position], dtype=float))
        self._earth_current_item.setData(pos=np.array([earth_position], dtype=float))
        if self._observed_current_item is not None:
            self._observed_current_item.setData(pos=np.array([object_position], dtype=float))
        if self._connector_item is not None:
            self._connector_item.setData(pos=np.array([earth_position, object_position], dtype=float))
        self._set_gl_label_position("sun", (0.0, 0.0, 0.0))
        self._set_gl_label_position("earth", (float(earth_position[0]), float(earth_position[1]), float(earth_position[2])))
        if self._is_target_visible(0):
            self._set_gl_label_position("object-primary", (float(object_position[0]), float(object_position[1]), float(object_position[2])))
        for comparison_index, current_item in enumerate(self._comparison_current_items):
            if current_item is None:
                continue
            if comparison_index >= len(self._comparison_tracks()):
                continue
            comparison_position = self._interpolate_position(self._comparison_tracks()[comparison_index].path_samples, playback_time)
            if comparison_position is None:
                continue
            current_item.setData(pos=np.array([comparison_position], dtype=float))
            self._set_gl_label_position(
                f"object-{comparison_index}",
                (float(comparison_position[0]), float(comparison_position[1]), float(comparison_position[2])),
            )
        for body in self._additional_bodies():
            current_item = self._additional_body_current_items.get(body.key)
            nearest_position = self._nearest_body_sample(body, playback_time)
            if current_item is None or nearest_position is None:
                continue
            current_item.setData(pos=np.array([nearest_position], dtype=float))
            self._set_gl_label_position(f"planet-{body.key}", nearest_position)

    def _set_playback_index(self, index: int, *, update_camera: bool = False) -> None:
        sample_count = len(self._timeline_times)
        if sample_count <= 0:
            self._frame_label.setText("0/0")
            return
        bounded_index = max(0, min(int(index), sample_count - 1))
        self._set_playback_time(self._timeline_times[bounded_index], update_camera=update_camera)

    def _handle_slider_changed(self, value: int) -> None:
        if self._playback_updating:
            return
        self._set_playback_index(value, update_camera=self._camera_mode_requires_tracking())

    def _handle_table_selection_changed(self) -> None:
        if self._playback_updating:
            return
        current_row = self._table.currentRow()
        if 0 <= current_row < len(self._frame_measurements):
            self._set_playback_time(self._frame_measurements[current_row].observation_time, update_camera=self._camera_mode_requires_tracking())

    def _handle_time_input_editing_finished(self) -> None:
        if self._playback_updating:
            return
        parsed_time = self._parse_playback_time_input(self._time_input.text())
        if parsed_time is None:
            self._sync_playback_time_display(self._current_playback_time())
            return
        self._set_playback_time(parsed_time, update_camera=self._camera_mode_requires_tracking())

    def _handle_play_toggled(self, is_checked: bool) -> None:
        self._sync_play_button_icon(is_checked)
        if is_checked and len(self._timeline_times) > 1:
            self._update_playback_timer_interval()
            self._playback_last_tick_seconds = perf_counter()
            self._playback_timer.start()
        else:
            self._playback_timer.stop()
            self._playback_last_tick_seconds = None

    def _update_playback_timer_interval(self) -> None:
        self._playback_timer.setInterval(16)

    def _advance_playback(self) -> None:
        sample_count = len(self._timeline_times)
        if sample_count <= 1:
            self._playback_timer.stop()
            self._playback_last_tick_seconds = None
            return
        current_tick_seconds = perf_counter()
        elapsed_seconds = self._playback_timer.interval() / 1000.0
        if self._playback_last_tick_seconds is not None:
            elapsed_seconds = max(0.0, current_tick_seconds - self._playback_last_tick_seconds)
        self._playback_last_tick_seconds = current_tick_seconds
        speed_seconds_per_second = float(self._speed_combo.currentData() or 86400.0)
        next_time = self._wrapped_playback_time(
            self._current_playback_time() + timedelta(seconds=speed_seconds_per_second * elapsed_seconds)
        )
        self._set_playback_time(next_time, update_camera=self._camera_mode_requires_tracking())

    def _handle_camera_mode_changed(self) -> None:
        self._apply_camera_mode()

    def _camera_mode_requires_tracking(self) -> bool:
        return str(self._camera_mode_combo.currentData() or "overview") in {"object-follow", "earth-follow"}

    def _handle_show_periods_toggled(self, _checked: bool) -> None:
        self._update_periods_label()

    def _handle_label_style_changed(self, *_args) -> None:
        self._draw_plots()
        self._update_plot_playback_markers()
        QTimer.singleShot(0, self._refresh_gl_after_show)

    def _handle_sample_points_toggled(self, _checked: bool) -> None:
        self._draw_plots()
        self._update_plot_playback_markers()
        QTimer.singleShot(0, self._refresh_gl_after_show)

    def _handle_object_visibility_toggled(self, key: str, checked: bool) -> None:
        previous_states = dict(self._object_visibility_states)
        self._object_visibility_states[key] = bool(checked)
        desired_targets = self._desired_context_targets()
        desired_keys = {self._target_visibility_key(target.detection) for target in desired_targets}
        if desired_keys.issubset(self._current_target_keys()):
            self._rebuild_object_toggle_controls()
            self._draw_plots()
            self._update_plot_playback_markers()
            QTimer.singleShot(0, self._refresh_gl_after_show)
            return
        self._pending_visibility_states = previous_states
        self._rebuild_object_toggle_controls()
        self._start_context_reload_for_current_span(targets=desired_targets)

    def _handle_span_changed(self) -> None:
        if self._context_reload_worker is not None:
            return
        span_data = self._span_combo.currentData()
        if not isinstance(span_data, tuple) or len(span_data) != 3:
            return
        span_key, padding_days, sample_count = span_data
        if span_key == "custom":
            self._active_span_key = "custom"
            self._custom_span_start = self._context.window_start.astimezone(UTC)
            self._custom_span_end = self._context.window_end.astimezone(UTC)
            self._sync_custom_span_inputs_to_state()
            self._set_custom_span_controls_visible(True)
            return
        was_custom = self._active_span_key == "custom"
        self._set_custom_span_controls_visible(False)
        self._active_span_key = str(span_key)
        current_padding_days = float(getattr(self._context, "arc_padding_days", 45.0))
        if (
            not was_custom
            and padding_days is not None
            and math.isclose(current_padding_days, float(padding_days), rel_tol=0.0, abs_tol=1e-6)
        ):
            return
        self._start_context_reload(
            float(padding_days),
            int(sample_count),
            self._show_planets_checkbox.isChecked(),
        )

    def _handle_custom_span_apply(self) -> None:
        if self._context_reload_worker is not None:
            return
        try:
            window_start, window_end = self._parse_custom_span_inputs()
        except ValueError as exc:
            QMessageBox.information(self, "Custom span", str(exc))
            return
        self._active_span_key = "custom"
        self._custom_span_start = window_start
        self._custom_span_end = window_end
        sample_count = self._sample_count_for_custom_window(window_start, window_end)
        self._start_context_reload(
            0.0,
            sample_count,
            self._show_planets_checkbox.isChecked(),
            window_start=window_start,
            window_end=window_end,
        )

    def _handle_planets_toggled(self, checked: bool) -> None:
        if self._context_reload_worker is not None:
            return
        current_value = bool(getattr(self._context, "include_major_planets", False))
        if checked == current_value:
            return
        if not checked:
            self._apply_planet_context_update(replace(self._context, additional_bodies=(), include_major_planets=False))
            return
        cached_bodies = load_cached_major_planet_heliocentric_paths(self._current_major_planet_query_times())
        if cached_bodies is not None:
            self._apply_planet_context_update(replace(self._context, additional_bodies=cached_bodies, include_major_planets=True))
            return
        self._start_context_reload_for_current_span(include_major_planets=True)

    def _start_context_reload_for_current_span(
        self,
        *,
        targets: tuple[AsteroidOrbitContextTarget, ...] | None = None,
        include_major_planets: bool | None = None,
    ) -> None:
        planets = self._show_planets_checkbox.isChecked() if include_major_planets is None else bool(include_major_planets)
        span_data = self._span_combo.currentData()
        if not isinstance(span_data, tuple) or len(span_data) != 3:
            return
        span_key, padding_days, sample_count = span_data
        if span_key == "custom" or self._active_span_key == "custom":
            window_start = self._custom_span_start
            window_end = self._custom_span_end
            try:
                parsed_start, parsed_end = self._parse_custom_span_inputs()
                window_start, window_end = parsed_start, parsed_end
                self._custom_span_start = window_start
                self._custom_span_end = window_end
            except ValueError:
                window_start = self._context.window_start.astimezone(UTC)
                window_end = self._context.window_end.astimezone(UTC)
            self._start_context_reload(
                0.0,
                self._sample_count_for_custom_window(window_start, window_end),
                planets,
                targets=targets,
                window_start=window_start,
                window_end=window_end,
            )
            return
        if padding_days is None or sample_count is None:
            return
        self._start_context_reload(float(padding_days), int(sample_count), planets, targets=targets)

    def _start_context_reload(
        self,
        arc_padding_days: float,
        sample_count: int,
        include_major_planets: bool,
        *,
        targets: tuple[AsteroidOrbitContextTarget, ...] | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> None:
        if self._context_reload_worker is not None:
            return
        self._playback_timer.stop()
        self._play_button.blockSignals(True)
        self._play_button.setChecked(False)
        self._sync_play_button_icon(False)
        self._play_button.blockSignals(False)
        loading_label = f"Loading {self._span_combo.currentText()} heliocentric context from JPL Horizons"
        if include_major_planets:
            loading_label += " with planets"
        self._set_context_loading(True, loading_label + "...")
        reload_targets = tuple(targets) if targets is not None else self._desired_context_targets()
        observation_times = self._observation_times_for_reload(reload_targets, window_start=window_start, window_end=window_end)
        self._context_reload_worker = AsteroidOrbitContextWorker(
            targets=reload_targets,
            available_targets=self._available_targets,
            arc_padding_days=arc_padding_days,
            sample_count=sample_count,
            include_major_planets=include_major_planets,
            window_start=window_start,
            window_end=window_end,
            observation_times=observation_times,
        )
        self._context_reload_worker.finished.connect(self._context_reload_worker.deleteLater)
        self._context_reload_worker.progress_updated.connect(self._handle_context_reload_progress)
        self._context_reload_worker.context_completed.connect(self._handle_context_reload_completed)
        self._context_reload_worker.context_failed.connect(self._handle_context_reload_failed)
        self._context_reload_worker.start()

    def _observation_times_for_reload(
        self,
        targets: tuple[AsteroidOrbitContextTarget, ...],
        *,
        window_start: datetime | None,
        window_end: datetime | None,
    ) -> tuple[datetime, ...]:
        for target in targets:
            if target.frame_measurements:
                return tuple(measurement.observation_time for measurement in target.frame_measurements)
        if self._frame_measurements:
            return tuple(measurement.observation_time for measurement in self._frame_measurements)
        start = (window_start or self._context.window_start).astimezone(UTC)
        end = (window_end or self._context.window_end).astimezone(UTC)
        return (start, end)

    @staticmethod
    def _sample_count_for_custom_window(window_start: datetime, window_end: datetime) -> int:
        total_days = max(1.0, (window_end - window_start).total_seconds() / 86400.0)
        return min(361, max(61, int(round(total_days)) + 1))

    @staticmethod
    def _format_custom_span_date(value: datetime) -> str:
        return value.astimezone(UTC).strftime("%Y-%m-%d")

    def _sync_custom_span_inputs_to_state(self) -> None:
        if not hasattr(self, "_custom_span_start_input"):
            return
        self._custom_span_start_input.setText(self._format_custom_span_date(self._custom_span_start))
        self._custom_span_end_input.setText(self._format_custom_span_date(self._custom_span_end))

    def _set_custom_span_controls_visible(self, visible: bool) -> None:
        for widget in (
            getattr(self, "_custom_span_start_label", None),
            getattr(self, "_custom_span_start_input", None),
            getattr(self, "_custom_span_end_label", None),
            getattr(self, "_custom_span_end_input", None),
            getattr(self, "_custom_span_apply_button", None),
        ):
            if widget is not None:
                widget.setVisible(bool(visible))

    def _parse_custom_span_inputs(self) -> tuple[datetime, datetime]:
        start_time = KnownObjectOrbit3DPlannerDialog._parse_midnight_utc_date(
            self._custom_span_start_input.text(),
            label="Start date",
        )
        end_time = KnownObjectOrbit3DPlannerDialog._parse_midnight_utc_date(
            self._custom_span_end_input.text(),
            label="End date",
        )
        if end_time <= start_time:
            raise ValueError("End date must be later than start date.")
        return start_time, end_time

    def _set_context_loading(self, is_loading: bool, message: str | None = None) -> None:
        self._span_combo.setEnabled(not is_loading)
        self._custom_span_start_input.setEnabled(not is_loading)
        self._custom_span_end_input.setEnabled(not is_loading)
        self._custom_span_apply_button.setEnabled(not is_loading)
        self._show_planets_checkbox.setEnabled(not is_loading)
        self._camera_mode_combo.setEnabled(not is_loading)
        self._play_button.setEnabled((not is_loading) and len(self._timeline_times) > 1)
        self._reset_time_button.setEnabled(not is_loading)
        self._settings_button.setEnabled(not is_loading)
        self._object_lookup_button.setEnabled((not is_loading) and self._lookup_exact_target is not None)
        self._object_menu_button.setEnabled(
            (not is_loading) and (bool(self._available_targets) or self._search_nearby_targets is not None)
        )
        self._speed_combo.setEnabled(not is_loading)
        self._time_input.setEnabled(not is_loading)
        self._frame_slider.setEnabled(not is_loading)
        self._table.setEnabled(not is_loading)
        self._summary_label.setText(message if is_loading and message else self._summary_text())

    def _handle_context_reload_progress(self, message: str) -> None:
        self._summary_label.setText(message)

    def _handle_context_reload_completed(self, result) -> None:
        previous_playback_time = self._current_playback_time()
        self._context_reload_worker = None
        reload_targets = tuple(getattr(result, "targets", ()))
        self._pending_visibility_states = None
        result_available_targets = tuple(getattr(result, "available_targets", ()))
        self._context_targets = tuple(reload_targets)
        self._available_targets = self._normalize_available_targets(self._context_targets, result_available_targets or self._available_targets)
        self._context = result.context
        if self._active_span_key == "custom":
            self._custom_span_start = self._context.window_start.astimezone(UTC)
            self._custom_span_end = self._context.window_end.astimezone(UTC)
            self._sync_custom_span_inputs_to_state()
        self._sync_primary_target_state()
        self._observation_reset_time = self._default_observation_reset_time()
        self._refresh_context_arrays()
        self._rebuild_object_toggle_controls()
        self._sync_span_combo_to_context()
        self._sync_planets_checkbox_to_context()
        self._populate_table()
        self._update_periods_label()
        self._draw_plots()
        self._set_context_loading(False)
        self._sync_playback_controls_to_context(preferred_time=previous_playback_time, update_camera=True)
        QTimer.singleShot(0, self._refresh_gl_after_show)

    def _handle_context_reload_failed(self, message: str) -> None:
        self._context_reload_worker = None
        if self._pending_visibility_states is not None:
            self._object_visibility_states = self._pending_visibility_states
            self._pending_visibility_states = None
            self._rebuild_object_toggle_controls()
        self._sync_span_combo_to_context()
        self._sync_planets_checkbox_to_context()
        self._set_context_loading(False)
        QMessageBox.warning(self, "3D view update failed", f"Could not update the 3D view. {self._summarize_error_text(message)}")

    def _apply_camera_mode(self) -> None:
        if self._gl_view is None:
            return
        scene_points = self._scene_points()
        scene_extent = max(1.0, float(np.max(np.linalg.norm(scene_points, axis=1))))
        playback_time = self._current_playback_time()
        object_position = self._interpolate_position(self._context.object_path_samples, playback_time)
        earth_position = self._interpolate_position(self._context.earth_path_samples, playback_time)
        if object_position is None:
            object_position = np.zeros(3, dtype=float)
        if earth_position is None:
            earth_position = np.zeros(3, dtype=float)
        mode = str(self._camera_mode_combo.currentData() or "overview")
        if mode == "topdown":
            target = QVector3D(0.0, 0.0, 0.0)
            self._gl_view.setCameraPosition(pos=target, distance=scene_extent * 2.5, elevation=90.0, azimuth=-90.0)
        elif mode == "side":
            target = QVector3D(0.0, 0.0, 0.0)
            self._gl_view.setCameraPosition(pos=target, distance=scene_extent * 2.3, elevation=6.0, azimuth=0.0)
        elif mode == "object-follow":
            target = QVector3D(float(object_position[0]), float(object_position[1]), float(object_position[2]))
            self._gl_view.setCameraPosition(pos=target, distance=max(0.9, scene_extent * 0.85), elevation=18.0, azimuth=-35.0)
        elif mode == "earth-follow":
            target = QVector3D(float(earth_position[0]), float(earth_position[1]), float(earth_position[2]))
            self._gl_view.setCameraPosition(pos=target, distance=max(0.9, scene_extent * 0.85), elevation=16.0, azimuth=30.0)
        else:
            target = QVector3D(0.0, 0.0, 0.0)
            self._gl_view.setCameraPosition(pos=target, distance=scene_extent * 2.1, elevation=24.0, azimuth=-58.0)

    def _add_gl_path(
        self,
        samples,
        points: np.ndarray,
        *,
        color: tuple[float, float, float],
        glow_color: tuple[float, float, float],
        peak_alpha: float,
        base_alpha: float,
        glow_peak_alpha: float,
        glow_base_alpha: float,
    ) -> None:
        if self._gl_view is None or points.size == 0:
            return
        glow_line = gl.GLLinePlotItem(
            pos=points,
            color=self._path_color_array(samples, glow_color, peak_alpha=glow_peak_alpha, base_alpha=glow_base_alpha),
            width=4.6,
            antialias=True,
            mode="line_strip",
        )
        main_line = gl.GLLinePlotItem(
            pos=points,
            color=self._path_color_array(samples, color, peak_alpha=peak_alpha, base_alpha=base_alpha),
            width=1.8,
            antialias=True,
            mode="line_strip",
        )
        self._gl_scene_items.extend([glow_line, main_line])
        self._gl_view.addItem(glow_line)
        self._gl_view.addItem(main_line)

    def _add_gl_observed_points(
        self,
        points: np.ndarray,
        *,
        color: tuple[float, float, float, float],
        size: float,
    ) -> None:
        if self._gl_view is None or points.size == 0:
            return
        observed_colors = np.tile(np.array([[color[0], color[1], color[2], color[3]]], dtype=float), (len(points), 1))
        observed_item = gl.GLScatterPlotItem(pos=points, color=observed_colors, size=size, pxMode=True)
        self._gl_scene_items.append(observed_item)
        self._gl_view.addItem(observed_item)

    def _add_gl_sun(self) -> None:
        if self._gl_view is None:
            return
        glow = gl.GLScatterPlotItem(pos=np.array([[0.0, 0.0, 0.0]], dtype=float), color=np.array([[1.0, 0.90, 0.35, 0.16]], dtype=float), size=34.0, pxMode=True)
        core = gl.GLScatterPlotItem(pos=np.array([[0.0, 0.0, 0.0]], dtype=float), color=np.array([[1.0, 0.82, 0.15, 0.98]], dtype=float), size=16.0, pxMode=True)
        self._gl_scene_items.extend([glow, core])
        self._gl_view.addItem(glow)
        self._gl_view.addItem(core)

    @staticmethod
    def _starfield_radius(scene_extent: float) -> float:
        return max(_KNOWN_OBJECT_3D_STARFIELD_MIN_RADIUS_AU, float(scene_extent) * _KNOWN_OBJECT_3D_STARFIELD_EXTENT_FACTOR)

    @staticmethod
    def _max_camera_distance(scene_extent: float) -> float:
        return float(scene_extent) * _KNOWN_OBJECT_3D_MAX_ZOOM_OUT_EXTENT_FACTOR

    def _apply_gl_camera_distance_limits(self, scene_extent: float) -> None:
        if self._gl_view is None or not hasattr(self._gl_view, "set_camera_distance_limits"):
            return
        self._gl_view.set_camera_distance_limits(
            _KNOWN_OBJECT_3D_MIN_CAMERA_DISTANCE_AU,
            self._max_camera_distance(scene_extent),
            minimum_far_clip=self._starfield_radius(scene_extent) * 1.2,
        )

    def _add_gl_starfield(self, scene_extent: float) -> None:
        if self._gl_view is None:
            return
        radius = self._starfield_radius(scene_extent)
        rng = np.random.default_rng(20260411)
        star_count = 220
        phi_values = rng.uniform(0.0, 2.0 * math.pi, star_count)
        cos_theta_values = rng.uniform(-1.0, 1.0, star_count)
        theta_values = np.arccos(cos_theta_values)
        x_values = radius * np.sin(theta_values) * np.cos(phi_values)
        y_values = radius * np.sin(theta_values) * np.sin(phi_values)
        z_values = radius * np.cos(theta_values)
        positions = np.column_stack([x_values, y_values, z_values]).astype(float)
        sizes = rng.uniform(2.0, 8.0, star_count)
        colors = np.ones((star_count, 4), dtype=float)
        colors[:, 0:3] = np.array([0.88, 0.93, 1.0])
        colors[::3, 0:3] = np.array([1.0, 1.0, 1.0])
        colors[:, 3] = 0.24
        star_item = gl.GLScatterPlotItem(pos=positions, color=colors, size=sizes, pxMode=True)
        self._gl_scene_items.append(star_item)
        self._gl_view.addItem(star_item)

    def _path_opacity_profile(self, samples, *, peak_alpha: float, base_alpha: float) -> np.ndarray:
        sample_count = len(samples)
        if sample_count <= 0:
            return np.zeros((0,), dtype=float)
        window_start = self._context.window_start
        window_end = self._context.window_end
        window_seconds = max(1.0, (window_end - window_start).total_seconds())
        observed_samples = self._context.observation_object_samples
        use_window_edge_fade = getattr(self, "_active_span_key", "local") == "custom" or not observed_samples
        if not use_window_edge_fade and observed_samples:
            observed_start = observed_samples[0].observation_time
            observed_end = observed_samples[-1].observation_time
            fade_before_seconds = (observed_start - window_start).total_seconds()
            fade_after_seconds = (window_end - observed_end).total_seconds()
            # Custom-like windows where "observations" already fill the span leave no
            # padded fade region; fall back to a proportional soft edge.
            if fade_before_seconds < window_seconds * 0.02 and fade_after_seconds < window_seconds * 0.02:
                use_window_edge_fade = True
            else:
                fade_before_seconds = max(1.0, fade_before_seconds)
                fade_after_seconds = max(1.0, fade_after_seconds)
                return self._opacity_profile_for_bright_core(
                    samples,
                    bright_start=observed_start,
                    bright_end=observed_end,
                    fade_before_seconds=fade_before_seconds,
                    fade_after_seconds=fade_after_seconds,
                    peak_alpha=peak_alpha,
                    base_alpha=base_alpha,
                )
        fade_seconds = max(1.0, window_seconds * _KNOWN_OBJECT_3D_PATH_EDGE_FADE_FRACTION)
        bright_start = window_start + timedelta(seconds=fade_seconds)
        bright_end = window_end - timedelta(seconds=fade_seconds)
        if bright_end <= bright_start:
            midpoint = window_start + (window_end - window_start) / 2
            bright_start = midpoint
            bright_end = midpoint
        return self._opacity_profile_for_bright_core(
            samples,
            bright_start=bright_start,
            bright_end=bright_end,
            fade_before_seconds=fade_seconds,
            fade_after_seconds=fade_seconds,
            peak_alpha=peak_alpha,
            base_alpha=base_alpha,
        )

    @staticmethod
    def _opacity_profile_for_bright_core(
        samples,
        *,
        bright_start: datetime,
        bright_end: datetime,
        fade_before_seconds: float,
        fade_after_seconds: float,
        peak_alpha: float,
        base_alpha: float,
    ) -> np.ndarray:
        alpha_values = np.empty(len(samples), dtype=float)
        for index, sample in enumerate(samples):
            sample_time = sample.observation_time
            if sample_time < bright_start:
                distance_ratio = (bright_start - sample_time).total_seconds() / fade_before_seconds
            elif sample_time > bright_end:
                distance_ratio = (sample_time - bright_end).total_seconds() / fade_after_seconds
            else:
                distance_ratio = 0.0
            fade_strength = max(0.0, 1.0 - min(1.0, distance_ratio))
            alpha_values[index] = base_alpha + ((peak_alpha - base_alpha) * (fade_strength ** 1.65))
        return alpha_values

    def _path_color_array(self, samples, color: tuple[float, float, float], *, peak_alpha: float, base_alpha: float) -> np.ndarray:
        alpha_values = self._path_opacity_profile(samples, peak_alpha=peak_alpha, base_alpha=base_alpha)
        colors = np.tile(np.array([[color[0], color[1], color[2], 1.0]], dtype=float), (len(alpha_values), 1))
        colors[:, 3] = alpha_values
        return colors

    def _add_faded_2d_path(
        self,
        ax,
        samples,
        *,
        color: tuple[float, float, float],
        glow_color: tuple[float, float, float],
        linewidth: float,
        glow_linewidth: float,
        label: str,
    ) -> None:
        points = self._sample_points(samples)
        if len(points) < 2:
            return
        xy_points = points[:, :2]
        segments = np.stack([xy_points[:-1], xy_points[1:]], axis=1)
        main_alpha_profile = self._path_opacity_profile(samples, peak_alpha=0.98, base_alpha=0.22)
        glow_alpha_profile = self._path_opacity_profile(samples, peak_alpha=0.24, base_alpha=0.05)
        main_alphas = 0.5 * (main_alpha_profile[:-1] + main_alpha_profile[1:])
        glow_alphas = 0.5 * (glow_alpha_profile[:-1] + glow_alpha_profile[1:])
        glow_colors = np.column_stack([np.tile(np.array(glow_color, dtype=float), (len(segments), 1)), glow_alphas])
        main_colors = np.column_stack([np.tile(np.array(color, dtype=float), (len(segments), 1)), main_alphas])
        ax.add_collection(LineCollection(segments, colors=glow_colors, linewidths=glow_linewidth, zorder=2))
        ax.add_collection(LineCollection(segments, colors=main_colors, linewidths=linewidth, zorder=3))
        ax.plot([], [], color=(*color, 1.0), linewidth=linewidth, label=label)

    @staticmethod
    def _summarize_error_text(message: str, *, max_length: int = 200) -> str:
        normalized = " ".join(str(message).split())
        if len(normalized) <= max_length:
            return normalized
        return f"{normalized[: max_length - 1].rstrip()}..."

    @staticmethod
    def _sample_points(samples) -> np.ndarray:
        if not samples:
            return np.zeros((0, 3), dtype=float)
        return np.array([[sample.x_au, sample.y_au, sample.z_au] for sample in samples], dtype=float)

    def _timeline_samples(self):
        if self._context.object_path_samples:
            return self._context.object_path_samples
        if self._context.observation_object_samples:
            return self._context.observation_object_samples
        if self._context.earth_path_samples:
            return self._context.earth_path_samples
        if self._context.observation_earth_samples:
            return self._context.observation_earth_samples
        return ()

    def _playback_window_bounds(self) -> tuple[datetime, datetime]:
        if self._timeline_times:
            return (self._timeline_times[0], self._timeline_times[-1])
        return (self._context.window_start, self._context.window_end)

    def _clamp_playback_time(self, observation_time: datetime) -> datetime:
        window_start, window_end = self._playback_window_bounds()
        if observation_time < window_start:
            return window_start
        if observation_time > window_end:
            return window_end
        return observation_time

    def _wrapped_playback_time(self, observation_time: datetime) -> datetime:
        window_start, window_end = self._playback_window_bounds()
        total_seconds = max(0.0, (window_end - window_start).total_seconds())
        if total_seconds <= 0.0:
            return window_start
        offset_seconds = (observation_time - window_start).total_seconds() % total_seconds
        return window_start + timedelta(seconds=offset_seconds)

    def _nearest_timeline_index(self, observation_time: datetime) -> int:
        if self._timeline_timestamps.size == 0:
            return 0
        return int(np.abs(self._timeline_timestamps - float(observation_time.timestamp())).argmin())

    @staticmethod
    def _format_playback_time_text(observation_time: datetime) -> str:
        return observation_time.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    @staticmethod
    def _parse_playback_time_input(raw_text: str) -> datetime | None:
        normalized = " ".join(str(raw_text).strip().split())
        if len(normalized) >= 11 and normalized[10] in {"T", "t"}:
            normalized = normalized[:10] + " " + normalized[11:]
        if not normalized:
            return None
        if normalized.upper().endswith(" UTC"):
            normalized = normalized[:-4].strip()
        for time_format in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(normalized, time_format).replace(tzinfo=UTC)
            except ValueError:
                continue
        return None

    def _sync_playback_time_display(self, observation_time: datetime) -> None:
        self._time_input.setText(self._format_playback_time_text(observation_time))

    def _sync_playback_controls_to_context(self, *, preferred_time: datetime | None, update_camera: bool) -> None:
        timeline_count = max(1, len(self._timeline_times))
        self._frame_slider.setRange(0, timeline_count - 1)
        target_time = preferred_time if preferred_time is not None else self._context.reference_time
        self._set_playback_time(target_time, update_camera=update_camera)

    def _default_observation_reset_time(self) -> datetime:
        if self._frame_measurements:
            return self._frame_measurements[0].observation_time
        if self._context.observation_object_samples:
            return self._context.observation_object_samples[0].observation_time
        if self._context.observation_earth_samples:
            return self._context.observation_earth_samples[0].observation_time
        return self._context.reference_time

    def _current_major_planet_query_times(self) -> tuple[datetime, ...]:
        if self._context.object_path_samples:
            return tuple(sample.observation_time for sample in self._context.object_path_samples)
        if self._context.earth_path_samples:
            return tuple(sample.observation_time for sample in self._context.earth_path_samples)
        if self._timeline_times:
            return self._timeline_times
        return (self._context.reference_time,)

    def _apply_planet_context_update(self, context: KnownObjectHeliocentricContext) -> None:
        preferred_time = self._current_playback_time()
        self._context = context
        self._refresh_context_arrays()
        self._sync_planets_checkbox_to_context()
        self._update_periods_label()
        self._draw_plots()
        self._set_context_loading(False)
        self._sync_playback_controls_to_context(preferred_time=preferred_time, update_camera=True)
        QTimer.singleShot(0, self._refresh_gl_after_show)

    def _handle_reset_time_clicked(self) -> None:
        self._set_playback_time(self._observation_reset_time, update_camera=self._camera_mode_requires_tracking())

    def _set_playback_time(self, observation_time: datetime, *, update_camera: bool = False) -> None:
        bounded_time = self._clamp_playback_time(observation_time)
        timeline_index = self._nearest_timeline_index(bounded_time)
        self._playback_time = bounded_time
        self._playback_index = timeline_index
        self._playback_updating = True
        try:
            self._frame_slider.setValue(timeline_index)
            self._sync_playback_time_display(bounded_time)
        finally:
            self._playback_updating = False
        sample_total = max(1, len(self._timeline_times))
        self._frame_label.setText(f"{timeline_index + 1}/{sample_total}")
        self._update_gl_playback_state()
        self._update_plot_playback_markers()
        if update_camera:
            self._apply_camera_mode()

    @staticmethod
    def _interpolate_position(samples, observation_time: datetime) -> np.ndarray | None:
        if not samples:
            return None
        if len(samples) == 1:
            sample = samples[0]
            return np.array([sample.x_au, sample.y_au, sample.z_au], dtype=float)
        timestamps = np.array([sample.observation_time.timestamp() for sample in samples], dtype=float)
        target_timestamp = float(observation_time.timestamp())
        x_values = np.array([sample.x_au for sample in samples], dtype=float)
        y_values = np.array([sample.y_au for sample in samples], dtype=float)
        z_values = np.array([sample.z_au for sample in samples], dtype=float)
        return np.array([
            float(np.interp(target_timestamp, timestamps, x_values)),
            float(np.interp(target_timestamp, timestamps, y_values)),
            float(np.interp(target_timestamp, timestamps, z_values)),
        ], dtype=float)

    @staticmethod
    def _interpolate_magnitude(samples, observation_time: datetime) -> float | None:
        if not samples:
            return None
        if len(samples) == 1:
            return float(samples[0].literature_magnitude)
        timestamps = np.array([sample.observation_time.timestamp() for sample in samples], dtype=float)
        magnitudes = np.array([sample.literature_magnitude for sample in samples], dtype=float)
        return float(np.interp(float(observation_time.timestamp()), timestamps, magnitudes))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._position_periods_panel()
        QTimer.singleShot(0, self._refresh_gl_after_show)
        QTimer.singleShot(75, self._refresh_gl_after_show)
        QTimer.singleShot(180, self._refresh_gl_after_show)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_periods_panel()
        if gl is not None and self.isVisible() and (self._gl_view is None or not self._gl_scene_items):
            QTimer.singleShot(0, self._refresh_gl_after_show)

    def closeEvent(self, event) -> None:
        self._playback_timer.stop()
        self._playback_last_tick_seconds = None
        self._context_reload_worker = None
        super().closeEvent(event)

    @staticmethod
    def _vector_norm(x_value: float, y_value: float, z_value: float) -> float:
        return float(math.sqrt((x_value * x_value) + (y_value * y_value) + (z_value * z_value)))

    @staticmethod
    def _apply_space_theme(ax, *, is_3d: bool = False) -> None:
        ax.set_facecolor("#08101d")
        ax.tick_params(colors="#dbe7ff")
        for spine in ax.spines.values():
            spine.set_color("#425a82")
        ax.grid(True, color="#5d6b8a", alpha=0.18)

    @staticmethod
    def _finalize_space_axes(ax) -> None:
        ax.xaxis.label.set_color("#edf4ff")
        ax.yaxis.label.set_color("#edf4ff")
        ax.title.set_color("#f6fbff")
        ax.tick_params(colors="#dbe7ff")

    @staticmethod
    def _style_space_legend(legend) -> None:
        if legend is None:
            return
        legend.get_frame().set_facecolor("#0d1424")
        legend.get_frame().set_edgecolor("#314669")
        legend.get_frame().set_alpha(0.92)
        for text in legend.get_texts():
            text.set_color("#eef5ff")

    @staticmethod
    def _add_starfield_2d(ax, x_min: float, x_max: float, y_min: float, y_max: float) -> None:
        rng = np.random.default_rng(20260411)
        x_span = max(0.1, x_max - x_min)
        y_span = max(0.1, y_max - y_min)
        star_count = 60
        x_values = rng.uniform(x_min - (0.12 * x_span), x_max + (0.12 * x_span), star_count)
        y_values = rng.uniform(y_min - (0.12 * y_span), y_max + (0.12 * y_span), star_count)
        sizes = rng.uniform(2.0, 8.0, star_count)
        colors = ["#ffffff" if index % 3 else "#cfe0ff" for index in range(star_count)]
        ax.scatter(x_values, y_values, s=sizes, c=colors, alpha=0.32, linewidths=0, zorder=0)


class AsteroidDiscoveryDialog(QDialog):
    def __init__(
        self,
        *,
        display: AnnotatedImageDisplay,
        result: MovingObjectDiscoveryResult,
        render_settings: AnnotatedImageRenderSettings | None = None,
        mark_candidate_on_main_image: Callable[[MovingObjectCandidate], None] | None = None,
        mark_all_candidates_on_main_image: Callable[[tuple[MovingObjectCandidate, ...]], None] | None = None,
        synthetic_track_candidate: Callable[[MovingObjectCandidate], None] | None = None,
        continue_sweep: Callable[[], None] | None = None,
        candidate_label_options: Sequence[tuple[str, str]] = (),
        candidate_label_lookup: Callable[[MovingObjectCandidate], str | None] | None = None,
        save_candidate_label: Callable[[MovingObjectCandidate, str], None] | None = None,
        candidate_prediction_lookup: Callable[[MovingObjectCandidate], tuple[str, float] | None] | None = None,
        train_candidate_model: Callable[[], str] | None = None,
        candidate_training_summary: Callable[[], str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Discover Results")
        self.resize(1540, 920)
        self.setMinimumSize(1360, 860)
        self._display = display
        self._result = result
        self._render_settings = render_settings
        self._mark_candidate_callback = mark_candidate_on_main_image
        self._mark_all_candidates_callback = mark_all_candidates_on_main_image
        self._synthetic_track_candidate_callback = synthetic_track_candidate
        self._continue_sweep_callback = continue_sweep
        self._candidate_label_options = tuple(candidate_label_options)
        self._candidate_label_lookup = candidate_label_lookup
        self._save_candidate_label_callback = save_candidate_label
        self._candidate_prediction_lookup = candidate_prediction_lookup
        self._train_candidate_model_callback = train_candidate_model
        self._candidate_training_summary_callback = candidate_training_summary
        self._quick_label_dialog: MovingObjectQuickLabelDialog | None = None
        self._trajectory_dialogs: list[MovingObjectTrajectoryDialog] = []
        self._summary_metric_value_labels: dict[str, QLabel] = {}
        self._loading_message = ""
        summary_panel = self._build_summary_panel()

        self._tabs = QTabWidget(self)

        self._recovered_table = QTableWidget(len(result.recovered_known_objects), 8, self)
        self._recovered_table.setHorizontalHeaderLabels(["Object", "Source", "V_mag", "In Limit", "Frames", "Recovered", "Motion", "Residual Score"])
        self._configure_table(self._recovered_table, ((0, 180), (1, 84), (2, 70), (3, 80), (4, 70), (5, 80), (6, 160), (7, 90)))
        self._recovered_table.itemSelectionChanged.connect(self._handle_selection_changed)

        self._missed_table = QTableWidget(len(result.missed_known_objects), 6, self)
        self._missed_table.setHorizontalHeaderLabels(["Object", "V_mag", "In Limit", "Status", "Confidence", "Motion"])
        self._configure_table(self._missed_table, ((0, 180), (1, 70), (2, 80), (3, 180), (4, 90), (5, 140)))
        self._missed_table.itemSelectionChanged.connect(self._handle_selection_changed)

        self._unmatched_table = QTableWidget(len(result.candidates), 8, self)
        self._unmatched_table.setHorizontalHeaderLabels(["Candidate", "Source", "Frames", "Motion", "Residual Score", "Deflection RMS", "Label", "ML Score"])
        self._configure_table(self._unmatched_table, ((0, 90), (1, 84), (2, 70), (3, 150), (4, 90), (5, 90), (6, 120), (7, 120)))
        self._unmatched_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._unmatched_table.itemSelectionChanged.connect(self._handle_selection_changed)

        self._review_table = QTableWidget(len(result.review_candidates), 8, self)
        self._review_table.setHorizontalHeaderLabels(["Candidate", "Source", "Frames", "Motion", "Residual Score", "Deflection RMS", "Label", "ML Score"])
        self._configure_table(self._review_table, ((0, 90), (1, 84), (2, 70), (3, 150), (4, 90), (5, 90), (6, 120), (7, 120)))
        self._review_table.itemSelectionChanged.connect(self._handle_selection_changed)

        self._tabs.addTab(self._recovered_table, f"Known Recovered ({len(result.recovered_known_objects)})")
        self._tabs.addTab(self._missed_table, f"Known Missed ({len(result.missed_known_objects)})")
        self._tabs.addTab(self._unmatched_table, f"Potential Discoveries ({len(result.candidates)})")
        self._tabs.addTab(self._review_table, f"Borderline Review ({len(result.review_candidates)})")
        self._tabs.currentChanged.connect(self._handle_tab_changed)

        self._image_view = AnnotatedImageView(self)
        self._details_output = QPlainTextEdit(self)
        self._details_output.setReadOnly(True)

        self._trajectory_button = QPushButton("Trajectory...", self)
        self._trajectory_button.clicked.connect(self._open_selected_trajectory)
        self._apply_trajectory_button_style()
        self._mark_selected_button = QPushButton("Mark Selected", self)
        self._mark_selected_button.clicked.connect(self._mark_selected_candidate_on_main_image)
        self._synthetic_track_button = QPushButton("Synthetic Track...", self)
        self._synthetic_track_button.clicked.connect(self._synthetic_track_selected_candidate)
        self._candidate_label_combo = QComboBox(self)
        self._candidate_label_combo.addItem("Choose label", "")
        for label_text, label_value in self._candidate_label_options:
            self._candidate_label_combo.addItem(label_text, label_value)
        self._candidate_label_combo.setEnabled(False)
        self._save_candidate_label_button = QPushButton("Save Label", self)
        self._save_candidate_label_button.clicked.connect(self._save_selected_candidate_label)
        self._save_candidate_label_button.setEnabled(False)
        self._train_candidate_model_button = QPushButton("Train Model", self)
        self._train_candidate_model_button.clicked.connect(self._train_candidate_model)
        self._train_candidate_model_button.setEnabled(self._train_candidate_model_callback is not None)
        self._quick_label_button = QPushButton("Label...", self)
        self._quick_label_button.clicked.connect(self._open_quick_label_dialog)
        self._quick_label_button.setEnabled(False)
        self._candidate_training_status_label = QLabel(self._candidate_training_summary_text(), self)
        self._candidate_training_status_label.setWordWrap(True)
        self._export_button = QToolButton(self)
        self._export_button.setText("Export...")
        self._export_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._export_menu = QMenu(self._export_button)
        self._export_button.setMenu(self._export_menu)
        self._export_benchmark_action = QAction("Benchmark CSV...", self._export_menu)
        self._export_benchmark_action.triggered.connect(self._export_benchmark_table)
        self._export_menu.addAction(self._export_benchmark_action)
        self._export_candidates_action = QAction("Candidate Review CSV...", self._export_menu)
        self._export_candidates_action.triggered.connect(self._export_unmatched_candidates_table)
        self._export_menu.addAction(self._export_candidates_action)
        self._export_summary_action = QAction("Summary CSV...", self._export_menu)
        self._export_summary_action.triggered.connect(self._export_summary_table)
        self._export_menu.addAction(self._export_summary_action)
        self._continue_sweep_button = QPushButton("Continue", self)
        self._continue_sweep_button.clicked.connect(self._continue_sweep_requested)
        self._continue_sweep_button.setEnabled(False)

        close_button = QPushButton("Close", self)
        close_button.clicked.connect(self.accept)

        right_panel = QWidget(self)
        right_layout = QVBoxLayout()
        right_layout.addWidget(self._image_view, stretch=1)
        right_layout.addWidget(self._details_output)
        right_panel.setLayout(right_layout)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._tabs)
        splitter.addWidget(right_panel)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([780, 900])

        layout = QVBoxLayout()
        layout.addWidget(summary_panel)
        layout.addWidget(splitter, stretch=1)
        training_row = QHBoxLayout()
        training_row.addWidget(QLabel("Candidate Label", self))
        training_row.addWidget(self._candidate_label_combo)
        training_row.addWidget(self._save_candidate_label_button)
        training_row.addWidget(self._train_candidate_model_button)
        training_row.addWidget(self._candidate_training_status_label, stretch=1)
        layout.addLayout(training_row)
        button_row = QHBoxLayout()
        button_row.addWidget(self._trajectory_button)
        button_row.addWidget(self._mark_selected_button)
        button_row.addWidget(self._quick_label_button)
        button_row.addWidget(self._synthetic_track_button)
        button_row.addWidget(self._export_button)
        button_row.addStretch(1)
        button_row.addWidget(self._continue_sweep_button)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)
        self.setLayout(layout)

        self._populate_recovered_table()
        self._populate_missed_table()
        self._populate_unmatched_table()
        self._populate_review_table()
        self._select_first_available_row()
        self._update_mark_buttons_state()
        self._sync_candidate_training_controls()
        self.set_continue_sweep_enabled(False)

    def _build_summary_panel(self) -> QWidget:
        panel = QFrame(self)
        panel.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        metrics_layout = QGridLayout()
        metrics_layout.setContentsMargins(0, 0, 0, 0)
        metrics_layout.setHorizontalSpacing(10)
        metrics_layout.setVerticalSpacing(10)
        metric_specs = (
            ("visible_limit", "Visible Limit", self._visible_limit_summary_value()),
            ("known_in_limit", "Known In Limit", self._count_summary_text(self._result.benchmark_known_count, "object")),
            (
                "recovered_in_limit",
                "Recovered In Limit",
                f"{self._result.benchmark_recovered_count} of {self._result.benchmark_known_count}",
            ),
            (
                "potential_discoveries",
                "Potential Discoveries",
                self._count_summary_text(len(self._result.candidates), "candidate"),
            ),
            (
                "borderline_review",
                "Borderline Review",
                self._count_summary_text(len(self._result.review_candidates), "tracklet"),
            ),
        )
        for column_index, (metric_key, title, value) in enumerate(metric_specs):
            metrics_layout.addWidget(self._build_summary_metric_card(metric_key, title, value, panel), 0, column_index)
            metrics_layout.setColumnStretch(column_index, 1)
        layout.addLayout(metrics_layout)

        self._summary_overview_label = QLabel(self._discovery_overview_text(), panel)
        self._summary_overview_label.setWordWrap(True)
        layout.addWidget(self._summary_overview_label)

        self._summary_method_label = QLabel(self._discovery_method_text(), panel)
        self._summary_method_label.setWordWrap(True)
        self._summary_method_label.setVisible(bool(self._summary_method_label.text()))
        layout.addWidget(self._summary_method_label)

        self._progress_note_label = QLabel(panel)
        self._progress_note_label.setWordWrap(True)
        self._progress_note_label.setVisible(False)
        layout.addWidget(self._progress_note_label)
        return panel

    def update_result(
        self,
        result: MovingObjectDiscoveryResult,
        *,
        display: AnnotatedImageDisplay | None = None,
        render_settings: AnnotatedImageRenderSettings | None = None,
    ) -> None:
        if self._quick_label_dialog is not None:
            self._quick_label_dialog.close()
            self._quick_label_dialog = None
        current_index = self._tabs.currentIndex()
        self._result = result
        if display is not None:
            self._display = display
        if render_settings is not None:
            self._render_settings = render_settings
        self._refresh_summary_panel()
        self._reset_table(self._recovered_table, len(result.recovered_known_objects))
        self._reset_table(self._missed_table, len(result.missed_known_objects))
        self._reset_table(self._unmatched_table, len(result.candidates))
        self._reset_table(self._review_table, len(result.review_candidates))
        self._populate_recovered_table()
        self._populate_missed_table()
        self._populate_unmatched_table()
        self._populate_review_table()
        self._refresh_candidate_training_summary()
        self._tabs.setTabText(0, f"Known Recovered ({len(result.recovered_known_objects)})")
        self._tabs.setTabText(1, f"Known Missed ({len(result.missed_known_objects)})")
        self._tabs.setTabText(2, f"Potential Discoveries ({len(result.candidates)})")
        self._tabs.setTabText(3, f"Borderline Review ({len(result.review_candidates)})")
        if 0 <= current_index < self._tabs.count():
            self._tabs.setCurrentIndex(current_index)
        self._select_first_available_row()
        self._apply_loading_message()

    def _refresh_candidate_training_summary(self) -> None:
        self._candidate_training_status_label.setText(self._candidate_training_summary_text())

    def _candidate_training_summary_text(self) -> str:
        if self._candidate_training_summary_callback is None:
            return "Candidate labels are saved locally after Discover review."
        return self._candidate_training_summary_callback()

    def _format_candidate_training_label(self, label: str) -> str:
        return str(label or "").replace("_", " ").title()

    def _candidate_label_value(self, candidate: MovingObjectCandidate) -> str | None:
        if self._candidate_label_lookup is None:
            return None
        return self._candidate_label_lookup(candidate)

    def _candidate_label_cell(self, candidate: MovingObjectCandidate) -> str:
        label = self._candidate_label_value(candidate)
        if not label:
            return "-"
        return self._format_candidate_training_label(label)

    def _candidate_prediction_cell(self, candidate: MovingObjectCandidate) -> str:
        if self._candidate_prediction_lookup is None:
            return "-"
        prediction = self._candidate_prediction_lookup(candidate)
        if prediction is None:
            return "-"
        label, confidence = prediction
        return f"{self._format_candidate_training_label(label)} {float(confidence):.0%}"

    def _selected_label_candidate(self) -> MovingObjectCandidate | None:
        current = self._tabs.currentWidget()
        if current is self._review_table:
            return self._selected_review_candidate()
        if current is self._unmatched_table:
            return self._selected_candidate()
        return None

    def _sync_candidate_training_controls(self) -> None:
        candidate = self._selected_label_candidate()
        can_save = candidate is not None and self._save_candidate_label_callback is not None and self._candidate_label_combo.count() > 1
        quick_label_candidates = self._quick_label_candidates()
        self._candidate_label_combo.setEnabled(can_save)
        self._save_candidate_label_button.setEnabled(can_save)
        self._train_candidate_model_button.setEnabled(self._train_candidate_model_callback is not None)
        self._quick_label_button.setEnabled(bool(quick_label_candidates) and self._save_candidate_label_callback is not None)
        selected_label = ""
        if candidate is not None:
            selected_label = self._candidate_label_value(candidate) or ""
        label_index = self._candidate_label_combo.findData(selected_label)
        self._candidate_label_combo.setCurrentIndex(0 if label_index < 0 else label_index)
        self._refresh_candidate_training_summary()

    def _save_selected_candidate_label(self) -> None:
        candidate = self._selected_label_candidate()
        label = str(self._candidate_label_combo.currentData() or "")
        if candidate is None or self._save_candidate_label_callback is None or not label:
            return
        self._save_candidate_label_callback(candidate, label)
        self._refresh_candidate_training_tables()
        self._sync_candidate_training_controls()
        self._handle_selection_changed()

    def _train_candidate_model(self) -> None:
        if self._train_candidate_model_callback is None:
            return
        message = self._train_candidate_model_callback()
        self._refresh_candidate_training_tables()
        self._sync_candidate_training_controls()
        self._handle_selection_changed()
        existing_text = self._details_output.toPlainText()
        if message:
            self._details_output.setPlainText(str(message) if not existing_text else f"{message}\n\n{existing_text}")

    def _refresh_candidate_training_tables(self) -> None:
        self._update_candidate_training_table_cells(self._unmatched_table, self._result.candidates)
        self._update_candidate_training_table_cells(self._review_table, self._result.review_candidates)

    def _update_candidate_training_table_cells(self, table: QTableWidget, candidates: tuple[MovingObjectCandidate, ...]) -> None:
        for row_index in range(table.rowCount()):
            item = table.item(row_index, 0)
            candidate_index = item.data(Qt.ItemDataRole.UserRole) if item is not None else row_index
            if not isinstance(candidate_index, int) or candidate_index < 0 or candidate_index >= len(candidates):
                continue
            candidate = candidates[candidate_index]
            label_item = QTableWidgetItem(self._candidate_label_cell(candidate))
            score_item = QTableWidgetItem(self._candidate_prediction_cell(candidate))
            label_item.setData(Qt.ItemDataRole.UserRole, candidate_index)
            score_item.setData(Qt.ItemDataRole.UserRole, candidate_index)
            table.setItem(row_index, 6, label_item)
            table.setItem(row_index, 7, score_item)

    def _quick_label_candidates(self) -> tuple[MovingObjectCandidate, ...]:
        current = self._tabs.currentWidget()
        if current is self._review_table:
            return self._result.review_candidates
        if current is self._unmatched_table:
            return self._result.candidates
        return ()

    def _select_candidate_for_quick_label(self, candidate: MovingObjectCandidate) -> None:
        table = self._unmatched_table
        candidates = self._result.candidates
        if candidate in self._result.review_candidates:
            table = self._review_table
            candidates = self._result.review_candidates
            self._tabs.setCurrentWidget(self._review_table)
        else:
            self._tabs.setCurrentWidget(self._unmatched_table)
        for row_index in range(table.rowCount()):
            item = table.item(row_index, 0)
            candidate_index = item.data(Qt.ItemDataRole.UserRole) if item is not None else row_index
            if isinstance(candidate_index, int) and 0 <= candidate_index < len(candidates) and candidates[candidate_index].candidate_id == candidate.candidate_id:
                table.setCurrentCell(row_index, 0)
                table.selectRow(row_index)
                self._handle_selection_changed()
                return

    def _save_quick_label_candidate(self, candidate: MovingObjectCandidate, label: str) -> None:
        if self._save_candidate_label_callback is None:
            return
        self._save_candidate_label_callback(candidate, label)
        self._refresh_candidate_training_tables()
        self._select_candidate_for_quick_label(candidate)
        self._sync_candidate_training_controls()

    def _open_quick_label_dialog(self) -> None:
        candidates = self._quick_label_candidates()
        if not candidates or self._save_candidate_label_callback is None:
            return
        if self._quick_label_dialog is not None and self._quick_label_dialog.isVisible():
            self._quick_label_dialog.raise_()
            self._quick_label_dialog.activateWindow()
            return
        selected_candidate = self._selected_label_candidate()
        dialog = MovingObjectQuickLabelDialog(
            candidates=candidates,
            label_options=self._candidate_label_options,
            label_lookup=self._candidate_label_value,
            save_label=self._save_quick_label_candidate,
            select_candidate=self._select_candidate_for_quick_label,
            start_candidate_id=None if selected_candidate is None else selected_candidate.candidate_id,
            parent=self,
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.destroyed.connect(lambda _obj=None: setattr(self, "_quick_label_dialog", None))
        self._quick_label_dialog = dialog
        dialog.open()

    def set_loading_message(self, message: str | None) -> None:
        self._loading_message = "" if message is None else str(message).strip()
        self._refresh_summary_panel()
        self._apply_loading_message()

    def set_continue_sweep_enabled(self, enabled: bool) -> None:
        allow_continue = bool(enabled) and self._continue_sweep_callback is not None
        self._continue_sweep_button.setEnabled(allow_continue)
        if allow_continue:
            self._apply_continue_sweep_button_style()
        else:
            self._continue_sweep_button.setStyleSheet("")

    def _apply_continue_sweep_button_style(self) -> None:
        accent = self.palette().color(QPalette.ColorRole.Highlight)
        text_color = "#ffffff" if accent.lightness() < 128 else "#1f1f1f"
        hover_color = accent.lighter(110).name().lower()
        pressed_color = accent.darker(110).name().lower()
        border_color = accent.darker(122).name().lower()
        self._continue_sweep_button.setStyleSheet(
            "QPushButton {"
            f"background-color: {accent.name().lower()};"
            f"color: {text_color};"
            f"border: 1px solid {border_color};"
            "padding: 4px 10px;"
            "font-weight: 600;"
            "}"
            "QPushButton:hover {"
            f"background-color: {hover_color};"
            "}"
            "QPushButton:pressed {"
            f"background-color: {pressed_color};"
            "}"
        )

    def _apply_trajectory_button_style(self) -> None:
        accent = self.palette().color(QPalette.ColorRole.Highlight)
        self._trajectory_button.setStyleSheet(
            "QPushButton {"
            "background-color: transparent;"
            f"border: 1px solid {accent.name().lower()};"
            "padding: 4px 10px;"
            "font-weight: 600;"
            "}"
            "QPushButton:hover {"
            f"background-color: {accent.lighter(185).name().lower()};"
            "}"
        )

    def _continue_sweep_requested(self) -> None:
        if self._continue_sweep_callback is None:
            return
        self._continue_sweep_callback()

    def _apply_loading_message(self) -> None:
        self._progress_note_label.setText(self._loading_message)
        self._progress_note_label.setVisible(bool(self._loading_message))
        if self._loading_message and not (self._result.all_candidates() or self._result.recovered_known_objects or self._result.missed_known_objects):
            self._trajectory_button.setEnabled(False)
            self._mark_selected_button.setEnabled(False)
            self._synthetic_track_button.setEnabled(False)
            self._details_output.setPlainText(self._loading_message)

    def _refresh_summary_panel(self) -> None:
        self._summary_metric_value_labels["visible_limit"].setText(self._visible_limit_summary_value())
        benchmark_pending = (
            bool(self._loading_message)
            and self._result.benchmark_known_count == 0
            and not self._result.recovered_known_objects
            and not self._result.missed_known_objects
        )
        self._summary_metric_value_labels["known_in_limit"].setText(
            "Pending" if benchmark_pending else self._count_summary_text(self._result.benchmark_known_count, "object")
        )
        self._summary_metric_value_labels["recovered_in_limit"].setText(
            "Pending"
            if benchmark_pending
            else f"{self._result.benchmark_recovered_count} of {self._result.benchmark_known_count}"
        )
        self._summary_metric_value_labels["potential_discoveries"].setText(
            self._count_summary_text(len(self._result.candidates), "candidate")
        )
        self._summary_metric_value_labels["borderline_review"].setText(
            self._count_summary_text(len(self._result.review_candidates), "tracklet")
        )
        self._summary_overview_label.setText(
            "Known-object benchmark rows stay pending until the current residual pass finishes. Partial residual candidates shown during the run are still useful for live review."
            if benchmark_pending
            else self._discovery_overview_text()
        )
        self._summary_method_label.setText(self._discovery_method_text())
        self._summary_method_label.setVisible(bool(self._summary_method_label.text()))

    @staticmethod
    def _reset_table(table: QTableWidget, row_count: int) -> None:
        table.clearContents()
        table.setRowCount(row_count)

    def _build_summary_metric_card(self, metric_key: str, title: str, value: str, parent: QWidget) -> QWidget:
        card = QFrame(parent)
        card.setFrameShape(QFrame.Shape.StyledPanel)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 8, 10, 8)
        card_layout.setSpacing(4)

        title_label = QLabel(title, card)
        title_font = QFont(title_label.font())
        title_font.setBold(True)
        title_label.setFont(title_font)
        card_layout.addWidget(title_label)

        value_label = QLabel(value, card)
        value_label.setWordWrap(True)
        value_font = QFont(value_label.font())
        value_font.setBold(True)
        value_font.setPointSize(value_font.pointSize() + 2)
        value_label.setFont(value_font)
        card_layout.addWidget(value_label)
        card_layout.addStretch(1)

        self._summary_metric_value_labels[metric_key] = value_label
        return card

    def _visible_limit_summary_value(self) -> str:
        estimate_result = self._result.estimate_result
        if estimate_result is None:
            return "Not estimated"
        return f"Gaia G {estimate_result.dimmest_visible_magnitude:.1f}"

    @staticmethod
    def _count_summary_text(count: int, singular: str) -> str:
        noun = singular if count == 1 else f"{singular}s"
        return f"{count} {noun}"

    def _discovery_overview_text(self) -> str:
        return (
            "Tabs below separate recovered known objects, known objects that were not recovered, "
            "stronger potential discoveries, and borderline better-detection possibilities from the current group."
        )

    def _discovery_method_text(self) -> str:
        methods_summary = self._result.methods_summary_text.strip()
        if not methods_summary:
            return ""
        return f"Method: {methods_summary}"

    def _configure_table(self, table: QTableWidget, widths: tuple[tuple[int, int], ...]) -> None:
        header = table.horizontalHeader()
        for column_index, width in widths:
            header.setSectionResizeMode(column_index, QHeaderView.ResizeMode.Interactive)
            table.setColumnWidth(column_index, width)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setSortingEnabled(False)

    def _populate_recovered_table(self) -> None:
        for row_index, recovered in enumerate(self._result.recovered_known_objects):
            candidate = recovered.candidate
            detection = recovered.detection
            items = [
                QTableWidgetItem(detection.name or detection.designation or "Unknown"),
                QTableWidgetItem(candidate_discovery_method_label(candidate)),
                QTableWidgetItem("-" if detection.predicted_magnitude is None else f"{detection.predicted_magnitude:.1f}"),
                QTableWidgetItem(self._estimated_limit_status_text(recovered.within_estimated_limit)),
                QTableWidgetItem(str(recovered.expected_frame_count)),
                QTableWidgetItem(str(recovered.matched_frame_count)),
                QTableWidgetItem(
                    f"{candidate.motion_px_per_hour:.2f} px/h"
                    + ("" if candidate.motion_arcsec_per_hour is None else f" | {candidate.motion_arcsec_per_hour:.2f} arcsec/h")
                ),
                QTableWidgetItem(f"{candidate.average_snr:.2f}"),
            ]
            for item in items:
                item.setData(Qt.ItemDataRole.UserRole, row_index)
            for column_index, item in enumerate(items):
                self._recovered_table.setItem(row_index, column_index, item)

    def _populate_missed_table(self) -> None:
        for row_index, missed in enumerate(self._result.missed_known_objects):
            detection = missed.detection
            items = [
                QTableWidgetItem(detection.name or detection.designation or "Unknown"),
                QTableWidgetItem("-" if detection.predicted_magnitude is None else f"{detection.predicted_magnitude:.1f}"),
                QTableWidgetItem(self._estimated_limit_status_text(missed.within_estimated_limit)),
                QTableWidgetItem(detection.status),
                QTableWidgetItem(f"{detection.confidence_score:.2f}"),
                QTableWidgetItem(
                    "-" if detection.motion_rate_arcsec_per_hour is None else f"{detection.motion_rate_arcsec_per_hour:.2f} arcsec/h"
                ),
            ]
            for item in items:
                item.setData(Qt.ItemDataRole.UserRole, row_index)
            for column_index, item in enumerate(items):
                self._missed_table.setItem(row_index, column_index, item)

    def _populate_unmatched_table(self) -> None:
        for row_index, candidate in enumerate(self._result.candidates):
            items = [
                QTableWidgetItem(candidate.candidate_id),
                QTableWidgetItem(candidate_discovery_method_label(candidate)),
                QTableWidgetItem(str(len(candidate.frame_detections))),
                QTableWidgetItem(
                    f"{candidate.motion_px_per_hour:.2f} px/h"
                    + ("" if candidate.motion_arcsec_per_hour is None else f" | {candidate.motion_arcsec_per_hour:.2f} arcsec/h")
                ),
                QTableWidgetItem(f"{candidate.average_snr:.2f}"),
                QTableWidgetItem(f"{candidate.fit_rms_px:.2f} px"),
                QTableWidgetItem(self._candidate_label_cell(candidate)),
                QTableWidgetItem(self._candidate_prediction_cell(candidate)),
            ]
            for item in items:
                item.setData(Qt.ItemDataRole.UserRole, row_index)
            for column_index, item in enumerate(items):
                self._unmatched_table.setItem(row_index, column_index, item)

    def _populate_review_table(self) -> None:
        for row_index, candidate in enumerate(self._result.review_candidates):
            items = [
                QTableWidgetItem(candidate.candidate_id),
                QTableWidgetItem(candidate_discovery_method_label(candidate)),
                QTableWidgetItem(str(len(candidate.frame_detections))),
                QTableWidgetItem(
                    f"{candidate.motion_px_per_hour:.2f} px/h"
                    + ("" if candidate.motion_arcsec_per_hour is None else f" | {candidate.motion_arcsec_per_hour:.2f} arcsec/h")
                ),
                QTableWidgetItem(f"{candidate.average_snr:.2f}"),
                QTableWidgetItem(f"{candidate.fit_rms_px:.2f} px"),
                QTableWidgetItem(self._candidate_label_cell(candidate)),
                QTableWidgetItem(self._candidate_prediction_cell(candidate)),
            ]
            for item in items:
                item.setData(Qt.ItemDataRole.UserRole, row_index)
            for column_index, item in enumerate(items):
                self._review_table.setItem(row_index, column_index, item)

    def _select_first_available_row(self) -> None:
        if self._result.recovered_known_objects:
            self._tabs.setCurrentWidget(self._recovered_table)
            self._recovered_table.selectRow(0)
            self._handle_selection_changed()
            return
        if self._result.missed_known_objects:
            self._tabs.setCurrentWidget(self._missed_table)
            self._missed_table.selectRow(0)
            self._handle_selection_changed()
            return
        if self._result.candidates:
            self._tabs.setCurrentWidget(self._unmatched_table)
            self._unmatched_table.selectRow(0)
            self._handle_selection_changed()
            return
        if self._result.review_candidates:
            self._tabs.setCurrentWidget(self._review_table)
            self._review_table.selectRow(0)
            self._handle_selection_changed()
            return
        self._trajectory_button.setEnabled(False)
        self._image_view.set_content(
            self._display,
            overlays=[],
            grid_overlays=[],
            editor_enabled=False,
            reset_view=True,
            render_settings=self._render_settings,
        )
        self._details_output.setPlainText("No known-object recoveries, potential discoveries, or better detection possibilities were found for the current group.")

    def _handle_tab_changed(self, _index: int) -> None:
        current = self._tabs.currentWidget()
        if current is self._recovered_table and self._recovered_table.rowCount() > 0 and not self._recovered_table.selectionModel().selectedRows():
            self._recovered_table.selectRow(0)
        elif current is self._missed_table and self._missed_table.rowCount() > 0 and not self._missed_table.selectionModel().selectedRows():
            self._missed_table.selectRow(0)
        elif current is self._unmatched_table and self._unmatched_table.rowCount() > 0 and not self._unmatched_table.selectionModel().selectedRows():
            self._unmatched_table.selectRow(0)
        elif current is self._review_table and self._review_table.rowCount() > 0 and not self._review_table.selectionModel().selectedRows():
            self._review_table.selectRow(0)
        self._handle_selection_changed()

    def _selected_review_candidate(self) -> MovingObjectCandidate | None:
        candidate = self._selected_item(self._review_table, self._result.review_candidates)
        return candidate if isinstance(candidate, MovingObjectCandidate) else None

    def _selected_item(self, table: QTableWidget, values: tuple[object, ...]) -> object | None:
        selected_rows = table.selectionModel().selectedRows() if table.selectionModel() is not None else []
        if not selected_rows:
            return None
        row = int(selected_rows[0].row())
        if row < 0 or row >= len(values):
            return None
        item = table.item(row, 0)
        if item is not None:
            value_index = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(value_index, int) and 0 <= value_index < len(values):
                return values[value_index]
        return values[row]

    def _selected_candidate(self) -> MovingObjectCandidate | None:
        candidate = self._selected_item(self._unmatched_table, self._result.candidates)
        return candidate if isinstance(candidate, MovingObjectCandidate) else None

    def _selected_candidates(self) -> tuple[MovingObjectCandidate, ...]:
        selection_model = self._unmatched_table.selectionModel()
        if selection_model is None:
            return ()
        selected_rows = sorted(index.row() for index in selection_model.selectedRows())
        candidates: list[MovingObjectCandidate] = []
        for row in selected_rows:
            if row < 0 or row >= len(self._result.candidates):
                continue
            item = self._unmatched_table.item(row, 0)
            if item is not None:
                value_index = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(value_index, int) and 0 <= value_index < len(self._result.candidates):
                    candidates.append(self._result.candidates[value_index])
                    continue
            candidates.append(self._result.candidates[row])
        return tuple(candidates)

    @staticmethod
    def _overlay_outline_color(color_value: str) -> str:
        color = QColor(color_value)
        if not color.isValid():
            return "#000000"
        return "#000000" if color.lightness() >= 128 else "#ffffff"

    def _update_mark_buttons_state(self) -> None:
        selected_candidates = self._selected_candidates() if self._tabs.currentWidget() is self._unmatched_table else ()
        selected_candidate = selected_candidates[0] if selected_candidates else None
        self._mark_selected_button.setEnabled(
            (self._mark_candidate_callback is not None or self._mark_all_candidates_callback is not None)
            and bool(selected_candidates)
        )
        self._synthetic_track_button.setEnabled(
            self._synthetic_track_candidate_callback is not None and selected_candidate is not None
        )

    def _mark_selected_candidate_on_main_image(self) -> None:
        candidates = self._selected_candidates()
        if not candidates:
            return
        if len(candidates) == 1 and self._mark_candidate_callback is not None:
            self._mark_candidate_callback(candidates[0])
            return
        if self._mark_all_candidates_callback is not None:
            self._mark_all_candidates_callback(candidates)
            return
        if self._mark_candidate_callback is None:
            return
        for candidate in candidates:
            self._mark_candidate_callback(candidate)

    def _synthetic_track_selected_candidate(self) -> None:
        candidate = self._selected_candidate()
        if candidate is None or self._synthetic_track_candidate_callback is None:
            return
        self._synthetic_track_candidate_callback(candidate)

    def _handle_candidate_selection_changed(self) -> None:
        self._handle_selection_changed()

    def _handle_selection_changed(self) -> None:
        self._update_trajectory_button_state()
        self._update_mark_buttons_state()
        self._sync_candidate_training_controls()
        current = self._tabs.currentWidget()
        if current is self._recovered_table:
            recovered = self._selected_item(self._recovered_table, self._result.recovered_known_objects)
            if isinstance(recovered, RecoveredKnownMovingObject):
                self._show_recovered_known_object(recovered)
            return
        if current is self._missed_table:
            missed = self._selected_item(self._missed_table, self._result.missed_known_objects)
            if isinstance(missed, MissedKnownMovingObject):
                self._show_missed_known_object(missed)
            return
        if current is self._review_table:
            candidate = self._selected_review_candidate()
            if candidate is not None:
                self._show_unmatched_candidate(candidate, category_label="Borderline review")
            return
        candidate = self._selected_candidate()
        if candidate is not None:
            self._show_unmatched_candidate(candidate, category_label="Potential discovery")

    def _show_recovered_known_object(self, recovered: RecoveredKnownMovingObject) -> None:
        overlays = [
            ImageOverlay(
                source_id=f"{recovered.candidate.candidate_id}:{detection.frame_index}",
                name=f"F{detection.frame_index + 1}",
                x=detection.x,
                y=detection.y,
                aperture_radius=3.0,
                annulus_inner_radius=3.0,
                annulus_outer_radius=3.0,
                color="#10b981",
                show_annulus=False,
                show_label=True,
                marker_style="circle",
                show_center_dot=False,
                outline_color=self._overlay_outline_color("#10b981"),
                outline_width=2.0,
            )
            for detection in recovered.candidate.frame_detections
        ]
        overlays.append(
            ImageOverlay(
                source_id=f"known:{recovered.detection.name or recovered.detection.designation or 'known'}",
                name=recovered.detection.name or recovered.detection.designation or "Known",
                x=recovered.reference_x,
                y=recovered.reference_y,
                aperture_radius=5.0,
                annulus_inner_radius=5.0,
                annulus_outer_radius=5.0,
                color="#38bdf8",
                show_annulus=False,
                show_label=True,
                marker_style="circle",
                show_center_dot=False,
                outline_color=self._overlay_outline_color("#38bdf8"),
                outline_width=2.0,
            )
        )
        motion_vector = MotionVectorOverlay(
            x=recovered.candidate.start_x,
            y=recovered.candidate.start_y,
            dx=recovered.candidate.end_x - recovered.candidate.start_x,
            dy=recovered.candidate.end_y - recovered.candidate.start_y,
            color="#38bdf8",
            width=2.0,
            show_anchor=True,
        )
        self._image_view.set_content(
            self._display,
            overlays=overlays,
            grid_overlays=[],
            editor_enabled=False,
            reset_view=True,
            render_settings=self._render_settings,
            motion_vector_overlays=[motion_vector],
        )
        self._image_view.focus_on(recovered.reference_x, recovered.reference_y, minimum_zoom_scale=3.0)
        self._details_output.setPlainText(self._recovered_known_details_text(recovered))

    def _show_missed_known_object(self, missed: MissedKnownMovingObject) -> None:
        overlays = [
            ImageOverlay(
                source_id=f"missed:{missed.detection.name or missed.detection.designation or 'missed'}",
                name=missed.detection.name or missed.detection.designation or "Missed",
                x=missed.reference_x,
                y=missed.reference_y,
                aperture_radius=5.0,
                annulus_inner_radius=5.0,
                annulus_outer_radius=5.0,
                color="#ef4444",
                show_annulus=False,
                show_label=True,
                marker_style="circle",
                show_center_dot=False,
                outline_color=self._overlay_outline_color("#ef4444"),
                outline_width=2.0,
            )
        ]
        self._image_view.set_content(
            self._display,
            overlays=overlays,
            grid_overlays=[],
            editor_enabled=False,
            reset_view=True,
            render_settings=self._render_settings,
        )
        self._image_view.focus_on(missed.reference_x, missed.reference_y, minimum_zoom_scale=3.0)
        self._details_output.setPlainText(self._missed_known_details_text(missed))

    def _show_unmatched_candidate(self, candidate: MovingObjectCandidate, *, category_label: str) -> None:
        overlays = [
            ImageOverlay(
                source_id=f"{candidate.candidate_id}:{detection.frame_index}",
                name=f"F{detection.frame_index + 1}",
                x=detection.x,
                y=detection.y,
                aperture_radius=4.5,
                annulus_inner_radius=4.5,
                annulus_outer_radius=4.5,
                color="#ef4444",
                show_annulus=False,
                show_label=True,
                marker_style="target",
                show_center_dot=True,
                outline_color=self._overlay_outline_color("#ef4444"),
                outline_width=3.0,
            )
            for detection in candidate.frame_detections
        ]
        motion_vector = MotionVectorOverlay(
            x=candidate.start_x,
            y=candidate.start_y,
            dx=candidate.end_x - candidate.start_x,
            dy=candidate.end_y - candidate.start_y,
            color="#38bdf8",
            width=2.0,
            show_anchor=True,
        )
        self._image_view.set_content(
            self._display,
            overlays=overlays,
            grid_overlays=[],
            editor_enabled=False,
            reset_view=True,
            render_settings=self._render_settings,
            motion_vector_overlays=[motion_vector],
        )
        midpoint_x = (candidate.start_x + candidate.end_x) / 2.0
        midpoint_y = (candidate.start_y + candidate.end_y) / 2.0
        self._image_view.focus_on(midpoint_x, midpoint_y, minimum_zoom_scale=3.0)
        self._details_output.setPlainText(self._candidate_details_text(candidate, category_label=category_label))

    def _open_selected_trajectory(self) -> None:
        selection = self._selected_trajectory_candidate()
        if selection is None:
            return
        object_label, candidate = selection
        self._show_trajectory_dialog(object_label, candidate)

    def _selected_trajectory_candidate(self) -> tuple[str, MovingObjectCandidate] | None:
        current = self._tabs.currentWidget()
        if current is self._recovered_table:
            recovered = self._selected_item(self._recovered_table, self._result.recovered_known_objects)
            if isinstance(recovered, RecoveredKnownMovingObject):
                label = recovered.detection.name or recovered.detection.designation or recovered.candidate.candidate_id
                return label, recovered.candidate
            return None
        if current is self._review_table:
            candidate = self._selected_review_candidate()
            if candidate is not None:
                return f"Candidate {candidate.candidate_id}", candidate
            return None
        candidate = self._selected_candidate()
        if candidate is not None:
            return f"Candidate {candidate.candidate_id}", candidate
        return None

    def _show_trajectory_dialog(self, object_label: str, candidate: MovingObjectCandidate) -> None:
        dialog = MovingObjectTrajectoryDialog(object_label=object_label, candidate=candidate, parent=self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.destroyed.connect(lambda _obj=None, dialog_ref=dialog: self._forget_trajectory_dialog(dialog_ref))
        self._trajectory_dialogs.append(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _forget_trajectory_dialog(self, dialog: MovingObjectTrajectoryDialog) -> None:
        if dialog in self._trajectory_dialogs:
            self._trajectory_dialogs.remove(dialog)

    def _update_trajectory_button_state(self) -> None:
        self._trajectory_button.setEnabled(self._selected_trajectory_candidate() is not None)

    def _estimated_limit_status_text(self, within_limit: bool) -> str:
        if self._result.estimate_result is None:
            return "-"
        return "Yes" if within_limit else "No"

    def _recovered_known_details_text(self, recovered: RecoveredKnownMovingObject) -> str:
        detection = recovered.detection
        candidate = recovered.candidate
        lines = [
            f"Known object: {detection.name or detection.designation or 'Unknown'}",
            f"Designation: {detection.designation or '-'}",
            f"Found by: {candidate_discovery_method_label(candidate)}",
            f"Status: {detection.status}",
            f"Confidence: {detection.confidence_score:.2f}",
            f"Predicted magnitude: {'-' if detection.predicted_magnitude is None else f'{detection.predicted_magnitude:.1f}'}",
        ]
        if self._result.estimate_result is not None:
            lines.append(f"Estimated visible limit: {self._result.estimate_result.dimmest_visible_magnitude:.1f} mag")
            lines.append(f"Within estimated limit: {'Yes' if recovered.within_estimated_limit else 'No'}")
        lines.extend(
            [
                f"Recovered frames: {recovered.matched_frame_count}/{recovered.expected_frame_count}",
                f"Match RMS: {recovered.match_rms_px:.2f} px",
                f"Max match offset: {recovered.max_match_offset_px:.2f} px",
                f"Linearity deflection RMS: {candidate.fit_rms_px:.2f} px",
                f"Max deflection: {candidate.max_deflection_px:.2f} px",
                f"Motion: {candidate.motion_px_per_hour:.2f} px/hour"
                + ("" if candidate.motion_arcsec_per_hour is None else f" | {candidate.motion_arcsec_per_hour:.2f} arcsec/hour"),
                f"Average residual score: {candidate.average_snr:.2f}",
                "",
                "Frame detections:",
            ]
        )
        for detection_row in candidate.frame_detections:
            position_text = f"x={detection_row.x:.2f}, y={detection_row.y:.2f}"
            if detection_row.ra_deg is not None and detection_row.dec_deg is not None:
                position_text += f" | RA={detection_row.ra_deg:.6f} deg, Dec={detection_row.dec_deg:.6f} deg"
            lines.append(f"F{detection_row.frame_index + 1} | {detection_row.source_path.name} | {detection_row.observation_time.isoformat()} | {position_text} | SNR={detection_row.local_snr:.2f}")
        return "\n".join(lines)

    def _missed_known_details_text(self, missed: MissedKnownMovingObject) -> str:
        detection = missed.detection
        lines = [
            f"Known object: {detection.name or detection.designation or 'Unknown'}",
            f"Designation: {detection.designation or '-'}",
            f"Status: {detection.status}",
            f"Confidence: {detection.confidence_score:.2f}",
            f"Predicted magnitude: {'-' if detection.predicted_magnitude is None else f'{detection.predicted_magnitude:.1f}'}",
        ]
        if self._result.estimate_result is not None:
            lines.append(f"Estimated visible limit: {self._result.estimate_result.dimmest_visible_magnitude:.1f} mag")
            lines.append(f"Within estimated limit: {'Yes' if missed.within_estimated_limit else 'No'}")
        lines.extend(
            [
                f"Expected frames: {missed.expected_frame_count}",
                f"Motion: {'-' if detection.motion_rate_arcsec_per_hour is None else f'{detection.motion_rate_arcsec_per_hour:.2f} arcsec/hour'}",
                f"Reference position: x={missed.reference_x:.2f}, y={missed.reference_y:.2f}",
                "",
                missed.summary_text,
            ]
        )
        return "\n".join(lines)

    def _candidate_details_text(self, candidate: MovingObjectCandidate, *, category_label: str) -> str:
        lines = [
            f"Candidate: {candidate.candidate_id}",
            f"Category: {category_label}",
            f"Found by: {candidate_discovery_method_label(candidate)}",
            f"Frames seen: {len(candidate.frame_detections)}",
            f"Motion: {candidate.motion_px_per_hour:.2f} px/hour"
            + ("" if candidate.motion_arcsec_per_hour is None else f" | {candidate.motion_arcsec_per_hour:.2f} arcsec/hour"),
            f"Track displacement: {candidate.displacement_px:.2f} px",
            f"Average residual score: {candidate.average_snr:.2f}",
            f"Peak residual: {candidate.peak_value:.1f}",
            f"Linearity deflection RMS: {candidate.fit_rms_px:.2f} px",
            f"Max deflection: {candidate.max_deflection_px:.2f} px",
        ]
        label = self._candidate_label_value(candidate)
        if label:
            lines.append(f"Training label: {self._format_candidate_training_label(label)}")
        if self._candidate_prediction_lookup is not None:
            prediction = self._candidate_prediction_lookup(candidate)
            if prediction is not None:
                prediction_label, confidence = prediction
                lines.append(f"ML score: {self._format_candidate_training_label(prediction_label)} ({float(confidence):.0%})")
        lines.extend(["", "Frame detections:"])
        for detection in candidate.frame_detections:
            position_text = f"x={detection.x:.2f}, y={detection.y:.2f}"
            if detection.ra_deg is not None and detection.dec_deg is not None:
                position_text += f" | RA={detection.ra_deg:.6f} deg, Dec={detection.dec_deg:.6f} deg"
            lines.append(f"F{detection.frame_index + 1} | {detection.source_path.name} | {detection.observation_time.isoformat()} | {position_text} | SNR={detection.local_snr:.2f}")
        return "\n".join(lines)

    def _export_benchmark_table(self) -> None:
        suggested_path = self._result.reference_path.with_name(f"{self._result.reference_path.stem}_discover_benchmark.csv")
        output_path = self._choose_csv_export_path("Export Discover Benchmark Table", suggested_path)
        if output_path is None:
            return
        rows = self._benchmark_export_rows()
        fieldnames = [
            "benchmark_status",
            "object_name",
            "designation",
            "object_type",
            "orbit_class",
            "predicted_magnitude",
            "confidence_score",
            "catalog_status",
            "likely_visible",
            "within_estimated_limit",
            "estimated_limit_magnitude",
            "expected_frame_count",
            "matched_frame_count",
            "recovered_fraction",
            "match_rms_px",
            "max_match_offset_px",
            "reference_x",
            "reference_y",
            "predicted_ra_deg",
            "predicted_dec_deg",
            "predicted_motion_arcsec_per_hour",
            "expected_trail_length_px",
            "candidate_id",
            "candidate_average_snr",
            "candidate_fit_rms_px",
            "candidate_motion_px_per_hour",
            "candidate_motion_arcsec_per_hour",
            "candidate_displacement_px",
            "candidate_discovery_method",
            "summary_text",
        ]
        self._write_csv_export(
            output_path=output_path,
            fieldnames=fieldnames,
            rows=rows,
            success_title="Discover Benchmark Exported",
            failure_title="Export Benchmark failed",
            details_prefix="Exported discover benchmark table",
        )

    def _export_unmatched_candidates_table(self) -> None:
        suggested_path = self._result.reference_path.with_name(f"{self._result.reference_path.stem}_discover_candidates.csv")
        output_path = self._choose_csv_export_path("Export Discover Candidate Review", suggested_path)
        if output_path is None:
            return
        rows = self._discovery_candidate_export_rows()
        fieldnames = [
            "discovery_bucket",
            "candidate_id",
            "observed_frame_count",
            "average_snr",
            "peak_value",
            "fit_rms_px",
            "max_deflection_px",
            "motion_px_per_hour",
            "motion_arcsec_per_hour",
            "displacement_px",
            "discovery_method",
            "start_x",
            "start_y",
            "end_x",
            "end_y",
            "first_detection_time_utc",
            "last_detection_time_utc",
            "frame_paths",
            "summary_text",
        ]
        self._write_csv_export(
            output_path=output_path,
            fieldnames=fieldnames,
            rows=rows,
            success_title="Discover Candidates Exported",
            failure_title="Export Unmatched failed",
            details_prefix="Exported discover candidate review table",
        )

    def _export_summary_table(self) -> None:
        suggested_path = self._result.reference_path.with_name(f"{self._result.reference_path.stem}_discover_summary.csv")
        output_path = self._choose_csv_export_path("Export Discover Summary Table", suggested_path)
        if output_path is None:
            return
        rows = self._summary_export_rows()
        fieldnames = [
            "benchmark_status",
            "object_name",
            "designation",
            "predicted_magnitude",
            "confidence_score",
            "catalog_status",
            "within_estimated_limit",
            "estimated_limit_magnitude",
            "matched_frame_count",
            "expected_frame_count",
            "recovered_fraction",
            "match_rms_px",
            "predicted_motion_arcsec_per_hour",
            "candidate_discovery_method",
            "summary_text",
        ]
        self._write_csv_export(
            output_path=output_path,
            fieldnames=fieldnames,
            rows=rows,
            success_title="Discover Summary Exported",
            failure_title="Export Summary failed",
            details_prefix="Exported discover summary table",
        )

    def _choose_csv_export_path(self, title: str, suggested_path: Path) -> Path | None:
        selected, _selected_filter = QFileDialog.getSaveFileName(
            self,
            title,
            str(suggested_path),
            "CSV Files (*.csv)",
        )
        if not selected:
            return None
        output_path = Path(selected).expanduser()
        if output_path.suffix.lower() != ".csv":
            output_path = output_path.with_suffix(".csv")
        return output_path

    def _write_csv_export(
        self,
        *,
        output_path: Path,
        fieldnames: list[str],
        rows: list[dict[str, object]],
        success_title: str,
        failure_title: str,
        details_prefix: str,
    ) -> None:
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        except OSError as exc:
            QMessageBox.warning(self, failure_title, str(exc))
            return
        existing_text = self._details_output.toPlainText()
        prefix_text = f"{details_prefix} to {output_path}"
        self._details_output.setPlainText(prefix_text if not existing_text else f"{prefix_text}\n\n{existing_text}")
        QMessageBox.information(self, success_title, f"Saved {len(rows)} row(s) to\n{output_path}")

    def _benchmark_export_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for recovered in self._result.recovered_known_objects:
            rows.append(self._recovered_benchmark_export_row(recovered))
        for missed in self._result.missed_known_objects:
            rows.append(self._missed_benchmark_export_row(missed))
        return rows

    def _recovered_benchmark_export_row(self, recovered: RecoveredKnownMovingObject) -> dict[str, object]:
        detection = recovered.detection
        candidate = recovered.candidate
        recovered_fraction = (
            float(recovered.matched_frame_count) / float(recovered.expected_frame_count)
            if recovered.expected_frame_count > 0
            else None
        )
        return {
            "benchmark_status": "recovered",
            "object_name": detection.name or detection.designation or "Unknown",
            "designation": detection.designation or "",
            "object_type": detection.object_type,
            "orbit_class": detection.orbit_class,
            "predicted_magnitude": detection.predicted_magnitude,
            "confidence_score": detection.confidence_score,
            "catalog_status": detection.status,
            "likely_visible": detection.likely_visible,
            "within_estimated_limit": recovered.within_estimated_limit,
            "estimated_limit_magnitude": None if self._result.estimate_result is None else self._result.estimate_result.dimmest_visible_magnitude,
            "expected_frame_count": recovered.expected_frame_count,
            "matched_frame_count": recovered.matched_frame_count,
            "recovered_fraction": recovered_fraction,
            "match_rms_px": recovered.match_rms_px,
            "max_match_offset_px": recovered.max_match_offset_px,
            "reference_x": recovered.reference_x,
            "reference_y": recovered.reference_y,
            "predicted_ra_deg": detection.predicted_ra_deg,
            "predicted_dec_deg": detection.predicted_dec_deg,
            "predicted_motion_arcsec_per_hour": detection.motion_rate_arcsec_per_hour,
            "expected_trail_length_px": detection.expected_trail_length_px,
            "candidate_id": candidate.candidate_id,
            "candidate_average_snr": candidate.average_snr,
            "candidate_fit_rms_px": candidate.fit_rms_px,
            "candidate_motion_px_per_hour": candidate.motion_px_per_hour,
            "candidate_motion_arcsec_per_hour": candidate.motion_arcsec_per_hour,
            "candidate_displacement_px": candidate.displacement_px,
            "candidate_discovery_method": candidate_discovery_method_label(candidate),
            "summary_text": recovered.summary_text,
        }

    def _missed_benchmark_export_row(self, missed: MissedKnownMovingObject) -> dict[str, object]:
        detection = missed.detection
        return {
            "benchmark_status": "missed",
            "object_name": detection.name or detection.designation or "Unknown",
            "designation": detection.designation or "",
            "object_type": detection.object_type,
            "orbit_class": detection.orbit_class,
            "predicted_magnitude": detection.predicted_magnitude,
            "confidence_score": detection.confidence_score,
            "catalog_status": detection.status,
            "likely_visible": detection.likely_visible,
            "within_estimated_limit": missed.within_estimated_limit,
            "estimated_limit_magnitude": None if self._result.estimate_result is None else self._result.estimate_result.dimmest_visible_magnitude,
            "expected_frame_count": missed.expected_frame_count,
            "matched_frame_count": None,
            "recovered_fraction": 0.0,
            "match_rms_px": None,
            "max_match_offset_px": None,
            "reference_x": missed.reference_x,
            "reference_y": missed.reference_y,
            "predicted_ra_deg": detection.predicted_ra_deg,
            "predicted_dec_deg": detection.predicted_dec_deg,
            "predicted_motion_arcsec_per_hour": detection.motion_rate_arcsec_per_hour,
            "expected_trail_length_px": detection.expected_trail_length_px,
            "candidate_id": "",
            "candidate_average_snr": None,
            "candidate_fit_rms_px": None,
            "candidate_motion_px_per_hour": None,
            "candidate_motion_arcsec_per_hour": None,
            "candidate_displacement_px": None,
            "candidate_discovery_method": "",
            "summary_text": missed.summary_text,
        }

    def _discovery_candidate_export_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for discovery_bucket, candidates in (
            ("potential_discovery", self._result.candidates),
            ("borderline_review", self._result.review_candidates),
        ):
            for candidate in candidates:
                first_detection = candidate.frame_detections[0] if candidate.frame_detections else None
                last_detection = candidate.frame_detections[-1] if candidate.frame_detections else None
                rows.append(
                    {
                        "discovery_bucket": discovery_bucket,
                        "candidate_id": candidate.candidate_id,
                        "observed_frame_count": len(candidate.frame_detections),
                        "average_snr": candidate.average_snr,
                        "peak_value": candidate.peak_value,
                        "fit_rms_px": candidate.fit_rms_px,
                        "max_deflection_px": candidate.max_deflection_px,
                        "motion_px_per_hour": candidate.motion_px_per_hour,
                        "motion_arcsec_per_hour": candidate.motion_arcsec_per_hour,
                        "displacement_px": candidate.displacement_px,
                        "discovery_method": candidate_discovery_method_label(candidate),
                        "start_x": candidate.start_x,
                        "start_y": candidate.start_y,
                        "end_x": candidate.end_x,
                        "end_y": candidate.end_y,
                        "first_detection_time_utc": None if first_detection is None else first_detection.observation_time.isoformat(),
                        "last_detection_time_utc": None if last_detection is None else last_detection.observation_time.isoformat(),
                        "frame_paths": "; ".join(detection.source_path.name for detection in candidate.frame_detections),
                        "summary_text": candidate.summary_text,
                    }
                )
        return rows

    def _summary_export_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for recovered in self._result.recovered_known_objects:
            rows.append(
                {
                    "benchmark_status": "recovered",
                    "object_name": recovered.detection.name or recovered.detection.designation or "Unknown",
                    "designation": recovered.detection.designation or "",
                    "predicted_magnitude": recovered.detection.predicted_magnitude,
                    "confidence_score": recovered.detection.confidence_score,
                    "catalog_status": recovered.detection.status,
                    "within_estimated_limit": recovered.within_estimated_limit,
                    "estimated_limit_magnitude": None if self._result.estimate_result is None else self._result.estimate_result.dimmest_visible_magnitude,
                    "matched_frame_count": recovered.matched_frame_count,
                    "expected_frame_count": recovered.expected_frame_count,
                    "recovered_fraction": (
                        float(recovered.matched_frame_count) / float(recovered.expected_frame_count)
                        if recovered.expected_frame_count > 0
                        else None
                    ),
                    "match_rms_px": recovered.match_rms_px,
                    "predicted_motion_arcsec_per_hour": recovered.detection.motion_rate_arcsec_per_hour,
                    "candidate_discovery_method": candidate_discovery_method_label(recovered.candidate),
                    "summary_text": recovered.summary_text,
                }
            )
        for missed in self._result.missed_known_objects:
            rows.append(
                {
                    "benchmark_status": "missed",
                    "object_name": missed.detection.name or missed.detection.designation or "Unknown",
                    "designation": missed.detection.designation or "",
                    "predicted_magnitude": missed.detection.predicted_magnitude,
                    "confidence_score": missed.detection.confidence_score,
                    "catalog_status": missed.detection.status,
                    "within_estimated_limit": missed.within_estimated_limit,
                    "estimated_limit_magnitude": None if self._result.estimate_result is None else self._result.estimate_result.dimmest_visible_magnitude,
                    "matched_frame_count": None,
                    "expected_frame_count": missed.expected_frame_count,
                    "recovered_fraction": 0.0,
                    "match_rms_px": None,
                    "predicted_motion_arcsec_per_hour": missed.detection.motion_rate_arcsec_per_hour,
                    "candidate_discovery_method": "",
                    "summary_text": missed.summary_text,
                }
            )
        return rows


@dataclass(frozen=True, slots=True)
class AsteroidDiscoveryRunOptions:
    assume_aligned: bool = False
    residual_min_snr: float = 0.0
    residual_max_snr: float = 0.0
    frames_per_batch: int = 0
    single_batch_only: bool = False
    binning_factor: int = 1
    use_temporary_cache: bool = True
    min_seed_displacement_px: float = 1.5
    motion_prior_bias: str = "balanced"
    retry_with_detailed_search: bool = False


class CalibrationPipelineDialog(QDialog):
    def __init__(
        self,
        *,
        default_root: Path,
        settings: AppSettings | None = None,
        parent: QWidget | None = None,
        workflow_mode: bool = False,
        science_path_override: Path | None = None,
        output_directory_override: Path | None = None,
        align_output_override: bool | None = None,
    ) -> None:
        super().__init__(parent)
        self._workflow_mode = workflow_mode
        self._dialog_title = "Workflow Calibration" if workflow_mode else "Calibrate Images"
        self.setWindowTitle(self._dialog_title)
        if workflow_mode:
            self.setObjectName("workflowCalibrationDialog")
        self.setMinimumWidth(760 if workflow_mode else 700)
        root_path = Path(default_root).expanduser()
        science_path = (
            Path(science_path_override).expanduser()
            if science_path_override is not None
            else (root_path if root_path.exists() else Path.cwd())
        )
        output_path = (
            Path(output_directory_override).expanduser()
            if output_directory_override is not None
            else self._default_output_directory(root_path)
        )
        saved_bias_path = "" if settings is None else str(settings.calibration_bias_path or "").strip()
        saved_dark_path = "" if settings is None else str(settings.calibration_dark_path or "").strip()
        saved_flat_path = "" if settings is None else str(settings.calibration_flat_path or "").strip()

        self._science_input = QLineEdit(str(science_path), self)
        self._output_input = QLineEdit(str(output_path), self)
        self._align_input = QCheckBox("Align calibrated images after dark/flat/bias correction", self)
        self._align_input.setToolTip("Use WCS reprojection to write aligned FITS copies after the calibration stage finishes.")
        if align_output_override is not None:
            self._align_input.setChecked(align_output_override)
        if workflow_mode:
            self._science_input.hide()
            self._output_input.hide()
            self._align_input.hide()
        self._bias_input = QLineEdit(saved_bias_path, self)
        self._dark_input = QLineEdit(saved_dark_path, self)
        self._flat_input = QLineEdit(saved_flat_path, self)
        for line_edit in (self._science_input, self._output_input, self._bias_input, self._dark_input, self._flat_input):
            line_edit.setMinimumWidth(420)

        intro = QLabel(
            "Choose the Bias, Dark, and Flat folders for this workflow run. The workflow already supplied the light-frame folder and alignment choice."
            if workflow_mode
            else "Prepare raw astronomy frames in two clear stages: choose the science images and calibration masters, then optionally align the calibrated output using WCS."
        )
        intro.setWordWrap(True)
        if workflow_mode:
            intro.setObjectName("workflowCalibrationIntroLabel")

        science_group = None if workflow_mode else self._science_group()
        masters_group = self._masters_group("Calibration Files" if workflow_mode else "2. Calibration Masters")
        alignment_group = None if workflow_mode else self._alignment_group()

        self._summary_label = QLabel(self)
        self._summary_label.setWordWrap(True)
        if workflow_mode:
            self._summary_label.setObjectName("workflowCalibrationSummaryLabel")
        self._update_summary()
        for widget in (self._science_input, self._output_input, self._bias_input, self._dark_input, self._flat_input):
            widget.textChanged.connect(self._update_summary)
        self._align_input.toggled.connect(self._update_summary)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        self._run_button = buttons.addButton(
            "Continue" if workflow_mode else "Run Calibration",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        if workflow_mode:
            self._run_button.setObjectName("workflowCalibrationRunButton")
        self._run_button.setToolTip(
            "Continue the workflow with these Bias, Dark, and Flat folder selections."
            if workflow_mode
            else "Start the calibration pipeline with the selected science images and calibration masters."
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        for button in buttons.buttons():
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        if not workflow_mode:
            self._apply_run_button_style()

        layout = QVBoxLayout()
        if workflow_mode:
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setSpacing(12)
        layout.addWidget(intro)
        if science_group is not None:
            layout.addWidget(science_group)
        layout.addWidget(masters_group)
        if alignment_group is not None:
            layout.addWidget(alignment_group)
        layout.addWidget(self._summary_label)
        layout.addWidget(buttons)
        self.setLayout(layout)
        if workflow_mode:
            self._apply_workflow_style()
        self.resize(max(700, self.sizeHint().width()), self.sizeHint().height())

    def request(self) -> CalibrationPipelineRequest:
        return CalibrationPipelineRequest(
            science_path=Path(self._science_input.text()).expanduser(),
            output_directory=Path(self._output_input.text()).expanduser(),
            bias_path=self._optional_path(self._bias_input),
            dark_path=self._optional_path(self._dark_input),
            flat_path=self._optional_path(self._flat_input),
            align_output=self._align_input.isChecked(),
        )

    def _path_row(self, line_edit: QLineEdit, *, browse_folder: Callable[[], None]) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(line_edit, stretch=1)
        folder_button = QPushButton("Folder...", self)
        folder_button.setCursor(Qt.CursorShape.PointingHandCursor)
        folder_button.clicked.connect(browse_folder)
        layout.addWidget(folder_button)
        row.setLayout(layout)
        return row

    def _master_path_row(self, line_edit: QLineEdit, file_title: str, folder_title: str) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(line_edit, stretch=1)
        file_button = QPushButton("File...", self)
        file_button.setCursor(Qt.CursorShape.PointingHandCursor)
        file_button.clicked.connect(lambda: self._browse_file(line_edit, file_title))
        folder_button = QPushButton("Folder...", self)
        folder_button.setCursor(Qt.CursorShape.PointingHandCursor)
        folder_button.clicked.connect(lambda: self._browse_folder(line_edit, folder_title))
        clear_button = QPushButton("Clear", self)
        clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_button.clicked.connect(line_edit.clear)
        layout.addWidget(file_button)
        layout.addWidget(folder_button)
        layout.addWidget(clear_button)
        row.setLayout(layout)
        return row

    def _master_folder_row(self, line_edit: QLineEdit, folder_title: str) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(line_edit, stretch=1)
        folder_button = QPushButton("Folder...", self)
        folder_button.setCursor(Qt.CursorShape.PointingHandCursor)
        folder_button.clicked.connect(lambda: self._browse_folder(line_edit, folder_title))
        clear_button = QPushButton("Clear", self)
        clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_button.clicked.connect(line_edit.clear)
        layout.addWidget(folder_button)
        layout.addWidget(clear_button)
        row.setLayout(layout)
        return row

    def _browse_folder(self, line_edit: QLineEdit, title: str) -> None:
        selected = QFileDialog.getExistingDirectory(self, title, line_edit.text() or str(Path.cwd()))
        if selected:
            line_edit.setText(selected)

    def _browse_file(self, line_edit: QLineEdit, title: str) -> None:
        selected, _selected_filter = QFileDialog.getOpenFileName(
            self,
            title,
            line_edit.text() or str(Path.cwd()),
            "Supported Image Files (*.fits *.fit *.xisf)",
        )
        if selected:
            line_edit.setText(selected)

    def _accept_if_valid(self) -> None:
        request = self.request()
        if not request.science_path.exists():
            QMessageBox.warning(self, self._dialog_title, "Choose an existing science image folder or file.")
            return
        for label, path in (("Bias", request.bias_path), ("Dark", request.dark_path), ("Flat", request.flat_path)):
            if path is not None and not path.exists():
                QMessageBox.warning(self, self._dialog_title, f"{label} path does not exist.")
                return
        if request.bias_path is None and request.dark_path is None and request.flat_path is None and not self._available_cached_master_labels():
            QMessageBox.warning(self, self._dialog_title, "Choose at least one bias, dark, or flat calibration source, or reuse cached masters already saved in the output folder.")
            return
        self.accept()

    def _update_summary(self) -> None:
        selected_masters = [label for label, widget in (("bias", self._bias_input), ("dark", self._dark_input), ("flat", self._flat_input)) if widget.text().strip()]
        cached_masters = [label for label in self._available_cached_master_labels() if label not in selected_masters]
        master_parts = selected_masters + [f"cached {label}" for label in cached_masters]
        master_text = ", ".join(master_parts) if master_parts else "no calibration masters selected"
        if self._workflow_mode:
            self._summary_label.setText(
                f"Workflow step: use these calibration folders for {master_text} when Generate continues."
            )
            return
        alignment_text = "then align calibrated frames" if self._align_input.isChecked() else "alignment skipped"
        self._summary_label.setText(f"Pipeline: science frames -> apply {master_text} -> write calibrated FITS -> {alignment_text}.")

    def _science_group(self) -> QGroupBox:
        science_group = QGroupBox("1. Science Images")
        science_layout = QFormLayout()
        science_layout.addRow("Input Folder", self._path_row(self._science_input, browse_folder=lambda: self._browse_folder(self._science_input, "Select science image folder")))
        science_layout.addRow("Output Folder", self._path_row(self._output_input, browse_folder=lambda: self._browse_folder(self._output_input, "Select calibration output folder")))
        science_group.setLayout(science_layout)
        return science_group

    def _masters_group(self, title: str) -> QGroupBox:
        masters_group = QGroupBox(title)
        masters_layout = QFormLayout()
        if self._workflow_mode:
            masters_layout.addRow("Bias", self._master_folder_row(self._bias_input, "Select bias frame folder"))
            masters_layout.addRow("Dark", self._master_folder_row(self._dark_input, "Select dark frame folder"))
            masters_layout.addRow("Flat", self._master_folder_row(self._flat_input, "Select flat frame folder"))
        else:
            masters_layout.addRow("Bias", self._master_path_row(self._bias_input, "Select bias master or frame", "Select bias frame folder"))
            masters_layout.addRow("Dark", self._master_path_row(self._dark_input, "Select dark master or frame", "Select dark frame folder"))
            masters_layout.addRow("Flat", self._master_path_row(self._flat_input, "Select flat master or frame", "Select flat frame folder"))
        masters_group.setLayout(masters_layout)
        return masters_group

    def _alignment_group(self) -> QGroupBox:
        alignment_group = QGroupBox("3. Optional Alignment")
        alignment_layout = QVBoxLayout()
        alignment_note = QLabel("Alignment runs after calibration and saves separate WCS-reprojected FITS copies in an aligned subfolder.")
        alignment_note.setWordWrap(True)
        alignment_layout.addWidget(self._align_input)
        alignment_layout.addWidget(alignment_note)
        alignment_group.setLayout(alignment_layout)
        return alignment_group

    def _cached_master_path(self, file_name: str) -> Path | None:
        output_text = self._output_input.text().strip()
        if not output_text:
            return None
        output_directory = Path(output_text).expanduser()
        if not output_directory.exists() or not output_directory.is_dir():
            return None
        for candidate in (output_directory / file_name, output_directory / "masters" / file_name):
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    def _available_cached_master_labels(self) -> list[str]:
        cached_labels: list[str] = []
        for label, file_name in (("bias", "master_bias.fits"), ("dark", "master_dark.fits"), ("flat", "master_flat_normalized.fits")):
            if self._cached_master_path(file_name) is not None:
                cached_labels.append(label)
        return cached_labels

    @staticmethod
    def _default_output_directory(root_path: Path) -> Path:
        return root_path / "calibration_output" if root_path.exists() and root_path.is_dir() else Path.cwd() / "calibration_output"

    def _apply_run_button_style(self) -> None:
        accent = self.palette().color(QPalette.ColorRole.Highlight)
        text_color = "#ffffff" if accent.lightness() < 128 else "#1f1f1f"
        hover_color = accent.lighter(110).name().lower()
        pressed_color = accent.darker(110).name().lower()
        border_color = accent.darker(122).name().lower()
        self._run_button.setStyleSheet(
            "QPushButton {"
            f"background-color: {accent.name().lower()};"
            f"color: {text_color};"
            f"border: 1px solid {border_color};"
            "padding: 4px 10px;"
            "font-weight: 600;"
            "}"
            "QPushButton:hover {"
            f"background-color: {hover_color};"
            "}"
            "QPushButton:pressed {"
            f"background-color: {pressed_color};"
            "}"
        )

    def _apply_workflow_style(self) -> None:
        palette = self.palette()
        window_bg = palette.window().color().name().lower()
        card_bg = QColor(window_bg).lighter(106).name().lower()
        border_color = QColor(window_bg).lighter(122).name().lower()
        accent_color = palette.color(QPalette.ColorRole.Highlight)
        accent = accent_color.name().lower()
        accent_soft = accent_color.lighter(130).name().lower()
        accent_deep = accent_color.darker(118).name().lower()
        body_text = palette.windowText().color().name().lower()
        muted_text = QColor(body_text).lighter(130).name().lower()
        self.setStyleSheet(
            "QDialog#workflowCalibrationDialog {"
            f"background-color: {window_bg};"
            f"color: {body_text};"
            "}"
            "QGroupBox {"
            f"background-color: {card_bg};"
            f"border: 1px solid {border_color};"
            "border-radius: 12px;"
            "margin-top: 20px;"
            "padding: 16px 12px 12px 12px;"
            "font-weight: 600;"
            f"color: {body_text};"
            "}"
            "QGroupBox::title {"
            "subcontrol-origin: margin;"
            "left: 12px;"
            "padding: 0 6px;"
            f"color: {accent};"
            "}"
            f"QLabel#workflowCalibrationIntroLabel {{ color: {muted_text}; padding: 0 2px 2px 2px; }}"
            f"QLabel#workflowCalibrationSummaryLabel {{ color: {accent_soft}; padding: 2px 2px 0 2px; }}"
            "QLineEdit {"
            f"background-color: {QColor(card_bg).lighter(106).name().lower()};"
            f"color: {body_text};"
            f"border: 1px solid {border_color};"
            "border-radius: 8px;"
            "padding: 7px 10px;"
            "selection-background-color: palette(highlight);"
            "}"
            "QPushButton {"
            f"background-color: {card_bg};"
            f"color: {body_text};"
            f"border: 1px solid {border_color};"
            "border-radius: 8px;"
            "padding: 6px 12px;"
            "font-weight: 600;"
            "}"
            f"QPushButton:hover {{ border-color: {accent_soft}; background-color: {QColor(card_bg).lighter(112).name().lower()}; }}"
            f"QPushButton:pressed {{ background-color: {QColor(card_bg).darker(108).name().lower()}; }}"
            f"QPushButton#workflowCalibrationRunButton {{ background-color: {accent}; color: {'#ffffff' if accent_color.lightness() < 128 else '#1f1f1f'}; border-color: {accent_deep}; }}"
            f"QPushButton#workflowCalibrationRunButton:hover {{ background-color: {QColor(accent_soft).lighter(105).name().lower()}; border: 2px solid {accent_soft}; }}"
            f"QPushButton#workflowCalibrationRunButton:pressed {{ background-color: {accent_deep}; border-color: {accent_deep}; }}"
        )

    @staticmethod
    def _optional_path(line_edit: QLineEdit) -> Path | None:
        text = line_edit.text().strip()
        return Path(text).expanduser() if text else None


class WorkflowCalibrationPipelineDialog(CalibrationPipelineDialog):
    def __init__(
        self,
        *,
        default_root: Path,
        settings: AppSettings | None = None,
        align_output: bool,
        parent: QWidget | None = None,
    ) -> None:
        root_path = Path(default_root).expanduser()
        super().__init__(
            default_root=root_path,
            settings=settings,
            parent=parent,
            workflow_mode=True,
            science_path_override=root_path,
            output_directory_override=self._default_output_directory(root_path),
            align_output_override=align_output,
        )


class AsteroidDiscoveryOptionsDialog(QDialog):
    def __init__(
        self,
        defaults: AsteroidDiscoveryRunOptions,
        *,
        frame_paths: Sequence[Path],
        frame_metadata: Mapping[str, ObservationMetadata],
        pixel_scale_arcsec_per_pixel: float | None,
        fallback_pixel_scale_arcsec_per_pixel: float | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Discover Pipeline")
        self.resize(560, 420)
        self._default_options = defaults
        self._frame_paths = [Path(path) for path in frame_paths]
        self._frame_metadata = dict(frame_metadata)
        self._pixel_scale_arcsec_per_pixel = (
            float(pixel_scale_arcsec_per_pixel)
            if pixel_scale_arcsec_per_pixel is not None
            else (
                None
                if fallback_pixel_scale_arcsec_per_pixel is None
                else float(fallback_pixel_scale_arcsec_per_pixel)
            )
        )

        description = QLabel(
            "Choose how Discover should prepare and search the current subgroup. Fast scan uses smaller temporary working copies first, while detailed search keeps the full-resolution working path."
        )
        description.setWordWrap(True)

        self._preset_input = QComboBox(self)
        self._preset_input.addItem("Detailed search", "detailed")
        self._preset_input.addItem("Fast scan", "fast")
        self._preset_input.addItem("Custom", "custom")
        self._preset_input.currentIndexChanged.connect(self._handle_preset_changed)

        self._alignment_input = QComboBox(self)
        self._alignment_input.addItem("Align first", False)
        self._alignment_input.addItem("Already aligned", True)
        self._set_combo_data(self._alignment_input, defaults.assume_aligned)

        self._binning_factor_input = QComboBox(self)
        self._binning_factor_input.addItem("Off (1x1)", 1)
        self._binning_factor_input.addItem("2x2", 2)
        self._binning_factor_input.addItem("3x3", 3)
        self._binning_factor_input.addItem("4x4", 4)
        self._set_combo_data(self._binning_factor_input, defaults.binning_factor)
        self._binning_factor_input.currentIndexChanged.connect(self._update_motion_range_preview)

        self._motion_prior_bias_input = QComboBox(self)
        self._motion_prior_bias_input.addItem("Balanced", "balanced")
        self._motion_prior_bias_input.addItem("Main-belt bias", "main_belt")
        self._motion_prior_bias_input.addItem("Faster near-Earth bias", "near_earth")
        self._set_combo_data(self._motion_prior_bias_input, str(defaults.motion_prior_bias or "balanced").strip().lower())
        self._motion_prior_bias_input.currentIndexChanged.connect(self._update_motion_range_preview)

        self._use_temporary_cache_input = QCheckBox("Write temporary prepared working frames to an auto-cleaned cache")
        self._use_temporary_cache_input.setChecked(defaults.use_temporary_cache)

        self._residual_min_snr_input = QDoubleSpinBox(self)
        self._residual_min_snr_input.setDecimals(1)
        self._residual_min_snr_input.setRange(0.0, 500.0)
        self._residual_min_snr_input.setSingleStep(0.5)
        self._residual_min_snr_input.setSpecialValueText("Disabled")
        self._residual_min_snr_input.setSuffix(" SNR")
        self._residual_min_snr_input.setValue(max(0.0, defaults.residual_min_snr))

        self._residual_max_snr_input = QDoubleSpinBox(self)
        self._residual_max_snr_input.setDecimals(1)
        self._residual_max_snr_input.setRange(0.0, 500.0)
        self._residual_max_snr_input.setSingleStep(0.5)
        self._residual_max_snr_input.setSpecialValueText("Disabled")
        self._residual_max_snr_input.setSuffix(" SNR")
        self._residual_max_snr_input.setValue(max(0.0, defaults.residual_max_snr))

        self._min_seed_displacement_input = QDoubleSpinBox(self)
        self._min_seed_displacement_input.setDecimals(2)
        self._min_seed_displacement_input.setRange(0.0, 100.0)
        self._min_seed_displacement_input.setSingleStep(0.1)
        self._min_seed_displacement_input.setSuffix(" px")
        self._min_seed_displacement_input.setValue(max(0.0, float(defaults.min_seed_displacement_px)))
        self._min_seed_displacement_input.setToolTip(
            "Minimum start-to-end motion required for a residual track seed before linking continues. Lower values allow slower movers but can admit more false seeds."
        )

        self._frames_per_batch_input = QSpinBox(self)
        self._frames_per_batch_input.setRange(0, 500)
        self._frames_per_batch_input.setSpecialValueText("Whole group")
        self._frames_per_batch_input.setSuffix(" frames")
        self._frames_per_batch_input.setValue(max(0, defaults.frames_per_batch))

        self._single_batch_only_input = QCheckBox("Only run one sampled batch from this group")
        self._single_batch_only_input.setChecked(defaults.single_batch_only)

        self._retry_with_detailed_search_input = QCheckBox("If no candidates are found, retry automatically with the detailed full-resolution search")
        self._retry_with_detailed_search_input.setChecked(defaults.retry_with_detailed_search)

        self._motion_range_preview_label = QLabel(self)
        self._motion_range_preview_label.setWordWrap(True)

        self._preset_note = QLabel(self)
        self._preset_note.setWordWrap(True)

        form_layout = QFormLayout()
        form_layout.addRow("Preset", self._preset_input)
        form_layout.addRow("Alignment", self._alignment_input)
        form_layout.addRow("Working Binning", self._binning_factor_input)
        form_layout.addRow("Motion Prior Bias", self._motion_prior_bias_input)
        form_layout.addRow("Temporary Cache", self._use_temporary_cache_input)
        form_layout.addRow("Residual Min SNR", self._residual_min_snr_input)
        form_layout.addRow("Residual Max SNR", self._residual_max_snr_input)
        form_layout.addRow("Min Seed Displacement", self._min_seed_displacement_input)
        form_layout.addRow("Frames per Batch", self._frames_per_batch_input)
        form_layout.addRow("Single Batch", self._single_batch_only_input)
        form_layout.addRow(self._retry_with_detailed_search_input)

        run_button = QPushButton("Run Discover", self)
        run_button.clicked.connect(self.accept)
        cancel_button = QPushButton("Cancel", self)
        cancel_button.clicked.connect(self.reject)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(cancel_button)
        button_row.addWidget(run_button)

        layout = QVBoxLayout()
        layout.addWidget(description)
        layout.addLayout(form_layout)
        layout.addWidget(self._motion_range_preview_label)
        layout.addWidget(self._preset_note)
        layout.addStretch(1)
        layout.addLayout(button_row)
        self.setLayout(layout)

        self._handle_preset_changed()
        self._update_motion_range_preview()

    def build_options(self) -> AsteroidDiscoveryRunOptions:
        minimum = max(0.0, float(self._residual_min_snr_input.value()))
        maximum = max(0.0, float(self._residual_max_snr_input.value()))
        if maximum > 0.0 and minimum > maximum:
            minimum, maximum = maximum, minimum
        return AsteroidDiscoveryRunOptions(
            assume_aligned=bool(self._alignment_input.currentData()),
            residual_min_snr=minimum,
            residual_max_snr=maximum,
            frames_per_batch=(
                min(len(self._frame_paths), 10)
                if self._single_batch_only_input.isChecked() and int(self._frames_per_batch_input.value()) <= 0
                else max(0, int(self._frames_per_batch_input.value()))
            ),
            single_batch_only=self._single_batch_only_input.isChecked(),
            binning_factor=int(self._binning_factor_input.currentData() or 1),
            use_temporary_cache=self._use_temporary_cache_input.isChecked(),
            min_seed_displacement_px=max(0.0, float(self._min_seed_displacement_input.value())),
            motion_prior_bias=str(self._motion_prior_bias_input.currentData() or "balanced"),
            retry_with_detailed_search=self._retry_with_detailed_search_input.isChecked(),
        )

    def _handle_preset_changed(self) -> None:
        preset = str(self._preset_input.currentData() or "custom")
        if preset == "fast":
            self._set_combo_data(self._binning_factor_input, max(2, int(self._default_options.binning_factor)))
            self._use_temporary_cache_input.setChecked(True)
            self._residual_min_snr_input.setValue(max(6.0, float(self._default_options.residual_min_snr)))
            self._residual_max_snr_input.setValue(max(0.0, float(self._default_options.residual_max_snr)))
            self._min_seed_displacement_input.setValue(max(0.0, float(self._default_options.min_seed_displacement_px)))
            self._frames_per_batch_input.setValue(max(6, int(self._default_options.frames_per_batch or 12)))
            self._single_batch_only_input.setChecked(self._default_options.single_batch_only)
            self._retry_with_detailed_search_input.setEnabled(True)
            self._preset_note.setText(
                "Fast scan uses binned temporary working frames plus smaller overlapping batches so Discover can sweep the subgroup quickly before you decide whether to run the slower full-resolution path."
            )
            return
        if preset == "detailed":
            self._set_combo_data(self._binning_factor_input, self._default_options.binning_factor)
            self._use_temporary_cache_input.setChecked(self._default_options.use_temporary_cache)
            self._residual_min_snr_input.setValue(self._default_options.residual_min_snr)
            self._residual_max_snr_input.setValue(self._default_options.residual_max_snr)
            self._min_seed_displacement_input.setValue(self._default_options.min_seed_displacement_px)
            self._frames_per_batch_input.setValue(self._default_options.frames_per_batch)
            self._single_batch_only_input.setChecked(self._default_options.single_batch_only)
            self._retry_with_detailed_search_input.setChecked(False)
            self._retry_with_detailed_search_input.setEnabled(False)
            self._preset_note.setText(
                "Detailed search uses your saved Discovery Advanced defaults. Leave batching at Whole group and binning at Off for the most exhaustive residual search, and enable Final Synthetic Sweep in Discovery Advanced when you want the slow last-stage velocity-grid pass."
            )
            return
        self._retry_with_detailed_search_input.setEnabled(True)
        self._preset_note.setText(
            "Custom keeps the current values below. Temporary cached working frames are deleted after the run and stale Discover cache folders are cleaned again on the next startup if the app shuts down unexpectedly."
        )

    def _update_motion_range_preview(self) -> None:
        pixel_scale_arcsec_per_pixel = self._pixel_scale_arcsec_per_pixel
        if pixel_scale_arcsec_per_pixel is None or pixel_scale_arcsec_per_pixel <= 0:
            self._motion_range_preview_label.setText(
                "Adaptive motion range preview unavailable: need a valid pixel scale from the current solved group or setup values."
            )
            return
        effective_binning_factor = int(self._binning_factor_input.currentData() or 1)
        estimate = _estimate_discovery_motion_range(
            self._frame_paths,
            frame_metadata=self._frame_metadata,
            pixel_scale_arcsec_per_pixel=pixel_scale_arcsec_per_pixel * max(1, effective_binning_factor),
            motion_prior_bias=str(self._motion_prior_bias_input.currentData() or "balanced"),
        )
        if estimate is None:
            self._motion_range_preview_label.setText(
                "Adaptive motion range preview unavailable: exposure times are missing from the current subgroup metadata."
            )
            return
        exposure_scale = float(estimate.median_exposure_seconds) / 3600.0
        min_exposure_motion_px = float(estimate.min_motion_px_per_hour * exposure_scale)
        max_exposure_motion_px = float(estimate.max_motion_px_per_hour * exposure_scale)
        self._motion_range_preview_label.setText(
            "Typical exposure motion in this group: about "
            f"{min_exposure_motion_px:.1f}-{max_exposure_motion_px:.1f} px during one "
            f"{estimate.median_exposure_seconds:.0f}s exposure at "
            f"{estimate.pixel_scale_arcsec_per_pixel:.2f} arcsec/px effective scale."
        )

    @staticmethod
    def _set_combo_data(combo_box: QComboBox, value: object) -> None:
        combo_index = combo_box.findData(value)
        if combo_index >= 0:
            combo_box.setCurrentIndex(combo_index)


class AsteroidRecoveryDialog(QDialog):
    def __init__(
        self,
        *,
        display: AnnotatedImageDisplay,
        result: MovingObjectRecoveryResult,
        render_settings: AnnotatedImageRenderSettings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Recover Known Moving Objects")
        self.resize(1220, 860)
        self._display = display
        self._result = result
        self._render_settings = render_settings
        self._trajectory_dialogs: list[MovingObjectTrajectoryDialog] = []

        summary_label = QLabel(result.summary_text)
        summary_label.setWordWrap(True)

        self._tabs = QTabWidget(self)

        self._recovered_table = QTableWidget(len(result.recovered_known_objects), 6, self)
        self._recovered_table.setHorizontalHeaderLabels(["Object", "Frames", "Recovered", "Motion", "Residual Score", "Match RMS"])
        self._configure_table(self._recovered_table, ((0, 180), (1, 70), (2, 80), (3, 160), (4, 90), (5, 90)))
        self._recovered_table.itemSelectionChanged.connect(self._handle_selection_changed)

        self._missed_table = QTableWidget(len(result.missed_known_objects), 5, self)
        self._missed_table.setHorizontalHeaderLabels(["Object", "V_mag", "Status", "Confidence", "Motion"])
        self._configure_table(self._missed_table, ((0, 180), (1, 70), (2, 180), (3, 90), (4, 140)))
        self._missed_table.itemSelectionChanged.connect(self._handle_selection_changed)

        self._unmatched_table = QTableWidget(len(result.unmatched_candidates), 5, self)
        self._unmatched_table.setHorizontalHeaderLabels(["Candidate", "Frames", "Motion", "Residual Score", "Fit RMS"])
        self._configure_table(self._unmatched_table, ((0, 90), (1, 70), (2, 150), (3, 90), (4, 90)))
        self._unmatched_table.itemSelectionChanged.connect(self._handle_selection_changed)

        self._tabs.addTab(self._recovered_table, f"Recovered Known ({len(result.recovered_known_objects)})")
        self._tabs.addTab(self._missed_table, f"Missed Known ({len(result.missed_known_objects)})")
        self._tabs.addTab(self._unmatched_table, f"Unmatched ({len(result.unmatched_candidates)})")
        self._tabs.currentChanged.connect(self._handle_tab_changed)

        self._image_view = AnnotatedImageView(self)
        self._details_output = QPlainTextEdit(self)
        self._details_output.setReadOnly(True)

        self._trajectory_button = QPushButton("Trajectory...", self)
        self._trajectory_button.clicked.connect(self._open_selected_trajectory)
        export_benchmark_button = QPushButton("Export Benchmark...", self)
        export_benchmark_button.clicked.connect(self._export_benchmark_table)
        export_unmatched_button = QPushButton("Export Unmatched...", self)
        export_unmatched_button.clicked.connect(self._export_unmatched_candidates_table)
        export_summary_button = QPushButton("Export Summary...", self)
        export_summary_button.clicked.connect(self._export_summary_table)
        close_button = QPushButton("Close", self)
        close_button.clicked.connect(self.accept)

        right_panel = QWidget(self)
        right_layout = QVBoxLayout()
        right_layout.addWidget(self._image_view, stretch=1)
        right_layout.addWidget(self._details_output)
        right_panel.setLayout(right_layout)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._tabs)
        splitter.addWidget(right_panel)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([420, 800])

        layout = QVBoxLayout()
        layout.addWidget(summary_label)
        layout.addWidget(splitter, stretch=1)
        button_row = QHBoxLayout()
        button_row.addWidget(self._trajectory_button)
        button_row.addWidget(export_benchmark_button)
        button_row.addWidget(export_unmatched_button)
        button_row.addWidget(export_summary_button)
        button_row.addStretch(1)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)
        self.setLayout(layout)

        self._populate_recovered_table()
        self._populate_missed_table()
        self._populate_unmatched_table()
        self._select_first_available_row()

    def _configure_table(self, table: QTableWidget, widths: tuple[tuple[int, int], ...]) -> None:
        header = table.horizontalHeader()
        for column_index, width in widths:
            header.setSectionResizeMode(column_index, QHeaderView.ResizeMode.Interactive)
            table.setColumnWidth(column_index, width)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setSortingEnabled(False)

    def _populate_recovered_table(self) -> None:
        for row_index, recovered in enumerate(self._result.recovered_known_objects):
            candidate = recovered.candidate
            detection = recovered.detection
            items = [
                QTableWidgetItem(detection.name or detection.designation or "Unknown"),
                QTableWidgetItem(str(recovered.expected_frame_count)),
                QTableWidgetItem(str(recovered.matched_frame_count)),
                QTableWidgetItem(
                    f"{candidate.motion_px_per_hour:.2f} px/h"
                    + ("" if candidate.motion_arcsec_per_hour is None else f" | {candidate.motion_arcsec_per_hour:.2f} arcsec/h")
                ),
                QTableWidgetItem(f"{candidate.average_snr:.2f}"),
                QTableWidgetItem(f"{recovered.match_rms_px:.2f} px"),
            ]
            for item in items:
                item.setData(Qt.ItemDataRole.UserRole, row_index)
            for column_index, item in enumerate(items):
                self._recovered_table.setItem(row_index, column_index, item)

    def _populate_missed_table(self) -> None:
        for row_index, missed in enumerate(self._result.missed_known_objects):
            detection = missed.detection
            items = [
                QTableWidgetItem(detection.name or detection.designation or "Unknown"),
                QTableWidgetItem("-" if detection.predicted_magnitude is None else f"{detection.predicted_magnitude:.1f}"),
                QTableWidgetItem(detection.status),
                QTableWidgetItem(f"{detection.confidence_score:.2f}"),
                QTableWidgetItem(
                    "-" if detection.motion_rate_arcsec_per_hour is None else f"{detection.motion_rate_arcsec_per_hour:.2f} arcsec/h"
                ),
            ]
            for item in items:
                item.setData(Qt.ItemDataRole.UserRole, row_index)
            for column_index, item in enumerate(items):
                self._missed_table.setItem(row_index, column_index, item)

    def _populate_unmatched_table(self) -> None:
        for row_index, candidate in enumerate(self._result.unmatched_candidates):
            items = [
                QTableWidgetItem(candidate.candidate_id),
                QTableWidgetItem(str(len(candidate.frame_detections))),
                QTableWidgetItem(
                    f"{candidate.motion_px_per_hour:.2f} px/h"
                    + ("" if candidate.motion_arcsec_per_hour is None else f" | {candidate.motion_arcsec_per_hour:.2f} arcsec/h")
                ),
                QTableWidgetItem(f"{candidate.average_snr:.2f}"),
                QTableWidgetItem(f"{candidate.fit_rms_px:.2f} px"),
            ]
            for item in items:
                item.setData(Qt.ItemDataRole.UserRole, row_index)
            for column_index, item in enumerate(items):
                self._unmatched_table.setItem(row_index, column_index, item)

    def _select_first_available_row(self) -> None:
        if self._result.recovered_known_objects:
            self._tabs.setCurrentWidget(self._recovered_table)
            self._recovered_table.selectRow(0)
            self._handle_selection_changed()
            return
        if self._result.missed_known_objects:
            self._tabs.setCurrentWidget(self._missed_table)
            self._missed_table.selectRow(0)
            self._handle_selection_changed()
            return
        if self._result.unmatched_candidates:
            self._tabs.setCurrentWidget(self._unmatched_table)
            self._unmatched_table.selectRow(0)
            self._handle_selection_changed()
            return
        self._image_view.set_content(
            self._display,
            overlays=[],
            grid_overlays=[],
            editor_enabled=False,
            reset_view=True,
            render_settings=self._render_settings,
        )
        self._details_output.setPlainText("No moving-object recovery results are available for the current group.")
        self._update_trajectory_button_state()

    def _handle_tab_changed(self, _index: int) -> None:
        current = self._tabs.currentWidget()
        if current is self._recovered_table and self._recovered_table.rowCount() > 0 and not self._recovered_table.selectionModel().selectedRows():
            self._recovered_table.selectRow(0)
        elif current is self._missed_table and self._missed_table.rowCount() > 0 and not self._missed_table.selectionModel().selectedRows():
            self._missed_table.selectRow(0)
        elif current is self._unmatched_table and self._unmatched_table.rowCount() > 0 and not self._unmatched_table.selectionModel().selectedRows():
            self._unmatched_table.selectRow(0)
        self._handle_selection_changed()

    def _handle_selection_changed(self) -> None:
        self._update_trajectory_button_state()
        current = self._tabs.currentWidget()
        if current is self._recovered_table:
            recovered = self._selected_item(self._recovered_table, self._result.recovered_known_objects)
            if recovered is not None:
                self._show_recovered_known_object(recovered)
            return
        if current is self._missed_table:
            missed = self._selected_item(self._missed_table, self._result.missed_known_objects)
            if missed is not None:
                self._show_missed_known_object(missed)
            return
        unmatched = self._selected_item(self._unmatched_table, self._result.unmatched_candidates)
        if unmatched is not None:
            self._show_unmatched_candidate(unmatched)

    def _selected_item(self, table: QTableWidget, values: tuple[object, ...]) -> object | None:
        selected_rows = table.selectionModel().selectedRows() if table.selectionModel() is not None else []
        if not selected_rows:
            return None
        row = int(selected_rows[0].row())
        if row < 0 or row >= len(values):
            return None
        item = table.item(row, 0)
        if item is not None:
            value_index = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(value_index, int) and 0 <= value_index < len(values):
                return values[value_index]
        return values[row]

    def _show_recovered_known_object(self, recovered: RecoveredKnownMovingObject) -> None:
        overlays = [
            ImageOverlay(
                source_id=f"{recovered.candidate.candidate_id}:{detection.frame_index}",
                name=f"F{detection.frame_index + 1}",
                x=detection.x,
                y=detection.y,
                aperture_radius=3.0,
                annulus_inner_radius=3.0,
                annulus_outer_radius=3.0,
                color="#10b981",
                show_annulus=False,
                show_label=True,
                marker_style="circle",
                show_center_dot=False,
            )
            for detection in recovered.candidate.frame_detections
        ]
        overlays.append(
            ImageOverlay(
                source_id=f"known:{recovered.detection.name or recovered.detection.designation or 'known'}",
                name=recovered.detection.name or recovered.detection.designation or "Known",
                x=recovered.reference_x,
                y=recovered.reference_y,
                aperture_radius=5.0,
                annulus_inner_radius=5.0,
                annulus_outer_radius=5.0,
                color="#38bdf8",
                show_annulus=False,
                show_label=True,
                marker_style="circle",
                show_center_dot=False,
            )
        )
        motion_vector = MotionVectorOverlay(
            x=recovered.candidate.start_x,
            y=recovered.candidate.start_y,
            dx=recovered.candidate.end_x - recovered.candidate.start_x,
            dy=recovered.candidate.end_y - recovered.candidate.start_y,
            color="#38bdf8",
            width=2.0,
            show_anchor=True,
        )
        self._image_view.set_content(
            self._display,
            overlays=overlays,
            grid_overlays=[],
            editor_enabled=False,
            reset_view=True,
            render_settings=self._render_settings,
            motion_vector_overlays=[motion_vector],
        )
        self._image_view.focus_on(recovered.reference_x, recovered.reference_y, minimum_zoom_scale=3.0)
        self._details_output.setPlainText(self._recovered_known_details_text(recovered))

    def _show_missed_known_object(self, missed: MissedKnownMovingObject) -> None:
        overlays = [
            ImageOverlay(
                source_id=f"missed:{missed.detection.name or missed.detection.designation or 'missed'}",
                name=missed.detection.name or missed.detection.designation or "Missed",
                x=missed.reference_x,
                y=missed.reference_y,
                aperture_radius=5.0,
                annulus_inner_radius=5.0,
                annulus_outer_radius=5.0,
                color="#ef4444",
                show_annulus=False,
                show_label=True,
                marker_style="circle",
                show_center_dot=False,
            )
        ]
        self._image_view.set_content(
            self._display,
            overlays=overlays,
            grid_overlays=[],
            editor_enabled=False,
            reset_view=True,
            render_settings=self._render_settings,
        )
        self._image_view.focus_on(missed.reference_x, missed.reference_y, minimum_zoom_scale=3.0)
        self._details_output.setPlainText(self._missed_known_details_text(missed))

    def _show_unmatched_candidate(self, candidate: MovingObjectCandidate) -> None:
        overlays = [
            ImageOverlay(
                source_id=f"{candidate.candidate_id}:{detection.frame_index}",
                name=f"F{detection.frame_index + 1}",
                x=detection.x,
                y=detection.y,
                aperture_radius=3.0,
                annulus_inner_radius=3.0,
                annulus_outer_radius=3.0,
                color="#f59e0b",
                show_annulus=False,
                show_label=True,
                marker_style="circle",
                show_center_dot=False,
            )
            for detection in candidate.frame_detections
        ]
        motion_vector = MotionVectorOverlay(
            x=candidate.start_x,
            y=candidate.start_y,
            dx=candidate.end_x - candidate.start_x,
            dy=candidate.end_y - candidate.start_y,
            color="#38bdf8",
            width=2.0,
            show_anchor=True,
        )
        self._image_view.set_content(
            self._display,
            overlays=overlays,
            grid_overlays=[],
            editor_enabled=False,
            reset_view=True,
            render_settings=self._render_settings,
            motion_vector_overlays=[motion_vector],
        )
        midpoint_x = (candidate.start_x + candidate.end_x) / 2.0
        midpoint_y = (candidate.start_y + candidate.end_y) / 2.0
        self._image_view.focus_on(midpoint_x, midpoint_y, minimum_zoom_scale=3.0)
        self._details_output.setPlainText(self._candidate_details_text(candidate))

    def _open_selected_trajectory(self) -> None:
        selection = self._selected_trajectory_candidate()
        if selection is None:
            return
        object_label, candidate = selection
        self._show_trajectory_dialog(object_label, candidate)

    def _selected_trajectory_candidate(self) -> tuple[str, MovingObjectCandidate] | None:
        current = self._tabs.currentWidget()
        if current is self._recovered_table:
            recovered = self._selected_item(self._recovered_table, self._result.recovered_known_objects)
            if isinstance(recovered, RecoveredKnownMovingObject):
                label = recovered.detection.name or recovered.detection.designation or recovered.candidate.candidate_id
                return label, recovered.candidate
            return None
        if current is self._unmatched_table:
            candidate = self._selected_item(self._unmatched_table, self._result.unmatched_candidates)
            if isinstance(candidate, MovingObjectCandidate):
                return f"Candidate {candidate.candidate_id}", candidate
        return None

    def _show_trajectory_dialog(self, object_label: str, candidate: MovingObjectCandidate) -> None:
        dialog = MovingObjectTrajectoryDialog(object_label=object_label, candidate=candidate, parent=self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.destroyed.connect(lambda _obj=None, dialog_ref=dialog: self._forget_trajectory_dialog(dialog_ref))
        self._trajectory_dialogs.append(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _forget_trajectory_dialog(self, dialog: MovingObjectTrajectoryDialog) -> None:
        if dialog in self._trajectory_dialogs:
            self._trajectory_dialogs.remove(dialog)

    def _update_trajectory_button_state(self) -> None:
        self._trajectory_button.setEnabled(self._selected_trajectory_candidate() is not None)

    def _recovered_known_details_text(self, recovered: RecoveredKnownMovingObject) -> str:
        detection = recovered.detection
        candidate = recovered.candidate
        lines = [
            f"Known object: {detection.name or detection.designation or 'Unknown'}",
            f"Designation: {detection.designation or '-'}",
            f"Status: {detection.status}",
            f"Confidence: {detection.confidence_score:.2f}",
            f"Predicted magnitude: {'-' if detection.predicted_magnitude is None else f'{detection.predicted_magnitude:.1f}'}",
            f"Recovered frames: {recovered.matched_frame_count}/{recovered.expected_frame_count}",
            f"Match RMS: {recovered.match_rms_px:.2f} px",
            f"Max match offset: {recovered.max_match_offset_px:.2f} px",
            f"Motion: {candidate.motion_px_per_hour:.2f} px/hour"
            + ("" if candidate.motion_arcsec_per_hour is None else f" | {candidate.motion_arcsec_per_hour:.2f} arcsec/hour"),
            f"Average residual score: {candidate.average_snr:.2f}",
            "",
            "Frame detections:",
        ]
        for detection_row in candidate.frame_detections:
            position_text = f"x={detection_row.x:.2f}, y={detection_row.y:.2f}"
            if detection_row.ra_deg is not None and detection_row.dec_deg is not None:
                position_text += f" | RA={detection_row.ra_deg:.6f} deg, Dec={detection_row.dec_deg:.6f} deg"
            lines.append(f"F{detection_row.frame_index + 1} | {detection_row.source_path.name} | {detection_row.observation_time.isoformat()} | {position_text} | SNR={detection_row.local_snr:.2f}")
        return "\n".join(lines)

    def _missed_known_details_text(self, missed: MissedKnownMovingObject) -> str:
        detection = missed.detection
        lines = [
            f"Known object: {detection.name or detection.designation or 'Unknown'}",
            f"Designation: {detection.designation or '-'}",
            f"Status: {detection.status}",
            f"Confidence: {detection.confidence_score:.2f}",
            f"Predicted magnitude: {'-' if detection.predicted_magnitude is None else f'{detection.predicted_magnitude:.1f}'}",
            f"Expected frames: {missed.expected_frame_count}",
            f"Motion: {'-' if detection.motion_rate_arcsec_per_hour is None else f'{detection.motion_rate_arcsec_per_hour:.2f} arcsec/hour'}",
            f"Reference position: x={missed.reference_x:.2f}, y={missed.reference_y:.2f}",
            "",
            missed.summary_text,
        ]
        return "\n".join(lines)

    def _candidate_details_text(self, candidate: MovingObjectCandidate) -> str:
        lines = [
            f"Candidate: {candidate.candidate_id}",
            f"Frames seen: {len(candidate.frame_detections)}",
            f"Motion: {candidate.motion_px_per_hour:.2f} px/hour"
            + ("" if candidate.motion_arcsec_per_hour is None else f" | {candidate.motion_arcsec_per_hour:.2f} arcsec/hour"),
            f"Track displacement: {candidate.displacement_px:.2f} px",
            f"Average residual score: {candidate.average_snr:.2f}",
            f"Peak residual: {candidate.peak_value:.1f}",
            f"Fit RMS: {candidate.fit_rms_px:.2f} px",
            "",
            "Frame detections:",
        ]
        for detection in candidate.frame_detections:
            position_text = f"x={detection.x:.2f}, y={detection.y:.2f}"
            if detection.ra_deg is not None and detection.dec_deg is not None:
                position_text += f" | RA={detection.ra_deg:.6f} deg, Dec={detection.dec_deg:.6f} deg"
            lines.append(f"F{detection.frame_index + 1} | {detection.source_path.name} | {detection.observation_time.isoformat()} | {position_text} | SNR={detection.local_snr:.2f}")
        return "\n".join(lines)

    def _export_benchmark_table(self) -> None:
        suggested_path = self._result.reference_path.with_name(f"{self._result.reference_path.stem}_recovery_benchmark.csv")
        output_path = self._choose_csv_export_path("Export Recovery Benchmark Table", suggested_path)
        if output_path is None:
            return
        rows = self._benchmark_export_rows()
        fieldnames = [
            "benchmark_status",
            "object_name",
            "designation",
            "object_type",
            "orbit_class",
            "predicted_magnitude",
            "confidence_score",
            "catalog_status",
            "likely_visible",
            "expected_frame_count",
            "matched_frame_count",
            "recovered_fraction",
            "match_rms_px",
            "max_match_offset_px",
            "reference_x",
            "reference_y",
            "predicted_ra_deg",
            "predicted_dec_deg",
            "predicted_motion_arcsec_per_hour",
            "expected_trail_length_px",
            "candidate_id",
            "candidate_average_snr",
            "candidate_fit_rms_px",
            "candidate_motion_px_per_hour",
            "candidate_motion_arcsec_per_hour",
            "candidate_displacement_px",
            "summary_text",
        ]
        self._write_csv_export(
            output_path=output_path,
            fieldnames=fieldnames,
            rows=rows,
            success_title="Recovery Benchmark Exported",
            failure_title="Export Benchmark failed",
            details_prefix="Exported recovery benchmark table",
        )

    def _export_unmatched_candidates_table(self) -> None:
        suggested_path = self._result.reference_path.with_name(f"{self._result.reference_path.stem}_recovery_unmatched.csv")
        output_path = self._choose_csv_export_path("Export Unmatched Recovery Candidates", suggested_path)
        if output_path is None:
            return
        rows = self._unmatched_export_rows()
        fieldnames = [
            "candidate_id",
            "observed_frame_count",
            "average_snr",
            "peak_value",
            "fit_rms_px",
            "motion_px_per_hour",
            "motion_arcsec_per_hour",
            "displacement_px",
            "start_x",
            "start_y",
            "end_x",
            "end_y",
            "first_detection_time_utc",
            "last_detection_time_utc",
            "frame_paths",
            "summary_text",
        ]
        self._write_csv_export(
            output_path=output_path,
            fieldnames=fieldnames,
            rows=rows,
            success_title="Unmatched Recovery Candidates Exported",
            failure_title="Export Unmatched failed",
            details_prefix="Exported unmatched recovery candidates",
        )

    def _export_summary_table(self) -> None:
        suggested_path = self._result.reference_path.with_name(f"{self._result.reference_path.stem}_recovery_summary.csv")
        output_path = self._choose_csv_export_path("Export Recovery Summary Table", suggested_path)
        if output_path is None:
            return
        rows = self._summary_export_rows()
        fieldnames = [
            "benchmark_status",
            "object_name",
            "designation",
            "predicted_magnitude",
            "confidence_score",
            "catalog_status",
            "matched_frame_count",
            "expected_frame_count",
            "recovered_fraction",
            "match_rms_px",
            "predicted_motion_arcsec_per_hour",
            "summary_text",
        ]
        self._write_csv_export(
            output_path=output_path,
            fieldnames=fieldnames,
            rows=rows,
            success_title="Recovery Summary Exported",
            failure_title="Export Summary failed",
            details_prefix="Exported recovery summary table",
        )

    def _choose_csv_export_path(self, title: str, suggested_path: Path) -> Path | None:
        selected, _selected_filter = QFileDialog.getSaveFileName(
            self,
            title,
            str(suggested_path),
            "CSV Files (*.csv)",
        )
        if not selected:
            return None
        output_path = Path(selected).expanduser()
        if output_path.suffix.lower() != ".csv":
            output_path = output_path.with_suffix(".csv")
        return output_path

    def _write_csv_export(
        self,
        *,
        output_path: Path,
        fieldnames: list[str],
        rows: list[dict[str, object]],
        success_title: str,
        failure_title: str,
        details_prefix: str,
    ) -> None:
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        except OSError as exc:
            QMessageBox.warning(self, failure_title, str(exc))
            return
        existing_text = self._details_output.toPlainText()
        prefix_text = f"{details_prefix} to {output_path}"
        self._details_output.setPlainText(prefix_text if not existing_text else f"{prefix_text}\n\n{existing_text}")
        QMessageBox.information(self, success_title, f"Saved {len(rows)} row(s) to\n{output_path}")

    def _benchmark_export_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for recovered in self._result.recovered_known_objects:
            rows.append(self._recovered_benchmark_export_row(recovered))
        for missed in self._result.missed_known_objects:
            rows.append(self._missed_benchmark_export_row(missed))
        return rows

    def _recovered_benchmark_export_row(self, recovered: RecoveredKnownMovingObject) -> dict[str, object]:
        detection = recovered.detection
        candidate = recovered.candidate
        recovered_fraction = (
            float(recovered.matched_frame_count) / float(recovered.expected_frame_count)
            if recovered.expected_frame_count > 0
            else None
        )
        return {
            "benchmark_status": "recovered",
            "object_name": detection.name or detection.designation or "Unknown",
            "designation": detection.designation or "",
            "object_type": detection.object_type,
            "orbit_class": detection.orbit_class,
            "predicted_magnitude": detection.predicted_magnitude,
            "confidence_score": detection.confidence_score,
            "catalog_status": detection.status,
            "likely_visible": detection.likely_visible,
            "expected_frame_count": recovered.expected_frame_count,
            "matched_frame_count": recovered.matched_frame_count,
            "recovered_fraction": recovered_fraction,
            "match_rms_px": recovered.match_rms_px,
            "max_match_offset_px": recovered.max_match_offset_px,
            "reference_x": recovered.reference_x,
            "reference_y": recovered.reference_y,
            "predicted_ra_deg": detection.predicted_ra_deg,
            "predicted_dec_deg": detection.predicted_dec_deg,
            "predicted_motion_arcsec_per_hour": detection.motion_rate_arcsec_per_hour,
            "expected_trail_length_px": detection.expected_trail_length_px,
            "candidate_id": candidate.candidate_id,
            "candidate_average_snr": candidate.average_snr,
            "candidate_fit_rms_px": candidate.fit_rms_px,
            "candidate_motion_px_per_hour": candidate.motion_px_per_hour,
            "candidate_motion_arcsec_per_hour": candidate.motion_arcsec_per_hour,
            "candidate_displacement_px": candidate.displacement_px,
            "summary_text": recovered.summary_text,
        }

    def _missed_benchmark_export_row(self, missed: MissedKnownMovingObject) -> dict[str, object]:
        detection = missed.detection
        return {
            "benchmark_status": "missed",
            "object_name": detection.name or detection.designation or "Unknown",
            "designation": detection.designation or "",
            "object_type": detection.object_type,
            "orbit_class": detection.orbit_class,
            "predicted_magnitude": detection.predicted_magnitude,
            "confidence_score": detection.confidence_score,
            "catalog_status": detection.status,
            "likely_visible": detection.likely_visible,
            "expected_frame_count": missed.expected_frame_count,
            "matched_frame_count": None,
            "recovered_fraction": 0.0,
            "match_rms_px": None,
            "max_match_offset_px": None,
            "reference_x": missed.reference_x,
            "reference_y": missed.reference_y,
            "predicted_ra_deg": detection.predicted_ra_deg,
            "predicted_dec_deg": detection.predicted_dec_deg,
            "predicted_motion_arcsec_per_hour": detection.motion_rate_arcsec_per_hour,
            "expected_trail_length_px": detection.expected_trail_length_px,
            "candidate_id": "",
            "candidate_average_snr": None,
            "candidate_fit_rms_px": None,
            "candidate_motion_px_per_hour": None,
            "candidate_motion_arcsec_per_hour": None,
            "candidate_displacement_px": None,
            "summary_text": missed.summary_text,
        }

    def _unmatched_export_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for candidate in self._result.unmatched_candidates:
            first_detection = candidate.frame_detections[0] if candidate.frame_detections else None
            last_detection = candidate.frame_detections[-1] if candidate.frame_detections else None
            rows.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "observed_frame_count": len(candidate.frame_detections),
                    "average_snr": candidate.average_snr,
                    "peak_value": candidate.peak_value,
                    "fit_rms_px": candidate.fit_rms_px,
                    "motion_px_per_hour": candidate.motion_px_per_hour,
                    "motion_arcsec_per_hour": candidate.motion_arcsec_per_hour,
                    "displacement_px": candidate.displacement_px,
                    "start_x": candidate.start_x,
                    "start_y": candidate.start_y,
                    "end_x": candidate.end_x,
                    "end_y": candidate.end_y,
                    "first_detection_time_utc": None if first_detection is None else first_detection.observation_time.isoformat(),
                    "last_detection_time_utc": None if last_detection is None else last_detection.observation_time.isoformat(),
                    "frame_paths": "; ".join(detection.source_path.name for detection in candidate.frame_detections),
                    "summary_text": candidate.summary_text,
                }
            )
        return rows

    def _summary_export_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for recovered in self._result.recovered_known_objects:
            rows.append(
                {
                    "benchmark_status": "recovered",
                    "object_name": recovered.detection.name or recovered.detection.designation or "Unknown",
                    "designation": recovered.detection.designation or "",
                    "predicted_magnitude": recovered.detection.predicted_magnitude,
                    "confidence_score": recovered.detection.confidence_score,
                    "catalog_status": recovered.detection.status,
                    "matched_frame_count": recovered.matched_frame_count,
                    "expected_frame_count": recovered.expected_frame_count,
                    "recovered_fraction": (
                        float(recovered.matched_frame_count) / float(recovered.expected_frame_count)
                        if recovered.expected_frame_count > 0
                        else None
                    ),
                    "match_rms_px": recovered.match_rms_px,
                    "predicted_motion_arcsec_per_hour": recovered.detection.motion_rate_arcsec_per_hour,
                    "summary_text": recovered.summary_text,
                }
            )
        for missed in self._result.missed_known_objects:
            rows.append(
                {
                    "benchmark_status": "missed",
                    "object_name": missed.detection.name or missed.detection.designation or "Unknown",
                    "designation": missed.detection.designation or "",
                    "predicted_magnitude": missed.detection.predicted_magnitude,
                    "confidence_score": missed.detection.confidence_score,
                    "catalog_status": missed.detection.status,
                    "matched_frame_count": None,
                    "expected_frame_count": missed.expected_frame_count,
                    "recovered_fraction": 0.0,
                    "match_rms_px": None,
                    "predicted_motion_arcsec_per_hour": missed.detection.motion_rate_arcsec_per_hour,
                    "summary_text": missed.summary_text,
                }
            )
        return rows


class SettingsDialog(QDialog):
    def __init__(self, root_path: Path, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(820, 660)
        self._root_path = root_path
        self._settings = settings
        self._theme = settings.theme
        self._custom_theme_colors = dict(settings.custom_theme_colors or default_custom_theme_colors())
        self._default_settings = AppSettings.defaults(root_path)
        self._default_config_path = default_settings_config_path()

        self._api_key_input = QLineEdit(settings.astrometry_api_key or "")
        self._api_key_input.setPlaceholderText("Astrometry.net API key")
        self._api_key_input.setEchoMode(QLineEdit.EchoMode.Password)

        self._use_default_settings_location_input = QCheckBox("Use default AppData settings location")
        self._use_default_settings_location_input.setChecked(self._is_default_settings_location(settings.config_path))
        self._use_default_settings_location_input.stateChanged.connect(self._update_settings_location_inputs)
        self._settings_location_input = QLineEdit(str(settings.config_path))
        self._settings_location_input.setPlaceholderText(str(self._default_config_path))
        self._settings_location_browse_button = QPushButton("Browse")
        self._settings_location_browse_button.clicked.connect(self._browse_settings_location)

        self._cache_dir_input = QLineEdit(str(settings.cache_dir))
        self._nearby_reference_count_input = QSpinBox()
        self._nearby_reference_count_input.setRange(1, 25)
        self._nearby_reference_count_input.setValue(settings.nearby_reference_count)
        self._shared_parallel_workers_input = QSpinBox()
        self._shared_parallel_workers_input.setRange(0, 32)
        self._shared_parallel_workers_input.setSpecialValueText("Auto")
        self._shared_parallel_workers_input.setValue(resolve_shared_parallel_workers(settings))
        self._sky_atlas_custom_overlay_cache_max_long_edge_input = QSpinBox()
        self._sky_atlas_custom_overlay_cache_max_long_edge_input.setRange(512, 8192)
        self._sky_atlas_custom_overlay_cache_max_long_edge_input.setSingleStep(256)
        self._sky_atlas_custom_overlay_cache_max_long_edge_input.setSuffix(" px")
        self._sky_atlas_custom_overlay_cache_max_long_edge_input.setValue(
            int(settings.sky_atlas_custom_overlay_cache_max_long_edge)
        )
        self._snr_binning_max_period_fraction_input = QDoubleSpinBox()
        self._snr_binning_max_period_fraction_input.setDecimals(3)
        self._snr_binning_max_period_fraction_input.setRange(0.001, 0.5)
        self._snr_binning_max_period_fraction_input.setSingleStep(0.005)
        self._snr_binning_max_period_fraction_input.setValue(settings.snr_binning_max_period_fraction)
        self._snr_binning_max_absolute_duration_seconds_input = QSpinBox()
        self._snr_binning_max_absolute_duration_seconds_input.setRange(1, 86400)
        self._snr_binning_max_absolute_duration_seconds_input.setSingleStep(30)
        self._snr_binning_max_absolute_duration_seconds_input.setSuffix(" s")
        self._snr_binning_max_absolute_duration_seconds_input.setValue(int(round(settings.snr_binning_max_absolute_duration_seconds)))
        self._snr_binning_target_snr_input = QDoubleSpinBox()
        self._snr_binning_target_snr_input.setDecimals(1)
        self._snr_binning_target_snr_input.setRange(1.0, 1000.0)
        self._snr_binning_target_snr_input.setSingleStep(1.0)
        self._snr_binning_target_snr_input.setValue(settings.snr_binning_target_snr)
        self._snr_binning_max_frames_per_bin_input = QSpinBox()
        self._snr_binning_max_frames_per_bin_input.setRange(1, 500)
        self._snr_binning_max_frames_per_bin_input.setValue(settings.snr_binning_max_frames_per_bin)
        self._snr_binning_min_frames_per_bin_input = QSpinBox()
        self._snr_binning_min_frames_per_bin_input.setRange(1, 500)
        self._snr_binning_min_frames_per_bin_input.setValue(settings.snr_binning_min_frames_per_bin)
        self._snr_binning_type_aware_thresholds_input = QCheckBox("Adjust period-fraction limits by variability type")
        self._snr_binning_type_aware_thresholds_input.setChecked(settings.snr_binning_type_aware_thresholds)
        self._snr_binning_type_aware_thresholds_input.stateChanged.connect(self._update_snr_binning_inputs)
        self._snr_binning_sharp_period_fraction_input = QDoubleSpinBox()
        self._snr_binning_sharp_period_fraction_input.setDecimals(3)
        self._snr_binning_sharp_period_fraction_input.setRange(0.001, 0.5)
        self._snr_binning_sharp_period_fraction_input.setSingleStep(0.005)
        self._snr_binning_sharp_period_fraction_input.setValue(settings.snr_binning_sharp_period_fraction)
        self._snr_binning_smooth_period_fraction_input = QDoubleSpinBox()
        self._snr_binning_smooth_period_fraction_input.setDecimals(3)
        self._snr_binning_smooth_period_fraction_input.setRange(0.001, 0.5)
        self._snr_binning_smooth_period_fraction_input.setSingleStep(0.005)
        self._snr_binning_smooth_period_fraction_input.setValue(settings.snr_binning_smooth_period_fraction)
        self._snr_binning_weighted_flux_binning_input = QCheckBox("Prefer weighted flux-space binning")
        self._snr_binning_weighted_flux_binning_input.setChecked(settings.snr_binning_weighted_flux_binning)
        self._snr_binning_allow_magnitude_fallback_input = QCheckBox("Allow direct magnitude averaging fallback")
        self._snr_binning_allow_magnitude_fallback_input.setChecked(settings.snr_binning_allow_magnitude_fallback)
        self._snr_binning_minimum_valid_points_per_bin_input = QSpinBox()
        self._snr_binning_minimum_valid_points_per_bin_input.setRange(1, 100)
        self._snr_binning_minimum_valid_points_per_bin_input.setValue(settings.snr_binning_minimum_valid_points_per_bin)
        self._snr_binning_outlier_rejection_enabled_input = QCheckBox("Enable sigma-clipping inside each bin")
        self._snr_binning_outlier_rejection_enabled_input.setChecked(settings.snr_binning_outlier_rejection_enabled)
        self._snr_binning_outlier_rejection_enabled_input.stateChanged.connect(self._update_snr_binning_inputs)
        self._snr_binning_sigma_clip_threshold_input = QDoubleSpinBox()
        self._snr_binning_sigma_clip_threshold_input.setDecimals(1)
        self._snr_binning_sigma_clip_threshold_input.setRange(1.0, 10.0)
        self._snr_binning_sigma_clip_threshold_input.setSingleStep(0.1)
        self._snr_binning_sigma_clip_threshold_input.setSuffix(" sigma")
        self._snr_binning_sigma_clip_threshold_input.setValue(settings.snr_binning_sigma_clip_threshold)
        self._snr_binning_dataset_mode_input = QComboBox()
        self._snr_binning_dataset_mode_input.addItem("Create derived dataset", "derived")
        self._snr_binning_dataset_mode_input.addItem("Replace processed view", "replace")
        selected_dataset_mode_index = self._snr_binning_dataset_mode_input.findData(settings.snr_binning_dataset_mode)
        if selected_dataset_mode_index >= 0:
            self._snr_binning_dataset_mode_input.setCurrentIndex(selected_dataset_mode_index)
        self._snr_binning_apply_to_selected_measurements_only_input = QCheckBox("Use only currently filtered measurements for each source")
        self._snr_binning_apply_to_selected_measurements_only_input.setChecked(settings.snr_binning_apply_to_selected_measurements_only)
        self._snr_binning_allow_periodless_fallback_input = QCheckBox("Allow fallback binning when no usable period is available")
        self._snr_binning_allow_periodless_fallback_input.setChecked(settings.snr_binning_allow_periodless_fallback)
        self._comparison_fit_stop_match_index_input = QDoubleSpinBox()
        self._comparison_fit_stop_match_index_input.setDecimals(1)
        self._comparison_fit_stop_match_index_input.setRange(0.0, 100.0)
        self._comparison_fit_stop_match_index_input.setSingleStep(1.0)
        self._comparison_fit_stop_match_index_input.setSpecialValueText("Disabled")
        self._comparison_fit_stop_match_index_input.setSuffix(" match")
        self._comparison_fit_stop_match_index_input.setValue(settings.comparison_fit_stop_match_index)
        self._comparison_fit_parallel_workers_input = QSpinBox()
        self._comparison_fit_parallel_workers_input.setRange(0, 32)
        self._comparison_fit_parallel_workers_input.setSpecialValueText("Auto")
        self._comparison_fit_parallel_workers_input.setValue(max(0, settings.comparison_fit_parallel_workers))
        self._sky_explorer_simbad_search_radius_arcsec_input = QDoubleSpinBox()
        self._sky_explorer_simbad_search_radius_arcsec_input.setDecimals(1)
        self._sky_explorer_simbad_search_radius_arcsec_input.setRange(1.0, 300.0)
        self._sky_explorer_simbad_search_radius_arcsec_input.setSingleStep(1.0)
        self._sky_explorer_simbad_search_radius_arcsec_input.setSuffix(" arcsec")
        self._sky_explorer_simbad_search_radius_arcsec_input.setValue(settings.sky_explorer_simbad_search_radius_arcsec)
        self._sky_explorer_gaia_max_magnitude_input = QDoubleSpinBox()
        self._sky_explorer_gaia_max_magnitude_input.setDecimals(1)
        self._sky_explorer_gaia_max_magnitude_input.setRange(-5.0, 30.0)
        self._sky_explorer_gaia_max_magnitude_input.setSingleStep(0.5)
        self._sky_explorer_gaia_max_magnitude_input.setSuffix(" mag")
        self._sky_explorer_gaia_max_magnitude_input.setValue(float(settings.sky_explorer_gaia_max_magnitude))
        self._sky_explorer_gaia_hard_cap_enabled_input = QCheckBox("Enable Gaia hard row cap")
        self._sky_explorer_gaia_hard_cap_enabled_input.setChecked(bool(settings.sky_explorer_gaia_hard_cap_enabled))
        self._sky_explorer_gaia_hard_cap_enabled_input.stateChanged.connect(self._update_sky_explorer_gaia_inputs)
        self._sky_explorer_gaia_hard_cap_rows_input = QSpinBox()
        self._sky_explorer_gaia_hard_cap_rows_input.setRange(1, 50000)
        self._sky_explorer_gaia_hard_cap_rows_input.setSingleStep(100)
        self._sky_explorer_gaia_hard_cap_rows_input.setSuffix(" rows")
        self._sky_explorer_gaia_hard_cap_rows_input.setValue(max(1, int(settings.sky_explorer_gaia_hard_cap_rows)))
        self._sky_explorer_mag_limit_examples_per_bin_input = QSpinBox()
        self._sky_explorer_mag_limit_examples_per_bin_input.setRange(1, 10)
        self._sky_explorer_mag_limit_examples_per_bin_input.setSuffix(" stars/bin")
        self._sky_explorer_mag_limit_examples_per_bin_input.setValue(
            max(1, min(10, int(getattr(settings, "sky_explorer_mag_limit_examples_per_bin", 1))))
        )
        self._sky_explorer_mag_limit_marker_color = _coerce_hex_color(
            getattr(settings, "sky_explorer_mag_limit_marker_color", "#3d8bfd"),
            default="#3d8bfd",
        )
        self._sky_explorer_mag_limit_text_color = _coerce_hex_color(
            getattr(settings, "sky_explorer_mag_limit_text_color", "#111827"),
            default="#111827",
        )
        self._sky_explorer_mag_limit_marker_color_button = QPushButton("Marker...")
        self._sky_explorer_mag_limit_marker_color_button.clicked.connect(self._choose_sky_explorer_mag_limit_marker_color)
        self._sky_explorer_mag_limit_text_color_button = QPushButton("Text...")
        self._sky_explorer_mag_limit_text_color_button.clicked.connect(self._choose_sky_explorer_mag_limit_text_color)
        self._sky_explorer_mag_limit_marker_stroke_color = _coerce_hex_color(
            getattr(settings, "sky_explorer_mag_limit_marker_stroke_color", "#111827"),
            default="#111827",
        )
        self._sky_explorer_mag_limit_text_stroke_color = _coerce_hex_color(
            getattr(settings, "sky_explorer_mag_limit_text_stroke_color", "#ffffff"),
            default="#ffffff",
        )
        self._sky_explorer_mag_limit_marker_stroke_color_button = QPushButton("Stroke...")
        self._sky_explorer_mag_limit_marker_stroke_color_button.clicked.connect(self._choose_sky_explorer_mag_limit_marker_stroke_color)
        self._sky_explorer_mag_limit_text_stroke_color_button = QPushButton("Stroke...")
        self._sky_explorer_mag_limit_text_stroke_color_button.clicked.connect(self._choose_sky_explorer_mag_limit_text_stroke_color)
        self._sky_explorer_mag_limit_target_size_input = QDoubleSpinBox()
        self._sky_explorer_mag_limit_target_size_input.setDecimals(1)
        self._sky_explorer_mag_limit_target_size_input.setRange(2.0, 40.0)
        self._sky_explorer_mag_limit_target_size_input.setSingleStep(0.5)
        self._sky_explorer_mag_limit_target_size_input.setSuffix(" px")
        self._sky_explorer_mag_limit_target_size_input.setValue(float(getattr(settings, "sky_explorer_mag_limit_target_size", 6.0)))
        self._sky_explorer_mag_limit_text_size_input = QDoubleSpinBox()
        self._sky_explorer_mag_limit_text_size_input.setDecimals(1)
        self._sky_explorer_mag_limit_text_size_input.setRange(7.0, 24.0)
        self._sky_explorer_mag_limit_text_size_input.setSingleStep(0.5)
        self._sky_explorer_mag_limit_text_size_input.setSuffix(" pt")
        self._sky_explorer_mag_limit_text_size_input.setValue(float(getattr(settings, "sky_explorer_mag_limit_text_size", 9.0)))
        self._sky_explorer_mag_limit_marker_stroke_width_input = QDoubleSpinBox()
        self._sky_explorer_mag_limit_marker_stroke_width_input.setDecimals(2)
        self._sky_explorer_mag_limit_marker_stroke_width_input.setRange(0.0, 8.0)
        self._sky_explorer_mag_limit_marker_stroke_width_input.setSingleStep(0.25)
        self._sky_explorer_mag_limit_marker_stroke_width_input.setSuffix(" px")
        self._sky_explorer_mag_limit_marker_stroke_width_input.setValue(float(getattr(settings, "sky_explorer_mag_limit_marker_stroke_width", 2.0)))
        self._sky_explorer_mag_limit_text_stroke_width_input = QDoubleSpinBox()
        self._sky_explorer_mag_limit_text_stroke_width_input.setDecimals(2)
        self._sky_explorer_mag_limit_text_stroke_width_input.setRange(0.0, 6.0)
        self._sky_explorer_mag_limit_text_stroke_width_input.setSingleStep(0.25)
        self._sky_explorer_mag_limit_text_stroke_width_input.setSuffix(" px")
        self._sky_explorer_mag_limit_text_stroke_width_input.setValue(float(getattr(settings, "sky_explorer_mag_limit_text_stroke_width", 0.0)))
        self._update_sky_explorer_mag_limit_marker_color_button()
        self._update_sky_explorer_mag_limit_text_color_button()
        self._update_sky_explorer_mag_limit_marker_stroke_color_button()
        self._update_sky_explorer_mag_limit_text_stroke_color_button()
        self._sky_explorer_annotated_galaxy_max_magnitude_enabled_input = QCheckBox("Limit galaxy annotations by magnitude")
        self._sky_explorer_annotated_galaxy_max_magnitude_enabled_input.setChecked(bool(getattr(settings, "sky_explorer_annotated_galaxy_max_magnitude_enabled", False)))
        self._sky_explorer_annotated_galaxy_max_magnitude_enabled_input.stateChanged.connect(self._update_sky_explorer_galaxy_annotation_inputs)
        self._sky_explorer_annotated_galaxy_max_magnitude_input = QDoubleSpinBox()
        self._sky_explorer_annotated_galaxy_max_magnitude_input.setDecimals(1)
        self._sky_explorer_annotated_galaxy_max_magnitude_input.setRange(-5.0, 30.0)
        self._sky_explorer_annotated_galaxy_max_magnitude_input.setSingleStep(0.5)
        self._sky_explorer_annotated_galaxy_max_magnitude_input.setSuffix(" mag")
        self._sky_explorer_annotated_galaxy_max_magnitude_input.setValue(float(getattr(settings, "sky_explorer_annotated_galaxy_max_magnitude", 17.0)))
        self._sky_explorer_annotated_galaxy_require_shape_metadata_input = QCheckBox("Only annotate galaxies with shape metadata")
        self._sky_explorer_annotated_galaxy_require_shape_metadata_input.setChecked(bool(getattr(settings, "sky_explorer_annotated_galaxy_require_shape_metadata", False)))
        self._sky_explorer_scale_extended_nebulae_input = QCheckBox("Scale extended nebula overlays")
        self._sky_explorer_scale_extended_nebulae_input.setChecked(bool(settings.sky_explorer_scale_extended_nebulae))
        self._sky_explorer_scale_overlay_strokes_input = QCheckBox("Scale overlay stroke width")
        self._sky_explorer_scale_overlay_strokes_input.setChecked(bool(settings.sky_explorer_scale_overlay_strokes))
        self._sky_explorer_marker_color_relation_input = QComboBox()
        self._sky_explorer_marker_color_relation_input.addItem("Fill bright, stroke dark", "stroke_dark_fill_bright")
        self._sky_explorer_marker_color_relation_input.addItem("Fill dark, stroke bright", "stroke_bright_fill_dark")
        self._set_combo_data(
            self._sky_explorer_marker_color_relation_input,
            getattr(settings, "sky_explorer_marker_color_relation", "stroke_dark_fill_bright"),
        )
        self._sky_explorer_text_color_relation_input = QComboBox()
        self._sky_explorer_text_color_relation_input.addItem("Dark", "dark")
        self._sky_explorer_text_color_relation_input.addItem("Bright", "bright")
        self._set_combo_data(
            self._sky_explorer_text_color_relation_input,
            getattr(settings, "sky_explorer_text_color_relation", "dark"),
        )
        self._sky_explorer_fill_opacity_input = QDoubleSpinBox()
        self._sky_explorer_fill_opacity_input.setDecimals(2)
        self._sky_explorer_fill_opacity_input.setRange(0.0, 1.0)
        self._sky_explorer_fill_opacity_input.setSingleStep(0.05)
        self._sky_explorer_fill_opacity_input.setValue(max(0.0, min(1.0, float(settings.sky_explorer_fill_opacity))))
        self._sky_explorer_stroke_opacity_input = QDoubleSpinBox()
        self._sky_explorer_stroke_opacity_input.setDecimals(2)
        self._sky_explorer_stroke_opacity_input.setRange(0.0, 1.0)
        self._sky_explorer_stroke_opacity_input.setSingleStep(0.05)
        self._sky_explorer_stroke_opacity_input.setValue(max(0.0, min(1.0, float(settings.sky_explorer_stroke_opacity))))
        self._sky_explorer_object_group_default_colors = {
            group_key: default_color
            for group_key, _group_title, default_color in sky_explorer_object_type_group_definitions()
        }
        self._sky_explorer_object_group_color_overrides: dict[str, str] = {
            str(group_key).strip(): str(color).strip().lower()
            for group_key, color in (settings.sky_explorer_object_group_color_overrides or {}).items()
            if str(group_key).strip() and str(color).strip()
        }
        self._sky_explorer_object_group_color_buttons: dict[str, QPushButton] = {}
        for group_key, _group_title, _default_color in sky_explorer_object_type_group_definitions():
            button = QPushButton()
            button.clicked.connect(lambda _checked=False, key=group_key: self._pick_sky_explorer_object_group_color(key))
            self._sky_explorer_object_group_color_buttons[group_key] = button
            self._update_sky_explorer_object_group_color_button(group_key)
        enabled_sky_explorer_layers = {str(layer).strip().lower() for layer in getattr(settings, "sky_explorer_enabled_layers", ())}
        self._sky_explorer_layer_inputs: dict[str, QCheckBox] = {}
        for layer_key, label_text, tooltip in _SKY_EXPLORER_SETTINGS_LAYER_FIELDS:
            checkbox = QCheckBox(label_text)
            checkbox.setChecked(layer_key in enabled_sky_explorer_layers)
            checkbox.setToolTip(tooltip)
            self._sky_explorer_layer_inputs[layer_key] = checkbox
        self._update_sky_explorer_gaia_inputs()
        self._update_sky_explorer_galaxy_annotation_inputs()
        self._asteroid_search_parallel_workers_input = QSpinBox()
        self._asteroid_search_parallel_workers_input.setRange(0, 32)
        self._asteroid_search_parallel_workers_input.setSpecialValueText("Auto")
        self._asteroid_search_parallel_workers_input.setValue(max(0, settings.asteroid_search_parallel_workers))
        self._asteroid_discovery_min_residual_snr_input = QDoubleSpinBox()
        self._asteroid_discovery_min_residual_snr_input.setDecimals(1)
        self._asteroid_discovery_min_residual_snr_input.setRange(0.0, 500.0)
        self._asteroid_discovery_min_residual_snr_input.setSingleStep(0.5)
        self._asteroid_discovery_min_residual_snr_input.setSpecialValueText("Disabled")
        self._asteroid_discovery_min_residual_snr_input.setSuffix(" SNR")
        self._asteroid_discovery_min_residual_snr_input.setValue(max(0.0, settings.asteroid_discovery_min_residual_snr))
        self._asteroid_discovery_max_residual_snr_input = QDoubleSpinBox()
        self._asteroid_discovery_max_residual_snr_input.setDecimals(1)
        self._asteroid_discovery_max_residual_snr_input.setRange(0.0, 500.0)
        self._asteroid_discovery_max_residual_snr_input.setSingleStep(0.5)
        self._asteroid_discovery_max_residual_snr_input.setSpecialValueText("Disabled")
        self._asteroid_discovery_max_residual_snr_input.setSuffix(" SNR")
        self._asteroid_discovery_max_residual_snr_input.setValue(max(0.0, settings.asteroid_discovery_max_residual_snr))
        self._asteroid_discovery_frames_per_batch_input = QSpinBox()
        self._asteroid_discovery_frames_per_batch_input.setRange(0, 500)
        self._asteroid_discovery_frames_per_batch_input.setSpecialValueText("Whole group")
        self._asteroid_discovery_frames_per_batch_input.setSuffix(" frames")
        self._asteroid_discovery_frames_per_batch_input.setValue(max(0, settings.asteroid_discovery_frames_per_batch))
        self._asteroid_discovery_binning_factor_input = QComboBox()
        self._asteroid_discovery_binning_factor_input.addItem("Off (1x1)", 1)
        self._asteroid_discovery_binning_factor_input.addItem("2x2", 2)
        self._asteroid_discovery_binning_factor_input.addItem("3x3", 3)
        self._asteroid_discovery_binning_factor_input.addItem("4x4", 4)
        self._set_combo_data(self._asteroid_discovery_binning_factor_input, settings.asteroid_discovery_binning_factor)
        self._asteroid_discovery_use_temporary_cache_input = QCheckBox("Use temporary prepared-frame cache during Discover")
        self._asteroid_discovery_use_temporary_cache_input.setChecked(settings.asteroid_discovery_use_temporary_cache)
        self._asteroid_discovery_min_candidate_frames_input = QSpinBox()
        self._asteroid_discovery_min_candidate_frames_input.setRange(2, 32)
        self._asteroid_discovery_min_candidate_frames_input.setSuffix(" frames")
        self._asteroid_discovery_min_candidate_frames_input.setValue(max(2, int(settings.asteroid_discovery_min_candidate_frames)))
        self._asteroid_discovery_detection_sigma_input = QDoubleSpinBox()
        self._asteroid_discovery_detection_sigma_input.setDecimals(1)
        self._asteroid_discovery_detection_sigma_input.setRange(0.5, 100.0)
        self._asteroid_discovery_detection_sigma_input.setSingleStep(0.5)
        self._asteroid_discovery_detection_sigma_input.setSuffix(" sigma")
        self._asteroid_discovery_detection_sigma_input.setValue(max(0.5, float(settings.asteroid_discovery_detection_sigma)))
        self._asteroid_discovery_detection_fwhm_input = QDoubleSpinBox()
        self._asteroid_discovery_detection_fwhm_input.setDecimals(1)
        self._asteroid_discovery_detection_fwhm_input.setRange(0.8, 20.0)
        self._asteroid_discovery_detection_fwhm_input.setSingleStep(0.2)
        self._asteroid_discovery_detection_fwhm_input.setSuffix(" px")
        self._asteroid_discovery_detection_fwhm_input.setValue(max(0.8, float(settings.asteroid_discovery_detection_fwhm)))
        self._asteroid_discovery_max_residuals_per_frame_input = QSpinBox()
        self._asteroid_discovery_max_residuals_per_frame_input.setRange(1, 500)
        self._asteroid_discovery_max_residuals_per_frame_input.setSuffix(" detections")
        self._asteroid_discovery_max_residuals_per_frame_input.setValue(max(1, int(settings.asteroid_discovery_max_residuals_per_frame)))
        self._asteroid_discovery_edge_margin_px_input = QSpinBox()
        self._asteroid_discovery_edge_margin_px_input.setRange(0, 512)
        self._asteroid_discovery_edge_margin_px_input.setSuffix(" px")
        self._asteroid_discovery_edge_margin_px_input.setValue(max(0, int(settings.asteroid_discovery_edge_margin_px)))
        self._asteroid_discovery_detector_mode_input = QComboBox()
        self._asteroid_discovery_detector_mode_input.addItem("Hybrid (point + streak)", "hybrid")
        self._asteroid_discovery_detector_mode_input.addItem("Point-like only", "point")
        self._asteroid_discovery_detector_mode_input.addItem("Streak-aware only", "streak")
        self._set_combo_data(self._asteroid_discovery_detector_mode_input, str(settings.asteroid_discovery_detector_mode or "hybrid").strip().lower())
        self._asteroid_discovery_streak_min_area_px_input = QSpinBox()
        self._asteroid_discovery_streak_min_area_px_input.setRange(2, 4096)
        self._asteroid_discovery_streak_min_area_px_input.setSuffix(" px")
        self._asteroid_discovery_streak_min_area_px_input.setValue(max(2, int(settings.asteroid_discovery_streak_min_area_px)))
        self._asteroid_discovery_streak_min_elongation_input = QDoubleSpinBox()
        self._asteroid_discovery_streak_min_elongation_input.setDecimals(1)
        self._asteroid_discovery_streak_min_elongation_input.setRange(1.0, 50.0)
        self._asteroid_discovery_streak_min_elongation_input.setSingleStep(0.1)
        self._asteroid_discovery_streak_min_elongation_input.setSuffix(" x")
        self._asteroid_discovery_streak_min_elongation_input.setValue(max(1.0, float(settings.asteroid_discovery_streak_min_elongation)))
        self._asteroid_discovery_potential_deflection_rms_input = QDoubleSpinBox()
        self._asteroid_discovery_potential_deflection_rms_input.setDecimals(2)
        self._asteroid_discovery_potential_deflection_rms_input.setRange(0.1, 20.0)
        self._asteroid_discovery_potential_deflection_rms_input.setSingleStep(0.1)
        self._asteroid_discovery_potential_deflection_rms_input.setSuffix(" px RMS")
        self._asteroid_discovery_potential_deflection_rms_input.setValue(max(0.1, float(settings.asteroid_discovery_potential_deflection_rms_px)))
        self._asteroid_discovery_review_deflection_rms_input = QDoubleSpinBox()
        self._asteroid_discovery_review_deflection_rms_input.setDecimals(2)
        self._asteroid_discovery_review_deflection_rms_input.setRange(0.1, 20.0)
        self._asteroid_discovery_review_deflection_rms_input.setSingleStep(0.1)
        self._asteroid_discovery_review_deflection_rms_input.setSuffix(" px RMS")
        self._asteroid_discovery_review_deflection_rms_input.setValue(max(float(settings.asteroid_discovery_potential_deflection_rms_px), float(settings.asteroid_discovery_review_deflection_rms_px)))
        self._asteroid_discovery_enable_synthetic_sweep_input = QCheckBox("Run a final synthetic-track sweep on each discovery search window")
        self._asteroid_discovery_enable_synthetic_sweep_input.setChecked(settings.asteroid_discovery_enable_synthetic_sweep)
        self._asteroid_discovery_synthetic_sweep_max_motion_px_per_hour_input = QDoubleSpinBox()
        self._asteroid_discovery_synthetic_sweep_max_motion_px_per_hour_input.setDecimals(1)
        self._asteroid_discovery_synthetic_sweep_max_motion_px_per_hour_input.setRange(0.1, 500.0)
        self._asteroid_discovery_synthetic_sweep_max_motion_px_per_hour_input.setSingleStep(0.5)
        self._asteroid_discovery_synthetic_sweep_max_motion_px_per_hour_input.setSuffix(" px/h")
        self._asteroid_discovery_synthetic_sweep_max_motion_px_per_hour_input.setValue(max(0.1, float(settings.asteroid_discovery_synthetic_sweep_max_motion_px_per_hour)))
        self._asteroid_discovery_synthetic_sweep_motion_step_px_per_hour_input = QDoubleSpinBox()
        self._asteroid_discovery_synthetic_sweep_motion_step_px_per_hour_input.setDecimals(1)
        self._asteroid_discovery_synthetic_sweep_motion_step_px_per_hour_input.setRange(0.1, 100.0)
        self._asteroid_discovery_synthetic_sweep_motion_step_px_per_hour_input.setSingleStep(0.1)
        self._asteroid_discovery_synthetic_sweep_motion_step_px_per_hour_input.setSuffix(" px/h")
        self._asteroid_discovery_synthetic_sweep_motion_step_px_per_hour_input.setValue(max(0.1, float(settings.asteroid_discovery_synthetic_sweep_motion_step_px_per_hour)))
        self._asteroid_discovery_synthetic_sweep_angle_step_deg_input = QDoubleSpinBox()
        self._asteroid_discovery_synthetic_sweep_angle_step_deg_input.setDecimals(1)
        self._asteroid_discovery_synthetic_sweep_angle_step_deg_input.setRange(1.0, 180.0)
        self._asteroid_discovery_synthetic_sweep_angle_step_deg_input.setSingleStep(1.0)
        self._asteroid_discovery_synthetic_sweep_angle_step_deg_input.setSuffix(" deg")
        self._asteroid_discovery_synthetic_sweep_angle_step_deg_input.setValue(max(1.0, float(settings.asteroid_discovery_synthetic_sweep_angle_step_deg)))
        self._asteroid_discovery_synthetic_sweep_direction_focus_input = QComboBox()
        self._asteroid_discovery_synthetic_sweep_direction_focus_input.addItem("All directions (360 deg)", "all_directions")
        self._asteroid_discovery_synthetic_sweep_direction_focus_input.addItem("Main-belt direction focus", "main_belt")
        self._set_combo_data(
            self._asteroid_discovery_synthetic_sweep_direction_focus_input,
            str(settings.asteroid_discovery_synthetic_sweep_direction_focus or "all_directions").strip().lower(),
        )
        self._asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg_input = QDoubleSpinBox()
        self._asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg_input.setDecimals(1)
        self._asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg_input.setRange(1.0, 180.0)
        self._asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg_input.setSingleStep(1.0)
        self._asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg_input.setSuffix(" deg")
        self._asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg_input.setValue(max(1.0, float(settings.asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg)))
        self._asteroid_discovery_synthetic_sweep_min_stacked_snr_input = QDoubleSpinBox()
        self._asteroid_discovery_synthetic_sweep_min_stacked_snr_input.setDecimals(1)
        self._asteroid_discovery_synthetic_sweep_min_stacked_snr_input.setRange(0.5, 500.0)
        self._asteroid_discovery_synthetic_sweep_min_stacked_snr_input.setSingleStep(0.5)
        self._asteroid_discovery_synthetic_sweep_min_stacked_snr_input.setSuffix(" SNR")
        self._asteroid_discovery_synthetic_sweep_min_stacked_snr_input.setValue(max(0.5, float(settings.asteroid_discovery_synthetic_sweep_min_stacked_snr)))
        self._asteroid_discovery_synthetic_sweep_save_stacks_input = QCheckBox("Save every sweep stack to the current data folder")
        self._asteroid_discovery_synthetic_sweep_save_stacks_input.setChecked(bool(settings.asteroid_discovery_synthetic_sweep_save_stacks))
        self._comparison_fit_allow_multiple_targets_input = QCheckBox("Allow multiple selected Source Results targets")
        self._comparison_fit_allow_multiple_targets_input.setChecked(settings.comparison_fit_allow_multiple_targets)
        self._comparison_fit_eclipsing_binary_match_tolerance_input = QDoubleSpinBox()
        self._comparison_fit_eclipsing_binary_match_tolerance_input.setDecimals(1)
        self._comparison_fit_eclipsing_binary_match_tolerance_input.setRange(0.0, 50.0)
        self._comparison_fit_eclipsing_binary_match_tolerance_input.setSingleStep(0.5)
        self._comparison_fit_eclipsing_binary_match_tolerance_input.setSpecialValueText("Disabled")
        self._comparison_fit_eclipsing_binary_match_tolerance_input.setSuffix(" match")
        self._comparison_fit_eclipsing_binary_match_tolerance_input.setValue(settings.comparison_fit_eclipsing_binary_match_tolerance)
        self._comparison_fit_fallback_candidate_pool_size_input = QSpinBox()
        self._comparison_fit_fallback_candidate_pool_size_input.setRange(0, 50)
        self._comparison_fit_fallback_candidate_pool_size_input.setSpecialValueText("Disabled")
        self._comparison_fit_fallback_candidate_pool_size_input.setValue(max(0, settings.comparison_fit_fallback_candidate_pool_size))
        self._comparison_fit_fallback_magnitude_tolerance_input = QDoubleSpinBox()
        self._comparison_fit_fallback_magnitude_tolerance_input.setDecimals(2)
        self._comparison_fit_fallback_magnitude_tolerance_input.setRange(0.0, 10.0)
        self._comparison_fit_fallback_magnitude_tolerance_input.setSingleStep(0.1)
        self._comparison_fit_fallback_magnitude_tolerance_input.setSpecialValueText("Disabled")
        self._comparison_fit_fallback_magnitude_tolerance_input.setSuffix(" mag")
        self._comparison_fit_fallback_magnitude_tolerance_input.setValue(settings.comparison_fit_fallback_magnitude_tolerance)
        self._scientific_light_curve_pdf_dpi_input = QSpinBox()
        self._scientific_light_curve_pdf_dpi_input.setRange(72, 1200)
        self._scientific_light_curve_pdf_dpi_input.setSingleStep(25)
        self._scientific_light_curve_pdf_dpi_input.setSuffix(" dpi")
        self._scientific_light_curve_pdf_dpi_input.setValue(max(72, int(settings.scientific_light_curve_pdf_dpi)))
        self._scientific_light_curve_pdf_paper_size_input = QComboBox()
        self._scientific_light_curve_pdf_paper_size_input.addItem("Letter", "Letter")
        self._scientific_light_curve_pdf_paper_size_input.addItem("A4", "A4")
        self._scientific_light_curve_pdf_paper_size_input.addItem("Legal", "Legal")
        selected_paper_index = self._scientific_light_curve_pdf_paper_size_input.findData(settings.scientific_light_curve_pdf_paper_size)
        if selected_paper_index >= 0:
            self._scientific_light_curve_pdf_paper_size_input.setCurrentIndex(selected_paper_index)
        self._hr_max_sources_input = QSpinBox()
        self._hr_max_sources_input.setRange(0, 50000)
        self._hr_max_sources_input.setSingleStep(500)
        self._hr_max_sources_input.setSpecialValueText("All matched sources")
        self._hr_max_sources_input.setValue(max(0, int(settings.hr_max_sources)))
        self._hr_table_row_limit_input = QSpinBox()
        self._hr_table_row_limit_input.setRange(1, 10000)
        self._hr_table_row_limit_input.setSingleStep(100)
        self._hr_table_row_limit_input.setValue(max(1, int(settings.hr_table_row_limit)))
        self._hr_table_row_limit_input.setToolTip("Limit how many HR rows are shown in the Source Results table.")
        self._hr_plot_require_parallax_input = QCheckBox("Require parallax-derived values by default")
        self._hr_plot_require_parallax_input.setChecked(settings.hr_plot_require_parallax)
        self._hr_plot_color_saturation_input = QDoubleSpinBox()
        self._hr_plot_color_saturation_input.setRange(0.0, 2.0)
        self._hr_plot_color_saturation_input.setSingleStep(0.1)
        self._hr_plot_color_saturation_input.setDecimals(2)
        self._hr_plot_color_saturation_input.setValue(settings.hr_plot_color_saturation)
        self._hr_plot_point_opacity_input = QDoubleSpinBox()
        self._hr_plot_point_opacity_input.setRange(0.05, 1.0)
        self._hr_plot_point_opacity_input.setSingleStep(0.05)
        self._hr_plot_point_opacity_input.setDecimals(2)
        self._hr_plot_point_opacity_input.setValue(settings.hr_plot_point_opacity)
        self._hr_selection_circle_color_input = QPushButton()
        self._hr_selection_circle_color = str(settings.hr_selection_circle_color or "#ffd166").strip().lower() or "#ffd166"
        self._hr_selection_circle_color_input.clicked.connect(self._pick_hr_selection_circle_color)
        self._update_hr_selection_circle_color_button()
        self._hr_selection_circle_opacity_input = QDoubleSpinBox()
        self._hr_selection_circle_opacity_input.setRange(0.0, 1.0)
        self._hr_selection_circle_opacity_input.setSingleStep(0.05)
        self._hr_selection_circle_opacity_input.setDecimals(2)
        self._hr_selection_circle_opacity_input.setValue(settings.hr_selection_circle_opacity)
        self._hr_selection_circle_size_factor_input = QDoubleSpinBox()
        self._hr_selection_circle_size_factor_input.setRange(1.0, 4.0)
        self._hr_selection_circle_size_factor_input.setSingleStep(0.05)
        self._hr_selection_circle_size_factor_input.setDecimals(2)
        self._hr_selection_circle_size_factor_input.setSuffix(" x")
        self._hr_selection_circle_size_factor_input.setValue(settings.hr_selection_circle_size_factor)
        self._hr_plot_hide_flagged_input = QCheckBox("Hide flagged rows by default")
        self._hr_plot_hide_flagged_input.setChecked(bool(settings.hr_plot_hide_flagged))
        self._hr_plot_hide_saturated_input = QCheckBox("Hide saturated rows by default")
        self._hr_plot_hide_saturated_input.setChecked(bool(settings.hr_plot_hide_saturated))
        self._hr_search_catalog_names_input = QCheckBox("Search for catalog/designation names")
        self._hr_search_catalog_names_input.setChecked(bool(getattr(settings, "hr_search_catalog_names", True)))
        self._hr_search_catalog_names_input.setToolTip(
            "Search bright HR targets in SIMBAD after the diagram loads so friendlier catalog or designation names can replace raw Gaia identifiers when available."
        )
        self._hr_search_catalog_names_magnitude_threshold_input = QDoubleSpinBox()
        self._hr_search_catalog_names_magnitude_threshold_input.setRange(-5.0, 30.0)
        self._hr_search_catalog_names_magnitude_threshold_input.setSingleStep(0.25)
        self._hr_search_catalog_names_magnitude_threshold_input.setDecimals(2)
        self._hr_search_catalog_names_magnitude_threshold_input.setSuffix(" mag")
        self._hr_search_catalog_names_magnitude_threshold_input.setValue(float(getattr(settings, "hr_search_catalog_names_magnitude_threshold", 9.0)))
        self._hr_search_catalog_names_magnitude_threshold_input.setToolTip(
            "Only HR rows at or brighter than this Gaia G magnitude threshold are included in the background catalog-name search."
        )
        self._hr_plot_apparent_mag_min_input = QDoubleSpinBox()
        self._hr_plot_apparent_mag_min_input.setRange(-5.0, 30.0)
        self._hr_plot_apparent_mag_min_input.setSingleStep(0.25)
        self._hr_plot_apparent_mag_min_input.setDecimals(2)
        self._hr_plot_apparent_mag_min_input.setValue(float(settings.hr_plot_apparent_magnitude_min))
        self._hr_plot_apparent_mag_min_input.setToolTip("Lower Gaia G apparent-magnitude bound used when a new HR diagram is opened.")
        self._hr_plot_apparent_mag_max_input = QDoubleSpinBox()
        self._hr_plot_apparent_mag_max_input.setRange(-5.0, 30.0)
        self._hr_plot_apparent_mag_max_input.setSingleStep(0.25)
        self._hr_plot_apparent_mag_max_input.setDecimals(2)
        self._hr_plot_apparent_mag_max_input.setValue(float(settings.hr_plot_apparent_magnitude_max))
        self._hr_plot_apparent_mag_max_input.setToolTip("Upper Gaia G apparent-magnitude bound used when a new HR diagram is opened.")
        self._hr_plot_marker_size_mode_input = QComboBox()
        self._hr_plot_marker_size_mode_input.addItem("Scaled by apparent magnitude", "scaled")
        self._hr_plot_marker_size_mode_input.addItem("Fixed size", "fixed")
        self._set_combo_data(self._hr_plot_marker_size_mode_input, settings.hr_plot_marker_size_mode)
        self._hr_plot_marker_size_mode_input.currentIndexChanged.connect(self._update_hr_plot_size_inputs)
        self._hr_plot_fixed_marker_size_input = QDoubleSpinBox()
        self._hr_plot_fixed_marker_size_input.setRange(2.0, 24.0)
        self._hr_plot_fixed_marker_size_input.setSingleStep(0.5)
        self._hr_plot_fixed_marker_size_input.setDecimals(1)
        self._hr_plot_fixed_marker_size_input.setValue(settings.hr_plot_fixed_marker_size)
        self._hr_motion_vector_color_input = QPushButton()
        self._hr_motion_vector_color = str(settings.hr_motion_vector_color or "#3d8bfd").strip().lower() or "#3d8bfd"
        self._hr_motion_vector_color_input.clicked.connect(self._pick_hr_motion_vector_color)
        self._update_hr_motion_vector_color_button()
        self._hr_motion_vector_width_input = QDoubleSpinBox()
        self._hr_motion_vector_width_input.setRange(0.5, 8.0)
        self._hr_motion_vector_width_input.setSingleStep(0.25)
        self._hr_motion_vector_width_input.setDecimals(2)
        self._hr_motion_vector_width_input.setValue(float(settings.hr_motion_vector_width))
        self._frame_edge_margin_percent_input = QDoubleSpinBox()
        self._frame_edge_margin_percent_input.setDecimals(1)
        self._frame_edge_margin_percent_input.setRange(0.0, 49.0)
        self._frame_edge_margin_percent_input.setSingleStep(1.0)
        self._frame_edge_margin_percent_input.setSuffix("%")
        self._frame_edge_margin_percent_input.setValue(settings.frame_edge_margin_percent)
        self._image_display_stretch_mode_input = QComboBox()
        self._image_display_stretch_mode_input.addItem("Auto Stretch", "stf")
        self._image_display_stretch_mode_input.addItem("Linear", "linear")
        self._image_display_stretch_mode_input.addItem("Asinh", "asinh")
        self._image_display_stretch_mode_input.addItem("Sqrt", "sqrt")
        self._image_display_stretch_mode_input.addItem("Log", "log")
        self._set_combo_data(self._image_display_stretch_mode_input, settings.image_display_stretch_mode)
        self._image_display_brightness_input = QDoubleSpinBox()
        self._image_display_brightness_input.setRange(-0.95, 0.95)
        self._image_display_brightness_input.setSingleStep(0.05)
        self._image_display_brightness_input.setDecimals(2)
        self._image_display_brightness_input.setValue(settings.image_display_brightness)
        self._image_display_contrast_input = QDoubleSpinBox()
        self._image_display_contrast_input.setRange(0.2, 4.0)
        self._image_display_contrast_input.setSingleStep(0.1)
        self._image_display_contrast_input.setDecimals(2)
        self._image_display_contrast_input.setValue(settings.image_display_contrast)
        self._image_display_inverted_input = QCheckBox("Invert")
        self._image_display_inverted_input.setChecked(settings.image_display_inverted)
        self._asteroid_estimate_snr_threshold_input = QDoubleSpinBox()
        self._asteroid_estimate_snr_threshold_input.setDecimals(1)
        self._asteroid_estimate_snr_threshold_input.setRange(0.1, 100.0)
        self._asteroid_estimate_snr_threshold_input.setSingleStep(0.5)
        self._asteroid_estimate_snr_threshold_input.setValue(settings.asteroid_estimate_snr_threshold)
        self._asteroid_estimate_start_magnitude_input = QDoubleSpinBox()
        self._asteroid_estimate_start_magnitude_input.setDecimals(1)
        self._asteroid_estimate_start_magnitude_input.setRange(-5.0, 30.0)
        self._asteroid_estimate_start_magnitude_input.setSingleStep(0.5)
        self._asteroid_estimate_start_magnitude_input.setSuffix(" mag")
        self._asteroid_estimate_start_magnitude_input.setValue(settings.asteroid_estimate_start_magnitude)
        self._asteroid_manual_magnitude_limit_override_enabled_input = QCheckBox("Use manual detection magnitude limit override")
        self._asteroid_manual_magnitude_limit_override_enabled_input.setChecked(settings.asteroid_manual_magnitude_limit_override_enabled)
        self._asteroid_manual_magnitude_limit_override_enabled_input.setToolTip(
            "When enabled, Generate uses the manual limit below instead of the saved or estimated asteroid/comet detection limit."
        )
        self._asteroid_manual_magnitude_limit_override_input = QDoubleSpinBox()
        self._asteroid_manual_magnitude_limit_override_input.setDecimals(2)
        self._asteroid_manual_magnitude_limit_override_input.setRange(5.0, 30.0)
        self._asteroid_manual_magnitude_limit_override_input.setSingleStep(0.25)
        self._asteroid_manual_magnitude_limit_override_input.setSuffix(" mag")
        self._asteroid_manual_magnitude_limit_override_input.setValue(settings.asteroid_manual_magnitude_limit_override)
        self._asteroid_manual_magnitude_limit_override_input.setToolTip(
            "Manual asteroid/comet detection magnitude limit used only while the override checkbox is enabled."
        )
        self._asteroid_manual_magnitude_limit_override_enabled_input.stateChanged.connect(self._update_asteroid_estimate_inputs)
        self._asteroid_estimate_stars_per_bin_input = QSpinBox()
        self._asteroid_estimate_stars_per_bin_input.setRange(2, 50)
        self._asteroid_estimate_stars_per_bin_input.setValue(settings.asteroid_estimate_stars_per_bin)
        self._asteroid_estimate_stars_per_bin_input.valueChanged.connect(self._update_asteroid_estimate_inputs)
        self._asteroid_estimate_required_visible_stars_input = QSpinBox()
        self._asteroid_estimate_required_visible_stars_input.setRange(1, max(1, settings.asteroid_estimate_stars_per_bin - 1))
        self._asteroid_estimate_required_visible_stars_input.setValue(settings.asteroid_estimate_required_visible_stars)
        self._asteroid_estimate_annotate_lowest_mag_stars_input = QCheckBox("Annotate the faintest confirmed stars after Estimate")
        self._asteroid_estimate_annotate_lowest_mag_stars_input.setChecked(settings.asteroid_estimate_annotate_lowest_mag_stars)
        self._asteroid_visual_show_known_objects_input = QCheckBox("Show asteroid/comet markers on the main image")
        self._asteroid_visual_show_known_objects_input.setChecked(settings.asteroid_visual_show_known_objects)
        self._asteroid_visual_show_potential_discoveries_input = QCheckBox("Show marked potential discoveries on the main image")
        self._asteroid_visual_show_potential_discoveries_input.setChecked(settings.asteroid_visual_show_potential_discoveries)
        self._asteroid_visual_label_all_objects_input = QCheckBox("Show asteroid/comet name labels on the main image")
        self._asteroid_visual_label_all_objects_input.setChecked(settings.asteroid_visual_label_all_objects)
        self._asteroid_visual_show_target_marker_input = QCheckBox("Use target marker for the selected asteroid/comet on the main image")
        self._asteroid_visual_show_target_marker_input.setChecked(settings.asteroid_visual_show_target_marker)
        self._asteroid_track_object_position_mode_input = QComboBox()
        self._asteroid_track_object_position_mode_input.addItem("Known / predicted position", "predicted")
        self._asteroid_track_object_position_mode_input.addItem("Detected local match", "measured")
        asteroid_track_position_index = self._asteroid_track_object_position_mode_input.findData(settings.asteroid_track_object_position_mode)
        self._asteroid_track_object_position_mode_input.setCurrentIndex(asteroid_track_position_index if asteroid_track_position_index >= 0 else 0)
        self._asteroid_visual_show_all_crosshairs_input = QCheckBox("Show prediction crosshairs for every detected object")
        self._asteroid_visual_show_all_crosshairs_input.setChecked(settings.asteroid_visual_show_all_crosshairs)
        self._asteroid_visual_highlight_selected_object_input = QCheckBox("Highlight the selected asteroid/comet with distinct colors")
        self._asteroid_visual_highlight_selected_object_input.setChecked(settings.asteroid_visual_highlight_selected_object)
        self._asteroid_visual_invert_annotation_colors_input = QCheckBox("Invert also swaps asteroid/comet annotation and text colors")
        self._asteroid_visual_invert_annotation_colors_input.setChecked(settings.asteroid_visual_invert_annotation_colors)
        self._asteroid_target_marker_line_color = str(settings.asteroid_target_marker_line_color or "#ef4444").strip().lower() or "#ef4444"
        self._asteroid_target_marker_accent_color = str(settings.asteroid_target_marker_accent_color or "#fca5a5").strip().lower() or "#fca5a5"
        self._asteroid_target_marker_text_color = str(settings.asteroid_target_marker_text_color or "#fff1f2").strip().lower() or "#fff1f2"
        self._asteroid_target_marker_outline_color = str(settings.asteroid_target_marker_outline_color or "#ffffff").strip().lower() or "#ffffff"
        self._asteroid_target_marker_line_color_button = QPushButton("Line...")
        self._asteroid_target_marker_line_color_button.clicked.connect(self._choose_asteroid_target_marker_line_color)
        self._asteroid_target_marker_accent_color_button = QPushButton("Accent...")
        self._asteroid_target_marker_accent_color_button.clicked.connect(self._choose_asteroid_target_marker_accent_color)
        self._asteroid_target_marker_text_color_button = QPushButton("Label...")
        self._asteroid_target_marker_text_color_button.clicked.connect(self._choose_asteroid_target_marker_text_color)
        self._asteroid_target_marker_outline_color_button = QPushButton("Outline...")
        self._asteroid_target_marker_outline_color_button.clicked.connect(self._choose_asteroid_target_marker_outline_color)
        self._asteroid_target_marker_line_width_input = QDoubleSpinBox()
        self._asteroid_target_marker_line_width_input.setRange(0.5, 8.0)
        self._asteroid_target_marker_line_width_input.setSingleStep(0.25)
        self._asteroid_target_marker_line_width_input.setDecimals(2)
        self._asteroid_target_marker_line_width_input.setSuffix(" px")
        self._asteroid_target_marker_line_width_input.setValue(float(settings.asteroid_target_marker_line_width))
        self._update_asteroid_target_marker_line_color_button()
        self._update_asteroid_target_marker_accent_color_button()
        self._update_asteroid_target_marker_text_color_button()
        self._update_asteroid_target_marker_outline_color_button()
        self._asteroid_blink_frame_duration_input = QComboBox()
        for interval_ms in _ASTEROID_BLINK_INTERVAL_OPTIONS_MS:
            self._asteroid_blink_frame_duration_input.addItem(f"{interval_ms / 1000.0:.2f} s", interval_ms)
        self._set_combo_data(self._asteroid_blink_frame_duration_input, settings.asteroid_blink_frame_duration_ms)
        self._asteroid_gif_export_scale_percent_input = QSpinBox()
        self._asteroid_gif_export_scale_percent_input.setRange(25, 400)
        self._asteroid_gif_export_scale_percent_input.setSingleStep(25)
        self._asteroid_gif_export_scale_percent_input.setSuffix(" %")
        self._asteroid_gif_export_scale_percent_input.setValue(settings.asteroid_gif_export_scale_percent)
        self._asteroid_mp4_export_scale_percent_input = QSpinBox()
        self._asteroid_mp4_export_scale_percent_input.setRange(25, 400)
        self._asteroid_mp4_export_scale_percent_input.setSingleStep(25)
        self._asteroid_mp4_export_scale_percent_input.setSuffix(" %")
        self._asteroid_mp4_export_scale_percent_input.setValue(settings.asteroid_mp4_export_scale_percent)
        self._asteroid_gif_export_loop_forever_input = QCheckBox("Loop exported GIF playback forever")
        self._asteroid_gif_export_loop_forever_input.setChecked(settings.asteroid_gif_export_loop_forever)
        self._synthetic_tracking_crop_radius_input = QSpinBox()
        self._synthetic_tracking_crop_radius_input.setRange(4, 65535)
        self._synthetic_tracking_crop_radius_input.setSuffix(" px")
        self._synthetic_tracking_crop_radius_input.setValue(settings.synthetic_tracking_crop_radius_pixels)
        self._synthetic_tracking_integration_mode_input = QComboBox()
        self._synthetic_tracking_integration_mode_input.addItem("Average", "average")
        self._synthetic_tracking_integration_mode_input.addItem("Mean", "mean")
        self._synthetic_tracking_integration_mode_input.addItem("Min", "min")
        self._synthetic_tracking_integration_mode_input.addItem("Max", "max")
        synthetic_tracking_integration_index = self._synthetic_tracking_integration_mode_input.findData(settings.synthetic_tracking_integration_mode)
        if synthetic_tracking_integration_index >= 0:
            self._synthetic_tracking_integration_mode_input.setCurrentIndex(synthetic_tracking_integration_index)
        self._synthetic_tracking_weight_mode_input = QComboBox()
        self._synthetic_tracking_weight_mode_input.addItem("PSF signal weight", "psf_signal_weight")
        self._synthetic_tracking_weight_mode_input.addItem("PSF SNR", "psf_snr")
        self._synthetic_tracking_weight_mode_input.addItem("SNR", "snr")
        self._synthetic_tracking_weight_mode_input.addItem("Average signal strength", "average_signal_strength")
        synthetic_tracking_weight_index = self._synthetic_tracking_weight_mode_input.findData(settings.synthetic_tracking_weight_mode)
        if synthetic_tracking_weight_index >= 0:
            self._synthetic_tracking_weight_mode_input.setCurrentIndex(synthetic_tracking_weight_index)
        self._synthetic_tracking_rejection_mode_input = QComboBox()
        self._synthetic_tracking_rejection_mode_input.addItem("No rejection", "no_rejection")
        self._synthetic_tracking_rejection_mode_input.addItem("Min/Max", "min_max")
        self._synthetic_tracking_rejection_mode_input.addItem("Sigma clipping", "sigma_clipping")
        self._synthetic_tracking_rejection_mode_input.addItem("Winsorized sigma clipping", "winsorized_sigma_clipping")
        self._synthetic_tracking_rejection_mode_input.addItem("Averaged sigma clipping", "averaged_sigma_clipping")
        synthetic_tracking_rejection_index = self._synthetic_tracking_rejection_mode_input.findData(settings.synthetic_tracking_rejection_mode)
        if synthetic_tracking_rejection_index >= 0:
            self._synthetic_tracking_rejection_mode_input.setCurrentIndex(synthetic_tracking_rejection_index)
        self._synthetic_tracking_backend_preference_input = QComboBox()
        self._synthetic_tracking_backend_preference_input.addItem("Auto", "auto")
        self._synthetic_tracking_backend_preference_input.addItem("CPU only", "cpu")
        self._synthetic_tracking_backend_preference_input.addItem("GPU when available", "gpu")
        synthetic_tracking_backend_index = self._synthetic_tracking_backend_preference_input.findData(settings.synthetic_tracking_backend_preference)
        if synthetic_tracking_backend_index >= 0:
            self._synthetic_tracking_backend_preference_input.setCurrentIndex(synthetic_tracking_backend_index)
        self._synthetic_tracking_allow_mixed_all_group_input = QCheckBox("Allow running from mixed 'All' groups")
        self._synthetic_tracking_allow_mixed_all_group_input.setChecked(settings.synthetic_tracking_allow_mixed_all_group)
        self._synthetic_tracking_advanced_enabled_input = QCheckBox("Open manual one-stack dialog")
        self._synthetic_tracking_advanced_enabled_input.setChecked(settings.synthetic_tracking_advanced_enabled)
        self._saturation_filter_enabled_input = QCheckBox("Skip saturated selected variable stars during analysis")
        self._saturation_filter_enabled_input.setChecked(settings.saturation_filter_enabled)
        self._interface_tips_enabled_input = QCheckBox("Show rotating tips in the status bar")
        self._interface_tips_enabled_input.setChecked(bool(getattr(settings, "interface_tips_enabled", True)))
        self._show_mode_launcher_on_startup_input = QCheckBox("Show mode picker on startup")
        self._show_mode_launcher_on_startup_input.setChecked(bool(getattr(settings, "show_mode_launcher_on_startup", True)))
        self._reference_star_magnitude_range_enabled_input = QCheckBox("Use reference-star magnitude range")
        self._reference_star_magnitude_range_enabled_input.setChecked(
            settings.reference_star_min_magnitude is not None or settings.reference_star_max_magnitude is not None
        )
        self._reference_star_magnitude_range_enabled_input.stateChanged.connect(self._update_reference_limit_inputs)
        self._reference_star_min_magnitude_input = QDoubleSpinBox()
        self._configure_float_spin_box(
            self._reference_star_min_magnitude_input,
            settings.reference_star_min_magnitude if settings.reference_star_min_magnitude is not None else 10.0,
            -5.0,
            30.0,
            " mag",
        )
        self._reference_star_min_magnitude_input.setSingleStep(0.5)
        self._reference_star_max_magnitude_input = QDoubleSpinBox()
        self._configure_float_spin_box(
            self._reference_star_max_magnitude_input,
            settings.reference_star_max_magnitude if settings.reference_star_max_magnitude is not None else 13.5,
            -5.0,
            30.0,
            " mag",
        )
        self._reference_star_max_magnitude_input.setSingleStep(0.5)
        self._observer_code_input = QLineEdit(settings.observer_code)
        self._observer_name_input = QLineEdit(settings.observer_name)
        self._organization_input = QLineEdit(settings.organization)
        self._site_name_input = QLineEdit(settings.site_name)
        self._observing_site_latitude_input = QLineEdit(self._optional_float_text(settings.observing_site_latitude_deg))
        self._observing_site_latitude_input.setPlaceholderText("e.g. 51.5074")
        self._observing_site_latitude_input.setClearButtonEnabled(True)
        self._observing_site_latitude_input.setValidator(QDoubleValidator(-90.0, 90.0, 6, self))
        self._observing_site_longitude_input = QLineEdit(self._optional_float_text(settings.observing_site_longitude_deg))
        self._observing_site_longitude_input.setPlaceholderText("e.g. -0.1278")
        self._observing_site_longitude_input.setClearButtonEnabled(True)
        self._observing_site_longitude_input.setValidator(QDoubleValidator(-180.0, 180.0, 6, self))
        self._observing_site_elevation_input = QLineEdit(self._optional_float_text(settings.observing_site_elevation_m))
        self._observing_site_elevation_input.setPlaceholderText("e.g. 45")
        self._observing_site_elevation_input.setClearButtonEnabled(True)
        self._observing_site_elevation_input.setValidator(QDoubleValidator(-500.0, 12000.0, 2, self))
        self._telescope_input = QLineEdit(settings.telescope)
        self._telescope_focal_length_input = QDoubleSpinBox()
        self._configure_optional_float_spin_box(self._telescope_focal_length_input, settings.telescope_focal_length_mm, 0.1, 100000.0, " mm", decimals=1, step=10.0)
        self._telescope_focal_length_input.valueChanged.connect(self._update_setup_derived_fields)
        self._telescope_aperture_input = QDoubleSpinBox()
        self._configure_optional_float_spin_box(self._telescope_aperture_input, settings.telescope_aperture_mm, 0.1, 100000.0, " mm", decimals=1, step=1.0)
        self._telescope_focal_ratio_input = QDoubleSpinBox()
        self._configure_optional_float_spin_box(self._telescope_focal_ratio_input, settings.telescope_focal_ratio, 0.1, 100.0, "", decimals=2, step=0.1)
        self._camera_input = QLineEdit(settings.camera)
        self._camera_pixel_size_input = QDoubleSpinBox()
        self._configure_optional_float_spin_box(self._camera_pixel_size_input, settings.camera_pixel_size_um, 0.1, 1000.0, " um", decimals=2, step=0.1)
        self._camera_pixel_size_input.valueChanged.connect(self._update_setup_derived_fields)
        self._setup_pixel_scale_input = QLineEdit()
        self._setup_pixel_scale_input.setReadOnly(True)
        self._setup_pixel_scale_input.setPlaceholderText("Calculated from focal length and pixel size")
        self._bortle_scale_input = QSpinBox()
        self._bortle_scale_input.setRange(0, 9)
        self._bortle_scale_input.setSpecialValueText("Unknown")
        self._bortle_scale_input.setValue(0 if settings.bortle_scale is None else int(settings.bortle_scale))
        self._filter_system_input = QComboBox()
        self._filter_system_input.setEditable(True)
        self._filter_system_input.addItems(_AAVSO_FILTER_OPTIONS)
        self._filter_system_input.setCurrentText(settings.filter_system)
        self._aavso_chart_id_input = QLineEdit(settings.aavso_chart_id)
        self._observation_timezone_input = QComboBox()
        self._observation_timezone_input.setEditable(True)
        self._observation_timezone_input.addItems(_OBSERVATION_TIMEZONE_OPTIONS)
        self._observation_timezone_input.setCurrentText(settings.observation_timezone)
        self._observation_timezone_input.setToolTip("Used for filename timestamps and DATE-OBS values that do not include an explicit timezone offset.")
        self._time_standard_input = QComboBox()
        self._time_standard_input.setEditable(True)
        for item in ("UTC", "JD_UTC", "HJD_UTC", "BJD_TDB"):
            self._time_standard_input.addItem(item)
        self._time_standard_input.setCurrentText(settings.time_standard or "UTC")
        self._transformed_input = QCheckBox("Measurements are transformed to the standard system")
        self._transformed_input.setChecked(settings.transformed)
        self._reduction_notes_input = QPlainTextEdit(settings.reduction_notes)
        self._reduction_notes_input.setPlaceholderText("Optional notes about calibration, exclusions, or reduction choices")
        self._reduction_notes_input.setTabChangesFocus(True)
        self._reduction_notes_input.setFixedHeight(78)
        self._photometry_aperture_mode_input = QComboBox()
        self._photometry_aperture_mode_input.addItem("Adaptive from image FWHM", PhotometryApertureMode.FWHM_SCALED)
        selected_aperture_mode_index = self._photometry_aperture_mode_input.findData(settings.photometry_aperture_mode)
        if selected_aperture_mode_index >= 0:
            self._photometry_aperture_mode_input.setCurrentIndex(selected_aperture_mode_index)
        self._aperture_radius_pixels_input = QDoubleSpinBox()
        self._configure_float_spin_box(self._aperture_radius_pixels_input, settings.aperture_radius_pixels, 1.0, 100.0, " px")
        self._annulus_inner_radius_pixels_input = QDoubleSpinBox()
        self._configure_float_spin_box(self._annulus_inner_radius_pixels_input, settings.annulus_inner_radius_pixels, 1.0, 150.0, " px")
        self._annulus_outer_radius_pixels_input = QDoubleSpinBox()
        self._configure_float_spin_box(self._annulus_outer_radius_pixels_input, settings.annulus_outer_radius_pixels, 1.0, 200.0, " px")
        self._aperture_radius_fwhm_scale_input = QDoubleSpinBox()
        self._configure_float_spin_box(self._aperture_radius_fwhm_scale_input, settings.aperture_radius_fwhm_scale, 0.5, 10.0, " x FWHM")
        self._annulus_inner_radius_fwhm_scale_input = QDoubleSpinBox()
        self._configure_float_spin_box(self._annulus_inner_radius_fwhm_scale_input, settings.annulus_inner_radius_fwhm_scale, 0.5, 20.0, " x FWHM")
        self._annulus_outer_radius_fwhm_scale_input = QDoubleSpinBox()
        self._configure_float_spin_box(self._annulus_outer_radius_fwhm_scale_input, settings.annulus_outer_radius_fwhm_scale, 0.5, 30.0, " x FWHM")
        self._variable_star_limit_mode_input = QComboBox()
        self._variable_star_limit_mode_input.addItem("Percentage of brightest stars", VariableStarLimitMode.PERCENT)
        self._variable_star_limit_mode_input.addItem("Absolute number of brightest stars", VariableStarLimitMode.COUNT)
        self._variable_star_limit_value_input = QSpinBox()
        selected_mode_index = self._variable_star_limit_mode_input.findData(settings.variable_star_limit_mode)
        if selected_mode_index >= 0:
            self._variable_star_limit_mode_input.setCurrentIndex(selected_mode_index)
        self._variable_star_limit_mode_input.currentIndexChanged.connect(self._update_variable_limit_input)
        self._variable_star_limit_value_input.setValue(settings.variable_star_limit_value)
        self._update_variable_limit_input()
        self._preview_variable_star_max_count_input = QSpinBox()
        self._preview_variable_star_max_count_input.setRange(0, 100000)
        self._preview_variable_star_max_count_input.setSpecialValueText("No limit")
        self._preview_variable_star_max_count_input.setValue(max(0, settings.preview_variable_star_max_count))
        self._preview_variable_star_magnitude_range_enabled_input = QCheckBox("Use magnitude range")
        self._preview_variable_star_magnitude_range_enabled_input.setChecked(
            settings.preview_variable_star_min_magnitude is not None or settings.preview_variable_star_max_magnitude is not None
        )
        self._preview_variable_star_magnitude_range_enabled_input.stateChanged.connect(self._update_preview_limit_inputs)
        self._preview_variable_star_min_magnitude_input = QDoubleSpinBox()
        self._configure_float_spin_box(
            self._preview_variable_star_min_magnitude_input,
            settings.preview_variable_star_min_magnitude if settings.preview_variable_star_min_magnitude is not None else 8.0,
            -5.0,
            30.0,
            " mag",
        )
        self._preview_variable_star_min_magnitude_input.setSingleStep(0.5)
        self._preview_variable_star_max_magnitude_input = QDoubleSpinBox()
        self._configure_float_spin_box(
            self._preview_variable_star_max_magnitude_input,
            settings.preview_variable_star_max_magnitude if settings.preview_variable_star_max_magnitude is not None else 15.0,
            -5.0,
            30.0,
            " mag",
        )
        self._preview_variable_star_max_magnitude_input.setSingleStep(0.5)
        self._designation_checkboxes: dict[VariableStarDesignationFamily, QCheckBox] = {}
        cache_browse = QPushButton("Browse")
        cache_browse.clicked.connect(self._browse_cache_dir)

        cache_row = QHBoxLayout()
        cache_row.addWidget(self._cache_dir_input, stretch=1)
        cache_row.addWidget(cache_browse)

        settings_location_row = QHBoxLayout()
        settings_location_row.addWidget(self._settings_location_input, stretch=1)
        settings_location_row.addWidget(self._settings_location_browse_button)

        form_layout = QFormLayout()
        form_layout.addRow("Astrometry API Key", self._api_key_input)

        cache_container = QWidget()
        cache_container.setLayout(cache_row)
        settings_location_container = QWidget()
        settings_location_container.setLayout(settings_location_row)
        form_layout.addRow("Cache Directory", cache_container)
        form_layout.addRow("Settings Location", settings_location_container)
        form_layout.addRow("Settings Location Mode", self._use_default_settings_location_input)
        form_layout.addRow("Interface Tips", self._interface_tips_enabled_input)
        form_layout.addRow("Mode Picker", self._show_mode_launcher_on_startup_input)
        image_display_layout = QHBoxLayout()
        image_display_layout.addWidget(self._image_display_stretch_mode_input)
        image_display_layout.addWidget(QLabel("Brightness"))
        image_display_layout.addWidget(self._image_display_brightness_input)
        image_display_layout.addWidget(QLabel("Contrast"))
        image_display_layout.addWidget(self._image_display_contrast_input)
        image_display_layout.addWidget(self._image_display_inverted_input)
        image_display_container = QWidget()
        image_display_container.setLayout(image_display_layout)
        form_layout.addRow("Image Display", image_display_container)
        self._clear_cache_button = QPushButton("Clear Cache")
        self._clear_cache_button.clicked.connect(self._clear_cache)
        self._clear_settings_button = QPushButton("Clear Settings")
        self._clear_settings_button.clicked.connect(self._clear_settings)
        clear_actions_row = QHBoxLayout()
        clear_actions_row.addWidget(self._clear_cache_button)
        clear_actions_row.addWidget(self._clear_settings_button)
        clear_actions_container = QWidget()
        clear_actions_container.setLayout(clear_actions_row)
        form_layout.addRow("", clear_actions_container)

        differential_photometry_form_layout = QFormLayout()
        differential_photometry_form_layout.addRow("Nearby Comparison Stars", self._nearby_reference_count_input)
        differential_photometry_form_layout.addRow("Frame Edge Margin", self._frame_edge_margin_percent_input)
        differential_photometry_form_layout.addRow("Saturation Filter", self._saturation_filter_enabled_input)
        reference_magnitude_grid = QGridLayout()
        reference_magnitude_grid.addWidget(self._reference_star_magnitude_range_enabled_input, 0, 0, 1, 2)
        reference_magnitude_grid.addWidget(QLabel("Min"), 1, 0)
        reference_magnitude_grid.addWidget(self._reference_star_min_magnitude_input, 1, 1)
        reference_magnitude_grid.addWidget(QLabel("Max"), 2, 0)
        reference_magnitude_grid.addWidget(self._reference_star_max_magnitude_input, 2, 1)
        reference_magnitude_container = QWidget()
        reference_magnitude_container.setLayout(reference_magnitude_grid)
        differential_photometry_form_layout.addRow("Reference-Star Magnitude Range", reference_magnitude_container)
        differential_photometry_form_layout.addRow("Adaptive Aperture Scale", self._aperture_radius_fwhm_scale_input)
        differential_photometry_form_layout.addRow("Adaptive Annulus Inner Scale", self._annulus_inner_radius_fwhm_scale_input)
        differential_photometry_form_layout.addRow("Adaptive Annulus Outer Scale", self._annulus_outer_radius_fwhm_scale_input)
        differential_photometry_form_layout.addRow("Variable Star Limit Mode", self._variable_star_limit_mode_input)
        differential_photometry_form_layout.addRow("Brightest Variable Stars to Analyze", self._variable_star_limit_value_input)
        differential_photometry_form_layout.addRow("Preview Variable-Star Max Count", self._preview_variable_star_max_count_input)
        preview_magnitude_grid = QGridLayout()
        preview_magnitude_grid.addWidget(self._preview_variable_star_magnitude_range_enabled_input, 0, 0, 1, 2)
        preview_magnitude_grid.addWidget(QLabel("Min"), 1, 0)
        preview_magnitude_grid.addWidget(self._preview_variable_star_min_magnitude_input, 1, 1)
        preview_magnitude_grid.addWidget(QLabel("Max"), 2, 0)
        preview_magnitude_grid.addWidget(self._preview_variable_star_max_magnitude_input, 2, 1)
        preview_magnitude_container = QWidget()
        preview_magnitude_container.setLayout(preview_magnitude_grid)
        differential_photometry_form_layout.addRow("Preview Variable-Star Magnitude Range", preview_magnitude_container)

        general_tab = QWidget()
        general_layout = QVBoxLayout()
        general_layout.addLayout(form_layout)

        designation_group = QGroupBox("Variable Star Designation Filters")
        designation_layout = QGridLayout()
        for index, family in enumerate(VariableStarDesignationFamily):
            checkbox = QCheckBox(VARIABLE_STAR_DESIGNATION_LABELS[family])
            checkbox.setChecked(family in settings.variable_star_designation_filters)
            self._designation_checkboxes[family] = checkbox
            designation_layout.addWidget(checkbox, index // 2, index % 2)
        designation_group.setLayout(designation_layout)

        differential_photometry_tab = QWidget()
        differential_photometry_layout = QVBoxLayout()
        differential_photometry_layout.addLayout(differential_photometry_form_layout)
        differential_photometry_layout.addWidget(designation_group)

        science_metadata_group = QGroupBox("Science Export")
        science_metadata_layout = QFormLayout()
        science_metadata_layout.addRow("Observer Code", self._observer_code_input)
        science_metadata_layout.addRow("Observer Name", self._observer_name_input)
        science_metadata_layout.addRow("Organization", self._organization_input)
        science_metadata_layout.addRow("AAVSO Export Filter", self._filter_system_input)
        science_metadata_layout.addRow("AAVSO Sequence/Chart ID", self._aavso_chart_id_input)
        science_metadata_layout.addRow("Image Timestamp Timezone", self._observation_timezone_input)
        science_metadata_layout.addRow("Time Standard", self._time_standard_input)
        science_metadata_layout.addRow("Transformed Data", self._transformed_input)
        science_metadata_layout.addRow("Reduction Notes", self._reduction_notes_input)
        science_metadata_group.setLayout(science_metadata_layout)
        differential_photometry_layout.addWidget(science_metadata_group)
        differential_photometry_layout.addStretch(1)
        differential_photometry_tab.setLayout(differential_photometry_layout)
        self._differential_photometry_tab = differential_photometry_tab
        self._science_export_group = science_metadata_group

        hr_tab = QWidget()
        hr_layout = QVBoxLayout()
        hr_description = QLabel(
            "HR Diagram settings control measurement scope, default plot filters, selected-point circle styling, and the Source Image proper-motion overlay styling."
        )
        hr_description.setWordWrap(True)
        hr_form_layout = QFormLayout()
        hr_form_layout.addRow("HR Max Sources", self._hr_max_sources_input)
        hr_form_layout.addRow("Table Row Limit", self._hr_table_row_limit_input)
        hr_form_layout.addRow("Require Parallax", self._hr_plot_require_parallax_input)
        hr_form_layout.addRow("Hide Flagged", self._hr_plot_hide_flagged_input)
        hr_form_layout.addRow("Hide Saturated", self._hr_plot_hide_saturated_input)
        hr_form_layout.addRow("Catalog Names", self._hr_search_catalog_names_input)
        hr_form_layout.addRow("Name Search Mag", self._hr_search_catalog_names_magnitude_threshold_input)
        hr_apparent_mag_range = QWidget()
        hr_apparent_mag_range_layout = QGridLayout()
        hr_apparent_mag_range_layout.setContentsMargins(0, 0, 0, 0)
        hr_apparent_mag_range_layout.setHorizontalSpacing(6)
        hr_apparent_mag_range_layout.addWidget(QLabel("Min"), 0, 0)
        hr_apparent_mag_range_layout.addWidget(self._hr_plot_apparent_mag_min_input, 0, 1)
        hr_apparent_mag_range_layout.addWidget(QLabel("Max"), 0, 2)
        hr_apparent_mag_range_layout.addWidget(self._hr_plot_apparent_mag_max_input, 0, 3)
        hr_apparent_mag_range.setLayout(hr_apparent_mag_range_layout)
        hr_form_layout.addRow("Apparent Mag", hr_apparent_mag_range)
        hr_form_layout.addRow("Color Saturation", self._hr_plot_color_saturation_input)
        hr_form_layout.addRow("Opacity", self._hr_plot_point_opacity_input)
        hr_form_layout.addRow("Selected Circle Color", self._hr_selection_circle_color_input)
        hr_form_layout.addRow("Selected Circle Opacity", self._hr_selection_circle_opacity_input)
        hr_form_layout.addRow("Selected Circle Size", self._hr_selection_circle_size_factor_input)
        hr_form_layout.addRow("Data Point Size", self._hr_plot_marker_size_mode_input)
        hr_form_layout.addRow("Fixed Point Size", self._hr_plot_fixed_marker_size_input)
        hr_form_layout.addRow("Motion Vector Color", self._hr_motion_vector_color_input)
        hr_form_layout.addRow("Motion Vector Width", self._hr_motion_vector_width_input)
        hr_layout.addWidget(hr_description)
        hr_layout.addLayout(hr_form_layout)
        hr_layout.addStretch(1)
        hr_tab.setLayout(hr_layout)

        asteroid_tab = QWidget()
        asteroid_layout = QVBoxLayout()
        asteroid_description = QLabel(
            "Asteroid/comet settings are grouped into search, visuals, and export defaults so the full workflow fits more cleanly on screen."
        )
        asteroid_description.setWordWrap(True)
        asteroid_estimate_group = QGroupBox("Estimate Options")
        asteroid_estimate_layout = QFormLayout()
        asteroid_estimate_layout.addRow("SNR Threshold", self._asteroid_estimate_snr_threshold_input)
        asteroid_estimate_layout.addRow("Start Mag", self._asteroid_estimate_start_magnitude_input)
        asteroid_estimate_layout.addRow("Manual Override", self._asteroid_manual_magnitude_limit_override_enabled_input)
        asteroid_estimate_layout.addRow("Manual Limit", self._asteroid_manual_magnitude_limit_override_input)
        asteroid_estimate_layout.addRow("Amount of Stars to Check", self._asteroid_estimate_stars_per_bin_input)
        asteroid_estimate_layout.addRow("Visible Stars Needed", self._asteroid_estimate_required_visible_stars_input)
        asteroid_estimate_layout.addRow("Annotate Lowest Mag Stars", self._asteroid_estimate_annotate_lowest_mag_stars_input)
        asteroid_estimate_group.setLayout(asteroid_estimate_layout)
        asteroid_discovery_group = QGroupBox("Discovery Advanced")
        asteroid_discovery_group_layout = QVBoxLayout()
        asteroid_discovery_note = QLabel(
            "Discover can batch a large subgroup into smaller overlapping search windows, optionally bin temporary working frames, filter residual detections by local SNR before tracklet linking, split linked tracklets by how well they follow a linear constant-velocity path, and optionally run a final synthetic-track sweep after normal linking on each discovery search window. That sweep now uses subpixel stack shifts, can stay 360 degrees or focus on the image-plane direction followed by known main-belt asteroids in the current field, exposes an explicit focus-width control for that main-belt mode, and can optionally save each tested stack under synthetic_track in the current data folder. Temporary cached working frames are deleted after the run and stale Discover cache folders are also cleaned on the next startup."
        )
        asteroid_discovery_note.setWordWrap(True)
        asteroid_discovery_group_layout.addWidget(asteroid_discovery_note)
        asteroid_discovery_sections_layout = QGridLayout()
        asteroid_discovery_sections_layout.setHorizontalSpacing(12)
        asteroid_discovery_sections_layout.setVerticalSpacing(12)

        asteroid_discovery_preparation_group = QGroupBox("Preparation")
        asteroid_discovery_preparation_layout = QFormLayout()
        asteroid_discovery_preparation_tooltips = [
            (
                "Search Workers",
                self._asteroid_search_parallel_workers_input,
                "Controls how many background workers Discover and Recover Known can use, including the final Discover synthetic sweep. Higher values can speed large runs but use more CPU and memory; lower values reduce system load. Auto lets the app choose.",
            ),
            (
                "Working Binning",
                self._asteroid_discovery_binning_factor_input,
                "Temporarily downsamples working frames before residual search. Higher binning can speed searches and smooth noise, but it reduces spatial detail; lower binning keeps maximum detail but is slower.",
            ),
            (
                "Temporary Cache",
                self._asteroid_discovery_use_temporary_cache_input,
                "Writes prepared working frames to an auto-cleaned temporary cache during Discover. Enabled can reduce repeat preparation cost on large runs but uses disk space; disabled keeps more work in memory.",
            ),
            (
                "Edge Margin",
                self._asteroid_discovery_edge_margin_px_input,
                "Ignores residual detections close to the frame border. Higher values avoid edge artifacts but can miss movers entering or leaving the field; lower values search more of the border but allow more junk.",
            ),
            (
                "Frames per Batch",
                self._asteroid_discovery_frames_per_batch_input,
                "Splits long subgroups into overlapping search windows. Smaller batches reduce memory use and can help long runs, but they create more windows; larger batches keep more global linking context. Whole group disables batching.",
            ),
            (
                "Track Crop Radius",
                self._synthetic_tracking_crop_radius_input,
                "Default crop radius used by Synthetic Track when it builds the object-centered stack preview. Larger radii keep more surrounding context but increase work per frame; smaller radii keep the stack tighter and faster.",
            ),
            (
                "Track Integration",
                self._synthetic_tracking_integration_mode_input,
                "Chooses how the aligned frame patches are reduced into the final stack. Average is the default unweighted blend, Mean applies the selected weighting, and Min or Max keep the per-pixel extrema.",
            ),
            (
                "Track Weights",
                self._synthetic_tracking_weight_mode_input,
                "Chooses the scalar frame weighting used by Mean integration. Average, Min, and Max ignore this setting.",
            ),
            (
                "Track Rejection",
                self._synthetic_tracking_rejection_mode_input,
                "Controls how outlier pixels are rejected before integration. Stronger rejection is more robust against artifacts but uses more memory.",
            ),
            (
                "Track Backend",
                self._synthetic_tracking_backend_preference_input,
                "Controls the preferred compute backend for full-frame Synthetic Track. Auto uses GPU when CuPy is available for No rejection full-frame Average/Mean/Min/Max stacks, CPU forces the NumPy path, and GPU requests CuPy but still falls back to CPU for unsupported rejection modes.",
            ),
            (
                "Track Mixed All Group",
                self._synthetic_tracking_allow_mixed_all_group_input,
                "Allows Synthetic Track to run while the asteroid/comet viewer is still on a mixed 'All' subgroup instead of requiring a single filter and exposure subgroup first.",
            ),
            (
                "Advanced Synthetic Track",
                self._synthetic_tracking_advanced_enabled_input,
                "When enabled, clicking Synthetic Track opens a manual one-stack dialog so the center, motion, crop radius, and combine mode can be edited before the stack is built.",
            ),
        ]
        for label_text, field, tooltip in asteroid_discovery_preparation_tooltips:
            self._add_tooltip_form_row(asteroid_discovery_preparation_layout, label_text, field, tooltip)
        asteroid_discovery_preparation_group.setLayout(asteroid_discovery_preparation_layout)

        asteroid_discovery_detection_group = QGroupBox("Residual Detection")
        asteroid_discovery_detection_layout = QFormLayout()
        asteroid_discovery_detection_tooltips = [
            (
                "Residual Min SNR",
                self._asteroid_discovery_min_residual_snr_input,
                "Rejects residual detections weaker than this local SNR before tracklet linking. Higher values remove faint noise but can miss dim movers; lower values keep more faint detections and more clutter. Disabled keeps all low-SNR residuals.",
            ),
            (
                "Residual Max SNR",
                self._asteroid_discovery_max_residual_snr_input,
                "Rejects residual detections stronger than this local SNR before linking. Lower values can suppress very bright artifacts or subtraction leftovers; higher values keep more strong detections. Disabled applies no upper limit.",
            ),
            (
                "Point Detection Threshold",
                self._asteroid_discovery_detection_sigma_input,
                "Sigma threshold for the point-source residual finder. Higher values are stricter and produce fewer false positives; lower values are more sensitive but admit more noise.",
            ),
            (
                "Point Detection FWHM",
                self._asteroid_discovery_detection_fwhm_input,
                "Expected point-source width for residual detection. Higher values favor broader or softer point-like residuals; lower values favor tighter peaks.",
            ),
            (
                "Max Residuals / Frame",
                self._asteroid_discovery_max_residuals_per_frame_input,
                "Caps how many residual detections per frame are kept for linking. Higher values preserve more candidates in crowded frames but slow the search; lower values are faster but can drop real movers.",
            ),
            (
                "Residual Detector",
                self._asteroid_discovery_detector_mode_input,
                "Chooses which residual shapes are searched before linking. Point-like favors compact sources, Streak-aware favors trailed movers, and Hybrid searches both for the broadest coverage.",
            ),
            (
                "Streak Min Area",
                self._asteroid_discovery_streak_min_area_px_input,
                "Minimum connected-pixel area required for the streak detector. Higher values reject tiny blobs but can miss short or faint streaks; lower values admit more borderline streak-like artifacts.",
            ),
            (
                "Streak Min Elongation",
                self._asteroid_discovery_streak_min_elongation_input,
                "Minimum length-to-width ratio for streak detections. Higher values keep only clearly elongated streaks; lower values allow rounder shapes and more false streaks.",
            ),
        ]
        for label_text, field, tooltip in asteroid_discovery_detection_tooltips:
            self._add_tooltip_form_row(asteroid_discovery_detection_layout, label_text, field, tooltip)
        asteroid_discovery_detection_group.setLayout(asteroid_discovery_detection_layout)

        asteroid_discovery_tracklets_group = QGroupBox("Tracklets and Sweep")
        asteroid_discovery_tracklets_layout = QFormLayout()
        self._asteroid_discovery_layout = asteroid_discovery_tracklets_layout
        asteroid_discovery_tracklets_tooltips = [
            (
                "Minimum Linked Frames",
                self._asteroid_discovery_min_candidate_frames_input,
                "Minimum number of frames a residual must link across before it is kept as a tracklet. Higher values reject short noisy tracks but can miss brief movers; lower values keep more tentative tracklets and more junk.",
            ),
            (
                "Potential Deflection RMS",
                self._asteroid_discovery_potential_deflection_rms_input,
                "Maximum linear-motion deflection RMS allowed in the stronger Potential Discoveries bucket. Lower values are stricter and leave fewer, cleaner candidates; higher values keep more imperfect tracklets in the main potential bucket.",
            ),
            (
                "Review Deflection RMS",
                self._asteroid_discovery_review_deflection_rms_input,
                "Maximum linear-motion deflection RMS allowed in Borderline Review after a tracklet misses the potential threshold. Lower values suppress more borderline tracklets; higher values keep more manual-review cases. Tracklets above this limit are discarded.",
            ),
            (
                "Final Synthetic Sweep",
                self._asteroid_discovery_enable_synthetic_sweep_input,
                "Runs a final synthetic-tracking velocity-grid sweep after the normal Discover linking pass. In a whole-group run there is one search window; in batched runs each overlapping search window gets its own sweep before aggregation. Enabled can recover fainter movers that never linked cleanly frame-to-frame, but it is the slowest Discover option.",
            ),
            (
                "Sweep Max Motion",
                self._asteroid_discovery_synthetic_sweep_max_motion_px_per_hour_input,
                "Maximum motion rate covered by the final synthetic sweep. Higher values search faster movers but add more velocity vectors and more runtime; lower values keep the sweep tighter and faster.",
            ),
            (
                "Sweep Motion Step",
                self._asteroid_discovery_synthetic_sweep_motion_step_px_per_hour_input,
                "Spacing between tested motion-rate shells in the final synthetic sweep. Lower values are more exhaustive but much slower; higher values are faster but can skip narrow velocity windows.",
            ),
            (
                "Sweep Angle Step",
                self._asteroid_discovery_synthetic_sweep_angle_step_deg_input,
                "Angular spacing between tested directions in the final synthetic sweep. Lower values cover direction space more densely but increase runtime sharply; higher values are faster but coarser.",
            ),
            (
                "Sweep Direction Focus",
                self._asteroid_discovery_synthetic_sweep_direction_focus_input,
                "Controls whether the final synthetic sweep covers the full 360-degree image plane or narrows itself to the direction followed by known main-belt asteroids in the current field. The focused mode runs fewer vectors when that direction can be inferred, and falls back to a full sweep if it cannot.",
            ),
            (
                "Sweep Focus Width",
                self._asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg_input,
                "Half-width of the allowed angle window around the inferred main-belt image-plane direction. Lower values apply a tighter prior and test fewer vectors; higher values are broader and approach a full sweep.",
            ),
            (
                "Sweep Min Stacked SNR",
                self._asteroid_discovery_synthetic_sweep_min_stacked_snr_input,
                "Minimum stacked synthetic-track SNR required before a velocity-stack peak is turned back into a Discover tracklet. Higher values keep only stronger stacked detections; lower values are more sensitive but admit more false positives.",
            ),
            (
                "Save Sweep Stacks",
                self._asteroid_discovery_synthetic_sweep_save_stacks_input,
                "Writes each tested synthetic-sweep velocity stack as a FITS file under synthetic_track beside the current data. This is useful for diagnosing missed movers, but dense sweeps can create many files.",
            ),
        ]
        for label_text, field, tooltip in asteroid_discovery_tracklets_tooltips:
            self._add_tooltip_form_row(asteroid_discovery_tracklets_layout, label_text, field, tooltip)
        asteroid_discovery_tracklets_group.setLayout(asteroid_discovery_tracklets_layout)

        asteroid_discovery_sections_layout.addWidget(asteroid_discovery_preparation_group, 0, 0)
        asteroid_discovery_sections_layout.addWidget(asteroid_discovery_detection_group, 0, 1)
        asteroid_discovery_sections_layout.addWidget(asteroid_discovery_tracklets_group, 1, 0, 1, 2)
        asteroid_discovery_group_layout.addLayout(asteroid_discovery_sections_layout)
        asteroid_discovery_group.setLayout(asteroid_discovery_group_layout)

        asteroid_visual_group = QGroupBox("Visuals")
        asteroid_visual_layout = QVBoxLayout()
        asteroid_visual_layout.addWidget(self._asteroid_visual_show_known_objects_input)
        asteroid_visual_layout.addWidget(self._asteroid_visual_show_potential_discoveries_input)
        asteroid_visual_layout.addWidget(self._asteroid_visual_label_all_objects_input)
        asteroid_visual_layout.addWidget(self._asteroid_visual_show_target_marker_input)
        asteroid_track_object_group = QGroupBox("Tracking")
        asteroid_track_object_layout = QFormLayout()
        asteroid_track_object_layout.addRow("Track Object Anchor", self._asteroid_track_object_position_mode_input)
        asteroid_track_object_group.setLayout(asteroid_track_object_layout)
        asteroid_visual_layout.addWidget(asteroid_track_object_group)
        asteroid_visual_layout.addWidget(self._asteroid_visual_show_all_crosshairs_input)
        asteroid_visual_layout.addWidget(self._asteroid_visual_highlight_selected_object_input)
        asteroid_visual_layout.addWidget(self._asteroid_visual_invert_annotation_colors_input)
        asteroid_target_marker_group = QGroupBox("Target Marker Style")
        asteroid_target_marker_layout = QFormLayout()
        asteroid_target_marker_layout.addRow("Line Color", self._asteroid_target_marker_line_color_button)
        asteroid_target_marker_layout.addRow("Accent Color", self._asteroid_target_marker_accent_color_button)
        asteroid_target_marker_layout.addRow("Label Color", self._asteroid_target_marker_text_color_button)
        asteroid_target_marker_layout.addRow("Outline Color", self._asteroid_target_marker_outline_color_button)
        asteroid_target_marker_layout.addRow("Thickness", self._asteroid_target_marker_line_width_input)
        asteroid_target_marker_group.setLayout(asteroid_target_marker_layout)
        asteroid_visual_layout.addWidget(asteroid_target_marker_group)
        asteroid_visual_group.setLayout(asteroid_visual_layout)
        asteroid_export_group = QGroupBox("Export")
        asteroid_export_layout = QFormLayout()
        asteroid_export_layout.addRow("Blink Frame Time", self._asteroid_blink_frame_duration_input)
        asteroid_export_layout.addRow("GIF Resolution", self._asteroid_gif_export_scale_percent_input)
        asteroid_export_layout.addRow("MP4 Resolution", self._asteroid_mp4_export_scale_percent_input)
        asteroid_export_layout.addRow("GIF Looping", self._asteroid_gif_export_loop_forever_input)
        asteroid_export_group.setLayout(asteroid_export_layout)
        asteroid_search_tab = QWidget()
        asteroid_search_layout = QVBoxLayout()
        asteroid_search_layout.addWidget(asteroid_estimate_group)
        asteroid_search_layout.addWidget(asteroid_discovery_group)
        asteroid_search_layout.addStretch(1)
        asteroid_search_tab.setLayout(asteroid_search_layout)

        asteroid_visual_tab = QWidget()
        asteroid_visual_tab_layout = QVBoxLayout()
        asteroid_visual_description = QLabel(
            "Visual defaults control which overlays are shown by default in asteroid/comet image review and discovery follow-up."
        )
        asteroid_visual_description.setWordWrap(True)
        asteroid_visual_tab_layout.addWidget(asteroid_visual_description)
        asteroid_visual_tab_layout.addWidget(asteroid_visual_group)
        asteroid_visual_tab_layout.addStretch(1)
        asteroid_visual_tab.setLayout(asteroid_visual_tab_layout)

        asteroid_export_tracking_tab = QWidget()
        asteroid_export_tracking_layout = QVBoxLayout()
        asteroid_export_tracking_description = QLabel(
            "Export defaults live here so the main search page stays focused on discovery, review, and synthetic-track workflow defaults."
        )
        asteroid_export_tracking_description.setWordWrap(True)
        asteroid_export_tracking_layout.addWidget(asteroid_export_tracking_description)
        asteroid_export_tracking_layout.addWidget(asteroid_export_group)
        asteroid_export_tracking_layout.addStretch(1)
        asteroid_export_tracking_tab.setLayout(asteroid_export_tracking_layout)

        self._asteroid_search_settings_tab = asteroid_search_tab
        self._asteroid_visual_settings_tab = asteroid_visual_tab
        self._asteroid_export_tracking_settings_tab = asteroid_export_tracking_tab
        self._asteroid_settings_subtabs = QTabWidget()
        self._asteroid_settings_subtabs.addTab(self._asteroid_search_settings_tab, "Search")
        self._asteroid_settings_subtabs.addTab(self._asteroid_visual_settings_tab, "Visuals")
        self._asteroid_settings_subtabs.addTab(self._asteroid_export_tracking_settings_tab, "Export")

        asteroid_layout.addWidget(asteroid_description)
        asteroid_layout.addWidget(self._asteroid_settings_subtabs, stretch=1)
        asteroid_tab.setLayout(asteroid_layout)

        self._general_settings_tab = general_tab
        self._hr_settings_tab = hr_tab
        self._asteroid_settings_tab = asteroid_tab
        setup_tab = QWidget()
        setup_layout = QVBoxLayout()
        setup_description = QLabel(
            "Setup stores telescope, camera, and observing-site details that can be reused in the asteroid/comet information panel and future exports. Pixel scale is calculated from focal length and pixel size."
        )
        setup_description.setWordWrap(True)
        setup_instrument_group = QGroupBox("Instrument")
        setup_instrument_layout = QFormLayout()
        setup_instrument_layout.addRow("Telescope", self._telescope_input)
        setup_instrument_layout.addRow("Focal Length", self._telescope_focal_length_input)
        setup_instrument_layout.addRow("Aperture", self._telescope_aperture_input)
        setup_instrument_layout.addRow("Focal Ratio", self._telescope_focal_ratio_input)
        setup_instrument_layout.addRow("Camera", self._camera_input)
        setup_instrument_layout.addRow("Pixel Size", self._camera_pixel_size_input)
        setup_instrument_layout.addRow("Pixel Scale", self._setup_pixel_scale_input)
        setup_instrument_group.setLayout(setup_instrument_layout)
        setup_site_group = QGroupBox("Observing Site")
        setup_site_layout = QFormLayout()
        setup_site_layout.addRow("Location", self._site_name_input)
        setup_site_layout.addRow("Observing Latitude", self._observing_site_latitude_input)
        setup_site_layout.addRow("Observing Longitude", self._observing_site_longitude_input)
        setup_site_layout.addRow("Observing Elevation", self._observing_site_elevation_input)
        setup_site_layout.addRow("Bortle Scale", self._bortle_scale_input)
        setup_site_group.setLayout(setup_site_layout)
        setup_layout.addWidget(setup_description)
        setup_layout.addWidget(setup_instrument_group)
        setup_layout.addWidget(setup_site_group)
        setup_layout.addStretch(1)
        setup_tab.setLayout(setup_layout)
        self._setup_settings_tab = setup_tab
        sky_explorer_tab = QWidget()
        sky_explorer_layout = QVBoxLayout()
        sky_explorer_description = QLabel(
            "Sky Explorer settings control the manual SIMBAD lookup radius and which background search sources are allowed when Explore resolves a field."
        )
        sky_explorer_description.setWordWrap(True)
        sky_explorer_form_layout = QFormLayout()
        self._sky_explorer_form_layout = sky_explorer_form_layout
        self._add_tooltip_form_row(
            sky_explorer_form_layout,
            "Search Radius",
            self._sky_explorer_simbad_search_radius_arcsec_input,
            "Search radius used by the Sky Explorer image right-click Search action when opening SIMBAD by coordinates. Increase it when the target is slightly offset or catalog positions are imprecise; lower values keep the search tighter.",
        )
        self._add_tooltip_form_row(
            sky_explorer_form_layout,
            "Gaia Max Mag",
            self._sky_explorer_gaia_max_magnitude_input,
            "Upper Gaia G magnitude used by Sky Explorer field-star queries. Lower values keep the field lighter and faster; higher values include fainter Gaia sources.",
        )
        self._add_tooltip_form_row(
            sky_explorer_form_layout,
            "Gaia Hard Cap",
            self._sky_explorer_gaia_hard_cap_enabled_input,
            "Optional hard cap for Gaia Sky Explorer queries. When enabled, Sky Explorer applies both the Gaia magnitude limit and this row cap to reduce very dense-field lookups.",
        )
        self._add_tooltip_form_row(
            sky_explorer_form_layout,
            "Gaia Cap Rows",
            self._sky_explorer_gaia_hard_cap_rows_input,
            "Maximum Gaia rows requested by Sky Explorer when the hard cap is enabled. Lower values reduce dense-field query cost more aggressively.",
        )
        self._add_tooltip_form_row(
            sky_explorer_form_layout,
            "Mag Limit Examples",
            self._sky_explorer_mag_limit_examples_per_bin_input,
            "Number of representative Gaia stars labeled in each 0.5 magnitude interval when the Sky Explorer Mag Limit annotation button is enabled.",
        )
        self._add_tooltip_form_row(
            sky_explorer_form_layout,
            "Mag Limit Marker",
            self._sky_explorer_mag_limit_marker_color_button,
            "Fill color used for Mag Limit representative star markers on the Sky Explorer image.",
        )
        self._add_tooltip_form_row(
            sky_explorer_form_layout,
            "Mag Limit Marker Stroke",
            self._sky_explorer_mag_limit_marker_stroke_color_button,
            "Stroke color used around Mag Limit representative star markers.",
        )
        self._add_tooltip_form_row(
            sky_explorer_form_layout,
            "Mag Limit Marker Stroke Width",
            self._sky_explorer_mag_limit_marker_stroke_width_input,
            "Stroke width for Mag Limit representative star markers. Set to 0 to hide the marker outline.",
        )
        self._add_tooltip_form_row(
            sky_explorer_form_layout,
            "Mag Limit Target Size",
            self._sky_explorer_mag_limit_target_size_input,
            "Radius in pixels used for Mag Limit representative star markers on the Sky Explorer image.",
        )
        self._add_tooltip_form_row(
            sky_explorer_form_layout,
            "Mag Limit Text",
            self._sky_explorer_mag_limit_text_color_button,
            "Text color used for Mag Limit representative star labels on the Sky Explorer image.",
        )
        self._add_tooltip_form_row(
            sky_explorer_form_layout,
            "Mag Limit Text Stroke",
            self._sky_explorer_mag_limit_text_stroke_color_button,
            "Outline color used around Mag Limit representative star labels.",
        )
        self._add_tooltip_form_row(
            sky_explorer_form_layout,
            "Mag Limit Text Stroke Width",
            self._sky_explorer_mag_limit_text_stroke_width_input,
            "Outline width for Mag Limit representative star labels. Set to 0 to disable the text outline.",
        )
        self._add_tooltip_form_row(
            sky_explorer_form_layout,
            "Mag Limit Text Size",
            self._sky_explorer_mag_limit_text_size_input,
            "Font size in points used for Mag Limit representative star labels.",
        )
        self._add_tooltip_form_row(
            sky_explorer_form_layout,
            "Galaxy Mag Limit",
            self._sky_explorer_annotated_galaxy_max_magnitude_enabled_input,
            "When enabled, only galaxies at or brighter than the threshold below are annotated on the image. Galaxies with unknown magnitude are skipped while this limit is active.",
        )
        self._add_tooltip_form_row(
            sky_explorer_form_layout,
            "Galaxy Max Mag",
            self._sky_explorer_annotated_galaxy_max_magnitude_input,
            "Upper magnitude limit used when the galaxy annotation magnitude filter is enabled. Lower values keep only brighter annotated galaxies.",
        )
        self._add_tooltip_form_row(
            sky_explorer_form_layout,
            "Galaxy Shape Only",
            self._sky_explorer_annotated_galaxy_require_shape_metadata_input,
            "When enabled, Sky Explorer only annotates galaxies that include enough catalog geometry to draw an oriented ellipse instead of a generic circle.",
        )
        self._add_tooltip_form_row(
            sky_explorer_form_layout,
            "Extended Nebula Scale",
            self._sky_explorer_scale_extended_nebulae_input,
            "Expands Sky Explorer nebula overlays from catalog diameters so broad emission, reflection, and dark-nebula structures better cover their visible extent. Disable this for strict catalog-size circles.",
        )
        self._add_tooltip_form_row(
            sky_explorer_form_layout,
            "Scale Stroke Width",
            self._sky_explorer_scale_overlay_strokes_input,
            "Scales Sky Explorer marker outline thickness with annotation size so large nebula and galaxy outlines remain readable. Disable this for fixed-width outlines.",
        )
        self._add_tooltip_form_row(
            sky_explorer_form_layout,
            "Marker Color Relation",
            self._sky_explorer_marker_color_relation_input,
            "Controls whether generated Sky Explorer type colors use the brighter related color for marker fill and the darker color for stroke, or invert that relation.",
        )
        self._add_tooltip_form_row(
            sky_explorer_form_layout,
            "Text Color Relation",
            self._sky_explorer_text_color_relation_input,
            "Controls whether Sky Explorer labels without an explicit text override use the darker or brighter related type color.",
        )
        self._add_tooltip_form_row(
            sky_explorer_form_layout,
            "Fill Opacity",
            self._sky_explorer_fill_opacity_input,
            "Opacity used for filled Sky Explorer object markers. Lower values keep the image visible underneath large galaxies and nebulae; higher values make filled markers stronger.",
        )
        self._add_tooltip_form_row(
            sky_explorer_form_layout,
            "Stroke Opacity",
            self._sky_explorer_stroke_opacity_input,
            "Opacity used for Sky Explorer marker outlines. Lower values make dense annotation fields quieter; higher values make object borders more prominent.",
        )
        sky_explorer_group_colors_group = QGroupBox("Object Group Colors")
        sky_explorer_group_colors_layout = QFormLayout()
        self._sky_explorer_object_group_color_layout = sky_explorer_group_colors_layout
        for group_key, group_title, _default_color in sky_explorer_object_type_group_definitions():
            button = self._sky_explorer_object_group_color_buttons[group_key]
            self._add_tooltip_form_row(
                sky_explorer_group_colors_layout,
                group_title,
                button,
                f"Main hue used for {group_title} Sky Explorer annotations. Object types inside this group keep nearby related colors.",
            )
        sky_explorer_group_colors_group.setLayout(sky_explorer_group_colors_layout)
        sky_explorer_sources_group = QGroupBox("Catalog Sources")
        sky_explorer_sources_layout = QVBoxLayout()
        sky_explorer_sources_layout.setContentsMargins(8, 8, 8, 8)
        sky_explorer_sources_layout.setSpacing(6)
        for layer_key in SKY_EXPLORER_LAYER_ORDER:
            checkbox = self._sky_explorer_layer_inputs.get(layer_key)
            if checkbox is not None:
                sky_explorer_sources_layout.addWidget(checkbox)
        sky_explorer_sources_group.setLayout(sky_explorer_sources_layout)
        sky_explorer_layout.addWidget(sky_explorer_description)
        sky_explorer_layout.addLayout(sky_explorer_form_layout)
        sky_explorer_layout.addWidget(sky_explorer_group_colors_group)
        sky_explorer_layout.addWidget(sky_explorer_sources_group)
        sky_explorer_layout.addStretch(1)
        sky_explorer_tab.setLayout(sky_explorer_layout)
        self._sky_explorer_settings_tab = sky_explorer_tab
        advanced_description = QLabel(
            "Advanced controls for background source actions. Find Better Fit and Increase SNR both run from Source Results, and Increase SNR uses conservative period-aware binning to derive cleaner light curves while preserving original measurements internally."
        )
        advanced_description.setWordWrap(True)
        advanced_form_layout = QFormLayout()
        self._advanced_form_layout = advanced_form_layout
        advanced_tooltips = [
            (
                "Shared Analysis Workers",
                self._shared_parallel_workers_input,
                "Controls the shared worker limit used by photometry processing, Deep Stack alignment, Synthetic Track, Calculate Period, and Pull Period. Higher values can speed larger jobs but use more CPU; Auto lets the app choose.",
            ),
            (
                "Sky Atlas Overlay Cache Size",
                self._sky_atlas_custom_overlay_cache_max_long_edge_input,
                "Maximum long edge saved for custom Sky Atlas overlay imports. Lower values use less disk and memory; higher values preserve more detail. Applies to newly imported overlays.",
            ),
            (
                "Increase SNR Max Period Fraction",
                self._snr_binning_max_period_fraction_input,
                "Largest fraction of the fitted period allowed inside one Increase SNR bin. Higher values allow longer bins and stronger smoothing, but can blur real variability; lower values preserve shape but gain less SNR.",
            ),
            (
                "Increase SNR Max Duration",
                self._snr_binning_max_absolute_duration_seconds_input,
                "Hard time-span cap for each Increase SNR bin. Higher values allow more aggressive averaging; lower values preserve time resolution.",
            ),
            (
                "Increase SNR Target SNR",
                self._snr_binning_target_snr_input,
                "Target signal-to-noise ratio for each derived bin. Higher values push the workflow toward larger bins and smoother curves; lower values stop earlier and preserve more cadence.",
            ),
            (
                "Increase SNR Max Frames/Bin",
                self._snr_binning_max_frames_per_bin_input,
                "Maximum number of measurements allowed in a single derived bin. Higher values permit stronger averaging; lower values keep finer time sampling.",
            ),
            (
                "Increase SNR Min Frames/Bin",
                self._snr_binning_min_frames_per_bin_input,
                "Minimum number of measurements required in a derived bin. Higher values demand denser bins and may skip sparse regions; lower values allow smaller bins.",
            ),
            (
                "Increase SNR Type Awareness",
                self._snr_binning_type_aware_thresholds_input,
                "Adjusts Increase SNR period-fraction limits by variability type. Enabled uses more conservative settings for sharp variables and looser ones for smooth variables; disabled uses one shared limit.",
            ),
            (
                "Sharp-Variable Fraction",
                self._snr_binning_sharp_period_fraction_input,
                "Period-fraction cap used for sharp or eclipse-like variables when type awareness is enabled. Higher values allow broader bins and can smear sharp events; lower values protect fast structure.",
            ),
            (
                "Smooth-Variable Fraction",
                self._snr_binning_smooth_period_fraction_input,
                "Period-fraction cap used for smoother variables when type awareness is enabled. Higher values allow more averaging; lower values preserve more light-curve detail.",
            ),
            (
                "Weighted Flux Binning",
                self._snr_binning_weighted_flux_binning_input,
                "Prefer flux-space weighting when deriving higher-SNR bins. Enabled usually gives better error-aware bins; disabled avoids that weighting path.",
            ),
            (
                "Magnitude Fallback",
                self._snr_binning_allow_magnitude_fallback_input,
                "Allows direct magnitude averaging when flux-space binning is not suitable. Enabled is more permissive and produces more fallback bins; disabled skips that fallback entirely.",
            ),
            (
                "Minimum Valid Points/Bin",
                self._snr_binning_minimum_valid_points_per_bin_input,
                "Minimum number of non-rejected measurements needed after filtering inside a bin. Higher values require denser bins; lower values keep more sparse bins.",
            ),
            (
                "Outlier Rejection",
                self._snr_binning_outlier_rejection_enabled_input,
                "Applies sigma-clipping inside each Increase SNR bin. Enabled removes outliers but can reject real excursions; disabled keeps every point.",
            ),
            (
                "Sigma-Clip Threshold",
                self._snr_binning_sigma_clip_threshold_input,
                "Clipping threshold used when outlier rejection is enabled. Lower values reject more points and are stricter; higher values keep more variation.",
            ),
            (
                "Increase SNR Dataset Mode",
                self._snr_binning_dataset_mode_input,
                "Controls how derived higher-SNR measurements are surfaced. Create derived dataset keeps the original processed view alongside derived results; Replace processed view swaps the visible processed view to the derived version.",
            ),
            (
                "Increase SNR Measurement Scope",
                self._snr_binning_apply_to_selected_measurements_only_input,
                "Limits Increase SNR to the currently filtered measurements for each source. Enabled respects the active filter view; disabled uses the full available measurement set.",
            ),
            (
                "Periodless Fallback",
                self._snr_binning_allow_periodless_fallback_input,
                "Allows Increase SNR to fall back to non-period-aware binning when no usable period is available. Enabled is more permissive; disabled skips sources without a usable period.",
            ),
            (
                "Stop When Match Index Reaches",
                self._comparison_fit_stop_match_index_input,
                "Stops Find Better Fit early once a strong enough match index is reached. Lower values stop sooner and search fewer combinations; higher values search longer for a better match. Disabled searches until the normal end.",
            ),
            (
                "Find Better Fit Workers",
                self._comparison_fit_parallel_workers_input,
                "Controls how many workers Find Better Fit can use while evaluating comparison sets. Higher values can speed broader searches but use more CPU; lower values reduce load. Auto lets the app choose.",
            ),
            (
                "Multiple Target Selection",
                self._comparison_fit_allow_multiple_targets_input,
                "Allows Find Better Fit to queue more than one selected Source Results target at a time. Enabled supports multi-target runs; disabled keeps the workflow single-target only.",
            ),
            (
                "Eclipsing-Binary Retry Near 50",
                self._comparison_fit_eclipsing_binary_match_tolerance_input,
                "Retries near-50 match-index results with the eclipsing-binary period convention. Higher values retry a wider band around 50; lower values retry only very close cases. Disabled turns off this retry.",
            ),
            (
                "Fallback Candidate Pool Size",
                self._comparison_fit_fallback_candidate_pool_size_input,
                "How many magnitude-similar fallback comparison stars Find Better Fit may try when the main search falls short. Higher values widen the rescue search; lower values keep it tighter. Disabled skips the fallback pool.",
            ),
            (
                "Fallback Magnitude Tolerance",
                self._comparison_fit_fallback_magnitude_tolerance_input,
                "Maximum magnitude difference allowed when building the fallback comparison-star pool. Higher values permit a looser fallback search; lower values keep fallback stars closer in brightness. Disabled turns off this limit-based fallback.",
            ),
            (
                "Scientific PDF DPI",
                self._scientific_light_curve_pdf_dpi_input,
                "Resolution used for scientific PDF-backed light-curve export rendering. Higher DPI produces sharper output and larger files; lower DPI exports faster and keeps files smaller.",
            ),
            (
                "Scientific PDF Paper Size",
                self._scientific_light_curve_pdf_paper_size_input,
                "Paper size used for scientific light-curve PDF export. Larger page formats leave more layout room; smaller formats keep a more compact figure.",
            ),
        ]
        for label_text, field, tooltip in advanced_tooltips:
            self._add_tooltip_form_row(advanced_form_layout, label_text, field, tooltip)
        advanced_group = QGroupBox("Advanced")
        advanced_group_layout = QVBoxLayout()
        advanced_group_layout.addWidget(advanced_description)
        advanced_group_layout.addLayout(advanced_form_layout)
        advanced_group.setLayout(advanced_group_layout)
        self._advanced_settings_group = advanced_group
        general_layout.addWidget(advanced_group)
        general_layout.addStretch(1)
        general_tab.setLayout(general_layout)
        self._settings_tabs = QTabWidget()
        self._settings_tabs.addTab(self._general_settings_tab, "General")
        self._settings_tabs.addTab(self._differential_photometry_tab, "Differential Photometry")
        self._settings_tabs.addTab(self._hr_settings_tab, "HR Diagram")
        self._settings_tabs.addTab(self._asteroid_settings_tab, "Asteroid/Comet")
        self._settings_tabs.addTab(self._sky_explorer_settings_tab, "Sky Explorer")
        self._settings_tabs.addTab(self._setup_settings_tab, "Setup")

        default_button = QPushButton("Default")
        default_button.clicked.connect(self._restore_defaults)
        save_button = QPushButton("Save")
        save_button.clicked.connect(self.accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(default_button)
        button_row.addWidget(cancel_button)
        button_row.addWidget(save_button)

        root_layout = QVBoxLayout()
        root_layout.addWidget(self._settings_tabs, stretch=1)
        root_layout.addLayout(button_row)
        self.setLayout(root_layout)
        self._update_aperture_inputs()
        self._update_reference_limit_inputs()
        self._update_preview_limit_inputs()
        self._update_snr_binning_inputs()
        self._update_asteroid_estimate_inputs()
        self._update_settings_location_inputs()
        self._update_hr_plot_size_inputs()
        self._update_setup_derived_fields()

    def _add_tooltip_form_row(self, layout: QFormLayout, label_text: str, field: QWidget, tooltip: str) -> None:
        label = QLabel(label_text)
        label.setToolTip(tooltip)
        label.setBuddy(field)
        field.setToolTip(tooltip)
        if isinstance(field, QComboBox) and field.isEditable() and field.lineEdit() is not None:
            field.lineEdit().setToolTip(tooltip)
        layout.addRow(label, field)

    def build_settings(self) -> AppSettings:
        limit_mode = self._variable_star_limit_mode_input.currentData()
        if not isinstance(limit_mode, VariableStarLimitMode):
            limit_mode = VariableStarLimitMode(str(limit_mode).strip().lower())
        aperture_mode = PhotometryApertureMode.FWHM_SCALED
        return replace(
            self._settings,
            astrometry_api_key=self._api_key_input.text().strip() or None,
            cache_dir=Path(self._cache_dir_input.text()).expanduser(),
            config_path=self._settings.config_path,
            interface_tips_enabled=self._interface_tips_enabled_input.isChecked(),
            show_mode_launcher_on_startup=self._show_mode_launcher_on_startup_input.isChecked(),
            nearby_reference_count=self._nearby_reference_count_input.value(),
            shared_parallel_workers=self._shared_parallel_workers_input.value(),
            sky_atlas_custom_overlay_cache_max_long_edge=self._sky_atlas_custom_overlay_cache_max_long_edge_input.value(),
            sky_explorer_simbad_search_radius_arcsec=self._sky_explorer_simbad_search_radius_arcsec_input.value(),
            sky_explorer_gaia_max_magnitude=self._sky_explorer_gaia_max_magnitude_input.value(),
            sky_explorer_gaia_hard_cap_enabled=self._sky_explorer_gaia_hard_cap_enabled_input.isChecked(),
            sky_explorer_gaia_hard_cap_rows=self._sky_explorer_gaia_hard_cap_rows_input.value(),
            sky_explorer_mag_limit_examples_per_bin=self._sky_explorer_mag_limit_examples_per_bin_input.value(),
            sky_explorer_mag_limit_marker_color=self._sky_explorer_mag_limit_marker_color,
            sky_explorer_mag_limit_marker_stroke_color=self._sky_explorer_mag_limit_marker_stroke_color,
            sky_explorer_mag_limit_marker_stroke_width=self._sky_explorer_mag_limit_marker_stroke_width_input.value(),
            sky_explorer_mag_limit_target_size=self._sky_explorer_mag_limit_target_size_input.value(),
            sky_explorer_mag_limit_text_color=self._sky_explorer_mag_limit_text_color,
            sky_explorer_mag_limit_text_stroke_color=self._sky_explorer_mag_limit_text_stroke_color,
            sky_explorer_mag_limit_text_stroke_width=self._sky_explorer_mag_limit_text_stroke_width_input.value(),
            sky_explorer_mag_limit_text_size=self._sky_explorer_mag_limit_text_size_input.value(),
            sky_explorer_annotated_galaxy_max_magnitude_enabled=self._sky_explorer_annotated_galaxy_max_magnitude_enabled_input.isChecked(),
            sky_explorer_annotated_galaxy_max_magnitude=self._sky_explorer_annotated_galaxy_max_magnitude_input.value(),
            sky_explorer_annotated_galaxy_require_shape_metadata=self._sky_explorer_annotated_galaxy_require_shape_metadata_input.isChecked(),
            sky_explorer_scale_extended_nebulae=self._sky_explorer_scale_extended_nebulae_input.isChecked(),
            sky_explorer_scale_overlay_strokes=self._sky_explorer_scale_overlay_strokes_input.isChecked(),
            sky_explorer_marker_color_relation=str(self._sky_explorer_marker_color_relation_input.currentData() or "stroke_dark_fill_bright"),
            sky_explorer_text_color_relation=str(self._sky_explorer_text_color_relation_input.currentData() or "dark"),
            sky_explorer_fill_opacity=self._sky_explorer_fill_opacity_input.value(),
            sky_explorer_stroke_opacity=self._sky_explorer_stroke_opacity_input.value(),
            sky_explorer_object_group_color_overrides=dict(self._sky_explorer_object_group_color_overrides),
            sky_explorer_enabled_layers=tuple(
                layer_key
                for layer_key in SKY_EXPLORER_LAYER_ORDER
                if self._sky_explorer_layer_inputs.get(layer_key) is not None
                and self._sky_explorer_layer_inputs[layer_key].isChecked()
            ),
            photometry_parallel_workers=self._shared_parallel_workers_input.value(),
            frame_edge_margin_percent=self._frame_edge_margin_percent_input.value(),
            saturation_filter_enabled=self._saturation_filter_enabled_input.isChecked(),
            image_display_stretch_mode=str(self._image_display_stretch_mode_input.currentData() or "stf"),
            image_display_brightness=self._image_display_brightness_input.value(),
            image_display_contrast=self._image_display_contrast_input.value(),
            image_display_inverted=self._image_display_inverted_input.isChecked(),
            asteroid_estimate_snr_threshold=self._asteroid_estimate_snr_threshold_input.value(),
            asteroid_estimate_start_magnitude=self._asteroid_estimate_start_magnitude_input.value(),
            asteroid_manual_magnitude_limit_override_enabled=self._asteroid_manual_magnitude_limit_override_enabled_input.isChecked(),
            asteroid_manual_magnitude_limit_override=self._asteroid_manual_magnitude_limit_override_input.value(),
            asteroid_estimate_stars_per_bin=self._asteroid_estimate_stars_per_bin_input.value(),
            asteroid_estimate_required_visible_stars=self._asteroid_estimate_required_visible_stars_input.value(),
            asteroid_estimate_annotate_lowest_mag_stars=self._asteroid_estimate_annotate_lowest_mag_stars_input.isChecked(),
            asteroid_visual_show_known_objects=self._asteroid_visual_show_known_objects_input.isChecked(),
            asteroid_visual_show_object_markers=self._asteroid_visual_show_known_objects_input.isChecked(),
            asteroid_visual_show_potential_discoveries=self._asteroid_visual_show_potential_discoveries_input.isChecked(),
            asteroid_visual_label_all_objects=self._asteroid_visual_label_all_objects_input.isChecked(),
            asteroid_visual_show_target_marker=self._asteroid_visual_show_target_marker_input.isChecked(),
            asteroid_track_object_position_mode=str(self._asteroid_track_object_position_mode_input.currentData() or "predicted"),
            asteroid_visual_show_all_crosshairs=self._asteroid_visual_show_all_crosshairs_input.isChecked(),
            asteroid_visual_highlight_selected_object=self._asteroid_visual_highlight_selected_object_input.isChecked(),
            asteroid_visual_invert_annotation_colors=self._asteroid_visual_invert_annotation_colors_input.isChecked(),
            asteroid_target_marker_line_color=self._asteroid_target_marker_line_color,
            asteroid_target_marker_accent_color=self._asteroid_target_marker_accent_color,
            asteroid_target_marker_text_color=self._asteroid_target_marker_text_color,
            asteroid_target_marker_outline_color=self._asteroid_target_marker_outline_color,
            asteroid_target_marker_line_width=self._asteroid_target_marker_line_width_input.value(),
            asteroid_blink_frame_duration_ms=int(self._asteroid_blink_frame_duration_input.currentData() or 50),
            asteroid_gif_export_scale_percent=self._asteroid_gif_export_scale_percent_input.value(),
            asteroid_mp4_export_scale_percent=self._asteroid_mp4_export_scale_percent_input.value(),
            asteroid_gif_export_loop_forever=self._asteroid_gif_export_loop_forever_input.isChecked(),
            synthetic_tracking_crop_radius_pixels=self._synthetic_tracking_crop_radius_input.value(),
            synthetic_tracking_integration_mode=str(self._synthetic_tracking_integration_mode_input.currentData() or "average"),
            synthetic_tracking_weight_mode=str(self._synthetic_tracking_weight_mode_input.currentData() or "psf_signal_weight"),
            synthetic_tracking_rejection_mode=str(self._synthetic_tracking_rejection_mode_input.currentData() or "no_rejection"),
            synthetic_tracking_backend_preference=str(self._synthetic_tracking_backend_preference_input.currentData() or "auto"),
            synthetic_tracking_combine_mode=(
                "sigma_clipped_mean"
                if str(self._synthetic_tracking_integration_mode_input.currentData() or "average") == "average"
                and str(self._synthetic_tracking_rejection_mode_input.currentData() or "no_rejection") == "sigma_clipping"
                else "mean"
            ),
            synthetic_tracking_allow_mixed_all_group=self._synthetic_tracking_allow_mixed_all_group_input.isChecked(),
            synthetic_tracking_advanced_enabled=self._synthetic_tracking_advanced_enabled_input.isChecked(),
            reference_star_min_magnitude=(
                min(self._reference_star_min_magnitude_input.value(), self._reference_star_max_magnitude_input.value())
                if self._reference_star_magnitude_range_enabled_input.isChecked()
                else None
            ),
            reference_star_max_magnitude=(
                max(self._reference_star_min_magnitude_input.value(), self._reference_star_max_magnitude_input.value())
                if self._reference_star_magnitude_range_enabled_input.isChecked()
                else None
            ),
            observer_code=self._observer_code_input.text().strip(),
            observer_name=self._observer_name_input.text().strip(),
            organization=self._organization_input.text().strip(),
            site_name=self._site_name_input.text().strip(),
            observing_site_latitude_deg=self._parse_optional_float(self._observing_site_latitude_input.text(), minimum=-90.0, maximum=90.0),
            observing_site_longitude_deg=self._parse_optional_float(self._observing_site_longitude_input.text(), minimum=-180.0, maximum=180.0),
            observing_site_elevation_m=self._parse_optional_float(self._observing_site_elevation_input.text(), minimum=-500.0, maximum=12000.0),
            telescope=self._telescope_input.text().strip(),
            telescope_focal_length_mm=self._optional_float_spin_value(self._telescope_focal_length_input),
            telescope_aperture_mm=self._optional_float_spin_value(self._telescope_aperture_input),
            telescope_focal_ratio=self._optional_float_spin_value(self._telescope_focal_ratio_input),
            camera=self._camera_input.text().strip(),
            camera_pixel_size_um=self._optional_float_spin_value(self._camera_pixel_size_input),
            bortle_scale=None if self._bortle_scale_input.value() <= 0 else self._bortle_scale_input.value(),
            filter_system=self._filter_system_input.currentText().strip(),
            aavso_chart_id=self._aavso_chart_id_input.text().strip(),
            observation_timezone=self._observation_timezone_input.currentText().strip() or "UTC",
            time_standard=self._time_standard_input.currentText().strip() or "UTC",
            transformed=self._transformed_input.isChecked(),
            reduction_notes=self._reduction_notes_input.toPlainText().strip(),
            photometry_aperture_mode=aperture_mode,
            aperture_radius_pixels=self._aperture_radius_pixels_input.value(),
            annulus_inner_radius_pixels=self._annulus_inner_radius_pixels_input.value(),
            annulus_outer_radius_pixels=self._annulus_outer_radius_pixels_input.value(),
            aperture_radius_fwhm_scale=self._aperture_radius_fwhm_scale_input.value(),
            annulus_inner_radius_fwhm_scale=self._annulus_inner_radius_fwhm_scale_input.value(),
            annulus_outer_radius_fwhm_scale=self._annulus_outer_radius_fwhm_scale_input.value(),
            variable_star_limit_mode=limit_mode,
            variable_star_limit_value=self._variable_star_limit_value_input.value(),
            variable_star_designation_filters=[family for family, checkbox in self._designation_checkboxes.items() if checkbox.isChecked()] or list(VariableStarDesignationFamily),
            calculate_period_parallel_workers=self._shared_parallel_workers_input.value(),
            literature_period_parallel_workers=self._shared_parallel_workers_input.value(),
            snr_binning_max_period_fraction=self._snr_binning_max_period_fraction_input.value(),
            snr_binning_max_absolute_duration_seconds=float(self._snr_binning_max_absolute_duration_seconds_input.value()),
            snr_binning_target_snr=self._snr_binning_target_snr_input.value(),
            snr_binning_max_frames_per_bin=self._snr_binning_max_frames_per_bin_input.value(),
            snr_binning_min_frames_per_bin=self._snr_binning_min_frames_per_bin_input.value(),
            snr_binning_type_aware_thresholds=self._snr_binning_type_aware_thresholds_input.isChecked(),
            snr_binning_sharp_period_fraction=self._snr_binning_sharp_period_fraction_input.value(),
            snr_binning_smooth_period_fraction=self._snr_binning_smooth_period_fraction_input.value(),
            snr_binning_weighted_flux_binning=self._snr_binning_weighted_flux_binning_input.isChecked(),
            snr_binning_allow_magnitude_fallback=self._snr_binning_allow_magnitude_fallback_input.isChecked(),
            snr_binning_minimum_valid_points_per_bin=self._snr_binning_minimum_valid_points_per_bin_input.value(),
            snr_binning_outlier_rejection_enabled=self._snr_binning_outlier_rejection_enabled_input.isChecked(),
            snr_binning_sigma_clip_threshold=self._snr_binning_sigma_clip_threshold_input.value(),
            snr_binning_dataset_mode=str(self._snr_binning_dataset_mode_input.currentData() or "derived"),
            snr_binning_apply_to_selected_measurements_only=self._snr_binning_apply_to_selected_measurements_only_input.isChecked(),
            snr_binning_allow_periodless_fallback=self._snr_binning_allow_periodless_fallback_input.isChecked(),
            comparison_fit_stop_match_index=self._comparison_fit_stop_match_index_input.value(),
            comparison_fit_parallel_workers=self._comparison_fit_parallel_workers_input.value(),
            asteroid_search_parallel_workers=self._asteroid_search_parallel_workers_input.value(),
            asteroid_discovery_min_residual_snr=self._asteroid_discovery_min_residual_snr_input.value(),
            asteroid_discovery_max_residual_snr=self._asteroid_discovery_max_residual_snr_input.value(),
            asteroid_discovery_frames_per_batch=self._asteroid_discovery_frames_per_batch_input.value(),
            asteroid_discovery_binning_factor=int(self._asteroid_discovery_binning_factor_input.currentData() or 1),
            asteroid_discovery_use_temporary_cache=self._asteroid_discovery_use_temporary_cache_input.isChecked(),
            asteroid_discovery_min_candidate_frames=self._asteroid_discovery_min_candidate_frames_input.value(),
            asteroid_discovery_detection_sigma=self._asteroid_discovery_detection_sigma_input.value(),
            asteroid_discovery_detection_fwhm=self._asteroid_discovery_detection_fwhm_input.value(),
            asteroid_discovery_max_residuals_per_frame=self._asteroid_discovery_max_residuals_per_frame_input.value(),
            asteroid_discovery_edge_margin_px=self._asteroid_discovery_edge_margin_px_input.value(),
            asteroid_discovery_detector_mode=str(self._asteroid_discovery_detector_mode_input.currentData() or "hybrid"),
            asteroid_discovery_streak_min_area_px=self._asteroid_discovery_streak_min_area_px_input.value(),
            asteroid_discovery_streak_min_elongation=self._asteroid_discovery_streak_min_elongation_input.value(),
            asteroid_discovery_potential_deflection_rms_px=self._asteroid_discovery_potential_deflection_rms_input.value(),
            asteroid_discovery_review_deflection_rms_px=max(self._asteroid_discovery_potential_deflection_rms_input.value(), self._asteroid_discovery_review_deflection_rms_input.value()),
            asteroid_discovery_enable_synthetic_sweep=self._asteroid_discovery_enable_synthetic_sweep_input.isChecked(),
            asteroid_discovery_synthetic_sweep_max_motion_px_per_hour=self._asteroid_discovery_synthetic_sweep_max_motion_px_per_hour_input.value(),
            asteroid_discovery_synthetic_sweep_motion_step_px_per_hour=min(
                self._asteroid_discovery_synthetic_sweep_max_motion_px_per_hour_input.value(),
                self._asteroid_discovery_synthetic_sweep_motion_step_px_per_hour_input.value(),
            ),
            asteroid_discovery_synthetic_sweep_angle_step_deg=self._asteroid_discovery_synthetic_sweep_angle_step_deg_input.value(),
            asteroid_discovery_synthetic_sweep_direction_focus=str(self._asteroid_discovery_synthetic_sweep_direction_focus_input.currentData() or "all_directions"),
            asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg=self._asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg_input.value(),
            asteroid_discovery_synthetic_sweep_min_stacked_snr=self._asteroid_discovery_synthetic_sweep_min_stacked_snr_input.value(),
            asteroid_discovery_synthetic_sweep_save_stacks=self._asteroid_discovery_synthetic_sweep_save_stacks_input.isChecked(),
            comparison_fit_allow_multiple_targets=self._comparison_fit_allow_multiple_targets_input.isChecked(),
            comparison_fit_eclipsing_binary_match_tolerance=self._comparison_fit_eclipsing_binary_match_tolerance_input.value(),
            comparison_fit_fallback_candidate_pool_size=self._comparison_fit_fallback_candidate_pool_size_input.value(),
            comparison_fit_fallback_magnitude_tolerance=self._comparison_fit_fallback_magnitude_tolerance_input.value(),
            scientific_light_curve_pdf_dpi=self._scientific_light_curve_pdf_dpi_input.value(),
            scientific_light_curve_pdf_paper_size=str(self._scientific_light_curve_pdf_paper_size_input.currentData() or "Letter"),
            hr_max_sources=self._hr_max_sources_input.value(),
            hr_table_row_limit=self._hr_table_row_limit_input.value(),
            hr_plot_require_parallax=self._hr_plot_require_parallax_input.isChecked(),
            hr_plot_color_saturation=self._hr_plot_color_saturation_input.value(),
            hr_plot_point_opacity=self._hr_plot_point_opacity_input.value(),
            hr_selection_circle_color=self._hr_selection_circle_color,
            hr_selection_circle_opacity=self._hr_selection_circle_opacity_input.value(),
            hr_selection_circle_size_factor=self._hr_selection_circle_size_factor_input.value(),
            hr_plot_hide_flagged=self._hr_plot_hide_flagged_input.isChecked(),
            hr_plot_hide_saturated=self._hr_plot_hide_saturated_input.isChecked(),
            hr_search_catalog_names=self._hr_search_catalog_names_input.isChecked(),
            hr_search_catalog_names_magnitude_threshold=self._hr_search_catalog_names_magnitude_threshold_input.value(),
            hr_plot_apparent_magnitude_min=min(self._hr_plot_apparent_mag_min_input.value(), self._hr_plot_apparent_mag_max_input.value()),
            hr_plot_apparent_magnitude_max=max(self._hr_plot_apparent_mag_min_input.value(), self._hr_plot_apparent_mag_max_input.value()),
            hr_plot_marker_size_mode=str(self._hr_plot_marker_size_mode_input.currentData() or "scaled"),
            hr_plot_fixed_marker_size=self._hr_plot_fixed_marker_size_input.value(),
            hr_motion_vector_color=self._hr_motion_vector_color,
            hr_motion_vector_width=self._hr_motion_vector_width_input.value(),
            preview_variable_star_max_count=self._preview_variable_star_max_count_input.value(),
            preview_variable_star_min_magnitude=(
                min(self._preview_variable_star_min_magnitude_input.value(), self._preview_variable_star_max_magnitude_input.value())
                if self._preview_variable_star_magnitude_range_enabled_input.isChecked()
                else None
            ),
            preview_variable_star_max_magnitude=(
                max(self._preview_variable_star_min_magnitude_input.value(), self._preview_variable_star_max_magnitude_input.value())
                if self._preview_variable_star_magnitude_range_enabled_input.isChecked()
                else None
            ),
            theme=self._theme,
            custom_theme_colors=dict(self._custom_theme_colors),
        )

    def _configure_float_spin_box(
        self,
        widget: QDoubleSpinBox,
        value: float,
        minimum: float,
        maximum: float,
        suffix: str,
    ) -> None:
        widget.setDecimals(2)
        widget.setRange(minimum, maximum)
        widget.setSingleStep(0.1)
        widget.setValue(value)
        widget.setSuffix(suffix)

    def _configure_optional_float_spin_box(
        self,
        widget: QDoubleSpinBox,
        value: float | None,
        minimum: float,
        maximum: float,
        suffix: str,
        *,
        decimals: int = 2,
        step: float = 0.1,
    ) -> None:
        widget.setDecimals(decimals)
        widget.setRange(0.0, maximum)
        widget.setSpecialValueText("Unknown")
        widget.setSingleStep(step)
        widget.setSuffix(suffix)
        widget.setValue(0.0 if value is None else min(maximum, max(minimum, float(value))))

    def _optional_float_spin_value(self, widget: QDoubleSpinBox) -> float | None:
        return None if widget.value() <= 0.0 else float(widget.value())

    def _optional_float_text(self, value: float | None) -> str:
        if value is None:
            return ""
        return f"{float(value):g}"

    def _parse_optional_float(self, text: str, *, minimum: float, maximum: float) -> float | None:
        normalized = text.strip()
        if not normalized:
            return None
        try:
            numeric = float(normalized)
        except ValueError:
            return None
        return min(maximum, max(minimum, numeric))

    def _browse_cache_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Select cache directory", self._cache_dir_input.text())
        if selected:
            self._cache_dir_input.setText(selected)

    def _clear_cache(self) -> None:
        cache_dir = Path(self._cache_dir_input.text()).expanduser()
        reply = QMessageBox.question(
            self,
            "Clear Cache",
            f"Delete all cached data in\n{cache_dir}\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        QMessageBox.information(self, "Cache Cleared", f"Cache directory cleared:\n{cache_dir}")

    def _clear_settings(self) -> None:
        config_path = self._settings.config_path.expanduser()
        legacy_config_path = self._root_path / ".photometry-settings.json"
        reply = QMessageBox.question(
            self,
            "Clear Settings",
            "Reset all settings to factory defaults and delete the saved settings file?\n\n"
            "Click Save after this to write the defaults, or Cancel to discard the reset.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for path in (config_path, legacy_config_path):
            if path.exists():
                path.unlink()
        self._restore_defaults()
        QMessageBox.information(self, "Settings Cleared", "Settings were reset to factory defaults in this dialog.")

    def selected_config_path_override(self) -> Path | None:
        if self._use_default_settings_location_input.isChecked():
            return None
        selected_text = self._settings_location_input.text().strip()
        return Path(selected_text).expanduser() if selected_text else None

    def _browse_settings_location(self) -> None:
        selected, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Select settings file location",
            self._settings_location_input.text() or str(self._default_config_path),
            "JSON Files (*.json);;All Files (*)",
        )
        if selected:
            self._settings_location_input.setText(selected)

    def _update_settings_location_inputs(self) -> None:
        use_default = self._use_default_settings_location_input.isChecked()
        self._settings_location_input.setEnabled(not use_default)
        self._settings_location_browse_button.setEnabled(not use_default)
        if use_default:
            self._settings_location_input.setText(str(self._default_config_path))

    def _update_setup_derived_fields(self) -> None:
        preview_settings = replace(
            self._settings,
            telescope_focal_length_mm=self._optional_float_spin_value(self._telescope_focal_length_input),
            camera_pixel_size_um=self._optional_float_spin_value(self._camera_pixel_size_input),
        )
        pixel_scale = setup_pixel_scale_arcsec_per_pixel(preview_settings)
        if pixel_scale is None:
            self._setup_pixel_scale_input.clear()
            return
        self._setup_pixel_scale_input.setText(f"{pixel_scale:.3f} arcsec/pixel")

    def _restore_defaults(self) -> None:
        defaults = self._default_settings
        self._theme = defaults.theme
        self._custom_theme_colors = dict(defaults.custom_theme_colors or default_custom_theme_colors())
        self._api_key_input.setText(defaults.astrometry_api_key or "")
        self._interface_tips_enabled_input.setChecked(bool(defaults.interface_tips_enabled))
        self._show_mode_launcher_on_startup_input.setChecked(bool(defaults.show_mode_launcher_on_startup))
        self._cache_dir_input.setText(str(defaults.cache_dir))
        self._nearby_reference_count_input.setValue(defaults.nearby_reference_count)
        self._shared_parallel_workers_input.setValue(resolve_shared_parallel_workers(defaults))
        self._sky_atlas_custom_overlay_cache_max_long_edge_input.setValue(
            int(defaults.sky_atlas_custom_overlay_cache_max_long_edge)
        )
        self._snr_binning_max_period_fraction_input.setValue(defaults.snr_binning_max_period_fraction)
        self._snr_binning_max_absolute_duration_seconds_input.setValue(int(round(defaults.snr_binning_max_absolute_duration_seconds)))
        self._snr_binning_target_snr_input.setValue(defaults.snr_binning_target_snr)
        self._snr_binning_max_frames_per_bin_input.setValue(defaults.snr_binning_max_frames_per_bin)
        self._snr_binning_min_frames_per_bin_input.setValue(defaults.snr_binning_min_frames_per_bin)
        self._snr_binning_type_aware_thresholds_input.setChecked(defaults.snr_binning_type_aware_thresholds)
        self._snr_binning_sharp_period_fraction_input.setValue(defaults.snr_binning_sharp_period_fraction)
        self._snr_binning_smooth_period_fraction_input.setValue(defaults.snr_binning_smooth_period_fraction)
        self._snr_binning_weighted_flux_binning_input.setChecked(defaults.snr_binning_weighted_flux_binning)
        self._snr_binning_allow_magnitude_fallback_input.setChecked(defaults.snr_binning_allow_magnitude_fallback)
        self._snr_binning_minimum_valid_points_per_bin_input.setValue(defaults.snr_binning_minimum_valid_points_per_bin)
        self._snr_binning_outlier_rejection_enabled_input.setChecked(defaults.snr_binning_outlier_rejection_enabled)
        self._snr_binning_sigma_clip_threshold_input.setValue(defaults.snr_binning_sigma_clip_threshold)
        self._set_combo_data(self._snr_binning_dataset_mode_input, defaults.snr_binning_dataset_mode)
        self._snr_binning_apply_to_selected_measurements_only_input.setChecked(defaults.snr_binning_apply_to_selected_measurements_only)
        self._snr_binning_allow_periodless_fallback_input.setChecked(defaults.snr_binning_allow_periodless_fallback)
        self._comparison_fit_stop_match_index_input.setValue(defaults.comparison_fit_stop_match_index)
        self._comparison_fit_parallel_workers_input.setValue(max(0, defaults.comparison_fit_parallel_workers))
        self._sky_explorer_simbad_search_radius_arcsec_input.setValue(defaults.sky_explorer_simbad_search_radius_arcsec)
        self._sky_explorer_gaia_max_magnitude_input.setValue(defaults.sky_explorer_gaia_max_magnitude)
        self._sky_explorer_gaia_hard_cap_enabled_input.setChecked(bool(defaults.sky_explorer_gaia_hard_cap_enabled))
        self._sky_explorer_gaia_hard_cap_rows_input.setValue(max(1, int(defaults.sky_explorer_gaia_hard_cap_rows)))
        self._sky_explorer_mag_limit_examples_per_bin_input.setValue(max(1, min(10, int(defaults.sky_explorer_mag_limit_examples_per_bin))))
        self._sky_explorer_mag_limit_marker_color = _coerce_hex_color(
            getattr(defaults, "sky_explorer_mag_limit_marker_color", "#3d8bfd"),
            default="#3d8bfd",
        )
        self._sky_explorer_mag_limit_text_color = _coerce_hex_color(
            getattr(defaults, "sky_explorer_mag_limit_text_color", "#111827"),
            default="#111827",
        )
        self._sky_explorer_mag_limit_marker_stroke_color = _coerce_hex_color(
            getattr(defaults, "sky_explorer_mag_limit_marker_stroke_color", "#111827"),
            default="#111827",
        )
        self._sky_explorer_mag_limit_text_stroke_color = _coerce_hex_color(
            getattr(defaults, "sky_explorer_mag_limit_text_stroke_color", "#ffffff"),
            default="#ffffff",
        )
        self._sky_explorer_mag_limit_target_size_input.setValue(float(getattr(defaults, "sky_explorer_mag_limit_target_size", 6.0)))
        self._sky_explorer_mag_limit_text_size_input.setValue(float(getattr(defaults, "sky_explorer_mag_limit_text_size", 9.0)))
        self._sky_explorer_mag_limit_marker_stroke_width_input.setValue(float(getattr(defaults, "sky_explorer_mag_limit_marker_stroke_width", 2.0)))
        self._sky_explorer_mag_limit_text_stroke_width_input.setValue(float(getattr(defaults, "sky_explorer_mag_limit_text_stroke_width", 0.0)))
        self._update_sky_explorer_mag_limit_marker_color_button()
        self._update_sky_explorer_mag_limit_text_color_button()
        self._update_sky_explorer_mag_limit_marker_stroke_color_button()
        self._update_sky_explorer_mag_limit_text_stroke_color_button()
        self._update_sky_explorer_gaia_inputs()
        self._sky_explorer_annotated_galaxy_max_magnitude_enabled_input.setChecked(bool(defaults.sky_explorer_annotated_galaxy_max_magnitude_enabled))
        self._sky_explorer_annotated_galaxy_max_magnitude_input.setValue(float(defaults.sky_explorer_annotated_galaxy_max_magnitude))
        self._sky_explorer_annotated_galaxy_require_shape_metadata_input.setChecked(bool(defaults.sky_explorer_annotated_galaxy_require_shape_metadata))
        self._update_sky_explorer_galaxy_annotation_inputs()
        self._sky_explorer_scale_extended_nebulae_input.setChecked(bool(defaults.sky_explorer_scale_extended_nebulae))
        self._sky_explorer_scale_overlay_strokes_input.setChecked(bool(defaults.sky_explorer_scale_overlay_strokes))
        self._set_combo_data(self._sky_explorer_marker_color_relation_input, defaults.sky_explorer_marker_color_relation)
        self._set_combo_data(self._sky_explorer_text_color_relation_input, defaults.sky_explorer_text_color_relation)
        self._sky_explorer_fill_opacity_input.setValue(defaults.sky_explorer_fill_opacity)
        self._sky_explorer_stroke_opacity_input.setValue(defaults.sky_explorer_stroke_opacity)
        self._sky_explorer_object_group_color_overrides = dict(defaults.sky_explorer_object_group_color_overrides or {})
        for group_key in self._sky_explorer_object_group_color_buttons:
            self._update_sky_explorer_object_group_color_button(group_key)
        default_sky_explorer_layers = {str(layer).strip().lower() for layer in getattr(defaults, "sky_explorer_enabled_layers", ())}
        for layer_key, checkbox in self._sky_explorer_layer_inputs.items():
            checkbox.setChecked(layer_key in default_sky_explorer_layers)
        self._asteroid_search_parallel_workers_input.setValue(max(0, defaults.asteroid_search_parallel_workers))
        self._asteroid_discovery_min_residual_snr_input.setValue(max(0.0, defaults.asteroid_discovery_min_residual_snr))
        self._asteroid_discovery_max_residual_snr_input.setValue(max(0.0, defaults.asteroid_discovery_max_residual_snr))
        self._asteroid_discovery_frames_per_batch_input.setValue(max(0, defaults.asteroid_discovery_frames_per_batch))
        self._set_combo_data(self._asteroid_discovery_binning_factor_input, defaults.asteroid_discovery_binning_factor)
        self._asteroid_discovery_use_temporary_cache_input.setChecked(defaults.asteroid_discovery_use_temporary_cache)
        self._asteroid_discovery_min_candidate_frames_input.setValue(max(2, int(defaults.asteroid_discovery_min_candidate_frames)))
        self._asteroid_discovery_detection_sigma_input.setValue(max(0.5, float(defaults.asteroid_discovery_detection_sigma)))
        self._asteroid_discovery_detection_fwhm_input.setValue(max(0.8, float(defaults.asteroid_discovery_detection_fwhm)))
        self._asteroid_discovery_max_residuals_per_frame_input.setValue(max(1, int(defaults.asteroid_discovery_max_residuals_per_frame)))
        self._asteroid_discovery_edge_margin_px_input.setValue(max(0, int(defaults.asteroid_discovery_edge_margin_px)))
        self._set_combo_data(self._asteroid_discovery_detector_mode_input, str(defaults.asteroid_discovery_detector_mode or "hybrid").strip().lower())
        self._asteroid_discovery_streak_min_area_px_input.setValue(max(2, int(defaults.asteroid_discovery_streak_min_area_px)))
        self._asteroid_discovery_streak_min_elongation_input.setValue(max(1.0, float(defaults.asteroid_discovery_streak_min_elongation)))
        self._asteroid_discovery_potential_deflection_rms_input.setValue(max(0.1, float(defaults.asteroid_discovery_potential_deflection_rms_px)))
        self._asteroid_discovery_review_deflection_rms_input.setValue(max(float(defaults.asteroid_discovery_potential_deflection_rms_px), float(defaults.asteroid_discovery_review_deflection_rms_px)))
        self._asteroid_discovery_enable_synthetic_sweep_input.setChecked(defaults.asteroid_discovery_enable_synthetic_sweep)
        self._asteroid_discovery_synthetic_sweep_max_motion_px_per_hour_input.setValue(max(0.1, float(defaults.asteroid_discovery_synthetic_sweep_max_motion_px_per_hour)))
        self._asteroid_discovery_synthetic_sweep_motion_step_px_per_hour_input.setValue(max(0.1, float(defaults.asteroid_discovery_synthetic_sweep_motion_step_px_per_hour)))
        self._asteroid_discovery_synthetic_sweep_angle_step_deg_input.setValue(max(1.0, float(defaults.asteroid_discovery_synthetic_sweep_angle_step_deg)))
        self._set_combo_data(
            self._asteroid_discovery_synthetic_sweep_direction_focus_input,
            str(defaults.asteroid_discovery_synthetic_sweep_direction_focus or "all_directions").strip().lower(),
        )
        self._asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg_input.setValue(max(1.0, float(defaults.asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg)))
        self._asteroid_discovery_synthetic_sweep_min_stacked_snr_input.setValue(max(0.5, float(defaults.asteroid_discovery_synthetic_sweep_min_stacked_snr)))
        self._asteroid_discovery_synthetic_sweep_save_stacks_input.setChecked(bool(defaults.asteroid_discovery_synthetic_sweep_save_stacks))
        self._comparison_fit_allow_multiple_targets_input.setChecked(defaults.comparison_fit_allow_multiple_targets)
        self._comparison_fit_eclipsing_binary_match_tolerance_input.setValue(defaults.comparison_fit_eclipsing_binary_match_tolerance)
        self._comparison_fit_fallback_candidate_pool_size_input.setValue(max(0, defaults.comparison_fit_fallback_candidate_pool_size))
        self._comparison_fit_fallback_magnitude_tolerance_input.setValue(defaults.comparison_fit_fallback_magnitude_tolerance)
        self._scientific_light_curve_pdf_dpi_input.setValue(max(72, int(defaults.scientific_light_curve_pdf_dpi)))
        default_paper_index = self._scientific_light_curve_pdf_paper_size_input.findData(defaults.scientific_light_curve_pdf_paper_size)
        self._scientific_light_curve_pdf_paper_size_input.setCurrentIndex(default_paper_index if default_paper_index >= 0 else 0)
        self._hr_max_sources_input.setValue(max(0, int(defaults.hr_max_sources)))
        self._hr_table_row_limit_input.setValue(max(1, int(defaults.hr_table_row_limit)))
        self._hr_plot_require_parallax_input.setChecked(defaults.hr_plot_require_parallax)
        self._hr_plot_color_saturation_input.setValue(defaults.hr_plot_color_saturation)
        self._hr_plot_point_opacity_input.setValue(defaults.hr_plot_point_opacity)
        self._hr_selection_circle_color = str(defaults.hr_selection_circle_color or "#ffd166").strip().lower() or "#ffd166"
        self._update_hr_selection_circle_color_button()
        self._hr_selection_circle_opacity_input.setValue(defaults.hr_selection_circle_opacity)
        self._hr_selection_circle_size_factor_input.setValue(defaults.hr_selection_circle_size_factor)
        self._hr_plot_hide_flagged_input.setChecked(bool(defaults.hr_plot_hide_flagged))
        self._hr_plot_hide_saturated_input.setChecked(bool(defaults.hr_plot_hide_saturated))
        self._hr_plot_apparent_mag_min_input.setValue(float(defaults.hr_plot_apparent_magnitude_min))
        self._hr_plot_apparent_mag_max_input.setValue(float(defaults.hr_plot_apparent_magnitude_max))
        self._set_combo_data(self._hr_plot_marker_size_mode_input, defaults.hr_plot_marker_size_mode)
        self._hr_plot_fixed_marker_size_input.setValue(defaults.hr_plot_fixed_marker_size)
        self._hr_motion_vector_color = str(defaults.hr_motion_vector_color or "#3d8bfd").strip().lower() or "#3d8bfd"
        self._update_hr_motion_vector_color_button()
        self._hr_motion_vector_width_input.setValue(float(defaults.hr_motion_vector_width))
        self._frame_edge_margin_percent_input.setValue(defaults.frame_edge_margin_percent)
        self._saturation_filter_enabled_input.setChecked(defaults.saturation_filter_enabled)
        self._set_combo_data(self._image_display_stretch_mode_input, defaults.image_display_stretch_mode)
        self._image_display_brightness_input.setValue(defaults.image_display_brightness)
        self._image_display_contrast_input.setValue(defaults.image_display_contrast)
        self._image_display_inverted_input.setChecked(defaults.image_display_inverted)
        self._asteroid_estimate_snr_threshold_input.setValue(defaults.asteroid_estimate_snr_threshold)
        self._asteroid_estimate_start_magnitude_input.setValue(defaults.asteroid_estimate_start_magnitude)
        self._asteroid_manual_magnitude_limit_override_enabled_input.setChecked(defaults.asteroid_manual_magnitude_limit_override_enabled)
        self._asteroid_manual_magnitude_limit_override_input.setValue(defaults.asteroid_manual_magnitude_limit_override)
        self._asteroid_estimate_stars_per_bin_input.setValue(defaults.asteroid_estimate_stars_per_bin)
        self._asteroid_estimate_required_visible_stars_input.setValue(defaults.asteroid_estimate_required_visible_stars)
        self._asteroid_estimate_annotate_lowest_mag_stars_input.setChecked(defaults.asteroid_estimate_annotate_lowest_mag_stars)
        self._asteroid_visual_show_known_objects_input.setChecked(defaults.asteroid_visual_show_known_objects)
        self._asteroid_visual_show_potential_discoveries_input.setChecked(defaults.asteroid_visual_show_potential_discoveries)
        self._asteroid_visual_label_all_objects_input.setChecked(defaults.asteroid_visual_label_all_objects)
        self._asteroid_visual_show_target_marker_input.setChecked(defaults.asteroid_visual_show_target_marker)
        self._set_combo_data(self._asteroid_track_object_position_mode_input, defaults.asteroid_track_object_position_mode)
        self._asteroid_visual_show_all_crosshairs_input.setChecked(defaults.asteroid_visual_show_all_crosshairs)
        self._asteroid_visual_highlight_selected_object_input.setChecked(defaults.asteroid_visual_highlight_selected_object)
        self._asteroid_visual_invert_annotation_colors_input.setChecked(defaults.asteroid_visual_invert_annotation_colors)
        self._asteroid_target_marker_line_color = str(defaults.asteroid_target_marker_line_color or "#ef4444").strip().lower() or "#ef4444"
        self._asteroid_target_marker_accent_color = str(defaults.asteroid_target_marker_accent_color or "#fca5a5").strip().lower() or "#fca5a5"
        self._asteroid_target_marker_text_color = str(defaults.asteroid_target_marker_text_color or "#fff1f2").strip().lower() or "#fff1f2"
        self._asteroid_target_marker_outline_color = str(defaults.asteroid_target_marker_outline_color or "#ffffff").strip().lower() or "#ffffff"
        self._asteroid_target_marker_line_width_input.setValue(float(defaults.asteroid_target_marker_line_width))
        self._update_asteroid_target_marker_line_color_button()
        self._update_asteroid_target_marker_accent_color_button()
        self._update_asteroid_target_marker_text_color_button()
        self._update_asteroid_target_marker_outline_color_button()
        self._set_combo_data(self._asteroid_blink_frame_duration_input, defaults.asteroid_blink_frame_duration_ms)
        self._asteroid_gif_export_scale_percent_input.setValue(defaults.asteroid_gif_export_scale_percent)
        self._asteroid_mp4_export_scale_percent_input.setValue(defaults.asteroid_mp4_export_scale_percent)
        self._asteroid_gif_export_loop_forever_input.setChecked(defaults.asteroid_gif_export_loop_forever)
        self._synthetic_tracking_crop_radius_input.setValue(defaults.synthetic_tracking_crop_radius_pixels)
        self._set_combo_data(self._synthetic_tracking_integration_mode_input, defaults.synthetic_tracking_integration_mode)
        self._set_combo_data(self._synthetic_tracking_weight_mode_input, defaults.synthetic_tracking_weight_mode)
        self._set_combo_data(self._synthetic_tracking_rejection_mode_input, defaults.synthetic_tracking_rejection_mode)
        self._set_combo_data(self._synthetic_tracking_backend_preference_input, defaults.synthetic_tracking_backend_preference)
        self._synthetic_tracking_allow_mixed_all_group_input.setChecked(defaults.synthetic_tracking_allow_mixed_all_group)
        self._synthetic_tracking_advanced_enabled_input.setChecked(defaults.synthetic_tracking_advanced_enabled)
        self._reference_star_magnitude_range_enabled_input.setChecked(
            defaults.reference_star_min_magnitude is not None or defaults.reference_star_max_magnitude is not None
        )
        self._reference_star_min_magnitude_input.setValue(defaults.reference_star_min_magnitude or 10.0)
        self._reference_star_max_magnitude_input.setValue(defaults.reference_star_max_magnitude or 13.5)
        self._observer_code_input.setText(defaults.observer_code)
        self._observer_name_input.setText(defaults.observer_name)
        self._organization_input.setText(defaults.organization)
        self._site_name_input.setText(defaults.site_name)
        self._observing_site_latitude_input.setText(self._optional_float_text(defaults.observing_site_latitude_deg))
        self._observing_site_longitude_input.setText(self._optional_float_text(defaults.observing_site_longitude_deg))
        self._observing_site_elevation_input.setText(self._optional_float_text(defaults.observing_site_elevation_m))
        self._telescope_input.setText(defaults.telescope)
        self._telescope_focal_length_input.setValue(0.0 if defaults.telescope_focal_length_mm is None else defaults.telescope_focal_length_mm)
        self._telescope_aperture_input.setValue(0.0 if defaults.telescope_aperture_mm is None else defaults.telescope_aperture_mm)
        self._telescope_focal_ratio_input.setValue(0.0 if defaults.telescope_focal_ratio is None else defaults.telescope_focal_ratio)
        self._camera_input.setText(defaults.camera)
        self._camera_pixel_size_input.setValue(0.0 if defaults.camera_pixel_size_um is None else defaults.camera_pixel_size_um)
        self._bortle_scale_input.setValue(0 if defaults.bortle_scale is None else int(defaults.bortle_scale))
        self._filter_system_input.setCurrentText(defaults.filter_system)
        self._aavso_chart_id_input.setText(defaults.aavso_chart_id)
        self._observation_timezone_input.setCurrentText(defaults.observation_timezone)
        self._time_standard_input.setCurrentText(defaults.time_standard)
        self._transformed_input.setChecked(defaults.transformed)
        self._reduction_notes_input.setPlainText(defaults.reduction_notes)
        self._set_combo_data(self._photometry_aperture_mode_input, defaults.photometry_aperture_mode)
        self._aperture_radius_pixels_input.setValue(defaults.aperture_radius_pixels)
        self._annulus_inner_radius_pixels_input.setValue(defaults.annulus_inner_radius_pixels)
        self._annulus_outer_radius_pixels_input.setValue(defaults.annulus_outer_radius_pixels)
        self._aperture_radius_fwhm_scale_input.setValue(defaults.aperture_radius_fwhm_scale)
        self._annulus_inner_radius_fwhm_scale_input.setValue(defaults.annulus_inner_radius_fwhm_scale)
        self._annulus_outer_radius_fwhm_scale_input.setValue(defaults.annulus_outer_radius_fwhm_scale)
        self._set_combo_data(self._variable_star_limit_mode_input, defaults.variable_star_limit_mode)
        self._variable_star_limit_value_input.setValue(defaults.variable_star_limit_value)
        self._preview_variable_star_max_count_input.setValue(max(0, defaults.preview_variable_star_max_count))
        self._preview_variable_star_magnitude_range_enabled_input.setChecked(
            defaults.preview_variable_star_min_magnitude is not None or defaults.preview_variable_star_max_magnitude is not None
        )
        self._preview_variable_star_min_magnitude_input.setValue(defaults.preview_variable_star_min_magnitude or 8.0)
        self._preview_variable_star_max_magnitude_input.setValue(defaults.preview_variable_star_max_magnitude or 15.0)
        self._use_default_settings_location_input.setChecked(True)
        for family, checkbox in self._designation_checkboxes.items():
            checkbox.setChecked(family in defaults.variable_star_designation_filters)
        self._update_aperture_inputs()
        self._update_variable_limit_input()
        self._update_reference_limit_inputs()
        self._update_preview_limit_inputs()
        self._update_snr_binning_inputs()
        self._update_asteroid_estimate_inputs()
        self._update_setup_derived_fields()
        self._update_settings_location_inputs()
        self._update_hr_plot_size_inputs()

    def _set_combo_data(self, combo_box: QComboBox, value: object) -> None:
        index = combo_box.findData(value)
        if index >= 0:
            combo_box.setCurrentIndex(index)

    def _is_default_settings_location(self, config_path: Path) -> bool:
        try:
            return config_path.expanduser().resolve() == self._default_config_path.expanduser().resolve()
        except OSError:
            return str(config_path.expanduser()) == str(self._default_config_path.expanduser())

    def _update_aperture_inputs(self) -> None:
        for widget in (
            self._aperture_radius_pixels_input,
            self._annulus_inner_radius_pixels_input,
            self._annulus_outer_radius_pixels_input,
        ):
            widget.setEnabled(False)
        for widget in (
            self._aperture_radius_fwhm_scale_input,
            self._annulus_inner_radius_fwhm_scale_input,
            self._annulus_outer_radius_fwhm_scale_input,
        ):
            widget.setEnabled(True)

    def _update_variable_limit_input(self) -> None:
        mode = self._variable_star_limit_mode_input.currentData()
        if mode == VariableStarLimitMode.COUNT:
            self._variable_star_limit_value_input.setRange(1, 100000)
            self._variable_star_limit_value_input.setSuffix("")
            if self._variable_star_limit_value_input.value() > 100000:
                self._variable_star_limit_value_input.setValue(100000)
            return

        self._variable_star_limit_value_input.setRange(1, 100)
        self._variable_star_limit_value_input.setSuffix("%")

    def _update_snr_binning_inputs(self) -> None:
        type_aware_enabled = self._snr_binning_type_aware_thresholds_input.isChecked()
        self._snr_binning_sharp_period_fraction_input.setEnabled(type_aware_enabled)
        self._snr_binning_smooth_period_fraction_input.setEnabled(type_aware_enabled)
        sigma_clip_enabled = self._snr_binning_outlier_rejection_enabled_input.isChecked()
        self._snr_binning_sigma_clip_threshold_input.setEnabled(sigma_clip_enabled)
        if self._variable_star_limit_value_input.value() > 100:
            self._variable_star_limit_value_input.setValue(100)

    def _update_preview_limit_inputs(self) -> None:
        self._preview_variable_star_min_magnitude_input.setEnabled(
            self._preview_variable_star_magnitude_range_enabled_input.isChecked()
        )
        self._preview_variable_star_max_magnitude_input.setEnabled(
            self._preview_variable_star_magnitude_range_enabled_input.isChecked()
        )

    def _update_sky_explorer_gaia_inputs(self) -> None:
        self._sky_explorer_gaia_hard_cap_rows_input.setEnabled(
            self._sky_explorer_gaia_hard_cap_enabled_input.isChecked()
        )

    def _update_sky_explorer_galaxy_annotation_inputs(self) -> None:
        self._sky_explorer_annotated_galaxy_max_magnitude_input.setEnabled(
            self._sky_explorer_annotated_galaxy_max_magnitude_enabled_input.isChecked()
        )

    def _update_asteroid_estimate_inputs(self) -> None:
        checked_stars = max(2, self._asteroid_estimate_stars_per_bin_input.value())
        self._asteroid_estimate_required_visible_stars_input.setRange(1, checked_stars - 1)
        if self._asteroid_estimate_required_visible_stars_input.value() >= checked_stars:
            self._asteroid_estimate_required_visible_stars_input.setValue(checked_stars - 1)
        self._asteroid_manual_magnitude_limit_override_input.setEnabled(
            self._asteroid_manual_magnitude_limit_override_enabled_input.isChecked()
        )

    def _update_reference_limit_inputs(self) -> None:
        self._reference_star_min_magnitude_input.setEnabled(
            self._reference_star_magnitude_range_enabled_input.isChecked()
        )
        self._reference_star_max_magnitude_input.setEnabled(
            self._reference_star_magnitude_range_enabled_input.isChecked()
        )

    def _update_hr_plot_size_inputs(self) -> None:
        size_mode = str(self._hr_plot_marker_size_mode_input.currentData() or "scaled")
        self._hr_plot_fixed_marker_size_input.setEnabled(size_mode == "fixed")

    def _pick_hr_selection_circle_color(self) -> None:
        selected = QColorDialog.getColor(QColor(self._hr_selection_circle_color), self, "HR Selected Circle Color")
        if not selected.isValid():
            return
        self._hr_selection_circle_color = selected.name().lower()
        self._update_hr_selection_circle_color_button()

    def _pick_sky_explorer_object_group_color(self, group_key: str) -> None:
        current_color = self._sky_explorer_object_group_color_overrides.get(
            group_key,
            self._sky_explorer_object_group_default_colors.get(group_key, "#3d8bfd"),
        )
        selected = QColorDialog.getColor(QColor(current_color), self, "Sky Explorer Object Group Color")
        if not selected.isValid():
            return
        self._sky_explorer_object_group_color_overrides[group_key] = selected.name().lower()
        self._update_sky_explorer_object_group_color_button(group_key)

    def _choose_sky_explorer_mag_limit_marker_color(self) -> None:
        selected = QColorDialog.getColor(QColor(self._sky_explorer_mag_limit_marker_color), self, "Sky Explorer Mag Limit Marker Color")
        if not selected.isValid():
            return
        self._sky_explorer_mag_limit_marker_color = selected.name(QColor.NameFormat.HexRgb).lower()
        self._update_sky_explorer_mag_limit_marker_color_button()

    def _update_sky_explorer_mag_limit_marker_color_button(self) -> None:
        text_color = "#ffffff" if QColor(self._sky_explorer_mag_limit_marker_color).lightness() < 128 else "#1f1f1f"
        self._sky_explorer_mag_limit_marker_color_button.setText(self._sky_explorer_mag_limit_marker_color.upper())
        self._sky_explorer_mag_limit_marker_color_button.setStyleSheet(
            f"background-color: {self._sky_explorer_mag_limit_marker_color}; color: {text_color}; padding: 4px 8px;"
        )

    def _choose_sky_explorer_mag_limit_text_color(self) -> None:
        selected = QColorDialog.getColor(QColor(self._sky_explorer_mag_limit_text_color), self, "Sky Explorer Mag Limit Text Color")
        if not selected.isValid():
            return
        self._sky_explorer_mag_limit_text_color = selected.name(QColor.NameFormat.HexRgb).lower()
        self._update_sky_explorer_mag_limit_text_color_button()

    def _update_sky_explorer_mag_limit_text_color_button(self) -> None:
        text_color = "#ffffff" if QColor(self._sky_explorer_mag_limit_text_color).lightness() < 128 else "#1f1f1f"
        self._sky_explorer_mag_limit_text_color_button.setText(self._sky_explorer_mag_limit_text_color.upper())
        self._sky_explorer_mag_limit_text_color_button.setStyleSheet(
            f"background-color: {self._sky_explorer_mag_limit_text_color}; color: {text_color}; padding: 4px 8px;"
        )

    def _choose_sky_explorer_mag_limit_marker_stroke_color(self) -> None:
        selected = QColorDialog.getColor(QColor(self._sky_explorer_mag_limit_marker_stroke_color), self, "Sky Explorer Mag Limit Marker Stroke Color")
        if not selected.isValid():
            return
        self._sky_explorer_mag_limit_marker_stroke_color = selected.name(QColor.NameFormat.HexRgb).lower()
        self._update_sky_explorer_mag_limit_marker_stroke_color_button()

    def _update_sky_explorer_mag_limit_marker_stroke_color_button(self) -> None:
        text_color = "#ffffff" if QColor(self._sky_explorer_mag_limit_marker_stroke_color).lightness() < 128 else "#1f1f1f"
        self._sky_explorer_mag_limit_marker_stroke_color_button.setText(self._sky_explorer_mag_limit_marker_stroke_color.upper())
        self._sky_explorer_mag_limit_marker_stroke_color_button.setStyleSheet(
            f"background-color: {self._sky_explorer_mag_limit_marker_stroke_color}; color: {text_color}; padding: 4px 8px;"
        )

    def _choose_sky_explorer_mag_limit_text_stroke_color(self) -> None:
        selected = QColorDialog.getColor(QColor(self._sky_explorer_mag_limit_text_stroke_color), self, "Sky Explorer Mag Limit Text Stroke Color")
        if not selected.isValid():
            return
        self._sky_explorer_mag_limit_text_stroke_color = selected.name(QColor.NameFormat.HexRgb).lower()
        self._update_sky_explorer_mag_limit_text_stroke_color_button()

    def _update_sky_explorer_mag_limit_text_stroke_color_button(self) -> None:
        text_color = "#ffffff" if QColor(self._sky_explorer_mag_limit_text_stroke_color).lightness() < 128 else "#1f1f1f"
        self._sky_explorer_mag_limit_text_stroke_color_button.setText(self._sky_explorer_mag_limit_text_stroke_color.upper())
        self._sky_explorer_mag_limit_text_stroke_color_button.setStyleSheet(
            f"background-color: {self._sky_explorer_mag_limit_text_stroke_color}; color: {text_color}; padding: 4px 8px;"
        )

    def _update_sky_explorer_object_group_color_button(self, group_key: str) -> None:
        button = self._sky_explorer_object_group_color_buttons.get(group_key)
        if button is None:
            return
        color_name = self._sky_explorer_object_group_color_overrides.get(
            group_key,
            self._sky_explorer_object_group_default_colors.get(group_key, "#3d8bfd"),
        )
        text_color = "#ffffff" if QColor(color_name).lightness() < 128 else "#1f1f1f"
        suffix = "" if group_key in self._sky_explorer_object_group_color_overrides else " default"
        button.setText(f"{color_name.upper()}{suffix}")
        button.setStyleSheet(f"background-color: {color_name}; color: {text_color}; padding: 4px 8px;")

    def _update_hr_selection_circle_color_button(self) -> None:
        text_color = "#ffffff" if QColor(self._hr_selection_circle_color).lightness() < 128 else "#1f1f1f"
        self._hr_selection_circle_color_input.setText(self._hr_selection_circle_color.upper())
        self._hr_selection_circle_color_input.setStyleSheet(
            f"background-color: {self._hr_selection_circle_color}; color: {text_color}; padding: 4px 8px;"
        )

    def _pick_hr_motion_vector_color(self) -> None:
        selected = QColorDialog.getColor(QColor(self._hr_motion_vector_color), self, "HR Motion Vector Color")
        if not selected.isValid():
            return
        self._hr_motion_vector_color = selected.name().lower()
        self._update_hr_motion_vector_color_button()

    def _update_hr_motion_vector_color_button(self) -> None:
        text_color = "#ffffff" if QColor(self._hr_motion_vector_color).lightness() < 128 else "#1f1f1f"
        self._hr_motion_vector_color_input.setText(self._hr_motion_vector_color.upper())
        self._hr_motion_vector_color_input.setStyleSheet(
            f"background-color: {self._hr_motion_vector_color}; color: {text_color}; padding: 4px 8px;"
        )

    def _choose_asteroid_target_marker_line_color(self) -> None:
        selected = QColorDialog.getColor(QColor(self._asteroid_target_marker_line_color), self, "Asteroid/Comet Target Marker Line Color")
        if not selected.isValid():
            return
        self._asteroid_target_marker_line_color = selected.name(QColor.NameFormat.HexRgb).lower()
        self._update_asteroid_target_marker_line_color_button()

    def _update_asteroid_target_marker_line_color_button(self) -> None:
        text_color = "#ffffff" if QColor(self._asteroid_target_marker_line_color).lightness() < 128 else "#1f1f1f"
        self._asteroid_target_marker_line_color_button.setText(self._asteroid_target_marker_line_color.upper())
        self._asteroid_target_marker_line_color_button.setStyleSheet(
            f"background-color: {self._asteroid_target_marker_line_color}; color: {text_color}; padding: 4px 8px;"
        )

    def _choose_asteroid_target_marker_accent_color(self) -> None:
        selected = QColorDialog.getColor(QColor(self._asteroid_target_marker_accent_color), self, "Asteroid/Comet Target Marker Accent Color")
        if not selected.isValid():
            return
        self._asteroid_target_marker_accent_color = selected.name(QColor.NameFormat.HexRgb).lower()
        self._update_asteroid_target_marker_accent_color_button()

    def _update_asteroid_target_marker_accent_color_button(self) -> None:
        text_color = "#ffffff" if QColor(self._asteroid_target_marker_accent_color).lightness() < 128 else "#1f1f1f"
        self._asteroid_target_marker_accent_color_button.setText(self._asteroid_target_marker_accent_color.upper())
        self._asteroid_target_marker_accent_color_button.setStyleSheet(
            f"background-color: {self._asteroid_target_marker_accent_color}; color: {text_color}; padding: 4px 8px;"
        )

    def _choose_asteroid_target_marker_text_color(self) -> None:
        selected = QColorDialog.getColor(QColor(self._asteroid_target_marker_text_color), self, "Asteroid/Comet Target Marker Label Color")
        if not selected.isValid():
            return
        self._asteroid_target_marker_text_color = selected.name(QColor.NameFormat.HexRgb).lower()
        self._update_asteroid_target_marker_text_color_button()

    def _choose_asteroid_target_marker_outline_color(self) -> None:
        selected = QColorDialog.getColor(QColor(self._asteroid_target_marker_outline_color), self, "Asteroid/Comet Target Marker Outline Color")
        if not selected.isValid():
            return
        self._asteroid_target_marker_outline_color = selected.name(QColor.NameFormat.HexRgb).lower()
        self._update_asteroid_target_marker_outline_color_button()

    def _update_asteroid_target_marker_text_color_button(self) -> None:
        text_color = "#ffffff" if QColor(self._asteroid_target_marker_text_color).lightness() < 128 else "#1f1f1f"
        self._asteroid_target_marker_text_color_button.setText(self._asteroid_target_marker_text_color.upper())
        self._asteroid_target_marker_text_color_button.setStyleSheet(
            f"background-color: {self._asteroid_target_marker_text_color}; color: {text_color}; padding: 4px 8px;"
        )

    def _update_asteroid_target_marker_outline_color_button(self) -> None:
        text_color = "#ffffff" if QColor(self._asteroid_target_marker_outline_color).lightness() < 128 else "#1f1f1f"
        self._asteroid_target_marker_outline_color_button.setText(self._asteroid_target_marker_outline_color.upper())
        self._asteroid_target_marker_outline_color_button.setStyleSheet(
            f"background-color: {self._asteroid_target_marker_outline_color}; color: {text_color}; padding: 4px 8px;"
        )


class PreviewSelectionDialog(QDialog):
    def __init__(self, preview: VariableSelectionPreview, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select Sources To Process")
        self.resize(900, 640)
        self._preview = preview
        self._accepted_source_keys: list[str] | None = None
        self._designation_checkboxes: dict[VariableStarDesignationFamily, QCheckBox] = {}
        self._family_counts = self._build_family_counts()
        self._saved_exoplanet_source_keys = self._saved_exoplanet_keys()

        self._summary_label = QLabel()
        self._summary_label.setWordWrap(True)

        self._default_limit_mode_input = QComboBox()
        self._default_limit_mode_input.addItem("Percentage of brightest stars", VariableStarLimitMode.PERCENT)
        self._default_limit_mode_input.addItem("Absolute number of brightest stars", VariableStarLimitMode.COUNT)
        selected_mode_index = self._default_limit_mode_input.findData(preview.variable_star_limit_mode)
        if selected_mode_index >= 0:
            self._default_limit_mode_input.setCurrentIndex(selected_mode_index)
        self._default_limit_mode_input.currentIndexChanged.connect(self._handle_default_selection_controls_changed)

        self._default_limit_value_input = QSpinBox()
        self._default_limit_value_input.setValue(preview.variable_star_limit_value)
        self._default_limit_value_input.valueChanged.connect(self._handle_default_selection_controls_changed)
        self._update_default_limit_input()

        self._name_filter_input = QLineEdit()
        self._name_filter_input.setPlaceholderText("Filter sources by name")
        self._name_filter_input.textChanged.connect(self._refresh_rows)

        self._type_filter_combo = QComboBox()
        self._type_filter_combo.addItem("All types", "all")
        self._type_filter_combo.addItem("Variables", "variable")
        self._type_filter_combo.addItem("Checks", "check")
        self._type_filter_combo.addItem("Exoplanets", "exoplanet")
        self._type_filter_combo.addItem("References", "reference")
        self._type_filter_combo.currentIndexChanged.connect(self._refresh_rows)

        self._source_table = QTableWidget(0, 5)
        self._source_table.setHorizontalHeaderLabels(["Source", "Catalog", "Type", "Magnitude", "Sky Position"])
        self._source_table.horizontalHeader().setStretchLastSection(True)
        self._source_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._source_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)

        default_controls_row = QHBoxLayout()
        default_controls_row.addWidget(QLabel("Default selection"))
        default_controls_row.addWidget(self._default_limit_mode_input)
        default_controls_row.addWidget(self._default_limit_value_input)
        default_controls_row.addStretch(1)

        designation_group = QGroupBox("Designation Families")
        designation_layout = QGridLayout()
        for index, family in enumerate(VariableStarDesignationFamily):
            count = self._family_counts.get(family, 0)
            checkbox = QCheckBox(f"{VARIABLE_STAR_DESIGNATION_LABELS[family]} ({count})")
            checkbox.setChecked(family in preview.variable_star_designation_filters and count > 0)
            checkbox.setEnabled(count > 0)
            checkbox.stateChanged.connect(self._refresh_rows)
            self._designation_checkboxes[family] = checkbox
            designation_layout.addWidget(checkbox, index // 3, index % 3)
        designation_group.setLayout(designation_layout)

        filters_row = QHBoxLayout()
        filters_row.addWidget(QLabel("Sources"))
        filters_row.addWidget(self._name_filter_input, stretch=1)
        filters_row.addWidget(self._type_filter_combo)

        self._process_default_button = QPushButton("Process Default")
        self._process_default_button.clicked.connect(self._accept_default)
        self._apply_process_default_button_style()
        process_all_button = QPushButton("Process All Visible")
        process_all_button.clicked.connect(self._accept_all_visible)
        process_button = QPushButton("Process Selected")
        process_button.clicked.connect(self.accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(cancel_button)
        button_row.addWidget(process_all_button)
        button_row.addWidget(self._process_default_button)
        button_row.addWidget(process_button)

        root_layout = QVBoxLayout()
        root_layout.addWidget(self._summary_label)
        root_layout.addLayout(default_controls_row)
        root_layout.addWidget(designation_group)
        root_layout.addLayout(filters_row)
        root_layout.addWidget(self._source_table)
        root_layout.addLayout(button_row)
        self.setLayout(root_layout)

        self._refresh_rows()

    def _apply_process_default_button_style(self) -> None:
        accent = self.palette().color(QPalette.ColorRole.Highlight)
        text_color = "#ffffff" if accent.lightness() < 128 else "#1f1f1f"
        hover_color = accent.lighter(110).name().lower()
        pressed_color = accent.darker(110).name().lower()
        border_color = accent.darker(122).name().lower()
        self._process_default_button.setStyleSheet(
            "QPushButton {"
            f"background-color: {accent.name().lower()};"
            f"color: {text_color};"
            f"border: 1px solid {border_color};"
            "padding: 4px 10px;"
            "font-weight: 600;"
            "}"
            "QPushButton:hover {"
            f"background-color: {hover_color};"
            "}"
            "QPushButton:pressed {"
            f"background-color: {pressed_color};"
            "}"
        )

    def variable_star_limit_mode(self) -> VariableStarLimitMode:
        mode = self._default_limit_mode_input.currentData()
        if isinstance(mode, VariableStarLimitMode):
            return mode
        return VariableStarLimitMode(str(mode).strip().lower())

    def variable_star_limit_value(self) -> int:
        return self._default_limit_value_input.value()

    def variable_star_designation_filters(self) -> list[VariableStarDesignationFamily]:
        return [family for family, checkbox in self._designation_checkboxes.items() if checkbox.isChecked()]

    def selected_source_keys(self) -> list[str]:
        if self._accepted_source_keys is not None:
            return list(self._accepted_source_keys)

        selection_model = self._source_table.selectionModel()
        if selection_model is None:
            return []

        selected_keys: list[str] = []
        for index in selection_model.selectedRows():
            item = self._source_table.item(index.row(), 0)
            if item is None:
                continue
            source_key = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(source_key, str) and source_key and source_key not in selected_keys:
                selected_keys.append(source_key)
        return selected_keys

    def accept(self) -> None:
        if not self.selected_source_keys():
            self._summary_label.setText("Select one or more source rows before continuing.")
            return
        super().accept()

    def _accept_default(self) -> None:
        default_keys = self._current_default_source_keys()
        if not default_keys:
            self._summary_label.setText("The current dialog default does not select any sources for this field.")
            return
        self._accepted_source_keys = default_keys
        super().accept()

    def _accept_all_visible(self) -> None:
        visible_keys: list[str] = []
        for row_index in range(self._source_table.rowCount()):
            item = self._source_table.item(row_index, 0)
            if item is None:
                continue
            source_key = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(source_key, str) and source_key and source_key not in visible_keys:
                visible_keys.append(source_key)
        if not visible_keys:
            self._summary_label.setText("There are no visible sources to process with the current filters.")
            return
        self._accepted_source_keys = visible_keys
        super().accept()

    def _refresh_rows(self) -> None:
        source_name_filter = self._name_filter_input.text().strip().lower()
        source_type = str(self._type_filter_combo.currentData() or "all")
        preselected_keys = set(self._current_default_source_keys())
        filtered_sources = []
        for entry in self._preview.candidate_sources:
            role = self._source_role(entry)
            if source_type != "all" and role.lower() != source_type:
                continue
            if entry.object_type != "exoplanet" and entry.is_variable:
                family = self._designation_family(entry)
                if family not in set(self.variable_star_designation_filters()):
                    continue
            if source_name_filter and source_name_filter not in entry.name.lower():
                continue
            filtered_sources.append(entry)

        self._source_table.blockSignals(True)
        self._source_table.clearSelection()
        self._source_table.setRowCount(len(filtered_sources))
        for row_index, entry in enumerate(filtered_sources):
            source_key = self._catalog_source_key(entry)
            values = [
                entry.name,
                entry.catalog,
                self._source_role(entry),
                f"{entry.magnitude:.2f}" if entry.magnitude is not None else "-",
                f"({entry.ra_deg:.6f}, {entry.dec_deg:.6f})",
            ]
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column_index == 0:
                    item.setData(Qt.ItemDataRole.UserRole, source_key)
                self._source_table.setItem(row_index, column_index, item)
            if source_key in preselected_keys:
                self._source_table.selectionModel().select(
                    self._source_table.model().index(row_index, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                )
        self._source_table.blockSignals(False)
        self._summary_label.setText(self._summary_text(self._preview))

    def _summary_text(self, preview: VariableSelectionPreview) -> str:
        variable_candidate_count = sum(1 for entry in preview.candidate_sources if entry.object_type != "exoplanet")
        active_variable_candidates = len(self._current_variable_candidates())
        exoplanet_count = sum(1 for entry in preview.candidate_sources if entry.object_type == "exoplanet")
        default_variable_keys = [key for key in self._current_default_source_keys() if not key.startswith("nasa:")]
        default_summary_label = self._current_selection_label()
        summary = (
            f"Object: {preview.object_name}\n"
            f"Variable stars found: {preview.total_variable_stars_found}\n"
            f"Variable stars shown in preview: {variable_candidate_count}\n"
            f"Variable stars matching dialog filters: {active_variable_candidates}\n"
            f"Candidate exoplanets: {exoplanet_count}\n"
            f"Default in this dialog: {default_summary_label} ({len(default_variable_keys)} variable source(s)"
            f"{self._saved_exoplanet_summary_suffix()})\n"
            "Select one or more sources to process."
        )
        if preview.notes:
            summary = f"{summary}\n" + "\n".join(preview.notes)
        return summary

    def _handle_default_selection_controls_changed(self) -> None:
        self._update_default_limit_input()
        self._refresh_rows()

    def _update_default_limit_input(self) -> None:
        if self.variable_star_limit_mode() == VariableStarLimitMode.COUNT:
            self._default_limit_value_input.setRange(1, 100000)
            self._default_limit_value_input.setSuffix("")
            return
        self._default_limit_value_input.setRange(1, 100)
        self._default_limit_value_input.setSuffix("%")
        if self._default_limit_value_input.value() > 100:
            self._default_limit_value_input.setValue(100)

    def _build_family_counts(self) -> dict[VariableStarDesignationFamily, int]:
        counts = {family: 0 for family in VariableStarDesignationFamily}
        for entry in self._preview.candidate_sources:
            if entry.object_type == "exoplanet" or not entry.is_variable:
                continue
            counts[self._designation_family(entry)] += 1
        return counts

    def _designation_family(self, entry: CatalogStar) -> VariableStarDesignationFamily:
        return classify_variable_star_designation(entry.name)

    def _current_variable_candidates(self) -> list[CatalogStar]:
        selected_families = set(self.variable_star_designation_filters())
        if not selected_families:
            return []
        return [
            entry
            for entry in self._preview.candidate_sources
            if entry.object_type != "exoplanet" and entry.is_variable and self._designation_family(entry) in selected_families
        ]

    def _current_default_source_keys(self) -> list[str]:
        variable_entries = self._select_brightest_variable_stars(
            self._current_variable_candidates(),
            self.variable_star_limit_mode(),
            self.variable_star_limit_value(),
        )
        default_keys = [self._catalog_source_key(entry) for entry in variable_entries]
        for source_key in self._saved_exoplanet_source_keys:
            if source_key not in default_keys:
                default_keys.append(source_key)
        return default_keys

    def _saved_exoplanet_keys(self) -> list[str]:
        candidate_source_keys = {self._catalog_source_key(entry) for entry in self._preview.candidate_sources if entry.object_type == "exoplanet"}
        return [key for key in self._preview.preselected_source_keys if key in candidate_source_keys]

    def _saved_exoplanet_summary_suffix(self) -> str:
        saved_exoplanet_count = len(self._saved_exoplanet_source_keys)
        if saved_exoplanet_count <= 0:
            return ")"
        return f", plus {saved_exoplanet_count} saved exoplanet entr{'y' if saved_exoplanet_count == 1 else 'ies'})"

    def _current_selection_label(self) -> str:
        if self.variable_star_limit_mode() == VariableStarLimitMode.COUNT:
            return f"top {self.variable_star_limit_value()} brightest stars"
        return f"top {self.variable_star_limit_value()}% brightest stars"

    def _select_brightest_variable_stars(
        self,
        variable_stars: list[CatalogStar],
        limit_mode: VariableStarLimitMode,
        limit_value: int,
    ) -> list[CatalogStar]:
        if not variable_stars:
            return []
        ordered = sorted(
            variable_stars,
            key=lambda star: (
                star.magnitude is None,
                star.magnitude if star.magnitude is not None else float("inf"),
                star.name.lower(),
            ),
        )
        if limit_mode == VariableStarLimitMode.COUNT:
            return ordered[: min(len(ordered), max(1, limit_value))]
        selected_count = min(len(ordered), max(1, int(len(ordered) * limit_value / 100)))
        return ordered[:selected_count]

    def _source_role(self, entry: CatalogStar) -> str:
        if entry.object_type == "exoplanet":
            return "Exoplanet"
        if entry.metadata.get("manual_role") == "check":
            return "Check"
        return "Variable" if entry.is_variable else "Reference"

    def _catalog_source_key(self, entry: CatalogStar) -> str:
        return f"{entry.catalog}:{entry.source_id}"


class ThemeCustomizeDialog(QDialog):
    def __init__(self, colors: dict[str, str] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Customize Theme")
        self.resize(560, 860)
        self._colors = dict(colors or default_custom_theme_colors())
        self._color_buttons: dict[str, QPushButton] = {}
        self._color_labels = {
            "window_bg": "Window Background",
            "panel_bg": "Panel Background",
            "text": "Main Text",
            "menu_bg": "Menu Background",
            "menu_text": "Menu Text",
            "accent": "Accent",
            "plot_bg": "Plot Background",
            "plot_axis": "Plot Axis/Text",
            "plot_points": "Plot Data Points",
            "plot_fit": "Plot Fitted Curve",
            "ra_grid": "RA Grid",
            "dec_grid": "Dec Grid",
            "asteroid_other_overlay_circle_color": "Circle Color",
            "asteroid_other_overlay_line_color": "Line Color",
            "asteroid_other_overlay_text_color": "Text Color",
            "asteroid_overlay_circle_color": "Circle Color",
            "asteroid_overlay_line_color": "Line Color",
            "asteroid_overlay_text_color": "Text Color",
        }
        self._equatorial_preview = _EquatorialGridPreview(self._colors, self)
        self._asteroid_overlay_preview = _AsteroidOverlayPreview(self._colors, self)

        general_form_layout = QFormLayout()
        for key, label in self._color_labels.items():
            if key.startswith("asteroid_overlay_") or key.startswith("asteroid_other_overlay_"):
                continue
            button = QPushButton()
            button.clicked.connect(lambda _checked=False, color_key=key: self._pick_color(color_key))
            self._color_buttons[key] = button
            self._update_color_button(key)
            general_form_layout.addRow(label, button)

        asteroid_other_form_layout = QFormLayout()
        for key in (
            "asteroid_other_overlay_circle_color",
            "asteroid_other_overlay_line_color",
            "asteroid_other_overlay_text_color",
        ):
            button = QPushButton()
            button.clicked.connect(lambda _checked=False, color_key=key: self._pick_color(color_key))
            self._color_buttons[key] = button
            self._update_color_button(key)
            asteroid_other_form_layout.addRow(self._color_labels[key], button)

        self._other_overlay_line_width_input = QDoubleSpinBox()
        self._other_overlay_line_width_input.setRange(0.5, 8.0)
        self._other_overlay_line_width_input.setSingleStep(0.25)
        self._other_overlay_line_width_input.setDecimals(2)
        self._other_overlay_line_width_input.setValue(self._numeric_theme_value("asteroid_other_overlay_line_width", 1.75))
        self._other_overlay_line_width_input.valueChanged.connect(
            lambda value: self._update_numeric_theme_value("asteroid_other_overlay_line_width", value)
        )
        asteroid_other_form_layout.addRow("Line Width", self._other_overlay_line_width_input)

        self._other_overlay_text_size_input = QDoubleSpinBox()
        self._other_overlay_text_size_input.setRange(7.0, 24.0)
        self._other_overlay_text_size_input.setSingleStep(0.5)
        self._other_overlay_text_size_input.setDecimals(1)
        self._other_overlay_text_size_input.setSuffix(" pt")
        self._other_overlay_text_size_input.setValue(self._numeric_theme_value("asteroid_other_overlay_text_size", 10.0))
        self._other_overlay_text_size_input.valueChanged.connect(
            lambda value: self._update_numeric_theme_value("asteroid_other_overlay_text_size", value)
        )
        asteroid_other_form_layout.addRow("Text Size", self._other_overlay_text_size_input)

        asteroid_selected_form_layout = QFormLayout()
        for key in (
            "asteroid_overlay_circle_color",
            "asteroid_overlay_line_color",
            "asteroid_overlay_text_color",
        ):
            button = QPushButton()
            button.clicked.connect(lambda _checked=False, color_key=key: self._pick_color(color_key))
            self._color_buttons[key] = button
            self._update_color_button(key)
            asteroid_selected_form_layout.addRow(self._color_labels[key], button)

        self._selected_overlay_line_width_input = QDoubleSpinBox()
        self._selected_overlay_line_width_input.setRange(0.5, 8.0)
        self._selected_overlay_line_width_input.setSingleStep(0.25)
        self._selected_overlay_line_width_input.setDecimals(2)
        self._selected_overlay_line_width_input.setValue(self._numeric_theme_value("asteroid_overlay_line_width", 1.5))
        self._selected_overlay_line_width_input.valueChanged.connect(
            lambda value: self._update_numeric_theme_value("asteroid_overlay_line_width", value)
        )
        asteroid_selected_form_layout.addRow("Line Width", self._selected_overlay_line_width_input)

        self._selected_overlay_text_size_input = QDoubleSpinBox()
        self._selected_overlay_text_size_input.setRange(7.0, 24.0)
        self._selected_overlay_text_size_input.setSingleStep(0.5)
        self._selected_overlay_text_size_input.setDecimals(1)
        self._selected_overlay_text_size_input.setSuffix(" pt")
        self._selected_overlay_text_size_input.setValue(self._numeric_theme_value("asteroid_overlay_text_size", 10.0))
        self._selected_overlay_text_size_input.valueChanged.connect(
            lambda value: self._update_numeric_theme_value("asteroid_overlay_text_size", value)
        )
        asteroid_selected_form_layout.addRow("Text Size", self._selected_overlay_text_size_input)
        self._overlay_line_width_input = self._selected_overlay_line_width_input
        self._overlay_text_size_input = self._selected_overlay_text_size_input

        general_group = QGroupBox("Application Colors")
        general_group.setLayout(general_form_layout)
        asteroid_other_group = QGroupBox("Asteroid/Comet Detection - Other Objects")
        asteroid_other_group.setLayout(asteroid_other_form_layout)
        asteroid_selected_group = QGroupBox("Asteroid/Comet Detection - Selected Object")
        asteroid_selected_group.setLayout(asteroid_selected_form_layout)

        preview_label = QLabel("Equatorial Grid Preview")
        asteroid_preview_label = QLabel("Asteroid/Comet Overlay Preview")

        reset_button = QPushButton("Reset Defaults")
        reset_button.clicked.connect(self._reset_defaults)
        save_button = QPushButton("Apply")
        save_button.clicked.connect(self.accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)

        button_row = QHBoxLayout()
        button_row.addWidget(reset_button)
        button_row.addStretch(1)
        button_row.addWidget(cancel_button)
        button_row.addWidget(save_button)

        root_layout = QVBoxLayout()
        root_layout.addWidget(general_group)
        root_layout.addWidget(asteroid_other_group)
        root_layout.addWidget(asteroid_selected_group)
        root_layout.addWidget(preview_label)
        root_layout.addWidget(self._equatorial_preview)
        root_layout.addWidget(asteroid_preview_label)
        root_layout.addWidget(self._asteroid_overlay_preview)
        root_layout.addStretch(1)
        root_layout.addLayout(button_row)
        self.setLayout(root_layout)

    def selected_colors(self) -> dict[str, str]:
        return dict(self._colors)

    def _pick_color(self, key: str) -> None:
        current_color = QColor(self._colors.get(key, default_custom_theme_colors()[key]))
        selected = QColorDialog.getColor(current_color, self, self._color_labels[key])
        if not selected.isValid():
            return
        self._colors[key] = selected.name().lower()
        self._update_color_button(key)

    def _reset_defaults(self) -> None:
        self._colors = default_custom_theme_colors()
        for widget, key, default in (
            (self._other_overlay_line_width_input, "asteroid_other_overlay_line_width", 1.75),
            (self._other_overlay_text_size_input, "asteroid_other_overlay_text_size", 10.0),
            (self._selected_overlay_line_width_input, "asteroid_overlay_line_width", 1.5),
            (self._selected_overlay_text_size_input, "asteroid_overlay_text_size", 10.0),
        ):
            widget.blockSignals(True)
            widget.setValue(self._numeric_theme_value(key, default))
            widget.blockSignals(False)
        for key in self._color_buttons:
            self._update_color_button(key)
        self._equatorial_preview.set_colors(self._colors)
        self._asteroid_overlay_preview.set_colors(self._colors)

    def _update_color_button(self, key: str) -> None:
        button = self._color_buttons[key]
        color = self._colors.get(key, default_custom_theme_colors()[key])
        text_color = "#ffffff" if QColor(color).lightness() < 128 else "#1f1f1f"
        button.setText(color.upper())
        button.setStyleSheet(f"background-color: {color}; color: {text_color}; padding: 4px 8px;")
        if key in {"plot_bg", "plot_axis", "ra_grid", "dec_grid"}:
            self._equatorial_preview.set_colors(self._colors)
        if key.startswith("asteroid_overlay_") or key.startswith("asteroid_other_overlay_"):
            self._asteroid_overlay_preview.set_colors(self._colors)

    def _numeric_theme_value(self, key: str, default: float) -> float:
        try:
            return float(self._colors.get(key, default_custom_theme_colors()[key]))
        except (TypeError, ValueError):
            return float(default)

    def _update_numeric_theme_value(self, key: str, value: float) -> None:
        self._colors[key] = f"{float(value):g}"
        self._asteroid_overlay_preview.set_colors(self._colors)


class ExportPreflightDialog(QDialog):
    def __init__(self, preflight: dict[str, object], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AAVSO Preflight Review")
        self.resize(680, 480)

        observation_count = int(preflight.get("observation_count", 0) or 0)
        std_count = int(preflight.get("standard_observation_count", 0) or 0)
        dif_count = int(preflight.get("differential_observation_count", 0) or 0)
        warning_count = int(preflight.get("warning_count", 0) or 0)
        skipped_count = int(preflight.get("skipped_measurement_count", 0) or 0)
        observer_code_present = bool(preflight.get("observer_code_present", False))
        chart_id_present = bool(preflight.get("chart_id_present", False))

        summary_label = QLabel(
            "\n".join(
                [
                    f"Rows ready for AAVSO export: {observation_count}",
                    f"Standard rows: {std_count}",
                    f"Differential rows: {dif_count}",
                    f"Warnings: {warning_count}",
                    f"Skipped measurements: {skipped_count}",
                    f"Observer code present: {'Yes' if observer_code_present else 'No'}",
                    f"Chart ID present: {'Yes' if chart_id_present else 'No'}",
                ]
            )
        )
        summary_label.setWordWrap(True)

        warnings_output = QTextEdit()
        warnings_output.setReadOnly(True)
        warning_lines = [str(item) for item in preflight.get("warnings", []) if str(item).strip()]
        if not warning_lines:
            warning_lines = ["No preflight warnings detected."]
        warnings_output.setPlainText("\n".join(warning_lines))

        continue_button = QPushButton("Export Anyway")
        continue_button.clicked.connect(self.accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(cancel_button)
        button_row.addWidget(continue_button)

        layout = QVBoxLayout()
        layout.addWidget(summary_label)
        layout.addWidget(QLabel("Warnings"))
        layout.addWidget(warnings_output, stretch=1)
        layout.addLayout(button_row)
        self.setLayout(layout)


class _EquatorialGridPreview(QWidget):
    def __init__(self, colors: dict[str, str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._colors = dict(colors)
        self.setMinimumHeight(120)

    def set_colors(self, colors: dict[str, str]) -> None:
        self._colors = dict(colors)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        background = QColor(self._colors.get("plot_bg", default_custom_theme_colors()["plot_bg"]))
        axis_color = QColor(self._colors.get("plot_axis", default_custom_theme_colors()["plot_axis"]))
        ra_color = QColor(self._colors.get("ra_grid", default_custom_theme_colors()["ra_grid"]))
        dec_color = QColor(self._colors.get("dec_grid", default_custom_theme_colors()["dec_grid"]))

        painter.fillRect(rect, background)
        painter.setPen(QPen(axis_color, 1))
        painter.drawRect(rect)

        inner = rect.adjusted(16, 16, -16, -16)
        ra_positions = [inner.top() + int(inner.height() * factor) for factor in (0.22, 0.5, 0.78)]
        dec_positions = [inner.left() + int(inner.width() * factor) for factor in (0.2, 0.52, 0.82)]

        painter.setPen(QPen(ra_color, 2))
        for y_pos in ra_positions:
            painter.drawLine(inner.left(), y_pos, inner.right(), y_pos)

        painter.setPen(QPen(dec_color, 2))
        for x_pos in dec_positions:
            painter.drawLine(x_pos, inner.top(), x_pos, inner.bottom())

        painter.setPen(axis_color)
        painter.drawText(inner.adjusted(4, 2, -4, -2), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, "RA")
        painter.drawText(inner.adjusted(4, 2, -4, -2), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight, "Dec")
        painter.drawText(inner.adjusted(0, 0, 0, -8), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, "05h20m   +12d")
        painter.end()


class _AsteroidOverlayPreview(QWidget):
    def __init__(self, colors: dict[str, str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._colors = dict(colors)
        self.setMinimumHeight(120)

    def set_colors(self, colors: dict[str, str]) -> None:
        self._colors = dict(colors)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        defaults = default_custom_theme_colors()
        background = QColor(self._colors.get("plot_bg", defaults["plot_bg"]))

        painter.fillRect(rect, background)
        painter.setPen(QPen(QColor("#3a3a3a"), 1))
        painter.drawRect(rect)

        self._draw_preview_sample(
            painter,
            sample_rect=QRect(rect.left() + 10, rect.top() + 10, max(120, (rect.width() // 2) - 20), rect.height() - 20),
            title="Other objects",
            line_color_key="asteroid_other_overlay_line_color",
            circle_color_key="asteroid_other_overlay_circle_color",
            text_color_key="asteroid_other_overlay_text_color",
            line_width_key="asteroid_other_overlay_line_width",
            text_size_key="asteroid_other_overlay_text_size",
            defaults=defaults,
        )
        self._draw_preview_sample(
            painter,
            sample_rect=QRect(rect.center().x() + 4, rect.top() + 10, max(120, (rect.width() // 2) - 20), rect.height() - 20),
            title="Selected object",
            line_color_key="asteroid_overlay_line_color",
            circle_color_key="asteroid_overlay_circle_color",
            text_color_key="asteroid_overlay_text_color",
            line_width_key="asteroid_overlay_line_width",
            text_size_key="asteroid_overlay_text_size",
            defaults=defaults,
        )
        painter.end()

    def _draw_preview_sample(
        self,
        painter: QPainter,
        *,
        sample_rect: QRect,
        title: str,
        line_color_key: str,
        circle_color_key: str,
        text_color_key: str,
        line_width_key: str,
        text_size_key: str,
        defaults: dict[str, str],
    ) -> None:
        line_color = QColor(self._colors.get(line_color_key, defaults[line_color_key]))
        circle_color = QColor(self._colors.get(circle_color_key, defaults[circle_color_key]))
        text_color = QColor(self._colors.get(text_color_key, defaults[text_color_key]))
        try:
            line_width = max(0.5, float(self._colors.get(line_width_key, defaults[line_width_key])))
        except (TypeError, ValueError):
            line_width = 1.5
        try:
            text_size = max(7.0, float(self._colors.get(text_size_key, defaults[text_size_key])))
        except (TypeError, ValueError):
            text_size = 10.0

        painter.setPen(QPen(QColor("#454b57"), 1))
        painter.drawRoundedRect(sample_rect, 6, 6)

        title_font = painter.font()
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor("#d7dce6"))
        painter.drawText(
            QRect(sample_rect.left() + 10, sample_rect.top() + 6, sample_rect.width() - 20, 18),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            title,
        )

        center_x = sample_rect.left() + 34
        center_y = sample_rect.center().y() + 4
        arm = 14.0
        painter.setPen(QPen(line_color, line_width))
        painter.drawLine(int(center_x - arm), int(center_y - arm), int(center_x + arm), int(center_y + arm))
        painter.drawLine(int(center_x - arm), int(center_y + arm), int(center_x + arm), int(center_y - arm))
        painter.setPen(QPen(circle_color, line_width))
        painter.drawEllipse(int(center_x - 6), int(center_y - 6), 12, 12)
        painter.drawEllipse(int(center_x + 24), int(center_y - 12), 24, 24)

        label_font = painter.font()
        label_font.setBold(False)
        label_font.setPointSizeF(text_size)
        painter.setFont(label_font)
        painter.setPen(text_color)
        painter.drawText(
            QRect(center_x + 44, center_y - 16, max(40, sample_rect.right() - (center_x + 48)), 32),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            title,
        )
