from __future__ import annotations



from concurrent.futures import ThreadPoolExecutor, as_completed

from collections.abc import Callable, Sequence

from dataclasses import dataclass, replace

from datetime import UTC, datetime, timedelta

from functools import lru_cache

import hashlib

from io import BytesIO

import json

import math

import os

import sqlite3

import re

from pathlib import Path

from photometry_app.platform_paths import application_cache_dir

from typing import Protocol

import warnings

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError



import numpy as np

from astropy import units as u

from astropy.coordinates import AltAz, Angle, EarthLocation, SkyCoord

from astropy.table import MaskedColumn, QTable, Row, Table

from astropy.time import Time

from astropy.utils.exceptions import AstropyUserWarning

from astropy.utils import iers

from astropy.wcs import WCS

from astropy.wcs.utils import proj_plane_pixel_scales

import requests



from photometry_app.core.catalogs import CatalogService

from photometry_app.core.image_io import read_header, read_header_and_shape, read_photometry_image_data

from photometry_app.core.local_wcs import solve_wcs_from_metadata_and_gaia

from photometry_app.core.models import CatalogStar, SolvedField

from photometry_app.core.settings import AppSettings

from photometry_app.core.wcs import AstrometryNetClient, extract_solved_field, infer_astrometry_solve_hints, validate_wcs



try:

    from astroquery.imcce import Miriade, Skybot, conf as skybot_conf

except ImportError:

    Miriade = None

    Skybot = None

    skybot_conf = None



try:

    from astroquery.jplhorizons import Horizons

except ImportError:

    Horizons = None


try:

    from astroquery.jplsbdb import SBDB

except ImportError:

    SBDB = None





_VISIBLE_LIMIT_STEP_MAG = 0.5

_VISIBLE_LIMIT_MAX_OFFSET_PX = 5.0

_VISIBLE_LIMIT_STOP_AFTER_UNSUCCESSFUL_BINS = 2

_SKYBOT_POSITION_ERROR_ARCSEC = 3600.0

_SKYBOT_MAX_HTTP_ATTEMPTS = 3

_SKYBOT_RETRYABLE_STATUS_CODES = frozenset({500, 502, 503, 504})

_TARGETED_INTERSTELLAR_FALLBACK_OBJECTS = (("3I/ATLAS", "3I"),)

_HORIZONS_VECTOR_QUERY_MAX_EPOCHS = 32

_SMALL_BODY_SEARCH_CANDIDATE_EXPANSION = 4

_SMALL_BODY_SEARCH_MAX_CANDIDATES = 64

_GLOBAL_BRIGHT_SKYBOT_TILE_RADIUS_DEG = 10.0

_GLOBAL_BRIGHT_SKYBOT_TILE_STEP_DEG = 15.0

_BRIGHT_OBJECT_PLANNER_DB_FILENAME = "bright_object_planner.sqlite3"

_MAJOR_PLANET_CACHE_FILENAME_PREFIX = "major_planet_vectors"

_MAJOR_PLANET_CACHE_TTL = timedelta(days=365 * 5)

_MAJOR_PLANET_CACHE_VERSION = 1

_BRIGHT_OBJECT_PLANNER_SNAPSHOT_TOLERANCE_HOURS = 18.0

_COMET_IDENTIFIER_PATTERN = re.compile(r"(^|[\s(])(?:\d+[cpd]|[cpdax]/\d{4}\s+[a-z]{1,3}\d+)(?:[\s)]|$)")

_INTERSTELLAR_IDENTIFIER_PATTERN = re.compile(r"(^|[\s(])\d+i(?:/|[\s)]|$)")


class _SmallBodyPredictionCacheMiss(RuntimeError):

    pass

_COMET_NAME_DESIGNATION_PATTERN = re.compile(r"([cpdax]/\d{4}\s+[a-z]{1,3}\d+(?:-[a-z])?)", re.IGNORECASE)

_UNPREFIXED_COMET_DESIGNATION_PATTERN = re.compile(r"^\d{4}\s+[a-z]{1,3}\d+(?:-[a-z])?$", re.IGNORECASE)

_SUN_GRAVITATIONAL_PARAMETER_AU3_PER_DAY2 = 0.00029591220828559115

_MAJOR_PLANET_TARGETS: tuple[tuple[str, str, str], ...] = (

    ("mercury", "Mercury", "199"),

    ("venus", "Venus", "299"),

    ("mars", "Mars", "499"),

    ("jupiter", "Jupiter", "599"),

    ("saturn", "Saturn", "699"),

    ("uranus", "Uranus", "799"),

    ("neptune", "Neptune", "899"),

)





@dataclass(slots=True)

class SolarSystemPrediction:

    name: str

    object_type: str

    orbit_class: str

    ra_deg: float

    dec_deg: float

    predicted_magnitude: float | None

    ra_rate_arcsec_per_hour: float | None

    dec_rate_arcsec_per_hour: float | None

    positional_uncertainty_arcsec: float | None = None

    designation: str | None = None





@dataclass(slots=True)

class SolarSystemDetection:

    name: str

    designation: str | None

    object_type: str

    orbit_class: str

    predicted_ra_deg: float

    predicted_dec_deg: float

    predicted_x: float

    predicted_y: float

    predicted_magnitude: float | None

    ra_rate_arcsec_per_hour: float | None

    dec_rate_arcsec_per_hour: float | None

    motion_rate_arcsec_per_hour: float | None

    expected_trail_length_px: float | None

    positional_uncertainty_arcsec: float | None

    altitude_deg: float | None

    likely_visible: bool

    confidence_score: float

    status: str

    measured_x: float | None = None

    measured_y: float | None = None

    match_offset_px: float | None = None

    local_snr: float | None = None

    local_peak_value: float | None = None

    local_flux: float | None = None

    local_fwhm_px: float | None = None





@dataclass(slots=True)

class SolarSystemFrameMeasurement:

    source_path: Path

    observation_time: datetime

    predicted_ra_deg: float

    predicted_dec_deg: float

    predicted_x: float

    predicted_y: float

    expected_trail_length_px: float | None

    measured_x: float | None = None

    measured_y: float | None = None

    measured_ra_deg: float | None = None

    measured_dec_deg: float | None = None

    match_offset_px: float | None = None

    local_snr: float | None = None

    local_peak_value: float | None = None

    local_flux: float | None = None

    local_fwhm_px: float | None = None

    likely_visible: bool = False


@dataclass(slots=True)
class SolarSystemSearchResult:

    detection: SolarSystemDetection

    angular_distance_deg: float | None

    is_in_image: bool





@dataclass(slots=True)

class SolarSystemStateVectorSample:

    observation_time: datetime

    x_au: float

    y_au: float

    z_au: float

    vx_au_per_day: float

    vy_au_per_day: float

    vz_au_per_day: float





@dataclass(slots=True)

class SolarSystemMagnitudeSample:

    observation_time: datetime

    literature_magnitude: float





@dataclass(slots=True)

class HeliocentricReferenceBody:

    key: str

    label: str

    resolved_target_name: str

    path_samples: tuple[SolarSystemStateVectorSample, ...]

    orbital_period_days: float | None = None





@dataclass(slots=True)

class KnownObjectComparisonTrack:

    object_label: str

    resolved_target_name: str

    path_samples: tuple[SolarSystemStateVectorSample, ...]

    observation_samples: tuple[SolarSystemStateVectorSample, ...]

    orbital_period_days: float | None = None

    magnitude_samples: tuple[SolarSystemMagnitudeSample, ...] = ()





@dataclass(slots=True)

class KnownObjectHeliocentricContext:

    object_label: str

    resolved_target_name: str

    earth_target_name: str

    window_start: datetime

    window_end: datetime

    arc_padding_days: float

    reference_time: datetime

    object_path_samples: tuple[SolarSystemStateVectorSample, ...]

    earth_path_samples: tuple[SolarSystemStateVectorSample, ...]

    observation_object_samples: tuple[SolarSystemStateVectorSample, ...]

    observation_earth_samples: tuple[SolarSystemStateVectorSample, ...]

    object_magnitude_samples: tuple[SolarSystemMagnitudeSample, ...] = ()

    object_orbital_period_days: float | None = None

    earth_orbital_period_days: float | None = None

    comparison_tracks: tuple[KnownObjectComparisonTrack, ...] = ()

    additional_bodies: tuple[HeliocentricReferenceBody, ...] = ()

    include_major_planets: bool = False





@dataclass(slots=True)

class SolarSystemDetectionResult:

    source_path: Path

    solved_field: SolvedField

    observation_time: datetime

    prediction_time: datetime

    exposure_seconds: float | None

    filter_name: str | None

    pixel_scale_arcsec_per_pixel: float | None

    field_width_deg: float | None

    field_height_deg: float | None

    magnitude_limit: float

    used_astrometry_fallback: bool

    detections: list[SolarSystemDetection]

    summary_text: str





@dataclass(slots=True)

class SolarSystemVisibilityEstimateResult:

    source_path: Path

    solved_field: SolvedField

    used_astrometry_fallback: bool

    tested_star_count: int

    visible_star_count: int

    dimmest_visible_magnitude: float

    summary_text: str

    annotated_stars: tuple["SolarSystemVisibilityEstimateStar", ...] = ()





@dataclass(slots=True)

class SolarSystemVisibilityEstimateStar:

    name: str

    magnitude: float

    predicted_x: float

    predicted_y: float

    measured_x: float | None = None

    measured_y: float | None = None

    local_snr: float | None = None

    match_offset_px: float | None = None





@dataclass(slots=True)

class _ProjectedCatalogStar:

    star: CatalogStar

    x: float

    y: float





@dataclass(slots=True)

class _VisibleLimitEstimateOptions:

    snr_threshold: float

    start_magnitude: float

    stars_per_bin: int

    required_visible_count: int

    annotate_lowest_mag_stars: bool





class SolarSystemQueryService(Protocol):

    def query_predictions(

        self,

        solved_field: SolvedField,

        observation_time: datetime,

        *,

        observatory_code: str | None = None,

        magnitude_limit: float | None = None,

    ) -> list[SolarSystemPrediction]: ...





class SkybotQueryService:

    def query_predictions(

        self,

        solved_field: SolvedField,

        observation_time: datetime,

        *,

        observatory_code: str | None = None,

        magnitude_limit: float | None = None,

    ) -> list[SolarSystemPrediction]:

        if Skybot is None or skybot_conf is None:

            raise RuntimeError("Solar-system prediction requires astroquery.imcce.Skybot.")



        field = SkyCoord(solved_field.center_ra_deg * u.deg, solved_field.center_dec_deg * u.deg)

        epoch = Time(observation_time)

        radius = min(max(float(solved_field.radius_deg), 0.01), 10.0) * u.deg

        table = _skybot_cone_search_table(

            field,

            radius,

            epoch,

            observatory_code=observatory_code,

            position_error_arcsec=_SKYBOT_POSITION_ERROR_ARCSEC,

            find_planets=False,

            find_asteroids=True,

            find_comets=True,

        )

        if table is None:

            predictions: list[SolarSystemPrediction] = []

        else:

            predictions = _predictions_from_skybot_table(table, magnitude_limit=magnitude_limit)

        return _supplement_interstellar_predictions(

            predictions,

            observation_time,

            observatory_code=observatory_code,

        )





def detect_known_solar_system_objects(

    source_path: Path,

    *,

    observation_time: datetime,

    settings: AppSettings,

    exposure_seconds: float | None,

    observer_latitude_deg: float | None,

    observer_longitude_deg: float | None,

    observer_elevation_m: float | None,

    filter_name: str | None = None,

    magnitude_limit: float = 18.0,

    observatory_code: str | None = None,

    alternate_observation_times: Sequence[datetime] | None = None,

    progress_callback: Callable[[str], None] | None = None,

    query_service: SolarSystemQueryService | None = None,

) -> SolarSystemDetectionResult:

    query_service = query_service or SkybotQueryService()

    solved_field, used_astrometry_fallback = _resolve_source_field(source_path, settings, progress_callback=progress_callback)

    if progress_callback is not None:

        progress_callback("Loading image data for solar-system inspection.")

    image_data = _collapsed_image_plane(read_photometry_image_data(source_path))

    wcs = _resolved_wcs_for_field(solved_field)

    pixel_scale_arcsec_per_pixel = _pixel_scale_arcsec_per_pixel(wcs)

    field_width_deg = None if solved_field.width <= 0 or pixel_scale_arcsec_per_pixel is None else (pixel_scale_arcsec_per_pixel * solved_field.width) / 3600.0

    field_height_deg = None if solved_field.height <= 0 or pixel_scale_arcsec_per_pixel is None else (pixel_scale_arcsec_per_pixel * solved_field.height) / 3600.0



    observer_location = _observer_location(observer_latitude_deg, observer_longitude_deg, observer_elevation_m)

    def _run_for_observation_time(candidate_observation_time: datetime) -> SolarSystemDetectionResult:
        midpoint_time = candidate_observation_time if exposure_seconds is None else candidate_observation_time + timedelta(seconds=max(0.0, float(exposure_seconds)) / 2.0)

        predictions = query_service.query_predictions(

            solved_field,

            midpoint_time,

            observatory_code=observatory_code,

            magnitude_limit=magnitude_limit,

        )

        detections: list[SolarSystemDetection] = []

        for prediction in predictions:

            pixel_x, pixel_y = wcs.world_to_pixel_values(prediction.ra_deg, prediction.dec_deg)

            if not np.isfinite(pixel_x) or not np.isfinite(pixel_y):

                continue

            if pixel_x < 0 or pixel_y < 0 or pixel_x >= solved_field.width or pixel_y >= solved_field.height:

                continue

            altitude_deg = _altitude_deg(prediction.ra_deg, prediction.dec_deg, midpoint_time, observer_location)

            if altitude_deg is not None and altitude_deg < 0.0:

                continue

            detection = _build_detection_from_prediction(

                prediction,

                image_data,

                solved_field,

                pixel_x=float(pixel_x),

                pixel_y=float(pixel_y),

                pixel_scale_arcsec_per_pixel=pixel_scale_arcsec_per_pixel,

                exposure_seconds=exposure_seconds,

                magnitude_limit=magnitude_limit,

                altitude_deg=altitude_deg,

            )

            detections.append(detection)

        detections.sort(key=lambda item: (-item.confidence_score, item.predicted_magnitude if item.predicted_magnitude is not None else 99.0, item.name.lower()))

        return SolarSystemDetectionResult(

            source_path=source_path,

            solved_field=solved_field,

            observation_time=candidate_observation_time,

            prediction_time=midpoint_time,

            exposure_seconds=exposure_seconds,

            filter_name=filter_name,

            pixel_scale_arcsec_per_pixel=pixel_scale_arcsec_per_pixel,

            field_width_deg=field_width_deg,

            field_height_deg=field_height_deg,

            magnitude_limit=float(magnitude_limit),

            used_astrometry_fallback=used_astrometry_fallback,

            detections=detections,

            summary_text=_detection_summary_text(detections),

        )

    candidate_times = _detection_observation_time_candidates(observation_time, alternate_observation_times)

    candidate_results: list[SolarSystemDetectionResult] = []

    for candidate_index, candidate_time in enumerate(candidate_times):

        if progress_callback is not None:

            if candidate_index == 0:

                progress_callback("Querying known asteroids and comets predicted in the field.")

            else:

                progress_callback("Checking an alternate observation timestamp against image evidence.")

        try:

            candidate_results.append(_run_for_observation_time(candidate_time))

        except Exception as exc:

            if candidate_index == 0:

                raise

            if progress_callback is not None:

                progress_callback(f"Skipping alternate observation timestamp: {exc}")

    return _select_best_detection_time_result(candidate_results, progress_callback=progress_callback)


