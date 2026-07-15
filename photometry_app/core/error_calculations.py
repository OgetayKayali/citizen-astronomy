from __future__ import annotations

import math

import numpy as np


MAG_ERROR_SCALE = 1.085736


def compute_flux_error(
    source_flux: np.ndarray | list[float] | float,
    sky_background_per_pixel: np.ndarray | list[float] | float,
    aperture_pixel_count: np.ndarray | list[float] | float,
    sky_pixel_count: np.ndarray | list[float] | float | None = None,
    read_noise_electrons: np.ndarray | list[float] | float = 0.0,
    dark_current_electrons_per_pixel_second: np.ndarray | list[float] | float = 0.0,
    exposure_seconds: np.ndarray | list[float] | float = 0.0,
    gain_electrons_per_adu: np.ndarray | list[float] | float = 1.0,
    counts_are_adu: bool = True,
    saturated: np.ndarray | list[bool] | bool | None = None,
    saturation_level: np.ndarray | list[float] | float | None = None,
) -> np.ndarray | float:
    """Compute CCD-aperture flux uncertainty with source, sky, read, and dark noise.

    Parameters are broadcast with numpy. `source_flux` is the background-subtracted
    source flux in either ADU or electrons. `sky_background_per_pixel` must be in the
    same units as `source_flux` before gain conversion. If `sky_pixel_count` is given,
    the background-estimation term is included.

    Saturated observations return `nan`.
    """
    source_flux = _as_float_array(source_flux)
    sky_background_per_pixel = _as_float_array(sky_background_per_pixel)
    aperture_pixel_count = _as_float_array(aperture_pixel_count)
    read_noise_electrons = _as_float_array(read_noise_electrons)
    dark_current_electrons_per_pixel_second = _as_float_array(dark_current_electrons_per_pixel_second)
    exposure_seconds = _as_float_array(exposure_seconds)
    gain_electrons_per_adu = _as_float_array(gain_electrons_per_adu)

    (
        source_flux,
        sky_background_per_pixel,
        aperture_pixel_count,
        read_noise_electrons,
        dark_current_electrons_per_pixel_second,
        exposure_seconds,
        gain_electrons_per_adu,
    ) = np.broadcast_arrays(
        source_flux,
        sky_background_per_pixel,
        aperture_pixel_count,
        read_noise_electrons,
        dark_current_electrons_per_pixel_second,
        exposure_seconds,
        gain_electrons_per_adu,
    )

    if sky_pixel_count is None:
        sky_pixel_count = np.full_like(aperture_pixel_count, np.inf, dtype=float)
    else:
        sky_pixel_count = np.broadcast_to(_as_float_array(sky_pixel_count), source_flux.shape)

    if saturated is None:
        saturated_mask = np.zeros_like(source_flux, dtype=bool)
    else:
        saturated_mask = np.broadcast_to(np.asarray(saturated, dtype=bool), source_flux.shape)

    if saturation_level is not None:
        saturation_level = np.broadcast_to(_as_float_array(saturation_level), source_flux.shape)
        saturated_mask = saturated_mask | (np.isfinite(saturation_level) & (source_flux >= saturation_level))

    valid = (
        np.isfinite(source_flux)
        & np.isfinite(sky_background_per_pixel)
        & np.isfinite(aperture_pixel_count)
        & np.isfinite(read_noise_electrons)
        & np.isfinite(dark_current_electrons_per_pixel_second)
        & np.isfinite(exposure_seconds)
        & np.isfinite(gain_electrons_per_adu)
        & (aperture_pixel_count > 0)
        & (gain_electrons_per_adu > 0)
        & (~saturated_mask)
    )

    gain = np.where(counts_are_adu, gain_electrons_per_adu, 1.0)
    source_electrons = np.clip(source_flux, 0.0, None) * gain
    sky_electrons_per_pixel = np.clip(sky_background_per_pixel, 0.0, None) * gain
    dark_electrons_per_pixel = np.clip(dark_current_electrons_per_pixel_second, 0.0, None) * np.clip(exposure_seconds, 0.0, None)
    read_variance = np.clip(read_noise_electrons, 0.0, None) ** 2
    background_variance_per_pixel = sky_electrons_per_pixel + dark_electrons_per_pixel + read_variance

    variance_electrons = source_electrons + (aperture_pixel_count * background_variance_per_pixel)
    with np.errstate(divide="ignore", invalid="ignore"):
        background_estimation_term = ((aperture_pixel_count ** 2) / sky_pixel_count) * background_variance_per_pixel
    background_estimation_term = np.where(np.isfinite(background_estimation_term), background_estimation_term, 0.0)
    variance_electrons = variance_electrons + background_estimation_term

    sigma_electrons = np.sqrt(np.clip(variance_electrons, 0.0, None))
    sigma_flux = np.where(counts_are_adu, sigma_electrons / gain_electrons_per_adu, sigma_electrons)

    return _return_like_input(np.where(valid, sigma_flux, np.nan))


