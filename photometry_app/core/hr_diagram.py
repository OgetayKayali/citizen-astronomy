from __future__ import annotations



from concurrent.futures import ThreadPoolExecutor, as_completed

from dataclasses import asdict, dataclass, field

import math

import os

from pathlib import Path

from typing import Callable



import numpy as np

from astropy.coordinates import SkyCoord

from astropy.stats import SigmaClip, sigma_clipped_stats

from astropy import units as u

from astropy.wcs import WCS

from astropy.wcs.utils import proj_plane_pixel_scales

from photutils.aperture import ApertureStats, CircularAnnulus, CircularAperture, aperture_photometry

from photutils.detection import DAOStarFinder



from photometry_app.core.image_io import read_header, read_photometry_image_data

from photometry_app.core.models import CatalogStar, ObservationMetadata, PhotometryMeasurement

from photometry_app.core.photometry import (

    _coerce_pixel_coordinates,

    _coerce_stat_value,

    _estimate_flux_error,

    _inside_image,

    _is_near_saturated,

    _measure_source_saturation,

    _near_saturation_flag,

    _resolve_catalog_source_position,

    _resolve_saturation_threshold,

    _saturation_flag,

    _usable_area_flag,

    _usable_image_margin,

)

from photometry_app.core.settings import AppSettings, resolve_shared_parallel_workers





@dataclass(slots=True)

class HrMeasurementRow:

    source_id: str

    source_name: str

    catalog: str

    ra_deg: float

    dec_deg: float

    gaia_g_mag: float | None

    gaia_bp_rp: float | None

    parallax_mas: float | None

    parallax_error_mas: float | None

    x: float

    y: float

    aperture_radius: float

    annulus_inner_radius: float

    annulus_outer_radius: float

    pm_ra_mas_per_year: float | None = None

    pm_dec_mas_per_year: float | None = None

    flux_red: float | None = None

    flux_green: float | None = None

    flux_blue: float | None = None

    flux_luminance: float | None = None

    flux_error_red: float | None = None

    flux_error_green: float | None = None

    flux_error_blue: float | None = None

    flux_error_luminance: float | None = None

    instrumental_mag_red: float | None = None

    instrumental_mag_green: float | None = None

    instrumental_mag_blue: float | None = None

    instrumental_mag_luminance: float | None = None

    instrumental_blue_minus_red: float | None = None

    calibrated_mag_luminance: float | None = None

    absolute_magnitude_proxy: float | None = None

    gaia_absolute_magnitude: float | None = None

    plot_color_index: float | None = None

    display_color_hex: str | None = None

    snr_red: float | None = None

    snr_green: float | None = None

    snr_blue: float | None = None

    snr_luminance: float | None = None

    background_red: float | None = None

    background_green: float | None = None

    background_blue: float | None = None

    background_luminance: float | None = None

    is_saturated: bool = False

    used_for_zero_point: bool = False

    flags: list[str] = field(default_factory=list)



    @property

    def proper_motion_magnitude_mas_per_year(self) -> float | None:

        pm_ra = _finite_float(self.pm_ra_mas_per_year)

        pm_dec = _finite_float(self.pm_dec_mas_per_year)

        if pm_ra is None and pm_dec is None:

            return None

        return float(math.hypot(pm_ra or 0.0, pm_dec or 0.0))





@dataclass(slots=True)

class HrWorkingTable:

    rows: list[HrMeasurementRow]

    plane_labels: list[str]

    measured_count: int

    usable_count: int

    zero_point_offset_mag: float | None = None

    zero_point_source_count: int = 0





@dataclass(slots=True)

class _DetectedSource:

    x: float

    y: float

    peak: float





@dataclass(slots=True)

class _MatchedDetectedSource:

    star: CatalogStar

    x: float

    y: float

    peak: float

    match_separation_arcsec: float





@dataclass(slots=True)

class _MeasuredHrRow:

    index: int

    row: HrMeasurementRow





