from __future__ import annotations

from datetime import datetime
from pathlib import Path
import unittest

from photometry_app.core.models import LightCurvePoint, PhotometryMeasurement
from photometry_app.core.pipeline import apply_saturation_frame_filter
from photometry_app.core.plotting import _light_curve_point_fit_weight


def _measurement(*, saturated: bool, source_id: str = "vsx-1") -> PhotometryMeasurement:
    return PhotometryMeasurement(
        source_id=source_id,
        source_name="DY Her",
        catalog="vsx",
        object_name="Demo",
        file_path=Path("frame.fits"),
        observation_time=datetime(2026, 3, 16, 1, 0, 0),
        filter_name="V",
        ra_deg=10.0,
        dec_deg=20.0,
        x=50.0,
        y=60.0,
        flux=5000.0,
        flux_error=15.0,
        instrumental_magnitude=-9.0,
        differential_magnitude=0.32,
        is_variable=True,
        is_reference=False,
        is_saturated=saturated,
    )


class SaturationFilterTest(unittest.TestCase):
    def test_filter_excludes_saturated_frames_but_keeps_the_target(self) -> None:
        measurements = [_measurement(saturated=False), _measurement(saturated=True)]
        updated, notes = apply_saturation_frame_filter(measurements, True)
        self.assertEqual(len(updated), 2)
        self.assertEqual({row.source_id for row in updated}, {"vsx-1"})
        self.assertFalse(updated[0].excluded_from_analysis)
        self.assertTrue(updated[1].excluded_from_analysis)
        self.assertIn("Saturated frame.", updated[1].exclusion_reasons)
        self.assertTrue(any("saturated frame" in note.casefold() for note in notes))
        self.assertFalse(any("variable star" in note.casefold() for note in notes))

    def test_disabled_filter_keeps_saturated_frames_in_analysis(self) -> None:
        measurements = [_measurement(saturated=True)]
        updated, notes = apply_saturation_frame_filter(measurements, False)
        self.assertEqual(len(updated), 1)
        self.assertFalse(updated[0].excluded_from_analysis)
        self.assertEqual(notes, [])

    def test_saturated_points_do_not_weight_the_fit(self) -> None:
        point = LightCurvePoint(
            observation_time=datetime(2026, 3, 16, 1, 0, 0),
            file_path=Path("frame.fits"),
            differential_magnitude=10.4,
            instrumental_magnitude=None,
            flux=None,
            flux_error=None,
            is_saturated=True,
        )
        self.assertEqual(_light_curve_point_fit_weight(point, "differential_magnitude"), 0.0)
