from __future__ import annotations



from dataclasses import dataclass

import math

from pathlib import Path



import numpy as np

from astropy.stats import SigmaClip, sigma_clipped_stats

from astropy.wcs import WCS

from photutils.aperture import CircularAnnulus, CircularAperture, ApertureStats, aperture_photometry



from photometry_app.core.image_io import read_header, read_image_data, read_photometry_image_data

from photometry_app.core.models import (

    CatalogStar,

    FileScanResult,

    ManualPhotometryConfig,

    ManualSourceConfig,

    ManualSourceRole,

    NEAR_SATURATION_FRACTION,

    PhotometryApertureMode,

    PhotometryMeasurement,

    RecenterMode,

)

from photometry_app.core.settings import AppSettings





@dataclass(slots=True)

class ApertureProfile:

    aperture_radius: float

    annulus_inner_radius: float

    annulus_outer_radius: float

    mode: PhotometryApertureMode

    fwhm_pixels: float | None = None

    note: str | None = None





@dataclass(slots=True)

class PhotometryFrameContext:

    source_path: Path

    wcs_path: Path

    source_header: object

    data: np.ndarray

    wcs: WCS

    background_median: float

    background_std: float

    sigma_clip: SigmaClip

    saturation_threshold: float | None

    is_2d: bool = True




_AUTO_RECENTER_MAX_SHIFT_PIXELS = 8.0




def resolve_aperture_profile(

    source_path: Path,

    wcs_path: Path,

    variable_stars: list[CatalogStar],

    reference_stars: list[CatalogStar],

    settings: AppSettings,

) -> ApertureProfile:

    fixed_profile = ApertureProfile(

        aperture_radius=settings.aperture_radius_pixels,

        annulus_inner_radius=settings.annulus_inner_radius_pixels,

        annulus_outer_radius=settings.annulus_outer_radius_pixels,

        mode=PhotometryApertureMode.FIXED,

    )

    if settings.photometry_aperture_mode != PhotometryApertureMode.FWHM_SCALED:

        return fixed_profile



    header = read_header(wcs_path)

    data = read_photometry_image_data(source_path)

    if data.ndim != 2:

        fixed_profile.note = "Adaptive aperture sizing fell back to fixed pixel radii because the image is not 2D."

        return fixed_profile



    wcs = WCS(header)

    frame_fwhm = _estimate_frame_fwhm(data, wcs, [*reference_stars, *variable_stars])

    if frame_fwhm is None:

        fixed_profile.note = "Adaptive aperture sizing fell back to fixed pixel radii because no reliable FWHM estimate was available."

        return fixed_profile



    aperture_radius, annulus_inner_radius, annulus_outer_radius = _adaptive_aperture_radii(settings, frame_fwhm)

    return ApertureProfile(

        aperture_radius=aperture_radius,

        annulus_inner_radius=annulus_inner_radius,

        annulus_outer_radius=annulus_outer_radius,

        mode=PhotometryApertureMode.FWHM_SCALED,

        fwhm_pixels=frame_fwhm,

        note=(

            f"Adaptive aperture sizing uses local source FWHM with frame FWHM {frame_fwhm:.2f} px as fallback; "

            f"frame fallback radii are aperture {aperture_radius:.2f} px and annulus {annulus_inner_radius:.2f}-{annulus_outer_radius:.2f} px."

        ),

    )





