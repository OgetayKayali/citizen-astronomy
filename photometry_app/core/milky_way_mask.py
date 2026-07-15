from __future__ import annotations

from dataclasses import dataclass

import numpy as np


MILKY_WAY_ALPHA_MASK_VERSION = "milky_way_alpha_mask_v1"


@dataclass(frozen=True, slots=True)
class MilkyWayMaskParameters:
    black_point: float = 0.02
    white_point: float = 0.095
    blur_radius_fraction: float = 0.006
    min_blur_radius_px: int = 2
    max_blur_radius_px: int = 96
    blur_passes: int = 3
    alpha_gamma: float = 0.85
    empty_alpha_threshold: float = 1.0 / 255.0
    empty_coverage_threshold: float = 0.0002


@dataclass(frozen=True, slots=True)
class MilkyWayTileSignalStats:
    has_signal: bool
    alpha_coverage: float
    mean_alpha: float
    max_alpha: float


DEFAULT_MILKY_WAY_MASK_PARAMETERS = MilkyWayMaskParameters()


def milky_way_alpha_mask_from_rgb(
    rgb_pixels: np.ndarray,
    parameters: MilkyWayMaskParameters | None = None,
) -> np.ndarray:
    params = parameters or DEFAULT_MILKY_WAY_MASK_PARAMETERS
    rgb = np.asarray(rgb_pixels)
    if rgb.ndim != 3 or rgb.shape[2] < 3:
        raise ValueError(f"Expected an RGB/RGBA image array, got shape {rgb.shape!r}")

    rgb_float = rgb[..., :3].astype(np.float32, copy=False)
    if np.issubdtype(rgb.dtype, np.integer):
        max_value = float(np.iinfo(rgb.dtype).max)
        rgb_float = rgb_float / np.float32(max(1.0, max_value))
    else:
        rgb_float = np.clip(rgb_float, np.float32(0.0), np.float32(1.0))

    luminance = (
        rgb_float[..., 0] * np.float32(0.2126)
        + rgb_float[..., 1] * np.float32(0.7152)
        + rgb_float[..., 2] * np.float32(0.0722)
    )
    broad_luminance = _box_blur_luminance(luminance, _resolved_blur_radius_px(luminance.shape, params), params.blur_passes)
    alpha = _smoothstep(float(params.black_point), float(params.white_point), broad_luminance)
    alpha = np.power(np.clip(alpha, np.float32(0.0), np.float32(1.0)), np.float32(max(0.05, float(params.alpha_gamma))))
    return np.clip(alpha * np.float32(255.0), np.float32(0.0), np.float32(255.0)).astype(np.uint8)


def apply_milky_way_alpha_mask(
    pixels: np.ndarray,
    parameters: MilkyWayMaskParameters | None = None,
    *,
    preserve_existing_alpha: bool = True,
) -> np.ndarray:
    image = np.asarray(pixels)
    if image.ndim != 3 or image.shape[2] < 3:
        raise ValueError(f"Expected an RGB/RGBA image array, got shape {image.shape!r}")
    rgb = image[..., :3].astype(np.uint8, copy=False)
    if preserve_existing_alpha and image.shape[2] >= 4:
        existing_alpha = image[..., 3].astype(np.uint8, copy=False)
        if np.any(existing_alpha < np.uint8(255)):
            return np.dstack((rgb, existing_alpha)).astype(np.uint8, copy=False)
    alpha = milky_way_alpha_mask_from_rgb(rgb, parameters)
    return np.dstack((rgb, alpha)).astype(np.uint8, copy=False)


