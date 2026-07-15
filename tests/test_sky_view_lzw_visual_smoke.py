from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import _tmp_sky_view_lzw_visual_smoke as lzw_visual_smoke


def _write_manifest(temp_root: Path) -> Path:
    manifest_path = temp_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "projection": "equirectangular_celestial",
                "source_name": "demo_source",
                "source_width": 32768,
                "source_height": 16384,
                "tile_size": 1024,
                "tile_width": 1026,
                "tile_height": 1026,
                "gutter_pixels": 1,
                "content_region": [1, 1, 1024, 1024],
                "padded_tile": True,
                "color_space": "srgb",
                "tone_preprocessing": "source_like",
                "orientation": {"ra_increases": "left", "dec_axis": "top_to_bottom"},
                "levels": [{"level": 2, "width": 32768, "height": 16384, "path": "L2/{x}_{y}.tiff"}],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return manifest_path


def _metric(manifest_path: Path, **overrides: object) -> dict[str, object]:
    metric = {
        "runtime_fields": {"tile_decode_failures": "0"},
        "renderer": "opengl",
        "asset_mode": "tiled_manifest",
        "asset_path": str(manifest_path.resolve()),
        "selected_level": "L2",
        "requested_tile_count": 28,
        "resident_tile_count": 28,
        "missing_tile_ids": [],
        "deferred_tile_ids": [],
        "cpu_fallback_used": False,
        "base_drawn": True,
        "base_source": "manifest_base",
        "base_skipped_reason": "none",
        "all_requested_tiles_resident": True,
        "debug_render_mode": "normal",
    }
    metric.update(overrides)
    return metric


class LzwVisualSmokeAuthorityTest(unittest.TestCase):
    def test_evaluate_authoritative_capture_marks_normal_capture_authoritative_when_conditions_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = _write_manifest(Path(temp_dir))
            metadata = lzw_visual_smoke._load_manifest_base_metadata(manifest_path)

            result = lzw_visual_smoke._evaluate_authoritative_capture(
                variant_name="normal",
                metric=_metric(manifest_path),
                manifest_path=manifest_path,
                manifest_base_metadata=metadata,
                capture_after_full_residency=True,
                capture_after_settled_paint=True,
            )

            self.assertTrue(result["authoritative"])
            self.assertEqual(result["failed_authoritative_conditions"], [])
            self.assertTrue(result["capture_after_full_residency"])
            self.assertTrue(result["capture_after_settled_paint"])

    def test_evaluate_authoritative_capture_accepts_resident_tiles_with_skipped_base(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = _write_manifest(Path(temp_dir))
            metadata = lzw_visual_smoke._load_manifest_base_metadata(manifest_path)

            result = lzw_visual_smoke._evaluate_authoritative_capture(
                variant_name="normal",
                metric=_metric(
                    manifest_path,
                    base_drawn=False,
                    base_source="manifest_base",
                    base_skipped_reason="all_tiles_resident",
                    all_requested_tiles_resident=True,
                ),
                manifest_path=manifest_path,
                manifest_base_metadata=metadata,
                capture_after_full_residency=True,
                capture_after_settled_paint=True,
            )

            self.assertTrue(result["authoritative"])
            self.assertTrue(result["base_ready_for_final"])
            self.assertEqual(result["failed_authoritative_conditions"], [])

    def test_evaluate_authoritative_capture_rejects_deferred_tiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = _write_manifest(Path(temp_dir))
            metadata = lzw_visual_smoke._load_manifest_base_metadata(manifest_path)

            result = lzw_visual_smoke._evaluate_authoritative_capture(
                variant_name="normal",
                metric=_metric(
                    manifest_path,
                    resident_tile_count=18,
                    deferred_tile_ids=["L2/7/0"],
                ),
                manifest_path=manifest_path,
                manifest_base_metadata=metadata,
                capture_after_full_residency=False,
                capture_after_settled_paint=False,
            )

            self.assertFalse(result["authoritative"])
            self.assertIn("requested_equals_resident", result["failed_authoritative_conditions"])
            self.assertIn("deferred_zero", result["failed_authoritative_conditions"])

    def test_evaluate_authoritative_capture_rejects_unexplained_missing_base_draw(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = _write_manifest(Path(temp_dir))
            metadata = lzw_visual_smoke._load_manifest_base_metadata(manifest_path)

            result = lzw_visual_smoke._evaluate_authoritative_capture(
                variant_name="normal",
                metric=_metric(manifest_path, base_drawn=False, base_source="none"),
                manifest_path=manifest_path,
                manifest_base_metadata=metadata,
                capture_after_full_residency=True,
                capture_after_settled_paint=True,
            )

            self.assertFalse(result["authoritative"])
            self.assertIn("base_ready_for_final", result["failed_authoritative_conditions"])
            self.assertIn("base_source_manifest_base", result["failed_authoritative_conditions"])

    def test_load_manifest_base_metadata_reports_content_region_cropping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = _write_manifest(Path(temp_dir))

            metadata = lzw_visual_smoke._load_manifest_base_metadata(manifest_path)

            self.assertEqual(metadata["content_region"], [1, 1, 1024, 1024])
            self.assertTrue(metadata["content_region_cropped"])
            self.assertEqual(metadata["base_stitch_mode"], "content_region_cropped")

    def test_append_progress_marker_creates_partial_case_entry(self) -> None:
        report: dict[str, object] = {"view_reports": []}
        view_case = lzw_visual_smoke.milky_way_validate.ViewCase("polar_medium", 40.0, 80.0, 45.0)

        entry = lzw_visual_smoke._append_progress_marker(report, view_case, "polar_setup_started")

        self.assertEqual(entry["case"]["name"], "polar_medium")
        self.assertEqual(entry["progress_markers"][0]["marker"], "polar_setup_started")
        self.assertEqual(entry["last_progress_marker"], "polar_setup_started")

    def test_update_case_report_entry_preserves_normal_artifact_without_variants(self) -> None:
        report: dict[str, object] = {"view_reports": []}
        view_case = lzw_visual_smoke.milky_way_validate.ViewCase("polar_medium", 40.0, 80.0, 45.0)
        entry = lzw_visual_smoke._ensure_case_report(report, view_case)
        lzw_visual_smoke._append_progress_marker(report, view_case, "polar_pre_capture_state_written")

        lzw_visual_smoke._update_case_report_entry(
            entry,
            view_case=view_case,
            settle_metrics=[_metric(Path("c:/manifest.json"), requested_tile_count=100, resident_tile_count=24, deferred_tile_ids=["L4/0/0"])],
            variant_reports={
                "normal": {
                    "screenshot": "C:/tmp/polar_medium_normal.png",
                    "metrics": {"last": _metric(Path("c:/manifest.json"), requested_tile_count=100, resident_tile_count=100)},
                    "tile_diagnostics": {},
                    "authoritative": True,
                    "failed_authoritative_conditions": [],
                }
            },
            pan_sweep={"count": 0},
            settle_progress=[{"frame_index": 1, "requested": 100, "resident": 24, "missing": 0, "deferred": 76}],
            extra_fields={"polar_variants_skipped": True},
        )

        self.assertEqual(entry["screenshot"], "C:/tmp/polar_medium_normal.png")
        self.assertTrue(entry["variant_reports"]["normal"]["authoritative"])
        self.assertTrue(entry["polar_variants_skipped"])
        self.assertEqual(entry["progress_markers"][0]["marker"], "polar_pre_capture_state_written")