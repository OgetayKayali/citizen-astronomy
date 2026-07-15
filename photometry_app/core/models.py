from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


NEAR_SATURATION_FRACTION = 0.95


class WcsStatus(StrEnum):
    SOLVED = "solved"
    UNSOLVED = "unsolved"
    INVALID = "invalid"


class VariableStarLimitMode(StrEnum):
    PERCENT = "percent"
    COUNT = "count"


class VariableStarDesignationFamily(StrEnum):
    NAMED = "named"
    GAIA = "gaia"
    ASASSN = "asassn"
    ATLAS = "atlas"
    ZTF = "ztf"
    OTHER = "other"


class PhotometryApertureMode(StrEnum):
    FIXED = "fixed"
    FWHM_SCALED = "fwhm_scaled"


class ObjectPhotometryMode(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


class AppMode(StrEnum):
    DIFFERENTIAL_PHOTOMETRY = "differential_photometry"
    HR_DIAGRAM = "hr_diagram"
    ASTEROID_COMET_DETECTION = "asteroid_comet_detection"
    ASTROSTACK = "astrostack"
    TRANSIENT_FINDER = "transient_finder"
    SKY_VIEW = "sky_view"
    SKY_EXPLORER = "sky_explorer"
    DISTANCE_MAP = "distance_map"
    ASTRO_TOOLS = "astro_tools"


class RecenterMode(StrEnum):
    NONE = "none"
    CENTROID = "centroid"
    CENTROID_LIMITED = "centroid_limited"


class ManualSourceRole(StrEnum):
    TARGET = "target"
    COMPARISON = "comparison"
    CHECK = "check"


@dataclass(slots=True)
class ObservationMetadata:
    date_obs: datetime | None
    filter_name: str | None
    exposure_seconds: float | None
    width: int | None
    height: int | None
    object_name: str | None
    frame_type: str | None = None
    binning: str | None = None


@dataclass(slots=True)
class FileScanResult:
    path: Path
    object_folder: str
    metadata: ObservationMetadata
    wcs_status: WcsStatus
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ObjectScanSummary:
    object_name: str
    files: list[FileScanResult] = field(default_factory=list)

    @property
    def solved_count(self) -> int:
        return sum(item.wcs_status == WcsStatus.SOLVED for item in self.files)

    @property
    def unsolved_count(self) -> int:
        return sum(item.wcs_status == WcsStatus.UNSOLVED for item in self.files)

    @property
    def invalid_count(self) -> int:
        return sum(item.wcs_status == WcsStatus.INVALID for item in self.files)


@dataclass(slots=True)
class ScanReport:
    root_path: Path
    object_summaries: list[ObjectScanSummary]

    @property
    def total_files(self) -> int:
        return sum(len(summary.files) for summary in self.object_summaries)


@dataclass(slots=True)
class SolvedField:
    center_ra_deg: float
    center_dec_deg: float
    radius_deg: float
    width: int
    height: int
    wcs_path: Path


@dataclass(slots=True)
class PlateSolveResult:
    source_path: Path
    status: WcsStatus
    solved_field: SolvedField | None
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CatalogStar:
    catalog: str
    source_id: str
    name: str
    ra_deg: float
    dec_deg: float
    magnitude: float | None
    is_variable: bool
    object_type: str = "star"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FieldCatalog:
    center_ra_deg: float
    center_dec_deg: float
    radius_deg: float
    gaia_stars: list[CatalogStar] = field(default_factory=list)
    variable_stars: list[CatalogStar] = field(default_factory=list)
    exoplanets: list[CatalogStar] = field(default_factory=list)


@dataclass(slots=True)
class PhotometryMeasurement:
    source_id: str
    source_name: str
    catalog: str
    object_name: str
    file_path: Path
    observation_time: datetime | None
    filter_name: str | None
    ra_deg: float
    dec_deg: float
    x: float
    y: float
    flux: float | None
    flux_error: float | None
    instrumental_magnitude: float | None
    differential_magnitude: float | None
    is_variable: bool
    is_reference: bool
    catalog_magnitude: float | None = None
    calibrated_magnitude: float | None = None
    calibrated_magnitude_error: float | None = None
    zero_point_magnitude: float | None = None
    zero_point_magnitude_error: float | None = None
    zero_point_source_count: int = 0
    is_check: bool = False
    flags: list[str] = field(default_factory=list)
    comparison_source_ids: list[str] = field(default_factory=list)
    comparison_source_names: list[str] = field(default_factory=list)
    comparison_reference_flux: float | None = None
    aperture_radius: float | None = None
    annulus_inner_radius: float | None = None
    annulus_outer_radius: float | None = None
    differential_magnitude_error: float | None = None
    snr: float | None = None
    background_level: float | None = None
    background_scatter: float | None = None
    centroid_shift_pixels: float | None = None
    peak_pixel_value: float | None = None
    saturation_threshold: float | None = None
    saturated_pixel_count: int = 0
    is_saturated: bool = False
    comparison_scatter: float | None = None
    quality_score: float = 1.0
    quality_weight: float | None = None
    excluded_from_analysis: bool = False
    exclusion_reasons: list[str] = field(default_factory=list)

    @property
    def saturation_fraction(self) -> float | None:
        if self.peak_pixel_value is None or self.saturation_threshold is None:
            return None
        if self.saturation_threshold <= 0:
            return None
        return float(self.peak_pixel_value) / float(self.saturation_threshold)

    @property
    def is_near_saturated(self) -> bool:
        saturation_fraction = self.saturation_fraction
        return bool(
            not self.is_saturated
            and saturation_fraction is not None
            and saturation_fraction >= NEAR_SATURATION_FRACTION
        )

    @property
    def saturation_status(self) -> str:
        if self.is_saturated:
            return "Saturated"
        if self.is_near_saturated:
            saturation_fraction = self.saturation_fraction
            if saturation_fraction is not None:
                return f"Near ({saturation_fraction * 100.0:.0f}%)"
            return "Near"
        return "OK"


@dataclass(slots=True)
class ScienceObservation:
    object_name: str
    source_id: str
    source_name: str
    role: str
    catalog: str
    filter_name: str
    observation_time_utc: str
    julian_date_utc: float | None
    file_path: str
    measurement_kind: str
    reported_value: float | None
    reported_uncertainty: float | None
    differential_magnitude: float | None
    differential_magnitude_error: float | None
    instrumental_magnitude: float | None
    flux: float | None
    flux_error: float | None
    snr: float | None
    quality_score: float
    quality_weight: float | None
    excluded_from_analysis: bool
    exclusion_reasons: str
    flags: str
    is_saturated: bool
    saturation_status: str
    peak_pixel_value: float | None
    saturation_threshold: float | None
    saturated_pixel_count: int
    background_level: float | None
    background_scatter: float | None
    centroid_shift_pixels: float | None
    aperture_radius: float | None
    annulus_inner_radius: float | None
    annulus_outer_radius: float | None
    calibration_state: str
    calibrated_magnitude: float | None
    zero_point_magnitude: float | None
    zero_point_source_count: int
    check_source_id: str
    check_source_name: str
    check_reference_magnitude: float | None
    check_calibrated_magnitude: float | None
    check_residual_magnitude: float | None
    check_residual_rms: float | None
    transformed_requested: bool
    aavso_mtype_candidate: str
    comparison_source_ids: str
    comparison_source_names: str
    comparison_source_count: int
    comparison_method: str
    comparison_reference_flux: float | None
    comparison_scatter: float | None
    ra_deg: float
    dec_deg: float
    x: float
    y: float

    @property
    def accepted_for_science_export(self) -> bool:
        return not self.excluded_from_analysis and not self.is_saturated and self.reported_value is not None

    @classmethod
    def field_names(cls) -> list[str]:
        return [
            "object_name",
            "source_id",
            "source_name",
            "role",
            "catalog",
            "filter_name",
            "observation_time_utc",
            "julian_date_utc",
            "file_path",
            "measurement_kind",
            "reported_value",
            "reported_uncertainty",
            "differential_magnitude",
            "differential_magnitude_error",
            "instrumental_magnitude",
            "flux",
            "flux_error",
            "snr",
            "quality_score",
            "quality_weight",
            "excluded_from_analysis",
            "exclusion_reasons",
            "flags",
            "is_saturated",
            "saturation_status",
            "peak_pixel_value",
            "saturation_threshold",
            "saturated_pixel_count",
            "background_level",
            "background_scatter",
            "centroid_shift_pixels",
            "aperture_radius",
            "annulus_inner_radius",
            "annulus_outer_radius",
            "calibration_state",
            "calibrated_magnitude",
            "zero_point_magnitude",
            "zero_point_source_count",
            "check_source_id",
            "check_source_name",
            "check_reference_magnitude",
            "check_calibrated_magnitude",
            "check_residual_magnitude",
            "check_residual_rms",
            "transformed_requested",
            "aavso_mtype_candidate",
            "comparison_source_ids",
            "comparison_source_names",
            "comparison_source_count",
            "comparison_method",
            "comparison_reference_flux",
            "comparison_scatter",
            "ra_deg",
            "dec_deg",
            "x",
            "y",
        ]

    def to_export_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.field_names()}


