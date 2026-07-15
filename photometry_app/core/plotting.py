from __future__ import annotations



from dataclasses import dataclass

from pathlib import Path



import numpy as np

from astropy.stats import sigma_clipped_stats

from astropy.time import Time

from astropy.timeseries import BoxLeastSquares, LombScargle

from astropy.visualization import AsinhStretch, ImageNormalize, PercentileInterval

from matplotlib import dates as mdates

from matplotlib.patches import Circle
from matplotlib.ticker import AutoMinorLocator



from photometry_app.core.image_io import read_image_data

from photometry_app.core.models import LightCurvePoint, LightCurveSeries, PhotometryMeasurement

from photometry_app.core.settings import default_custom_theme_colors





@dataclass(frozen=True)

class LightCurveFitConfig:

    mode: str = "polynomial"

    polynomial_degree: int = 3

    periodic_period_hours: float = 4.0

    periodic_harmonics: int = 2

    spline_smoothing: float = 0.35

    robust_iterations: int = 4





@dataclass(frozen=True)

class AnnotatedImageDisplay:

    image_path: Path

    normalized_data: np.ndarray

    norm: ImageNormalize

    preview_normalized: np.ndarray | None = None

    asinh_preview: np.ndarray | None = None

    auto_stretch_preview: np.ndarray | None = None

    linear_preview_normalized: np.ndarray | None = None

    color_preview_normalized: np.ndarray | None = None

    color_asinh_preview: np.ndarray | None = None

    color_auto_stretch_preview: np.ndarray | None = None

    color_linear_preview_normalized: np.ndarray | None = None

    recommended_stretch_mode: str = "stf"





@dataclass(frozen=True)

class AnnotatedImageRenderSettings:

    stretch_mode: str = "stf"

    black_point: float = 0.0

    midtone_point: float = 0.5

    white_point: float = 1.0

    curve_points: tuple[tuple[float, float], ...] = ()

    brightness: float = 0.0

    contrast: float = 1.0

    inverted: bool = False





@dataclass(frozen=True)

class LightCurveRenderPoint:

    x: float

    y: float

    y_error: float | None

    fit_weight: float | None

    source_point: LightCurvePoint





@dataclass(frozen=True)

class LightCurvePlotPayload:

    title: str

    y_axis_label: str

    x_axis_label: str

    x_axis_mode: str

    invert_y: bool

    points: tuple[LightCurveRenderPoint, ...] = ()

    fit_x_values: np.ndarray | None = None

    fit_y_values: np.ndarray | None = None

    empty_message: str | None = None

    index_labels: tuple[str, ...] = ()

    x_limits: tuple[float, float] | None = None





@dataclass(frozen=True)

class FitPeriodInferenceResult:

    period_hours: float

    periodic_harmonics: int | None = None

    method: str = "harmonic_fit"

    eclipse_duration_hours: float | None = None





_PERIOD_CONVENTION_STANDARD = "standard"

_PERIOD_CONVENTION_ECLIPSING_BINARY = "eclipsing_binary"

_ECLIPSING_BINARY_DOUBLED_PERIOD_BIC_TOLERANCE = 6.0

_ECLIPSING_BINARY_PHASE_BINS = 40

_PERIOD_SEARCH_EDGE_RELATIVE_TOLERANCE = 0.005

_ANNOTATED_IMAGE_BACKGROUND_SIGMA = 1.25

_ANNOTATED_IMAGE_STF_SHADOW_SIGMA = 2.8

_ANNOTATED_IMAGE_STF_TARGET_BACKGROUND = 0.25

_ANNOTATED_IMAGE_STF_NATIVE_RANGE_FLOOR = 65535.0

_ANNOTATED_IMAGE_HIGHLIGHT_PERCENTILE = 99.85

_LIKELY_ALREADY_STRETCHED_SUFFIXES = {".jpg", ".jpeg"}

_RASTER_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}





def resolve_light_curve_theme_colors(theme: str, custom_colors: dict[str, str] | None = None) -> dict[str, str]:

    normalized_theme = str(theme).strip().lower()

    if normalized_theme == "dark":

        return {

            "background_color": "#181a1f",

            "axis_color": "#d8dee9",

            "grid_alpha": "0.32",

            "grid_color": "#4c566a",

            "empty_text_color": "#9aa5b1",

            "error_bar_color": "#8b949e",

            "point_pen": "#7cc7ff",

            "point_brush": "#2f81f7",

            "hover_pen": "#b6e3ff",

            "hover_brush": "#9cdcfe",

            "fit_curve_color": "#ff9f1c",

            "selection_color": "#ff9f1c",

            "hover_style": "background-color: #2b2b2b; color: #f2f2f2; border: 1px solid #6b6b6b; padding: 4px 6px; border-radius: 3px;",

        }

    if normalized_theme == "dracula":

        return {

            "background_color": "#1e1f29",

            "axis_color": "#f8f8f2",

            "grid_alpha": "0.28",

            "grid_color": "#44475a",

            "empty_text_color": "#bdc0cc",

            "error_bar_color": "#bd93f9",

            "point_pen": "#8be9fd",

            "point_brush": "#50fa7b",

            "hover_pen": "#f1fa8c",

            "hover_brush": "#f1fa8c",

            "fit_curve_color": "#ffb86c",

            "selection_color": "#ff79c6",

            "hover_style": "background-color: #282a36; color: #f8f8f2; border: 1px solid #6272a4; padding: 4px 6px; border-radius: 3px;",

        }

    if normalized_theme == "nord":

        return {

            "background_color": "#2e3440",

            "axis_color": "#eceff4",

            "grid_alpha": "0.26",

            "grid_color": "#4c566a",

            "empty_text_color": "#d8dee9",

            "error_bar_color": "#88c0d0",

            "point_pen": "#88c0d0",

            "point_brush": "#5e81ac",

            "hover_pen": "#a3be8c",

            "hover_brush": "#a3be8c",

            "fit_curve_color": "#ebcb8b",

            "selection_color": "#bf616a",

            "hover_style": "background-color: #3b4252; color: #eceff4; border: 1px solid #81a1c1; padding: 4px 6px; border-radius: 3px;",

        }

    if normalized_theme == "tokyo-night":

        return {

            "background_color": "#1a1b26",

            "axis_color": "#c0caf5",

            "grid_alpha": "0.28",

            "grid_color": "#414868",

            "empty_text_color": "#a9b1d6",

            "error_bar_color": "#7aa2f7",

            "point_pen": "#7aa2f7",

            "point_brush": "#bb9af7",

            "hover_pen": "#9ece6a",

            "hover_brush": "#9ece6a",

            "fit_curve_color": "#ff9e64",

            "selection_color": "#f7768e",

            "hover_style": "background-color: #24283b; color: #c0caf5; border: 1px solid #565f89; padding: 4px 6px; border-radius: 3px;",

        }

    if normalized_theme == "gruvbox":

        return {

            "background_color": "#282828",

            "axis_color": "#ebdbb2",

            "grid_alpha": "0.26",

            "grid_color": "#504945",

            "empty_text_color": "#d5c4a1",

            "error_bar_color": "#83a598",

            "point_pen": "#83a598",

            "point_brush": "#8ec07c",

            "hover_pen": "#fabd2f",

            "hover_brush": "#fabd2f",

            "fit_curve_color": "#fe8019",

            "selection_color": "#fb4934",

            "hover_style": "background-color: #3c3836; color: #ebdbb2; border: 1px solid #665c54; padding: 4px 6px; border-radius: 3px;",

        }

    if normalized_theme == "catppuccin":

        return {

            "background_color": "#1e1e2e",

            "axis_color": "#cdd6f4",

            "grid_alpha": "0.28",

            "grid_color": "#45475a",

            "empty_text_color": "#bac2de",

            "error_bar_color": "#89b4fa",

            "point_pen": "#89b4fa",

            "point_brush": "#cba6f7",

            "hover_pen": "#a6e3a1",

            "hover_brush": "#a6e3a1",

            "fit_curve_color": "#fab387",

            "selection_color": "#f38ba8",

            "hover_style": "background-color: #313244; color: #cdd6f4; border: 1px solid #585b70; padding: 4px 6px; border-radius: 3px;",

        }

    if normalized_theme == "solarized-dark":

        return {

            "background_color": "#002b36",

            "axis_color": "#93a1a1",

            "grid_alpha": "0.28",

            "grid_color": "#586e75",

            "empty_text_color": "#839496",

            "error_bar_color": "#268bd2",

            "point_pen": "#268bd2",

            "point_brush": "#2aa198",

            "hover_pen": "#b58900",

            "hover_brush": "#b58900",

            "fit_curve_color": "#cb4b16",

            "selection_color": "#dc322f",

            "hover_style": "background-color: #073642; color: #93a1a1; border: 1px solid #586e75; padding: 4px 6px; border-radius: 3px;",

        }

    if normalized_theme == "one-dark":

        return {

            "background_color": "#1e2127",

            "axis_color": "#abb2bf",

            "grid_alpha": "0.28",

            "grid_color": "#4b5263",

            "empty_text_color": "#9da5b4",

            "error_bar_color": "#61afef",

            "point_pen": "#61afef",

            "point_brush": "#c678dd",

            "hover_pen": "#98c379",

            "hover_brush": "#98c379",

            "fit_curve_color": "#d19a66",

            "selection_color": "#e06c75",

            "hover_style": "background-color: #282c34; color: #abb2bf; border: 1px solid #5c6370; padding: 4px 6px; border-radius: 3px;",

        }

    if normalized_theme == "custom":

        palette = default_custom_theme_colors()

        if isinstance(custom_colors, dict):

            for key in palette:

                value = custom_colors.get(key)

                if isinstance(value, str) and len(value.strip()) == 7 and value.strip().startswith("#"):

                    palette[key] = value.strip().lower()

        point_color = palette["plot_points"]

        hover_brush = _blend_hex(point_color, "#ffffff", 0.35)

        axis_color = palette["plot_axis"]

        grid_color = _blend_hex(axis_color, palette["plot_bg"], 0.55)

        return {

            "background_color": palette["plot_bg"],

            "axis_color": axis_color,

            "grid_alpha": "0.30",

            "grid_color": grid_color,

            "empty_text_color": axis_color,

            "error_bar_color": axis_color,

            "point_pen": point_color,

            "point_brush": point_color,

            "hover_pen": hover_brush,

            "hover_brush": hover_brush,

            "fit_curve_color": palette["plot_fit"],

            "selection_color": palette["plot_fit"],

            "hover_style": (

                f"background-color: {palette['menu_bg']}; color: {palette['menu_text']}; "

                f"border: 1px solid {axis_color}; padding: 4px 6px; border-radius: 3px;"

            ),

        }

    return {

        "background_color": "#ffffff",

        "axis_color": "#2b2b2b",

        "grid_alpha": "0.25",

        "grid_color": "#b8b0a2",

        "empty_text_color": "#444444",

        "error_bar_color": "#7f8c8d",

        "point_pen": "#1f4e79",

        "point_brush": "#4ea8de",

        "hover_pen": "#1f4e79",

        "hover_brush": "#9dd9f3",

        "fit_curve_color": "#ff9f1c",

        "selection_color": "#ff9f1c",

        "hover_style": "background-color: #f7f7f7; color: #1f1f1f; border: 1px solid #b8b8b8; padding: 4px 6px; border-radius: 3px;",

    }





def light_curve_axis_label(y_axis_mode: str) -> str:

    if y_axis_mode == "calibrated_magnitude":

        return "Calibrated Magnitude"

    if y_axis_mode == "instrumental_magnitude":

        return "Instrumental Magnitude"

    if y_axis_mode == "flux":

        return "Flux (counts)"

    if y_axis_mode == "relative_flux":

        return "Relative Flux (target/reference)"

    return "Differential Magnitude"





def _is_magnitude_axis(y_axis_mode: str) -> bool:

    return y_axis_mode in {"differential_magnitude", "calibrated_magnitude", "instrumental_magnitude"}





