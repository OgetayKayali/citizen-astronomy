from __future__ import annotations

import hashlib
import json
import os
import pickle
import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from photometry_app.core.discovery import MovingObjectCandidate
from photometry_app.core.models import CatalogStar, FileScanResult, LightCurveSeries, PhotometryMeasurement
from photometry_app.core.settings import AppSettings
from photometry_app.core.transient import TransientCandidate


TRAINING_MODE_TRANSIENT = "transient"
TRAINING_MODE_ASTEROID_DISCOVERY = "asteroid_discovery"
TRAINING_MODE_DIFFERENTIAL = "differential_detectability"
_SCHEMA_VERSION = 1


@dataclass(slots=True)
class CandidateLabelRecord:
    mode: str
    candidate_key: str
    label: str
    confidence: str
    notes: str
    updated_utc: datetime


@dataclass(slots=True)
class CandidatePrediction:
    model_id: str
    label: str
    confidence: float
    probabilities: dict[str, float]


@dataclass(slots=True)
class CandidateTrainingResult:
    trained: bool
    message: str
    model_id: str | None = None
    example_count: int = 0
    class_counts: dict[str, int] | None = None
    metrics: dict[str, float] | None = None


def default_training_database_path() -> Path:
    override = os.getenv("CITIZEN_PHOTOMETRY_TRAINING_DB")
    if override:
        return Path(override).expanduser()
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "CitizenPhotometry" / "candidate-training.sqlite3"
    return Path.home() / ".citizen-photometry" / "candidate-training.sqlite3"


def transient_candidate_training_key(root_path: Path, candidate: TransientCandidate) -> str:
    parts = [
        str(Path(root_path).expanduser()),
        f"{candidate.ra_deg:.5f}",
        f"{candidate.dec_deg:.5f}",
        ",".join(sorted(detection.source_path.name for detection in candidate.detections)),
    ]
    return _digest_key(parts)


def transient_candidate_training_features(candidate: TransientCandidate) -> dict[str, float]:
    snrs = np.array([detection.snr for detection in candidate.detections], dtype=float)
    fluxes = np.array([detection.flux for detection in candidate.detections], dtype=float)
    peaks = np.array([detection.peak_value for detection in candidate.detections], dtype=float)
    separations = [detection.nearest_catalog_separation_arcsec for detection in candidate.detections]
    finite_separations = np.array([value for value in separations if value is not None and np.isfinite(value)], dtype=float)
    span_seconds = 0.0
    if candidate.first_observation is not None and candidate.last_observation is not None:
        span_seconds = max(0.0, (candidate.last_observation - candidate.first_observation).total_seconds())
    return {
        "frame_count": float(candidate.frame_count),
        "detection_count": float(candidate.detection_count),
        "detection_fraction": _safe_ratio(candidate.detection_count, max(1, candidate.frame_count)),
        "median_snr": float(candidate.median_snr),
        "max_snr": float(candidate.max_snr),
        "snr_stddev": _nanstd(snrs),
        "snr_min": _nanmin(snrs),
        "flux_median": _nanmedian(fluxes),
        "flux_max": _nanmax(fluxes),
        "flux_min": _nanmin(fluxes),
        "flux_abs_median": _nanmedian(np.abs(fluxes)),
        "peak_max": _nanmax(peaks),
        "peak_median": _nanmedian(peaks),
        "variability_snr": float(candidate.variability_snr),
        "flux_ratio": float(candidate.flux_ratio),
        "has_catalog_neighbor": 1.0 if candidate.nearest_catalog_separation_arcsec is not None else 0.0,
        "nearest_catalog_separation_arcsec": float(candidate.nearest_catalog_separation_arcsec or 9999.0),
        "detection_catalog_match_fraction": _safe_ratio(float(finite_separations.size), max(1, candidate.detection_count)),
        "median_detection_catalog_separation_arcsec": _nanmedian(finite_separations) if finite_separations.size else 9999.0,
        "observation_span_seconds": span_seconds,
    }


