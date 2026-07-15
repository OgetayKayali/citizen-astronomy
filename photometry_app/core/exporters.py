from __future__ import annotations


from collections.abc import Callable



import csv

from io import BytesIO

from dataclasses import asdict, dataclass, replace

from functools import lru_cache

from importlib.metadata import PackageNotFoundError, version as package_version

import json

import math

from statistics import median

from pathlib import Path

from datetime import timedelta



import astropy.units as u

from astropy.coordinates import AltAz, EarthLocation, SkyCoord

from astropy.time import Time

from astropy.utils import iers

from matplotlib.figure import Figure

import numpy as np

from PySide6.QtGui import QImage



from photometry_app.core.animation_export import export_qimages_to_gif

from photometry_app.core.image_io import read_header

from photometry_app.core.models import LightCurveSeries, PhotometryMeasurement, ProcessingReport, ScienceObservation

from photometry_app.core.plotting import (
    AnnotatedImageDisplay,
    LightCurvePlotPayload,
    build_annotated_image_display,
    build_light_curve_plot_payload,
    plot_annotated_image,
    plot_light_curve,
    plot_light_curve_payload,
)

_LIGHT_CURVE_GIF_FRAME_DURATION_MS = 40
_LIGHT_CURVE_GIF_MINIMUM_DURATION_SECONDS = 5.0
_LIGHT_CURVE_GIF_INITIAL_HOLD_FRAMES = 10
_LIGHT_CURVE_GIF_FINAL_HOLD_FRAMES = 15
_LIGHT_CURVE_GIF_FIT_STAGE_TARGET_FRAMES = 30
_LIGHT_CURVE_GIF_GRID_ALPHA = 0.5


class AnimatedLightCurveExportCanceled(RuntimeError):
    pass





_AAVSO_EXTENDED_COLUMNS = [

    "NAME",

    "DATE",

    "MAG",

    "MERR",

    "FILT",

    "TRANS",

    "MTYPE",

    "CNAME",

    "CMAG",

    "KNAME",

    "KMAG",

    "AMASS",

    "GROUP",

    "CHART",

    "NOTES",

]



_AAVSO_FILTER_ALIASES = {

    "u": "U",

    "johnsonu": "U",

    "johnson_u": "U",

    "b": "B",

    "johnsonb": "B",

    "johnson_b": "B",

    "v": "V",

    "johnsonv": "V",

    "johnson_v": "V",

    "r": "R",

    "rc": "R",

    "r_c": "R",

    "cousinsr": "R",

    "cousins_r": "R",

    "i": "I",

    "ic": "I",

    "i_c": "I",

    "cousinsi": "I",

    "cousins_i": "I",

    "j": "J",

    "h": "H",

    "k": "K",

    "su": "SU",

    "sloanu": "SU",

    "sloan_u": "SU",

    "sg": "SG",

    "sloang": "SG",

    "sloan_g": "SG",

    "sr": "SR",

    "sloanr": "SR",

    "sloan_r": "SR",

    "si": "SI",

    "sloani": "SI",

    "sloan_i": "SI",

    "sz": "SZ",

    "sloanz": "SZ",

    "sloan_z": "SZ",

    "tb": "TB",

    "tg": "TG",

    "tr": "TR",

    "cv": "CV",

    "cr": "CR",

    "clear": "CV",

    "unfiltered": "CV",

    "ha": "HA",

    "h_alpha": "HA",

    "hac": "HAC",

    "h_alpha_continuum": "HAC",

    "stu": "STU",

    "stv": "STV",

    "stb": "STB",

    "sty": "STY",

    "sthbw": "STHBW",

    "sthbn": "STHBN",

    "ma": "MA",

    "mb": "MB",

    "mi": "MI",

    "y": "Y",

}



iers.conf.auto_download = False





def _aavso_software_header() -> str:

    try:

        resolved_version = package_version("citizen-photometry")

    except PackageNotFoundError:

        resolved_version = "dev"

    return f"Citizen Photometry {resolved_version}"





@dataclass(slots=True)

class ScienceExportMetadata:

    observer_code: str = ""

    observer_name: str = ""

    organization: str = ""

    site_name: str = ""

    observing_site_latitude_deg: float | None = None

    observing_site_longitude_deg: float | None = None

    observing_site_elevation_m: float | None = None

    telescope: str = ""

    camera: str = ""

    filter_system: str = ""

    aavso_chart_id: str = ""

    time_standard: str = "UTC"

    transformed: bool = False

    reduction_notes: str = ""





@dataclass(slots=True)

class AavsoExtendedObservation:

    source_id: str

    source_name: str

    filter_code: str

    mtype: str

    magnitude: float

    export_line: str

    warnings: list[str]

    zero_point_mag: float | None = None





@dataclass(slots=True)

class _ExportMeasurementIndex:

    reference_by_frame_filter_source: dict[tuple[str, str, str], PhotometryMeasurement]

    check_by_frame_filter: dict[tuple[str, str], list[PhotometryMeasurement]]





def export_processing_report(

    report: ProcessingReport,

    output_dir: Path,

    measurements: list[PhotometryMeasurement] | None = None,

    light_curves: list[LightCurveSeries] | None = None,

    export_suffix: str | None = None,

    export_name_stem: str | None = None,

    plot_y_axis_mode: str = "differential_magnitude",

    plot_theme: str = "normal",

    custom_theme_colors: dict[str, str] | None = None,

    science_metadata: ScienceExportMetadata | None = None,

) -> dict[str, Path | int]:

    output_dir.mkdir(parents=True, exist_ok=True)

    name_suffix = f"_{_safe_suffix(export_suffix)}" if export_suffix else ""

    name_stem = _safe_suffix(export_name_stem) if export_name_stem else report.object_name

    selected_measurements = measurements if measurements is not None else report.measurements

    selected_light_curves = light_curves if light_curves is not None else report.light_curves



    measurements_path = output_dir / f"{name_stem}{name_suffix}_measurements.csv"

    light_curves_path = output_dir / f"{name_stem}{name_suffix}_light_curves.csv"

    summary_path = output_dir / f"{name_stem}{name_suffix}_summary.json"

    accepted_observations_path = output_dir / f"{name_stem}{name_suffix}_accepted_observations.csv"

    accepted_observations_json_path = output_dir / f"{name_stem}{name_suffix}_accepted_observations.json"

    rejected_observations_path = output_dir / f"{name_stem}{name_suffix}_rejected_observations.csv"

    rejected_observations_json_path = output_dir / f"{name_stem}{name_suffix}_rejected_observations.json"

    reference_manifest_path = output_dir / f"{name_stem}{name_suffix}_reference_manifest.csv"

    reduction_manifest_path = output_dir / f"{name_stem}{name_suffix}_reduction_manifest.json"

    provenance_manifest_path = output_dir / f"{name_stem}{name_suffix}_provenance_manifest.json"

    aavso_extended_path = output_dir / f"{name_stem}{name_suffix}_aavso_extended.txt"

    aavso_preflight_path = output_dir / f"{name_stem}{name_suffix}_aavso_preflight.json"

    readme_path = output_dir / f"{name_stem}{name_suffix}_README.txt"

    plots_dir = output_dir / f"{name_stem}{name_suffix}_plots"

    annotated_images_dir = output_dir / f"{name_stem}{name_suffix}_annotated_images"

    plots_dir.mkdir(parents=True, exist_ok=True)



    measurement_index = _build_export_measurement_index(report.measurements)



    _write_measurements_csv(selected_measurements, measurements_path)

    _write_light_curves_csv(selected_light_curves, light_curves_path)

    science_rows = _science_observation_rows(

        report,

        selected_measurements,

        science_metadata=science_metadata,

        measurement_index=measurement_index,

    )

    accepted_rows, rejected_rows = _split_science_observation_rows(science_rows)

    _write_summary_json(

        report,

        selected_measurements,

        selected_light_curves,

        summary_path,

        accepted_observation_count=len(accepted_rows),

        rejected_observation_count=len(rejected_rows),

    )

    _write_science_observations_csv(accepted_rows, accepted_observations_path)

    _write_science_observations_json(accepted_rows, accepted_observations_json_path)

    _write_science_observations_csv(rejected_rows, rejected_observations_path)

    _write_science_observations_json(rejected_rows, rejected_observations_json_path)

    _write_reference_manifest_csv(report, selected_measurements, accepted_rows, reference_manifest_path)

    aavso_observations, aavso_preflight = _build_aavso_extended_observations(

        report,

        selected_measurements,

        science_metadata=science_metadata,

        measurement_index=measurement_index,

    )

    aavso_observation_count = _write_aavso_extended_file(aavso_observations, aavso_extended_path, science_metadata=science_metadata)

    _write_aavso_preflight_json(aavso_preflight, aavso_preflight_path)

    _write_science_export_readme(

        readme_path,

        aavso_upload_file=aavso_extended_path.name,

        preflight_file=aavso_preflight_path.name,

        accepted_csv_file=accepted_observations_path.name,

        accepted_json_file=accepted_observations_json_path.name,

        summary_file=summary_path.name,

        reduction_manifest_file=reduction_manifest_path.name,

        provenance_manifest_file=provenance_manifest_path.name,

    )

    _write_reduction_manifest_json(

        report,

        selected_measurements,

        accepted_rows,

        rejected_rows,

        reduction_manifest_path,

        science_metadata=science_metadata,

    )

    _write_provenance_manifest_json(

        report,

        selected_measurements,

        accepted_rows,

        rejected_rows,

        provenance_manifest_path,

        science_metadata=science_metadata,

    )



    exported_plots: list[str] = []

    for series in selected_light_curves:

        plot_path = plots_dir / _plot_file_name(series)

        export_light_curve_plot(

            series,

            plot_path,

            y_axis_mode=plot_y_axis_mode,

            theme=plot_theme,

            custom_theme_colors=custom_theme_colors,

        )

        exported_plots.append(str(plot_path))



    return {

        "measurements_csv": measurements_path,

        "light_curves_csv": light_curves_path,

        "summary_json": summary_path,

        "accepted_observations_csv": accepted_observations_path,

        "accepted_observations_json": accepted_observations_json_path,

        "accepted_observation_count": len(accepted_rows),

        "rejected_observations_csv": rejected_observations_path,

        "rejected_observations_json": rejected_observations_json_path,

        "rejected_observation_count": len(rejected_rows),

        "reference_manifest_csv": reference_manifest_path,

        "reduction_manifest_json": reduction_manifest_path,

        "provenance_manifest_json": provenance_manifest_path,

        "aavso_extended_txt": aavso_extended_path,

        "aavso_preflight_json": aavso_preflight_path,

        "science_export_readme_txt": readme_path,

        "aavso_observation_count": aavso_observation_count,

        "aavso_preflight_warning_count": int(aavso_preflight.get("warning_count", 0)),

        "aavso_standard_observation_count": int(aavso_preflight.get("standard_observation_count", 0)),

        "aavso_differential_observation_count": int(aavso_preflight.get("differential_observation_count", 0)),

        "plots_dir": plots_dir,

        "plot_count": len(exported_plots),

        "annotated_images_dir": annotated_images_dir,

        "annotated_image_count": 0,

    }





