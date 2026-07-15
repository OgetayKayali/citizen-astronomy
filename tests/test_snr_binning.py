from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path

from photometry_app.core.models import PhotometryMeasurement
from photometry_app.core.snr_binning import SnrBinningSettings, SnrBinningTask, process_snr_binning_task


class SnrBinningTest(unittest.TestCase):
    def _build_measurements(self, count: int = 6, cadence_seconds: int = 60, snr: float = 10.0) -> list[PhotometryMeasurement]:
        start = datetime(2026, 3, 31, 0, 0, 0)
        measurements: list[PhotometryMeasurement] = []
        for index in range(count):
            flux = 1000.0 + (index * 20.0)
            flux_error = flux / snr
            measurements.append(
                PhotometryMeasurement(
                    source_id="vsx-1",
                    source_name="Target",
                    catalog="vsx",
                    object_name="Demo",
                    file_path=Path(f"frame_{index:02d}.fits"),
                    observation_time=start + timedelta(seconds=index * cadence_seconds),
                    filter_name="V",
                    ra_deg=10.0,
                    dec_deg=20.0,
                    x=50.0 + index,
                    y=60.0 + index,
                    flux=flux,
                    flux_error=flux_error,
                    instrumental_magnitude=-2.5,
                    differential_magnitude=12.0 + (index * 0.01),
                    differential_magnitude_error=0.03,
                    is_variable=True,
                    is_reference=False,
                    comparison_reference_flux=2000.0 + (index * 10.0),
                    quality_score=0.95,
                    quality_weight=1.0 / (flux_error * flux_error),
                    snr=snr,
                )
            )
        return measurements

    def test_period_aware_binning_prefers_flux_weighted_derived_series(self) -> None:
        task = SnrBinningTask(
            source_id="vsx-1",
            source_name="Target",
            catalog="vsx",
            variability_type="RS",
            period_days=0.2,
            measurements_by_filter={"V": self._build_measurements(count=6, cadence_seconds=60, snr=10.0)},
        )
        settings = SnrBinningSettings(target_snr=30.0, max_absolute_bin_duration_seconds=600.0)

        result = process_snr_binning_task(task, settings)

        self.assertEqual(result.status, "processed")
        self.assertEqual(result.processed_count, 1)
        series_result = result.series_results[0]
        self.assertEqual(series_result.status, "processed")
        self.assertEqual(series_result.original_measurement_count, 6)
        self.assertEqual(series_result.new_binned_measurement_count, 1)
        self.assertEqual(series_result.chosen_frames_per_bin, 8)
        self.assertGreaterEqual(series_result.estimated_snr_improvement, 1.0)
        self.assertIn("Derived SNR bin", series_result.binned_measurements[0].flags[0])
        self.assertEqual(series_result.binned_measurements[0].file_path.name, "frame_00.fits")
        self.assertIsNotNone(series_result.binned_measurements[0].differential_magnitude)

    def test_missing_period_skips_unless_periodless_fallback_is_enabled(self) -> None:
        task = SnrBinningTask(
            source_id="vsx-1",
            source_name="Target",
            catalog="vsx",
            variability_type="RS",
            period_days=None,
            measurements_by_filter={"V": self._build_measurements(count=5, cadence_seconds=60, snr=12.0)},
        )

        skipped = process_snr_binning_task(task, SnrBinningSettings())
        processed = process_snr_binning_task(task, SnrBinningSettings(allow_periodless_fallback=True, target_snr=20.0))

        self.assertEqual(skipped.status, "skipped")
        self.assertEqual(skipped.series_results[0].reason, "missing usable period")
        self.assertEqual(processed.status, "processed")
        self.assertEqual(processed.series_results[0].status, "processed")

    def test_type_aware_sharp_thresholds_can_force_unbinned_skip(self) -> None:
        task = SnrBinningTask(
            source_id="vsx-1",
            source_name="Target",
            catalog="vsx",
            variability_type="EA",
            period_days=0.1,
            measurements_by_filter={"V": self._build_measurements(count=6, cadence_seconds=60, snr=10.0)},
        )
        baseline = process_snr_binning_task(
            task,
            SnrBinningSettings(
                max_absolute_bin_duration_seconds=5000.0,
                max_period_fraction=0.03,
                variability_type_aware_thresholds=False,
                target_snr=20.0,
            ),
        )
        conservative = process_snr_binning_task(
            task,
            SnrBinningSettings(
                max_absolute_bin_duration_seconds=5000.0,
                max_period_fraction=0.03,
                variability_type_aware_thresholds=True,
                sharp_period_fraction_override=0.01,
                target_snr=20.0,
            ),
        )

        self.assertEqual(baseline.status, "processed")
        self.assertEqual(conservative.status, "skipped")
        self.assertIn("conservative period limits", conservative.series_results[0].reason)


if __name__ == "__main__":
    unittest.main()