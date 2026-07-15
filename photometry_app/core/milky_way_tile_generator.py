from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from photometry_app.core.milky_way_assets import parse_milky_way_manifest, tile_grid_dimensions
from photometry_app.core.milky_way_mask import (
    DEFAULT_MILKY_WAY_MASK_PARAMETERS,
    MilkyWayMaskParameters,
    apply_milky_way_alpha_mask,
    milky_way_alpha_tile_stats,
    milky_way_mask_manifest_payload,
)


@dataclass(frozen=True, slots=True)
class LoadedMilkyWaySource:
    path: Path
    width: int
    height: int
    dtype: str
    bit_depth: int
    color_mode: str
    color_space: str
    data: np.ndarray


@dataclass(frozen=True, slots=True)
class MilkyWayTileGenerationSummary:
    source_path: Path
    output_directory: Path
    manifest_path: Path
    source_width: int
    source_height: int
    source_dtype: str
    source_bit_depth: int
    source_color_mode: str
    tile_size: int
    tile_width: int
    tile_height: int
    gutter_pixels: int
    padded_tile: bool
    tile_format: str
    compression: str
    file_extension: str
    codec: str
    tone_preprocessing: str
    alpha_masked: bool
    signal_tile_count: int
    empty_tile_count: int
    level_widths: tuple[int, ...]
    tile_counts: tuple[tuple[int, int, int], ...]
    disk_usage_bytes: int


@dataclass(frozen=True, slots=True)
class MilkyWayTileOutputFormat:
    tile_format: str
    compression: str
    file_extension: str
    codec: str
    save_options: tuple[tuple[str, object], ...]


DEFAULT_TILE_OUTPUT_FORMAT = "tiff_lzw"


_TILE_OUTPUT_FORMATS: dict[str, MilkyWayTileOutputFormat] = {
    "png": MilkyWayTileOutputFormat(
        tile_format="png",
        compression="png",
        file_extension=".png",
        codec="png",
        save_options=(),
    ),
    "tiff_lzw": MilkyWayTileOutputFormat(
        tile_format="tiff",
        compression="tiff_lzw",
        file_extension=".tiff",
        codec="tiff_lzw",
        save_options=(("compression", "tiff_lzw"),),
    ),
    "tiff_deflate": MilkyWayTileOutputFormat(
        tile_format="tiff",
        compression="tiff_adobe_deflate",
        file_extension=".tiff",
        codec="tiff_deflate",
        save_options=(("compression", "tiff_adobe_deflate"),),
    ),
}


def supported_tile_output_formats() -> tuple[str, ...]:
    return tuple(_TILE_OUTPUT_FORMATS.keys())


def default_tile_output_format_name() -> str:
    return DEFAULT_TILE_OUTPUT_FORMAT


def resolve_tile_output_format(tile_format: str) -> MilkyWayTileOutputFormat:
    normalized = str(tile_format or DEFAULT_TILE_OUTPUT_FORMAT).strip().casefold().replace("-", "_")
    try:
        return _TILE_OUTPUT_FORMATS[normalized]
    except KeyError as exc:
        supported = ", ".join(sorted(_TILE_OUTPUT_FORMATS))
        raise ValueError(f"Unsupported Milky Way tile format '{tile_format}'. Supported formats: {supported}") from exc


def default_level_widths(source_width: int) -> list[int]:
    resolved_source_width = max(1, int(source_width))
    if resolved_source_width <= 2048:
        return [resolved_source_width]
    widths: list[int] = [2048]
    next_width = 4096
    while next_width < resolved_source_width:
        widths.append(next_width)
        next_width *= 2
    if widths[-1] != resolved_source_width:
        widths.append(resolved_source_width)
    return widths


def load_milky_way_source_image(source_path: str | Path, *, tone_preprocessing: str = "source_like") -> LoadedMilkyWaySource:
    resolved_path = Path(source_path).expanduser().resolve()
    suffix = resolved_path.suffix.casefold()
    if suffix == ".exr":
        return _load_exr_source(resolved_path, tone_preprocessing=tone_preprocessing)
    return _load_standard_source(resolved_path)


