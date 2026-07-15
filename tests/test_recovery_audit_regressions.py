from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPointF, Qt
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
    KnownObjectOrbit3DDialog,
    KnownObjectOrbit3DPlannerDialog,
    KnownObjectOrbit3DPlannerRequest,
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
