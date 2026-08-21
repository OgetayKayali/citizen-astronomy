from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import unittest

from PySide6.QtWidgets import QApplication

from photometry_app.core.variable_ephemeris import (
    TonightSchedule,
    TonightScheduleSample,
    VariableEphemerisEvent,
    VsxEphemerisStar,
    compute_variable_ephemeris,
    twilight_marks_from_samples,
)
from photometry_app.ui.variable_ephemeris_dialog import (
    VariableEphemerisDialog,
    _MOON_CURVE_COLOR,
    _SCHEDULE_ALTITUDE_MAX_DEG,
    _STAR_CURVE_COLOR,
    _eclipse_window_fade_weight,
    _interpolate_altitude,
    _mpl_local_time,
)
from astropy.time import Time


def _synthetic_schedule(*, include_star: bool) -> TonightSchedule:
    start = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)
    samples = []
    for index in range(97):
        hours = index * 10 / 60.0
        local = start + timedelta(minutes=10 * index)
        if hours < 8.0:
            sun_alt = 10.0 - hours * 4.0
        else:
            sun_alt = -22.0 + (hours - 8.0) * 4.0
        star_alt = max(0.0, 34.0 - abs(hours - 1.5) * 4.0) if include_star else None
        moon_alt = max(-5.0, 42.0 - abs(hours - 6.0) * 5.0)
        samples.append(
            TonightScheduleSample(
                local=local,
                sun_altitude_deg=sun_alt,
                moon_altitude_deg=moon_alt,
                star_altitude_deg=star_alt,
            )
        )
    marks, dark_start, dark_end = twilight_marks_from_samples(samples)
    return TonightSchedule(
        timezone_name="US/Central",
        latitude_deg=31.546934,
        longitude_deg=-99.38194444,
        start_local=start,
        end_local=start + timedelta(hours=16),
        moon_illumination_percent=52.0,
        samples=tuple(samples),
        marks=marks,
        dark_start_local=dark_start,
        dark_end_local=dark_end,
    )