@dataclass(slots=True)
class ManualSourceConfig:
    source_id: str
    name: str
    role: ManualSourceRole
    ra_deg: float
    dec_deg: float
    reference_frame_name: str
    reference_x: float
    reference_y: float
    aperture_radius: float
    annulus_inner_radius: float
    annulus_outer_radius: float
    catalog: str = "manual"


@dataclass(slots=True)
class ManualPhotometryConfig:
    object_name: str
    mode: ObjectPhotometryMode = ObjectPhotometryMode.AUTO
    reference_frame_name: str | None = None
    keep_comparison_stars: bool = True
    recenter_mode: RecenterMode = RecenterMode.CENTROID
    max_recenter_radius_pixels: float = 6.0
    fallback_to_wcs_on_centroid_failure: bool = True
    sources: list[ManualSourceConfig] = field(default_factory=list)

    @property
    def target_source(self) -> ManualSourceConfig | None:
        return next((item for item in self.sources if item.role == ManualSourceRole.TARGET), None)

    @property
    def comparison_sources(self) -> list[ManualSourceConfig]:
        return [item for item in self.sources if item.role == ManualSourceRole.COMPARISON]

    @property
    def check_source(self) -> ManualSourceConfig | None:
        return next((item for item in self.sources if item.role == ManualSourceRole.CHECK), None)

    @property
    def measured_sources(self) -> list[ManualSourceConfig]:
        return [item for item in self.sources if item.role != ManualSourceRole.COMPARISON]


