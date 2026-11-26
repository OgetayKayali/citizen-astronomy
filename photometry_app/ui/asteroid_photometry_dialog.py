"""Asteroid exposure photometry in the shared guided Photometry workspace."""
from dataclasses import replace
import hashlib
import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QLabel, QFileDialog, QMessageBox, QTableWidgetItem, QHeaderView

from photometry_app.core.asteroid_photometry import measure_asteroid_sequence
from photometry_app.core.comet_photometry import export_comet_measurements, measure_comet_aperture, fit_comet_zero_point
from photometry_app.ui.comet_photometry_dialog import CometPhotometryDialog, CometPreparationWorker
from photometry_app.ui.image_view import ImageOverlay


class AsteroidPreparationWorker(CometPreparationWorker):
    def __init__(self, **kwargs):
        # V can identify reference stars for differential clear/L photometry;
        # the dialog only applies catalog calibration in the actual image band.
        kwargs["band"] = kwargs["band"] or "V"
        super().__init__(**kwargs)


class AsteroidMeasurementWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, pair, comparisons, options, parent=None):
        super().__init__(parent)
        self.pair, self.comparisons, self.options = pair, comparisons, options

    def run(self):
        try:
            self.completed.emit(measure_asteroid_sequence(self.pair, self.comparisons,
                **self.options, progress=self.progress.emit, cancel=self.isInterruptionRequested))
        except Exception as exc:
            self.failed.emit(str(exc))