def generate_milky_way_tile_pyramid(
    source_path: str | Path,
    output_directory: str | Path,
    *,
    tile_size: int = 1024,
    pad_gutter: int = 0,
    level_widths: Sequence[int] | None = None,
    tone_preprocessing: str = "source_like",
    color_space: str = "unknown",
    source_name: str | None = None,
    tile_format: str = DEFAULT_TILE_OUTPUT_FORMAT,
    write_alpha_mask: bool = True,
    mask_parameters: MilkyWayMaskParameters | None = None,
) -> MilkyWayTileGenerationSummary:
    loaded_source = load_milky_way_source_image(source_path, tone_preprocessing=tone_preprocessing)
    output_root = Path(output_directory).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    output_format = resolve_tile_output_format(tile_format)
    resolved_mask_parameters = mask_parameters or DEFAULT_MILKY_WAY_MASK_PARAMETERS

    if loaded_source.width != loaded_source.height * 2:
        raise ValueError(
            f"Milky Way source must have a 2:1 aspect ratio, got {loaded_source.width}x{loaded_source.height}"
        )

    resolved_tile_size = max(1, int(tile_size))
    resolved_pad_gutter = max(0, int(pad_gutter))
    resolved_level_widths = [int(width) for width in (level_widths or default_level_widths(loaded_source.width))]
    resolved_level_widths = sorted({width for width in resolved_level_widths if width > 0})
    if not resolved_level_widths:
        raise ValueError("Milky Way tile generator requires at least one output level")

    level_records: list[dict[str, object]] = []
    tile_signal_records: list[dict[str, object]] = []
    tile_counts: list[tuple[int, int, int]] = []
    signal_tile_count = 0
    empty_tile_count = 0
    total_disk_usage_bytes = 0
    source_image = _array_to_image(loaded_source.data)

    if loaded_source.width * loaded_source.height >= 100_000_000:
        print(
            "Warning: large Milky Way source detected; this foundation pass may require high RAM usage for 16k/32k inputs.",
        )

    for level_index, level_width in enumerate(resolved_level_widths):
        if level_width > loaded_source.width:
            raise ValueError(f"Requested level width {level_width} exceeds source width {loaded_source.width}")
        level_height = max(1, int(round((float(level_width) / float(loaded_source.width)) * float(loaded_source.height))))
        if level_width != level_height * 2:
            raise ValueError(f"Generated level {level_width}x{level_height} does not maintain a 2:1 aspect ratio")
        level_directory = output_root / f"L{level_index}"
        level_directory.mkdir(parents=True, exist_ok=True)
        if level_width == loaded_source.width:
            level_image = source_image
        else:
            level_image = source_image.resize((level_width, level_height), resample=_resample_filter())
        level_array = np.asarray(level_image, dtype=np.uint8)
        if write_alpha_mask:
            level_output_array = apply_milky_way_alpha_mask(level_array, resolved_mask_parameters)
            level_output_image = _array_to_image(level_output_array)
            level_alpha = level_output_array[..., 3]
        else:
            level_output_array = level_array
            level_output_image = level_image
            level_alpha = None

        tile_count_x, tile_count_y = tile_grid_dimensions(level_width, level_height, resolved_tile_size)
        tile_counts.append((level_index, tile_count_x * tile_count_y, level_width))
        for x_index in range(tile_count_x):
            for y_index in range(tile_count_y):
                left = x_index * resolved_tile_size
                top = y_index * resolved_tile_size
                right = min(left + resolved_tile_size, level_width)
                bottom = min(top + resolved_tile_size, level_height)
                core_width = max(1, right - left)
                core_height = max(1, bottom - top)
                if level_alpha is not None:
                    tile_stats = milky_way_alpha_tile_stats(level_alpha[top:bottom, left:right], resolved_mask_parameters)
                    if tile_stats.has_signal:
                        signal_tile_count += 1
                    else:
                        empty_tile_count += 1
                    tile_signal_records.append(
                        {
                            "level": level_index,
                            "x": x_index,
                            "y": y_index,
                            "has_signal": bool(tile_stats.has_signal),
                            "alpha_coverage": round(float(tile_stats.alpha_coverage), 8),
                            "mean_alpha": round(float(tile_stats.mean_alpha), 8),
                            "max_alpha": round(float(tile_stats.max_alpha), 8),
                        }
                    )
                if resolved_pad_gutter > 0:
                    tile = _build_padded_tile_image(
                        level_output_array,
                        left=left,
                        top=top,
                        core_width=core_width,
                        core_height=core_height,
                        level_width=level_width,
                        level_height=level_height,
                        gutter_pixels=resolved_pad_gutter,
                    )
                else:
                    tile = level_output_image.crop((left, top, right, bottom))
                tile_path = level_directory / f"{x_index}_{y_index}{output_format.file_extension}"
                tile.save(tile_path, **dict(output_format.save_options))
                total_disk_usage_bytes += tile_path.stat().st_size

        level_records.append(
            {
                "level": level_index,
                "width": level_width,
                "height": level_height,
                "path": f"L{level_index}/{{x}}_{{y}}{output_format.file_extension}",
            }
        )

    manifest_payload = {
        "schema_version": 1,
        "projection": "equirectangular_celestial",
        "source_name": source_name or loaded_source.path.name,
        "source_width": loaded_source.width,
        "source_height": loaded_source.height,
        "tile_size": resolved_tile_size,
        "tile_width": resolved_tile_size + (resolved_pad_gutter * 2),
        "tile_height": resolved_tile_size + (resolved_pad_gutter * 2),
        "gutter_pixels": resolved_pad_gutter,
        "content_region": [resolved_pad_gutter, resolved_pad_gutter, resolved_tile_size, resolved_tile_size],
        "padded_tile": resolved_pad_gutter > 0,
        "tile_format": output_format.tile_format,
        "compression": output_format.compression,
        "file_extension": output_format.file_extension,
        "codec": output_format.codec,
        "color_space": str(color_space or loaded_source.color_space or "unknown").strip().casefold(),
        "tone_preprocessing": str(tone_preprocessing or "none").strip().casefold(),
        "orientation": {
            "ra_increases": "left",
            "dec_axis": "top_to_bottom",
        },
        "levels": level_records,
    }
    if write_alpha_mask:
        alpha_mask_payload = milky_way_mask_manifest_payload(resolved_mask_parameters)
        alpha_mask_payload["tiles"] = tile_signal_records
        manifest_payload["alpha_mask"] = alpha_mask_payload
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(_json_dumps(manifest_payload), encoding="utf-8")
    parse_milky_way_manifest(manifest_payload, manifest_path=manifest_path, strict=False)

    return MilkyWayTileGenerationSummary(
        source_path=loaded_source.path,
        output_directory=output_root,
        manifest_path=manifest_path,
        source_width=loaded_source.width,
        source_height=loaded_source.height,
        source_dtype=loaded_source.dtype,
        source_bit_depth=loaded_source.bit_depth,
        source_color_mode=loaded_source.color_mode,
        tile_size=resolved_tile_size,
        tile_width=resolved_tile_size + (resolved_pad_gutter * 2),
        tile_height=resolved_tile_size + (resolved_pad_gutter * 2),
        gutter_pixels=resolved_pad_gutter,
        padded_tile=resolved_pad_gutter > 0,
        tile_format=output_format.tile_format,
        compression=output_format.compression,
        file_extension=output_format.file_extension,
        codec=output_format.codec,
        tone_preprocessing=str(tone_preprocessing or "none").strip().casefold(),
        alpha_masked=bool(write_alpha_mask),
        signal_tile_count=signal_tile_count,
        empty_tile_count=empty_tile_count,
        level_widths=tuple(resolved_level_widths),
        tile_counts=tuple(tile_counts),
        disk_usage_bytes=total_disk_usage_bytes + manifest_path.stat().st_size,
    )


