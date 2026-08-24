from __future__ import annotations

from dataclasses import dataclass


ASTEROID_VISUAL_MARKER_STYLES = ("target", "circle", "brackets", "crosshair", "pointer")
ASTEROID_VISUAL_MARKER_STYLE_LABELS = {
    "target": "Square aim",
    "circle": "Circle",
    "brackets": "Corner brackets",
    "crosshair": "Open crosshair",
    "pointer": "Pointer",
}
TARGET_FIELD_MARKER_NONE = "none"
DEFAULT_TARGET_FIELD_MARKER_STYLE = TARGET_FIELD_MARKER_NONE
TARGET_FIELD_MARKER_STYLES = (TARGET_FIELD_MARKER_NONE,) + ASTEROID_VISUAL_MARKER_STYLES
TARGET_FIELD_MARKER_STYLE_LABELS = {
    TARGET_FIELD_MARKER_NONE: "None",
    **ASTEROID_VISUAL_MARKER_STYLE_LABELS,
}

_ASTEROID_MARKER_ALIASES = {
    "square": "target",
    "aim": "target",
    "square-aim": "target",
    "square_aim": "target",
    "diamond": "target",
    "open_cross": "crosshair",
    "open-cross": "crosshair",
    "open_crosshair": "crosshair",
    "corner": "brackets",
    "corner_brackets": "brackets",
    "corner-brackets": "brackets",
    "open_pointer": "pointer",
    "open-pointer": "pointer",
}


@dataclass(frozen=True, slots=True)
class TargetMarkerAppearance:
    line_color: str = "#ef4444"
    outline_color: str = "#ffffff"
    line_width: float = 2.0
    length_percent: float = 36.0
    gap_percent: float = 20.0


def coerce_asteroid_visual_marker_style(value: object) -> str:
    key = str(value or "target").strip().lower()
    key = _ASTEROID_MARKER_ALIASES.get(key, key)
    return key if key in ASTEROID_VISUAL_MARKER_STYLES else "target"


def coerce_target_field_marker_style(value: object) -> str:
    key = str(value or TARGET_FIELD_MARKER_NONE).strip().lower()
    if key in {"", TARGET_FIELD_MARKER_NONE, "off", "false", "0"}:
        return TARGET_FIELD_MARKER_NONE
    aliased = _ASTEROID_MARKER_ALIASES.get(key, key)
    return aliased if aliased in ASTEROID_VISUAL_MARKER_STYLES else TARGET_FIELD_MARKER_NONE


def pointer_marker_segments(
    center_x: float,
    center_y: float,
    *,
    left: float,
    top: float,
    gap: float,
) -> tuple[tuple[tuple[float, float], tuple[float, float]], ...]:
    """Left and top arms that stop `gap` pixels before the target."""
    inner_x = float(center_x) - float(gap)
    inner_y = float(center_y) - float(gap)
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    if inner_x > float(left) + 1.0:
        segments.append(((float(left), float(center_y)), (inner_x, float(center_y))))
    if inner_y > float(top) + 1.0:
        segments.append(((float(center_x), float(top)), (float(center_x), inner_y)))
    return tuple(segments)
