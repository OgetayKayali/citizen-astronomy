from __future__ import annotations

import json
import math
import shutil
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable, Sequence

import numpy as np
from PIL import Image
from PySide6.QtGui import QImage, QImageReader

from photometry_app.core.milky_way_assets import (
    load_milky_way_manifest,
    resolve_manifest_tile_path,
    tile_grid_dimensions,
    tiles_from_radec_bounds,
)
from photometry_app.ui.sky_view_milky_way_gl import OpenGLMilkyWayLayer, SkyViewMilkyWayTileDrawRequest


@dataclass(frozen=True, slots=True)
class MilkyWayTileSampleRegion:
    name: str
    min_ra_deg: float
    max_ra_deg: float
    min_dec_deg: float
    max_dec_deg: float


@dataclass(frozen=True, slots=True)
class MilkyWayTileSample:
    region_name: str
    level: int
    x_index: int
    y_index: int
    source_path: Path
    width: int
    height: int
    file_size_bytes: int
    tile_grid_shape: tuple[int, int]


@dataclass(frozen=True, slots=True)
class MilkyWayTileFormatVariant:
    name: str
    suffix: str
    save_options: tuple[tuple[str, object], ...]
    lossless_expected: bool
    dependency_risk: str
    platform_risk: str
    compatibility_note: str


@dataclass(frozen=True, slots=True)
class MilkyWayVariantTileAsset:
    variant_name: str
    source_sample: MilkyWayTileSample
    output_path: Path
    output_size_bytes: int
    pixel_equal: bool
    differing_pixel_count: int
    max_channel_delta: int
    orientation_tag: int | None
    source_format: str
    error: str | None = None


DEFAULT_SAMPLE_REGIONS: tuple[MilkyWayTileSampleRegion, ...] = (
    MilkyWayTileSampleRegion("galactic_center_dense", 252.0, 282.0, -40.0, -16.0),
    MilkyWayTileSampleRegion("cygnus_rich", 292.0, 320.0, 22.0, 52.0),
    MilkyWayTileSampleRegion("outskirts_faint", 96.0, 144.0, -8.0, 18.0),
    MilkyWayTileSampleRegion("dark_sky", 188.0, 236.0, 18.0, 48.0),
    MilkyWayTileSampleRegion("ra_seam_adjacent", 350.0, 10.0, -16.0, 16.0),
    MilkyWayTileSampleRegion("polar_adjacent", 20.0, 70.0, 68.0, 88.0),
)


def supported_qimage_formats() -> set[str]:
    return {bytes(image_format).decode("ascii", "ignore").strip().casefold() for image_format in QImageReader.supportedImageFormats()}


def default_format_variants() -> tuple[MilkyWayTileFormatVariant, ...]:
    return (
        MilkyWayTileFormatVariant(
            name="png_current",
            suffix=".png",
            save_options=(),
            lossless_expected=True,
            dependency_risk="lowest",
            platform_risk="lowest",
            compatibility_note="Current production tile format and current runtime decode path.",
        ),
        MilkyWayTileFormatVariant(
            name="tiff_deflate",
            suffix=".tiff",
            save_options=(("compression", "tiff_adobe_deflate"),),
            lossless_expected=True,
            dependency_risk="low",
            platform_risk="medium",
            compatibility_note="Requires Qt TIFF decoder plugin and Pillow TIFF writer support.",
        ),
        MilkyWayTileFormatVariant(
            name="tiff_lzw",
            suffix=".tiff",
            save_options=(("compression", "tiff_lzw"),),
            lossless_expected=True,
            dependency_risk="low",
            platform_risk="medium",
            compatibility_note="Same runtime decoder path as TIFF-deflate with alternate TIFF compression.",
        ),
        MilkyWayTileFormatVariant(
            name="tiff_raw",
            suffix=".tiff",
            save_options=(("compression", "raw"),),
            lossless_expected=True,
            dependency_risk="low",
            platform_risk="medium",
            compatibility_note="Useful upper bound for TIFF decode speed at much larger disk size.",
        ),
        MilkyWayTileFormatVariant(
            name="webp_lossless",
            suffix=".webp",
            save_options=(("lossless", True), ("quality", 100), ("method", 6)),
            lossless_expected=True,
            dependency_risk="medium",
            platform_risk="medium",
            compatibility_note="Requires Qt WebP decoder support and Pillow WebP writer support.",
        ),
    )


