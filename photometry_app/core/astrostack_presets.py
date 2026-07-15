from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

ASTROSTACK_PRESET_VERSION = 2
ASTROSTACK_PRESET_KIND = "astrostack_overlay_preset"
ASTROSTACK_PRESET_FILE_FILTER = "Deep Stack Preset (*.astrostack.json);;JSON Files (*.json)"

_LAYER_X_FIELDS = ("x", "x2")
_LAYER_Y_FIELDS = ("y", "y2")
_LAYER_MIN_DIM_FIELDS = (
    "radius",
    "text_size",
    "line_width",
    "plot_title_offset_x",
    "plot_title_offset_y",
    "plot_x_label_offset_x",
    "plot_x_label_offset_y",
    "plot_y_label_offset_x",
    "plot_y_label_offset_y",
    "plot_curve_width",
    "plot_highlight_radius",
    "plot_corner_radius",
    "plot_chart_margin_left",
    "plot_chart_margin_right",
    "plot_chart_margin_top",
    "plot_chart_margin_bottom",
    "plot_title_font_size",
    "plot_label_font_size",
)


@dataclass(frozen=True, slots=True)
class AstrostackOverlayPresetState:
    version: int
    layers: tuple[dict[str, Any], ...]
    reference_size: tuple[int, int] | None = None
    crop: dict[str, Any] | None = None
    signal_region: dict[str, Any] | None = None
    background_region: dict[str, Any] | None = None


def _normalized_reference_size(reference_size: tuple[int, int] | None) -> tuple[int, int]:
    if reference_size is None:
        return 1, 1
    width = max(1, int(reference_size[0]))
    height = max(1, int(reference_size[1]))
    return width, height


def _reference_min(reference_size: tuple[int, int] | None) -> float:
    width, height = _normalized_reference_size(reference_size)
    return float(max(1, min(width, height)))


def normalize_astrostack_layer(layer: Mapping[str, Any], reference_size: tuple[int, int]) -> dict[str, Any]:
    width, height = _normalized_reference_size(reference_size)
    reference_min = _reference_min(reference_size)
    normalized = dict(layer)
    for field_name in _LAYER_X_FIELDS:
        if field_name in normalized:
            normalized[field_name] = float(normalized[field_name]) / float(width)
    for field_name in _LAYER_Y_FIELDS:
        if field_name in normalized:
            normalized[field_name] = float(normalized[field_name]) / float(height)
    for field_name in _LAYER_MIN_DIM_FIELDS:
        if field_name in normalized:
            normalized[field_name] = float(normalized[field_name]) / reference_min
    return normalized


def denormalize_astrostack_layer(
    layer: Mapping[str, Any],
    reference_size: tuple[int, int],
    target_size: tuple[int, int],
) -> dict[str, Any]:
    source_width, source_height = _normalized_reference_size(reference_size)
    target_width, target_height = _normalized_reference_size(target_size)
    source_min = _reference_min(reference_size)
    target_min = _reference_min(target_size)
    denormalized = dict(layer)
    for field_name in _LAYER_X_FIELDS:
        if field_name in denormalized:
            denormalized[field_name] = float(denormalized[field_name]) * float(target_width)
    for field_name in _LAYER_Y_FIELDS:
        if field_name in denormalized:
            denormalized[field_name] = float(denormalized[field_name]) * float(target_height)
    for field_name in _LAYER_MIN_DIM_FIELDS:
        if field_name in denormalized:
            denormalized[field_name] = float(denormalized[field_name]) * target_min
    return denormalized


def normalize_astrostack_crop(
    crop: Mapping[str, Any] | None,
    reference_size: tuple[int, int],
) -> dict[str, Any] | None:
    if crop is None:
        return None
    width, height = _normalized_reference_size(reference_size)
    return {
        "shape": str(crop.get("shape", "rectangle")),
        "mode": str(crop.get("mode", "include")),
        "x0": float(crop["x0"]) / float(width),
        "y0": float(crop["y0"]) / float(height),
        "x1": float(crop["x1"]) / float(width),
        "y1": float(crop["y1"]) / float(height),
    }


def denormalize_astrostack_crop(
    crop: Mapping[str, Any] | None,
    reference_size: tuple[int, int],
    target_size: tuple[int, int],
) -> dict[str, Any] | None:
    if crop is None:
        return None
    target_width, target_height = _normalized_reference_size(target_size)
    return {
        "shape": str(crop.get("shape", "rectangle")),
        "mode": str(crop.get("mode", "include")),
        "x0": float(crop["x0"]) * float(target_width),
        "y0": float(crop["y0"]) * float(target_height),
        "x1": float(crop["x1"]) * float(target_width),
        "y1": float(crop["y1"]) * float(target_height),
    }