def _detection_observation_time_candidates(
    observation_time: datetime,
    alternate_observation_times: Sequence[datetime] | None,
) -> tuple[datetime, ...]:
    candidates: list[datetime] = [_as_utc_datetime(observation_time)]
    if alternate_observation_times is not None:
        candidates.extend(_as_utc_datetime(candidate) for candidate in alternate_observation_times)

    ordered_times: list[datetime] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.isoformat(timespec="microseconds")
        if key in seen:
            continue
        seen.add(key)
        ordered_times.append(candidate)
    return tuple(ordered_times)


def _as_utc_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _select_best_detection_time_result(
    results: list[SolarSystemDetectionResult],
    *,
    progress_callback: Callable[[str], None] | None = None,
) -> SolarSystemDetectionResult:
    if not results:
        raise RuntimeError("Solar-system detection did not produce a result.")

    best_index, best_result = max(
        enumerate(results),
        key=lambda item: (_detection_result_quality_key(item[1]), -item[0]),
    )
    if best_index > 0:
        message = (
            "Selected an alternate observation timestamp because it produced stronger local matches "
            "for the predicted asteroids/comets."
        )
        best_result.summary_text = f"{best_result.summary_text} {message}"
        if progress_callback is not None:
            progress_callback(message)
    return best_result


def _detection_result_quality_key(result: SolarSystemDetectionResult) -> tuple[int, int, float, float, float]:
    likely_visible_count = sum(1 for detection in result.detections if detection.likely_visible)
    close_match_count = sum(
        1
        for detection in result.detections
        if detection.match_offset_px is not None
        and detection.match_offset_px <= 8.0
        and detection.local_snr is not None
        and detection.local_snr >= 3.0
    )
    local_snr_score = sum(max(0.0, min(float(detection.local_snr or 0.0), 30.0)) for detection in result.detections)
    median_offset = _median_match_offset(result.detections)
    confidence_score = sum(float(detection.confidence_score) for detection in result.detections)
    return (
        likely_visible_count,
        close_match_count,
        local_snr_score,
        -median_offset,
        confidence_score,
    )


def _median_match_offset(detections: Sequence[SolarSystemDetection]) -> float:
    offsets = sorted(float(detection.match_offset_px) for detection in detections if detection.match_offset_px is not None)
    if not offsets:
        return 1_000_000.0
    midpoint = len(offsets) // 2
    if len(offsets) % 2:
        return offsets[midpoint]
    return (offsets[midpoint - 1] + offsets[midpoint]) / 2.0


def search_nearby_known_solar_system_objects(

    solved_field: SolvedField,

    *,

    observation_time: datetime,

    exposure_seconds: float | None,

    observer_latitude_deg: float | None,

    observer_longitude_deg: float | None,

    observer_elevation_m: float | None,

    search_radius_deg: float,

    magnitude_limit: float = 18.0,

    observatory_code: str | None = None,

    query_service: SolarSystemQueryService | None = None,

) -> list[SolarSystemSearchResult]:

    query_service = query_service or SkybotQueryService()

    expanded_field = replace(solved_field, radius_deg=min(max(float(search_radius_deg), 0.01), 10.0))
    wcs = _resolved_wcs_for_field(solved_field)
    pixel_scale_arcsec_per_pixel = _pixel_scale_arcsec_per_pixel(wcs)
    midpoint_time = observation_time if exposure_seconds is None else observation_time + timedelta(seconds=max(0.0, float(exposure_seconds)) / 2.0)
    predictions = query_service.query_predictions(
        expanded_field,
        midpoint_time,
        observatory_code=observatory_code,
        magnitude_limit=magnitude_limit,
    )
    observer_location = _observer_location(observer_latitude_deg, observer_longitude_deg, observer_elevation_m)
    field_center = SkyCoord(solved_field.center_ra_deg * u.deg, solved_field.center_dec_deg * u.deg)
    search_results: list[SolarSystemSearchResult] = []
    for prediction in predictions:
        pixel_x, pixel_y = wcs.world_to_pixel_values(prediction.ra_deg, prediction.dec_deg)
        if not np.isfinite(pixel_x) or not np.isfinite(pixel_y):
            continue
        altitude_deg = _altitude_deg(prediction.ra_deg, prediction.dec_deg, midpoint_time, observer_location)
        if altitude_deg is not None and altitude_deg < 0.0:
            continue
        is_in_image = bool(
            0.0 <= float(pixel_x) < float(solved_field.width)
            and 0.0 <= float(pixel_y) < float(solved_field.height)
        )
        search_results.append(
            SolarSystemSearchResult(
                detection=_build_unmeasured_detection_from_prediction(
                    prediction,
                    pixel_x=float(pixel_x),
                    pixel_y=float(pixel_y),
                    pixel_scale_arcsec_per_pixel=pixel_scale_arcsec_per_pixel,
                    exposure_seconds=exposure_seconds,
                    magnitude_limit=magnitude_limit,
                    altitude_deg=altitude_deg,
                    status_override="Predicted in field" if is_in_image else "Outside image",
                ),
                angular_distance_deg=float(
                    field_center.separation(SkyCoord(prediction.ra_deg * u.deg, prediction.dec_deg * u.deg)).deg
                ),
                is_in_image=is_in_image,
            )
        )
    search_results.sort(
        key=lambda item: (
            item.is_in_image,
            item.detection.predicted_magnitude if item.detection.predicted_magnitude is not None else 99.0,
            item.angular_distance_deg,
            item.detection.name.lower(),
        )
    )
    return search_results


def lookup_known_solar_system_object(

    identifier: str,

    solved_field: SolvedField,

    *,

    observation_time: datetime,

    exposure_seconds: float | None,

    observer_latitude_deg: float | None,

    observer_longitude_deg: float | None,

    observer_elevation_m: float | None,

    magnitude_limit: float = 18.0,

    observatory_code: str | None = None,

) -> SolarSystemSearchResult | None:

    normalized_identifier = (identifier or "").strip()
    if not normalized_identifier:
        return None
    midpoint_time = observation_time if exposure_seconds is None else observation_time + timedelta(seconds=max(0.0, float(exposure_seconds)) / 2.0)
    prediction = _query_known_small_body_prediction(normalized_identifier, midpoint_time, observatory_code=observatory_code)
    if prediction is None:
        return None
    return _build_search_result_from_prediction(
        prediction,
        solved_field,
        midpoint_time=midpoint_time,
        exposure_seconds=exposure_seconds,
        observer_latitude_deg=observer_latitude_deg,
        observer_longitude_deg=observer_longitude_deg,
        observer_elevation_m=observer_elevation_m,
        magnitude_limit=magnitude_limit,
    )


def search_known_solar_system_objects(

    identifier: str,

    solved_field: SolvedField,

    *,

    observation_time: datetime,

    exposure_seconds: float | None,

    observer_latitude_deg: float | None,

    observer_longitude_deg: float | None,

    observer_elevation_m: float | None,

    magnitude_limit: float = 18.0,

    observatory_code: str | None = None,

    max_results: int = 25,

) -> list[SolarSystemSearchResult]:

    normalized_identifier = (identifier or "").strip()
    if not normalized_identifier:
        return []
    midpoint_time = observation_time if exposure_seconds is None else observation_time + timedelta(seconds=max(0.0, float(exposure_seconds)) / 2.0)
    search_results: list[SolarSystemSearchResult] = []
    seen_keys: set[tuple[str, str]] = set()
    resolved_predictions = _query_known_small_body_predictions(
        _search_known_small_body_candidates(
            normalized_identifier,
            max_results=_small_body_search_candidate_budget(max_results),
        ),
        midpoint_time,
        observatory_code=observatory_code,
    )
    for _candidate_identifier, prediction in resolved_predictions:
        if prediction is None:
            continue
        result = _build_search_result_from_prediction(
            prediction,
            solved_field,
            midpoint_time=midpoint_time,
            exposure_seconds=exposure_seconds,
            observer_latitude_deg=observer_latitude_deg,
            observer_longitude_deg=observer_longitude_deg,
            observer_elevation_m=observer_elevation_m,
            magnitude_limit=magnitude_limit,
        )
        if result is None:
            continue
        detection = result.detection
        detection_key = (
            (detection.designation or "").strip().lower(),
            (detection.name or "").strip().lower(),
        )
        if detection_key in seen_keys:
            continue
        seen_keys.add(detection_key)
        search_results.append(result)
    search_results.sort(
        key=lambda item: (
            item.is_in_image,
            item.detection.predicted_magnitude if item.detection.predicted_magnitude is not None else 99.0,
            item.angular_distance_deg if item.angular_distance_deg is not None else 999.0,
            item.detection.name.lower(),
        )
    )
    return search_results[: max(1, int(max_results))]


def search_known_solar_system_objects_globally(

    identifier: str,

    *,

    observation_time: datetime,

    observer_latitude_deg: float | None,

    observer_longitude_deg: float | None,

    observer_elevation_m: float | None,

    magnitude_limit: float = 18.0,

    observatory_code: str | None = None,

    max_results: int = 100,

    max_parallel_workers: int = 0,

    include_asteroids: bool = True,

    include_comets: bool = True,

    result_callback: Callable[[SolarSystemSearchResult], None] | None = None,

    progress_callback: Callable[[str], None] | None = None,

) -> list[SolarSystemSearchResult]:

    normalized_identifier = (identifier or "").strip()
    if not normalized_identifier:
        return []
    search_results: list[SolarSystemSearchResult] = []
    seen_keys: set[tuple[str, str]] = set()
    observer_location = _observer_location(observer_latitude_deg, observer_longitude_deg, observer_elevation_m)
    if progress_callback is not None:
        progress_callback(f"Resolving planning matches for {normalized_identifier}...")

    def _accept_prediction(_candidate_identifier: str, prediction: SolarSystemPrediction | None) -> None:
        if prediction is None or not _prediction_matches_requested_types(prediction, include_asteroids=include_asteroids, include_comets=include_comets):
            return
        altitude_deg = _altitude_deg(prediction.ra_deg, prediction.dec_deg, observation_time, observer_location)
        detection = _build_unmeasured_detection_from_prediction(
            prediction,
            pixel_x=float("nan"),
            pixel_y=float("nan"),
            pixel_scale_arcsec_per_pixel=None,
            exposure_seconds=None,
            magnitude_limit=magnitude_limit,
            altitude_deg=altitude_deg,
            status_override="Planning candidate",
        )
        detection_key = (
            (detection.designation or "").strip().lower(),
            (detection.name or "").strip().lower(),
        )
        if detection_key in seen_keys:
            return
        seen_keys.add(detection_key)
        search_result = SolarSystemSearchResult(
            detection=detection,
            angular_distance_deg=None,
            is_in_image=False,
        )
        search_results.append(search_result)
        if result_callback is not None:
            result_callback(search_result)

    _query_known_small_body_predictions(
        _search_known_small_body_candidates(
            normalized_identifier,
            max_results=_small_body_search_candidate_budget(max_results),
        ),
        observation_time,
        observatory_code=observatory_code,
        max_parallel_workers=max_parallel_workers,
        result_callback=_accept_prediction,
        progress_callback=progress_callback,
    )
    search_results.sort(
        key=lambda item: (
            item.detection.predicted_magnitude if item.detection.predicted_magnitude is not None else 99.0,
            item.detection.name.lower(),
        )
    )
    return search_results[: max(1, int(max_results))]


def search_bright_solar_system_objects_globally(

    *,

    observation_time: datetime,

    observer_latitude_deg: float | None,

    observer_longitude_deg: float | None,

    observer_elevation_m: float | None,

    magnitude_limit: float = 18.0,

    observatory_code: str | None = None,

    include_asteroids: bool = True,

    include_comets: bool = True,

    max_results: int = 100,

    max_parallel_workers: int = 0,

    cache_dir: Path | None = None,

    use_local_database: bool = True,

    result_callback: Callable[[SolarSystemSearchResult], None] | None = None,

    progress_callback: Callable[[str], None] | None = None,

) -> list[SolarSystemSearchResult]:

    if not include_asteroids and not include_comets:
        return []
    if Skybot is None or skybot_conf is None:
        raise RuntimeError("Bright-object planning search requires astroquery.imcce.Skybot.")

    observer_location = _observer_location(observer_latitude_deg, observer_longitude_deg, observer_elevation_m)
    if use_local_database and cache_dir is not None:
        cached_results = _load_bright_object_planner_snapshot(
            cache_dir=cache_dir,
            observation_time=observation_time,
            observer_location=observer_location,
            magnitude_limit=magnitude_limit,
            observatory_code=observatory_code,
            include_asteroids=include_asteroids,
            include_comets=include_comets,
            max_results=max_results,
        )
        if cached_results is not None:
            if progress_callback is not None:
                progress_callback(
                    f"Loaded {len(cached_results)} bright planning candidate(s) from the local database for {observation_time.astimezone(UTC):%Y-%m-%d %H:%M UTC}."
                )
            if result_callback is not None:
                for search_result in cached_results:
                    result_callback(search_result)
            return cached_results
        if progress_callback is not None:
            progress_callback("No local bright-object planning snapshot covered this UTC. Falling back to the live sky scan.")

    tile_centers = _global_bright_skybot_tile_centers()
    search_results: list[SolarSystemSearchResult] = []
    seen_keys: set[tuple[str, str]] = set()

    if progress_callback is not None:
        progress_callback("Scanning the sky for bright asteroid/comet planning candidates...")

    def _accept_prediction(prediction: SolarSystemPrediction) -> None:
        if (
            not _prediction_matches_requested_types(prediction, include_asteroids=include_asteroids, include_comets=include_comets)
            or prediction.predicted_magnitude is None
            or prediction.predicted_magnitude > magnitude_limit
        ):
            return
        altitude_deg = _altitude_deg(prediction.ra_deg, prediction.dec_deg, observation_time, observer_location)
        detection = _build_unmeasured_detection_from_prediction(
            prediction,
            pixel_x=float("nan"),
            pixel_y=float("nan"),
            pixel_scale_arcsec_per_pixel=None,
            exposure_seconds=None,
            magnitude_limit=magnitude_limit,
            altitude_deg=altitude_deg,
            status_override="Planning candidate",
        )
        detection_key = (
            (detection.designation or "").strip().lower(),
            (detection.name or "").strip().lower(),
        )
        if detection_key in seen_keys:
            return
        seen_keys.add(detection_key)
        search_result = SolarSystemSearchResult(
            detection=detection,
            angular_distance_deg=None,
            is_in_image=False,
        )
        search_results.append(search_result)
        if result_callback is not None:
            result_callback(search_result)

    max_workers = _resolve_small_body_parallel_workers(len(tile_centers), max_parallel_workers)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                _query_global_bright_skybot_tile_predictions,
                ra_deg,
                dec_deg,
                observation_time,
                observatory_code=observatory_code,
                include_asteroids=include_asteroids,
                include_comets=include_comets,
            ): (ra_deg, dec_deg)
            for ra_deg, dec_deg in tile_centers
        }
        for completed_count, future in enumerate(as_completed(future_map), start=1):
            if progress_callback is not None:
                progress_callback(f"Scanning sky tiles for bright planning candidates... {completed_count}/{len(tile_centers)}")
            try:
                predictions = future.result()
            except Exception:
                continue
            for prediction in predictions:
                _accept_prediction(prediction)

    search_results.sort(
        key=lambda item: (
            item.detection.predicted_magnitude if item.detection.predicted_magnitude is not None else 99.0,
            item.detection.name.lower(),
        )
    )
    limited_results = search_results[: max(1, int(max_results))]
    if cache_dir is not None:
        _store_bright_object_planner_snapshot(
            cache_dir=cache_dir,
            observation_time=observation_time,
            observatory_code=observatory_code,
            magnitude_limit=magnitude_limit,
            max_results=max_results,
            search_results=limited_results,
        )
    return limited_results


def _small_body_search_candidate_budget(max_results: int) -> int:

    requested_results = max(1, int(max_results))
    return min(_SMALL_BODY_SEARCH_MAX_CANDIDATES, max(requested_results, requested_results * _SMALL_BODY_SEARCH_CANDIDATE_EXPANSION))