def measure_targets(

    source_path: Path,

    scan_result: FileScanResult,

    wcs_path: Path,

    variable_stars: list[CatalogStar],

    reference_stars: list[CatalogStar],

    aperture_radius: float = 5.0,

    annulus_inner_radius: float = 8.0,

    annulus_outer_radius: float = 12.0,

    frame_edge_margin_percent: float = 5.0,

    saturation_filter_enabled: bool = True,

    settings: AppSettings | None = None,

    frame_context: PhotometryFrameContext | None = None,

    adaptive_frame_fwhm_pixels: float | None = None,

) -> list[PhotometryMeasurement]:

    if frame_context is None:

        header = read_header(wcs_path)

        source_header = read_header(source_path)

        data = read_photometry_image_data(source_path)

        is_2d = data.ndim == 2

        if is_2d:

            wcs = WCS(header)

            _, background_median, background_std = sigma_clipped_stats(data, sigma=3.0)

            sigma_clip = SigmaClip(sigma=3.0)

            saturation_threshold = _resolve_saturation_threshold(source_header, data, source_path)

        else:

            wcs = WCS(header)

            background_median = 0.0

            background_std = 0.0

            sigma_clip = SigmaClip(sigma=3.0)

            saturation_threshold = None

    else:

        source_header = frame_context.source_header

        data = frame_context.data

        is_2d = bool(frame_context.is_2d and data.ndim == 2)

        wcs = frame_context.wcs

        background_median = float(frame_context.background_median)

        background_std = float(frame_context.background_std)

        sigma_clip = frame_context.sigma_clip

        saturation_threshold = frame_context.saturation_threshold



    if not is_2d:

        return [

            _flagged_measurement(source_path, scan_result, variable_stars[0], "Non-2D image.", True, False)

        ] if variable_stars else []



    fixed_aperture_radius = max(1.0, float(aperture_radius))

    fixed_annulus_inner_radius = max(fixed_aperture_radius + 0.5, float(annulus_inner_radius))

    fixed_annulus_outer_radius = max(fixed_annulus_inner_radius + 0.5, float(annulus_outer_radius))

    adaptive_settings = settings if settings is not None and settings.photometry_aperture_mode == PhotometryApertureMode.FWHM_SCALED else None

    frame_fwhm = _valid_fwhm_value(adaptive_frame_fwhm_pixels)

    if adaptive_settings is not None and frame_fwhm is None:

        frame_fwhm = _estimate_frame_fwhm(data, wcs, [*reference_stars, *variable_stars])

    if adaptive_settings is not None and frame_fwhm is not None:

        initial_aperture_radius, _, _ = _adaptive_aperture_radii(adaptive_settings, frame_fwhm)

    else:

        initial_aperture_radius = fixed_aperture_radius



    measurements: list[PhotometryMeasurement] = []

    for star in [*variable_stars, *reference_stars]:

        predicted_x, predicted_y = _coerce_pixel_coordinates(*wcs.world_to_pixel_values(star.ra_deg, star.dec_deg))

        x, y, flags = _resolve_catalog_source_position(data, predicted_x, predicted_y, initial_aperture_radius)

        source_aperture_radius = fixed_aperture_radius

        source_annulus_inner_radius = fixed_annulus_inner_radius

        source_annulus_outer_radius = fixed_annulus_outer_radius

        if adaptive_settings is not None:

            source_fwhm = _estimate_star_fwhm(data, x, y)

            if source_fwhm is None:

                source_fwhm = frame_fwhm

            if source_fwhm is not None:

                source_aperture_radius, source_annulus_inner_radius, source_annulus_outer_radius = _adaptive_aperture_radii(adaptive_settings, source_fwhm)

        centroid_shift = math.hypot(x - predicted_x, y - predicted_y)

        usable_margin = _usable_image_margin(data.shape, source_annulus_outer_radius, frame_edge_margin_percent)

        if not _inside_image(x, y, data.shape, usable_margin):

            flags.append(_usable_area_flag(frame_edge_margin_percent))

            measurements.append(

                _build_measurement(

                    source_path,

                    scan_result,

                    star,

                    x,

                    y,

                    None,

                    None,

                    None,

                    None,

                    star.is_variable,

                    not star.is_variable,

                    flags,

                    aperture_radius=source_aperture_radius,

                    annulus_inner_radius=source_annulus_inner_radius,

                    annulus_outer_radius=source_annulus_outer_radius,

                    centroid_shift_pixels=centroid_shift,

                )

            )

            continue



        positions = [(x, y)]

        aperture = CircularAperture(positions, r=source_aperture_radius)

        annulus = CircularAnnulus(positions, r_in=source_annulus_inner_radius, r_out=source_annulus_outer_radius)

        annulus_stats = ApertureStats(data, annulus, sigma_clip=sigma_clip)

        local_background = float(annulus_stats.median[0]) if np.isfinite(annulus_stats.median[0]) else float(background_median)

        local_background_scatter = _coerce_stat_value(annulus_stats.std[0])

        peak_pixel_value, saturated_pixel_count, is_saturated = _measure_source_saturation(

            data,

            x,

            y,

            source_aperture_radius,

            saturation_threshold,

        )

        if _is_near_saturated(peak_pixel_value, saturation_threshold, is_saturated):

            flags.append(_near_saturation_flag(peak_pixel_value, saturation_threshold))

        if saturation_filter_enabled and is_saturated:

            flags.append(_saturation_flag(peak_pixel_value, saturation_threshold, saturated_pixel_count))



        phot_table = aperture_photometry(data, aperture)

        aperture_sum = float(phot_table["aperture_sum"][0])

        background_corrected_flux = aperture_sum - local_background * aperture.area



        flux_error = None

        instrumental_magnitude = None

        snr = None

        if background_corrected_flux > 0:

            flux_error = _estimate_flux_error(background_corrected_flux, aperture.area, annulus.area, annulus_stats.std[0], background_std)

            instrumental_magnitude = -2.5 * math.log10(background_corrected_flux)

            if flux_error is not None and flux_error > 0:

                snr = background_corrected_flux / flux_error

        else:

            flags.append("Non-positive background-subtracted flux.")



        measurements.append(

            _build_measurement(

                source_path,

                scan_result,

                star,

                x,

                y,

                background_corrected_flux,

                flux_error,

                instrumental_magnitude,

                None,

                star.is_variable,

                not star.is_variable,

                flags,

                aperture_radius=source_aperture_radius,

                annulus_inner_radius=source_annulus_inner_radius,

                annulus_outer_radius=source_annulus_outer_radius,

                snr=snr,

                background_level=local_background,

                background_scatter=local_background_scatter,

                centroid_shift_pixels=centroid_shift,

                peak_pixel_value=peak_pixel_value,

                saturation_threshold=saturation_threshold,

                saturated_pixel_count=saturated_pixel_count,

                is_saturated=is_saturated,

            )

        )



    return measurements





