from __future__ import annotations

import unittest
from datetime import date

from photometry_app.core.observation_orbit import (
    archive_duration_headline,
    archive_span_caption,
    archive_span_years,
    date_from_year_fraction,
    elapsed_years,
    month_activity,
    orbit_display_bounds,
    orbit_integration_level,
    sample_orbit_nights,
    year_fraction,
)


class ObservationOrbitMappingTest(unittest.TestCase):
    def test_january_aligns_every_year_including_leap_years(self) -> None:
        self.assertEqual(year_fraction(date(2023, 1, 1)), 0.0)
        self.assertEqual(year_fraction(date(2024, 1, 1)), 0.0)
        self.assertAlmostEqual(year_fraction(date(2023, 7, 1)), 0.5, places=4)
        self.assertAlmostEqual(year_fraction(date(2024, 7, 1)), 0.5, places=4)
        leap = year_fraction(date(2024, 2, 29))
        self.assertGreater(leap, year_fraction(date(2024, 2, 28)))
        self.assertLess(leap, year_fraction(date(2024, 3, 1)))
        self.assertAlmostEqual(year_fraction(date(2023, 3, 1)), year_fraction(date(2024, 3, 1)), places=6)

    def test_round_trip_year_fraction(self) -> None:
        for day in (date(2024, 1, 1), date(2024, 2, 29), date(2024, 12, 31), date(2025, 6, 15)):
            recovered = date_from_year_fraction(day.year, year_fraction(day))
            self.assertEqual((recovered.year, recovered.month), (day.year, day.month))
            self.assertLessEqual(abs((recovered - day).days), 1)


class ObservationOrbitArchiveTest(unittest.TestCase):
    def test_one_year_archive_is_one_turn(self) -> None:
        orbit = sample_orbit_nights(
            first=date(2024, 6, 12),
            last=date(2024, 8, 20),
            nights=(
                (date(2024, 6, 12), 7200.0, 12),
                (date(2024, 7, 4), 3600.0, 6),
                (date(2024, 8, 20), 5400.0, 9),
            ),
        )
        self.assertIsNotNone(orbit)
        assert orbit is not None
        self.assertLess(orbit.span_years, 1.0)
        self.assertAlmostEqual(orbit.display_years, 1.0, places=2)
        self.assertEqual(orbit.night_count, 3)

    def test_ten_year_archive_has_more_turns_than_one_year(self) -> None:
        one = sample_orbit_nights(
            first=date(2024, 1, 10),
            last=date(2024, 3, 10),
            nights=((date(2024, 1, 10), 3600.0, 4), (date(2024, 3, 10), 1800.0, 2)),
        )
        ten = sample_orbit_nights(
            first=date(2015, 2, 1),
            last=date(2024, 11, 15),
            nights=(
                (date(2015, 2, 1), 3600.0, 4),
                (date(2018, 7, 1), 7200.0, 8),
                (date(2024, 11, 15), 1800.0, 2),
            ),
        )
        self.assertIsNotNone(one)
        self.assertIsNotNone(ten)
        assert one is not None and ten is not None
        self.assertGreater(ten.display_years, one.display_years + 7)
        self.assertEqual(len(ten.days), 3)
        self.assertEqual(len(one.days), 2)

    def test_empty_years_remain_in_the_geometry(self) -> None:
        orbit = sample_orbit_nights(
            first=date(2018, 3, 4),
            last=date(2023, 1, 9),
            nights=((date(2018, 3, 4), 2400.0, 3), (date(2023, 1, 9), 4800.0, 5)),
        )
        self.assertIsNotNone(orbit)
        assert orbit is not None
        years = {item.observation_date.year for item in orbit.days}
        self.assertEqual(years, {2018, 2023})
        self.assertGreaterEqual(orbit.end_date.year - orbit.start_date.year, 4)
        self.assertGreater(orbit.display_years, 4.5)
        self.assertGreater(orbit.span_years, 4.5)

    def test_single_night_still_builds_a_full_turn(self) -> None:
        orbit = sample_orbit_nights(
            first=date(2021, 12, 31),
            last=date(2021, 12, 31),
            nights=((date(2021, 12, 31), 900.0, 1),),
        )
        self.assertIsNotNone(orbit)
        assert orbit is not None
        self.assertEqual(orbit.night_count, 1)
        self.assertEqual(len(orbit.days), 1)
        self.assertAlmostEqual(orbit.display_years, 1.0, places=2)

    def test_seasonality_ring_prefers_winter_nights(self) -> None:
        nights = tuple(
            (date(2020 + year, month, 10), 3600.0, 4)
            for year in range(5)
            for month in (1, 12)
        ) + tuple((date(2020 + year, 7, 10), 300.0, 1) for year in range(5))
        orbit = sample_orbit_nights(first=date(2020, 1, 10), last=date(2024, 12, 10), nights=nights)
        self.assertIsNotNone(orbit)
        assert orbit is not None
        winter = sum(item.exposure_seconds for item in orbit.season_bins if item.year_fraction < 0.12 or item.year_fraction > 0.88)
        summer = sum(item.exposure_seconds for item in orbit.season_bins if 0.45 < item.year_fraction < 0.62)
        self.assertGreater(winter, summer)

    def test_mid_year_start_and_end_keep_elapsed_time(self) -> None:
        start, end = orbit_display_bounds(date(2019, 11, 20), date(2021, 2, 3))
        self.assertEqual(start, date(2019, 11, 20))
        self.assertEqual(end, date(2021, 2, 3))
        self.assertGreater(archive_span_years(start, end), 1.1)

    def test_very_long_archives_aggregate_inner_turns(self) -> None:
        nights = tuple((date(year, 6, 1), 1800.0, 2) for year in range(1995, 2026, 2))
        orbit = sample_orbit_nights(first=date(1995, 6, 1), last=date(2025, 6, 1), nights=nights)
        self.assertIsNotNone(orbit)
        assert orbit is not None
        self.assertTrue(orbit.uses_weekly_inner)
        self.assertLessEqual(orbit.display_years, 40.1)
        self.assertLess(len(orbit.days), 31 * 366)