def preview_aavso_extended_export(

    report: ProcessingReport,

    measurements: list[PhotometryMeasurement] | None = None,

    *,

    science_metadata: ScienceExportMetadata | None = None,

) -> dict[str, object]:

    selected_measurements = measurements if measurements is not None else report.measurements

    measurement_index = _build_export_measurement_index(report.measurements)

    _observations, payload = _build_aavso_extended_observations(

        report,

        selected_measurements,

        science_metadata=science_metadata,

        measurement_index=measurement_index,

    )

    return payload





def measurement_airmass(measurement: PhotometryMeasurement, science_metadata: ScienceExportMetadata | None = None) -> float | None:

    metadata = science_metadata or ScienceExportMetadata()

    header_airmass = _header_airmass(measurement.file_path)

    if header_airmass is not None:

        return header_airmass

    return _computed_airmass(measurement, metadata)





def export_light_curve_plots(

    report: ProcessingReport,

    output_dir: Path,

    light_curves: list[LightCurveSeries] | None = None,

    export_suffix: str | None = None,

    y_axis_mode: str = "differential_magnitude",

    x_axis_mode: str = "datetime",

    theme: str = "normal",

    custom_theme_colors: dict[str, str] | None = None,

) -> dict[str, Path | int]:

    output_dir.mkdir(parents=True, exist_ok=True)

    name_suffix = f"_{_safe_suffix(export_suffix)}" if export_suffix else ""

    selected_light_curves = light_curves if light_curves is not None else report.light_curves

    plots_dir = output_dir / f"{report.object_name}{name_suffix}_plots"

    plots_dir.mkdir(parents=True, exist_ok=True)



    exported_plots: list[str] = []

    for series in selected_light_curves:

        plot_path = plots_dir / _plot_file_name(series)

        export_light_curve_plot(

            series,

            plot_path,

            y_axis_mode=y_axis_mode,

            x_axis_mode=x_axis_mode,

            theme=theme,

            custom_theme_colors=custom_theme_colors,

        )

        exported_plots.append(str(plot_path))



    return {"plots_dir": plots_dir, "plot_count": len(exported_plots)}





def export_annotated_images(

    report: ProcessingReport,

    output_dir: Path,

    measurements: list[PhotometryMeasurement] | None = None,

    export_suffix: str | None = None,

) -> dict[str, Path | int]:

    output_dir.mkdir(parents=True, exist_ok=True)

    name_suffix = f"_{_safe_suffix(export_suffix)}" if export_suffix else ""

    selected_measurements = measurements if measurements is not None else report.measurements

    annotated_images_dir = output_dir / f"{report.object_name}{name_suffix}_annotated_images"

    annotated_images_dir.mkdir(parents=True, exist_ok=True)

    measurement_index = _build_export_measurement_index(report.measurements)

    annotated_display_cache: dict[Path, AnnotatedImageDisplay] = {}



    exported_annotated_images: list[str] = []

    for measurement in [item for item in selected_measurements if item.is_variable]:

        annotated_path = annotated_images_dir / _annotated_image_file_name(measurement)

        export_annotated_image_plot(

            measurement,

            _comparison_measurements_for_export(measurement, measurement_index),

            annotated_path,

            display_cache=annotated_display_cache,

        )

        exported_annotated_images.append(str(annotated_path))



    return {"annotated_images_dir": annotated_images_dir, "annotated_image_count": len(exported_annotated_images)}





def export_light_curve_plot(

    series: LightCurveSeries,

    output_path: Path,

    y_axis_mode: str = "differential_magnitude",

    x_axis_mode: str = "datetime",

    fit_config: object | None = None,

    phase_period_hours: float | None = None,

    phase_anchor_mode: str = "first_observation",

    theme: str = "normal",

    custom_theme_colors: dict[str, str] | None = None,

    export_style: str = "themed",

    x_limits: tuple[float, float] | None = None,

    y_limits: tuple[float, float] | None = None,

    figure_size_inches: tuple[float, float] | None = None,

    dpi: int | None = None,

) -> None:

    figure = Figure(figsize=(figure_size_inches or (8, 4.5)))

    try:

        axis = figure.add_subplot(111)

        plot_light_curve(

            axis,

            series,

            "No valid values for the selected light-curve axis.",

            fit_config=fit_config,

            y_axis_mode=y_axis_mode,

            x_axis_mode=x_axis_mode,

            phase_period_hours=phase_period_hours,

            phase_anchor_mode=phase_anchor_mode,

            theme=theme,

            custom_theme_colors=custom_theme_colors,

            export_style=export_style,

            x_limits=x_limits,

            y_limits=y_limits,

        )

        figure.tight_layout()

        resolved_dpi = dpi if dpi is not None else (200 if export_style == "scientific" else 150)

        figure.savefig(output_path, dpi=resolved_dpi, facecolor=figure.get_facecolor())

    finally:

        figure.clear()





def export_annotated_image_plot(

    measurement: PhotometryMeasurement,

    comparison_measurements: list[PhotometryMeasurement],

    output_path: Path,

    display_cache: dict[Path, AnnotatedImageDisplay] | None = None,

) -> None:

    figure = Figure(figsize=(8, 8))

    try:

        cached_display = None if display_cache is None else display_cache.get(measurement.file_path)

        if cached_display is None and display_cache is not None:

            try:

                cached_display = build_annotated_image_display(measurement.file_path)

            except Exception:

                cached_display = None

            else:

                display_cache[measurement.file_path] = cached_display

        axis = figure.add_subplot(111)

        plot_annotated_image(

            axis,

            measurement.file_path,

            measurement,

            comparison_measurements,

            "Unable to render the selected image.",

            display=cached_display,

        )

        figure.tight_layout()

        figure.savefig(output_path, dpi=150)

    finally:

        figure.clear()





def _write_measurements_csv(measurements: list[PhotometryMeasurement], output_path: Path) -> None:

    with output_path.open("w", encoding="utf-8", newline="") as handle:

        writer = csv.writer(handle)

        writer.writerow(

            [

                "source_id",

                "source_name",

                "catalog",

                "object_name",

                "file_path",

                "observation_time",

                "filter_name",

                "ra_deg",

                "dec_deg",

                "x",

                "y",

                "flux",

                "flux_error",

                "instrumental_magnitude",

                "differential_magnitude",

                "differential_magnitude_error",

                "snr",

                "peak_pixel_value",

                "saturation_threshold",

                "saturated_pixel_count",

                "is_saturated",

                "quality_score",

                "quality_weight",

                "excluded_from_analysis",

                "exclusion_reasons",

                "is_variable",

                "is_reference",

                "flags",

            ]

        )

        for measurement in measurements:

            writer.writerow(

                [

                    measurement.source_id,

                    measurement.source_name,

                    measurement.catalog,

                    measurement.object_name,

                    str(measurement.file_path),

                    measurement.observation_time.isoformat() if measurement.observation_time else "",

                    measurement.filter_name or "",

                    measurement.ra_deg,

                    measurement.dec_deg,

                    measurement.x,

                    measurement.y,

                    measurement.flux,

                    measurement.flux_error,

                    measurement.instrumental_magnitude,

                    measurement.differential_magnitude,

                    measurement.differential_magnitude_error,

                    measurement.snr,

                    measurement.peak_pixel_value,

                    measurement.saturation_threshold,

                    measurement.saturated_pixel_count,

                    measurement.is_saturated,

                    measurement.quality_score,

                    measurement.quality_weight,

                    measurement.excluded_from_analysis,

                    "; ".join(measurement.exclusion_reasons),

                    measurement.is_variable,

                    measurement.is_reference,

                    "; ".join(measurement.flags),

                ]

            )





