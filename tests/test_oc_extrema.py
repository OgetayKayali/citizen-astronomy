from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from astropy.time import Time
import numpy as np

from photometry_app.core.models import LightCurvePoint, LightCurveSeries
from photometry_app.core.oc_extrema import (
    EXTREMUM_MAXIMUM,
    EXTREMUM_MINIMUM,
    ExtremumRecord,
    OcStarLog,
    apply_star_name,
    compute_oc_residuals,
    import_photometry_table,
    make_star_key,
    mark_extremum_near_jd,
    mark_series_extrema,
    oc_log_from_payload,
    oc_log_to_payload,
    series_matches_star,
    upsert_records,
)


def _pulse_series(start: datetime | None = None) -> LightCurveSeries:
    origin = start or datetime(2026, 3, 16, 1, 0, 0)
    points: list[LightCurvePoint] = []
    for index in range(25):
        phase = index / 24.0
        magnitude = 10.8 + (0.45 * ((1.0 - abs((phase - 0.35) * 2.0 - 1.0)) ** 2))
        points.append(
            LightCurvePoint(
                observation_time=origin + timedelta(minutes=index * 8),
                file_path=Path(f"frame_{index:02d}.fits"),
                differential_magnitude=magnitude,
                instrumental_magnitude=None,
                flux=None,
                flux_error=None,
                standard_magnitude=magnitude,
                standard_magnitude_error=0.01,
                differential_magnitude_error=0.01,
            )
        )
    return LightCurveSeries(
        object_name="Night1",
        source_id="vsx-dyher",
        source_name="DY Her",
        filter_name="V",
        points=points,
    )


