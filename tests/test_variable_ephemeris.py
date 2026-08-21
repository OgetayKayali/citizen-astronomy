from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
import unittest

from astropy.time import Time

from photometry_app.core.variable_ephemeris import (
    TonightScheduleSample,
    VariableEphemerisLookupError,
    VsxEphemerisStar,
    compute_site_tonight_schedule,
    compute_variable_ephemeris,
    current_ephemeris_phase,
    daylight_sky_factor,
    ephemeris_sky_rgb,
    event_kinds_for_variability_type,
    format_site_coordinate_lines,
    lookup_vsx_star_by_name,
    parse_vsx_object_xml,
    twilight_marks_from_samples,
)


_RR_LYR_XML = """
<VSXObject>
  <Name>RR Lyr</Name>
  <AUID>000-BCG-719</AUID>
  <RA2000>291.36629</RA2000>
  <Declination2000>42.78436</Declination2000>
  <VariabilityType>RRAB/BL</VariabilityType>
  <Period>0.566775</Period>
  <Epoch>2459422.522</Epoch>
  <RiseDuration>16</RiseDuration>
  <MaxMag>7.17 V</MaxMag>
  <MinMag>8.14 V</MinMag>
  <OID>18050</OID>
</VSXObject>
"""

_ALGOL_XML = """
<VSXObject>
  <Name>bet Per</Name>
  <OID>26202</OID>
  <RA2000>47.04221</RA2000>
  <Declination2000>40.95567</Declination2000>
  <VariabilityType>EA/SD</VariabilityType>
  <Period>2.867343</Period>
  <Epoch>2457675.72</Epoch>
  <EclipseDuration>14</EclipseDuration>
  <MaxMag>2.09 V</MaxMag>
  <MinMag>3.30 V</MinMag>
</VSXObject>
"""

_W_UMA_XML = """
<VSXObject>
  <Name>W UMa</Name>
  <RA2000>145.93946</RA2000>
  <Declination2000>55.95253</Declination2000>
  <VariabilityType>EW/KW</VariabilityType>
  <Period>0.3336334</Period>
  <Epoch>2453761.977</Epoch>
  <MaxMag>7.75 V</MaxMag>
  <MinMag>8.48 V</MinMag>
  <OID>37110</OID>
</VSXObject>
"""


def _star(
    *,
    variability_type: str = "RRAB",
    period_days: float | None = 1.0,
    epoch_hjd: float | None = None,
    ra_deg: float = 10.0,
    dec_deg: float = 20.0,
    eclipse_duration_hours: float | None = None,
) -> VsxEphemerisStar:
    if epoch_hjd is None:
        epoch_hjd = float(Time("2026-01-01T00:00:00", scale="utc").jd)
    return VsxEphemerisStar(
        name="Test Star",
        oid="1",
        ra_deg=ra_deg,
        dec_deg=dec_deg,
        variability_type=variability_type,
        period_days=period_days,
        epoch_hjd=epoch_hjd,
        max_mag=7.0,
        min_mag=8.0,
        eclipse_duration_hours=eclipse_duration_hours,
        source="test",
    )


