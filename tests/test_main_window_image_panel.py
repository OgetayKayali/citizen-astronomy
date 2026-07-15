from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np

from astropy.wcs import WCS

from PySide6.QtWidgets import QApplication

from photometry_app.core.models import CatalogStar, FieldCatalog, LightCurvePoint, LightCurveSeries, PhotometryMeasurement, ProcessingReport
from photometry_app.core.plotting import build_annotated_image_display_from_array
from photometry_app.ui.main_window import MainWindow


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class MainWindowImagePanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._config_dir = tempfile.TemporaryDirectory()
        self._state_dir = tempfile.TemporaryDirectory()
        self._previous_config_path = os.environ.get("CITIZEN_PHOTOMETRY_CONFIG_PATH")
        self._previous_state_path = os.environ.get("CITIZEN_PHOTOMETRY_STATE_PATH")
        config_path = Path(self._config_dir.name) / "settings.json"
        state_path = Path(self._state_dir.name) / "state.json"
        config_path.write_text("{}", encoding="utf-8")
        state_path.write_text("{}", encoding="utf-8")
        os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = str(config_path)
        os.environ["CITIZEN_PHOTOMETRY_STATE_PATH"] = str(state_path)
        self.window = MainWindow()

    def tearDown(self) -> None:
        self.window.close()
        if self._previous_config_path is None:
            os.environ.pop("CITIZEN_PHOTOMETRY_CONFIG_PATH", None)
        else:
            os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = self._previous_config_path
        if self._previous_state_path is None:
            os.environ.pop("CITIZEN_PHOTOMETRY_STATE_PATH", None)
        else:
            os.environ["CITIZEN_PHOTOMETRY_STATE_PATH"] = self._previous_state_path
        self._config_dir.cleanup()
        self._state_dir.cleanup()

    def test_processing_completed_selects_first_source_and_populates_image_panel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "frame_01.fits"
            image_path.write_text("placeholder", encoding="utf-8")

            entry = CatalogStar(
                catalog="vsx",
                source_id="vsx-1",
                name="Target",
                ra_deg=10.0,
                dec_deg=20.0,
                magnitude=12.1,
                is_variable=True,
            )
            measurement = PhotometryMeasurement(
                source_id="vsx-1",
                source_name="Target",
                catalog="vsx",
                object_name="Demo",
                file_path=image_path,
                observation_time=datetime(2026, 4, 14, 1, 2, 3),
                filter_name="V",
                ra_deg=10.0,
                dec_deg=20.0,
                x=50.0,
                y=60.0,
                flux=1000.0,
                flux_error=10.0,
                instrumental_magnitude=-2.5,
                differential_magnitude=12.0,
                differential_magnitude_error=0.05,
                is_variable=True,
                is_reference=False,
                flags=[],
            )
            series = LightCurveSeries(
                object_name="Demo",
                source_id="vsx-1",
                source_name="Target",
                filter_name="V",
                points=[
                    LightCurvePoint(
                        observation_time=measurement.observation_time,
                        file_path=image_path,
                        differential_magnitude=12.0,
                        instrumental_magnitude=-2.5,
                        flux=1000.0,
                        flux_error=10.0,
                        differential_magnitude_error=0.05,
                    )
                ],
            )
            report = ProcessingReport(
                object_name="Demo",
                files_processed=1,
                solved_files=1,
                field_catalog=FieldCatalog(
                    center_ra_deg=10.0,
                    center_dec_deg=20.0,
                    radius_deg=1.0,
                    variable_stars=[entry],
                ),
                measurements=[measurement],
                light_curves=[series],
                notes=[],
            )
            display = build_annotated_image_display_from_array(
                np.arange(25, dtype=float).reshape(5, 5),
                image_path=image_path,
            )

            with patch("photometry_app.ui.main_window.build_annotated_image_display", return_value=display):
                self.window._handle_processing_completed(report)

            self.assertEqual(self.window._source_table.currentRow(), 0)
            self.assertIsNotNone(self.window._image_view._display)
            assert self.window._image_view._display is not None
            self.assertEqual(self.window._image_view._display.image_path, image_path)

    def test_center_object_toggle_focuses_image_view_on_selected_source_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "frame_01.fits"
            image_path.write_text("placeholder", encoding="utf-8")

            entry = CatalogStar(
                catalog="vsx",
                source_id="vsx-1",
                name="Target",
                ra_deg=10.0,
                dec_deg=20.0,
                magnitude=12.1,
                is_variable=True,
            )
            measurement = PhotometryMeasurement(
                source_id="vsx-1",
                source_name="Target",
                catalog="vsx",
                object_name="Demo",
                file_path=image_path,
                observation_time=datetime(2026, 4, 14, 1, 2, 3),
                filter_name="V",
                ra_deg=10.0,
                dec_deg=20.0,
                x=50.0,
                y=60.0,
                flux=1000.0,
                flux_error=10.0,
                instrumental_magnitude=-2.5,
                differential_magnitude=12.0,
                differential_magnitude_error=0.05,
                is_variable=True,
                is_reference=False,
                flags=[],
            )
            series = LightCurveSeries(
                object_name="Demo",
                source_id="vsx-1",
                source_name="Target",
                filter_name="V",
                points=[
                    LightCurvePoint(
                        observation_time=measurement.observation_time,
                        file_path=image_path,
                        differential_magnitude=12.0,
                        instrumental_magnitude=-2.5,
                        flux=1000.0,
                        flux_error=10.0,
                        differential_magnitude_error=0.05,
                    )
                ],
            )
            report = ProcessingReport(
                object_name="Demo",
                files_processed=1,
                solved_files=1,
                field_catalog=FieldCatalog(
                    center_ra_deg=10.0,
                    center_dec_deg=20.0,
                    radius_deg=1.0,
                    variable_stars=[entry],
                ),
                measurements=[measurement],
                light_curves=[series],
                notes=[],
            )
            display = build_annotated_image_display_from_array(
                np.arange(25, dtype=float).reshape(5, 5),
                image_path=image_path,
            )
            wcs = WCS(naxis=2)
            wcs.wcs.crpix = [1.0, 1.0]
            wcs.wcs.cdelt = np.array([-1.0 / 3600.0, 1.0 / 3600.0])
            wcs.wcs.crval = [entry.ra_deg, entry.dec_deg]
            wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]

            with (
                patch("photometry_app.ui.main_window.build_annotated_image_display", return_value=display),
                patch("photometry_app.ui.main_window.read_header", return_value=wcs.to_header()),
            ):
                self.window._handle_processing_completed(report)
                self.window._image_view._zoom_scale = 1.0

                self.window._image_center_object_button.click()

            self.assertTrue(self.window._image_center_object_button.isCheckable())
            self.assertTrue(self.window._image_center_object_button.isChecked())
            self.assertIsNotNone(self.window._image_view._view_center)
            assert self.window._image_view._view_center is not None
            self.assertAlmostEqual(self.window._image_view._view_center.x(), 0.0, places=2)
            self.assertAlmostEqual(self.window._image_view._view_center.y(), 0.0, places=2)
            self.assertGreaterEqual(self.window._image_view._zoom_scale, 3.0)

    def test_center_object_toggle_auto_follows_source_result_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "frame_01.fits"
            image_path.write_text("placeholder", encoding="utf-8")

            first_entry = CatalogStar(
                catalog="vsx",
                source_id="vsx-1",
                name="Target A",
                ra_deg=10.0,
                dec_deg=20.0,
                magnitude=12.1,
                is_variable=True,
            )
            second_entry = CatalogStar(
                catalog="vsx",
                source_id="vsx-2",
                name="Target B",
                ra_deg=10.0 - (2.0 / 3600.0),
                dec_deg=20.0,
                magnitude=12.4,
                is_variable=True,
            )
            first_measurement = PhotometryMeasurement(
                source_id="vsx-1",
                source_name="Target A",
                catalog="vsx",
                object_name="Demo",
                file_path=image_path,
                observation_time=datetime(2026, 4, 14, 1, 2, 3),
                filter_name="V",
                ra_deg=first_entry.ra_deg,
                dec_deg=first_entry.dec_deg,
                x=50.0,
                y=60.0,
                flux=1000.0,
                flux_error=10.0,
                instrumental_magnitude=-2.5,
                differential_magnitude=12.0,
                differential_magnitude_error=0.05,
                is_variable=True,
                is_reference=False,
                flags=[],
            )
            second_measurement = PhotometryMeasurement(
                source_id="vsx-2",
                source_name="Target B",
                catalog="vsx",
                object_name="Demo",
                file_path=image_path,
                observation_time=datetime(2026, 4, 14, 1, 12, 3),
                filter_name="V",
                ra_deg=second_entry.ra_deg,
                dec_deg=second_entry.dec_deg,
                x=52.0,
                y=60.0,
                flux=900.0,
                flux_error=12.0,
                instrumental_magnitude=-2.3,
                differential_magnitude=12.3,
                differential_magnitude_error=0.06,
                is_variable=True,
                is_reference=False,
                flags=[],
            )
            first_series = LightCurveSeries(
                object_name="Demo",
                source_id="vsx-1",
                source_name="Target A",
                filter_name="V",
                points=[
                    LightCurvePoint(
                        observation_time=first_measurement.observation_time,
                        file_path=image_path,
                        differential_magnitude=12.0,
                        instrumental_magnitude=-2.5,
                        flux=1000.0,
                        flux_error=10.0,
                        differential_magnitude_error=0.05,
                    )
                ],
            )
            second_series = LightCurveSeries(
                object_name="Demo",
                source_id="vsx-2",
                source_name="Target B",
                filter_name="V",
                points=[
                    LightCurvePoint(
                        observation_time=second_measurement.observation_time,
                        file_path=image_path,
                        differential_magnitude=12.3,
                        instrumental_magnitude=-2.3,
                        flux=900.0,
                        flux_error=12.0,
                        differential_magnitude_error=0.06,
                    )
                ],
            )
            report = ProcessingReport(
                object_name="Demo",
                files_processed=1,
                solved_files=1,
                field_catalog=FieldCatalog(
                    center_ra_deg=10.0,
                    center_dec_deg=20.0,
                    radius_deg=1.0,
                    variable_stars=[first_entry, second_entry],
                ),
                measurements=[first_measurement, second_measurement],
                light_curves=[first_series, second_series],
                notes=[],
            )
            display = build_annotated_image_display_from_array(
                np.arange(25, dtype=float).reshape(5, 5),
                image_path=image_path,
            )

            with patch("photometry_app.ui.main_window.build_annotated_image_display", return_value=display):
                self.window._handle_processing_completed(report)
                self.assertTrue(self.window._image_center_object_button.isEnabled())
                self.window._source_table.clearSelection()
                self.window._source_table.setCurrentCell(0, 0)
                self.window._source_table.selectRow(0)
                self.window._image_center_object_button.setChecked(True)
                with (
                    patch.object(self.window, "_image_focus_coordinates_for_selected_source", return_value=(12.0, 34.0)),
                    patch.object(self.window._image_view, "focus_on") as focus_on,
                ):
                    self.window._select_next_source_result()

            self.assertEqual(self.window._source_table.currentRow(), 1)
            focus_on.assert_called_once_with(12.0, 34.0, minimum_zoom_scale=3.0)