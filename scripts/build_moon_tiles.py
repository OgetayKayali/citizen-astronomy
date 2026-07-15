from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import shutil
import statistics
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen

from photometry_app.ui.moon_system import MoonCache, MoonTileKey, MoonTiledTextureManager


MANIFEST_FILENAME = "build_manifest.json"
SUPPORTED_TILE_FORMATS = {"png", "jpg", "jpeg", "tif", "tiff", "webp"}
SYNTHETIC_SOURCE_DIRNAME = "source"
CANONICAL_MOON_CONVENTION_ID = "moon_shader_equirectangular_v1"
CANONICAL_MOON_CONVENTION = {
    "id": CANONICAL_MOON_CONVENTION_ID,
    "u_direction": "increasing_east_longitude",
    "v_direction": "north_to_south",
    "longitude_seam_convention": "u=0/1 at -180/+180 degrees; u=0.5 at 0 degrees",
    "center_longitude_deg": 0.0,
    "north_pole_at_v": 0.0,
    "south_pole_at_v": 1.0,
}
POLAR_CAP_PROJECTION = "azimuthal_equidistant"
LUNAR_LANDMARK_UVS: tuple[dict[str, object], ...] = (
    {"name": "Tycho", "longitude_deg": -11.36, "latitude_deg": -43.31},
    {"name": "Copernicus", "longitude_deg": -20.08, "latitude_deg": 9.62},
    {"name": "Mare Crisium", "longitude_deg": 59.10, "latitude_deg": 17.00},
    {"name": "Mare Imbrium", "longitude_deg": -15.00, "latitude_deg": 32.80},
    {"name": "Plato", "longitude_deg": -9.30, "latitude_deg": 51.60},
)


def _sha256_file(path: Path | None) -> str | None:

    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_transform_manifest(
    *,
    flip_u: bool,
    flip_v: bool,
    longitude_shift_pixels: int,
    longitude_shift_degrees: float,
    rotate_180: bool,
    source_crop: tuple[int, int, int, int] | None,
    source_resize: tuple[int, int] | None,
    resolved_shift_pixels: int,
    transformed_width: int,
) -> dict[str, object]:

    total_shift_degrees = float(longitude_shift_degrees) + (
        360.0 * float(longitude_shift_pixels) / max(1.0, float(transformed_width))
    )
    return {
        "flip_u": bool(flip_u),
        "flip_v": bool(flip_v),
        "rotate_180": bool(rotate_180),
        "longitude_shift_pixels_requested": int(longitude_shift_pixels),
        "longitude_shift_degrees_requested": float(longitude_shift_degrees),
        "longitude_shift_pixels_applied": int(resolved_shift_pixels),
        "longitude_shift_degrees_applied": float(total_shift_degrees),
        "source_crop": list(source_crop) if source_crop is not None else None,
        "source_resize": list(source_resize) if source_resize is not None else None,
    }


def _transform_source_image(
    source_image: QImage,
    *,
    flip_u: bool = False,
    flip_v: bool = False,
    longitude_shift_pixels: int = 0,
    longitude_shift_degrees: float = 0.0,
    rotate_180: bool = False,
    source_crop: tuple[int, int, int, int] | None = None,
    source_resize: tuple[int, int] | None = None,
) -> tuple[QImage, dict[str, object]]:

    if source_image.isNull():
        raise ValueError("Cannot transform a null Moon source image.")
    transformed = QImage(source_image)
    if source_crop is not None:
        crop_x, crop_y, crop_width, crop_height = source_crop
        if crop_width <= 0 or crop_height <= 0:
            raise ValueError("Source crop width and height must be positive.")
        transformed = transformed.copy(int(crop_x), int(crop_y), int(crop_width), int(crop_height))
        if transformed.isNull():
            raise ValueError("Source crop does not overlap the Moon source image.")
    if source_resize is not None:
        resize_width, resize_height = source_resize
        if resize_width <= 0 or resize_height <= 0:
            raise ValueError("Source resize width and height must be positive.")
        transformed = _scaled_level_image(transformed, int(resize_width), int(resize_height))
    resolved_flip_u = bool(flip_u)
    resolved_flip_v = bool(flip_v)
    if rotate_180:
        resolved_flip_u = not resolved_flip_u
        resolved_flip_v = not resolved_flip_v
    if resolved_flip_u or resolved_flip_v:
        orientation = Qt.Orientation.Horizontal if resolved_flip_u else Qt.Orientation.Vertical
        if resolved_flip_u and resolved_flip_v:
            orientation = Qt.Orientation.Horizontal | Qt.Orientation.Vertical
        transformed = transformed.flipped(orientation)
    width = max(1, int(transformed.width()))
    pixel_shift_from_degrees = int(round(float(longitude_shift_degrees) * width / 360.0))
    resolved_shift_pixels = (int(longitude_shift_pixels) + pixel_shift_from_degrees) % width
    if resolved_shift_pixels:
        rolled = QImage(transformed.size(), transformed.format())
        rolled.fill(Qt.GlobalColor.transparent)
        painter = QPainter(rolled)
        try:
            painter.drawImage(resolved_shift_pixels, 0, transformed)
            painter.drawImage(resolved_shift_pixels - width, 0, transformed)
        finally:
            painter.end()
        transformed = rolled
    metadata = _source_transform_manifest(
        flip_u=flip_u,
        flip_v=flip_v,
        longitude_shift_pixels=longitude_shift_pixels,
        longitude_shift_degrees=longitude_shift_degrees,
        rotate_180=rotate_180,
        source_crop=source_crop,
        source_resize=source_resize,
        resolved_shift_pixels=resolved_shift_pixels,
        transformed_width=width,
    )
    return transformed, metadata


def _landmark_uv(longitude_deg: float, latitude_deg: float) -> tuple[float, float]:

    return (0.5 + float(longitude_deg) / 360.0) % 1.0, max(0.0, min(1.0, 0.5 - float(latitude_deg) / 180.0))


def _uv_layout_summary(max_level: int) -> dict[str, object]:

    level_summary: dict[str, dict[str, int]] = {}
    for level in range(max(0, int(max_level)) + 1):
        columns, rows = MoonTiledTextureManager.tile_grid_dimensions(level)
        level_summary[f"L{level}"] = {"columns": int(columns), "rows": int(rows)}
    return {
        "u_formula": "fract(0.5 + longitude / 2pi)",
        "v_formula": "clamp(0.5 - latitude / pi, 0, 1)",
        "north_toward_smaller_v": True,
        "levels": level_summary,
    }


def default_moon_tile_paths(project_root: Path) -> dict[str, Path]:

    resolved_root = Path(project_root).resolve()
    return {
        "project_root": resolved_root,
        "albedo_source": resolved_root / MoonCache.DEFAULT_COLOR_TEXTURE,
        "displacement_source": resolved_root / MoonCache.DEFAULT_DISPLACEMENT_TEXTURE,
        "output_dir": resolved_root / "assets" / "moon_tiles",
    }


def _resolve_path(project_root: Path, path_value: str | Path | None, default_path: Path | None = None) -> Path | None:

    if path_value is None:
        return Path(default_path).resolve() if default_path is not None else None
    candidate = Path(path_value)
    if not candidate.is_absolute():
        candidate = Path(project_root) / candidate
    return candidate.resolve()


def _load_image(path: Path, *, label: str) -> QImage:

    image = QImage(str(path))
    if image.isNull():
        raise ValueError(f"Unable to read {label} image: {path}")
    return image


def _max_supported_level(width: int, height: int, tile_size: int) -> int:

    if tile_size <= 0:
        raise ValueError("Tile size must be positive.")
    capacity = min(float(width) / float(tile_size * 2), float(height) / float(tile_size))
    if capacity < 1.0:
        raise ValueError(
            f"Source dimensions {width}x{height} are too small for tile size {tile_size}; "
            "need at least 2*tile_size by tile_size pixels."
        )
    return int(math.floor(math.log2(capacity)))


def _scaled_level_image(source_image: QImage, width: int, height: int) -> QImage:

    return source_image.scaled(
        int(width),
        int(height),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _clear_existing_output(output_dir: Path) -> None:

    for child_name in ("albedo", "normal", "polar_caps"):
        child_path = output_dir / child_name
        if child_path.exists():
            shutil.rmtree(child_path)
    manifest_path = output_dir / MANIFEST_FILENAME
    if manifest_path.exists():
        manifest_path.unlink()


def _save_tile_image(image: QImage, output_path: Path) -> None:

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(output_path)):
        raise ValueError(f"Failed to write tile image: {output_path}")


def _image_rgb_array(image: QImage) -> np.ndarray:

    converted = image.convertToFormat(QImage.Format.Format_RGB888)
    width = max(1, int(converted.width()))
    height = max(1, int(converted.height()))
    bytes_per_line = int(converted.bytesPerLine())
    values = np.frombuffer(converted.bits(), dtype=np.uint8, count=bytes_per_line * height).reshape((height, bytes_per_line))
    return values[:, : width * 3].reshape((height, width, 3)).copy()


def _rgb_array_image(values: np.ndarray) -> QImage:

    resolved = np.ascontiguousarray(np.clip(values, 0, 255).astype(np.uint8))
    height, width, _channels = resolved.shape
    return QImage(resolved.data, int(width), int(height), int(width) * 3, QImage.Format.Format_RGB888).copy()


