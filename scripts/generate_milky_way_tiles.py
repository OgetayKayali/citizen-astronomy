from __future__ import annotations

import argparse
from pathlib import Path

from photometry_app.core.milky_way_tile_generator import (
    default_tile_output_format_name,
    default_level_widths,
    generate_milky_way_tile_pyramid,
    load_milky_way_source_image,
    supported_tile_output_formats,
)


def _parse_level_widths(value: str | None) -> list[int] | None:
    if value is None:
        return None
    parts = [segment.strip() for segment in value.split(",")]
    widths = [int(segment) for segment in parts if segment]
    return widths or None


def main() -> int:
    default_tile_format = default_tile_output_format_name()
    parser = argparse.ArgumentParser(description="Generate a Milky Way tile pyramid and manifest for Sky View.")
    parser.add_argument("input", help="Input source image path")
    parser.add_argument("output", help="Output directory for tiles and manifest")
    parser.add_argument("--tile-size", type=int, default=1024)
    parser.add_argument("--pad-gutter", type=int, default=0, help="Offline gutter pixels to bake around each tile")
    parser.add_argument(
        "--tile-format",
        choices=supported_tile_output_formats(),
        default=default_tile_format,
        help=(
            "Output tile format for the pyramid. "
            f"Default: {default_tile_format} for production generation; png and tiff_deflate remain supported."
        ),
    )
    parser.add_argument("--level-widths", help="Comma-separated output widths, for example 2048,4096,8192,16384,32768")
    parser.add_argument(
        "--tone-preprocessing",
        choices=("none", "source_like", "display_toned"),
        default="source_like",
        help="Explicit preprocessing mode recorded into the manifest.",
    )
    parser.add_argument("--color-space", choices=("srgb", "linear", "unknown"), default="unknown")
    parser.add_argument(
        "--no-alpha-mask",
        action="store_true",
        help="Write legacy RGB tiles without the Milky-Way-only alpha mask or signal metadata.",
    )
    args = parser.parse_args()

    loaded_source = load_milky_way_source_image(args.input, tone_preprocessing=args.tone_preprocessing)
    level_widths = _parse_level_widths(args.level_widths)
    if level_widths is None:
        level_widths = default_level_widths(loaded_source.width)

    summary = generate_milky_way_tile_pyramid(
        args.input,
        args.output,
        tile_size=args.tile_size,
        pad_gutter=args.pad_gutter,
        level_widths=level_widths,
        tone_preprocessing=args.tone_preprocessing,
        color_space=args.color_space,
        source_name=Path(args.input).name,
        tile_format=args.tile_format,
        write_alpha_mask=not args.no_alpha_mask,
    )

    print(f"Input: {summary.source_path}")
    print(
        "Source: "
        f"{summary.source_width}x{summary.source_height}, dtype={summary.source_dtype}, "
        f"bit_depth={summary.source_bit_depth}, mode={summary.source_color_mode}"
    )
    print(f"Tone preprocessing: {summary.tone_preprocessing}")
    print(f"Tile size: {summary.tile_size}")
    print(f"Tile dimensions: {summary.tile_width}x{summary.tile_height} (gutter={summary.gutter_pixels}, padded={int(summary.padded_tile)})")
    print(
        "Tile output: "
        f"format={summary.tile_format}, compression={summary.compression}, "
        f"extension={summary.file_extension}, codec={summary.codec}"
    )
    print(
        "Alpha mask: "
        f"enabled={int(summary.alpha_masked)}, signal_tiles={summary.signal_tile_count}, "
        f"empty_tiles={summary.empty_tile_count}"
    )
    print("Levels:")
    for level_index, tile_count, level_width in summary.tile_counts:
        print(f"  L{level_index}: width={level_width}, tiles={tile_count}")
    print(f"Estimated disk usage: {summary.disk_usage_bytes / (1024.0 * 1024.0):.2f} MiB")
    print(f"Manifest: {summary.manifest_path}")
    if summary.tile_counts:
        first_level_index = summary.tile_counts[0][0]
        print(f"Sample tile path: {summary.output_directory / f'L{first_level_index}' / f'0_0{summary.file_extension}'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())