def milky_way_alpha_tile_stats(
    alpha_pixels: np.ndarray,
    parameters: MilkyWayMaskParameters | None = None,
) -> MilkyWayTileSignalStats:
    params = parameters or DEFAULT_MILKY_WAY_MASK_PARAMETERS
    alpha = np.asarray(alpha_pixels, dtype=np.float32)
    if alpha.size <= 0:
        return MilkyWayTileSignalStats(False, 0.0, 0.0, 0.0)
    if alpha.max(initial=0.0) > 1.0:
        alpha = alpha / np.float32(255.0)
    alpha = np.clip(alpha, np.float32(0.0), np.float32(1.0))
    threshold = np.float32(max(0.0, min(1.0, float(params.empty_alpha_threshold))))
    coverage = float(np.count_nonzero(alpha > threshold)) / float(alpha.size)
    mean_alpha = float(np.mean(alpha))
    max_alpha = float(np.max(alpha))
    has_signal = max_alpha > float(threshold) and coverage >= max(0.0, float(params.empty_coverage_threshold))
    return MilkyWayTileSignalStats(
        has_signal=bool(has_signal),
        alpha_coverage=coverage,
        mean_alpha=mean_alpha,
        max_alpha=max_alpha,
    )


def milky_way_mask_manifest_payload(parameters: MilkyWayMaskParameters | None = None) -> dict[str, object]:
    params = parameters or DEFAULT_MILKY_WAY_MASK_PARAMETERS
    return {
        "version": 1,
        "algorithm": MILKY_WAY_ALPHA_MASK_VERSION,
        "black_point": float(params.black_point),
        "white_point": float(params.white_point),
        "blur_radius_fraction": float(params.blur_radius_fraction),
        "min_blur_radius_px": int(params.min_blur_radius_px),
        "max_blur_radius_px": int(params.max_blur_radius_px),
        "blur_passes": int(params.blur_passes),
        "alpha_gamma": float(params.alpha_gamma),
        "empty_alpha_threshold": float(params.empty_alpha_threshold),
        "empty_coverage_threshold": float(params.empty_coverage_threshold),
    }


def _resolved_blur_radius_px(shape: tuple[int, ...], parameters: MilkyWayMaskParameters) -> int:
    height = int(shape[0]) if shape else 0
    width = int(shape[1]) if len(shape) > 1 else 0
    image_span = max(width, height, 1)
    radius = int(round(float(image_span) * max(0.0, float(parameters.blur_radius_fraction))))
    radius = max(int(parameters.min_blur_radius_px), radius)
    return max(0, min(int(parameters.max_blur_radius_px), radius))


def _box_blur_luminance(values: np.ndarray, radius: int, passes: int) -> np.ndarray:
    result = np.asarray(values, dtype=np.float32)
    resolved_radius = max(0, int(radius))
    if resolved_radius <= 0:
        return result
    for _pass_index in range(max(1, int(passes))):
        result = _box_blur_axis(_box_blur_axis(result, resolved_radius, axis=1), resolved_radius, axis=0)
    return result


def _box_blur_axis(values: np.ndarray, radius: int, *, axis: int) -> np.ndarray:
    resolved_radius = max(0, int(radius))
    if resolved_radius <= 0:
        return values.astype(np.float32, copy=False)
    pad_width = [(0, 0)] * values.ndim
    pad_width[int(axis)] = (resolved_radius, resolved_radius)
    padded = np.pad(values.astype(np.float32, copy=False), pad_width, mode="edge")
    cumulative = np.cumsum(padded, axis=axis, dtype=np.float32)
    zero_shape = list(cumulative.shape)
    zero_shape[int(axis)] = 1
    cumulative = np.concatenate((np.zeros(zero_shape, dtype=np.float32), cumulative), axis=axis)
    window = resolved_radius * 2 + 1
    high_slice = [slice(None)] * cumulative.ndim
    low_slice = [slice(None)] * cumulative.ndim
    high_slice[int(axis)] = slice(window, None)
    low_slice[int(axis)] = slice(None, -window)
    return (cumulative[tuple(high_slice)] - cumulative[tuple(low_slice)]) / np.float32(window)


def _smoothstep(edge0: float, edge1: float, values: np.ndarray) -> np.ndarray:
    lower = float(edge0)
    upper = max(lower + 1.0e-6, float(edge1))
    t = np.clip((np.asarray(values, dtype=np.float32) - np.float32(lower)) / np.float32(upper - lower), np.float32(0.0), np.float32(1.0))
    return t * t * (np.float32(3.0) - np.float32(2.0) * t)