from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from photometry_app.core.milky_way_assets import (
    MilkyWayAssetResolver,
    MilkyWayManifestLevel,
    MilkyWayManifestValidationError,
    expand_tile_selection,
    load_milky_way_manifest,
    parse_milky_way_manifest,
    select_milky_way_level,
    select_milky_way_level_with_hysteresis,
    tile_xy_from_uv,
    tiles_from_radec_bounds,
)
from photometry_app.core.milky_way_tile_generator import generate_milky_way_tile_pyramid, load_milky_way_source_image


def _manifest_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "projection": "equirectangular_celestial",
        "source_name": "demo.png",
        "source_width": 8192,
        "source_height": 4096,
        "tile_size": 1024,
        "color_space": "srgb",
        "tone_preprocessing": "source_like",
        "orientation": {
            "ra_increases": "left",
            "dec_axis": "top_to_bottom",
        },
        "levels": [
            {"level": 0, "width": 2048, "height": 1024, "path": "L0/{x}_{y}.png"},
            {"level": 1, "width": 4096, "height": 2048, "path": "L1/{x}_{y}.png"},
            {"level": 2, "width": 8192, "height": 4096, "path": "L2/{x}_{y}.png"},
        ],
    }


class MilkyWayManifestTest(unittest.TestCase):
    def test_manifest_parses_valid_payload(self) -> None:
        manifest = parse_milky_way_manifest(_manifest_payload())

        self.assertEqual(manifest.projection, "equirectangular_celestial")
        self.assertEqual(manifest.tile_size, 1024)
        self.assertEqual(manifest.tile_width, 1024)
        self.assertEqual(manifest.tile_height, 1024)
        self.assertEqual(manifest.gutter_pixels, 0)
        self.assertEqual(manifest.content_region, (0, 0, 1024, 1024))
        self.assertFalse(manifest.padded_tile)
        self.assertEqual(manifest.highest_level.width, 8192)
        self.assertEqual(manifest.level_by_number(1).height, 2048)

    def test_manifest_parses_padded_tile_metadata(self) -> None:
        payload = _manifest_payload()
        payload["tile_width"] = 1026
        payload["tile_height"] = 1026
        payload["gutter_pixels"] = 1
        payload["content_region"] = [1, 1, 1024, 1024]
        payload["padded_tile"] = True

        manifest = parse_milky_way_manifest(payload)

        self.assertEqual(manifest.tile_width, 1026)
        self.assertEqual(manifest.tile_height, 1026)
        self.assertEqual(manifest.gutter_pixels, 1)
        self.assertEqual(manifest.content_region, (1, 1, 1024, 1024))
        self.assertTrue(manifest.padded_tile)

    def test_manifest_parses_alpha_mask_tile_signal_metadata(self) -> None:
        payload = _manifest_payload()
        payload["alpha_mask"] = {
            "version": 1,
            "algorithm": "milky_way_alpha_mask_v1",
            "black_point": 0.02,
            "white_point": 0.095,
            "blur_radius_fraction": 0.006,
            "min_blur_radius_px": 2,
            "max_blur_radius_px": 96,
            "blur_passes": 3,
            "alpha_gamma": 0.85,
            "empty_alpha_threshold": 1.0 / 255.0,
            "empty_coverage_threshold": 0.0002,
            "tiles": [
                {
                    "level": 1,
                    "x": 2,
                    "y": 3,
                    "has_signal": False,
                    "alpha_coverage": 0.0,
                    "mean_alpha": 0.0,
                    "max_alpha": 0.0,
                },
                {
                    "level": 1,
                    "x": 3,
                    "y": 3,
                    "has_signal": True,
                    "alpha_coverage": 0.5,
                    "mean_alpha": 0.25,
                    "max_alpha": 1.0,
                },
            ],
        }

        manifest = parse_milky_way_manifest(payload)

        self.assertIsNotNone(manifest.alpha_mask)
        self.assertEqual(manifest.alpha_mask.algorithm, "milky_way_alpha_mask_v1")
        self.assertFalse(manifest.tile_signal(1, 2, 3).has_signal)
        self.assertTrue(manifest.tile_signal(1, 3, 3).has_signal)
        self.assertIsNone(manifest.tile_signal(1, 4, 3))

    def test_manifest_parses_tiff_lzw_metadata_and_path_extension(self) -> None:
        payload = _manifest_payload()
        payload["tile_format"] = "tiff"
        payload["compression"] = "tiff_lzw"
        payload["file_extension"] = ".tiff"
        payload["codec"] = "tiff_lzw"
        payload["levels"] = [
            {"level": 0, "width": 2048, "height": 1024, "path": "L0/{x}_{y}.tiff"},
            {"level": 1, "width": 4096, "height": 2048, "path": "L1/{x}_{y}.tiff"},
            {"level": 2, "width": 8192, "height": 4096, "path": "L2/{x}_{y}.tiff"},
        ]

        manifest = parse_milky_way_manifest(payload)

        self.assertEqual(manifest.tile_format, "tiff")
        self.assertEqual(manifest.compression, "tiff_lzw")
        self.assertEqual(manifest.file_extension, ".tiff")
        self.assertEqual(manifest.codec, "tiff_lzw")
        self.assertTrue(manifest.level_by_number(2).path.endswith(".tiff"))

    def test_manifest_requires_tile_placeholders(self) -> None:
        payload = _manifest_payload()
        payload["levels"] = [{"level": 0, "width": 2048, "height": 1024, "path": "L0/no_placeholders.png"}]

        with self.assertRaises(MilkyWayManifestValidationError):
            parse_milky_way_manifest(payload)

    def test_manifest_strict_validation_checks_tile_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "L0").mkdir(parents=True)
            (root / "L0" / "0_0.png").write_bytes(b"png")
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                """
{
  "schema_version": 1,
  "projection": "equirectangular_celestial",
  "source_name": "demo.png",
  "source_width": 2048,
  "source_height": 1024,
  "tile_size": 1024,
  "color_space": "srgb",
  "tone_preprocessing": "source_like",
  "orientation": {
    "ra_increases": "left",
    "dec_axis": "top_to_bottom"
  },
  "levels": [
    {
      "level": 0,
      "width": 2048,
      "height": 1024,
      "path": "L0/{x}_{y}.png"
    }
  ]
}
                """.strip(),
                encoding="utf-8",
            )

            with self.assertRaises(MilkyWayManifestValidationError):
                load_milky_way_manifest(manifest_path, strict=True)