def available_format_variants(*, qimage_formats: set[str] | None = None) -> tuple[MilkyWayTileFormatVariant, ...]:
    resolved_formats = qimage_formats or supported_qimage_formats()
    variants: list[MilkyWayTileFormatVariant] = []
    for variant in default_format_variants():
        if variant.suffix == ".png":
            variants.append(variant)
            continue
        suffix_name = variant.suffix.lstrip(".").casefold()
        if suffix_name not in resolved_formats:
            continue
        variants.append(variant)
    return tuple(variants)


def select_representative_tile_samples(
    manifest_path: Path,
    *,
    regions: Sequence[MilkyWayTileSampleRegion] = DEFAULT_SAMPLE_REGIONS,
    levels: Sequence[int] = (2, 3, 4),
    per_bucket_cap: int = 12,
) -> tuple[MilkyWayTileSample, ...]:
    manifest = load_milky_way_manifest(manifest_path)
    samples: list[MilkyWayTileSample] = []
    seen: set[tuple[int, int, int]] = set()

    for region in regions:
        for level_number in levels:
            level = manifest.level_by_number(level_number)
            tile_count_x, tile_count_y = tile_grid_dimensions(level.width, level.height, manifest.tile_size)
            selected_tiles = sorted(
                tiles_from_radec_bounds(
                    region.min_ra_deg,
                    region.max_ra_deg,
                    region.min_dec_deg,
                    region.max_dec_deg,
                    level_width=level.width,
                    level_height=level.height,
                    tile_size=manifest.tile_size,
                    ra_increases=manifest.orientation_ra_increases,
                    margin=0,
                )
            )
            for x_index, y_index in _sample_evenly(selected_tiles, per_bucket_cap):
                tile_key = (int(level_number), int(x_index), int(y_index))
                if tile_key in seen:
                    continue
                tile_path = resolve_manifest_tile_path(manifest, level_number, x_index, y_index)
                if not tile_path.is_file():
                    continue
                width, height = image_dimensions(tile_path)
                samples.append(
                    MilkyWayTileSample(
                        region_name=region.name,
                        level=int(level_number),
                        x_index=int(x_index),
                        y_index=int(y_index),
                        source_path=tile_path,
                        width=width,
                        height=height,
                        file_size_bytes=int(tile_path.stat().st_size),
                        tile_grid_shape=(tile_count_x, tile_count_y),
                    )
                )
                seen.add(tile_key)

    return tuple(sorted(samples, key=lambda item: (item.level, item.region_name, item.y_index, item.x_index)))


def image_dimensions(image_path: Path) -> tuple[int, int]:
    with Image.open(image_path) as image:
        return int(image.width), int(image.height)


def materialize_variant_assets(
    samples: Sequence[MilkyWayTileSample],
    output_root: Path,
    *,
    variants: Sequence[MilkyWayTileFormatVariant],
    overwrite: bool = True,
) -> tuple[MilkyWayVariantTileAsset, ...]:
    output_root = output_root.resolve()
    if output_root.exists() and overwrite:
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    assets: list[MilkyWayVariantTileAsset] = []
    for sample in samples:
        reference_array = read_pil_rgb_array(sample.source_path)
        for variant in variants:
            variant_output_path = output_root / variant.name / f"L{sample.level}" / f"{sample.x_index}_{sample.y_index}{variant.suffix}"
            variant_output_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                write_variant_asset(sample.source_path, variant_output_path, variant=variant)
                equality = verify_lossless_pixel_equality(sample.source_path, variant_output_path)
                orientation_tag = orientation_tag_value(variant_output_path)
                source_format = qimage_format_name(QImage(str(variant_output_path)))
                assets.append(
                    MilkyWayVariantTileAsset(
                        variant_name=variant.name,
                        source_sample=sample,
                        output_path=variant_output_path,
                        output_size_bytes=int(variant_output_path.stat().st_size),
                        pixel_equal=bool(equality["equal"]),
                        differing_pixel_count=int(equality["differing_pixel_count"]),
                        max_channel_delta=int(equality["max_channel_delta"]),
                        orientation_tag=orientation_tag,
                        source_format=source_format,
                    )
                )
            except Exception as exc:
                assets.append(
                    MilkyWayVariantTileAsset(
                        variant_name=variant.name,
                        source_sample=sample,
                        output_path=variant_output_path,
                        output_size_bytes=0,
                        pixel_equal=False,
                        differing_pixel_count=int(reference_array.shape[0] * reference_array.shape[1]),
                        max_channel_delta=255,
                        orientation_tag=None,
                        source_format="unknown",
                        error=f"{exc.__class__.__name__}: {exc}",
                    )
                )
    return tuple(assets)