def _write_light_curves_csv(light_curves: list[LightCurveSeries], output_path: Path) -> None:

    with output_path.open("w", encoding="utf-8", newline="") as handle:

        writer = csv.writer(handle)

        writer.writerow(

            [

                "object_name",

                "source_id",

                "source_name",

                "filter_name",

                "observation_time",

                "file_path",

                "differential_magnitude",

                "differential_magnitude_error",

                "instrumental_magnitude",

                "flux",

                "flux_error",

                "quality_score",

                "quality_weight",

                "excluded_from_analysis",

                "exclusion_reasons",

            ]

        )

        for series in light_curves:

            for point in series.points:

                writer.writerow(

                    [

                        series.object_name,

                        series.source_id,

                        series.source_name,

                        series.filter_name,

                        point.observation_time.isoformat() if point.observation_time else "",

                        str(point.file_path),

                        point.differential_magnitude,

                        point.differential_magnitude_error,

                        point.instrumental_magnitude,

                        point.flux,

                        point.flux_error,

                        point.quality_score,

                        point.quality_weight,

                        point.excluded_from_analysis,

                        "; ".join(point.exclusion_reasons),

                    ]

                )





def _write_summary_json(

    report: ProcessingReport,

    measurements: list[PhotometryMeasurement],

    light_curves: list[LightCurveSeries],

    output_path: Path,

    *,

    accepted_observation_count: int,

    rejected_observation_count: int,

) -> None:

    payload = {

        "object_name": report.object_name,

        "files_processed": report.files_processed,

        "solved_files": report.solved_files,

        "reference_stars": len(report.reference_stars),

        "variable_stars": report.total_variable_stars_found,

        "variable_stars_found": report.total_variable_stars_found,

        "variable_stars_analyzed": len(report.field_catalog.variable_stars) if report.field_catalog else 0,

        "variable_star_limit_mode": report.variable_star_limit_mode.value,

        "variable_star_limit_value": report.variable_star_limit_value,

        "measurement_count": len(measurements),

        "light_curve_count": len(light_curves),

        "accepted_observation_count": accepted_observation_count,

        "rejected_observation_count": rejected_observation_count,

        "notes": report.notes,

    }

    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")





def _science_observation_rows(

    report: ProcessingReport,

    measurements: list[PhotometryMeasurement],

    *,

    science_metadata: ScienceExportMetadata | None,

    measurement_index: _ExportMeasurementIndex | None = None,

) -> list[ScienceObservation]:

    rows: list[ScienceObservation] = []

    reference_lookup = {star.source_id: star for star in report.reference_stars}

    metadata = science_metadata or ScienceExportMetadata()

    resolved_measurement_index = measurement_index or _build_export_measurement_index(measurements)

    check_rms_lookup = _check_residual_rms_by_series(measurements, resolved_measurement_index, reference_lookup)

    for measurement in measurements:

        if measurement.is_reference:

            continue

        calibration_context = _science_calibration_context(

            measurement,

            resolved_measurement_index,

            reference_lookup,

            metadata,

        )

        measurement_kind, reported_value, reported_uncertainty = _science_measurement_value(measurement, calibration_context)

        check_diagnostics = _check_diagnostics_for_measurement(

            measurement,

            resolved_measurement_index,

            reference_lookup,

            check_rms_lookup,

        )

        rows.append(

            ScienceObservation(

                object_name=measurement.object_name,

                source_id=measurement.source_id,

                source_name=measurement.source_name,

                role=_science_measurement_role(measurement),

                catalog=measurement.catalog,

                filter_name=measurement.filter_name or "",

                observation_time_utc=measurement.observation_time.isoformat() if measurement.observation_time else "",

                julian_date_utc=_julian_date_utc(measurement.observation_time),

                file_path=str(measurement.file_path),

                measurement_kind=measurement_kind,

                reported_value=reported_value,

                reported_uncertainty=reported_uncertainty,

                differential_magnitude=measurement.differential_magnitude,

                differential_magnitude_error=measurement.differential_magnitude_error,

                instrumental_magnitude=measurement.instrumental_magnitude,

                flux=measurement.flux,

                flux_error=measurement.flux_error,

                snr=measurement.snr,

                quality_score=measurement.quality_score,

                quality_weight=measurement.quality_weight,

                excluded_from_analysis=measurement.excluded_from_analysis,

                exclusion_reasons="; ".join(measurement.exclusion_reasons),

                flags="; ".join(measurement.flags),

                is_saturated=measurement.is_saturated,

                saturation_status=measurement.saturation_status,

                peak_pixel_value=measurement.peak_pixel_value,

                saturation_threshold=measurement.saturation_threshold,

                saturated_pixel_count=measurement.saturated_pixel_count,

                background_level=measurement.background_level,

                background_scatter=measurement.background_scatter,

                centroid_shift_pixels=measurement.centroid_shift_pixels,

                aperture_radius=measurement.aperture_radius,

                annulus_inner_radius=measurement.annulus_inner_radius,

                annulus_outer_radius=measurement.annulus_outer_radius,

                calibration_state=calibration_context["calibration_state"],

                calibrated_magnitude=calibration_context["calibrated_magnitude"],

                zero_point_magnitude=calibration_context["zero_point_magnitude"],

                zero_point_source_count=calibration_context["zero_point_source_count"],

                check_source_id=check_diagnostics["check_source_id"],

                check_source_name=check_diagnostics["check_source_name"],

                check_reference_magnitude=check_diagnostics["check_reference_magnitude"],

                check_calibrated_magnitude=check_diagnostics["check_calibrated_magnitude"],

                check_residual_magnitude=check_diagnostics["check_residual_magnitude"],

                check_residual_rms=check_diagnostics["check_residual_rms"],

                transformed_requested=calibration_context["transformed_requested"],

                aavso_mtype_candidate=calibration_context["aavso_mtype_candidate"],

                comparison_source_ids="; ".join(measurement.comparison_source_ids),

                comparison_source_names="; ".join(measurement.comparison_source_names),

                comparison_source_count=len(measurement.comparison_source_ids),

                comparison_method=_science_comparison_method(measurement),

                comparison_reference_flux=measurement.comparison_reference_flux,

                comparison_scatter=measurement.comparison_scatter,

                ra_deg=measurement.ra_deg,

                dec_deg=measurement.dec_deg,

                x=measurement.x,

                y=measurement.y,

            )

        )

    return rows





def _split_science_observation_rows(

    rows: list[ScienceObservation],

) -> tuple[list[ScienceObservation], list[ScienceObservation]]:

    accepted = [row for row in rows if row.accepted_for_science_export]

    rejected = [row for row in rows if not row.accepted_for_science_export]

    return accepted, rejected





def _science_measurement_role(measurement: PhotometryMeasurement) -> str:

    if measurement.is_check:

        return "check"

    if measurement.is_variable:

        return "variable"

    return "target"





def _science_measurement_value(

    measurement: PhotometryMeasurement,

    calibration_context: dict[str, object],

) -> tuple[str, float | None, float | None]:

    calibrated_magnitude = calibration_context.get("calibrated_magnitude")

    calibrated_magnitude_error = calibration_context.get("calibrated_magnitude_error")

    if calibrated_magnitude is not None:

        return "calibrated_magnitude", calibrated_magnitude, calibrated_magnitude_error

    if measurement.differential_magnitude is not None:

        return "differential_magnitude", measurement.differential_magnitude, measurement.differential_magnitude_error

    if measurement.instrumental_magnitude is not None:

        return "instrumental_magnitude", measurement.instrumental_magnitude, measurement.flux_error

    if measurement.flux is not None:

        return "flux", measurement.flux, measurement.flux_error

    return "none", None, None





def _science_comparison_method(measurement: PhotometryMeasurement) -> str:

    comparison_count = len(measurement.comparison_source_ids)

    if comparison_count == 0:

        return "none"

    if comparison_count == 1:

        return "single"

    return "ensemble"