class MilkyWayAssetResolverTest(unittest.TestCase):
    def test_resolver_prefers_manifest_but_keeps_runtime_global_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "textures").mkdir(parents=True)
            manifest_path = root / "milky_way_tiles" / "manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(str(_manifest_payload()).replace("'", '"'), encoding="utf-8")
            global_path = root / "textures" / "milkyway_2020_8k.png"
            preview_path = root / "textures" / "milkyway_2020_4k_preview.png"
            global_path.write_bytes(b"global")
            preview_path.write_bytes(b"preview")

            resolver = MilkyWayAssetResolver(
                project_root=root,
                tile_manifest_path=manifest_path,
                global_texture_path=global_path,
                preview_texture_path=preview_path,
            )
            asset_info = resolver.resolve()

            self.assertEqual(asset_info.asset_mode, "tiled_manifest")
            self.assertEqual(asset_info.runtime_single_texture_mode, "single_global")
            self.assertEqual(asset_info.runtime_single_texture_path, global_path)

    def test_resolver_prefers_current_default_32k_manifest_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            preview_path = root / "textures" / "milkyway_2020_4k_preview.png"
            preview_path.parent.mkdir(parents=True)
            preview_path.write_bytes(b"preview")
            manifest_path_32k = root / "textures" / "milky_way_tiles_32k_padded_lzw_benchmark" / "manifest.json"
            manifest_path_32k.parent.mkdir(parents=True)
            manifest_path_32k.write_text(str(_manifest_payload()).replace("'", '"'), encoding="utf-8")

            resolver = MilkyWayAssetResolver(project_root=root)
            asset_info = resolver.resolve()

            self.assertEqual(asset_info.asset_mode, "tiled_manifest")
            self.assertEqual(asset_info.active_path, manifest_path_32k)
            self.assertEqual(asset_info.runtime_single_texture_mode, "preview_fallback")
            self.assertEqual(asset_info.runtime_single_texture_path, preview_path)

    def test_resolver_high_detail_prefers_32k_manifest_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            preview_path = root / "textures" / "milkyway_2020_4k_preview.png"
            preview_path.parent.mkdir(parents=True)
            preview_path.write_bytes(b"preview")
            manifest_path_32k = root / "textures" / "milky_way_tiles_32k_padded_lzw_benchmark" / "manifest.json"
            manifest_path_32k.parent.mkdir(parents=True)
            manifest_path_32k.write_text(str(_manifest_payload()).replace("'", '"'), encoding="utf-8")

            resolver = MilkyWayAssetResolver(project_root=root)
            asset_info = resolver.resolve_high_detail()

            self.assertEqual(asset_info.asset_mode, "tiled_manifest")
            self.assertEqual(asset_info.active_path, manifest_path_32k)
            self.assertEqual(asset_info.runtime_single_texture_mode, "preview_fallback")
            self.assertEqual(asset_info.runtime_single_texture_path, preview_path)

    def test_invalid_manifest_falls_back_to_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            preview_path = root / "textures" / "milkyway_2020_4k_preview.png"
            preview_path.parent.mkdir(parents=True)
            preview_path.write_bytes(b"preview")
            manifest_path = root / "milky_way_tiles" / "manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text("{}", encoding="utf-8")

            resolver = MilkyWayAssetResolver(
                project_root=root,
                tile_manifest_path=manifest_path,
                preview_texture_path=preview_path,
            )
            asset_info = resolver.resolve()

            self.assertEqual(asset_info.asset_mode, "preview_fallback")
            self.assertIn("manifest", asset_info.fallback_reason.casefold())