def write_variant_asset(source_path: Path, output_path: Path, *, variant: MilkyWayTileFormatVariant) -> None:
    if variant.name == "png_current":
        shutil.copy2(source_path, output_path)
        return

    save_options = {key: value for key, value in variant.save_options}
    with Image.open(source_path) as image:
        image.convert("RGB").save(output_path, **save_options)


def read_pil_rgb_array(image_path: Path) -> np.ndarray:
    with Image.open(image_path) as image:
        return np.asarray(image.convert("RGB"), dtype=np.uint8)


def qimage_rgb_array(image_path: Path) -> np.ndarray:
    image = QImage(str(image_path))
    if image.isNull():
        raise ValueError(f"Failed to decode image with QImage: {image_path}")
    rgb_image = image.convertToFormat(QImage.Format.Format_RGB888)
    bytes_per_line = int(rgb_image.bytesPerLine())
    buffer = bytes(rgb_image.bits())
    array = np.frombuffer(buffer, dtype=np.uint8).reshape((int(rgb_image.height()), bytes_per_line))
    return array[:, : int(rgb_image.width()) * 3].reshape((int(rgb_image.height()), int(rgb_image.width()), 3)).copy()


def verify_lossless_pixel_equality(reference_path: Path, candidate_path: Path) -> dict[str, object]:
    reference = read_pil_rgb_array(reference_path)
    candidate = read_pil_rgb_array(candidate_path)
    if reference.shape != candidate.shape:
        return {
            "equal": False,
            "differing_pixel_count": int(reference.shape[0] * reference.shape[1]),
            "max_channel_delta": 255,
            "shape_mismatch": True,
        }
    differences = np.abs(reference.astype(np.int16) - candidate.astype(np.int16))
    differing_pixels = np.any(differences != 0, axis=2)
    return {
        "equal": bool(not np.any(differing_pixels)),
        "differing_pixel_count": int(np.count_nonzero(differing_pixels)),
        "max_channel_delta": int(np.max(differences)) if differences.size else 0,
        "shape_mismatch": False,
    }


def orientation_tag_value(image_path: Path) -> int | None:
    try:
        with Image.open(image_path) as image:
            tags = getattr(image, "tag_v2", None)
            if tags is None:
                return None
            value = tags.get(274)
            if value is None:
                return None
            return int(value)
    except Exception:
        return None


def benchmark_decode_variants(
    assets: Sequence[MilkyWayVariantTileAsset],
    *,
    repetitions: int,
) -> tuple[dict[str, object], ...]:
    measurements: list[dict[str, object]] = []
    resolved_repetitions = max(1, int(repetitions))
    for asset in assets:
        if asset.error is not None:
            continue
        for repetition_index in range(resolved_repetitions):
            measurements.append(measure_read_decode_convert(asset, repetition_index=repetition_index))
    return tuple(measurements)


def measure_read_decode_convert(asset: MilkyWayVariantTileAsset, *, repetition_index: int) -> dict[str, object]:
    read_start = perf_counter()
    payload = asset.output_path.read_bytes()
    read_seconds = perf_counter() - read_start

    decode_start = perf_counter()
    image = QImage.fromData(payload)
    decode_seconds = perf_counter() - decode_start
    if image.isNull():
        raise ValueError(f"QImage failed to decode {asset.output_path}")

    convert_start = perf_counter()
    rgb_image = image.convertToFormat(QImage.Format.Format_RGB888)
    convert_seconds = perf_counter() - convert_start

    return {
        "variant_name": asset.variant_name,
        "region_name": asset.source_sample.region_name,
        "level": int(asset.source_sample.level),
        "x_index": int(asset.source_sample.x_index),
        "y_index": int(asset.source_sample.y_index),
        "file_path": str(asset.output_path),
        "file_size_bytes": int(asset.output_size_bytes),
        "decoded_size_bytes": image_approx_bytes(rgb_image),
        "read_ms": read_seconds * 1000.0,
        "decode_ms": decode_seconds * 1000.0,
        "convert_ms": convert_seconds * 1000.0,
        "total_ms": (read_seconds + decode_seconds + convert_seconds) * 1000.0,
        "repetition_index": int(repetition_index),
        "source_format": qimage_format_name(image),
        "output_format": qimage_format_name(rgb_image),
    }