def _science_calibration_context(

    measurement: PhotometryMeasurement,

    measurement_index: _ExportMeasurementIndex,

    reference_lookup: dict[str, object],

    metadata: ScienceExportMetadata,

) -> dict[str, object]:

    comparison_measurements = _comparison_measurements_for_export(measurement, measurement_index)

    calibrated_magnitude, calibrated_magnitude_error, zero_point_magnitude, zero_point_source_count = _resolved_export_calibration(

        measurement,

        comparison_measurements,

        reference_lookup,

    )



    if metadata.transformed and calibrated_magnitude is not None:

        calibration_state = "transformed_candidate_std"

        aavso_mtype_candidate = "STD"

    elif calibrated_magnitude is not None:

        calibration_state = "zero_point_calibrated_untransformed"

        aavso_mtype_candidate = "STD"

    elif measurement.differential_magnitude is not None:

        calibration_state = "differential_only"

        aavso_mtype_candidate = "DIF"

    elif measurement.instrumental_magnitude is not None:

        calibration_state = "instrumental_only"

        aavso_mtype_candidate = "na"

    elif measurement.flux is not None:

        calibration_state = "flux_only"

        aavso_mtype_candidate = "na"

    else:

        calibration_state = "unreportable"

        aavso_mtype_candidate = "na"



    return {

        "calibration_state": calibration_state,

        "calibrated_magnitude": calibrated_magnitude,

        "calibrated_magnitude_error": calibrated_magnitude_error,

        "zero_point_magnitude": zero_point_magnitude,

        "zero_point_source_count": zero_point_source_count,

        "transformed_requested": bool(metadata.transformed),

        "aavso_mtype_candidate": aavso_mtype_candidate,

    }





def measurement_check_diagnostics(

    measurement: PhotometryMeasurement,

    measurements: list[PhotometryMeasurement],

    reference_stars: list[object],

) -> dict[str, object]:

    reference_lookup = {getattr(star, "source_id"): star for star in reference_stars}

    measurement_index = _build_export_measurement_index(measurements)

    check_rms_lookup = _check_residual_rms_by_series(measurements, measurement_index, reference_lookup)

    return _check_diagnostics_for_measurement(measurement, measurement_index, reference_lookup, check_rms_lookup)





def _julian_date_utc(observation_time: object) -> float | None:

    if observation_time is None:

        return None

    try:

        return float(Time(observation_time, scale="utc").jd)

    except Exception:

        return None





def _write_science_observations_csv(rows: list[ScienceObservation], output_path: Path) -> None:

    with output_path.open("w", encoding="utf-8", newline="") as handle:

        writer = csv.writer(handle)

        writer.writerow(ScienceObservation.field_names())

        for row in rows:

            payload = row.to_export_dict()

            writer.writerow([payload[field_name] for field_name in ScienceObservation.field_names()])





def _write_science_observations_json(rows: list[ScienceObservation], output_path: Path) -> None:

    payload = {

        "schema": "science-observation-v3",

        "field_names": ScienceObservation.field_names(),

        "observation_count": len(rows),

        "rows": [row.to_export_dict() for row in rows],

    }

    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")





def _write_reference_manifest_csv(

    report: ProcessingReport,

    measurements: list[PhotometryMeasurement],

    accepted_rows: list[ScienceObservation],

    output_path: Path,

) -> None:

    usage_counts: dict[str, int] = {}

    accepted_usage_counts: dict[str, int] = {}

    for measurement in measurements:

        if measurement.is_reference:

            continue

        for source_id in measurement.comparison_source_ids:

            usage_counts[source_id] = usage_counts.get(source_id, 0) + 1

            if not measurement.excluded_from_analysis and not measurement.is_saturated:

                accepted_usage_counts[source_id] = accepted_usage_counts.get(source_id, 0) + 1



    reference_measurements_by_id: dict[str, list[PhotometryMeasurement]] = {}

    for measurement in measurements:

        if not measurement.is_reference:

            continue

        reference_measurements_by_id.setdefault(measurement.source_id, []).append(measurement)



    with output_path.open("w", encoding="utf-8", newline="") as handle:

        writer = csv.writer(handle)

        writer.writerow(

            [

                "source_id",

                "source_name",

                "catalog",

                "ra_deg",

                "dec_deg",

                "catalog_magnitude",

                "usage_count",

                "accepted_usage_count",

                "filter_names",

                "file_count",

                "ever_saturated",

                "ever_near_saturated",

                "max_saturation_fraction",

                "median_flux",

                "median_snr",

            ]

        )

        for star in report.reference_stars:

            star_measurements = reference_measurements_by_id.get(star.source_id, [])

            saturation_fractions = [item.saturation_fraction for item in star_measurements if item.saturation_fraction is not None]

            fluxes = [item.flux for item in star_measurements if item.flux is not None]

            snr_values = [item.snr for item in star_measurements if item.snr is not None]

            filter_names = sorted({item.filter_name or "" for item in star_measurements if item.filter_name})

            file_count = len({str(item.file_path) for item in star_measurements})

            writer.writerow(

                [

                    star.source_id,

                    star.name,

                    star.catalog,

                    star.ra_deg,

                    star.dec_deg,

                    star.magnitude,

                    usage_counts.get(star.source_id, 0),

                    accepted_usage_counts.get(star.source_id, 0),

                    "; ".join(filter_names),

                    file_count,

                    any(item.is_saturated for item in star_measurements),

                    any(item.is_near_saturated for item in star_measurements),

                    max(saturation_fractions) if saturation_fractions else None,

                    median(fluxes) if fluxes else None,

                    median(snr_values) if snr_values else None,

                ]

            )





def _write_reduction_manifest_json(

    report: ProcessingReport,

    measurements: list[PhotometryMeasurement],

    accepted_rows: list[ScienceObservation],

    rejected_rows: list[ScienceObservation],

    output_path: Path,

    *,

    science_metadata: ScienceExportMetadata | None,

) -> None:

    observation_times = sorted(

        [measurement.observation_time.isoformat() for measurement in measurements if measurement.observation_time is not None]

    )

    filter_names = sorted({measurement.filter_name for measurement in measurements if measurement.filter_name})

    aperture_radii = [measurement.aperture_radius for measurement in measurements if measurement.aperture_radius is not None]

    annulus_inner_radii = [measurement.annulus_inner_radius for measurement in measurements if measurement.annulus_inner_radius is not None]

    annulus_outer_radii = [measurement.annulus_outer_radius for measurement in measurements if measurement.annulus_outer_radius is not None]

    payload = {

        "export_profile": "science-ready-v1",

        "observation_schema": "science-observation-v3",

        "submission_metadata": asdict(science_metadata or ScienceExportMetadata()),

        "object_name": report.object_name,

        "files_processed": report.files_processed,

        "solved_files": report.solved_files,

        "filters": filter_names,

        "observation_start_utc": observation_times[0] if observation_times else None,

        "observation_end_utc": observation_times[-1] if observation_times else None,

        "accepted_observation_count": len(accepted_rows),

        "rejected_observation_count": len(rejected_rows),

        "reference_star_count": len(report.reference_stars),

        "variable_star_count": report.total_variable_stars_found,

        "variable_star_limit_mode": report.variable_star_limit_mode.value,

        "variable_star_limit_value": report.variable_star_limit_value,

        "photometry_mode": report.photometry_mode.value,

        "manual_mode": report.manual_config is not None,

        "median_aperture_radius": median(aperture_radii) if aperture_radii else None,

        "median_annulus_inner_radius": median(annulus_inner_radii) if annulus_inner_radii else None,

        "median_annulus_outer_radius": median(annulus_outer_radii) if annulus_outer_radii else None,

        "notes": list(report.notes),

    }

    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")





def _write_provenance_manifest_json(

    report: ProcessingReport,

    measurements: list[PhotometryMeasurement],

    accepted_rows: list[ScienceObservation],

    rejected_rows: list[ScienceObservation],

    output_path: Path,

    *,

    science_metadata: ScienceExportMetadata | None,

) -> None:

    calibration_state_counts: dict[str, int] = {}

    comparison_method_counts: dict[str, int] = {}

    filter_counts: dict[str, int] = {}

    for row in accepted_rows + rejected_rows:

        calibration_state_counts[row.calibration_state] = calibration_state_counts.get(row.calibration_state, 0) + 1

        comparison_method_counts[row.comparison_method] = comparison_method_counts.get(row.comparison_method, 0) + 1

        filter_key = row.filter_name or ""

        filter_counts[filter_key] = filter_counts.get(filter_key, 0) + 1



    file_paths = sorted({str(measurement.file_path) for measurement in measurements})

    reference_summary = [

        {

            "source_id": star.source_id,

            "source_name": star.name,

            "catalog": star.catalog,

            "magnitude": star.magnitude,

        }

        for star in report.reference_stars

    ]

    payload = {

        "schema": "science-provenance-v1",

        "observation_schema": "science-observation-v3",

        "submission_metadata": asdict(science_metadata or ScienceExportMetadata()),

        "object_name": report.object_name,

        "photometry_mode": report.photometry_mode.value,

        "manual_mode": report.manual_config is not None,

        "manual_reference_frame": report.manual_config.reference_frame_name if report.manual_config is not None else None,

        "files_processed": report.files_processed,

        "solved_files": report.solved_files,

        "file_paths": file_paths,

        "file_notes": dict(report.file_notes),

        "report_notes": list(report.notes),

        "accepted_observation_count": len(accepted_rows),

        "rejected_observation_count": len(rejected_rows),

        "calibration_state_counts": calibration_state_counts,

        "comparison_method_counts": comparison_method_counts,

        "filter_counts": filter_counts,

        "reference_stars": reference_summary,

        "variable_star_limit_mode": report.variable_star_limit_mode.value,

        "variable_star_limit_value": report.variable_star_limit_value,

    }

    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")





