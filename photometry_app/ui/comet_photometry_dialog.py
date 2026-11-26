"""Guided comet workspace using CAst's image viewer and inherited theme."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import math

import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QSplitter,
    QTableWidget, QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout, QWidget, QStackedWidget,
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from photometry_app.core.comet_photometry import (
    CometFrame, build_comet_stack_pair, comet_catalog_band, site_from_comet_headers,
    export_comet_measurements, fit_comet_zero_point, load_comet_session,
    measure_comet_aperture, save_comet_session,
)
from photometry_app.ui.comet_stack_preview import CONTRAST_PRESETS, render_comet_stack_preview
from photometry_app.core.comet_reporting import aperture_dust_proxy, clear_filter, magnitude_system, preparation_band
from photometry_app.core.comet_aperture_geometry import aperture_scale
from photometry_app.core.comet_extinction import MODELS, comparison_extinction
from photometry_app.core.comet_references import (
    comparison_path_pixels, query_comet_references, read_comparison_sequence, write_comparison_sequence,
)
from photometry_app.ui.comet_report_dialog import CometReportDialog
from photometry_app.core.comet_morphology import (
    build_radial_star_filter, comparison_star_fwhm, rectangle_separation, tail_geometry,
)
from photometry_app.core.solar_system import predict_detection_coordinates
from photometry_app.ui.image_view import AnnotatedImageView, ImageOverlay, SelectionOverlay


class ApertureRadiusBox(QDoubleSpinBox):
    def textFromValue(self, value):
        text = super().textFromValue(round(value, 7))
        decimal = self.locale().decimalPoint()
        return text.rstrip("0").rstrip(decimal) if decimal in text else text


def _radius_bounds(pair, scale, units):
    # Include the historical range of 1–2000 in either unit, even when a
    # saved angular radius was originally measured with the legacy scale.
    anchor = pair.provenance.get("legacy_pixel_scale", pair.pixel_scale)
    factor = scale if units else 1
    return min(1, 1 / scale, 1 / anchor) * factor, 2000 * max(1, 1 / scale, 1 / anchor) * factor


class CometPreparationWorker(QThread):
    completed = Signal(object, object, str)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, *, frames, reference_path, detection, solved_field, use_horizons,
                 observer, band, parent=None):
        super().__init__(parent)
        self.frames = frames
        self.reference_path = reference_path
        self.detection = detection
        self.solved_field = solved_field
        self.use_horizons = use_horizons
        self.observer = observer
        self.band = band
        self.distances = []

    def _wait_to_retry(self, seconds):
        # Keep Cancel responsive during backoff as well as between requests.
        for _ in range(seconds * 10):
            if self.isInterruptionRequested():
                raise InterruptedError("Preparation cancelled.")
            self.msleep(100)

    def _horizons_positions(self, frames):
        from astroquery.jplhorizons import Horizons
        from astropy.time import Time
        from requests import RequestException, HTTPError, ConnectionError, Timeout

        updated = []
        # Batch astrometric positions and distances at the same mid-exposure epochs.
        batch_size = 20
        for offset in range(0, len(frames), batch_size):
            batch = frames[offset:offset + batch_size]
            for attempt in range(3):
                if self.isInterruptionRequested():
                    raise InterruptedError("Preparation cancelled.")
                self.progress.emit(f"Getting JPL Horizons positions · frames {offset + 1}–{offset + len(batch)} of {len(frames)} · attempt {attempt + 1}/3")
                query = Horizons(id=self.detection.designation or self.detection.name,
                                 id_type="smallbody", location=self.observer,
                                 epochs=[Time(f.midpoint).jd for f in batch])
                query.TIMEOUT = 30
                try:
                    table = query.ephemerides(quantities="1,19,20", extra_precision=True, cache=attempt == 0)
                    break
                except RequestException as exc:
                    status = exc.response.status_code if exc.response is not None else None
                    retryable = (isinstance(exc, (ConnectionError, Timeout)) or
                                 isinstance(exc, HTTPError) and status in (429, 500, 502, 503, 504))
                    reason = f"HTTP {status}" if status is not None else "connection or timeout error"
                    if retryable and attempt < 2:
                        self.progress.emit(f"JPL Horizons returned a {reason}. Retrying…")
                        self._wait_to_retry(attempt + 1)
                        continue
                    raise RuntimeError(f"JPL Horizons request failed ({reason}) after {attempt + 1} attempt(s). No new stacks were created. Retry Prepare Photometry shortly.") from exc
            if self.isInterruptionRequested():
                raise InterruptedError("Preparation cancelled.")
            if len(table) != len(batch):
                raise ValueError("Horizons returned an incomplete ephemeris. Check the target designation.")
            # Match by epoch rather than assuming service row order.
            epochs = np.asarray(table["datetime_jd"], dtype=float)
            for frame in batch:
                epoch = Time(frame.midpoint).jd
                idx = int(np.argmin(abs(epochs - epoch)))
                if abs(epochs[idx] - epoch) * 86400 > 1:
                    raise ValueError("Horizons times do not match the selected exposures.")
                updated.append(replace(frame, ra_deg=float(table["RA"][idx]), dec_deg=float(table["DEC"][idx])))
                if "r" in table.colnames and "delta" in table.colnames:
                    r, delta = float(table["r"][idx]), float(table["delta"][idx])
                    if np.isfinite([r, delta]).all() and min(r, delta) > 0:
                        self.distances.append((r, delta))
        return updated

    def run(self):
        try:
            frames = self.frames
            tracking = "Catalog linear motion; inspect centering across the sequence"
            if self.use_horizons:
                frames = self._horizons_positions(frames)
                tracking = "JPL Horizons · observing site" if isinstance(self.observer, dict) else "JPL Horizons · geocentric"
            pair = build_comet_stack_pair(frames, reference_path=self.reference_path,
                                         progress=self.progress.emit, cancel=self.isInterruptionRequested,
                                         tracking_source=tracking)
            pair.provenance["observer"] = self.observer
            if len(self.distances) == len(frames) and self.distances:
                r, delta = np.mean(self.distances, axis=0)
                pair.provenance["geometry"] = {"heliocentric_au": float(r), "observer_au": float(delta),
                                               "source": "JPL Horizons, mean of selected exposure midpoints"}
            # The asteroid workflow queries V references for differential fluxes
            # and keeps its own passband policy.
            if type(self) is CometPreparationWorker and self.band == "V" and clear_filter(frames[0].filter_name):
                pair.provenance["clear_v_reference"] = True
            stars, note = [], "Instrumental measurements · this filter has no supported APASS passband."
            if self.band and not self.isInterruptionRequested():
                self.progress.emit("Finding comparison stars near the target…")
                name, stars = query_comet_references(pair, self.band)
                pair.provenance["comparison_catalog"] = name
                note = (f"{len(stars)} {name} stars available. Review their selection and residuals."
                        if stars else f"No usable {name} sequence. Instrumental photometry is available.")
            if self.isInterruptionRequested():
                raise InterruptedError("Preparation cancelled.")
            self.completed.emit(pair, stars, note)
        except Exception as exc:
            self.failed.emit(str(exc))


class CometPhotometryDialog(QDialog):
    workflow = "comet"
    preparation_worker_type = None
    def __init__(self, *, frame_paths, frame_metadata, detection_result, detection_index,
                 reference_path, observer=None, parent=None):
        super().__init__(parent)
        self.setObjectName("cometPhotometry")
        self.setWindowTitle("Photometry")
        self.resize(1240, 820)
        self.setMinimumSize(920, 660)
        self.paths = list(frame_paths)
        self.metadata = frame_metadata
        self.detection_result = detection_result
        self.detection = detection_result.detections[detection_index]
        self.reference_path = Path(reference_path)
        self.observer = observer if observer is not None else (site_from_comet_headers(self.paths) or "500")
        self._header_site = observer is None and isinstance(self.observer, dict)
        self._extinction_cache = None
        self.target_name = self.detection.name
        self.band = comet_catalog_band(detection_result.filter_name or "")
        self.pair = None
        self.comparisons = []
        self.rows = []
        self.zero_point = None
        self._worker = None
        self._restoring = False
        self._session_path = None
        self._review_note = ""
        self._sky_center = None
        self._sky_rectangle = None
        self._first_sky_corner = None
        self._star_widths = {}
        self._median_fwhm = None
        self._picking_sky = False
        self._picking_center = False
        self._picking_tail = False
        self._pick_click_guard = QTimer(self)
        self._pick_click_guard.setSingleShot(True)
        self._pick_click_guard.setInterval(QGuiApplication.styleHints().mouseDoubleClickInterval())
        self._tail_endpoint = None
        self._tail_contrast = None
        self._radial_filter_key = None
        self._radial_filter = None
        self._result_pages = []
        self._close_when_finished = False
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(180)
        self._debounce.timeout.connect(self._measure)
        self._build_ui()
        self._apply_theme()
        self._sync_ready()

    def _label(self, text, role="", parent=None):
        label = QLabel(text, parent or self)
        label.setWordWrap(True)
        if role:
            label.setProperty("role", role)
        return label

    def _button(self, text, callback, primary=False):
        button = QPushButton(text)
        button.setAutoDefault(False)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumHeight(34)
        if primary:
            button.setProperty("role", "primary")
        button.clicked.connect(callback)
        return button

    def _card(self, title, layout):
        card = QFrame()
        card.setProperty("role", "card")
        body = QVBoxLayout(card)
        body.setContentsMargins(14, 14, 14, 14)
        body.setSpacing(10)
        body.addWidget(self._label(title, "section"))
        body.addLayout(layout)
        return card

    def _spin(self, value, minimum, maximum, suffix=" px"):
        box = QDoubleSpinBox()
        box.setRange(minimum, maximum)
        box.setDecimals(1)
        box.setValue(value)
        box.setSuffix(suffix)
        box.setKeyboardTracking(False)
        box.valueChanged.connect(self._schedule_measure)
        return box

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 14)
        root.setSpacing(12)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        self.title_label = self._label(self.target_name, "title")
        font = QFont(self.font())
        font.setPointSize(max(15, font.pointSize() + 5))
        font.setBold(True)
        self.title_label.setFont(font)
        titles.addWidget(self.title_label)
        self.subtitle = self._label("Photometry  ·  Measure the coma, inspect the evidence, keep the full record.", "muted")
        titles.addWidget(self.subtitle)
        header.addLayout(titles, 1)
        self.open_button = self._button("Open Session…", self._open_session)
        header.addWidget(self.open_button)
        root.addLayout(header)

        steps = QHBoxLayout()
        self.step_buttons = []
        for index, label in enumerate(("1  Prepare", "2  Measure", "3  Review")):
            button = self._button(label, lambda _=False, i=index: self._select_step(i))
            button.setProperty("role", "step")
            self.step_buttons.append(button)
            steps.addWidget(button, 1)
        root.addLayout(steps)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        controls = QVBoxLayout(left)
        controls.setContentsMargins(0, 0, 8, 0)
        controls.setSpacing(12)
        preparation = QVBoxLayout()
        filters = sorted({getattr(self.metadata.get(str(p.resolve())), "filter_name", None) or "Unknown" for p in self.paths})
        self.frame_summary = self._label(f"{len(self.paths)} frames · {', '.join(filters)}", "muted")
        preparation.addWidget(self.frame_summary)
        if self._header_site:
            preparation.addWidget(self._label(
                f"FITS observing site · {self.observer['lat']:.4f}° latitude, "
                f"{self.observer['lon']:.4f}° east longitude, {1000*self.observer['elevation']:.0f} m "
                "(0 m when height is absent).", "muted"))
        preparation.addWidget(self._label("Review frames, then prepare the aligned previews.", "muted"))
        if not isinstance(self.observer, dict):
            preparation.addWidget(self._label("No observing site configured. Horizons will use Earth's center; set your site in Settings for local positions.", "muted"))
        self.calibrated = QCheckBox("Images are calibrated and linear")
        self.calibrated.setToolTip("Use bias/dark/flat-corrected monochrome frames. No image calibration is applied by this workspace.")
        self.calibrated.toggled.connect(self._sync_ready)
        preparation.addWidget(self.calibrated)
        self.prepare_button = self._button("Prepare Photometry", self._prepare, True)
        preparation.addWidget(self.prepare_button)
        self.cancel_button = self._button("Cancel Preparation", self._cancel)
        self.cancel_button.hide()
        preparation.addWidget(self.cancel_button)
        controls.addWidget(self._card("Your images", preparation))

        measurement = QVBoxLayout()
        self.aperture_guidance = self._label("Prepare Photometry first. The comet image and aperture controls will appear when the stacks are ready.", "muted")
        measurement.addWidget(self.aperture_guidance)
        self.aperture_controls = QWidget()
        aperture_layout = QVBoxLayout(self.aperture_controls)
        aperture_layout.setContentsMargins(0, 0, 0, 0)
        self.show_comet = self._button("Show Comet", self._show_comet)
        aperture_layout.addWidget(self.show_comet)
        self.pick_center = self._button("Set Coma Center", self._pick_center)
        aperture_layout.addWidget(self.pick_center)
        aperture_form = QFormLayout()
        self.radius = ApertureRadiusBox()
        self.radius.setDecimals(12)
        self.radius.setRange(1, 2000)
        self.radius.setValue(12)
        self.radius.setSuffix(" px")
        self.radius.setKeyboardTracking(False)
        self.radius.valueChanged.connect(self._schedule_measure)
        aperture_form.addRow("Coma radius", self.radius)
        self.units = QComboBox()
        self.units.addItems(["Pixels", "Arcseconds"])
        self.units.currentIndexChanged.connect(self._units_changed)
        aperture_form.addRow("Units", self.units)
        aperture_layout.addLayout(aperture_form)
        self.radius_hint = self._label("A fixed-aperture measurement of the coma.", "muted")
        aperture_layout.addWidget(self.radius_hint)
        self.reset_center = self._button("Reset to Predicted Center", self._reset_center)
        aperture_layout.addWidget(self.reset_center)
        if self.workflow == "comet":
            tail_actions = QHBoxLayout()
            self.pick_tail = self._button("Measure Tail", self._pick_tail)
            self.clear_tail = self._button("Clear Tail", self._clear_tail)
            tail_actions.addWidget(self.pick_tail)
            tail_actions.addWidget(self.clear_tail)
            aperture_layout.addLayout(tail_actions)
            self.tail_result = self._label("", "muted")
            aperture_layout.addWidget(self.tail_result)
        measurement.addWidget(self.aperture_controls)
        self.measurement_card = self._card("Coma aperture", measurement)
        controls.addWidget(self.measurement_card)

        advanced_header = QToolButton()
        advanced_header.setText("Advanced controls")
        advanced_header.setCheckable(True)
        advanced_header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        advanced_header.setArrowType(Qt.ArrowType.RightArrow)
        controls.addWidget(advanced_header)
        self.advanced = QWidget()
        advanced_layout = QFormLayout(self.advanced)
        advanced_layout.setContentsMargins(4, 4, 4, 4)
        if self.workflow == "comet":
            self.star_filter = QCheckBox("Filter bright stars in coma")
            self.star_filter.setToolTip("Replace bright outliers in two-pixel rings around the coma. A jet or tail can also trigger replacement. Compare both fluxes before reporting.")
            self.star_filter.toggled.connect(self._schedule_measure)
            advanced_layout.addRow(self.star_filter)
            self.filter_statistic = QComboBox()
            self.filter_statistic.addItem("Mean (COBS recipe)", "mean")
            self.filter_statistic.addItem("Median", "median")
            self.filter_statistic.currentIndexChanged.connect(self._schedule_measure)
            advanced_layout.addRow("Radial ring statistic", self.filter_statistic)
            self.filter_ignore = self._spin(7, 0, 2000)
            self.filter_coefficient = self._spin(3, 1, 20, " × ring level")
            advanced_layout.addRow("Protected core radius", self.filter_ignore)
            advanced_layout.addRow("Star threshold", self.filter_coefficient)
            self.filter_preview = self._button("Compare Filtered Aperture…", self._preview_filter)
            advanced_layout.addRow(self.filter_preview)
        self.background_mode = QComboBox()
        self.background_mode.addItems(["Sky annulus", "Separate sky patch"])
        if self.workflow == "comet":
            self.background_mode.addItem("Separate sky rectangle")
        self.background_mode.currentIndexChanged.connect(self._cancel_image_pick)
        self.background_mode.currentIndexChanged.connect(self._schedule_measure)
        advanced_layout.addRow("Background", self.background_mode)
        self.pick_sky = self._button("Pick Sky Patch on Image", self._pick_sky)
        self.pick_sky.hide()
        advanced_layout.addRow(self.pick_sky)
        self.sky_patch_radius = self._spin(12, 3, 1000)
        self.sky_patch_radius.setEnabled(False)
        advanced_layout.addRow("Sky patch radius", self.sky_patch_radius)
        self.auto_sky = QCheckBox("Scale sky annulus with aperture")
        self.auto_sky.setChecked(True)
        self.auto_sky.toggled.connect(self._schedule_measure)
        advanced_layout.addRow(self.auto_sky)
        self.sky_inner = self._spin(24, 2, 6000)
        self.sky_outer = self._spin(36, 3, 8000)
        advanced_layout.addRow("Sky inner radius", self.sky_inner)
        advanced_layout.addRow("Sky outer radius", self.sky_outer)
        self.star_radius = self._spin(8, 2, 100)
        advanced_layout.addRow("Star aperture", self.star_radius)
        self.solar_colors = QCheckBox("Only solar-like stars (B−V 0.5–0.8)")
        self.solar_colors.toggled.connect(self._schedule_measure)
        advanced_layout.addRow(self.solar_colors)
        if self.workflow == "comet":
            self.extinction = QComboBox()
            self.extinction.addItems(MODELS)
            self.extinction.currentIndexChanged.connect(self._schedule_measure)
            self.extinction.setToolTip("Correct differential attenuation between the comet and the comparison stars across the selected exposures. Green visual presets require V frames.")
            advanced_layout.addRow("Differential extinction", self.extinction)
            self.extinction_k = self._spin(0, 0, 3, " mag/airmass")
            self.extinction_k.setDecimals(3)
            self.extinction_k.setSingleStep(0.01)
            self.extinction_k.setEnabled(False)
            advanced_layout.addRow("Measured coefficient", self.extinction_k)
            self.extinction.currentTextChanged.connect(lambda model: self.extinction_k.setEnabled(model == "Measured coefficient"))
        self.tracking = QComboBox()
        self.tracking.addItems(["JPL Horizons", "Catalog motion · short sequences"])
        self.tracking.setToolTip("Changing tracking requires Prepare again. Catalog motion uses constant rates; verify the stack.")
        advanced_layout.addRow("Tracking", self.tracking)
        if self.workflow == "comet":
            self.clear_reference = QCheckBox("Reference clear/L to APASS V")
            self.clear_reference.setToolTip("Applies on the next preparation. The magnitude keeps the original filter label.")
            advanced_layout.addRow(self.clear_reference)
        advanced_layout.addRow(self._label("Sky and star radii are always in pixels. Color filtering does not apply a photometric transformation.", "muted"))
        self.advanced.hide()
        advanced_header.toggled.connect(self.advanced.setVisible)
        advanced_header.toggled.connect(lambda on: advanced_header.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow))
        controls.addWidget(self.advanced)
        controls.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(left)
        scroll.setMinimumWidth(300)
        splitter.addWidget(scroll)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 0, 0)
        self.result_label = self._label("Start with your calibrated images", "result")
        right_layout.addWidget(self.result_label)
        self.result_detail = self._label("No comet image has been prepared yet. Confirm your images are calibrated, then click Prepare Photometry on the left.", "muted")
        right_layout.addWidget(self.result_detail)
        self.tabs = QTabWidget()
        self.frames_table = self._table(["Use", "Frame", "Exposure", "Filter", "Observed"])
        for row, path in enumerate(self.paths):
            self.frames_table.insertRow(row)
            metadata = self.metadata.get(str(path.resolve()))
            item = QTableWidgetItem()
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.frames_table.setItem(row, 0, item)
            values = [path.name, str(getattr(metadata, "exposure_seconds", None) or "Missing"),
                      getattr(metadata, "filter_name", None) or "Unknown",
                      str(getattr(metadata, "date_obs", None) or "Missing")]
            for col, value in enumerate(values, 1):
                self.frames_table.setItem(row, col, QTableWidgetItem(value))
        self.frames_table.itemChanged.connect(self._sync_ready)
        self.tabs.addTab(self.frames_table, "Frames")
        self.comet_view = AnnotatedImageView(self)
        self.comet_view.set_message("Your comet-aligned stack will appear here.")
        self.comet_view.imageDoubleClicked.connect(self._center_clicked)
        self.comet_view.imagePressed.connect(self._sky_clicked)
        comet_page = QWidget()
        comet_layout = QVBoxLayout(comet_page)
        comet_layout.setContentsMargins(0, 4, 0, 0)
        self.comet_guidance = self._label("The inner circle is the coma aperture; the two outer circles mark the sky annulus. Use Set Coma Center, then click the coma in this image. Drag to pan; scroll to zoom.", "muted")
        comet_layout.addWidget(self.comet_guidance)
        if self.workflow == "comet":
            contrast_controls = QHBoxLayout()
            contrast_controls.addWidget(self._label("Display contrast"))
            self.comet_contrast = QComboBox()
            self.comet_contrast.addItems(CONTRAST_PRESETS)
            self.comet_contrast.setToolTip("Adjust the preview stretch to inspect the coma and tail. All measurements use the linear stack.")
            self.comet_contrast.currentTextChanged.connect(self._contrast_changed)
            contrast_controls.addWidget(self.comet_contrast)
            contrast_controls.addStretch()
            comet_layout.addLayout(contrast_controls)
        comet_layout.addWidget(self.comet_view, 1)
        self._add_result_page(comet_page, "Comet", "Prepare the comet image", "Prepare Photometry creates a comet-aligned image and places an aperture at the predicted comet position. You can then adjust its center and radius here.")
        comparison_page = QWidget()
        comp_layout = QVBoxLayout(comparison_page)
        comp_layout.setContentsMargins(0, 4, 0, 0)
        self.comparison_hint = self._label("Comparison stars are measured on the star-aligned stack.", "muted")
        comp_layout.addWidget(self.comparison_hint)
        if self.workflow == "comet":
            sequence = QHBoxLayout()
            self.import_refs = self._button("Import Comparison CSV…", self._import_references)
            self.export_refs = self._button("Export Comparison CSV…", self._export_references)
            sequence.addWidget(self.import_refs)
            sequence.addWidget(self.export_refs)
            comp_layout.addLayout(sequence)
        comp_split = QSplitter(Qt.Orientation.Vertical)
        comp_split.setChildrenCollapsible(False)
        self.star_view = AnnotatedImageView(self)
        self.star_view.setMinimumHeight(160)
        self.star_view.set_message("Catalog comparison stars will appear here.")
        comp_split.addWidget(self.star_view)
        columns = ["Use", "Star", "Catalog mag", "B−V", "Residual", "Quality"]
        if self.workflow == "comet":
            columns.append("FWHM (px)")
            widths = QHBoxLayout()
            self.fwhm_hint = self._label("Select comparison stars to estimate the stellar FWHM.", "muted")
            widths.addWidget(self.fwhm_hint, 1)
            self.apply_fwhm = self._button("Use 4 × FWHM Radius", self._use_fwhm)
            self.apply_fwhm.setEnabled(False)
            widths.addWidget(self.apply_fwhm)
            comp_layout.addLayout(widths)
        self.star_table = self._table(columns)
        self.star_table.setMinimumHeight(130)
        self.star_table.itemChanged.connect(self._comparison_changed)
        self.star_table.itemSelectionChanged.connect(self._highlight_star)
        comp_split.addWidget(self.star_table)
        comp_split.setSizes([240, 180])
        comp_layout.addWidget(comp_split, 1)
        self._add_result_page(comparison_page, "Comparison Stars", "Prepare the comparison-star image", "Prepare Photometry creates a star-aligned image and searches for comparison stars when a catalog supports the selected band.")
        self.figure = Figure(figsize=(6, 4), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self._add_result_page(self.canvas, "Growth Curve", "Review how brightness changes with aperture", "Prepare Photometry first, then choose the coma aperture in the Comet tab. The growth curve updates as you adjust the radius.")
        self.measurements_table = self._table(["Radius (px)", "Radius (″)", "Flux (native units/s)", "Magnitude", "Est. error", "Quality"])
        self.measurements_table.horizontalHeaderItem(2).setToolTip("Background-subtracted sum in native linear FITS pixel units per second. Check BUNIT in the source images for the detector unit.")
        self._add_result_page(self.measurements_table, "Measurements", "Your measurements will appear here", "Prepare Photometry first. Measurements will include aperture size, flux, magnitude, uncertainty, and quality flags.")
        self.tabs.currentChanged.connect(self._sync_steps)
        right_layout.addWidget(self.tabs, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([310, 880])
        root.addWidget(splitter, 1)
        self.status = self._label("Review the selected frames, confirm calibration, then click Prepare Photometry.", "muted")
        root.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumHeight(4)
        self.progress.setTextVisible(False)
        self.progress.hide()
        root.addWidget(self.progress)
        footer = QHBoxLayout()
        self.save_button = self._button("Save Session…", self._save_session)
        self.export_button = self._button("Export Measurements…", self._export, True)
        footer.addWidget(self.save_button)
        if self.workflow == "comet":
            self.report_button = self._button("Comet Report…", self._open_report)
            footer.addWidget(self.report_button)
        footer.addStretch()
        footer.addWidget(self._button("Close", self.reject))
        footer.addWidget(self.export_button)
        root.addLayout(footer)
        self._sync_steps()

    def _add_result_page(self, content, title, heading, explanation):
        page = QStackedWidget()
        empty = QWidget()
        layout = QVBoxLayout(empty)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addStretch()
        layout.addWidget(self._label(heading, "result"))
        layout.addWidget(self._label(explanation, "muted"))
        action = self._button("Go to Prepare", self._go_to_prepare, True)
        layout.addWidget(action, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        page.addWidget(empty)
        page.addWidget(content)
        self._result_pages.append((page, action))
        self.tabs.addTab(page, title)

    def _go_to_prepare(self):
        self.tabs.setCurrentIndex(0)
        if self._worker is None:
            if self.calibrated.isChecked():
                self.prepare_button.setFocus()
                self.status.setText("Click Prepare Photometry on the left to create the comet and star images.")
            else:
                self.calibrated.setFocus()
                self.status.setText("Confirm Images are calibrated and linear, then click Prepare Photometry on the left.")

    def _table(self, columns):
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        return table

    def _apply_theme(self):
        palette = self.palette()
        parent = self.parentWidget()
        colors = parent._resolved_theme_editor_colors() if parent and hasattr(parent, "_resolved_theme_editor_colors") else {}
        self._bg = colors.get("panel_bg", palette.window().color().name())
        self._text = colors.get("text", palette.windowText().color().name())
        self._accent = colors.get("accent", palette.highlight().color().name())
        muted = colors.get("placeholder", palette.placeholderText().color().name())
        border = colors.get("gridline", palette.mid().color().name())
        card = QColor(self._bg).lighter(108).name()
        accent = QColor(self._accent)
        channels = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055)**2
                    for v in (accent.redF(), accent.greenF(), accent.blueF())]
        luminance = sum(a * b for a, b in zip(channels, (0.2126, 0.7152, 0.0722)))
        contrast = "#101820" if luminance > 0.179 else "#ffffff"
        self.setStyleSheet(f"""
            QDialog#cometPhotometry {{ background: {self._bg}; color: {self._text}; }}
            QLabel {{ color: {self._text}; background: transparent; }}
            QLabel[role='muted'] {{ color: {muted}; }}
            QLabel[role='section'] {{ font-weight: 600; }}
            QLabel[role='result'] {{ font-size: 21px; font-weight: 600; padding: 4px 0; }}
            QFrame[role='card'] {{ background: {card}; border: 1px solid {border}; border-radius: 12px; }}
            QPushButton {{ padding: 7px 12px; border: 1px solid {border}; border-radius: 8px;
                           background: {card}; color: {self._text}; font-weight: 600; }}
            QPushButton:hover {{ border-color: {self._accent}; }}
            QPushButton[role='primary'], QPushButton[active='true'] {{ background: {self._accent}; color: {contrast}; }}
            QPushButton:disabled {{ color: {muted}; background: {self._bg}; border-color: {border}; }}
            QDoubleSpinBox, QComboBox {{ min-height: 28px; padding: 2px 5px; }}
            QTabWidget::pane {{ border: 1px solid {border}; border-radius: 6px; }}
            QHeaderView::section {{ padding: 7px; }}
            QProgressBar::chunk {{ background: {self._accent}; }}
        """)
        self.figure.set_facecolor(self._bg)

    def _sync_steps(self, *_):
        active = 0 if self.tabs.currentIndex() == 0 else 1 if self.tabs.currentIndex() < 3 else 2
        for index, button in enumerate(self.step_buttons):
            button.setProperty("active", index == active)
            button.style().unpolish(button)
            button.style().polish(button)

    def _select_step(self, index):
        self.tabs.setCurrentIndex((0, 1, 3)[index])

    def _sync_ready(self, *_):
        busy = self._worker is not None
        if self.workflow == "comet":
            self.import_refs.setEnabled(self.pair is not None and bool(self.band) and not busy)
            self.export_refs.setEnabled(bool(self.comparisons) and not busy)
            self.import_refs.setToolTip("Use sky-coordinate comparisons in the prepared catalog band. For L images select the V reference option before preparing.")
        selected = sum(self.frames_table.item(row, 0).checkState() == Qt.CheckState.Checked
                       for row in range(self.frames_table.rowCount()))
        self.prepare_button.setEnabled(not busy and self.calibrated.isChecked() and selected > 0)
        self.prepare_button.setToolTip("Confirm that your frames are calibrated and linear." if not self.calibrated.isChecked() else "Prepare matching linear stacks using the checked frames.")
        self.open_button.setEnabled(not busy)
        self.frames_table.setEnabled(not busy)
        self.tracking.setEnabled(not busy)
        self.aperture_controls.setVisible(self.pair is not None)
        self.aperture_controls.setEnabled(self.pair is not None and not busy)
        self.aperture_guidance.setText(
            "In the Comet tab, use Set Coma Center and click the coma. Adjust Coma radius to resize the inner circle. Double-clicking the image also sets the center."
            if self.pair is not None else
            "Preparing the comet image… Aperture controls will appear when preparation finishes."
            if busy else "Prepare Photometry first. The comet image and aperture controls will appear when the stacks are ready.")
        self.advanced.setEnabled(not busy)
        self.star_table.setEnabled(not busy)
        if self.workflow == "comet":
            enabled = self.pair is not None and not busy
            self.clear_tail.setEnabled(enabled and self._tail_endpoint is not None)
            self.comet_contrast.setEnabled(enabled)
            self.star_filter.setEnabled(enabled)
            self.apply_fwhm.setEnabled(enabled and self._median_fwhm is not None)
            self.filter_ignore.setEnabled(enabled and self.star_filter.isChecked())
            self.filter_statistic.setEnabled(enabled and self.star_filter.isChecked())
            self.filter_coefficient.setEnabled(enabled and self.star_filter.isChecked())
            self.filter_preview.setEnabled(enabled and self.star_filter.isChecked() and bool(self.rows))
        for page, action in self._result_pages:
            page.setCurrentIndex(1 if self.pair is not None else 0)
            action.setText("View Preparation Progress" if busy else "Go to Prepare")
        self.save_button.setEnabled(self.pair is not None and not busy)
        self.export_button.setEnabled(bool(self.rows) and not busy)
        if self.workflow == "comet":
            self.report_button.setEnabled(bool(self.rows) and not busy)
        self.cancel_button.setVisible(busy)
        self.progress.setVisible(busy)

    def _prepare(self):
        self._cancel_image_pick()
        frames = []
        try:
            for row, path in enumerate(self.paths):
                if self.frames_table.item(row, 0).checkState() != Qt.CheckState.Checked:
                    continue
                metadata = self.metadata.get(str(path.resolve()))
                if metadata is None or metadata.date_obs is None or not metadata.exposure_seconds or metadata.exposure_seconds <= 0:
                    raise ValueError(f"{path.name}: an observation time and positive exposure are required.")
                midpoint = metadata.date_obs + timedelta(seconds=metadata.exposure_seconds / 2)
                if midpoint.tzinfo is None:
                    raise ValueError(f"{path.name}: resolve the observation timezone before measuring.")
                ra, dec = predict_detection_coordinates(self.detection,
                    reference_observation_time=self.detection_result.prediction_time, observation_time=midpoint)
                frames.append(CometFrame(path, midpoint, metadata.exposure_seconds, ra, dec, metadata.filter_name or ""))
            if not frames:
                raise ValueError("Select at least one frame.")
            band = preparation_band(frames[0].filter_name, self.workflow == "comet" and self.clear_reference.isChecked())
            self._worker = (self.preparation_worker_type or CometPreparationWorker)(frames=frames, reference_path=self.reference_path,
                detection=self.detection, solved_field=self.detection_result.solved_field,
                use_horizons=self.tracking.currentIndex() == 0, observer=self.observer, band=band, parent=self)
            self._worker.progress.connect(self.status.setText)
            self._worker.completed.connect(self._prepared)
            self._worker.failed.connect(self._failed)
            self._worker.finished.connect(self._worker_finished)
            self._sync_ready()
            self._worker.start()
        except Exception as exc:
            self.status.setText(str(exc))

    def _prepared(self, pair, stars, note):
        self.pair = pair
        self.band = preparation_band(pair.provenance.get("filter", ""), pair.provenance.get("clear_v_reference", False))
        self.target_name = self.detection.name
        self.title_label.setText(self.target_name)
        self.comparisons = stars
        self._review_note = note
        self._center = (pair.center_x, pair.center_y)
        self.radius.setRange(*_radius_bounds(pair, self._angular_scale(), self.units.currentIndex()))
        self._sky_center = None
        self._sky_rectangle = None
        self._star_widths.clear()
        self._tail_endpoint = None
        self._tail_contrast = None
        self._radial_filter_key = None
        self._radial_filter = None
        self._cancel_image_pick()
        self._session_path = None
        self.prepare_button.setText("Prepare Photometry")
        self.subtitle.setText("Photometry  ·  Measure the coma, inspect the evidence, keep the full record.")
        self._show_pair()
        self._measure()
        self._show_comet()

    def _failed(self, message):
        self.status.setText(message + (" For a short sequence, Advanced controls → Tracking → Catalog motion offers an approximate offline alternative; inspect the alignment."
                                      if "Horizons" in message and self.tracking.currentIndex() == 0 else ""))

    def _worker_finished(self):
        worker = self._worker
        self._worker = None
        if worker:
            worker.deleteLater()
        self._sync_ready()
        if self._close_when_finished:
            self.reject()

    def _cancel(self):
        if self._worker:
            self._worker.requestInterruption()
            self.status.setText("Cancelling after the current image or catalog request…")

    def reject(self):
        if self._worker:
            self._close_when_finished = True
            self._cancel()
            return
        super().reject()

    def closeEvent(self, event):
        if self._worker:
            self._close_when_finished = True
            self._cancel()
            event.ignore()
            return
        super().closeEvent(event)

    def _show_pair(self):
        pair = self.pair
        # AnnotatedImageView keys its display cache to an existing file. A saved
        # session can supply that identity even when originals have moved.
        display_path = pair.reference_path if pair.reference_path.exists() else self._session_path
        for view, data in ((self.comet_view, pair.comet), (self.star_view, pair.stars)):
            preset = self.comet_contrast.currentText() if self.workflow == "comet" and view is self.comet_view else "Balanced"
            display, raster = render_comet_stack_preview(data, display_path, preset)
            # Editor click mode emits single clicks on release, while leaving
            # dragging available for panning. Only an armed picker changes data.
            view.set_content(display, [], [], view is self.comet_view, reset_view=True, pre_rendered_qimage=raster)
        self.frame_summary.setText(f"{pair.frame_count} prepared frames · {pair.provenance.get('filter') or 'Unknown filter'}\n{pair.provenance.get('tracking', '')}")
        self.comparison_hint.setText(self._review_note)

    def _contrast_changed(self, preset):
        if self._restoring or self.pair is None:
            return
        path = self.pair.reference_path if self.pair.reference_path.exists() else self._session_path
        display, raster = render_comet_stack_preview(self.pair.comet, path, preset)
        self.comet_view.set_content(display, [], [], True, pre_rendered_qimage=raster)
        self._measure()

    def _center_clicked(self, x, y, button, modifiers):
        if (self.pair is not None and self._worker is None and not self._picking_sky
                and not self._picking_tail and button == Qt.MouseButton.LeftButton
                and (self._picking_center or not self._pick_click_guard.isActive())):
            self._center = (float(x), float(y))
            self._cancel_image_pick()
            self._schedule_measure()

    def _cancel_image_pick(self, *_):
        self._picking_center = False
        self._first_sky_corner = None
        self._picking_sky = False
        self._picking_tail = False
        if self.workflow == "comet":
            self.pick_tail.setText("Measure Tail")
        self.comet_view.unsetCursor()
        self.pick_center.setText("Set Coma Center")

    def _show_comet(self):
        if self.pair is not None:
            self.tabs.setCurrentIndex(1)
            radius = self.radius.value() / (self._angular_scale() if self.units.currentIndex() else 1)
            height, width = self.pair.comet.shape
            fit_scale = min(max(1, self.comet_view.width() - 16) / width,
                            max(1, self.comet_view.height() - 16) / height)
            # Keep the aperture discernible even on a large detector: target a
            # roughly 32-screen-pixel radius, without extreme pixel enlargement.
            zoom = max(1.0, min(3.0, 32 / radius) / fit_scale)
            self.comet_view.focus_on(*self._center, minimum_zoom_scale=zoom)

    def _pick_center(self):
        if self.pair is None or self._worker is not None:
            return
        if self._picking_center:
            self._cancel_image_pick()
            self.status.setText("Center selection cancelled. Drag the image to pan, or choose Set Coma Center to try again.")
            return
        self._cancel_image_pick()
        self._picking_center = True
        self._show_comet()
        self.pick_center.setText("Cancel Center Selection")
        self.comet_view.setCursor(Qt.CursorShape.CrossCursor)
        self.status.setText("Click once on the coma center in the Comet image on the right. The inner aperture circle will move there.")

    def _pick_sky(self):
        if self.pair and self._worker is None:
            self._cancel_image_pick()
            self._picking_sky = True
            self.tabs.setCurrentIndex(1)
            self.status.setText("Click the first corner of a clear sky rectangle." if self.background_mode.currentIndex() == 2
                                else "Click a clear background patch away from the coma and tail.")
            self.comet_view.setCursor(Qt.CursorShape.CrossCursor)

    def _pick_tail(self):
        if self.pair is None or self._worker is not None:
            return
        was_picking = self._picking_tail
        self._cancel_image_pick()
        if was_picking:
            self.status.setText("Tail selection cancelled.")
            return
        self._picking_tail = True
        self.tabs.setCurrentIndex(1)
        self.pick_tail.setText("Cancel Tail Selection")
        self.comet_view.setCursor(Qt.CursorShape.CrossCursor)
        self.status.setText("Click the visible end of the tail. The ruler starts at the selected coma center.")

    def _clear_tail(self):
        self._tail_endpoint = None
        self._tail_contrast = None
        self._cancel_image_pick()
        self._schedule_measure()

    def _sky_clicked(self, x, y, button, modifiers):
        if self._worker is not None or self.pair is None:
            return
        if self._picking_center:
            self._center_clicked(x, y, button, modifiers)
            return
        if self._picking_tail and button == Qt.MouseButton.LeftButton:
            tip = (float(x), float(y))
            height, width = self.pair.comet.shape
            if not (0 <= x < width and 0 <= y < height):
                self.status.setText("Choose a tail endpoint inside the image.")
                return
            try:
                tail_geometry(self.pair.header_text, self._center, tip)
            except ValueError as exc:
                self.status.setText(str(exc))
                return
            self._tail_endpoint = tip
            self._tail_contrast = self.comet_contrast.currentText()
            # A double click delivers the first single-click release first.
            # Consuming that placement must not then recenter the coma there.
            self._pick_click_guard.start()
            self._cancel_image_pick()
            self._schedule_measure()
            return
        if self._picking_sky and button == Qt.MouseButton.LeftButton:
            if self.background_mode.currentIndex() == 2:
                if self._first_sky_corner is None:
                    self._first_sky_corner = (float(x), float(y))
                    self.status.setText("Click the opposite corner of the sky rectangle.")
                    return
                sx, sy = self._first_sky_corner
                rect = (min(sx,x), min(sy,y), max(sx,x), max(sy,y))
                if rect[2] == rect[0] or rect[3] == rect[1]:
                    self.status.setText("Click an opposite corner with a different x and y.")
                    return
                self._sky_rectangle = rect
                self._pick_click_guard.start()
                self._cancel_image_pick()
                self._schedule_measure()
                return
            self._sky_center = (float(x), float(y))
            self._pick_click_guard.start()
            self._picking_sky = False
            self.comet_view.unsetCursor()
            self.background_mode.setCurrentIndex(1)
            self._schedule_measure()

    def _reset_center(self):
        if self.pair:
            self._cancel_image_pick()
            self._center = (self.pair.center_x, self.pair.center_y)
            self._show_comet()
            self._schedule_measure()

    def _angular_scale(self):
        if self.workflow == "asteroid":
            return self.pair.pixel_scale
        return aperture_scale(self.pair.header_text, *self._center).equivalent_arcsec

    def _units_changed(self, index):
        if self.pair:
            scale = self._angular_scale()
            new_value = self.radius.value() * (scale if index else 1 / scale)
            self.radius.setRange(*_radius_bounds(self.pair, scale, index))
            if not self._restoring:
                self.radius.setValue(new_value)
        self.radius.setSuffix(" ″" if index == 1 else " px")
        self._schedule_measure()

    def _schedule_measure(self, *_):
        patch_mode = self.background_mode.currentIndex() == 1
        separate = self.background_mode.currentIndex() != 0
        self.pick_sky.setText("Pick Sky Rectangle on Image" if self.background_mode.currentIndex() == 2 else "Pick Sky Patch on Image")
        self.pick_sky.setVisible(separate)
        self.sky_patch_radius.setEnabled(patch_mode)
        self.auto_sky.setEnabled(not separate)
        if self.pair and not self._restoring:
            self.export_button.setEnabled(False)
            self._debounce.start()

    def _comparison_changed(self, item):
        if item.column() == 0 and not self._restoring and item.row() < len(self.comparisons):
            self.comparisons[item.row()].selected = item.checkState() == Qt.CheckState.Checked
            self._schedule_measure()

    def _highlight_star(self):
        row = self.star_table.currentRow()
        if 0 <= row < len(self.comparisons):
            star = self.comparisons[row]
            # Overlay labels link the table to the same image, without moving
            # the viewport away from the user's comparison context.
            self.star_view.set_overlays([ImageOverlay(star.source_id, star.name, star.x, star.y,
                self.star_radius.value(), 1.5 * self.star_radius.value(), 2.5 * self.star_radius.value(), self._accent)])

    def _import_references(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Comparison Sequence", "", "Comparison sequence (*.csv)")
        if not path:
            return
        try:
            name, stars = read_comparison_sequence(Path(path), self.pair, self.band)
            self.comparisons = stars
            self.pair.provenance["comparison_catalog"] = name
            self._review_note = f"{len(stars)} {name} comparison stars from {Path(path).name}."
            self.comparison_hint.setText(self._review_note)
            self._measure()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Could not import comparison stars", str(exc))

    def _export_references(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Comparison Sequence", "comparisons.csv", "Comparison sequence (*.csv)")
        if path:
            try:
                write_comparison_sequence(Path(path), self.pair, self.comparisons, self.band)
                self.status.setText("Comparison sequence exported with catalog, passband, sky coordinates and selection.")
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, "Could not export comparison stars", str(exc))

    def _use_fwhm(self):
        if self._median_fwhm is not None:
            self.star_radius.setValue(math.ceil(4 * self._median_fwhm))

    def _preview_filter(self):
        self._measure()
        if not self.rows or self._radial_filter is None:
            return
        current = next(row for row in self.rows if row["selected_aperture"])
        x, y = self._center
        radius = current["radius_px"]
        height, width = self.pair.comet.shape
        x0, x1 = max(0, math.floor(x-radius-1)), min(width, math.ceil(x+radius+2))
        y0, y1 = max(0, math.floor(y-radius-1)), min(height, math.ceil(y+radius+2))
        step = max(1, math.ceil(max(x1-x0, y1-y0) / 640))
        raw = self.pair.comet[y0:y1:step, x0:x1:step]
        corrected = raw.copy()
        model = self._radial_filter
        iy, ix = model.y-y0, model.x-x0
        inside = ((iy >= 0) & (iy < y1-y0) & (ix >= 0) & (ix < x1-x0)
                  & (iy % step == 0) & (ix % step == 0))
        corrected[iy[inside] // step, ix[inside] // step] = model.replacement[inside]
        finite = raw[np.isfinite(raw)]
        if not finite.size:
            self.status.setText("The selected aperture contains no finite image samples.")
            return
        low, high = np.percentile(finite, [2, 99.5])
        dialog = QDialog(self)
        dialog.setWindowTitle("Compare Filtered Aperture")
        dialog.resize(720, 400)
        layout = QVBoxLayout(dialog)
        figure = Figure(tight_layout=True)
        canvas = FigureCanvasQTAgg(figure)
        layout.addWidget(canvas)
        from matplotlib.patches import Circle
        for ax, cutout, title in zip(figure.subplots(1, 2), (raw, corrected), ("Original stack", "Radial star filter")):
            ax.imshow(cutout, cmap="gray", vmin=low, vmax=high, origin="lower", interpolation="nearest")
            ax.add_patch(Circle(((x-x0)/step, (y-y0)/step), radius/step,
                                fill=False, edgecolor=self._accent, linewidth=1))
            ax.set_title(title)
            ax.set_axis_off()
        layout.addWidget(self._label(f"{current['replaced_pixel_count']} aperture pixels replaced using the ring {self.filter_statistic.currentData()}. Both views use the same stretch."))
        dialog.exec()

    def _measure(self):
        if self.pair is None:
            return
        self._debounce.stop()
        self.rows = []
        try:
            pair = self.pair
            geometry = aperture_scale(pair.header_text, *self._center)
            local_scale = geometry.equivalent_arcsec
            self.radius.setRange(*_radius_bounds(pair, local_scale, self.units.currentIndex()))
            radius = self.radius.value() / (local_scale if self.units.currentIndex() == 1 else 1)
            inner = 2 * radius if self.auto_sky.isChecked() else self.sky_inner.value()
            outer = 3 * radius if self.auto_sky.isChecked() else self.sky_outer.value()
            bg_index = self.background_mode.currentIndex()
            patch_mode = bg_index != 0
            if not patch_mode and not 0 < radius < inner < outer:
                raise ValueError("Place the sky annulus outside the coma: coma radius < sky inner < sky outer.")
            self.sky_inner.setEnabled(not self.auto_sky.isChecked() and not patch_mode)
            self.sky_outer.setEnabled(not self.auto_sky.isChecked() and not patch_mode)
            if self.auto_sky.isChecked() and not patch_mode:
                for control, value in ((self.sky_inner, inner), (self.sky_outer, outer)):
                    control.blockSignals(True)
                    control.setValue(value)
                    control.blockSignals(False)
            x, y = self._center
            sky_patch = None
            sky_rectangle = None
            sky_overlays = []
            overlays = [ImageOverlay("coma", "Coma", x, y, radius, inner, outer, self._accent, show_annulus=not patch_mode)]
            tail_length = tail_pa = None
            self.tail_result.clear()
            if self._tail_endpoint is not None:
                try:
                    tail_length, tail_pa = tail_geometry(pair.header_text, self._center, self._tail_endpoint)
                except ValueError as exc:
                    self.tail_result.setText(str(exc))
                else:
                    self.tail_result.setText(f"Tail {tail_length:.2f}′ · PA {tail_pa:.1f}° east of north")
                    overlays.append(ImageOverlay("tail", "Tail", x, y, 0, 0, 0, self._accent,
                        marker_style="ruler", show_annulus=False,
                        endpoint_x=self._tail_endpoint[0], endpoint_y=self._tail_endpoint[1]))
            if bg_index == 2:
                if self._sky_rectangle is None:
                    self.comet_view.set_overlays(overlays, selection_overlays=[])
                    raise ValueError("Choose Pick Sky Rectangle on Image, then click two opposite corners.")
                sky_rectangle = self._sky_rectangle
                sky_overlays = [SelectionOverlay("rectangle", *sky_rectangle, self._accent, fill=False, label="Sky")]
                if rectangle_separation(x, y, sky_rectangle) <= radius:
                    self.comet_view.set_overlays(overlays, selection_overlays=sky_overlays)
                    raise ValueError("Move the sky rectangle clear of the coma aperture.")
            elif bg_index == 1:
                if self._sky_center is None:
                    self.comet_view.set_overlays(overlays, selection_overlays=[])
                    raise ValueError("Choose Pick Sky Patch on Image, then click a clear background region.")
                sky_patch = (*self._sky_center, self.sky_patch_radius.value())
                overlays.append(ImageOverlay("sky", "Sky", *self._sky_center, self.sky_patch_radius.value(),
                                             0, 0, self._accent, show_annulus=False))
                if math.hypot(sky_patch[0] - x, sky_patch[1] - y) <= radius + sky_patch[2]:
                    self.comet_view.set_overlays(overlays, selection_overlays=[])
                    raise ValueError("Move the sky patch clear of the coma aperture.")
            self.comet_view.set_overlays(overlays, selection_overlays=sky_overlays)
            if not self._picking_center and not self._picking_sky and not self._picking_tail:
                self.comet_guidance.setText("The inner circle is the coma aperture; " +
                    ("the rectangle marks the sky background. " if bg_index == 2 else
                     "the separate circle marks the sky patch. " if patch_mode else "the two outer circles mark the sky annulus. ") +
                    "Use Set Coma Center, then click the coma in this image. Drag to pan; scroll to zoom.")
            self.radius_hint.setText(f"{radius:.1f} px · {radius * local_scale:.1f}″ area-equivalent radius\n" +
                                    (self.background_mode.currentText() if patch_mode else f"Sky: {inner:.1f}–{outer:.1f} px"))
            star_measurements = {}
            selected_stars = []
            star_issues = {}
            sr = self.star_radius.value()
            if self.comparisons:
                track = comparison_path_pixels(pair) + np.array([x-pair.center_x, y-pair.center_y])
            for star in self.comparisons:
                if np.min(np.hypot(track[:, 0]-star.x, track[:, 1]-star.y)) <= radius + 2.5*sr:
                    star_issues[star.source_id] = "Comparison aperture or sky overlaps the measured coma path"
                    continue
                selected = star.selected and (not self.solar_colors.isChecked() or
                           (star.color_bv is not None and 0.5 <= star.color_bv <= 0.8))
                selected_stars.append(replace(star, selected=selected))
                try:
                    star_measurements[star.source_id] = measure_comet_aperture(pair.stars, x=star.x, y=star.y,
                        radius=sr, sky_inner=1.5 * sr, sky_outer=2.5 * sr,
                        valid_mask=pair.star_coverage, required_coverage=pair.frame_count)
                except ValueError as exc:
                    star_issues[star.source_id] = str(exc)
            extinction_model = self.extinction.currentText()
            k = target_extinction = None
            if extinction_model != "None":
                if not self.band or not any(s.selected for s in selected_stars):
                    raise ValueError("Differential extinction needs selected comparison stars in a prepared calibration band.")
                key = (id(pair), extinction_model, self.extinction_k.value(),
                       tuple((s.source_id, s.x, s.y) for s in selected_stars))
                if self._extinction_cache is None or self._extinction_cache[0] != key:
                    self._extinction_cache = (key, comparison_extinction(pair, selected_stars, extinction_model,
                                                                         self.extinction_k.value()))
                k, target_extinction, differences = self._extinction_cache[1]
                for s in selected_stars:
                    if s.source_id not in differences:
                        star_issues[s.source_id] = "Below horizon at a selected midpoint"
                selected_stars = [replace(s, magnitude=s.magnitude + differences[s.source_id])
                                  for s in selected_stars if s.source_id in differences]
            self.zero_point = fit_comet_zero_point(selected_stars, star_measurements) if self.band else None
            if extinction_model != "None" and self.zero_point is None:
                raise ValueError("Differential extinction requires at least three usable comparison stars.")
            self._fill_comparisons(star_measurements, star_issues)
            radii = sorted(set([radius, *np.linspace(max(1, radius / 4), radius * 1.5, 10)]))
            # All growth-curve points share exactly the same background region.
            radii = [r for r in radii if patch_mode or r < inner]
            if sky_patch:
                radii = [r for r in radii if r + sky_patch[2] < math.hypot(sky_patch[0] - x, sky_patch[1] - y)]
            if sky_rectangle:
                separation = rectangle_separation(x, y, sky_rectangle)
                radii = [r for r in radii if r < separation]
            if self.star_filter.isChecked():
                statistic = self.filter_statistic.currentData()
                key = (id(pair), x, y, max(radii), self.filter_ignore.value(), self.filter_coefficient.value(), statistic)
                if key != self._radial_filter_key:
                    self._radial_filter = build_radial_star_filter(pair.comet, pair.comet_coverage, pair.frame_count,
                        self._center, max(radii), self.filter_ignore.value(), self.filter_coefficient.value(), statistic)
                    self._radial_filter_key = key
            else:
                self._radial_filter_key = None
                self._radial_filter = None
            for r in radii:
                result = measure_comet_aperture(pair.comet, x=x, y=y, radius=r,
                    sky_inner=inner, sky_outer=outer, valid_mask=pair.comet_coverage, required_coverage=pair.frame_count, sky_patch=sky_patch,
                    sky_rectangle=sky_rectangle, radial_filter=self._radial_filter)
                zp = self.zero_point
                mag = None if result.instrumental_magnitude is None else result.instrumental_magnitude + (zp.value if zp else 0)
                error = None if result.magnitude_error is None else math.hypot(result.magnitude_error, zp.error if zp else 0)
                self.rows.append({"target": self.target_name, "radius_px": float(r), "radius_arcsec": float(r * local_scale),
                    "area_equivalent_scale_arcsec_per_px": local_scale,
                    "pixel_scale_x_arcsec": geometry.x_arcsec, "pixel_scale_y_arcsec": geometry.y_arcsec,
                    "pixel_axis_angle_deg": geometry.axis_angle_deg,
                    "aperture_semimajor_arcsec": float(r * geometry.major_arcsec),
                    "aperture_semiminor_arcsec": float(r * geometry.minor_arcsec),
                    "selected_aperture": math.isclose(r, radius), "comparison_aperture_radius_px": sr,
                    "comparison_median_fwhm_px": self._median_fwhm,
                    "center_x": x, "center_y": y, "sky_inner_px": inner, "sky_outer_px": outer,
                    "background_method": self.background_mode.currentText() if patch_mode else "Annulus",
                    "sky_rectangle_x_min": sky_rectangle[0] if sky_rectangle else None,
                    "sky_rectangle_y_min": sky_rectangle[1] if sky_rectangle else None,
                    "sky_rectangle_x_max": sky_rectangle[2] if sky_rectangle else None,
                    "sky_rectangle_y_max": sky_rectangle[3] if sky_rectangle else None,
                    "sky_patch_x": self._sky_center[0] if sky_patch else None,
                    "sky_patch_y": self._sky_center[1] if sky_patch else None,
                    "sky_patch_radius": self.sky_patch_radius.value() if sky_patch else None,
                    "flux_counts_per_second": result.flux, "flux_error_background_only": result.flux_error,
                    "flux_units": pair.provenance.get("units", "native input units per second"),
                    "radial_filter": self.star_filter.isChecked(),
                    "radial_filter_method": "2 px rings, positive native ring level, one-pass replacement" if self._radial_filter is not None else None,
                    "radial_filter_statistic": self.filter_statistic.currentData() if self._radial_filter is not None else None,
                    "radial_filter_ignore_px": self.filter_ignore.value() if self._radial_filter is not None else None,
                    "radial_filter_coefficient": self.filter_coefficient.value() if self._radial_filter is not None else None,
                    "replaced_pixel_count": result.replaced_pixels,
                    "unfiltered_flux_counts_per_second": result.unfiltered_flux,
                    "tail_end_x": self._tail_endpoint[0] if self._tail_endpoint else None,
                    "tail_end_y": self._tail_endpoint[1] if self._tail_endpoint else None,
                    "tail_length_arcmin": tail_length, "tail_pa_deg": tail_pa,
                    "tail_selection_contrast": self._tail_contrast,
                    "magnitude": mag, "magnitude_error_estimate": error,
                    "comparison_catalog": pair.provenance.get("comparison_catalog", "APASS"),
                    "magnitude_system": magnitude_system(pair.provenance.get("filter", ""), self.band if zp else None,
                                                         pair.provenance.get("comparison_catalog", "APASS")),
                    "background_counts_per_second": result.background, "coverage": result.coverage,
                    "sky_rms_native_units_per_second": result.background_rms,
                    "sky_pixels_retained": result.sky_pixels, "sky_pixels_clipped": result.sky_pixels_clipped,
                    "comparison_count": zp.count if zp else 0, "zero_point": zp.value if zp else None,
                    "zero_point_error": zp.error if zp else None,
                    "zero_point_scatter": zp.scatter if zp else None, "frame_count": pair.frame_count,
                    "filter": pair.provenance.get("filter", ""), "tracking": pair.provenance.get("tracking", ""),
                    "start_midpoint": min(f["midpoint"] for f in pair.provenance["frames"]),
                    "end_midpoint": max(f["midpoint"] for f in pair.provenance["frames"]),
                    "extinction_model": extinction_model, "extinction_coefficient_mag_airmass": k,
                    "target_stack_extinction_mag": target_extinction,
                    "quality": "; ".join(result.flags),
                    "uncertainty_note": "Background + calibration scatter; excludes detector noise, resampling covariance, star-filter bias and transformation systematics"})
            for row in self.rows:
                row.update(aperture_dust_proxy(row, pair.provenance.get("geometry", {})))
            current = min(self.rows, key=lambda row: abs(row["radius_px"] - radius))
            mag = current["magnitude"]
            if mag is None:
                self.result_label.setText("Adjust the aperture or center")
            else:
                self.result_label.setText(f"{mag:.3f} mag  ·  {current['magnitude_system']}")
            error_text = "—" if current["magnitude_error_estimate"] is None else f"{current['magnitude_error_estimate']:.3f} mag"
            self.result_detail.setText(f"Fixed coma aperture · estimated error {error_text} · {current['comparison_count']} comparison stars\n"
                                      "Estimated uncertainty includes background and calibration only.")
            self.result_detail.setToolTip(current["uncertainty_note"])
            if self._radial_filter is not None:
                self.result_detail.setText(self.result_detail.text() + f"\nStar filter replaced {current['replaced_pixel_count']} aperture pixels. Check for tail or jet suppression in Compare Filtered Aperture.")
            self.status.setText(current["quality"] or ("Catalog zero point applied. Inspect the growth curve before exporting."
                                if self.zero_point else "Instrumental result. At least three usable, selected comparison stars are needed for calibration."))
            self._fill_results()
        except Exception as exc:
            self.rows = []
            self.result_label.setText("Measurement needs attention")
            self.result_detail.setText(str(exc))
            self.status.setText(str(exc))
            self.measurements_table.setRowCount(0)
            self.figure.clear()
            self.canvas.draw_idle()
        self._sync_ready()

    def _fill_comparisons(self, measurements, issues):
        self._restoring = True
        self.star_table.setRowCount(len(self.comparisons))
        selected_widths = []
        sky_widths = []
        for row, star in enumerate(self.comparisons):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Checked if star.selected else Qt.CheckState.Unchecked)
            self.star_table.setItem(row, 0, check)
            m = measurements.get(star.source_id)
            residual = self.zero_point.residuals.get(star.source_id) if self.zero_point else None
            issue = issues.get(star.source_id) or ("; ".join(m.flags) if m else "Not measured")
            if self.solar_colors.isChecked() and not (star.color_bv is not None and 0.5 <= star.color_bv <= 0.8):
                issue = "Outside color selection"
            if self.zero_point and star.source_id in self.zero_point.rejected:
                issue = "Calibration outlier"
            values = [star.name, f"{star.magnitude:.3f}", "—" if star.color_bv is None else f"{star.color_bv:.2f}",
                      "—" if residual is None else f"{residual:+.3f}", issue or "Usable"]
            if self.workflow == "comet":
                position = (star.x, star.y)
                if position not in self._star_widths:
                    width = comparison_star_fwhm(
                        self.pair.stars, self.pair.star_coverage, self.pair.frame_count, *position)
                    angular_width = None if width is None else width * aperture_scale(
                        self.pair.header_text, *position).equivalent_arcsec
                    self._star_widths[position] = (width, angular_width)
                width, angular_width = self._star_widths[position]
                values.append("—" if width is None else f"{width:.2f}")
                if width is not None and star.selected and not issue:
                    selected_widths.append(width)
                    sky_widths.append(angular_width)
            for col, value in enumerate(values, 1):
                self.star_table.setItem(row, col, QTableWidgetItem(value))
        self._restoring = False
        if self.workflow == "comet":
            self._median_fwhm = float(np.median(selected_widths)) if len(selected_widths) >= 3 else None
            if self._median_fwhm is not None:
                self.fwhm_hint.setText(f"{len(selected_widths)} selected stars · median FWHM {self._median_fwhm:.2f} px "
                    f"({np.median(sky_widths):.2f}″). Suggested star aperture radius {math.ceil(4*self._median_fwhm)} px.")
            else:
                self.fwhm_hint.setText(f"{len(selected_widths)} usable stellar widths. Select at least three isolated stars for a median FWHM.")
        self.star_view.set_overlays([ImageOverlay(s.source_id, "", s.x, s.y, self.star_radius.value(), 0, 0,
                                                self._accent, show_annulus=False, show_label=False)
                                     for s in self.comparisons if s.selected])

    def _fill_results(self):
        self.measurements_table.setRowCount(len(self.rows))
        for index, row in enumerate(self.rows):
            keys = ("radius_px", "radius_arcsec", "flux_counts_per_second", "magnitude", "magnitude_error_estimate", "quality")
            for col, key in enumerate(keys):
                value = row[key]
                text = "—" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value or "Measured")
                self.measurements_table.setItem(index, col, QTableWidgetItem(text))
        self.figure.clear()
        axis = self.figure.add_subplot(121 if self.zero_point else 111)
        axis.set_facecolor(self._bg)
        valid = [r for r in self.rows if r["magnitude"] is not None]
        if valid:
            axis.errorbar([r["radius_arcsec"] for r in valid], [r["magnitude"] for r in valid],
                          yerr=[r["magnitude_error_estimate"] or 0 for r in valid], color=self._accent,
                          marker="o", markersize=4, capsize=3)
            axis.invert_yaxis()
        axis.set_xlabel("Coma aperture radius (arcseconds)", color=self._text)
        axis.set_ylabel("Magnitude" if self.zero_point else "Instrumental magnitude", color=self._text)
        axis.set_title("Growth curve · shared sky region", color=self._text, pad=16)
        axis.tick_params(colors=self._text)
        for spine in axis.spines.values():
            spine.set_color(self._text)
            spine.set_alpha(0.25)
        axis.grid(alpha=0.15)
        if self.zero_point:
            calibration = self.figure.add_subplot(122)
            calibration.set_facecolor(self._bg)
            for star in self.comparisons:
                residual = self.zero_point.residuals.get(star.source_id)
                if residual is not None:
                    calibration.plot(star.magnitude, residual,
                                     "x" if star.source_id in self.zero_point.rejected else "o", color=self._accent)
            calibration.axhline(0, color=self._text, alpha=0.3)
            calibration.set_xlabel("Catalog magnitude", color=self._text)
            calibration.set_ylabel("Calibration residual (mag)", color=self._text)
            calibration.set_title("Comparison-star agreement", color=self._text, pad=16)
            calibration.tick_params(colors=self._text)
            for spine in calibration.spines.values():
                spine.set_color(self._text)
                spine.set_alpha(0.25)
            calibration.grid(alpha=0.15)
        self.canvas.draw_idle()

    def _session_settings(self):
        return {"workflow": self.workflow, "target": self.target_name, "band": self.band, "center": list(self._center),
                **({"extinction": self.extinction.currentText(), "extinction_k": self.extinction_k.value(),
                    "tail_endpoint": self._tail_endpoint, "tail_contrast": self._tail_contrast,
                    "display_contrast": self.comet_contrast.currentText(), "radial_filter": self.star_filter.isChecked(),
                    "filter_ignore": self.filter_ignore.value(), "filter_coefficient": self.filter_coefficient.value(),
                    "filter_statistic": self.filter_statistic.currentData()}
                    if self.workflow == "comet" else {}),
                "radius": self.radius.value(), "units": self.units.currentIndex(),
                "auto_sky": self.auto_sky.isChecked(), "sky_inner": self.sky_inner.value(),
                "sky_outer": self.sky_outer.value(), "star_radius": self.star_radius.value(),
                "background_mode": self.background_mode.currentIndex(), "sky_center": self._sky_center,
                **({"sky_rectangle": self._sky_rectangle} if self.workflow == "comet" else {}),
                "sky_patch_radius": self.sky_patch_radius.value(),
                "solar_colors": self.solar_colors.isChecked()}

    def _save_session(self):
        self._measure()
        path, _ = QFileDialog.getSaveFileName(self, "Save Photometry Session", str(self.reference_path.parent / "photometry.castphotometry"), "CAst photometry session (*.castphotometry *.castcomet)")
        if path:
            try:
                save_comet_session(Path(path), self.pair, self._session_settings(), self.comparisons)
                self.status.setText("Session saved with both linear stacks, comparison stars, and measurement settings.")
            except Exception as exc:
                QMessageBox.warning(self, "Could not save session", str(exc))

    def _open_session(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Photometry Session", str(self.reference_path.parent), "CAst photometry session (*.castphotometry *.castcomet)")
        if not path:
            return
        try:
            pair, settings, stars, changes = load_comet_session(Path(path))
            if settings.get("workflow", "comet") != self.workflow:
                raise ValueError(f"This is a {settings.get('workflow', 'comet')} session. Open Photometry with that object type selected.")
            required = {"target", "band", "center", "radius", "units", "auto_sky", "sky_inner", "sky_outer", "star_radius", "solar_colors"}
            if not required <= settings.keys() or len(settings["center"]) != 2:
                raise ValueError("The session is missing measurement settings.")
            if not isinstance(settings["target"], str) or not all(
                    isinstance(settings[key], bool) for key in ("auto_sky", "solar_colors")):
                raise ValueError("The saved target or measurement options are invalid.")
            for key in ("sky_inner", "sky_outer", "star_radius"):
                control = getattr(self, key)
                if not math.isfinite(settings[key]) or not control.minimum() <= settings[key] <= control.maximum():
                    raise ValueError(f"Invalid session setting: {key}.")
            if (not np.isfinite(settings["center"]).all() or settings["units"] not in (0, 1)
                    or not (0 <= settings["center"][0] < pair.comet.shape[1]
                            and 0 <= settings["center"][1] < pair.comet.shape[0])):
                raise ValueError("Invalid session center or aperture units.")
            scale = pair.pixel_scale
            if self.workflow == "comet":
                scale = aperture_scale(pair.header_text, *settings["center"]).equivalent_arcsec
            if self.workflow == "comet" and "aperture_scale_model" not in pair.provenance:
                if not 1 <= settings["radius"] <= 2000:
                    raise ValueError("Invalid legacy session aperture radius.")
                if settings["units"]:
                    # Preserve the detector aperture while relabeling its angular radius.
                    settings["radius"] = settings["radius"] / pair.pixel_scale * scale
                pair.provenance["legacy_pixel_scale"] = pair.pixel_scale
                pair.provenance["aperture_scale_model"] = "Local area-equivalent celestial WCS"
                pair.pixel_scale = aperture_scale(pair.header_text, pair.center_x, pair.center_y).equivalent_arcsec
            lower, upper = _radius_bounds(pair, scale, settings["units"])
            if not math.isfinite(settings["radius"]) or not lower - 1e-9 <= settings["radius"] <= upper + 1e-9:
                raise ValueError("Invalid session setting: radius.")
            clear_v = self.workflow == "comet" and pair.provenance.get("clear_v_reference") is True
            if settings["band"] != preparation_band(pair.provenance.get("filter", ""), clear_v):
                raise ValueError("The session calibration band does not match its images.")
            if settings.get("background_mode", 0) not in range(self.background_mode.count()):
                raise ValueError("Invalid session background method.")
            sky_center = settings.get("sky_center")
            if sky_center is not None and (len(sky_center) != 2 or not np.isfinite(sky_center).all()):
                raise ValueError("Invalid session sky position.")
            patch_radius = settings.get("sky_patch_radius", 12)
            if not math.isfinite(patch_radius) or not self.sky_patch_radius.minimum() <= patch_radius <= self.sky_patch_radius.maximum():
                raise ValueError("Invalid session sky radius.")
            if self.workflow == "comet":
                sky_rect = settings.get("sky_rectangle")
                if sky_rect is not None:
                    if len(sky_rect) != 4:
                        raise ValueError("Invalid saved sky rectangle.")
                    rectangle_separation(*settings["center"], sky_rect)
                tip = settings.get("tail_endpoint")
                if tip is not None and (len(tip) != 2 or not np.isfinite(tip).all() or
                        not (0 <= tip[0] < pair.comet.shape[1] and 0 <= tip[1] < pair.comet.shape[0])):
                    raise ValueError("Invalid saved tail endpoint.")
                for key, control, default in (("filter_ignore", self.filter_ignore, 7),
                                              ("filter_coefficient", self.filter_coefficient, 3)):
                    if not control.minimum() <= settings.get(key, default) <= control.maximum():
                        raise ValueError("Invalid saved radial filter setting.")
                if not isinstance(settings.get("radial_filter", False), bool):
                    raise ValueError("Invalid saved radial filter selection.")
                if settings.get("filter_statistic", "mean") not in ("mean", "median"):
                    raise ValueError("Invalid saved ring statistic.")
                if (settings.get("display_contrast", "Balanced") not in CONTRAST_PRESETS or
                        settings.get("tail_contrast") not in (None, *CONTRAST_PRESETS)):
                    raise ValueError("Invalid saved comet display contrast.")
            if self.workflow == "comet" and (settings.get("extinction", "None") not in MODELS
                    or not 0 <= settings.get("extinction_k", 0) <= 3):
                raise ValueError("Invalid session extinction settings.")
            self._debounce.stop()
            self._cancel_image_pick()
            self._restoring = True
            self.pair, self.comparisons = pair, stars
            self.target_name = settings["target"]
            self.title_label.setText(self.target_name)
            self.band = settings["band"]
            if self.workflow == "comet":
                self._sky_rectangle = tuple(sky_rect) if sky_rect is not None else None
                self._star_widths.clear()
                self.clear_reference.setChecked(clear_v)
            self._center = tuple(settings["center"])
            self._sky_center = settings.get("sky_center")
            if self.workflow == "comet":
                self._tail_endpoint = tuple(tip) if tip is not None else None
                self._tail_contrast = settings.get("tail_contrast") if tip is not None else None
                self.comet_contrast.setCurrentText(settings.get("display_contrast", "Balanced"))
                self._radial_filter_key = None
                self.filter_ignore.setValue(settings.get("filter_ignore", 7))
                self.filter_coefficient.setValue(settings.get("filter_coefficient", 3))
                self.filter_statistic.setCurrentIndex(self.filter_statistic.findData(settings.get("filter_statistic", "mean")))
                self.star_filter.setChecked(settings.get("radial_filter", False))
            self.background_mode.setCurrentIndex(settings.get("background_mode", 0))
            self.sky_patch_radius.setValue(settings.get("sky_patch_radius", 12))
            self.units.setCurrentIndex(settings["units"])
            self.radius.setRange(lower, upper)
            for key in ("radius", "sky_inner", "sky_outer", "star_radius"):
                getattr(self, key).setValue(settings[key])
            self.auto_sky.setChecked(settings["auto_sky"])
            self.solar_colors.setChecked(settings["solar_colors"])
            if self.workflow == "comet":
                self.extinction.setCurrentText(settings.get("extinction", "None"))
                self.extinction_k.setValue(settings.get("extinction_k", 0))
            self._session_path = Path(path)
            self._review_note = "Restored comparison stars and linear stacks. " + "; ".join(changes)
            self._restoring = False
            self._show_pair()
            self._measure()
            self._show_comet()
            # The original launch selection remains available, explicitly labeled.
            self.prepare_button.setText("Prepare Original Selection")
            self.prepare_button.setToolTip(f"Build new stacks for {self.detection.name} from the Frames tab; replaces the opened session in this window.")
            self.subtitle.setText("Saved session · measurements use the embedded stacks. Original selection remains in Frames.")
            if changes:
                self.status.setText("Using saved stacks. " + "; ".join(changes))
        except Exception as exc:
            self._restoring = False
            QMessageBox.warning(self, "Could not open session", str(exc))

    def _open_report(self):
        self._measure()
        row = next((row for row in self.rows if row["selected_aperture"]), None)
        if row is None:
            return
        dialog = CometReportDialog(self, row)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.pair.provenance["geometry"] = dialog.distances()
            self.pair.provenance["report_inputs"] = dialog.inputs()
            self._measure()

    def _export(self):
        self._measure()
        if not self.rows:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Comet Measurements", str(self.reference_path.parent / "comet_photometry.csv"), "CSV (*.csv)")
        if path:
            try:
                export_comet_measurements(Path(path), self.rows)
                self.status.setText("Measurements exported with aperture, calibration, timing, and uncertainty details.")
            except Exception as exc:
                QMessageBox.warning(self, "Could not export measurements", str(exc))
