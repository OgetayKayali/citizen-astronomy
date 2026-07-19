from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Literal

import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.wcs import WCS
from PIL import Image, ImageDraw, ImageFont

from photometry_app.core.plotting import AnnotatedImageRenderSettings, render_image_path_for_display
from photometry_app.core.sky_explorer import SkyExplorerObject

SkyExplorerCollageLayout = Literal["uniform", "natural", "mosaic"]
SkyExplorerCollageScope = Literal["selected", "all_eligible_visible"]

DEFAULT_COLLAGE_MARGIN_FRACTION = 0.15
DEFAULT_MOSAIC_GAP_PX = 16
DEFAULT_MOSAIC_TARGET_WIDTH_PX = 1600
_MIN_CROP_HALF_EXTENT_PX = 4.0
_FOOTPRINT_SAMPLE_COUNT = 32


@dataclass(frozen=True, slots=True)
class SkyExplorerCollageOptions:
    layout: SkyExplorerCollageLayout = "uniform"
    margin_fraction: float = DEFAULT_COLLAGE_MARGIN_FRACTION
    show_labels: bool = True
    columns: int | None = None
    gap_px: int = 12
    padding_px: int = 16
    label_height_px: int = 28
    mosaic_target_width_px: int = DEFAULT_MOSAIC_TARGET_WIDTH_PX
    canvas_rgb: tuple[int, int, int] = (32, 34, 40)
    label_rgb: tuple[int, int, int] = (235, 235, 240)
    tile_background_rgb: tuple[int, int, int] = (20, 22, 26)


@dataclass(frozen=True, slots=True)
class SkyExplorerCatalogSizeAxes:
    major_axis_arcmin: float
    minor_axis_arcmin: float
    position_angle_deg: float


@dataclass(frozen=True, slots=True)
class SkyExplorerCollageCrop:
    sky_object: SkyExplorerObject
    image_rgb: np.ndarray
    was_clipped: bool
    was_padded: bool
    requested_width: int
    requested_height: int


@dataclass(frozen=True, slots=True)
class SkyExplorerCollageResult:
    image_rgb: np.ndarray
    included_objects: tuple[SkyExplorerObject, ...]
    skipped_objects: tuple[SkyExplorerObject, ...]
    clipped_count: int
    padded_count: int
    columns: int
    rows: int
    layout: SkyExplorerCollageLayout
    margin_fraction: float


def _metadata_float(metadata: dict[str, object], *keys: str, require_positive: bool = True) -> float | None:
    for key in keys:
        raw_value = metadata.get(key)
        if not isinstance(raw_value, (int, float, str)):
            continue
        try:
            numeric_value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(numeric_value):
            continue
        if require_positive and numeric_value <= 0.0:
            continue
        return numeric_value
    return None


def sky_explorer_object_has_catalog_size(sky_object: SkyExplorerObject) -> bool:
    return sky_explorer_catalog_size_axes(sky_object) is not None


def sky_explorer_catalog_size_axes(sky_object: SkyExplorerObject) -> SkyExplorerCatalogSizeAxes | None:
    metadata = sky_object.metadata if isinstance(sky_object.metadata, dict) else {}
    major = _metadata_float(metadata, "catalog_major_axis_arcmin", "catalog_size_arcmin")
    minor = _metadata_float(metadata, "catalog_minor_axis_arcmin", "catalog_size_arcmin", "catalog_major_axis_arcmin")
    size = _metadata_float(metadata, "catalog_size_arcmin")
    if major is None and size is not None:
        major = size
    if minor is None and major is not None:
        minor = major
    if major is None and minor is not None:
        major = minor
    if major is None or minor is None:
        return None
    if minor > major:
        major, minor = minor, major
    position_angle = _metadata_float(metadata, "catalog_position_angle_deg", require_positive=False)
    if position_angle is None:
        position_angle = 0.0
    return SkyExplorerCatalogSizeAxes(
        major_axis_arcmin=float(major),
        minor_axis_arcmin=float(minor),
        position_angle_deg=float(position_angle),
    )