def transient_candidate_training_payload(root_path: Path, candidate: TransientCandidate) -> dict[str, Any]:
    return {
        "root_path": str(Path(root_path).expanduser()),
        "candidate_id": candidate.candidate_id,
        "ra_deg": candidate.ra_deg,
        "dec_deg": candidate.dec_deg,
        "summary_text": candidate.summary_text,
        "source_paths": [str(detection.source_path) for detection in candidate.detections],
        "first_observation": _datetime_to_text(candidate.first_observation),
        "last_observation": _datetime_to_text(candidate.last_observation),
    }


def moving_object_candidate_training_key(root_path: Path, candidate: MovingObjectCandidate) -> str:
    detections = tuple(candidate.frame_detections)
    source_names = ",".join(sorted(detection.source_path.name for detection in detections))
    first_time = min((detection.observation_time for detection in detections), default=None)
    last_time = max((detection.observation_time for detection in detections), default=None)
    parts = [
        str(Path(root_path).expanduser()),
        candidate.candidate_id,
        source_names,
        _datetime_to_text(first_time),
        _datetime_to_text(last_time),
        f"{candidate.start_x:.3f},{candidate.start_y:.3f}",
        f"{candidate.end_x:.3f},{candidate.end_y:.3f}",
        f"{candidate.motion_px_per_hour:.4f}",
    ]
    return _digest_key(parts)


def moving_object_candidate_training_features(candidate: MovingObjectCandidate) -> dict[str, float]:
    detections = tuple(candidate.frame_detections)
    snrs = np.array([detection.local_snr for detection in detections], dtype=float)
    peaks = np.array([detection.peak_value for detection in detections], dtype=float)
    x_values = np.array([detection.x for detection in detections], dtype=float)
    y_values = np.array([detection.y for detection in detections], dtype=float)
    frame_indices = np.array([detection.frame_index for detection in detections], dtype=float)
    span_seconds = 0.0
    if detections:
        first_observation = min(detection.observation_time for detection in detections)
        last_observation = max(detection.observation_time for detection in detections)
        span_seconds = max(0.0, (last_observation - first_observation).total_seconds())
    frame_index_span = 0.0
    if frame_indices.size:
        frame_index_span = float(np.nanmax(frame_indices) - np.nanmin(frame_indices) + 1.0)
    delta_x = float(candidate.end_x - candidate.start_x)
    delta_y = float(candidate.end_y - candidate.start_y)
    return {
        "detection_count": float(len(detections)),
        "frame_index_span": frame_index_span,
        "linked_frame_fraction": _safe_ratio(float(len(detections)), max(1.0, frame_index_span)),
        "average_snr": float(candidate.average_snr),
        "local_snr_min": _nanmin(snrs),
        "local_snr_max": _nanmax(snrs),
        "local_snr_median": _nanmedian(snrs),
        "local_snr_stddev": _nanstd(snrs),
        "peak_value": float(candidate.peak_value),
        "peak_min": _nanmin(peaks),
        "peak_max": _nanmax(peaks),
        "peak_median": _nanmedian(peaks),
        "fit_rms_px": float(candidate.fit_rms_px),
        "max_deflection_px": float(candidate.max_deflection_px),
        "motion_px_per_hour": float(candidate.motion_px_per_hour),
        "has_arcsec_motion": 1.0 if candidate.motion_arcsec_per_hour is not None else 0.0,
        "motion_arcsec_per_hour": float(candidate.motion_arcsec_per_hour or 0.0),
        "displacement_px": float(candidate.displacement_px),
        "delta_x": delta_x,
        "delta_y": delta_y,
        "abs_delta_x": abs(delta_x),
        "abs_delta_y": abs(delta_y),
        "start_x": float(candidate.start_x),
        "start_y": float(candidate.start_y),
        "end_x": float(candidate.end_x),
        "end_y": float(candidate.end_y),
        "x_stddev": _nanstd(x_values),
        "y_stddev": _nanstd(y_values),
        "observation_span_seconds": span_seconds,
        "method_is_synthetic_sweep": 1.0 if candidate.discovery_method == "synthetic_sweep" else 0.0,
    }