def _polar_cap_source_uv(
    texture_size: int,
    pole: str,
    latitude_coverage_deg: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:

    resolved_size = max(2, int(texture_size))
    resolved_pole = str(pole).strip().casefold()
    if resolved_pole not in {"north", "south"}:
        raise ValueError("Polar cap pole must be 'north' or 'south'.")
    coverage = max(0.0, min(89.999, float(latitude_coverage_deg)))
    y_indices, x_indices = np.mgrid[0:resolved_size, 0:resolved_size]
    cap_x = ((x_indices.astype(np.float32) + 0.5) / float(resolved_size)) * 2.0 - 1.0
    cap_y = ((y_indices.astype(np.float32) + 0.5) / float(resolved_size)) * 2.0 - 1.0
    radius = np.sqrt(cap_x * cap_x + cap_y * cap_y)
    sampled_radius = np.clip(radius, 0.0, 1.0)
    longitude = np.arctan2(cap_x, -cap_y)
    longitude = np.where(radius <= 1.0e-8, 0.0, longitude)
    angular_distance = np.radians(90.0 - coverage) * sampled_radius
    latitude = np.pi * 0.5 - angular_distance
    if resolved_pole == "south":
        latitude = -latitude
    source_u = np.mod(0.5 + longitude / (2.0 * np.pi), 1.0)
    source_v = np.clip(0.5 - latitude / np.pi, 0.0, 1.0)
    return source_u, source_v, radius <= 1.0


def _sample_equirectangular_rgb(image: QImage, source_u: np.ndarray, source_v: np.ndarray) -> np.ndarray:

    source = _image_rgb_array(image).astype(np.float32)
    height, width, _channels = source.shape
    sample_x = np.mod(source_u * float(width) - 0.5, float(width))
    sample_y = np.clip(source_v * float(height) - 0.5, 0.0, float(height - 1))
    x0 = np.floor(sample_x).astype(np.int32)
    y0 = np.floor(sample_y).astype(np.int32)
    x1 = (x0 + 1) % width
    y1 = np.minimum(y0 + 1, height - 1)
    fx = (sample_x - x0).astype(np.float32)[..., None]
    fy = (sample_y - y0).astype(np.float32)[..., None]
    top = source[y0, x0] * (1.0 - fx) + source[y0, x1] * fx
    bottom = source[y1, x0] * (1.0 - fx) + source[y1, x1] * fx
    return top * (1.0 - fy) + bottom * fy


def _sample_planar_rgb(image: QImage, source_u: np.ndarray, source_v: np.ndarray) -> np.ndarray:

    source = _image_rgb_array(image).astype(np.float32)
    height, width, _channels = source.shape
    sample_x = np.clip(source_u * float(width) - 0.5, 0.0, float(width - 1))
    sample_y = np.clip(source_v * float(height) - 0.5, 0.0, float(height - 1))
    x0 = np.floor(sample_x).astype(np.int32)
    y0 = np.floor(sample_y).astype(np.int32)
    x1 = np.minimum(x0 + 1, width - 1)
    y1 = np.minimum(y0 + 1, height - 1)
    fx = (sample_x - x0).astype(np.float32)[..., None]
    fy = (sample_y - y0).astype(np.float32)[..., None]
    top = source[y0, x0] * (1.0 - fx) + source[y0, x1] * fx
    bottom = source[y1, x0] * (1.0 - fx) + source[y1, x1] * fx
    return top * (1.0 - fy) + bottom * fy


def _project_polar_cap_image(
    source_image: QImage,
    *,
    pole: str,
    texture_size: int,
    latitude_coverage_deg: float,
) -> QImage:

    source_u, source_v, _inside_disc = _polar_cap_source_uv(texture_size, pole, latitude_coverage_deg)
    return _rgb_array_image(_sample_equirectangular_rgb(source_image, source_u, source_v))


def _smootherstep_array(values: np.ndarray) -> np.ndarray:

    resolved = np.clip(values, 0.0, 1.0)
    return resolved * resolved * resolved * (resolved * (resolved * 6.0 - 15.0) + 10.0)


def _luminance_array(rgb: np.ndarray) -> np.ndarray:

    resolved = np.asarray(rgb, dtype=np.float32)
    return (
        resolved[..., 0] * 0.2126
        + resolved[..., 1] * 0.7152
        + resolved[..., 2] * 0.0722
    )


def _gaussian_blur_scalar_array(values: np.ndarray, *, passes: int = 1) -> np.ndarray:

    result = np.asarray(values, dtype=np.float32)
    for _ in range(max(0, int(passes))):
        padded = np.pad(result, ((1, 1), (1, 1)), mode="edge")
        result = (
            padded[:-2, :-2]
            + padded[:-2, 1:-1] * 2.0
            + padded[:-2, 2:]
            + padded[1:-1, :-2] * 2.0
            + padded[1:-1, 1:-1] * 4.0
            + padded[1:-1, 2:] * 2.0
            + padded[2:, :-2]
            + padded[2:, 1:-1] * 2.0
            + padded[2:, 2:]
        ) / 16.0
    return result


def _gaussian_blur_rgb_array(values: np.ndarray, *, passes: int = 1) -> np.ndarray:

    channels = [
        _gaussian_blur_scalar_array(np.asarray(values, dtype=np.float32)[..., channel], passes=passes)
        for channel in range(3)
    ]
    return np.stack(channels, axis=-1)


def _masked_mean(values: np.ndarray, mask: np.ndarray) -> float:

    resolved_mask = np.asarray(mask, dtype=bool)
    if not np.any(resolved_mask):
        return 0.0
    return float(np.mean(np.asarray(values, dtype=np.float32)[resolved_mask]))


def _masked_std(values: np.ndarray, mask: np.ndarray) -> float:

    resolved_mask = np.asarray(mask, dtype=bool)
    if not np.any(resolved_mask):
        return 0.0
    return float(np.std(np.asarray(values, dtype=np.float32)[resolved_mask]))


def _polar_cap_absolute_latitudes(texture_size: int, pole: str, latitude_coverage_deg: float) -> tuple[np.ndarray, np.ndarray]:

    _source_u, source_v, inside_disc = _polar_cap_source_uv(texture_size, pole, latitude_coverage_deg)
    return np.abs((0.5 - source_v) * 180.0), inside_disc.astype(bool)


def _polar_cap_region_metrics(
    cap_image: QImage,
    *,
    pole: str,
    latitude_coverage_deg: float,
    blend_start_lat_deg: float,
    blend_end_lat_deg: float,
) -> dict[str, object]:

    texture_size = max(2, int(cap_image.width()))
    absolute_latitudes, inside_disc = _polar_cap_absolute_latitudes(texture_size, pole, latitude_coverage_deg)
    cap_rgb = _image_rgb_array(cap_image).astype(np.float32)
    cap_luma = _luminance_array(cap_rgb)
    detail_map = np.abs(cap_luma - _gaussian_blur_scalar_array(cap_luma, passes=1))
    outer_band_end = min(float(blend_end_lat_deg), float(blend_start_lat_deg) + max(4.0, (float(blend_end_lat_deg) - float(blend_start_lat_deg)) * 0.55))
    core_start = min(89.5, max(float(blend_end_lat_deg), float(blend_start_lat_deg) + 8.0))
    surrounding_mask = inside_disc & (absolute_latitudes >= float(blend_start_lat_deg)) & (absolute_latitudes <= outer_band_end)
    core_mask = inside_disc & (absolute_latitudes >= core_start)
    if not np.any(surrounding_mask):
        surrounding_mask = inside_disc & (absolute_latitudes >= float(blend_start_lat_deg))
    if not np.any(core_mask):
        core_mask = inside_disc & (absolute_latitudes >= float(blend_end_lat_deg))
    surrounding_detail = _masked_mean(detail_map, surrounding_mask)
    core_detail = _masked_mean(detail_map, core_mask)
    surrounding_contrast = _masked_std(cap_luma, surrounding_mask)
    core_contrast = _masked_std(cap_luma, core_mask)
    surrounding_luma = _masked_mean(cap_luma, surrounding_mask)
    core_luma = _masked_mean(cap_luma, core_mask)
    detail_delta = (
        (core_detail - surrounding_detail) / max(1.0e-6, surrounding_detail)
        if surrounding_detail > 1.0e-6
        else 0.0
    )
    return {
        "surrounding_latitude_band_deg": [float(blend_start_lat_deg), float(outer_band_end)],
        "core_latitude_band_deg": [float(core_start), 90.0],
        "surrounding_detail_energy": float(surrounding_detail),
        "core_detail_energy": float(core_detail),
        "surrounding_luminance_std": float(surrounding_contrast),
        "core_luminance_std": float(core_contrast),
        "surrounding_luminance_mean": float(surrounding_luma),
        "core_luminance_mean": float(core_luma),
        "core_sharpness_estimate": float(core_detail),
        "core_vs_surrounding_detail_delta": float(detail_delta),
    }


def _polar_cap_regional_metrics(
    source_image: QImage,
    cap_image: QImage,
    *,
    pole: str,
    latitude_coverage_deg: float,
    blend_start_lat_deg: float,
    blend_end_lat_deg: float,
) -> list[dict[str, object]]:

    sample_latitudes = (
        float(blend_start_lat_deg),
        float(blend_start_lat_deg + (blend_end_lat_deg - blend_start_lat_deg) * 0.5),
        float(blend_end_lat_deg),
        float(min(89.0, max(blend_end_lat_deg + 6.0, 84.0))),
    )
    unique_latitudes: list[float] = []
    for latitude in sample_latitudes:
        clipped = max(float(blend_start_lat_deg), min(89.0, float(latitude)))
        if all(abs(existing - clipped) > 0.25 for existing in unique_latitudes):
            unique_latitudes.append(clipped)
    return [
        _polar_cap_boundary_metrics(
            source_image,
            cap_image,
            pole=pole,
            latitude_deg=latitude,
            latitude_coverage_deg=latitude_coverage_deg,
        )
        for latitude in unique_latitudes
    ]


def _polar_cap_relative_footprint_metrics(
    reference_image: QImage,
    cap_image: QImage,
    *,
    pole: str,
    latitude_coverage_deg: float,
    blend_start_lat_deg: float,
    blend_end_lat_deg: float,
) -> dict[str, object]:

    reference_metrics = _polar_cap_region_metrics(
        reference_image,
        pole=pole,
        latitude_coverage_deg=latitude_coverage_deg,
        blend_start_lat_deg=blend_start_lat_deg,
        blend_end_lat_deg=blend_end_lat_deg,
    )
    actual_metrics = _polar_cap_region_metrics(
        cap_image,
        pole=pole,
        latitude_coverage_deg=latitude_coverage_deg,
        blend_start_lat_deg=blend_start_lat_deg,
        blend_end_lat_deg=blend_end_lat_deg,
    )
    reference_core_detail = float(reference_metrics.get("core_detail_energy", 0.0))
    reference_surrounding_detail = float(reference_metrics.get("surrounding_detail_energy", 0.0))
    actual_core_detail = float(actual_metrics.get("core_detail_energy", 0.0))
    actual_surrounding_detail = float(actual_metrics.get("surrounding_detail_energy", 0.0))
    core_transfer_delta = (
        (actual_core_detail - reference_core_detail) / max(1.0e-6, reference_core_detail)
        if reference_core_detail > 1.0e-6
        else 0.0
    )
    surrounding_transfer_delta = (
        (actual_surrounding_detail - reference_surrounding_detail) / max(1.0e-6, reference_surrounding_detail)
        if reference_surrounding_detail > 1.0e-6
        else 0.0
    )
    return {
        **actual_metrics,
        "reference_core_detail_energy": reference_core_detail,
        "reference_surrounding_detail_energy": reference_surrounding_detail,
        "core_detail_transfer_delta": float(core_transfer_delta),
        "surrounding_detail_transfer_delta": float(surrounding_transfer_delta),
        "core_vs_surrounding_detail_delta": float(core_transfer_delta - surrounding_transfer_delta),
    }


def _polar_cap_boundary_samples(
    source_image: QImage,
    cap_image: QImage,
    *,
    pole: str,
    latitude_deg: float,
    latitude_coverage_deg: float,
    sample_count: int = 256,
) -> tuple[np.ndarray, np.ndarray]:

    count = max(16, int(sample_count))
    longitudes = ((np.arange(count, dtype=np.float32) + 0.5) / float(count)) * (2.0 * np.pi) - np.pi
    radius = (90.0 - abs(float(latitude_deg))) / max(1.0e-9, 90.0 - float(latitude_coverage_deg))
    cap_u = 0.5 + np.sin(longitudes) * radius * 0.5
    cap_v = 0.5 - np.cos(longitudes) * radius * 0.5
    signed_latitude = abs(float(latitude_deg)) if str(pole).casefold() == "north" else -abs(float(latitude_deg))
    source_u = np.mod(0.5 + longitudes / (2.0 * np.pi), 1.0)
    source_v = np.full_like(source_u, np.clip(0.5 - signed_latitude / 180.0, 0.0, 1.0))
    expected = _sample_equirectangular_rgb(source_image, source_u, source_v).reshape((-1, 3))
    actual = _sample_planar_rgb(cap_image, cap_u, cap_v).reshape((-1, 3))
    return expected, actual


def _polar_cap_boundary_metrics(
    source_image: QImage,
    cap_image: QImage,
    *,
    pole: str,
    latitude_deg: float,
    latitude_coverage_deg: float,
) -> dict[str, object]:

    expected, actual = _polar_cap_boundary_samples(
        source_image,
        cap_image,
        pole=pole,
        latitude_deg=latitude_deg,
        latitude_coverage_deg=latitude_coverage_deg,
    )
    normalized_delta = np.abs(expected - actual) / 255.0
    expected_luma = np.dot(expected, np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)) / 255.0
    actual_luma = np.dot(actual, np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)) / 255.0
    expected_contrast = float(np.std(expected_luma))
    actual_contrast = float(np.std(actual_luma))
    return {
        "latitude_deg": float(latitude_deg),
        "sample_count": int(expected.shape[0]),
        "mean_color_delta": float(np.mean(normalized_delta)),
        "max_color_delta": float(np.max(normalized_delta)),
        "mean_luminance_delta": float(np.mean(np.abs(expected_luma - actual_luma))),
        "max_luminance_delta": float(np.max(np.abs(expected_luma - actual_luma))),
        "expected_luminance_std": expected_contrast,
        "cap_luminance_std": actual_contrast,
        "contrast_delta": float(abs(expected_contrast - actual_contrast) / max(1.0e-6, expected_contrast)),
        "mean_expected_rgb": [float(value) for value in np.mean(expected, axis=0)],
        "mean_cap_rgb": [float(value) for value in np.mean(actual, axis=0)],
    }


def _apply_polar_cap_boundary_normalization(
    source_image: QImage,
    cap_image: QImage,
    *,
    pole: str,
    latitude_coverage_deg: float,
    blend_start_lat_deg: float,
    blend_end_lat_deg: float,
    strength: float,
) -> tuple[QImage, dict[str, object]]:

    resolved_strength = max(0.0, min(1.0, float(strength)))
    before = _polar_cap_boundary_metrics(
        source_image,
        cap_image,
        pole=pole,
        latitude_deg=blend_start_lat_deg,
        latitude_coverage_deg=latitude_coverage_deg,
    )
    expected, actual = _polar_cap_boundary_samples(
        source_image,
        cap_image,
        pole=pole,
        latitude_deg=blend_start_lat_deg,
        latitude_coverage_deg=latitude_coverage_deg,
    )
    expected_mean = np.mean(expected, axis=0)
    actual_mean = np.mean(actual, axis=0)
    expected_std = np.std(expected, axis=0)
    actual_std = np.std(actual, axis=0)
    contrast_gain = np.clip(expected_std / np.maximum(actual_std, 1.0), 0.97, 1.03)
    color_offset = np.clip(expected_mean - actual_mean, -8.0, 8.0)
    cap_values = _image_rgb_array(cap_image).astype(np.float32)
    source_u, source_v, inside_disc = _polar_cap_source_uv(
        int(cap_image.width()),
        pole,
        latitude_coverage_deg,
    )
    absolute_latitudes = np.abs((0.5 - source_v) * 180.0)
    transition = _smootherstep_array(
        (absolute_latitudes - float(blend_start_lat_deg))
        / max(1.0e-9, float(blend_end_lat_deg) - float(blend_start_lat_deg))
    )
    adjustment_weight = (1.0 - transition) * inside_disc.astype(np.float32) * resolved_strength
    matched = (cap_values - actual_mean.reshape((1, 1, 3))) * contrast_gain.reshape((1, 1, 3)) + expected_mean.reshape((1, 1, 3))
    adjusted_values = cap_values * (1.0 - adjustment_weight[..., None]) + matched * adjustment_weight[..., None]
    adjusted_image = _rgb_array_image(adjusted_values)
    after = _polar_cap_boundary_metrics(
        source_image,
        adjusted_image,
        pole=pole,
        latitude_deg=blend_start_lat_deg,
        latitude_coverage_deg=latitude_coverage_deg,
    )
    return adjusted_image, {
        "enabled": resolved_strength > 0.0,
        "mode": "boundary_luma_contrast_color_balance_taper",
        "strength": float(resolved_strength),
        "color_offset_rgb": [float(value) for value in color_offset],
        "contrast_gain_rgb": [float(value) for value in contrast_gain],
        "before": before,
        "after": after,
    }