def _valid_fwhm_value(value: float | None) -> float | None:

    if value is None:

        return None

    try:

        fwhm = float(value)

    except (TypeError, ValueError):

        return None

    if not np.isfinite(fwhm) or fwhm <= 0.0:

        return None

    return fwhm




def _adaptive_aperture_radii(settings: AppSettings, fwhm_pixels: float) -> tuple[float, float, float]:

    fwhm = _valid_fwhm_value(fwhm_pixels)

    if fwhm is None:

        return settings.aperture_radius_pixels, settings.annulus_inner_radius_pixels, settings.annulus_outer_radius_pixels

    aperture_radius = max(1.0, settings.aperture_radius_fwhm_scale * fwhm)

    annulus_inner_radius = max(aperture_radius + 1.0, settings.annulus_inner_radius_fwhm_scale * fwhm)

    annulus_outer_radius = max(annulus_inner_radius + 1.0, settings.annulus_outer_radius_fwhm_scale * fwhm)

    return aperture_radius, annulus_inner_radius, annulus_outer_radius




def measure_manual_sources(

    source_path: Path,

    scan_result: FileScanResult,

    wcs_path: Path,

    manual_config: ManualPhotometryConfig,

    frame_edge_margin_percent: float = 5.0,

    saturation_filter_enabled: bool = True,

) -> list[PhotometryMeasurement]:

    header = read_header(wcs_path)

    source_header = read_header(source_path)

    data = read_photometry_image_data(source_path)



    if data.ndim != 2:

        target = manual_config.target_source

        if target is None:

            return []

        return [_flagged_manual_measurement(source_path, scan_result, target, "Non-2D image.")]



    wcs = WCS(header)

    _, background_median, background_std = sigma_clipped_stats(data, sigma=3.0)

    sigma_clip = SigmaClip(sigma=3.0)

    saturation_threshold = _resolve_saturation_threshold(source_header, data, source_path)

    comparison_ids = [item.source_id for item in manual_config.comparison_sources]

    comparison_names = [item.name for item in manual_config.comparison_sources]



    measurements: list[PhotometryMeasurement] = []

    for source in manual_config.sources:

        predicted_x, predicted_y = _coerce_pixel_coordinates(*wcs.world_to_pixel_values(source.ra_deg, source.dec_deg))

        measured_x, measured_y, recenter_flags = _resolve_manual_source_position(data, predicted_x, predicted_y, source, manual_config)

        flags = list(recenter_flags)

        centroid_shift = math.hypot(measured_x - predicted_x, measured_y - predicted_y)

        usable_margin = _usable_image_margin(data.shape, source.annulus_outer_radius, frame_edge_margin_percent)

        if not _inside_image(measured_x, measured_y, data.shape, usable_margin):

            flags.append(_usable_area_flag(frame_edge_margin_percent))

            measurements.append(

                _build_manual_measurement(

                    source_path,

                    scan_result,

                    source,

                    measured_x,

                    measured_y,

                    None,

                    None,

                    None,

                    None,

                    flags,

                    comparison_ids if source.role != ManualSourceRole.COMPARISON else [],

                    comparison_names if source.role != ManualSourceRole.COMPARISON else [],

                    centroid_shift_pixels=centroid_shift,

                )

            )

            continue



        aperture = CircularAperture([(measured_x, measured_y)], r=source.aperture_radius)

        annulus = CircularAnnulus([(measured_x, measured_y)], r_in=source.annulus_inner_radius, r_out=source.annulus_outer_radius)

        annulus_stats = ApertureStats(data, annulus, sigma_clip=sigma_clip)

        local_background = float(annulus_stats.median[0]) if np.isfinite(annulus_stats.median[0]) else float(background_median)

        local_background_scatter = _coerce_stat_value(annulus_stats.std[0])

        peak_pixel_value, saturated_pixel_count, is_saturated = _measure_source_saturation(

            data,

            measured_x,

            measured_y,

            source.aperture_radius,

            saturation_threshold,

        )

        if _is_near_saturated(peak_pixel_value, saturation_threshold, is_saturated):

            flags.append(_near_saturation_flag(peak_pixel_value, saturation_threshold))

        if saturation_filter_enabled and is_saturated:

            flags.append(_saturation_flag(peak_pixel_value, saturation_threshold, saturated_pixel_count))



        phot_table = aperture_photometry(data, aperture)

        aperture_sum = float(phot_table["aperture_sum"][0])

        background_corrected_flux = aperture_sum - local_background * aperture.area



        flux_error = None

        instrumental_magnitude = None

        snr = None

        if background_corrected_flux > 0:

            flux_error = _estimate_flux_error(background_corrected_flux, aperture.area, annulus.area, annulus_stats.std[0], background_std)

            instrumental_magnitude = -2.5 * math.log10(background_corrected_flux)

            if flux_error is not None and flux_error > 0:

                snr = background_corrected_flux / flux_error

        else:

            flags.append("Non-positive background-subtracted flux.")



        measurements.append(

            _build_manual_measurement(

                source_path,

                scan_result,

                source,

                measured_x,

                measured_y,

                background_corrected_flux,

                flux_error,

                instrumental_magnitude,

                None,

                flags,

                comparison_ids if source.role != ManualSourceRole.COMPARISON else [],

                comparison_names if source.role != ManualSourceRole.COMPARISON else [],

                snr=snr,

                background_level=local_background,

                background_scatter=local_background_scatter,

                centroid_shift_pixels=centroid_shift,

                peak_pixel_value=peak_pixel_value,

                saturation_threshold=saturation_threshold,

                saturated_pixel_count=saturated_pixel_count,

                is_saturated=is_saturated,

            )

        )



    return measurements