def _build_aavso_extended_observations(

    report: ProcessingReport,

    selected_measurements: list[PhotometryMeasurement],

    *,

    science_metadata: ScienceExportMetadata | None,

    measurement_index: _ExportMeasurementIndex | None = None,

) -> tuple[list[AavsoExtendedObservation], dict[str, object]]:

    metadata = science_metadata or ScienceExportMetadata()

    reference_lookup = {star.source_id: star for star in report.reference_stars}

    resolved_measurement_index = measurement_index or _build_export_measurement_index(report.measurements)

    observations: list[AavsoExtendedObservation] = []

    skipped_measurements: list[dict[str, object]] = []

    row_warnings: list[str] = []

    for measurement in selected_measurements:

        observation, skip_reason = _aavso_extended_observation(

            measurement,

            resolved_measurement_index,

            reference_lookup,

            metadata,

        )

        if observation is None:

            if skip_reason is not None:

                skipped_measurements.append(

                    {

                        "source_id": measurement.source_id,

                        "source_name": measurement.source_name,

                        "file_path": str(measurement.file_path),

                        "filter_name": measurement.filter_name or "",

                        "reason": skip_reason,

                    }

                )

            continue

        observations.append(observation)

        row_warnings.extend(observation.warnings)



    warnings: list[str] = []

    if not metadata.observer_code:

        warnings.append("Observer code is blank; fill in Observer Code in Settings before uploading to AAVSO.")

    if not metadata.aavso_chart_id:

        warnings.append("AAVSO sequence/chart ID is blank; set it in Settings if you used an AAVSO sequence.")

    if any(item.mtype == "DIF" for item in observations):

        warnings.append("At least one exported row remains differential (MTYPE=DIF); no comparison-star zero point was available for every row.")

    if any(item.filter_code == "O" for item in observations):

        warnings.append("At least one exported row uses filter code O and should be reviewed before upload.")

    if any("Airmass missing" in warning for warning in row_warnings):

        warnings.append("At least one exported row is missing airmass; no header value or computed site fallback was available.")

    if skipped_measurements:

        warnings.append(f"Skipped {len(skipped_measurements)} measurement(s) that were not exportable to AAVSO.")

    warnings.extend(row_warnings)

    deduplicated_warnings: list[str] = []

    for warning in warnings:

        if warning and warning not in deduplicated_warnings:

            deduplicated_warnings.append(warning)



    payload = {

        "export_profile": "aavso-extended-v1",

        "observer_code_present": bool(metadata.observer_code.strip()),

        "chart_id_present": bool(metadata.aavso_chart_id.strip()),

        "transformed_requested": bool(metadata.transformed),

        "observation_count": len(observations),

        "standard_observation_count": sum(1 for item in observations if item.mtype == "STD"),

        "differential_observation_count": sum(1 for item in observations if item.mtype == "DIF"),

        "warning_count": len(deduplicated_warnings),

        "warnings": deduplicated_warnings,

        "skipped_measurement_count": len(skipped_measurements),

        "skipped_measurements": skipped_measurements,

        "rows": [

            {

                "source_id": item.source_id,

                "source_name": item.source_name,

                "filter_code": item.filter_code,

                "mtype": item.mtype,

                "magnitude": item.magnitude,

                "zero_point_mag": item.zero_point_mag,

                "warnings": item.warnings,

            }

            for item in observations

        ],

    }

    return observations, payload





def _write_aavso_extended_file(

    observations: list[AavsoExtendedObservation],

    output_path: Path,

    *,

    science_metadata: ScienceExportMetadata | None,

) -> int:

    metadata = science_metadata or ScienceExportMetadata()

    lines = [

        "#TYPE=EXTENDED",

        f"#OBSCODE={_aavso_header_text(metadata.observer_code)}",

        f"#SOFTWARE={_aavso_header_text(_aavso_software_header())}",

        "#DELIM=,",

        "#DATE=JD",

        "#OBSTYPE=CCD",

        f"#{','.join(_AAVSO_EXTENDED_COLUMNS)}",

    ]



    for item in observations:

        lines.append(item.export_line)



    output_path.write_text("\n".join(lines) + "\n", encoding="ascii", errors="replace")

    return len(observations)





def _write_aavso_preflight_json(payload: dict[str, object], output_path: Path) -> None:

    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")





def _aavso_extended_observation(

    measurement: PhotometryMeasurement,

    measurement_index: _ExportMeasurementIndex,

    reference_lookup: dict[str, object],

    metadata: ScienceExportMetadata,

) -> tuple[AavsoExtendedObservation | None, str | None]:

    if measurement.is_reference or not measurement.is_variable:

        return None, None

    if measurement.excluded_from_analysis or measurement.is_saturated:

        return None, "measurement_excluded_or_saturated"

    if measurement.observation_time is None:

        return None, "observation_time_missing"



    comparison_measurements = _comparison_measurements_for_export(measurement, measurement_index)

    observation_timestamp = _aavso_observation_timestamp(measurement)

    standard_magnitude, standard_magnitude_error, zero_point_mag, zero_point_source_count = _resolved_export_calibration(

        measurement,

        comparison_measurements,

        reference_lookup,

    )



    warnings: list[str] = []

    if metadata.transformed and standard_magnitude is None:

        warnings.append("Transformed export requested but no calibrated standard magnitude was available; row exported as DIF.")



    mtype = "STD" if standard_magnitude is not None else "DIF"

    export_magnitude = standard_magnitude if mtype == "STD" else measurement.differential_magnitude

    export_magnitude_error = standard_magnitude_error if mtype == "STD" else measurement.differential_magnitude_error

    if export_magnitude is None:

        return None, "submit_ready_magnitude_missing"



    comparison_name = "na"

    comparison_mag = "na"

    comparison_reference_mag = None

    if len(comparison_measurements) > 1 or len(measurement.comparison_source_ids) > 1:

        comparison_name = "ENSEMBLE"

    elif comparison_measurements:

        comparison_row = comparison_measurements[0]

        comparison_name = _aavso_text(comparison_row.source_name or comparison_row.source_id, 20)

        comparison_reference_mag = _aavso_reference_magnitude(comparison_row, reference_lookup, zero_point_mag)

        comparison_mag = _aavso_number(comparison_row.instrumental_magnitude, precision=3, default="na")

    elif measurement.comparison_source_names or measurement.comparison_source_ids:

        comparison_name = _aavso_text(

            (measurement.comparison_source_names or measurement.comparison_source_ids)[0],

            20,

        )

        warnings.append("Comparison star measurements were unavailable for this row.")



    check_measurement = _aavso_check_measurement(measurement, measurement_index)

    check_name = _aavso_text(check_measurement.source_name or check_measurement.source_id, 20) if check_measurement else "na"

    check_reference_mag = _aavso_reference_magnitude(check_measurement, reference_lookup, zero_point_mag) if check_measurement else None

    check_mag = _aavso_number(check_measurement.instrumental_magnitude, precision=3, default="na") if check_measurement else "na"

    export_filter_name = metadata.filter_system.strip() if metadata.filter_system else (measurement.filter_name or "")

    filter_code, filter_note = _aavso_filter_code(export_filter_name)

    if metadata.filter_system and measurement.filter_name and measurement.filter_name.strip() != metadata.filter_system.strip():

        override_note = f"Source filter: {_aavso_free_text(measurement.filter_name)}; exported as {_aavso_free_text(metadata.filter_system)} per settings"

        filter_note = override_note if filter_note is None else f"{filter_note}; {override_note}"

    if filter_code == "O":

        warnings.append("Filter could not be mapped to a standard AAVSO code.")

    airmass = _aavso_airmass(measurement, metadata, observation_time=observation_timestamp)

    if airmass is None:

        warnings.append(f"Airmass missing for {measurement.file_path.name}.")

    notes = _aavso_notes(

        measurement,

        metadata,

        comparison_measurements,

        check_measurement,

        filter_note,

        comparison_reference_mag,

        check_reference_mag,

        zero_point_source_count,

        airmass,

    )



    fields = [

        _aavso_text(measurement.source_name, 30),

        _aavso_julian_date(observation_timestamp),

        _aavso_number(export_magnitude, precision=4, default="na"),

        _aavso_number(export_magnitude_error, precision=3, default="na"),

        filter_code,

        "YES" if mtype == "STD" and metadata.transformed else "NO",

        mtype,

        comparison_name,

        comparison_mag,

        check_name,

        check_mag,

        _aavso_number(airmass, precision=3, default="na"),

        "na",

        _aavso_text(metadata.aavso_chart_id, 20),

        notes,

    ]

    return (

        AavsoExtendedObservation(

            source_id=measurement.source_id,

            source_name=measurement.source_name,

            filter_code=filter_code,

            mtype=mtype,

            magnitude=float(export_magnitude),

            export_line=",".join(fields),

            warnings=warnings,

            zero_point_mag=zero_point_mag,

        ),

        None,

    )