def measure_hr_sources(

    source_path: Path,

    gaia_stars: list[CatalogStar],

    settings: AppSettings,

    metadata: ObservationMetadata | None = None,

    progress_callback: Callable[[str], None] | None = None,

) -> HrWorkingTable:

    header = read_header(source_path)

    data = read_photometry_image_data(source_path)

    planes = _extract_named_planes(data)

    centroid_plane = _centroid_plane_from_named_planes(planes)

    wcs = WCS(header)

    sigma_clip = SigmaClip(sigma=3.0)

    _, _, global_background_std = sigma_clipped_stats(centroid_plane, sigma=3.0)

    plane_background_levels = {

        label: float(sigma_clipped_stats(plane, sigma=3.0)[1])

        for label, plane in planes.items()

    }

    saturation_threshold = _resolve_saturation_threshold(header, centroid_plane, source_path)

    aperture_radius = max(1.0, float(settings.aperture_radius_pixels))

    annulus_inner_radius = max(aperture_radius + 0.5, float(settings.annulus_inner_radius_pixels))

    annulus_outer_radius = max(annulus_inner_radius + 0.5, float(settings.annulus_outer_radius_pixels))

    usable_margin = _usable_image_margin(centroid_plane.shape, annulus_outer_radius, settings.frame_edge_margin_percent)



    detected_sources = _detect_hr_image_sources(centroid_plane, aperture_radius, usable_margin)

    if progress_callback is not None:

        progress_callback(f"Detected {len(detected_sources)} image source(s) on the H-R source image.")

    matched_sources = _match_detected_sources_to_gaia(detected_sources, gaia_stars, wcs, aperture_radius)

    if progress_callback is not None:

        progress_callback(

            f"Matched {len(matched_sources)} detected image source(s) to Gaia within the H-R cross-match radius."

        )



    max_sources = max(0, int(getattr(settings, "hr_max_sources", 0)))

    if max_sources > 0 and len(matched_sources) > max_sources:

        matched_sources = matched_sources[:max_sources]

        if progress_callback is not None:

            progress_callback(f"Applied the H-R max-source cap: measuring the brightest {len(matched_sources)} matched source(s).")



    if progress_callback is not None:

        progress_callback(f"Preparing H-R measurements for {len(matched_sources)} Gaia-matched image source(s).")



    rows = _measure_hr_rows(

        matched_sources,

        planes,

        centroid_plane,

        sigma_clip,

        plane_background_levels,

        global_background_std,

        saturation_threshold,

        aperture_radius,

        annulus_inner_radius,

        annulus_outer_radius,

        usable_margin,

        settings,

        progress_callback=progress_callback,

    )



    usable_count = sum(1 for row in rows if row.flux_luminance is not None or any(getattr(row, f"flux_{label}") is not None for label in planes))

    if progress_callback is not None and rows:

        progress_callback(

            f"[H-R {len(rows)}/{len(rows)}] Finalizing measurements and building the H-R working table."

        )

    zero_point_offset_mag, zero_point_source_count = _apply_photometric_calibration(rows)

    if progress_callback is not None:

        progress_callback(

            "H-R source measurement complete: "

            f"{len(rows)} row(s) measured, {usable_count} usable, {zero_point_source_count} zero-point source(s)."

        )

    return HrWorkingTable(

        rows=rows,

        plane_labels=list(planes.keys()),

        measured_count=len(rows),

        usable_count=usable_count,

        zero_point_offset_mag=zero_point_offset_mag,

        zero_point_source_count=zero_point_source_count,

    )





@dataclass(slots=True)

class _PlaneMeasurement:

    flux: float | None

    flux_error: float | None

    instrumental_magnitude: float | None

    snr: float | None

    background_level: float | None

    flag: str | None = None





