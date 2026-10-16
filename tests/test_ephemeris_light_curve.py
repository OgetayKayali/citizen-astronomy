from __future__ import annotations

from datetime import UTC
from pathlib import Path
import math
import unittest

from astropy.time import Time

from photometry_app.core.ephemeris_light_curve import (
    EpochMagnitudeSample,
    EphemerisLightCurveError,
    build_epoch_light_curve,
    epoch_display_window,
    samples_from_photometry_import,
)
from photometry_app.core.models import LightCurvePoint, LightCurveSeries
from photometry_app.core.oc_extrema import ORIGIN_AAVSO, OcSession, PhotometryImport


_EPOCH_HJD = 2460000.0
_PERIOD_DAYS = 1.0


def _sample(epoch: float, magnitude: float, band: str = "V", uncertainty: float | None = 0.01) -> EpochMagnitudeSample:
    return EpochMagnitudeSample(
        jd=_EPOCH_HJD + epoch * _PERIOD_DAYS,
        magnitude=magnitude,
        uncertainty=uncertainty,
        band=band,
    )


def _pulsator_samples() -> list[EpochMagnitudeSample]:
    samples: list[EpochMagnitudeSample] = []
    for cycle in range(12, 19):
        for phase, magnitude in ((0.0, 10.0), (0.25, 11.0), (0.5, 12.0), (0.75, 11.0)):
            samples.append(_sample(cycle + phase, magnitude))
    return samples


def _magnitude_near(epochs: tuple[float, ...], magnitudes: tuple[float, ...], epoch: float) -> float:
    index = min(range(len(epochs)), key=lambda item: abs(epochs[item] - epoch))
    return magnitudes[index]