def _estimate_frame_fwhm(data: np.ndarray, wcs: WCS, stars: list[CatalogStar], max_samples: int = 24) -> float | None:

    ordered_stars = sorted(

        stars,

        key=lambda star: (

            star.magnitude is None,

            star.magnitude if star.magnitude is not None else float("inf"),

            star.name.lower(),

        ),

    )

    fwhm_values: list[float] = []

    seen_sources: set[str] = set()

    for star in ordered_stars:

        if star.source_id in seen_sources:

            continue

        seen_sources.add(star.source_id)

        x, y = _coerce_pixel_coordinates(*wcs.world_to_pixel_values(star.ra_deg, star.dec_deg))

        fwhm = _estimate_star_fwhm(data, x, y)

        if fwhm is None:

            continue

        fwhm_values.append(fwhm)

        if len(fwhm_values) >= max_samples:

            break



    if not fwhm_values:

        return None

    return float(np.median(fwhm_values))





def _estimate_star_fwhm(data: np.ndarray, x: float, y: float, half_size: int = 7) -> float | None:

    if not _inside_image(x, y, data.shape, half_size + 1):

        return None



    x_center = int(round(x))

    y_center = int(round(y))

    y_start = y_center - half_size

    y_end = y_center + half_size + 1

    x_start = x_center - half_size

    x_end = x_center + half_size + 1

    cutout = np.asarray(data[y_start:y_end, x_start:x_end], dtype=float)

    if cutout.size == 0:

        return None



    _, local_background, local_background_std = sigma_clipped_stats(cutout, sigma=3.0)

    signal = cutout - float(local_background)

    signal[signal < 0] = 0

    peak_signal = float(np.max(signal)) if signal.size else 0.0

    core_threshold = max(peak_signal * 0.03, float(local_background_std) * 1.5)

    core_signal = np.where(signal >= core_threshold, signal, 0.0)

    total_signal = float(core_signal.sum())

    if total_signal <= 0:

        return None



    y_indices, x_indices = np.indices(core_signal.shape, dtype=float)

    x_centroid = float((core_signal * x_indices).sum() / total_signal)

    y_centroid = float((core_signal * y_indices).sum() / total_signal)

    if abs(x_centroid - half_size) > 2.0 or abs(y_centroid - half_size) > 2.0:

        return None



    radial_distances = np.hypot(x_indices - x_centroid, y_indices - y_centroid).ravel()

    radial_signal = signal.ravel()

    center_signal = radial_signal[radial_distances <= 1.0]

    peak_estimate = float(np.percentile(center_signal, 90.0)) if center_signal.size else peak_signal

    if peak_estimate > 0.0:

        half_max = peak_estimate * 0.5

        bin_edges = np.arange(0.0, float(half_size) + 1.0, 0.5)

        previous_radius = 0.0

        previous_signal = peak_estimate

        for lower_radius, upper_radius in zip(bin_edges, bin_edges[1:]):

            mask = (radial_distances >= lower_radius) & (radial_distances < upper_radius)

            if int(np.count_nonzero(mask)) < 3:

                continue

            radius = (float(lower_radius) + float(upper_radius)) * 0.5

            annulus_signal = float(np.median(radial_signal[mask]))

            if radius < 0.75:

                previous_radius = radius

                previous_signal = annulus_signal

                continue

            if annulus_signal <= half_max:

                if previous_signal == annulus_signal:

                    radial_fwhm = 2.0 * radius

                else:

                    fraction = (half_max - annulus_signal) / (previous_signal - annulus_signal)

                    radial_fwhm = 2.0 * (radius + (fraction * (previous_radius - radius)))

                if np.isfinite(radial_fwhm) and 0.8 <= radial_fwhm <= (half_size * 2):

                    return float(radial_fwhm)

                break

            previous_radius = radius

            previous_signal = annulus_signal



    sigma_x_sq = float((core_signal * ((x_indices - x_centroid) ** 2)).sum() / total_signal)

    sigma_y_sq = float((core_signal * ((y_indices - y_centroid) ** 2)).sum() / total_signal)

    sigma = math.sqrt(max(0.0, (sigma_x_sq + sigma_y_sq) / 2.0))

    fwhm = 2.354820045 * sigma

    if not np.isfinite(fwhm) or fwhm < 0.8 or fwhm > (half_size * 2):

        return None

    return float(fwhm)