def _write_science_export_readme(

    output_path: Path,

    *,

    aavso_upload_file: str,

    preflight_file: str,

    accepted_csv_file: str,

    accepted_json_file: str,

    summary_file: str,

    reduction_manifest_file: str,

    provenance_manifest_file: str,

) -> None:

    lines = [

        "Citizen Photometry Science Export",

        "",

        f"Upload this file to AAVSO: {aavso_upload_file}",

        "",

        "Before uploading:",

        f"- Review {preflight_file} for warnings and skipped rows.",

        f"- Use {accepted_csv_file} or {accepted_json_file} to inspect the accepted observations that went into the AAVSO file.",

        "- If you used a non-AAVSO chart, replace the CHART field with K and describe the chart source in NOTES before upload.",

        "",

        "Other files in this folder:",

        f"- {summary_file}: compact export summary.",

        f"- {reduction_manifest_file}: reduction metadata and submission context.",

        f"- {provenance_manifest_file}: provenance and audit trail for the export.",

        "",

        "Note: annotated images are not required for AAVSO submission and are not included in this report export.",

    ]

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")





def _aavso_zero_point_mag(

    comparison_measurements: list[PhotometryMeasurement],

    reference_lookup: dict[str, object],

) -> tuple[float | None, int]:

    zero_points: list[float] = []

    for comparison_measurement in comparison_measurements:

        catalog_magnitude = _aavso_reference_magnitude(comparison_measurement, reference_lookup, None)

        if catalog_magnitude is None or comparison_measurement.instrumental_magnitude is None:

            continue

        zero_points.append(catalog_magnitude - comparison_measurement.instrumental_magnitude)

    if not zero_points:

        return None, 0

    return median(zero_points), len(zero_points)





def _resolved_export_calibration(

    measurement: PhotometryMeasurement,

    comparison_measurements: list[PhotometryMeasurement],

    reference_lookup: dict[str, object],

) -> tuple[float | None, float | None, float | None, int]:

    zero_point_mag = measurement.zero_point_magnitude

    zero_point_source_count = measurement.zero_point_source_count

    if zero_point_mag is None or zero_point_source_count <= 0:

        zero_point_mag, zero_point_source_count = _aavso_zero_point_mag(comparison_measurements, reference_lookup)



    calibrated_magnitude = measurement.calibrated_magnitude

    if calibrated_magnitude is None and measurement.instrumental_magnitude is not None and zero_point_mag is not None:

        calibrated_magnitude = measurement.instrumental_magnitude + zero_point_mag



    calibrated_magnitude_error = measurement.calibrated_magnitude_error

    if calibrated_magnitude_error is None and calibrated_magnitude is not None:

        calibrated_magnitude_error = measurement.differential_magnitude_error



    return calibrated_magnitude, calibrated_magnitude_error, zero_point_mag, zero_point_source_count





def _check_diagnostics_for_measurement(

    measurement: PhotometryMeasurement,

    measurement_index: _ExportMeasurementIndex,

    reference_lookup: dict[str, object],

    check_rms_lookup: dict[tuple[str, str], float],

) -> dict[str, object]:

    empty = {

        "check_source_id": "",

        "check_source_name": "",

        "check_reference_magnitude": None,

        "check_calibrated_magnitude": None,

        "check_residual_magnitude": None,

        "check_residual_rms": None,

    }

    if measurement.is_reference or not measurement.is_variable:

        return empty



    comparison_measurements = _comparison_measurements_for_export(measurement, measurement_index)

    _calibrated_magnitude, _calibrated_error, zero_point_mag, _zero_point_count = _resolved_export_calibration(

        measurement,

        comparison_measurements,

        reference_lookup,

    )

    check_measurement = _aavso_check_measurement(measurement, measurement_index)

    if check_measurement is None:

        return empty



    check_reference_magnitude = _check_reference_magnitude(check_measurement, reference_lookup)

    check_calibrated_magnitude = None

    if check_measurement.calibrated_magnitude is not None:

        check_calibrated_magnitude = check_measurement.calibrated_magnitude

    elif zero_point_mag is not None and check_measurement.instrumental_magnitude is not None:

        check_calibrated_magnitude = float(check_measurement.instrumental_magnitude + zero_point_mag)



    check_residual_magnitude = None

    if check_calibrated_magnitude is not None and check_reference_magnitude is not None:

        check_residual_magnitude = float(check_calibrated_magnitude - check_reference_magnitude)



    return {

        "check_source_id": check_measurement.source_id,

        "check_source_name": check_measurement.source_name,

        "check_reference_magnitude": check_reference_magnitude,

        "check_calibrated_magnitude": check_calibrated_magnitude,

        "check_residual_magnitude": check_residual_magnitude,

        "check_residual_rms": check_rms_lookup.get(_series_filter_key(measurement)),

    }





def _check_residual_rms_by_series(

    measurements: list[PhotometryMeasurement],

    measurement_index: _ExportMeasurementIndex,

    reference_lookup: dict[str, object],

) -> dict[tuple[str, str], float]:

    grouped_residuals: dict[tuple[str, str], list[float]] = {}

    for measurement in measurements:

        if measurement.is_reference or not measurement.is_variable:

            continue

        diagnostics = _check_diagnostics_for_measurement(measurement, measurement_index, reference_lookup, {})

        residual = diagnostics["check_residual_magnitude"]

        if residual is None or not np.isfinite(residual):

            continue

        grouped_residuals.setdefault(_series_filter_key(measurement), []).append(float(residual))



    rms_lookup: dict[tuple[str, str], float] = {}

    for key, residuals in grouped_residuals.items():

        if not residuals:

            continue

        residual_array = np.asarray(residuals, dtype=float)

        rms_lookup[key] = float(np.sqrt(np.mean(np.square(residual_array))))

    return rms_lookup





def _series_filter_key(measurement: PhotometryMeasurement) -> tuple[str, str]:

    return (measurement.source_id, measurement.filter_name or "")





def _check_reference_magnitude(

    measurement: PhotometryMeasurement,

    reference_lookup: dict[str, object],

) -> float | None:

    if measurement.catalog_magnitude is not None:

        return float(measurement.catalog_magnitude)

    reference_star = reference_lookup.get(measurement.source_id)

    catalog_magnitude = getattr(reference_star, "magnitude", None)

    if catalog_magnitude is None:

        return None

    return float(catalog_magnitude)





def _aavso_reference_magnitude(

    measurement: PhotometryMeasurement | None,

    reference_lookup: dict[str, object],

    zero_point_mag: float | None,

) -> float | None:

    if measurement is None:

        return None

    if measurement.catalog_magnitude is not None:

        return float(measurement.catalog_magnitude)

    reference_star = reference_lookup.get(measurement.source_id)

    catalog_magnitude = getattr(reference_star, "magnitude", None)

    if catalog_magnitude is not None:

        return float(catalog_magnitude)

    if zero_point_mag is not None and measurement.instrumental_magnitude is not None:

        return float(measurement.instrumental_magnitude + zero_point_mag)

    return None





def _aavso_check_measurement(

    variable_measurement: PhotometryMeasurement,

    measurement_index: _ExportMeasurementIndex,

) -> PhotometryMeasurement | None:

    candidates = [

        measurement

        for measurement in measurement_index.check_by_frame_filter.get(_measurement_frame_filter_key(variable_measurement), [])

        if measurement.source_id != variable_measurement.source_id

    ]

    if not candidates:

        return None

    return candidates[0]





def _aavso_filter_code(filter_name: str | None) -> tuple[str, str | None]:

    if not filter_name:

        return "O", "Original filter missing in source metadata"

    normalized = filter_name.strip().lower().replace(" ", "").replace("-", "_")

    code = _AAVSO_FILTER_ALIASES.get(normalized)

    if code is not None:

        return code, None

    return "O", f"Original filter: {_aavso_free_text(filter_name)}"





def _aavso_notes(

    measurement: PhotometryMeasurement,

    metadata: ScienceExportMetadata,

    comparison_measurements: list[PhotometryMeasurement],

    check_measurement: PhotometryMeasurement | None,

    filter_note: str | None,

    comparison_reference_mag: float | None,

    check_reference_mag: float | None,

    zero_point_source_count: int,

    airmass: float | None,

) -> str:

    note_parts: list[str] = []

    if metadata.reduction_notes:

        note_parts.append(_aavso_free_text(metadata.reduction_notes))

    if measurement.flags:

        note_parts.append(f"Flags: {_aavso_free_text('; '.join(measurement.flags))}")

    if filter_note:

        note_parts.append(_aavso_free_text(filter_note))

    if zero_point_source_count > 0:

        note_parts.append(f"Zero-point sources: {zero_point_source_count}")

    if airmass is None:

        note_parts.append("Airmass unavailable")



    subfields: list[str] = []

    if measurement.instrumental_magnitude is not None:

        subfields.append(f"|VMAGINS={_aavso_number(measurement.instrumental_magnitude, precision=3, default='na')}")

    if len(comparison_measurements) == 1 and comparison_measurements[0].instrumental_magnitude is not None:

        subfields.append(

            f"|CMAGINS={_aavso_number(comparison_measurements[0].instrumental_magnitude, precision=3, default='na')}"

        )

    if comparison_reference_mag is not None:

        subfields.append(f"|CREFMAG={_aavso_number(comparison_reference_mag, precision=3, default='na')}")

    if check_measurement is not None and check_measurement.instrumental_magnitude is not None:

        subfields.append(f"|KMAGINS={_aavso_number(check_measurement.instrumental_magnitude, precision=3, default='na')}")

    if check_reference_mag is not None:

        subfields.append(f"|KREFMAG={_aavso_number(check_reference_mag, precision=3, default='na')}")



    note_text = " ".join(part for part in note_parts if part)

    if note_text and subfields:

        return _aavso_note_text(note_text + "".join(subfields))

    if note_text:

        return _aavso_note_text(note_text)

    if subfields:

        return _aavso_note_text("".join(subfields))

    return "na"