class MilkyWayTileHelperTest(unittest.TestCase):
    def test_tile_xy_from_uv_wraps_horizontally_and_clamps_vertically(self) -> None:
        self.assertEqual(tile_xy_from_uv(1.0, 0.0, level_width=4096, level_height=2048, tile_size=1024), (0, 0))
        self.assertEqual(tile_xy_from_uv(0.999, 1.2, level_width=4096, level_height=2048, tile_size=1024), (3, 1))

    def test_ra_seam_selection_returns_tiles_on_both_sides(self) -> None:
        tiles = tiles_from_radec_bounds(
            359.0,
            1.0,
            -8.0,
            8.0,
            level_width=4096,
            level_height=2048,
            tile_size=1024,
        )

        selected_columns = {x_index for x_index, _y_index in tiles}
        self.assertIn(0, selected_columns)
        self.assertIn(3, selected_columns)

    def test_dec_clamps_near_poles(self) -> None:
        tiles = tiles_from_radec_bounds(
            40.0,
            60.0,
            85.0,
            95.0,
            level_width=4096,
            level_height=2048,
            tile_size=1024,
        )

        self.assertTrue(all(y_index == 0 for _x_index, y_index in tiles))

    def test_tile_margin_expands_selection(self) -> None:
        expanded = expand_tile_selection({(0, 0)}, tile_count_x=4, tile_count_y=2, margin=1)

        self.assertIn((3, 0), expanded)
        self.assertIn((1, 1), expanded)
        self.assertEqual(len(expanded), 6)

    def test_full_sky_selection_includes_all_columns(self) -> None:
        tiles = tiles_from_radec_bounds(
            0.0,
            360.0,
            -90.0,
            90.0,
            level_width=4096,
            level_height=2048,
            tile_size=1024,
        )

        self.assertEqual({x_index for x_index, _y_index in tiles}, {0, 1, 2, 3})


class MilkyWayLevelSelectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.levels = (
            MilkyWayManifestLevel(level=0, width=2048, height=1024, path="L0/{x}_{y}.png"),
            MilkyWayManifestLevel(level=1, width=4096, height=2048, path="L1/{x}_{y}.png"),
            MilkyWayManifestLevel(level=2, width=8192, height=4096, path="L2/{x}_{y}.png"),
            MilkyWayManifestLevel(level=3, width=16384, height=8192, path="L3/{x}_{y}.png"),
            MilkyWayManifestLevel(level=4, width=32768, height=16384, path="L4/{x}_{y}.png"),
        )

    def test_resolution_caps_limit_selected_level(self) -> None:
        low_level = select_milky_way_level(
            self.levels,
            viewport_width_px=1600,
            viewport_height_px=900,
            field_width_deg=120.0,
            field_height_deg=67.5,
            resolution_mode="low",
        )
        medium_level = select_milky_way_level(
            self.levels,
            viewport_width_px=1600,
            viewport_height_px=900,
            field_width_deg=120.0,
            field_height_deg=67.5,
            resolution_mode="medium",
        )
        high_level = select_milky_way_level(
            self.levels,
            viewport_width_px=1600,
            viewport_height_px=900,
            field_width_deg=20.0,
            field_height_deg=11.25,
            resolution_mode="high",
        )

        self.assertEqual(low_level.width, 4096)
        self.assertEqual(medium_level.width, 8192)
        self.assertEqual(high_level.width, 32768)

    def test_hysteresis_keeps_previous_level_near_threshold(self) -> None:
        selected_level = select_milky_way_level_with_hysteresis(
            self.levels,
            viewport_width_px=1024,
            viewport_height_px=512,
            field_width_deg=90.5,
            field_height_deg=45.25,
            resolution_mode="high",
            previous_level=1,
            hysteresis_ratio=0.12,
        )

        self.assertEqual(selected_level.level, 1)

    def test_hysteresis_allows_downshift_when_previous_level_is_far_too_detailed(self) -> None:
        selected_level = select_milky_way_level_with_hysteresis(
            self.levels,
            viewport_width_px=1024,
            viewport_height_px=512,
            field_width_deg=180.0,
            field_height_deg=90.0,
            resolution_mode="high",
            previous_level=4,
            hysteresis_ratio=0.12,
        )

        self.assertEqual(selected_level.level, 0)