def estimate_period_hours(

    series: LightCurveSeries,

    y_axis_mode: str = "differential_magnitude",

    minimum_period_hours: float = 0.1,

    maximum_period_hours: float | None = None,

    method: str = "gls",

) -> float | None:

    timed_points = _timed_series_points(series, y_axis_mode)

    if len(timed_points) < 6:

        return None



    timed_points.sort(key=lambda item: item[0])

    time_days = np.asarray([mdates.date2num(observation_time) for observation_time, _, _ in timed_points], dtype=float)

    magnitude_values = np.asarray([value for _, value, _ in timed_points], dtype=float)

    if not np.all(np.isfinite(time_days)) or not np.all(np.isfinite(magnitude_values)):

        return None



    unique_times = np.unique(time_days)

    if unique_times.size < 6:

        return None



    minimum_period_days, maximum_period_days = _period_search_bounds_days(

        unique_times,

        minimum_period_hours,

        maximum_period_hours,

    )

    if minimum_period_days is None or maximum_period_days is None:

        return None



    minimum_frequency = 1.0 / maximum_period_days

    maximum_frequency = 1.0 / minimum_period_days

    if not np.isfinite(minimum_frequency) or not np.isfinite(maximum_frequency) or maximum_frequency <= minimum_frequency:

        return None



    normalized_time_days = time_days - float(time_days[0])

    error_values = np.asarray([error if error is not None and error > 0 else np.nan for _, _, error in timed_points], dtype=float)

    if str(method).strip().lower() == "bls":

        bls_values = magnitude_values

        bls_errors = error_values

        if _is_magnitude_axis(y_axis_mode):

            bls_values = np.power(10.0, -0.4 * magnitude_values)

            if np.isfinite(error_values).any():

                bls_errors = np.abs(bls_values) * error_values * (np.log(10.0) / 2.5)

        bls_result = _estimate_period_duration_hours_bls(

            normalized_time_days,

            bls_values,

            bls_errors,

            minimum_period_days,

            maximum_period_days,

        )

        return None if bls_result is None else bls_result[0]

    return _estimate_period_hours_gls(

        normalized_time_days,

        magnitude_values,

        error_values,

        minimum_frequency,

        maximum_frequency,

    )





def infer_fit_period_result(

    series: LightCurveSeries,

    fit_config: LightCurveFitConfig | None = None,

    y_axis_mode: str = "differential_magnitude",

    minimum_period_hours: float = 0.1,

    maximum_period_hours: float | None = None,

    method: str = "harmonic_fit",

    period_convention: str = _PERIOD_CONVENTION_STANDARD,

) -> FitPeriodInferenceResult | None:

    normalized_method = str(method).strip().lower()

    if normalized_method == "bls":

        timed_points = _timed_series_points(series, y_axis_mode)

        if len(timed_points) < 6:

            return None

        timed_points.sort(key=lambda item: item[0])

        time_days = np.asarray([mdates.date2num(observation_time) for observation_time, _, _ in timed_points], dtype=float)

        magnitude_values = np.asarray([value for _, value, _ in timed_points], dtype=float)

        if not np.all(np.isfinite(time_days)) or not np.all(np.isfinite(magnitude_values)):

            return None

        unique_times = np.unique(time_days)

        if unique_times.size < 6:

            return None

        minimum_period_days, maximum_period_days = _period_search_bounds_days(

            unique_times,

            minimum_period_hours,

            maximum_period_hours,

        )

        if minimum_period_days is None or maximum_period_days is None:

            return None

        normalized_time_days = time_days - float(time_days[0])

        error_values = np.asarray([error if error is not None and error > 0 else np.nan for _, _, error in timed_points], dtype=float)

        bls_values = magnitude_values

        bls_errors = error_values

        if _is_magnitude_axis(y_axis_mode):

            bls_values = np.power(10.0, -0.4 * magnitude_values)

            if np.isfinite(error_values).any():

                bls_errors = np.abs(bls_values) * error_values * (np.log(10.0) / 2.5)

        bls_result = _estimate_period_duration_hours_bls(

            normalized_time_days,

            bls_values,

            bls_errors,

            minimum_period_days,

            maximum_period_days,

        )

        if bls_result is None:

            return None

        period_hours, eclipse_duration_hours = bls_result

        base_config = fit_config or LightCurveFitConfig(mode="periodic")

        candidate_period_days, _candidate_harmonics = _apply_period_convention_days(

            np.asarray([], dtype=float),

            np.asarray([], dtype=float),

            np.asarray([], dtype=float),

            base_config,

            period_hours / 24.0,

            None,

            (minimum_period_hours / 24.0),

            (maximum_period_hours / 24.0) if maximum_period_hours is not None else float("inf"),

            period_convention,

            series=series,

            y_axis_mode=y_axis_mode,

        )

        resolved_period_hours = period_hours if candidate_period_days is None else (candidate_period_days * 24.0)

        if _period_is_at_search_edge_days(resolved_period_hours / 24.0, minimum_period_days, maximum_period_days):

            return None

        return FitPeriodInferenceResult(

            period_hours=resolved_period_hours,

            periodic_harmonics=None,

            method="bls",

            eclipse_duration_hours=eclipse_duration_hours,

        )



    timed_entries: list[tuple[float, float, float | None, float | None]] = []

    for point in series.points:

        value = _light_curve_point_value(point, y_axis_mode)

        if point.observation_time is None or value is None:

            continue

        timed_entries.append(

            (

                float(mdates.date2num(point.observation_time)),

                float(value),

                _light_curve_point_error(point, y_axis_mode),

                _light_curve_point_fit_weight(point, y_axis_mode),

            )

        )

    if len(timed_entries) < 6:

        return None

    timed_entries.sort(key=lambda item: item[0])

    time_days = np.asarray([item[0] for item in timed_entries], dtype=float)

    y_values = np.asarray([item[1] for item in timed_entries], dtype=float)

    if not np.all(np.isfinite(time_days)) or not np.all(np.isfinite(y_values)):

        return None

    unique_times = np.unique(time_days)

    if unique_times.size < 6:

        return None

    minimum_period_days, maximum_period_days = _period_search_bounds_days(

        unique_times,

        minimum_period_hours,

        maximum_period_hours,

    )

    if minimum_period_days is None or maximum_period_days is None:

        return None

    base_config = fit_config or LightCurveFitConfig(mode="periodic")

    fit_weights = np.asarray([item[3] if item[3] is not None else 1.0 for item in timed_entries], dtype=float)

    best_period_days, best_harmonics = _scan_periodic_fit_period_days(

        time_days,

        y_values,

        fit_weights,

        base_config,

        minimum_period_days,

        maximum_period_days,

    )

    if best_period_days is None or best_harmonics is None or not np.isfinite(best_period_days) or best_period_days <= 0:

        return None

    best_period_days, best_harmonics = _apply_period_convention_days(

        time_days,

        y_values,

        fit_weights,

        base_config,

        best_period_days,

        best_harmonics,

        minimum_period_days,

        maximum_period_days,

        period_convention,

    )

    if best_period_days is None or best_harmonics is None or not np.isfinite(best_period_days) or best_period_days <= 0:

        return None

    if _period_is_at_search_edge_days(best_period_days, minimum_period_days, maximum_period_days):

        return None

    return FitPeriodInferenceResult(

        period_hours=best_period_days * 24.0,

        periodic_harmonics=best_harmonics,

        method="harmonic_fit",

    )





def infer_fit_period_hours(

    series: LightCurveSeries,

    fit_config: LightCurveFitConfig | None = None,

    y_axis_mode: str = "differential_magnitude",

    minimum_period_hours: float = 0.1,

    maximum_period_hours: float | None = None,

    method: str = "harmonic_fit",

    period_convention: str = _PERIOD_CONVENTION_STANDARD,

) -> float | None:

    result = infer_fit_period_result(

        series,

        fit_config=fit_config,

        y_axis_mode=y_axis_mode,

        minimum_period_hours=minimum_period_hours,

        maximum_period_hours=maximum_period_hours,

        method=method,

        period_convention=period_convention,

    )

    return None if result is None else result.period_hours
    
    
    
def period_result_hits_search_edge(
    series: LightCurveSeries,
    result: FitPeriodInferenceResult,
    y_axis_mode: str = "differential_magnitude",
    minimum_period_hours: float = 0.1,
    maximum_period_hours: float | None = None,
) -> bool:
    timed_points = _timed_series_points(series, y_axis_mode)
    if len(timed_points) < 6:
        return False
    time_days = np.asarray([mdates.date2num(observation_time) for observation_time, _, _ in timed_points], dtype=float)
    if not np.all(np.isfinite(time_days)):
        return False
    minimum_period_days, maximum_period_days = _period_search_bounds_days(
        np.unique(time_days),
        minimum_period_hours,
        maximum_period_hours,
    )
    if minimum_period_days is None or maximum_period_days is None:
        return False
    return _period_is_at_search_edge_days(result.period_hours / 24.0, minimum_period_days, maximum_period_days)

def plot_light_curve(

    axis: object,

    series: LightCurveSeries,

    empty_message: str,

    fit_config: LightCurveFitConfig | None = None,

    y_axis_mode: str = "differential_magnitude",

    x_axis_mode: str = "datetime",

    phase_period_hours: float | None = None,

    phase_anchor_mode: str = "first_observation",

    theme: str = "normal",

    custom_theme_colors: dict[str, str] | None = None,

    export_style: str = "themed",

    x_limits: tuple[float, float] | None = None,

    y_limits: tuple[float, float] | None = None,

) -> None:

    payload = build_light_curve_plot_payload(

        series,

        empty_message,

        fit_config=fit_config,

        y_axis_mode=y_axis_mode,

        x_axis_mode=x_axis_mode,

        phase_period_hours=phase_period_hours,

        phase_anchor_mode=phase_anchor_mode,

    )

    plot_light_curve_payload(

        axis,

        payload,

        theme=theme,

        custom_theme_colors=custom_theme_colors,

        export_style=export_style,

        x_limits=x_limits,

        y_limits=y_limits,

    )




def plot_light_curve_payload(

    axis: object,

    payload: LightCurvePlotPayload,

    *,

    theme: str = "normal",

    custom_theme_colors: dict[str, str] | None = None,

    export_style: str = "themed",

    x_limits: tuple[float, float] | None = None,

    y_limits: tuple[float, float] | None = None,

    show_empty_message: bool = True,

    grid_alpha_override: float | None = None,

    show_minor_grid: bool = False,

) -> None:

    theme_colors = _resolved_light_curve_export_colors(export_style, theme, custom_theme_colors)

    _apply_light_curve_export_theme(axis, theme_colors, export_style=export_style)

    axis.set_axisbelow(True)

    axis.set_title(payload.title)

    axis.set_ylabel(payload.y_axis_label)

    axis.set_xlabel(payload.x_axis_label)



    if payload.points:

        x_values = [point.x for point in payload.points]

        y_values = [point.y for point in payload.points]

        y_errors = [point.y_error for point in payload.points]

        _plot_series_markers(axis, x_values, y_values, y_errors, theme_colors, export_style=export_style)

        if payload.fit_x_values is not None and payload.fit_y_values is not None:

            axis.plot(payload.fit_x_values, payload.fit_y_values, color=theme_colors["fit_curve_color"], linewidth=1.6, alpha=0.9)

        if y_limits is None:

            _set_light_curve_y_limits(axis, y_values, payload.fit_y_values)

        else:

            axis.set_ylim(*y_limits)

        if payload.x_axis_mode == "datetime":

            locator = mdates.AutoDateLocator(minticks=3, maxticks=8)

            axis.xaxis.set_major_locator(locator)

            axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))

            axis.figure.autofmt_xdate()

        elif payload.x_axis_mode == "jd":

            axis.ticklabel_format(axis="x", style="plain", useOffset=False)

        elif payload.x_axis_mode == "phase":

            axis.set_xlim(*(x_limits or payload.x_limits or (-0.02, 1.02)))

        elif payload.index_labels:

            axis.set_xticks(list(range(len(payload.index_labels))))

            axis.set_xticklabels(list(payload.index_labels), rotation=30, ha="right")

        elif x_limits is not None:

            axis.set_xlim(*x_limits)

        if x_limits is not None and payload.x_axis_mode not in {"phase", "index"}:

            axis.set_xlim(*x_limits)

        if payload.invert_y:

            axis.invert_yaxis()

        _apply_light_curve_grid(
            axis,
            theme_colors,
            export_style=export_style,
            grid_alpha_override=grid_alpha_override,
            show_minor_grid=show_minor_grid,
            x_axis_mode=payload.x_axis_mode,
        )
        return


    if x_limits is not None:

        axis.set_xlim(*x_limits)

    elif payload.x_axis_mode == "phase":

        axis.set_xlim(*(payload.x_limits or (-0.02, 1.02)))

    if y_limits is not None:

        axis.set_ylim(*y_limits)

    if payload.x_axis_mode == "datetime":

        locator = mdates.AutoDateLocator(minticks=3, maxticks=8)

        axis.xaxis.set_major_locator(locator)

        axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))

        axis.figure.autofmt_xdate()

    elif payload.x_axis_mode == "jd":

        axis.ticklabel_format(axis="x", style="plain", useOffset=False)

    elif payload.index_labels:

        axis.set_xticks(list(range(len(payload.index_labels))))

        axis.set_xticklabels(list(payload.index_labels), rotation=30, ha="right")

    if payload.invert_y:

        axis.invert_yaxis()

    if show_empty_message:

        axis.text(

            0.5,

            0.5,

            payload.empty_message or "No light-curve points are available.",

            ha="center",

            va="center",

            color=theme_colors["empty_text_color"],

            transform=axis.transAxes,

        )

    _apply_light_curve_grid(
        axis,
        theme_colors,
        export_style=export_style,
        grid_alpha_override=grid_alpha_override,
        show_minor_grid=show_minor_grid,
        x_axis_mode=payload.x_axis_mode,
    )