def _query_known_small_body_predictions(

    identifiers: tuple[str, ...],

    observation_time: datetime,

    *,

    observatory_code: str | None,

    max_parallel_workers: int = 0,

    result_callback: Callable[[str, SolarSystemPrediction | None], None] | None = None,

    progress_callback: Callable[[str], None] | None = None,

) -> tuple[tuple[str, SolarSystemPrediction | None], ...]:

    ordered_identifiers = tuple(dict.fromkeys((identifier or "").strip() for identifier in identifiers if (identifier or "").strip()))
    if not ordered_identifiers:
        return ()
    if len(ordered_identifiers) == 1:
        identifier = ordered_identifiers[0]
        prediction = _query_known_small_body_prediction(identifier, observation_time, observatory_code=observatory_code)
        if result_callback is not None:
            result_callback(identifier, prediction)
        if progress_callback is not None:
            progress_callback("Resolved 1/1 planning candidates...")
        return ((identifier, prediction),)

    resolved_predictions: dict[str, SolarSystemPrediction | None] = {}
    max_workers = _resolve_small_body_parallel_workers(len(ordered_identifiers), max_parallel_workers)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                _query_known_small_body_prediction,
                identifier,
                observation_time,
                observatory_code=observatory_code,
            ): identifier
            for identifier in ordered_identifiers
        }
        for completed_count, future in enumerate(as_completed(future_map), start=1):
            identifier = future_map[future]
            try:
                resolved_predictions[identifier] = future.result()
            except Exception:
                resolved_predictions[identifier] = None
            if result_callback is not None:
                result_callback(identifier, resolved_predictions[identifier])
            if progress_callback is not None:
                progress_callback(f"Resolved {completed_count}/{len(ordered_identifiers)} planning candidates...")
    return tuple((identifier, resolved_predictions.get(identifier)) for identifier in ordered_identifiers)


def _resolve_small_body_parallel_workers(total_count: int, configured_workers: int) -> int:

    if total_count <= 0:
        return 1
    cpu_count = os.cpu_count() or 1
    if configured_workers > 0:
        return max(1, min(total_count, cpu_count, configured_workers))
    return max(1, min(total_count, max(1, cpu_count - 1)))


def _bright_object_planner_database_path(cache_dir: Path) -> Path:

    return Path(cache_dir).expanduser() / "solar_system" / _BRIGHT_OBJECT_PLANNER_DB_FILENAME


def _normalized_observatory_code(observatory_code: str | None) -> str:

    return (observatory_code or "").strip() or "500"


def _ensure_bright_object_planner_database(connection: sqlite3.Connection) -> None:

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS bright_object_planner_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            observation_unix_seconds REAL NOT NULL,
            observation_time_utc TEXT NOT NULL,
            observatory_code TEXT NOT NULL,
            stored_magnitude_limit REAL NOT NULL,
            stored_max_results INTEGER NOT NULL,
            created_time_utc TEXT NOT NULL,
            UNIQUE (observation_time_utc, observatory_code, stored_magnitude_limit, stored_max_results)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS bright_object_planner_entries (
            snapshot_id INTEGER NOT NULL,
            rank_index INTEGER NOT NULL,
            name TEXT NOT NULL,
            designation TEXT,
            object_type TEXT NOT NULL,
            orbit_class TEXT NOT NULL,
            ra_deg REAL NOT NULL,
            dec_deg REAL NOT NULL,
            predicted_magnitude REAL,
            ra_rate_arcsec_per_hour REAL,
            dec_rate_arcsec_per_hour REAL,
            positional_uncertainty_arcsec REAL,
            PRIMARY KEY (snapshot_id, rank_index),
            FOREIGN KEY (snapshot_id) REFERENCES bright_object_planner_snapshots(snapshot_id) ON DELETE CASCADE
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_bright_object_planner_snapshots_lookup
        ON bright_object_planner_snapshots (observatory_code, observation_unix_seconds)
        """
    )


def _store_bright_object_planner_snapshot(

    *,

    cache_dir: Path,

    observation_time: datetime,

    observatory_code: str | None,

    magnitude_limit: float,

    max_results: int,

    search_results: list[SolarSystemSearchResult],

) -> None:

    database_path = _bright_object_planner_database_path(cache_dir)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_time = observation_time.astimezone(UTC)
    normalized_code = _normalized_observatory_code(observatory_code)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(database_path)
        _ensure_bright_object_planner_database(connection)
        connection.execute(
            """
            INSERT INTO bright_object_planner_snapshots (
                observation_unix_seconds,
                observation_time_utc,
                observatory_code,
                stored_magnitude_limit,
                stored_max_results,
                created_time_utc
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (observation_time_utc, observatory_code, stored_magnitude_limit, stored_max_results)
            DO UPDATE SET created_time_utc = excluded.created_time_utc
            """,
            (
                float(normalized_time.timestamp()),
                normalized_time.isoformat(),
                normalized_code,
                float(magnitude_limit),
                max(1, int(max_results)),
                datetime.now(UTC).isoformat(),
            ),
        )
        snapshot_row = connection.execute(
            """
            SELECT snapshot_id
            FROM bright_object_planner_snapshots
            WHERE observation_time_utc = ? AND observatory_code = ? AND stored_magnitude_limit = ? AND stored_max_results = ?
            """,
            (
                normalized_time.isoformat(),
                normalized_code,
                float(magnitude_limit),
                max(1, int(max_results)),
            ),
        ).fetchone()
        if snapshot_row is None:
            return
        snapshot_id = int(snapshot_row[0])
        connection.execute("DELETE FROM bright_object_planner_entries WHERE snapshot_id = ?", (snapshot_id,))
        connection.executemany(
            """
            INSERT INTO bright_object_planner_entries (
                snapshot_id,
                rank_index,
                name,
                designation,
                object_type,
                orbit_class,
                ra_deg,
                dec_deg,
                predicted_magnitude,
                ra_rate_arcsec_per_hour,
                dec_rate_arcsec_per_hour,
                positional_uncertainty_arcsec
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    snapshot_id,
                    rank_index,
                    result.detection.name,
                    result.detection.designation,
                    result.detection.object_type,
                    result.detection.orbit_class,
                    float(result.detection.predicted_ra_deg),
                    float(result.detection.predicted_dec_deg),
                    None if result.detection.predicted_magnitude is None else float(result.detection.predicted_magnitude),
                    None if result.detection.ra_rate_arcsec_per_hour is None else float(result.detection.ra_rate_arcsec_per_hour),
                    None if result.detection.dec_rate_arcsec_per_hour is None else float(result.detection.dec_rate_arcsec_per_hour),
                    None if result.detection.positional_uncertainty_arcsec is None else float(result.detection.positional_uncertainty_arcsec),
                )
                for rank_index, result in enumerate(search_results)
            ],
        )
        connection.commit()
    except (OSError, sqlite3.Error):
        return
    finally:
        if connection is not None:
            connection.close()


def _load_bright_object_planner_snapshot(

    *,

    cache_dir: Path,

    observation_time: datetime,

    observer_location: EarthLocation | None,

    magnitude_limit: float,

    observatory_code: str | None,

    include_asteroids: bool,

    include_comets: bool,

    max_results: int,

) -> list[SolarSystemSearchResult] | None:

    database_path = _bright_object_planner_database_path(cache_dir)
    if not database_path.exists():
        return None
    normalized_code = _normalized_observatory_code(observatory_code)
    normalized_time = observation_time.astimezone(UTC)
    tolerance_seconds = max(0.0, float(_BRIGHT_OBJECT_PLANNER_SNAPSHOT_TOLERANCE_HOURS)) * 3600.0
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(database_path)
        _ensure_bright_object_planner_database(connection)
        snapshot_row = connection.execute(
            """
            SELECT snapshot_id, observation_time_utc
            FROM bright_object_planner_snapshots
            WHERE observatory_code = ?
              AND stored_magnitude_limit >= ?
              AND stored_max_results >= ?
              AND ABS(observation_unix_seconds - ?) <= ?
            ORDER BY ABS(observation_unix_seconds - ?), stored_magnitude_limit ASC, stored_max_results ASC
            LIMIT 1
            """,
            (
                normalized_code,
                float(magnitude_limit),
                max(1, int(max_results)),
                float(normalized_time.timestamp()),
                tolerance_seconds,
                float(normalized_time.timestamp()),
            ),
        ).fetchone()
        if snapshot_row is None:
            return None
        entry_rows = connection.execute(
            """
            SELECT
                name,
                designation,
                object_type,
                orbit_class,
                ra_deg,
                dec_deg,
                predicted_magnitude,
                ra_rate_arcsec_per_hour,
                dec_rate_arcsec_per_hour,
                positional_uncertainty_arcsec
            FROM bright_object_planner_entries
            WHERE snapshot_id = ?
            ORDER BY rank_index ASC
            LIMIT ?
            """,
            (int(snapshot_row[0]), max(1, int(max_results))),
        ).fetchall()
    except (OSError, sqlite3.Error):
        return None
    finally:
        if connection is not None:
            connection.close()

    results: list[SolarSystemSearchResult] = []
    for row in entry_rows:
        prediction = SolarSystemPrediction(
            name=str(row[0]),
            designation=None if row[1] is None else str(row[1]),
            object_type=str(row[2]),
            orbit_class=str(row[3]),
            ra_deg=float(row[4]),
            dec_deg=float(row[5]),
            predicted_magnitude=None if row[6] is None else float(row[6]),
            ra_rate_arcsec_per_hour=None if row[7] is None else float(row[7]),
            dec_rate_arcsec_per_hour=None if row[8] is None else float(row[8]),
            positional_uncertainty_arcsec=None if row[9] is None else float(row[9]),
        )
        if not _prediction_matches_requested_types(prediction, include_asteroids=include_asteroids, include_comets=include_comets):
            continue
        altitude_deg = _altitude_deg(prediction.ra_deg, prediction.dec_deg, observation_time, observer_location)
        results.append(
            SolarSystemSearchResult(
                detection=_build_unmeasured_detection_from_prediction(
                    prediction,
                    pixel_x=float("nan"),
                    pixel_y=float("nan"),
                    pixel_scale_arcsec_per_pixel=None,
                    exposure_seconds=None,
                    magnitude_limit=magnitude_limit,
                    altitude_deg=altitude_deg,
                    status_override="Planning candidate",
                ),
                angular_distance_deg=None,
                is_in_image=False,
            )
        )
    return results[: max(1, int(max_results))] if results else []


def _prediction_matches_requested_types(

    prediction: SolarSystemPrediction,

    *,

    include_asteroids: bool,

    include_comets: bool,

) -> bool:

    object_type = str(prediction.object_type or "").strip().lower()
    if "comet" in object_type:
        return include_comets
    if "asteroid" in object_type:
        return include_asteroids
    return include_asteroids or include_comets


def _global_bright_skybot_tile_centers() -> tuple[tuple[float, float], ...]:

    centers: list[tuple[float, float]] = [(0.0, -90.0), (0.0, 90.0)]
    seen: set[tuple[float, float]] = {(0.0, -90.0), (0.0, 90.0)}
    declination_values = np.arange(-75.0, 90.0, _GLOBAL_BRIGHT_SKYBOT_TILE_STEP_DEG, dtype=float)
    for band_index, declination_deg in enumerate(declination_values):
        cos_declination = max(math.cos(math.radians(float(declination_deg))), 0.25)
        ra_step_deg = min(360.0, _GLOBAL_BRIGHT_SKYBOT_TILE_STEP_DEG / cos_declination)
        ra_offset_deg = 0.0 if band_index % 2 == 0 else ra_step_deg * 0.5
        center_count = max(1, int(math.ceil(360.0 / ra_step_deg)))
        for center_index in range(center_count):
            ra_deg = float((ra_offset_deg + (center_index * ra_step_deg)) % 360.0)
            center = (round(ra_deg, 6), round(float(declination_deg), 6))
            if center in seen:
                continue
            seen.add(center)
            centers.append(center)
    return tuple(centers)


def _query_global_bright_skybot_tile_predictions(

    ra_deg: float,

    dec_deg: float,

    observation_time: datetime,

    *,

    observatory_code: str | None,

    include_asteroids: bool,

    include_comets: bool,

) -> tuple[SolarSystemPrediction, ...]:

    field = SkyCoord(float(ra_deg) * u.deg, float(dec_deg) * u.deg)
    epoch = Time(observation_time)
    table = _skybot_cone_search_table(
        field,
        _GLOBAL_BRIGHT_SKYBOT_TILE_RADIUS_DEG * u.deg,
        epoch,
        observatory_code=observatory_code,
        position_error_arcsec=_SKYBOT_POSITION_ERROR_ARCSEC,
        find_planets=False,
        find_asteroids=include_asteroids,
        find_comets=include_comets,
    )
    if table is None:
        return ()
    return tuple(_predictions_from_skybot_table(table, magnitude_limit=None))


def _build_search_result_from_prediction(

    prediction: SolarSystemPrediction,

    solved_field: SolvedField,

    *,

    midpoint_time: datetime,

    exposure_seconds: float | None,

    observer_latitude_deg: float | None,

    observer_longitude_deg: float | None,

    observer_elevation_m: float | None,

    magnitude_limit: float,

) -> SolarSystemSearchResult | None:

    wcs = _resolved_wcs_for_field(solved_field)
    pixel_x, pixel_y = wcs.world_to_pixel_values(prediction.ra_deg, prediction.dec_deg)
    is_projectable = bool(np.isfinite(pixel_x) and np.isfinite(pixel_y))
    if not is_projectable:
        pixel_x = float("nan")
        pixel_y = float("nan")
    pixel_scale_arcsec_per_pixel = _pixel_scale_arcsec_per_pixel(wcs)
    observer_location = _observer_location(observer_latitude_deg, observer_longitude_deg, observer_elevation_m)
    altitude_deg = _altitude_deg(prediction.ra_deg, prediction.dec_deg, midpoint_time, observer_location)
    is_in_image = bool(
        is_projectable
        and 0.0 <= float(pixel_x) < float(solved_field.width)
        and 0.0 <= float(pixel_y) < float(solved_field.height)
    )
    field_center = SkyCoord(solved_field.center_ra_deg * u.deg, solved_field.center_dec_deg * u.deg)
    return SolarSystemSearchResult(
        detection=_build_unmeasured_detection_from_prediction(
            prediction,
            pixel_x=float(pixel_x),
            pixel_y=float(pixel_y),
            pixel_scale_arcsec_per_pixel=pixel_scale_arcsec_per_pixel,
            exposure_seconds=exposure_seconds,
            magnitude_limit=magnitude_limit,
            altitude_deg=altitude_deg,
            status_override="Predicted in field" if is_in_image else ("Outside image" if is_projectable else "Outside field"),
        ),
        angular_distance_deg=float(
            field_center.separation(SkyCoord(prediction.ra_deg * u.deg, prediction.dec_deg * u.deg)).deg
        ),
        is_in_image=is_in_image,
    )