class AsteroidPhotometryDialog(CometPhotometryDialog):
    workflow = "asteroid"
    preparation_worker_type = AsteroidPreparationWorker

    def _build_ui(self):
        super()._build_ui()
        self.tabs.setTabText(1, "Asteroid")
        self.tabs.setTabText(3, "Light Curve")
        self.subtitle.setText("Photometry · Measure each exposure and review the asteroid light curve.")
        self.background_mode.setCurrentIndex(0)
        self.background_mode.hide()
        self.advanced.layout().labelForField(self.background_mode).hide()
        self.sky_patch_radius.hide()
        self.advanced.layout().labelForField(self.sky_patch_radius).hide()
        for label in self.measurement_card.findChildren(QLabel):
            if label.text() == "Coma aperture":
                label.setText("Target aperture")
        self.aperture_controls.layout().itemAt(2).layout().labelForField(self.radius).setText("Target radius")
        self.show_comet.setText("Show Asteroid")
        self.radius.setValue(8)
        self.measurements_table.setHorizontalHeaderLabels(["Midpoint (UTC)", "Exposure (s)", "Flux (counts/s)", "Magnitude", "Est. error", "Quality"])
        self.measurements_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.measurements_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        explanations = [
            ("Prepare the asteroid preview", "Prepare Photometry builds a tracked preview for choosing the target aperture. Each original exposure is measured separately for the light curve."),
            ("Prepare the comparison-star image", "Prepare Photometry finds reference stars. Review their selection before measuring the individual exposures."),
            ("Your asteroid light curve will appear here", "Prepare Photometry, then adjust the target aperture. Each point represents one exposure at its UTC midpoint."),
            ("Your exposure measurements will appear here", "Prepare Photometry to measure each exposure separately. Failed measurements retain their quality flags.")]
        for (page, _), (heading, description) in zip(self._result_pages, explanations):
            labels = page.widget(0).findChildren(QLabel)
            labels[0].setText(heading)
            labels[1].setText(description)

    def _sync_ready(self, *_):
        super()._sync_ready()
        if self._debounce.isActive():
            self.export_button.setEnabled(False)
            self.save_button.setEnabled(False)
        self.aperture_guidance.setText("In the Asteroid tab, use Set Target Center and click the asteroid. Target radius sets the aperture used on every original exposure. Include the full trail if visible."
            if self.pair else "Prepare Photometry first. The tracked preview and target-aperture controls will appear when preparation finishes.")
        self.pick_center.setText("Cancel Center Selection" if self._picking_center else "Set Target Center")
        if not self.pair:
            self.result_label.setText("Start with your calibrated images")
            self.result_detail.setText("Prepare a tracked preview, then measure each exposure to build the asteroid light curve.")

    def _prepared(self, pair, stars, note):
        super()._prepared(pair, stars, note)
        self.subtitle.setText("Photometry · Measure each exposure and review the asteroid light curve.")

    def _pick_center(self):
        super()._pick_center()
        self.pick_center.setText("Cancel Center Selection" if self._picking_center else "Set Target Center")
        if self._picking_center:
            self.status.setText("Click the asteroid center in the image on the right. The offset will follow its predicted path in every exposure.")

    def _session_settings(self):
        return {**super()._session_settings(), "workflow": "asteroid"}

    def _measure(self):
        if self.pair is None:
            return
        self._debounce.stop()
        if self._worker is not None:
            self._measurement_pending = True
            return
        settings = self._session_settings()
        signature = hashlib.sha256(json.dumps([settings, [(s.source_id, s.selected) for s in self.comparisons]], sort_keys=True).encode()).hexdigest()
        saved = self.pair.provenance.get("asteroid_measurements", {})
        if saved.get("signature") == signature:
            self.rows = saved["rows"]
            self._render_series()
            self._sync_ready()
            return
        self.rows = []
        self.measurements_table.setRowCount(0)
        self.figure.clear()
        self.canvas.draw_idle()
        radius = self.radius.value() / (self.pair.pixel_scale if self.units.currentIndex() else 1)
        inner = radius * 2 if self.auto_sky.isChecked() else self.sky_inner.value()
        outer = radius * 3 if self.auto_sky.isChecked() else self.sky_outer.value()
        self.radius_hint.setText(f"{radius:.1f} px · {radius * self.pair.pixel_scale:.1f}″ radius\nSky: {inner:.1f}–{outer:.1f} px")
        self.comet_guidance.setText("The inner circle is the target aperture; outer circles mark the sky annulus. Set Target Center adjusts the path offset. Each exposure is measured separately.")
        self.comet_view.set_overlays([ImageOverlay("target", "Asteroid", *self._center, radius, inner, outer, self._accent)])
        if not 0 < radius < inner < outer:
            self.result_label.setText("Adjust the sky annulus")
            self.status.setText("Target radius must be smaller than sky inner radius, which must be smaller than sky outer radius.")
            self._sync_ready()
            return
        stars = [replace(s, selected=s.selected and (not self.solar_colors.isChecked() or
                 s.color_bv is not None and 0.5 <= s.color_bv <= 0.8)) for s in self.comparisons]
        preview, issues = {}, {}
        sr = self.star_radius.value()
        for star in stars:
            try:
                preview[star.source_id] = measure_comet_aperture(self.pair.stars, x=star.x, y=star.y,
                    radius=sr, sky_inner=1.5*sr, sky_outer=2.5*sr,
                    valid_mask=self.pair.star_coverage, required_coverage=self.pair.frame_count)
            except ValueError as exc:
                issues[star.source_id] = str(exc)
        self.zero_point = fit_comet_zero_point(stars, preview) if self.band else None
        self._fill_comparisons(preview, issues)
        self.comparison_hint.setText("Reference-star preview and residuals are from the stack. Each exposure is calibrated separately; inspect its comparison count and quality flags in Measurements.")
        self._measurement_signature = signature
        self._measurement_pending = False
        self._worker = AsteroidMeasurementWorker(self.pair, stars, dict(radius=radius, sky_inner=inner,
            sky_outer=outer, star_radius=self.star_radius.value(), center=self._center,
            band=self.band, target=self.target_name), self)
        self._worker.progress.connect(self.status.setText)
        self._worker.completed.connect(self._measured)
        self._worker.failed.connect(self._failed)
        self._worker.finished.connect(self._worker_finished)
        self.result_label.setText("Measuring individual exposures…")
        self._sync_ready()
        self._worker.start()

    def _worker_finished(self):
        super()._worker_finished()
        if getattr(self, "_measurement_pending", False) and not self._close_when_finished:
            self._measurement_pending = False
            self._measure()

    def _measured(self, rows):
        self.rows = rows
        self.pair.provenance["asteroid_measurements"] = {"signature": self._measurement_signature, "rows": rows}
        self._render_series()

    def _failed(self, message):
        self._measurement_pending = False
        super()._failed(message)
        self.result_label.setText("Photometry needs attention")
        self.result_detail.setText(message)

    def _open_session(self):
        super()._open_session()
        if self._session_path is not None:
            self.subtitle.setText("Saved asteroid session · exposure results are embedded. Changing measurement settings requires the original images.")

    def _render_series(self):
        self.measurements_table.setRowCount(len(self.rows))
        keys = ("midpoint_utc", "exposure_seconds", "flux_counts_per_second", "magnitude", "magnitude_error_estimate", "quality")
        for i, row in enumerate(self.rows):
            for col, key in enumerate(keys):
                value = row.get(key)
                self.measurements_table.setItem(i, col, QTableWidgetItem("—" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)))
        valid = [r for r in self.rows if r["magnitude"] is not None]
        self.figure.clear()
        axis = self.figure.add_subplot(111)
        axis.set_facecolor(self._bg)
        if valid:
            start = datetime.fromisoformat(valid[0]["midpoint_utc"])
            hours = [(datetime.fromisoformat(r["midpoint_utc"]) - start).total_seconds() / 3600 for r in valid]
            axis.errorbar(hours, [r["magnitude"] for r in valid], yerr=[r["magnitude_error_estimate"] or 0 for r in valid], fmt="o", color=self._accent, markersize=4)
            axis.invert_yaxis()
            axis.set_xlabel(f"Hours since {start.isoformat()} (UTC)", color=self._text)
        axis.set_ylabel(valid[0]["magnitude_system"] if valid else "Magnitude", color=self._text)
        axis.set_title("Asteroid light curve · one point per exposure", color=self._text)
        axis.tick_params(colors=self._text)
        for spine in axis.spines.values():
            spine.set_color(self._text)
            spine.set_alpha(.25)
        axis.grid(alpha=.2)
        self.canvas.draw_idle()
        self.result_label.setText(f"{len(valid)} of {len(self.rows)} exposures measured")
        self.result_detail.setText((valid[0]["magnitude_system"] if valid else "Inspect the quality flags") + " · estimated errors include background and calibration/ensemble only.")
        self.status.setText("Review the light curve and quality flags. Circular apertures must contain the full target trail. Period fitting is not included.")

    def _export(self):
        if not self.rows or self._worker is not None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Asteroid Measurements", str(self.reference_path.parent / "asteroid_photometry.csv"), "CSV (*.csv)")
        if path:
            try:
                export_comet_measurements(Path(path), self.rows)
                self.status.setText("Asteroid exposure measurements exported.")
            except Exception as exc:
                QMessageBox.warning(self, "Could not export measurements", str(exc))