def _build_padded_tile_image(
    level_array: np.ndarray,
    *,
    left: int,
    top: int,
    core_width: int,
    core_height: int,
    level_width: int,
    level_height: int,
    gutter_pixels: int,
):
    from PIL import Image

    x_indices = np.arange(left - gutter_pixels, left + core_width + gutter_pixels, dtype=np.int64)
    y_indices = np.arange(top - gutter_pixels, top + core_height + gutter_pixels, dtype=np.int64)
    wrapped_x = np.mod(x_indices, max(1, int(level_width)))
    clamped_y = np.clip(y_indices, 0, max(0, int(level_height) - 1))
    padded_array = level_array[np.ix_(clamped_y, wrapped_x)].copy()
    return Image.fromarray(padded_array, mode=_array_image_mode(padded_array))


def _array_to_image(image_array: np.ndarray):
    from PIL import Image

    if image_array.dtype != np.uint8:
        raise ValueError("Milky Way tile generator currently writes 8-bit RGB/RGBA tiles")
    return Image.fromarray(image_array, mode=_array_image_mode(image_array))


def _array_image_mode(image_array: np.ndarray) -> str:
    if image_array.ndim != 3 or image_array.shape[2] not in {3, 4}:
        raise ValueError(f"Unsupported Milky Way tile image shape {image_array.shape!r}")
    return "RGBA" if image_array.shape[2] == 4 else "RGB"


def _json_dumps(payload: dict[str, object]) -> str:
    import json

    return json.dumps(payload, indent=2)