def _apply_light_curve_grid(
    axis: object,
    theme_colors: dict[str, str],
    *,
    export_style: str,
    grid_alpha_override: float | None,
    show_minor_grid: bool,
    x_axis_mode: str,
) -> None:
    major_alpha = float(grid_alpha_override) if grid_alpha_override is not None else float(theme_colors["grid_alpha"])
    line_style = "--" if export_style == "scientific" else "-"
    major_line_width = 0.7 if export_style == "scientific" else 0.8
    axis.grid(
        True,
        which="major",
        color=theme_colors["grid_color"],
        alpha=major_alpha,
        linestyle=line_style,
        linewidth=major_line_width,
    )
    if not show_minor_grid:
        return
    if x_axis_mode == "datetime":
        axis.xaxis.set_minor_locator(mdates.AutoDateLocator(minticks=6, maxticks=18))
    elif x_axis_mode != "index":
        axis.xaxis.set_minor_locator(AutoMinorLocator(2))
    axis.yaxis.set_minor_locator(AutoMinorLocator(2))
    axis.grid(
        True,
        which="minor",
        color=theme_colors["grid_color"],
        alpha=max(0.08, major_alpha * 0.45),
        linestyle=line_style,
        linewidth=max(0.35, major_line_width * 0.7),
    )





def _resolved_light_curve_export_colors(

    export_style: str,

    theme: str,

    custom_theme_colors: dict[str, str] | None,

) -> dict[str, str]:

    if str(export_style).strip().lower() == "scientific":

        return {

            "background_color": "#ffffff",

            "axis_color": "#111111",

            "grid_alpha": "0.18",

            "grid_color": "#808080",

            "empty_text_color": "#333333",

            "error_bar_color": "#4d4d4d",

            "point_pen": "#111111",

            "point_brush": "#111111",

            "hover_pen": "#111111",

            "hover_brush": "#111111",

            "fit_curve_color": "#005f9e",

            "selection_color": "#005f9e",

            "hover_style": "",

        }

    return resolve_light_curve_theme_colors(theme, custom_theme_colors)





def build_light_curve_plot_payload(

    series: LightCurveSeries,

    empty_message: str,

    fit_config: LightCurveFitConfig | None = None,

    y_axis_mode: str = "differential_magnitude",

    x_axis_mode: str = "datetime",

    phase_period_hours: float | None = None,

    phase_anchor_mode: str = "first_observation",

) -> LightCurvePlotPayload:

    title = f"{series.source_name} [{series.filter_name}]"

    y_axis_label = light_curve_axis_label(y_axis_mode)

    invert_y = _is_magnitude_axis(y_axis_mode)



    timed_points: list[LightCurveRenderPoint] = []

    phase_mode = x_axis_mode == "phase"

    phase_period_days = (phase_period_hours / 24.0) if phase_period_hours is not None and phase_period_hours > 0 else None

    phase_anchor_jd = _phase_anchor_jd(series, y_axis_mode, phase_anchor_mode) if phase_mode else None

    for point in series.points:

        value = _light_curve_point_value(point, y_axis_mode)

        if point.observation_time is None or value is None:

            continue

        x_value = _phase_axis_value(point.observation_time, phase_period_days, phase_anchor_jd) if phase_mode else _time_axis_value(point.observation_time, x_axis_mode)

        if x_value is None:

            continue

        timed_points.append(

            LightCurveRenderPoint(

                x=x_value,

                y=value,

                y_error=_light_curve_point_error(point, y_axis_mode),

                fit_weight=_light_curve_point_fit_weight(point, y_axis_mode),

                source_point=point,

            )

        )

    if timed_points:

        fit_x_values, fit_y_values = _build_fit_curve(

            [point.x for point in timed_points],

            [point.y for point in timed_points],

            [point.fit_weight for point in timed_points],

            fit_config,

            y_axis_mode=y_axis_mode,

            x_units="phase" if phase_mode else "days",

        )

        return LightCurvePlotPayload(

            title=title,

            y_axis_label=y_axis_label,

            x_axis_label="Phase" if phase_mode else ("Julian Date (JD)" if x_axis_mode == "jd" else "Observation Time"),

            x_axis_mode="phase" if phase_mode else ("jd" if x_axis_mode == "jd" else "datetime"),

            invert_y=invert_y,

            points=tuple(timed_points),

            fit_x_values=fit_x_values,

            fit_y_values=fit_y_values,

        )



    index_labels = tuple(point.file_path.name for point in series.points)

    value_points: list[LightCurveRenderPoint] = []

    for index, point in enumerate(series.points):

        value = _light_curve_point_value(point, y_axis_mode)

        if value is None:

            continue

        value_points.append(

            LightCurveRenderPoint(

                x=float(index),

                y=value,

                y_error=_light_curve_point_error(point, y_axis_mode),

                fit_weight=_light_curve_point_fit_weight(point, y_axis_mode),

                source_point=point,

            )

        )

    fit_x_values, fit_y_values = _build_fit_curve(

        [point.x for point in value_points],

        [point.y for point in value_points],

        [point.fit_weight for point in value_points],

        fit_config,

        y_axis_mode=y_axis_mode,

        x_units="index",

    )

    return LightCurvePlotPayload(

        title=title,

        y_axis_label=y_axis_label,

        x_axis_label="Observation",

        x_axis_mode="index",

        invert_y=invert_y,

        points=tuple(value_points),

        fit_x_values=fit_x_values,

        fit_y_values=fit_y_values,

        empty_message=None if value_points else empty_message,

        index_labels=index_labels,

    )





def _time_axis_value(observation_time: object, x_axis_mode: str) -> float:

    if x_axis_mode == "jd":

        return float(Time(observation_time).jd)

    return float(mdates.date2num(observation_time))





def _phase_axis_value(observation_time: object, period_days: float | None, anchor_jd: float | None) -> float | None:

    if period_days is None or period_days <= 0:

        return None

    if anchor_jd is None or not np.isfinite(anchor_jd):

        return None

    observation_jd = float(Time(observation_time).jd)

    return float(((observation_jd - anchor_jd) / period_days) % 1.0)





def _phase_anchor_jd(series: LightCurveSeries, y_axis_mode: str, anchor_mode: str) -> float | None:

    timed_values: list[tuple[float, float]] = []

    for point in series.points:

        if point.observation_time is None:

            continue

        value = _light_curve_point_value(point, y_axis_mode)

        if value is None or not np.isfinite(value):

            continue

        timed_values.append((float(Time(point.observation_time).jd), float(value)))

    if not timed_values:

        return None



    normalized_mode = str(anchor_mode).strip().lower()

    if normalized_mode == "primary_minimum":

        if _is_magnitude_axis(y_axis_mode):

            return max(timed_values, key=lambda item: item[1])[0]

        return min(timed_values, key=lambda item: item[1])[0]



    return min(jd for jd, _value in timed_values)





def _estimate_period_hours_gls(

    normalized_time_days: np.ndarray,

    magnitude_values: np.ndarray,

    error_values: np.ndarray,

    minimum_frequency: float,

    maximum_frequency: float,

) -> float | None:

    valid_errors = np.isfinite(error_values) & (error_values > 0)

    lomb_scargle = LombScargle(

        normalized_time_days,

        magnitude_values,

        dy=error_values if np.count_nonzero(valid_errors) >= 3 else None,

    )

    frequency, power = lomb_scargle.autopower(

        minimum_frequency=minimum_frequency,

        maximum_frequency=maximum_frequency,

        samples_per_peak=10,

    )

    if frequency.size == 0 or power.size == 0:

        return None

    best_index = int(np.argmax(power))

    best_frequency = float(frequency[best_index])

    if not np.isfinite(best_frequency) or best_frequency <= 0:

        return None

    best_frequency = _promote_gls_subharmonic_frequency(frequency, power, best_frequency)

    if not np.isfinite(best_frequency) or best_frequency <= 0:

        return None

    return 24.0 / best_frequency





def _period_search_bounds_days(

    unique_time_days: np.ndarray,

    minimum_period_hours: float,

    maximum_period_hours: float | None,

) -> tuple[float | None, float | None]:

    if unique_time_days.size < 6:

        return None, None

    total_span_days = float(unique_time_days[-1] - unique_time_days[0])

    if not np.isfinite(total_span_days) or total_span_days <= 0:

        return None, None

    cadence_days = np.diff(unique_time_days)

    positive_cadence_days = cadence_days[cadence_days > 0]

    if positive_cadence_days.size == 0:

        return None, None

    minimum_period_days = max(minimum_period_hours / 24.0, float(np.median(positive_cadence_days)) * 2.0)

    maximum_period_days = min((maximum_period_hours or 24.0 * total_span_days * 0.95) / 24.0, total_span_days * 0.95)

    if not np.isfinite(maximum_period_days) or maximum_period_days <= minimum_period_days:

        return None, None

    return minimum_period_days, maximum_period_days



def _period_is_at_search_edge_days(period_days: float, minimum_period_days: float, maximum_period_days: float) -> bool:

    if (

        not np.isfinite(period_days)

        or not np.isfinite(minimum_period_days)

        or not np.isfinite(maximum_period_days)

        or maximum_period_days <= minimum_period_days

    ):

        return False

    search_span_days = maximum_period_days - minimum_period_days

    edge_margin_days = max(search_span_days * _PERIOD_SEARCH_EDGE_RELATIVE_TOLERANCE, 1e-9)

    return period_days <= (minimum_period_days + edge_margin_days) or period_days >= (maximum_period_days - edge_margin_days)





def _estimate_period_hours_bls(

    normalized_time_days: np.ndarray,

    magnitude_values: np.ndarray,

    error_values: np.ndarray,

    minimum_period_days: float,

    maximum_period_days: float,

) -> float | None:

    result = _estimate_period_duration_hours_bls(

        normalized_time_days,

        magnitude_values,

        error_values,

        minimum_period_days,

        maximum_period_days,

    )

    return None if result is None else result[0]





def _estimate_period_duration_hours_bls(

    normalized_time_days: np.ndarray,

    magnitude_values: np.ndarray,

    error_values: np.ndarray,

    minimum_period_days: float,

    maximum_period_days: float,

) -> tuple[float, float | None] | None:

    if normalized_time_days.size < 6:

        return None

    cadence_days = np.diff(np.unique(normalized_time_days))

    positive_cadence = cadence_days[cadence_days > 0]

    minimum_duration = max(

        minimum_period_days * 0.03,

        float(np.median(positive_cadence)) * 1.5 if positive_cadence.size else minimum_period_days * 0.05,

    )

    maximum_allowed_duration = minimum_period_days * 0.4

    minimum_duration = min(minimum_duration, maximum_allowed_duration * 0.6)

    maximum_duration = min(max(minimum_duration * 1.8, minimum_period_days * 0.12), maximum_allowed_duration)

    if maximum_duration <= minimum_duration:

        maximum_duration = minimum_duration * 1.2

    durations = np.unique(np.asarray([minimum_duration, minimum_duration * 1.7, maximum_duration], dtype=float))

    valid_errors = np.isfinite(error_values) & (error_values > 0)

    model = BoxLeastSquares(

        normalized_time_days,

        magnitude_values,

        dy=error_values if np.count_nonzero(valid_errors) >= 3 else None,

    )

    result = model.autopower(

        durations,

        minimum_period=minimum_period_days,

        maximum_period=maximum_period_days,

        frequency_factor=8.0,

    )

    if result.period.size == 0 or result.power.size == 0:

        return None

    best_index = int(np.argmax(result.power))

    period_grid = np.asarray(result.period, dtype=float)

    power_grid = np.asarray(result.power, dtype=float)

    duration_grid = np.asarray(getattr(result, "duration", np.full(period_grid.shape, np.nan)), dtype=float)

    best_period = float(period_grid[best_index])

    best_power = float(power_grid[best_index])

    best_duration = float(duration_grid[best_index]) if duration_grid.size == period_grid.size else float("nan")

    for harmonic in (2, 3, 4):

        candidate_period = best_period * harmonic

        if candidate_period <= minimum_period_days or candidate_period >= maximum_period_days:

            continue

        nearest_index = int(np.argmin(np.abs(period_grid - candidate_period)))

        nearest_period = float(period_grid[nearest_index])

        if not np.isfinite(nearest_period) or abs(nearest_period - candidate_period) > (candidate_period * 0.05):

            continue

        nearest_power = float(power_grid[nearest_index])

        if nearest_power >= (best_power * 0.9):

            best_period = nearest_period

            best_power = nearest_power

            if duration_grid.size == period_grid.size:

                best_duration = float(duration_grid[nearest_index])

    if not np.isfinite(best_period) or best_period <= 0:

        return None

    eclipse_duration_hours = best_duration * 24.0 if np.isfinite(best_duration) and best_duration > 0 else None

    return best_period * 24.0, eclipse_duration_hours