def _coerce_pixel_coordinates(x: object, y: object) -> tuple[float, float]:

    x_value = float(np.asarray(x, dtype=float).reshape(-1)[0])

    y_value = float(np.asarray(y, dtype=float).reshape(-1)[0])

    return x_value, y_value





def _estimate_flux_error(

    background_corrected_flux: float,

    aperture_area: float,

    annulus_area: float,

    local_background_std: object,

    fallback_background_std: float,

) -> float:

    local_sigma = _coerce_stat_value(local_background_std)

    if local_sigma is None or local_sigma <= 0:

        local_sigma = max(0.0, float(fallback_background_std))

    aperture_area_value = max(1.0, float(aperture_area))

    annulus_area_value = max(1.0, float(annulus_area))

    background_variance = aperture_area_value * (local_sigma ** 2) * (1.0 + (aperture_area_value / annulus_area_value))

    return math.sqrt(max(0.0, abs(background_corrected_flux)) + max(0.0, background_variance))





def _coerce_stat_value(value: object) -> float | None:

    try:

        scalar = float(np.asarray(value, dtype=float).reshape(-1)[0])

    except (TypeError, ValueError, IndexError):

        return None

    if not np.isfinite(scalar):

        return None

    return scalar





def _resolve_saturation_threshold(header: object, data: np.ndarray, source_path: Path) -> float | None:

    for keyword in ("SATURATE", "SATURATION", "SATLEVEL", "SATMAX", "DATAMAX", "MAXLIN", "MAXADU", "CLIPLEVEL"):

        try:

            value = header.get(keyword)

        except AttributeError:

            value = None

        threshold = _coerce_stat_value(value)

        if threshold is not None and threshold > 0:

            return threshold



    finite_values = np.asarray(data[np.isfinite(data)], dtype=float)

    if finite_values.size == 0:

        return None

    finite_max = float(np.max(finite_values))

    if finite_max <= 0:

        return None



    try:

        bitpix = int(header.get("BITPIX", 0))

    except Exception:

        bitpix = 0

    bzero = _coerce_stat_value(getattr(header, "get", lambda *args, **kwargs: None)("BZERO", 0.0)) or 0.0

    if bitpix == 8:

        return 255.0

    if bitpix == 16:

        return 65535.0 if bzero >= 32768.0 else 32767.0

    if source_path.suffix.lower() == ".xisf":

        return 65535.0

    if finite_max > 60000.0:

        return 65535.0

    if finite_max > 30000.0:

        return 32767.0

    return None





