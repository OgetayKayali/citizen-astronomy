from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from photometry_app.core.solar_system import (
    HeliocentricReferenceBody,
    KnownObjectComparisonTrack,
    KnownObjectHeliocentricContext,
    SolarSystemDetection,
    SolarSystemFrameMeasurement,
    SolarSystemMagnitudeSample,
    SolarSystemStateVectorSample,
)
from photometry_app.ui.dialogs import (
    _KNOWN_OBJECT_3D_MAX_ZOOM_OUT_EXTENT_FACTOR,
    _KNOWN_OBJECT_3D_MIN_CAMERA_DISTANCE_AU,
    _KnownObjectOrbitGLViewWidget,
    KnownObjectOrbit3DDialog,
    KnownObjectOrbit3DPlannerDialog,
    KnownObjectOrbit3DPlannerRequest,
    KnownObjectOrbit3DSaveDialog,
    KnownObjectOrbit3DSaveExportPlan,
    KnownObjectOrbit3DSearchEntry,
)
from photometry_app.ui.main_window import MainWindow
from photometry_app.ui.workers import AsteroidOrbitContextTarget


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _state_vector(observation_time: datetime, x_au: float, y_au: float, z_au: float) -> SolarSystemStateVectorSample:
    return SolarSystemStateVectorSample(
        observation_time=observation_time,
        x_au=x_au,
        y_au=y_au,
        z_au=z_au,
        vx_au_per_day=0.0,
        vy_au_per_day=0.0,
        vz_au_per_day=0.0,
    )


def _frame_measurement(observation_time: datetime, source_name: str) -> SolarSystemFrameMeasurement:
    return SolarSystemFrameMeasurement(
        source_path=Path(source_name),
        observation_time=observation_time,
        predicted_ra_deg=10.0,
        predicted_dec_deg=20.0,
        predicted_x=100.0,
        predicted_y=200.0,
        expected_trail_length_px=3.5,
    )


def _detection(name: str, designation: str | None, object_type: str) -> SolarSystemDetection:
    return SolarSystemDetection(
        name=name,
        designation=designation,
        object_type=object_type,
        orbit_class=object_type.lower(),
        predicted_ra_deg=10.0,
        predicted_dec_deg=20.0,
        predicted_x=100.0,
        predicted_y=200.0,
        predicted_magnitude=12.3,
        ra_rate_arcsec_per_hour=1.0,
        dec_rate_arcsec_per_hour=1.0,
        motion_rate_arcsec_per_hour=1.4,
        expected_trail_length_px=3.5,
        positional_uncertainty_arcsec=0.8,
        altitude_deg=45.0,
        likely_visible=True,
        confidence_score=0.95,
        status="Known object",
    )


def _context(label: str, resolved_target_name: str, base_time: datetime) -> KnownObjectHeliocentricContext:
    path_times = tuple(base_time + timedelta(days=index) for index in range(3))
    observed_times = tuple(base_time + timedelta(days=index) for index in range(2))
    return KnownObjectHeliocentricContext(
        object_label=label,
        resolved_target_name=resolved_target_name,
        earth_target_name="Earth",
        window_start=path_times[0],
        window_end=path_times[-1],
        arc_padding_days=45.0,
        reference_time=base_time,
        object_path_samples=tuple(_state_vector(sample_time, 1.0 + (index * 0.2), 0.5 + (index * 0.1), 0.1) for index, sample_time in enumerate(path_times)),
        earth_path_samples=tuple(_state_vector(sample_time, 0.1 + (index * 0.01), 1.0 + (index * 0.02), 0.0) for index, sample_time in enumerate(path_times)),
        observation_object_samples=tuple(_state_vector(sample_time, 1.0 + (index * 0.2), 0.5 + (index * 0.1), 0.1) for index, sample_time in enumerate(observed_times)),
        observation_earth_samples=tuple(_state_vector(sample_time, 0.1 + (index * 0.01), 1.0 + (index * 0.02), 0.0) for index, sample_time in enumerate(observed_times)),
        object_magnitude_samples=tuple(
            SolarSystemMagnitudeSample(observation_time=sample_time, literature_magnitude=12.0 + index)
            for index, sample_time in enumerate(path_times)
        ),
        object_orbital_period_days=365.25,
        earth_orbital_period_days=365.25,
    )


class RecoveryAuditRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_main_window_missing_private_handler_raises_instead_of_becoming_no_op(self) -> None:
        window = MainWindow()
        try:
            with self.assertRaises(AttributeError):
                getattr(window, "_missing_recovery_probe_handler")
        finally:
            window.close()

    def test_known_object_orbit_3d_dialog_uses_unix_timestamps_and_bottom_hover_labels(self) -> None:
        base_time = datetime(2026, 4, 14, 6, 0, tzinfo=UTC)
        detection = _detection("12P/Pons-Brooks", "12P", "Comet")
        frame_measurements = (
            _frame_measurement(base_time, "frame_01.fits"),
            _frame_measurement(base_time + timedelta(days=1), "frame_02.fits"),
        )
        dialog = KnownObjectOrbit3DDialog(
            detection=detection,
            frame_measurements=frame_measurements,
            context=_context("12P/Pons-Brooks", "DES=12P;CAP", base_time),
        )

        try:
            first_series = dialog._distance_hover_series[0]
            self.assertAlmostEqual(first_series[1][0], base_time.timestamp())

            dialog._set_time_series_hover("distance", float(first_series[1][0]))

            self.assertEqual(dialog._distance_hover_artists["x_text"], "2026-04-14")
            self.assertTrue(any("2026-04-14" in text for text in dialog._distance_hover_artists["series_texts"]))
            self.assertAlmostEqual(
                float(dialog._distance_hover_artists["x_annotation"].pos().y()),
                dialog._time_series_bottom_y(dialog._distance_plot),
            )
        finally:
            dialog.close()

    def test_known_object_orbit_3d_dialog_syncs_primary_target_state_after_reload(self) -> None:
        base_time = datetime(2026, 4, 14, 6, 0, tzinfo=UTC)
        comet_detection = _detection("12P/Pons-Brooks", "12P", "Comet")
        comet_measurements = (
            _frame_measurement(base_time, "comet_01.fits"),
            _frame_measurement(base_time + timedelta(days=1), "comet_02.fits"),
        )
        dialog = KnownObjectOrbit3DDialog(
            detection=comet_detection,
            frame_measurements=comet_measurements,
            context=_context("12P/Pons-Brooks", "DES=12P;CAP", base_time),
        )

        asteroid_detection = _detection("Ceres", "1", "Asteroid")
        asteroid_measurements = (
            _frame_measurement(base_time, "asteroid_01.fits"),
            _frame_measurement(base_time + timedelta(days=1), "asteroid_02.fits"),
        )
        asteroid_target = AsteroidOrbitContextTarget(
            detection=asteroid_detection,
            frame_measurements=asteroid_measurements,
        )

        try:
            dialog._handle_context_reload_completed(
                SimpleNamespace(
                    context=_context("Ceres", "1 Ceres", base_time),
                    targets=(asteroid_target,),
                    available_targets=(asteroid_target,),
                )
            )

            self.assertIn("Ceres", dialog.windowTitle())
            self.assertIn("Ceres", dialog._summary_text())
            self.assertEqual(dialog._primary_target_style()["hex"], dialog._asteroid_color_hex)
        finally:
            dialog.close()

    def test_known_object_orbit_3d_dialog_adds_nearby_targets_through_objects_menu(self) -> None:
        base_time = datetime(2026, 4, 14, 6, 0, tzinfo=UTC)
        detection = _detection("12P/Pons-Brooks", "12P", "Comet")
        frame_measurements = (
            _frame_measurement(base_time, "frame_01.fits"),
            _frame_measurement(base_time + timedelta(days=1), "frame_02.fits"),
        )
        nearby_target = AsteroidOrbitContextTarget(
            detection=_detection("(2) Pallas", "2", "Asteroid"),
            frame_measurements=frame_measurements,
        )
        dialog = KnownObjectOrbit3DDialog(
            detection=detection,
            frame_measurements=frame_measurements,
            context=_context("12P/Pons-Brooks", "DES=12P;CAP", base_time),
            search_nearby_targets=lambda radius, magnitude: (
                KnownObjectOrbit3DSearchEntry(target=nearby_target, angular_distance_deg=2.1, is_in_image=False),
            ),
        )

        try:
            self.assertIn("Nearby Search...", [action.text() for action in dialog._object_menu.actions()])

            with (
                patch("photometry_app.ui.dialogs.KnownObjectOrbit3DNearbySearchDialog") as search_dialog_class,
                patch.object(dialog, "_start_context_reload") as start_reload,
            ):
                dialog_instance = search_dialog_class.return_value
                dialog_instance.exec.return_value = int(QDialog.DialogCode.Accepted)
                dialog_instance.selected_entries.return_value = (
                    KnownObjectOrbit3DSearchEntry(target=nearby_target, angular_distance_deg=2.1, is_in_image=False),
                )

                dialog._handle_nearby_search_requested()

            target_keys = {dialog._target_visibility_key(target.detection) for target in dialog._available_targets}
            self.assertIn(dialog._target_visibility_key(nearby_target.detection), target_keys)
            self.assertTrue(dialog._object_visibility_states[dialog._target_visibility_key(nearby_target.detection)])
            start_reload.assert_called_once()
        finally:
            dialog.close()

    def test_known_object_orbit_3d_dialog_lookup_button_adds_selected_target(self) -> None:
        base_time = datetime(2026, 4, 14, 6, 0, tzinfo=UTC)
        detection = _detection("12P/Pons-Brooks", "12P", "Comet")
        frame_measurements = (
            _frame_measurement(base_time, "frame_01.fits"),
            _frame_measurement(base_time + timedelta(days=1), "frame_02.fits"),
        )
        lookup_target = AsteroidOrbitContextTarget(
            detection=_detection("(2) Pallas", "2", "Asteroid"),
            frame_measurements=frame_measurements,
        )
        dialog = KnownObjectOrbit3DDialog(
            detection=detection,
            frame_measurements=frame_measurements,
            context=_context("12P/Pons-Brooks", "DES=12P;CAP", base_time),
            lookup_exact_target=lambda identifier: (
                KnownObjectOrbit3DSearchEntry(target=lookup_target, angular_distance_deg=2.1, is_in_image=False),
            ),
        )

        try:
            self.assertFalse(dialog._object_lookup_button.isHidden())
            self.assertTrue(dialog._object_lookup_button.isEnabled())
            with (
                patch("photometry_app.ui.dialogs.KnownObjectOrbit3DExactLookupDialog") as lookup_dialog_class,
                patch.object(dialog, "_start_context_reload") as start_reload,
            ):
                dialog_instance = lookup_dialog_class.return_value
                dialog_instance.exec.return_value = int(QDialog.DialogCode.Accepted)
                dialog_instance.selected_entries.return_value = (
                    KnownObjectOrbit3DSearchEntry(target=lookup_target, angular_distance_deg=2.1, is_in_image=False),
                )

                dialog._handle_exact_lookup_requested()

            self.assertTrue(dialog._object_visibility_states[dialog._target_visibility_key(lookup_target.detection)])
            start_reload.assert_called_once()
        finally:
            dialog.close()

    def test_known_object_orbit_3d_starfield_stays_beyond_max_zoom_out(self) -> None:
        for scene_extent in (1.0, 50.0):
            starfield_radius = KnownObjectOrbit3DDialog._starfield_radius(scene_extent)
            max_distance = KnownObjectOrbit3DDialog._max_camera_distance(scene_extent)
            self.assertGreater(starfield_radius, max_distance * 2.0, scene_extent)

    @unittest.skipIf(_KnownObjectOrbitGLViewWidget is None, "pyqtgraph.opengl is unavailable")
    def test_known_object_orbit_3d_gl_view_clamps_wheel_zoom_distance(self) -> None:
        view = _KnownObjectOrbitGLViewWidget()
        try:
            view.set_camera_distance_limits(_KNOWN_OBJECT_3D_MIN_CAMERA_DISTANCE_AU, 700.0, minimum_far_clip=2400.0)

            view.opts["distance"] = 50000.0
            view._clamp_camera_distance()
            self.assertEqual(view.opts["distance"], 700.0)

            view.opts["distance"] = 1.0e-6
            view._clamp_camera_distance()
            self.assertEqual(view.opts["distance"], _KNOWN_OBJECT_3D_MIN_CAMERA_DISTANCE_AU)

            view.opts["distance"] = 0.05
            matrix = view.projectionMatrix((0, 0, 100, 100), (0, 0, 100, 100))
            near_clip = 0.05 * 0.001
            far_clip = 2400.0
            expected_depth_scale = -(far_clip + near_clip) / (far_clip - near_clip)
            self.assertAlmostEqual(float(matrix.row(2).z()), expected_depth_scale, places=6)
        finally:
            view.deleteLater()

    def test_known_object_orbit_3d_derives_sky_track_radec_from_vectors(self) -> None:
        radec = KnownObjectOrbit3DDialog._geocentric_radec_from_positions(
            np.array([1.0, 0.0, 0.0], dtype=float),
            np.array([0.0, 0.0, 0.0], dtype=float),
        )

        self.assertIsNotNone(radec)
        assert radec is not None
        self.assertAlmostEqual(radec[0], 0.0, places=6)
        self.assertAlmostEqual(radec[1], 0.0, places=6)

        radec = KnownObjectOrbit3DDialog._geocentric_radec_from_positions(
            np.array([0.0, 1.0, 0.0], dtype=float),
            np.array([0.0, 0.0, 0.0], dtype=float),
        )

        self.assertIsNotNone(radec)
        assert radec is not None
        self.assertAlmostEqual(radec[0], 90.0, places=6)
        self.assertAlmostEqual(radec[1], 23.4392911, places=6)

    def test_known_object_orbit_3d_uses_centered_all_sky_azimuthal_projection(self) -> None:
        x_values, y_values, valid = KnownObjectOrbit3DDialog._project_sky_radec(
            np.array([0.0, 90.0, 180.0], dtype=float),
            np.array([0.0, 0.0, 0.0], dtype=float),
            0.0,
            0.0,
        )

        self.assertTrue(bool(valid[0]))
        self.assertAlmostEqual(float(x_values[0]), 0.0, places=6)
        self.assertAlmostEqual(float(y_values[0]), 0.0, places=6)
        self.assertTrue(bool(valid[1]))
        self.assertAlmostEqual(float(x_values[1]), 90.0, places=6)
        self.assertAlmostEqual(float(y_values[1]), 0.0, places=6)
        self.assertFalse(bool(valid[2]))

    def test_known_object_orbit_3d_dialog_keeps_sky_track_without_data_tabs(self) -> None:
        base_time = datetime(2026, 4, 14, 6, 0, tzinfo=UTC)
        detection = _detection("12P/Pons-Brooks", "12P", "Comet")
        frame_measurements = (
            _frame_measurement(base_time, "frame_01.fits"),
            _frame_measurement(base_time + timedelta(days=1), "frame_02.fits"),
        )
        dialog = KnownObjectOrbit3DDialog(
            detection=detection,
            frame_measurements=frame_measurements,
            context=_context("12P/Pons-Brooks", "DES=12P;CAP", base_time),
        )

        try:
            self.assertIn("sky_track", dialog._info_panel_order)
            self.assertNotIn("data", dialog._info_panel_order)
            self.assertIs(dialog._info_panel_widgets["sky_track"], dialog._sky_track_plot)
            self.assertIs(dialog._info_panel_widgets["data"], dialog._table)
            self.assertGreater(dialog._sky_track_ra_deg.size, 0)
            self.assertGreater(dialog._sky_track_projected_x.size, 0)
            self.assertIsNotNone(dialog._sky_track_playback_item)
            self.assertIsNotNone(dialog._sky_track_fit_bounds)
            x_range = dialog._sky_track_plot.getPlotItem().getViewBox().viewRange()[0]
            y_range = dialog._sky_track_plot.getPlotItem().getViewBox().viewRange()[1]
            self.assertGreater(abs(x_range[1] - x_range[0]), 0.0)
            self.assertGreater(abs(y_range[1] - y_range[0]), 0.0)
            # Aspect-locked fit should keep equal degree scales while matching panel aspect.
            dialog._sky_track_plot.resize(640, 200)
            dialog._apply_sky_track_view_fit()
            x_range = dialog._sky_track_plot.getPlotItem().getViewBox().viewRange()[0]
            y_range = dialog._sky_track_plot.getPlotItem().getViewBox().viewRange()[1]
            self.assertGreater(abs(x_range[1] - x_range[0]), abs(y_range[1] - y_range[0]))
        finally:
            dialog.close()

    def test_known_object_orbit_3d_sky_track_click_jumps_to_nearest_sample(self) -> None:
        base_time = datetime(2026, 4, 14, 6, 0, tzinfo=UTC)
        detection = _detection("12P/Pons-Brooks", "12P", "Comet")
        frame_measurements = (
            _frame_measurement(base_time, "frame_01.fits"),
            _frame_measurement(base_time + timedelta(days=1), "frame_02.fits"),
        )
        dialog = KnownObjectOrbit3DDialog(
            detection=detection,
            frame_measurements=frame_measurements,
            context=_context("12P/Pons-Brooks", "DES=12P;CAP", base_time),
        )

        class _ClickEvent:
            def __init__(self, scene_position) -> None:
                self._scene_position = scene_position
                self.accepted = False

            def button(self):
                return Qt.MouseButton.LeftButton

            def scenePos(self):
                return self._scene_position

            def accept(self) -> None:
                self.accepted = True

        try:
            target_index = min(1, len(dialog._sky_track_projected_times) - 1)
            view_box = dialog._sky_track_plot.getPlotItem().getViewBox()
            scene_position = view_box.mapViewToScene(
                QPointF(
                    float(dialog._sky_track_projected_x[target_index]),
                    float(dialog._sky_track_projected_y[target_index]),
                )
            )
            click_event = _ClickEvent(scene_position)

            dialog._handle_sky_track_plot_mouse_clicked(click_event)

            self.assertTrue(click_event.accepted)
            self.assertEqual(dialog._current_playback_time(), dialog._sky_track_projected_times[target_index])
        finally:
            dialog.close()

    def test_known_object_orbit_3d_sky_track_marker_stays_on_adaptive_curve(self) -> None:
        base_time = datetime(2026, 4, 14, 6, 0, tzinfo=UTC)
        detection = _detection("12P/Pons-Brooks", "12P", "Comet")
        frame_measurements = (
            _frame_measurement(base_time, "frame_01.fits"),
            _frame_measurement(base_time + timedelta(days=1), "frame_02.fits"),
        )
        dialog = KnownObjectOrbit3DDialog(
            detection=detection,
            frame_measurements=frame_measurements,
            context=_context("12P/Pons-Brooks", "DES=12P;CAP", base_time),
        )

        try:
            playback_time = base_time + timedelta(days=0.37)
            dialog._playback_time = playback_time
            object_position = dialog._interpolate_position(dialog._context.object_path_samples, playback_time)
            earth_position = dialog._interpolate_position(dialog._context.earth_path_samples, playback_time)
            dialog._update_sky_track_playback_marker(object_position, earth_position)

            marker_x, marker_y = dialog._sky_track_playback_item.getData()
            self.assertEqual(len(marker_x), 1)
            projected = dialog._sky_track_projected_series[0]
            curve_x = np.asarray(projected["projected_x"], dtype=float)
            curve_y = np.asarray(projected["projected_y"], dtype=float)
            distances = [
                dialog._point_to_segment_distance(
                    (float(marker_x[0]), float(marker_y[0])),
                    (float(curve_x[index]), float(curve_y[index])),
                    (float(curve_x[index + 1]), float(curve_y[index + 1])),
                )
                for index in range(len(curve_x) - 1)
            ]
            self.assertTrue(distances)
            self.assertLessEqual(min(distances), 0.011)
            self.assertGreater(len(curve_x), len(dialog._sky_track_ra_deg))
        finally:
            dialog.close()

    def test_known_object_orbit_3d_sky_track_draws_visible_comparison_tracks(self) -> None:
        base_time = datetime(2026, 4, 14, 6, 0, tzinfo=UTC)
        comet_detection = _detection("12P/Pons-Brooks", "12P", "Comet")
        asteroid_detection = _detection("(20) Massalia", "20", "Asteroid")
        frame_measurements = (
            _frame_measurement(base_time, "frame_01.fits"),
            _frame_measurement(base_time + timedelta(days=1), "frame_02.fits"),
        )
        path_times = tuple(base_time + timedelta(days=index) for index in range(3))
        comparison_track = KnownObjectComparisonTrack(
            object_label="(20) Massalia",
            resolved_target_name="20 Massalia",
            path_samples=tuple(_state_vector(sample_time, 1.3 + (index * 0.1), 0.6 + (index * 0.05), 0.1) for index, sample_time in enumerate(path_times)),
            observation_samples=tuple(_state_vector(sample_time, 1.3 + (index * 0.1), 0.6 + (index * 0.05), 0.1) for index, sample_time in enumerate(path_times[:2])),
            magnitude_samples=tuple(
                SolarSystemMagnitudeSample(observation_time=sample_time, literature_magnitude=11.5 + index)
                for index, sample_time in enumerate(path_times)
            ),
            orbital_period_days=1450.0,
        )
        context = KnownObjectHeliocentricContext(
            object_label="12P/Pons-Brooks",
            resolved_target_name="DES=12P;CAP",
            earth_target_name="Earth",
            window_start=path_times[0],
            window_end=path_times[-1],
            arc_padding_days=45.0,
            reference_time=base_time,
            object_path_samples=tuple(_state_vector(sample_time, 1.0 + (index * 0.2), 0.5 + (index * 0.1), 0.1) for index, sample_time in enumerate(path_times)),
            earth_path_samples=tuple(_state_vector(sample_time, 0.1 + (index * 0.01), 1.0 + (index * 0.02), 0.0) for index, sample_time in enumerate(path_times)),
            observation_object_samples=tuple(_state_vector(sample_time, 1.0 + (index * 0.2), 0.5 + (index * 0.1), 0.1) for index, sample_time in enumerate(path_times[:2])),
            observation_earth_samples=tuple(_state_vector(sample_time, 0.1 + (index * 0.01), 1.0 + (index * 0.02), 0.0) for index, sample_time in enumerate(path_times[:2])),
            object_magnitude_samples=tuple(
                SolarSystemMagnitudeSample(observation_time=sample_time, literature_magnitude=12.0 + index)
                for index, sample_time in enumerate(path_times)
            ),
            comparison_tracks=(comparison_track,),
            object_orbital_period_days=365.25,
            earth_orbital_period_days=365.25,
        )
        targets = (
            AsteroidOrbitContextTarget(detection=comet_detection, frame_measurements=frame_measurements),
            AsteroidOrbitContextTarget(detection=asteroid_detection, frame_measurements=frame_measurements),
        )
        dialog = KnownObjectOrbit3DDialog(
            detection=comet_detection,
            frame_measurements=frame_measurements,
            context=context,
            targets=targets,
            available_targets=targets,
        )

        try:
            self.assertEqual(len(dialog._sky_track_series), 2)
            self.assertEqual(len(dialog._sky_track_projected_series), 2)
            labels = [str(entry["label"]) for entry in dialog._sky_track_projected_series]
            self.assertIn("12P/Pons-Brooks", labels)
            self.assertIn("(20) Massalia", labels)
            colors = {str(entry["label"]): str(entry["color_hex"]) for entry in dialog._sky_track_projected_series}
            self.assertEqual(colors["12P/Pons-Brooks"], dialog._comet_color_hex)
            self.assertEqual(colors["(20) Massalia"], dialog._asteroid_color_hex)
            self.assertGreater(dialog._sky_track_projected_x.size, dialog._sky_track_ra_deg.size)

            dialog._update_sky_track_playback_marker(
                np.array([1.0, 0.5, 0.1], dtype=float),
                np.array([0.1, 1.0, 0.0], dtype=float),
            )
            playback_data = dialog._sky_track_playback_item.getData()
            self.assertEqual(len(playback_data[0]), 2)

            comparison_series = next(entry for entry in dialog._sky_track_projected_series if int(entry["target_index"]) == 1)
            comparison_x = np.asarray(comparison_series["projected_x"], dtype=float)
            comparison_y = np.asarray(comparison_series["projected_y"], dtype=float)
            comparison_times = tuple(comparison_series["projected_times"])
            self.assertGreater(comparison_x.size, 0)

            class _ClickEvent:
                def __init__(self, scene_position) -> None:
                    self._scene_position = scene_position
                    self.accepted = False

                def button(self):
                    return Qt.MouseButton.LeftButton

                def scenePos(self):
                    return self._scene_position

                def accept(self) -> None:
                    self.accepted = True

            target_index = min(1, len(comparison_times) - 1)
            view_box = dialog._sky_track_plot.getPlotItem().getViewBox()
            scene_position = view_box.mapViewToScene(
                QPointF(float(comparison_x[target_index]), float(comparison_y[target_index]))
            )
            click_event = _ClickEvent(scene_position)
            dialog._handle_sky_track_plot_mouse_clicked(click_event)
            self.assertTrue(click_event.accepted)
            self.assertEqual(dialog._current_playback_time(), comparison_times[target_index])

            primary_key = dialog._target_visibility_key(comet_detection)
            dialog._object_visibility_states[primary_key] = False
            dialog._draw_sky_track_plot()
            self.assertEqual(len(dialog._sky_track_projected_series), 1)
            self.assertEqual(int(dialog._sky_track_projected_series[0]["target_index"]), 1)
        finally:
            dialog.close()

    def test_known_object_orbit_3d_sky_track_settings_control_labels_density_and_constellations(self) -> None:
        base_time = datetime(2026, 4, 14, 6, 0, tzinfo=UTC)
        detection = _detection("12P/Pons-Brooks", "12P", "Comet")
        frame_measurements = (
            _frame_measurement(base_time, "frame_01.fits"),
            _frame_measurement(base_time + timedelta(days=1), "frame_02.fits"),
        )
        dialog = KnownObjectOrbit3DDialog(
            detection=detection,
            frame_measurements=frame_measurements,
            context=_context("12P/Pons-Brooks", "DES=12P;CAP", base_time),
        )

        try:
            self.assertEqual(dialog._sky_track_density_key(), "medium")
            self.assertAlmostEqual(dialog._sky_track_magnitude_limit(), 2.5)
            self.assertFalse(dialog._sky_track_bayer_labels_enabled())
            self.assertTrue(dialog._sky_track_constellations_enabled())
            self.assertAlmostEqual(dialog._sky_track_star_draw_radius_deg(), 180.0)
            self.assertEqual(
                KnownObjectOrbit3DDialog._sky_track_bayer_label_from_alias(
                    "Alpha Orionis",
                    dialog._sky_track_constellation_abbreviation_map(),
                ),
                "a Ori",
            )
            self.assertEqual(
                KnownObjectOrbit3DDialog._sky_track_bayer_label_from_alias(
                    "Zeta Ophiuchi",
                    dialog._sky_track_constellation_abbreviation_map(),
                ),
                "z Oph",
            )

            dialog._sky_track_density_combo.setCurrentIndex(dialog._sky_track_density_combo.findData("sparse"))
            dialog._sky_track_extent_spin.setValue(90.0)
            dialog._sky_track_bayer_checkbox.setChecked(True)
            dialog._sky_track_constellations_checkbox.setChecked(False)
            dialog._handle_sky_track_display_settings_changed()

            self.assertEqual(dialog._sky_track_density_key(), "sparse")
            self.assertAlmostEqual(dialog._sky_track_magnitude_limit(), 1.2)
            self.assertAlmostEqual(dialog._sky_track_star_draw_radius_deg(), 90.0)
            self.assertTrue(dialog._sky_track_bayer_labels_enabled())
            self.assertFalse(dialog._sky_track_constellations_enabled())
            self.assertGreater(dialog._sky_track_projected_x.size, 0)
        finally:
            dialog.close()

    def test_known_object_orbit_3d_info_panels_default_order_and_can_be_reordered(self) -> None:
        base_time = datetime(2026, 4, 14, 6, 0, tzinfo=UTC)
        detection = _detection("12P/Pons-Brooks", "12P", "Comet")
        frame_measurements = (
            _frame_measurement(base_time, "frame_01.fits"),
            _frame_measurement(base_time + timedelta(days=1), "frame_02.fits"),
        )
        dialog = KnownObjectOrbit3DDialog(
            detection=detection,
            frame_measurements=frame_measurements,
            context=_context("12P/Pons-Brooks", "DES=12P;CAP", base_time),
        )

        try:
            self.assertEqual(dialog._info_panel_order, ["topdown", "sky_track", "magnitude", "distance"])
            self.assertEqual(dialog._right_splitter.count(), 4)
            self.assertIs(dialog._right_splitter.widget(0), dialog._topdown_plot)
            self.assertIs(dialog._right_splitter.widget(1), dialog._sky_track_plot)
            self.assertIs(dialog._right_splitter.widget(2), dialog._magnitude_plot)
            self.assertIs(dialog._right_splitter.widget(3), dialog._distance_plot)
            self.assertNotIn(
                dialog._table,
                [dialog._right_splitter.widget(index) for index in range(dialog._right_splitter.count())],
            )
            self.assertEqual(
                [dialog._panel_order_list.item(index).data(Qt.ItemDataRole.UserRole) for index in range(dialog._panel_order_list.count())],
                ["topdown", "sky_track", "magnitude", "distance"],
            )
            self.assertGreaterEqual(dialog._panel_add_combo.findData("data"), 0)

            dialog._panel_order_list.setCurrentRow(1)
            dialog._handle_panel_move_up()
            self.assertEqual(dialog._info_panel_order, ["sky_track", "topdown", "magnitude", "distance"])
            self.assertIs(dialog._right_splitter.widget(0), dialog._sky_track_plot)
            self.assertIs(dialog._right_splitter.widget(1), dialog._topdown_plot)

            dialog._panel_add_combo.setCurrentIndex(dialog._panel_add_combo.findData("data"))
            dialog._handle_panel_add()
            self.assertEqual(dialog._info_panel_order[-1], "data")
            self.assertEqual(dialog._right_splitter.count(), 5)
            self.assertIs(dialog._right_splitter.widget(4), dialog._table)

            dialog._panel_order_list.setCurrentRow(dialog._panel_order_list.count() - 1)
            dialog._handle_panel_remove()
            self.assertNotIn("data", dialog._info_panel_order)
            self.assertEqual(dialog._right_splitter.count(), 4)

            dialog._handle_panel_layout_reset()
            self.assertEqual(dialog._info_panel_order, ["topdown", "sky_track", "magnitude", "distance"])
            self.assertIs(dialog._right_splitter.widget(0), dialog._topdown_plot)
            self.assertIs(dialog._right_splitter.widget(1), dialog._sky_track_plot)
            self.assertIs(dialog._right_splitter.widget(2), dialog._magnitude_plot)
            self.assertIs(dialog._right_splitter.widget(3), dialog._distance_plot)
        finally:
            dialog.close()

    def test_known_object_orbit_3d_save_dialog_plans_stills_and_animations(self) -> None:
        thirty_days_seconds = 30.0 * 86400.0
        dialog = KnownObjectOrbit3DSaveDialog(
            animation_window_seconds=thirty_days_seconds,
            speed_seconds_per_second=86400.0,
            speed_label="1 d/s",
            capture_size_provider=lambda include_panel: (1600, 900) if include_panel else (1080, 720),
        )
        try:
            still_plan = dialog.export_plan()
            self.assertEqual(still_plan.export_format, "png")
            self.assertFalse(still_plan.is_animation)
            self.assertEqual(still_plan.frame_count, 1)
            self.assertIn("1080 x 720", dialog._details_label.text())

            mp4_index = dialog._format_combo.findData("mp4")
            self.assertGreaterEqual(mp4_index, 0)
            dialog._format_combo.setCurrentIndex(mp4_index)
            dialog._with_panel_radio.setChecked(True)
            animation_plan = dialog.export_plan()
            self.assertTrue(animation_plan.is_animation)
            self.assertTrue(animation_plan.include_info_panel)
            self.assertAlmostEqual(animation_plan.total_duration_seconds, 30.0)
            self.assertEqual(animation_plan.frame_count, 900)
            self.assertEqual(animation_plan.frame_duration_ms, 33)
            details_text = dialog._details_label.text()
            self.assertIn("900 frames", details_text)
            self.assertIn("30 s", details_text)
            self.assertIn("1600 x 900", details_text)
            self.assertIn("Estimated file size", details_text)
        finally:
            dialog.close()

    def test_known_object_orbit_3d_save_dialog_omits_animation_formats_without_timeline(self) -> None:
        dialog = KnownObjectOrbit3DSaveDialog(
            animation_window_seconds=0.0,
            speed_seconds_per_second=86400.0,
            speed_label="1 d/s",
            capture_size_provider=lambda include_panel: (800, 600),
        )
        try:
            available_formats = {dialog._format_combo.itemData(index) for index in range(dialog._format_combo.count())}
            self.assertEqual(available_formats, {"png", "jpg"})
        finally:
            dialog.close()

    def test_known_object_orbit_3d_compose_side_by_side_keeps_info_panel(self) -> None:
        left = QImage(40, 30, QImage.Format.Format_RGB888)
        left.fill(Qt.GlobalColor.red)
        right = QImage(20, 30, QImage.Format.Format_RGB888)
        right.fill(Qt.GlobalColor.blue)

        composed = KnownObjectOrbit3DDialog._compose_side_by_side_export_image(left, right)

        self.assertEqual(composed.width(), 60)
        self.assertEqual(composed.height(), 30)
        self.assertEqual(QColor(composed.pixel(5, 5)), QColor(Qt.GlobalColor.red))
        self.assertEqual(QColor(composed.pixel(45, 5)), QColor(Qt.GlobalColor.blue))

    def test_known_object_orbit_3d_capture_with_info_panel_stitches_both_sides(self) -> None:
        base_time = datetime(2026, 4, 14, 6, 0, tzinfo=UTC)
        detection = _detection("12P/Pons-Brooks", "12P", "Comet")
        frame_measurements = (
            _frame_measurement(base_time, "frame_01.fits"),
            _frame_measurement(base_time + timedelta(days=1), "frame_02.fits"),
        )
        dialog = KnownObjectOrbit3DDialog(
            detection=detection,
            frame_measurements=frame_measurements,
            context=_context("12P/Pons-Brooks", "DES=12P;CAP", base_time),
        )
        left = QImage(40, 30, QImage.Format.Format_RGB888)
        left.fill(Qt.GlobalColor.darkCyan)
        right = QImage(24, 30, QImage.Format.Format_RGB888)
        right.fill(Qt.GlobalColor.darkYellow)
        try:
            with (
                patch.object(dialog, "_capture_gl_view_image", return_value=left),
                patch.object(dialog, "_capture_info_panel_image", return_value=right),
            ):
                composed = dialog._capture_export_image(True)
            self.assertIsNotNone(composed)
            assert composed is not None
            self.assertEqual(composed.width(), 64)
            self.assertEqual(composed.height(), 30)
            self.assertEqual(QColor(composed.pixel(2, 2)), QColor(Qt.GlobalColor.darkCyan))
            self.assertEqual(QColor(composed.pixel(50, 2)), QColor(Qt.GlobalColor.darkYellow))
        finally:
            dialog.close()

    def test_known_object_orbit_3d_dialog_save_button_exports_still_image(self) -> None:
        base_time = datetime(2026, 4, 14, 6, 0, tzinfo=UTC)
        detection = _detection("12P/Pons-Brooks", "12P", "Comet")
        frame_measurements = (
            _frame_measurement(base_time, "frame_01.fits"),
            _frame_measurement(base_time + timedelta(days=1), "frame_02.fits"),
        )
        dialog = KnownObjectOrbit3DDialog(
            detection=detection,
            frame_measurements=frame_measurements,
            context=_context("12P/Pons-Brooks", "DES=12P;CAP", base_time),
        )
        captured_image = QImage(64, 48, QImage.Format.Format_RGB888)
        captured_image.fill(Qt.GlobalColor.darkBlue)
        try:
            self.assertEqual(dialog._save_view_button.text(), "Save")
            self.assertTrue(dialog._save_view_button.isEnabled())
            with tempfile.TemporaryDirectory() as temp_dir:
                output_path = Path(temp_dir) / "trajectory.png"
                still_plan = KnownObjectOrbit3DSaveExportPlan(
                    export_format="png",
                    include_info_panel=False,
                    is_animation=False,
                    frame_count=1,
                    frame_duration_ms=0,
                    total_duration_seconds=0.0,
                )
                with (
                    patch("photometry_app.ui.dialogs.KnownObjectOrbit3DSaveDialog") as save_dialog_class,
                    patch(
                        "photometry_app.ui.dialogs.QFileDialog.getSaveFileName",
                        return_value=(str(output_path), "PNG image (*.png)"),
                    ),
                    patch.object(dialog, "_capture_export_image", return_value=captured_image) as capture,
                    patch("photometry_app.ui.dialogs.QMessageBox.information"),
                ):
                    save_dialog_instance = save_dialog_class.return_value
                    save_dialog_instance.exec.return_value = int(QDialog.DialogCode.Accepted)
                    save_dialog_instance.export_plan.return_value = still_plan

                    dialog._handle_save_view_requested()

                capture.assert_called_once_with(False)
                self.assertTrue(output_path.exists())
                self.assertGreater(output_path.stat().st_size, 0)
        finally:
            dialog.close()

    def test_known_object_orbit_3d_dialog_exports_gif_animation_over_timeline(self) -> None:
        base_time = datetime(2026, 4, 14, 6, 0, tzinfo=UTC)
        detection = _detection("12P/Pons-Brooks", "12P", "Comet")
        frame_measurements = (
            _frame_measurement(base_time, "frame_01.fits"),
            _frame_measurement(base_time + timedelta(days=1), "frame_02.fits"),
        )
        dialog = KnownObjectOrbit3DDialog(
            detection=detection,
            frame_measurements=frame_measurements,
            context=_context("12P/Pons-Brooks", "DES=12P;CAP", base_time),
        )
        captured_image = QImage(32, 24, QImage.Format.Format_RGB888)
        captured_image.fill(Qt.GlobalColor.black)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                output_path = Path(temp_dir) / "trajectory.gif"
                animation_plan = KnownObjectOrbit3DSaveExportPlan(
                    export_format="gif",
                    include_info_panel=False,
                    is_animation=True,
                    frame_count=4,
                    frame_duration_ms=100,
                    total_duration_seconds=0.4,
                )
                captured_times: list[datetime] = []
                original_set_playback_time = dialog._set_playback_time

                def record_playback_time(observation_time: datetime, **kwargs) -> None:
                    captured_times.append(observation_time)
                    original_set_playback_time(observation_time, **kwargs)

                with (
                    patch.object(dialog, "_capture_export_image", return_value=captured_image),
                    patch.object(dialog, "_set_playback_time", side_effect=record_playback_time),
                    patch("photometry_app.ui.dialogs.QMessageBox.information"),
                ):
                    dialog._export_trajectory_animation(output_path, animation_plan)

                self.assertTrue(output_path.exists())
                self.assertGreater(output_path.stat().st_size, 0)
                window_start, window_end = dialog._playback_window_bounds()
                window_seconds = (window_end - window_start).total_seconds()
                self.assertEqual(len(captured_times), 5)
                self.assertEqual(captured_times[0], window_start)
                self.assertEqual(
                    captured_times[2],
                    window_start + timedelta(seconds=window_seconds * 2 / 4),
                )
        finally:
            dialog.close()

    def test_known_object_orbit_3d_planner_dialog_keeps_enter_bound_to_search(self) -> None:
        dialog = KnownObjectOrbit3DPlannerDialog(
            default_start_time=datetime(2026, 4, 22, 0, 0, tzinfo=UTC),
            default_end_time=datetime(2026, 4, 23, 0, 0, tzinfo=UTC),
            search_callback=lambda request, **_kwargs: (),
        )

        try:
            self.assertFalse(dialog._search_button.autoDefault())
            self.assertFalse(dialog._add_button.autoDefault())

            dialog._identifier_input.setText("Pallas")

            with patch.object(dialog, "_begin_search") as begin_search:
                dialog._start_search()

            begin_search.assert_called_once()
            self.assertEqual(dialog._start_time_input.text(), "2026-04-22")
            self.assertEqual(dialog._end_time_input.text(), "2026-04-23")
        finally:
            dialog.close()

    def test_known_object_orbit_3d_planner_dialog_rejects_blank_identifier(self) -> None:
        captured_requests: list[KnownObjectOrbit3DPlannerRequest] = []
        dialog = KnownObjectOrbit3DPlannerDialog(
            default_start_time=datetime(2026, 4, 22, 0, 0, tzinfo=UTC),
            default_end_time=datetime(2026, 4, 23, 0, 0, tzinfo=UTC),
            search_callback=lambda request, **_kwargs: captured_requests.append(request) or (),
        )

        try:
            dialog._identifier_input.setText("")

            dialog._start_search()
            QTest.qWait(200)
            self._app.processEvents()

            self.assertEqual(captured_requests, [])
            self.assertEqual(dialog._status_label.text(), "Enter an asteroid or comet name/designation before searching.")
        finally:
            if dialog._worker is not None:
                dialog._worker.wait(1000)
                self._app.processEvents()
            dialog.close()

    def test_known_object_orbit_3d_planner_dialog_builds_midnight_utc_dates(self) -> None:
        dialog = KnownObjectOrbit3DPlannerDialog(
            default_start_time=datetime(2026, 4, 22, 6, 30, tzinfo=UTC),
            default_end_time=datetime(2026, 4, 23, 18, 45, tzinfo=UTC),
            search_callback=lambda request, **_kwargs: (),
        )

        try:
            dialog._identifier_input.setText("Pallas")
            dialog._start_time_input.setText("2026-04-22")
            dialog._end_time_input.setText("2026-04-23")

            request = dialog._build_request()

            self.assertIsNotNone(request)
            assert request is not None
            self.assertEqual(request.start_time, datetime(2026, 4, 22, 0, 0, tzinfo=UTC))
            self.assertEqual(request.end_time, datetime(2026, 4, 23, 0, 0, tzinfo=UTC))
        finally:
            dialog.close()

    def test_known_object_orbit_3d_dialog_uses_window_timeline_for_manual_time_and_playback(self) -> None:
        base_time = datetime(2026, 4, 14, 6, 0, tzinfo=UTC)
        detection = _detection("12P/Pons-Brooks", "12P", "Comet")
        frame_measurements = (
            _frame_measurement(base_time, "frame_01.fits"),
            _frame_measurement(base_time + timedelta(days=1), "frame_02.fits"),
        )
        dialog = KnownObjectOrbit3DDialog(
            detection=detection,
            frame_measurements=frame_measurements,
            context=_context("12P/Pons-Brooks", "DES=12P;CAP", base_time),
        )

        try:
            self.assertEqual(dialog._frame_slider.maximum(), 2)

            dialog._time_input.setText("2026-04-16 06:00:00 UTC")
            dialog._handle_time_input_editing_finished()

            self.assertEqual(dialog._current_playback_time(), base_time + timedelta(days=2))
            self.assertEqual(dialog._frame_slider.value(), 2)

            dialog._speed_combo.setCurrentIndex(2)
            dialog._playback_timer.setInterval(1000)
            dialog._set_playback_time(base_time, update_camera=False)
            dialog._advance_playback()

            self.assertEqual(dialog._current_playback_time(), base_time + timedelta(days=1))
        finally:
            dialog.close()

    def test_known_object_orbit_3d_dialog_reset_button_restores_observation_time(self) -> None:
        base_time = datetime(2026, 4, 14, 6, 0, tzinfo=UTC)
        detection = _detection("12P/Pons-Brooks", "12P", "Comet")
        frame_measurements = (
            _frame_measurement(base_time, "frame_01.fits"),
            _frame_measurement(base_time + timedelta(days=1), "frame_02.fits"),
        )
        dialog = KnownObjectOrbit3DDialog(
            detection=detection,
            frame_measurements=frame_measurements,
            context=_context("12P/Pons-Brooks", "DES=12P;CAP", base_time),
        )

        try:
            self.assertFalse(dialog._reset_time_button.icon().isNull())
            self.assertEqual(dialog._reset_time_button.toolTip(), "Return to the observation time")
            self.assertEqual(dialog._reset_time_button.height(), dialog._play_button.height())

            dialog._set_playback_time(base_time + timedelta(days=2), update_camera=False)
            dialog._handle_reset_time_clicked()

            self.assertEqual(dialog._current_playback_time(), base_time)
            self.assertEqual(dialog._frame_slider.value(), 0)
        finally:
            dialog.close()

    def test_known_object_orbit_3d_dialog_uses_cached_planets_without_reloading_context(self) -> None:
        base_time = datetime(2026, 4, 14, 6, 0, tzinfo=UTC)
        detection = _detection("12P/Pons-Brooks", "12P", "Comet")
        frame_measurements = (
            _frame_measurement(base_time, "frame_01.fits"),
            _frame_measurement(base_time + timedelta(days=1), "frame_02.fits"),
        )
        cached_body = HeliocentricReferenceBody(
            key="mars",
            label="Mars",
            resolved_target_name="Mars (499)",
            path_samples=(
                _state_vector(base_time, 1.5, 0.2, 0.0),
                _state_vector(base_time + timedelta(days=1), 1.6, 0.25, 0.0),
                _state_vector(base_time + timedelta(days=2), 1.7, 0.3, 0.0),
            ),
            orbital_period_days=687.0,
        )
        dialog = KnownObjectOrbit3DDialog(
            detection=detection,
            frame_measurements=frame_measurements,
            context=_context("12P/Pons-Brooks", "DES=12P;CAP", base_time),
        )

        try:
            with (
                patch("photometry_app.ui.dialogs.load_cached_major_planet_heliocentric_paths", return_value=(cached_body,)),
                patch.object(dialog, "_start_context_reload") as start_reload,
            ):
                dialog._handle_planets_toggled(True)

            self.assertTrue(dialog._context.include_major_planets)
            self.assertEqual([body.label for body in dialog._context.additional_bodies], ["Mars"])
            self.assertTrue(dialog._show_planets_checkbox.isChecked())
            start_reload.assert_not_called()
        finally:
            dialog.close()

    def test_known_object_orbit_3d_dialog_uses_target_type_colors_for_right_side_plots(self) -> None:
        base_time = datetime(2026, 4, 14, 6, 0, tzinfo=UTC)
        comet_detection = _detection("12P/Pons-Brooks", "12P", "Comet")
        asteroid_detection = _detection("(20) Massalia", "20", "Asteroid")
        frame_measurements = (
            _frame_measurement(base_time, "frame_01.fits"),
            _frame_measurement(base_time + timedelta(days=1), "frame_02.fits"),
        )
        path_times = tuple(base_time + timedelta(days=index) for index in range(3))
        comparison_track = KnownObjectComparisonTrack(
            object_label="(20) Massalia",
            resolved_target_name="20 Massalia",
            path_samples=tuple(_state_vector(sample_time, 1.3 + (index * 0.1), 0.6 + (index * 0.05), 0.1) for index, sample_time in enumerate(path_times)),
            observation_samples=tuple(_state_vector(sample_time, 1.3 + (index * 0.1), 0.6 + (index * 0.05), 0.1) for index, sample_time in enumerate(path_times[:2])),
            magnitude_samples=tuple(
                SolarSystemMagnitudeSample(observation_time=sample_time, literature_magnitude=11.5 + index)
                for index, sample_time in enumerate(path_times)
            ),
            orbital_period_days=1450.0,
        )
        context = KnownObjectHeliocentricContext(
            object_label="12P/Pons-Brooks",
            resolved_target_name="DES=12P;CAP",
            earth_target_name="Earth",
            window_start=path_times[0],
            window_end=path_times[-1],
            arc_padding_days=45.0,
            reference_time=base_time,
            object_path_samples=tuple(_state_vector(sample_time, 1.0 + (index * 0.2), 0.5 + (index * 0.1), 0.1) for index, sample_time in enumerate(path_times)),
            earth_path_samples=tuple(_state_vector(sample_time, 0.1 + (index * 0.01), 1.0 + (index * 0.02), 0.0) for index, sample_time in enumerate(path_times)),
            observation_object_samples=tuple(_state_vector(sample_time, 1.0 + (index * 0.2), 0.5 + (index * 0.1), 0.1) for index, sample_time in enumerate(path_times[:2])),
            observation_earth_samples=tuple(_state_vector(sample_time, 0.1 + (index * 0.01), 1.0 + (index * 0.02), 0.0) for index, sample_time in enumerate(path_times[:2])),
            object_magnitude_samples=tuple(
                SolarSystemMagnitudeSample(observation_time=sample_time, literature_magnitude=12.0 + index)
                for index, sample_time in enumerate(path_times)
            ),
            comparison_tracks=(comparison_track,),
            object_orbital_period_days=365.25,
            earth_orbital_period_days=365.25,
        )
        targets = (
            AsteroidOrbitContextTarget(detection=comet_detection, frame_measurements=frame_measurements),
            AsteroidOrbitContextTarget(detection=asteroid_detection, frame_measurements=frame_measurements),
        )
        dialog = KnownObjectOrbit3DDialog(
            detection=comet_detection,
            frame_measurements=frame_measurements,
            context=context,
            targets=targets,
            available_targets=targets,
        )

        try:
            distance_colors = {label: color for label, _x, _y, _unit, color in dialog._distance_hover_series}
            magnitude_colors = {label: color for label, _x, _y, _unit, color in dialog._magnitude_hover_series}
            self.assertEqual(distance_colors["Object-Sun"], dialog._comet_color_hex)
            self.assertEqual(distance_colors["(20) Massalia-Sun"], dialog._asteroid_color_hex)
            self.assertEqual(magnitude_colors["12P/Pons-Brooks"], dialog._comet_color_hex)
            self.assertEqual(magnitude_colors["(20) Massalia"], dialog._asteroid_color_hex)
        finally:
            dialog.close()

    def test_known_object_orbit_3d_dialog_left_click_on_right_plot_jumps_to_clicked_date(self) -> None:
        base_time = datetime(2026, 4, 14, 6, 0, tzinfo=UTC)
        detection = _detection("12P/Pons-Brooks", "12P", "Comet")
        frame_measurements = (
            _frame_measurement(base_time, "frame_01.fits"),
            _frame_measurement(base_time + timedelta(days=1), "frame_02.fits"),
        )
        dialog = KnownObjectOrbit3DDialog(
            detection=detection,
            frame_measurements=frame_measurements,
            context=_context("12P/Pons-Brooks", "DES=12P;CAP", base_time),
        )

        class _FakeMouseClickEvent:

            def __init__(self, scene_pos: QPointF) -> None:
                self._scene_pos = scene_pos
                self.accepted = False

            def button(self):
                return Qt.MouseButton.LeftButton

            def scenePos(self) -> QPointF:
                return self._scene_pos

            def accept(self) -> None:
                self.accepted = True

        try:
            target_time = base_time + timedelta(hours=18)
            target_x = dialog._datetime_to_time_axis_value(target_time)
            scene_pos = dialog._distance_plot.getPlotItem().getViewBox().mapViewToScene(QPointF(target_x, 1.0))
            event = _FakeMouseClickEvent(scene_pos)

            dialog._handle_distance_plot_mouse_clicked(event)

            self.assertEqual(dialog._current_playback_time(), target_time)
            self.assertEqual(dialog._distance_hover_artists["x_text"], "2026-04-15")
            self.assertTrue(event.accepted)
        finally:
            dialog.close()

    def test_known_object_orbit_3d_dialog_hides_sample_points_until_enabled(self) -> None:
        base_time = datetime(2026, 4, 14, 6, 0, tzinfo=UTC)
        detection = _detection("12P/Pons-Brooks", "12P", "Comet")
        frame_measurements = (
            _frame_measurement(base_time, "frame_01.fits"),
            _frame_measurement(base_time + timedelta(days=1), "frame_02.fits"),
        )
        path_times = tuple(base_time + timedelta(days=index) for index in range(3))
        context = KnownObjectHeliocentricContext(
            object_label="12P/Pons-Brooks",
            resolved_target_name="DES=12P;CAP",
            earth_target_name="Earth",
            window_start=path_times[0],
            window_end=path_times[-1],
            arc_padding_days=45.0,
            reference_time=base_time,
            object_path_samples=tuple(_state_vector(sample_time, 1.0 + (index * 0.2), 0.5 + (index * 0.1), 0.1) for index, sample_time in enumerate(path_times)),
            earth_path_samples=tuple(_state_vector(sample_time, 0.1 + (index * 0.01), 1.0 + (index * 0.02), 0.0) for index, sample_time in enumerate(path_times)),
            observation_object_samples=tuple(_state_vector(sample_time, 1.0 + (index * 0.2), 0.5 + (index * 0.1), 0.1) for index, sample_time in enumerate(path_times[:2])),
            observation_earth_samples=tuple(_state_vector(sample_time, 0.1 + (index * 0.01), 1.0 + (index * 0.02), 0.0) for index, sample_time in enumerate(path_times[:2])),
            object_magnitude_samples=tuple(
                SolarSystemMagnitudeSample(observation_time=sample_time, literature_magnitude=12.0 + index)
                for index, sample_time in enumerate(path_times)
            ),
            object_orbital_period_days=365.25,
            earth_orbital_period_days=365.25,
        )
        dialog = KnownObjectOrbit3DDialog(
            detection=detection,
            frame_measurements=frame_measurements,
            context=context,
        )

        try:
            self.assertFalse(dialog._show_sample_points_checkbox.isChecked())

            topdown_items_without_samples = len(dialog._topdown_plot.getPlotItem().items)
            distance_items_without_samples = len(dialog._distance_plot.getPlotItem().items)

            dialog._show_sample_points_checkbox.setChecked(True)
            self._app.processEvents()

            self.assertGreater(len(dialog._topdown_plot.getPlotItem().items), topdown_items_without_samples)
            self.assertGreater(len(dialog._distance_plot.getPlotItem().items), distance_items_without_samples)
        finally:
            dialog.close()

    def test_trajectory_view_opens_without_objects_and_exposes_custom_span(self) -> None:
        base_time = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)
        path_times = tuple(base_time + timedelta(days=index) for index in range(3))
        empty_context = KnownObjectHeliocentricContext(
            object_label="Trajectory View",
            resolved_target_name="Earth only",
            earth_target_name="Earth",
            window_start=path_times[0],
            window_end=path_times[-1],
            arc_padding_days=45.0,
            reference_time=base_time,
            object_path_samples=(),
            earth_path_samples=tuple(
                _state_vector(sample_time, 0.1 + (index * 0.01), 1.0 + (index * 0.02), 0.0)
                for index, sample_time in enumerate(path_times)
            ),
            observation_object_samples=(),
            observation_earth_samples=tuple(
                _state_vector(sample_time, 0.1 + (index * 0.01), 1.0 + (index * 0.02), 0.0)
                for index, sample_time in enumerate(path_times[:2])
            ),
            earth_orbital_period_days=365.25,
        )
        dialog = KnownObjectOrbit3DDialog(
            detection=None,
            frame_measurements=(),
            context=empty_context,
            targets=(),
            available_targets=(),
            lookup_exact_target=lambda _identifier: (),
        )
        try:
            self.assertEqual(dialog.windowTitle(), "3D View - Trajectory View")
            self.assertEqual(dialog._context_targets, ())
            self.assertIn("No objects", dialog._summary_text())
            self.assertEqual(len(dialog._timeline_times), 3)
            span_keys = [
                dialog._span_combo.itemData(index)[0]
                for index in range(dialog._span_combo.count())
                if isinstance(dialog._span_combo.itemData(index), tuple)
            ]
            self.assertIn("custom", span_keys)
            custom_index = next(
                index
                for index in range(dialog._span_combo.count())
                if isinstance(dialog._span_combo.itemData(index), tuple)
                and dialog._span_combo.itemData(index)[0] == "custom"
            )
            dialog._span_combo.setCurrentIndex(custom_index)
            self._app.processEvents()
            self.assertEqual(dialog._active_span_key, "custom")
            self.assertFalse(dialog._custom_span_start_input.isHidden())
            self.assertFalse(dialog._custom_span_end_input.isHidden())
            self.assertEqual(dialog._custom_span_start_input.text(), "2026-07-20")
            self.assertEqual(dialog._custom_span_end_input.text(), "2026-07-22")
        finally:
            dialog.close()

    def test_trajectory_view_custom_span_fades_path_edges(self) -> None:
        base_time = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
        path_times = tuple(base_time + timedelta(days=index * 10) for index in range(11))
        context = KnownObjectHeliocentricContext(
            object_label="(1) Ceres",
            resolved_target_name="1",
            earth_target_name="Earth",
            window_start=path_times[0],
            window_end=path_times[-1],
            arc_padding_days=50.0,
            reference_time=base_time + timedelta(days=50),
            object_path_samples=tuple(
                _state_vector(sample_time, 1.0 + (index * 0.05), 0.5, 0.1)
                for index, sample_time in enumerate(path_times)
            ),
            earth_path_samples=tuple(
                _state_vector(sample_time, 0.1, 1.0 + (index * 0.01), 0.0)
                for index, sample_time in enumerate(path_times)
            ),
            # Custom reloads often use window endpoints as the only "observation" times.
            observation_object_samples=(
                _state_vector(path_times[0], 1.0, 0.5, 0.1),
                _state_vector(path_times[-1], 1.5, 0.5, 0.1),
            ),
            observation_earth_samples=(
                _state_vector(path_times[0], 0.1, 1.0, 0.0),
                _state_vector(path_times[-1], 0.1, 1.1, 0.0),
            ),
            object_orbital_period_days=1681.0,
            earth_orbital_period_days=365.25,
        )
        detection = _detection("(1) Ceres", "1", "Asteroid")
        dialog = KnownObjectOrbit3DDialog(
            detection=detection,
            frame_measurements=(
                _frame_measurement(path_times[0], "frame_01.fits"),
                _frame_measurement(path_times[-1], "frame_02.fits"),
            ),
            context=context,
        )
        try:
            dialog._active_span_key = "custom"
            alpha_values = dialog._path_opacity_profile(
                context.object_path_samples,
                peak_alpha=0.98,
                base_alpha=0.20,
            )
            self.assertEqual(len(alpha_values), len(path_times))
            self.assertAlmostEqual(float(alpha_values[0]), 0.20, places=3)
            self.assertAlmostEqual(float(alpha_values[-1]), 0.20, places=3)
            self.assertGreater(float(alpha_values[len(alpha_values) // 2]), 0.9)
        finally:
            dialog.close()

    def test_trajectory_view_button_enabled_without_selected_object(self) -> None:
        window = MainWindow()
        try:
            window._sync_asteroid_focus_controls()
            self.assertTrue(window._asteroid_3d_button.isEnabled())
            window._set_asteroid_detection_busy(True)
            self.assertFalse(window._asteroid_3d_button.isEnabled())
            window._set_asteroid_detection_busy(False)
            self.assertTrue(window._asteroid_3d_button.isEnabled())
        finally:
            window.close()