def flux_error_to_mag_error(
    flux: np.ndarray | list[float] | float,
    flux_error: np.ndarray | list[float] | float,
) -> np.ndarray | float:
    """Convert flux uncertainty to magnitude uncertainty using 1.085736 * sigma_F / F."""
    flux, flux_error = np.broadcast_arrays(_as_float_array(flux), _as_float_array(flux_error))
    valid = np.isfinite(flux) & np.isfinite(flux_error) & (flux > 0) & (flux_error >= 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        mag_error = MAG_ERROR_SCALE * flux_error / flux
    return _return_like_input(np.where(valid, mag_error, np.nan))


def compute_ensemble_mag_error(
    comparison_fluxes: np.ndarray | list[float],
    comparison_flux_errors: np.ndarray | list[float],
    weights: np.ndarray | list[float] | None = None,
    axis: int = -1,
) -> np.ndarray | float:
    """Compute ensemble-comparison magnitude uncertainty from flux-space weighted averaging.

    If `weights` is `None`, an unweighted ensemble is used. Otherwise, the weighted
    mean comparison flux is formed and its uncertainty is propagated from the input
    comparison flux errors.
    """
    comparison_fluxes = _as_float_array(comparison_fluxes)
    comparison_flux_errors = _as_float_array(comparison_flux_errors)
    if weights is None:
        weights = np.ones_like(comparison_fluxes, dtype=float)
    else:
        weights = _as_float_array(weights)

    comparison_fluxes, comparison_flux_errors, weights = np.broadcast_arrays(
        comparison_fluxes,
        comparison_flux_errors,
        weights,
    )

    valid = (
        np.isfinite(comparison_fluxes)
        & np.isfinite(comparison_flux_errors)
        & np.isfinite(weights)
        & (comparison_fluxes > 0)
        & (comparison_flux_errors >= 0)
        & (weights > 0)
    )
    safe_weights = np.where(valid, weights, 0.0)
    safe_fluxes = np.where(valid, comparison_fluxes, 0.0)
    safe_errors = np.where(valid, comparison_flux_errors, 0.0)

    weight_sum = np.sum(safe_weights, axis=axis)
    valid_count = np.sum(valid, axis=axis)
    weighted_flux = np.sum(safe_weights * safe_fluxes, axis=axis)
    weighted_flux = np.divide(weighted_flux, weight_sum, out=np.full_like(weight_sum, np.nan, dtype=float), where=weight_sum > 0)

    weighted_flux_error = np.sqrt(np.sum((safe_weights * safe_errors) ** 2, axis=axis))
    weighted_flux_error = np.divide(
        weighted_flux_error,
        weight_sum,
        out=np.full_like(weight_sum, np.nan, dtype=float),
        where=weight_sum > 0,
    )

    ensemble_mag_error = flux_error_to_mag_error(weighted_flux, weighted_flux_error)
    ensemble_mag_error = np.where(valid_count > 0, ensemble_mag_error, np.nan)
    return _return_like_input(ensemble_mag_error)


def compute_differential_mag_error(
    target_mag_error: np.ndarray | list[float] | float,
    ensemble_mag_error: np.ndarray | list[float] | float,
) -> np.ndarray | float:
    """Combine target and ensemble magnitude uncertainties in quadrature."""
    target_mag_error, ensemble_mag_error = np.broadcast_arrays(
        _as_float_array(target_mag_error),
        _as_float_array(ensemble_mag_error),
    )
    valid = np.isfinite(target_mag_error) & np.isfinite(ensemble_mag_error) & (target_mag_error >= 0) & (ensemble_mag_error >= 0)
    diff_error = np.sqrt(np.clip(target_mag_error, 0.0, None) ** 2 + np.clip(ensemble_mag_error, 0.0, None) ** 2)
    return _return_like_input(np.where(valid, diff_error, np.nan))


def compute_empirical_scatter(
    residuals: np.ndarray | list[float],
    method: str = "mad",
    sigma_clip: float | None = 4.0,
) -> float:
    """Estimate empirical scatter from check-star or comparison residuals in magnitudes.

    The default `mad` method uses a robust median absolute deviation estimate.
    Set `method="std"` for a standard deviation estimate after optional clipping.
    """
    values = _as_float_array(residuals).reshape(-1)
    values = values[np.isfinite(values)]
    if values.size < 2:
        return float("nan")

    if sigma_clip is not None and sigma_clip > 0:
        for _ in range(3):
            center = np.median(values)
            scale = 1.4826 * np.median(np.abs(values - center))
            if not np.isfinite(scale) or scale <= 0:
                break
            keep = np.abs(values - center) <= (sigma_clip * scale)
            if keep.all():
                break
            values = values[keep]
            if values.size < 2:
                return float("nan")

    if method == "std":
        return float(np.std(values, ddof=1))
    if method != "mad":
        raise ValueError("method must be 'mad' or 'std'.")
    center = np.median(values)
    return float(1.4826 * np.median(np.abs(values - center)))


def compute_scintillation_error(
    telescope_diameter_cm: np.ndarray | list[float] | float,
    exposure_seconds: np.ndarray | list[float] | float,
    airmass: np.ndarray | list[float] | float,
    observatory_altitude_m: np.ndarray | list[float] | float = 0.0,
    comparison_star_count: np.ndarray | list[float] | float = 0.0,
    scale_height_m: float = 8000.0,
    coefficient: float = 0.09,
) -> np.ndarray | float:
    """Compute scintillation magnitude error using a standard Young-style approximation.

    If `comparison_star_count` is positive, the result is adjusted for differential
    photometry against an ensemble by a factor of sqrt(1 + 1 / N_comp).
    """
    telescope_diameter_cm, exposure_seconds, airmass, observatory_altitude_m, comparison_star_count = np.broadcast_arrays(
        _as_float_array(telescope_diameter_cm),
        _as_float_array(exposure_seconds),
        _as_float_array(airmass),
        _as_float_array(observatory_altitude_m),
        _as_float_array(comparison_star_count),
    )

    valid = (
        np.isfinite(telescope_diameter_cm)
        & np.isfinite(exposure_seconds)
        & np.isfinite(airmass)
        & np.isfinite(observatory_altitude_m)
        & (telescope_diameter_cm > 0)
        & (exposure_seconds > 0)
        & (airmass >= 1.0)
    )

    scint_fraction = (
        coefficient
        * np.power(telescope_diameter_cm, -2.0 / 3.0)
        * np.power(airmass, 1.75)
        * np.exp(-observatory_altitude_m / scale_height_m)
        / np.sqrt(2.0 * exposure_seconds)
    )
    differential_factor = np.ones_like(scint_fraction, dtype=float)
    positive_comparison_mask = comparison_star_count > 0
    differential_factor[positive_comparison_mask] = np.sqrt(
        1.0 + (1.0 / comparison_star_count[positive_comparison_mask])
    )
    scint_mag_error = MAG_ERROR_SCALE * scint_fraction * differential_factor
    return _return_like_input(np.where(valid, scint_mag_error, np.nan))


def compute_total_mag_error(
    theoretical_error: np.ndarray | list[float] | float,
    empirical_scatter: np.ndarray | list[float] | float = 0.0,
    scintillation_error: np.ndarray | list[float] | float = 0.0,
) -> np.ndarray | float:
    """Combine theoretical, empirical, and scintillation terms in quadrature."""
    theoretical_error, empirical_scatter, scintillation_error = np.broadcast_arrays(
        _as_float_array(theoretical_error),
        _as_float_array(empirical_scatter),
        _as_float_array(scintillation_error),
    )
    valid = np.isfinite(theoretical_error) & (theoretical_error >= 0)
    empirical_scatter = np.where(np.isfinite(empirical_scatter) & (empirical_scatter >= 0), empirical_scatter, 0.0)
    scintillation_error = np.where(np.isfinite(scintillation_error) & (scintillation_error >= 0), scintillation_error, 0.0)
    total_error = np.sqrt((np.clip(theoretical_error, 0.0, None) ** 2) + (empirical_scatter ** 2) + (scintillation_error ** 2))
    return _return_like_input(np.where(valid, total_error, np.nan))


def _as_float_array(values: np.ndarray | list[float] | float) -> np.ndarray:
    return np.asarray(values, dtype=float)


def _return_like_input(values: np.ndarray) -> np.ndarray | float:
    if values.ndim == 0:
        return float(values)
    return values


if __name__ == "__main__":
    target_flux = np.array([14500.0, 15120.0, 14980.0])
    comparison_fluxes = np.array(
        [
            [21000.0, 19500.0, 22300.0],
            [20800.0, 19750.0, 22050.0],
            [21150.0, 19620.0, 22410.0],
        ]
    )

    target_flux_error = compute_flux_error(
        source_flux=target_flux,
        sky_background_per_pixel=120.0,
        aperture_pixel_count=78.0,
        sky_pixel_count=500.0,
        read_noise_electrons=4.5,
        dark_current_electrons_per_pixel_second=0.02,
        exposure_seconds=60.0,
        gain_electrons_per_adu=1.6,
    )
    comparison_flux_errors = compute_flux_error(
        source_flux=comparison_fluxes,
        sky_background_per_pixel=120.0,
        aperture_pixel_count=78.0,
        sky_pixel_count=500.0,
        read_noise_electrons=4.5,
        dark_current_electrons_per_pixel_second=0.02,
        exposure_seconds=60.0,
        gain_electrons_per_adu=1.6,
    )

    target_mag_error = flux_error_to_mag_error(target_flux, target_flux_error)
    ensemble_mag_error = compute_ensemble_mag_error(comparison_fluxes, comparison_flux_errors)
    theoretical_diff_error = compute_differential_mag_error(target_mag_error, ensemble_mag_error)

    check_residuals = np.array([0.004, -0.003, 0.006, -0.002, 0.001, -0.005, 0.003])
    empirical_scatter = compute_empirical_scatter(check_residuals)
    scintillation_error = compute_scintillation_error(
        telescope_diameter_cm=28.0,
        exposure_seconds=60.0,
        airmass=np.array([1.15, 1.18, 1.21]),
        observatory_altitude_m=450.0,
        comparison_star_count=3,
    )
    total_error = compute_total_mag_error(theoretical_diff_error, empirical_scatter, scintillation_error)

    print("Target mag error:", np.round(target_mag_error, 5))
    print("Ensemble mag error:", np.round(ensemble_mag_error, 5))
    print("Differential mag error:", np.round(theoretical_diff_error, 5))
    print("Total mag error:", np.round(total_error, 5))