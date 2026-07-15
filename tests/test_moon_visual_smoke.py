from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from PySide6.QtGui import QColor, QImage

from scripts.build_moon_tiles import build_moon_tile_tree
from scripts.moon_visual_smoke import (
    build_moon_phase5g_real_tile_assets_report,
    build_moon_phase5j_runtime_transition_report,
    build_moon_phase5k_normal_continuity_report,
    build_moon_phase5l_polar_guard_report,
    build_moon_phase5m_polar_cap_report,
    build_moon_phase5n_polar_cap_blend_report,
    build_moon_phase5o_polar_cap_integration_report,
)


def _write_gradient_image(path: Path, width: int, height: int) -> None:

    image = QImage(width, height, QImage.Format.Format_RGB888)
    for y in range(height):
        for x in range(width):
            image.setPixelColor(
                x,
                y,
                QColor((x * 255) // max(1, width - 1), (y * 255) // max(1, height - 1), 128),
            )
    if not image.save(str(path)):
        raise RuntimeError(f"Failed to write test image: {path}")


class MoonVisualSmokeReportTest(unittest.TestCase):

    def test_phase5g_real_tile_report_activates_tiled_shader(self) -> None:

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

            report = build_moon_phase5g_real_tile_assets_report(root, output_dir=output_dir)

            self.assertTrue(report["real_tile_assets_present"])
            self.assertTrue(report["real_tile_validation"]["manager_compatibility"]["usable"])
            self.assertTrue(report["real_tiled_shader_active"])
            self.assertGreater(report["resident_real_albedo_tile_count"], 0)
            self.assertGreater(report["resident_real_normal_tile_count"], 0)
            self.assertTrue(report["selected_real_tile_keys"])
            self.assertFalse(report["high_zoom_global_texture_warning"])
            self.assertGreater(report["visible_tile_count"], 0)
            self.assertGreater(report["selected_tile_count"], 0)
            self.assertGreater(report["bound_tile_count"], 0)
            self.assertGreaterEqual(report["visible_coverage_estimate"], 0.75)
            self.assertLessEqual(report["fallback_coverage_estimate"], 0.25)
            self.assertEqual(report["tile_uv_validation_status"], "pass")
            self.assertEqual(report["tile_lod_luminance_consistency"]["status"], "pass")
            self.assertEqual(report["tiled_normal_validation_status"], "pass")

    def test_phase5j_zoom_transition_uses_live_equivalence_gate(self) -> None:

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

            report = build_moon_phase5j_runtime_transition_report(root, output_dir=output_dir)

            self.assertTrue(report["transition_safe"])
            views = report["views"]
            self.assertEqual(len(views), 2)
            self.assertEqual(views[0]["active_source_mode"], "global")
            self.assertEqual(views[1]["active_source_mode"], "tiled")
            self.assertEqual(views[1]["live_equivalence_status"], "pass")
            self.assertGreaterEqual(views[1]["visible_coverage_estimate"], 0.999)
            self.assertFalse(views[1]["out_of_bounds_tile_sampling"])
            self.assertEqual(views[0]["tile_cache_generation_id"], views[1]["tile_cache_generation_id"])

    def test_phase5k_transition_keeps_tiled_albedo_while_fading_normals(self) -> None:

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

            report = build_moon_phase5k_normal_continuity_report(root, output_dir=output_dir)

            self.assertTrue(report["default_transition_suppresses_tiled_normal"])
            views = report["views"]
            self.assertEqual(views[0]["active_source_mode"], "global")
            self.assertEqual(views[1]["active_source_mode"], "tiled")
            self.assertEqual(views[1]["active_normal_source"], "global")
            self.assertEqual(views[1]["tiled_normal_fade_factor"], 0.0)
            self.assertTrue(views[1]["normal_tile_statistics"])
            modes = {entry["mode"]: entry for entry in report["diagnostic_modes_at_1_1"]}
            self.assertEqual(modes["tiled_albedo_tiled_normal_unpolished"]["active_normal_source"], "tiled")
            self.assertEqual(modes["tiled_albedo_global_normal"]["active_normal_source"], "global")
            self.assertEqual(modes["tiled_albedo_no_normal"]["active_normal_source"], "none")
            self.assertEqual(modes["global_albedo_global_normal"]["active_source_mode"], "global")

    def test_phase5l_smoke_reports_visible_polar_guard_and_seam_wrap(self) -> None:

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

            report = build_moon_phase5l_polar_guard_report(root, output_dir=output_dir)

            self.assertTrue(report["seam_wrap_active"])
            guarded = report["guarded_view"]
            self.assertIn("visible_pole_proximity", guarded)
            self.assertIn("visible_seam_proximity", guarded)
            self.assertIn("polar_normal_fade_factor", guarded)
            self.assertIn("polar_albedo_fade_factor", guarded)
            self.assertIn("polar_analytic_normal_active", guarded)
            self.assertIn("pole_singularity_guard_active", guarded)

    def test_phase5m_smoke_routes_visible_south_pole_through_cap(self) -> None:

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
                polar_cap_size=64,
            )

            report = build_moon_phase5m_polar_cap_report(root, output_dir=output_dir)

            self.assertTrue(report["south_polar_cap_routes_visible_artifact"])
            self.assertTrue(report["fallback_is_safe"])
            south_views = [view for view in report["views"] if float(view["visible_pole_latitude"] or 0.0) < 0.0]
            self.assertTrue(south_views)
            self.assertTrue(all(view["south_polar_cap_available"] for view in south_views))
            self.assertEqual(south_views[-1]["polar_cap_projection"], "azimuthal_equidistant")

    def test_phase5m_missing_caps_retain_phase5l_fallback(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            albedo_source = root / "albedo.png"
            output_dir = root / "assets" / "moon_tiles"
            _write_gradient_image(albedo_source, 64, 32)
            build_moon_tile_tree(
                root,
                albedo_source=albedo_source,
                output_dir=output_dir,
                tile_size=16,
                max_level=1,
                generate_polar_caps=False,
            )

            report = build_moon_phase5m_polar_cap_report(root, output_dir=output_dir)

            self.assertFalse(report["south_polar_cap_routes_visible_artifact"])
            self.assertTrue(report["fallback_is_safe"])
            self.assertTrue(
                all(
                    "phase5l_fallback" in str(view["polar_cap_fallback_reason"])
                    for view in report["views"]
                    if float(view["visible_pole_latitude"] or 0.0) < 0.0
                )
            )

    def test_phase5n_smoke_reports_blend_metrics_and_conservative_normal_route(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            albedo_source = root / "albedo.png"
            displacement_source = root / "height.png"
            output_dir = root / "assets" / "moon_tiles"
            _write_gradient_image(albedo_source, 128, 64)
            _write_gradient_image(displacement_source, 128, 64)
            build_moon_tile_tree(
                root,
                albedo_source=albedo_source,
                displacement_source=displacement_source,
                output_dir=output_dir,
                tile_size=16,
                max_level=1,
                polar_cap_size=128,
            )

            report = build_moon_phase5n_polar_cap_blend_report(root, output_dir=output_dir)

            self.assertTrue(report["boundary_match_bounded"])
            self.assertTrue(report["normal_blend_is_conservative"])
            view = report["views"][-1]
            self.assertEqual(view["polar_cap_size_px"], 128)
            self.assertEqual(view["polar_cap_blend_start_lat_deg"], 60.0)
            self.assertEqual(view["polar_cap_blend_end_lat_deg"], 78.0)
            self.assertIn("linear_mipmap_linear", view["polar_cap_filtering"])

    def test_phase5o_smoke_reports_cap_core_integration_metrics(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            albedo_source = root / "albedo.png"
            displacement_source = root / "height.png"
            output_dir = root / "assets" / "moon_tiles"
            _write_gradient_image(albedo_source, 128, 64)
            _write_gradient_image(displacement_source, 128, 64)

            build_moon_tile_tree(
                root,
                albedo_source=albedo_source,
                displacement_source=displacement_source,
                output_dir=output_dir,
                tile_size=16,
                max_level=1,
                polar_cap_size=128,
            )

            report = build_moon_phase5o_polar_cap_integration_report(root, output_dir=output_dir)

            self.assertTrue(report["boundary_contrast_bounded"])
            self.assertTrue(report["core_detail_is_integrated"])
            self.assertTrue(report["footprint_risk_clear"])
            view = report["views"][-1]
            self.assertEqual(view["polar_cap_size_px"], 128)
            self.assertGreaterEqual(float(view["polar_cap_core_sharpness_estimate"] or 0.0), 0.0)


if __name__ == "__main__":
    unittest.main()