def _aavso_julian_date(observation_time: object) -> str:

    jd = _julian_date_utc(observation_time)

    return f"{jd:.5f}" if jd is not None else "na"





def _aavso_number(value: float | None, *, precision: int, default: str) -> str:

    if value is None:

        return default

    formatted = f"{float(value):.{precision}f}".rstrip("0")

    if formatted.endswith("."):

        formatted += "0"

    return formatted





def _aavso_free_text(value: str) -> str:

    return value.replace("\r", " ").replace("\n", " ").replace(",", ";").replace("|", "/").strip()





def _aavso_note_text(value: str) -> str:

    normalized = value.replace("\r", " ").replace("\n", " ").replace(",", ";").strip()

    return normalized or "na"





def _aavso_header_text(value: str | None) -> str:

    return _aavso_free_text(value or "")





def _aavso_airmass(

    measurement: PhotometryMeasurement,

    metadata: ScienceExportMetadata,

    *,

    observation_time: object | None = None,

) -> float | None:

    header_airmass = _header_airmass(measurement.file_path)

    if header_airmass is not None:

        return header_airmass

    return _computed_airmass(measurement, metadata, observation_time=observation_time)





def _aavso_observation_timestamp(measurement: PhotometryMeasurement) -> object:

    observation_time = measurement.observation_time

    if observation_time is None:

        return None

    exposure_seconds = _header_exposure_seconds(measurement.file_path)

    if exposure_seconds is None or exposure_seconds <= 0:

        return observation_time

    return observation_time + timedelta(seconds=(exposure_seconds / 2.0))





@lru_cache(maxsize=512)

def _header_airmass(image_path: Path) -> float | None:

    try:

        header = read_header(image_path)

    except Exception:

        return None

    for key in ("AIRMASS", "SECZ", "SECAIRM", "AIRMAS"):

        value = header.get(key)

        if value in (None, ""):

            continue

        try:

            numeric = float(value)

        except (TypeError, ValueError):

            continue

        if numeric > 0:

            return numeric

    return None





@lru_cache(maxsize=512)

def _header_exposure_seconds(image_path: Path) -> float | None:

    try:

        header = read_header(image_path)

    except Exception:

        return None

    for key in ("EXPTIME", "EXPOSURE", "EXPOSURE_TIME"):

        value = header.get(key)

        if value in (None, ""):

            continue

        try:

            numeric = float(value)

        except (TypeError, ValueError):

            continue

        if numeric > 0:

            return numeric

    return None





def _computed_airmass(

    measurement: PhotometryMeasurement,

    metadata: ScienceExportMetadata,

    *,

    observation_time: object | None = None,

) -> float | None:

    return _computed_airmass_cached(

        observation_time if observation_time is not None else measurement.observation_time,

        float(measurement.ra_deg),

        float(measurement.dec_deg),

        metadata.observing_site_latitude_deg,

        metadata.observing_site_longitude_deg,

        metadata.observing_site_elevation_m,

    )





@lru_cache(maxsize=8192)

def _computed_airmass_cached(

    observation_time: datetime | None,

    ra_deg: float,

    dec_deg: float,

    latitude_deg: float | None,

    longitude_deg: float | None,

    elevation_m: float | None,

) -> float | None:

    return _computed_airmass_uncached(

        observation_time,

        ra_deg,

        dec_deg,

        latitude_deg,

        longitude_deg,

        elevation_m,

    )





def _computed_airmass_uncached(

    observation_time: datetime | None,

    ra_deg: float,

    dec_deg: float,

    latitude_deg: float | None,

    longitude_deg: float | None,

    elevation_m: float | None,

) -> float | None:

    if observation_time is None:

        return None

    if latitude_deg is None or longitude_deg is None:

        return None

    try:

        location = EarthLocation(

            lat=float(latitude_deg) * u.deg,

            lon=float(longitude_deg) * u.deg,

            height=float(elevation_m or 0.0) * u.m,

        )

        target = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")

        altaz = target.transform_to(AltAz(obstime=Time(observation_time, scale="utc"), location=location))

        secz = getattr(altaz, "secz", None)

        if secz is None:

            return None

        numeric = float(secz.value)

    except Exception:

        return None

    if not math.isfinite(numeric) or numeric <= 0:

        return None

    return numeric





def _aavso_text(value: str | None, limit: int | None = None) -> str:

    normalized = _aavso_free_text(value or "")

    if limit is not None:

        normalized = normalized[:limit]

    return normalized or "na"





def _plot_file_name(series: LightCurveSeries) -> str:

    safe_name = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in series.source_name)

    safe_filter = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in series.filter_name)

    return f"{safe_name}_{safe_filter}.png"





def _annotated_image_file_name(measurement: PhotometryMeasurement) -> str:

    safe_name = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in measurement.source_name)

    safe_filter = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in (measurement.filter_name or "unknown"))

    safe_file = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in measurement.file_path.stem)

    return f"{safe_name}_{safe_filter}_{safe_file}.png"





def _comparison_measurements_for_export(

    measurement: PhotometryMeasurement,

    measurement_index: _ExportMeasurementIndex,

) -> list[PhotometryMeasurement]:

    if not measurement.comparison_source_ids:

        return []

    frame_filter_key = _measurement_frame_filter_key(measurement)

    return [

        measurement_index.reference_by_frame_filter_source[source_key]

        for source_key in [(*frame_filter_key, source_id) for source_id in measurement.comparison_source_ids]

        if source_key in measurement_index.reference_by_frame_filter_source

    ]





def _build_export_measurement_index(all_measurements: list[PhotometryMeasurement]) -> _ExportMeasurementIndex:

    reference_by_frame_filter_source: dict[tuple[str, str, str], PhotometryMeasurement] = {}

    check_by_frame_filter: dict[tuple[str, str], list[PhotometryMeasurement]] = {}

    for measurement in all_measurements:

        frame_filter_key = _measurement_frame_filter_key(measurement)

        if measurement.is_reference:

            reference_by_frame_filter_source[(*frame_filter_key, measurement.source_id)] = measurement

            continue

        if measurement.is_check:

            check_by_frame_filter.setdefault(frame_filter_key, []).append(measurement)

    for frame_filter_key, measurements in check_by_frame_filter.items():

        check_by_frame_filter[frame_filter_key] = sorted(

            measurements,

            key=lambda item: (item.source_name.lower(), item.source_id),

        )

    return _ExportMeasurementIndex(

        reference_by_frame_filter_source=reference_by_frame_filter_source,

        check_by_frame_filter=check_by_frame_filter,

    )





def _measurement_frame_filter_key(measurement: PhotometryMeasurement) -> tuple[str, str]:

    return (str(measurement.file_path), measurement.filter_name or "")





def _safe_suffix(value: str) -> str:

    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value).strip("_") or "filtered"