@dataclass(slots=True)
class AperturePreset:
    name: str
    aperture_radius: float
    annulus_inner_radius: float
    annulus_outer_radius: float
    recenter_mode: RecenterMode
    max_recenter_radius_pixels: float = 6.0
    fallback_to_wcs_on_centroid_failure: bool = True
    comparison_source_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LightCurvePoint:
    observation_time: datetime | None
    file_path: Path
    differential_magnitude: float | None
    instrumental_magnitude: float | None
    flux: float | None
    flux_error: float | None
    calibrated_magnitude: float | None = None
    calibrated_magnitude_error: float | None = None
    comparison_reference_flux: float | None = None
    differential_magnitude_error: float | None = None
    quality_score: float = 1.0
    quality_weight: float | None = None
    excluded_from_analysis: bool = False
    exclusion_reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LightCurveSeries:
    object_name: str
    source_id: str
    source_name: str
    filter_name: str
    points: list[LightCurvePoint] = field(default_factory=list)
    candidate_score: float = 0.0
    variability_metrics: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class ProcessingReport:
    object_name: str
    files_processed: int
    solved_files: int
    field_catalog: FieldCatalog | None
    total_variable_stars_found: int = 0
    variable_star_limit_mode: VariableStarLimitMode = VariableStarLimitMode.PERCENT
    variable_star_limit_value: int = 100
    variable_star_designation_filters: list[VariableStarDesignationFamily] = field(default_factory=lambda: list(VariableStarDesignationFamily))
    reference_stars: list[CatalogStar] = field(default_factory=list)
    measurements: list[PhotometryMeasurement] = field(default_factory=list)
    light_curves: list[LightCurveSeries] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    file_notes: dict[str, list[str]] = field(default_factory=dict)
    photometry_mode: ObjectPhotometryMode = ObjectPhotometryMode.AUTO
    manual_config: ManualPhotometryConfig | None = None

    @property
    def variable_star_selection_label(self) -> str:
        if self.variable_star_limit_mode == VariableStarLimitMode.COUNT:
            return f"top {self.variable_star_limit_value}"
        return f"{self.variable_star_limit_value}%"


@dataclass(slots=True)
class VariableSelectionPreview:
    object_name: str
    total_variable_stars_found: int
    analyzed_variable_star_count: int
    variable_star_limit_mode: VariableStarLimitMode
    variable_star_limit_value: int
    variable_star_designation_filters: list[VariableStarDesignationFamily] = field(default_factory=lambda: list(VariableStarDesignationFamily))
    candidate_sources: list[CatalogStar] = field(default_factory=list)
    preselected_source_keys: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def variable_star_selection_label(self) -> str:
        if self.variable_star_limit_mode == VariableStarLimitMode.COUNT:
            return f"top {self.variable_star_limit_value}"
        return f"{self.variable_star_limit_value}%"


@dataclass(slots=True)
class RunHistoryEntry:
    timestamp_utc: datetime
    object_name: str
    files_processed: int
    solved_files: int
    measurement_count: int
    light_curve_count: int
    notes: list[str] = field(default_factory=list)