def partition_sky_explorer_collage_objects(
    objects: Sequence[SkyExplorerObject],
) -> tuple[tuple[SkyExplorerObject, ...], tuple[SkyExplorerObject, ...]]:
    eligible: list[SkyExplorerObject] = []
    skipped: list[SkyExplorerObject] = []
    for sky_object in objects:
        if sky_explorer_object_has_catalog_size(sky_object):
            eligible.append(sky_object)
        else:
            skipped.append(sky_object)
    return tuple(eligible), tuple(skipped)


def automatic_collage_column_count(object_count: int) -> int:
    count = max(0, int(object_count))
    if count <= 0:
        return 1
    if count <= 2:
        return count
    return max(1, int(math.ceil(math.sqrt(count))))


def _sky_explorer_mosaic_sort_key(crop: SkyExplorerCollageCrop) -> tuple[float, int, str]:
    axes = sky_explorer_catalog_size_axes(crop.sky_object)
    angular_area = (
        float(axes.major_axis_arcmin) * float(axes.minor_axis_arcmin)
        if axes is not None
        else 0.0
    )
    pixel_area = int(crop.requested_width) * int(crop.requested_height)
    return angular_area, pixel_area, _object_label_text(crop.sky_object).casefold()


def order_sky_explorer_mosaic_crops(
    crops: Sequence[SkyExplorerCollageCrop],
) -> tuple[SkyExplorerCollageCrop, ...]:
    """Order mosaic crops by catalog sky footprint, largest first."""
    return tuple(sorted(crops, key=_sky_explorer_mosaic_sort_key, reverse=True))


def _mosaic_crop_weight(crop: SkyExplorerCollageCrop) -> float:
    axes = sky_explorer_catalog_size_axes(crop.sky_object)
    if axes is not None:
        return max(0.05, float(axes.major_axis_arcmin) * float(axes.minor_axis_arcmin))
    return max(0.05, float(crop.requested_width * crop.requested_height) / 10000.0)


def _mosaic_crop_aspect(crop: SkyExplorerCollageCrop) -> float:
    return max(0.05, float(crop.requested_width) / float(max(1, crop.requested_height)))


def _ensure_rgb_uint8(image: np.ndarray) -> np.ndarray:
    array = np.asarray(image)
    if array.ndim == 2:
        rgb = np.repeat(array[:, :, None], 3, axis=2)
    elif array.ndim == 3 and array.shape[2] >= 3:
        rgb = array[:, :, :3]
    else:
        raise ValueError("Rendered collage source must be grayscale or RGB.")
    if rgb.dtype != np.uint8:
        finite = np.asarray(rgb, dtype=np.float32)
        if float(np.nanmax(finite)) <= 1.0 + 1e-6:
            finite = finite * 255.0
        rgb = np.clip(np.nan_to_num(finite, nan=0.0), 0.0, 255.0).astype(np.uint8)
    return np.ascontiguousarray(rgb)


def _world_to_pixel(wcs: WCS, ra_deg: float, dec_deg: float) -> tuple[float, float] | None:
    try:
        x_values, y_values = wcs.world_to_pixel_values(float(ra_deg), float(dec_deg))
    except Exception:
        return None
    x_value = float(np.asarray(x_values).reshape(-1)[0])
    y_value = float(np.asarray(y_values).reshape(-1)[0])
    if not (math.isfinite(x_value) and math.isfinite(y_value)):
        return None
    return x_value, y_value