def moving_object_candidate_training_payload(root_path: Path, candidate: MovingObjectCandidate) -> dict[str, Any]:
    detections = tuple(candidate.frame_detections)
    first_observation = min((detection.observation_time for detection in detections), default=None)
    last_observation = max((detection.observation_time for detection in detections), default=None)
    return {
        "root_path": str(Path(root_path).expanduser()),
        "candidate_id": candidate.candidate_id,
        "summary_text": candidate.summary_text,
        "discovery_method": candidate.discovery_method,
        "first_observation": _datetime_to_text(first_observation),
        "last_observation": _datetime_to_text(last_observation),
        "frame_detections": [
            {
                "source_path": str(detection.source_path),
                "observation_time": _datetime_to_text(detection.observation_time),
                "frame_index": detection.frame_index,
                "x": detection.x,
                "y": detection.y,
                "ra_deg": detection.ra_deg,
                "dec_deg": detection.dec_deg,
                "local_snr": detection.local_snr,
                "peak_value": detection.peak_value,
            }
            for detection in detections
        ],
    }


def differential_source_training_key(
    root_path: Path,
    object_name: str,
    entry: CatalogStar,
    measurements: list[PhotometryMeasurement],
) -> str:
    source_names = sorted({measurement.file_path.name for measurement in measurements})
    filters = sorted({(measurement.filter_name or "-") for measurement in measurements})
    first_observation = min((measurement.observation_time for measurement in measurements), default=None)
    last_observation = max((measurement.observation_time for measurement in measurements), default=None)
    parts = [
        str(Path(root_path).expanduser()),
        str(object_name or ""),
        str(entry.catalog),
        str(entry.source_id),
        _datetime_to_text(first_observation),
        _datetime_to_text(last_observation),
        ",".join(source_names),
        ",".join(filters),
    ]
    return _digest_key(parts)