def _measure_source_saturation(

    data: np.ndarray,

    x: float,

    y: float,

    aperture_radius: float,

    saturation_threshold: float | None,

) -> tuple[float | None, int, bool]:

    if saturation_threshold is None or saturation_threshold <= 0 or not np.isfinite(saturation_threshold):

        return None, 0, False

    half_size = max(2, int(math.ceil(aperture_radius)))

    x_center = int(round(x))

    y_center = int(round(y))

    y_start = max(0, y_center - half_size)

    y_end = min(data.shape[0], y_center + half_size + 1)

    x_start = max(0, x_center - half_size)

    x_end = min(data.shape[1], x_center + half_size + 1)

    cutout = np.asarray(data[y_start:y_end, x_start:x_end], dtype=float)

    if cutout.size == 0:

        return None, 0, False



    y_indices, x_indices = np.indices(cutout.shape, dtype=float)

    local_x_center = float(x - x_start)

    local_y_center = float(y - y_start)

    aperture_mask = ((x_indices - local_x_center) ** 2) + ((y_indices - local_y_center) ** 2) <= (aperture_radius ** 2)

    aperture_pixels = cutout[aperture_mask]

    if aperture_pixels.size == 0:

        return None, 0, False

    finite_pixels = aperture_pixels[np.isfinite(aperture_pixels)]

    if finite_pixels.size == 0:

        return None, 0, False

    peak_pixel_value = float(np.max(finite_pixels))

    saturated_pixel_count = int(np.count_nonzero(finite_pixels >= saturation_threshold))

    return peak_pixel_value, saturated_pixel_count, saturated_pixel_count > 0





def _saturation_flag(peak_pixel_value: float | None, saturation_threshold: float | None, saturated_pixel_count: int) -> str:

    if peak_pixel_value is None or saturation_threshold is None:

        return "Saturated source core detected."

    return (

        f"Saturated source core detected (peak {peak_pixel_value:.0f} >= {saturation_threshold:.0f}; "

        f"{saturated_pixel_count} saturated pixel(s))."

    )





def _is_near_saturated(

    peak_pixel_value: float | None,

    saturation_threshold: float | None,

    is_saturated: bool,

) -> bool:

    if is_saturated or peak_pixel_value is None or saturation_threshold is None:

        return False

    if saturation_threshold <= 0 or not np.isfinite(saturation_threshold):

        return False

    return bool(peak_pixel_value >= (saturation_threshold * NEAR_SATURATION_FRACTION))





def _near_saturation_flag(peak_pixel_value: float | None, saturation_threshold: float | None) -> str:

    if peak_pixel_value is None or saturation_threshold is None:

        return "Source core is near saturation."

    return (

        f"Source core is near saturation (peak {peak_pixel_value:.0f} is "

        f"{(peak_pixel_value / saturation_threshold) * 100.0:.1f}% of {saturation_threshold:.0f})."

    )





def _inside_image(x: float, y: float, shape: tuple[int, int], margin: float | tuple[float, float]) -> bool:

    height, width = shape

    if isinstance(margin, tuple):

        margin_x, margin_y = margin

    else:

        margin_x = margin_y = margin

    return margin_x <= x < (width - margin_x) and margin_y <= y < (height - margin_y)





def _usable_image_margin(shape: tuple[int, int], annulus_outer_radius: float, frame_edge_margin_percent: float) -> tuple[float, float]:

    height, width = shape

    fraction = min(0.49, max(0.0, frame_edge_margin_percent / 100.0))

    return max(float(annulus_outer_radius), width * fraction), max(float(annulus_outer_radius), height * fraction)





def _usable_area_flag(frame_edge_margin_percent: float) -> str:

    if frame_edge_margin_percent <= 0:

        return "Target lies outside the usable image area."

    return f"Target lies outside the usable image area (configured {frame_edge_margin_percent:.1f}% edge margin)."