def estimate_visible_magnitude_limit(

    source_path: Path,

    *,

    settings: AppSettings,

    progress_callback: Callable[[str], None] | None = None,

    catalog_service: CatalogService | None = None,

) -> SolarSystemVisibilityEstimateResult:

    options = _visible_limit_estimate_options(settings)

    solved_field, used_astrometry_fallback = _resolve_source_field(source_path, settings, progress_callback=progress_callback)

    if progress_callback is not None:

        progress_callback("Loading image data for visible-limit estimation.")

    image_data = _collapsed_image_plane(read_photometry_image_data(source_path))

    if image_data.ndim != 2 or image_data.size == 0:

        raise ValueError("The selected source image does not contain a measurable luminance plane for visible-limit estimation.")



    catalog_service = catalog_service or CatalogService(settings.cache_dir / "catalogs")

    gaia_stars = [

        star

        for star in catalog_service.query_gaia_stars(solved_field, progress_callback=progress_callback)

        if star.magnitude is not None and np.isfinite(float(star.magnitude))

    ]

    if not gaia_stars:

        raise ValueError("Gaia did not return any usable field stars with magnitudes for this image.")



    wcs = _resolved_wcs_for_field(solved_field)

    projected_stars = _project_catalog_stars_into_image(gaia_stars, wcs, solved_field)

    if progress_callback is not None:

        progress_callback(

            f"Projected {len(projected_stars)} Gaia star(s) into the image and sampling them in {_VISIBLE_LIMIT_STEP_MAG:.1f} mag steps starting at {options.start_magnitude:.1f} mag."

        )

    if not projected_stars:

        raise ValueError("No Gaia stars with usable magnitudes project inside the current image footprint.")



    if progress_callback is not None:

        progress_callback(

            f"Estimating visible limit by checking up to {options.stars_per_bin} star(s) per bin and requiring {options.required_visible_count} visible star(s) at SNR >= {options.snr_threshold:.1f}."

        )



    bin_results = _probe_visible_limit_bins(image_data, projected_stars, options=options, progress_callback=progress_callback)

    tested_star_count = sum(result.tested_count for result in bin_results)

    successful_bins = [result for result in bin_results if result.visible_count >= options.required_visible_count]

    if successful_bins:

        chosen_result = successful_bins[-1]

        summary_text = (

            f"Estimated visible limit: Gaia G {chosen_result.dimmest_visible_magnitude:.1f} from "

            f"{chosen_result.visible_count}/{chosen_result.tested_count} visible star(s) in the "

            f"{chosen_result.bin_start_magnitude:.1f}-{chosen_result.bin_start_magnitude + _VISIBLE_LIMIT_STEP_MAG:.1f} mag bin "

            f"(median SNR {chosen_result.median_visible_snr:.1f})."

        )

    else:

        visible_results = [result for result in bin_results if result.visible_count > 0]

        if not visible_results:

            raise ValueError("No sampled Gaia stars were visible strongly enough to estimate a magnitude limit.")

        chosen_result = visible_results[-1]

        summary_text = (

            f"Approximate visible limit: Gaia G {chosen_result.dimmest_visible_magnitude:.1f} from a fallback sample of "

            f"{chosen_result.visible_count}/{chosen_result.tested_count} visible star(s) in the "

            f"{chosen_result.bin_start_magnitude:.1f}-{chosen_result.bin_start_magnitude + _VISIBLE_LIMIT_STEP_MAG:.1f} mag bin."

        )

    annotated_stars: tuple[SolarSystemVisibilityEstimateStar, ...] = ()

    if options.annotate_lowest_mag_stars:

        annotated_stars = tuple(

            sorted(chosen_result.visible_stars, key=lambda item: item.magnitude, reverse=True)[: options.required_visible_count]

        )

    return SolarSystemVisibilityEstimateResult(

        source_path=source_path,

        solved_field=solved_field,

        used_astrometry_fallback=used_astrometry_fallback,

        tested_star_count=tested_star_count,

        visible_star_count=chosen_result.visible_count,

        dimmest_visible_magnitude=float(chosen_result.dimmest_visible_magnitude),

        summary_text=summary_text,

        annotated_stars=annotated_stars,

    )





def _resolve_source_field(

    source_path: Path,

    settings: AppSettings,

    *,

    progress_callback: Callable[[str], None] | None = None,

) -> tuple[SolvedField, bool]:

    header, width, height = read_header_and_shape(source_path)

    valid_wcs, reasons = validate_wcs(header, source_path)

    if valid_wcs:

        solved_field = extract_solved_field(header, width, height, source_path)

        if solved_field is None:

            raise ValueError("Could not derive an image footprint from the selected source image.")

        return solved_field, False



    local_result = solve_wcs_from_metadata_and_gaia(
        source_path,
        settings.cache_dir / "solar-system-wcs",
        progress_callback=progress_callback,
    )
    if local_result.solved_field is not None:

        return local_result.solved_field, True
    reasons.extend(local_result.reasons)



    if not settings.astrometry_api_key:

        reason_text = " ".join(reason.strip() for reason in reasons if reason.strip()) or "The selected image does not contain a usable celestial WCS."

        raise ValueError(f"Selected source image does not contain a usable celestial WCS. {reason_text}")



    if progress_callback is not None:

        progress_callback("Embedded WCS was unusable; attempting astrometry.net fallback for the solar-system mode.")

    hints = infer_astrometry_solve_hints(header, width, height, source_path)

    result = AstrometryNetClient(settings.astrometry_api_key).solve_file(

        source_path,

        settings.cache_dir / "solar-system-wcs",

        hints=hints,

    )

    if result.solved_field is None:

        reason_text = " ".join(reason.strip() for reason in [*reasons, *result.reasons] if reason.strip()) or "Astrometry fallback did not return a valid WCS."

        raise ValueError(f"Could not recover a usable celestial WCS. {reason_text}")

    if progress_callback is not None:

        progress_callback("Recovered a usable WCS for solar-system mode via astrometry.net fallback.")

    return result.solved_field, True





def _resolved_wcs_for_field(solved_field: SolvedField) -> WCS:

    header = read_header(solved_field.wcs_path)

    return WCS(header)





def _predictions_from_skybot_table(table: Table, *, magnitude_limit: float | None) -> list[SolarSystemPrediction]:

    predictions: list[SolarSystemPrediction] = []

    for row in table:

        designation = _row_designation_text(row)

        display_name = _prediction_display_name(_row_text(row, "Name"), designation)

        object_type, orbit_class = _classify_prediction(

            _row_text(row, "Type"),

            display_name,

        )

        magnitude = _row_float(row, "V")

        if magnitude_limit is not None and magnitude is not None and magnitude > float(magnitude_limit) + 1.0 and object_type != "Comet":

            continue

        predictions.append(

            SolarSystemPrediction(

                name=display_name,

                designation=designation,

                object_type=object_type,

                orbit_class=orbit_class,

                ra_deg=_required_row_float(row, "RA"),

                dec_deg=_required_row_float(row, "DEC"),

                predicted_magnitude=magnitude,

                ra_rate_arcsec_per_hour=_row_float(row, "RA_rate"),

                dec_rate_arcsec_per_hour=_row_float(row, "DEC_rate"),

                positional_uncertainty_arcsec=_row_float(row, "posunc"),

            )

        )

    return predictions





def _supplement_interstellar_predictions(

    predictions: list[SolarSystemPrediction],

    observation_time: datetime,

    *,

    observatory_code: str | None,

) -> list[SolarSystemPrediction]:

    if Miriade is None:

        return predictions



    supplemented = list(predictions)

    for object_name, lookup_name in _TARGETED_INTERSTELLAR_FALLBACK_OBJECTS:

        designation = object_name.split("/", 1)[0] if "/" in object_name else None

        existing_index = _matching_prediction_index(supplemented, object_name, designation)

        existing_prediction = supplemented[existing_index] if existing_index is not None else None

        if existing_prediction is not None and not _prediction_needs_interstellar_refresh(existing_prediction, object_name, designation):

            continue

        prediction = _query_known_interstellar_prediction(

            object_name,

            observation_time,

            observatory_code=observatory_code,

            lookup_name=lookup_name,

        )

        if prediction is None:

            continue

        merged_prediction = prediction if existing_prediction is None else _merge_authoritative_prediction(existing_prediction, prediction)

        if existing_index is None:

            supplemented.append(merged_prediction)

        else:

            supplemented[existing_index] = merged_prediction

    return supplemented





def _matching_prediction_index(

    predictions: list[SolarSystemPrediction],

    object_name: str,

    designation: str | None,

) -> int | None:

    target_key = _prediction_identity_key(object_name, designation)

    for index, prediction in enumerate(predictions):

        if _prediction_identity_key(prediction.name, prediction.designation) == target_key:

            return index

    return None





def _prediction_needs_interstellar_refresh(

    prediction: SolarSystemPrediction,

    canonical_name: str,

    canonical_designation: str | None,

) -> bool:

    if (prediction.name or "").strip() != canonical_name:

        return True

    if (prediction.designation or "").strip() != ((canonical_designation or "").strip()):

        return True

    return prediction.predicted_magnitude is None





def _merge_authoritative_prediction(

    existing_prediction: SolarSystemPrediction,

    authoritative_prediction: SolarSystemPrediction,

) -> SolarSystemPrediction:

    return SolarSystemPrediction(

        name=authoritative_prediction.name,

        designation=authoritative_prediction.designation or existing_prediction.designation,

        object_type=authoritative_prediction.object_type,

        orbit_class=authoritative_prediction.orbit_class,

        ra_deg=authoritative_prediction.ra_deg,

        dec_deg=authoritative_prediction.dec_deg,

        predicted_magnitude=(

            authoritative_prediction.predicted_magnitude

            if authoritative_prediction.predicted_magnitude is not None

            else existing_prediction.predicted_magnitude

        ),

        ra_rate_arcsec_per_hour=(

            authoritative_prediction.ra_rate_arcsec_per_hour

            if authoritative_prediction.ra_rate_arcsec_per_hour is not None

            else existing_prediction.ra_rate_arcsec_per_hour

        ),

        dec_rate_arcsec_per_hour=(

            authoritative_prediction.dec_rate_arcsec_per_hour

            if authoritative_prediction.dec_rate_arcsec_per_hour is not None

            else existing_prediction.dec_rate_arcsec_per_hour

        ),

        positional_uncertainty_arcsec=(

            authoritative_prediction.positional_uncertainty_arcsec

            if authoritative_prediction.positional_uncertainty_arcsec is not None

            else existing_prediction.positional_uncertainty_arcsec

        ),

    )





def _query_known_interstellar_prediction(

    object_name: str,

    observation_time: datetime,

    *,

    observatory_code: str | None,

    lookup_name: str | None = None,

) -> SolarSystemPrediction | None:

    location_code = (observatory_code or "").strip() or "500"

    normalized_time = observation_time.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")

    return _cached_known_interstellar_prediction(

        object_name,

        (lookup_name or object_name).strip() or object_name,

        normalized_time,

        location_code,

    )





@lru_cache(maxsize=32)

def _cached_known_interstellar_prediction(

    object_name: str,

    lookup_name: str,

    observation_time_iso: str,

    location_code: str,

) -> SolarSystemPrediction | None:

    prediction = _horizons_known_interstellar_prediction(

        object_name,

        lookup_name,

        observation_time_iso,

        location_code,

    )

    if prediction is not None:

        return prediction

    return _miriade_known_interstellar_prediction(

        object_name,

        lookup_name,

        observation_time_iso,

        location_code,

    )


def _query_known_small_body_prediction(

    identifier: str,

    observation_time: datetime,

    *,

    observatory_code: str | None,

) -> SolarSystemPrediction | None:

    if Horizons is None:
        return None
    location_code = (observatory_code or "").strip() or "500"
    normalized_time = observation_time.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")
    prediction = _cached_known_small_body_prediction(identifier.strip(), normalized_time, location_code)
    if prediction is not None or location_code == "500":
        return prediction
    return _cached_known_small_body_prediction(identifier.strip(), normalized_time, "500")


def _search_known_small_body_candidates(identifier: str, *, max_results: int) -> tuple[str, ...]:

    normalized_identifier = (identifier or "").strip()
    if not normalized_identifier:
        return ()
    return _cached_small_body_search_candidates(normalized_identifier, max(1, int(max_results)))


def _preferred_small_body_search_identifier(name_text: str | None, designation_text: str | None) -> str | None:

    designation = (designation_text or "").strip()
    name = (name_text or "").strip()
    if designation:
        if _UNPREFIXED_COMET_DESIGNATION_PATTERN.fullmatch(designation):
            comet_match = _COMET_NAME_DESIGNATION_PATTERN.search(name)
            if comet_match is not None:
                return comet_match.group(1).upper()
        return designation
    comet_match = _COMET_NAME_DESIGNATION_PATTERN.search(name)
    if comet_match is not None:
        return comet_match.group(1).upper()
    return name or None


@lru_cache(maxsize=64)
def _cached_small_body_search_candidates(identifier: str, max_results: int) -> tuple[str, ...]:

    if SBDB is None:
        return (identifier,)
    candidates: list[str] = []
    seen: set[str] = set()

    def add_candidate(value: str | None) -> None:
        candidate_value = (value or "").strip()
        if not candidate_value:
            return
        candidate_key = candidate_value.lower()
        if candidate_key in seen:
            return
        seen.add(candidate_key)
        candidates.append(candidate_value)

    def add_preferred_candidate(primary: str | None, fallback: str | None = None) -> None:
        primary_text = _preferred_small_body_search_identifier(fallback, primary)
        if primary_text:
            add_candidate(primary_text)
            return
        add_candidate(fallback)

    query_terms = [identifier]
    if "*" not in identifier:
        query_terms.append(f"{identifier}*")

    for query_text in query_terms:
        try:
            result = SBDB.query(query_text, id_type="search")
        except Exception:
            continue

        object_data = result.get("object") if isinstance(result, dict) else None
        if isinstance(object_data, dict):
            add_preferred_candidate(
                object_data.get("des"),
                object_data.get("shortname") or object_data.get("fullname"),
            )

        list_data = result.get("list") if isinstance(result, dict) else None
        if isinstance(list_data, dict):
            names = list(list_data.get("name") or ())
            designations = list(list_data.get("pdes") or ())
            row_count = max(len(names), len(designations))
            for row_index in range(row_count):
                if len(candidates) >= max_results:
                    break
                preferred_designation = str(designations[row_index]) if row_index < len(designations) else None
                preferred_name = str(names[row_index]) if row_index < len(names) else None
                add_preferred_candidate(preferred_designation, preferred_name)

        if len(candidates) >= max_results:
            break

    if not candidates:
        add_candidate(identifier)
    return tuple(candidates[:max_results])


def _resolve_known_small_body_prediction(

    identifier: str,

    observation_time_iso: str,

    location_code: str,

) -> SolarSystemPrediction | None:

    epoch = Time(observation_time_iso, format="isot", scale="utc")
    for _attempt_index in range(2):
        encountered_exception = False
        for target_id, id_type in _small_body_lookup_candidates(identifier):
            try:
                query_kwargs: dict[str, object] = {
                    "id": target_id,
                    "location": location_code,
                    "epochs": epoch.jd,
                }
                if id_type:
                    query_kwargs["id_type"] = id_type
                table = Horizons(**query_kwargs).ephemerides()
            except Exception:
                encountered_exception = True
                continue
            if table is None or len(table) == 0:
                continue
            row = table[0]
            display_name = (_row_text(row, "targetname") or identifier).strip() or identifier
            object_type, orbit_class = _classify_prediction(None, f"{identifier} {display_name}".strip())
            designation = identifier if identifier.strip().lower() != display_name.lower() else None
            return SolarSystemPrediction(
                name=display_name,
                designation=designation,
                object_type=object_type,
                orbit_class=orbit_class,
                ra_deg=_required_row_float(row, "RA"),
                dec_deg=_required_row_float(row, "DEC"),
                predicted_magnitude=_row_float(row, "V", "Tmag", "APmag", "Nmag", "VMag"),
                ra_rate_arcsec_per_hour=_row_float(row, "RA_rate"),
                dec_rate_arcsec_per_hour=_row_float(row, "DEC_rate"),
                positional_uncertainty_arcsec=_row_float(row, "SMAA_3sigma", "RSS_3sigma", "SMIA_3sigma"),
            )
        if not encountered_exception:
            break
    return None


@lru_cache(maxsize=64)
def _cached_known_small_body_prediction_hit(

    identifier: str,

    observation_time_iso: str,

    location_code: str,

) -> SolarSystemPrediction:

    prediction = _resolve_known_small_body_prediction(identifier, observation_time_iso, location_code)
    if prediction is None:
        raise _SmallBodyPredictionCacheMiss()
    return prediction


