from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Callable

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage

from photometry_app.core.image_io import is_supported_image_path, read_header, read_header_and_shape, read_image_data
from photometry_app.core.settings import AppSettings, SkyAtlasCustomOverlayRecord
from photometry_app.core.sky_atlas_survey_storage import (
    overlay_directory,
    overlay_image_relative_path,
    overlay_wcs_relative_path,
)
from photometry_app.core.sky_explorer import _resolve_source_field
from photometry_app.core.wcs import celestial_wcs, scale_wcs_pixel_grid

SKY_ATLAS_OVERLAY_SUFFIXES = {".png", ".xisf", ".tif", ".tiff", ".fit", ".fits"}
SKY_ATLAS_OVERLAY_FILE_FILTER = (
    "Sky overlay images (*.png *.xisf *.tif *.tiff *.fit *.fits);;"
    "PNG (*.png);;"
    "XISF (*.xisf);;"
    "TIFF (*.tif *.tiff);;"
    "FITS (*.fit *.fits)"
)
_STANDARD_OVERLAY_SUFFIXES = {".png", ".tif", ".tiff"}
SKY_ATLAS_CUSTOM_OVERLAY_CACHE_MAX_LONG_EDGE_MIN = 512
SKY_ATLAS_CUSTOM_OVERLAY_CACHE_MAX_LONG_EDGE_MAX = 8192
SKY_ATLAS_CUSTOM_OVERLAY_CACHE_MAX_LONG_EDGE_DEFAULT = 2048


@dataclass(slots=True)
class LoadedSkyAtlasCustomOverlay:
    overlay_id: str
    display_name: str
    image_rgb: np.ndarray
    image_alpha: np.ndarray | None
    wcs: WCS
    width: int
    height: int


def is_sky_atlas_overlay_path(path: Path) -> bool:
    return path.suffix.casefold() in SKY_ATLAS_OVERLAY_SUFFIXES


def _companion_wcs_path(source_path: Path) -> Path | None:
    for suffix in (".fits", ".fit", ".fts", ".fz"):
        candidate = source_path.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    return None


def _resolve_overlay_cache_max_long_edge(settings: AppSettings) -> int:
    return max(
        SKY_ATLAS_CUSTOM_OVERLAY_CACHE_MAX_LONG_EDGE_MIN,
        min(
            SKY_ATLAS_CUSTOM_OVERLAY_CACHE_MAX_LONG_EDGE_MAX,
            int(getattr(settings, "sky_atlas_custom_overlay_cache_max_long_edge", SKY_ATLAS_CUSTOM_OVERLAY_CACHE_MAX_LONG_EDGE_DEFAULT)),
        ),
    )


def _downscale_overlay_arrays(
    image_rgb: np.ndarray,
    image_alpha: np.ndarray | None,
    *,
    max_long_edge: int,
) -> tuple[np.ndarray, np.ndarray | None, int, int]:
    source_height, source_width = image_rgb.shape[:2]
    longest_edge = max(source_width, source_height)
    if longest_edge <= max_long_edge:
        return image_rgb, image_alpha, source_width, source_height

    scale = float(max_long_edge) / float(longest_edge)
    target_width = max(1, int(round(source_width * scale)))
    target_height = max(1, int(round(source_height * scale)))
    if target_width == source_width and target_height == source_height:
        return image_rgb, image_alpha, source_width, source_height

    rgb_image = Image.fromarray(np.ascontiguousarray(image_rgb), mode="RGB")
    resized_rgb = np.asarray(rgb_image.resize((target_width, target_height), Image.Resampling.LANCZOS))
    resized_alpha = None
    if image_alpha is not None:
        alpha_image = Image.fromarray(np.ascontiguousarray(image_alpha), mode="L")
        resized_alpha = np.asarray(alpha_image.resize((target_width, target_height), Image.Resampling.LANCZOS))
    return resized_rgb, resized_alpha, target_width, target_height


def _write_overlay_wcs_cache(path: Path, wcs: WCS, width: int, height: int) -> None:
    header = wcs.to_header()
    header["NAXIS"] = 2
    header["NAXIS1"] = int(width)
    header["NAXIS2"] = int(height)
    placeholder = np.zeros((int(height), int(width)), dtype=np.float32)
    fits.PrimaryHDU(data=placeholder, header=header).writeto(path, overwrite=True)