def _resolve_catalog_source_position(

    data: np.ndarray,

    predicted_x: float,

    predicted_y: float,

    aperture_radius: float,

) -> tuple[float, float, list[str]]:

    search_radius = max(4.0, min(_AUTO_RECENTER_MAX_SHIFT_PIXELS, aperture_radius))

    centroid = _centroid_source(data, predicted_x, predicted_y, search_radius)

    if centroid is None:

        return predicted_x, predicted_y, ["Auto centroid recenter failed; used WCS-predicted position."]



    centroid_x, centroid_y = centroid

    shift = math.hypot(centroid_x - predicted_x, centroid_y - predicted_y)

    if shift > _AUTO_RECENTER_MAX_SHIFT_PIXELS:

        return predicted_x, predicted_y, [

            f"Auto centroid shift {shift:.2f} px exceeded the max recenter radius; used WCS-predicted position."

        ]

    return centroid_x, centroid_y, []





def _resolve_manual_source_position(

    data: np.ndarray,

    predicted_x: float,

    predicted_y: float,

    source: ManualSourceConfig,

    manual_config: ManualPhotometryConfig,

) -> tuple[float, float, list[str]]:

    if manual_config.recenter_mode == RecenterMode.NONE:

        return predicted_x, predicted_y, []



    search_radius = max(source.aperture_radius * 2.0, manual_config.max_recenter_radius_pixels)

    centroid = _centroid_source(data, predicted_x, predicted_y, search_radius)

    if centroid is None:

        if manual_config.fallback_to_wcs_on_centroid_failure:

            return predicted_x, predicted_y, ["Centroid recenter failed; used WCS-predicted position."]

        return predicted_x, predicted_y, ["Centroid recenter failed; measurement left flagged because WCS fallback is disabled."]



    centroid_x, centroid_y = centroid

    shift = math.hypot(centroid_x - predicted_x, centroid_y - predicted_y)

    if manual_config.recenter_mode == RecenterMode.CENTROID_LIMITED and shift > manual_config.max_recenter_radius_pixels:

        if manual_config.fallback_to_wcs_on_centroid_failure:

            return predicted_x, predicted_y, [

                f"Centroid shift {shift:.2f} px exceeded the max recenter radius; used WCS-predicted position."

            ]

        return predicted_x, predicted_y, [

            f"Centroid shift {shift:.2f} px exceeded the max recenter radius; measurement left flagged because WCS fallback is disabled."

        ]

    return centroid_x, centroid_y, []





def _centroid_source(data: np.ndarray, x: float, y: float, search_radius: float) -> tuple[float, float] | None:

    half_size = max(3, int(math.ceil(search_radius)))

    if not _inside_image(x, y, data.shape, half_size + 1):

        return None



    x_center = int(round(x))

    y_center = int(round(y))

    y_start = y_center - half_size

    y_end = y_center + half_size + 1

    x_start = x_center - half_size

    x_end = x_center + half_size + 1

    cutout = np.asarray(data[y_start:y_end, x_start:x_end], dtype=float)

    if cutout.size == 0:

        return None



    _, local_background, _ = sigma_clipped_stats(cutout, sigma=3.0)

    signal = cutout - float(local_background)

    signal[signal < 0] = 0

    total_signal = float(signal.sum())

    if total_signal <= 0:

        return None



    y_indices, x_indices = np.indices(signal.shape, dtype=float)

    x_centroid = float((signal * x_indices).sum() / total_signal)

    y_centroid = float((signal * y_indices).sum() / total_signal)

    return x_start + x_centroid, y_start + y_centroid





def _flagged_measurement(

    source_path: Path,

    scan_result: FileScanResult,

    star: CatalogStar,

    reason: str,

    is_variable: bool,

    is_reference: bool,

) -> PhotometryMeasurement:

    return _build_measurement(source_path, scan_result, star, float("nan"), float("nan"), None, None, None, None, is_variable, is_reference, [reason])





def _flagged_manual_measurement(

    source_path: Path,

    scan_result: FileScanResult,

    source: ManualSourceConfig,

    reason: str,

) -> PhotometryMeasurement:

    return _build_manual_measurement(

        source_path,

        scan_result,

        source,

        float("nan"),

        float("nan"),

        None,

        None,

        None,

        None,

        [reason],

        [],

        [],

    )