def _cached_known_small_body_prediction(

    identifier: str,

    observation_time_iso: str,

    location_code: str,

) -> SolarSystemPrediction | None:

    try:
        return _cached_known_small_body_prediction_hit(identifier, observation_time_iso, location_code)
    except _SmallBodyPredictionCacheMiss:
        return None


def _clear_cached_known_small_body_prediction() -> None:

    _cached_known_small_body_prediction_hit.cache_clear()


_cached_known_small_body_prediction.cache_clear = _clear_cached_known_small_body_prediction


def _small_body_lookup_candidates(identifier: str) -> tuple[tuple[str, str | None], ...]:

    normalized_identifier = (identifier or "").strip()
    if not normalized_identifier:
        return ()
    candidates: list[tuple[str, str | None]] = []
    seen: set[tuple[str, str | None]] = set()

    def add_candidate(value: str | None, id_type: str | None = "smallbody") -> None:
        candidate_value = (value or "").strip()
        if not candidate_value:
            return
        candidate = (candidate_value, id_type)
        if candidate in seen:
            return
        seen.add(candidate)
        candidates.append(candidate)

    object_type, _orbit_class = _classify_prediction(None, normalized_identifier)
    if object_type == "Comet":
        add_candidate(f"DES={normalized_identifier};CAP", None)
        if "/" in normalized_identifier:
            add_candidate(f"DES={normalized_identifier.split('/', 1)[0]};CAP", None)
    add_candidate(normalized_identifier)
    if "/" in normalized_identifier:
        add_candidate(normalized_identifier.split("/", 1)[0])
    return tuple(candidates)





def _miriade_known_interstellar_prediction(

    object_name: str,

    lookup_name: str,

    observation_time_iso: str,

    location_code: str,

) -> SolarSystemPrediction | None:

    if Miriade is None:

        return None

    try:

        table = Miriade.get_ephemerides(

            lookup_name,

            objtype="comet",

            epoch=Time(observation_time_iso, format="isot", scale="utc"),

            location=location_code,

        )

    except Exception:

        return None

    if table is None or len(table) == 0:

        return None

    row = table[0]

    name = _prediction_display_name(_row_text(row, "target"), object_name.split("/", 1)[0] if "/" in object_name else None)

    ra_rate_arcsec_per_hour = _row_float(row, "RAcosD_rate")

    if ra_rate_arcsec_per_hour is not None:

        ra_rate_arcsec_per_hour *= 60.0

    dec_rate_arcsec_per_hour = _row_float(row, "DEC_rate")

    if dec_rate_arcsec_per_hour is not None:

        dec_rate_arcsec_per_hour *= 60.0

    designation = object_name.split("/", 1)[0] if "/" in object_name else None

    return SolarSystemPrediction(

        name=name,

        designation=designation,

        object_type="Comet",

        orbit_class="interstellar",

        ra_deg=_required_row_float(row, "RA"),

        dec_deg=_required_row_float(row, "DEC"),

        predicted_magnitude=_row_float(row, "V"),

        ra_rate_arcsec_per_hour=ra_rate_arcsec_per_hour,

        dec_rate_arcsec_per_hour=dec_rate_arcsec_per_hour,

        positional_uncertainty_arcsec=_row_float(row, "posunc"),

    )





def _horizons_known_interstellar_prediction(

    object_name: str,

    lookup_name: str,

    observation_time_iso: str,

    location_code: str,

) -> SolarSystemPrediction | None:

    if Horizons is None:

        return None

    try:

        epoch = Time(observation_time_iso, format="isot", scale="utc")

        table = Horizons(id=lookup_name, location=location_code, epochs=epoch.jd).ephemerides()

    except Exception:

        return None

    if table is None or len(table) == 0:

        return None

    row = table[0]

    designation = object_name.split("/", 1)[0] if "/" in object_name else None

    return SolarSystemPrediction(

        name=object_name,

        designation=designation,

        object_type="Comet",

        orbit_class="interstellar",

        ra_deg=_required_row_float(row, "RA"),

        dec_deg=_required_row_float(row, "DEC"),

        predicted_magnitude=_row_float(row, "Tmag", "APmag", "Nmag", "VMag"),

        ra_rate_arcsec_per_hour=_row_float(row, "RA_rate"),

        dec_rate_arcsec_per_hour=_row_float(row, "DEC_rate"),

        positional_uncertainty_arcsec=_row_float(row, "SMAA_3sigma", "RSS_3sigma", "SMIA_3sigma"),

    )





def _prediction_identity_key(name_text: str | None, designation_text: str | None) -> str:

    name = (name_text or "").strip().lower()

    designation = (designation_text or "").strip().lower()

    return designation or name





def _skybot_cone_search_table(

    field: SkyCoord,

    radius: u.Quantity,

    epoch: Time,

    *,

    observatory_code: str | None,

    position_error_arcsec: float,

    find_planets: bool,

    find_asteroids: bool,

    find_comets: bool,

) -> Table:

    payload = _skybot_query_payload(

        field,

        radius,

        epoch,

        observatory_code=observatory_code,

        position_error_arcsec=position_error_arcsec,

        find_planets=find_planets,

        find_asteroids=find_asteroids,

        find_comets=find_comets,

    )

    request_url = _skybot_server_url()

    timeout_seconds = float(skybot_conf.timeout)

    for attempt_index in range(_SKYBOT_MAX_HTTP_ATTEMPTS):

        try:

            response = requests.get(

                request_url,

                params=payload,

                timeout=timeout_seconds,

            )

            response.raise_for_status()

            return _parse_skybot_votable(response.content, response_text=response.text)

        except requests.HTTPError as exc:

            status_code = exc.response.status_code if exc.response is not None else None

            if status_code in _SKYBOT_RETRYABLE_STATUS_CODES and attempt_index + 1 < _SKYBOT_MAX_HTTP_ATTEMPTS:

                continue

            if status_code in _SKYBOT_RETRYABLE_STATUS_CODES:

                raise RuntimeError(

                    f"SkyBoT service returned HTTP {status_code} after {_SKYBOT_MAX_HTTP_ATTEMPTS} attempts. The upstream IMCCE service is currently failing for this query; please try again shortly."

                ) from exc

            raise RuntimeError(f"SkyBoT request failed with HTTP {status_code}: {exc}") from exc

        except requests.RequestException as exc:

            if attempt_index + 1 < _SKYBOT_MAX_HTTP_ATTEMPTS:

                continue

            raise RuntimeError(

                f"SkyBoT request failed after {_SKYBOT_MAX_HTTP_ATTEMPTS} attempts: {exc}"

            ) from exc

    raise RuntimeError("SkyBoT request failed without returning a response.")





def _skybot_query_payload(

    field: SkyCoord,

    radius: u.Quantity,

    epoch: Time,

    *,

    observatory_code: str | None,

    position_error_arcsec: float,

    find_planets: bool,

    find_asteroids: bool,

    find_comets: bool,

) -> dict[str, object]:

    search_radius = Angle(radius).to_value(u.deg)

    location = (observatory_code or "").strip() or "500"

    return {

        "-ra": float(field.ra.deg),

        "-dec": float(field.dec.deg),

        "-rd": min(max(search_radius, 0.0), 10.0),

        "-ep": str(epoch.jd),

        "-loc": location,

        "-filter": float(max(0.0, position_error_arcsec)),

        "-objFilter": f"{int(find_asteroids)}{int(find_planets)}{int(find_comets)}",

        "-refsys": "EQJ2000",

        "-output": "all",

        "-mime": "votable",

    }





def _skybot_server_url() -> str:

    server = skybot_conf.skybot_server

    if isinstance(server, (list, tuple)):

        if not server:

            raise RuntimeError("astroquery.imcce SkyBoT server configuration is empty.")

        return str(server[0])

    return str(server)





def _parse_skybot_votable(content: bytes, *, response_text: str | None = None) -> Table:

    normalized_content = _extract_votable_document_bytes(content)

    fallback_text_content = _extract_votable_document_bytes(response_text.encode("utf-8")) if response_text else None

    try:

        with warnings.catch_warnings():

            warnings.filterwarnings(

                "ignore",

                category=AstropyUserWarning,

                message=r"column ra|(column de) has a unit but is kept as a MaskedColumn",

            )

            try:

                results = QTable.read(BytesIO(normalized_content), format="votable")

            except Exception:

                if fallback_text_content is None:

                    raise

                results = QTable.read(BytesIO(fallback_text_content), format="votable")

    except Exception as exc:

        detail = ""

        if response_text:

            detail = response_text.strip().splitlines()[0][:200]

        if detail:

            raise RuntimeError(f"SkyBoT query returned an unreadable response: {detail}") from exc

        raise RuntimeError("SkyBoT query returned an unreadable response.") from exc



    if len(results) == 0:

        return results



    if "ra" in results.colnames and "de" in results.colnames:

        coordinates = SkyCoord(ra=results["ra"], dec=results["de"], unit=(u.hourangle, u.deg), frame="icrs")

        results["ra"] = coordinates.ra.deg

        results["ra"].unit = u.deg

        results["de"] = coordinates.dec.deg

        results["de"].unit = u.deg



    for field_name in list(results.colnames):

        mapped_name = skybot_conf.field_names.get(field_name)

        if mapped_name and mapped_name != field_name:

            results.rename_column(field_name, mapped_name)



    if "Number" in results.colnames:

        unnumbered_mask = [not str(value).isdigit() for value in results["Number"]]

        numbers = [int(value) if str(value).isdigit() else 0 for value in results["Number"]]

        results.replace_column(

            "Number",

            MaskedColumn(numbers, name="Number", mask=unnumbered_mask),

        )



    return results



def _extract_votable_document_bytes(content: bytes) -> bytes:

    normalized_content = content.lstrip()

    lower_content = normalized_content.lower()

    marker_indices = [

        index

        for index in (

            lower_content.find(b"<?xml"),

            lower_content.find(b"<votable"),

        )

        if index >= 0

    ]

    if not marker_indices:

        return normalized_content

    return normalized_content[min(marker_indices) :]





def _build_detection_from_prediction(

    prediction: SolarSystemPrediction,

    image_data: np.ndarray,

    solved_field: SolvedField,

    *,

    pixel_x: float,

    pixel_y: float,

    pixel_scale_arcsec_per_pixel: float | None,

    exposure_seconds: float | None,

    magnitude_limit: float,

    altitude_deg: float | None,

) -> SolarSystemDetection:

    motion_rate = _motion_rate_arcsec_per_hour(prediction.ra_rate_arcsec_per_hour, prediction.dec_rate_arcsec_per_hour)

    expected_trail_length_px = None

    if motion_rate is not None and pixel_scale_arcsec_per_pixel is not None and pixel_scale_arcsec_per_pixel > 0 and exposure_seconds is not None:

        expected_trail_length_px = (motion_rate * max(0.0, float(exposure_seconds)) / 3600.0) / pixel_scale_arcsec_per_pixel



    measured_x, measured_y, match_offset_px, local_snr, local_peak_value, local_flux, local_fwhm_px = _measure_local_match(

        image_data,

        pixel_x,

        pixel_y,

        expected_trail_length_px=expected_trail_length_px,

    )

    confidence_score = _confidence_score(

        prediction.predicted_magnitude,

        magnitude_limit,

        local_snr,

        match_offset_px,

        expected_trail_length_px,

        solved_field,

        pixel_x,

        pixel_y,

    )

    likely_visible = bool(local_snr is not None and local_snr >= 4.0 and match_offset_px is not None and match_offset_px <= 8.0)

    status = _detection_status(likely_visible, confidence_score, prediction.predicted_magnitude, magnitude_limit)

    return SolarSystemDetection(

        name=prediction.name,

        designation=prediction.designation,

        object_type=prediction.object_type,

        orbit_class=prediction.orbit_class,

        predicted_ra_deg=prediction.ra_deg,

        predicted_dec_deg=prediction.dec_deg,

        predicted_x=pixel_x,

        predicted_y=pixel_y,

        predicted_magnitude=prediction.predicted_magnitude,

        ra_rate_arcsec_per_hour=prediction.ra_rate_arcsec_per_hour,

        dec_rate_arcsec_per_hour=prediction.dec_rate_arcsec_per_hour,

        motion_rate_arcsec_per_hour=motion_rate,

        expected_trail_length_px=expected_trail_length_px,

        positional_uncertainty_arcsec=prediction.positional_uncertainty_arcsec,

        altitude_deg=altitude_deg,

        likely_visible=likely_visible,

        confidence_score=confidence_score,

        status=status,

        measured_x=measured_x,

        measured_y=measured_y,

        match_offset_px=match_offset_px,

        local_snr=local_snr,

        local_peak_value=local_peak_value,

        local_flux=local_flux,

        local_fwhm_px=local_fwhm_px,

    )


def _build_unmeasured_detection_from_prediction(

    prediction: SolarSystemPrediction,

    *,

    pixel_x: float,

    pixel_y: float,

    pixel_scale_arcsec_per_pixel: float | None,

    exposure_seconds: float | None,

    magnitude_limit: float,

    altitude_deg: float | None,

    status_override: str | None = None,

) -> SolarSystemDetection:

    motion_rate = _motion_rate_arcsec_per_hour(prediction.ra_rate_arcsec_per_hour, prediction.dec_rate_arcsec_per_hour)
    expected_trail_length_px = None
    if motion_rate is not None and pixel_scale_arcsec_per_pixel is not None and pixel_scale_arcsec_per_pixel > 0 and exposure_seconds is not None:
        expected_trail_length_px = (motion_rate * max(0.0, float(exposure_seconds)) / 3600.0) / pixel_scale_arcsec_per_pixel
    status = status_override or _detection_status(False, 0.0, prediction.predicted_magnitude, magnitude_limit)
    return SolarSystemDetection(
        name=prediction.name,
        designation=prediction.designation,
        object_type=prediction.object_type,
        orbit_class=prediction.orbit_class,
        predicted_ra_deg=prediction.ra_deg,
        predicted_dec_deg=prediction.dec_deg,
        predicted_x=float(pixel_x),
        predicted_y=float(pixel_y),
        predicted_magnitude=prediction.predicted_magnitude,
        ra_rate_arcsec_per_hour=prediction.ra_rate_arcsec_per_hour,
        dec_rate_arcsec_per_hour=prediction.dec_rate_arcsec_per_hour,
        motion_rate_arcsec_per_hour=motion_rate,
        expected_trail_length_px=expected_trail_length_px,
        positional_uncertainty_arcsec=prediction.positional_uncertainty_arcsec,
        altitude_deg=altitude_deg,
        likely_visible=False,
        confidence_score=0.0,
        status=status,
    )





def _measure_local_match(

    image_data: np.ndarray,

    predicted_x: float,

    predicted_y: float,

    *,

    expected_trail_length_px: float | None,

) -> tuple[float | None, float | None, float | None, float | None, float | None, float | None, float | None]:

    if image_data.ndim != 2 or image_data.size == 0:

        return None, None, None, None, None, None

    search_radius = max(5, min(18, int(math.ceil(5.0 + min(expected_trail_length_px or 0.0, 10.0)))))

    x_center = int(round(predicted_x))

    y_center = int(round(predicted_y))

    x0 = max(0, x_center - search_radius)

    x1 = min(image_data.shape[1], x_center + search_radius + 1)

    y0 = max(0, y_center - search_radius)

    y1 = min(image_data.shape[0], y_center + search_radius + 1)

    if x0 >= x1 or y0 >= y1:

        return None, None, None, None, None, None, None



    local = np.asarray(image_data[y0:y1, x0:x1], dtype=float)

    finite = local[np.isfinite(local)]

    if finite.size == 0:

        return None, None, None, None, None, None, None

    background = float(np.nanmedian(finite))

    mad = float(np.nanmedian(np.abs(finite - background)))

    sigma = max(1e-6, 1.4826 * mad)

    peak_index = int(np.nanargmax(local))

    peak_y_local, peak_x_local = np.unravel_index(peak_index, local.shape)

    measured_x = float(x0 + peak_x_local)

    measured_y = float(y0 + peak_y_local)

    peak_value = float(local[peak_y_local, peak_x_local])

    local_snr = (peak_value - background) / sigma

    match_offset_px = float(math.hypot(measured_x - predicted_x, measured_y - predicted_y))

    aperture_radius = max(2.5, min(float(search_radius), 4.0 + (expected_trail_length_px or 0.0) * 0.5))

    yy, xx = np.indices(local.shape, dtype=float)

    aperture_mask = ((xx - peak_x_local) ** 2 + (yy - peak_y_local) ** 2) <= (aperture_radius ** 2)

    local_flux = float(np.nansum(local[aperture_mask] - background)) if np.any(aperture_mask) else None

    local_fwhm_px = _estimate_local_match_fwhm(local, peak_x_local=peak_x_local, peak_y_local=peak_y_local)

    return measured_x, measured_y, match_offset_px, float(local_snr), peak_value, local_flux, local_fwhm_px