def _save_overlay_png_cache(path: Path, image_rgb: np.ndarray, image_alpha: np.ndarray | None) -> None:
    rgb = np.ascontiguousarray(image_rgb)
    if image_alpha is None:
        Image.fromarray(rgb, mode="RGB").save(path, format="PNG")
        return
    alpha = np.ascontiguousarray(image_alpha)
    rgba = np.dstack((rgb, alpha))
    Image.fromarray(rgba, mode="RGBA").save(path, format="PNG")


def _load_source_overlay_arrays(source_path: Path) -> tuple[np.ndarray, np.ndarray | None]:
    suffix = source_path.suffix.casefold()
    if suffix in _STANDARD_OVERLAY_SUFFIXES:
        image = QImage(str(source_path))
        if not image.isNull():
            return _qimage_to_arrays(image)
    if not is_supported_image_path(source_path):
        raise ValueError(f"Unsupported overlay image: {source_path.name}")
    return _array_to_overlay_rgb_alpha(read_image_data(source_path, dtype=None))


def import_sky_overlay(
    source_path: Path,
    settings: AppSettings,
    *,
    survey_id: str,
    progress_callback: Callable[[str], None] | None = None,
) -> SkyAtlasCustomOverlayRecord:
    resolved_survey_id = str(survey_id or "").strip()
    if not resolved_survey_id:
        raise ValueError("A survey id is required to import a custom sky overlay.")

    resolved_source = source_path.expanduser().resolve()
    if not is_sky_atlas_overlay_path(resolved_source):
        supported = ", ".join(sorted(SKY_ATLAS_OVERLAY_SUFFIXES))
        raise ValueError(f"Sky Atlas custom overlays support {supported} images only.")
    if not resolved_source.is_file():
        raise ValueError(f"Image not found: {resolved_source}")

    _header, source_width, source_height = read_header_and_shape(resolved_source)
    if source_width is None or source_height is None or source_width <= 0 or source_height <= 0:
        raise ValueError("Could not determine the overlay image dimensions.")

    resolve_path = resolved_source
    if resolved_source.suffix.casefold() in _STANDARD_OVERLAY_SUFFIXES:
        resolve_path = _companion_wcs_path(resolved_source) or resolved_source
    if progress_callback is not None:
        progress_callback("Reading WCS or plate-solving the overlay image...")
    solved_field, _used_astrometry = _resolve_source_field(
        resolve_path,
        settings,
        progress_callback=progress_callback,
    )

    if progress_callback is not None:
        progress_callback("Preparing cached overlay image...")
    image_rgb, image_alpha = _load_source_overlay_arrays(resolved_source)
    max_long_edge = _resolve_overlay_cache_max_long_edge(settings)
    cached_rgb, cached_alpha, cached_width, cached_height = _downscale_overlay_arrays(
        image_rgb,
        image_alpha,
        max_long_edge=max_long_edge,
    )

    solved_header = read_header(solved_field.wcs_path)
    overlay_wcs = celestial_wcs(solved_header)
    if cached_width != int(source_width) or cached_height != int(source_height):
        overlay_wcs = scale_wcs_pixel_grid(
            overlay_wcs,
            source_width=int(source_width),
            source_height=int(source_height),
            target_width=cached_width,
            target_height=cached_height,
        )

    overlay_id = uuid.uuid4().hex
    cache_root = overlay_directory(resolved_survey_id, overlay_id)
    cache_root.mkdir(parents=True, exist_ok=True)
    cached_image_path = cache_root / "overlay.png"
    cached_wcs_path = cache_root / "overlay.wcs.fits"
    _save_overlay_png_cache(cached_image_path, cached_rgb, cached_alpha)
    _write_overlay_wcs_cache(cached_wcs_path, overlay_wcs, cached_width, cached_height)

    overlay_manifest = {
        "overlay_id": overlay_id,
        "display_name": resolved_source.name,
        "source_image_path": str(resolved_source),
        "width": int(cached_width),
        "height": int(cached_height),
    }
    (cache_root / "overlay.json").write_text(
        json.dumps(overlay_manifest, indent=2),
        encoding="utf-8",
    )

    return SkyAtlasCustomOverlayRecord(
        overlay_id=overlay_id,
        display_name=resolved_source.name,
        cached_image_path=overlay_image_relative_path(overlay_id),
        cached_wcs_path=overlay_wcs_relative_path(overlay_id),
        source_image_path=str(resolved_source),
        width=int(cached_width),
        height=int(cached_height),
    )