def benchmark_prepared_tile_variants(
    assets: Sequence[MilkyWayVariantTileAsset],
    *,
    manifest_path: Path,
    repetitions: int,
) -> tuple[dict[str, object], ...]:
    manifest = load_milky_way_manifest(manifest_path)
    measurements: list[dict[str, object]] = []
    resolved_repetitions = max(1, int(repetitions))
    for repetition_index in range(resolved_repetitions):
        layer = OpenGLMilkyWayLayer()
        for asset in assets:
            if asset.error is not None:
                continue
            layer.reset_frame_diagnostics()
            cache_key = (
                asset.variant_name,
                str(asset.output_path),
                repetition_index,
                int(asset.source_sample.level),
                int(asset.source_sample.x_index),
                int(asset.source_sample.y_index),
            )
            tile_request = SkyViewMilkyWayTileDrawRequest(
                texture_path=asset.output_path,
                texture_cache_key=cache_key,
                level=int(asset.source_sample.level),
                x_index=int(asset.source_sample.x_index),
                y_index=int(asset.source_sample.y_index),
                vertices=(),
                tile_grid_shape=asset.source_sample.tile_grid_shape,
                padded_tile=bool(manifest.padded_tile),
                gutter_pixels=int(manifest.gutter_pixels),
                content_region=tuple(int(value) for value in manifest.content_region),
            )
            prepared_entry, cache_hit = layer._resolve_prepared_tile(tile_request, cache_budget_bytes=1024 * 1024 * 1024)
            if prepared_entry is None:
                raise ValueError(f"Prepared tile resolution failed for {asset.output_path}")
            measurements.append(
                {
                    "variant_name": asset.variant_name,
                    "region_name": asset.source_sample.region_name,
                    "level": int(asset.source_sample.level),
                    "x_index": int(asset.source_sample.x_index),
                    "y_index": int(asset.source_sample.y_index),
                    "file_path": str(asset.output_path),
                    "file_size_bytes": int(asset.output_size_bytes),
                    "prepare_total_ms": prepared_entry.prepare_total_seconds * 1000.0,
                    "read_ms": prepared_entry.file_read_seconds * 1000.0,
                    "decode_ms": prepared_entry.decode_seconds * 1000.0,
                    "convert_ms": prepared_entry.convert_seconds * 1000.0,
                    "padding_ms": prepared_entry.padding_seconds * 1000.0,
                    "upload_image_bytes": int(prepared_entry.approx_bytes),
                    "output_width": int(prepared_entry.width),
                    "output_height": int(prepared_entry.height),
                    "core_width": int(prepared_entry.core_width),
                    "core_height": int(prepared_entry.core_height),
                    "cache_hit": bool(cache_hit),
                    "repetition_index": int(repetition_index),
                    "pixel_equal": bool(asset.pixel_equal),
                }
            )
    return tuple(measurements)


def image_approx_bytes(image: QImage) -> int:
    try:
        return max(1, int(image.sizeInBytes()))
    except Exception:
        return max(1, int(image.bytesPerLine()) * int(image.height()))


def qimage_format_name(image: QImage) -> str:
    try:
        format_value = image.format()
        return str(getattr(format_value, "name", format_value))
    except Exception:
        return "unknown"


def aggregate_measurements(
    measurements: Sequence[dict[str, object]],
    *,
    metric_fields: Sequence[str],
) -> dict[str, dict[str, dict[str, float]]]:
    grouped: dict[str, dict[str, list[dict[str, object]]]] = {}
    for measurement in measurements:
        variant_name = str(measurement.get("variant_name", "unknown"))
        level_key = f"L{int(measurement.get('level', 0))}"
        grouped.setdefault(variant_name, {}).setdefault("all", []).append(measurement)
        grouped.setdefault(variant_name, {}).setdefault(level_key, []).append(measurement)

    summary: dict[str, dict[str, dict[str, float]]] = {}
    for variant_name, buckets in grouped.items():
        variant_summary: dict[str, dict[str, float]] = {}
        for bucket_name, bucket_values in buckets.items():
            bucket_summary: dict[str, float] = {"count": float(len(bucket_values))}
            for metric_field in metric_fields:
                values = [float(item.get(metric_field, 0.0)) for item in bucket_values]
                bucket_summary.update(summarize_numeric_series(metric_field, values))
            variant_summary[bucket_name] = bucket_summary
        summary[variant_name] = variant_summary
    return summary


