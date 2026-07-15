from __future__ import annotations

import numpy as np


def clip_roi_bounds(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    left = int(min(x0, x1))
    right = int(max(x0, x1))
    top = int(min(y0, y1))
    bottom = int(max(y0, y1))
    left = max(0, min(width - 1, left))
    right = max(left + 1, min(width, right))
    top = max(0, min(height - 1, top))
    bottom = max(top + 1, min(height, bottom))
    return left, top, right, bottom


def extract_region_pixels(array: np.ndarray, x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    data = np.asarray(array, dtype=np.float64)
    if data.size == 0:
        return np.asarray([], dtype=np.float64)
    if data.ndim == 3:
        data = np.mean(data, axis=2)
    height, width = data.shape
    left, top, right, bottom = clip_roi_bounds(x0, y0, x1, y1, width=width, height=height)
    return np.asarray(data[top:bottom, left:right], dtype=np.float64)


def estimate_global_signal_noise(array: np.ndarray) -> tuple[float, float]:
    data = np.asarray(array, dtype=np.float64)
    if data.size == 0:
        return 0.0, 1e-6
    if data.ndim == 3:
        data = np.mean(data, axis=2)
    flat = data.reshape(-1)
    median = float(np.median(flat))
    mad = float(np.median(np.abs(flat - median)))
    sigma = 1.4826 * mad if mad > 0.0 else float(np.std(flat))
    if sigma <= 0.0:
        sigma = 1e-6
    signal = float(np.percentile(flat, 99.5) - median)
    return max(0.0, signal), max(1e-6, sigma)


def estimate_region_signal_noise(
    array: np.ndarray,
    signal_bounds: tuple[float, float, float, float],
    background_bounds: tuple[float, float, float, float],
) -> tuple[float, float]:
    signal_pixels = extract_region_pixels(array, *signal_bounds)
    background_pixels = extract_region_pixels(array, *background_bounds)
    if signal_pixels.size == 0 or background_pixels.size == 0:
        return estimate_global_signal_noise(array)
    background_flat = background_pixels.reshape(-1)
    signal_flat = signal_pixels.reshape(-1)
    background_median = float(np.median(background_flat))
    mad = float(np.median(np.abs(background_flat - background_median)))
    sigma = 1.4826 * mad if mad > 0.0 else float(np.std(background_flat))
    if sigma <= 0.0:
        sigma = 1e-6
    signal = float(np.mean(signal_flat) - background_median)
    return max(0.0, signal), max(1e-6, sigma)


def shift_roi_bounds(
    bounds: tuple[float, float, float, float],
    offset_x: float,
    offset_y: float,
) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = bounds
    return (
        float(x0) + float(offset_x),
        float(y0) + float(offset_y),
        float(x1) + float(offset_x),
        float(y1) + float(offset_y),
    )


def roi_bounds_to_cropped_data_space(
    bounds: tuple[float, float, float, float],
    crop_origin: tuple[float, float],
) -> tuple[float, float, float, float]:
    crop_x0, crop_y0 = crop_origin
    return shift_roi_bounds(bounds, -crop_x0, -crop_y0)


def format_astrostack_metric_value(metric_kind: str, value: float) -> str:
    del metric_kind
    numeric = float(value)
    if not np.isfinite(numeric):
        numeric = 0.0
    return f"{max(0.0, numeric):.3e}"