import_png_overlay = import_sky_overlay


def _qimage_to_arrays_via_png(image: QImage) -> tuple[np.ndarray, np.ndarray | None]:
    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise ValueError("Could not open the in-memory buffer for overlay import.")
    if not image.save(buffer, "PNG"):
        raise ValueError("Could not encode the overlay image.")
    with BytesIO(bytes(buffer.data())) as payload:
        frame = Image.open(payload)
        frame.load()
    return _array_to_overlay_rgb_alpha(np.asarray(frame))


def _qimage_to_arrays(image: QImage) -> tuple[np.ndarray, np.ndarray | None]:
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    if converted.isNull():
        raise ValueError("Could not convert the overlay image to RGBA8888.")
    width = max(1, int(converted.width()))
    height = max(1, int(converted.height()))
    bytes_per_line = max(width * 4, int(converted.bytesPerLine()))
    byte_count = height * bytes_per_line
    payload = converted.constBits().tobytes()
    if len(payload) < byte_count:
        payload = converted.bits().tobytes()
    if len(payload) < byte_count:
        return _qimage_to_arrays_via_png(image)
    rgba_buffer = np.frombuffer(payload, dtype=np.uint8, count=byte_count)
    rgba = rgba_buffer.reshape((height, bytes_per_line))[:, : width * 4].reshape((height, width, 4)).copy()
    rgb = rgba[..., :3]
    alpha = rgba[..., 3]
    if np.all(alpha == 255):
        return rgb, None
    return rgb, alpha


def _normalize_display_channel(channel: np.ndarray) -> np.ndarray:
    values = np.asarray(channel, dtype=np.float32)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return np.zeros(values.shape, dtype=np.uint8)
    low = float(np.percentile(finite, 1.0))
    high = float(np.percentile(finite, 99.0))
    if high <= low:
        high = low + 1.0
    scaled = np.clip((values - low) / (high - low), 0.0, 1.0)
    return np.clip(scaled * 255.0, 0.0, 255.0).astype(np.uint8)


def _normalize_display_rgb(rgb: np.ndarray) -> np.ndarray:
    channels = np.asarray(rgb)
    if channels.dtype == np.uint8:
        return np.ascontiguousarray(channels[..., :3])
    if channels.ndim != 3 or channels.shape[-1] < 3:
        raise ValueError("Overlay RGB data must have at least three channels.")
    normalized = np.stack(
        (
            _normalize_display_channel(channels[..., 0]),
            _normalize_display_channel(channels[..., 1]),
            _normalize_display_channel(channels[..., 2]),
        ),
        axis=-1,
    )
    return np.ascontiguousarray(normalized)