def _scan_periodic_fit_period_days(

    x_array: np.ndarray,

    y_array: np.ndarray,

    fit_weights: np.ndarray,

    fit_config: LightCurveFitConfig,

    minimum_period_days: float,

    maximum_period_days: float,

) -> tuple[float | None, int | None]:

    if x_array.size < 6 or maximum_period_days <= minimum_period_days:

        return None, None

    candidate_periods = np.geomspace(minimum_period_days, maximum_period_days, num=240)

    max_harmonics = min(6, max(1, fit_config.periodic_harmonics, 4))

    best_period_days, best_score, best_harmonics = _best_period_from_candidates(

        x_array,

        y_array,

        fit_weights,

        fit_config,

        candidate_periods,

        max_harmonics=max_harmonics,

    )

    if best_period_days is None or best_score is None or best_harmonics is None:

        return None, None

    refined_best_period, refined_best_score, refined_best_harmonics = _refine_period_candidate_days(

        x_array,

        y_array,

        fit_weights,

        fit_config,

        best_period_days,

        minimum_period_days,

        maximum_period_days,

        max_harmonics,

    )

    if refined_best_period is not None and refined_best_score is not None and refined_best_harmonics is not None and refined_best_score < best_score:

        best_period_days = refined_best_period

        best_score = refined_best_score

        best_harmonics = refined_best_harmonics

    for harmonic in (2, 3, 4):

        harmonic_period_days = best_period_days * harmonic

        if harmonic_period_days <= minimum_period_days or harmonic_period_days >= maximum_period_days:

            continue

        harmonic_period, harmonic_score, harmonic_count = _refine_period_candidate_days(

            x_array,

            y_array,

            fit_weights,

            fit_config,

            harmonic_period_days,

            minimum_period_days,

            maximum_period_days,

            max_harmonics,

        )

        if harmonic_period is not None and harmonic_score is not None and harmonic_count is not None and harmonic_score < best_score:

            best_period_days = harmonic_period

            best_score = harmonic_score

            best_harmonics = harmonic_count

    return best_period_days, best_harmonics





def _apply_period_convention_days(

    x_array: np.ndarray,

    y_array: np.ndarray,

    fit_weights: np.ndarray,

    fit_config: LightCurveFitConfig,

    best_period_days: float | None,

    best_harmonics: int | None,

    minimum_period_days: float,

    maximum_period_days: float,

    period_convention: str,

    *,

    series: LightCurveSeries | None = None,

    y_axis_mode: str = "differential_magnitude",

) -> tuple[float | None, int | None]:

    normalized_convention = str(period_convention).strip().lower()

    if normalized_convention != _PERIOD_CONVENTION_ECLIPSING_BINARY:

        return best_period_days, best_harmonics

    if best_period_days is None or not np.isfinite(best_period_days) or best_period_days <= 0:

        return best_period_days, best_harmonics



    if x_array.size == 0 or y_array.size == 0 or fit_weights.size == 0:

        if series is None:

            return best_period_days, best_harmonics

        timed_entries: list[tuple[float, float, float | None, float | None]] = []

        for point in series.points:

            value = _light_curve_point_value(point, y_axis_mode)

            if point.observation_time is None or value is None:

                continue

            timed_entries.append(

                (

                    float(mdates.date2num(point.observation_time)),

                    float(value),

                    _light_curve_point_error(point, y_axis_mode),

                    _light_curve_point_fit_weight(point, y_axis_mode),

                )

            )

        if len(timed_entries) < 6:

            return best_period_days, best_harmonics

        timed_entries.sort(key=lambda item: item[0])

        x_array = np.asarray([item[0] for item in timed_entries], dtype=float)

        y_array = np.asarray([item[1] for item in timed_entries], dtype=float)

        fit_weights = np.asarray([item[3] if item[3] is not None else 1.0 for item in timed_entries], dtype=float)



    doubled_period_days = best_period_days * 2.0

    if not np.isfinite(doubled_period_days) or doubled_period_days <= best_period_days or doubled_period_days >= maximum_period_days:

        return best_period_days, best_harmonics



    max_harmonics = _max_periodic_harmonics(fit_config)

    resolved_best_period, best_score, resolved_best_harmonics = _score_period_candidate_days(

        x_array,

        y_array,

        fit_weights,

        fit_config,

        best_period_days,

        max_harmonics,

    )

    if resolved_best_period is not None:

        best_period_days = resolved_best_period

    if resolved_best_harmonics is not None:

        best_harmonics = resolved_best_harmonics



    doubled_period, doubled_score, doubled_harmonics = _score_period_candidate_days(

        x_array,

        y_array,

        fit_weights,

        fit_config,

        doubled_period_days,

        max_harmonics,

    )

    if doubled_period is None or doubled_score is None or doubled_harmonics is None:

        return best_period_days, best_harmonics

    if _has_single_eclipse_feature_per_half_cycle(x_array, y_array, doubled_period):

        return doubled_period, doubled_harmonics

    if best_score is None or doubled_score <= (best_score + _ECLIPSING_BINARY_DOUBLED_PERIOD_BIC_TOLERANCE):

        return doubled_period, doubled_harmonics

    return best_period_days, best_harmonics





def _max_periodic_harmonics(fit_config: LightCurveFitConfig) -> int:

    return min(6, max(1, fit_config.periodic_harmonics, 4))





def _score_period_candidate_days(

    x_array: np.ndarray,

    y_array: np.ndarray,

    fit_weights: np.ndarray,

    fit_config: LightCurveFitConfig,

    candidate_period_days: float,

    max_harmonics: int,

) -> tuple[float | None, float | None, int | None]:

    return _best_period_from_candidates(

        x_array,

        y_array,

        fit_weights,

        fit_config,

        np.asarray([candidate_period_days], dtype=float),

        max_harmonics=max_harmonics,

    )





