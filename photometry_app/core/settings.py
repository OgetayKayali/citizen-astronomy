from __future__ import annotations



import json

import os

import uuid

from dataclasses import dataclass

from pathlib import Path



from photometry_app.core.models import (

    AppMode,

    AperturePreset,

    ManualPhotometryConfig,

    ManualSourceConfig,

    ManualSourceRole,

    ObjectPhotometryMode,

    PhotometryApertureMode,

    RecenterMode,

    VariableStarDesignationFamily,

    VariableStarLimitMode,

)


def resolve_shared_parallel_workers(settings: AppSettings | None) -> int:
    if settings is None:
        return 0
    configured_workers = max(0, int(getattr(settings, "shared_parallel_workers", 0)))
    if configured_workers > 0:
        return configured_workers
    return max(
        max(0, int(getattr(settings, "photometry_parallel_workers", 0))),
        max(0, int(getattr(settings, "calculate_period_parallel_workers", 0))),
        max(0, int(getattr(settings, "literature_period_parallel_workers", 0))),
    )


def resolve_astrostack_parallel_workers(settings: AppSettings | None) -> int:
    return resolve_shared_parallel_workers(settings)





_DEFAULT_CUSTOM_THEME_COLORS = {

    "window_bg": "#2b2d31",

    "panel_bg": "#1f2125",

    "text": "#f0f2f5",

    "menu_bg": "#2b2d31",

    "menu_text": "#f0f2f5",

    "accent": "#3d8bfd",

    "plot_bg": "#181a1f",

    "plot_axis": "#d8dee9",

    "plot_points": "#2f81f7",

    "plot_fit": "#ff9f1c",

    "ra_grid": "#4fc3f7",

    "dec_grid": "#ffd166",

    "asteroid_other_overlay_circle_color": "#fbbf24",

    "asteroid_other_overlay_line_color": "#f8fafc",

    "asteroid_other_overlay_text_color": "#f8fafc",

    "asteroid_other_overlay_line_width": "1.75",

    "asteroid_other_overlay_text_size": "10.0",

    "asteroid_overlay_circle_color": "#38bdf8",

    "asteroid_overlay_line_color": "#8b5cf6",

    "asteroid_overlay_text_color": "#f8fafc",

    "asteroid_overlay_line_width": "1.5",

    "asteroid_overlay_text_size": "10.0",

}



_VALID_THEME_NAMES = {"normal", "dark", "dracula", "nord", "tokyo-night", "gruvbox", "catppuccin", "solarized-dark", "one-dark", "custom"}
_DEFAULT_THEME = "gruvbox"

_APP_STATE_FILE_NAME = "state.json"

_SETTINGS_FILE_NAME = "settings.json"

_SETTINGS_CONFIG_PATH_KEY = "settings_config_path"

_ANNOTATED_IMAGE_STRETCH_MODES = {"linear", "asinh", "stf", "sqrt", "log"}

_HR_MARKER_SIZE_MODES = {"scaled", "fixed"}

_SYNTHETIC_TRACKING_COMBINE_MODES = {"mean", "sigma_clipped_mean"}
_SYNTHETIC_TRACKING_INTEGRATION_MODES = {"average", "mean", "min", "max"}
_SYNTHETIC_TRACKING_WEIGHT_MODES = {"psf_signal_weight", "psf_snr", "snr", "average_signal_strength"}
_SYNTHETIC_TRACKING_REJECTION_MODES = {"no_rejection", "min_max", "sigma_clipping", "winsorized_sigma_clipping", "averaged_sigma_clipping"}
_SYNTHETIC_TRACKING_BACKEND_PREFERENCES = {"auto", "cpu", "gpu"}

_DISCOVERY_SYNTHETIC_SWEEP_DIRECTION_FOCUS_MODES = {"all_directions", "main_belt"}


