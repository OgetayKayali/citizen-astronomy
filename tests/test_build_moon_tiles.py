from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from PySide6.QtGui import QColor, QImage

from scripts.build_moon_tiles import (
    _sample_tile_tree_rgb_at_uv,
    audit_moon_texture_sources,
    build_moon_tile_tree,
    generate_synthetic_moon_source_maps,
    validate_global_vs_tiled_registration,
    validate_moon_tile_tree,
    validate_polar_cap_assets,
    validate_tile_lod_luminance_consistency,
    validate_tile_uv_sampling,
    validate_tiled_normal_maps,
)


def _write_gradient_image(path: Path, width: int, height: int, *, seam_edges: bool = False) -> None:

    image = QImage(width, height, QImage.Format.Format_RGB888)
    for y in range(height):
        for x in range(width):
            if seam_edges and x == 0:
                color = QColor(255, 0, 0)
            elif seam_edges and x == width - 1:
                color = QColor(0, 0, 255)
            else:
                color = QColor((x * 255) // max(1, width - 1), (y * 255) // max(1, height - 1), 96)
            image.setPixelColor(x, y, color)
    if not image.save(str(path)):
        raise RuntimeError(f"Failed to write test image: {path}")


class MoonTileBuildScriptTest(unittest.TestCase):

    def test_build_and_validate_synthetic_tile_tree(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            albedo_source = root / "albedo.png"
            displacement_source = root / "height.png"
            _write_gradient_image(albedo_source, 64, 32)
            _write_gradient_image(displacement_source, 64, 32)

            build_report = build_moon_tile_tree(
                root,
                albedo_source=albedo_source,
                displacement_source=displacement_source,
                output_dir=root / "assets" / "moon_tiles",
                tile_size=16,
                max_level=1,
            )

            self.assertEqual(build_report["written_albedo_tile_count"], 10)
            self.assertEqual(build_report["written_normal_tile_count"], 10)

            validation_report = validate_moon_tile_tree(root, output_dir=root / "assets" / "moon_tiles")
            self.assertTrue(validation_report["manifest_present"])
            self.assertFalse(validation_report["missing_albedo_tiles"])
            self.assertFalse(validation_report["missing_normal_pairs"])
            self.assertFalse(validation_report["invalid_images"])
            self.assertFalse(validation_report["inconsistent_tile_sizes"])
            self.assertTrue(validation_report["manager_compatibility"]["usable"])
            self.assertEqual(validation_report["global_vs_tiled_registration"]["status"], "pass")
            self.assertEqual(validation_report["polar_cap_validation"]["status"], "pass")
            self.assertEqual(validation_report["levels"]["L0"]["albedo_count"], 2)
            self.assertEqual(validation_report["levels"]["L1"]["albedo_count"], 8)

    def test_manifest_records_canonical_source_convention_and_registration(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "canonical.png"
            _write_gradient_image(source, 128, 64, seam_edges=True)
            output_dir = root / "assets" / "moon_tiles"
            report = build_moon_tile_tree(root, albedo_source=source, output_dir=output_dir, tile_size=16, max_level=2)
            manifest = json.loads((output_dir / "build_manifest.json").read_text(encoding="utf-8"))

            self.assertEqual(manifest["canonical_convention"]["id"], "moon_shader_equirectangular_v1")
            self.assertEqual(manifest["source_transform"]["flip_u"], False)
            self.assertEqual(manifest["source_transform"]["flip_v"], False)
            self.assertIn("source_sha256", manifest["albedo"])
            self.assertTrue(manifest["registration"]["registered_to_active_global_texture"])
            self.assertEqual(report["global_vs_tiled_registration"]["status"], "pass")
            self.assertEqual(manifest["polar_caps"]["projection"], "azimuthal_equidistant")
            self.assertEqual(manifest["polar_caps"]["caps"]["north"]["pole"], "north")
            self.assertEqual(manifest["polar_caps"]["caps"]["south"]["pole"], "south")
            self.assertTrue((output_dir / manifest["polar_caps"]["caps"]["south"]["albedo_path"]).is_file())
            self.assertEqual(manifest["polar_caps"]["blend_curve"]["type"], "smootherstep_power")
            self.assertEqual(manifest["polar_caps"]["blend_start_lat_deg"], 60.0)
            self.assertEqual(manifest["polar_caps"]["blend_end_lat_deg"], 78.0)
            self.assertEqual(manifest["polar_caps"]["normal_blend_start_lat_deg"], 78.0)
            self.assertEqual(manifest["polar_caps"]["filtering_policy"]["wrap_s"], "clamp_to_edge")
            self.assertIn("appearance_matching", manifest["polar_caps"])
            self.assertGreater(manifest["polar_caps"]["appearance_matching"]["albedo_sharpness"], 0.0)

    def test_polar_cap_projection_validates_center_and_blend_samples(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "canonical.png"
            output_dir = root / "assets" / "moon_tiles"
            _write_gradient_image(source, 128, 64, seam_edges=True)
            build_moon_tile_tree(
                root,
                albedo_source=source,
                global_albedo_source=source,
                output_dir=output_dir,
                tile_size=16,
                max_level=1,
                polar_cap_size=128,
                polar_cap_boundary_match_strength=0.5,
            )

            validation = validate_polar_cap_assets(root, output_dir=output_dir)
            manifest = json.loads((output_dir / "build_manifest.json").read_text(encoding="utf-8"))

            self.assertEqual(validation["status"], "pass")
            self.assertEqual(manifest["polar_caps"]["texture_size"], 128)
            self.assertTrue(manifest["polar_caps"]["boundary_normalization"]["enabled"])
            self.assertTrue(validation["pole_center_finite"])
            self.assertTrue(validation["no_nan_uvs"])
            self.assertEqual(validation["out_of_bounds_sample_count"], 0)
            self.assertTrue(validation["boundary_deltas_bounded"])
            self.assertLessEqual(validation["boundary_mean_color_delta"], 0.02)
            self.assertLessEqual(validation["boundary_mean_luminance_delta"], 0.02)
            self.assertLessEqual(validation["boundary_mean_contrast_delta"], 0.18)
            self.assertEqual(validation["caps"]["north"]["status"], "pass")
            self.assertEqual(validation["caps"]["south"]["status"], "pass")
            self.assertIn("after", validation["caps"]["south"]["boundary_normalization"])
            self.assertIn("appearance_harmonization", validation["caps"]["south"])
            self.assertIn("footprint_metrics", validation["caps"]["south"])

    def test_manifest_records_conservative_height_normal_smoothing(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            albedo_source = root / "albedo.png"
            displacement_source = root / "height.png"
            output_dir = root / "assets" / "moon_tiles"
            _write_gradient_image(albedo_source, 64, 32)
            _write_gradient_image(displacement_source, 64, 32)

            build_moon_tile_tree(
                root,
                albedo_source=albedo_source,
                displacement_source=displacement_source,
                output_dir=output_dir,
                tile_size=16,
                max_level=1,
                normal_smoothing_passes=3,
                polar_normal_smoothing_passes=4,
                polar_normal_fade_start_lat_deg=68.0,
                polar_normal_fade_end_lat_deg=85.0,
            )
            manifest = json.loads((output_dir / "build_manifest.json").read_text(encoding="utf-8"))

            self.assertEqual(manifest["normal"]["source_kind"], "height")
            self.assertEqual(manifest["normal"]["smoothing_passes"], 3)
            self.assertEqual(manifest["normal"]["normal_generation"], "height_gradient_gaussian_smoothed")
            self.assertEqual(manifest["normal"]["polar_smoothing"]["additional_smoothing_passes"], 4)
            self.assertEqual(manifest["normal"]["polar_smoothing"]["derivative_fade_start_lat_deg"], 68.0)
            self.assertEqual(manifest["normal"]["polar_smoothing"]["derivative_fade_end_lat_deg"], 85.0)
            self.assertTrue(manifest["normal"]["polar_smoothing"]["analytic_normal_at_exact_pole"])

    def test_global_vs_tiled_registration_detects_v_flip_and_longitude_shift(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "canonical.png"
            _write_gradient_image(source, 128, 64, seam_edges=True)
            flipped_dir = root / "flipped"
            shifted_dir = root / "shifted"
            build_moon_tile_tree(
                root,
                albedo_source=source,
                global_albedo_source=source,
                output_dir=flipped_dir,
                tile_size=16,
                max_level=2,
                flip_v=True,
            )
            build_moon_tile_tree(
                root,
                albedo_source=source,
                global_albedo_source=source,
                output_dir=shifted_dir,
                tile_size=16,
                max_level=2,
                longitude_shift_pixels=16,
            )

            flipped = validate_global_vs_tiled_registration(root, output_dir=flipped_dir, global_albedo_source=source)
            shifted = validate_global_vs_tiled_registration(root, output_dir=shifted_dir, global_albedo_source=source)

            self.assertEqual(flipped["status"], "fail")
            self.assertTrue(flipped["possible_v_flip_detected"])
            self.assertEqual(shifted["status"], "fail")
            self.assertGreater(abs(float(shifted["detected_longitude_shift_degrees"])), 20.0)

    def test_registered_tiles_preserve_uv_geography_across_detail_levels(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "geography_gradient.png"
            _write_gradient_image(source, 128, 64)
            output_dir = root / "assets" / "moon_tiles"
            build_moon_tile_tree(root, albedo_source=source, global_albedo_source=source, output_dir=output_dir, tile_size=16, max_level=2)

            registration = validate_global_vs_tiled_registration(root, output_dir=output_dir, global_albedo_source=source, level=2)
            low_detail = _sample_tile_tree_rgb_at_uv(output_dir, 1, 0.38, 0.46)
            high_detail = _sample_tile_tree_rgb_at_uv(output_dir, 2, 0.38, 0.46)
            audit = audit_moon_texture_sources(root, output_dir=output_dir)

            self.assertTrue(registration["registered"])
            self.assertLessEqual(max(abs(low_detail[index] - high_detail[index]) for index in range(3)), 12)
            self.assertEqual(audit["canonical_convention"]["id"], "moon_shader_equirectangular_v1")

    def test_validation_reports_missing_normal_pair_and_invalid_image(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            albedo_source = root / "albedo.png"
            displacement_source = root / "height.png"
            output_dir = root / "assets" / "moon_tiles"
            _write_gradient_image(albedo_source, 64, 32)
            _write_gradient_image(displacement_source, 64, 32)
            build_moon_tile_tree(
                root,
                albedo_source=albedo_source,
                displacement_source=displacement_source,
                output_dir=output_dir,
                tile_size=16,
                max_level=1,
            )

            missing_normal_tile = output_dir / "normal" / "L1" / "2_1.png"
            missing_normal_tile.unlink()
            broken_albedo_tile = output_dir / "albedo" / "L1" / "0_0.png"
            broken_albedo_tile.write_text("not an image", encoding="utf-8")

            validation_report = validate_moon_tile_tree(root, output_dir=output_dir)

            self.assertIn("normal:L1:2_1", validation_report["missing_normal_pairs"])
            self.assertEqual(len(validation_report["invalid_images"]), 1)
            self.assertFalse(validation_report["manager_compatibility"]["usable"])

    def test_build_preserves_source_edges_at_longitude_seam(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            albedo_source = root / "seam_albedo.png"
            _write_gradient_image(albedo_source, 64, 32, seam_edges=True)

            build_moon_tile_tree(
                root,
                albedo_source=albedo_source,
                output_dir=root / "assets" / "moon_tiles",
                tile_size=16,
                max_level=1,
            )

            left_tile = QImage(str(root / "assets" / "moon_tiles" / "albedo" / "L1" / "0_0.png"))
            right_tile = QImage(str(root / "assets" / "moon_tiles" / "albedo" / "L1" / "3_0.png"))
            self.assertFalse(left_tile.isNull())
            self.assertFalse(right_tile.isNull())

            left_color = left_tile.pixelColor(0, left_tile.height() // 2)
            right_color = right_tile.pixelColor(right_tile.width() - 1, right_tile.height() // 2)
            self.assertGreater(left_color.red(), 200)
            self.assertLess(left_color.blue(), 40)
            self.assertGreater(right_color.blue(), 200)
            self.assertLess(right_color.red(), 40)

    def test_builds_synthetic_uv_grid_tile_pyramid(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            synthetic_report = generate_synthetic_moon_source_maps(root / "source", width=128, height=64)
            paths = synthetic_report["paths"]
            output_dir = root / "assets" / "moon_tiles_synthetic"

            build_report = build_moon_tile_tree(
                root,
                albedo_source=Path(paths["uv_grid"]),
                normal_source=Path(paths["flat_normal"]),
                output_dir=output_dir,
                tile_size=16,
                max_level=2,
            )

            self.assertEqual(build_report["written_albedo_tile_count"], 42)
            self.assertEqual(build_report["written_normal_tile_count"], 42)
            validation_report = validate_moon_tile_tree(root, output_dir=output_dir)
            self.assertTrue(validation_report["manager_compatibility"]["usable"])
            self.assertEqual(validation_report["tile_uv_validation"]["status"], "pass")

    def test_source_vs_tile_uv_sampling_matches_boundaries_seam_and_poles(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            synthetic_report = generate_synthetic_moon_source_maps(root / "source", width=128, height=64)
            paths = synthetic_report["paths"]
            output_dir = root / "assets" / "moon_tiles_synthetic"
            build_moon_tile_tree(
                root,
                albedo_source=Path(paths["uv_grid"]),
                normal_source=Path(paths["flat_normal"]),
                output_dir=output_dir,
                tile_size=16,
                max_level=2,
            )

            report = validate_tile_uv_sampling(root, output_dir=output_dir, source_image_path=Path(paths["uv_grid"]), levels=[0, 1, 2], samples_per_level=64)

            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["failed_sample_count"], 0)
            self.assertGreater(report["tested_sample_count"], 200)

    def test_tiled_sampling_preserves_v_orientation(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            synthetic_report = generate_synthetic_moon_source_maps(root / "source", width=128, height=64)
            paths = synthetic_report["paths"]
            output_dir = root / "assets" / "moon_tiles_synthetic"
            build_moon_tile_tree(
                root,
                albedo_source=Path(paths["uv_grid"]),
                normal_source=Path(paths["flat_normal"]),
                output_dir=output_dir,
                tile_size=16,
                max_level=1,
            )

            north_sample = _sample_tile_tree_rgb_at_uv(output_dir, 1, 0.42, 0.08)
            south_sample = _sample_tile_tree_rgb_at_uv(output_dir, 1, 0.42, 0.92)

            self.assertLess(north_sample[1], south_sample[1])

    def test_lod_luminance_consistency_and_flat_normals_validate(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            synthetic_report = generate_synthetic_moon_source_maps(root / "source", width=128, height=64)
            paths = synthetic_report["paths"]
            output_dir = root / "assets" / "moon_tiles_synthetic"
            build_moon_tile_tree(
                root,
                albedo_source=Path(paths["uv_grid"]),
                normal_source=Path(paths["flat_normal"]),
                output_dir=output_dir,
                tile_size=16,
                max_level=2,
            )

            luminance_report = validate_tile_lod_luminance_consistency(root, output_dir=output_dir)
            normal_report = validate_tiled_normal_maps(root, output_dir=output_dir, expected_flat=True)

            self.assertEqual(luminance_report["status"], "pass")
            self.assertLessEqual(luminance_report["max_mean_luminance_jump"], 0.08)
            self.assertEqual(normal_report["status"], "pass")
            self.assertLessEqual(normal_report["neutral_mean_delta"], 2.0)


if __name__ == "__main__":
    unittest.main()