def _estimate_local_match_fwhm(local: np.ndarray, *, peak_x_local: int, peak_y_local: int, half_size: int = 7) -> float | None:

    half_size = max(3, int(half_size))
    y0 = max(0, int(peak_y_local) - half_size)
    y1 = min(local.shape[0], int(peak_y_local) + half_size + 1)
    x0 = max(0, int(peak_x_local) - half_size)
    x1 = min(local.shape[1], int(peak_x_local) + half_size + 1)
    if x0 >= x1 or y0 >= y1:
        return None

    cutout = np.asarray(local[y0:y1, x0:x1], dtype=float)
    if cutout.size == 0 or not np.isfinite(cutout).any():
        return None

    peak_x_offset = float(int(peak_x_local) - x0)
    peak_y_offset = float(int(peak_y_local) - y0)
    border_mask = np.zeros(cutout.shape, dtype=bool)
    border_mask[0, :] = True
    border_mask[-1, :] = True
    border_mask[:, 0] = True
    border_mask[:, -1] = True
    border_pixels = cutout[border_mask & np.isfinite(cutout)]
    background = float(np.median(border_pixels)) if border_pixels.size > 0 else float(np.nanmedian(cutout))

    yy, xx = np.indices(cutout.shape, dtype=float)
    radius_sq = ((xx - peak_x_offset) ** 2) + ((yy - peak_y_offset) ** 2)
    core_radius_sq = float(max(2.25, min(16.0, float(half_size * half_size) / 4.0)))
    weights = np.where(np.isfinite(cutout), cutout - background, 0.0)
    weights = np.where(radius_sq <= core_radius_sq, weights, 0.0)
    weights = np.where(weights > 0.0, weights, 0.0)
    total_weight = float(np.sum(weights))
    if total_weight <= 0.0:
        return None

    radial_second_moment = float(np.sum(weights * radius_sq) / total_weight)
    sigma = math.sqrt(max(radial_second_moment / 2.0, 0.0))
    fwhm = 2.354820045 * sigma
    if not np.isfinite(fwhm) or fwhm < 0.8 or fwhm > (half_size * 2):
        return None
    return float(fwhm)





@dataclass(slots=True)

class _VisibleLimitBinProbeResult:

    bin_start_magnitude: float

    tested_count: int

    visible_count: int

    dimmest_visible_magnitude: float

    median_visible_snr: float

    visible_stars: tuple[SolarSystemVisibilityEstimateStar, ...] = ()





def _visible_limit_estimate_options(settings: AppSettings) -> _VisibleLimitEstimateOptions:

    stars_per_bin = max(2, int(settings.asteroid_estimate_stars_per_bin))

    required_visible_count = min(max(1, int(settings.asteroid_estimate_required_visible_stars)), stars_per_bin - 1)

    return _VisibleLimitEstimateOptions(

        snr_threshold=min(100.0, max(0.1, float(settings.asteroid_estimate_snr_threshold))),

        start_magnitude=min(30.0, max(-5.0, float(settings.asteroid_estimate_start_magnitude))),

        stars_per_bin=stars_per_bin,

        required_visible_count=required_visible_count,

        annotate_lowest_mag_stars=bool(settings.asteroid_estimate_annotate_lowest_mag_stars),

    )





def _project_catalog_stars_into_image(

    catalog_stars: list[CatalogStar],

    wcs: WCS,

    solved_field: SolvedField,

) -> list[_ProjectedCatalogStar]:

    usable_stars = [star for star in catalog_stars if star.magnitude is not None and np.isfinite(float(star.magnitude))]

    if not usable_stars:

        return []



    star_ra = np.asarray([star.ra_deg for star in usable_stars], dtype=float)

    star_dec = np.asarray([star.dec_deg for star in usable_stars], dtype=float)

    star_x, star_y = wcs.world_to_pixel_values(star_ra, star_dec)

    border_margin = 8.0

    projected: list[_ProjectedCatalogStar] = []

    for star, x_value, y_value in zip(usable_stars, star_x, star_y, strict=False):

        if not np.isfinite(x_value) or not np.isfinite(y_value):

            continue

        x = float(x_value)

        y = float(y_value)

        if x < border_margin or y < border_margin or x >= (solved_field.width - border_margin) or y >= (solved_field.height - border_margin):

            continue

        projected.append(_ProjectedCatalogStar(star=star, x=x, y=y))

    projected.sort(key=lambda item: float(item.star.magnitude or 99.0))

    return projected





def _probe_visible_limit_bins(

    image_data: np.ndarray,

    projected_stars: list[_ProjectedCatalogStar],

    *,

    options: _VisibleLimitEstimateOptions,

    progress_callback: Callable[[str], None] | None = None,

) -> list[_VisibleLimitBinProbeResult]:

    if not projected_stars:

        return []

    available_bin_starts = sorted(

        {

            options.start_magnitude

            + (math.floor((float(item.star.magnitude or options.start_magnitude) - options.start_magnitude) / _VISIBLE_LIMIT_STEP_MAG) * _VISIBLE_LIMIT_STEP_MAG)

            for item in projected_stars

            if item.star.magnitude is not None and float(item.star.magnitude) >= options.start_magnitude

        }

    )

    probe_results: list[_VisibleLimitBinProbeResult] = []

    consecutive_unsuccessful_bins = 0

    for bin_start in available_bin_starts:

        bin_end = bin_start + _VISIBLE_LIMIT_STEP_MAG

        bin_candidates = [

            item

            for item in projected_stars

            if item.star.magnitude is not None and bin_start <= float(item.star.magnitude) < bin_end

        ]

        if progress_callback is not None:

            progress_callback(

                f"Searching for {bin_start:.1f}-{bin_end:.1f} mag stars. {len(bin_candidates)} Gaia candidate(s) in this bin."

            )

        if len(bin_candidates) >= options.required_visible_count:

            bin_candidates.sort(key=lambda item: abs(float(item.star.magnitude or bin_start) - (bin_start + (_VISIBLE_LIMIT_STEP_MAG / 2.0))))

            visible_stars: list[SolarSystemVisibilityEstimateStar] = []

            visible_snrs: list[float] = []

            tested_count = 0

            for candidate in bin_candidates[: options.stars_per_bin]:

                measured_x, measured_y, match_offset_px, local_snr, _local_peak_value, _local_flux, _local_fwhm_px = _measure_local_match(

                    image_data,

                    candidate.x,

                    candidate.y,

                    expected_trail_length_px=None,

                )

                tested_count += 1

                if (

                    local_snr is None

                    or match_offset_px is None

                    or local_snr < options.snr_threshold

                    or match_offset_px > _VISIBLE_LIMIT_MAX_OFFSET_PX

                    or candidate.star.magnitude is None

                ):

                    if progress_callback is not None:

                        if local_snr is None or match_offset_px is None:

                            progress_callback(

                                f"Searching for {bin_start:.1f} mag stars. {candidate.star.name}: no measurable match in the local search window."

                            )

                        else:

                            progress_callback(

                                f"Searching for {bin_start:.1f} mag stars. {candidate.star.name}: no match, SNR={local_snr:.1f}, offset={match_offset_px:.1f} px."

                            )

                    continue

                visible_stars.append(

                    SolarSystemVisibilityEstimateStar(

                        name=candidate.star.name,

                        magnitude=float(candidate.star.magnitude),

                        predicted_x=float(candidate.x),

                        predicted_y=float(candidate.y),

                        measured_x=measured_x,

                        measured_y=measured_y,

                        local_snr=float(local_snr),

                        match_offset_px=float(match_offset_px),

                    )

                )

                visible_snrs.append(float(local_snr))

                if progress_callback is not None:

                    progress_callback(

                        f"Searching for {bin_start:.1f} mag stars. Match found for {candidate.star.name}; mag={float(candidate.star.magnitude):.1f}, SNR={local_snr:.1f}, offset={match_offset_px:.1f} px."

                    )

            if visible_stars:

                median_visible_snr = float(np.median(np.asarray(visible_snrs, dtype=float)))

                probe_results.append(

                    _VisibleLimitBinProbeResult(

                        bin_start_magnitude=bin_start,

                        tested_count=tested_count,

                        visible_count=len(visible_stars),

                        dimmest_visible_magnitude=max(item.magnitude for item in visible_stars),

                        median_visible_snr=median_visible_snr,

                        visible_stars=tuple(visible_stars),

                    )

                )

                if progress_callback is not None:

                    progress_callback(

                        f"{bin_start:.1f}-{bin_end:.1f} mag bin complete: {len(visible_stars)}/{tested_count} visible; dimmest match {max(item.magnitude for item in visible_stars):.1f}, median SNR {median_visible_snr:.1f}."

                    )

                if len(visible_stars) >= options.required_visible_count:

                    consecutive_unsuccessful_bins = 0

                else:

                    consecutive_unsuccessful_bins += 1

            else:

                if progress_callback is not None:

                    progress_callback(

                        f"{bin_start:.1f}-{bin_end:.1f} mag bin complete: 0/{tested_count} visible matches."

                    )

                consecutive_unsuccessful_bins += 1

        elif probe_results:

            if progress_callback is not None:

                progress_callback(

                    f"Skipping {bin_start:.1f}-{bin_end:.1f} mag bin because it has only {len(bin_candidates)} Gaia candidate(s); continuing to the next sampled bin."

                )

            consecutive_unsuccessful_bins += 1

        elif progress_callback is not None:

            progress_callback(

                f"Skipping {bin_start:.1f}-{bin_end:.1f} mag bin because it has only {len(bin_candidates)} Gaia candidate(s)."

            )

        if consecutive_unsuccessful_bins >= _VISIBLE_LIMIT_STOP_AFTER_UNSUCCESSFUL_BINS:

            if progress_callback is not None:

                progress_callback(

                    f"Stopping the visible-limit search after {_VISIBLE_LIMIT_STOP_AFTER_UNSUCCESSFUL_BINS} successive unsuccessful magnitude bins."

                )

            break

    return probe_results





def _confidence_score(

    predicted_magnitude: float | None,

    magnitude_limit: float,

    local_snr: float | None,

    match_offset_px: float | None,

    expected_trail_length_px: float | None,

    solved_field: SolvedField,

    predicted_x: float,

    predicted_y: float,

) -> float:

    score = 0.1

    if predicted_magnitude is not None:

        score += max(0.0, min(0.25, (magnitude_limit - predicted_magnitude) / max(2.0, magnitude_limit)))

    if local_snr is not None:

        score += max(0.0, min(0.4, local_snr / 20.0))

    if match_offset_px is not None:

        score += max(0.0, 0.25 * (1.0 - min(match_offset_px, 10.0) / 10.0))

    if expected_trail_length_px is not None and expected_trail_length_px > 1.5:

        score += 0.05

    edge_margin = min(predicted_x, predicted_y, solved_field.width - predicted_x, solved_field.height - predicted_y)

    if edge_margin < 12.0:

        score -= 0.15

    return max(0.0, min(0.99, score))





def _detection_status(likely_visible: bool, confidence_score: float, predicted_magnitude: float | None, magnitude_limit: float) -> str:

    if likely_visible and confidence_score >= 0.75:

        return "High-confidence match"

    if likely_visible and confidence_score >= 0.5:

        return "Plausible visible match"

    if predicted_magnitude is not None and predicted_magnitude <= magnitude_limit:

        return "Predicted in field; visual confirmation weak"

    return "Predicted in field"





def _detection_summary_text(detections: list[SolarSystemDetection]) -> str:

    if not detections:

        return "No likely detectable known asteroids or comets were found in this field."

    likely_visible_count = sum(item.likely_visible for item in detections)

    if likely_visible_count > 0:

        label = "object" if likely_visible_count == 1 else "objects"

        return f"You captured {likely_visible_count} known solar-system {label} in this image."

    label = "object" if len(detections) == 1 else "objects"

    return f"SkyBoT predicts {len(detections)} known solar-system {label} in this field, but visual confirmation is still uncertain."





def _observer_location(latitude_deg: float | None, longitude_deg: float | None, elevation_m: float | None) -> EarthLocation | None:

    if latitude_deg is None or longitude_deg is None:

        return None

    return EarthLocation(

        lat=float(latitude_deg) * u.deg,

        lon=float(longitude_deg) * u.deg,

        height=float(elevation_m or 0.0) * u.m,

    )





def _altitude_deg(ra_deg: float, dec_deg: float, observation_time: datetime, observer_location: EarthLocation | None) -> float | None:

    if observer_location is None:

        return None

    coord = SkyCoord(ra_deg * u.deg, dec_deg * u.deg)

    try:

        with iers.conf.set_temp("auto_max_age", None):

            altaz = coord.transform_to(AltAz(obstime=Time(observation_time), location=observer_location))

    except Exception:

        return None

    if not np.isfinite(altaz.alt.deg):

        return None

    return float(altaz.alt.deg)





def predict_detection_coordinates(

    detection: SolarSystemDetection,

    *,

    reference_observation_time: datetime,

    observation_time: datetime,

) -> tuple[float, float]:

    delta_hours = (observation_time - reference_observation_time).total_seconds() / 3600.0

    ra_deg = detection.predicted_ra_deg + ((detection.ra_rate_arcsec_per_hour or 0.0) * delta_hours / 3600.0)

    dec_deg = detection.predicted_dec_deg + ((detection.dec_rate_arcsec_per_hour or 0.0) * delta_hours / 3600.0)

    return float(ra_deg), float(dec_deg)





def _build_known_object_comparison_track(

    detection: SolarSystemDetection,

    *,

    sampled_times: tuple[datetime, ...],

    observation_times: tuple[datetime, ...],

) -> KnownObjectComparisonTrack:

    resolved_target_name, path_samples = _query_known_object_heliocentric_samples(detection, sampled_times)

    if sampled_times == observation_times:

        observation_samples = path_samples

    else:

        observation_samples = _query_known_object_heliocentric_samples(detection, observation_times)[1]

    magnitude_samples = _query_known_object_magnitude_samples(detection, sampled_times)[1]

    return KnownObjectComparisonTrack(

        object_label=detection.name or detection.designation or "Known Object",

        resolved_target_name=resolved_target_name,

        path_samples=path_samples,

        observation_samples=observation_samples,

        orbital_period_days=_estimate_orbital_period_days(path_samples),

        magnitude_samples=magnitude_samples,

    )