def summarize_numeric_series(metric_name: str, values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {
            f"{metric_name}_mean": 0.0,
            f"{metric_name}_median": 0.0,
            f"{metric_name}_p90": 0.0,
            f"{metric_name}_max": 0.0,
            f"{metric_name}_total": 0.0,
        }
    resolved_values = sorted(float(value) for value in values)
    if len(resolved_values) == 1:
        p90_value = resolved_values[0]
    else:
        p90_index = min(len(resolved_values) - 1, int(math.ceil(len(resolved_values) * 0.9)) - 1)
        p90_value = resolved_values[p90_index]
    return {
        f"{metric_name}_mean": statistics.mean(resolved_values),
        f"{metric_name}_median": statistics.median(resolved_values),
        f"{metric_name}_p90": p90_value,
        f"{metric_name}_max": max(resolved_values),
        f"{metric_name}_total": sum(resolved_values),
    }


def summarize_variant_assets(
    assets: Sequence[MilkyWayVariantTileAsset],
    *,
    variants: Sequence[MilkyWayTileFormatVariant],
) -> dict[str, dict[str, object]]:
    variant_lookup = {variant.name: variant for variant in variants}
    summary: dict[str, dict[str, object]] = {}
    for variant_name, variant_assets in _group_assets_by_variant(assets).items():
        variant = variant_lookup[variant_name]
        orientation_values = sorted({value for value in (asset.orientation_tag for asset in variant_assets) if value is not None})
        summary[variant_name] = {
            "sample_count": len(variant_assets),
            "successful_count": sum(1 for asset in variant_assets if asset.error is None),
            "error_count": sum(1 for asset in variant_assets if asset.error is not None),
            "pixel_equal_count": sum(1 for asset in variant_assets if asset.pixel_equal),
            "pixel_mismatch_count": sum(1 for asset in variant_assets if asset.error is None and not asset.pixel_equal),
            "differing_pixel_count_total": sum(int(asset.differing_pixel_count) for asset in variant_assets if asset.error is None),
            "max_channel_delta": max((int(asset.max_channel_delta) for asset in variant_assets if asset.error is None), default=0),
            "orientation_tags": orientation_values,
            "dependency_risk": variant.dependency_risk,
            "platform_risk": variant.platform_risk,
            "compatibility_note": variant.compatibility_note,
            "lossless_expected": variant.lossless_expected,
            "disk_size_total_bytes": sum(int(asset.output_size_bytes) for asset in variant_assets if asset.error is None),
        }
    return summary


def full_tree_bytes_by_level(manifest_path: Path) -> dict[str, int]:
    manifest = load_milky_way_manifest(manifest_path)
    result: dict[str, int] = {}
    for level in manifest.levels:
        level_root = manifest_path.parent / f"L{int(level.level)}"
        result[f"L{int(level.level)}"] = sum(path.stat().st_size for path in level_root.glob("*.*") if path.is_file())
    return result


def estimate_projected_full_tree_sizes(
    assets: Sequence[MilkyWayVariantTileAsset],
    *,
    source_manifest_path: Path,
) -> dict[str, dict[str, object]]:
    source_sizes = full_tree_bytes_by_level(source_manifest_path)
    grouped_assets = _group_assets_by_variant(assets)
    projections: dict[str, dict[str, object]] = {}
    for variant_name, variant_assets in grouped_assets.items():
        per_level_ratio: dict[str, float] = {}
        for level_key in sorted({f"L{asset.source_sample.level}" for asset in variant_assets}):
            level_assets = [asset for asset in variant_assets if f"L{asset.source_sample.level}" == level_key and asset.error is None]
            if not level_assets:
                continue
            source_total = sum(asset.source_sample.file_size_bytes for asset in level_assets)
            variant_total = sum(asset.output_size_bytes for asset in level_assets)
            per_level_ratio[level_key] = (float(variant_total) / float(source_total)) if source_total > 0 else 0.0
        projected_level_sizes = {
            level_key: int(round(float(source_sizes.get(level_key, 0)) * per_level_ratio.get(level_key, 1.0)))
            for level_key in sorted(source_sizes)
        }
        projections[variant_name] = {
            "projected_total_bytes": sum(projected_level_sizes.values()),
            "projected_level_bytes": projected_level_sizes,
            "sample_level_size_ratio": per_level_ratio,
        }
    return projections


def build_summary_tsv_rows(
    *,
    decode_summary: dict[str, dict[str, dict[str, float]]],
    prepare_summary: dict[str, dict[str, dict[str, float]]],
    asset_summary: dict[str, dict[str, object]],
    projected_sizes: dict[str, dict[str, object]],
) -> list[str]:
    rows = [
        "variant\tbucket\tdecode_total_ms\tdecode_median_ms\tdecode_p90_ms\tdecode_max_ms\tprepare_total_ms\tprepare_median_ms\tprepare_p90_ms\tdisk_total_mb\tprojected_full_mb\tpixel_mismatches\torientation_tags"
    ]
    for variant_name in sorted(decode_summary):
        variant_asset_summary = asset_summary.get(variant_name, {})
        projected_total_bytes = int(projected_sizes.get(variant_name, {}).get("projected_total_bytes", 0))
        orientation_tags = variant_asset_summary.get("orientation_tags", [])
        for bucket_name in sorted(decode_summary[variant_name]):
            decode_bucket = decode_summary[variant_name][bucket_name]
            prepare_bucket = prepare_summary.get(variant_name, {}).get(bucket_name, {})
            rows.append(
                "\t".join(
                    (
                        variant_name,
                        bucket_name,
                        f"{float(decode_bucket.get('decode_ms_total', 0.0)):.3f}",
                        f"{float(decode_bucket.get('decode_ms_median', 0.0)):.3f}",
                        f"{float(decode_bucket.get('decode_ms_p90', 0.0)):.3f}",
                        f"{float(decode_bucket.get('decode_ms_max', 0.0)):.3f}",
                        f"{float(prepare_bucket.get('prepare_total_ms_total', 0.0)):.3f}",
                        f"{float(prepare_bucket.get('prepare_total_ms_median', 0.0)):.3f}",
                        f"{float(prepare_bucket.get('prepare_total_ms_p90', 0.0)):.3f}",
                        f"{float(variant_asset_summary.get('disk_size_total_bytes', 0)) / (1024.0 * 1024.0):.3f}",
                        f"{float(projected_total_bytes) / (1024.0 * 1024.0):.3f}",
                        str(int(variant_asset_summary.get('pixel_mismatch_count', 0))),
                        ",".join(str(value) for value in orientation_tags) if orientation_tags else "none",
                    )
                )
            )
    return rows


def write_benchmark_report(
    payload: dict[str, object],
    *,
    json_path: Path,
    summary_path: Path,
) -> None:
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    summary_rows = build_summary_tsv_rows(
        decode_summary=payload.get("decode_summary", {}),
        prepare_summary=payload.get("prepare_summary", {}),
        asset_summary=payload.get("asset_summary", {}),
        projected_sizes=payload.get("projected_sizes", {}),
    )
    summary_path.write_text("\n".join(summary_rows) + "\n", encoding="utf-8")


def _sample_evenly(items: Sequence[tuple[int, int]], cap: int) -> tuple[tuple[int, int], ...]:
    resolved_cap = max(1, int(cap))
    if len(items) <= resolved_cap:
        return tuple(items)
    indexes = np.linspace(0, len(items) - 1, num=resolved_cap, dtype=np.int64)
    return tuple(items[int(index)] for index in indexes)


def _group_assets_by_variant(assets: Sequence[MilkyWayVariantTileAsset]) -> dict[str, list[MilkyWayVariantTileAsset]]:
    grouped: dict[str, list[MilkyWayVariantTileAsset]] = {}
    for asset in assets:
        grouped.setdefault(asset.variant_name, []).append(asset)
    return grouped


def json_ready_sample(sample: MilkyWayTileSample) -> dict[str, object]:
    payload = asdict(sample)
    payload["source_path"] = str(sample.source_path)
    return payload


def json_ready_asset(asset: MilkyWayVariantTileAsset) -> dict[str, object]:
    payload = asdict(asset)
    payload["output_path"] = str(asset.output_path)
    payload["source_sample"] = json_ready_sample(asset.source_sample)
    return payload