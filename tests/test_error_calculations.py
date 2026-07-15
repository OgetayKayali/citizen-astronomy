from __future__ import annotations

import math
import unittest

import numpy as np

from photometry_app.core.error_calculations import (
    compute_differential_mag_error,
    compute_empirical_scatter,
    compute_ensemble_mag_error,
    compute_flux_error,
    compute_scintillation_error,
    compute_total_mag_error,
    flux_error_to_mag_error,
)


class ErrorCalculationsTest(unittest.TestCase):
    def test_compute_flux_error_matches_ccd_noise_model(self) -> None:
        sigma_flux = compute_flux_error(
            source_flux=10000.0,
            sky_background_per_pixel=100.0,
            aperture_pixel_count=50.0,
            sky_pixel_count=500.0,
            read_noise_electrons=5.0,
            dark_current_electrons_per_pixel_second=0.02,
            exposure_seconds=60.0,
            gain_electrons_per_adu=2.0,
        )

        source_e = 10000.0 * 2.0
        sky_e = 100.0 * 2.0
        dark_e = 0.02 * 60.0
        background_per_pixel = sky_e + dark_e + (5.0 ** 2)
        variance_e = source_e + (50.0 * background_per_pixel) + (((50.0 ** 2) / 500.0) * background_per_pixel)
        expected_sigma = math.sqrt(variance_e) / 2.0
        self.assertAlmostEqual(sigma_flux, expected_sigma, places=6)

    def test_flux_error_to_mag_error_returns_nan_for_nonpositive_flux(self) -> None:
        mag_error = flux_error_to_mag_error(np.array([1000.0, 0.0, -5.0]), np.array([10.0, 10.0, 10.0]))
        self.assertTrue(np.isfinite(mag_error[0]))
        self.assertTrue(np.isnan(mag_error[1]))
        self.assertTrue(np.isnan(mag_error[2]))

    def test_compute_ensemble_mag_error_supports_weighted_and_unweighted_modes(self) -> None:
        comparison_fluxes = np.array([20000.0, 22000.0, 18000.0])
        comparison_flux_errors = np.array([100.0, 120.0, 90.0])

        unweighted = compute_ensemble_mag_error(comparison_fluxes, comparison_flux_errors)
        weighted = compute_ensemble_mag_error(comparison_fluxes, comparison_flux_errors, weights=np.array([1.0, 2.0, 1.0]))

        self.assertTrue(np.isfinite(unweighted))
        self.assertTrue(np.isfinite(weighted))
        self.assertNotEqual(unweighted, weighted)

    def test_compute_differential_mag_error_combines_target_and_ensemble_terms(self) -> None:
        diff_error = compute_differential_mag_error(0.012, 0.008)
        self.assertAlmostEqual(diff_error, math.sqrt((0.012 ** 2) + (0.008 ** 2)), places=9)

    def test_compute_empirical_scatter_is_robust_to_outlier(self) -> None:
        residuals = np.array([0.003, -0.002, 0.004, -0.001, 0.0035, 0.2])
        scatter = compute_empirical_scatter(residuals, method="mad")
        self.assertLess(scatter, 0.02)

    def test_compute_scintillation_error_increases_with_airmass(self) -> None:
        low_airmass = compute_scintillation_error(28.0, 60.0, 1.1, observatory_altitude_m=400.0)
        high_airmass = compute_scintillation_error(28.0, 60.0, 2.0, observatory_altitude_m=400.0)
        self.assertGreater(high_airmass, low_airmass)

    def test_compute_total_mag_error_combines_all_terms(self) -> None:
        total_error = compute_total_mag_error(0.01, 0.02, 0.03)
        self.assertAlmostEqual(total_error, math.sqrt((0.01 ** 2) + (0.02 ** 2) + (0.03 ** 2)), places=9)


if __name__ == "__main__":
    unittest.main()