def _padded_sky_footprint_pixel_samples(
    sky_object: SkyExplorerObject,
    axes: SkyExplorerCatalogSizeAxes,
    wcs: WCS,
    *,
    margin_fraction: float,
) -> list[tuple[float, float]]:
    expansion = max(0.0, float(margin_fraction))
    major_half = float(axes.major_axis_arcmin) * 0.5 * (1.0 + expansion)
    minor_half = float(axes.minor_axis_arcmin) * 0.5 * (1.0 + expansion)
    center = SkyCoord(float(sky_object.ra_deg) * u.deg, float(sky_object.dec_deg) * u.deg)
    samples: list[tuple[float, float]] = []
    center_pixel = _world_to_pixel(wcs, float(sky_object.ra_deg), float(sky_object.dec_deg))
    if center_pixel is not None:
        samples.append(center_pixel)
    for index in range(_FOOTPRINT_SAMPLE_COUNT):
        angle = (2.0 * math.pi * index) / float(_FOOTPRINT_SAMPLE_COUNT)
        major_component = major_half * math.cos(angle)
        minor_component = minor_half * math.sin(angle)
        try:
            major_point = center.directional_offset_by(
                float(axes.position_angle_deg) * u.deg,
                float(major_component) * u.arcmin,
            )
            sample_coord = major_point.directional_offset_by(
                float(axes.position_angle_deg + 90.0) * u.deg,
                float(minor_component) * u.arcmin,
            )
        except Exception:
            continue
        pixel = _world_to_pixel(wcs, float(sample_coord.ra.deg), float(sample_coord.dec.deg))
        if pixel is not None:
            samples.append(pixel)
    for position_angle, half_axis in (
        (axes.position_angle_deg, major_half),
        (axes.position_angle_deg + 180.0, major_half),
        (axes.position_angle_deg + 90.0, minor_half),
        (axes.position_angle_deg + 270.0, minor_half),
    ):
        try:
            endpoint = center.directional_offset_by(float(position_angle) * u.deg, float(half_axis) * u.arcmin)
        except Exception:
            continue
        pixel = _world_to_pixel(wcs, float(endpoint.ra.deg), float(endpoint.dec.deg))
        if pixel is not None:
            samples.append(pixel)
    return samples


def sky_explorer_object_crop_pixel_bounds(
    sky_object: SkyExplorerObject,
    wcs: WCS,
    *,
    margin_fraction: float = DEFAULT_COLLAGE_MARGIN_FRACTION,
    image_width: int | None = None,
    image_height: int | None = None,
) -> tuple[int, int, int, int, bool, bool] | None:
    """Return (x0, y0, x1, y1, was_clipped, was_padded) for an axis-aligned crop centered on the object."""
    axes = sky_explorer_catalog_size_axes(sky_object)
    if axes is None:
        return None
    samples = _padded_sky_footprint_pixel_samples(
        sky_object,
        axes,
        wcs,
        margin_fraction=margin_fraction,
    )
    if not samples:
        return None
    center_x = float(sky_object.pixel_x)
    center_y = float(sky_object.pixel_y)
    center_pixel = _world_to_pixel(wcs, float(sky_object.ra_deg), float(sky_object.dec_deg))
    if center_pixel is not None:
        center_x, center_y = center_pixel
    half_width = max(abs(sample_x - center_x) for sample_x, _sample_y in samples)
    half_height = max(abs(sample_y - center_y) for _sample_x, sample_y in samples)
    half_width = max(_MIN_CROP_HALF_EXTENT_PX, float(half_width))
    half_height = max(_MIN_CROP_HALF_EXTENT_PX, float(half_height))
    requested_x0 = int(math.floor(center_x - half_width))
    requested_y0 = int(math.floor(center_y - half_height))
    requested_x1 = int(math.ceil(center_x + half_width))
    requested_y1 = int(math.ceil(center_y + half_height))
    if requested_x1 <= requested_x0:
        requested_x1 = requested_x0 + 1
    if requested_y1 <= requested_y0:
        requested_y1 = requested_y0 + 1

    was_clipped = False
    was_padded = False
    x0, y0, x1, y1 = requested_x0, requested_y0, requested_x1, requested_y1
    if image_width is not None and image_height is not None:
        width = int(image_width)
        height = int(image_height)
        if requested_x0 < 0 or requested_y0 < 0 or requested_x1 > width or requested_y1 > height:
            was_padded = True
        clipped_x0 = max(0, requested_x0)
        clipped_y0 = max(0, requested_y0)
        clipped_x1 = min(width, requested_x1)
        clipped_y1 = min(height, requested_y1)
        if clipped_x1 <= clipped_x0 or clipped_y1 <= clipped_y0:
            return None
        if (clipped_x0, clipped_y0, clipped_x1, clipped_y1) != (requested_x0, requested_y0, requested_x1, requested_y1):
            was_clipped = True
        # Keep requested bounds for padding so the object stays centered in the tile.
        x0, y0, x1, y1 = requested_x0, requested_y0, requested_x1, requested_y1
    return x0, y0, x1, y1, was_clipped, was_padded