def _line_colors(dialog: VariableEphemerisDialog) -> dict[str, str]:
    return {
        line.get_label(): str(line.get_color())
        for line in dialog._schedule_panel._axes.lines
        if line.get_label() and not str(line.get_label()).startswith("_")
    }


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class VariableEphemerisDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _ensure_app()

    def test_search_populates_local_times_without_an_image(self) -> None:
        star = VsxEphemerisStar(
            name="RR Lyr",
            oid="18050",
            ra_deg=291.36629,
            dec_deg=42.78436,
            variability_type="RRAB/BL",
            period_days=1.0,
            epoch_hjd=2461041.5,
            max_mag=7.17,
            min_mag=8.14,
            eclipse_duration_hours=2.0,
            source="VSX",
        )
        forecast = compute_variable_ephemeris(
            star,
            timezone_name="America/New_York",
            now=datetime(2026, 1, 1, 22, 0, tzinfo=UTC),
        )
        dialog = VariableEphemerisDialog(timezone_name="America/New_York")
        dialog._handle_lookup_completed(forecast)
        self.assertEqual(dialog._star_name_label.text(), "RR Lyr")
        self.assertEqual(dialog._star_type_label.text(), "RRAB/BL")
        self.assertIn("1 d", dialog._period_label.text())
        self.assertGreater(dialog._events_table.rowCount(), 0)
        self.assertEqual(dialog._events_table.columnCount(), 5)
        self.assertEqual(dialog._events_table.horizontalHeaderItem(2).text(), "Window")
        self.assertEqual(dialog._events_table.horizontalHeaderItem(4).text(), "Observable")
        self.assertTrue(
            any("2026" in (dialog._events_table.item(row, 1).text() or "") for row in range(dialog._events_table.rowCount()))
        )
        self.assertTrue(dialog._verdict_label.text())
        self.assertFalse(dialog._events_table.item(0, 0).background().color().alpha() == 0)
        dialog.close()

    def test_night_rows_are_darker_than_day_rows(self) -> None:
        star = VsxEphemerisStar(
            name="Test",
            oid="1",
            ra_deg=10.0,
            dec_deg=20.0,
            variability_type="RRAB",
            period_days=0.5,
            epoch_hjd=float(Time("2026-01-01T00:00:00", scale="utc").jd),
            max_mag=None,
            min_mag=None,
            eclipse_duration_hours=None,
            source="test",
        )
        forecast = compute_variable_ephemeris(
            star,
            timezone_name="UTC",
            now=datetime(2026, 1, 1, 1, 0, tzinfo=UTC),
        )
        dialog = VariableEphemerisDialog(timezone_name="UTC")
        dialog._handle_lookup_completed(forecast)
        noon_row = next(
            row
            for row in range(dialog._events_table.rowCount())
            if "12:" in (dialog._events_table.item(row, 1).text() or "")
        )
        midnight_row = next(
            row
            for row in range(dialog._events_table.rowCount())
            if "00:" in (dialog._events_table.item(row, 1).text() or "")
        )
        noon_color = dialog._events_table.item(noon_row, 0).background().color()
        midnight_color = dialog._events_table.item(midnight_row, 0).background().color()
        noon_luma = 0.2126 * noon_color.red() + 0.7152 * noon_color.green() + 0.0722 * noon_color.blue()
        midnight_luma = 0.2126 * midnight_color.red() + 0.7152 * midnight_color.green() + 0.0722 * midnight_color.blue()
        self.assertGreater(noon_luma, midnight_luma)
        dialog.close()

    def test_failed_lookup_clears_results(self) -> None:
        dialog = VariableEphemerisDialog(timezone_name="UTC")
        dialog._handle_lookup_failed("No VSX match for 'nope'.")
        self.assertEqual(dialog._star_name_label.text(), "—")
        self.assertEqual(dialog._events_table.rowCount(), 0)
        self.assertIn("No VSX match", dialog._status_label.text())
        self.assertFalse(dialog._schedule_panel._placeholder.isHidden())
        self.assertIn("Observing Latitude", dialog._schedule_panel._placeholder.text())
        dialog.close()

    def test_empty_search_does_not_start_a_worker(self) -> None:
        dialog = VariableEphemerisDialog(timezone_name="UTC")
        dialog._handle_search_clicked()
        self.assertIsNone(dialog._worker)
        self.assertIn("Enter a variable-star name", dialog._status_label.text())
        dialog.close()

    def test_schedule_panel_plots_moon_and_star_curves(self) -> None:
        star = VsxEphemerisStar(
            name="XZ And",
            oid="1",
            ra_deg=25.0,
            dec_deg=40.0,
            variability_type="EA",
            period_days=1.0,
            epoch_hjd=float(Time("2026-08-19T00:00:00", scale="utc").jd),
            max_mag=None,
            min_mag=None,
            eclipse_duration_hours=None,
            source="test",
        )
        forecast = compute_variable_ephemeris(
            star,
            timezone_name="UTC",
            now=datetime(2026, 8, 19, 22, 0, tzinfo=UTC),
        )
        schedule = _synthetic_schedule(include_star=True)
        dialog = VariableEphemerisDialog(timezone_name="US/Central")
        dialog._handle_lookup_completed(replace(forecast, tonight_schedule=schedule, site_configured=True))
        self.assertEqual(dialog._schedule_panel._title.text(), "Tonight's Schedule")
        self.assertIn("US/Central", dialog._schedule_panel._timezone_label.text())
        self.assertIn("31° 32' 49\" N", dialog._schedule_panel._coords_dms_label.text())
        self.assertIn("Moon Illumination: 52%", dialog._schedule_panel._moon_label.text())
        self.assertFalse(dialog._schedule_panel._canvas.isHidden())
        self.assertTrue(dialog._schedule_panel._placeholder.isHidden())
        labels = {text.get_text() for text in dialog._schedule_panel._axes.texts}
        self.assertIn("Astronomical Dawn", labels)
        self.assertIn("Nautical Dawn", labels)
        self.assertIn("Nautical Dusk", labels)
        self.assertIn("Astronomical Dark", labels)
        colors = _line_colors(dialog)
        self.assertEqual(colors["Moon"], _MOON_CURVE_COLOR)
        self.assertEqual(colors["XZ And"], _STAR_CURVE_COLOR)
        self.assertEqual(dialog._schedule_panel._axes.get_ylim(), (0.0, _SCHEDULE_ALTITUDE_MAX_DEG))
        dialog.close()

    def test_schedule_shows_moon_without_searching(self) -> None:
        dialog = VariableEphemerisDialog(timezone_name="US/Central")
        schedule = _synthetic_schedule(include_star=False)
        dialog._site_schedule = schedule
        dialog._schedule_panel.show_schedule(schedule)
        self.assertFalse(dialog._schedule_panel._canvas.isHidden())
        self.assertTrue(dialog._schedule_panel._placeholder.isHidden())
        colors = _line_colors(dialog)
        self.assertEqual(colors["Moon"], _MOON_CURVE_COLOR)
        self.assertNotIn("XZ And", colors)
        self.assertEqual(dialog._schedule_panel._axes.get_ylim(), (0.0, _SCHEDULE_ALTITUDE_MAX_DEG))
        dialog.close()

    def test_failed_lookup_keeps_moon_schedule(self) -> None:
        dialog = VariableEphemerisDialog(timezone_name="US/Central")
        schedule = _synthetic_schedule(include_star=False)
        dialog._site_schedule = schedule
        dialog._schedule_panel.show_schedule(schedule)
        dialog._handle_lookup_failed("No VSX match for 'nope'.")
        self.assertEqual(dialog._events_table.rowCount(), 0)
        colors = _line_colors(dialog)
        self.assertEqual(colors["Moon"], _MOON_CURVE_COLOR)
        self.assertFalse(dialog._schedule_panel._canvas.isHidden())
        dialog.close()

    def test_schedule_hover_reports_moon_and_star_altitudes(self) -> None:
        dialog = VariableEphemerisDialog(timezone_name="US/Central")
        schedule = _synthetic_schedule(include_star=True)
        dialog._schedule_panel.show_schedule(schedule, star_name="XZ And")
        sample = schedule.samples[18]
        dialog._schedule_panel._update_hover(_mpl_local_time(sample.local))
        self.assertTrue(dialog._schedule_panel._hover_vline.get_visible())
        self.assertTrue(dialog._schedule_panel._hover_moon_marker.get_visible())
        self.assertTrue(dialog._schedule_panel._hover_star_marker.get_visible())
        text = dialog._schedule_panel._hover_annot.get_text()
        self.assertIn("Moon", text)
        self.assertIn("XZ And", text)
        self.assertIn(f"{sample.moon_altitude_deg:.0f}°", text)
        self.assertIn(f"{sample.star_altitude_deg:.0f}°", text)
        self.assertIn("21:", text)
        dialog._schedule_panel._hide_hover()
        self.assertFalse(dialog._schedule_panel._hover_vline.get_visible())
        dialog.close()

    def test_schedule_hover_omits_star_when_only_the_moon_is_plotted(self) -> None:
        dialog = VariableEphemerisDialog(timezone_name="US/Central")
        schedule = _synthetic_schedule(include_star=False)
        dialog._schedule_panel.show_schedule(schedule)
        sample = schedule.samples[18]
        dialog._schedule_panel._update_hover(_mpl_local_time(sample.local))
        text = dialog._schedule_panel._hover_annot.get_text()
        self.assertIn("Moon", text)
        self.assertNotIn("XZ And", text)
        self.assertFalse(dialog._schedule_panel._hover_star_marker.get_visible())
        dialog.close()

    def test_schedule_labels_min_max_events_on_the_curve(self) -> None:
        dialog = VariableEphemerisDialog(timezone_name="US/Central")
        schedule = _synthetic_schedule(include_star=True)
        sample = schedule.samples[18]
        event = VariableEphemerisEvent(
            kind="Min I",
            cycle=1,
            utc=sample.local,
            local=sample.local,
            altitude_deg=sample.star_altitude_deg,
            sun_altitude_deg=sample.sun_altitude_deg,
            is_night=True,
            is_up=True,
            observable=True,
        )
        dialog._schedule_panel.show_schedule(schedule, events=[event], star_name="XZ And")
        labels = {text.get_text() for text in dialog._schedule_panel._axes.texts}
        self.assertTrue(any("Min I" in label and "21:" in label for label in labels))
        self.assertGreaterEqual(len(dialog._schedule_panel._axes.collections), 2)
        dialog.close()

    def test_schedule_draws_eclipse_window_and_table_shows_it(self) -> None:
        dialog = VariableEphemerisDialog(timezone_name="UTC")
        schedule = _synthetic_schedule(include_star=True)
        sample = schedule.samples[18]
        event = VariableEphemerisEvent(
            kind="Min I",
            cycle=1,
            utc=sample.local,
            local=sample.local,
            altitude_deg=sample.star_altitude_deg,
            sun_altitude_deg=sample.sun_altitude_deg,
            is_night=True,
            is_up=True,
            observable=True,
            window_start_local=sample.local - timedelta(hours=2),
            window_end_local=sample.local + timedelta(hours=2),
            window_observable=True,
        )
        star = VsxEphemerisStar(
            name="XZ And",
            oid="1",
            ra_deg=25.0,
            dec_deg=40.0,
            variability_type="EA",
            period_days=1.0,
            epoch_hjd=float(Time("2026-08-19T00:00:00", scale="utc").jd),
            max_mag=None,
            min_mag=None,
            eclipse_duration_hours=4.0,
            source="test",
        )
        forecast = compute_variable_ephemeris(star, timezone_name="UTC", now=datetime(2026, 8, 19, 22, 0, tzinfo=UTC))
        dialog._schedule_panel.show_schedule(schedule, events=[], star_name="XZ And")
        images_without_window = len(dialog._schedule_panel._axes.images)
        dialog._handle_lookup_completed(
            replace(forecast, tonight_schedule=schedule, site_configured=True, events=[event])
        )
        self.assertEqual(dialog._events_table.item(0, 2).text(), "19:00–23:00")
        self.assertGreater(len(dialog._schedule_panel._axes.images), images_without_window)
        dialog.close()

    def test_eclipse_window_fade_peaks_at_mid_eclipse(self) -> None:
        start, mid, end = 0.0, 1.0, 2.0
        self.assertAlmostEqual(_eclipse_window_fade_weight(start, start, mid, end), 0.0)
        self.assertAlmostEqual(_eclipse_window_fade_weight(end, start, mid, end), 0.0)
        self.assertAlmostEqual(_eclipse_window_fade_weight(mid, start, mid, end), 1.0)
        self.assertGreater(
            _eclipse_window_fade_weight(1.0, start, mid, end),
            _eclipse_window_fade_weight(0.35, start, mid, end),
        )
        self.assertGreater(
            _eclipse_window_fade_weight(0.5, start, mid, end),
            _eclipse_window_fade_weight(0.15, start, mid, end),
        )
        self.assertGreater(
            _eclipse_window_fade_weight(1.5, start, mid, end),
            _eclipse_window_fade_weight(1.85, start, mid, end),
        )

    def test_hover_altitude_interpolates_between_samples(self) -> None:
        altitude = _interpolate_altitude([0.0, 10.0], [10.0, 20.0], 5.0)
        self.assertEqual(altitude, 15.0)

    def test_schedule_panel_asks_for_site_when_coordinates_are_missing(self) -> None:
        star = VsxEphemerisStar(
            name="RR Lyr",
            oid="18050",
            ra_deg=291.36629,
            dec_deg=42.78436,
            variability_type="RRAB/BL",
            period_days=1.0,
            epoch_hjd=2461041.5,
            max_mag=7.17,
            min_mag=8.14,
            eclipse_duration_hours=2.0,
            source="VSX",
        )
        forecast = compute_variable_ephemeris(
            star,
            timezone_name="America/New_York",
            now=datetime(2026, 1, 1, 22, 0, tzinfo=UTC),
        )
        dialog = VariableEphemerisDialog(timezone_name="America/New_York")
        dialog._handle_lookup_completed(forecast)
        self.assertFalse(forecast.site_configured)
        self.assertIn("Observing Latitude", dialog._schedule_panel._placeholder.text())
        dialog.close()


if __name__ == "__main__":
    unittest.main()