def _array_to_overlay_rgb_alpha(data: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
    arr = np.asarray(data)
    if arr.ndim == 2:
        gray = _normalize_display_channel(arr)
        return np.stack((gray, gray, gray), axis=-1), None

    if arr.ndim == 3:
        if arr.shape[0] in (3, 4) and arr.shape[0] < min(arr.shape[1], arr.shape[2]):
            arr = np.moveaxis(arr, 0, -1)
        if arr.shape[-1] >= 3:
            rgb = _normalize_display_rgb(arr[..., :3])
            alpha = None
            if arr.shape[-1] >= 4:
                alpha_channel = arr[..., 3]
                if alpha_channel.dtype == np.uint8:
                    alpha = np.ascontiguousarray(alpha_channel)
                else:
                    alpha = _normalize_display_channel(alpha_channel)
                if np.all(alpha == 255):
                    alpha = None
            return rgb, alpha

    raise ValueError("Overlay image must be grayscale or RGB/RGBA.")


def _load_overlay_arrays_from_path(path: Path) -> tuple[np.ndarray, np.ndarray | None]:
    suffix = path.suffix.casefold()
    if suffix not in SKY_ATLAS_OVERLAY_SUFFIXES:
        raise ValueError(f"Unsupported overlay image: {path.name}")

    if suffix in _STANDARD_OVERLAY_SUFFIXES:
        image = QImage(str(path))
        if not image.isNull():
            return _qimage_to_arrays(image)

    if not is_supported_image_path(path):
        raise ValueError(f"Unsupported overlay image: {path.name}")

    return _array_to_overlay_rgb_alpha(read_image_data(path, dtype=None))


def load_overlay_record(
    record: SkyAtlasCustomOverlayRecord,
    *,
    survey_id: str | None = None,
) -> LoadedSkyAtlasCustomOverlay | None:
    if survey_id:
        from photometry_app.core.sky_atlas_survey_storage import resolve_overlay_record_for_survey

        record = resolve_overlay_record_for_survey(survey_id, record)
    image_path = Path(record.cached_image_path).expanduser()
    wcs_path = Path(record.cached_wcs_path).expanduser()
    if not image_path.is_file() or not wcs_path.is_file():
        return None

    try:
        wcs_header = read_header(wcs_path)
        wcs = celestial_wcs(wcs_header)
        wcs_width = int(wcs_header.get("NAXIS1", 0) or record.width)
        wcs_height = int(wcs_header.get("NAXIS2", 0) or record.height)
        image_width = int(record.width)
        image_height = int(record.height)
        if wcs_width != image_width or wcs_height != image_height:
            wcs = scale_wcs_pixel_grid(
                wcs,
                source_width=wcs_width,
                source_height=wcs_height,
                target_width=image_width,
                target_height=image_height,
            )
    except Exception:
        return None

    try:
        image_rgb, image_alpha = _load_overlay_arrays_from_path(image_path)
    except Exception:
        return None

    return LoadedSkyAtlasCustomOverlay(
        overlay_id=record.overlay_id,
        display_name=record.display_name,
        image_rgb=image_rgb,
        image_alpha=image_alpha,
        wcs=wcs,
        width=int(record.width),
        height=int(record.height),
    )


def overlay_view_target(overlay: LoadedSkyAtlasCustomOverlay) -> tuple[float, float, float]:
    """Return RA deg, Dec deg, and a suggested field width for framing an overlay."""
    from photometry_app.core.wcs import _estimate_radius_deg

    center = overlay.wcs.pixel_to_world(overlay.width / 2.0, overlay.height / 2.0)
    radius_deg = _estimate_radius_deg(overlay.wcs, overlay.width, overlay.height)
    field_width_deg = max(min(radius_deg * 2.4, 120.0), 0.25)
    return float(center.ra.deg) % 360.0, float(center.dec.deg), field_width_deg


def apply_overlay_edge_feather(
    alpha: np.ndarray,
    *,
    width: int,
    height: int,
    feather_amount: float,
) -> np.ndarray:
    resolved_feather = max(0.0, min(1.0, float(feather_amount)))
    if resolved_feather <= 1.0e-6:
        return alpha

    feather_pixels = resolved_feather * float(min(width, height)) * 0.25
    if feather_pixels <= 1.0:
        return alpha

    y_coords = np.arange(height, dtype=np.float32)[:, None]
    x_coords = np.arange(width, dtype=np.float32)[None, :]
    edge_distance = np.minimum(
        np.minimum(y_coords, float(height - 1) - y_coords),
        np.minimum(x_coords, float(width - 1) - x_coords),
    )
    normalized = np.clip(edge_distance / np.float32(feather_pixels), np.float32(0.0), np.float32(1.0))
    feather_mask = normalized * normalized * (np.float32(3.0) - np.float32(2.0) * normalized)

    alpha_channel = alpha.astype(np.float32, copy=False)
    return np.clip(alpha_channel * feather_mask, np.float32(0.0), np.float32(255.0)).astype(np.uint8)


def build_overlay_display_qimage(
    overlay: LoadedSkyAtlasCustomOverlay,
    *,
    brightness: float,
    saturation: float,
    feather: float = 0.0,
) -> QImage:
    rgb = overlay.image_rgb.astype(np.float32, copy=False)
    toned = apply_overlay_tone_controls(
        rgb.reshape(-1, 3),
        brightness=brightness,
        saturation=saturation,
    ).reshape(overlay.height, overlay.width, 3)
    toned_rgb = np.clip(toned, 0.0, 255.0).astype(np.uint8)

    resolved_feather = max(0.0, min(1.0, float(feather)))
    if overlay.image_alpha is not None:
        alpha = overlay.image_alpha.astype(np.uint8, copy=False)
    elif resolved_feather > 1.0e-6:
        alpha = np.full((overlay.height, overlay.width), 255, dtype=np.uint8)
    else:
        alpha = None

    if alpha is not None and resolved_feather > 1.0e-6:
        alpha = apply_overlay_edge_feather(
            alpha,
            width=overlay.width,
            height=overlay.height,
            feather_amount=resolved_feather,
        )

    if alpha is not None:
        rgba = np.ascontiguousarray(
            np.dstack((toned_rgb, alpha)),
        )
        bytes_per_line = int(rgba.strides[0])
        image = QImage(
            rgba.data,
            overlay.width,
            overlay.height,
            bytes_per_line,
            QImage.Format.Format_RGBA8888,
        ).copy()
        return image

    rgb_buffer = np.ascontiguousarray(toned_rgb)
    bytes_per_line = int(rgb_buffer.strides[0])
    image = QImage(
        rgb_buffer.data,
        overlay.width,
        overlay.height,
        bytes_per_line,
        QImage.Format.Format_RGB888,
    ).copy()
    return image


def apply_overlay_tone_controls(
    sampled_rgb: np.ndarray,
    *,
    brightness: float,
    saturation: float,
) -> np.ndarray:
    rgb = np.clip(sampled_rgb / np.float32(255.0), np.float32(0.0), np.float32(1.0))
    rgb = rgb * np.float32(max(0.0, float(brightness)))
    rgb = rgb / (np.float32(1.0) + rgb)
    luminance = (
        rgb[..., 0] * np.float32(0.2126)
        + rgb[..., 1] * np.float32(0.7152)
        + rgb[..., 2] * np.float32(0.0722)
    )[..., np.newaxis]
    rgb = luminance + (rgb - luminance) * np.float32(max(0.0, float(saturation)))
    return np.clip(rgb * np.float32(255.0), np.float32(0.0), np.float32(255.0)).astype(np.float32, copy=False)


def sample_overlay_at_sky_coordinates(
    overlay: LoadedSkyAtlasCustomOverlay,
    ra_deg: np.ndarray,
    dec_deg: np.ndarray,
    *,
    interpolation: str = "nearest",
) -> tuple[np.ndarray, np.ndarray]:
    px, py = overlay.wcs.world_to_pixel_values(ra_deg, dec_deg)
    width_limit = max(0, overlay.width - 1)
    height_limit = max(0, overlay.height - 1)
    valid = (
        np.isfinite(px)
        & np.isfinite(py)
        & (px >= 0.0)
        & (py >= 0.0)
        & (px <= float(width_limit))
        & (py <= float(height_limit))
    )

    sampled_rgb = np.zeros((*ra_deg.shape, 3), dtype=np.float32)
    sampled_alpha = np.zeros(ra_deg.shape, dtype=np.float32)
    if not np.any(valid):
        return sampled_rgb, sampled_alpha

    image_rgb = overlay.image_rgb.astype(np.float32)
    if overlay.image_alpha is not None:
        image_alpha = overlay.image_alpha.astype(np.float32) / np.float32(255.0)
    else:
        image_alpha = np.ones((overlay.height, overlay.width), dtype=np.float32)

    resolved_interpolation = str(interpolation or "nearest").strip().casefold()
    if resolved_interpolation == "bilinear":
        x0 = np.floor(px[valid]).astype(np.int32)
        y0 = np.floor(py[valid]).astype(np.int32)
        fx = (px[valid] - x0.astype(np.float64)).astype(np.float32)
        fy = (py[valid] - y0.astype(np.float64)).astype(np.float32)

        def _bilinear(channel: np.ndarray) -> np.ndarray:
            v00 = channel[y0, x0]
            v10 = channel[y0, x0 + 1]
            v01 = channel[y0 + 1, x0]
            v11 = channel[y0 + 1, x0 + 1]
            top = v00 * (1.0 - fx) + v10 * fx
            bottom = v01 * (1.0 - fx) + v11 * fx
            return top * (1.0 - fy) + bottom * fy

        rgb_valid = np.stack(
            (
                _bilinear(image_rgb[..., 0]),
                _bilinear(image_rgb[..., 1]),
                _bilinear(image_rgb[..., 2]),
            ),
            axis=-1,
        )
        alpha_valid = _bilinear(image_alpha)
    else:
        x_idx = np.clip(np.rint(px[valid]).astype(np.int32), 0, overlay.width - 1)
        y_idx = np.clip(np.rint(py[valid]).astype(np.int32), 0, overlay.height - 1)
        rgb_valid = image_rgb[y_idx, x_idx]
        alpha_valid = image_alpha[y_idx, x_idx]

    sampled_rgb[valid] = rgb_valid
    sampled_alpha[valid] = alpha_valid
    return sampled_rgb, sampled_alpha