def crop_sky_explorer_object_from_rendered(
    rendered_image: np.ndarray,
    sky_object: SkyExplorerObject,
    wcs: WCS,
    *,
    margin_fraction: float = DEFAULT_COLLAGE_MARGIN_FRACTION,
    pad_rgb: tuple[int, int, int] = (0, 0, 0),
) -> SkyExplorerCollageCrop | None:
    rgb = _ensure_rgb_uint8(rendered_image)
    height, width = rgb.shape[:2]
    bounds = sky_explorer_object_crop_pixel_bounds(
        sky_object,
        wcs,
        margin_fraction=margin_fraction,
        image_width=width,
        image_height=height,
    )
    if bounds is None:
        return None
    x0, y0, x1, y1, was_clipped, was_padded = bounds
    crop_width = max(1, x1 - x0)
    crop_height = max(1, y1 - y0)
    crop = np.full((crop_height, crop_width, 3), pad_rgb, dtype=np.uint8)

    src_x0 = max(0, x0)
    src_y0 = max(0, y0)
    src_x1 = min(width, x1)
    src_y1 = min(height, y1)
    if src_x1 <= src_x0 or src_y1 <= src_y0:
        return None
    dst_x0 = src_x0 - x0
    dst_y0 = src_y0 - y0
    dst_x1 = dst_x0 + (src_x1 - src_x0)
    dst_y1 = dst_y0 + (src_y1 - src_y0)
    crop[dst_y0:dst_y1, dst_x0:dst_x1] = rgb[src_y0:src_y1, src_x0:src_x1]
    return SkyExplorerCollageCrop(
        sky_object=sky_object,
        image_rgb=np.ascontiguousarray(crop),
        was_clipped=was_clipped,
        was_padded=was_padded,
        requested_width=crop_width,
        requested_height=crop_height,
    )


