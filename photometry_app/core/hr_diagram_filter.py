"""Display filters for HR Diagram mode.

Color and spectral type use Gaia BP-RP. Distance uses Gaia parallax.
Luminosity and luminosity class use Gaia absolute G. A star with no value
for an enabled filter is removed.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from photometry_app.core.distance_map import parallax_to_distance_pc


SOLAR_ABSOLUTE_G_MAGNITUDE = 4.67
STELLAR_CLASS_MAGNITUDE_TOLERANCE = 1.75
_CLASS_COLOR_MARGIN = 0.08

TEMPERATURE_MIN_K = 2600.0
TEMPERATURE_MAX_K = 30000.0
DISTANCE_MIN_PC = 0.1
DISTANCE_MAX_PC = 1_000_000.0
LUMINOSITY_MIN_SOLAR = 1.0e-6
LUMINOSITY_MAX_SOLAR = 1.0e7

# Same Gaia BP-RP temperature anchors as the HR plot axis.
_BP_RP_COLOR_ANCHORS = np.asarray((-0.4, 0.0, 0.3, 0.6, 0.9, 1.2, 1.5, 1.8, 2.2, 2.6, 3.0), dtype=float)
_BP_RP_TEMPERATURE_ANCHORS = np.asarray(
    (30000.0, 10000.0, 7600.0, 6200.0, 5300.0, 4600.0, 4000.0, 3600.0, 3200.0, 2900.0, 2600.0),
    dtype=float,
)
_TEMPERATURE_ASCENDING_ANCHORS = _BP_RP_TEMPERATURE_ANCHORS[::-1]
_COLOR_FOR_ASCENDING_TEMPERATURE = _BP_RP_COLOR_ANCHORS[::-1]

# name, minimum K inclusive, maximum K exclusive
SPECTRAL_TYPE_BOUNDS: tuple[tuple[str, float, float], ...] = (
    ("M", 0.0, 3700.0),
    ("K", 3700.0, 5200.0),
    ("G", 5200.0, 6000.0),
    ("F", 6000.0, 7500.0),
    ("A", 7500.0, 10000.0),
    ("B", 10000.0, 25000.0),
    ("O", 25000.0, math.inf),
)
SPECTRAL_TYPE_ORDER: tuple[str, ...] = ("O", "B", "A", "F", "G", "K", "M")

# Educational class-guide curves, matching the HR plot overlay.
HR_LUMINOSITY_CLASS_GUIDES: tuple[tuple[str, tuple[float, ...], tuple[float, ...]], ...] = (
    ("Supergiants", (-0.35, 0.15, 0.7, 1.35, 2.1, 2.85), (-9.4, -9.1, -8.7, -8.1, -7.0, -5.6)),
    ("Giants", (0.75, 1.1, 1.45, 1.9, 2.35, 2.8), (-2.7, -2.3, -1.8, -1.0, -0.1, 1.2)),
    ("Subgiants", (0.4, 0.75, 1.1, 1.5, 1.95, 2.35), (0.2, 0.8, 1.6, 2.6, 3.7, 5.0)),
    (
        "Main Sequence",
        (-0.4, -0.1, 0.25, 0.6, 0.95, 1.3, 1.7, 2.1, 2.55, 3.0),
        (-3.3, -1.8, 0.0, 2.2, 4.8, 7.0, 8.8, 10.3, 11.6, 13.0),
    ),
    ("White Dwarfs", (-0.55, -0.2, 0.15, 0.55, 0.95, 1.35), (8.1, 8.8, 9.8, 11.0, 12.3, 13.8)),
)
LUMINOSITY_CLASS_ORDER: tuple[str, ...] = tuple(name for name, _x_values, _y_values in HR_LUMINOSITY_CLASS_GUIDES)

_REMOVAL_ORDER = (
    "missing_color",
    "outside_color",
    "missing_distance",
    "outside_distance",
    "missing_luminosity",
    "outside_luminosity",
    "missing_stellar_class",
    "outside_stellar_class",
)


def color_index_to_temperature_kelvin(value: float) -> float:
    return float(
        np.interp(
            float(value),
            _BP_RP_COLOR_ANCHORS,
            _BP_RP_TEMPERATURE_ANCHORS,
            left=float(_BP_RP_TEMPERATURE_ANCHORS[0]),
            right=float(_BP_RP_TEMPERATURE_ANCHORS[-1]),
        )
    )


def temperature_kelvin_to_color_index(value: float) -> float:
    return float(
        np.interp(
            float(value),
            _TEMPERATURE_ASCENDING_ANCHORS,
            _COLOR_FOR_ASCENDING_TEMPERATURE,
            left=float(_COLOR_FOR_ASCENDING_TEMPERATURE[0]),
            right=float(_COLOR_FOR_ASCENDING_TEMPERATURE[-1]),
        )
    )


def absolute_g_to_luminosity(absolute_magnitude: float) -> float:
    return float(10.0 ** ((SOLAR_ABSOLUTE_G_MAGNITUDE - float(absolute_magnitude)) / 2.5))


def format_solar_luminosity(value: float) -> str:
    if not math.isfinite(value) or value <= 0.0:
        return "-"
    if value >= 1.0e4 or value < 1.0e-2:
        return f"{value:.2e}"
    if value >= 100.0:
        return f"{value:.0f}"
    if value >= 10.0:
        return f"{value:.1f}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def spectral_type_for_temperature(temperature_k: float) -> str | None:
    if not math.isfinite(temperature_k):
        return None
    for name, minimum_k, maximum_k in SPECTRAL_TYPE_BOUNDS:
        if minimum_k <= float(temperature_k) < maximum_k:
            return name
    return None


def luminosity_class_for_position(bp_rp: float, absolute_magnitude: float) -> str | None:
    if not math.isfinite(bp_rp) or not math.isfinite(absolute_magnitude):
        return None
    best_name: str | None = None
    best_delta = math.inf
    for name, x_values, y_values in HR_LUMINOSITY_CLASS_GUIDES:
        x_min = float(x_values[0])
        x_max = float(x_values[-1])
        if bp_rp < x_min - _CLASS_COLOR_MARGIN or bp_rp > x_max + _CLASS_COLOR_MARGIN:
            continue
        clamped_color = min(max(float(bp_rp), x_min), x_max)
        guide_magnitude = float(np.interp(clamped_color, x_values, y_values))
        color_penalty = abs(float(bp_rp) - clamped_color) * 4.0
        delta = abs(float(absolute_magnitude) - guide_magnitude) + color_penalty
        if delta < best_delta:
            best_delta = delta
            best_name = name
    if best_name is None or best_delta > STELLAR_CLASS_MAGNITUDE_TOLERANCE:
        return None
    return best_name


def _finite(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _finite_or(value: object, default: float) -> float:
    number = _finite(value)
    return default if number is None else number


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _outside_range(value: float, minimum: float, maximum: float) -> bool:
    if value < minimum and not math.isclose(value, minimum, rel_tol=1e-9, abs_tol=1e-6):
        return True
    if value > maximum and not math.isclose(value, maximum, rel_tol=1e-9, abs_tol=1e-6):
        return True
    return False


def _ordered_names(selected: frozenset[str], order: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(name for name in order if name in selected)


@dataclass(frozen=True, slots=True)
class HrDiagramFilter:
    color_enabled: bool = False
    temperature_min_k: float = TEMPERATURE_MIN_K
    temperature_max_k: float = TEMPERATURE_MAX_K
    distance_enabled: bool = False
    distance_min_pc: float = 1.0
    distance_max_pc: float = 100_000.0
    luminosity_enabled: bool = False
    luminosity_min_solar: float = LUMINOSITY_MIN_SOLAR
    luminosity_max_solar: float = LUMINOSITY_MAX_SOLAR
    stellar_class_enabled: bool = False
    spectral_types: frozenset[str] = field(default_factory=lambda: frozenset(SPECTRAL_TYPE_ORDER))
    luminosity_classes: frozenset[str] = field(default_factory=lambda: frozenset(LUMINOSITY_CLASS_ORDER))

    def normalized(self) -> HrDiagramFilter:
        temperature_min = _clamp(_finite_or(self.temperature_min_k, TEMPERATURE_MIN_K), TEMPERATURE_MIN_K, TEMPERATURE_MAX_K)
        temperature_max = _clamp(_finite_or(self.temperature_max_k, TEMPERATURE_MAX_K), TEMPERATURE_MIN_K, TEMPERATURE_MAX_K)
        if temperature_min > temperature_max:
            temperature_min, temperature_max = temperature_max, temperature_min

        distance_min = _clamp(_finite_or(self.distance_min_pc, 1.0), DISTANCE_MIN_PC, DISTANCE_MAX_PC)
        distance_max = _clamp(_finite_or(self.distance_max_pc, 100_000.0), DISTANCE_MIN_PC, DISTANCE_MAX_PC)
        if distance_min > distance_max:
            distance_min, distance_max = distance_max, distance_min

        luminosity_min = _clamp(
            _finite_or(self.luminosity_min_solar, LUMINOSITY_MIN_SOLAR),
            LUMINOSITY_MIN_SOLAR,
            LUMINOSITY_MAX_SOLAR,
        )
        luminosity_max = _clamp(
            _finite_or(self.luminosity_max_solar, LUMINOSITY_MAX_SOLAR),
            LUMINOSITY_MIN_SOLAR,
            LUMINOSITY_MAX_SOLAR,
        )
        if luminosity_min <= 0.0:
            luminosity_min = LUMINOSITY_MIN_SOLAR
        if luminosity_max <= 0.0:
            luminosity_max = LUMINOSITY_MAX_SOLAR
        if luminosity_min > luminosity_max:
            luminosity_min, luminosity_max = luminosity_max, luminosity_min

        spectral_types = frozenset(name for name in self.spectral_types if name in SPECTRAL_TYPE_ORDER)
        luminosity_classes = frozenset(name for name in self.luminosity_classes if name in LUMINOSITY_CLASS_ORDER)
        stellar_class_enabled = bool(self.stellar_class_enabled) and bool(spectral_types or luminosity_classes)
        return HrDiagramFilter(
            color_enabled=bool(self.color_enabled),
            temperature_min_k=temperature_min,
            temperature_max_k=temperature_max,
            distance_enabled=bool(self.distance_enabled),
            distance_min_pc=distance_min,
            distance_max_pc=distance_max,
            luminosity_enabled=bool(self.luminosity_enabled),
            luminosity_min_solar=luminosity_min,
            luminosity_max_solar=luminosity_max,
            stellar_class_enabled=stellar_class_enabled,
            spectral_types=spectral_types,
            luminosity_classes=luminosity_classes,
        )

    @property
    def is_active(self) -> bool:
        normalized = self.normalized()
        return bool(
            normalized.color_enabled
            or normalized.distance_enabled
            or normalized.luminosity_enabled
            or normalized.stellar_class_enabled
        )


@dataclass(frozen=True, slots=True)
class HrDiagramFilterSummary:
    diagram_filter: HrDiagramFilter
    considered: int
    kept: int
    removed: dict[str, int]
    kept_spectral_types: dict[str, int]
    kept_luminosity_classes: dict[str, int]


def star_temperature_k(row: object) -> float | None:
    bp_rp = _finite(getattr(row, "gaia_bp_rp", None))
    if bp_rp is None:
        return None
    return color_index_to_temperature_kelvin(bp_rp)


def star_distance_pc(row: object) -> float | None:
    return parallax_to_distance_pc(_finite(getattr(row, "parallax_mas", None)))


def star_luminosity_solar(row: object) -> float | None:
    absolute_magnitude = _finite(getattr(row, "gaia_absolute_magnitude", None))
    if absolute_magnitude is None:
        return None
    luminosity = absolute_g_to_luminosity(absolute_magnitude)
    if not math.isfinite(luminosity) or luminosity <= 0.0:
        return None
    return luminosity


def star_spectral_type(row: object) -> str | None:
    temperature = star_temperature_k(row)
    if temperature is None:
        return None
    return spectral_type_for_temperature(temperature)


def star_luminosity_class(row: object) -> str | None:
    bp_rp = _finite(getattr(row, "gaia_bp_rp", None))
    absolute_magnitude = _finite(getattr(row, "gaia_absolute_magnitude", None))
    if bp_rp is None or absolute_magnitude is None:
        return None
    return luminosity_class_for_position(bp_rp, absolute_magnitude)


def hr_diagram_filter_rejection(row: object, diagram_filter: HrDiagramFilter) -> str | None:
    """Return why a star fails, or None when it stays. Earlier filters win."""

    active = diagram_filter.normalized()
    if not active.is_active:
        return None
    if active.color_enabled:
        temperature = star_temperature_k(row)
        if temperature is None:
            return "missing_color"
        if _outside_range(temperature, active.temperature_min_k, active.temperature_max_k):
            return "outside_color"
    if active.distance_enabled:
        distance = star_distance_pc(row)
        if distance is None:
            return "missing_distance"
        if _outside_range(distance, active.distance_min_pc, active.distance_max_pc):
            return "outside_distance"
    if active.luminosity_enabled:
        luminosity = star_luminosity_solar(row)
        if luminosity is None:
            return "missing_luminosity"
        if _outside_range(luminosity, active.luminosity_min_solar, active.luminosity_max_solar):
            return "outside_luminosity"
    if active.stellar_class_enabled:
        if active.spectral_types:
            spectral_type = star_spectral_type(row)
            if spectral_type is None:
                return "missing_stellar_class"
            if spectral_type not in active.spectral_types:
                return "outside_stellar_class"
        if active.luminosity_classes:
            luminosity_class = star_luminosity_class(row)
            if luminosity_class is None:
                return "missing_stellar_class"
            if luminosity_class not in active.luminosity_classes:
                return "outside_stellar_class"
    return None


def row_passes_hr_diagram_filter(row: object, diagram_filter: HrDiagramFilter | None) -> bool:
    if diagram_filter is None or not diagram_filter.is_active:
        return True
    return hr_diagram_filter_rejection(row, diagram_filter) is None


def summarize_hr_diagram_filter(rows: list[object], diagram_filter: HrDiagramFilter) -> HrDiagramFilterSummary:
    active = diagram_filter.normalized()
    removed = {reason: 0 for reason in _REMOVAL_ORDER}
    kept_spectral: Counter[str] = Counter()
    kept_luminosity: Counter[str] = Counter()
    kept = 0
    for row in rows:
        reason = hr_diagram_filter_rejection(row, active)
        if reason is None:
            kept += 1
            if active.stellar_class_enabled and active.spectral_types:
                spectral_type = star_spectral_type(row)
                if spectral_type is not None:
                    kept_spectral[spectral_type] += 1
            if active.stellar_class_enabled and active.luminosity_classes:
                luminosity_class = star_luminosity_class(row)
                if luminosity_class is not None:
                    kept_luminosity[luminosity_class] += 1
            continue
        removed[reason] = removed.get(reason, 0) + 1
    return HrDiagramFilterSummary(
        diagram_filter=active,
        considered=len(rows),
        kept=kept,
        removed=removed,
        kept_spectral_types=dict(kept_spectral),
        kept_luminosity_classes=dict(kept_luminosity),
    )


def _stars(count: int) -> str:
    return "1 star" if count == 1 else f"{count} stars"


def _format_temperature(value: float) -> str:
    return f"{int(round(value)):,} K"


def _format_color(value: float) -> str:
    return f"{value:.2f}"


def _format_distance(value: float) -> str:
    if value >= 100.0:
        return f"{value:.0f} pc"
    return f"{value:.1f} pc"


def _join_names(names: tuple[str, ...]) -> str:
    if not names:
        return "none"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _count_phrase(counts: dict[str, int], order: tuple[str, ...]) -> str:
    parts = [f"{name} {counts[name]}" for name in order if counts.get(name, 0)]
    return ", ".join(parts) if parts else "none"


def hr_diagram_filter_work_log_lines(
    summary: HrDiagramFilterSummary,
    *,
    plotted_count: int | None = None,
    matching_count: int | None = None,
    roi_active: bool = False,
) -> list[str]:
    active = summary.diagram_filter.normalized()
    if not active.is_active:
        return [
            "HR diagram filter is off. Color/temperature, distance, luminosity, and stellar class are not removing stars."
        ]

    lines = [
        "HR diagram filter is on. Stars missing a selected measurement are removed. A star removed by an earlier filter is not counted again.",
    ]
    unused = []
    if not active.color_enabled:
        unused.append("color/temperature")
    if not active.distance_enabled:
        unused.append("distance")
    if not active.luminosity_enabled:
        unused.append("luminosity")
    if not active.stellar_class_enabled:
        unused.append("stellar class")
    if unused:
        lines.append("Not used: " + ", ".join(unused) + ".")

    if summary.considered == 0:
        lines.extend(_criterion_lines(active, summary, include_counts=False))
        lines.append("No measured HR stars are loaded, so the filter has not removed anyone yet.")
        return lines

    field = "after the image ROI" if roi_active else "in the measured field"
    lines.append(f"Considered {_stars(summary.considered)} {field}.")
    lines.extend(_criterion_lines(active, summary, include_counts=True))
    lines.append(f"This filter keeps {summary.kept} of {summary.considered} stars.")
    if active.stellar_class_enabled and summary.kept:
        if active.spectral_types:
            lines.append(
                "Kept spectral types: "
                + _count_phrase(summary.kept_spectral_types, SPECTRAL_TYPE_ORDER)
                + "."
            )
        if active.luminosity_classes:
            lines.append(
                "Kept luminosity classes: "
                + _count_phrase(summary.kept_luminosity_classes, LUMINOSITY_CLASS_ORDER)
                + "."
            )
    if plotted_count is not None and matching_count is not None:
        plotted_noun = "star" if plotted_count == 1 else "stars"
        matching_noun = "row" if matching_count == 1 else "rows"
        lines.append(
            f"The plot is showing {plotted_count} plotted {plotted_noun} and {matching_count} matching table {matching_noun}. "
            "Hide Saturated, Require Parallax, the apparent-magnitude range, missing axis values, and Only Group can still remove stars."
        )
    return lines


def _criterion_lines(active: HrDiagramFilter, summary: HrDiagramFilterSummary, *, include_counts: bool) -> list[str]:
    lines: list[str] = []
    remaining = summary.considered
    if active.color_enabled:
        cool_color = temperature_kelvin_to_color_index(active.temperature_min_k)
        hot_color = temperature_kelvin_to_color_index(active.temperature_max_k)
        text = (
            "Color/temperature: "
            f"{_format_temperature(active.temperature_min_k)} to {_format_temperature(active.temperature_max_k)} "
            f"(Gaia BP-RP {_format_color(cool_color)} to {_format_color(hot_color)}; hotter is the smaller BP-RP). "
            "Temperature uses the same Gaia BP-RP scale as the plot."
        )
        if include_counts:
            missing = summary.removed["missing_color"]
            outside = summary.removed["outside_color"]
            remaining -= missing + outside
            text += (
                f" Removed {_stars(missing)} with no Gaia BP-RP, so color and temperature were unavailable."
                f" Removed {_stars(outside)} outside that range."
                f" {_stars(remaining)} remained."
            )
        lines.append(text)
    if active.distance_enabled:
        text = (
            "Distance: "
            f"{_format_distance(active.distance_min_pc)} to {_format_distance(active.distance_max_pc)}. "
            "Distance in parsecs is 1000 divided by Gaia parallax in mas. "
            "Missing or non-positive parallax counts as unavailable."
        )
        if include_counts:
            missing = summary.removed["missing_distance"]
            outside = summary.removed["outside_distance"]
            remaining -= missing + outside
            text += (
                f" Removed {_stars(missing)} with no usable Gaia parallax, so distance was unavailable."
                f" Removed {_stars(outside)} outside that range."
                f" {_stars(remaining)} remained."
            )
        lines.append(text)
    if active.luminosity_enabled:
        text = (
            "Luminosity: "
            f"{format_solar_luminosity(active.luminosity_min_solar)} to {format_solar_luminosity(active.luminosity_max_solar)} Lsun, "
            f"from Gaia absolute G with solar absolute G = {SOLAR_ABSOLUTE_G_MAGNITUDE:.2f}."
        )
        if include_counts:
            missing = summary.removed["missing_luminosity"]
            outside = summary.removed["outside_luminosity"]
            remaining -= missing + outside
            text += (
                f" Removed {_stars(missing)} with no Gaia absolute G magnitude, so luminosity was unavailable."
                f" Removed {_stars(outside)} outside that range."
                f" {_stars(remaining)} remained."
            )
        lines.append(text)
    if active.stellar_class_enabled:
        spectral_names = _ordered_names(active.spectral_types, SPECTRAL_TYPE_ORDER)
        luminosity_names = _ordered_names(active.luminosity_classes, LUMINOSITY_CLASS_ORDER)
        parts = []
        if spectral_names:
            parts.append("spectral types " + _join_names(spectral_names))
        if luminosity_names:
            parts.append("luminosity classes " + _join_names(luminosity_names))
        text = "Stellar class: " + "; ".join(parts) + "."
        if spectral_names:
            text += " Spectral type comes from the Gaia BP-RP temperature."
        if luminosity_names:
            text += (
                " Luminosity class is the nearest class guide within "
                f"{STELLAR_CLASS_MAGNITUDE_TOLERANCE:.2f} mag of absolute G at that BP-RP "
                "(Supergiants, Giants, Subgiants, Main Sequence, White Dwarfs)."
            )
        if include_counts:
            missing = summary.removed["missing_stellar_class"]
            outside = summary.removed["outside_stellar_class"]
            remaining -= missing + outside
            if spectral_names and not luminosity_names:
                missing_detail = "with no Gaia BP-RP, so spectral type was unavailable"
            else:
                missing_detail = (
                    "with no stellar class (missing Gaia BP-RP or absolute G, or farther than "
                    f"{STELLAR_CLASS_MAGNITUDE_TOLERANCE:.2f} mag from every class guide)"
                )
            text += (
                f" Removed {_stars(missing)} {missing_detail}."
                f" Removed {_stars(outside)} whose class was not selected."
                f" {_stars(remaining)} remained."
            )
        lines.append(text)
    return lines


def hr_diagram_filter_tooltip(diagram_filter: HrDiagramFilter) -> str:
    intro = (
        "Filter the HR diagram by color/temperature, distance, luminosity, and stellar class. "
        "Stars missing a selected measurement are removed."
    )
    active = diagram_filter.normalized()
    if not active.is_active:
        return intro
    details = _criterion_lines(active, HrDiagramFilterSummary(active, 0, 0, {}, {}, {}), include_counts=False)
    return intro + "\n\nActive filter:\n" + "\n".join(details)