class OcExtremaTest(unittest.TestCase):
    def test_star_key_prefers_source_id(self) -> None:
        self.assertEqual(make_star_key("vsx-1", "DY Her"), "vsx-1")
        self.assertEqual(make_star_key("", "DY  Her"), "dy her")

    def test_apply_star_name_updates_log_and_records_without_changing_key(self) -> None:
        log = OcStarLog(
            star_key="manual-target",
            star_name="Target",
            records=[ExtremumRecord("a", "Target", "", "Night1", EXTREMUM_MAXIMUM, 2460000.1)],
        )
        updated = apply_star_name(log, "  DY Her  ")
        self.assertEqual(updated.star_key, "manual-target")
        self.assertEqual(updated.star_name, "DY Her")
        self.assertEqual(updated.records[0].star_name, "DY Her")

    def test_mark_series_extrema_finds_max_and_min(self) -> None:
        series = _pulse_series()
        records = mark_series_extrema(series, y_axis_mode="standard_magnitude")
        kinds = {record.kind for record in records}
        self.assertIn(EXTREMUM_MAXIMUM, kinds)
        self.assertIn(EXTREMUM_MINIMUM, kinds)
        maximum = next(record for record in records if record.kind == EXTREMUM_MAXIMUM)
        minimum = next(record for record in records if record.kind == EXTREMUM_MINIMUM)
        self.assertLess(maximum.magnitude or 99.0, minimum.magnitude or 0.0)
        self.assertGreater(maximum.jd, 0.0)
        self.assertIsNotNone(maximum.amplitude)
        self.assertGreater(maximum.amplitude or 0.0, 0.2)

    def test_mark_series_extrema_keeps_every_cycle(self) -> None:
        origin = datetime(2026, 2, 22, 1, 0, 0)
        period_minutes = 124.0
        points: list[LightCurvePoint] = []
        for index in range(90):
            elapsed = index * 8.0
            phase = (elapsed / period_minutes)
            magnitude = 11.35 + (0.28 * np.cos(2.0 * np.pi * phase))
            points.append(
                LightCurvePoint(
                    observation_time=origin + timedelta(minutes=elapsed),
                    file_path=Path(f"frame_{index:02d}.fits"),
                    differential_magnitude=magnitude,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    standard_magnitude=magnitude,
                    standard_magnitude_error=0.01,
                    differential_magnitude_error=0.01,
                )
            )
        series = LightCurveSeries("Night1", "vsx-1", "AE UMa", "B", points)
        records = mark_series_extrema(
            series,
            y_axis_mode="standard_magnitude",
            min_separation_days=0.35 * (period_minutes / 1440.0),
        )
        maxima = [record for record in records if record.kind == EXTREMUM_MAXIMUM]
        minima = [record for record in records if record.kind == EXTREMUM_MINIMUM]
        self.assertGreaterEqual(len(maxima), 3)
        self.assertGreaterEqual(len(minima), 3)
        max_jds = [record.jd for record in maxima]
        self.assertEqual(len(max_jds), len(set(round(value, 4) for value in max_jds)))
        chosen = mark_extremum_near_jd(
            series,
            maxima[1].jd + 0.002,
            y_axis_mode="standard_magnitude",
            min_separation_days=0.35 * (period_minutes / 1440.0),
        )
        self.assertEqual(chosen.kind, EXTREMUM_MAXIMUM)
        self.assertAlmostEqual(chosen.jd, maxima[1].jd, places=5)

    def test_compute_oc_residuals_uses_nearest_epoch(self) -> None:
        record = ExtremumRecord(
            record_id="one",
            star_name="DY Her",
            source_id="vsx-dyher",
            session_name="Night1",
            kind=EXTREMUM_MAXIMUM,
            jd=2460002.10,
        )
        residuals = compute_oc_residuals([record], t0_hjd=2460000.0, period_days=1.0, kind=EXTREMUM_MAXIMUM)
        self.assertEqual(len(residuals), 1)
        self.assertEqual(residuals[0].epoch, 2)
        self.assertAlmostEqual(residuals[0].oc_days, 0.10, places=6)

    def test_upsert_replaces_same_session_kind_near_same_jd(self) -> None:
        log = OcStarLog(star_key="vsx-dyher", star_name="DY Her", source_id="vsx-dyher")
        first = ExtremumRecord("a", "DY Her", "vsx-dyher", "Night1", EXTREMUM_MAXIMUM, 2460000.10)
        second = ExtremumRecord("b", "DY Her", "vsx-dyher", "Night1", EXTREMUM_MAXIMUM, 2460000.1004, magnitude=10.9)
        log = upsert_records(log, [first])
        log = upsert_records(log, [second])
        self.assertEqual(len(log.records), 1)
        self.assertEqual(log.records[0].record_id, "b")
        self.assertAlmostEqual(log.records[0].magnitude or 0.0, 10.9)

    def test_payload_round_trip_preserves_records(self) -> None:
        log = OcStarLog(
            star_key="vsx-dyher",
            star_name="DY Her",
            source_id="vsx-dyher",
            t0_hjd=2430000.12,
            period_days=0.148773,
            records=[
                ExtremumRecord("a", "DY Her", "vsx-dyher", "Night1", EXTREMUM_MAXIMUM, 2460000.10, 0.0002),
            ],
        )
        restored = oc_log_from_payload(oc_log_to_payload(log))
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.star_key, "vsx-dyher")
        self.assertAlmostEqual(restored.period_days or 0.0, 0.148773)
        self.assertEqual(len(restored.records), 1)
        self.assertAlmostEqual(restored.records[0].jd, 2460000.10)

    def test_import_cast_light_curve_csv(self) -> None:
        start = datetime(2026, 3, 16, 1, 0, 0)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "DY_Her_light_curves.csv"
            path.write_text(
                "object_name,source_id,source_name,filter_name,observation_time,file_path,differential_magnitude,differential_magnitude_error\n"
                f"Night1,vsx-dyher,DY Her,V,{start.isoformat()},frame.fits,10.91,0.01\n"
                f"Night1,vsx-dyher,DY Her,V,{(start + timedelta(minutes=8)).isoformat()},frame2.fits,10.95,0.01\n",
                encoding="utf-8",
            )
            imported = import_photometry_table(path, star_name="DY Her", source_id="vsx-dyher")
        self.assertEqual(len(imported.sessions), 1)
        self.assertEqual(len(imported.sessions[0].series.points), 2)
        self.assertEqual(imported.sessions[0].series.source_name, "DY Her")

    def test_import_aavso_extended(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dyher_aavso.txt"
            path.write_text(
                "#TYPE=EXTENDED\n"
                "#DATE=JD\n"
                "#NAME,DATE,MAG,MERR,FILT,TRANS,MTYPE,CNAME,CMAG,KNAME,KMAG,AMASS,GROUP,CHART,NOTES\n"
                "DY HER,2460000.12345,10.914,0.002,V,NO,STD,na,na,na,na,1.1,na,X12345,na\n"
                "DY HER,2460000.13000,10.940,0.003,V,NO,STD,na,na,na,na,1.1,na,X12345,na\n",
                encoding="utf-8",
            )
            imported = import_photometry_table(path, star_name="DY Her", source_id="vsx-dyher")
        self.assertEqual(len(imported.sessions), 1)
        self.assertEqual(len(imported.sessions[0].series.points), 2)
        first_jd = float(Time(imported.sessions[0].series.points[0].observation_time).jd)
        self.assertAlmostEqual(first_jd, 2460000.12345, places=5)

    def test_import_extrema_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dyher_extrema.csv"
            path.write_text(
                "Star,JD(MAX),JD(MIN),Filter\n"
                "DY Her,2460000.111,2460000.090,V\n",
                encoding="utf-8",
            )
            imported = import_photometry_table(path, star_name="DY Her", source_id="vsx-dyher")
        self.assertEqual(len(imported.records), 2)
        kinds = {record.kind for record in imported.records}
        self.assertEqual(kinds, {EXTREMUM_MAXIMUM, EXTREMUM_MINIMUM})

    def test_series_matches_star_by_name_or_id(self) -> None:
        series = _pulse_series()
        self.assertTrue(series_matches_star(series, source_id="vsx-dyher", star_name="Other"))
        self.assertTrue(series_matches_star(series, source_id="", star_name="DY Her"))
        self.assertFalse(series_matches_star(series, source_id="other", star_name="AE UMa"))