def serialize_astrostack_overlay_preset(
    layers: Sequence[Any],
    *,
    reference_size: tuple[int, int] | None,
    crop: Mapping[str, Any] | None = None,
    signal_region: Mapping[str, Any] | None = None,
    background_region: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if reference_size is None:
        raise ValueError("Astrostack presets require a reference image size.")
    normalized_layers = [normalize_astrostack_layer(asdict(layer), reference_size) for layer in layers]
    payload: dict[str, Any] = {
        "version": ASTROSTACK_PRESET_VERSION,
        "kind": ASTROSTACK_PRESET_KIND,
        "reference_size": {
            "width": max(1, int(reference_size[0])),
            "height": max(1, int(reference_size[1])),
        },
        "layers": normalized_layers,
    }
    normalized_crop = normalize_astrostack_crop(crop, reference_size)
    if normalized_crop is not None:
        payload["crop"] = normalized_crop
    normalized_signal_region = normalize_astrostack_crop(signal_region, reference_size)
    if normalized_signal_region is not None:
        payload["signal_region"] = normalized_signal_region
    normalized_background_region = normalize_astrostack_crop(background_region, reference_size)
    if normalized_background_region is not None:
        payload["background_region"] = normalized_background_region
    return payload


def write_astrostack_overlay_preset(
    path: Path,
    layers: Sequence[Any],
    *,
    reference_size: tuple[int, int] | None,
    crop: Mapping[str, Any] | None = None,
    signal_region: Mapping[str, Any] | None = None,
    background_region: Mapping[str, Any] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            serialize_astrostack_overlay_preset(
                layers,
                reference_size=reference_size,
                crop=crop,
                signal_region=signal_region,
                background_region=background_region,
            ),
            indent=2,
        ),
        encoding="utf-8",
    )


def _parse_reference_size(payload: Mapping[str, Any]) -> tuple[int, int] | None:
    raw_reference_size = payload.get("reference_size")
    if not isinstance(raw_reference_size, Mapping):
        return None
    try:
        width = max(1, int(raw_reference_size["width"]))
        height = max(1, int(raw_reference_size["height"]))
    except (KeyError, TypeError, ValueError):
        return None
    return width, height


def read_astrostack_overlay_preset(path: Path) -> AstrostackOverlayPresetState:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read preset file: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Preset file must contain a JSON object.")
    if payload.get("kind") != ASTROSTACK_PRESET_KIND:
        raise ValueError("File is not an Astrostack overlay preset.")
    raw_layers = payload.get("layers")
    if not isinstance(raw_layers, list):
        raise ValueError("Preset file is missing a layers list.")
    layers: list[dict[str, Any]] = []
    for index, item in enumerate(raw_layers, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Layer {index} is not a valid object.")
        layers.append(dict(item))
    version = int(payload.get("version", 1))
    reference_size = _parse_reference_size(payload)
    crop_payload = payload.get("crop")
    crop = dict(crop_payload) if isinstance(crop_payload, dict) else None
    signal_payload = payload.get("signal_region")
    signal_region = dict(signal_payload) if isinstance(signal_payload, dict) else None
    background_payload = payload.get("background_region")
    background_region = dict(background_payload) if isinstance(background_payload, dict) else None
    return AstrostackOverlayPresetState(
        version=version,
        layers=tuple(layers),
        reference_size=reference_size,
        crop=crop,
        signal_region=signal_region,
        background_region=background_region,
    )


def materialize_astrostack_preset_layers(
    preset: AstrostackOverlayPresetState,
    target_size: tuple[int, int],
) -> list[dict[str, Any]]:
    if preset.version < 2 or preset.reference_size is None:
        return [dict(layer) for layer in preset.layers]
    return [
        denormalize_astrostack_layer(layer, preset.reference_size, target_size)
        for layer in preset.layers
    ]


def materialize_astrostack_preset_crop(
    preset: AstrostackOverlayPresetState,
    target_size: tuple[int, int],
) -> dict[str, Any] | None:
    if preset.crop is None:
        return None
    if preset.version < 2 or preset.reference_size is None:
        return dict(preset.crop)
    return denormalize_astrostack_crop(preset.crop, preset.reference_size, target_size)


def materialize_astrostack_preset_region(
    region: Mapping[str, Any] | None,
    *,
    preset: AstrostackOverlayPresetState,
    target_size: tuple[int, int],
) -> dict[str, Any] | None:
    if region is None:
        return None
    if preset.version < 2 or preset.reference_size is None:
        return dict(region)
    return denormalize_astrostack_crop(region, preset.reference_size, target_size)