class ObservationHistoryCaptionTest(unittest.TestCase):
    def test_archive_span_caption_uses_months_under_one_year(self) -> None:
        self.assertEqual(archive_span_caption(0, 2.0), "No dated imaging nights yet")
        self.assertEqual(archive_span_caption(1, 0.0), "1 imaging night across 1 month")
        self.assertEqual(archive_span_caption(48, 0.67), "48 imaging nights across 8 months")
        self.assertEqual(archive_span_caption(357, 2.3), "357 imaging nights across 2.3 years")
        self.assertEqual(archive_span_caption(12, 1.0), "12 imaging nights across 1 year")
        self.assertEqual(archive_duration_headline(2.3), "2.3 YEARS")
        self.assertEqual(archive_duration_headline(1.0), "1 YEAR")
        self.assertEqual(archive_duration_headline(0.67), "8 MONTHS")


class ObservationOrbitIntegrationScaleTest(unittest.TestCase):
    def test_night_level_uses_hours_not_relative_brightness(self) -> None:
        self.assertEqual(orbit_integration_level(0.0), 0)
        self.assertEqual(orbit_integration_level(3600.0), 1)
        self.assertEqual(orbit_integration_level(3 * 3600.0), 2)
        self.assertEqual(orbit_integration_level(6 * 3600.0), 3)
        self.assertEqual(orbit_integration_level(10 * 3600.0), 4)

    def test_month_activity_aggregates_one_month(self) -> None:
        orbit = sample_orbit_nights(
            first=date(2025, 8, 1),
            last=date(2025, 8, 20),
            nights=(
                (date(2025, 8, 12), 3600.0, 4),
                (date(2025, 8, 13), 7200.0, 8),
                (date(2025, 9, 1), 1800.0, 2),
            ),
        )
        self.assertIsNotNone(orbit)
        assert orbit is not None
        nights, seconds = month_activity(orbit, year=2025, month=8)
        self.assertEqual(nights, 2)
        self.assertEqual(seconds, 10800.0)


class ObservationOrbitMarkGeometryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def test_consecutive_nights_stay_separated(self) -> None:
        from photometry_app.ui.observation_orbit_widget import (
            ObservationOrbitWidget,
            _DAY_OCCUPANCY,
            _TRACK_FILL,
        )

        self.assertGreaterEqual(_TRACK_FILL, 1.10)
        self.assertLessEqual(_TRACK_FILL, 1.22)
        self.assertGreaterEqual(_DAY_OCCUPANCY, 0.88)
        self.assertLessEqual(_DAY_OCCUPANCY, 0.94)

        orbit = sample_orbit_nights(
            first=date(2024, 6, 12),
            last=date(2024, 6, 20),
            nights=(
                (date(2024, 6, 12), 3600.0, 4),
                (date(2024, 6, 13), 7200.0, 8),
                (date(2024, 6, 20), 1800.0, 2),
            ),
        )
        self.assertIsNotNone(orbit)
        assert orbit is not None
        widget = ObservationOrbitWidget()
        widget.resize(640, 640)
        widget.set_orbit(orbit)
        widget._layout_metrics(640, 640)
        first, second, isolated = orbit.days
        path_a = widget._day_path(first)
        path_b = widget._day_path(second)
        path_c = widget._day_path(isolated)
        self.assertFalse(path_a.isEmpty())
        self.assertFalse(path_a.intersects(path_b))
        self.assertFalse(path_a.intersects(path_c))
        mid_x, mid_y = widget._polar(
            widget._r0 + first.elapsed_years * widget._pitch,
            widget._day_mid_fraction(first),
        )
        self.assertIs(widget._hit_day(mid_x, mid_y), first)
        export = widget.render_to_image()
        self.assertGreaterEqual(export.devicePixelRatio(), 2.0)
        self.assertGreater(export.width(), 640)
        widget.grab()
        cache = widget._cache
        self.assertIsNotNone(cache)
        self.assertAlmostEqual(cache.devicePixelRatio(), 1.0)
        self.assertEqual(cache.width(), int(round(640 * widget._device_pixel_ratio())))
        widget._hovered = first
        widget._hovered_year = None
        widget.update()
        widget.grab()
        self.assertIsNone(widget._hovered_year)
        title, lines = widget._center_copy()
        self.assertIn("2024", title)
        self.assertTrue(any("integration" in line for line in lines))
        widget._hovered = None
        headline, summary = widget._center_copy()
        self.assertIn("NIGHT", headline)
        self.assertEqual(len(summary), 2)
        widget.deleteLater()

    def test_month_hover_follows_calendar_angle(self) -> None:
        from photometry_app.ui.observation_orbit_widget import ObservationOrbitWidget

        orbit = sample_orbit_nights(
            first=date(2024, 6, 12),
            last=date(2026, 8, 20),
            nights=(
                (date(2024, 6, 12), 3600.0, 4),
                (date(2025, 8, 15), 7200.0, 8),
                (date(2026, 8, 20), 1800.0, 2),
            ),
        )
        self.assertIsNotNone(orbit)
        assert orbit is not None
        widget = ObservationOrbitWidget()
        widget.resize(640, 640)
        widget.set_orbit(orbit)
        widget._layout_metrics(640, 640)
        august = date(2025, 8, 15)
        february_frac = year_fraction(date(2025, 2, 15))
        x, y = widget._polar(
            widget._r0 + elapsed_years(august, orbit.start_date) * widget._pitch,
            february_frac,
        )
        _day, _year, month, _pattern = widget._hit_focus(x, y)
        self.assertEqual(month, (2025, 2))
        widget.deleteLater()


class ThemeFilterPaletteTest(unittest.TestCase):
    def test_theme_filter_colors_stay_in_accent_family(self) -> None:
        from PySide6.QtGui import QColor

        from photometry_app.ui.astro_tools_panel import theme_filter_colors

        accent = QColor.fromHslF(0.97, 0.55, 0.42)
        surface = QColor.fromHslF(0.97, 0.20, 0.12)
        colors = theme_filter_colors(accent, surface, 6)
        self.assertEqual(len(colors), 6)
        for color in colors:
            _hue, saturation, lightness, _alpha = color.getHslF()
            self.assertLessEqual(saturation, 0.52)
            hue_delta = min(abs(color.hueF() - accent.hueF()), 1.0 - abs(color.hueF() - accent.hueF()))
            self.assertLess(hue_delta, 0.18)
            self.assertGreaterEqual(abs(lightness - surface.lightnessF()), 0.12)


if __name__ == "__main__":
    unittest.main()
