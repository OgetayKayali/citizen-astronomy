from __future__ import annotations

import unittest

import numpy as np

from photometry_app.core.astrostack_metrics import (
    estimate_global_signal_noise,
    estimate_region_signal_noise,
    extract_region_pixels,
    format_astrostack_metric_value,
    roi_bounds_to_cropped_data_space,
    shift_roi_bounds,
)


class AstrostackMetricsTest(unittest.TestCase):
    def test_extract_region_pixels_returns_requested_window(self) -> None:
        data = np.arange(100, dtype=np.float64).reshape(10, 10)
        region = extract_region_pixels(data, 2.0, 3.0, 5.0, 6.0)
        self.assertEqual(region.shape, (3, 3))
        self.assertEqual(float(region[0, 0]), float(data[3, 2]))

    def test_estimate_region_signal_noise_uses_background_noise(self) -> None:
        data = np.zeros((20, 20), dtype=np.float64)
        data[5:10, 5:10] = 4.0
        background = np.random.default_rng(0).normal(0.0, 0.2, (20, 20))
        data[15:20, 15:20] = background[15:20, 15:20]
        signal, noise = estimate_region_signal_noise(
            data,
            (5.0, 5.0, 9.0, 9.0),
            (15.0, 15.0, 19.0, 19.0),
        )
        self.assertGreater(signal, 2.0)
        self.assertGreater(noise, 0.0)
        self.assertLess(noise, 1.0)

    def test_estimate_global_signal_noise_matches_legacy_behavior(self) -> None:
        data = np.linspace(0.0, 10.0, 100, dtype=np.float64).reshape(10, 10)
        signal, noise = estimate_global_signal_noise(data)
        self.assertGreater(signal, 0.0)
        self.assertGreater(noise, 0.0)

    def test_roi_bounds_crop_conversion_samples_expected_pixels(self) -> None:
        data = np.zeros((30, 30), dtype=np.float64)
        data[12:18, 12:18] = 8.0
        background = np.random.default_rng(1).normal(0.0, 0.25, (30, 30))
        data[22:28, 22:28] = background[22:28, 22:28]
        full_signal_bounds = (12.0, 12.0, 17.0, 17.0)
        full_background_bounds = (22.0, 22.0, 27.0, 27.0)
        crop_origin = (10.0, 10.0)
        cropped_data = data[10:25, 10:25]
        signal, noise = estimate_region_signal_noise(
            cropped_data,
            roi_bounds_to_cropped_data_space(full_signal_bounds, crop_origin),
            roi_bounds_to_cropped_data_space(full_background_bounds, crop_origin),
        )
        self.assertGreater(signal, 5.0)
        self.assertGreater(noise, 0.0)

    def test_shift_roi_bounds_round_trip(self) -> None:
        bounds = (12.0, 15.0, 40.0, 55.0)
        shifted = shift_roi_bounds(bounds, 10.0, 20.0)
        restored = shift_roi_bounds(shifted, -10.0, -20.0)
        self.assertEqual(bounds, restored)

    def test_format_astrostack_metric_value_uses_scientific_notation(self) -> None:
        signal_text = format_astrostack_metric_value("signal", 1234.5)
        noise_text = format_astrostack_metric_value("noise", 0.00042)
        self.assertIn("e", signal_text.lower())
        self.assertIn("e", noise_text.lower())
        self.assertEqual(format_astrostack_metric_value("signal", 0.0), "0.000e+00")


if __name__ == "__main__":
    unittest.main()