def _measure_plane(

    plane: np.ndarray,

    x: float,

    y: float,

    aperture_radius: float,

    annulus_inner_radius: float,

    annulus_outer_radius: float,

    sigma_clip: SigmaClip,

    global_background_level: float,

    global_background_std: float,

) -> _PlaneMeasurement:

    aperture = CircularAperture([(x, y)], r=aperture_radius)

    annulus = CircularAnnulus([(x, y)], r_in=annulus_inner_radius, r_out=annulus_outer_radius)

    annulus_stats = ApertureStats(plane, annulus, sigma_clip=sigma_clip)

    local_background = float(annulus_stats.median[0]) if np.isfinite(annulus_stats.median[0]) else global_background_level

    phot_table = aperture_photometry(plane, aperture)

    aperture_sum = float(phot_table["aperture_sum"][0])

    background_corrected_flux = aperture_sum - local_background * aperture.area

    if background_corrected_flux <= 0:

        return _PlaneMeasurement(

            flux=None,

            flux_error=None,

            instrumental_magnitude=None,

            snr=None,

            background_level=local_background,

            flag="Non-positive background-subtracted flux.",

        )

    flux_error = _estimate_flux_error(background_corrected_flux, aperture.area, annulus.area, annulus_stats.std[0], global_background_std)

    instrumental_magnitude = -2.5 * math.log10(background_corrected_flux) if background_corrected_flux > 0 else None

    snr = background_corrected_flux / flux_error if flux_error is not None and flux_error > 0 else None

    return _PlaneMeasurement(

        flux=background_corrected_flux,

        flux_error=flux_error,

        instrumental_magnitude=instrumental_magnitude,

        snr=snr,

        background_level=local_background,

    )





def hr_working_table_payload(table: HrWorkingTable) -> dict[str, object]:

    return {

        "plane_labels": list(table.plane_labels),

        "measured_count": table.measured_count,

        "usable_count": table.usable_count,

        "zero_point_offset_mag": table.zero_point_offset_mag,

        "zero_point_source_count": table.zero_point_source_count,

        "rows": [asdict(row) for row in table.rows],

    }





def _extract_named_planes(data: np.ndarray) -> dict[str, np.ndarray]:

    if data.ndim == 2:

        plane = np.asarray(data, dtype=float)

        return {"luminance": plane}

    if data.ndim != 3:

        raise ValueError("H-R source photometry currently supports 2D monochrome or 3-channel RGB image data.")



    if data.shape[-1] in {3, 4}:

        ordered = np.moveaxis(data[..., :3], -1, 0)

    elif data.shape[0] in {3, 4}:

        ordered = np.asarray(data[:3], dtype=float)

    else:

        raise ValueError("Could not determine the RGB channel axis for the selected H-R source image.")



    red_plane = np.asarray(ordered[0], dtype=float)

    green_plane = np.asarray(ordered[1], dtype=float)

    blue_plane = np.asarray(ordered[2], dtype=float)

    luminance_plane = np.mean(np.stack([red_plane, green_plane, blue_plane], axis=0), axis=0)

    return {

        "red": red_plane,

        "green": green_plane,

        "blue": blue_plane,

        "luminance": luminance_plane,

    }





def _centroid_plane_from_named_planes(planes: dict[str, np.ndarray]) -> np.ndarray:

    luminance_plane = planes.get("luminance")

    if luminance_plane is not None:

        return luminance_plane

    return next(iter(planes.values()))





def _coerce_bp_rp(metadata: dict[str, object]) -> float | None:

    return _coerce_stat_value(metadata.get("bp_rp"))





def _detect_hr_image_sources(

    centroid_plane: np.ndarray,

    aperture_radius: float,

    usable_margin: int,

) -> list[_DetectedSource]:

    _, background_median, background_std = sigma_clipped_stats(centroid_plane, sigma=3.0)

    if not np.isfinite(background_std) or background_std <= 0:

        return []



    detection_image = np.asarray(centroid_plane, dtype=float) - float(background_median)

    finder = DAOStarFinder(

        fwhm=max(2.0, min(8.0, aperture_radius * 0.8)),

        threshold=max(float(background_std) * 5.0, 1e-6),

        exclude_border=True,

    )

    detections = finder(detection_image)

    if detections is None or len(detections) == 0:

        return []



    rows: list[_DetectedSource] = []

    for row in detections:

        x = _coerce_stat_value(row.get("xcentroid"))

        y = _coerce_stat_value(row.get("ycentroid"))

        peak = _coerce_stat_value(row.get("peak"))

        if x is None or y is None or peak is None:

            continue

        if not _inside_image(x, y, centroid_plane.shape, usable_margin):

            continue

        rows.append(_DetectedSource(x=x, y=y, peak=peak))

    rows.sort(key=lambda item: item.peak, reverse=True)

    return rows