class MilkyWayTileGeneratorTest(unittest.TestCase):
    def test_generator_writes_manifest_and_tiles_for_small_png(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.png"
            output_dir = root / "tiles"
            Image.fromarray(np.full((128, 256, 3), 64, dtype=np.uint8), mode="RGB").save(source_path)

            summary = generate_milky_way_tile_pyramid(
                source_path,
                output_dir,
                tile_size=64,
                level_widths=[128, 256],
                tone_preprocessing="none",
                color_space="srgb",
                tile_format="png",
            )

            self.assertTrue(summary.manifest_path.is_file())
            manifest = load_milky_way_manifest(summary.manifest_path)
            self.assertEqual([level.width for level in manifest.levels], [128, 256])
            self.assertIsNotNone(manifest.alpha_mask)
            self.assertGreater(summary.signal_tile_count, 0)
            self.assertTrue((output_dir / "L0" / "0_0.png").is_file())
            self.assertTrue((output_dir / "L1" / "3_1.png").is_file())
            self.assertEqual(Image.open(output_dir / "L0" / "0_0.png").mode, "RGBA")

    def test_generator_can_bake_wrapped_and_clamped_tile_gutters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.png"
            output_dir = root / "tiles"

            source_pixels = np.zeros((128, 256, 3), dtype=np.uint8)
            for y_index in range(2):
                for x_index in range(4):
                    source_pixels[y_index * 64 : (y_index + 1) * 64, x_index * 64 : (x_index + 1) * 64] = (
                        x_index * 40,
                        y_index * 90,
                        20,
                    )
            Image.fromarray(source_pixels, mode="RGB").save(source_path)

            summary = generate_milky_way_tile_pyramid(
                source_path,
                output_dir,
                tile_size=64,
                pad_gutter=1,
                level_widths=[256],
                tone_preprocessing="none",
                color_space="srgb",
                tile_format="png",
            )

            self.assertEqual(summary.tile_width, 66)
            self.assertEqual(summary.tile_height, 66)
            self.assertEqual(summary.gutter_pixels, 1)
            self.assertTrue(summary.padded_tile)

            manifest = load_milky_way_manifest(summary.manifest_path)
            self.assertEqual(manifest.tile_width, 66)
            self.assertEqual(manifest.tile_height, 66)
            self.assertEqual(manifest.gutter_pixels, 1)
            self.assertEqual(manifest.content_region, (1, 1, 64, 64))
            self.assertTrue(manifest.padded_tile)

            top_left_tile = np.asarray(Image.open(output_dir / "L0" / "0_0.png"))
            wrapped_neighbor_tile = np.asarray(Image.open(output_dir / "L0" / "3_0.png"))
            bottom_left_tile = np.asarray(Image.open(output_dir / "L0" / "0_1.png"))

            self.assertEqual(tuple(top_left_tile.shape[:2]), (66, 66))
            np.testing.assert_array_equal(top_left_tile[1:65, 1:65, :3], source_pixels[:64, :64])
            np.testing.assert_array_equal(top_left_tile[1:65, 0, :3], wrapped_neighbor_tile[1:65, 64, :3])
            np.testing.assert_array_equal(top_left_tile[0, 1:65, :3], top_left_tile[1, 1:65, :3])
            np.testing.assert_array_equal(bottom_left_tile[65, 1:65, :3], bottom_left_tile[64, 1:65, :3])

    def test_generator_records_empty_and_signal_tiles_from_alpha_mask(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.png"
            output_dir = root / "tiles"
            source_pixels = np.zeros((128, 256, 3), dtype=np.uint8)
            source_pixels[:, 128:] = 160
            Image.fromarray(source_pixels, mode="RGB").save(source_path)

            summary = generate_milky_way_tile_pyramid(
                source_path,
                output_dir,
                tile_size=64,
                level_widths=[256],
                tone_preprocessing="none",
                color_space="srgb",
                tile_format="png",
            )

            manifest = load_milky_way_manifest(summary.manifest_path)
            self.assertIsNotNone(manifest.alpha_mask)
            self.assertGreater(summary.empty_tile_count, 0)
            self.assertGreater(summary.signal_tile_count, 0)
            self.assertFalse(manifest.tile_signal(0, 0, 0).has_signal)
            self.assertTrue(manifest.tile_signal(0, 3, 0).has_signal)

    def test_generator_can_write_tiff_lzw_tiles_and_manifest_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.png"
            output_dir = root / "tiles"
            Image.fromarray(np.full((128, 256, 3), 96, dtype=np.uint8), mode="RGB").save(source_path)

            summary = generate_milky_way_tile_pyramid(
                source_path,
                output_dir,
                tile_size=64,
                level_widths=[256],
                tone_preprocessing="none",
                color_space="srgb",
                tile_format="tiff_lzw",
            )

            manifest = load_milky_way_manifest(summary.manifest_path)

            self.assertEqual(summary.tile_format, "tiff")
            self.assertEqual(summary.compression, "tiff_lzw")
            self.assertEqual(summary.file_extension, ".tiff")
            self.assertEqual(summary.codec, "tiff_lzw")
            self.assertEqual(manifest.tile_format, "tiff")
            self.assertEqual(manifest.compression, "tiff_lzw")
            self.assertEqual(manifest.file_extension, ".tiff")
            self.assertEqual(manifest.codec, "tiff_lzw")
            self.assertTrue((output_dir / "L0" / "0_0.tiff").is_file())
            self.assertTrue(manifest.level_by_number(0).path.endswith(".tiff"))

    def test_generator_defaults_to_tiff_lzw_tiles_and_manifest_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.png"
            output_dir = root / "tiles"
            Image.fromarray(np.full((128, 256, 3), 64, dtype=np.uint8), mode="RGB").save(source_path)

            summary = generate_milky_way_tile_pyramid(
                source_path,
                output_dir,
                tile_size=64,
                level_widths=[256],
                tone_preprocessing="none",
                color_space="srgb",
            )

            manifest = load_milky_way_manifest(summary.manifest_path)

            self.assertEqual(summary.tile_format, "tiff")
            self.assertEqual(summary.compression, "tiff_lzw")
            self.assertEqual(summary.file_extension, ".tiff")
            self.assertEqual(summary.codec, "tiff_lzw")
            self.assertEqual(manifest.tile_format, "tiff")
            self.assertEqual(manifest.compression, "tiff_lzw")
            self.assertEqual(manifest.file_extension, ".tiff")
            self.assertEqual(manifest.codec, "tiff_lzw")
            self.assertTrue((output_dir / "L0" / "0_0.tiff").is_file())

    def test_exr_reader_fails_loudly_if_it_decodes_uint8(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.exr"
            source_path.write_bytes(b"not-really-exr")
            fake_image = np.zeros((1, 32, 64, 3), dtype=np.uint8)

            with patch("imageio.v3.imread", return_value=fake_image):
                with self.assertRaises(RuntimeError):
                    load_milky_way_source_image(source_path, tone_preprocessing="source_like")


if __name__ == "__main__":
    unittest.main()