def _apply_polar_cap_core_harmonization(
    cap_image: QImage,
    *,
    pole: str,
    latitude_coverage_deg: float,
    blend_start_lat_deg: float,
    blend_end_lat_deg: float,
    sharpness: float,
    local_contrast: float,
    strength: float,
) -> tuple[QImage, dict[str, object]]:

    resolved_strength = max(0.0, min(1.0, float(strength)))
    resolved_sharpness = max(0.0, float(sharpness))
    resolved_local_contrast = max(0.0, float(local_contrast))
    before = _polar_cap_region_metrics(
        cap_image,
        pole=pole,
        latitude_coverage_deg=latitude_coverage_deg,
        blend_start_lat_deg=blend_start_lat_deg,
        blend_end_lat_deg=blend_end_lat_deg,
    )
    if resolved_strength <= 0.0 or (resolved_sharpness <= 0.0 and resolved_local_contrast <= 0.0):
        return cap_image, {
            "enabled": False,
            "mode": "disabled",
            "strength": float(resolved_strength),
            "albedo_sharpness": float(resolved_sharpness),
            "local_contrast": float(resolved_local_contrast),
            "computed_unsharp_amount": 0.0,
            "computed_contrast_gain": 1.0,
            "computed_luma_offset": 0.0,
            "before": before,
            "after": before,
        }
    cap_values = _image_rgb_array(cap_image).astype(np.float32)
    absolute_latitudes, inside_disc = _polar_cap_absolute_latitudes(int(cap_image.width()), pole, latitude_coverage_deg)
    core_weight = _smootherstep_array(
        (absolute_latitudes - float(blend_end_lat_deg))
        / max(1.0e-9, 90.0 - float(blend_end_lat_deg))
    ) * inside_disc.astype(np.float32) * resolved_strength
    outer_band_end = min(float(blend_end_lat_deg), float(blend_start_lat_deg) + max(4.0, (float(blend_end_lat_deg) - float(blend_start_lat_deg)) * 0.55))
    core_start = min(89.5, max(float(blend_end_lat_deg), float(blend_start_lat_deg) + 8.0))
    surrounding_mask = inside_disc & (absolute_latitudes >= float(blend_start_lat_deg)) & (absolute_latitudes <= outer_band_end)
    core_mask = inside_disc & (absolute_latitudes >= core_start)
    if not np.any(surrounding_mask):
        surrounding_mask = inside_disc & (absolute_latitudes >= float(blend_start_lat_deg))
    if not np.any(core_mask):
        core_mask = inside_disc & (absolute_latitudes >= float(blend_end_lat_deg))
    cap_luma = _luminance_array(cap_values)
    detail_map = np.abs(cap_luma - _gaussian_blur_scalar_array(cap_luma, passes=1))
    surrounding_mean_rgb = np.mean(cap_values[surrounding_mask], axis=0) if np.any(surrounding_mask) else np.mean(cap_values[inside_disc], axis=0)
    core_mean_rgb = np.mean(cap_values[core_mask], axis=0) if np.any(core_mask) else surrounding_mean_rgb
    surrounding_detail = _masked_mean(detail_map, surrounding_mask)
    core_detail = _masked_mean(detail_map, core_mask)
    surrounding_contrast = _masked_std(cap_luma, surrounding_mask)
    core_contrast = _masked_std(cap_luma, core_mask)
    desired_contrast_gain = np.clip(
        max(1.0e-6, surrounding_contrast) / max(1.0e-6, core_contrast),
        0.97,
        1.14,
    )
    contrast_gain = 1.0 + (desired_contrast_gain - 1.0) * min(1.0, resolved_local_contrast) * resolved_strength
    desired_sharpness_gain = np.clip(
        max(1.0e-6, surrounding_detail) / max(1.0e-6, core_detail),
        1.0,
        1.28,
    )
    unsharp_amount = min(resolved_sharpness, desired_sharpness_gain - 1.0)
    blurred = _gaussian_blur_rgb_array(cap_values, passes=1)
    sharpened = cap_values + unsharp_amount * (cap_values - blurred)
    color_offset = np.clip(surrounding_mean_rgb - core_mean_rgb, -6.0, 6.0)
    matched = (sharpened - core_mean_rgb.reshape((1, 1, 3))) * contrast_gain + core_mean_rgb.reshape((1, 1, 3)) + color_offset.reshape((1, 1, 3))
    adjusted_values = cap_values * (1.0 - core_weight[..., None]) + matched * core_weight[..., None]
    adjusted_image = _rgb_array_image(adjusted_values)
    after = _polar_cap_region_metrics(
        adjusted_image,
        pole=pole,
        latitude_coverage_deg=latitude_coverage_deg,
        blend_start_lat_deg=blend_start_lat_deg,
        blend_end_lat_deg=blend_end_lat_deg,
    )
    return adjusted_image, {
        "enabled": True,
        "mode": "core_unsharp_luma_contrast_taper",
        "strength": float(resolved_strength),
        "albedo_sharpness": float(resolved_sharpness),
        "local_contrast": float(resolved_local_contrast),
        "computed_unsharp_amount": float(unsharp_amount),
        "computed_contrast_gain": float(contrast_gain),
        "computed_luma_offset": float(np.mean(color_offset)),
        "before": before,
        "after": after,
    }


def _build_polar_cap_albedo_image(
    source_image: QImage,
    *,
    pole: str,
    texture_size: int,
    latitude_coverage_deg: float,
    blend_start_lat_deg: float,
    blend_end_lat_deg: float,
    boundary_match_enabled: bool,
    boundary_match_strength: float,
    albedo_sharpness: float,
    local_contrast: float,
    core_match_strength: float,
) -> tuple[QImage, QImage, dict[str, object], dict[str, object], list[dict[str, object]]]:

    polar_albedo = _project_polar_cap_image(
        source_image,
        pole=pole,
        texture_size=texture_size,
        latitude_coverage_deg=latitude_coverage_deg,
    )
    if boundary_match_enabled:
        polar_albedo, boundary_normalization_report = _apply_polar_cap_boundary_normalization(
            source_image,
            polar_albedo,
            pole=pole,
            latitude_coverage_deg=latitude_coverage_deg,
            blend_start_lat_deg=blend_start_lat_deg,
            blend_end_lat_deg=blend_end_lat_deg,
            strength=boundary_match_strength,
        )
    else:
        boundary_metrics = _polar_cap_boundary_metrics(
            source_image,
            polar_albedo,
            pole=pole,
            latitude_deg=blend_start_lat_deg,
            latitude_coverage_deg=latitude_coverage_deg,
        )
        boundary_normalization_report = {
            "enabled": False,
            "mode": "disabled",
            "strength": 0.0,
            "before": boundary_metrics,
            "after": boundary_metrics,
        }
    harmonization_reference = polar_albedo
    polar_albedo, appearance_harmonization_report = _apply_polar_cap_core_harmonization(
        polar_albedo,
        pole=pole,
        latitude_coverage_deg=latitude_coverage_deg,
        blend_start_lat_deg=blend_start_lat_deg,
        blend_end_lat_deg=blend_end_lat_deg,
        sharpness=albedo_sharpness,
        local_contrast=local_contrast,
        strength=core_match_strength,
    )
    regional_metrics = _polar_cap_regional_metrics(
        source_image,
        polar_albedo,
        pole=pole,
        latitude_coverage_deg=latitude_coverage_deg,
        blend_start_lat_deg=blend_start_lat_deg,
        blend_end_lat_deg=blend_end_lat_deg,
    )
    appearance_harmonization_report = {
        **appearance_harmonization_report,
        "regional_metrics_after": regional_metrics,
        "relative_footprint_metrics_after": _polar_cap_relative_footprint_metrics(
            harmonization_reference,
            polar_albedo,
            pole=pole,
            latitude_coverage_deg=latitude_coverage_deg,
            blend_start_lat_deg=blend_start_lat_deg,
            blend_end_lat_deg=blend_end_lat_deg,
        ),
    }
    return polar_albedo, harmonization_reference, boundary_normalization_report, appearance_harmonization_report, regional_metrics