def _build_measurement(

    source_path: Path,

    scan_result: FileScanResult,

    star: CatalogStar,

    x: float,

    y: float,

    flux: float | None,

    flux_error: float | None,

    instrumental_magnitude: float | None,

    differential_magnitude: float | None,

    is_variable: bool,

    is_reference: bool,

    flags: list[str],

    aperture_radius: float | None = None,

    annulus_inner_radius: float | None = None,

    annulus_outer_radius: float | None = None,

    snr: float | None = None,

    background_level: float | None = None,

    background_scatter: float | None = None,

    centroid_shift_pixels: float | None = None,

    peak_pixel_value: float | None = None,

    saturation_threshold: float | None = None,

    saturated_pixel_count: int = 0,

    is_saturated: bool = False,

) -> PhotometryMeasurement:

    quality_weight = None

    if flux_error is not None and flux_error > 0:

        quality_weight = 1.0 / (flux_error * flux_error)

    return PhotometryMeasurement(

        source_id=star.source_id,

        source_name=star.name,

        catalog=star.catalog,

        object_name=scan_result.object_folder,

        file_path=source_path,

        observation_time=scan_result.metadata.date_obs,

        filter_name=scan_result.metadata.filter_name,

        ra_deg=star.ra_deg,

        dec_deg=star.dec_deg,

        x=float(x),

        y=float(y),

        flux=flux,

        flux_error=flux_error,

        instrumental_magnitude=instrumental_magnitude,

        differential_magnitude=differential_magnitude,

        is_variable=is_variable,

        is_reference=is_reference,

        catalog_magnitude=star.magnitude,

        flags=flags,

        aperture_radius=aperture_radius,

        annulus_inner_radius=annulus_inner_radius,

        annulus_outer_radius=annulus_outer_radius,

        snr=snr,

        background_level=background_level,

        background_scatter=background_scatter,

        centroid_shift_pixels=centroid_shift_pixels,

        peak_pixel_value=peak_pixel_value,

        saturation_threshold=saturation_threshold,

        saturated_pixel_count=saturated_pixel_count,

        is_saturated=is_saturated,

        quality_weight=quality_weight,

    )





def _build_manual_measurement(

    source_path: Path,

    scan_result: FileScanResult,

    source: ManualSourceConfig,

    x: float,

    y: float,

    flux: float | None,

    flux_error: float | None,

    instrumental_magnitude: float | None,

    differential_magnitude: float | None,

    flags: list[str],

    comparison_source_ids: list[str],

    comparison_source_names: list[str],

    snr: float | None = None,

    background_level: float | None = None,

    background_scatter: float | None = None,

    centroid_shift_pixels: float | None = None,

    peak_pixel_value: float | None = None,

    saturation_threshold: float | None = None,

    saturated_pixel_count: int = 0,

    is_saturated: bool = False,

) -> PhotometryMeasurement:

    quality_weight = None

    if flux_error is not None and flux_error > 0:

        quality_weight = 1.0 / (flux_error * flux_error)

    return PhotometryMeasurement(

        source_id=source.source_id,

        source_name=source.name,

        catalog="manual",

        object_name=scan_result.object_folder,

        file_path=source_path,

        observation_time=scan_result.metadata.date_obs,

        filter_name=scan_result.metadata.filter_name,

        ra_deg=source.ra_deg,

        dec_deg=source.dec_deg,

        x=float(x),

        y=float(y),

        flux=flux,

        flux_error=flux_error,

        instrumental_magnitude=instrumental_magnitude,

        differential_magnitude=differential_magnitude,

        is_variable=source.role != ManualSourceRole.COMPARISON,

        is_reference=source.role == ManualSourceRole.COMPARISON,

        catalog_magnitude=None,

        is_check=source.role == ManualSourceRole.CHECK,

        flags=flags,

        comparison_source_ids=list(comparison_source_ids),

        comparison_source_names=list(comparison_source_names),

        aperture_radius=source.aperture_radius,

        annulus_inner_radius=source.annulus_inner_radius,

        annulus_outer_radius=source.annulus_outer_radius,

        snr=snr,

        background_level=background_level,

        background_scatter=background_scatter,

        centroid_shift_pixels=centroid_shift_pixels,

        peak_pixel_value=peak_pixel_value,

        saturation_threshold=saturation_threshold,

        saturated_pixel_count=saturated_pixel_count,

        is_saturated=is_saturated,

        quality_weight=quality_weight,

    )