def build_multi_known_object_heliocentric_context(

    detections: list[SolarSystemDetection] | tuple[SolarSystemDetection, ...],

    *,

    observation_times: list[datetime] | tuple[datetime, ...] | None = None,

    arc_padding_days: float = 45.0,

    sample_count: int = 61,

    include_major_planets: bool = False,

    window_start: datetime | None = None,

    window_end: datetime | None = None,

    progress_callback: Callable[[str], None] | None = None,

) -> KnownObjectHeliocentricContext:

    if Horizons is None:

        raise RuntimeError("3D known-object views require astroquery.jplhorizons.")



    normalized_detections = tuple(detections)

    explicit_window = window_start is not None and window_end is not None

    if explicit_window:

        resolved_window_start = _as_utc_datetime(window_start)

        resolved_window_end = _as_utc_datetime(window_end)

        if resolved_window_end <= resolved_window_start:

            raise ValueError("Custom trajectory window end must be later than the start.")

    else:

        resolved_window_start = None

        resolved_window_end = None



    normalized_observation_times = _unique_utc_datetimes(observation_times or ())

    if not normalized_observation_times:

        if not explicit_window:

            raise ValueError("At least one observation time is required for the 3D known-object view.")

        # Empty / custom windows without frame times still need marker epochs.

        normalized_observation_times = (resolved_window_start, resolved_window_end)



    window_padding_days = max(0.0, float(arc_padding_days))

    path_sample_count = max(2, int(sample_count))

    if explicit_window:

        window_start = resolved_window_start

        window_end = resolved_window_end

        # Preserve a comparable half-span so Span combo sync can still estimate Local-like padding.

        window_padding_days = max(

            0.0,

            ((window_end - window_start).total_seconds() / 86400.0) / 2.0,

        )

    else:

        window_start = normalized_observation_times[0] - timedelta(days=window_padding_days)

        window_end = normalized_observation_times[-1] + timedelta(days=window_padding_days)

        if window_end <= window_start:

            window_end = window_start + timedelta(days=max(1.0, window_padding_days * 2.0 or 1.0))



    reference_time = window_start + (window_end - window_start) / 2

    sampled_times = _linspace_datetimes(window_start, window_end, path_sample_count)



    target_tracks: list[KnownObjectComparisonTrack] = []

    total_targets = len(normalized_detections)

    for target_index, detection in enumerate(normalized_detections, start=1):

        if progress_callback is not None:

            if total_targets == 1:

                progress_callback("Querying JPL Horizons heliocentric vectors for the selected object.")

            else:

                target_label = detection.name or detection.designation or f"selected object {target_index}"

                progress_callback(

                    f"Querying JPL Horizons heliocentric vectors for {target_label} ({target_index}/{total_targets})."

                )

        target_tracks.append(

            _build_known_object_comparison_track(

                detection,

                sampled_times=sampled_times,

                observation_times=normalized_observation_times,

            )

        )



    primary_track = target_tracks[0] if target_tracks else None

    comparison_tracks = tuple(target_tracks[1:])



    if progress_callback is not None:

        progress_callback("Querying JPL Horizons heliocentric vectors for Earth.")

    earth_target_name, earth_path_samples = _query_horizons_vector_samples(

        target_id="399",

        query_times=sampled_times,

        location_code="500@10",

    )

    earth_observation_samples = earth_path_samples if sampled_times == normalized_observation_times else _query_horizons_vector_samples(

        target_id="399",

        query_times=normalized_observation_times,

        location_code="500@10",

    )[1]

    additional_bodies = _query_major_planet_heliocentric_paths(sampled_times, progress_callback=progress_callback) if include_major_planets else ()

    return KnownObjectHeliocentricContext(

        object_label=primary_track.object_label if primary_track is not None else "Trajectory View",

        resolved_target_name=primary_track.resolved_target_name if primary_track is not None else "Earth only",

        earth_target_name=earth_target_name,

        window_start=window_start,

        window_end=window_end,

        arc_padding_days=window_padding_days,

        reference_time=reference_time,

        object_path_samples=primary_track.path_samples if primary_track is not None else (),

        earth_path_samples=earth_path_samples,

        observation_object_samples=primary_track.observation_samples if primary_track is not None else (),

        observation_earth_samples=earth_observation_samples,

        object_magnitude_samples=primary_track.magnitude_samples if primary_track is not None else (),

        object_orbital_period_days=primary_track.orbital_period_days if primary_track is not None else None,

        earth_orbital_period_days=_estimate_orbital_period_days(earth_path_samples),

        comparison_tracks=comparison_tracks,

        additional_bodies=additional_bodies,

        include_major_planets=bool(include_major_planets),

    )





def build_known_object_heliocentric_context(

    detection: SolarSystemDetection,

    *,

    observation_times: list[datetime] | tuple[datetime, ...] | None = None,

    arc_padding_days: float = 45.0,

    sample_count: int = 61,

    include_major_planets: bool = False,

    window_start: datetime | None = None,

    window_end: datetime | None = None,

    progress_callback: Callable[[str], None] | None = None,

) -> KnownObjectHeliocentricContext:

    return build_multi_known_object_heliocentric_context(

        (detection,),

        observation_times=observation_times,

        arc_padding_days=arc_padding_days,

        sample_count=sample_count,

        include_major_planets=include_major_planets,

        window_start=window_start,

        window_end=window_end,

        progress_callback=progress_callback,

    )





def _query_known_object_heliocentric_samples(

    detection: SolarSystemDetection,

    query_times: tuple[datetime, ...],

) -> tuple[str, tuple[SolarSystemStateVectorSample, ...]]:

    error_messages: list[str] = []

    for target_id, id_type in _known_object_horizons_candidates(detection):

        try:

            return _query_horizons_vector_samples(

                target_id=target_id,

                query_times=query_times,

                location_code="500@10",

                id_type=id_type,

            )

        except Exception as exc:

            error_messages.append(f"{target_id}: {_summarize_horizons_error(exc)}")

    object_label = detection.name or detection.designation or "the selected object"

    details = "; ".join(error_messages)

    raise ValueError(f"Could not load heliocentric vectors for {object_label} from JPL Horizons. {details}".strip())





def _query_known_object_magnitude_samples(

    detection: SolarSystemDetection,

    query_times: tuple[datetime, ...],

) -> tuple[str, tuple[SolarSystemMagnitudeSample, ...]]:

    fallback_target_name = detection.name or detection.designation or "Known Object"

    for target_id, id_type in _known_object_horizons_candidates(detection):

        try:

            target_name, samples = _query_horizons_ephemeris_magnitude_samples(

                target_id=target_id,

                query_times=query_times,

                location_code="500@399",

                id_type=id_type,

            )

        except Exception:

            continue

        if samples:

            return target_name, samples

        fallback_target_name = target_name or fallback_target_name

    return fallback_target_name, ()





def _known_object_horizons_candidates(detection: SolarSystemDetection) -> tuple[tuple[str, str | None], ...]:

    candidates: list[tuple[str, str | None]] = []

    seen: set[tuple[str, str | None]] = set()

    is_comet = "comet" in str(detection.object_type or "").strip().lower()



    def add_candidate(value: str | None, id_type: str | None = "smallbody") -> None:

        text = (value or "").strip()

        if not text:

            return

        candidate = (text, id_type)

        if candidate in seen:

            return

        seen.add(candidate)

        candidates.append(candidate)



    def add_comet_designation_candidate(value: str | None) -> None:

        text = (value or "").strip()

        if not text:

            return

        add_candidate(f"DES={text};CAP", None)



    if is_comet:

        add_comet_designation_candidate(detection.designation)

    add_candidate(detection.designation)



    name_text = (detection.name or "").strip()

    if name_text:

        if "/" in name_text:

            split_name = name_text.split("/", 1)[0].strip()

            if is_comet:

                add_comet_designation_candidate(split_name)

            add_candidate(split_name)

        if name_text.startswith("(") and ")" in name_text:

            add_candidate(name_text[1 : name_text.index(")")].strip())

            add_candidate(name_text[name_text.index(")") + 1 :].strip())

        if is_comet:

            add_comet_designation_candidate(name_text)

        add_candidate(name_text)



    return tuple(candidates)





def _query_horizons_vector_samples(

    *,

    target_id: str,

    query_times: tuple[datetime, ...],

    location_code: str,

    id_type: str | None = None,

) -> tuple[str, tuple[SolarSystemStateVectorSample, ...]]:

    target_name: str | None = None

    combined_samples: list[SolarSystemStateVectorSample] = []

    for time_chunk in _chunked_datetimes(query_times, _HORIZONS_VECTOR_QUERY_MAX_EPOCHS):

        chunk_target_name, chunk_samples = _query_horizons_vector_samples_chunk(

            target_id=target_id,

            query_times=time_chunk,

            location_code=location_code,

            id_type=id_type,

        )

        if target_name is None:

            target_name = chunk_target_name

        combined_samples.extend(chunk_samples)

    return (target_name or target_id), tuple(combined_samples)





def _query_horizons_ephemeris_magnitude_samples(

    *,

    target_id: str,

    query_times: tuple[datetime, ...],

    location_code: str,

    id_type: str | None = None,

) -> tuple[str, tuple[SolarSystemMagnitudeSample, ...]]:

    target_name: str | None = None

    combined_samples: list[SolarSystemMagnitudeSample] = []

    for time_chunk in _chunked_datetimes(query_times, _HORIZONS_VECTOR_QUERY_MAX_EPOCHS):

        chunk_target_name, chunk_samples = _query_horizons_ephemeris_magnitude_samples_chunk(

            target_id=target_id,

            query_times=time_chunk,

            location_code=location_code,

            id_type=id_type,

        )

        if target_name is None:

            target_name = chunk_target_name

        combined_samples.extend(chunk_samples)

    return (target_name or target_id), tuple(combined_samples)





def _query_major_planet_heliocentric_paths(

    query_times: tuple[datetime, ...],

    *,

    progress_callback: Callable[[str], None] | None = None,

) -> tuple[HeliocentricReferenceBody, ...]:

    cached_bodies = load_cached_major_planet_heliocentric_paths(query_times)

    if cached_bodies is not None:

        if progress_callback is not None:

            progress_callback("Loaded cached heliocentric vectors for major planets.")

        return cached_bodies

    bodies: list[HeliocentricReferenceBody] = []

    for _key, label, target_id in _MAJOR_PLANET_TARGETS:

        if progress_callback is not None:

            progress_callback(f"Querying JPL Horizons heliocentric vectors for {label}.")

        resolved_target_name, samples = _query_horizons_vector_samples(

            target_id=target_id,

            query_times=query_times,

            location_code="500@10",

        )

        bodies.append(

            HeliocentricReferenceBody(

                key=_key,

                label=label,

                resolved_target_name=resolved_target_name,

                path_samples=samples,

                orbital_period_days=_estimate_orbital_period_days(samples),

            )

        )

    cached_result = tuple(bodies)

    _store_cached_major_planet_heliocentric_paths(query_times, cached_result)

    return cached_result



def load_cached_major_planet_heliocentric_paths(

    query_times: tuple[datetime, ...],

) -> tuple[HeliocentricReferenceBody, ...] | None:

    if not query_times:

        return ()

    return _load_cached_major_planet_heliocentric_paths(query_times)



def _major_planet_cache_directory() -> Path:
    return application_cache_dir() / "solar_system"



def _major_planet_query_times_payload(query_times: tuple[datetime, ...]) -> tuple[str, ...]:

    return tuple(normalized_time.astimezone(UTC).isoformat() for normalized_time in query_times)



def _major_planet_cache_path(query_times: tuple[datetime, ...]) -> Path:

    normalized_times = _major_planet_query_times_payload(query_times)

    cache_key = hashlib.sha256("|".join(normalized_times).encode("utf-8")).hexdigest()

    return _major_planet_cache_directory() / f"{_MAJOR_PLANET_CACHE_FILENAME_PREFIX}_{cache_key}.json"



def _serialize_state_vector_sample(sample: SolarSystemStateVectorSample) -> dict[str, object]:

    return {

        "observation_time": sample.observation_time.astimezone(UTC).isoformat(),

        "x_au": float(sample.x_au),

        "y_au": float(sample.y_au),

        "z_au": float(sample.z_au),

        "vx_au_per_day": float(sample.vx_au_per_day),

        "vy_au_per_day": float(sample.vy_au_per_day),

        "vz_au_per_day": float(sample.vz_au_per_day),

    }



def _deserialize_state_vector_sample(payload: dict[str, object]) -> SolarSystemStateVectorSample:

    return SolarSystemStateVectorSample(

        observation_time=datetime.fromisoformat(str(payload["observation_time"])).astimezone(UTC),

        x_au=float(payload["x_au"]),

        y_au=float(payload["y_au"]),

        z_au=float(payload["z_au"]),

        vx_au_per_day=float(payload["vx_au_per_day"]),

        vy_au_per_day=float(payload["vy_au_per_day"]),

        vz_au_per_day=float(payload["vz_au_per_day"]),

    )



def _serialize_reference_body(body: HeliocentricReferenceBody) -> dict[str, object]:

    return {

        "key": body.key,

        "label": body.label,

        "resolved_target_name": body.resolved_target_name,

        "orbital_period_days": None if body.orbital_period_days is None else float(body.orbital_period_days),

        "path_samples": [_serialize_state_vector_sample(sample) for sample in body.path_samples],

    }



def _deserialize_reference_body(payload: dict[str, object]) -> HeliocentricReferenceBody:

    raw_orbital_period_days = payload.get("orbital_period_days")

    return HeliocentricReferenceBody(

        key=str(payload["key"]),

        label=str(payload["label"]),

        resolved_target_name=str(payload["resolved_target_name"]),

        path_samples=tuple(_deserialize_state_vector_sample(sample_payload) for sample_payload in payload.get("path_samples", [])),

        orbital_period_days=None if raw_orbital_period_days is None else float(raw_orbital_period_days),

    )



def _load_cached_major_planet_heliocentric_paths(

    query_times: tuple[datetime, ...],

) -> tuple[HeliocentricReferenceBody, ...] | None:

    cache_path = _major_planet_cache_path(query_times)

    try:

        raw_payload = json.loads(cache_path.read_text(encoding="utf-8"))

    except (OSError, ValueError, TypeError):

        return None

    if int(raw_payload.get("version", 0)) != _MAJOR_PLANET_CACHE_VERSION:

        return None

    created_at_raw = raw_payload.get("created_at")

    if not isinstance(created_at_raw, str):

        return None

    try:

        created_at = datetime.fromisoformat(created_at_raw).astimezone(UTC)

    except ValueError:

        return None

    if (datetime.now(tz=UTC) - created_at) > _MAJOR_PLANET_CACHE_TTL:

        return None

    if tuple(raw_payload.get("query_times", ())) != _major_planet_query_times_payload(query_times):

        return None

    raw_bodies = raw_payload.get("bodies")

    if not isinstance(raw_bodies, list):

        return None

    try:

        return tuple(_deserialize_reference_body(body_payload) for body_payload in raw_bodies)

    except (KeyError, TypeError, ValueError):

        return None



def _store_cached_major_planet_heliocentric_paths(

    query_times: tuple[datetime, ...],

    bodies: tuple[HeliocentricReferenceBody, ...],

) -> None:

    cache_path = _major_planet_cache_path(query_times)

    payload = {

        "version": _MAJOR_PLANET_CACHE_VERSION,

        "created_at": datetime.now(tz=UTC).isoformat(),

        "query_times": list(_major_planet_query_times_payload(query_times)),

        "bodies": [_serialize_reference_body(body) for body in bodies],

    }

    try:

        cache_path.parent.mkdir(parents=True, exist_ok=True)

        cache_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    except OSError:

        return





def _query_horizons_vector_samples_chunk(

    *,

    target_id: str,

    query_times: tuple[datetime, ...],

    location_code: str,

    id_type: str | None = None,

) -> tuple[str, tuple[SolarSystemStateVectorSample, ...]]:

    if Horizons is None:

        raise RuntimeError("JPL Horizons is unavailable.")

    if not query_times:

        raise ValueError("At least one query time is required.")



    epochs = Time([time_point.strftime("%Y-%m-%dT%H:%M:%S") for time_point in query_times], format="isot", scale="utc")

    query_kwargs: dict[str, object] = {

        "id": target_id,

        "location": location_code,

        "epochs": [float(value) for value in np.atleast_1d(epochs.jd)],

    }

    if id_type:

        query_kwargs["id_type"] = id_type



    table = Horizons(**query_kwargs).vectors()

    if table is None or len(table) == 0:

        raise ValueError("No state vectors were returned.")

    if len(table) != len(query_times):

        raise ValueError(f"Expected {len(query_times)} state-vector row(s) but received {len(table)}.")



    target_name = _row_text(table[0], "targetname") or target_id

    samples = tuple(

        SolarSystemStateVectorSample(

            observation_time=query_time,

            x_au=_required_row_float(row, "x"),

            y_au=_required_row_float(row, "y"),

            z_au=_required_row_float(row, "z"),

            vx_au_per_day=_required_row_float(row, "vx"),

            vy_au_per_day=_required_row_float(row, "vy"),

            vz_au_per_day=_required_row_float(row, "vz"),

        )

        for query_time, row in zip(query_times, table, strict=True)

    )

    return target_name, samples