def _match_detected_sources_to_gaia(

    detected_sources: list[_DetectedSource],

    gaia_stars: list[CatalogStar],

    wcs: WCS,

    aperture_radius: float,

) -> list[_MatchedDetectedSource]:

    if not detected_sources or not gaia_stars:

        return []



    detected_x = np.asarray([source.x for source in detected_sources], dtype=float)

    detected_y = np.asarray([source.y for source in detected_sources], dtype=float)

    detected_ra, detected_dec = wcs.pixel_to_world_values(detected_x, detected_y)

    detected_coords = SkyCoord(detected_ra * u.deg, detected_dec * u.deg)

    gaia_coords = SkyCoord(

        np.asarray([star.ra_deg for star in gaia_stars], dtype=float) * u.deg,

        np.asarray([star.dec_deg for star in gaia_stars], dtype=float) * u.deg,

    )

    matched_indices, separations, _ = detected_coords.match_to_catalog_sky(gaia_coords)



    try:

        pixel_scale_arcsec = float((proj_plane_pixel_scales(wcs) * u.deg).mean().to_value(u.arcsec))

    except Exception:

        pixel_scale_arcsec = 1.5

    max_separation_arcsec = max(1.5, min(6.0, max(pixel_scale_arcsec * 2.5, aperture_radius)))



    best_matches: dict[str, _MatchedDetectedSource] = {}

    for detection, matched_index, separation in zip(detected_sources, matched_indices, separations, strict=False):

        separation_arcsec = float(separation.arcsecond)

        if not np.isfinite(separation_arcsec) or separation_arcsec > max_separation_arcsec:

            continue

        star = gaia_stars[int(matched_index)]

        candidate = _MatchedDetectedSource(

            star=star,

            x=detection.x,

            y=detection.y,

            peak=detection.peak,

            match_separation_arcsec=separation_arcsec,

        )

        existing = best_matches.get(star.source_id)

        if existing is None or candidate.match_separation_arcsec < existing.match_separation_arcsec:

            best_matches[star.source_id] = candidate



    return sorted(best_matches.values(), key=lambda item: item.peak, reverse=True)





def _coerce_parallax_mas(metadata: dict[str, object]) -> float | None:

    return _coerce_stat_value(metadata.get("parallax_mas"))





def _coerce_parallax_error_mas(metadata: dict[str, object]) -> float | None:

    return _coerce_stat_value(metadata.get("parallax_error_mas"))





def _coerce_pm_ra_mas_per_year(metadata: dict[str, object]) -> float | None:

    return _coerce_stat_value(metadata.get("pm_ra"))





def _coerce_pm_dec_mas_per_year(metadata: dict[str, object]) -> float | None:

    return _coerce_stat_value(metadata.get("pm_dec"))