def differential_source_training_features(
    *,
    entry: CatalogStar,
    measurements: list[PhotometryMeasurement],
    series_list: list[LightCurveSeries],
    settings: AppSettings | None = None,
    scan_results: list[FileScanResult] | None = None,
) -> dict[str, float]:
    settings = settings if settings is not None else AppSettings()
    scan_results = [] if scan_results is None else scan_results

    differential_values = np.array(
        [
            float(measurement.differential_magnitude)
            for measurement in measurements
            if measurement.differential_magnitude is not None and np.isfinite(float(measurement.differential_magnitude))
        ],
        dtype=float,
    )
    magnitude_errors = np.array(
        [
            float(measurement.differential_magnitude_error)
            for measurement in measurements
            if measurement.differential_magnitude_error is not None and np.isfinite(float(measurement.differential_magnitude_error))
        ],
        dtype=float,
    )
    snrs = np.array(
        [float(measurement.snr) for measurement in measurements if measurement.snr is not None and np.isfinite(float(measurement.snr))],
        dtype=float,
    )
    fluxes = np.array(
        [float(measurement.flux) for measurement in measurements if measurement.flux is not None and np.isfinite(float(measurement.flux))],
        dtype=float,
    )
    flux_errors = np.array(
        [
            float(measurement.flux_error)
            for measurement in measurements
            if measurement.flux_error is not None and np.isfinite(float(measurement.flux_error))
        ],
        dtype=float,
    )
    comparison_scatter = np.array(
        [
            float(measurement.comparison_scatter)
            for measurement in measurements
            if measurement.comparison_scatter is not None and np.isfinite(float(measurement.comparison_scatter))
        ],
        dtype=float,
    )
    quality_scores = np.array(
        [float(measurement.quality_score) for measurement in measurements if np.isfinite(float(measurement.quality_score))],
        dtype=float,
    )
    background_levels = np.array(
        [
            float(measurement.background_level)
            for measurement in measurements
            if measurement.background_level is not None and np.isfinite(float(measurement.background_level))
        ],
        dtype=float,
    )
    background_scatter = np.array(
        [
            float(measurement.background_scatter)
            for measurement in measurements
            if measurement.background_scatter is not None and np.isfinite(float(measurement.background_scatter))
        ],
        dtype=float,
    )
    centroid_shifts = np.array(
        [
            float(measurement.centroid_shift_pixels)
            for measurement in measurements
            if measurement.centroid_shift_pixels is not None and np.isfinite(float(measurement.centroid_shift_pixels))
        ],
        dtype=float,
    )
    aperture_radii = np.array(
        [
            float(measurement.aperture_radius)
            for measurement in measurements
            if measurement.aperture_radius is not None and np.isfinite(float(measurement.aperture_radius))
        ],
        dtype=float,
    )
    annulus_inner_radii = np.array(
        [
            float(measurement.annulus_inner_radius)
            for measurement in measurements
            if measurement.annulus_inner_radius is not None and np.isfinite(float(measurement.annulus_inner_radius))
        ],
        dtype=float,
    )
    annulus_outer_radii = np.array(
        [
            float(measurement.annulus_outer_radius)
            for measurement in measurements
            if measurement.annulus_outer_radius is not None and np.isfinite(float(measurement.annulus_outer_radius))
        ],
        dtype=float,
    )
    comparison_counts = np.array([float(len(measurement.comparison_source_ids)) for measurement in measurements], dtype=float)
    saturation_fractions = np.array(
        [
            float(measurement.saturation_fraction)
            for measurement in measurements
            if measurement.saturation_fraction is not None and np.isfinite(float(measurement.saturation_fraction))
        ],
        dtype=float,
    )
    candidate_scores = np.array([float(series.candidate_score) for series in series_list], dtype=float)
    metric_values = {
        metric_name: np.array(
            [
                float(series.variability_metrics.get(metric_name))
                for series in series_list
                if metric_name in series.variability_metrics and np.isfinite(float(series.variability_metrics.get(metric_name)))
            ],
            dtype=float,
        )
        for metric_name in ("mad", "rms", "amplitude", "reduced_chi2", "von_neumann", "stetson_j")
    }
    exposures = np.array(
        [
            float(result.metadata.exposure_seconds)
            for result in scan_results
            if result.metadata.exposure_seconds is not None and np.isfinite(float(result.metadata.exposure_seconds))
        ],
        dtype=float,
    )
    widths = np.array(
        [float(result.metadata.width) for result in scan_results if result.metadata.width is not None],
        dtype=float,
    )
    heights = np.array(
        [float(result.metadata.height) for result in scan_results if result.metadata.height is not None],
        dtype=float,
    )
    binning_values = np.array(
        [
            float(parsed_binning)
            for result in scan_results
            if (parsed_binning := _parse_binning_value(result.metadata.binning)) is not None
        ],
        dtype=float,
    )
    measurement_count = len(measurements)
    valid_point_count = sum(1 for measurement in measurements if measurement.differential_magnitude is not None)
    excluded_point_count = sum(1 for measurement in measurements if measurement.excluded_from_analysis)
    flagged_count = sum(1 for measurement in measurements if measurement.flags)
    saturated_count = sum(1 for measurement in measurements if measurement.is_saturated)
    near_saturated_count = sum(1 for measurement in measurements if measurement.is_near_saturated)
    unique_file_count = len({measurement.file_path.resolve() for measurement in measurements if measurement.file_path.exists()})
    unique_filter_count = len({measurement.filter_name or "-" for measurement in measurements})
    first_observation = min((measurement.observation_time for measurement in measurements), default=None)
    last_observation = max((measurement.observation_time for measurement in measurements), default=None)
    observation_span_seconds = 0.0
    if first_observation is not None and last_observation is not None:
        observation_span_seconds = max(0.0, (last_observation - first_observation).total_seconds())

    return {
        "source_magnitude": float(entry.magnitude) if entry.magnitude is not None and np.isfinite(float(entry.magnitude)) else 99.0,
        "measurement_count": float(measurement_count),
        "valid_point_count": float(valid_point_count),
        "excluded_point_count": float(excluded_point_count),
        "flagged_count": float(flagged_count),
        "valid_point_fraction": _safe_ratio(valid_point_count, max(1, measurement_count)),
        "excluded_point_fraction": _safe_ratio(excluded_point_count, max(1, measurement_count)),
        "flagged_fraction": _safe_ratio(flagged_count, max(1, measurement_count)),
        "saturated_fraction": _safe_ratio(saturated_count, max(1, measurement_count)),
        "near_saturated_fraction": _safe_ratio(near_saturated_count, max(1, measurement_count)),
        "unique_file_count": float(unique_file_count),
        "unique_filter_count": float(unique_filter_count),
        "observation_span_seconds": observation_span_seconds,
        "median_snr": _nanmedian(snrs),
        "max_snr": _nanmax(snrs),
        "min_snr": _nanmin(snrs),
        "snr_stddev": _nanstd(snrs),
        "median_flux": _nanmedian(fluxes),
        "max_flux": _nanmax(fluxes),
        "min_flux": _nanmin(fluxes),
        "flux_stddev": _nanstd(fluxes),
        "median_flux_error": _nanmedian(flux_errors),
        "median_mag_error": _nanmedian(magnitude_errors),
        "light_curve_rms": _nanstd(differential_values),
        "light_curve_range": _nanmax(differential_values) - _nanmin(differential_values) if differential_values.size else 0.0,
        "median_comparison_scatter": _nanmedian(comparison_scatter),
        "median_quality_score": _nanmedian(quality_scores),
        "median_background_level": _nanmedian(background_levels),
        "median_background_scatter": _nanmedian(background_scatter),
        "median_centroid_shift_px": _nanmedian(centroid_shifts),
        "max_centroid_shift_px": _nanmax(centroid_shifts),
        "median_aperture_radius": _nanmedian(aperture_radii),
        "median_annulus_inner_radius": _nanmedian(annulus_inner_radii),
        "median_annulus_outer_radius": _nanmedian(annulus_outer_radii),
        "median_comparison_star_count": _nanmedian(comparison_counts),
        "max_saturation_fraction": _nanmax(saturation_fractions),
        "series_count": float(len(series_list)),
        "best_candidate_score": _nanmax(candidate_scores),
        "median_candidate_score": _nanmedian(candidate_scores),
        "best_metric_mad": _nanmax(metric_values["mad"]),
        "best_metric_rms": _nanmax(metric_values["rms"]),
        "best_metric_amplitude": _nanmax(metric_values["amplitude"]),
        "best_metric_reduced_chi2": _nanmax(metric_values["reduced_chi2"]),
        "best_metric_von_neumann": _nanmax(metric_values["von_neumann"]),
        "best_metric_stetson_j": _nanmax(metric_values["stetson_j"]),
        "median_exposure_seconds": _nanmedian(exposures),
        "min_exposure_seconds": _nanmin(exposures),
        "max_exposure_seconds": _nanmax(exposures),
        "median_frame_width": _nanmedian(widths),
        "median_frame_height": _nanmedian(heights),
        "median_binning": _nanmedian(binning_values),
        "site_latitude_deg": float(settings.observing_site_latitude_deg or 0.0),
        "site_longitude_deg": float(settings.observing_site_longitude_deg or 0.0),
        "site_elevation_m": float(settings.observing_site_elevation_m or 0.0),
        "telescope_focal_length_mm": float(settings.telescope_focal_length_mm or 0.0),
        "telescope_aperture_mm": float(settings.telescope_aperture_mm or 0.0),
        "telescope_focal_ratio": float(settings.telescope_focal_ratio or 0.0),
        "camera_pixel_size_um": float(settings.camera_pixel_size_um or 0.0),
        "bortle_scale": float(settings.bortle_scale or 0.0),
        "aperture_mode_fixed": 1.0 if str(settings.photometry_aperture_mode.value) == "fixed" else 0.0,
        "aperture_mode_fwhm_scaled": 1.0 if str(settings.photometry_aperture_mode.value) == "fwhm_scaled" else 0.0,
        "settings_aperture_radius_pixels": float(settings.aperture_radius_pixels),
        "settings_annulus_inner_radius_pixels": float(settings.annulus_inner_radius_pixels),
        "settings_annulus_outer_radius_pixels": float(settings.annulus_outer_radius_pixels),
        "settings_aperture_radius_fwhm_scale": float(settings.aperture_radius_fwhm_scale),
        "settings_annulus_inner_radius_fwhm_scale": float(settings.annulus_inner_radius_fwhm_scale),
        "settings_annulus_outer_radius_fwhm_scale": float(settings.annulus_outer_radius_fwhm_scale),
    }