def generate_synthetic_moon_source_maps(
    output_dir: str | Path,
    *,
    width: int = 512,
    height: int = 256,
    include_tile_labels: bool = False,
) -> dict[str, object]:

    resolved_output_dir = Path(output_dir).resolve()
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    image_width = max(16, int(width))
    image_height = max(8, int(height))
    uv_grid = QImage(image_width, image_height, QImage.Format.Format_RGB888)
    grid_columns = 16
    grid_rows = 8
    for y in range(image_height):
        v = y / max(1, image_height - 1)
        for x in range(image_width):
            u = x / max(1, image_width - 1)
            red = int(round(u * 255.0))
            green = int(round(v * 255.0))
            blue = 96
            if x == 0:
                red, green, blue = 255, 32, 32
            elif x == image_width - 1:
                red, green, blue = 32, 32, 255
            elif x % max(1, image_width // grid_columns) == 0 or y % max(1, image_height // grid_rows) == 0:
                red = min(255, red + 92)
                green = min(255, green + 92)
                blue = 230
            uv_grid.setPixelColor(x, y, QColor(red, green, blue))
    if include_tile_labels:
        painter = QPainter(uv_grid)
        try:
            painter.setPen(QPen(QColor(255, 255, 255), 1))
            painter.setFont(QFont("Arial", max(7, image_width // 96)))
            for tile_y in range(grid_rows):
                for tile_x in range(grid_columns):
                    painter.drawText(
                        int(tile_x * image_width / grid_columns) + 3,
                        int((tile_y + 0.5) * image_height / grid_rows),
                        f"{tile_x},{tile_y}",
                    )
        finally:
            painter.end()

    flat_normal = QImage(image_width, image_height, QImage.Format.Format_RGB888)
    flat_normal.fill(QColor(128, 128, 255))

    bump_height = QImage(image_width, image_height, QImage.Format.Format_Grayscale8)
    center_u = 0.38
    center_v = 0.46
    sigma_u = 0.07
    sigma_v = 0.11
    for y in range(image_height):
        v = y / max(1, image_height - 1)
        for x in range(image_width):
            u = x / max(1, image_width - 1)
            seam_distance = min(abs(u - center_u), 1.0 - abs(u - center_u))
            radial = (seam_distance / sigma_u) ** 2 + ((v - center_v) / sigma_v) ** 2
            value = int(round(96.0 + 128.0 * math.exp(-0.5 * radial)))
            bump_height.setPixelColor(x, y, QColor(value, value, value))
    bump_normal = MoonCache._normal_image_from_height_image(bump_height)

    paths = {
        "uv_grid": resolved_output_dir / "synthetic_moon_uv_grid.png",
        "flat_normal": resolved_output_dir / "synthetic_moon_flat_normal.png",
        "bump_height": resolved_output_dir / "synthetic_moon_bump_height.png",
        "bump_normal": resolved_output_dir / "synthetic_moon_bump_normal.png",
    }
    _save_tile_image(uv_grid, paths["uv_grid"])
    _save_tile_image(flat_normal, paths["flat_normal"])
    _save_tile_image(bump_height, paths["bump_height"])
    _save_tile_image(bump_normal, paths["bump_normal"])
    return {
        "output_dir": str(resolved_output_dir),
        "width": int(image_width),
        "height": int(image_height),
        "paths": {key: str(path) for key, path in paths.items()},
        "uv_layout": _uv_layout_summary(4),
    }


def _level_build_entries(max_level: int, tile_size: int) -> list[dict[str, int]]:

    entries: list[dict[str, int]] = []
    for level in range(max_level + 1):
        columns, rows = MoonTiledTextureManager.tile_grid_dimensions(level)
        entries.append(
            {
                "level": int(level),
                "columns": int(columns),
                "rows": int(rows),
                "tile_count": int(columns * rows),
                "level_width": int(columns * tile_size),
                "level_height": int(rows * tile_size),
            }
        )
    return entries


def build_moon_tile_tree(
    project_root: Path,
    *,
    albedo_source: str | Path | None = None,
    global_albedo_source: str | Path | None = None,
    normal_source: str | Path | None = None,
    displacement_source: str | Path | None = None,
    output_dir: str | Path | None = None,
    tile_size: int = 512,
    max_level: int | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    tile_format: str = "png",
    flip_u: bool = False,
    flip_v: bool = False,
    longitude_shift_pixels: int = 0,
    longitude_shift_degrees: float = 0.0,
    rotate_180: bool = False,
    source_crop: tuple[int, int, int, int] | None = None,
    source_resize: tuple[int, int] | None = None,
    normal_smoothing_passes: int = 2,
    polar_normal_smoothing_passes: int = 3,
    polar_normal_fade_start_lat_deg: float = 70.0,
    polar_normal_fade_end_lat_deg: float = 86.0,
    generate_polar_caps: bool = True,
    polar_cap_size: int | None = None,
    polar_cap_latitude_coverage_deg: float = 60.0,
    polar_cap_blend_start_lat_deg: float = 60.0,
    polar_cap_blend_end_lat_deg: float = 78.0,
    polar_cap_blend_curve_power: float = 1.0,
    polar_cap_normal_blend_start_lat_deg: float = 78.0,
    polar_cap_normal_blend_end_lat_deg: float = 86.0,
    polar_cap_boundary_match: bool = True,
    polar_cap_boundary_match_strength: float = 0.35,
    polar_cap_albedo_sharpness: float = 0.22,
    polar_cap_local_contrast: float = 1.0,
    polar_cap_core_match_strength: float = 0.72,
) -> dict[str, object]:

    paths = default_moon_tile_paths(Path(project_root))
    resolved_root = paths["project_root"]
    resolved_albedo_source = _resolve_path(resolved_root, albedo_source, paths["albedo_source"])
    resolved_global_albedo_source = _resolve_path(
        resolved_root,
        global_albedo_source,
        resolved_albedo_source,
    )
    resolved_output_dir = _resolve_path(resolved_root, output_dir, paths["output_dir"])
    resolved_normal_source = _resolve_path(resolved_root, normal_source) if normal_source is not None else None
    resolved_displacement_source = _resolve_path(resolved_root, displacement_source, paths["displacement_source"])
    resolved_tile_format = str(tile_format).strip().casefold()
    resolved_normal_smoothing_passes = max(0, int(normal_smoothing_passes))
    resolved_polar_smoothing_passes = max(0, int(polar_normal_smoothing_passes))
    resolved_polar_fade_start_lat_deg = max(0.0, min(90.0, float(polar_normal_fade_start_lat_deg)))
    resolved_polar_fade_end_lat_deg = max(0.0, min(90.0, float(polar_normal_fade_end_lat_deg)))
    if resolved_polar_fade_end_lat_deg <= resolved_polar_fade_start_lat_deg:
        raise ValueError("Polar normal fade end latitude must be greater than the start latitude.")
    requested_polar_cap_size = None if polar_cap_size is None else max(16, int(polar_cap_size))
    resolved_polar_cap_coverage_deg = max(0.0, min(89.999, float(polar_cap_latitude_coverage_deg)))
    resolved_polar_cap_blend_start_deg = max(0.0, min(89.999, float(polar_cap_blend_start_lat_deg)))
    resolved_polar_cap_blend_end_deg = max(0.0, min(89.999, float(polar_cap_blend_end_lat_deg)))
    resolved_polar_cap_blend_curve_power = max(0.1, float(polar_cap_blend_curve_power))
    resolved_polar_cap_normal_blend_start_deg = max(0.0, min(89.999, float(polar_cap_normal_blend_start_lat_deg)))
    resolved_polar_cap_normal_blend_end_deg = max(0.0, min(89.999, float(polar_cap_normal_blend_end_lat_deg)))
    resolved_polar_cap_boundary_match_strength = max(0.0, min(1.0, float(polar_cap_boundary_match_strength)))
    resolved_polar_cap_albedo_sharpness = max(0.0, float(polar_cap_albedo_sharpness))
    resolved_polar_cap_local_contrast = max(0.0, float(polar_cap_local_contrast))
    resolved_polar_cap_core_match_strength = max(0.0, min(1.0, float(polar_cap_core_match_strength)))
    if not (
        resolved_polar_cap_coverage_deg <= resolved_polar_cap_blend_start_deg
        < resolved_polar_cap_blend_end_deg
    ):
        raise ValueError("Polar cap latitude coverage must reach the blend band and blend end must exceed blend start.")
    if not (
        resolved_polar_cap_blend_start_deg <= resolved_polar_cap_normal_blend_start_deg
        < resolved_polar_cap_normal_blend_end_deg
    ):
        raise ValueError("Polar cap normal blend must begin within or after the albedo blend band.")

    if resolved_tile_format not in SUPPORTED_TILE_FORMATS:
        raise ValueError(f"Unsupported tile format '{tile_format}'. Expected one of {sorted(SUPPORTED_TILE_FORMATS)}.")
    if resolved_albedo_source is None or not resolved_albedo_source.is_file():
        raise ValueError(f"Albedo source image not found: {resolved_albedo_source}")
    if resolved_output_dir is None:
        raise ValueError("Output directory could not be resolved.")

    raw_albedo_image = _load_image(resolved_albedo_source, label="albedo")
    albedo_image, source_transform = _transform_source_image(
        raw_albedo_image,
        flip_u=flip_u,
        flip_v=flip_v,
        longitude_shift_pixels=longitude_shift_pixels,
        longitude_shift_degrees=longitude_shift_degrees,
        rotate_180=rotate_180,
        source_crop=source_crop,
        source_resize=source_resize,
    )
    albedo_width = max(1, int(albedo_image.width()))
    albedo_height = max(1, int(albedo_image.height()))
    resolved_polar_cap_size = (
        requested_polar_cap_size
        if requested_polar_cap_size is not None
        else min(2048, max(64, int(albedo_width)))
    )
    supported_max_level = _max_supported_level(albedo_width, albedo_height, int(tile_size))
    resolved_max_level = supported_max_level if max_level is None else int(max_level)
    if resolved_max_level < 0:
        raise ValueError("Max level must be zero or greater.")
    if resolved_max_level > supported_max_level:
        raise ValueError(
            f"Requested max level L{resolved_max_level} exceeds supported level L{supported_max_level} "
            f"for {albedo_width}x{albedo_height} source and tile size {tile_size}."
        )

    normal_source_kind = "missing"
    normal_input_image: QImage | None = None
    normal_input_path: Path | None = None
    if resolved_normal_source is not None:
        if not resolved_normal_source.is_file():
            raise ValueError(f"Normal source image not found: {resolved_normal_source}")
        normal_source_kind = "normal"
        normal_input_path = resolved_normal_source
        normal_input_image, _normal_transform = _transform_source_image(
            _load_image(resolved_normal_source, label="normal"),
            flip_u=flip_u,
            flip_v=flip_v,
            longitude_shift_pixels=longitude_shift_pixels,
            longitude_shift_degrees=longitude_shift_degrees,
            rotate_180=rotate_180,
            source_crop=source_crop,
            source_resize=source_resize,
        )
    elif resolved_displacement_source is not None and resolved_displacement_source.is_file():
        normal_source_kind = "height"
        normal_input_path = resolved_displacement_source
        normal_input_image, _normal_transform = _transform_source_image(
            _load_image(resolved_displacement_source, label="displacement"),
            flip_u=flip_u,
            flip_v=flip_v,
            longitude_shift_pixels=longitude_shift_pixels,
            longitude_shift_degrees=longitude_shift_degrees,
            rotate_180=rotate_180,
            source_crop=source_crop,
            source_resize=source_resize,
        )

    if resolved_output_dir.exists() and not overwrite:
        has_existing_tiles = any((resolved_output_dir / child_name).exists() for child_name in ("albedo", "normal"))
        if has_existing_tiles:
            raise ValueError(
                f"Output directory already contains moon tiles: {resolved_output_dir}. "
                "Use --overwrite to replace them."
            )

    levels = _level_build_entries(resolved_max_level, int(tile_size))
    planned_tile_count = sum(entry["tile_count"] for entry in levels)
    manifest = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "tile_size": int(tile_size),
        "max_level": int(resolved_max_level),
        "tile_format": resolved_tile_format,
        "edge_padding_px": 0,
        "output_dir": str(resolved_output_dir),
        "uv_layout": _uv_layout_summary(resolved_max_level),
        "canonical_convention": dict(CANONICAL_MOON_CONVENTION),
        "source_transform": source_transform,
        "albedo": {
            "source_path": str(resolved_albedo_source),
            "source_filename": resolved_albedo_source.name,
            "source_sha256": _sha256_file(resolved_albedo_source),
            "source_width_raw": int(raw_albedo_image.width()),
            "source_height_raw": int(raw_albedo_image.height()),
            "source_width": int(albedo_width),
            "source_height": int(albedo_height),
            "u_direction": CANONICAL_MOON_CONVENTION["u_direction"],
            "v_direction": CANONICAL_MOON_CONVENTION["v_direction"],
            "longitude_seam_convention": CANONICAL_MOON_CONVENTION["longitude_seam_convention"],
        },
        "normal": {
            "source_path": str(normal_input_path) if normal_input_path is not None else None,
            "source_filename": normal_input_path.name if normal_input_path is not None else None,
            "source_sha256": _sha256_file(normal_input_path),
            "source_kind": normal_source_kind,
            "source_width": int(normal_input_image.width()) if normal_input_image is not None else None,
            "source_height": int(normal_input_image.height()) if normal_input_image is not None else None,
            "source_convention": CANONICAL_MOON_CONVENTION_ID,
            "transform_matches_albedo": True,
            "smoothing_passes": resolved_normal_smoothing_passes if normal_source_kind == "height" else 0,
            "normal_generation": "height_gradient_gaussian_smoothed" if normal_source_kind == "height" else normal_source_kind,
            "polar_smoothing": {
                "enabled": normal_source_kind == "height",
                "additional_smoothing_passes": resolved_polar_smoothing_passes if normal_source_kind == "height" else 0,
                "derivative_fade_start_lat_deg": resolved_polar_fade_start_lat_deg,
                "derivative_fade_end_lat_deg": resolved_polar_fade_end_lat_deg,
                "analytic_normal_at_exact_pole": True,
            },
        },
        "polar_caps": {
            "enabled": bool(generate_polar_caps),
            "projection": POLAR_CAP_PROJECTION,
            "texture_size": int(resolved_polar_cap_size),
            "latitude_coverage_deg": float(resolved_polar_cap_coverage_deg),
            "blend_start_lat_deg": float(resolved_polar_cap_blend_start_deg),
            "blend_end_lat_deg": float(resolved_polar_cap_blend_end_deg),
            "blend_curve": {
                "type": "smootherstep_power",
                "power": float(resolved_polar_cap_blend_curve_power),
            },
            "normal_blend_start_lat_deg": float(resolved_polar_cap_normal_blend_start_deg),
            "normal_blend_end_lat_deg": float(resolved_polar_cap_normal_blend_end_deg),
            "filtering_policy": {
                "magnification": "linear",
                "minification": "linear_mipmap_linear",
                "wrap_s": "clamp_to_edge",
                "wrap_t": "clamp_to_edge",
            },
            "boundary_normalization": {
                "enabled": bool(polar_cap_boundary_match),
                "mode": "boundary_luma_contrast_color_balance_taper",
                "strength": float(resolved_polar_cap_boundary_match_strength if polar_cap_boundary_match else 0.0),
            },
            "appearance_matching": {
                "reconstruction_filter": "bilinear_projected",
                "detail_reconstruction": "core_unsharp_luma_contrast_taper",
                "albedo_sharpness": float(resolved_polar_cap_albedo_sharpness),
                "local_contrast": float(resolved_polar_cap_local_contrast),
                "boundary_match_strength": float(resolved_polar_cap_boundary_match_strength if polar_cap_boundary_match else 0.0),
                "core_match_strength": float(resolved_polar_cap_core_match_strength),
            },
            "source_convention": CANONICAL_MOON_CONVENTION_ID,
            "source_transform": dict(source_transform),
            "source_hash": _sha256_file(resolved_albedo_source),
            "caps": {
                pole: {
                    "pole": pole,
                    "projection": POLAR_CAP_PROJECTION,
                    "latitude_coverage_deg": float(resolved_polar_cap_coverage_deg),
                    "texture_size": int(resolved_polar_cap_size),
                    "albedo_path": str(Path("polar_caps") / "albedo" / f"{pole}.png") if generate_polar_caps else None,
                    "normal_path": (
                        str(Path("polar_caps") / "normal" / f"{pole}.png")
                        if generate_polar_caps and normal_source_kind == "height"
                        else None
                    ),
                    "albedo_source_sha256": _sha256_file(resolved_albedo_source),
                    "normal_source_sha256": _sha256_file(normal_input_path),
                    "normal_projection_status": (
                        "height_projected_then_planar_gradient"
                        if generate_polar_caps and normal_source_kind == "height"
                        else "analytic_normal_fallback"
                    ),
                }
                for pole in ("north", "south")
            },
            "validation": {"status": "pending" if generate_polar_caps and not dry_run else "not_generated"},
        },
        "registration": {
            "validation_status": "pending" if not dry_run else "not_run_dry_run",
            "registered_to_active_global_texture": False,
            "global_source_path": str(resolved_global_albedo_source) if resolved_global_albedo_source is not None else None,
            "global_source_filename": resolved_global_albedo_source.name if resolved_global_albedo_source is not None else None,
            "global_source_sha256": _sha256_file(resolved_global_albedo_source),
        },
        "levels": levels,
    }

    if dry_run:
        return {
            "dry_run": True,
            "output_dir": str(resolved_output_dir),
            "tile_size": int(tile_size),
            "max_level": int(resolved_max_level),
            "planned_albedo_tile_count": int(planned_tile_count),
            "planned_normal_tile_count": int(planned_tile_count if normal_input_image is not None else 0),
            "planned_polar_cap_count": int(2 if generate_polar_caps else 0),
            "manifest": manifest,
        }

    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    if overwrite:
        _clear_existing_output(resolved_output_dir)

    written_albedo_tiles = 0
    written_normal_tiles = 0
    written_polar_cap_albedo_textures = 0
    written_polar_cap_normal_textures = 0
    tile_suffix = f".{resolved_tile_format}"
    for level_entry in levels:
        level = int(level_entry["level"])
        columns = int(level_entry["columns"])
        rows = int(level_entry["rows"])
        level_width = int(level_entry["level_width"])
        level_height = int(level_entry["level_height"])

        scaled_albedo = _scaled_level_image(albedo_image, level_width, level_height).convertToFormat(QImage.Format.Format_RGB888)
        scaled_normal: QImage | None = None
        if normal_input_image is not None:
            scaled_input = _scaled_level_image(normal_input_image, level_width, level_height)
            if normal_source_kind == "normal":
                scaled_normal = scaled_input.convertToFormat(QImage.Format.Format_RGB888)
            else:
                scaled_normal = MoonCache._normal_image_from_height_image(
                    scaled_input,
                    smoothing_passes=resolved_normal_smoothing_passes,
                    polar_smoothing_passes=resolved_polar_smoothing_passes,
                    polar_derivative_fade_start_lat_deg=resolved_polar_fade_start_lat_deg,
                    polar_derivative_fade_end_lat_deg=resolved_polar_fade_end_lat_deg,
                )

        for tile_y in range(rows):
            for tile_x in range(columns):
                tile_path = resolved_output_dir / "albedo" / f"L{level}" / f"{tile_x}_{tile_y}{tile_suffix}"
                tile_image = scaled_albedo.copy(tile_x * int(tile_size), tile_y * int(tile_size), int(tile_size), int(tile_size))
                _save_tile_image(tile_image, tile_path)
                written_albedo_tiles += 1

                if scaled_normal is not None:
                    normal_path = resolved_output_dir / "normal" / f"L{level}" / f"{tile_x}_{tile_y}{tile_suffix}"
                    normal_tile = scaled_normal.copy(tile_x * int(tile_size), tile_y * int(tile_size), int(tile_size), int(tile_size))
                    _save_tile_image(normal_tile, normal_path)
                    written_normal_tiles += 1

    if generate_polar_caps:
        for pole in ("north", "south"):
            polar_albedo, _harmonization_reference, boundary_normalization_report, appearance_harmonization_report, regional_metrics = _build_polar_cap_albedo_image(
                albedo_image,
                pole=pole,
                texture_size=resolved_polar_cap_size,
                latitude_coverage_deg=resolved_polar_cap_coverage_deg,
                blend_start_lat_deg=resolved_polar_cap_blend_start_deg,
                blend_end_lat_deg=resolved_polar_cap_blend_end_deg,
                boundary_match_enabled=polar_cap_boundary_match,
                boundary_match_strength=resolved_polar_cap_boundary_match_strength,
                albedo_sharpness=resolved_polar_cap_albedo_sharpness,
                local_contrast=resolved_polar_cap_local_contrast,
                core_match_strength=resolved_polar_cap_core_match_strength,
            )
            manifest["polar_caps"]["caps"][pole]["boundary_normalization"] = boundary_normalization_report
            manifest["polar_caps"]["caps"][pole]["appearance_harmonization"] = appearance_harmonization_report
            manifest["polar_caps"]["caps"][pole]["regional_metrics"] = regional_metrics
            _save_tile_image(polar_albedo, resolved_output_dir / "polar_caps" / "albedo" / f"{pole}.png")
            written_polar_cap_albedo_textures += 1
            if normal_source_kind == "height" and normal_input_image is not None:
                polar_height = _project_polar_cap_image(
                    normal_input_image,
                    pole=pole,
                    texture_size=resolved_polar_cap_size,
                    latitude_coverage_deg=resolved_polar_cap_coverage_deg,
                )
                polar_normal = MoonCache._normal_image_from_planar_height_image(
                    polar_height,
                    smoothing_passes=resolved_normal_smoothing_passes + resolved_polar_smoothing_passes,
                )
                _save_tile_image(polar_normal, resolved_output_dir / "polar_caps" / "normal" / f"{pole}.png")
                written_polar_cap_normal_textures += 1

    manifest_path = resolved_output_dir / MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    registration_report = validate_global_vs_tiled_registration(
        resolved_root,
        output_dir=resolved_output_dir,
        global_albedo_source=resolved_global_albedo_source,
        samples_per_axis=12,
    )
    manifest["registration"] = {
        **manifest["registration"],
        "validation_status": str(registration_report["status"]),
        "registered_to_active_global_texture": bool(registration_report["registered"]),
        "mean_color_delta": registration_report["mean_color_delta"],
        "max_color_delta": registration_report["max_color_delta"],
        "mean_luminance_delta": registration_report["mean_luminance_delta"],
        "max_luminance_delta": registration_report["max_luminance_delta"],
        "detected_longitude_shift_degrees": registration_report["detected_longitude_shift_degrees"],
        "possible_u_flip_detected": registration_report["possible_u_flip_detected"],
        "possible_v_flip_detected": registration_report["possible_v_flip_detected"],
        "validation_level": registration_report["level"],
    }
    polar_cap_validation = validate_polar_cap_assets(resolved_root, output_dir=resolved_output_dir)
    manifest["polar_caps"]["validation"] = polar_cap_validation
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "dry_run": False,
        "output_dir": str(resolved_output_dir),
        "tile_size": int(tile_size),
        "max_level": int(resolved_max_level),
        "written_albedo_tile_count": int(written_albedo_tiles),
        "written_normal_tile_count": int(written_normal_tiles),
        "written_polar_cap_albedo_texture_count": int(written_polar_cap_albedo_textures),
        "written_polar_cap_normal_texture_count": int(written_polar_cap_normal_textures),
        "manifest_path": str(manifest_path),
        "manifest": manifest,
        "global_vs_tiled_registration": registration_report,
        "polar_cap_validation": polar_cap_validation,
    }


def _parse_lod_level(name: str) -> int | None:

    if len(name) < 2 or not name.startswith("L"):
        return None
    try:
        return int(name[1:])
    except ValueError:
        return None


def _parse_tile_coordinates(stem: str) -> tuple[int, int] | None:

    parts = stem.split("_")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _collect_tile_files(root: Path, kind: str) -> tuple[dict[MoonTileKey, Path], list[dict[str, object]], dict[str, list[dict[str, object]]]]:

    valid_tiles: dict[MoonTileKey, Path] = {}
    invalid_images: list[dict[str, object]] = []
    size_records: dict[str, list[dict[str, object]]] = {}
    if not root.is_dir():
        return valid_tiles, invalid_images, size_records

    for lod_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        lod_level = _parse_lod_level(lod_dir.name)
        if lod_level is None:
            continue
        columns, rows = MoonTiledTextureManager.tile_grid_dimensions(lod_level)
        level_key = f"L{lod_level}"
        for path in sorted(candidate for candidate in lod_dir.iterdir() if candidate.is_file()):
            if path.suffix.casefold() not in MoonTiledTextureManager.SUPPORTED_SUFFIXES:
                continue
            parsed = _parse_tile_coordinates(path.stem)
            if parsed is None:
                continue
            tile_x, tile_y = parsed
            if not (0 <= tile_x < columns and 0 <= tile_y < rows):
                continue
            image = QImage(str(path))
            if image.isNull():
                invalid_images.append({"kind": kind, "path": str(path), "reason": "unreadable_image"})
                continue
            image = image.convertToFormat(QImage.Format.Format_RGB888)
            key = MoonTileKey(kind, lod_level, tile_x, tile_y)
            valid_tiles[key] = path
            size_records.setdefault(level_key, []).append(
                {
                    "kind": kind,
                    "path": str(path),
                    "width": int(image.width()),
                    "height": int(image.height()),
                }
            )
    return valid_tiles, invalid_images, size_records


def _load_manifest(output_dir: Path) -> dict[str, object] | None:

    manifest_path = output_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _expected_levels_from_manifest(manifest: dict[str, object] | None) -> list[int]:

    if not manifest:
        return []
    levels = manifest.get("levels")
    if not isinstance(levels, list):
        return []
    resolved: list[int] = []
    for item in levels:
        if isinstance(item, dict) and "level" in item:
            try:
                resolved.append(int(item["level"]))
            except (TypeError, ValueError):
                continue
    return sorted(set(resolved))


def _detected_source_dimensions(manifest: dict[str, object] | None) -> dict[str, object]:

    if not manifest:
        return {"albedo": None, "normal": None}
    albedo = manifest.get("albedo") if isinstance(manifest.get("albedo"), dict) else {}
    normal = manifest.get("normal") if isinstance(manifest.get("normal"), dict) else {}
    return {
        "albedo": {
            "width": albedo.get("source_width"),
            "height": albedo.get("source_height"),
            "path": albedo.get("source_path"),
        },
        "normal": {
            "width": normal.get("source_width"),
            "height": normal.get("source_height"),
            "path": normal.get("source_path"),
            "source_kind": normal.get("source_kind"),
        },
    }


def _manifest_source_transform(manifest: dict[str, object] | None) -> dict[str, object]:

    transform = manifest.get("source_transform") if isinstance(manifest, dict) else None
    return transform if isinstance(transform, dict) else {}


def _apply_manifest_source_transform(image: QImage, manifest: dict[str, object] | None) -> QImage:

    transform = _manifest_source_transform(manifest)
    crop_value = transform.get("source_crop")
    resize_value = transform.get("source_resize")
    crop = tuple(int(value) for value in crop_value) if isinstance(crop_value, list) and len(crop_value) == 4 else None
    resize = tuple(int(value) for value in resize_value) if isinstance(resize_value, list) and len(resize_value) == 2 else None
    transformed, _metadata = _transform_source_image(
        image,
        flip_u=bool(transform.get("flip_u", False)),
        flip_v=bool(transform.get("flip_v", False)),
        longitude_shift_pixels=int(transform.get("longitude_shift_pixels_requested", 0) or 0),
        longitude_shift_degrees=float(transform.get("longitude_shift_degrees_requested", 0.0) or 0.0),
        rotate_180=bool(transform.get("rotate_180", False)),
        source_crop=crop,  # type: ignore[arg-type]
        source_resize=resize,  # type: ignore[arg-type]
    )
    return transformed


def _first_existing_tile_path(output_dir: Path, kind: str, lod_level: int, tile_x: int, tile_y: int) -> Path | None:

    tile_base = output_dir / kind / f"L{lod_level}" / f"{tile_x}_{tile_y}"
    for suffix in sorted(MoonTiledTextureManager.SUPPORTED_SUFFIXES):
        candidate = tile_base.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    return None


def _sample_image_rgb_at_uv(image: QImage, texture_u: float, texture_v: float) -> tuple[int, int, int]:

    if image.isNull():
        raise ValueError("Cannot sample a null image.")
    width = max(1, int(image.width()))
    height = max(1, int(image.height()))
    wrapped_u = float(texture_u) % 1.0
    clamped_v = max(0.0, min(1.0, float(texture_v)))
    pixel_x = min(width - 1, max(0, int(math.floor(wrapped_u * width))))
    pixel_y = min(height - 1, max(0, int(math.floor(clamped_v * height))))
    color = image.pixelColor(pixel_x, pixel_y)
    return int(color.red()), int(color.green()), int(color.blue())


def _sample_tile_tree_rgb_at_uv(
    output_dir: Path,
    lod_level: int,
    texture_u: float,
    texture_v: float,
    *,
    image_cache: dict[Path, QImage] | None = None,
) -> tuple[int, int, int]:

    columns, rows = MoonTiledTextureManager.tile_grid_dimensions(lod_level)
    wrapped_u = float(texture_u) % 1.0
    clamped_v = max(0.0, min(1.0, float(texture_v)))
    tile_x = min(columns - 1, max(0, int(math.floor(wrapped_u * columns))))
    tile_y = min(rows - 1, max(0, int(math.floor(clamped_v * rows))))
    tile_path = _first_existing_tile_path(output_dir, "albedo", lod_level, tile_x, tile_y)
    if tile_path is None:
        raise ValueError(f"Missing albedo tile L{lod_level}/{tile_x}_{tile_y} under {output_dir}")
    tile_image = image_cache.get(tile_path) if image_cache is not None else None
    if tile_image is None:
        tile_image = QImage(str(tile_path)).convertToFormat(QImage.Format.Format_RGB888)
        if image_cache is not None:
            image_cache[tile_path] = tile_image
    bounds = MoonTiledTextureManager.tile_uv_bounds(MoonTileKey("albedo", lod_level, tile_x, tile_y))
    span_u = max(1.0e-9, bounds.u_max - bounds.u_min)
    span_v = max(1.0e-9, bounds.v_max - bounds.v_min)
    local_u = max(0.0, min(1.0, (wrapped_u - bounds.u_min) / span_u))
    local_v = max(0.0, min(1.0, (clamped_v - bounds.v_min) / span_v))
    return _sample_image_rgb_at_uv(tile_image, local_u, local_v)


def _uv_validation_samples(level: int, *, random_count: int) -> list[tuple[float, float, str]]:

    rng = random.Random(8675309 + int(level))
    samples: list[tuple[float, float, str]] = [
        (0.0, 0.0, "seam_north_pole"),
        (0.0, 0.5, "u0_seam"),
        (1.0 - 1.0e-7, 0.5, "u1_seam"),
        (0.5, 0.0, "north_pole"),
        (0.5, 1.0, "south_pole"),
        (0.5, 0.5, "center"),
    ]
    columns, rows = MoonTiledTextureManager.tile_grid_dimensions(level)
    epsilon = 1.0e-5
    for tile_x in range(1, columns):
        boundary_u = tile_x / columns
        samples.extend(
            (
                (boundary_u - epsilon, 0.5, "u_boundary_left"),
                (boundary_u, 0.5, "u_boundary_exact"),
                (boundary_u + epsilon, 0.5, "u_boundary_right"),
            )
        )
    for tile_y in range(1, rows):
        boundary_v = tile_y / rows
        samples.extend(
            (
                (0.5, boundary_v - epsilon, "v_boundary_above"),
                (0.5, boundary_v, "v_boundary_exact"),
                (0.5, boundary_v + epsilon, "v_boundary_below"),
            )
        )
    for _index in range(max(0, int(random_count))):
        samples.append((rng.random(), rng.random(), "random"))
    return samples


def validate_tile_uv_sampling(
    project_root: Path,
    *,
    output_dir: str | Path | None = None,
    source_image_path: str | Path | None = None,
    levels: list[int] | None = None,
    samples_per_level: int = 96,
    tolerance: int = 3,
) -> dict[str, object]:

    paths = default_moon_tile_paths(Path(project_root))
    resolved_root = paths["project_root"]
    resolved_output_dir = _resolve_path(resolved_root, output_dir, paths["output_dir"])
    if resolved_output_dir is None:
        raise ValueError("Output directory could not be resolved.")
    manifest = _load_manifest(resolved_output_dir)
    source_path = _resolve_path(resolved_root, source_image_path)
    if source_path is None and isinstance(manifest, dict) and isinstance(manifest.get("albedo"), dict):
        source_path = _resolve_path(resolved_root, manifest["albedo"].get("source_path"))
    if source_path is None or not source_path.is_file():
        return {"status": "missing_source", "source_path": str(source_path), "max_delta": None, "failed_sample_count": 0}
    source_image = QImage(str(source_path)).convertToFormat(QImage.Format.Format_RGB888)
    if source_image.isNull():
        return {"status": "unreadable_source", "source_path": str(source_path), "max_delta": None, "failed_sample_count": 0}
    source_image = _apply_manifest_source_transform(source_image, manifest)
    if levels is None:
        levels = _expected_levels_from_manifest(manifest)
        if not levels:
            levels = sorted(
                int(path.name[1:])
                for path in (resolved_output_dir / "albedo").iterdir()
                if path.is_dir() and path.name.startswith("L") and path.name[1:].isdigit()
            ) if (resolved_output_dir / "albedo").is_dir() else []

    max_delta = 0
    failed_samples: list[dict[str, object]] = []
    tested_sample_count = 0
    image_cache: dict[Path, QImage] = {}
    for level in sorted(set(int(level) for level in levels)):
        columns, rows = MoonTiledTextureManager.tile_grid_dimensions(level)
        tile_path = _first_existing_tile_path(resolved_output_dir, "albedo", level, 0, 0)
        if tile_path is None:
            failed_samples.append({"level": level, "label": "missing_level", "delta": None})
            continue
        tile_size_image = QImage(str(tile_path))
        tile_width = max(1, int(tile_size_image.width()))
        tile_height = max(1, int(tile_size_image.height()))
        reference_image = _scaled_level_image(source_image, columns * tile_width, rows * tile_height).convertToFormat(QImage.Format.Format_RGB888)
        for texture_u, texture_v, label in _uv_validation_samples(level, random_count=samples_per_level):
            expected = _sample_image_rgb_at_uv(reference_image, texture_u, texture_v)
            try:
                actual = _sample_tile_tree_rgb_at_uv(resolved_output_dir, level, texture_u, texture_v, image_cache=image_cache)
            except ValueError as exc:
                failed_samples.append(
                    {
                        "level": level,
                        "u": float(texture_u),
                        "v": float(texture_v),
                        "label": label,
                        "error": str(exc),
                    }
                )
                if len(failed_samples) >= 24:
                    break
                continue
            delta = max(abs(expected[channel] - actual[channel]) for channel in range(3))
            max_delta = max(max_delta, delta)
            tested_sample_count += 1
            if delta > int(tolerance):
                failed_samples.append(
                    {
                        "level": level,
                        "u": float(texture_u),
                        "v": float(texture_v),
                        "label": label,
                        "expected_rgb": list(expected),
                        "actual_rgb": list(actual),
                        "delta": int(delta),
                    }
                )
                if len(failed_samples) >= 24:
                    break
        if len(failed_samples) >= 24:
            break
    return {
        "status": "pass" if not failed_samples else "fail",
        "source_path": str(source_path),
        "output_dir": str(resolved_output_dir),
        "levels": [int(level) for level in sorted(set(levels))],
        "tested_sample_count": int(tested_sample_count),
        "failed_sample_count": int(len(failed_samples)),
        "max_delta": int(max_delta),
        "tolerance": int(tolerance),
        "failed_samples": failed_samples,
    }


def _registration_uv_samples(samples_per_axis: int) -> list[tuple[float, float]]:

    grid_size = max(4, int(samples_per_axis))
    samples = [
        ((x_index + 0.5) / grid_size, (y_index + 0.5) / max(2, grid_size // 2))
        for y_index in range(max(2, grid_size // 2))
        for x_index in range(grid_size)
    ]
    samples.extend(
        _landmark_uv(float(landmark["longitude_deg"]), float(landmark["latitude_deg"]))
        for landmark in LUNAR_LANDMARK_UVS
    )
    return samples


def _color_delta(expected: tuple[int, int, int], actual: tuple[int, int, int]) -> float:

    return sum(abs(expected[index] - actual[index]) for index in range(3)) / (3.0 * 255.0)


def _luminance(rgb: tuple[int, int, int]) -> float:

    return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255.0


def _registration_metrics(
    reference_image: QImage,
    output_dir: Path,
    lod_level: int,
    samples: list[tuple[float, float]],
    *,
    tile_u_shift: float = 0.0,
    tile_flip_u: bool = False,
    tile_flip_v: bool = False,
    image_cache: dict[Path, QImage] | None = None,
) -> dict[str, float]:

    color_deltas: list[float] = []
    luminance_deltas: list[float] = []
    for texture_u, texture_v in samples:
        expected = _sample_image_rgb_at_uv(reference_image, texture_u, texture_v)
        candidate_u = (1.0 - texture_u if tile_flip_u else texture_u) + float(tile_u_shift)
        candidate_v = 1.0 - texture_v if tile_flip_v else texture_v
        actual = _sample_tile_tree_rgb_at_uv(
            output_dir,
            lod_level,
            candidate_u,
            candidate_v,
            image_cache=image_cache,
        )
        color_deltas.append(_color_delta(expected, actual))
        luminance_deltas.append(abs(_luminance(expected) - _luminance(actual)))
    return {
        "mean_color_delta": float(sum(color_deltas) / max(1, len(color_deltas))),
        "max_color_delta": float(max(color_deltas, default=0.0)),
        "mean_luminance_delta": float(sum(luminance_deltas) / max(1, len(luminance_deltas))),
        "max_luminance_delta": float(max(luminance_deltas, default=0.0)),
    }


def _landmark_registration_diagnostics(
    reference_image: QImage,
    output_dir: Path,
    lod_level: int,
    *,
    image_cache: dict[Path, QImage],
) -> list[dict[str, object]]:

    patch_offsets = (-0.004, 0.0, 0.004)
    search_step_deg = 1.0
    search_steps = range(-6, 7)
    reports: list[dict[str, object]] = []
    for landmark in LUNAR_LANDMARK_UVS:
        expected_u, expected_v = _landmark_uv(float(landmark["longitude_deg"]), float(landmark["latitude_deg"]))
        reference_patch = [
            _sample_image_rgb_at_uv(reference_image, expected_u + delta_u, expected_v + delta_v)
            for delta_v in patch_offsets
            for delta_u in patch_offsets
        ]
        best_score = float("inf")
        best_distance = float("inf")
        best_uv = (expected_u, expected_v)
        for offset_v_steps in search_steps:
            for offset_u_steps in search_steps:
                candidate_u = expected_u + float(offset_u_steps) * search_step_deg / 360.0
                candidate_v = expected_v - float(offset_v_steps) * search_step_deg / 180.0
                candidate_patch = [
                    _sample_tile_tree_rgb_at_uv(
                        output_dir,
                        lod_level,
                        candidate_u + delta_u,
                        candidate_v + delta_v,
                        image_cache=image_cache,
                    )
                    for delta_v in patch_offsets
                    for delta_u in patch_offsets
                ]
                score = sum(_color_delta(reference_patch[index], candidate_patch[index]) for index in range(len(reference_patch))) / len(reference_patch)
                distance = abs(float(offset_u_steps)) + abs(float(offset_v_steps))
                if score < best_score - 1.0e-12 or (abs(score - best_score) <= 1.0e-12 and distance < best_distance):
                    best_score = score
                    best_distance = distance
                    best_uv = (candidate_u % 1.0, max(0.0, min(1.0, candidate_v)))
        longitude_offset_deg = ((best_uv[0] - expected_u + 0.5) % 1.0 - 0.5) * 360.0
        latitude_offset_deg = (expected_v - best_uv[1]) * 180.0
        reports.append(
            {
                "name": str(landmark["name"]),
                "longitude_deg": float(landmark["longitude_deg"]),
                "latitude_deg": float(landmark["latitude_deg"]),
                "expected_global_uv": [float(expected_u), float(expected_v)],
                "best_tiled_uv": [float(best_uv[0]), float(best_uv[1])],
                "longitude_offset_deg": float(longitude_offset_deg),
                "latitude_offset_deg": float(latitude_offset_deg),
                "patch_mean_color_delta": float(best_score),
            }
        )
    return reports


def validate_global_vs_tiled_registration(
    project_root: Path,
    *,
    output_dir: str | Path | None = None,
    global_albedo_source: str | Path | None = None,
    level: int | None = None,
    samples_per_axis: int = 16,
    max_mean_color_delta: float = 0.015,
    max_peak_color_delta: float = 0.06,
    max_mean_luminance_delta: float = 0.015,
) -> dict[str, object]:

    paths = default_moon_tile_paths(Path(project_root))
    resolved_root = paths["project_root"]
    resolved_output_dir = _resolve_path(resolved_root, output_dir, paths["output_dir"])
    if resolved_output_dir is None:
        raise ValueError("Output directory could not be resolved.")
    manifest = _load_manifest(resolved_output_dir)
    registration = manifest.get("registration") if isinstance(manifest, dict) else None
    manifest_global_path = registration.get("global_source_path") if isinstance(registration, dict) else None
    resolved_global_source = _resolve_path(
        resolved_root,
        global_albedo_source if global_albedo_source is not None else manifest_global_path,
        paths["albedo_source"],
    )
    base_report: dict[str, object] = {
        "status": "missing_global_source",
        "registered": False,
        "global_source_path": str(resolved_global_source) if resolved_global_source is not None else None,
        "global_source_sha256": _sha256_file(resolved_global_source),
        "tile_source_sha256": (
            manifest.get("albedo", {}).get("source_sha256")
            if isinstance(manifest, dict) and isinstance(manifest.get("albedo"), dict)
            else None
        ),
        "level": None,
        "tested_sample_count": 0,
        "mean_color_delta": None,
        "max_color_delta": None,
        "mean_luminance_delta": None,
        "max_luminance_delta": None,
        "detected_longitude_shift_degrees": None,
        "possible_u_flip_detected": False,
        "possible_v_flip_detected": False,
        "landmarks": [],
    }
    if resolved_global_source is None or not resolved_global_source.is_file():
        return base_report
    global_image = QImage(str(resolved_global_source)).convertToFormat(QImage.Format.Format_RGB888)
    if global_image.isNull():
        base_report["status"] = "unreadable_global_source"
        return base_report
    levels = _expected_levels_from_manifest(manifest)
    if level is None:
        level = max(levels) if levels else None
    if level is None:
        base_report["status"] = "missing_tile_level"
        return base_report
    first_tile_path = _first_existing_tile_path(resolved_output_dir, "albedo", int(level), 0, 0)
    if first_tile_path is None:
        base_report["status"] = "missing_tile_level"
        return base_report
    first_tile = QImage(str(first_tile_path))
    if first_tile.isNull():
        base_report["status"] = "unreadable_tile"
        return base_report
    columns, rows = MoonTiledTextureManager.tile_grid_dimensions(int(level))
    reference_image = _scaled_level_image(global_image, columns * int(first_tile.width()), rows * int(first_tile.height())).convertToFormat(QImage.Format.Format_RGB888)
    samples = _registration_uv_samples(samples_per_axis)
    image_cache: dict[Path, QImage] = {}
    try:
        identity_metrics = _registration_metrics(reference_image, resolved_output_dir, int(level), samples, image_cache=image_cache)
    except ValueError:
        base_report["status"] = "missing_tile_sample"
        return base_report
    candidate_reports: list[tuple[float, bool, bool, dict[str, float]]] = []
    for flip_u in (False, True):
        for flip_v in (False, True):
            for shift_deg in range(-180, 180, 10):
                metrics = _registration_metrics(
                    reference_image,
                    resolved_output_dir,
                    int(level),
                    samples,
                    tile_u_shift=float(shift_deg) / 360.0,
                    tile_flip_u=flip_u,
                    tile_flip_v=flip_v,
                    image_cache=image_cache,
                )
                candidate_reports.append((float(shift_deg), flip_u, flip_v, metrics))
    coarse_best = min(candidate_reports, key=lambda item: (item[3]["mean_color_delta"], item[3]["mean_luminance_delta"]))
    refined_reports: list[tuple[float, bool, bool, dict[str, float]]] = []
    for quarter_step in range(-20, 21):
        shift_deg = coarse_best[0] + float(quarter_step) * 0.5
        metrics = _registration_metrics(
            reference_image,
            resolved_output_dir,
            int(level),
            samples,
            tile_u_shift=shift_deg / 360.0,
            tile_flip_u=coarse_best[1],
            tile_flip_v=coarse_best[2],
            image_cache=image_cache,
        )
        refined_reports.append((shift_deg, coarse_best[1], coarse_best[2], metrics))
    best_shift_deg, best_flip_u, best_flip_v, best_metrics = min(
        refined_reports,
        key=lambda item: (item[3]["mean_color_delta"], item[3]["mean_luminance_delta"]),
    )
    normalized_shift_deg = ((float(best_shift_deg) + 180.0) % 360.0) - 180.0
    identity_score = identity_metrics["mean_color_delta"] + identity_metrics["mean_luminance_delta"]
    best_score = best_metrics["mean_color_delta"] + best_metrics["mean_luminance_delta"]
    transform_is_material = best_score + 0.002 < identity_score
    registered = (
        identity_metrics["mean_color_delta"] <= float(max_mean_color_delta)
        and identity_metrics["max_color_delta"] <= float(max_peak_color_delta)
        and identity_metrics["mean_luminance_delta"] <= float(max_mean_luminance_delta)
    )
    return {
        **base_report,
        "status": "pass" if registered else "fail",
        "registered": bool(registered),
        "level": int(level),
        "tested_sample_count": int(len(samples)),
        **identity_metrics,
        "best_fit_metrics": best_metrics,
        "detected_longitude_shift_degrees": float(normalized_shift_deg) if transform_is_material else 0.0,
        "possible_u_flip_detected": bool(best_flip_u and transform_is_material),
        "possible_v_flip_detected": bool(best_flip_v and transform_is_material),
        "landmarks": _landmark_registration_diagnostics(reference_image, resolved_output_dir, int(level), image_cache=image_cache),
        "tolerances": {
            "max_mean_color_delta": float(max_mean_color_delta),
            "max_peak_color_delta": float(max_peak_color_delta),
            "max_mean_luminance_delta": float(max_mean_luminance_delta),
        },
    }


def validate_polar_cap_assets(
    project_root: Path,
    *,
    output_dir: str | Path | None = None,
    tolerance: int = 2,
) -> dict[str, object]:

    paths = default_moon_tile_paths(Path(project_root))
    resolved_root = paths["project_root"]
    resolved_output_dir = _resolve_path(resolved_root, output_dir, paths["output_dir"])
    if resolved_output_dir is None:
        raise ValueError("Output directory could not be resolved.")
    manifest = _load_manifest(resolved_output_dir)
    cap_manifest = manifest.get("polar_caps") if isinstance(manifest, dict) else None
    if not isinstance(cap_manifest, dict) or not bool(cap_manifest.get("enabled", False)):
        return {"status": "missing", "available": False, "caps": {}, "fallback": "phase5l_polar_guard"}
    albedo_manifest = manifest.get("albedo") if isinstance(manifest.get("albedo"), dict) else {}
    source_path = _resolve_path(resolved_root, albedo_manifest.get("source_path"))
    if source_path is None or not source_path.is_file():
        return {"status": "missing_source", "available": False, "caps": {}, "fallback": "phase5l_polar_guard"}
    source_image = _apply_manifest_source_transform(_load_image(source_path, label="polar-cap source"), manifest)
    texture_size = max(2, int(cap_manifest.get("texture_size", 0) or 0))
    coverage_deg = float(cap_manifest.get("latitude_coverage_deg", 0.0) or 0.0)
    blend_start_deg = float(cap_manifest.get("blend_start_lat_deg", 60.0) or 60.0)
    blend_end_deg = float(cap_manifest.get("blend_end_lat_deg", 78.0) or 78.0)
    projection = str(cap_manifest.get("projection") or "")
    boundary_normalization = cap_manifest.get("boundary_normalization") if isinstance(cap_manifest.get("boundary_normalization"), dict) else {}
    appearance_matching = cap_manifest.get("appearance_matching") if isinstance(cap_manifest.get("appearance_matching"), dict) else {}
    caps = cap_manifest.get("caps") if isinstance(cap_manifest.get("caps"), dict) else {}
    cap_reports: dict[str, object] = {}
    maximum_delta = 0
    boundary_mean_color_delta = 0.0
    boundary_mean_luminance_delta = 0.0
    boundary_mean_contrast_delta = 0.0
    minimum_core_sharpness = float("inf")
    softest_detail_delta = 0.0
    footprint_risk = False
    all_valid = projection == POLAR_CAP_PROJECTION
    probe_pixels = (
        (0.5, 0.5, "pole_center"),
        (0.5, 0.20, "inner_meridian"),
        (0.75, 0.5, "inner_equator"),
        (0.5, 0.08, "blend_side"),
    )
    for pole in ("north", "south"):
        item = caps.get(pole) if isinstance(caps.get(pole), dict) else {}
        relative_path = item.get("albedo_path") if isinstance(item, dict) else None
        cap_path = resolved_output_dir / str(relative_path) if relative_path else None
        cap_image = QImage(str(cap_path)).convertToFormat(QImage.Format.Format_RGB888) if cap_path is not None else QImage()
        expected_image, expected_reference_image, expected_boundary_report, expected_appearance_report, expected_regional_metrics = _build_polar_cap_albedo_image(
            source_image,
            pole=pole,
            texture_size=texture_size,
            latitude_coverage_deg=coverage_deg,
            blend_start_lat_deg=blend_start_deg,
            blend_end_lat_deg=blend_end_deg,
            boundary_match_enabled=bool(boundary_normalization.get("enabled", False)),
            boundary_match_strength=float(boundary_normalization.get("strength", 0.0) or 0.0),
            albedo_sharpness=float(appearance_matching.get("albedo_sharpness", 0.0) or 0.0),
            local_contrast=float(appearance_matching.get("local_contrast", 0.0) or 0.0),
            core_match_strength=float(appearance_matching.get("core_match_strength", 0.0) or 0.0),
        )
        boundary_metrics = (
            _polar_cap_boundary_metrics(
                source_image,
                cap_image,
                pole=pole,
                latitude_deg=blend_start_deg,
                latitude_coverage_deg=coverage_deg,
            )
            if not cap_image.isNull()
            else {}
        )
        boundary_mean_color_delta = max(boundary_mean_color_delta, float(boundary_metrics.get("mean_color_delta", 1.0)))
        boundary_mean_luminance_delta = max(boundary_mean_luminance_delta, float(boundary_metrics.get("mean_luminance_delta", 1.0)))
        boundary_mean_contrast_delta = max(boundary_mean_contrast_delta, float(boundary_metrics.get("contrast_delta", 1.0)))
        footprint_metrics = (
            _polar_cap_relative_footprint_metrics(
                expected_reference_image,
                cap_image,
                pole=pole,
                latitude_coverage_deg=coverage_deg,
                blend_start_lat_deg=blend_start_deg,
                blend_end_lat_deg=blend_end_deg,
            )
            if not cap_image.isNull()
            else {}
        )
        regional_metrics = (
            _polar_cap_regional_metrics(
                source_image,
                cap_image,
                pole=pole,
                latitude_coverage_deg=coverage_deg,
                blend_start_lat_deg=blend_start_deg,
                blend_end_lat_deg=blend_end_deg,
            )
            if not cap_image.isNull()
            else expected_regional_metrics
        )
        minimum_core_sharpness = min(minimum_core_sharpness, float(footprint_metrics.get("core_sharpness_estimate", 0.0)))
        softest_detail_delta = min(
            softest_detail_delta,
            float(footprint_metrics.get("core_vs_surrounding_detail_delta", 0.0)),
        )
        footprint_risk = footprint_risk or bool(
            float(boundary_metrics.get("contrast_delta", 0.0)) > 0.18
            or float(footprint_metrics.get("core_vs_surrounding_detail_delta", 0.0)) < -0.16
        )
        sample_reports: list[dict[str, object]] = []
        cap_valid = (
            not cap_image.isNull()
            and int(cap_image.width()) == texture_size
            and int(cap_image.height()) == texture_size
            and str(item.get("pole") or "") == pole
            and str(item.get("projection") or "") == POLAR_CAP_PROJECTION
        )
        for sample_u, sample_v, label in probe_pixels:
            expected = _sample_image_rgb_at_uv(expected_image, sample_u, sample_v)
            actual = _sample_image_rgb_at_uv(cap_image, sample_u, sample_v) if not cap_image.isNull() else (0, 0, 0)
            delta = max(abs(expected[channel] - actual[channel]) for channel in range(3))
            maximum_delta = max(maximum_delta, delta)
            sample_reports.append({"label": label, "uv": [sample_u, sample_v], "delta": int(delta)})
            cap_valid = cap_valid and delta <= int(tolerance)
        cap_reports[pole] = {
            "pole": pole,
            "albedo_path": str(cap_path) if cap_path is not None else None,
            "normal_path": item.get("normal_path") if isinstance(item, dict) else None,
            "finite_center": not cap_image.isNull(),
            "in_bounds_sample_count": len(probe_pixels),
            "out_of_bounds_sample_count": 0,
            "status": "pass" if cap_valid else "fail",
            "samples": sample_reports,
            "boundary_metrics": boundary_metrics,
            "boundary_normalization": item.get("boundary_normalization") if isinstance(item, dict) else None,
            "appearance_harmonization": item.get("appearance_harmonization") if isinstance(item, dict) else None,
            "regional_metrics": regional_metrics,
            "expected_regional_metrics": expected_regional_metrics,
            "expected_boundary_normalization": expected_boundary_report,
            "expected_appearance_harmonization": expected_appearance_report,
            "footprint_metrics": footprint_metrics,
            "footprint_risk": bool(
                float(boundary_metrics.get("contrast_delta", 0.0)) > 0.18
                or float(footprint_metrics.get("core_vs_surrounding_detail_delta", 0.0)) < -0.16
            ),
        }
        all_valid = all_valid and cap_valid
    return {
        "status": "pass" if all_valid else "fail",
        "available": bool(all_valid),
        "projection": projection,
        "texture_size": int(texture_size),
        "latitude_coverage_deg": float(coverage_deg),
        "blend_start_lat_deg": float(blend_start_deg),
        "blend_end_lat_deg": float(blend_end_deg),
        "max_color_delta": int(maximum_delta),
        "boundary_mean_color_delta": float(boundary_mean_color_delta),
        "boundary_mean_luminance_delta": float(boundary_mean_luminance_delta),
        "boundary_mean_contrast_delta": float(boundary_mean_contrast_delta),
        "core_sharpness_estimate": 0.0 if minimum_core_sharpness == float("inf") else float(minimum_core_sharpness),
        "core_vs_surrounding_detail_delta": float(softest_detail_delta),
        "footprint_risk": bool(footprint_risk or texture_size < 1024),
        "boundary_deltas_bounded": bool(
            boundary_mean_color_delta <= 0.02
            and boundary_mean_luminance_delta <= 0.02
            and boundary_mean_contrast_delta <= 0.18
        ),
        "pole_center_finite": bool(all(item["finite_center"] for item in cap_reports.values())),
        "no_nan_uvs": True,
        "out_of_bounds_sample_count": 0,
        "caps": cap_reports,
        "fallback": "" if all_valid else "phase5l_polar_guard",
    }


def audit_moon_texture_sources(project_root: Path, *, output_dir: str | Path | None = None) -> dict[str, object]:

    paths = default_moon_tile_paths(Path(project_root))
    resolved_root = paths["project_root"]
    resolved_output_dir = _resolve_path(resolved_root, output_dir, paths["output_dir"])
    if resolved_output_dir is None:
        raise ValueError("Output directory could not be resolved.")
    manifest = _load_manifest(resolved_output_dir)

    def source_record(role: str, path: Path | None, assumption: str) -> dict[str, object]:

        image = QImage(str(path)) if path is not None and path.is_file() else QImage()
        return {
            "role": role,
            "filename": path.name if path is not None else None,
            "path": str(path) if path is not None else None,
            "sha256": _sha256_file(path),
            "width": int(image.width()) if not image.isNull() else None,
            "height": int(image.height()) if not image.isNull() else None,
            "orientation_assumption": assumption,
            "uv_convention": CANONICAL_MOON_CONVENTION_ID,
        }

    manifest_albedo = manifest.get("albedo") if isinstance(manifest, dict) and isinstance(manifest.get("albedo"), dict) else {}
    manifest_normal = manifest.get("normal") if isinstance(manifest, dict) and isinstance(manifest.get("normal"), dict) else {}
    real_tile_albedo_source = _resolve_path(resolved_root, manifest_albedo.get("source_path"))
    real_tile_normal_source = _resolve_path(resolved_root, manifest_normal.get("source_path"))
    synthetic_dir = resolved_root / "assets" / "moon_tiles_synthetic"
    return {
        "canonical_convention": dict(CANONICAL_MOON_CONVENTION),
        "sources": [
            source_record("active_global_albedo", paths["albedo_source"], "row 0 is north; mirrored only at OpenGL upload"),
            source_record("active_global_normal_height", paths["displacement_source"], "height-derived normals use global Moon UV grid"),
            source_record("real_tiled_albedo_source", real_tile_albedo_source, "declared transforms are applied before tile split"),
            source_record("real_tiled_normal_or_ldem_source", real_tile_normal_source, "same declared transforms as real albedo"),
            source_record(
                "synthetic_global_and_tiled_albedo",
                synthetic_dir / SYNTHETIC_SOURCE_DIRNAME / "synthetic_moon_uv_grid.png",
                "UV reference image; row 0 is north",
            ),
            source_record(
                "synthetic_global_and_tiled_normal",
                synthetic_dir / SYNTHETIC_SOURCE_DIRNAME / "synthetic_moon_flat_normal.png",
                "flat tangent-space normal in canonical UV grid",
            ),
        ],
        "generated_intermediates": {
            "real_tile_output_dir": str(resolved_output_dir),
            "real_manifest_path": str(resolved_output_dir / MANIFEST_FILENAME),
            "real_manifest_present": manifest is not None,
            "real_polar_cap_albedo_paths": [
                str(resolved_output_dir / "polar_caps" / "albedo" / f"{pole}.png")
                for pole in ("north", "south")
            ],
            "real_polar_cap_normal_paths": [
                str(resolved_output_dir / "polar_caps" / "normal" / f"{pole}.png")
                for pole in ("north", "south")
            ],
            "synthetic_tile_output_dir": str(synthetic_dir),
            "synthetic_manifest_path": str(synthetic_dir / MANIFEST_FILENAME),
            "synthetic_manifest_present": (synthetic_dir / MANIFEST_FILENAME).is_file(),
            "runtime_cached_or_resized_versions": [
                f"global albedo runtime LOD {width}w (generated in memory)" for width in MoonCache.DEFAULT_LOD_WIDTHS
            ]
            + [f"global normal runtime LOD {width}w (generated in memory)" for width in MoonCache.DEFAULT_LOD_WIDTHS],
        },
        "manifest_source_transform": _manifest_source_transform(manifest),
    }


def _image_luminance_samples(image: QImage, *, step: int) -> list[float]:

    converted = image.convertToFormat(QImage.Format.Format_RGB888)
    width = max(1, int(converted.width()))
    height = max(1, int(converted.height()))
    stride = max(1, int(step))
    samples: list[float] = []
    for y in range(0, height, stride):
        for x in range(0, width, stride):
            color = converted.pixelColor(x, y)
            samples.append((0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()) / 255.0)
    return samples


def validate_tile_lod_luminance_consistency(
    project_root: Path,
    *,
    output_dir: str | Path | None = None,
    max_mean_jump: float = 0.08,
) -> dict[str, object]:

    paths = default_moon_tile_paths(Path(project_root))
    resolved_root = paths["project_root"]
    resolved_output_dir = _resolve_path(resolved_root, output_dir, paths["output_dir"])
    if resolved_output_dir is None:
        raise ValueError("Output directory could not be resolved.")
    manifest = _load_manifest(resolved_output_dir)
    levels = _expected_levels_from_manifest(manifest)
    level_reports: list[dict[str, object]] = []
    for level in levels:
        samples: list[float] = []
        columns, rows = MoonTiledTextureManager.tile_grid_dimensions(level)
        for tile_y in range(rows):
            for tile_x in range(columns):
                tile_path = _first_existing_tile_path(resolved_output_dir, "albedo", level, tile_x, tile_y)
                if tile_path is None:
                    continue
                image = QImage(str(tile_path))
                if image.isNull():
                    continue
                samples.extend(_image_luminance_samples(image, step=max(1, int(image.width()) // 16)))
        if samples:
            level_reports.append(
                {
                    "level": int(level),
                    "mean_luminance": float(sum(samples) / len(samples)),
                    "median_luminance": float(statistics.median(samples)),
                    "sample_count": int(len(samples)),
                }
            )
    jumps = [
        abs(float(level_reports[index]["mean_luminance"]) - float(level_reports[index - 1]["mean_luminance"]))
        for index in range(1, len(level_reports))
    ]
    max_jump = max(jumps) if jumps else 0.0
    return {
        "status": "pass" if max_jump <= float(max_mean_jump) and bool(level_reports) else "fail",
        "max_mean_luminance_jump": float(max_jump),
        "max_allowed_mean_luminance_jump": float(max_mean_jump),
        "levels": level_reports,
    }


def validate_tiled_normal_maps(
    project_root: Path,
    *,
    output_dir: str | Path | None = None,
    expected_flat: bool = False,
) -> dict[str, object]:

    paths = default_moon_tile_paths(Path(project_root))
    resolved_root = paths["project_root"]
    resolved_output_dir = _resolve_path(resolved_root, output_dir, paths["output_dir"])
    if resolved_output_dir is None:
        raise ValueError("Output directory could not be resolved.")
    normal_root = resolved_output_dir / "normal"
    if not normal_root.is_dir():
        return {"status": "missing", "normal_tile_count": 0, "neutral_mean_delta": None, "invalid_images": []}
    invalid_images: list[str] = []
    samples: list[tuple[int, int, int]] = []
    for path in sorted(normal_root.rglob("*")):
        if not path.is_file() or path.suffix.casefold() not in MoonTiledTextureManager.SUPPORTED_SUFFIXES:
            continue
        image = QImage(str(path)).convertToFormat(QImage.Format.Format_RGB888)
        if image.isNull():
            invalid_images.append(str(path))
            continue
        step = max(1, int(image.width()) // 12)
        for y in range(0, int(image.height()), step):
            for x in range(0, int(image.width()), step):
                color = image.pixelColor(x, y)
                samples.append((int(color.red()), int(color.green()), int(color.blue())))
    if not samples:
        return {"status": "missing", "normal_tile_count": 0, "neutral_mean_delta": None, "invalid_images": invalid_images}
    mean_rgb = tuple(sum(sample[channel] for sample in samples) / len(samples) for channel in range(3))
    neutral_delta = max(abs(mean_rgb[0] - 128.0), abs(mean_rgb[1] - 128.0), abs(mean_rgb[2] - 255.0))
    xy_span = max(
        max(sample[0] for sample in samples) - min(sample[0] for sample in samples),
        max(sample[1] for sample in samples) - min(sample[1] for sample in samples),
    )
    z_min = min(sample[2] for sample in samples)
    if expected_flat:
        status = "pass" if neutral_delta <= 2.0 and xy_span <= 4 and not invalid_images else "fail"
    else:
        status = "pass" if z_min >= 160 and not invalid_images else "fail"
    return {
        "status": status,
        "normal_tile_count": int(len({path.parent.name + '/' + path.name for path in normal_root.rglob('*') if path.is_file()})),
        "sample_count": int(len(samples)),
        "mean_rgb": [float(value) for value in mean_rgb],
        "neutral_mean_delta": float(neutral_delta),
        "xy_channel_span": int(xy_span),
        "z_min": int(z_min),
        "invalid_images": invalid_images,
    }


def validate_moon_tile_tree(project_root: Path, *, output_dir: str | Path | None = None) -> dict[str, object]:

    paths = default_moon_tile_paths(Path(project_root))
    resolved_root = paths["project_root"]
    resolved_output_dir = _resolve_path(resolved_root, output_dir, paths["output_dir"])
    if resolved_output_dir is None:
        raise ValueError("Output directory could not be resolved.")

    manifest = _load_manifest(resolved_output_dir)
    albedo_root = resolved_output_dir / "albedo"
    normal_root = resolved_output_dir / "normal"
    albedo_tiles, invalid_albedo, albedo_sizes = _collect_tile_files(albedo_root, "albedo")
    normal_tiles, invalid_normal, normal_sizes = _collect_tile_files(normal_root, "normal")
    invalid_images = invalid_albedo + invalid_normal

    detected_levels = sorted(
        set(_expected_levels_from_manifest(manifest))
        | {key.lod_level for key in albedo_tiles}
        | {key.lod_level for key in normal_tiles}
    )
    missing_albedo_tiles: list[str] = []
    missing_normal_pairs: list[str] = []
    for level in detected_levels:
        columns, rows = MoonTiledTextureManager.tile_grid_dimensions(level)
        for tile_y in range(rows):
            for tile_x in range(columns):
                albedo_key = MoonTileKey("albedo", level, tile_x, tile_y)
                if albedo_key not in albedo_tiles:
                    missing_albedo_tiles.append(f"albedo:L{level}:{tile_x}_{tile_y}")
                    continue
                normal_key = MoonTileKey("normal", level, tile_x, tile_y)
                normal_expected = bool(normal_tiles) or (
                    isinstance(manifest, dict)
                    and isinstance(manifest.get("normal"), dict)
                    and str(manifest["normal"].get("source_kind")) in {"normal", "height"}
                )
                if normal_expected and normal_key not in normal_tiles:
                    missing_normal_pairs.append(f"normal:L{level}:{tile_x}_{tile_y}")

    inconsistent_tile_sizes: list[dict[str, object]] = []
    manifest_tile_size = None
    if isinstance(manifest, dict):
        try:
            manifest_tile_size = int(manifest.get("tile_size"))
        except (TypeError, ValueError):
            manifest_tile_size = None
    size_records: dict[str, list[dict[str, object]]] = {}
    for source_records in (albedo_sizes, normal_sizes):
        for level_key, records in source_records.items():
            size_records.setdefault(level_key, []).extend(records)
    for level_key, records in sorted(size_records.items()):
        for record in records:
            expected_width = manifest_tile_size
            expected_height = manifest_tile_size
            if manifest_tile_size is not None and (
                int(record["width"]) != expected_width or int(record["height"]) != expected_height
            ):
                inconsistent_tile_sizes.append(
                    {
                        "path": record["path"],
                        "kind": record["kind"],
                        "level": level_key,
                        "width": record["width"],
                        "height": record["height"],
                        "expected_width": expected_width,
                        "expected_height": expected_height,
                    }
                )

    estimated_disk_size_bytes = 0
    for root in (albedo_root, normal_root):
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.casefold() in MoonTiledTextureManager.SUPPORTED_SUFFIXES:
                estimated_disk_size_bytes += int(path.stat().st_size)

    manager = MoonTiledTextureManager(project_root=resolved_root, albedo_tile_root=albedo_root, normal_tile_root=normal_root)
    try:
        manager_albedo_keys = manager._scan_tile_root(albedo_root, "albedo")
        manager_normal_keys = manager._scan_tile_root(normal_root, "normal")
    finally:
        manager.shutdown()

    expected_uv_layout = _uv_layout_summary(max(detected_levels) if detected_levels else 0)
    manager_usable = (
        bool(manager_albedo_keys)
        and not invalid_images
        and not missing_albedo_tiles
        and not inconsistent_tile_sizes
        and not missing_normal_pairs
    )
    tile_uv_validation = validate_tile_uv_sampling(resolved_root, output_dir=resolved_output_dir, samples_per_level=24, tolerance=3)
    tile_lod_luminance_consistency = validate_tile_lod_luminance_consistency(resolved_root, output_dir=resolved_output_dir)
    tiled_normal_validation = validate_tiled_normal_maps(resolved_root, output_dir=resolved_output_dir)
    global_vs_tiled_registration = validate_global_vs_tiled_registration(resolved_root, output_dir=resolved_output_dir)
    polar_cap_validation = validate_polar_cap_assets(resolved_root, output_dir=resolved_output_dir)
    return {
        "output_dir": str(resolved_output_dir),
        "manifest_path": str(resolved_output_dir / MANIFEST_FILENAME),
        "manifest_present": manifest is not None,
        "detected_source_dimensions": _detected_source_dimensions(manifest),
        "levels": {
            f"L{level}": {
                "columns": MoonTiledTextureManager.tile_grid_dimensions(level)[0],
                "rows": MoonTiledTextureManager.tile_grid_dimensions(level)[1],
                "albedo_count": sum(1 for key in albedo_tiles if key.lod_level == level),
                "normal_count": sum(1 for key in normal_tiles if key.lod_level == level),
            }
            for level in detected_levels
        },
        "missing_albedo_tiles": missing_albedo_tiles,
        "missing_normal_pairs": missing_normal_pairs,
        "invalid_images": invalid_images,
        "inconsistent_tile_sizes": inconsistent_tile_sizes,
        "estimated_disk_size_bytes": int(estimated_disk_size_bytes),
        "expected_uv_layout": expected_uv_layout,
        "tile_uv_validation": tile_uv_validation,
        "tile_lod_luminance_consistency": tile_lod_luminance_consistency,
        "tiled_normal_validation": tiled_normal_validation,
        "global_vs_tiled_registration": global_vs_tiled_registration,
        "polar_cap_validation": polar_cap_validation,
        "source_audit": audit_moon_texture_sources(resolved_root, output_dir=resolved_output_dir),
        "manager_compatibility": {
            "usable": bool(
                manager_usable
                and tile_uv_validation.get("status") == "pass"
                and tile_lod_luminance_consistency.get("status") == "pass"
                and global_vs_tiled_registration.get("status") == "pass"
                and polar_cap_validation.get("status") in {"pass", "missing"}
            ),
            "scan_albedo_count": len(manager_albedo_keys),
            "scan_normal_count": len(manager_normal_keys),
        },
    }


def _parse_source_crop(value: str) -> tuple[int, int, int, int]:

    parts = [part.strip() for part in str(value).split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("Source crop must be X,Y,WIDTH,HEIGHT.")
    try:
        crop = tuple(int(part) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Source crop values must be integers.") from exc
    if crop[2] <= 0 or crop[3] <= 0:
        raise argparse.ArgumentTypeError("Source crop width and height must be positive.")
    return crop  # type: ignore[return-value]


def _parse_source_resize(value: str) -> tuple[int, int]:

    normalized = str(value).lower().replace("x", ",")
    parts = [part.strip() for part in normalized.split(",")]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("Source resize must be WIDTHxHEIGHT.")
    try:
        resize = tuple(int(part) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Source resize values must be integers.") from exc
    if resize[0] <= 0 or resize[1] <= 0:
        raise argparse.ArgumentTypeError("Source resize dimensions must be positive.")
    return resize  # type: ignore[return-value]


def _build_parser() -> argparse.ArgumentParser:

    default_paths = default_moon_tile_paths(Path(__file__).resolve().parents[1])
    parser = argparse.ArgumentParser(description="Build and validate Moon tile assets for Photometry.")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("build", "validate", "synthetic-sources", "validate-uv", "validate-registration", "audit"),
        default="build",
    )
    parser.add_argument("--project-root", default=str(default_paths["project_root"]))
    parser.add_argument("--output-dir", default=str(default_paths["output_dir"]))
    parser.add_argument("--albedo-source", default=str(default_paths["albedo_source"]))
    parser.add_argument("--global-albedo-source")
    parser.add_argument("--normal-source")
    parser.add_argument("--displacement-source", default=str(default_paths["displacement_source"]))
    parser.add_argument("--synthetic-source-dir", default=str(default_paths["project_root"] / "assets" / "moon_tiles_synthetic" / SYNTHETIC_SOURCE_DIRNAME))
    parser.add_argument("--synthetic-width", type=int, default=512)
    parser.add_argument("--synthetic-height", type=int, default=256)
    parser.add_argument("--synthetic-labels", action="store_true")
    parser.add_argument("--tile-size", type=int, default=512)
    parser.add_argument("--max-level", type=int)
    parser.add_argument("--tile-format", default="png")
    parser.add_argument("--flip-u", action="store_true")
    parser.add_argument("--flip-v", action="store_true")
    parser.add_argument("--longitude-shift-pixels", type=int, default=0)
    parser.add_argument("--longitude-shift-degrees", type=float, default=0.0)
    parser.add_argument("--rotate-180", action="store_true")
    parser.add_argument("--source-crop", type=_parse_source_crop)
    parser.add_argument("--source-resize", type=_parse_source_resize)
    parser.add_argument("--normal-smoothing-passes", type=int, default=2)
    parser.add_argument("--polar-normal-smoothing-passes", type=int, default=3)
    parser.add_argument("--polar-normal-fade-start-lat-deg", type=float, default=70.0)
    parser.add_argument("--polar-normal-fade-end-lat-deg", type=float, default=86.0)
    parser.add_argument("--no-polar-caps", action="store_true")
    parser.add_argument("--polar-cap-size", type=int, default=2048)
    parser.add_argument("--polar-cap-latitude-coverage-deg", type=float, default=60.0)
    parser.add_argument("--polar-cap-blend-start-lat-deg", type=float, default=60.0)
    parser.add_argument("--polar-cap-blend-end-lat-deg", type=float, default=78.0)
    parser.add_argument("--polar-cap-blend-curve-power", type=float, default=1.0)
    parser.add_argument("--polar-cap-normal-blend-start-lat-deg", type=float, default=78.0)
    parser.add_argument("--polar-cap-normal-blend-end-lat-deg", type=float, default=86.0)
    parser.add_argument("--no-polar-cap-boundary-match", action="store_true")
    parser.add_argument("--polar-cap-boundary-match-strength", type=float, default=0.35)
    parser.add_argument("--polar-cap-albedo-sharpness", type=float, default=0.22)
    parser.add_argument("--polar-cap-local-contrast", type=float, default=1.0)
    parser.add_argument("--polar-cap-core-match-strength", type=float, default=0.72)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:

    parser = _build_parser()
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    if args.command == "validate":
        report = validate_moon_tile_tree(project_root, output_dir=args.output_dir)
    elif args.command == "validate-uv":
        report = validate_tile_uv_sampling(project_root, output_dir=args.output_dir, source_image_path=args.albedo_source)
    elif args.command == "validate-registration":
        report = validate_global_vs_tiled_registration(
            project_root,
            output_dir=args.output_dir,
            global_albedo_source=args.global_albedo_source or args.albedo_source,
        )
    elif args.command == "audit":
        report = audit_moon_texture_sources(project_root, output_dir=args.output_dir)
    elif args.command == "synthetic-sources":
        report = generate_synthetic_moon_source_maps(
            args.synthetic_source_dir,
            width=args.synthetic_width,
            height=args.synthetic_height,
            include_tile_labels=args.synthetic_labels,
        )
    else:
        report = build_moon_tile_tree(
            project_root,
            albedo_source=args.albedo_source,
            global_albedo_source=args.global_albedo_source,
            normal_source=args.normal_source,
            displacement_source=args.displacement_source,
            output_dir=args.output_dir,
            tile_size=args.tile_size,
            max_level=args.max_level,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
            tile_format=args.tile_format,
            flip_u=args.flip_u,
            flip_v=args.flip_v,
            longitude_shift_pixels=args.longitude_shift_pixels,
            longitude_shift_degrees=args.longitude_shift_degrees,
            rotate_180=args.rotate_180,
            source_crop=args.source_crop,
            source_resize=args.source_resize,
            normal_smoothing_passes=args.normal_smoothing_passes,
            polar_normal_smoothing_passes=args.polar_normal_smoothing_passes,
            polar_normal_fade_start_lat_deg=args.polar_normal_fade_start_lat_deg,
            polar_normal_fade_end_lat_deg=args.polar_normal_fade_end_lat_deg,
            generate_polar_caps=not args.no_polar_caps,
            polar_cap_size=args.polar_cap_size,
            polar_cap_latitude_coverage_deg=args.polar_cap_latitude_coverage_deg,
            polar_cap_blend_start_lat_deg=args.polar_cap_blend_start_lat_deg,
            polar_cap_blend_end_lat_deg=args.polar_cap_blend_end_lat_deg,
            polar_cap_blend_curve_power=args.polar_cap_blend_curve_power,
            polar_cap_normal_blend_start_lat_deg=args.polar_cap_normal_blend_start_lat_deg,
            polar_cap_normal_blend_end_lat_deg=args.polar_cap_normal_blend_end_lat_deg,
            polar_cap_boundary_match=not args.no_polar_cap_boundary_match,
            polar_cap_boundary_match_strength=args.polar_cap_boundary_match_strength,
            polar_cap_albedo_sharpness=args.polar_cap_albedo_sharpness,
            polar_cap_local_contrast=args.polar_cap_local_contrast,
            polar_cap_core_match_strength=args.polar_cap_core_match_strength,
        )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