class EphemerisLightCurveTests(unittest.TestCase):
    def test_window_covers_upcoming_cycles_and_caps_short_periods(self) -> None:
        start, end = epoch_display_window(5.2, 40.0)
        self.assertEqual(start, 5.0)
        self.assertAlmostEqual(end - 5.2, 2.0, places=6)

        short_start, short_end = epoch_display_window(100.3, 0.2)
        self.assertEqual(short_start, 100.0)
        self.assertAlmostEqual(short_end - short_start, 12.0, places=6)
        self.assertGreater(short_end, 100.3)

    def test_folded_template_repeats_on_future_epochs(self) -> None:
        curve = build_epoch_light_curve(
            _pulsator_samples(),
            epoch_hjd=_EPOCH_HJD,
            period_days=_PERIOD_DAYS,
            current_epoch=20.4,
        )
        self.assertAlmostEqual(curve.current_epoch, 20.4, places=6)
        self.assertEqual(curve.epoch_start, 20.0)
        self.assertGreater(curve.epoch_end, 20.4)
        self.assertEqual(len(curve.bands), 1)
        band = curve.bands[0]
        self.assertEqual(band.label, "V")
        bright = _magnitude_near(band.curve_epoch, band.curve_magnitude, 21.03)
        faint = _magnitude_near(band.curve_epoch, band.curve_magnitude, 21.53)
        self.assertLess(bright, faint)
        self.assertAlmostEqual(
            _magnitude_near(band.curve_epoch, band.curve_magnitude, 21.03),
            _magnitude_near(band.curve_epoch, band.curve_magnitude, 22.03),
            places=6,
        )
        self.assertTrue(any(epoch > curve.current_epoch for epoch in band.curve_epoch))
        self.assertTrue(any(epoch < curve.current_epoch for epoch in band.curve_epoch))
        samples_in_cycle = [epoch for epoch in band.curve_epoch if 21.0 <= epoch < 22.0]
        self.assertGreaterEqual(len(samples_in_cycle), 40)

    def test_periodic_fit_follows_a_smooth_cosine(self) -> None:
        samples = [
            _sample(cycle + phase, 11.0 - math.cos(2.0 * math.pi * phase))
            for cycle in range(12, 19)
            for phase in (index / 24.0 for index in range(24))
        ]
        curve = build_epoch_light_curve(
            samples,
            epoch_hjd=_EPOCH_HJD,
            period_days=_PERIOD_DAYS,
            current_epoch=20.4,
        )
        band = curve.bands[0]
        cycle = [
            (epoch - math.floor(epoch), magnitude)
            for epoch, magnitude in zip(band.curve_epoch, band.curve_magnitude, strict=True)
            if 21.0 <= epoch < 22.0
        ]
        errors = [magnitude - (11.0 - math.cos(2.0 * math.pi * phase)) for phase, magnitude in cycle]
        rms = math.sqrt(sum(error * error for error in errors) / len(errors))
        self.assertLess(rms, 0.05)
        steps = [abs(cycle[index + 1][1] - cycle[index][1]) for index in range(len(cycle) - 1)]
        self.assertLess(max(steps), 0.2)

    def test_only_in_window_observations_are_plotted_as_points(self) -> None:
        samples = _pulsator_samples()
        samples.append(_sample(20.2, 10.4))
        samples.append(_sample(3.0, 10.0))
        curve = build_epoch_light_curve(
            samples,
            epoch_hjd=_EPOCH_HJD,
            period_days=_PERIOD_DAYS,
            current_epoch=20.4,
        )
        window = curve.bands[0].window_epoch
        self.assertTrue(any(abs(epoch - 20.2) < 1e-6 for epoch in window))
        self.assertFalse(any(epoch < 10.0 for epoch in window))
        self.assertGreater(curve.observation_count, len(window))

    def test_bands_stay_separate(self) -> None:
        samples = _pulsator_samples()
        samples.extend(_sample(cycle + 0.0, 11.5, band="B") for cycle in range(12, 19))
        samples.extend(_sample(cycle + 0.5, 13.5, band="B") for cycle in range(12, 19))
        samples.extend(_sample(cycle + 0.25, 12.5, band="B") for cycle in range(12, 19))
        curve = build_epoch_light_curve(
            samples,
            epoch_hjd=_EPOCH_HJD,
            period_days=_PERIOD_DAYS,
            current_epoch=20.4,
        )
        by_label = {band.label: band for band in curve.bands}
        self.assertEqual(set(by_label), {"V", "B"})
        v_mag = _magnitude_near(by_label["V"].curve_epoch, by_label["V"].curve_magnitude, 21.03)
        b_mag = _magnitude_near(by_label["B"].curve_epoch, by_label["B"].curve_magnitude, 21.03)
        self.assertLess(v_mag, b_mag)

    def test_too_few_or_single_phase_points_are_rejected(self) -> None:
        with self.assertRaises(EphemerisLightCurveError):
            build_epoch_light_curve(
                [_sample(1.0, 10.0), _sample(1.2, 10.2)],
                epoch_hjd=_EPOCH_HJD,
                period_days=_PERIOD_DAYS,
                current_epoch=20.4,
            )
        clustered = [_sample(cycle + 0.1, 10.0) for cycle in range(10)]
        with self.assertRaises(EphemerisLightCurveError):
            build_epoch_light_curve(
                clustered,
                epoch_hjd=_EPOCH_HJD,
                period_days=_PERIOD_DAYS,
                current_epoch=20.4,
            )

    def test_import_keeps_band_and_magnitude(self) -> None:
        observed = Time(_EPOCH_HJD + 20.2, format="jd", scale="utc").to_datetime()
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)
        session = OcSession(
            session_name="AAVSO V",
            series=LightCurveSeries(
                object_name="AAVSO V",
                source_id="",
                source_name="RR Lyr",
                filter_name="V",
                points=[
                    LightCurvePoint(
                        observation_time=observed,
                        file_path=Path("aavso-aid"),
                        differential_magnitude=10.4,
                        instrumental_magnitude=None,
                        flux=None,
                        flux_error=None,
                        standard_magnitude=10.4,
                        standard_magnitude_error=0.02,
                    )
                ],
            ),
            origin=ORIGIN_AAVSO,
        )
        samples = samples_from_photometry_import(PhotometryImport(sessions=(session,), records=()))
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].band, "V")
        self.assertAlmostEqual(samples[0].magnitude, 10.4, places=6)
        self.assertAlmostEqual(samples[0].jd, _EPOCH_HJD + 20.2, places=5)


if __name__ == "__main__":
    unittest.main()