def _fit_image_into_tile(
    image_rgb: np.ndarray,
    tile_width: int,
    tile_height: int,
    *,
    background_rgb: tuple[int, int, int],
) -> np.ndarray:
    source = _ensure_rgb_uint8(image_rgb)
    source_height, source_width = source.shape[:2]
    if source_width <= 0 or source_height <= 0:
        return np.full((tile_height, tile_width, 3), background_rgb, dtype=np.uint8)
    scale = min(float(tile_width) / float(source_width), float(tile_height) / float(source_height))
    scaled_width = max(1, int(round(source_width * scale)))
    scaled_height = max(1, int(round(source_height * scale)))
    pil_image = Image.fromarray(source, mode="RGB").resize((scaled_width, scaled_height), Image.Resampling.BILINEAR)
    tile = Image.new("RGB", (tile_width, tile_height), background_rgb)
    offset_x = max(0, (tile_width - scaled_width) // 2)
    offset_y = max(0, (tile_height - scaled_height) // 2)
    tile.paste(pil_image, (offset_x, offset_y))
    return np.asarray(tile, dtype=np.uint8)


def _object_label_text(sky_object: SkyExplorerObject) -> str:
    label = str(sky_object.short_label or sky_object.name or sky_object.source_id or "Object").strip()
    return label or "Object"


def _draw_label_strip(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    text: str,
    background_rgb: tuple[int, int, int],
    text_rgb: tuple[int, int, int],
) -> None:
    draw.rectangle((x, y, x + width - 1, y + height - 1), fill=background_rgb)
    font = ImageFont.load_default()
    label = text if len(text) <= 48 else f"{text[:45]}..."
    try:
        text_bbox = draw.textbbox((0, 0), label, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
    except Exception:
        text_width, text_height = draw.textsize(label, font=font)
    text_x = x + max(4, (width - text_width) // 2)
    text_y = y + max(2, (height - text_height) // 2)
    draw.text((text_x, text_y), label, fill=text_rgb, font=font)


@dataclass(frozen=True, slots=True)
class _MosaicTilePlacement:
    crop: SkyExplorerCollageCrop
    x: int
    y: int
    width: int
    height: int


def _partition_mosaic_items(
    items: Sequence[tuple[SkyExplorerCollageCrop, float]],
) -> tuple[list[tuple[SkyExplorerCollageCrop, float]], list[tuple[SkyExplorerCollageCrop, float]]]:
    ordered = list(items)
    if len(ordered) <= 1:
        return ordered, []
    total_weight = sum(weight for _crop, weight in ordered)
    best_index = 1
    best_score = float("inf")
    for index in range(1, len(ordered)):
        left_weight = sum(weight for _crop, weight in ordered[:index])
        right_weight = total_weight - left_weight
        balance = abs(left_weight - right_weight) / max(total_weight, 1e-6)
        # Prefer keeping the largest crop alone when it dominates the set.
        dominance_bonus = 0.0
        if index == 1 and left_weight >= 0.42 * total_weight:
            dominance_bonus = -0.08
        score = balance + dominance_bonus
        if score < best_score:
            best_score = score
            best_index = index
    return ordered[:best_index], ordered[best_index:]


def _should_split_mosaic_vertically(
    items: Sequence[tuple[SkyExplorerCollageCrop, float]],
    *,
    width: int,
    height: int,
) -> bool:
    total_weight = sum(weight for _crop, weight in items) or 1.0
    mean_aspect = sum(_mosaic_crop_aspect(crop) * weight for crop, weight in items) / total_weight
    region_aspect = float(width) / float(max(1, height))
    if len(items) == 2:
        left_aspect = _mosaic_crop_aspect(items[0][0])
        right_aspect = _mosaic_crop_aspect(items[1][0])
        # Side-by-side when both are tall-ish relative to a wide canvas.
        if region_aspect >= 1.15 and min(left_aspect, right_aspect) <= 1.05:
            return True
        if region_aspect <= 0.9 and max(left_aspect, right_aspect) >= 1.2:
            return False
    if region_aspect >= mean_aspect:
        return True
    return region_aspect >= 1.0


def _layout_mosaic_tiles(
    items: Sequence[tuple[SkyExplorerCollageCrop, float]],
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    gap: int,
    placements: list[_MosaicTilePlacement],
) -> None:
    if not items or width <= 0 or height <= 0:
        return
    if len(items) == 1:
        placements.append(
            _MosaicTilePlacement(
                crop=items[0][0],
                x=int(x),
                y=int(y),
                width=max(1, int(width)),
                height=max(1, int(height)),
            )
        )
        return

    left_items, right_items = _partition_mosaic_items(items)
    if not left_items or not right_items:
        placements.append(
            _MosaicTilePlacement(
                crop=items[0][0],
                x=int(x),
                y=int(y),
                width=max(1, int(width)),
                height=max(1, int(height)),
            )
        )
        return

    left_weight = sum(weight for _crop, weight in left_items)
    right_weight = sum(weight for _crop, weight in right_items)
    total_weight = max(left_weight + right_weight, 1e-6)
    split_vertical = _should_split_mosaic_vertically(items, width=width, height=height)

    if split_vertical and width > gap + 2:
        left_width = max(1, int(round((width - gap) * (left_weight / total_weight))))
        left_width = min(left_width, width - gap - 1)
        right_width = max(1, width - gap - left_width)
        _layout_mosaic_tiles(
            left_items,
            x=x,
            y=y,
            width=left_width,
            height=height,
            gap=gap,
            placements=placements,
        )
        _layout_mosaic_tiles(
            right_items,
            x=x + left_width + gap,
            y=y,
            width=right_width,
            height=height,
            gap=gap,
            placements=placements,
        )
        return

    top_height = max(1, int(round((height - gap) * (left_weight / total_weight))))
    top_height = min(top_height, height - gap - 1)
    bottom_height = max(1, height - gap - top_height)
    _layout_mosaic_tiles(
        left_items,
        x=x,
        y=y,
        width=width,
        height=top_height,
        gap=gap,
        placements=placements,
    )
    _layout_mosaic_tiles(
        right_items,
        x=x,
        y=y + top_height + gap,
        width=width,
        height=bottom_height,
        gap=gap,
        placements=placements,
    )


def _cover_resize_rgb(image_rgb: np.ndarray, target_width: int, target_height: int) -> np.ndarray:
    source = _ensure_rgb_uint8(image_rgb)
    source_height, source_width = source.shape[:2]
    target_width = max(1, int(target_width))
    target_height = max(1, int(target_height))
    if source_width <= 0 or source_height <= 0:
        return np.zeros((target_height, target_width, 3), dtype=np.uint8)
    scale = max(float(target_width) / float(source_width), float(target_height) / float(source_height))
    scaled_width = max(1, int(math.ceil(source_width * scale)))
    scaled_height = max(1, int(math.ceil(source_height * scale)))
    resized = Image.fromarray(source, mode="RGB").resize((scaled_width, scaled_height), Image.Resampling.BILINEAR)
    left = max(0, (scaled_width - target_width) // 2)
    top = max(0, (scaled_height - target_height) // 2)
    cropped = resized.crop((left, top, left + target_width, top + target_height))
    return np.asarray(cropped, dtype=np.uint8)


def _draw_mosaic_label_badge(
    canvas: Image.Image,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    text: str,
    background_rgb: tuple[int, int, int],
    text_rgb: tuple[int, int, int],
) -> None:
    draw = ImageDraw.Draw(canvas, "RGBA")
    font = ImageFont.load_default()
    label = text if len(text) <= 36 else f"{text[:33]}..."
    try:
        text_bbox = draw.textbbox((0, 0), label, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
    except Exception:
        text_width, text_height = draw.textsize(label, font=font)
    badge_width = min(width - 8, text_width + 12)
    badge_height = min(height - 8, text_height + 8)
    if badge_width <= 0 or badge_height <= 0:
        return
    badge_x = x + 6
    badge_y = y + max(6, height - badge_height - 6)
    fill = (*background_rgb, 210)
    draw.rectangle(
        (badge_x, badge_y, badge_x + badge_width - 1, badge_y + badge_height - 1),
        fill=fill,
    )
    draw.text((badge_x + 6, badge_y + 4), label, fill=(*text_rgb, 255), font=font)


def _estimate_mosaic_canvas_size(
    crops: Sequence[SkyExplorerCollageCrop],
    *,
    target_width: int,
    gap: int,
    padding: int,
) -> tuple[int, int]:
    total_area = sum(max(1, crop.requested_width * crop.requested_height) for crop in crops)
    mean_aspect = sum(_mosaic_crop_aspect(crop) for crop in crops) / float(max(1, len(crops)))
    width = max(480, int(target_width))
    # Choose a height that roughly preserves mean crop aspect while leaving room for gutters.
    content_width = max(1, width - padding * 2)
    estimated_height = int(round(content_width / max(0.55, min(1.85, mean_aspect))))
    # Softly pull toward sqrt(area) packing proportions.
    sqrt_area_height = int(round(math.sqrt(max(1.0, float(total_area)) / max(mean_aspect, 0.2))))
    height = int(round(0.65 * estimated_height + 0.35 * sqrt_area_height))
    height = max(360, height + padding * 2 + gap)
    return width, height


def _compose_sky_explorer_mosaic(
    crops: Sequence[SkyExplorerCollageCrop],
    options: SkyExplorerCollageOptions,
) -> tuple[np.ndarray, int, int]:
    ordered_crops = order_sky_explorer_mosaic_crops(crops)
    gap = max(DEFAULT_MOSAIC_GAP_PX, int(options.gap_px))
    padding = max(8, int(options.padding_px))
    canvas_width, canvas_height = _estimate_mosaic_canvas_size(
        ordered_crops,
        target_width=max(480, int(options.mosaic_target_width_px)),
        gap=gap,
        padding=padding,
    )
    content_x = padding
    content_y = padding
    content_width = max(1, canvas_width - padding * 2)
    content_height = max(1, canvas_height - padding * 2)
    weighted_items = [(crop, _mosaic_crop_weight(crop)) for crop in ordered_crops]
    placements: list[_MosaicTilePlacement] = []
    _layout_mosaic_tiles(
        weighted_items,
        x=content_x,
        y=content_y,
        width=content_width,
        height=content_height,
        gap=gap,
        placements=placements,
    )

    canvas = Image.new("RGBA", (canvas_width, canvas_height), (*options.canvas_rgb, 255))
    for placement in placements:
        covered = _cover_resize_rgb(placement.crop.image_rgb, placement.width, placement.height)
        canvas.paste(Image.fromarray(covered, mode="RGB"), (placement.x, placement.y))
        if options.show_labels:
            _draw_mosaic_label_badge(
                canvas,
                x=placement.x,
                y=placement.y,
                width=placement.width,
                height=placement.height,
                text=_object_label_text(placement.crop.sky_object),
                background_rgb=options.tile_background_rgb,
                text_rgb=options.label_rgb,
            )

    # Approximate column/row counts for summary UI from the placement tree shape.
    unique_x = sorted({placement.x for placement in placements})
    unique_y = sorted({placement.y for placement in placements})
    columns = max(1, len(unique_x))
    rows = max(1, len(unique_y))
    return np.asarray(canvas.convert("RGB"), dtype=np.uint8), columns, rows


def compose_sky_explorer_collage(
    crops: Sequence[SkyExplorerCollageCrop],
    options: SkyExplorerCollageOptions | None = None,
) -> tuple[np.ndarray, int, int]:
    collage_options = options or SkyExplorerCollageOptions()
    if not crops:
        raise ValueError("At least one collage crop is required.")
    columns = int(collage_options.columns) if collage_options.columns and collage_options.columns > 0 else automatic_collage_column_count(len(crops))
    columns = max(1, min(columns, len(crops)))
    rows = int(math.ceil(len(crops) / float(columns)))
    gap = max(0, int(collage_options.gap_px))
    padding = max(0, int(collage_options.padding_px))
    label_height = max(0, int(collage_options.label_height_px)) if collage_options.show_labels else 0

    if collage_options.layout == "mosaic":
        return _compose_sky_explorer_mosaic(crops, collage_options)

    if collage_options.layout == "uniform":
        tile_width = max(crop.image_rgb.shape[1] for crop in crops)
        tile_height = max(crop.image_rgb.shape[0] for crop in crops)
        cell_widths = [tile_width] * columns
        cell_heights = [tile_height] * rows
        prepared: list[np.ndarray] = [
            _fit_image_into_tile(
                crop.image_rgb,
                tile_width,
                tile_height,
                background_rgb=collage_options.tile_background_rgb,
            )
            for crop in crops
        ]
    else:
        cell_widths = [0] * columns
        cell_heights = [0] * rows
        prepared = [_ensure_rgb_uint8(crop.image_rgb) for crop in crops]
        for index, image in enumerate(prepared):
            row_index = index // columns
            column_index = index % columns
            cell_widths[column_index] = max(cell_widths[column_index], int(image.shape[1]))
            cell_heights[row_index] = max(cell_heights[row_index], int(image.shape[0]))

    canvas_width = padding * 2 + sum(cell_widths) + gap * max(0, columns - 1)
    canvas_height = padding * 2 + sum(cell_heights) + gap * max(0, rows - 1) + label_height * rows
    canvas = Image.new("RGB", (canvas_width, canvas_height), collage_options.canvas_rgb)
    draw = ImageDraw.Draw(canvas)

    for index, (crop, image) in enumerate(zip(crops, prepared)):
        row_index = index // columns
        column_index = index % columns
        cell_width = cell_widths[column_index]
        cell_height = cell_heights[row_index]
        x = padding + sum(cell_widths[:column_index]) + gap * column_index
        y = padding + sum(cell_heights[:row_index]) + gap * row_index + label_height * row_index
        if collage_options.layout == "natural":
            tile = _fit_image_into_tile(
                image,
                cell_width,
                cell_height,
                background_rgb=collage_options.tile_background_rgb,
            )
        else:
            tile = image
        canvas.paste(Image.fromarray(tile, mode="RGB"), (x, y))
        if collage_options.show_labels and label_height > 0:
            _draw_label_strip(
                draw,
                x=x,
                y=y + cell_height,
                width=cell_width,
                height=label_height,
                text=_object_label_text(crop.sky_object),
                background_rgb=collage_options.tile_background_rgb,
                text_rgb=collage_options.label_rgb,
            )
    return np.asarray(canvas, dtype=np.uint8), columns, rows


def build_sky_explorer_collage(
    *,
    source_image_path: Path,
    objects: Sequence[SkyExplorerObject],
    wcs: WCS,
    render_settings: AnnotatedImageRenderSettings | None = None,
    options: SkyExplorerCollageOptions | None = None,
) -> SkyExplorerCollageResult:
    collage_options = options or SkyExplorerCollageOptions()
    eligible, skipped = partition_sky_explorer_collage_objects(objects)
    if not eligible:
        raise ValueError("No eligible Sky Explorer objects with catalog size metadata were provided.")
    rendered = render_image_path_for_display(Path(source_image_path), render_settings)
    rgb = _ensure_rgb_uint8(rendered)
    crops: list[SkyExplorerCollageCrop] = []
    failed: list[SkyExplorerObject] = []
    for sky_object in eligible:
        crop = crop_sky_explorer_object_from_rendered(
            rgb,
            sky_object,
            wcs,
            margin_fraction=collage_options.margin_fraction,
            pad_rgb=collage_options.tile_background_rgb,
        )
        if crop is None:
            failed.append(sky_object)
            continue
        crops.append(crop)
    if not crops:
        raise ValueError("Unable to crop any eligible Sky Explorer objects for the collage.")
    if collage_options.layout == "mosaic":
        crops = list(order_sky_explorer_mosaic_crops(crops))
    image_rgb, columns, rows = compose_sky_explorer_collage(crops, collage_options)
    return SkyExplorerCollageResult(
        image_rgb=image_rgb,
        included_objects=tuple(crop.sky_object for crop in crops),
        skipped_objects=tuple(list(skipped) + failed),
        clipped_count=sum(1 for crop in crops if crop.was_clipped),
        padded_count=sum(1 for crop in crops if crop.was_padded),
        columns=columns,
        rows=rows,
        layout=collage_options.layout,
        margin_fraction=float(collage_options.margin_fraction),
    )


def save_sky_explorer_collage_image(image_rgb: np.ndarray, output_path: Path) -> None:
    path = Path(output_path)
    rgb = _ensure_rgb_uint8(image_rgb)
    suffix = path.suffix.lower()
    image = Image.fromarray(rgb, mode="RGB")
    if suffix in {".jpg", ".jpeg"}:
        image.save(path, format="JPEG", quality=95)
    elif suffix == ".bmp":
        image.save(path, format="BMP")
    else:
        if suffix != ".png":
            path = path.with_suffix(".png")
        image.save(path, format="PNG")


def format_sky_explorer_collage_summary(
    *,
    candidate_count: int,
    eligible_count: int,
    skipped_missing_size_count: int,
    included_count: int | None = None,
    clipped_count: int = 0,
    padded_count: int = 0,
    columns: int | None = None,
    rows: int | None = None,
    layout: SkyExplorerCollageLayout | None = None,
    margin_fraction: float = DEFAULT_COLLAGE_MARGIN_FRACTION,
    output_width: int | None = None,
    output_height: int | None = None,
) -> str:
    included = included_count if included_count is not None else eligible_count
    margin_percent = max(0.0, float(margin_fraction) * 100.0)
    lines = [
        f"Candidates: {int(candidate_count)}",
        f"Eligible with catalog size: {int(eligible_count)}",
        f"Skipped (missing size): {int(skipped_missing_size_count)}",
        f"Included in collage: {int(included)}",
        f"Margin: {margin_percent:.0f}%",
    ]
    if layout is not None:
        layout_label = {
            "uniform": "Uniform Tiles",
            "natural": "Natural Sizes",
            "mosaic": "Packed Mosaic",
        }[layout]
        lines.append(f"Layout: {layout_label}")
    if columns is not None and rows is not None:
        if layout == "mosaic":
            lines.append(f"Packed mosaic with {int(included)} interlocking tile(s)")
        else:
            lines.append(f"Grid: {int(columns)} × {int(rows)}")
    if clipped_count or padded_count:
        lines.append(f"Edge crops clipped: {int(clipped_count)}; padded: {int(padded_count)}")
    if output_width is not None and output_height is not None:
        lines.append(f"Output size: {int(output_width)} × {int(output_height)} px")
    return "\n".join(lines)