def _read_json_payload(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig"))

_SKY_EXPLORER_QUERY_LAYER_KEYS: tuple[str, ...] = (
    "deep_sky",
    "general_objects",
    "solar_system",
    "variable_stars",
    "gaia_stars",
    "exoplanets",
)


def default_sky_explorer_enabled_layers() -> tuple[str, ...]:
    return _SKY_EXPLORER_QUERY_LAYER_KEYS

_HR_MOTION_GROUP_PRESET_DEFAULTS = {

    "default": ("auto", 1.0, "auto", True),

    "tight": ("auto", 0.8, "auto", True),

    "loose": ("auto", 1.35, "never", False),

    "parallax": ("auto", 0.95, "always", True),

}

_HR_MOTION_GROUP_METHODS = {"auto", "lightweight", "sklearn"}

_HR_MOTION_GROUP_PARALLAX_MODES = {"auto", "always", "never"}





@dataclass(slots=True)
class ObservingSitePreset:

    name: str

    latitude_deg: float

    longitude_deg: float

    elevation_m: float | None = None


@dataclass(slots=True)
class SkyAtlasCustomOverlayRecord:
    overlay_id: str
    display_name: str
    cached_image_path: str
    cached_wcs_path: str
    source_image_path: str
    width: int
    height: int


@dataclass(slots=True)
class SkyAtlasCustomOverlaySurvey:
    survey_id: str
    name: str
    filter_name: str
    overlays: list[SkyAtlasCustomOverlayRecord]


@dataclass(slots=True)

class AppSettings:

    astrometry_api_key: str | None

    cache_dir: Path

    config_path: Path

    assume_aligned_images: bool

    nearby_reference_count: int

    photometry_aperture_mode: PhotometryApertureMode

    aperture_radius_pixels: float

    annulus_inner_radius_pixels: float

    annulus_outer_radius_pixels: float

    aperture_radius_fwhm_scale: float

    annulus_inner_radius_fwhm_scale: float

    annulus_outer_radius_fwhm_scale: float

    variable_star_limit_mode: VariableStarLimitMode

    variable_star_limit_value: int

    variable_star_designation_filters: list[VariableStarDesignationFamily]

    shared_parallel_workers: int = 0

    astrostack_parallel_workers: int = 0

    photometry_parallel_workers: int = 0

    calculate_period_parallel_workers: int = 0

    literature_period_parallel_workers: int = 0

    snr_binning_max_period_fraction: float = 0.03

    snr_binning_max_absolute_duration_seconds: float = 600.0

    snr_binning_target_snr: float = 30.0

    snr_binning_max_frames_per_bin: int = 15

    snr_binning_min_frames_per_bin: int = 1

    snr_binning_type_aware_thresholds: bool = True

    snr_binning_sharp_period_fraction: float = 0.015

    snr_binning_smooth_period_fraction: float = 0.05

    snr_binning_weighted_flux_binning: bool = True

    snr_binning_allow_magnitude_fallback: bool = True

    snr_binning_minimum_valid_points_per_bin: int = 2

    snr_binning_outlier_rejection_enabled: bool = False

    snr_binning_sigma_clip_threshold: float = 3.5

    snr_binning_dataset_mode: str = "derived"

    snr_binning_apply_to_selected_measurements_only: bool = False

    snr_binning_allow_periodless_fallback: bool = False

    comparison_fit_stop_match_index: float = 95.0

    comparison_fit_parallel_workers: int = 0

    asteroid_search_parallel_workers: int = 0

    asteroid_discovery_min_residual_snr: float = 0.0

    asteroid_discovery_max_residual_snr: float = 0.0

    asteroid_discovery_frames_per_batch: int = 0

    asteroid_discovery_binning_factor: int = 1
    asteroid_discovery_assume_aligned: bool = False
    asteroid_discovery_single_batch_only: bool = False
    asteroid_discovery_min_seed_displacement_px: float = 1.5
    asteroid_discovery_motion_prior_bias: str = "balanced"
    asteroid_discovery_retry_with_detailed_search: bool = False

    asteroid_discovery_use_temporary_cache: bool = False


    synthetic_tracking_advanced_enabled: bool = False
    asteroid_discovery_min_candidate_frames: int = 3

    asteroid_discovery_detection_sigma: float = 5.0

    asteroid_discovery_detection_fwhm: float = 3.0

    asteroid_discovery_max_residuals_per_frame: int = 24

    asteroid_discovery_edge_margin_px: int = 6

    asteroid_discovery_detector_mode: str = "hybrid"

    asteroid_discovery_streak_min_area_px: int = 6

    asteroid_discovery_streak_min_elongation: float = 1.8

    asteroid_discovery_potential_deflection_rms_px: float = 0.9

    asteroid_discovery_review_deflection_rms_px: float = 1.8

    asteroid_discovery_enable_synthetic_sweep: bool = False

    asteroid_discovery_synthetic_sweep_max_motion_px_per_hour: float = 12.0

    asteroid_discovery_synthetic_sweep_motion_step_px_per_hour: float = 1.0

    asteroid_discovery_synthetic_sweep_angle_step_deg: float = 30.0

    asteroid_discovery_synthetic_sweep_direction_focus: str = "all_directions"

    asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg: float = 45.0

    asteroid_discovery_synthetic_sweep_min_stacked_snr: float = 6.0

    asteroid_discovery_synthetic_sweep_save_stacks: bool = False

    comparison_fit_allow_multiple_targets: bool = False

    comparison_fit_eclipsing_binary_match_tolerance: float = 2.0

    comparison_fit_fallback_candidate_pool_size: int = 8

    comparison_fit_fallback_magnitude_tolerance: float = 0.75

    discovery_max_candidate_count: int = 60

    discovery_min_magnitude: float | None = 10.0

    discovery_max_magnitude: float | None = 15.5

    discovery_min_candidate_score: float = 25.0

    light_curve_scientific_export_enabled: bool = True

    scientific_light_curve_pdf_dpi: int = 300

    scientific_light_curve_pdf_paper_size: str = "Letter"

    calibration_bias_path: str = ""

    calibration_dark_path: str = ""

    calibration_flat_path: str = ""

    hr_max_sources: int = 5000

    hr_table_row_limit: int = 1000

    hr_motion_vector_color: str = "#3d8bfd"

    hr_motion_vector_color_by_angle: bool = False

    hr_motion_vector_saturation_by_magnitude: bool = False

    hr_motion_vector_width: float = 1.5

    hr_roi_drag_color: str = "#ff9f1c"

    hr_roi_color: str = "#2dd4bf"

    hr_selection_circle_color: str = "#ffd166"

    hr_selection_circle_opacity: float = 0.85

    hr_selection_circle_size_factor: float = 1.35

    hr_plot_hide_flagged: bool = False

    hr_plot_hide_saturated: bool = True

    hr_search_catalog_names: bool = True

    hr_search_catalog_names_magnitude_threshold: float = 9.0

    hr_plot_apparent_magnitude_min: float = -5.0

    hr_plot_apparent_magnitude_max: float = 30.0

    hr_plot_color_saturation: float = 1.0

    hr_plot_point_opacity: float = 0.8

    hr_plot_x_log_scale: bool = False

    hr_plot_marker_size_mode: str = "scaled"

    hr_plot_fixed_marker_size: float = 8.0

    hr_plot_require_parallax: bool = True

    hr_motion_group_preset: str = "default"

    hr_motion_group_method: str = "auto"

    hr_motion_group_strictness: float = 1.0

    hr_motion_group_parallax_mode: str = "auto"

    hr_motion_group_refine_hr_consistency: bool = True

    hr_motion_group_auto_filter: bool = False

    hr_left_splitter_sizes: list[int] | None = None

    hr_main_splitter_sizes: list[int] | None = None

    differential_analysis_splitter_sizes: list[int] | None = None

    differential_results_splitter_sizes: list[int] | None = None

    differential_main_splitter_sizes: list[int] | None = None

    asteroid_main_splitter_sizes: list[int] | None = None

    asteroid_results_splitter_sizes: list[int] | None = None

    sky_explorer_left_splitter_sizes: list[int] | None = None

    sky_explorer_results_splitter_sizes: list[int] | None = None

    sky_explorer_main_splitter_sizes: list[int] | None = None

    sky_explorer_object_type_column_widths: list[int] | None = None

    sky_explorer_results_column_widths: list[int] | None = None

    sky_explorer_simbad_search_radius_arcsec: float = 10.0

    sky_explorer_gaia_max_magnitude: float = 17.0

    sky_explorer_gaia_hard_cap_enabled: bool = False

    sky_explorer_gaia_hard_cap_rows: int = 1000

    sky_explorer_mag_limit_examples_per_bin: int = 1

    sky_explorer_mag_limit_marker_color: str = "#3d8bfd"

    sky_explorer_mag_limit_marker_stroke_color: str = "#111827"

    sky_explorer_mag_limit_marker_stroke_width: float = 2.0

    sky_explorer_mag_limit_target_size: float = 6.0

    sky_explorer_mag_limit_text_color: str = "#111827"

    sky_explorer_mag_limit_text_stroke_color: str = "#ffffff"

    sky_explorer_mag_limit_text_stroke_width: float = 0.0

    sky_explorer_mag_limit_text_size: float = 9.0

    sky_explorer_hide_objects_without_magnitude: bool = False

    sky_explorer_scale_extended_nebulae: bool = True

    sky_explorer_scale_overlay_strokes: bool = True

    sky_explorer_marker_color_relation: str = "stroke_dark_fill_bright"

    sky_explorer_text_color_relation: str = "dark"

    sky_explorer_annotated_galaxy_max_magnitude_enabled: bool = False

    sky_explorer_annotated_galaxy_max_magnitude: float = 17.0

    sky_explorer_annotated_galaxy_require_shape_metadata: bool = False

    sky_explorer_enabled_layers: tuple[str, ...] = _SKY_EXPLORER_QUERY_LAYER_KEYS

    sky_explorer_fill_opacity: float = 0.25

    sky_explorer_stroke_opacity: float = 1.0

    sky_explorer_object_group_color_overrides: dict[str, str] | None = None

    sky_explorer_object_type_color_overrides: dict[str, tuple[str, str]] | None = None

    sky_explorer_object_type_text_color_overrides: dict[str, str] | None = None

    sky_explorer_object_type_font_overrides: dict[str, str] | None = None

    distance_map_max_magnitude: float = 17.0

    distance_map_max_distance_pc: float = 500.0

    distance_map_max_stars: int = 500

    distance_map_min_parallax_snr: float = 5.0

    distance_map_limit_to_image_footprint: bool = True

    distance_map_cluster_preset: str = "default"

    distance_map_cluster_method: str = "auto"

    distance_map_cluster_strictness: float = 1.0

    distance_map_cluster_parallax_mode: str = "auto"

    distance_map_cluster_refine_magnitude_consistency: bool = True

    distance_map_cluster_auto_filter: bool = False

    distance_map_cluster_mark_on_image: bool = True

    distance_map_model_cluster_depth: bool = True

    distance_map_show_parallax_uncertainty: bool = False

    distance_map_use_external_cluster_catalog: bool = False

    frame_edge_margin_percent: float = 5.0

    saturation_filter_enabled: bool = True

    image_frame_margin_enabled: bool = True

    image_display_stretch_mode: str = "stf"

    image_display_auto_stretch_default_migrated: bool = True

    image_display_black_point: float = 0.0

    image_display_midtone_point: float = 0.5

    image_display_white_point: float = 1.0

    image_display_curve_points: tuple[tuple[float, float], ...] = ()

    image_display_brightness: float = 0.0

    image_display_contrast: float = 1.0

    image_display_inverted: bool = False

    asteroid_estimate_snr_threshold: float = 4.0

    asteroid_estimate_start_magnitude: float = 15.0

    asteroid_default_magnitude_limit: float = 18.0

    asteroid_manual_magnitude_limit_override_enabled: bool = False

    asteroid_manual_magnitude_limit_override: float = 18.0

    asteroid_estimate_stars_per_bin: int = 6

    asteroid_estimate_required_visible_stars: int = 3

    asteroid_estimate_annotate_lowest_mag_stars: bool = False

    asteroid_visual_show_known_objects: bool = True

    asteroid_visual_show_object_markers: bool = True

    asteroid_visual_show_potential_discoveries: bool = True

    asteroid_visual_label_all_objects: bool = True

    asteroid_visual_show_target_marker: bool = False

    asteroid_visual_show_all_crosshairs: bool = True

    asteroid_visual_highlight_selected_object: bool = True

    asteroid_visual_invert_annotation_colors: bool = True

    asteroid_track_object_position_mode: str = "predicted"

    asteroid_target_marker_line_color: str = "#ef4444"

    asteroid_target_marker_accent_color: str = "#fca5a5"

    asteroid_target_marker_text_color: str = "#fff1f2"

    asteroid_target_marker_outline_color: str = "#ffffff"

    asteroid_target_marker_line_width: float = 4.0

    asteroid_blink_frame_duration_ms: int = 50

    asteroid_gif_export_scale_percent: int = 100

    asteroid_mp4_export_scale_percent: int = 100

    asteroid_gif_export_loop_forever: bool = True

    synthetic_tracking_crop_radius_pixels: int = 24

    synthetic_tracking_integration_mode: str = "average"

    synthetic_tracking_weight_mode: str = "psf_signal_weight"

    synthetic_tracking_rejection_mode: str = "no_rejection"

    synthetic_tracking_backend_preference: str = "auto"

    synthetic_tracking_combine_mode: str = "mean"

    synthetic_tracking_allow_mixed_all_group: bool = False

    reference_star_min_magnitude: float | None = None

    reference_star_max_magnitude: float | None = None

    observer_code: str = ""

    observer_name: str = ""

    organization: str = ""

    site_name: str = ""

    observing_site_latitude_deg: float | None = None

    observing_site_longitude_deg: float | None = None

    observing_site_elevation_m: float | None = None

    observing_site_presets: list[ObservingSitePreset] | None = None

    sky_atlas_custom_overlays_enabled: bool = False

    sky_atlas_custom_overlay_opacity: float = 0.85

    sky_atlas_custom_overlay_brightness: float = 1.0

    sky_atlas_custom_overlay_saturation: float = 1.0

    sky_atlas_custom_overlay_feather: float = 0.0

    sky_atlas_custom_overlay_cache_max_long_edge: int = 2048

    sky_atlas_custom_overlays: list[SkyAtlasCustomOverlayRecord] | None = None

    sky_atlas_custom_overlay_surveys: list[SkyAtlasCustomOverlaySurvey] | None = None

    sky_atlas_custom_overlay_active_survey_id: str | None = None

    sky_atlas_star_magnitude_limit: float = 8.7

    sky_atlas_star_brightness: float = 1.0

    sky_atlas_star_size: float = 1.0

    sky_atlas_star_magnitude_size_contrast: float = 1.0

    sky_atlas_star_twinkle: float = 0.5

    sky_atlas_show_messier: bool = True

    sky_atlas_show_ngc: bool = True

    sky_atlas_show_ic: bool = False

    sky_atlas_show_vdb: bool = False

    sky_atlas_show_ldn: bool = False

    sky_atlas_show_lbn: bool = False

    sky_atlas_object_magnitude_limit: float = 10.0

    sky_atlas_object_label_magnitude_limit: float = 8.0

    telescope: str = ""

    telescope_focal_length_mm: float | None = None

    telescope_aperture_mm: float | None = None

    telescope_focal_ratio: float | None = None

    camera: str = ""

    camera_pixel_size_um: float | None = None

    bortle_scale: int | None = None

    filter_system: str = ""

    aavso_chart_id: str = ""

    observation_timezone: str = "UTC"

    time_standard: str = "UTC"

    transformed: bool = False

    reduction_notes: str = ""

    preview_variable_star_max_count: int = 0

    preview_variable_star_min_magnitude: float | None = None

    preview_variable_star_max_magnitude: float | None = None

    interface_tips_enabled: bool = True

    show_mode_launcher_on_startup: bool = True

    app_mode: AppMode = AppMode.DIFFERENTIAL_PHOTOMETRY

    theme: str = _DEFAULT_THEME

    custom_theme_colors: dict[str, str] | None = None

    equatorial_grid_ra_density: int = 5

    equatorial_grid_dec_density: int = 5

    image_equatorial_grid_enabled: bool = False

    image_mark_saturated_enabled: bool = True

    selected_catalog_source_ids: dict[str, list[str]] | None = None

    manual_photometry_configs: dict[str, ManualPhotometryConfig] | None = None

    aperture_presets: list[AperturePreset] | None = None

    literature_period_cache: dict[str, dict[str, dict[str, object]]] | None = None

    calculated_period_cache: dict[str, dict[str, dict[str, object]]] | None = None



    @classmethod

    def defaults(cls, root_path: Path) -> "AppSettings":

        config_path = _settings_config_path()

        settings_root = _settings_root(root_path)

        legacy_config_path = settings_root / ".photometry-settings.json"

        source_path = config_path if config_path.exists() else legacy_config_path

        payload: dict[str, object] = {}

        if source_path.exists():

            try:

                existing_payload = _read_json_payload(source_path)

            except (OSError, json.JSONDecodeError):

                existing_payload = {}

            default_payload = existing_payload.get("default_settings") if isinstance(existing_payload, dict) else None

            if isinstance(default_payload, dict):

                payload = default_payload

        return _settings_from_payload(payload, config_path, use_last_theme=False)



    @classmethod

    def from_root(cls, root_path: Path) -> "AppSettings":

        config_path = _settings_config_path()

        settings_root = _settings_root(root_path)

        legacy_config_path = settings_root / ".photometry-settings.json"

        config_source_path = config_path if config_path.exists() else legacy_config_path

        payload: dict[str, str] = {}

        if config_source_path.exists():

            payload = _read_json_payload(config_source_path)

        return _settings_from_payload(payload, config_path, use_last_theme=True)



    def save(self, root_path: Path) -> None:

        config_path = _settings_config_path()

        payload = _settings_payload(self, config_path.parent)

        default_payload = None

        if config_path.exists():

            try:

                existing_payload = _read_json_payload(config_path)

            except (OSError, json.JSONDecodeError):

                existing_payload = {}

            existing_defaults = existing_payload.get("default_settings") if isinstance(existing_payload, dict) else None

            if isinstance(existing_defaults, dict):

                default_payload = existing_defaults

        if default_payload is None:

            default_payload = _settings_payload(AppSettings.defaults(root_path), config_path.parent)

        payload["default_settings"] = default_payload

        config_path.parent.mkdir(parents=True, exist_ok=True)

        config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")





def valid_theme_names() -> set[str]:

    return set(_VALID_THEME_NAMES)





def load_last_theme() -> str | None:

    state = _load_app_state()

    theme = state.get("last_theme")

    if not isinstance(theme, str):

        return None

    normalized = theme.strip().lower()

    return normalized if normalized in _VALID_THEME_NAMES else None





def save_last_theme(theme: str) -> None:

    normalized = str(theme).strip().lower()

    if normalized not in _VALID_THEME_NAMES:

        return

    state = _load_app_state()

    state["last_theme"] = normalized

    state_path = _app_state_path()

    state_path.parent.mkdir(parents=True, exist_ok=True)

    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")





def _load_app_state() -> dict[str, object]:

    state_path = _app_state_path()

    if not state_path.exists():

        return {}

    try:

        payload = _read_json_payload(state_path)

    except (OSError, json.JSONDecodeError):

        return {}

    return payload if isinstance(payload, dict) else {}





def _app_state_path() -> Path:

    override = os.getenv("CITIZEN_PHOTOMETRY_STATE_PATH")

    if override:

        return Path(override).expanduser()

    local_app_data = os.getenv("LOCALAPPDATA")

    if local_app_data:

        return Path(local_app_data) / "CitizenPhotometry" / _APP_STATE_FILE_NAME

    return Path.home() / ".citizen-photometry" / _APP_STATE_FILE_NAME





def default_settings_config_path() -> Path:

    local_app_data = os.getenv("LOCALAPPDATA")

    if local_app_data:

        return Path(local_app_data) / "CitizenPhotometry" / _SETTINGS_FILE_NAME

    return Path.home() / ".citizen-photometry" / _SETTINGS_FILE_NAME





def load_settings_config_override() -> Path | None:

    state = _load_app_state()

    value = state.get(_SETTINGS_CONFIG_PATH_KEY)

    if not isinstance(value, str) or not value.strip():

        return None

    return Path(value).expanduser()





def save_settings_config_override(config_path: Path | None) -> None:

    state = _load_app_state()

    if config_path is None:

        state.pop(_SETTINGS_CONFIG_PATH_KEY, None)

    else:

        state[_SETTINGS_CONFIG_PATH_KEY] = str(config_path.expanduser())

    state_path = _app_state_path()

    state_path.parent.mkdir(parents=True, exist_ok=True)

    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")





def _settings_config_path() -> Path:

    override = os.getenv("CITIZEN_PHOTOMETRY_CONFIG_PATH")

    if override:

        return Path(override).expanduser()

    state_override = load_settings_config_override()

    if state_override is not None:

        return state_override

    return default_settings_config_path()





def _relative_or_absolute(path: Path, root_path: Path) -> str:

    try:

        return str(path.resolve().relative_to(root_path.resolve()))

    except ValueError:

        return str(path.resolve())





def _settings_root(path: Path) -> Path:

    resolved = path.expanduser().resolve()

    if (resolved / "Files").exists():

        return resolved

    if resolved.name.lower() == "files":

        return resolved.parent

    if resolved.parent.name.lower() == "files":

        return resolved.parent.parent

    return resolved





def _settings_from_payload(payload: dict[str, object], config_path: Path, use_last_theme: bool) -> AppSettings:

    cache_dir_value = payload.get("cache_dir", ".photometry-cache")

    config_base_path = config_path.parent

    cache_dir = (config_base_path / cache_dir_value).resolve() if not Path(cache_dir_value).is_absolute() else Path(cache_dir_value)

    def _coerce_optional_settings_path(key: str) -> str:
        raw_value = str(payload.get(key, "") or "").strip()
        if not raw_value:
            return ""
        path_value = Path(raw_value).expanduser()
        if not path_value.is_absolute():
            path_value = (config_base_path / path_value).resolve()
        else:
            path_value = path_value.resolve()
        return str(path_value)

    aperture_mode_value = str(payload.get("photometry_aperture_mode", PhotometryApertureMode.FWHM_SCALED.value)).strip().lower()

    try:

        photometry_aperture_mode = PhotometryApertureMode(aperture_mode_value)

    except ValueError:

        photometry_aperture_mode = PhotometryApertureMode.FWHM_SCALED

    if photometry_aperture_mode == PhotometryApertureMode.FIXED:

        photometry_aperture_mode = PhotometryApertureMode.FWHM_SCALED

    aperture_radius_pixels = max(1.0, float(payload.get("aperture_radius_pixels", 5.0)))

    annulus_inner_radius_pixels = max(aperture_radius_pixels + 0.5, float(payload.get("annulus_inner_radius_pixels", 8.0)))

    annulus_outer_radius_pixels = max(annulus_inner_radius_pixels + 0.5, float(payload.get("annulus_outer_radius_pixels", 12.0)))

    aperture_radius_fwhm_scale = max(0.5, float(payload.get("aperture_radius_fwhm_scale", 1.6)))

    annulus_inner_radius_fwhm_scale = max(aperture_radius_fwhm_scale + 0.1, float(payload.get("annulus_inner_radius_fwhm_scale", 3.0)))

    annulus_outer_radius_fwhm_scale = max(annulus_inner_radius_fwhm_scale + 0.1, float(payload.get("annulus_outer_radius_fwhm_scale", 4.5)))

    legacy_percent = min(100, max(1, int(payload.get("variable_star_selection_percent", 100))))

    limit_mode_value = str(payload.get("variable_star_limit_mode", VariableStarLimitMode.PERCENT.value)).strip().lower()

    try:

        variable_star_limit_mode = VariableStarLimitMode(limit_mode_value)

    except ValueError:

        variable_star_limit_mode = VariableStarLimitMode.PERCENT

    variable_star_limit_value = int(payload.get("variable_star_limit_value", legacy_percent if variable_star_limit_mode == VariableStarLimitMode.PERCENT else 100))

    if variable_star_limit_mode == VariableStarLimitMode.PERCENT:

        variable_star_limit_value = min(100, max(1, variable_star_limit_value))

    else:

        variable_star_limit_value = max(1, variable_star_limit_value)

    preview_variable_star_max_count = max(0, int(payload.get("preview_variable_star_max_count", 0)))

    preview_variable_star_min_magnitude_value = payload.get("preview_variable_star_min_magnitude")

    preview_variable_star_min_magnitude = None

    if preview_variable_star_min_magnitude_value not in (None, ""):

        preview_variable_star_min_magnitude = min(30.0, max(-5.0, float(preview_variable_star_min_magnitude_value)))

    preview_variable_star_max_magnitude_value = payload.get("preview_variable_star_max_magnitude")

    preview_variable_star_max_magnitude = None

    if preview_variable_star_max_magnitude_value not in (None, ""):

        preview_variable_star_max_magnitude = min(30.0, max(-5.0, float(preview_variable_star_max_magnitude_value)))

    if preview_variable_star_min_magnitude is not None and preview_variable_star_max_magnitude is not None and preview_variable_star_min_magnitude > preview_variable_star_max_magnitude:

        preview_variable_star_min_magnitude, preview_variable_star_max_magnitude = preview_variable_star_max_magnitude, preview_variable_star_min_magnitude

    reference_star_min_magnitude_value = payload.get("reference_star_min_magnitude")

    reference_star_min_magnitude = None

    if reference_star_min_magnitude_value not in (None, ""):

        reference_star_min_magnitude = min(30.0, max(-5.0, float(reference_star_min_magnitude_value)))

    reference_star_max_magnitude_value = payload.get("reference_star_max_magnitude")

    reference_star_max_magnitude = None

    if reference_star_max_magnitude_value not in (None, ""):

        reference_star_max_magnitude = min(30.0, max(-5.0, float(reference_star_max_magnitude_value)))

    if reference_star_min_magnitude is not None and reference_star_max_magnitude is not None and reference_star_min_magnitude > reference_star_max_magnitude:

        reference_star_min_magnitude, reference_star_max_magnitude = reference_star_max_magnitude, reference_star_min_magnitude

    observer_code = _coerce_metadata_text(payload.get("observer_code"))

    observer_name = _coerce_metadata_text(payload.get("observer_name"))

    organization = _coerce_metadata_text(payload.get("organization"))

    site_name = _coerce_metadata_text(payload.get("site_name"))

    observing_site_latitude_deg = _coerce_optional_float(payload.get("observing_site_latitude_deg"), minimum=-90.0, maximum=90.0)

    observing_site_longitude_deg = _coerce_optional_float(payload.get("observing_site_longitude_deg"), minimum=-180.0, maximum=180.0)

    observing_site_elevation_m = _coerce_optional_float(payload.get("observing_site_elevation_m"), minimum=-500.0, maximum=12000.0)

    observing_site_presets = _coerce_observing_site_presets(payload.get("observing_site_presets"))

    sky_atlas_custom_overlays_enabled = bool(payload.get("sky_atlas_custom_overlays_enabled", False))

    sky_atlas_custom_overlay_opacity = _coerce_optional_float(payload.get("sky_atlas_custom_overlay_opacity"), minimum=0.0, maximum=1.0) or 0.85

    sky_atlas_custom_overlay_brightness = _coerce_optional_float(payload.get("sky_atlas_custom_overlay_brightness"), minimum=0.35, maximum=2.0) or 1.0

    sky_atlas_custom_overlay_saturation = _coerce_optional_float(payload.get("sky_atlas_custom_overlay_saturation"), minimum=0.0, maximum=2.4) or 1.0

    sky_atlas_custom_overlay_feather = _coerce_optional_float(payload.get("sky_atlas_custom_overlay_feather"), minimum=0.0, maximum=1.0) or 0.0

    sky_atlas_custom_overlay_cache_max_long_edge = max(
        512,
        min(8192, int(payload.get("sky_atlas_custom_overlay_cache_max_long_edge", 2048) or 2048)),
    )

    sky_atlas_custom_overlays = _coerce_sky_atlas_custom_overlays(payload.get("sky_atlas_custom_overlays"))

    sky_atlas_custom_overlay_surveys = _coerce_sky_atlas_custom_overlay_surveys(
        payload.get("sky_atlas_custom_overlay_surveys")
    )

    sky_atlas_custom_overlay_active_survey_id = _coerce_metadata_text(
        payload.get("sky_atlas_custom_overlay_active_survey_id")
    )

    sky_atlas_star_magnitude_limit = (
        _coerce_optional_float(payload.get("sky_atlas_star_magnitude_limit"), minimum=3.0, maximum=15.0) or 8.7
    )

    sky_atlas_star_brightness = (
        _coerce_optional_float(payload.get("sky_atlas_star_brightness"), minimum=0.35, maximum=2.0) or 1.0
    )

    sky_atlas_star_size = _coerce_optional_float(payload.get("sky_atlas_star_size"), minimum=0.4, maximum=2.5) or 1.0

    sky_atlas_star_magnitude_size_contrast = (
        _coerce_optional_float(payload.get("sky_atlas_star_magnitude_size_contrast"), minimum=0.0, maximum=2.0) or 1.0
    )

    sky_atlas_star_twinkle = (
        _coerce_optional_float(payload.get("sky_atlas_star_twinkle"), minimum=0.0, maximum=1.0) or 0.5
    )

    sky_atlas_show_messier = bool(payload.get("sky_atlas_show_messier", True))

    sky_atlas_show_ngc = bool(payload.get("sky_atlas_show_ngc", True))

    sky_atlas_show_ic = bool(payload.get("sky_atlas_show_ic", False))

    sky_atlas_show_vdb = bool(payload.get("sky_atlas_show_vdb", False))

    sky_atlas_show_ldn = bool(payload.get("sky_atlas_show_ldn", False))

    sky_atlas_show_lbn = bool(payload.get("sky_atlas_show_lbn", False))

    sky_atlas_object_magnitude_limit = (
        _coerce_optional_float(payload.get("sky_atlas_object_magnitude_limit"), minimum=1.0, maximum=16.0) or 10.0
    )

    sky_atlas_object_label_magnitude_limit = (
        _coerce_optional_float(payload.get("sky_atlas_object_label_magnitude_limit"), minimum=1.0, maximum=16.0)
        or 8.0
    )

    sky_atlas_custom_overlay_surveys, sky_atlas_custom_overlay_active_survey_id = (
        _migrate_sky_atlas_custom_overlay_surveys(
            sky_atlas_custom_overlay_surveys,
            sky_atlas_custom_overlays,
            sky_atlas_custom_overlay_active_survey_id,
        )
    )

    telescope = _coerce_metadata_text(payload.get("telescope"))

    telescope_focal_length_mm = _coerce_optional_float(payload.get("telescope_focal_length_mm"), minimum=0.1, maximum=100000.0)

    telescope_aperture_mm = _coerce_optional_float(payload.get("telescope_aperture_mm"), minimum=0.1, maximum=100000.0)

    telescope_focal_ratio = _coerce_optional_float(payload.get("telescope_focal_ratio"), minimum=0.1, maximum=100.0)

    camera = _coerce_metadata_text(payload.get("camera"))

    camera_pixel_size_um = _coerce_optional_float(payload.get("camera_pixel_size_um"), minimum=0.1, maximum=1000.0)

    bortle_scale_value = payload.get("bortle_scale")

    bortle_scale = None if bortle_scale_value in (None, "") else min(9, max(1, int(bortle_scale_value)))

    filter_system = _coerce_metadata_text(payload.get("filter_system"))

    aavso_chart_id = _coerce_metadata_text(payload.get("aavso_chart_id"))

    observation_timezone = _coerce_timezone_name(payload.get("observation_timezone"))

    time_standard = _coerce_time_standard(payload.get("time_standard"))

    reduction_notes = _coerce_metadata_text(payload.get("reduction_notes"), preserve_newlines=True)

    app_mode_value = str(payload.get("app_mode", AppMode.DIFFERENTIAL_PHOTOMETRY.value)).strip().lower()

    try:

        app_mode = AppMode(app_mode_value)

    except ValueError:

        app_mode = AppMode.DIFFERENTIAL_PHOTOMETRY

    theme_default = load_last_theme() or _DEFAULT_THEME if use_last_theme else _DEFAULT_THEME

    theme = str(payload.get("theme", theme_default)).strip().lower()

    if theme not in _VALID_THEME_NAMES:

        theme = _DEFAULT_THEME

    raw_image_display_stretch_mode = payload.get("image_display_stretch_mode")
    image_display_stretch_mode = _coerce_image_stretch_mode(raw_image_display_stretch_mode)
    image_display_auto_stretch_default_migrated = bool(payload.get("image_display_auto_stretch_default_migrated", False))
    if (
        not image_display_auto_stretch_default_migrated
        and raw_image_display_stretch_mode is not None
        and str(raw_image_display_stretch_mode).strip().lower() == "asinh"
    ):
        image_display_stretch_mode = "stf"
    image_display_auto_stretch_default_migrated = True

    asteroid_estimate_snr_threshold = min(100.0, max(0.1, float(payload.get("asteroid_estimate_snr_threshold", 4.0))))

    asteroid_estimate_start_magnitude = min(30.0, max(-5.0, float(payload.get("asteroid_estimate_start_magnitude", 15.0))))

    asteroid_default_magnitude_limit = min(30.0, max(5.0, float(payload.get("asteroid_default_magnitude_limit", 18.0))))

    asteroid_manual_magnitude_limit_override_enabled = bool(payload.get("asteroid_manual_magnitude_limit_override_enabled", False))

    asteroid_manual_magnitude_limit_override = min(30.0, max(5.0, float(payload.get("asteroid_manual_magnitude_limit_override", 18.0))))

    asteroid_estimate_stars_per_bin = max(2, int(payload.get("asteroid_estimate_stars_per_bin", 6)))

    asteroid_estimate_required_visible_stars = max(1, int(payload.get("asteroid_estimate_required_visible_stars", 3)))

    asteroid_estimate_required_visible_stars = min(asteroid_estimate_required_visible_stars, asteroid_estimate_stars_per_bin - 1)

    asteroid_discovery_min_residual_snr = min(500.0, max(0.0, float(payload.get("asteroid_discovery_min_residual_snr", 0.0))))

    asteroid_discovery_max_residual_snr = min(500.0, max(0.0, float(payload.get("asteroid_discovery_max_residual_snr", 0.0))))

    if asteroid_discovery_max_residual_snr > 0.0 and asteroid_discovery_min_residual_snr > asteroid_discovery_max_residual_snr:

        asteroid_discovery_min_residual_snr, asteroid_discovery_max_residual_snr = asteroid_discovery_max_residual_snr, asteroid_discovery_min_residual_snr

    asteroid_discovery_frames_per_batch = max(0, int(payload.get("asteroid_discovery_frames_per_batch", 0)))

    asteroid_discovery_binning_factor = int(payload.get("asteroid_discovery_binning_factor", 1) or 1)

    if asteroid_discovery_binning_factor not in {1, 2, 3, 4}:

        asteroid_discovery_binning_factor = 1

    asteroid_discovery_use_temporary_cache = bool(payload.get("asteroid_discovery_use_temporary_cache", False))

    asteroid_discovery_assume_aligned = bool(payload.get("asteroid_discovery_assume_aligned", False))

    asteroid_discovery_single_batch_only = bool(payload.get("asteroid_discovery_single_batch_only", False))

    asteroid_discovery_min_seed_displacement_px = max(0.0, float(payload.get("asteroid_discovery_min_seed_displacement_px", 1.5)))

    asteroid_discovery_motion_prior_bias = str(payload.get("asteroid_discovery_motion_prior_bias", "balanced") or "balanced").strip().lower() or "balanced"

    if asteroid_discovery_motion_prior_bias not in {"balanced", "main_belt", "near_earth"}:

        asteroid_discovery_motion_prior_bias = "balanced"

    asteroid_discovery_retry_with_detailed_search = bool(payload.get("asteroid_discovery_retry_with_detailed_search", False))

    asteroid_discovery_min_candidate_frames = max(2, min(32, int(payload.get("asteroid_discovery_min_candidate_frames", 3))))

    asteroid_discovery_detection_sigma = min(100.0, max(0.5, float(payload.get("asteroid_discovery_detection_sigma", 5.0))))

    asteroid_discovery_detection_fwhm = min(20.0, max(0.8, float(payload.get("asteroid_discovery_detection_fwhm", 3.0))))

    asteroid_discovery_max_residuals_per_frame = max(1, min(500, int(payload.get("asteroid_discovery_max_residuals_per_frame", 24))))

    asteroid_discovery_edge_margin_px = max(0, min(512, int(payload.get("asteroid_discovery_edge_margin_px", 6))))

    asteroid_discovery_detector_mode = str(payload.get("asteroid_discovery_detector_mode", "hybrid") or "hybrid").strip().lower()

    if asteroid_discovery_detector_mode not in {"point", "hybrid", "streak"}:

        asteroid_discovery_detector_mode = "hybrid"

    asteroid_discovery_streak_min_area_px = max(2, min(4096, int(payload.get("asteroid_discovery_streak_min_area_px", 6))))

    asteroid_discovery_streak_min_elongation = min(50.0, max(1.0, float(payload.get("asteroid_discovery_streak_min_elongation", 1.8))))

    asteroid_discovery_potential_deflection_rms_px = min(20.0, max(0.1, float(payload.get("asteroid_discovery_potential_deflection_rms_px", 0.9))))

    asteroid_discovery_review_deflection_rms_px = min(20.0, max(0.1, float(payload.get("asteroid_discovery_review_deflection_rms_px", 1.8))))

    if asteroid_discovery_review_deflection_rms_px < asteroid_discovery_potential_deflection_rms_px:

        asteroid_discovery_review_deflection_rms_px = asteroid_discovery_potential_deflection_rms_px

    asteroid_discovery_enable_synthetic_sweep = bool(payload.get("asteroid_discovery_enable_synthetic_sweep", False))

    asteroid_discovery_synthetic_sweep_max_motion_px_per_hour = min(

        500.0,

        max(0.1, float(payload.get("asteroid_discovery_synthetic_sweep_max_motion_px_per_hour", 12.0))),

    )

    asteroid_discovery_synthetic_sweep_motion_step_px_per_hour = min(

        asteroid_discovery_synthetic_sweep_max_motion_px_per_hour,

        max(0.1, float(payload.get("asteroid_discovery_synthetic_sweep_motion_step_px_per_hour", 1.0))),

    )

    asteroid_discovery_synthetic_sweep_angle_step_deg = min(

        180.0,

        max(1.0, float(payload.get("asteroid_discovery_synthetic_sweep_angle_step_deg", 30.0))),

    )

    asteroid_discovery_synthetic_sweep_direction_focus = _coerce_discovery_synthetic_sweep_direction_focus(

        payload.get("asteroid_discovery_synthetic_sweep_direction_focus", "all_directions")

    )

    asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg = min(

        180.0,

        max(1.0, float(payload.get("asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg", 45.0))),

    )

    asteroid_discovery_synthetic_sweep_min_stacked_snr = min(

        500.0,

        max(0.5, float(payload.get("asteroid_discovery_synthetic_sweep_min_stacked_snr", 6.0))),

    )

    asteroid_discovery_synthetic_sweep_save_stacks = bool(payload.get("asteroid_discovery_synthetic_sweep_save_stacks", False))

    legacy_asteroid_visual_show_known_objects = bool(payload.get("asteroid_visual_show_known_objects", True))

    asteroid_visual_show_object_markers = bool(payload.get("asteroid_visual_show_object_markers", legacy_asteroid_visual_show_known_objects))

    asteroid_visual_label_all_objects = bool(payload.get("asteroid_visual_label_all_objects", True))

    asteroid_visual_show_target_marker = bool(payload.get("asteroid_visual_show_target_marker", False))

    asteroid_track_object_position_mode = _coerce_asteroid_track_object_position_mode(payload.get("asteroid_track_object_position_mode"))

    if "asteroid_visual_show_object_markers" not in payload and not legacy_asteroid_visual_show_known_objects:

        asteroid_visual_label_all_objects = False

    synthetic_tracking_crop_radius_pixels = min(65535, max(4, int(payload.get("synthetic_tracking_crop_radius_pixels", 24))))

    legacy_synthetic_tracking_combine_mode = _coerce_synthetic_tracking_combine_mode(payload.get("synthetic_tracking_combine_mode"))
    synthetic_tracking_integration_mode = _coerce_synthetic_tracking_integration_mode(
        payload.get("synthetic_tracking_integration_mode"),
        legacy_combine_mode=legacy_synthetic_tracking_combine_mode,
    )
    synthetic_tracking_weight_mode = _coerce_synthetic_tracking_weight_mode(
        payload.get("synthetic_tracking_weight_mode"),
        legacy_combine_mode=legacy_synthetic_tracking_combine_mode,
    )
    synthetic_tracking_rejection_mode = _coerce_synthetic_tracking_rejection_mode(
        payload.get("synthetic_tracking_rejection_mode"),
        legacy_combine_mode=legacy_synthetic_tracking_combine_mode,
    )
    synthetic_tracking_backend_preference = _coerce_synthetic_tracking_backend_preference(
        payload.get("synthetic_tracking_backend_preference", "auto")
    )
    synthetic_tracking_combine_mode = _synthetic_tracking_legacy_combine_mode(
        synthetic_tracking_integration_mode,
        synthetic_tracking_rejection_mode,
    )

    distance_map_cluster_settings = _coerce_distance_map_cluster_settings(
        preset=payload.get("distance_map_cluster_preset", "default"),
        method=payload.get("distance_map_cluster_method", "auto"),
        strictness=payload.get("distance_map_cluster_strictness", 1.0),
        parallax_mode=payload.get("distance_map_cluster_parallax_mode", "auto"),
        refine_magnitude_consistency=payload.get("distance_map_cluster_refine_magnitude_consistency", True),
        auto_filter=payload.get("distance_map_cluster_auto_filter", False),
        mark_on_image=payload.get("distance_map_cluster_mark_on_image", True),
    )

    hr_motion_group_preset, hr_motion_group_method, hr_motion_group_strictness, hr_motion_group_parallax_mode, hr_motion_group_refine_hr_consistency, hr_motion_group_auto_filter = _coerce_hr_motion_group_settings(

        preset=payload.get("hr_motion_group_preset", "default"),

        method=payload.get("hr_motion_group_method", "auto"),

        strictness=payload.get("hr_motion_group_strictness", 1.0),

        parallax_mode=payload.get("hr_motion_group_parallax_mode", "auto"),

        refine_hr_consistency=payload.get("hr_motion_group_refine_hr_consistency", True),

        auto_filter=payload.get("hr_motion_group_auto_filter", False),

    )

    legacy_grid_density = min(12, max(2, int(payload.get("equatorial_grid_density", 5))))

    return AppSettings(

        astrometry_api_key=os.getenv("CITIZEN_PHOTOMETRY_ASTROMETRY_API_KEY") or payload.get("astrometry_api_key") or None,

        cache_dir=cache_dir,

        config_path=config_path,

        assume_aligned_images=bool(payload.get("assume_aligned_images", False)),

        nearby_reference_count=max(1, int(payload.get("nearby_reference_count", 5))),

        photometry_aperture_mode=photometry_aperture_mode,

        aperture_radius_pixels=aperture_radius_pixels,

        annulus_inner_radius_pixels=annulus_inner_radius_pixels,

        annulus_outer_radius_pixels=annulus_outer_radius_pixels,

        aperture_radius_fwhm_scale=aperture_radius_fwhm_scale,

        annulus_inner_radius_fwhm_scale=annulus_inner_radius_fwhm_scale,

        annulus_outer_radius_fwhm_scale=annulus_outer_radius_fwhm_scale,

        variable_star_limit_mode=variable_star_limit_mode,

        variable_star_limit_value=variable_star_limit_value,

        variable_star_designation_filters=_coerce_designation_filters(payload.get("variable_star_designation_filters")),

        shared_parallel_workers=(
            max(0, int(payload.get("shared_parallel_workers", 0)))
            if "shared_parallel_workers" in payload
            else max(
                max(0, int(payload.get("photometry_parallel_workers", 0))),
                max(0, int(payload.get("calculate_period_parallel_workers", 0))),
                max(0, int(payload.get("literature_period_parallel_workers", 0))),
            )
        ),

        astrostack_parallel_workers=max(0, int(payload.get("astrostack_parallel_workers", 0))),

        photometry_parallel_workers=(
            max(0, int(payload.get("shared_parallel_workers", 0)))
            if "shared_parallel_workers" in payload
            else max(
                max(0, int(payload.get("photometry_parallel_workers", 0))),
                max(0, int(payload.get("calculate_period_parallel_workers", 0))),
                max(0, int(payload.get("literature_period_parallel_workers", 0))),
            )
        ),

        calculate_period_parallel_workers=(
            max(0, int(payload.get("shared_parallel_workers", 0)))
            if "shared_parallel_workers" in payload
            else max(
                max(0, int(payload.get("photometry_parallel_workers", 0))),
                max(0, int(payload.get("calculate_period_parallel_workers", 0))),
                max(0, int(payload.get("literature_period_parallel_workers", 0))),
            )
        ),

        literature_period_parallel_workers=(
            max(0, int(payload.get("shared_parallel_workers", 0)))
            if "shared_parallel_workers" in payload
            else max(
                max(0, int(payload.get("photometry_parallel_workers", 0))),
                max(0, int(payload.get("calculate_period_parallel_workers", 0))),
                max(0, int(payload.get("literature_period_parallel_workers", 0))),
            )
        ),

        snr_binning_max_period_fraction=min(0.5, max(0.001, float(payload.get("snr_binning_max_period_fraction", 0.03)))),

        snr_binning_max_absolute_duration_seconds=min(86400.0, max(1.0, float(payload.get("snr_binning_max_absolute_duration_seconds", 600.0)))),

        snr_binning_target_snr=min(1000.0, max(1.0, float(payload.get("snr_binning_target_snr", 30.0)))),

        snr_binning_max_frames_per_bin=max(1, int(payload.get("snr_binning_max_frames_per_bin", 15))),

        snr_binning_min_frames_per_bin=max(1, int(payload.get("snr_binning_min_frames_per_bin", 1))),

        snr_binning_type_aware_thresholds=bool(payload.get("snr_binning_type_aware_thresholds", True)),

        snr_binning_sharp_period_fraction=min(0.5, max(0.001, float(payload.get("snr_binning_sharp_period_fraction", 0.015)))),

        snr_binning_smooth_period_fraction=min(0.5, max(0.001, float(payload.get("snr_binning_smooth_period_fraction", 0.05)))),

        snr_binning_weighted_flux_binning=bool(payload.get("snr_binning_weighted_flux_binning", True)),

        snr_binning_allow_magnitude_fallback=bool(payload.get("snr_binning_allow_magnitude_fallback", True)),

        snr_binning_minimum_valid_points_per_bin=max(1, int(payload.get("snr_binning_minimum_valid_points_per_bin", 2))),

        snr_binning_outlier_rejection_enabled=bool(payload.get("snr_binning_outlier_rejection_enabled", False)),

        snr_binning_sigma_clip_threshold=min(10.0, max(1.0, float(payload.get("snr_binning_sigma_clip_threshold", 3.5)))),

        snr_binning_dataset_mode=("replace" if str(payload.get("snr_binning_dataset_mode", "derived")).strip().lower() == "replace" else "derived"),

        snr_binning_apply_to_selected_measurements_only=bool(payload.get("snr_binning_apply_to_selected_measurements_only", False)),

        snr_binning_allow_periodless_fallback=bool(payload.get("snr_binning_allow_periodless_fallback", False)),

        comparison_fit_stop_match_index=min(100.0, max(0.0, float(payload.get("comparison_fit_stop_match_index", 95.0)))),

        comparison_fit_parallel_workers=max(0, int(payload.get("comparison_fit_parallel_workers", 0))),

        asteroid_search_parallel_workers=max(0, int(payload.get("asteroid_search_parallel_workers", 0))),

        asteroid_discovery_min_residual_snr=asteroid_discovery_min_residual_snr,

        asteroid_discovery_max_residual_snr=asteroid_discovery_max_residual_snr,

        asteroid_discovery_frames_per_batch=asteroid_discovery_frames_per_batch,

        asteroid_discovery_binning_factor=asteroid_discovery_binning_factor,

        asteroid_discovery_use_temporary_cache=asteroid_discovery_use_temporary_cache,

        asteroid_discovery_assume_aligned=asteroid_discovery_assume_aligned,

        asteroid_discovery_single_batch_only=asteroid_discovery_single_batch_only,

        asteroid_discovery_min_seed_displacement_px=asteroid_discovery_min_seed_displacement_px,

        asteroid_discovery_motion_prior_bias=asteroid_discovery_motion_prior_bias,

        asteroid_discovery_retry_with_detailed_search=asteroid_discovery_retry_with_detailed_search,

        asteroid_discovery_min_candidate_frames=asteroid_discovery_min_candidate_frames,

        asteroid_discovery_detection_sigma=asteroid_discovery_detection_sigma,

        asteroid_discovery_detection_fwhm=asteroid_discovery_detection_fwhm,

        asteroid_discovery_max_residuals_per_frame=asteroid_discovery_max_residuals_per_frame,

        asteroid_discovery_edge_margin_px=asteroid_discovery_edge_margin_px,

        asteroid_discovery_detector_mode=asteroid_discovery_detector_mode,

        asteroid_discovery_streak_min_area_px=asteroid_discovery_streak_min_area_px,

        asteroid_discovery_streak_min_elongation=asteroid_discovery_streak_min_elongation,

        asteroid_discovery_potential_deflection_rms_px=asteroid_discovery_potential_deflection_rms_px,

        asteroid_discovery_review_deflection_rms_px=asteroid_discovery_review_deflection_rms_px,

        asteroid_discovery_enable_synthetic_sweep=asteroid_discovery_enable_synthetic_sweep,

        asteroid_discovery_synthetic_sweep_max_motion_px_per_hour=asteroid_discovery_synthetic_sweep_max_motion_px_per_hour,

        asteroid_discovery_synthetic_sweep_motion_step_px_per_hour=asteroid_discovery_synthetic_sweep_motion_step_px_per_hour,

        asteroid_discovery_synthetic_sweep_angle_step_deg=asteroid_discovery_synthetic_sweep_angle_step_deg,

        asteroid_discovery_synthetic_sweep_direction_focus=asteroid_discovery_synthetic_sweep_direction_focus,

        asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg=asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg,

        asteroid_discovery_synthetic_sweep_min_stacked_snr=asteroid_discovery_synthetic_sweep_min_stacked_snr,

        asteroid_discovery_synthetic_sweep_save_stacks=asteroid_discovery_synthetic_sweep_save_stacks,

        comparison_fit_allow_multiple_targets=bool(payload.get("comparison_fit_allow_multiple_targets", False)),

        comparison_fit_eclipsing_binary_match_tolerance=min(50.0, max(0.0, float(payload.get("comparison_fit_eclipsing_binary_match_tolerance", 2.0)))),

        comparison_fit_fallback_candidate_pool_size=max(0, int(payload.get("comparison_fit_fallback_candidate_pool_size", 8))),

        comparison_fit_fallback_magnitude_tolerance=min(10.0, max(0.0, float(payload.get("comparison_fit_fallback_magnitude_tolerance", 0.75)))),

        discovery_max_candidate_count=max(0, int(payload.get("discovery_max_candidate_count", 60))),

        discovery_min_magnitude=_coerce_optional_float(payload.get("discovery_min_magnitude", 10.0), minimum=-5.0, maximum=30.0),

        discovery_max_magnitude=_coerce_optional_float(payload.get("discovery_max_magnitude", 15.5), minimum=-5.0, maximum=30.0),

        discovery_min_candidate_score=min(100.0, max(0.0, float(payload.get("discovery_min_candidate_score", 25.0)))),

        light_curve_scientific_export_enabled=bool(payload.get("light_curve_scientific_export_enabled", True)),

        scientific_light_curve_pdf_dpi=min(1200, max(72, int(payload.get("scientific_light_curve_pdf_dpi", 300)))),

        scientific_light_curve_pdf_paper_size=str(payload.get("scientific_light_curve_pdf_paper_size", "Letter")).strip() or "Letter",

        calibration_bias_path=_coerce_optional_settings_path("calibration_bias_path"),

        calibration_dark_path=_coerce_optional_settings_path("calibration_dark_path"),

        calibration_flat_path=_coerce_optional_settings_path("calibration_flat_path"),

        hr_max_sources=max(0, int(payload.get("hr_max_sources", 5000))),

        hr_table_row_limit=min(10000, max(1, int(payload.get("hr_table_row_limit", 1000)))),

        hr_motion_vector_color=_coerce_hex_color(payload.get("hr_motion_vector_color"), default="#3d8bfd"),

        hr_motion_vector_color_by_angle=bool(payload.get("hr_motion_vector_color_by_angle", False)),

        hr_motion_vector_saturation_by_magnitude=bool(payload.get("hr_motion_vector_saturation_by_magnitude", False)),

        hr_motion_vector_width=min(8.0, max(0.5, float(payload.get("hr_motion_vector_width", 1.5)))),

        hr_roi_drag_color=_coerce_hex_color(payload.get("hr_roi_drag_color"), default="#ff9f1c"),

        hr_roi_color=_coerce_hex_color(payload.get("hr_roi_color"), default="#2dd4bf"),

        hr_selection_circle_color=_coerce_hex_color(payload.get("hr_selection_circle_color"), default="#ffd166"),

        hr_selection_circle_opacity=min(1.0, max(0.0, float(payload.get("hr_selection_circle_opacity", 0.85)))),

        hr_selection_circle_size_factor=min(4.0, max(1.0, float(payload.get("hr_selection_circle_size_factor", 1.35)))),

        hr_plot_hide_flagged=bool(payload.get("hr_plot_hide_flagged", False)),

        hr_plot_hide_saturated=bool(payload.get("hr_plot_hide_saturated", True)),

        hr_search_catalog_names=bool(payload.get("hr_search_catalog_names", True)),

        hr_search_catalog_names_magnitude_threshold=min(30.0, max(-5.0, float(payload.get("hr_search_catalog_names_magnitude_threshold", 9.0)))),

        hr_plot_apparent_magnitude_min=min(30.0, max(-5.0, float(payload.get("hr_plot_apparent_magnitude_min", -5.0)))),

        hr_plot_apparent_magnitude_max=min(30.0, max(-5.0, float(payload.get("hr_plot_apparent_magnitude_max", 30.0)))),

        hr_plot_color_saturation=min(2.0, max(0.0, float(payload.get("hr_plot_color_saturation", 1.0)))),

        hr_plot_point_opacity=min(1.0, max(0.05, float(payload.get("hr_plot_point_opacity", 0.8)))),

        hr_plot_x_log_scale=bool(payload.get("hr_plot_x_log_scale", False)),

        hr_plot_marker_size_mode=_coerce_hr_marker_size_mode(payload.get("hr_plot_marker_size_mode")),

        hr_plot_fixed_marker_size=min(24.0, max(2.0, float(payload.get("hr_plot_fixed_marker_size", 8.0)))),

        hr_plot_require_parallax=bool(payload.get("hr_plot_require_parallax", True)),

        hr_motion_group_preset=hr_motion_group_preset,

        hr_motion_group_method=hr_motion_group_method,

        hr_motion_group_strictness=hr_motion_group_strictness,

        hr_motion_group_parallax_mode=hr_motion_group_parallax_mode,

        hr_motion_group_refine_hr_consistency=hr_motion_group_refine_hr_consistency,

        hr_motion_group_auto_filter=hr_motion_group_auto_filter,

        hr_left_splitter_sizes=_coerce_splitter_sizes(payload.get("hr_left_splitter_sizes"), expected_count=2),

        hr_main_splitter_sizes=_coerce_splitter_sizes(payload.get("hr_main_splitter_sizes"), expected_count=2),
        differential_analysis_splitter_sizes=_coerce_splitter_sizes(payload.get("differential_analysis_splitter_sizes"), expected_count=2),
        differential_results_splitter_sizes=_coerce_splitter_sizes(payload.get("differential_results_splitter_sizes"), expected_count=2),
        differential_main_splitter_sizes=_coerce_splitter_sizes(payload.get("differential_main_splitter_sizes"), expected_count=2),
        asteroid_main_splitter_sizes=_coerce_splitter_sizes(payload.get("asteroid_main_splitter_sizes"), expected_count=2),
        asteroid_results_splitter_sizes=_coerce_splitter_sizes(payload.get("asteroid_results_splitter_sizes"), expected_count=2),
        sky_explorer_left_splitter_sizes=_coerce_splitter_sizes(payload.get("sky_explorer_left_splitter_sizes"), expected_count=2),
        sky_explorer_results_splitter_sizes=_coerce_splitter_sizes(payload.get("sky_explorer_results_splitter_sizes"), expected_count=2),
        sky_explorer_main_splitter_sizes=_coerce_splitter_sizes(payload.get("sky_explorer_main_splitter_sizes"), expected_count=2),
        sky_explorer_object_type_column_widths=_coerce_sky_explorer_object_type_column_widths(payload.get("sky_explorer_object_type_column_widths")),
        sky_explorer_results_column_widths=_coerce_sky_explorer_results_column_widths(payload.get("sky_explorer_results_column_widths")),
        sky_explorer_simbad_search_radius_arcsec=_coerce_sky_explorer_simbad_search_radius_arcsec(payload.get("sky_explorer_simbad_search_radius_arcsec", 10.0)),
        sky_explorer_gaia_max_magnitude=_coerce_sky_explorer_gaia_max_magnitude(payload.get("sky_explorer_gaia_max_magnitude", 17.0)),
        sky_explorer_gaia_hard_cap_enabled=bool(payload.get("sky_explorer_gaia_hard_cap_enabled", False)),
        sky_explorer_gaia_hard_cap_rows=_coerce_sky_explorer_gaia_hard_cap_rows(payload.get("sky_explorer_gaia_hard_cap_rows", 1000)),
        sky_explorer_mag_limit_examples_per_bin=_coerce_sky_explorer_mag_limit_examples_per_bin(payload.get("sky_explorer_mag_limit_examples_per_bin", 1)),
        sky_explorer_mag_limit_marker_color=_coerce_hex_color(payload.get("sky_explorer_mag_limit_marker_color"), default="#3d8bfd"),
        sky_explorer_mag_limit_marker_stroke_color=_coerce_hex_color(payload.get("sky_explorer_mag_limit_marker_stroke_color"), default="#111827"),
        sky_explorer_mag_limit_marker_stroke_width=_coerce_sky_explorer_mag_limit_marker_stroke_width(payload.get("sky_explorer_mag_limit_marker_stroke_width", 2.0)),
        sky_explorer_mag_limit_target_size=_coerce_sky_explorer_mag_limit_target_size(payload.get("sky_explorer_mag_limit_target_size", 6.0)),
        sky_explorer_mag_limit_text_color=_coerce_hex_color(payload.get("sky_explorer_mag_limit_text_color"), default="#111827"),
        sky_explorer_mag_limit_text_stroke_color=_coerce_hex_color(payload.get("sky_explorer_mag_limit_text_stroke_color"), default="#ffffff"),
        sky_explorer_mag_limit_text_stroke_width=_coerce_sky_explorer_mag_limit_text_stroke_width(payload.get("sky_explorer_mag_limit_text_stroke_width", 0.0)),
        sky_explorer_mag_limit_text_size=_coerce_sky_explorer_mag_limit_text_size(payload.get("sky_explorer_mag_limit_text_size", 9.0)),
        sky_explorer_hide_objects_without_magnitude=bool(payload.get("sky_explorer_hide_objects_without_magnitude", False)),
        sky_explorer_scale_extended_nebulae=bool(payload.get("sky_explorer_scale_extended_nebulae", True)),
        sky_explorer_scale_overlay_strokes=bool(payload.get("sky_explorer_scale_overlay_strokes", True)),
        sky_explorer_marker_color_relation=_coerce_sky_explorer_marker_color_relation(payload.get("sky_explorer_marker_color_relation")),
        sky_explorer_text_color_relation=_coerce_sky_explorer_text_color_relation(payload.get("sky_explorer_text_color_relation")),
        sky_explorer_annotated_galaxy_max_magnitude_enabled=bool(payload.get("sky_explorer_annotated_galaxy_max_magnitude_enabled", False)),
        sky_explorer_annotated_galaxy_max_magnitude=_coerce_sky_explorer_annotated_galaxy_max_magnitude(payload.get("sky_explorer_annotated_galaxy_max_magnitude", 17.0)),
        sky_explorer_annotated_galaxy_require_shape_metadata=bool(payload.get("sky_explorer_annotated_galaxy_require_shape_metadata", False)),
        sky_explorer_enabled_layers=_coerce_sky_explorer_enabled_layers(payload.get("sky_explorer_enabled_layers")),
        sky_explorer_fill_opacity=_coerce_sky_explorer_opacity(payload.get("sky_explorer_fill_opacity"), default=0.25),
        sky_explorer_stroke_opacity=_coerce_sky_explorer_opacity(payload.get("sky_explorer_stroke_opacity"), default=1.0),
        sky_explorer_object_group_color_overrides=_coerce_sky_explorer_object_group_color_overrides(payload.get("sky_explorer_object_group_color_overrides")),
        sky_explorer_object_type_color_overrides=_coerce_sky_explorer_object_type_color_overrides(payload.get("sky_explorer_object_type_color_overrides")),
        sky_explorer_object_type_text_color_overrides=_coerce_sky_explorer_object_type_text_color_overrides(payload.get("sky_explorer_object_type_text_color_overrides")),
        sky_explorer_object_type_font_overrides=_coerce_sky_explorer_object_type_font_overrides(payload.get("sky_explorer_object_type_font_overrides")),

        distance_map_max_magnitude=_coerce_distance_map_max_magnitude(payload.get("distance_map_max_magnitude", 17.0)),
        distance_map_max_distance_pc=_coerce_distance_map_max_distance_pc(payload.get("distance_map_max_distance_pc", 500.0)),
        distance_map_max_stars=_coerce_distance_map_max_stars(payload.get("distance_map_max_stars", 500)),
        distance_map_min_parallax_snr=_coerce_distance_map_min_parallax_snr(payload.get("distance_map_min_parallax_snr", 5.0)),
        distance_map_limit_to_image_footprint=bool(payload.get("distance_map_limit_to_image_footprint", True)),
        distance_map_cluster_preset=distance_map_cluster_settings.preset,
        distance_map_cluster_method=distance_map_cluster_settings.method,
        distance_map_cluster_strictness=distance_map_cluster_settings.strictness,
        distance_map_cluster_parallax_mode=distance_map_cluster_settings.parallax_mode,
        distance_map_cluster_refine_magnitude_consistency=distance_map_cluster_settings.refine_magnitude_consistency,
        distance_map_cluster_auto_filter=distance_map_cluster_settings.auto_filter,
        distance_map_cluster_mark_on_image=distance_map_cluster_settings.mark_on_image,
        distance_map_model_cluster_depth=bool(payload.get("distance_map_model_cluster_depth", True)),
        distance_map_show_parallax_uncertainty=bool(payload.get("distance_map_show_parallax_uncertainty", False)),
        distance_map_use_external_cluster_catalog=bool(payload.get("distance_map_use_external_cluster_catalog", False)),

        frame_edge_margin_percent=min(49.0, max(0.0, float(payload.get("frame_edge_margin_percent", 5.0)))),

        saturation_filter_enabled=bool(payload.get("saturation_filter_enabled", True)),

        image_frame_margin_enabled=bool(payload.get("image_frame_margin_enabled", True)),

        image_display_stretch_mode=image_display_stretch_mode,

        image_display_auto_stretch_default_migrated=image_display_auto_stretch_default_migrated,

        image_display_black_point=min(1.0, max(0.0, float(payload.get("image_display_black_point", 0.0)))),

        image_display_midtone_point=min(1.0, max(0.0, float(payload.get("image_display_midtone_point", 0.5)))),

        image_display_white_point=min(1.0, max(0.0, float(payload.get("image_display_white_point", 1.0)))),

        image_display_curve_points=_coerce_image_display_curve_points(payload.get("image_display_curve_points")),

        image_display_brightness=min(0.95, max(-0.95, float(payload.get("image_display_brightness", 0.0)))),

        image_display_contrast=min(4.0, max(0.2, float(payload.get("image_display_contrast", 1.0)))),

        image_display_inverted=bool(payload.get("image_display_inverted", False)),

        asteroid_estimate_snr_threshold=asteroid_estimate_snr_threshold,

        asteroid_estimate_start_magnitude=asteroid_estimate_start_magnitude,

        asteroid_default_magnitude_limit=asteroid_default_magnitude_limit,

        asteroid_manual_magnitude_limit_override_enabled=asteroid_manual_magnitude_limit_override_enabled,

        asteroid_manual_magnitude_limit_override=asteroid_manual_magnitude_limit_override,

        asteroid_estimate_stars_per_bin=asteroid_estimate_stars_per_bin,

        asteroid_estimate_required_visible_stars=asteroid_estimate_required_visible_stars,

        asteroid_estimate_annotate_lowest_mag_stars=bool(payload.get("asteroid_estimate_annotate_lowest_mag_stars", False)),

        asteroid_visual_show_known_objects=asteroid_visual_show_object_markers,

        asteroid_visual_show_object_markers=asteroid_visual_show_object_markers,

        asteroid_visual_show_potential_discoveries=bool(payload.get("asteroid_visual_show_potential_discoveries", True)),

        asteroid_visual_label_all_objects=asteroid_visual_label_all_objects,

        asteroid_visual_show_target_marker=asteroid_visual_show_target_marker,

        asteroid_track_object_position_mode=asteroid_track_object_position_mode,

        asteroid_visual_show_all_crosshairs=bool(payload.get("asteroid_visual_show_all_crosshairs", True)),

        asteroid_visual_highlight_selected_object=bool(payload.get("asteroid_visual_highlight_selected_object", True)),

        asteroid_visual_invert_annotation_colors=bool(payload.get("asteroid_visual_invert_annotation_colors", True)),

        asteroid_target_marker_line_color=_coerce_hex_color(payload.get("asteroid_target_marker_line_color"), default="#ef4444"),

        asteroid_target_marker_accent_color=_coerce_hex_color(payload.get("asteroid_target_marker_accent_color"), default="#fca5a5"),

        asteroid_target_marker_text_color=_coerce_hex_color(payload.get("asteroid_target_marker_text_color"), default="#fff1f2"),

        asteroid_target_marker_outline_color=_coerce_hex_color(payload.get("asteroid_target_marker_outline_color"), default="#ffffff"),

        asteroid_target_marker_line_width=min(8.0, max(0.5, float(payload.get("asteroid_target_marker_line_width", 4.0)))),

        asteroid_blink_frame_duration_ms=min(2000, max(50, int(payload.get("asteroid_blink_frame_duration_ms", 50)))),

        asteroid_gif_export_scale_percent=min(400, max(25, int(payload.get("asteroid_gif_export_scale_percent", 100)))),

        asteroid_mp4_export_scale_percent=min(400, max(25, int(payload.get("asteroid_mp4_export_scale_percent", 100)))),

        asteroid_gif_export_loop_forever=bool(payload.get("asteroid_gif_export_loop_forever", True)),

        synthetic_tracking_crop_radius_pixels=synthetic_tracking_crop_radius_pixels,

        synthetic_tracking_integration_mode=synthetic_tracking_integration_mode,

        synthetic_tracking_weight_mode=synthetic_tracking_weight_mode,

        synthetic_tracking_rejection_mode=synthetic_tracking_rejection_mode,

        synthetic_tracking_backend_preference=synthetic_tracking_backend_preference,

        synthetic_tracking_combine_mode=synthetic_tracking_combine_mode,

        synthetic_tracking_allow_mixed_all_group=bool(payload.get("synthetic_tracking_allow_mixed_all_group", False)),

        synthetic_tracking_advanced_enabled=bool(payload.get("synthetic_tracking_advanced_enabled", False)),

        reference_star_min_magnitude=reference_star_min_magnitude,

        reference_star_max_magnitude=reference_star_max_magnitude,

        observer_code=observer_code,

        observer_name=observer_name,

        organization=organization,

        site_name=site_name,

        observing_site_latitude_deg=observing_site_latitude_deg,

        observing_site_longitude_deg=observing_site_longitude_deg,

        observing_site_elevation_m=observing_site_elevation_m,

        observing_site_presets=observing_site_presets,

        sky_atlas_custom_overlays_enabled=sky_atlas_custom_overlays_enabled,

        sky_atlas_custom_overlay_opacity=sky_atlas_custom_overlay_opacity,

        sky_atlas_custom_overlay_brightness=sky_atlas_custom_overlay_brightness,

        sky_atlas_custom_overlay_saturation=sky_atlas_custom_overlay_saturation,

        sky_atlas_custom_overlay_feather=sky_atlas_custom_overlay_feather,

        sky_atlas_custom_overlay_cache_max_long_edge=sky_atlas_custom_overlay_cache_max_long_edge,

        sky_atlas_custom_overlays=sky_atlas_custom_overlays,

        sky_atlas_custom_overlay_surveys=sky_atlas_custom_overlay_surveys,

        sky_atlas_custom_overlay_active_survey_id=sky_atlas_custom_overlay_active_survey_id,

        sky_atlas_star_magnitude_limit=sky_atlas_star_magnitude_limit,

        sky_atlas_star_brightness=sky_atlas_star_brightness,

        sky_atlas_star_size=sky_atlas_star_size,

        sky_atlas_star_magnitude_size_contrast=sky_atlas_star_magnitude_size_contrast,

        sky_atlas_star_twinkle=sky_atlas_star_twinkle,

        sky_atlas_show_messier=sky_atlas_show_messier,

        sky_atlas_show_ngc=sky_atlas_show_ngc,

        sky_atlas_show_ic=sky_atlas_show_ic,

        sky_atlas_show_vdb=sky_atlas_show_vdb,

        sky_atlas_show_ldn=sky_atlas_show_ldn,

        sky_atlas_show_lbn=sky_atlas_show_lbn,

        sky_atlas_object_magnitude_limit=sky_atlas_object_magnitude_limit,

        sky_atlas_object_label_magnitude_limit=sky_atlas_object_label_magnitude_limit,

        telescope=telescope,

        telescope_focal_length_mm=telescope_focal_length_mm,

        telescope_aperture_mm=telescope_aperture_mm,

        telescope_focal_ratio=telescope_focal_ratio,

        camera=camera,

        camera_pixel_size_um=camera_pixel_size_um,

        bortle_scale=bortle_scale,

        filter_system=filter_system,

        aavso_chart_id=aavso_chart_id,

        observation_timezone=observation_timezone,

        time_standard=time_standard,

        transformed=bool(payload.get("transformed", False)),

        reduction_notes=reduction_notes,

        preview_variable_star_max_count=preview_variable_star_max_count,

        preview_variable_star_min_magnitude=preview_variable_star_min_magnitude,

        preview_variable_star_max_magnitude=preview_variable_star_max_magnitude,

        interface_tips_enabled=bool(payload.get("interface_tips_enabled", True)),

        show_mode_launcher_on_startup=bool(payload.get("show_mode_launcher_on_startup", True)),

        app_mode=app_mode,

        theme=theme,

        custom_theme_colors=_coerce_custom_theme_colors(payload.get("custom_theme_colors")),

        equatorial_grid_ra_density=min(12, max(2, int(payload.get("equatorial_grid_ra_density", legacy_grid_density)))),

        equatorial_grid_dec_density=min(12, max(2, int(payload.get("equatorial_grid_dec_density", legacy_grid_density)))),

        image_equatorial_grid_enabled=bool(payload.get("image_equatorial_grid_enabled", False)),

        image_mark_saturated_enabled=bool(payload.get("image_mark_saturated_enabled", True)),

        selected_catalog_source_ids=_coerce_selected_catalog_source_ids(payload.get("selected_catalog_source_ids")),

        manual_photometry_configs=_coerce_manual_configs(payload.get("manual_photometry_configs")),

        aperture_presets=_coerce_aperture_presets(payload.get("aperture_presets")),

        literature_period_cache=_coerce_period_cache(payload.get("literature_period_cache")),

        calculated_period_cache=_coerce_period_cache(payload.get("calculated_period_cache")),

    )





def _settings_payload(settings: AppSettings, config_base_path: Path) -> dict[str, object]:

    limit_mode = _coerce_limit_mode(settings.variable_star_limit_mode)

    cache_dir_value = _relative_or_absolute(settings.cache_dir, config_base_path)

    def _optional_settings_path_value(path_text: str) -> str:
        stripped = str(path_text or "").strip()
        if not stripped:
            return ""
        return _relative_or_absolute(Path(stripped).expanduser(), config_base_path)

    hr_motion_group_preset, hr_motion_group_method, hr_motion_group_strictness, hr_motion_group_parallax_mode, hr_motion_group_refine_hr_consistency, hr_motion_group_auto_filter = _coerce_hr_motion_group_settings(

        preset=settings.hr_motion_group_preset,

        method=settings.hr_motion_group_method,

        strictness=settings.hr_motion_group_strictness,

        parallax_mode=settings.hr_motion_group_parallax_mode,

        refine_hr_consistency=settings.hr_motion_group_refine_hr_consistency,

        auto_filter=settings.hr_motion_group_auto_filter,

    )

    distance_map_cluster_settings = _coerce_distance_map_cluster_settings(
        preset=settings.distance_map_cluster_preset,
        method=settings.distance_map_cluster_method,
        strictness=settings.distance_map_cluster_strictness,
        parallax_mode=settings.distance_map_cluster_parallax_mode,
        refine_magnitude_consistency=settings.distance_map_cluster_refine_magnitude_consistency,
        auto_filter=settings.distance_map_cluster_auto_filter,
        mark_on_image=settings.distance_map_cluster_mark_on_image,
    )

    return {

        "astrometry_api_key": settings.astrometry_api_key or "",

        "cache_dir": cache_dir_value,

        "assume_aligned_images": settings.assume_aligned_images,

        "nearby_reference_count": settings.nearby_reference_count,

        "photometry_aperture_mode": settings.photometry_aperture_mode.value,

        "aperture_radius_pixels": settings.aperture_radius_pixels,

        "annulus_inner_radius_pixels": settings.annulus_inner_radius_pixels,

        "annulus_outer_radius_pixels": settings.annulus_outer_radius_pixels,

        "aperture_radius_fwhm_scale": settings.aperture_radius_fwhm_scale,

        "annulus_inner_radius_fwhm_scale": settings.annulus_inner_radius_fwhm_scale,

        "annulus_outer_radius_fwhm_scale": settings.annulus_outer_radius_fwhm_scale,

        "variable_star_limit_mode": limit_mode.value,

        "variable_star_limit_value": settings.variable_star_limit_value,

        "variable_star_designation_filters": [item.value for item in _coerce_designation_filters(settings.variable_star_designation_filters)],

        "shared_parallel_workers": resolve_shared_parallel_workers(settings),

        "photometry_parallel_workers": resolve_shared_parallel_workers(settings),

        "calculate_period_parallel_workers": resolve_shared_parallel_workers(settings),

        "literature_period_parallel_workers": resolve_shared_parallel_workers(settings),

        "snr_binning_max_period_fraction": min(0.5, max(0.001, float(settings.snr_binning_max_period_fraction))),

        "snr_binning_max_absolute_duration_seconds": min(86400.0, max(1.0, float(settings.snr_binning_max_absolute_duration_seconds))),

        "snr_binning_target_snr": min(1000.0, max(1.0, float(settings.snr_binning_target_snr))),

        "snr_binning_max_frames_per_bin": max(1, int(settings.snr_binning_max_frames_per_bin)),

        "snr_binning_min_frames_per_bin": max(1, int(settings.snr_binning_min_frames_per_bin)),

        "snr_binning_type_aware_thresholds": bool(settings.snr_binning_type_aware_thresholds),

        "snr_binning_sharp_period_fraction": min(0.5, max(0.001, float(settings.snr_binning_sharp_period_fraction))),

        "snr_binning_smooth_period_fraction": min(0.5, max(0.001, float(settings.snr_binning_smooth_period_fraction))),

        "snr_binning_weighted_flux_binning": bool(settings.snr_binning_weighted_flux_binning),

        "snr_binning_allow_magnitude_fallback": bool(settings.snr_binning_allow_magnitude_fallback),

        "snr_binning_minimum_valid_points_per_bin": max(1, int(settings.snr_binning_minimum_valid_points_per_bin)),

        "snr_binning_outlier_rejection_enabled": bool(settings.snr_binning_outlier_rejection_enabled),

        "snr_binning_sigma_clip_threshold": min(10.0, max(1.0, float(settings.snr_binning_sigma_clip_threshold))),

        "snr_binning_dataset_mode": "replace" if str(settings.snr_binning_dataset_mode).strip().lower() == "replace" else "derived",

        "snr_binning_apply_to_selected_measurements_only": bool(settings.snr_binning_apply_to_selected_measurements_only),

        "snr_binning_allow_periodless_fallback": bool(settings.snr_binning_allow_periodless_fallback),

        "comparison_fit_stop_match_index": min(100.0, max(0.0, float(settings.comparison_fit_stop_match_index))),

        "comparison_fit_parallel_workers": max(0, int(settings.comparison_fit_parallel_workers)),

        "asteroid_search_parallel_workers": max(0, int(settings.asteroid_search_parallel_workers)),

        "asteroid_discovery_min_residual_snr": min(500.0, max(0.0, float(settings.asteroid_discovery_min_residual_snr))),

        "asteroid_discovery_max_residual_snr": min(500.0, max(0.0, float(settings.asteroid_discovery_max_residual_snr))),

        "asteroid_discovery_frames_per_batch": max(0, int(settings.asteroid_discovery_frames_per_batch)),

        "asteroid_discovery_binning_factor": 1 if int(settings.asteroid_discovery_binning_factor) not in {1, 2, 3, 4} else int(settings.asteroid_discovery_binning_factor),

        "asteroid_discovery_use_temporary_cache": bool(settings.asteroid_discovery_use_temporary_cache),

        "asteroid_discovery_assume_aligned": bool(settings.asteroid_discovery_assume_aligned),

        "asteroid_discovery_single_batch_only": bool(settings.asteroid_discovery_single_batch_only),

        "asteroid_discovery_min_seed_displacement_px": max(0.0, float(settings.asteroid_discovery_min_seed_displacement_px)),

        "asteroid_discovery_motion_prior_bias": (
            str(settings.asteroid_discovery_motion_prior_bias or "balanced").strip().lower()
            if str(settings.asteroid_discovery_motion_prior_bias or "balanced").strip().lower() in {"balanced", "main_belt", "near_earth"}
            else "balanced"
        ),

        "asteroid_discovery_retry_with_detailed_search": bool(settings.asteroid_discovery_retry_with_detailed_search),

        "asteroid_discovery_min_candidate_frames": max(2, min(32, int(settings.asteroid_discovery_min_candidate_frames))),

        "asteroid_discovery_detection_sigma": min(100.0, max(0.5, float(settings.asteroid_discovery_detection_sigma))),

        "asteroid_discovery_detection_fwhm": min(20.0, max(0.8, float(settings.asteroid_discovery_detection_fwhm))),

        "asteroid_discovery_max_residuals_per_frame": max(1, min(500, int(settings.asteroid_discovery_max_residuals_per_frame))),

        "asteroid_discovery_edge_margin_px": max(0, min(512, int(settings.asteroid_discovery_edge_margin_px))),

        "asteroid_discovery_detector_mode": str(settings.asteroid_discovery_detector_mode or "hybrid").strip().lower() if str(settings.asteroid_discovery_detector_mode or "hybrid").strip().lower() in {"point", "hybrid", "streak"} else "hybrid",

        "asteroid_discovery_streak_min_area_px": max(2, min(4096, int(settings.asteroid_discovery_streak_min_area_px))),

        "asteroid_discovery_streak_min_elongation": min(50.0, max(1.0, float(settings.asteroid_discovery_streak_min_elongation))),

        "asteroid_discovery_potential_deflection_rms_px": min(20.0, max(0.1, float(settings.asteroid_discovery_potential_deflection_rms_px))),

        "asteroid_discovery_review_deflection_rms_px": min(20.0, max(float(settings.asteroid_discovery_potential_deflection_rms_px), float(settings.asteroid_discovery_review_deflection_rms_px))),

        "asteroid_discovery_enable_synthetic_sweep": bool(settings.asteroid_discovery_enable_synthetic_sweep),

        "asteroid_discovery_synthetic_sweep_max_motion_px_per_hour": min(500.0, max(0.1, float(settings.asteroid_discovery_synthetic_sweep_max_motion_px_per_hour))),

        "asteroid_discovery_synthetic_sweep_motion_step_px_per_hour": min(
            max(0.1, float(settings.asteroid_discovery_synthetic_sweep_max_motion_px_per_hour)),
            max(0.1, float(settings.asteroid_discovery_synthetic_sweep_motion_step_px_per_hour)),
        ),

        "asteroid_discovery_synthetic_sweep_angle_step_deg": min(180.0, max(1.0, float(settings.asteroid_discovery_synthetic_sweep_angle_step_deg))),

        "asteroid_discovery_synthetic_sweep_direction_focus": _coerce_discovery_synthetic_sweep_direction_focus(settings.asteroid_discovery_synthetic_sweep_direction_focus),

        "asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg": min(
            180.0,
            max(1.0, float(settings.asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg)),
        ),

        "asteroid_discovery_synthetic_sweep_min_stacked_snr": min(500.0, max(0.5, float(settings.asteroid_discovery_synthetic_sweep_min_stacked_snr))),

        "asteroid_discovery_synthetic_sweep_save_stacks": bool(settings.asteroid_discovery_synthetic_sweep_save_stacks),

        "comparison_fit_allow_multiple_targets": bool(settings.comparison_fit_allow_multiple_targets),

        "comparison_fit_eclipsing_binary_match_tolerance": min(50.0, max(0.0, float(settings.comparison_fit_eclipsing_binary_match_tolerance))),

        "comparison_fit_fallback_candidate_pool_size": max(0, int(settings.comparison_fit_fallback_candidate_pool_size)),

        "comparison_fit_fallback_magnitude_tolerance": min(10.0, max(0.0, float(settings.comparison_fit_fallback_magnitude_tolerance))),

        "discovery_max_candidate_count": max(0, int(settings.discovery_max_candidate_count)),

        "discovery_min_magnitude": None if settings.discovery_min_magnitude is None else float(settings.discovery_min_magnitude),

        "discovery_max_magnitude": None if settings.discovery_max_magnitude is None else float(settings.discovery_max_magnitude),

        "discovery_min_candidate_score": min(100.0, max(0.0, float(settings.discovery_min_candidate_score))),

        "light_curve_scientific_export_enabled": bool(settings.light_curve_scientific_export_enabled),

        "scientific_light_curve_pdf_dpi": min(1200, max(72, int(settings.scientific_light_curve_pdf_dpi))),

        "scientific_light_curve_pdf_paper_size": str(settings.scientific_light_curve_pdf_paper_size).strip() or "Letter",

        "calibration_bias_path": _optional_settings_path_value(settings.calibration_bias_path),

        "calibration_dark_path": _optional_settings_path_value(settings.calibration_dark_path),

        "calibration_flat_path": _optional_settings_path_value(settings.calibration_flat_path),

        "hr_max_sources": max(0, int(settings.hr_max_sources)),

        "hr_table_row_limit": min(10000, max(1, int(settings.hr_table_row_limit))),

        "hr_motion_vector_color": _coerce_hex_color(settings.hr_motion_vector_color, default="#3d8bfd"),

        "hr_motion_vector_color_by_angle": bool(settings.hr_motion_vector_color_by_angle),

        "hr_motion_vector_saturation_by_magnitude": bool(settings.hr_motion_vector_saturation_by_magnitude),

        "hr_motion_vector_width": min(8.0, max(0.5, float(settings.hr_motion_vector_width))),

        "hr_roi_drag_color": _coerce_hex_color(settings.hr_roi_drag_color, default="#ff9f1c"),

        "hr_roi_color": _coerce_hex_color(settings.hr_roi_color, default="#2dd4bf"),

        "hr_selection_circle_color": _coerce_hex_color(settings.hr_selection_circle_color, default="#ffd166"),

        "hr_selection_circle_opacity": min(1.0, max(0.0, float(settings.hr_selection_circle_opacity))),

        "hr_selection_circle_size_factor": min(4.0, max(1.0, float(settings.hr_selection_circle_size_factor))),

        "hr_plot_hide_flagged": bool(settings.hr_plot_hide_flagged),

        "hr_plot_hide_saturated": bool(settings.hr_plot_hide_saturated),

        "hr_search_catalog_names": bool(settings.hr_search_catalog_names),

        "hr_search_catalog_names_magnitude_threshold": min(30.0, max(-5.0, float(settings.hr_search_catalog_names_magnitude_threshold))),

        "hr_plot_apparent_magnitude_min": min(30.0, max(-5.0, float(settings.hr_plot_apparent_magnitude_min))),

        "hr_plot_apparent_magnitude_max": min(30.0, max(-5.0, float(settings.hr_plot_apparent_magnitude_max))),

        "hr_plot_color_saturation": min(2.0, max(0.0, float(settings.hr_plot_color_saturation))),

        "hr_plot_point_opacity": min(1.0, max(0.05, float(settings.hr_plot_point_opacity))),

        "hr_plot_marker_size_mode": _coerce_hr_marker_size_mode(settings.hr_plot_marker_size_mode),

        "hr_plot_fixed_marker_size": min(24.0, max(2.0, float(settings.hr_plot_fixed_marker_size))),

        "hr_plot_require_parallax": bool(settings.hr_plot_require_parallax),

        "hr_motion_group_preset": hr_motion_group_preset,

        "hr_motion_group_method": hr_motion_group_method,

        "hr_motion_group_strictness": hr_motion_group_strictness,

        "hr_motion_group_parallax_mode": hr_motion_group_parallax_mode,

        "hr_motion_group_refine_hr_consistency": bool(hr_motion_group_refine_hr_consistency),

        "hr_motion_group_auto_filter": bool(hr_motion_group_auto_filter),

        "hr_left_splitter_sizes": _coerce_splitter_sizes(settings.hr_left_splitter_sizes, expected_count=2),

        "hr_main_splitter_sizes": _coerce_splitter_sizes(settings.hr_main_splitter_sizes, expected_count=2),
        "differential_analysis_splitter_sizes": _coerce_splitter_sizes(settings.differential_analysis_splitter_sizes, expected_count=2),
        "differential_results_splitter_sizes": _coerce_splitter_sizes(settings.differential_results_splitter_sizes, expected_count=2),
        "differential_main_splitter_sizes": _coerce_splitter_sizes(settings.differential_main_splitter_sizes, expected_count=2),
        "asteroid_main_splitter_sizes": _coerce_splitter_sizes(settings.asteroid_main_splitter_sizes, expected_count=2),
        "asteroid_results_splitter_sizes": _coerce_splitter_sizes(settings.asteroid_results_splitter_sizes, expected_count=2),
        "sky_explorer_left_splitter_sizes": _coerce_splitter_sizes(settings.sky_explorer_left_splitter_sizes, expected_count=2),
        "sky_explorer_results_splitter_sizes": _coerce_splitter_sizes(settings.sky_explorer_results_splitter_sizes, expected_count=2),
        "sky_explorer_main_splitter_sizes": _coerce_splitter_sizes(settings.sky_explorer_main_splitter_sizes, expected_count=2),
        "sky_explorer_object_type_column_widths": _coerce_sky_explorer_object_type_column_widths(settings.sky_explorer_object_type_column_widths),
        "sky_explorer_results_column_widths": _coerce_sky_explorer_results_column_widths(settings.sky_explorer_results_column_widths),
        "sky_explorer_simbad_search_radius_arcsec": _coerce_sky_explorer_simbad_search_radius_arcsec(settings.sky_explorer_simbad_search_radius_arcsec),
        "sky_explorer_gaia_max_magnitude": _coerce_sky_explorer_gaia_max_magnitude(settings.sky_explorer_gaia_max_magnitude),
        "sky_explorer_gaia_hard_cap_enabled": bool(settings.sky_explorer_gaia_hard_cap_enabled),
        "sky_explorer_gaia_hard_cap_rows": _coerce_sky_explorer_gaia_hard_cap_rows(settings.sky_explorer_gaia_hard_cap_rows),
        "sky_explorer_mag_limit_examples_per_bin": _coerce_sky_explorer_mag_limit_examples_per_bin(settings.sky_explorer_mag_limit_examples_per_bin),
        "sky_explorer_mag_limit_marker_color": _coerce_hex_color(settings.sky_explorer_mag_limit_marker_color, default="#3d8bfd"),
        "sky_explorer_mag_limit_marker_stroke_color": _coerce_hex_color(settings.sky_explorer_mag_limit_marker_stroke_color, default="#111827"),
        "sky_explorer_mag_limit_marker_stroke_width": _coerce_sky_explorer_mag_limit_marker_stroke_width(settings.sky_explorer_mag_limit_marker_stroke_width),
        "sky_explorer_mag_limit_target_size": _coerce_sky_explorer_mag_limit_target_size(settings.sky_explorer_mag_limit_target_size),
        "sky_explorer_mag_limit_text_color": _coerce_hex_color(settings.sky_explorer_mag_limit_text_color, default="#111827"),
        "sky_explorer_mag_limit_text_stroke_color": _coerce_hex_color(settings.sky_explorer_mag_limit_text_stroke_color, default="#ffffff"),
        "sky_explorer_mag_limit_text_stroke_width": _coerce_sky_explorer_mag_limit_text_stroke_width(settings.sky_explorer_mag_limit_text_stroke_width),
        "sky_explorer_mag_limit_text_size": _coerce_sky_explorer_mag_limit_text_size(settings.sky_explorer_mag_limit_text_size),
        "sky_explorer_hide_objects_without_magnitude": bool(settings.sky_explorer_hide_objects_without_magnitude),
        "sky_explorer_scale_extended_nebulae": bool(settings.sky_explorer_scale_extended_nebulae),
        "sky_explorer_scale_overlay_strokes": bool(settings.sky_explorer_scale_overlay_strokes),
        "sky_explorer_marker_color_relation": _coerce_sky_explorer_marker_color_relation(settings.sky_explorer_marker_color_relation),
        "sky_explorer_text_color_relation": _coerce_sky_explorer_text_color_relation(settings.sky_explorer_text_color_relation),
        "sky_explorer_annotated_galaxy_max_magnitude_enabled": bool(settings.sky_explorer_annotated_galaxy_max_magnitude_enabled),
        "sky_explorer_annotated_galaxy_max_magnitude": _coerce_sky_explorer_annotated_galaxy_max_magnitude(settings.sky_explorer_annotated_galaxy_max_magnitude),
        "sky_explorer_annotated_galaxy_require_shape_metadata": bool(settings.sky_explorer_annotated_galaxy_require_shape_metadata),
        "sky_explorer_enabled_layers": list(_coerce_sky_explorer_enabled_layers(settings.sky_explorer_enabled_layers)),
        "sky_explorer_fill_opacity": _coerce_sky_explorer_opacity(settings.sky_explorer_fill_opacity, default=0.25),
        "sky_explorer_stroke_opacity": _coerce_sky_explorer_opacity(settings.sky_explorer_stroke_opacity, default=1.0),
        "sky_explorer_object_group_color_overrides": _coerce_sky_explorer_object_group_color_overrides(settings.sky_explorer_object_group_color_overrides),
        "sky_explorer_object_type_color_overrides": _sky_explorer_object_type_color_overrides_to_payload(settings.sky_explorer_object_type_color_overrides),
        "sky_explorer_object_type_text_color_overrides": _coerce_sky_explorer_object_type_text_color_overrides(settings.sky_explorer_object_type_text_color_overrides),
        "sky_explorer_object_type_font_overrides": _coerce_sky_explorer_object_type_font_overrides(settings.sky_explorer_object_type_font_overrides),

        "distance_map_max_magnitude": _coerce_distance_map_max_magnitude(settings.distance_map_max_magnitude),
        "distance_map_max_distance_pc": _coerce_distance_map_max_distance_pc(settings.distance_map_max_distance_pc),
        "distance_map_max_stars": _coerce_distance_map_max_stars(settings.distance_map_max_stars),
        "distance_map_min_parallax_snr": _coerce_distance_map_min_parallax_snr(settings.distance_map_min_parallax_snr),
        "distance_map_limit_to_image_footprint": bool(settings.distance_map_limit_to_image_footprint),
        "distance_map_cluster_preset": distance_map_cluster_settings.preset,
        "distance_map_cluster_method": distance_map_cluster_settings.method,
        "distance_map_cluster_strictness": distance_map_cluster_settings.strictness,
        "distance_map_cluster_parallax_mode": distance_map_cluster_settings.parallax_mode,
        "distance_map_cluster_refine_magnitude_consistency": bool(distance_map_cluster_settings.refine_magnitude_consistency),
        "distance_map_cluster_auto_filter": bool(distance_map_cluster_settings.auto_filter),
        "distance_map_cluster_mark_on_image": bool(distance_map_cluster_settings.mark_on_image),
        "distance_map_model_cluster_depth": bool(settings.distance_map_model_cluster_depth),
        "distance_map_show_parallax_uncertainty": bool(settings.distance_map_show_parallax_uncertainty),
        "distance_map_use_external_cluster_catalog": bool(settings.distance_map_use_external_cluster_catalog),

        "frame_edge_margin_percent": min(49.0, max(0.0, float(settings.frame_edge_margin_percent))),

        "saturation_filter_enabled": bool(settings.saturation_filter_enabled),

        "image_frame_margin_enabled": bool(settings.image_frame_margin_enabled),

        "image_display_stretch_mode": _coerce_image_stretch_mode(settings.image_display_stretch_mode),

        "image_display_auto_stretch_default_migrated": bool(settings.image_display_auto_stretch_default_migrated),

        "image_display_black_point": min(1.0, max(0.0, float(settings.image_display_black_point))),

        "image_display_midtone_point": min(1.0, max(0.0, float(settings.image_display_midtone_point))),

        "image_display_white_point": min(1.0, max(0.0, float(settings.image_display_white_point))),

        "image_display_curve_points": _coerce_image_display_curve_points(settings.image_display_curve_points),

        "image_display_brightness": min(0.95, max(-0.95, float(settings.image_display_brightness))),

        "image_display_contrast": min(4.0, max(0.2, float(settings.image_display_contrast))),

        "image_display_inverted": bool(settings.image_display_inverted),

        "asteroid_estimate_snr_threshold": min(100.0, max(0.1, float(settings.asteroid_estimate_snr_threshold))),

        "asteroid_estimate_start_magnitude": min(30.0, max(-5.0, float(settings.asteroid_estimate_start_magnitude))),

        "asteroid_default_magnitude_limit": min(30.0, max(5.0, float(settings.asteroid_default_magnitude_limit))),

        "asteroid_manual_magnitude_limit_override_enabled": bool(settings.asteroid_manual_magnitude_limit_override_enabled),

        "asteroid_manual_magnitude_limit_override": min(30.0, max(5.0, float(settings.asteroid_manual_magnitude_limit_override))),

        "asteroid_estimate_stars_per_bin": max(2, int(settings.asteroid_estimate_stars_per_bin)),

        "asteroid_estimate_required_visible_stars": min(

            max(1, int(settings.asteroid_estimate_required_visible_stars)),

            max(2, int(settings.asteroid_estimate_stars_per_bin)) - 1,

        ),

        "asteroid_estimate_annotate_lowest_mag_stars": bool(settings.asteroid_estimate_annotate_lowest_mag_stars),

        "asteroid_visual_show_known_objects": bool(settings.asteroid_visual_show_object_markers),

        "asteroid_visual_show_object_markers": bool(settings.asteroid_visual_show_object_markers),

        "asteroid_visual_show_potential_discoveries": bool(settings.asteroid_visual_show_potential_discoveries),

        "asteroid_visual_label_all_objects": bool(settings.asteroid_visual_label_all_objects),

        "asteroid_visual_show_target_marker": bool(settings.asteroid_visual_show_target_marker),

        "asteroid_track_object_position_mode": _coerce_asteroid_track_object_position_mode(settings.asteroid_track_object_position_mode),

        "asteroid_visual_show_all_crosshairs": bool(settings.asteroid_visual_show_all_crosshairs),

        "asteroid_visual_highlight_selected_object": bool(settings.asteroid_visual_highlight_selected_object),

        "asteroid_visual_invert_annotation_colors": bool(settings.asteroid_visual_invert_annotation_colors),

        "asteroid_target_marker_line_color": _coerce_hex_color(settings.asteroid_target_marker_line_color, default="#ef4444"),

        "asteroid_target_marker_accent_color": _coerce_hex_color(settings.asteroid_target_marker_accent_color, default="#fca5a5"),

        "asteroid_target_marker_text_color": _coerce_hex_color(settings.asteroid_target_marker_text_color, default="#fff1f2"),

        "asteroid_target_marker_outline_color": _coerce_hex_color(settings.asteroid_target_marker_outline_color, default="#ffffff"),

        "asteroid_target_marker_line_width": min(8.0, max(0.5, float(settings.asteroid_target_marker_line_width))),

        "asteroid_blink_frame_duration_ms": min(2000, max(50, int(settings.asteroid_blink_frame_duration_ms))),

        "astrostack_parallel_workers": max(0, int(settings.astrostack_parallel_workers)),

        "asteroid_gif_export_scale_percent": min(400, max(25, int(settings.asteroid_gif_export_scale_percent))),

        "asteroid_mp4_export_scale_percent": min(400, max(25, int(settings.asteroid_mp4_export_scale_percent))),

        "asteroid_gif_export_loop_forever": bool(settings.asteroid_gif_export_loop_forever),

        "synthetic_tracking_crop_radius_pixels": min(65535, max(4, int(settings.synthetic_tracking_crop_radius_pixels))),

        "synthetic_tracking_integration_mode": _coerce_synthetic_tracking_integration_mode(
            settings.synthetic_tracking_integration_mode,
            legacy_combine_mode=settings.synthetic_tracking_combine_mode,
        ),

        "synthetic_tracking_weight_mode": _coerce_synthetic_tracking_weight_mode(
            settings.synthetic_tracking_weight_mode,
            legacy_combine_mode=settings.synthetic_tracking_combine_mode,
        ),

        "synthetic_tracking_rejection_mode": _coerce_synthetic_tracking_rejection_mode(
            settings.synthetic_tracking_rejection_mode,
            legacy_combine_mode=settings.synthetic_tracking_combine_mode,
        ),

        "synthetic_tracking_backend_preference": _coerce_synthetic_tracking_backend_preference(
            settings.synthetic_tracking_backend_preference,
        ),

        "synthetic_tracking_combine_mode": _synthetic_tracking_legacy_combine_mode(
            _coerce_synthetic_tracking_integration_mode(
                settings.synthetic_tracking_integration_mode,
                legacy_combine_mode=settings.synthetic_tracking_combine_mode,
            ),
            _coerce_synthetic_tracking_rejection_mode(
                settings.synthetic_tracking_rejection_mode,
                legacy_combine_mode=settings.synthetic_tracking_combine_mode,
            ),
        ),

        "synthetic_tracking_allow_mixed_all_group": bool(settings.synthetic_tracking_allow_mixed_all_group),

        "synthetic_tracking_advanced_enabled": bool(settings.synthetic_tracking_advanced_enabled),

        "reference_star_min_magnitude": None if settings.reference_star_min_magnitude is None else float(settings.reference_star_min_magnitude),

        "reference_star_max_magnitude": None if settings.reference_star_max_magnitude is None else float(settings.reference_star_max_magnitude),

        "observer_code": _coerce_metadata_text(settings.observer_code),

        "observer_name": _coerce_metadata_text(settings.observer_name),

        "organization": _coerce_metadata_text(settings.organization),

        "site_name": _coerce_metadata_text(settings.site_name),

        "observing_site_latitude_deg": None if settings.observing_site_latitude_deg is None else min(90.0, max(-90.0, float(settings.observing_site_latitude_deg))),

        "observing_site_longitude_deg": None if settings.observing_site_longitude_deg is None else min(180.0, max(-180.0, float(settings.observing_site_longitude_deg))),

        "observing_site_elevation_m": None if settings.observing_site_elevation_m is None else min(12000.0, max(-500.0, float(settings.observing_site_elevation_m))),

        "observing_site_presets": _serialize_observing_site_presets(settings.observing_site_presets),

        "sky_atlas_custom_overlays_enabled": bool(settings.sky_atlas_custom_overlays_enabled),

        "sky_atlas_custom_overlay_opacity": max(0.0, min(1.0, float(settings.sky_atlas_custom_overlay_opacity))),

        "sky_atlas_custom_overlay_brightness": max(0.35, min(2.0, float(settings.sky_atlas_custom_overlay_brightness))),

        "sky_atlas_custom_overlay_saturation": max(0.0, min(2.4, float(settings.sky_atlas_custom_overlay_saturation))),

        "sky_atlas_custom_overlay_feather": max(0.0, min(1.0, float(settings.sky_atlas_custom_overlay_feather))),

        "sky_atlas_custom_overlay_cache_max_long_edge": max(
            512,
            min(8192, int(settings.sky_atlas_custom_overlay_cache_max_long_edge)),
        ),

        "sky_atlas_custom_overlays": _serialize_sky_atlas_custom_overlays(settings.sky_atlas_custom_overlays),

        "sky_atlas_custom_overlay_surveys": _serialize_sky_atlas_custom_overlay_surveys(
            settings.sky_atlas_custom_overlay_surveys
        ),

        "sky_atlas_custom_overlay_active_survey_id": _coerce_metadata_text(
            settings.sky_atlas_custom_overlay_active_survey_id
        ),
        "sky_atlas_star_magnitude_limit": max(3.0, min(15.0, float(settings.sky_atlas_star_magnitude_limit))),
        "sky_atlas_star_brightness": max(0.35, min(2.0, float(settings.sky_atlas_star_brightness))),
        "sky_atlas_star_size": max(0.4, min(2.5, float(settings.sky_atlas_star_size))),
        "sky_atlas_star_magnitude_size_contrast": max(
            0.0, min(2.0, float(settings.sky_atlas_star_magnitude_size_contrast))
        ),
        "sky_atlas_star_twinkle": max(0.0, min(1.0, float(settings.sky_atlas_star_twinkle))),
        "sky_atlas_show_messier": bool(settings.sky_atlas_show_messier),
        "sky_atlas_show_ngc": bool(settings.sky_atlas_show_ngc),
        "sky_atlas_show_ic": bool(settings.sky_atlas_show_ic),
        "sky_atlas_show_vdb": bool(settings.sky_atlas_show_vdb),
        "sky_atlas_show_ldn": bool(settings.sky_atlas_show_ldn),
        "sky_atlas_show_lbn": bool(settings.sky_atlas_show_lbn),
        "sky_atlas_object_magnitude_limit": max(1.0, min(16.0, float(settings.sky_atlas_object_magnitude_limit))),
        "sky_atlas_object_label_magnitude_limit": max(
            1.0, min(16.0, float(settings.sky_atlas_object_label_magnitude_limit))
        ),

        "telescope": _coerce_metadata_text(settings.telescope),

        "telescope_focal_length_mm": None if settings.telescope_focal_length_mm is None else min(100000.0, max(0.1, float(settings.telescope_focal_length_mm))),

        "telescope_aperture_mm": None if settings.telescope_aperture_mm is None else min(100000.0, max(0.1, float(settings.telescope_aperture_mm))),

        "telescope_focal_ratio": None if settings.telescope_focal_ratio is None else min(100.0, max(0.1, float(settings.telescope_focal_ratio))),

        "camera": _coerce_metadata_text(settings.camera),

        "camera_pixel_size_um": None if settings.camera_pixel_size_um is None else min(1000.0, max(0.1, float(settings.camera_pixel_size_um))),

        "bortle_scale": None if settings.bortle_scale is None else min(9, max(1, int(settings.bortle_scale))),

        "filter_system": _coerce_metadata_text(settings.filter_system),

        "aavso_chart_id": _coerce_metadata_text(settings.aavso_chart_id),

        "observation_timezone": _coerce_timezone_name(settings.observation_timezone),

        "time_standard": _coerce_time_standard(settings.time_standard),

        "transformed": bool(settings.transformed),

        "reduction_notes": _coerce_metadata_text(settings.reduction_notes, preserve_newlines=True),

        "preview_variable_star_max_count": max(0, int(settings.preview_variable_star_max_count)),

        "preview_variable_star_min_magnitude": None if settings.preview_variable_star_min_magnitude is None else float(settings.preview_variable_star_min_magnitude),

        "preview_variable_star_max_magnitude": None if settings.preview_variable_star_max_magnitude is None else float(settings.preview_variable_star_max_magnitude),

        "interface_tips_enabled": bool(settings.interface_tips_enabled),

        "show_mode_launcher_on_startup": bool(settings.show_mode_launcher_on_startup),

        "app_mode": settings.app_mode.value,

        "theme": settings.theme if settings.theme in _VALID_THEME_NAMES else _DEFAULT_THEME,

        "custom_theme_colors": _coerce_custom_theme_colors(settings.custom_theme_colors),

        "equatorial_grid_ra_density": min(12, max(2, int(settings.equatorial_grid_ra_density))),

        "equatorial_grid_dec_density": min(12, max(2, int(settings.equatorial_grid_dec_density))),

        "image_equatorial_grid_enabled": bool(settings.image_equatorial_grid_enabled),

        "image_mark_saturated_enabled": bool(settings.image_mark_saturated_enabled),

        "selected_catalog_source_ids": _selected_catalog_source_ids_to_payload(settings.selected_catalog_source_ids or {}),

        "variable_star_selection_percent": settings.variable_star_limit_value if limit_mode == VariableStarLimitMode.PERCENT else 100,

        "manual_photometry_configs": _manual_configs_to_payload(settings.manual_photometry_configs or {}),

        "aperture_presets": _aperture_presets_to_payload(settings.aperture_presets or []),

        "literature_period_cache": _period_cache_to_payload(settings.literature_period_cache or {}),

        "calculated_period_cache": _period_cache_to_payload(settings.calculated_period_cache or {}),

    }





def _coerce_limit_mode(value: VariableStarLimitMode | str) -> VariableStarLimitMode:

    if isinstance(value, VariableStarLimitMode):

        return value

    try:

        return VariableStarLimitMode(str(value).strip().lower())

    except ValueError:

        return VariableStarLimitMode.PERCENT





def _coerce_hr_motion_group_settings(

    *,

    preset: object,

    method: object,

    strictness: object,

    parallax_mode: object,

    refine_hr_consistency: object,

    auto_filter: object,

) -> tuple[str, str, float, str, bool, bool]:

    normalized_preset = str(preset).strip().lower()

    if normalized_preset not in _HR_MOTION_GROUP_PRESET_DEFAULTS and normalized_preset != "custom":

        normalized_preset = "default"

    normalized_auto_filter = bool(auto_filter)

    if normalized_preset != "custom":

        normalized_method, normalized_strictness, normalized_parallax_mode, normalized_refine_hr_consistency = _HR_MOTION_GROUP_PRESET_DEFAULTS[normalized_preset]

        return (

            normalized_preset,

            normalized_method,

            normalized_strictness,

            normalized_parallax_mode,

            normalized_refine_hr_consistency,

            normalized_auto_filter,

        )



    normalized_method = str(method).strip().lower()

    if normalized_method not in _HR_MOTION_GROUP_METHODS:

        normalized_method = "lightweight"

    normalized_parallax_mode = str(parallax_mode).strip().lower()

    if normalized_parallax_mode not in _HR_MOTION_GROUP_PARALLAX_MODES:

        normalized_parallax_mode = "auto"

    return (

        "custom",

        normalized_method,

        min(2.5, max(0.4, float(strictness))),

        normalized_parallax_mode,

        bool(refine_hr_consistency),

        normalized_auto_filter,

    )





def _coerce_splitter_sizes(value: object, *, expected_count: int) -> list[int] | None:

    if not isinstance(value, (list, tuple)) or len(value) != expected_count:

        return None

    normalized_sizes: list[int] = []

    for item in value:

        try:

            size = int(item)

        except (TypeError, ValueError):

            return None

        if size <= 0:

            return None

        normalized_sizes.append(size)

    return normalized_sizes


def _coerce_column_widths(value: object, *, expected_count: int) -> list[int] | None:

    if not isinstance(value, list) or len(value) != expected_count:

        return None

    normalized_widths: list[int] = []

    for item in value:

        try:

            width = int(item)

        except (TypeError, ValueError):

            return None

        if width <= 0:

            return None

        normalized_widths.append(width)

    return normalized_widths


def _coerce_sky_explorer_object_type_column_widths(value: object) -> list[int] | None:

    current_widths = _coerce_column_widths(value, expected_count=6)

    if current_widths is not None:

        return current_widths

    legacy_widths = _coerce_column_widths(value, expected_count=5)

    if legacy_widths is None:

        return None

    return [legacy_widths[0], legacy_widths[1], legacy_widths[2], 150, legacy_widths[3], legacy_widths[4]]


def _coerce_sky_explorer_results_column_widths(value: object) -> list[int] | None:

    current_widths = _coerce_column_widths(value, expected_count=6)

    if current_widths is not None:

        return current_widths

    legacy_widths = _coerce_column_widths(value, expected_count=7)

    if legacy_widths is None:

        return None

    return [legacy_widths[1], legacy_widths[2], legacy_widths[3], legacy_widths[4], legacy_widths[5], legacy_widths[6]]


def _normalize_hex_color(value: object) -> str | None:

    if not isinstance(value, str):

        return None

    normalized = value.strip().lower()

    if len(normalized) != 7 or not normalized.startswith("#"):

        return None

    if any(character not in "0123456789abcdef" for character in normalized[1:]):

        return None

    return normalized





def _coerce_image_stretch_mode(value: object) -> str:

    normalized = str(value or "stf").strip().lower()

    return normalized if normalized in _ANNOTATED_IMAGE_STRETCH_MODES else "stf"





def _coerce_hr_marker_size_mode(value: object) -> str:

    normalized = str(value or "scaled").strip().lower()

    return normalized if normalized in _HR_MARKER_SIZE_MODES else "scaled"





def _coerce_synthetic_tracking_combine_mode(value: object) -> str:

    normalized = str(value or "mean").strip().lower()

    return normalized if normalized in _SYNTHETIC_TRACKING_COMBINE_MODES else "mean"


def _coerce_synthetic_tracking_integration_mode(value: object, *, legacy_combine_mode: str | None = None) -> str:

    normalized = str(value or "").strip().lower()
    if normalized in _SYNTHETIC_TRACKING_INTEGRATION_MODES:
        return normalized
    if _coerce_synthetic_tracking_combine_mode(legacy_combine_mode) == "sigma_clipped_mean":
        return "average"
    return "average"


def _coerce_synthetic_tracking_weight_mode(value: object, *, legacy_combine_mode: str | None = None) -> str:

    normalized = str(value or "").strip().lower()
    if normalized in _SYNTHETIC_TRACKING_WEIGHT_MODES:
        return normalized
    del legacy_combine_mode
    return "psf_signal_weight"


def _coerce_synthetic_tracking_rejection_mode(value: object, *, legacy_combine_mode: str | None = None) -> str:

    normalized = str(value or "").strip().lower()
    if normalized in _SYNTHETIC_TRACKING_REJECTION_MODES:
        return normalized
    if _coerce_synthetic_tracking_combine_mode(legacy_combine_mode) == "sigma_clipped_mean":
        return "sigma_clipping"
    return "no_rejection"


def _coerce_synthetic_tracking_backend_preference(value: object) -> str:

    normalized = str(value or "").strip().lower()
    if normalized in _SYNTHETIC_TRACKING_BACKEND_PREFERENCES:
        return normalized
    return "auto"


def _synthetic_tracking_legacy_combine_mode(integration_mode: object, rejection_mode: object) -> str:

    normalized_integration = _coerce_synthetic_tracking_integration_mode(integration_mode)
    normalized_rejection = _coerce_synthetic_tracking_rejection_mode(rejection_mode)
    if normalized_integration == "average" and normalized_rejection == "sigma_clipping":
        return "sigma_clipped_mean"
    return "mean"


def _coerce_asteroid_track_object_position_mode(value: object) -> str:

    normalized = str(value or "predicted").strip().lower()

    return normalized if normalized in {"predicted", "measured"} else "predicted"


def _coerce_discovery_synthetic_sweep_direction_focus(value: object) -> str:

    normalized = str(value or "all_directions").strip().lower()

    return normalized if normalized in _DISCOVERY_SYNTHETIC_SWEEP_DIRECTION_FOCUS_MODES else "all_directions"





def _coerce_metadata_text(value: object, preserve_newlines: bool = False) -> str:

    if not isinstance(value, str):

        return ""

    normalized = value.replace("\r\n", "\n").replace("\r", "\n") if preserve_newlines else value

    return normalized.strip()





def _coerce_hex_color(value: object, *, default: str) -> str:

    normalized = _normalize_hex_color(value)

    return default if normalized is None else normalized


def _coerce_sky_explorer_simbad_search_radius_arcsec(value: object) -> float:

    try:

        numeric = float(value)

    except (TypeError, ValueError):

        return 10.0

    return min(300.0, max(1.0, numeric))


def _coerce_sky_explorer_gaia_max_magnitude(value: object) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 17.0
    return min(30.0, max(-5.0, numeric))


def _coerce_distance_map_max_magnitude(value: object) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 17.0
    return min(22.0, max(1.0, numeric))


def _coerce_distance_map_max_distance_pc(value: object) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 500.0
    return min(50000.0, max(1.0, numeric))


def _coerce_distance_map_max_stars(value: object) -> int:
    from photometry_app.core.distance_map import DISTANCE_MAP_MAX_STAR_COUNT

    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return 500
    return min(DISTANCE_MAP_MAX_STAR_COUNT, max(10, numeric))


def _coerce_distance_map_min_parallax_snr(value: object) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 5.0
    return min(50.0, max(0.0, numeric))


def _coerce_distance_map_cluster_settings(
    *,
    preset: object,
    method: object,
    strictness: object,
    parallax_mode: object,
    refine_magnitude_consistency: object,
    auto_filter: object,
    mark_on_image: object,
):
    from photometry_app.core.distance_map_clusters import coerce_distance_map_cluster_settings

    return coerce_distance_map_cluster_settings(
        preset=preset,
        method=method,
        strictness=strictness,
        parallax_mode=parallax_mode,
        refine_magnitude_consistency=refine_magnitude_consistency,
        auto_filter=auto_filter,
        mark_on_image=mark_on_image,
    )


def _coerce_sky_explorer_annotated_galaxy_max_magnitude(value: object) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 17.0
    return min(30.0, max(-5.0, numeric))


def _coerce_sky_explorer_gaia_hard_cap_rows(value: object) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return 1000
    return min(50000, max(1, numeric))


def _coerce_sky_explorer_mag_limit_examples_per_bin(value: object) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return 1
    return min(10, max(1, numeric))


def _coerce_sky_explorer_mag_limit_target_size(value: object) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 6.0
    return min(40.0, max(2.0, numeric))


def _coerce_sky_explorer_mag_limit_text_size(value: object) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 9.0
    return min(24.0, max(7.0, numeric))


def _coerce_sky_explorer_mag_limit_marker_stroke_width(value: object) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 2.0
    return min(8.0, max(0.0, numeric))


def _coerce_sky_explorer_mag_limit_text_stroke_width(value: object) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(6.0, max(0.0, numeric))


def _coerce_sky_explorer_opacity(value: object, *, default: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = float(default)
    return min(1.0, max(0.0, numeric))


def _coerce_sky_explorer_object_group_color_overrides(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    overrides: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        normalized_key = key.strip()
        normalized_color = _normalize_hex_color(item)
        if not normalized_key or normalized_color is None:
            continue
        overrides[normalized_key] = normalized_color
    return overrides


def _coerce_sky_explorer_enabled_layers(value: object) -> tuple[str, ...]:

    if value is None:

        return default_sky_explorer_enabled_layers()

    if not isinstance(value, (list, tuple)):

        return default_sky_explorer_enabled_layers()

    normalized_layers: list[str] = []

    for item in value:

        normalized = str(item or "").strip().lower()

        if normalized in _SKY_EXPLORER_QUERY_LAYER_KEYS and normalized not in normalized_layers:

            normalized_layers.append(normalized)

    return tuple(normalized_layers)


def _coerce_sky_explorer_object_type_color_overrides(value: object) -> dict[str, tuple[str, str]]:

    if not isinstance(value, dict):

        return {}

    overrides: dict[str, tuple[str, str]] = {}

    for key, item in value.items():

        if not isinstance(key, str):

            continue

        normalized_key = key.strip()

        if not normalized_key:

            continue

        stroke_value: object | None = None

        fill_value: object | None = None

        if isinstance(item, dict):

            stroke_value = item.get("stroke")

            fill_value = item.get("fill")

        elif isinstance(item, (list, tuple)) and len(item) == 2:

            stroke_value, fill_value = item

        stroke_color = _normalize_hex_color(stroke_value)

        fill_color = _normalize_hex_color(fill_value)

        if stroke_color is None or fill_color is None:

            continue

        overrides[normalized_key] = (stroke_color, fill_color)

    return overrides


def _sky_explorer_object_type_color_overrides_to_payload(value: dict[str, tuple[str, str]] | None) -> dict[str, dict[str, str]]:

    if not isinstance(value, dict):

        return {}

    payload: dict[str, dict[str, str]] = {}

    for key, item in value.items():

        if not isinstance(key, str) or not isinstance(item, (tuple, list)) or len(item) != 2:

            continue

        stroke_color = _normalize_hex_color(item[0])

        fill_color = _normalize_hex_color(item[1])

        if stroke_color is None or fill_color is None:

            continue

        normalized_key = key.strip()

        if not normalized_key:

            continue

        payload[normalized_key] = {"stroke": stroke_color, "fill": fill_color}

    return payload


def _coerce_sky_explorer_object_type_text_color_overrides(value: object) -> dict[str, str]:

    if not isinstance(value, dict):

        return {}

    overrides: dict[str, str] = {}

    for key, item in value.items():

        if not isinstance(key, str):

            continue

        normalized_key = key.strip()

        normalized_color = _normalize_hex_color(item)

        if not normalized_key or normalized_color is None:

            continue

        overrides[normalized_key] = normalized_color

    return overrides


def _coerce_sky_explorer_marker_color_relation(value: object) -> str:

    normalized = str(value or "").strip().lower()

    if normalized in {"stroke_bright_fill_dark", "invert", "inverted", "bright_stroke", "fill_dark"}:

        return "stroke_bright_fill_dark"

    return "stroke_dark_fill_bright"


def _coerce_sky_explorer_text_color_relation(value: object) -> str:

    normalized = str(value or "").strip().lower()

    return "bright" if normalized == "bright" else "dark"


def _coerce_sky_explorer_object_type_font_overrides(value: object) -> dict[str, str]:

    if not isinstance(value, dict):

        return {}

    overrides: dict[str, str] = {}

    for key, item in value.items():

        if not isinstance(key, str) or not isinstance(item, str):

            continue

        normalized_key = key.strip()

        normalized_font = item.strip()

        if not normalized_key or not normalized_font:

            continue

        overrides[normalized_key] = normalized_font

    return overrides





def _coerce_time_standard(value: object) -> str:

    normalized = _coerce_metadata_text(value).upper()

    return normalized or "UTC"





def _coerce_timezone_name(value: object) -> str:

    if not isinstance(value, str):

        return "UTC"

    normalized = value.strip()

    return normalized or "UTC"





def _coerce_optional_float(value: object, *, minimum: float, maximum: float) -> float | None:

    if value in (None, ""):

        return None

    try:

        numeric = float(value)

    except (TypeError, ValueError):

        return None

    return min(maximum, max(minimum, numeric))
    
def _coerce_observing_site_presets(value: object) -> list[ObservingSitePreset] | None:
    if not isinstance(value, list):
        return None
    presets: list[ObservingSitePreset] = []
    seen_keys: set[tuple[str, float, float, float | None]] = set()
    for entry in value[:32]:
        if not isinstance(entry, dict):
            continue
        name = _coerce_metadata_text(entry.get("name"))
        latitude_deg = _coerce_optional_float(entry.get("latitude_deg"), minimum=-90.0, maximum=90.0)
        longitude_deg = _coerce_optional_float(entry.get("longitude_deg"), minimum=-180.0, maximum=180.0)
        elevation_m = _coerce_optional_float(entry.get("elevation_m"), minimum=-500.0, maximum=12000.0)
        if latitude_deg is None or longitude_deg is None:
            continue
        normalized_key = (
            name.casefold(),
            round(float(latitude_deg), 6),
            round(float(longitude_deg), 6),
            None if elevation_m is None else round(float(elevation_m), 2),
        )
        if normalized_key in seen_keys:
            continue
        seen_keys.add(normalized_key)
        presets.append(
            ObservingSitePreset(
                name=name,
                latitude_deg=float(latitude_deg),
                longitude_deg=float(longitude_deg),
                elevation_m=None if elevation_m is None else float(elevation_m),
            )
        )
    return presets or None

def _serialize_observing_site_presets(presets: list[ObservingSitePreset] | None) -> list[dict[str, object]] | None:
    if not presets:
        return None
    serialized: list[dict[str, object]] = []
    for preset in presets:
        serialized.append(
            {
                "name": _coerce_metadata_text(preset.name),
                "latitude_deg": min(90.0, max(-90.0, float(preset.latitude_deg))),
                "longitude_deg": min(180.0, max(-180.0, float(preset.longitude_deg))),
                "elevation_m": None if preset.elevation_m is None else min(12000.0, max(-500.0, float(preset.elevation_m))),
            }
        )
    return serialized





def _coerce_sky_atlas_custom_overlays(value: object) -> list[SkyAtlasCustomOverlayRecord] | None:
    if not isinstance(value, list):
        return None
    overlays: list[SkyAtlasCustomOverlayRecord] = []
    seen_ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        overlay_id = _coerce_metadata_text(item.get("overlay_id"))
        display_name = _coerce_metadata_text(item.get("display_name"))
        cached_image_path = _coerce_metadata_text(item.get("cached_image_path"))
        cached_wcs_path = _coerce_metadata_text(item.get("cached_wcs_path"))
        source_image_path = _coerce_metadata_text(item.get("source_image_path"))
        width = _coerce_optional_float(item.get("width"), minimum=1.0, maximum=100000.0)
        height = _coerce_optional_float(item.get("height"), minimum=1.0, maximum=100000.0)
        if not overlay_id or not display_name or not cached_image_path or not cached_wcs_path or width is None or height is None:
            continue
        if overlay_id in seen_ids:
            continue
        seen_ids.add(overlay_id)
        overlays.append(
            SkyAtlasCustomOverlayRecord(
                overlay_id=overlay_id,
                display_name=display_name,
                cached_image_path=cached_image_path,
                cached_wcs_path=cached_wcs_path,
                source_image_path=source_image_path,
                width=int(width),
                height=int(height),
            )
        )
    return overlays or None


def _serialize_sky_atlas_custom_overlays(
    overlays: list[SkyAtlasCustomOverlayRecord] | None,
) -> list[dict[str, object]] | None:
    if not overlays:
        return None
    serialized: list[dict[str, object]] = []
    for overlay in overlays:
        serialized.append(
            {
                "overlay_id": _coerce_metadata_text(overlay.overlay_id),
                "display_name": _coerce_metadata_text(overlay.display_name),
                "cached_image_path": _coerce_metadata_text(overlay.cached_image_path),
                "cached_wcs_path": _coerce_metadata_text(overlay.cached_wcs_path),
                "source_image_path": _coerce_metadata_text(overlay.source_image_path),
                "width": max(1, int(overlay.width)),
                "height": max(1, int(overlay.height)),
            }
        )
    return serialized


def _coerce_sky_atlas_custom_overlay_surveys(
    value: object,
) -> list[SkyAtlasCustomOverlaySurvey] | None:
    if not isinstance(value, list):
        return None
    surveys: list[SkyAtlasCustomOverlaySurvey] = []
    seen_ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        survey_id = _coerce_metadata_text(item.get("survey_id"))
        name = _coerce_metadata_text(item.get("name"))
        filter_name = _coerce_metadata_text(item.get("filter_name")) or ""
        overlays = _coerce_sky_atlas_custom_overlays(item.get("overlays")) or []
        if not survey_id or not name:
            continue
        if survey_id in seen_ids:
            continue
        seen_ids.add(survey_id)
        surveys.append(
            SkyAtlasCustomOverlaySurvey(
                survey_id=survey_id,
                name=name,
                filter_name=filter_name,
                overlays=overlays,
            )
        )
    return surveys or None


def _serialize_sky_atlas_custom_overlay_surveys(
    surveys: list[SkyAtlasCustomOverlaySurvey] | None,
) -> list[dict[str, object]] | None:
    if not surveys:
        return None
    serialized: list[dict[str, object]] = []
    for survey in surveys:
        overlay_payload = _serialize_sky_atlas_custom_overlays(survey.overlays)
        serialized.append(
            {
                "survey_id": _coerce_metadata_text(survey.survey_id),
                "name": _coerce_metadata_text(survey.name),
                "filter_name": _coerce_metadata_text(survey.filter_name) or "",
                "overlays": overlay_payload or [],
            }
        )
    return serialized


def _migrate_sky_atlas_custom_overlay_surveys(
    surveys: list[SkyAtlasCustomOverlaySurvey] | None,
    legacy_overlays: list[SkyAtlasCustomOverlayRecord] | None,
    active_survey_id: str | None,
) -> tuple[list[SkyAtlasCustomOverlaySurvey] | None, str | None]:
    if surveys:
        survey_ids = {survey.survey_id for survey in surveys}
        if active_survey_id not in survey_ids:
            active_survey_id = surveys[0].survey_id if surveys else None
        return surveys, active_survey_id
    if not legacy_overlays:
        return None, None

    migrated_survey_id = uuid.uuid4().hex
    migrated = [
        SkyAtlasCustomOverlaySurvey(
            survey_id=migrated_survey_id,
            name="Imported overlays",
            filter_name="",
            overlays=list(legacy_overlays),
        )
    ]
    return migrated, migrated_survey_id


def sky_atlas_custom_overlay_surveys_for_settings(
    settings: AppSettings,
) -> list[SkyAtlasCustomOverlaySurvey]:
    from photometry_app.core.sky_atlas_survey_storage import (
        discover_surveys,
        migrate_legacy_survey_to_disk,
        resolve_survey_overlay_paths,
    )

    try:
        discovered = discover_surveys()
    except Exception:
        discovered = []
    if discovered:
        return discovered

    surveys: list[SkyAtlasCustomOverlaySurvey] = []
    if settings.sky_atlas_custom_overlay_surveys:
        surveys = list(settings.sky_atlas_custom_overlay_surveys)
    elif settings.sky_atlas_custom_overlays:
        migrated, _active_id = _migrate_sky_atlas_custom_overlay_surveys(
            None,
            settings.sky_atlas_custom_overlays,
            settings.sky_atlas_custom_overlay_active_survey_id,
        )
        surveys = list(migrated or [])

    if not surveys:
        return []

    resolved: list[SkyAtlasCustomOverlaySurvey] = []
    for survey in surveys:
        try:
            resolved.append(resolve_survey_overlay_paths(migrate_legacy_survey_to_disk(survey)))
        except Exception:
            resolved.append(resolve_survey_overlay_paths(survey))
    return resolved


def sky_atlas_custom_overlays_for_active_survey(
    settings: AppSettings,
) -> list[SkyAtlasCustomOverlayRecord]:
    from photometry_app.core.sky_atlas_survey_storage import resolve_overlay_record_for_survey

    surveys = sky_atlas_custom_overlay_surveys_for_settings(settings)
    if not surveys:
        return []
    active_survey_id = settings.sky_atlas_custom_overlay_active_survey_id
    if active_survey_id:
        for survey in surveys:
            if survey.survey_id == active_survey_id:
                return [
                    resolve_overlay_record_for_survey(survey.survey_id, overlay)
                    for overlay in survey.overlays
                ]
    first_survey = surveys[0]
    return [
        resolve_overlay_record_for_survey(first_survey.survey_id, overlay)
        for overlay in first_survey.overlays
    ]


def _coerce_designation_filters(value: object) -> list[VariableStarDesignationFamily]:

    if not isinstance(value, list):

        return list(VariableStarDesignationFamily)



    selected: list[VariableStarDesignationFamily] = []

    for item in value:

        try:

            family = item if isinstance(item, VariableStarDesignationFamily) else VariableStarDesignationFamily(str(item).strip().lower())

        except ValueError:

            continue

        if family not in selected:

            selected.append(family)

    return selected or list(VariableStarDesignationFamily)





def _coerce_selected_catalog_source_ids(value: object) -> dict[str, list[str]]:

    if not isinstance(value, dict):

        return {}



    selections: dict[str, list[str]] = {}

    for object_name, item in value.items():

        if not isinstance(object_name, str) or not isinstance(item, list):

            continue

        source_ids: list[str] = []

        for source_key in item:

            if not isinstance(source_key, str):

                continue

            normalized = source_key.strip()

            if normalized and normalized not in source_ids:

                source_ids.append(normalized)

        if source_ids:

            selections[object_name] = source_ids

    return selections





def _selected_catalog_source_ids_to_payload(value: dict[str, list[str]]) -> dict[str, list[str]]:

    return {

        object_name: [source_key for source_key in source_keys if isinstance(source_key, str) and source_key.strip()]

        for object_name, source_keys in value.items()

        if isinstance(object_name, str) and isinstance(source_keys, list)

    }





def _coerce_period_cache(value: object) -> dict[str, dict[str, dict[str, object]]]:

    if not isinstance(value, dict):

        return {}



    caches: dict[str, dict[str, dict[str, object]]] = {}

    for object_name, cache_entries in value.items():

        if not isinstance(object_name, str) or not isinstance(cache_entries, dict):

            continue

        normalized_entries: dict[str, dict[str, object]] = {}

        for cache_key, cache_payload in cache_entries.items():

            if not isinstance(cache_key, str) or not isinstance(cache_payload, dict):

                continue

            normalized_entries[cache_key.strip()] = {

                key: entry_value

                for key, entry_value in cache_payload.items()

                if isinstance(key, str)

            }

        if normalized_entries:

            caches[object_name] = normalized_entries

    return caches





def _period_cache_to_payload(value: dict[str, dict[str, dict[str, object]]]) -> dict[str, dict[str, dict[str, object]]]:

    return {

        object_name: {

            cache_key: {

                key: entry_value

                for key, entry_value in cache_payload.items()

                if isinstance(key, str)

            }

            for cache_key, cache_payload in cache_entries.items()

            if isinstance(cache_key, str) and isinstance(cache_payload, dict)

        }

        for object_name, cache_entries in value.items()

        if isinstance(object_name, str) and isinstance(cache_entries, dict)

    }





def default_custom_theme_colors() -> dict[str, str]:

    return dict(_DEFAULT_CUSTOM_THEME_COLORS)





def _coerce_custom_theme_colors(value: object) -> dict[str, str]:

    colors = default_custom_theme_colors()

    if not isinstance(value, dict):

        return colors

    numeric_ranges = {

        "asteroid_other_overlay_line_width": (0.5, 8.0),

        "asteroid_other_overlay_text_size": (7.0, 24.0),

        "asteroid_overlay_line_width": (0.5, 8.0),

        "asteroid_overlay_text_size": (7.0, 24.0),

    }

    for key in colors:

        item = value.get(key)

        if isinstance(item, str):

            normalized = item.strip().lower()

            if key in numeric_ranges:

                try:

                    numeric = float(normalized)

                except ValueError:

                    continue

                minimum, maximum = numeric_ranges[key]

                colors[key] = f"{min(maximum, max(minimum, numeric)):g}"

                continue

            if len(normalized) == 7 and normalized.startswith("#") and all(character in "0123456789abcdef" for character in normalized[1:]):

                colors[key] = normalized

    return colors





def setup_pixel_scale_arcsec_per_pixel(settings: AppSettings) -> float | None:

    focal_length_mm = settings.telescope_focal_length_mm

    pixel_size_um = settings.camera_pixel_size_um

    if focal_length_mm is None or pixel_size_um is None:

        return None

    if focal_length_mm <= 0 or pixel_size_um <= 0:

        return None

    return 206.265 * (float(pixel_size_um) / float(focal_length_mm))





def _coerce_manual_configs(value: object) -> dict[str, ManualPhotometryConfig]:

    if not isinstance(value, dict):

        return {}



    configs: dict[str, ManualPhotometryConfig] = {}

    for object_name, payload in value.items():

        if not isinstance(object_name, str) or not isinstance(payload, dict):

            continue

        configs[object_name] = _manual_config_from_payload(object_name, payload)

    return configs





def _manual_config_from_payload(object_name: str, payload: dict[str, object]) -> ManualPhotometryConfig:

    mode_value = str(payload.get("mode", ObjectPhotometryMode.AUTO.value)).strip().lower()

    recenter_value = str(payload.get("recenter_mode", RecenterMode.CENTROID.value)).strip().lower()

    try:

        mode = ObjectPhotometryMode(mode_value)

    except ValueError:

        mode = ObjectPhotometryMode.AUTO

    try:

        recenter_mode = RecenterMode(recenter_value)

    except ValueError:

        recenter_mode = RecenterMode.CENTROID



    raw_sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []

    sources: list[ManualSourceConfig] = []

    for item in raw_sources:

        if not isinstance(item, dict):

            continue

        role_value = str(item.get("role", ManualSourceRole.COMPARISON.value)).strip().lower()

        try:

            role = ManualSourceRole(role_value)

        except ValueError:

            role = ManualSourceRole.COMPARISON

        try:

            sources.append(

                ManualSourceConfig(

                    source_id=str(item.get("source_id") or f"manual-{len(sources) + 1}"),

                    name=str(item.get("name") or f"Manual {role.value.title()}"),

                    role=role,

                    ra_deg=float(item["ra_deg"]),

                    dec_deg=float(item["dec_deg"]),

                    reference_frame_name=str(item.get("reference_frame_name") or payload.get("reference_frame_name") or ""),

                    reference_x=float(item["reference_x"]),

                    reference_y=float(item["reference_y"]),

                    aperture_radius=max(1.0, float(item.get("aperture_radius", 5.0))),

                    annulus_inner_radius=max(1.5, float(item.get("annulus_inner_radius", 8.0))),

                    annulus_outer_radius=max(2.0, float(item.get("annulus_outer_radius", 12.0))),

                )

            )

        except (KeyError, TypeError, ValueError):

            continue



    return ManualPhotometryConfig(

        object_name=object_name,

        mode=mode,

        reference_frame_name=str(payload.get("reference_frame_name")) if payload.get("reference_frame_name") else None,

        keep_comparison_stars=bool(payload.get("keep_comparison_stars", True)),

        recenter_mode=recenter_mode,

        max_recenter_radius_pixels=max(1.0, float(payload.get("max_recenter_radius_pixels", 6.0))),

        fallback_to_wcs_on_centroid_failure=bool(payload.get("fallback_to_wcs_on_centroid_failure", True)),

        sources=sources,

    )





def _manual_configs_to_payload(configs: dict[str, ManualPhotometryConfig]) -> dict[str, object]:

    payload: dict[str, object] = {}

    for object_name, config in configs.items():

        payload[object_name] = {

            "mode": config.mode.value,

            "reference_frame_name": config.reference_frame_name,

            "keep_comparison_stars": config.keep_comparison_stars,

            "recenter_mode": config.recenter_mode.value,

            "max_recenter_radius_pixels": config.max_recenter_radius_pixels,

            "fallback_to_wcs_on_centroid_failure": config.fallback_to_wcs_on_centroid_failure,

            "sources": [

                {

                    "source_id": item.source_id,

                    "name": item.name,

                    "role": item.role.value,

                    "ra_deg": item.ra_deg,

                    "dec_deg": item.dec_deg,

                    "reference_frame_name": item.reference_frame_name,

                    "reference_x": item.reference_x,

                    "reference_y": item.reference_y,

                    "aperture_radius": item.aperture_radius,

                    "annulus_inner_radius": item.annulus_inner_radius,

                    "annulus_outer_radius": item.annulus_outer_radius,

                }

                for item in config.sources

            ],

        }

    return payload


def _coerce_image_display_curve_points(value: object) -> tuple[tuple[float, float], ...]:

    if value in (None, ""):

        return ()

    if not isinstance(value, (list, tuple)):

        return ()

    points: list[tuple[float, float]] = []

    for raw_point in value:

        if not isinstance(raw_point, (list, tuple)) or len(raw_point) != 2:

            continue

        try:

            x_value = min(1.0, max(0.0, float(raw_point[0])))

            y_value = min(1.0, max(0.0, float(raw_point[1])))

        except (TypeError, ValueError):

            continue

        points.append((x_value, y_value))

    if len(points) < 2:

        return ()

    points.sort(key=lambda point: point[0])

    deduplicated: list[tuple[float, float]] = []

    for x_value, y_value in points[:16]:

        if deduplicated and abs(x_value - deduplicated[-1][0]) <= 1e-5:

            deduplicated[-1] = (x_value, y_value)

        else:

            deduplicated.append((x_value, y_value))

    if len(deduplicated) == 2 and deduplicated[0] == (0.0, 0.0) and deduplicated[1] == (1.0, 1.0):

        return ()

    return tuple(deduplicated) if len(deduplicated) >= 2 else ()



def _coerce_aperture_presets(value: object) -> list[AperturePreset]:

    if not isinstance(value, list):

        return []



    presets: list[AperturePreset] = []

    for item in value:

        if not isinstance(item, dict):

            continue

        recenter_value = str(item.get("recenter_mode", RecenterMode.CENTROID.value)).strip().lower()

        try:

            recenter_mode = RecenterMode(recenter_value)

        except ValueError:

            recenter_mode = RecenterMode.CENTROID

        try:

            presets.append(

                AperturePreset(

                    name=str(item.get("name") or f"Preset {len(presets) + 1}"),

                    aperture_radius=max(1.0, float(item.get("aperture_radius", 5.0))),

                    annulus_inner_radius=max(1.5, float(item.get("annulus_inner_radius", 8.0))),

                    annulus_outer_radius=max(2.0, float(item.get("annulus_outer_radius", 12.0))),

                    recenter_mode=recenter_mode,

                    max_recenter_radius_pixels=max(1.0, float(item.get("max_recenter_radius_pixels", 6.0))),

                    fallback_to_wcs_on_centroid_failure=bool(item.get("fallback_to_wcs_on_centroid_failure", True)),

                    comparison_source_ids=[str(source_id) for source_id in item.get("comparison_source_ids", [])],

                )

            )

        except (TypeError, ValueError):

            continue

    return presets





def _aperture_presets_to_payload(presets: list[AperturePreset]) -> list[dict[str, object]]:

    return [

        {

            "name": item.name,

            "aperture_radius": item.aperture_radius,

            "annulus_inner_radius": item.annulus_inner_radius,

            "annulus_outer_radius": item.annulus_outer_radius,

            "recenter_mode": item.recenter_mode.value,

            "max_recenter_radius_pixels": item.max_recenter_radius_pixels,

            "fallback_to_wcs_on_centroid_failure": item.fallback_to_wcs_on_centroid_failure,

            "comparison_source_ids": item.comparison_source_ids,

        }

        for item in presets

    ]