def _has_single_eclipse_feature_per_half_cycle(

    x_array: np.ndarray,

    y_array: np.ndarray,

    period_days: float,

) -> bool:

    if x_array.size < 12 or y_array.size < 12 or not np.isfinite(period_days) or period_days <= 0:

        return False



    phase = np.mod((x_array - float(np.min(x_array))) / period_days, 1.0)

    bin_edges = np.linspace(0.0, 1.0, _ECLIPSING_BINARY_PHASE_BINS + 1)

    bin_indices = np.digitize(phase, bin_edges, right=False) - 1

    binned_values = np.full(_ECLIPSING_BINARY_PHASE_BINS, np.nan, dtype=float)



    for index in range(_ECLIPSING_BINARY_PHASE_BINS):

        values = y_array[bin_indices == index]

        if values.size > 0:

            binned_values[index] = float(np.median(values))



    valid_mask = np.isfinite(binned_values)

    if int(np.count_nonzero(valid_mask)) < max(8, _ECLIPSING_BINARY_PHASE_BINS // 3):

        return False



    valid_indices = np.flatnonzero(valid_mask)

    interpolated = np.interp(np.arange(_ECLIPSING_BINARY_PHASE_BINS), valid_indices, binned_values[valid_mask])

    signal = np.abs(interpolated - float(np.median(interpolated)))

    peak_threshold = 0.35 * float(np.max(signal))

    if not np.isfinite(peak_threshold) or peak_threshold <= 0:

        return False



    def window_strength(center: float, half_width: float = 0.075) -> float:

        distances = np.abs(((np.arange(_ECLIPSING_BINARY_PHASE_BINS) + 0.5) / _ECLIPSING_BINARY_PHASE_BINS) - center)

        wrapped_distances = np.minimum(distances, 1.0 - distances)

        window_values = signal[wrapped_distances <= half_width]

        if window_values.size == 0:

            return 0.0

        return float(np.mean(window_values))



    primary_strength = window_strength(0.0)

    secondary_strength = window_strength(0.5)

    quarter_strength = max(window_strength(0.25), window_strength(0.75))

    eclipse_strength = min(primary_strength, secondary_strength)

    if eclipse_strength < peak_threshold:

        return False

    if (eclipse_strength / max(primary_strength, secondary_strength, 1e-9)) < 0.6:

        return False

    return quarter_strength < (0.6 * eclipse_strength)





def _refine_period_candidate_days(

    x_array: np.ndarray,

    y_array: np.ndarray,

    fit_weights: np.ndarray,

    fit_config: LightCurveFitConfig,

    center_period_days: float,

    minimum_period_days: float,

    maximum_period_days: float,

    max_harmonics: int,

) -> tuple[float | None, float | None, int | None]:

    if not np.isfinite(center_period_days) or center_period_days <= 0:

        return None, None, None

    log_min = float(np.log(minimum_period_days))

    log_max = float(np.log(maximum_period_days))

    best_period_days = center_period_days

    best_score: float | None = None

    best_harmonics: int | None = None

    best_log_period = float(np.log(center_period_days))

    for window_scale, sample_count in ((0.18, 180), (0.06, 180), (0.02, 200)):

        lower_bound = max(log_min, best_log_period - window_scale)

        upper_bound = min(log_max, best_log_period + window_scale)

        if upper_bound <= lower_bound:

            continue

        refined_candidates = np.exp(np.linspace(lower_bound, upper_bound, num=sample_count))

        refined_period_days, refined_score, refined_harmonics = _best_period_from_candidates(

            x_array,

            y_array,

            fit_weights,

            fit_config,

            refined_candidates,

            max_harmonics=max_harmonics,

        )

        if refined_period_days is None or refined_score is None or refined_harmonics is None:

            continue

        best_period_days = refined_period_days

        best_score = refined_score

        best_harmonics = refined_harmonics

        best_log_period = float(np.log(best_period_days))

    return best_period_days, best_score, best_harmonics





def _best_period_from_candidates(

    x_array: np.ndarray,

    y_array: np.ndarray,

    fit_weights: np.ndarray,

    fit_config: LightCurveFitConfig,

    candidate_periods: np.ndarray,

    max_harmonics: int,

) -> tuple[float | None, float | None, int | None]:

    best_period_days: float | None = None

    best_score: float | None = None

    best_harmonics: int | None = None

    normalized_weights = _normalized_fit_weights(fit_weights)

    for candidate_period in np.asarray(candidate_periods, dtype=float):

        if not np.isfinite(candidate_period) or candidate_period <= 0:

            continue

        for harmonic_count in range(1, max_harmonics + 1):

            candidate_config = LightCurveFitConfig(

                mode="periodic",

                polynomial_degree=fit_config.polynomial_degree,

                periodic_period_hours=float(candidate_period * 24.0),

                periodic_harmonics=harmonic_count,

                spline_smoothing=fit_config.spline_smoothing,

                robust_iterations=fit_config.robust_iterations,

            )

            _fit_y, model_y, parameter_count = _periodic_fit_curve(

                x_array,

                y_array,

                x_array,

                candidate_config,

                "days",

                normalized_weights,

            )

            if model_y is None or parameter_count <= 0:

                continue

            bic_score = _weighted_bic(y_array, model_y, normalized_weights, parameter_count)

            if bic_score is None:

                continue

            if best_score is None or bic_score < best_score:

                best_period_days = float(candidate_period)

                best_score = float(bic_score)

                best_harmonics = harmonic_count

    return best_period_days, best_score, best_harmonics





def _timed_series_points(series: LightCurveSeries, y_axis_mode: str) -> list[tuple[object, float, float | None]]:

    timed_points = []

    for point in series.points:

        value = _light_curve_point_value(point, y_axis_mode)

        error = _light_curve_point_error(point, y_axis_mode)

        if point.observation_time is not None and value is not None:

            timed_points.append((point.observation_time, value, error))

    return timed_points





def _light_curve_point_value(point: object, y_axis_mode: str) -> float | None:

    if y_axis_mode == "calibrated_magnitude":

        return point.calibrated_magnitude

    if y_axis_mode == "instrumental_magnitude":

        return point.instrumental_magnitude

    if y_axis_mode == "flux":

        return point.flux

    if y_axis_mode == "relative_flux":

        if point.flux is None or point.comparison_reference_flux is None or point.comparison_reference_flux <= 0:

            return None

        return point.flux / point.comparison_reference_flux

    return point.differential_magnitude





def _light_curve_point_error(point: object, y_axis_mode: str) -> float | None:

    if y_axis_mode == "calibrated_magnitude":

        if point.calibrated_magnitude_error is not None:

            return point.calibrated_magnitude_error

        return point.differential_magnitude_error

    if y_axis_mode == "instrumental_magnitude":

        if point.flux is None or point.flux_error is None or point.flux <= 0 or point.flux_error < 0:

            return None

        return (2.5 / np.log(10.0)) * point.flux_error / point.flux

    if y_axis_mode == "flux":

        return point.flux_error

    if y_axis_mode == "relative_flux":

        value = _light_curve_point_value(point, y_axis_mode)

        if value is None:

            return None

        if point.differential_magnitude_error is not None:

            return abs(value) * point.differential_magnitude_error * (np.log(10.0) / 2.5)

        if point.flux_error is not None and point.comparison_reference_flux is not None and point.comparison_reference_flux > 0:

            return point.flux_error / point.comparison_reference_flux

        return None

    return point.differential_magnitude_error





def _light_curve_point_fit_weight(point: object, y_axis_mode: str) -> float | None:

    if getattr(point, "excluded_from_analysis", False):

        return 0.0

    quality_weight = getattr(point, "quality_weight", None)

    if quality_weight is not None and quality_weight > 0:

        return quality_weight

    error = _light_curve_point_error(point, y_axis_mode)

    if error is not None and error > 0:

        return 1.0 / (error * error)

    return 1.0





def _plot_series_markers(

    axis: object,

    x_values: list[float],

    y_values: list[float],

    y_errors: list[float | None],

    theme_colors: dict[str, str],

    export_style: str = "themed",

) -> None:

    finite_errors = np.asarray([error if error is not None else np.nan for error in y_errors], dtype=float)

    marker_size = 2.8 if export_style == "scientific" else 3.0

    error_line_width = 0.7 if export_style == "scientific" else 0.8

    marker_edge_width = 0.6 if export_style == "scientific" else 0.8

    if finite_errors.size and np.isfinite(finite_errors).any():

        axis.errorbar(

            x_values,

            y_values,

            yerr=finite_errors,

            fmt="o",

            linestyle="None",

            markersize=marker_size,

            elinewidth=error_line_width,

            capsize=0,

            alpha=0.9,

            color=theme_colors["point_pen"],

            markerfacecolor=theme_colors["point_brush"],

            markeredgecolor=theme_colors["point_pen"],

            markeredgewidth=marker_edge_width,

            ecolor=theme_colors["error_bar_color"],

        )

        return

    axis.plot(

        x_values,

        y_values,

        marker="o",

        linestyle="None",

        markersize=marker_size,

        color=theme_colors["point_pen"],

        markerfacecolor=theme_colors["point_brush"],

        markeredgecolor=theme_colors["point_pen"],

        markeredgewidth=marker_edge_width,

    )





def _plot_fit_curve(

    axis: object,

    x_values: list[float],

    y_values: list[float],

    fit_config: LightCurveFitConfig,

    x_units: str,

    theme_colors: dict[str, str],

) -> np.ndarray | None:

    fit_x, fit_y = _build_fit_curve(x_values, y_values, [1.0] * len(x_values), fit_config, x_units=x_units)

    if fit_x is None or fit_y is None:

        return None

    axis.plot(fit_x, fit_y, color=theme_colors["fit_curve_color"], linewidth=1.6, alpha=0.9)

    return fit_y





def _apply_light_curve_export_theme(axis: object, theme_colors: dict[str, str], *, export_style: str = "themed") -> None:

    axis.set_facecolor(theme_colors["background_color"])

    axis.figure.set_facecolor(theme_colors["background_color"])

    axis.title.set_color(theme_colors["axis_color"])

    axis.xaxis.label.set_color(theme_colors["axis_color"])

    axis.yaxis.label.set_color(theme_colors["axis_color"])

    axis.tick_params(axis="x", colors=theme_colors["axis_color"], labelsize=(10 if export_style == "scientific" else None))

    axis.tick_params(axis="y", colors=theme_colors["axis_color"], labelsize=(10 if export_style == "scientific" else None))

    for spine in axis.spines.values():

        spine.set_color(theme_colors["axis_color"])

        spine.set_linewidth(1.1 if export_style == "scientific" else 1.0)





def _blend_hex(color_a: str, color_b: str, weight_b: float) -> str:

    red_a, green_a, blue_a = _hex_to_rgb(color_a)

    red_b, green_b, blue_b = _hex_to_rgb(color_b)

    weight = min(1.0, max(0.0, float(weight_b)))

    blended = (

        round(red_a * (1.0 - weight) + red_b * weight),

        round(green_a * (1.0 - weight) + green_b * weight),

        round(blue_a * (1.0 - weight) + blue_b * weight),

    )

    return _rgb_to_hex(blended)





def _hex_to_rgb(color_value: str) -> tuple[int, int, int]:

    normalized = color_value.strip().lstrip("#")

    if len(normalized) != 6:

        return (255, 255, 255)

    return (int(normalized[0:2], 16), int(normalized[2:4], 16), int(normalized[4:6], 16))





def _rgb_to_hex(color_value: tuple[int, int, int]) -> str:

    red, green, blue = color_value

    return f"#{red:02x}{green:02x}{blue:02x}"





def _build_fit_curve(

    x_values: list[float],

    y_values: list[float],

    fit_weights: list[float | None],

    fit_config: LightCurveFitConfig | None,

    y_axis_mode: str,

    x_units: str,

) -> tuple[np.ndarray | None, np.ndarray | None]:

    if fit_config is None or len(x_values) < 2:

        return None, None

    x_array = np.asarray(x_values, dtype=float)

    y_array = np.asarray(y_values, dtype=float)

    sort_order = np.argsort(x_array)

    x_array = x_array[sort_order]

    y_array = y_array[sort_order]

    weight_array = np.asarray([weight if weight is not None else 1.0 for weight in fit_weights], dtype=float)[sort_order]

    if not np.all(np.isfinite(x_array)) or not np.all(np.isfinite(y_array)):

        return None, None

    fit_x = _fit_curve_x_grid(x_array, fit_config, x_units)

    if fit_x is None or fit_x.size == 0 or float(np.max(x_array) - np.min(x_array)) <= 0:

        return None, None

    if fit_config.mode == "periodic":

        fit_y, _model_y, _ = _periodic_fit_curve(x_array, y_array, fit_x, fit_config, x_units, weight_array)

    elif fit_config.mode == "spline":

        fit_y, _model_y, _ = _spline_fit_curve(x_array, y_array, fit_x, fit_config, weight_array)

    elif fit_config.mode == "auto":

        fit_y = _auto_fit_curve(x_array, y_array, fit_x, fit_config, x_units, weight_array)

    else:

        fit_y, _model_y, _ = _polynomial_fit_curve(x_array, y_array, fit_x, fit_config, weight_array)

    if fit_y is None:

        return None, None

    if not _fit_curve_is_reasonable(y_array, fit_y, y_axis_mode):

        return None, None

    return fit_x, fit_y





def _fit_curve_x_grid(

    x_array: np.ndarray,

    fit_config: LightCurveFitConfig,

    x_units: str,

) -> np.ndarray | None:

    x_min = float(np.min(x_array))

    x_max = float(np.max(x_array))

    x_span = x_max - x_min

    if not np.isfinite(x_span) or x_span <= 0:

        return None



    if x_units == "phase":

        return np.linspace(0.0, 1.0, 600)



    sample_count = 400

    if x_units == "days" and fit_config.mode in {"periodic", "auto"}:

        period_days = fit_config.periodic_period_hours / 24.0

        if np.isfinite(period_days) and period_days > 0:

            cycle_count = max(1.0, x_span / period_days)

            requested_harmonics = max(1, int(fit_config.periodic_harmonics))

            samples_per_cycle = max(48, 24 * requested_harmonics)

            sample_count = int(min(8000, max(400, np.ceil(cycle_count * samples_per_cycle))))



    return np.linspace(x_min, x_max, sample_count)





def _fit_curve_is_reasonable(

    observed_y: np.ndarray,

    fit_y: np.ndarray,

    y_axis_mode: str,

) -> bool:

    finite_observed = np.asarray(observed_y[np.isfinite(observed_y)], dtype=float)

    finite_fit = np.asarray(fit_y[np.isfinite(fit_y)], dtype=float)

    if finite_observed.size == 0 or finite_fit.size == 0:

        return False

    observed_min = float(np.min(finite_observed))

    observed_max = float(np.max(finite_observed))

    observed_span = observed_max - observed_min

    observed_median = float(np.median(finite_observed))

    observed_mad = float(np.median(np.abs(finite_observed - observed_median))) if finite_observed.size else 0.0

    tolerance = max(1.5, observed_span * 8.0, observed_mad * 20.0)

    fit_min = float(np.min(finite_fit))

    fit_max = float(np.max(finite_fit))

    if fit_min < (observed_min - tolerance) or fit_max > (observed_max + tolerance):

        return False

    if _is_magnitude_axis(y_axis_mode):

        if np.max(np.abs(finite_fit - observed_median)) > max(5.0, tolerance):

            return False

    return True





def light_curve_y_limits(y_values: list[float], fit_y_values: np.ndarray | None = None) -> tuple[float, float] | None:

    finite_values = np.asarray([value for value in y_values if np.isfinite(value)], dtype=float)

    if fit_y_values is not None:

        finite_fit_values = np.asarray(fit_y_values[np.isfinite(fit_y_values)], dtype=float)

        if finite_fit_values.size:

            finite_values = np.concatenate((finite_values, finite_fit_values)) if finite_values.size else finite_fit_values

    if finite_values.size == 0:

        return None



    minimum = float(np.min(finite_values))

    maximum = float(np.max(finite_values))

    span = maximum - minimum

    padding = max(span * 0.08, 0.05 if span == 0 else 0.0)

    if span == 0:

        padding = max(abs(minimum) * 0.08, 0.05)

    return (minimum - padding, maximum + padding)





def _set_light_curve_y_limits(axis: object, y_values: list[float], fit_y_values: np.ndarray | None = None) -> None:

    limits = light_curve_y_limits(y_values, fit_y_values)

    if limits is None:

        return

    axis.set_ylim(*limits)





def _polynomial_fit_curve(

    x_array: np.ndarray,

    y_array: np.ndarray,

    fit_x: np.ndarray,

    fit_config: LightCurveFitConfig,

    fit_weights: np.ndarray,

) -> tuple[np.ndarray | None, np.ndarray | None, int]:

    x_span = float(np.max(x_array) - np.min(x_array))

    if not np.isfinite(x_span) or x_span <= 0:

        return None, None, 0

    x_center = float(np.mean(x_array))

    x_scale = x_span or 1.0

    x_normalized = (x_array - x_center) / x_scale

    fit_x_normalized = (fit_x - x_center) / x_scale

    degree = min(max(1, fit_config.polynomial_degree), len(x_array) - 1)

    if degree < 1:

        return None, None, 0

    base_weights = _normalized_fit_weights(fit_weights)

    coefficients = _robust_polynomial_coefficients(x_normalized, y_array, degree, base_weights, fit_config.robust_iterations)

    if coefficients is None:

        return None, None, 0

    return np.polyval(coefficients, fit_x_normalized), np.polyval(coefficients, x_normalized), degree + 1





def _periodic_fit_curve(

    x_array: np.ndarray,

    y_array: np.ndarray,

    fit_x: np.ndarray,

    fit_config: LightCurveFitConfig,

    x_units: str,

    fit_weights: np.ndarray,

) -> tuple[np.ndarray | None, np.ndarray | None, int]:

    if x_units not in {"days", "phase"}:

        return None, None, 0

    period_days = 1.0 if x_units == "phase" else (fit_config.periodic_period_hours / 24.0)

    if not np.isfinite(period_days) or period_days <= 0:

        return None, None, 0

    if len(x_array) < 3:

        return None, None, 0

    harmonics = min(max(1, fit_config.periodic_harmonics), max(1, (len(x_array) - 1) // 2))

    phase_origin = 0.0 if x_units == "phase" else float(np.min(x_array))

    phase = 2.0 * np.pi * (x_array - phase_origin) / period_days

    fit_phase = 2.0 * np.pi * (fit_x - phase_origin) / period_days

    design_columns = [np.ones_like(phase)]

    fit_columns = [np.ones_like(fit_phase)]

    for harmonic in range(1, harmonics + 1):

        design_columns.append(np.cos(harmonic * phase))

        design_columns.append(np.sin(harmonic * phase))

        fit_columns.append(np.cos(harmonic * fit_phase))

        fit_columns.append(np.sin(harmonic * fit_phase))

    design_matrix = np.column_stack(design_columns)

    fit_matrix = np.column_stack(fit_columns)

    coefficients = _robust_weighted_linear_fit(design_matrix, y_array, fit_weights, fit_config.robust_iterations)

    if coefficients is None:

        return None, None, 0

    return fit_matrix @ coefficients, design_matrix @ coefficients, design_matrix.shape[1]





def _spline_fit_curve(

    x_array: np.ndarray,

    y_array: np.ndarray,

    fit_x: np.ndarray,

    fit_config: LightCurveFitConfig,

    fit_weights: np.ndarray,

) -> tuple[np.ndarray | None, np.ndarray | None, int]:

    if len(x_array) < 4:

        return None, None, 0

    x_min = float(np.min(x_array))

    x_span = float(np.max(x_array) - x_min)

    if not np.isfinite(x_span) or x_span <= 0:

        return None, None, 0

    x_normalized = (x_array - x_min) / x_span

    fit_x_normalized = (fit_x - x_min) / x_span

    knot_count = min(6, max(3, fit_config.polynomial_degree + 1))

    knots = np.quantile(x_normalized, np.linspace(0.15, 0.85, knot_count))

    design_matrix = _cubic_spline_design_matrix(x_normalized, knots)

    fit_matrix = _cubic_spline_design_matrix(fit_x_normalized, knots)

    coefficients = _robust_weighted_ridge_fit(

        design_matrix,

        y_array,

        fit_weights,

        ridge_penalty=max(1e-4, float(fit_config.spline_smoothing) * 8.0),

        penalty_start=4,

        robust_iterations=fit_config.robust_iterations,

    )

    if coefficients is None:

        return None, None, 0

    return fit_matrix @ coefficients, design_matrix @ coefficients, design_matrix.shape[1]





def _auto_fit_curve(

    x_array: np.ndarray,

    y_array: np.ndarray,

    fit_x: np.ndarray,

    fit_config: LightCurveFitConfig,

    x_units: str,

    fit_weights: np.ndarray,

) -> np.ndarray | None:

    candidates: list[tuple[np.ndarray, np.ndarray, int]] = []

    polynomial_candidate = _polynomial_fit_curve(x_array, y_array, fit_x, fit_config, fit_weights)

    if polynomial_candidate[0] is not None and polynomial_candidate[1] is not None and polynomial_candidate[2] > 0:

        candidates.append((polynomial_candidate[0], polynomial_candidate[1], polynomial_candidate[2]))

    spline_candidate = _spline_fit_curve(x_array, y_array, fit_x, fit_config, fit_weights)

    if spline_candidate[0] is not None and spline_candidate[1] is not None and spline_candidate[2] > 0:

        candidates.append((spline_candidate[0], spline_candidate[1], spline_candidate[2]))

    if _supports_periodic_auto_fit(x_array, fit_config, x_units):

        periodic_candidate = _periodic_fit_curve(x_array, y_array, fit_x, fit_config, x_units, fit_weights)

        if periodic_candidate[0] is not None and periodic_candidate[1] is not None and periodic_candidate[2] > 0:

            candidates.append((periodic_candidate[0], periodic_candidate[1], periodic_candidate[2]))

    if not candidates:

        return None

    best_fit: np.ndarray | None = None

    best_score: float | None = None

    normalized_weights = _normalized_fit_weights(fit_weights)

    for fit_grid_y, model_y, parameter_count in candidates:

        bic_score = _weighted_bic(y_array, model_y, normalized_weights, parameter_count)

        if bic_score is None:

            continue

        if best_score is None or bic_score < best_score:

            best_score = bic_score

            best_fit = fit_grid_y

    return best_fit





def _normalized_fit_weights(fit_weights: np.ndarray) -> np.ndarray:

    normalized = np.asarray(fit_weights, dtype=float)

    normalized[~np.isfinite(normalized)] = 0.0

    normalized[normalized < 0] = 0.0

    if not np.any(normalized > 0):

        normalized = np.ones_like(normalized, dtype=float)

    return normalized





def _promote_gls_subharmonic_frequency(

    frequency_grid: np.ndarray,

    power_grid: np.ndarray,

    best_frequency: float,

) -> float:

    if frequency_grid.size == 0 or power_grid.size == 0:

        return best_frequency

    best_index = int(np.argmin(np.abs(frequency_grid - best_frequency)))

    best_power = float(power_grid[best_index])

    if not np.isfinite(best_power) or best_power <= 0:

        return best_frequency

    promoted_frequency = best_frequency

    promoted_power = best_power

    for harmonic in (2, 3, 4):

        candidate_frequency = best_frequency / harmonic

        if candidate_frequency <= 0:

            continue

        candidate_index = int(np.argmin(np.abs(frequency_grid - candidate_frequency)))

        grid_frequency = float(frequency_grid[candidate_index])

        candidate_power = float(power_grid[candidate_index])

        if not np.isfinite(grid_frequency) or grid_frequency <= 0 or not np.isfinite(candidate_power):

            continue

        if abs(grid_frequency - candidate_frequency) > (candidate_frequency * 0.08):

            continue

        if candidate_power >= promoted_power * 0.95:

            promoted_frequency = grid_frequency

            promoted_power = candidate_power

    return promoted_frequency





def _supports_periodic_auto_fit(

    x_array: np.ndarray,

    fit_config: LightCurveFitConfig,

    x_units: str,

) -> bool:

    if x_units == "phase":

        return True

    if x_units != "days" or x_array.size < 8:

        return False

    period_days = fit_config.periodic_period_hours / 24.0

    if not np.isfinite(period_days) or period_days <= 0:

        return False

    x_span = float(np.max(x_array) - np.min(x_array))

    if not np.isfinite(x_span) or x_span < (period_days * 1.5):

        return False

    phase = ((x_array - float(np.min(x_array))) / period_days) % 1.0

    occupied_bins = np.unique(np.floor(phase * 8.0).astype(int))

    if occupied_bins.size < 6:

        return False

    sorted_phase = np.sort(phase)

    wrapped_phase = np.concatenate((sorted_phase, [sorted_phase[0] + 1.0]))

    max_gap = float(np.max(np.diff(wrapped_phase)))

    return np.isfinite(max_gap) and max_gap <= 0.35





def _robust_polynomial_coefficients(

    x_values: np.ndarray,

    y_values: np.ndarray,

    degree: int,

    fit_weights: np.ndarray,

    robust_iterations: int,

) -> np.ndarray | None:

    combined_weights = _normalized_fit_weights(fit_weights)

    if len(x_values) <= degree:

        return None

    coefficients: np.ndarray | None = None

    for _ in range(max(1, robust_iterations)):

        weight_vector = np.sqrt(np.maximum(combined_weights, 1e-12))

        coefficients = np.polyfit(x_values, y_values, deg=degree, w=weight_vector)

        model_y = np.polyval(coefficients, x_values)

        robust_weights = _robust_residual_weights(y_values - model_y)

        combined_weights = _normalized_fit_weights(fit_weights) * robust_weights

    return coefficients





def _robust_weighted_linear_fit(

    design_matrix: np.ndarray,

    y_values: np.ndarray,

    fit_weights: np.ndarray,

    robust_iterations: int,

) -> np.ndarray | None:

    combined_weights = _normalized_fit_weights(fit_weights)

    coefficients: np.ndarray | None = None

    for _ in range(max(1, robust_iterations)):

        sqrt_weights = np.sqrt(np.maximum(combined_weights, 1e-12))

        weighted_design = design_matrix * sqrt_weights[:, None]

        weighted_y = y_values * sqrt_weights

        coefficients, _, _, _ = np.linalg.lstsq(weighted_design, weighted_y, rcond=None)

        model_y = design_matrix @ coefficients

        robust_weights = _robust_residual_weights(y_values - model_y)

        combined_weights = _normalized_fit_weights(fit_weights) * robust_weights

    return coefficients





def _robust_weighted_ridge_fit(

    design_matrix: np.ndarray,

    y_values: np.ndarray,

    fit_weights: np.ndarray,

    ridge_penalty: float,

    penalty_start: int,

    robust_iterations: int,

) -> np.ndarray | None:

    combined_weights = _normalized_fit_weights(fit_weights)

    penalty = np.zeros(design_matrix.shape[1], dtype=float)

    penalty[penalty_start:] = ridge_penalty

    coefficients: np.ndarray | None = None

    for _ in range(max(1, robust_iterations)):

        weight_matrix = np.diag(np.maximum(combined_weights, 1e-12))

        left = design_matrix.T @ weight_matrix @ design_matrix + np.diag(penalty)

        right = design_matrix.T @ weight_matrix @ y_values

        coefficients = np.linalg.solve(left, right)

        model_y = design_matrix @ coefficients

        robust_weights = _robust_residual_weights(y_values - model_y)

        combined_weights = _normalized_fit_weights(fit_weights) * robust_weights

    return coefficients





def _cubic_spline_design_matrix(x_values: np.ndarray, knots: np.ndarray) -> np.ndarray:

    columns = [np.ones_like(x_values), x_values, x_values ** 2, x_values ** 3]

    for knot in knots:

        columns.append(np.maximum(0.0, x_values - float(knot)) ** 3)

    return np.column_stack(columns)





def _robust_residual_weights(residuals: np.ndarray) -> np.ndarray:

    scale = 1.4826 * np.median(np.abs(residuals - np.median(residuals)))

    if not np.isfinite(scale) or scale <= 0:

        return np.ones_like(residuals, dtype=float)

    scaled = residuals / (4.685 * scale)

    weights = np.square(1.0 - np.square(np.clip(scaled, -1.0, 1.0)))

    weights[np.abs(scaled) >= 1.0] = 0.0

    return weights





def _weighted_bic(

    observed_y: np.ndarray,

    model_y: np.ndarray,

    fit_weights: np.ndarray,

    parameter_count: int,

) -> float | None:

    if observed_y.size != model_y.size or observed_y.size < 3 or parameter_count <= 0:

        return None

    normalized_weights = _normalized_fit_weights(fit_weights)

    residuals = observed_y - model_y

    rss = float(np.sum(normalized_weights * (residuals ** 2)) / np.mean(normalized_weights))

    rss = max(rss, 1e-12)

    n = observed_y.size

    return n * np.log(rss / n) + parameter_count * np.log(n)





def plot_annotated_image(

    axis: object,

    image_path: Path,

    measurement: PhotometryMeasurement | None,

    comparison_measurements: list[PhotometryMeasurement],

    empty_message: str,

    display: AnnotatedImageDisplay | None = None,

) -> None:

    axis.set_facecolor("black")

    try:

        display_payload = display or build_annotated_image_display(image_path)

    except Exception as exc:

        axis.set_title("Annotated Image")

        axis.text(0.5, 0.5, f"{empty_message}\n{exc}", ha="center", va="center", transform=axis.transAxes)

        return

    axis.imshow(display_payload.normalized_data, origin="upper", cmap="gray", norm=display_payload.norm)

    axis.set_title(_image_title(image_path, measurement))

    axis.set_xlabel("X (px)")

    axis.set_ylabel("Y (px)")

    axis.set_aspect("equal")



    if measurement is not None:

        _draw_source_overlay(axis, measurement, "lime")

        for comparison in comparison_measurements:

            _draw_source_overlay(axis, comparison, "gold", show_annulus=False)





def build_annotated_image_display(image_path: Path) -> AnnotatedImageDisplay:

    raw_data = np.asarray(read_image_data(image_path, dtype=None))

    raw_float_data = np.asarray(raw_data, dtype=float)

    color_data: np.ndarray | None = None

    linear_preview_normalized = _native_preview_data(raw_data)

    color_linear_preview_normalized = None

    data = raw_float_data

    if raw_data.ndim == 3:

        color_data = _coerce_multichannel_image_for_display(raw_float_data)

        color_linear_preview_normalized = _native_preview_rgb_data(raw_data)

        if linear_preview_normalized is None and color_linear_preview_normalized is not None:

            linear_preview_normalized = np.asarray(np.mean(color_linear_preview_normalized, axis=-1), dtype=float)

        data = _collapse_multichannel_image_for_display(raw_float_data)

    recommended_stretch_mode = _recommended_stretch_mode_for_image(image_path, raw_data, data)

    return build_annotated_image_display_from_array(

        data,

        image_path=image_path,

        color_data=color_data,

        linear_preview_normalized=linear_preview_normalized,

        color_linear_preview_normalized=color_linear_preview_normalized,

        recommended_stretch_mode=recommended_stretch_mode,

    )





def build_annotated_image_display_from_array(

    data: np.ndarray,

    *,

    image_path: Path,

    color_data: np.ndarray | None = None,

    linear_preview_normalized: np.ndarray | None = None,

    color_linear_preview_normalized: np.ndarray | None = None,

    recommended_stretch_mode: str = "stf",

) -> AnnotatedImageDisplay:

    if data.ndim != 2 or data.size == 0:

        raise ValueError("Image is not a usable 2D frame.")



    finite_pixels = data[np.isfinite(data)]

    if finite_pixels.size == 0:

        raise ValueError("Image contains no finite pixel values.")



    normalized_data = np.nan_to_num(

        data,

        nan=float(np.median(finite_pixels)),

        posinf=float(np.max(finite_pixels)),

        neginf=float(np.min(finite_pixels)),

    )

    vmin, vmax = _annotated_image_display_limits(normalized_data)

    norm = ImageNormalize(normalized_data, vmin=vmin, vmax=vmax, stretch=AsinhStretch())

    preview_normalized = _normalized_preview_data(normalized_data)

    asinh_preview = None if str(recommended_stretch_mode).strip().lower() == "linear" else np.asarray(norm(normalized_data), dtype=float)

    auto_stretch_source = _auto_stretch_source_normalized_data(normalized_data)

    auto_stretch_preview = None if str(recommended_stretch_mode).strip().lower() == "linear" else _stretched_image_data(
        auto_stretch_source,
        stretch_mode="stf",
        statistics_normalized=auto_stretch_source,
    )

    color_preview_normalized = None

    color_asinh_preview = None

    color_auto_stretch_preview = None

    if color_data is not None:

        color_preview_normalized = _normalized_preview_rgb_data(color_data, vmin=vmin, vmax=vmax)

        if str(recommended_stretch_mode).strip().lower() != "linear":

            color_asinh_preview = np.asarray(AsinhStretch()(color_preview_normalized), dtype=float)

            color_auto_stretch_source = _auto_stretch_source_normalized_data(color_data)

            color_auto_stretch_preview = _stretched_image_data(
                color_auto_stretch_source,
                stretch_mode="stf",
                statistics_normalized=auto_stretch_source,
            )

    return AnnotatedImageDisplay(

        image_path=image_path,

        normalized_data=normalized_data,

        norm=norm,

        preview_normalized=preview_normalized,

        asinh_preview=asinh_preview,

        auto_stretch_preview=auto_stretch_preview,

        linear_preview_normalized=linear_preview_normalized,

        color_preview_normalized=color_preview_normalized,

        color_asinh_preview=color_asinh_preview,

        color_auto_stretch_preview=color_auto_stretch_preview,

        color_linear_preview_normalized=color_linear_preview_normalized,

        recommended_stretch_mode=str(recommended_stretch_mode or "stf"),

    )





def build_placeholder_annotated_image_display(image_path: Path) -> AnnotatedImageDisplay:

    placeholder_data = np.zeros((1, 1), dtype=np.float32)

    placeholder_norm = ImageNormalize(placeholder_data, vmin=0.0, vmax=1.0, stretch=AsinhStretch())

    return AnnotatedImageDisplay(

        image_path=image_path,

        normalized_data=placeholder_data,

        norm=placeholder_norm,

        preview_normalized=placeholder_data,

        asinh_preview=placeholder_data,

        auto_stretch_preview=placeholder_data,

        linear_preview_normalized=placeholder_data,

        color_preview_normalized=None,

        color_asinh_preview=None,

        color_auto_stretch_preview=None,

        color_linear_preview_normalized=None,

        recommended_stretch_mode="asinh",

    )



def _recommended_stretch_mode_for_image(image_path: Path, raw_data: np.ndarray, grayscale_data: np.ndarray) -> str:

    suffix = image_path.suffix.lower()

    if suffix in _LIKELY_ALREADY_STRETCHED_SUFFIXES:

        return "linear"

    if suffix not in _RASTER_IMAGE_SUFFIXES:


        return "stf"

    sampled_pixels = _sampled_finite_pixels(np.asarray(grayscale_data, dtype=np.float32))

    if sampled_pixels.size == 0:


        return "stf"

    if np.issubdtype(raw_data.dtype, np.integer):

        if np.iinfo(raw_data.dtype).bits <= 8:

            return "linear"

        minimum_value, maximum_value = _native_preview_range(raw_data.dtype)

        if maximum_value <= minimum_value:


            return "stf"

        normalized_sample = np.clip((sampled_pixels - minimum_value) / float(maximum_value - minimum_value), 0.0, 1.0)

    else:

        sample_min = float(np.nanpercentile(sampled_pixels, 1.0))

        sample_max = float(np.nanpercentile(sampled_pixels, 99.9))

        if not np.isfinite(sample_min) or not np.isfinite(sample_max) or sample_max <= sample_min:


            return "stf"

        normalized_sample = np.clip((sampled_pixels - sample_min) / float(sample_max - sample_min), 0.0, 1.0)

    bright_fraction = float(np.mean(normalized_sample >= 0.05))

    upper_percentile = float(np.nanpercentile(normalized_sample, 99.0))

    if bright_fraction >= 0.03 and upper_percentile >= 0.5:

        return "linear"


    return "stf"





def render_image_path_for_display(image_path: Path, settings: AnnotatedImageRenderSettings | None = None) -> np.ndarray:

    render_settings = settings or AnnotatedImageRenderSettings()

    raw_source_data = np.asarray(read_image_data(image_path, dtype=None))

    raw_data = np.asarray(raw_source_data, dtype=np.float32)

    linear_normalized = _native_preview_data(raw_source_data)

    color_linear_normalized = None

    if raw_data.ndim == 3:

        color_data = _coerce_multichannel_image_for_display(raw_data).astype(np.float32, copy=False)

        color_linear_normalized = _native_preview_rgb_data(raw_source_data)

        grayscale_data = _collapse_multichannel_image_for_display(raw_data).astype(np.float32, copy=False)

    else:

        color_data = None

        grayscale_data = np.asarray(raw_data, dtype=np.float32)

    if grayscale_data.ndim != 2 or grayscale_data.size == 0:

        raise ValueError("Image is not a usable 2D frame.")



    sampled_pixels = _sampled_finite_pixels(grayscale_data)

    if sampled_pixels.size == 0:

        raise ValueError("Image contains no finite pixel values.")

    median_value = float(np.nanmedian(sampled_pixels))

    minimum_value = float(np.nanmin(sampled_pixels))

    maximum_value = float(np.nanmax(sampled_pixels))

    grayscale_clean = np.nan_to_num(grayscale_data, nan=median_value, posinf=maximum_value, neginf=minimum_value)

    vmin, vmax = _annotated_image_display_limits_from_pixels(sampled_pixels)

    normalized_grayscale = _normalize_image_plane(grayscale_clean, vmin=vmin, vmax=vmax)

    if color_data is not None:

        color_clean = np.nan_to_num(color_data, nan=median_value, posinf=maximum_value, neginf=minimum_value)

        normalized = _normalize_image_plane(color_clean, vmin=vmin, vmax=vmax)

    else:

        normalized = normalized_grayscale

    normalized_stretch_mode = render_settings.stretch_mode.strip().lower()

    auto_stretch_source_grayscale: np.ndarray | None = None

    if normalized_stretch_mode == "linear":

        preferred_linear = color_linear_normalized if color_linear_normalized is not None else linear_normalized

        if preferred_linear is not None:

            normalized = np.asarray(preferred_linear, dtype=np.float32)

    elif normalized_stretch_mode == "stf":

        auto_stretch_source_grayscale = _auto_stretch_source_normalized_data(grayscale_clean)

        if color_data is not None:

            normalized = _auto_stretch_source_normalized_data(color_clean)

        else:

            normalized = auto_stretch_source_grayscale

    normalized = _apply_image_level_points(normalized, render_settings)

    normalized = _apply_image_curve_points(normalized, render_settings)

    stretched = _stretched_image_data(
        normalized,
        stretch_mode=render_settings.stretch_mode,
        statistics_normalized=auto_stretch_source_grayscale,
    )

    adjusted = ((stretched - 0.5) * max(0.05, float(render_settings.contrast))) + 0.5 + float(render_settings.brightness)

    adjusted = np.clip(adjusted, 0.0, 1.0)

    if render_settings.inverted:

        adjusted = 1.0 - adjusted

    return np.ascontiguousarray((adjusted * 255.0).astype(np.uint8))





def render_annotated_image(display: AnnotatedImageDisplay, settings: AnnotatedImageRenderSettings | None = None) -> np.ndarray:

    render_settings = settings or AnnotatedImageRenderSettings()

    stretch_mode = render_settings.stretch_mode.strip().lower()

    linear_normalized = (

        display.color_linear_preview_normalized

        if display.color_linear_preview_normalized is not None

        else display.linear_preview_normalized

    )

    normalized = display.color_preview_normalized

    if normalized is None:

        normalized = display.preview_normalized

        if normalized is None:

            normalized = _normalized_preview_data(display.normalized_data)

    if stretch_mode == "linear" and linear_normalized is not None:

        normalized = linear_normalized

    if stretch_mode == "asinh" and _using_default_image_levels(render_settings) and _using_default_image_curve(render_settings):

        if display.color_preview_normalized is not None:

            stretched = display.color_asinh_preview

            if stretched is None:

                stretched = np.asarray(AsinhStretch()(display.color_preview_normalized), dtype=float)

        else:

            stretched = display.asinh_preview

            if stretched is None:

                stretched = np.asarray(display.norm(display.normalized_data), dtype=float)

    elif stretch_mode == "stf" and _using_default_image_levels(render_settings) and _using_default_image_curve(render_settings):

        if display.color_preview_normalized is not None:

            stretched = display.color_auto_stretch_preview

            if stretched is None:

                stretched = _stretched_image_data(display.color_preview_normalized, stretch_mode="stf")

        else:

            stretched = display.auto_stretch_preview

            if stretched is None:

                stretched = _stretched_image_data(
                    display.preview_normalized if display.preview_normalized is not None else display.normalized_data,
                    stretch_mode="stf",
                )

    else:

        leveled = _apply_image_level_points(normalized, render_settings)

        leveled = _apply_image_curve_points(leveled, render_settings)

        stretched = _stretched_image_data(leveled, stretch_mode=stretch_mode)



    adjusted = ((stretched - 0.5) * max(0.05, float(render_settings.contrast))) + 0.5 + float(render_settings.brightness)

    adjusted = np.clip(adjusted, 0.0, 1.0)

    if render_settings.inverted:

        adjusted = 1.0 - adjusted

    return np.ascontiguousarray((adjusted * 255.0).astype(np.uint8))





def _normalized_preview_data(data: np.ndarray) -> np.ndarray:

    vmin, vmax = _annotated_image_display_limits(data)

    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:

        return np.zeros_like(data, dtype=float)

    normalized = (np.asarray(data, dtype=float) - float(vmin)) / float(vmax - vmin)

    return np.clip(normalized, 0.0, 1.0)



def _native_preview_data(data: np.ndarray) -> np.ndarray | None:

    if not np.issubdtype(data.dtype, np.integer):

        return None

    minimum_value, maximum_value = _native_preview_range(data.dtype)

    normalized = (np.asarray(data, dtype=float) - minimum_value) / float(maximum_value - minimum_value)

    return np.clip(normalized, 0.0, 1.0)




def _resolved_image_level_points(render_settings: AnnotatedImageRenderSettings) -> tuple[float, float, float]:

    minimum_span = 1e-3

    black_point = min(1.0 - minimum_span, max(0.0, float(render_settings.black_point)))

    white_point = min(1.0, max(black_point + minimum_span, float(render_settings.white_point)))

    midtone_point = min(white_point - minimum_span, max(black_point + minimum_span, float(render_settings.midtone_point)))

    return black_point, midtone_point, white_point




def _using_default_image_levels(render_settings: AnnotatedImageRenderSettings) -> bool:

    black_point, midtone_point, white_point = _resolved_image_level_points(render_settings)

    return (

        abs(black_point - 0.0) <= 1e-6

        and abs(midtone_point - 0.5) <= 1e-6

        and abs(white_point - 1.0) <= 1e-6

    )





def _resolved_image_curve_points(render_settings: AnnotatedImageRenderSettings) -> tuple[tuple[float, float], ...]:

    raw_points = getattr(render_settings, "curve_points", ()) or ()

    resolved: list[tuple[float, float]] = []

    for raw_point in raw_points:

        try:

            raw_x, raw_y = raw_point

            x_value = min(1.0, max(0.0, float(raw_x)))

            y_value = min(1.0, max(0.0, float(raw_y)))

        except (TypeError, ValueError):

            continue

        if not np.isfinite(x_value) or not np.isfinite(y_value):

            continue

        resolved.append((x_value, y_value))

    if len(resolved) < 2:

        return ((0.0, 0.0), (1.0, 1.0))

    resolved.sort(key=lambda point: point[0])

    deduplicated: list[tuple[float, float]] = []

    for x_value, y_value in resolved:

        if deduplicated and abs(x_value - deduplicated[-1][0]) <= 1e-5:

            deduplicated[-1] = (x_value, y_value)

        else:

            deduplicated.append((x_value, y_value))

    if len(deduplicated) < 2:

        return ((0.0, 0.0), (1.0, 1.0))

    return tuple(deduplicated)



def _using_default_image_curve(render_settings: AnnotatedImageRenderSettings) -> bool:

    points = _resolved_image_curve_points(render_settings)

    return (

        len(points) == 2

        and abs(points[0][0] - 0.0) <= 1e-6

        and abs(points[0][1] - 0.0) <= 1e-6

        and abs(points[1][0] - 1.0) <= 1e-6

        and abs(points[1][1] - 1.0) <= 1e-6

    )



def _apply_image_curve_points(normalized: np.ndarray, render_settings: AnnotatedImageRenderSettings) -> np.ndarray:

    if _using_default_image_curve(render_settings):

        return normalized

    points = _resolved_image_curve_points(render_settings)

    x_values = np.asarray([point[0] for point in points], dtype=np.float32)

    y_values = np.asarray([point[1] for point in points], dtype=np.float32)

    normalized_array = np.clip(np.asarray(normalized, dtype=np.float32), 0.0, 1.0)

    mapped = np.interp(normalized_array.ravel(), x_values, y_values).reshape(normalized_array.shape)

    return mapped.astype(np.float32, copy=False)



def _apply_image_level_points(normalized: np.ndarray, render_settings: AnnotatedImageRenderSettings) -> np.ndarray:

    black_point, midtone_point, white_point = _resolved_image_level_points(render_settings)

    if _using_default_image_levels(render_settings):

        return normalized

    leveled = (np.asarray(normalized, dtype=np.float32) - np.float32(black_point)) / np.float32(white_point - black_point)

    leveled = np.clip(leveled, 0.0, 1.0)

    midtone_position = min(1.0 - 1e-3, max(1e-3, float((midtone_point - black_point) / float(white_point - black_point))))

    gamma = float(np.log(0.5) / np.log(midtone_position))

    if not np.isfinite(gamma) or gamma <= 0:

        return leveled

    return np.power(leveled, gamma)





def _sampled_finite_pixels(data: np.ndarray, *, max_sample_pixels: int = 524288) -> np.ndarray:

    finite_mask = np.isfinite(data)

    if not finite_mask.any():

        return np.asarray([], dtype=np.float32)

    sample = data

    if data.ndim >= 2 and data.size > max_sample_pixels:

        stride = max(1, int(np.ceil(np.sqrt(data.size / max_sample_pixels))))

        sample = data[::stride, ::stride]

        finite_mask = np.isfinite(sample)

    return np.asarray(sample[finite_mask], dtype=np.float32)





def _annotated_image_display_limits_from_pixels(finite_pixels: np.ndarray) -> tuple[float, float]:

    if finite_pixels.size == 0:

        return 0.0, 1.0



    _, background_median, background_std = sigma_clipped_stats(finite_pixels, sigma=3.0, maxiters=5)

    high_percentile = float(np.nanpercentile(finite_pixels, _ANNOTATED_IMAGE_HIGHLIGHT_PERCENTILE))



    if not np.isfinite(background_median):

        background_median = float(np.nanmedian(finite_pixels))

    if not np.isfinite(background_std) or background_std <= 0:

        interval = PercentileInterval(99.5)

        vmin, vmax = interval.get_limits(finite_pixels)

        if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:

            minimum = float(np.nanmin(finite_pixels))

            maximum = float(np.nanmax(finite_pixels))

            if not np.isfinite(minimum) or not np.isfinite(maximum) or maximum <= minimum:

                return 0.0, 1.0

            return minimum, maximum

        return float(vmin), float(vmax)



    vmin = float(background_median + (_ANNOTATED_IMAGE_BACKGROUND_SIGMA * background_std))

    vmax = float(max(high_percentile, background_median + (8.0 * background_std)))

    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:

        interval = PercentileInterval(99.5)

        fallback_min, fallback_max = interval.get_limits(finite_pixels)

        return float(fallback_min), float(fallback_max)

    return vmin, vmax





def _annotated_image_display_limits(data: np.ndarray) -> tuple[float, float]:

    finite_pixels = np.asarray(data[np.isfinite(data)], dtype=float)

    return _annotated_image_display_limits_from_pixels(finite_pixels)





def _normalize_image_plane(data: np.ndarray, *, vmin: float, vmax: float) -> np.ndarray:

    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:

        return np.zeros_like(data, dtype=np.float32)

    normalized = (np.asarray(data, dtype=np.float32) - np.float32(vmin)) / np.float32(vmax - vmin)

    return np.clip(normalized, 0.0, 1.0)





def _stretched_image_data(
    normalized: np.ndarray,
    *,
    stretch_mode: str,
    statistics_normalized: np.ndarray | None = None,
) -> np.ndarray:

    normalized_mode = stretch_mode.strip().lower()

    if normalized_mode == "linear":

        return normalized

    if normalized_mode == "stf":

        return _stf_stretched_image_data(normalized, statistics_normalized=statistics_normalized)

    if normalized_mode == "sqrt":

        return np.sqrt(normalized)

    if normalized_mode == "log":

        return np.log1p(999.0 * normalized) / np.log(1000.0)

    return np.asarray(AsinhStretch()(normalized), dtype=np.float32)



def _stf_stretched_image_data(normalized: np.ndarray, *, statistics_normalized: np.ndarray | None = None) -> np.ndarray:

    normalized_array = np.clip(np.asarray(normalized, dtype=np.float32), 0.0, 1.0)

    midtones_balance, shadows_clip, highlights_clip = _adaptive_display_function_parameters(
        statistics_normalized if statistics_normalized is not None else normalized_array,
    )

    return _apply_display_function(
        normalized_array,
        midtones_balance=midtones_balance,
        shadows_clip=shadows_clip,
        highlights_clip=highlights_clip,
    )



def _adaptive_display_function_parameters(normalized: np.ndarray) -> tuple[float, float, float]:

    statistics_plane = np.asarray(normalized, dtype=np.float32)

    if statistics_plane.ndim == 3:

        statistics_plane = _collapse_multichannel_image_for_display(statistics_plane)

    sampled_pixels = _sampled_finite_pixels(statistics_plane)

    if sampled_pixels.size == 0:

        return 0.5, 0.0, 1.0



    median_value = float(np.nanmedian(sampled_pixels))

    if not np.isfinite(median_value):

        return 0.5, 0.0, 1.0



    median_absolute_deviation = float(np.nanmedian(np.abs(sampled_pixels - median_value)))

    robust_sigma = 1.4826 * median_absolute_deviation

    if not np.isfinite(robust_sigma):

        robust_sigma = 0.0



    if median_value <= 0.5:

        shadows_clip = float(np.clip(median_value - (_ANNOTATED_IMAGE_STF_SHADOW_SIGMA * robust_sigma), 0.0, 1.0))

        highlights_clip = 1.0

        median_position = float(np.clip(median_value - shadows_clip, 0.0, 1.0))

        target_value = _ANNOTATED_IMAGE_STF_TARGET_BACKGROUND

    else:

        shadows_clip = 0.0

        highlights_clip = float(np.clip(median_value + (_ANNOTATED_IMAGE_STF_SHADOW_SIGMA * robust_sigma), 0.0, 1.0))

        median_position = float(np.clip(highlights_clip - median_value, 0.0, 1.0))

        target_value = 1.0 - _ANNOTATED_IMAGE_STF_TARGET_BACKGROUND



    midtones_balance = _midtones_balance_for_target(median_position, target_value)

    return midtones_balance, shadows_clip, highlights_clip



def _apply_display_function(
    normalized: np.ndarray,
    *,
    midtones_balance: float,
    shadows_clip: float,
    highlights_clip: float,
) -> np.ndarray:

    denominator = max(1e-6, float(highlights_clip) - float(shadows_clip))

    clipped = np.clip((np.asarray(normalized, dtype=np.float32) - np.float32(shadows_clip)) / np.float32(denominator), 0.0, 1.0)

    return _midtones_transfer_function(clipped, float(midtones_balance))



def _midtones_balance_for_target(input_value: float, target_value: float) -> float:

    normalized_input = float(np.clip(input_value, 1e-6, 1.0 - 1e-6))

    normalized_target = float(np.clip(target_value, 1e-6, 1.0 - 1e-6))

    return float(_midtones_transfer_function(np.array([normalized_input], dtype=np.float32), normalized_target)[0])



def _midtones_transfer_function(values: np.ndarray, midtones_balance: float) -> np.ndarray:

    clipped_values = np.clip(np.asarray(values, dtype=np.float32), 0.0, 1.0)

    clipped_midtones = float(np.clip(midtones_balance, 1e-6, 1.0 - 1e-6))

    denominator = ((2.0 * clipped_midtones) - 1.0) * clipped_values - clipped_midtones

    safe_denominator = np.where(np.abs(denominator) <= 1e-6, np.float32(-1e-6), denominator)

    transformed = ((clipped_midtones - 1.0) * clipped_values) / safe_denominator

    return np.clip(np.asarray(transformed, dtype=np.float32), 0.0, 1.0)



def _auto_stretch_source_normalized_data(data: np.ndarray) -> np.ndarray:
    data_array = np.asarray(data, dtype=np.float32)

    statistics_plane = data_array

    if statistics_plane.ndim == 3:

        statistics_plane = _collapse_multichannel_image_for_display(statistics_plane)

    finite_pixels = _sampled_finite_pixels(statistics_plane)

    if finite_pixels.size == 0:

        return np.zeros_like(data_array, dtype=np.float32)



    minimum_value = float(np.nanmin(finite_pixels))

    maximum_value = float(np.nanmax(finite_pixels))

    if not np.isfinite(minimum_value) or not np.isfinite(maximum_value) or maximum_value <= minimum_value:

        return np.zeros_like(data_array, dtype=np.float32)



    if minimum_value >= -1e-6 and maximum_value <= 1.0 + 1e-6:

        lower_bound = 0.0

        upper_bound = 1.0

    elif minimum_value >= 0.0 and maximum_value > 256.0:

        lower_bound = 0.0

        upper_bound = max(
            _ANNOTATED_IMAGE_STF_NATIVE_RANGE_FLOOR,
            float((2 ** int(np.ceil(np.log2(maximum_value + 1.0)))) - 1.0),
        )

    else:

        lower_bound = min(0.0, minimum_value)

        upper_bound = maximum_value



    return _normalize_image_plane(data_array, vmin=lower_bound, vmax=upper_bound)





def _normalized_preview_rgb_data(data: np.ndarray, *, vmin: float, vmax: float) -> np.ndarray:

    if data.ndim != 3 or data.shape[-1] != 3:

        raise ValueError("RGB preview data must be an HxWx3 array.")

    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:

        return np.zeros_like(data, dtype=float)

    normalized = (np.asarray(data, dtype=float) - float(vmin)) / float(vmax - vmin)

    return np.clip(normalized, 0.0, 1.0)



def _native_preview_rgb_data(data: np.ndarray) -> np.ndarray | None:

    if data.ndim != 3 or data.shape[-1] != 3 or not np.issubdtype(data.dtype, np.integer):

        return None

    minimum_value, maximum_value = _native_preview_range(data.dtype)

    normalized = (np.asarray(data, dtype=float) - minimum_value) / float(maximum_value - minimum_value)

    return np.clip(normalized, 0.0, 1.0)



def _native_preview_range(dtype: np.dtype | type[np.generic]) -> tuple[float, float]:

    info = np.iinfo(dtype)

    if info.max <= info.min:

        return 0.0, 1.0

    return float(info.min), float(info.max)





def _collapse_multichannel_image_for_display(data: np.ndarray) -> np.ndarray:

    if data.shape[-1] in {1, 3, 4}:

        return np.asarray(np.mean(data[..., :3], axis=-1), dtype=float)

    if data.shape[0] in {1, 3, 4}:

        return np.asarray(np.mean(data[:3], axis=0), dtype=float)

    raise ValueError("Image is not a usable grayscale or RGB frame.")





def _coerce_multichannel_image_for_display(data: np.ndarray) -> np.ndarray:

    if data.shape[-1] in {3, 4}:

        return np.asarray(data[..., :3], dtype=float)

    if data.shape[0] in {3, 4}:

        return np.asarray(np.moveaxis(data[:3], 0, -1), dtype=float)

    if data.shape[-1] == 1:

        single_channel = np.asarray(data[..., 0], dtype=float)

        return np.stack((single_channel, single_channel, single_channel), axis=-1)

    if data.shape[0] == 1:

        single_channel = np.asarray(data[0], dtype=float)

        return np.stack((single_channel, single_channel, single_channel), axis=-1)

    raise ValueError("Image is not a usable grayscale or RGB frame.")





def _draw_source_overlay(

    axis: object,

    measurement: PhotometryMeasurement,

    color: str,

    show_annulus: bool = True,

) -> None:

    aperture_radius = measurement.aperture_radius or 5.0

    annulus_inner_radius = measurement.annulus_inner_radius or max(aperture_radius + 2.0, 8.0)

    annulus_outer_radius = measurement.annulus_outer_radius or max(annulus_inner_radius + 2.0, 12.0)

    axis.add_patch(Circle((measurement.x, measurement.y), aperture_radius, fill=False, edgecolor=color, linewidth=1.6))

    if show_annulus:

        axis.add_patch(

            Circle(

                (measurement.x, measurement.y),

                annulus_inner_radius,

                fill=False,

                edgecolor=color,

                linewidth=1.0,

                linestyle="--",

                alpha=0.65,

            )

        )

        axis.add_patch(

            Circle(

                (measurement.x, measurement.y),

                annulus_outer_radius,

                fill=False,

                edgecolor=color,

                linewidth=1.0,

                linestyle="--",

                alpha=0.65,

            )

        )

    axis.text(

        measurement.x + aperture_radius + 2.0,

        measurement.y + aperture_radius + 2.0,

        measurement.source_name,

        color=color,

        fontsize=8,

        bbox={"facecolor": "black", "alpha": 0.35, "edgecolor": "none", "pad": 1.5},

        clip_on=True,

    )





def _image_title(image_path: Path, measurement: PhotometryMeasurement | None) -> str:

    if measurement is None:

        return image_path.name

    return f"{image_path.name} | {measurement.source_name} [{measurement.filter_name or '-'}]"