class VariableEphemerisTests(unittest.TestCase):
    def test_parse_vsx_object_xml_reads_epoch_period_and_percent_duration(self) -> None:
        star = parse_vsx_object_xml(_RR_LYR_XML)
        self.assertIsNotNone(star)
        assert star is not None
        self.assertEqual(star.name, "RR Lyr")
        self.assertEqual(star.oid, "18050")
        self.assertEqual(star.variability_type, "RRAB/BL")
        self.assertAlmostEqual(star.period_days or 0.0, 0.566775)
        self.assertAlmostEqual(star.epoch_hjd or 0.0, 2459422.522)
        self.assertAlmostEqual(star.max_mag or 0.0, 7.17)
        self.assertAlmostEqual(star.min_mag or 0.0, 8.14)
        self.assertIsNone(star.eclipse_duration_hours)
        forecast = compute_variable_ephemeris(
            star,
            timezone_name="UTC",
            now=datetime(2026, 8, 20, 6, 0, tzinfo=UTC),
        )
        self.assertTrue(forecast.events)
        self.assertTrue(all(event.window_start_local is None for event in forecast.events))

    def test_parse_vsx_object_xml_reads_eclipse_duration_for_ea_stars(self) -> None:
        star = parse_vsx_object_xml(_ALGOL_XML)
        self.assertIsNotNone(star)
        assert star is not None
        self.assertEqual(star.name, "bet Per")
        self.assertEqual(star.variability_type, "EA/SD")
        self.assertAlmostEqual(star.period_days or 0.0, 2.867343)
        self.assertAlmostEqual(
            star.eclipse_duration_hours or 0.0,
            0.14 * 2.867343 * 24.0,
            places=5,
        )
        forecast = compute_variable_ephemeris(
            star,
            timezone_name="UTC",
            now=datetime(2026, 8, 20, 6, 0, tzinfo=UTC),
        )
        min_ii = next(event for event in forecast.events if event.kind == "Min II")
        self.assertIsNotNone(min_ii.window_start_local)
        self.assertIsNotNone(min_ii.window_end_local)

    def test_eclipsing_types_use_minima_and_pulsators_use_maxima(self) -> None:
        self.assertEqual(event_kinds_for_variability_type("EW/KW"), (("Min I", 0.0), ("Min II", 0.5)))
        self.assertEqual(event_kinds_for_variability_type("EA"), (("Min I", 0.0), ("Min II", 0.5)))
        self.assertEqual(event_kinds_for_variability_type("RRAB/BL"), (("Max", 0.0),))
        self.assertEqual(event_kinds_for_variability_type("DCEP"), (("Max", 0.0),))

    def test_next_maximum_is_computed_in_the_requested_timezone(self) -> None:
        star = _star(period_days=1.0)
        now = datetime(2026, 1, 1, 6, 0, tzinfo=UTC)
        forecast = compute_variable_ephemeris(star, timezone_name="America/New_York", now=now)
        self.assertAlmostEqual(forecast.current_phase or -1.0, 0.25, places=5)
        next_max = next(event for event in forecast.events if event.utc > now)
        self.assertEqual(next_max.kind, "Max")
        self.assertEqual(next_max.utc, datetime(2026, 1, 2, 0, 0, tzinfo=UTC))
        self.assertEqual(next_max.local.tzinfo.key, "America/New_York")
        self.assertEqual(next_max.local.hour, 19)
        self.assertEqual(next_max.local.day, 1)

    def test_utc_offset_timezone_is_applied_instead_of_falling_back_to_utc(self) -> None:
        star = _star(period_days=1.0)
        now = datetime(2026, 1, 1, 6, 0, tzinfo=UTC)
        forecast = compute_variable_ephemeris(star, timezone_name="UTC-05:00", now=now)
        next_max = next(event for event in forecast.events if event.utc > now)
        self.assertEqual(forecast.timezone_name, "UTC-05:00")
        self.assertEqual(next_max.utc, datetime(2026, 1, 2, 0, 0, tzinfo=UTC))
        self.assertEqual(next_max.local.hour, 19)
        self.assertEqual(next_max.local.day, 1)

    def test_next_secondary_minimum_is_half_a_period_after_epoch(self) -> None:
        star = _star(variability_type="EW/KW", period_days=1.0)
        now = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)
        forecast = compute_variable_ephemeris(star, timezone_name="UTC", now=now)
        min_ii = next(event for event in forecast.events if event.kind == "Min II" and event.utc > now)
        next_min_i = next(event for event in forecast.events if event.kind == "Min I" and event.utc > now)
        self.assertEqual(min_ii.utc, datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
        self.assertEqual(next_min_i.utc, datetime(2026, 1, 2, 0, 0, tzinfo=UTC))

    def test_tonight_uses_the_local_evening_window_without_a_site(self) -> None:
        star = _star(period_days=1.0)
        now = datetime(2026, 1, 1, 22, 0, tzinfo=UTC)
        forecast = compute_variable_ephemeris(star, timezone_name="UTC", now=now)
        self.assertTrue(forecast.tonight_events)
        self.assertEqual(forecast.tonight_events[0].kind, "Max")
        self.assertEqual(forecast.tonight_events[0].local.hour, 0)
        self.assertIn("Tonight", forecast.summary)
        self.assertFalse(forecast.site_configured)

    def test_late_night_event_on_a_later_date_is_still_dark_and_observable(self) -> None:
        star = _star(period_days=2.0)
        now = datetime(2026, 1, 1, 15, 0, tzinfo=UTC)
        forecast = compute_variable_ephemeris(star, timezone_name="UTC", now=now)
        next_event = next(event for event in forecast.events if event.utc > now)
        self.assertEqual(next_event.utc, datetime(2026, 1, 3, 0, 0, tzinfo=UTC))
        self.assertTrue(next_event.is_night)
        self.assertTrue(next_event.observable)
        self.assertEqual(forecast.tonight_events, [])
        self.assertIn("No min/max tonight", forecast.summary)

    def test_afternoon_event_is_not_dark(self) -> None:
        star = _star(period_days=1.0, epoch_hjd=float(Time("2026-01-01T12:00:00", scale="utc").jd))
        now = datetime(2026, 1, 1, 15, 0, tzinfo=UTC)
        forecast = compute_variable_ephemeris(star, timezone_name="UTC", now=now)
        first = next(event for event in forecast.events if event.utc >= now)
        self.assertEqual(first.utc.hour, 12)
        self.assertFalse(first.is_night)
        self.assertFalse(first.observable)

    def test_daytime_event_is_not_treated_as_observable_tonight(self) -> None:
        star = _star(period_days=2.0)
        now = datetime(2026, 1, 1, 15, 0, tzinfo=UTC)
        forecast = compute_variable_ephemeris(star, timezone_name="UTC", now=now)
        self.assertEqual(forecast.tonight_events, [])
        self.assertEqual(forecast.tonight_observable, [])
        self.assertIn("No min/max tonight", forecast.summary)

    def test_missing_period_explains_that_times_cannot_be_calculated(self) -> None:
        star = _star(period_days=None)
        forecast = compute_variable_ephemeris(star, timezone_name="UTC", now=datetime(2026, 1, 1, 22, 0, tzinfo=UTC))
        self.assertEqual(forecast.events, [])
        self.assertIn("Period or Epoch is missing", forecast.summary)

    def test_current_phase_wraps_through_the_cycle(self) -> None:
        star = _star(period_days=2.0)
        now = datetime(2026, 1, 4, 0, 0, tzinfo=UTC)
        self.assertAlmostEqual(current_ephemeris_phase(star, now) or -1.0, 0.5, places=5)

    def test_lookup_uses_vsx_api_before_vizier(self) -> None:
        star = parse_vsx_object_xml(_W_UMA_XML)
        with patch(
            "photometry_app.core.variable_ephemeris._lookup_vsx_object_api",
            return_value=star,
        ) as api_lookup, patch(
            "photometry_app.core.variable_ephemeris._lookup_vsx_vizier_by_name",
        ) as vizier_lookup:
            resolved = lookup_vsx_star_by_name("W UMa")
        self.assertEqual(resolved.name, "W UMa")
        self.assertEqual(resolved.variability_type, "EW/KW")
        self.assertIsNone(resolved.eclipse_duration_hours)
        api_lookup.assert_called_once()
        vizier_lookup.assert_not_called()

    def test_lookup_raises_when_the_name_is_unknown(self) -> None:
        with patch("photometry_app.core.variable_ephemeris._lookup_vsx_object_api", return_value=None), patch(
            "photometry_app.core.variable_ephemeris._lookup_vsx_vizier_by_name",
            return_value=None,
        ), patch("photometry_app.core.variable_ephemeris._lookup_vsx_via_simbad", return_value=None):
            with self.assertRaises(VariableEphemerisLookupError):
                lookup_vsx_star_by_name("not a real star")

    def test_empty_name_is_rejected(self) -> None:
        with self.assertRaises(VariableEphemerisLookupError):
            lookup_vsx_star_by_name("   ")

    def test_site_marks_an_event_observable_only_when_dark_and_up(self) -> None:
        star = _star(period_days=1.0)
        now = datetime(2026, 1, 1, 22, 0, tzinfo=UTC)
        night_start = datetime(2026, 1, 1, 18, 0, tzinfo=UTC)
        night_end = datetime(2026, 1, 2, 6, 0, tzinfo=UTC)
        with patch(
            "photometry_app.core.variable_ephemeris._observer_location",
            return_value=object(),
        ), patch(
            "photometry_app.core.variable_ephemeris._local_night_window",
            return_value=(night_start, night_end),
        ), patch(
            "photometry_app.core.variable_ephemeris._star_altitude_deg",
            return_value=45.0,
        ), patch(
            "photometry_app.core.variable_ephemeris._sun_altitude_deg",
            return_value=-20.0,
        ):
            forecast = compute_variable_ephemeris(
                star,
                timezone_name="UTC",
                latitude_deg=40.0,
                longitude_deg=-75.0,
                now=now,
            )
        self.assertTrue(forecast.site_configured)
        self.assertTrue(forecast.tonight_observable)
        self.assertIn("alt 45°", forecast.summary)

    def test_low_altitude_event_is_not_observable(self) -> None:
        star = _star(period_days=1.0)
        now = datetime(2026, 1, 1, 22, 0, tzinfo=UTC)
        night_start = datetime(2026, 1, 1, 18, 0, tzinfo=UTC)
        night_end = datetime(2026, 1, 2, 6, 0, tzinfo=UTC)
        with patch(
            "photometry_app.core.variable_ephemeris._observer_location",
            return_value=object(),
        ), patch(
            "photometry_app.core.variable_ephemeris._local_night_window",
            return_value=(night_start, night_end),
        ), patch(
            "photometry_app.core.variable_ephemeris._star_altitude_deg",
            return_value=3.0,
        ), patch(
            "photometry_app.core.variable_ephemeris._sun_altitude_deg",
            return_value=-20.0,
        ):
            forecast = compute_variable_ephemeris(
                star,
                timezone_name="UTC",
                latitude_deg=40.0,
                longitude_deg=-75.0,
                now=now,
            )
        self.assertTrue(forecast.tonight_events)
        self.assertEqual(forecast.tonight_observable, [])
        self.assertIn("too low", forecast.summary)

    def test_settings_min_altitude_controls_whether_an_event_is_up(self) -> None:
        star = _star(period_days=1.0)
        now = datetime(2026, 1, 1, 22, 0, tzinfo=UTC)
        night_start = datetime(2026, 1, 1, 18, 0, tzinfo=UTC)
        night_end = datetime(2026, 1, 2, 6, 0, tzinfo=UTC)
        with patch(
            "photometry_app.core.variable_ephemeris._observer_location",
            return_value=object(),
        ), patch(
            "photometry_app.core.variable_ephemeris._local_night_window",
            return_value=(night_start, night_end),
        ), patch(
            "photometry_app.core.variable_ephemeris._star_altitude_deg",
            return_value=8.0,
        ), patch(
            "photometry_app.core.variable_ephemeris._sun_altitude_deg",
            return_value=-20.0,
        ):
            at_default = compute_variable_ephemeris(
                star,
                timezone_name="UTC",
                latitude_deg=40.0,
                longitude_deg=-75.0,
                now=now,
                min_altitude_deg=5.0,
            )
            raised = compute_variable_ephemeris(
                star,
                timezone_name="UTC",
                latitude_deg=40.0,
                longitude_deg=-75.0,
                now=now,
                min_altitude_deg=10.0,
            )
        self.assertTrue(at_default.tonight_observable)
        self.assertEqual(raised.tonight_observable, [])
        self.assertIn("too low", raised.summary)

    def test_recently_passed_event_is_kept_for_two_days(self) -> None:
        star = _star(period_days=1.0)
        now = datetime(2026, 1, 2, 0, 30, tzinfo=UTC)
        forecast = compute_variable_ephemeris(star, timezone_name="UTC", now=now)
        times = [event.utc for event in forecast.events]
        self.assertIn(datetime(2026, 1, 1, 0, 0, tzinfo=UTC), times)
        self.assertIn(datetime(2026, 1, 2, 0, 0, tzinfo=UTC), times)
        self.assertIn(datetime(2026, 1, 3, 0, 0, tzinfo=UTC), times)
        self.assertNotIn(datetime(2025, 12, 31, 0, 0, tzinfo=UTC), times)
        self.assertTrue(forecast.tonight_events)
        self.assertEqual(forecast.tonight_events[0].utc, datetime(2026, 1, 2, 0, 0, tzinfo=UTC))

    def test_eclipsing_duration_adds_a_window_around_minima(self) -> None:
        star = _star(variability_type="EA", period_days=1.0, eclipse_duration_hours=4.0)
        now = datetime(2026, 1, 1, 22, 0, tzinfo=UTC)
        forecast = compute_variable_ephemeris(star, timezone_name="UTC", now=now)
        min_i = next(event for event in forecast.events if event.kind == "Min I" and event.utc == datetime(2026, 1, 2, 0, 0, tzinfo=UTC))
        self.assertEqual(min_i.window_start_local, datetime(2026, 1, 1, 22, 0, tzinfo=UTC))
        self.assertEqual(min_i.window_end_local, datetime(2026, 1, 2, 2, 0, tzinfo=UTC))
        self.assertTrue(min_i.window_observable)
        self.assertIn(min_i, forecast.tonight_events)
        self.assertIn("eclipse 22:00–02:00", forecast.summary)
        self.assertIn("mid 00:00", forecast.summary)

    def test_pulsators_do_not_get_an_eclipse_window_even_with_duration(self) -> None:
        star = _star(variability_type="RRAB", period_days=1.0, eclipse_duration_hours=4.0)
        now = datetime(2026, 1, 1, 22, 0, tzinfo=UTC)
        forecast = compute_variable_ephemeris(star, timezone_name="UTC", now=now)
        maximum = next(event for event in forecast.events if event.kind == "Max" and event.utc == datetime(2026, 1, 2, 0, 0, tzinfo=UTC))
        self.assertIsNone(maximum.window_start_local)
        self.assertIsNone(maximum.window_end_local)
        self.assertNotIn("eclipse", forecast.summary)

    def test_eclipsing_star_without_duration_keeps_a_midpoint_only(self) -> None:
        star = _star(variability_type="EA", period_days=1.0, eclipse_duration_hours=None)
        now = datetime(2026, 1, 1, 22, 0, tzinfo=UTC)
        forecast = compute_variable_ephemeris(star, timezone_name="UTC", now=now)
        min_i = next(event for event in forecast.events if event.kind == "Min I" and event.utc == datetime(2026, 1, 2, 0, 0, tzinfo=UTC))
        self.assertIsNone(min_i.window_start_local)
        self.assertIn("Min I at 00:00", forecast.summary)

    def test_daylight_sky_factor_is_brighter_for_high_sun_than_night(self) -> None:
        self.assertGreater(daylight_sky_factor(sun_altitude_deg=55.0), 0.95)
        self.assertLess(daylight_sky_factor(sun_altitude_deg=-20.0), 0.05)
        self.assertGreater(
            daylight_sky_factor(sun_altitude_deg=25.0),
            daylight_sky_factor(sun_altitude_deg=8.0),
        )
        self.assertGreater(
            daylight_sky_factor(sun_altitude_deg=8.0),
            daylight_sky_factor(sun_altitude_deg=0.0),
        )
        self.assertGreater(
            daylight_sky_factor(sun_altitude_deg=0.0),
            daylight_sky_factor(sun_altitude_deg=-12.0),
        )
        noon = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        afternoon = datetime(2026, 1, 1, 16, 0, tzinfo=UTC)
        midnight = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        self.assertGreater(
            daylight_sky_factor(sun_altitude_deg=None, local=noon),
            daylight_sky_factor(sun_altitude_deg=None, local=afternoon),
        )
        self.assertGreater(
            daylight_sky_factor(sun_altitude_deg=None, local=afternoon),
            daylight_sky_factor(sun_altitude_deg=None, local=midnight),
        )

    def test_sky_rgb_gradient_runs_from_gold_day_to_navy_night(self) -> None:
        day = ephemeris_sky_rgb(1.0)
        night = ephemeris_sky_rgb(0.0)
        dusk = ephemeris_sky_rgb(0.5)
        afternoon = ephemeris_sky_rgb(daylight_sky_factor(sun_altitude_deg=25.0))
        self.assertGreater(day[0] + day[1], night[0] + night[1])
        self.assertGreater(night[2], night[1])
        self.assertGreater(dusk[0], dusk[2])
        self.assertNotEqual(day, afternoon)

    def test_twilight_marks_include_dusk_dawn_and_astronomical_dark(self) -> None:
        start = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)
        samples = []
        for index in range(97):
            hours = index * 10 / 60.0
            local = start + timedelta(minutes=10 * index)
            if hours < 8.0:
                sun_alt = 10.0 - hours * 4.0
            else:
                sun_alt = -22.0 + (hours - 8.0) * 4.0
            samples.append(
                TonightScheduleSample(
                    local=local,
                    sun_altitude_deg=sun_alt,
                    moon_altitude_deg=18.0,
                    star_altitude_deg=30.0,
                )
            )
        marks, dark_start, dark_end = twilight_marks_from_samples(samples)
        names = {mark.name: mark.local for mark in marks}
        self.assertIn("Nautical Dusk", names)
        self.assertIn("Astronomical Dawn", names)
        self.assertIn("Nautical Dawn", names)
        self.assertIn("Astronomical Dark", names)
        self.assertEqual(names["Nautical Dusk"].hour, 23)
        self.assertEqual(names["Nautical Dusk"].minute, 30)
        self.assertIsNotNone(dark_start)
        self.assertIsNotNone(dark_end)
        self.assertLess(dark_start, names["Astronomical Dark"])
        self.assertLess(names["Astronomical Dark"], dark_end)

    def test_site_coordinates_format_dms_and_decimal(self) -> None:
        dms, decimal = format_site_coordinate_lines(31.546934, -99.38194444)
        self.assertEqual(dms, "31° 32' 49\" N, 99° 22' 55\" W")
        self.assertEqual(decimal, "31.546934, -99.381944")

    def test_site_schedule_can_be_built_without_a_star(self) -> None:
        self.assertIsNone(compute_site_tonight_schedule(timezone_name="UTC"))
        start = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)
        samples = tuple(
            TonightScheduleSample(
                local=start + timedelta(minutes=10 * index),
                sun_altitude_deg=-20.0,
                moon_altitude_deg=40.0 - index * 0.4,
                star_altitude_deg=None,
            )
            for index in range(5)
        )
        with patch(
            "photometry_app.core.variable_ephemeris._observer_location",
            return_value=object(),
        ), patch(
            "photometry_app.core.variable_ephemeris._sample_tonight_schedule",
            return_value=samples,
        ):
            schedule = compute_site_tonight_schedule(
                timezone_name="UTC",
                latitude_deg=31.546934,
                longitude_deg=-99.38194444,
                now=datetime(2026, 8, 19, 22, 0, tzinfo=UTC),
            )
        self.assertIsNotNone(schedule)
        assert schedule is not None
        self.assertTrue(all(sample.star_altitude_deg is None for sample in schedule.samples))
        self.assertEqual(schedule.samples[0].moon_altitude_deg, 40.0)


if __name__ == "__main__":
    unittest.main()