def export_light_curve_animated_gif(
    series: LightCurveSeries,
    output_path: Path,
    *,
    fit_config: object | None = None,
    y_axis_mode: str = "differential_magnitude",
    x_axis_mode: str = "datetime",
    phase_period_hours: float | None = None,
    phase_anchor_mode: str = "first_observation",
    plot_theme: str = "normal",
    custom_theme_colors: dict[str, str] | None = None,
    x_limits: tuple[float, float] | None = None,
    y_limits: tuple[float, float] | None = None,
    figure_size_inches: tuple[float, float] = (11.5, 6.6),
    dpi: int = 140,
    frame_duration_ms: int = _LIGHT_CURVE_GIF_FRAME_DURATION_MS,
    minimum_duration_seconds: float = _LIGHT_CURVE_GIF_MINIMUM_DURATION_SECONDS,
    progress_callback: Callable[[int, int, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> None:
    payload = build_light_curve_plot_payload(
        series,
        "No light-curve points are available for animation.",
        fit_config=fit_config,
        y_axis_mode=y_axis_mode,
        x_axis_mode=x_axis_mode,
        phase_period_hours=phase_period_hours,
        phase_anchor_mode=phase_anchor_mode,
    )
    animation_plan = _light_curve_animation_plan(
        payload,
        frame_duration_ms=frame_duration_ms,
        minimum_duration_seconds=minimum_duration_seconds,
    )
    progress_total = animation_plan.rendered_frame_count + 1
    if progress_callback is not None:
        progress_callback(0, progress_total, "Preparing animated light curve...")
    _raise_if_light_curve_animation_canceled(is_cancelled)
    resolved_x_limits, resolved_y_limits = _resolved_light_curve_animation_limits(
        payload,
        plot_theme=plot_theme,
        custom_theme_colors=custom_theme_colors,
        x_limits=x_limits,
        y_limits=y_limits,
        figure_size_inches=figure_size_inches,
        dpi=dpi,
    )
    _raise_if_light_curve_animation_canceled(is_cancelled)
    frames = _build_light_curve_animation_frames(
        payload,
        animation_plan=animation_plan,
        plot_theme=plot_theme,
        custom_theme_colors=custom_theme_colors,
        x_limits=resolved_x_limits,
        y_limits=resolved_y_limits,
        figure_size_inches=figure_size_inches,
        dpi=dpi,
        frame_duration_ms=frame_duration_ms,
        minimum_duration_seconds=minimum_duration_seconds,
        progress_callback=progress_callback,
        progress_total=progress_total,
        is_cancelled=is_cancelled,
    )
    _raise_if_light_curve_animation_canceled(is_cancelled)
    if progress_callback is not None:
        progress_callback(animation_plan.rendered_frame_count, progress_total, "Encoding animated GIF...")
    export_qimages_to_gif(
        frames,
        output_path,
        frame_duration_ms=frame_duration_ms,
        loop_count=0,
    )
    if progress_callback is not None:
        progress_callback(progress_total, progress_total, f"Saved animated GIF to {output_path.name}.")


def _resolved_light_curve_animation_limits(
    payload: LightCurvePlotPayload,
    *,
    plot_theme: str,
    custom_theme_colors: dict[str, str] | None,
    x_limits: tuple[float, float] | None,
    y_limits: tuple[float, float] | None,
    figure_size_inches: tuple[float, float],
    dpi: int,
) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    if x_limits is not None and y_limits is not None:
        return x_limits, y_limits
    figure = Figure(figsize=figure_size_inches, dpi=dpi)
    axis = figure.add_subplot(111)
    try:
        plot_light_curve_payload(
            axis,
            payload,
            theme=plot_theme,
            custom_theme_colors=custom_theme_colors,
            export_style="themed",
            x_limits=x_limits,
            y_limits=y_limits,
        )
        measured_x_limits = x_limits if x_limits is not None else tuple(float(value) for value in axis.get_xlim())
        measured_y_limits = y_limits if y_limits is not None else tuple(float(value) for value in axis.get_ylim())
        return measured_x_limits, measured_y_limits
    finally:
        figure.clear()


def _build_light_curve_animation_frames(
    payload: LightCurvePlotPayload,
    *,
    animation_plan: _LightCurveAnimationPlan | None = None,
    plot_theme: str,
    custom_theme_colors: dict[str, str] | None,
    x_limits: tuple[float, float] | None,
    y_limits: tuple[float, float] | None,
    figure_size_inches: tuple[float, float],
    dpi: int,
    frame_duration_ms: int,
    minimum_duration_seconds: float,
    progress_callback: Callable[[int, int, str], None] | None = None,
    progress_total: int | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> list[QImage]:
    plan = animation_plan or _light_curve_animation_plan(
        payload,
        frame_duration_ms=frame_duration_ms,
        minimum_duration_seconds=minimum_duration_seconds,
    )

    frames: list[QImage] = []
    rendered_frame_count = 0

    def render_frame(frame_payload: LightCurvePlotPayload) -> QImage:
        nonlocal rendered_frame_count
        _raise_if_light_curve_animation_canceled(is_cancelled)
        image = _render_light_curve_payload_frame(
            frame_payload,
            plot_theme=plot_theme,
            custom_theme_colors=custom_theme_colors,
            x_limits=x_limits,
            y_limits=y_limits,
            figure_size_inches=figure_size_inches,
            dpi=dpi,
        )
        rendered_frame_count += 1
        if progress_callback is not None and progress_total is not None:
            progress_callback(
                rendered_frame_count,
                progress_total,
                f"Rendering animation frame {rendered_frame_count}/{plan.rendered_frame_count}...",
            )
        return image

    empty_payload = replace(payload, points=(), fit_x_values=None, fit_y_values=None)
    empty_frame = render_frame(empty_payload)
    frames.extend([empty_frame.copy()] * _LIGHT_CURVE_GIF_INITIAL_HOLD_FRAMES)

    point_count = len(payload.points)
    fit_point_count = 0 if payload.fit_x_values is None else int(len(payload.fit_x_values))
    if plan.point_stage_frames > 0:
        for frame_index in range(plan.point_stage_frames):
            visible_point_count = point_count if point_count <= 0 else min(point_count, max(1, int(math.ceil(((frame_index + 1) * point_count) / plan.point_stage_frames))))
            point_payload = replace(
                payload,
                points=tuple(payload.points[:visible_point_count]),
                fit_x_values=None,
                fit_y_values=None,
                empty_message=None,
            )
            frames.append(render_frame(point_payload))

    if plan.fit_stage_frames > 0 and payload.fit_x_values is not None and payload.fit_y_values is not None:
        for frame_index in range(plan.fit_stage_frames):
            visible_fit_count = min(
                fit_point_count,
                max(2, int(math.ceil(((frame_index + 1) * fit_point_count) / plan.fit_stage_frames))),
            )
            fit_payload = replace(
                payload,
                fit_x_values=np.asarray(payload.fit_x_values[:visible_fit_count], dtype=float),
                fit_y_values=np.asarray(payload.fit_y_values[:visible_fit_count], dtype=float),
                empty_message=None,
            )
            frames.append(render_frame(fit_payload))

    final_payload = replace(payload, empty_message=None)
    final_frame = render_frame(final_payload)
    frames.extend([final_frame.copy()] * max(1, plan.final_hold_frames))
    return frames


@dataclass(frozen=True)
class _LightCurveAnimationPlan:
    point_stage_frames: int
    fit_stage_frames: int
    final_hold_frames: int
    rendered_frame_count: int


def _light_curve_animation_plan(
    payload: LightCurvePlotPayload,
    *,
    frame_duration_ms: int,
    minimum_duration_seconds: float,
) -> _LightCurveAnimationPlan:
    baseline_target_frames = 125
    point_count = len(payload.points)
    fit_point_count = 0 if payload.fit_x_values is None else int(len(payload.fit_x_values))
    fit_stage_frames = 0 if fit_point_count <= 1 else _LIGHT_CURVE_GIF_FIT_STAGE_TARGET_FRAMES
    point_stage_frames = max(
        point_count,
        max(0, baseline_target_frames - _LIGHT_CURVE_GIF_INITIAL_HOLD_FRAMES - _LIGHT_CURVE_GIF_FINAL_HOLD_FRAMES - fit_stage_frames),
    )
    required_total_frames = max(1, int(math.ceil((max(0.0, float(minimum_duration_seconds)) * 1000.0) / max(20, int(frame_duration_ms)))))
    total_frames = _LIGHT_CURVE_GIF_INITIAL_HOLD_FRAMES + point_stage_frames + fit_stage_frames + _LIGHT_CURVE_GIF_FINAL_HOLD_FRAMES
    final_hold_frames = _LIGHT_CURVE_GIF_FINAL_HOLD_FRAMES + max(0, required_total_frames - total_frames)
    return _LightCurveAnimationPlan(
        point_stage_frames=point_stage_frames,
        fit_stage_frames=fit_stage_frames,
        final_hold_frames=final_hold_frames,
        rendered_frame_count=point_stage_frames + fit_stage_frames + 2,
    )


def _raise_if_light_curve_animation_canceled(is_cancelled: Callable[[], bool] | None) -> None:
    if is_cancelled is not None and is_cancelled():
        raise AnimatedLightCurveExportCanceled("Animated light-curve export canceled.")


def _render_light_curve_payload_frame(
    payload: LightCurvePlotPayload,
    *,
    plot_theme: str,
    custom_theme_colors: dict[str, str] | None,
    x_limits: tuple[float, float] | None,
    y_limits: tuple[float, float] | None,
    figure_size_inches: tuple[float, float],
    dpi: int,
) -> QImage:
    figure = Figure(figsize=figure_size_inches, dpi=dpi)
    axis = figure.add_subplot(111)
    try:
        plot_light_curve_payload(
            axis,
            payload,
            theme=plot_theme,
            custom_theme_colors=custom_theme_colors,
            export_style="themed",
            x_limits=x_limits,
            y_limits=y_limits,
            show_empty_message=False,
            grid_alpha_override=_LIGHT_CURVE_GIF_GRID_ALPHA,
            show_minor_grid=True,
        )
        figure.tight_layout()
        buffer = BytesIO()
        figure.savefig(buffer, format="png", dpi=dpi, facecolor=figure.get_facecolor())
        image = QImage()
        if not image.loadFromData(buffer.getvalue(), "PNG"):
            raise OSError("Unable to render the animated light-curve frame.")
        return image
    finally:
        figure.clear()

