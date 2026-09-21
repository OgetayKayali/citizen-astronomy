"""Convert NASA SVS Milky Way EXRs into PixInsight / StarXTerminator TIFFs.

PixInsight cannot open OpenEXR, and the 32k master is too large for StarXTerminator
as one image. This script writes a 4k test TIFF and overlapping 4k tiles for the
32k master, then stitches processed tiles back into one 32-bit TIFF.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import tifffile

from photometry_app.core.milky_way_tile_generator import _load_openexr_pixels


DEFAULT_SOURCE_32K = Path("textures") / "milkyway_2020_32k.exr"
DEFAULT_SOURCE_4K = Path("textures") / "milkyway_2020_4k.exr"
DEFAULT_OUTPUT_ROOT = Path("textures") / "starxt"
DEFAULT_TILE_SIZE = 4096
DEFAULT_OVERLAP = 384


def _as_rgb_float32(image: np.ndarray) -> np.ndarray:
    rgb = np.asarray(image)
    if rgb.ndim == 4 and rgb.shape[0] == 1:
        rgb = rgb[0]
    if rgb.ndim != 3 or rgb.shape[2] < 3:
        raise ValueError(f"Expected an RGB image, got shape {rgb.shape!r}")
    rgb = np.nan_to_num(rgb[..., :3], nan=0.0, posinf=0.0, neginf=0.0)
    return np.maximum(rgb.astype(np.float32, copy=False), np.float32(0.0))


def _write_tiff(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(
        path,
        _as_rgb_float32(image),
        photometric="rgb",
        planarconfig="contig",
        compression="adobe_deflate",
        metadata=None,
    )


def _load_exr_rgb(path: Path) -> np.ndarray:
    return _as_rgb_float32(_load_openexr_pixels(path))


def _extract_tile(
    image: np.ndarray,
    *,
    x0: int,
    y0: int,
    tile_size: int,
    overlap: int,
) -> tuple[np.ndarray, dict[str, int]]:
    height, width = image.shape[:2]
    x_start = x0 - overlap
    x_end = x0 + tile_size + overlap
    y_start = max(0, y0 - overlap)
    y_end = min(height, y0 + tile_size + overlap)
    xs = np.arange(x_start, x_end) % width
    patch = np.ascontiguousarray(image[y_start:y_end][:, xs])
    return patch, {
        "x0": x0,
        "y0": y0,
        "x_start": x_start,
        "x_end": x_end,
        "y_start": y_start,
        "y_end": y_end,
        "core_left": overlap,
        "core_top": y0 - y_start,
    }


def export_full_tiff(source: Path, output: Path) -> Path:
    _write_tiff(output, _load_exr_rgb(source))
    return output


def split_tiles(
    source: Path,
    output_dir: Path,
    *,
    tile_size: int = DEFAULT_TILE_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> Path:
    image = _load_exr_rgb(source)
    height, width = image.shape[:2]
    if width % tile_size or height % tile_size:
        raise ValueError(
            f"Image {width}x{height} is not divisible by tile size {tile_size}. "
            "Use --tile-size 4096 on the 32k master."
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    columns = width // tile_size
    rows = height // tile_size
    records: list[dict[str, object]] = []
    for row in range(rows):
        for col in range(columns):
            x0 = col * tile_size
            y0 = row * tile_size
            name = f"r{row:02d}_c{col:02d}.tif"
            patch, geometry = _extract_tile(
                image,
                x0=x0,
                y0=y0,
                tile_size=tile_size,
                overlap=overlap,
            )
            _write_tiff(output_dir / name, patch)
            records.append({"name": name, "row": row, "col": col, **geometry})
            print(f"Wrote {name}  {patch.shape[1]}x{patch.shape[0]}")
    manifest = {
        "source": str(source),
        "width": width,
        "height": height,
        "tile_size": tile_size,
        "overlap": overlap,
        "rows": rows,
        "columns": columns,
        "tiles": records,
    }
    manifest_path = output_dir / "starxt_tiles.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def stitch_tiles(processed_dir: Path, output: Path, manifest_path: Path | None = None) -> Path:
    resolved_manifest = Path(manifest_path) if manifest_path is not None else processed_dir / "starxt_tiles.json"
    if not resolved_manifest.is_file():
        fallback = processed_dir.parent / "tiles_in" / "starxt_tiles.json"
        if fallback.is_file():
            resolved_manifest = fallback
        else:
            raise FileNotFoundError(f"Missing tile manifest: {resolved_manifest}")
    manifest = json.loads(resolved_manifest.read_text(encoding="utf-8"))
    width = int(manifest["width"])
    height = int(manifest["height"])
    tile_size = int(manifest["tile_size"])
    mosaic = np.zeros((height, width, 3), dtype=np.float32)
    for record in manifest["tiles"]:
        tile_path = processed_dir / str(record["name"])
        if not tile_path.is_file():
            raise FileNotFoundError(f"Processed tile is missing: {tile_path}")
        patch = _as_rgb_float32(tifffile.imread(tile_path))
        core_left = int(record["core_left"])
        core_top = int(record["core_top"])
        x0 = int(record["x0"])
        y0 = int(record["y0"])
        core = patch[core_top : core_top + tile_size, core_left : core_left + tile_size]
        if core.shape[0] != tile_size or core.shape[1] != tile_size:
            raise ValueError(
                f"{tile_path.name} core is {core.shape[1]}x{core.shape[0]}, expected {tile_size}x{tile_size}. "
                "Do not crop, rotate, or resample the tiles in PixInsight."
            )
        mosaic[y0 : y0 + tile_size, x0 : x0 + tile_size] = core
        print(f"Placed {tile_path.name}")
    _write_tiff(output, mosaic)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare or stitch StarXTerminator Milky Way TIFFs.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export-4k", help="Write the 4k EXR as one PixInsight TIFF.")
    export_parser.add_argument("--input", type=Path, default=DEFAULT_SOURCE_4K)
    export_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "milkyway_2020_4k.tif")

    split_parser = subparsers.add_parser("split-32k", help="Split the 32k EXR into overlapping 4k TIFFs.")
    split_parser.add_argument("--input", type=Path, default=DEFAULT_SOURCE_32K)
    split_parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_ROOT / "tiles_in")
    split_parser.add_argument("--tile-size", type=int, default=DEFAULT_TILE_SIZE)
    split_parser.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP)

    stitch_parser = subparsers.add_parser("stitch", help="Rebuild one 32k TIFF from processed tiles.")
    stitch_parser.add_argument("--processed-dir", type=Path, default=DEFAULT_OUTPUT_ROOT / "tiles_out")
    stitch_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "milkyway_2020_32k_starless.tif")
    stitch_parser.add_argument("--manifest", type=Path, default=None)

    args = parser.parse_args()
    if args.command == "export-4k":
        path = export_full_tiff(args.input, args.output)
        print(f"Wrote {path}")
        return 0
    if args.command == "split-32k":
        manifest = split_tiles(
            args.input,
            args.output_dir,
            tile_size=args.tile_size,
            overlap=args.overlap,
        )
        print(f"Wrote {manifest}")
        return 0
    output = stitch_tiles(args.processed_dir, args.output, args.manifest)
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