def differential_source_training_payload(
    *,
    root_path: Path,
    object_name: str,
    entry: CatalogStar,
    measurements: list[PhotometryMeasurement],
    series_list: list[LightCurveSeries],
    settings: AppSettings | None = None,
    scan_results: list[FileScanResult] | None = None,
) -> dict[str, Any]:
    settings = settings if settings is not None else AppSettings()
    scan_results = [] if scan_results is None else scan_results
    filters = sorted({measurement.filter_name or "-" for measurement in measurements})
    return {
        "root_path": str(Path(root_path).expanduser()),
        "object_name": str(object_name or ""),
        "catalog": entry.catalog,
        "source_id": entry.source_id,
        "source_name": entry.name,
        "ra_deg": entry.ra_deg,
        "dec_deg": entry.dec_deg,
        "magnitude": entry.magnitude,
        "object_type": entry.object_type,
        "is_catalog_variable": bool(entry.is_variable),
        "filters": filters,
        "measurement_files": [str(measurement.file_path) for measurement in measurements],
        "series_filters": [series.filter_name for series in series_list],
        "catalog_metadata": dict(entry.metadata),
        "settings_snapshot": {
            "site_name": settings.site_name,
            "telescope": settings.telescope,
            "camera": settings.camera,
            "filter_system": settings.filter_system,
            "bortle_scale": settings.bortle_scale,
        },
        "scan_metadata": [
            {
                "path": str(result.path),
                "filter_name": result.metadata.filter_name,
                "exposure_seconds": result.metadata.exposure_seconds,
                "width": result.metadata.width,
                "height": result.metadata.height,
                "binning": result.metadata.binning,
            }
            for result in scan_results
        ],
    }