def _load_exr_source(source_path: Path, *, tone_preprocessing: str) -> LoadedMilkyWaySource:
    openexr_error: Exception | None = None
    try:
        image = _load_openexr_pixels(source_path)
    except Exception as exc:
        openexr_error = exc
    else:
        if not np.issubdtype(image.dtype, np.floating):
            raise RuntimeError(
                "EXR source was decoded as non-HDR data "
                f"({image.dtype}) through OpenEXR. Install a proper EXR-capable library instead of silently generating bad Milky Way tiles."
            )
        rgb = np.asarray(image[..., :3], dtype=np.float32)
        rgb = np.nan_to_num(rgb, nan=0.0, posinf=0.0, neginf=0.0)
        data = _tone_map_float_source(rgb, tone_preprocessing=tone_preprocessing)
        return LoadedMilkyWaySource(
            path=source_path,
            width=int(rgb.shape[1]),
            height=int(rgb.shape[0]),
            dtype=str(image.dtype),
            bit_depth=16 if str(image.dtype) == "float16" else 32,
            color_mode="RGB",
            color_space="unknown",
            data=data,
        )

    try:
        import imageio.v3 as iio
    except Exception as exc:
        raise RuntimeError(
            "EXR input requires imageio with working EXR support. Install a real EXR-capable reader before generating Milky Way tiles."
        ) from openexr_error or exc

    try:
        image = iio.imread(source_path)
    except Exception as exc:
        raise RuntimeError(
            "EXR input could not be decoded by either OpenEXR or imageio. Install a real EXR-capable reader before generating Milky Way tiles."
        ) from openexr_error or exc
    if image.ndim == 4 and image.shape[0] == 1:
        image = image[0]
    if image.ndim != 3 or image.shape[2] < 3:
        raise RuntimeError(f"Unsupported EXR shape {image.shape!r} for Milky Way tile generation")
    if not np.issubdtype(image.dtype, np.floating):
        raise RuntimeError(
            "EXR source was decoded as non-HDR data "
            f"({image.dtype}). Install a proper EXR plugin/library instead of silently generating bad Milky Way tiles."
        )

    rgb = np.asarray(image[..., :3], dtype=np.float32)
    rgb = np.nan_to_num(rgb, nan=0.0, posinf=0.0, neginf=0.0)
    data = _tone_map_float_source(rgb, tone_preprocessing=tone_preprocessing)
    return LoadedMilkyWaySource(
        path=source_path,
        width=int(rgb.shape[1]),
        height=int(rgb.shape[0]),
        dtype=str(image.dtype),
        bit_depth=16 if str(image.dtype) == "float16" else 32,
        color_mode="RGB",
        color_space="unknown",
        data=data,
    )


def _load_openexr_pixels(source_path: Path) -> np.ndarray:
    import OpenEXR

    exr_file = OpenEXR.File(str(source_path))
    channels = exr_file.channels()
    rgb_pixels = channels.get("RGB")
    if rgb_pixels is None:
        if not all(channel_name in channels for channel_name in ("R", "G", "B")):
            raise RuntimeError("OpenEXR source does not contain RGB channels")
        rgb_pixels = np.stack((channels["R"].pixels, channels["G"].pixels, channels["B"].pixels), axis=-1)
    else:
        rgb_pixels = rgb_pixels.pixels
    return np.asarray(rgb_pixels)


def _load_standard_source(source_path: Path) -> LoadedMilkyWaySource:
    from PIL import Image

    image = Image.open(source_path)
    source_mode = image.mode
    rgb_image = image.convert("RGB")
    image_array = np.asarray(rgb_image, dtype=np.uint8)
    return LoadedMilkyWaySource(
        path=source_path,
        width=int(rgb_image.width),
        height=int(rgb_image.height),
        dtype=str(image_array.dtype),
        bit_depth=8,
        color_mode=source_mode,
        color_space="srgb" if source_mode in {"RGB", "RGBA", "P"} else "unknown",
        data=image_array,
    )


def _resample_filter():
    from PIL import Image

    return Image.Resampling.LANCZOS


def _tone_map_float_source(image: np.ndarray, *, tone_preprocessing: str) -> np.ndarray:
    resolved_mode = str(tone_preprocessing or "source_like").strip().casefold()
    rgb = np.maximum(np.asarray(image, dtype=np.float32), np.float32(0.0))
    if resolved_mode == "none":
        maximum_value = float(np.max(rgb)) if rgb.size else 0.0
        if maximum_value > 1.0:
            raise RuntimeError(
                "Float HDR Milky Way source contains values above 1.0; use --tone-preprocessing source_like or display_toned explicitly."
            )
        normalized = np.clip(rgb, np.float32(0.0), np.float32(1.0))
    elif resolved_mode == "source_like":
        scale = max(float(np.percentile(rgb, 99.8)), 1.0e-6)
        normalized = np.clip(rgb / np.float32(scale), np.float32(0.0), np.float32(1.0))
    elif resolved_mode == "display_toned":
        scale = max(float(np.percentile(rgb, 99.5)), 1.0e-6)
        normalized = rgb / np.float32(scale)
        normalized = normalized / (np.float32(1.0) + normalized)
        normalized = np.clip(normalized, np.float32(0.0), np.float32(1.0))
    else:
        raise RuntimeError(f"Unsupported tone preprocessing mode: {tone_preprocessing}")
    encoded = np.power(np.clip(normalized, np.float32(0.0), np.float32(1.0)), np.float32(1.0 / 2.2))
    return np.clip(encoded * np.float32(255.0), np.float32(0.0), np.float32(255.0)).astype(np.uint8)