def _measure_hr_rows(

    matched_sources: list[_MatchedDetectedSource],

    planes: dict[str, np.ndarray],

    centroid_plane: np.ndarray,

    sigma_clip: SigmaClip,

    plane_background_levels: dict[str, float],

    global_background_std: float,

    saturation_threshold: float | None,

    aperture_radius: float,

    annulus_inner_radius: float,

    annulus_outer_radius: float,

    usable_margin: int,

    settings: AppSettings,

    progress_callback: Callable[[str], None] | None = None,

) -> list[HrMeasurementRow]:

    total_count = len(matched_sources)

    if total_count == 0:

        return []



    worker_count = _resolve_hr_parallel_worker_count(settings, total_count)

    progress_step = max(1, total_count // 200)



    if worker_count <= 1:

        rows: list[HrMeasurementRow] = []

        for index, matched_source in enumerate(matched_sources, start=1):

            if progress_callback is not None and (index == 1 or index == total_count or index % progress_step == 0):

                progress_callback(f"[H-R {index}/{total_count}] Measuring Gaia-matched sources on the image.")

            measured = _measure_single_hr_row(

                index,

                matched_source,

                planes,

                centroid_plane,

                sigma_clip,

                plane_background_levels,

                global_background_std,

                saturation_threshold,

                aperture_radius,

                annulus_inner_radius,

                annulus_outer_radius,

                usable_margin,

                settings,

            )

            rows.append(measured.row)

        return rows



    if progress_callback is not None:

        progress_callback(f"Using {worker_count} worker threads for Gaia-matched H-R source measurement.")



    measured_rows: list[_MeasuredHrRow] = []

    completed_count = 0

    with ThreadPoolExecutor(max_workers=worker_count) as executor:

        futures = [

            executor.submit(

                _measure_single_hr_row,

                index,

                matched_source,

                planes,

                centroid_plane,

                sigma_clip,

                plane_background_levels,

                global_background_std,

                saturation_threshold,

                aperture_radius,

                annulus_inner_radius,

                annulus_outer_radius,

                usable_margin,

                settings,

            )

            for index, matched_source in enumerate(matched_sources, start=1)

        ]

        for future in as_completed(futures):

            measured_rows.append(future.result())

            completed_count += 1

            if progress_callback is not None and (completed_count == 1 or completed_count == total_count or completed_count % progress_step == 0):

                progress_callback(f"[H-R {completed_count}/{total_count}] Measuring Gaia-matched sources on the image.")



    measured_rows.sort(key=lambda item: item.index)

    return [item.row for item in measured_rows]





def _measure_single_hr_row(

    index: int,

    matched_source: _MatchedDetectedSource,

    planes: dict[str, np.ndarray],

    centroid_plane: np.ndarray,

    sigma_clip: SigmaClip,

    plane_background_levels: dict[str, float],

    global_background_std: float,

    saturation_threshold: float | None,

    aperture_radius: float,

    annulus_inner_radius: float,

    annulus_outer_radius: float,

    usable_margin: int,

    settings: AppSettings,

) -> _MeasuredHrRow:

    star = matched_source.star

    x, y, flags = _resolve_catalog_source_position(centroid_plane, matched_source.x, matched_source.y, aperture_radius)

    if matched_source.match_separation_arcsec > 2.0:

        flags.append(f"Catalog match offset {matched_source.match_separation_arcsec:.2f} arcsec.")

    peak_pixel_value, saturated_pixel_count, is_saturated = _measure_source_saturation(

        centroid_plane,

        x,

        y,

        aperture_radius,

        saturation_threshold,

    )

    row = HrMeasurementRow(

        source_id=star.source_id,

        source_name=star.name,

        catalog=star.catalog,

        ra_deg=star.ra_deg,

        dec_deg=star.dec_deg,

        gaia_g_mag=star.magnitude,

        gaia_bp_rp=_coerce_bp_rp(star.metadata),

        parallax_mas=_coerce_parallax_mas(star.metadata),

        parallax_error_mas=_coerce_parallax_error_mas(star.metadata),

        x=x,

        y=y,

        aperture_radius=aperture_radius,

        annulus_inner_radius=annulus_inner_radius,

        annulus_outer_radius=annulus_outer_radius,

        pm_ra_mas_per_year=_coerce_pm_ra_mas_per_year(star.metadata),

        pm_dec_mas_per_year=_coerce_pm_dec_mas_per_year(star.metadata),

        is_saturated=is_saturated,

        flags=list(flags),

    )

    if not _inside_image(x, y, centroid_plane.shape, usable_margin):

        row.flags.append(_usable_area_flag(settings.frame_edge_margin_percent))

        return _MeasuredHrRow(index=index, row=row)

    if _is_near_saturated(peak_pixel_value, saturation_threshold, is_saturated):

        row.flags.append(_near_saturation_flag(peak_pixel_value, saturation_threshold))

    if settings.saturation_filter_enabled and is_saturated:

        row.flags.append(_saturation_flag(peak_pixel_value, saturation_threshold, saturated_pixel_count))



    for label, plane in planes.items():

        measurement = _measure_plane(

            plane,

            x,

            y,

            aperture_radius,

            annulus_inner_radius,

            annulus_outer_radius,

            sigma_clip,

            plane_background_levels[label],

            global_background_std,

        )

        setattr(row, f"flux_{label}", measurement.flux)

        setattr(row, f"flux_error_{label}", measurement.flux_error)

        setattr(row, f"instrumental_mag_{label}", measurement.instrumental_magnitude)

        setattr(row, f"snr_{label}", measurement.snr)

        setattr(row, f"background_{label}", measurement.background_level)

        if measurement.flag is not None:

            row.flags.append(f"{label.title()}: {measurement.flag}")



    if row.instrumental_mag_blue is not None and row.instrumental_mag_red is not None:

        row.instrumental_blue_minus_red = row.instrumental_mag_blue - row.instrumental_mag_red

    row.plot_color_index = row.gaia_bp_rp if row.gaia_bp_rp is not None else row.instrumental_blue_minus_red

    row.display_color_hex = _display_color_hex(

        gaia_bp_rp=row.gaia_bp_rp,

        red_flux=row.flux_red,

        green_flux=row.flux_green,

        blue_flux=row.flux_blue,

    )

    return _MeasuredHrRow(index=index, row=row)





def _resolve_hr_parallel_worker_count(settings: AppSettings, total_count: int) -> int:

    configured_workers = resolve_shared_parallel_workers(settings)

    if configured_workers > 0:

        return min(configured_workers, total_count)

    cpu_count = os.cpu_count() or 1

    return max(1, min(total_count, 8, cpu_count))





def _apply_photometric_calibration(rows: list[HrMeasurementRow]) -> tuple[float | None, int]:

    zero_point_rows = [row for row in rows if _row_is_usable_for_zero_point(row)]

    for row in rows:

        row.gaia_absolute_magnitude = _absolute_magnitude_from_parallax(row.gaia_g_mag, row.parallax_mas)

    if not zero_point_rows:

        for row in rows:

            row.plot_color_index = row.gaia_bp_rp if row.gaia_bp_rp is not None else row.instrumental_blue_minus_red

        return None, 0



    zero_point_offsets = [

        offset

        for offset in (

            _finite_float(row.gaia_g_mag - row.instrumental_mag_luminance)

            for row in zero_point_rows

            if row.gaia_g_mag is not None and row.instrumental_mag_luminance is not None

        )

        if offset is not None

    ]

    if not zero_point_offsets:

        for row in rows:

            row.plot_color_index = row.gaia_bp_rp if row.gaia_bp_rp is not None else row.instrumental_blue_minus_red

        return None, 0



    zero_point_offset_mag = float(np.median(np.asarray(zero_point_offsets, dtype=float)))

    if not np.isfinite(zero_point_offset_mag):

        for row in rows:

            row.plot_color_index = row.gaia_bp_rp if row.gaia_bp_rp is not None else row.instrumental_blue_minus_red

        return None, 0



    applied_zero_point_rows = [

        row

        for row in zero_point_rows

        if row.gaia_g_mag is not None

        and row.instrumental_mag_luminance is not None

        and _finite_float(row.gaia_g_mag - row.instrumental_mag_luminance) is not None

    ]



    for row in rows:

        row.used_for_zero_point = row in applied_zero_point_rows

        row.plot_color_index = row.gaia_bp_rp if row.gaia_bp_rp is not None else row.instrumental_blue_minus_red

        if row.instrumental_mag_luminance is not None:

            calibrated_mag_luminance = _finite_float(row.instrumental_mag_luminance + zero_point_offset_mag)

            row.calibrated_mag_luminance = calibrated_mag_luminance

        row.absolute_magnitude_proxy = _absolute_magnitude_from_parallax(row.calibrated_mag_luminance, row.parallax_mas)

    return zero_point_offset_mag, len(applied_zero_point_rows)





def _row_is_usable_for_zero_point(row: HrMeasurementRow) -> bool:

    return bool(

        _finite_float(row.gaia_g_mag) is not None

        and _finite_float(row.instrumental_mag_luminance) is not None

        and not row.is_saturated

        and not any("Non-positive background-subtracted flux" in flag for flag in row.flags)

    )





def _absolute_magnitude_from_parallax(apparent_magnitude: float | None, parallax_mas: float | None) -> float | None:

    if apparent_magnitude is None or parallax_mas is None:

        return None

    if not np.isfinite(apparent_magnitude) or not np.isfinite(parallax_mas) or parallax_mas <= 0:

        return None

    return float(apparent_magnitude + (5.0 * math.log10(parallax_mas)) - 10.0)





def _display_color_hex(

    gaia_bp_rp: float | None,

    red_flux: float | None,

    green_flux: float | None,

    blue_flux: float | None,

) -> str | None:

    gaia_color = _stellar_color_hex_from_bp_rp(gaia_bp_rp)

    if gaia_color is not None:

        return gaia_color

    return _instrumental_display_color_hex(red_flux, green_flux, blue_flux)





def _stellar_color_hex_from_bp_rp(gaia_bp_rp: float | None) -> str | None:

    color_index = _finite_float(gaia_bp_rp)

    if color_index is None:

        return None



    anchors = [

        (-0.4, (56, 112, 255)),

        (0.0, (96, 154, 255)),

        (0.4, (176, 206, 255)),

        (0.8, (255, 245, 214)),

        (1.2, (255, 214, 142)),

        (1.6, (255, 168, 78)),

        (2.0, (255, 120, 28)),

        (2.5, (255, 76, 6)),

        (3.0, (240, 32, 0)),

    ]

    if color_index <= anchors[0][0]:

        return _rgb_hex(*_boost_rgb_saturation(anchors[0][1], factor=1.18))

    if color_index >= anchors[-1][0]:

        return _rgb_hex(*_boost_rgb_saturation(anchors[-1][1], factor=1.18))



    for (left_index, left_rgb), (right_index, right_rgb) in zip(anchors, anchors[1:], strict=False):

        if left_index <= color_index <= right_index:

            span = max(1e-9, right_index - left_index)

            ratio = (color_index - left_index) / span

            red = int(round(left_rgb[0] + ((right_rgb[0] - left_rgb[0]) * ratio)))

            green = int(round(left_rgb[1] + ((right_rgb[1] - left_rgb[1]) * ratio)))

            blue = int(round(left_rgb[2] + ((right_rgb[2] - left_rgb[2]) * ratio)))

            return _rgb_hex(*_boost_rgb_saturation((red, green, blue), factor=1.18))

    return None





def _instrumental_display_color_hex(red_flux: float | None, green_flux: float | None, blue_flux: float | None) -> str | None:

    values = [red_flux, green_flux, blue_flux]

    if any(value is None or value <= 0 or not np.isfinite(value) for value in values):

        return None

    red = float(red_flux)

    green = float(green_flux)

    blue = float(blue_flux)

    max_flux = max(red, green, blue)

    if max_flux <= 0:

        return None

    normalized = np.asarray([red / max_flux, green / max_flux, blue / max_flux], dtype=float)

    neutral = float(np.mean(normalized))

    blended = (normalized * 0.78) + (neutral * 0.22)

    channels = [int(round(max(0.0, min(1.0, value)) * 255.0)) for value in blended]

    return _rgb_hex(*_boost_rgb_saturation(tuple(channels), factor=1.12))





def _boost_rgb_saturation(rgb: tuple[int, int, int], factor: float) -> tuple[int, int, int]:

    red, green, blue = [float(channel) for channel in rgb]

    midpoint = (red + green + blue) / 3.0

    boosted = [midpoint + ((channel - midpoint) * factor) for channel in (red, green, blue)]

    return tuple(max(0, min(255, int(round(channel)))) for channel in boosted)





def _rgb_hex(red: int, green: int, blue: int) -> str:

    return f"#{max(0, min(255, red)):02x}{max(0, min(255, green)):02x}{max(0, min(255, blue)):02x}"





def _finite_float(value: object) -> float | None:

    if value is None:

        return None

    try:

        numeric_value = float(value)

    except (TypeError, ValueError):

        return None

    return numeric_value if np.isfinite(numeric_value) else None