class CandidateTrainingStore:
    def __init__(self, database_path: Path | None = None) -> None:
        self.database_path = Path(database_path) if database_path is not None else default_training_database_path()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def record_label(
        self,
        *,
        mode: str,
        candidate_key: str,
        features: dict[str, float],
        payload: dict[str, Any],
        label: str,
        confidence: str = "sure",
        notes: str = "",
    ) -> CandidateLabelRecord:
        normalized_mode = _normalize_text(mode)
        normalized_label = _normalize_text(label)
        if not normalized_mode:
            raise ValueError("mode is required")
        if not normalized_label:
            raise ValueError("label is required")
        now = _utc_now()
        example_id = _example_id(normalized_mode, candidate_key)
        feature_json = _json_dumps(_numeric_features(features))
        payload_json = _json_dumps(payload)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO examples(example_id, mode, candidate_key, features_json, payload_json, created_utc, updated_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(example_id) DO UPDATE SET
                    features_json=excluded.features_json,
                    payload_json=excluded.payload_json,
                    updated_utc=excluded.updated_utc
                """,
                (example_id, normalized_mode, candidate_key, feature_json, payload_json, _datetime_to_text(now), _datetime_to_text(now)),
            )
            connection.execute(
                """
                INSERT INTO labels(example_id, label, confidence, notes, updated_utc)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(example_id) DO UPDATE SET
                    label=excluded.label,
                    confidence=excluded.confidence,
                    notes=excluded.notes,
                    updated_utc=excluded.updated_utc
                """,
                (example_id, normalized_label, _normalize_text(confidence) or "sure", str(notes or ""), _datetime_to_text(now)),
            )
        return CandidateLabelRecord(
            mode=normalized_mode,
            candidate_key=candidate_key,
            label=normalized_label,
            confidence=_normalize_text(confidence) or "sure",
            notes=str(notes or ""),
            updated_utc=now,
        )

    def label_for_key(self, mode: str, candidate_key: str) -> CandidateLabelRecord | None:
        example_id = _example_id(_normalize_text(mode), candidate_key)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT examples.mode, examples.candidate_key, labels.label, labels.confidence, labels.notes, labels.updated_utc
                FROM labels
                JOIN examples ON examples.example_id = labels.example_id
                WHERE labels.example_id = ?
                """,
                (example_id,),
            ).fetchone()
        if row is None:
            return None
        return CandidateLabelRecord(
            mode=str(row["mode"]),
            candidate_key=str(row["candidate_key"]),
            label=str(row["label"]),
            confidence=str(row["confidence"]),
            notes=str(row["notes"] or ""),
            updated_utc=_datetime_from_text(str(row["updated_utc"])),
        )

    def train_model(self, mode: str) -> CandidateTrainingResult:
        examples = self._labeled_examples(_normalize_text(mode))
        if len(examples) < 2:
            return CandidateTrainingResult(False, "Label at least two candidates before training.", example_count=len(examples))
        labels = [label for _features, label in examples]
        class_counts = dict(sorted(Counter(labels).items()))
        if len(class_counts) < 2:
            return CandidateTrainingResult(
                False,
                "Training needs at least two different labels.",
                example_count=len(examples),
                class_counts=class_counts,
            )
        feature_names = sorted({name for features, _label in examples for name in features})
        x = np.array([_feature_vector(features, feature_names) for features, _label in examples], dtype=float)
        y = np.array(labels, dtype=object)
        metrics: dict[str, float] = {}
        message_suffix = ""
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.metrics import accuracy_score, precision_recall_fscore_support
        from sklearn.model_selection import train_test_split

        model = RandomForestClassifier(n_estimators=160, random_state=42, class_weight="balanced_subsample")
        can_validate = len(examples) >= 6 and min(class_counts.values()) >= 2
        if can_validate:
            x_train, x_validation, y_train, y_validation = train_test_split(
                x,
                y,
                test_size=0.25,
                random_state=42,
                stratify=y,
            )
            model.fit(x_train, y_train)
            predicted = model.predict(x_validation)
            precision, recall, f1, _support = precision_recall_fscore_support(
                y_validation,
                predicted,
                average="weighted",
                zero_division=0,
            )
            metrics = {
                "validation_accuracy": float(accuracy_score(y_validation, predicted)),
                "validation_precision": float(precision),
                "validation_recall": float(recall),
                "validation_f1": float(f1),
                "validation_count": float(len(y_validation)),
            }
            model.fit(x, y)
        else:
            model.fit(x, y)
            message_suffix = " Add more balanced labels to unlock held-out validation metrics."
        model_id = f"{_normalize_text(mode)}-{_utc_now().strftime('%Y%m%d%H%M%S')}-{hashlib.sha1(pickle.dumps(class_counts)).hexdigest()[:8]}"
        created_utc = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO model_runs(model_id, mode, model_type, feature_names_json, class_counts_json, metrics_json, model_blob, created_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    model_id,
                    _normalize_text(mode),
                    "RandomForestClassifier",
                    _json_dumps(feature_names),
                    _json_dumps(class_counts),
                    _json_dumps(metrics),
                    pickle.dumps(model),
                    _datetime_to_text(created_utc),
                ),
            )
        return CandidateTrainingResult(
            True,
            f"Trained Random Forest on {len(examples)} labeled candidates across {len(class_counts)} labels.{message_suffix}",
            model_id=model_id,
            example_count=len(examples),
            class_counts=class_counts,
            metrics=metrics,
        )

    def predict(self, mode: str, features: dict[str, float]) -> CandidatePrediction | None:
        row = self._latest_model_row(_normalize_text(mode))
        if row is None:
            return None
        feature_names = json.loads(str(row["feature_names_json"]))
        if not isinstance(feature_names, list):
            return None
        model = pickle.loads(row["model_blob"])
        vector = np.array([_feature_vector(_numeric_features(features), [str(name) for name in feature_names])], dtype=float)
        predicted_label = str(model.predict(vector)[0])
        probabilities: dict[str, float] = {}
        confidence = 1.0
        if hasattr(model, "predict_proba"):
            probability_values = model.predict_proba(vector)[0]
            classes = [str(item) for item in getattr(model, "classes_", [])]
            probabilities = {label: float(value) for label, value in zip(classes, probability_values)}
            confidence = float(probabilities.get(predicted_label, max(probabilities.values(), default=1.0)))
        return CandidatePrediction(
            model_id=str(row["model_id"]),
            label=predicted_label,
            confidence=confidence,
            probabilities=probabilities,
        )

    def training_summary(self, mode: str) -> str:
        normalized_mode = _normalize_text(mode)
        examples = self._labeled_examples(normalized_mode)
        counts = Counter(label for _features, label in examples)
        latest = self._latest_model_row(normalized_mode)
        if not examples:
            return "No labels saved yet."
        label_text = ", ".join(f"{label}: {count}" for label, count in sorted(counts.items()))
        if latest is None:
            return f"{len(examples)} labeled candidate(s): {label_text}. No model trained yet."
        return f"{len(examples)} labeled candidate(s): {label_text}. Latest model: {latest['model_id']}."

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS examples (
                    example_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    candidate_key TEXT NOT NULL,
                    features_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_utc TEXT NOT NULL,
                    updated_utc TEXT NOT NULL,
                    UNIQUE(mode, candidate_key)
                );

                CREATE TABLE IF NOT EXISTS labels (
                    example_id TEXT PRIMARY KEY REFERENCES examples(example_id) ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    updated_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS model_runs (
                    model_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    model_type TEXT NOT NULL,
                    feature_names_json TEXT NOT NULL,
                    class_counts_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    model_blob BLOB NOT NULL,
                    created_utc TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_examples_mode ON examples(mode);
                CREATE INDEX IF NOT EXISTS idx_model_runs_mode_created ON model_runs(mode, created_utc DESC);
                """
            )

    def _labeled_examples(self, mode: str) -> list[tuple[dict[str, float], str]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT examples.features_json, labels.label
                FROM examples
                JOIN labels ON labels.example_id = examples.example_id
                WHERE examples.mode = ?
                ORDER BY labels.updated_utc ASC
                """,
                (mode,),
            ).fetchall()
        examples: list[tuple[dict[str, float], str]] = []
        for row in rows:
            try:
                features = json.loads(str(row["features_json"]))
            except json.JSONDecodeError:
                continue
            if isinstance(features, dict):
                examples.append((_numeric_features(features), str(row["label"])))
        return examples

    def _latest_model_row(self, mode: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT model_id, feature_names_json, model_blob
                FROM model_runs
                WHERE mode = ?
                ORDER BY created_utc DESC, model_id DESC
                LIMIT 1
                """,
                (mode,),
            ).fetchone()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()


def _digest_key(parts: list[str]) -> str:
    payload = "\n".join(parts).encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()


def _example_id(mode: str, candidate_key: str) -> str:
    return _digest_key([mode, candidate_key])


def _normalize_text(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def _numeric_features(features: dict[str, Any]) -> dict[str, float]:
    numeric: dict[str, float] = {}
    for key, value in features.items():
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        if not math_is_finite(numeric_value):
            numeric_value = 0.0
        numeric[str(key)] = numeric_value
    return numeric


def _feature_vector(features: dict[str, float], feature_names: list[str]) -> list[float]:
    return [float(features.get(name, 0.0)) for name in feature_names]


def _parse_binning_value(value: object) -> float | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    for separator in ("x", "*"):
        if separator in text:
            text = text.split(separator, 1)[0].strip()
            break
    try:
        numeric_value = float(text)
    except (TypeError, ValueError):
        return None
    if not math_is_finite(numeric_value) or numeric_value <= 0:
        return None
    return numeric_value


def _safe_ratio(numerator: float, denominator: float) -> float:
    denominator = float(denominator)
    if denominator == 0.0:
        return 0.0
    return float(numerator) / denominator


def _nanmedian(values: np.ndarray) -> float:
    return float(np.nanmedian(values)) if values.size else 0.0


def _nanmin(values: np.ndarray) -> float:
    return float(np.nanmin(values)) if values.size else 0.0


def _nanmax(values: np.ndarray) -> float:
    return float(np.nanmax(values)) if values.size else 0.0


def _nanstd(values: np.ndarray) -> float:
    return float(np.nanstd(values)) if values.size else 0.0


def math_is_finite(value: float) -> bool:
    return bool(np.isfinite(value))


def _json_dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _datetime_to_text(value: datetime | None) -> str:
    if value is None:
        return ""
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.astimezone(UTC).isoformat()


def _datetime_from_text(value: str) -> datetime:
    if not value:
        return _utc_now()
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)