def _query_horizons_ephemeris_magnitude_samples_chunk(

    *,

    target_id: str,

    query_times: tuple[datetime, ...],

    location_code: str,

    id_type: str | None = None,

) -> tuple[str, tuple[SolarSystemMagnitudeSample, ...]]:

    if Horizons is None:

        raise RuntimeError("JPL Horizons is unavailable.")

    if not query_times:

        raise ValueError("At least one query time is required.")



    epochs = Time([time_point.strftime("%Y-%m-%dT%H:%M:%S") for time_point in query_times], format="isot", scale="utc")

    query_kwargs: dict[str, object] = {

        "id": target_id,

        "location": location_code,

        "epochs": [float(value) for value in np.atleast_1d(epochs.jd)],

    }

    if id_type:

        query_kwargs["id_type"] = id_type



    table = Horizons(**query_kwargs).ephemerides()

    if table is None or len(table) == 0:

        raise ValueError("No ephemerides were returned.")

    if len(table) != len(query_times):

        raise ValueError(f"Expected {len(query_times)} ephemeris row(s) but received {len(table)}.")



    target_name = _row_text(table[0], "targetname") or target_id

    samples = tuple(

        SolarSystemMagnitudeSample(

            observation_time=query_time,

            literature_magnitude=magnitude,

        )

        for query_time, row in zip(query_times, table, strict=True)

        for magnitude in [_row_float(row, "V", "Tmag", "APmag", "Nmag", "VMag")]

        if magnitude is not None

    )

    return target_name, samples





def _chunked_datetimes(values: tuple[datetime, ...], chunk_size: int) -> tuple[tuple[datetime, ...], ...]:

    normalized_chunk_size = max(1, int(chunk_size))

    return tuple(

        values[start_index : start_index + normalized_chunk_size]

        for start_index in range(0, len(values), normalized_chunk_size)

    )





def _estimate_orbital_period_days(samples: tuple[SolarSystemStateVectorSample, ...]) -> float | None:

    if not samples:

        return None

    sample = samples[0]

    radius_au = math.sqrt((sample.x_au * sample.x_au) + (sample.y_au * sample.y_au) + (sample.z_au * sample.z_au))

    speed_squared = (sample.vx_au_per_day * sample.vx_au_per_day) + (sample.vy_au_per_day * sample.vy_au_per_day) + (sample.vz_au_per_day * sample.vz_au_per_day)

    if not math.isfinite(radius_au) or radius_au <= 0.0 or not math.isfinite(speed_squared):

        return None

    specific_energy = (0.5 * speed_squared) - (_SUN_GRAVITATIONAL_PARAMETER_AU3_PER_DAY2 / radius_au)

    if not math.isfinite(specific_energy) or specific_energy >= 0.0:

        return None

    semi_major_axis_au = -_SUN_GRAVITATIONAL_PARAMETER_AU3_PER_DAY2 / (2.0 * specific_energy)

    if not math.isfinite(semi_major_axis_au) or semi_major_axis_au <= 0.0:

        return None

    orbital_period_days = 2.0 * math.pi * math.sqrt((semi_major_axis_au ** 3) / _SUN_GRAVITATIONAL_PARAMETER_AU3_PER_DAY2)

    if not math.isfinite(orbital_period_days) or orbital_period_days <= 0.0:

        return None

    return float(orbital_period_days)





def _summarize_horizons_error(error: Exception) -> str:

    message = str(error).strip()

    if " for url:" in message:

        message = message.split(" for url:", 1)[0].strip()

    response = getattr(error, "response", None)

    if response is not None:

        status_code = getattr(response, "status_code", None)

        reason = str(getattr(response, "reason", "") or "").strip()

        if status_code:

            return f"HTTP {status_code}{f' {reason}' if reason else ''}".strip()

    return message or error.__class__.__name__





def _unique_utc_datetimes(values: list[datetime] | tuple[datetime, ...]) -> tuple[datetime, ...]:

    ordered_times: list[datetime] = []

    seen: set[str] = set()

    for value in values:

        normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

        key = normalized.isoformat(timespec="microseconds")

        if key in seen:

            continue

        seen.add(key)

        ordered_times.append(normalized)

    ordered_times.sort()

    return tuple(ordered_times)





def _linspace_datetimes(start_time: datetime, end_time: datetime, count: int) -> tuple[datetime, ...]:

    if count <= 1 or end_time <= start_time:

        return (start_time,)

    total_seconds = (end_time - start_time).total_seconds()

    return tuple(

        start_time + timedelta(seconds=(total_seconds * index / (count - 1)))

        for index in range(count)

    )





def _measure_detection_in_frame_data(

    source_path: Path,

    detection: SolarSystemDetection,

    *,

    wcs: WCS,

    image_data: np.ndarray | None,

    pixel_scale_arcsec_per_pixel: float | None,

    reference_observation_time: datetime,

    observation_time: datetime,

    exposure_seconds: float | None,

    measure_local_match: bool,

) -> SolarSystemFrameMeasurement | None:

    predicted_ra_deg, predicted_dec_deg = predict_detection_coordinates(

        detection,

        reference_observation_time=reference_observation_time,

        observation_time=observation_time,

    )

    predicted_x, predicted_y = wcs.world_to_pixel_values(predicted_ra_deg, predicted_dec_deg)

    if not np.isfinite(predicted_x) or not np.isfinite(predicted_y):

        return None

    expected_trail_length_px = detection.expected_trail_length_px

    if (

        detection.motion_rate_arcsec_per_hour is not None

        and pixel_scale_arcsec_per_pixel is not None

        and pixel_scale_arcsec_per_pixel > 0

        and exposure_seconds is not None

    ):

        expected_trail_length_px = (detection.motion_rate_arcsec_per_hour * max(0.0, float(exposure_seconds)) / 3600.0) / pixel_scale_arcsec_per_pixel

    measured_x = None

    measured_y = None

    measured_ra_deg = None

    measured_dec_deg = None

    match_offset_px = None

    local_snr = None

    local_peak_value = None

    local_flux = None

    local_fwhm_px = None

    if measure_local_match:

        if image_data is None:

            return None

        measured_x, measured_y, match_offset_px, local_snr, local_peak_value, local_flux, local_fwhm_px = _measure_local_match(

            image_data,

            float(predicted_x),

            float(predicted_y),

            expected_trail_length_px=expected_trail_length_px,

        )

        if measured_x is not None and measured_y is not None:

            try:

                measured_ra_deg, measured_dec_deg = wcs.pixel_to_world_values(float(measured_x), float(measured_y))

            except Exception:

                measured_ra_deg = None

                measured_dec_deg = None

            else:

                if not (np.isfinite(measured_ra_deg) and np.isfinite(measured_dec_deg)):

                    measured_ra_deg = None

                    measured_dec_deg = None

                else:

                    measured_ra_deg = float(measured_ra_deg)

                    measured_dec_deg = float(measured_dec_deg)

    likely_visible = bool(local_snr is not None and local_snr >= 4.0 and match_offset_px is not None and match_offset_px <= 8.0)

    return SolarSystemFrameMeasurement(

        source_path=source_path,

        observation_time=observation_time,

        predicted_ra_deg=predicted_ra_deg,

        predicted_dec_deg=predicted_dec_deg,

        predicted_x=float(predicted_x),

        predicted_y=float(predicted_y),

        expected_trail_length_px=expected_trail_length_px,

        measured_x=measured_x,

        measured_y=measured_y,

        measured_ra_deg=measured_ra_deg,

        measured_dec_deg=measured_dec_deg,

        match_offset_px=match_offset_px,

        local_snr=local_snr,

        local_peak_value=local_peak_value,

        local_flux=local_flux,

        local_fwhm_px=local_fwhm_px,

        likely_visible=likely_visible,

    )





def measure_detections_in_frame(

    source_path: Path,

    detections: list[SolarSystemDetection],

    *,

    reference_observation_time: datetime,

    observation_time: datetime,

    exposure_seconds: float | None,

    measure_local_match: bool = True,

    fallback_solved_field: SolvedField | None = None,

) -> list[SolarSystemFrameMeasurement | None]:

    if not detections:

        return []

    header = read_header(source_path)

    wcs = WCS(header)

    if not getattr(wcs, "has_celestial", False):

        if fallback_solved_field is None:

            return [None] * len(detections)

        wcs = _resolved_wcs_for_field(fallback_solved_field)

        if not getattr(wcs, "has_celestial", False):

            return [None] * len(detections)

    image_data = _collapsed_image_plane(read_photometry_image_data(source_path)) if measure_local_match else None

    pixel_scale_arcsec_per_pixel = _pixel_scale_arcsec_per_pixel(wcs)

    measurements: list[SolarSystemFrameMeasurement | None] = []

    for detection in detections:

        try:

            measurement = _measure_detection_in_frame_data(

                source_path,

                detection,

                wcs=wcs,

                image_data=image_data,

                pixel_scale_arcsec_per_pixel=pixel_scale_arcsec_per_pixel,

                reference_observation_time=reference_observation_time,

                observation_time=observation_time,

                exposure_seconds=exposure_seconds,

                measure_local_match=measure_local_match,

            )

        except Exception:

            if measure_local_match:

                try:

                    measurement = _measure_detection_in_frame_data(

                        source_path,

                        detection,

                        wcs=wcs,

                        image_data=None,

                        pixel_scale_arcsec_per_pixel=pixel_scale_arcsec_per_pixel,

                        reference_observation_time=reference_observation_time,

                        observation_time=observation_time,

                        exposure_seconds=exposure_seconds,

                        measure_local_match=False,

                    )

                except Exception:

                    measurement = None

            else:

                measurement = None

        measurements.append(measurement)

    return measurements





def measure_detection_in_frame(

    source_path: Path,

    detection: SolarSystemDetection,

    *,

    reference_observation_time: datetime,

    observation_time: datetime,

    exposure_seconds: float | None,

    measure_local_match: bool = True,

    fallback_solved_field: SolvedField | None = None,

) -> SolarSystemFrameMeasurement | None:

    measurements = measure_detections_in_frame(

        source_path,

        [detection],

        reference_observation_time=reference_observation_time,

        observation_time=observation_time,

        exposure_seconds=exposure_seconds,

        measure_local_match=measure_local_match,

        fallback_solved_field=fallback_solved_field,

    )

    return measurements[0] if measurements else None





def parse_observation_time(value: str, *, fallback_timezone: str = "UTC") -> datetime:

    text = value.strip()

    if not text:

        raise ValueError("Observation time is required.")

    normalized = text.replace("Z", "+00:00")

    parsed = datetime.fromisoformat(normalized)

    if parsed.tzinfo is None:

        try:

            timezone = ZoneInfo(fallback_timezone)

        except ZoneInfoNotFoundError:

            timezone = UTC

        return parsed.replace(tzinfo=timezone).astimezone(UTC)

    return parsed.astimezone(UTC)





def _collapsed_image_plane(image_data: np.ndarray) -> np.ndarray:

    data = np.asarray(image_data, dtype=float)

    if data.ndim == 2:

        return data

    if data.ndim == 3 and data.shape[-1] in {1, 3, 4}:

        return np.nanmean(data, axis=-1)

    if data.ndim == 3 and data.shape[0] in {1, 3, 4}:

        return np.nanmean(data, axis=0)

    raise ValueError("Solar-system mode currently supports grayscale or RGB-style images only.")





def _pixel_scale_arcsec_per_pixel(wcs: WCS) -> float | None:

    try:

        scales = proj_plane_pixel_scales(wcs) * u.deg

    except Exception:

        return None

    if scales.size == 0:

        return None

    pixel_scale_deg = float(np.nanmean(scales.to_value(u.deg)))

    if not np.isfinite(pixel_scale_deg) or pixel_scale_deg <= 0:

        return None

    return pixel_scale_deg * 3600.0





def _motion_rate_arcsec_per_hour(ra_rate_arcsec_per_hour: float | None, dec_rate_arcsec_per_hour: float | None) -> float | None:

    if ra_rate_arcsec_per_hour is None and dec_rate_arcsec_per_hour is None:

        return None

    return float(math.hypot(float(ra_rate_arcsec_per_hour or 0.0), float(dec_rate_arcsec_per_hour or 0.0)))





def _classify_prediction(type_text: str | None, name_text: str | None) -> tuple[str, str]:

    normalized_type = (type_text or "").strip().lower()

    normalized_name = " ".join((name_text or "").strip().lower().split())

    has_comet_identifier = bool(_COMET_IDENTIFIER_PATTERN.search(normalized_name))

    has_interstellar_identifier = bool(_INTERSTELLAR_IDENTIFIER_PATTERN.search(normalized_name))

    if has_comet_identifier or "comet" in normalized_type:

        if "interstellar" in normalized_type or has_interstellar_identifier:

            return "Comet", "interstellar"

        return "Comet", "comet"

    if "interstellar" in normalized_type or has_interstellar_identifier:

        return "Comet", "interstellar"

    if "trojan" in normalized_type:

        return "Asteroid", "trojan"

    if any(token in normalized_type for token in ("neo", "near-earth", "aten", "apollo", "amor")):

        return "Asteroid", "near-Earth"

    if normalized_type.startswith("mb") or "main" in normalized_type:

        return "Asteroid", "main-belt"

    if normalized_type:

        return "Asteroid", type_text or "minor body"

    return "Asteroid", "minor body"





def _row_designation_text(row: Row) -> str | None:

    text = _row_text(row, "Number")

    if text in {None, "--", "-1"}:

        return None

    return text





def _prediction_display_name(name_text: str | None, designation_text: str | None) -> str:

    name = (name_text or "").strip()

    designation = (designation_text or "").strip()

    if not designation:

        return name or "Unknown Object"

    normalized_name = name.lower()

    normalized_designation = designation.lower()

    if not name:

        return designation

    if normalized_name == normalized_designation:

        return name

    if normalized_name.startswith(f"{normalized_designation}/") or normalized_name.startswith(f"({normalized_designation})"):

        return name

    if designation.isdigit():

        return f"({designation}) {name}"

    return f"{designation}/{name}" if "/" not in name else name





def _row_text(row: Row, *column_names: str) -> str | None:

    for column_name in column_names:

        if column_name not in row.colnames:

            continue

        value = row[column_name]

        if getattr(value, "mask", False):

            continue

        text = str(value).strip()

        if not text or text == "--":

            continue

        return text

    return None





def _row_float(row: Row, *column_names: str) -> float | None:

    for column_name in column_names:

        if column_name not in row.colnames:

            continue

        value = row[column_name]

        if getattr(value, "mask", False):

            continue

        if hasattr(value, "to_value"):

            try:

                numeric_value = float(value.to_value())

            except (TypeError, ValueError):

                continue

            if np.isfinite(numeric_value):

                return numeric_value

        try:

            numeric_value = float(value)

        except (TypeError, ValueError):

            continue

        if np.isfinite(numeric_value):

            return numeric_value

    return None





def _required_row_float(row: Row, *column_names: str) -> float:

    value = _row_float(row, *column_names)

    if value is None:

        joined_names = ", ".join(column_names)

        raise ValueError(f"SkyBoT result row is missing required numeric column(s): {joined_names}")

    return value
