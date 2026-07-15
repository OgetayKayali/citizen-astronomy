from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from photometry_app.core.milky_way_tile_format_benchmark import (
    MilkyWayTileFormatVariant,
    build_summary_tsv_rows,
    default_format_variants,
    qimage_rgb_array,
    verify_lossless_pixel_equality,
    write_benchmark_report,
)


class MilkyWayTileFormatBenchmarkTest(unittest.TestCase):
    def test_verify_lossless_pixel_equality_detects_exact_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.png"
            candidate_path = root / "candidate.tiff"
            pixels = np.zeros((4, 6, 3), dtype=np.uint8)
            pixels[:, :, 0] = 10
            pixels[:, :, 1] = 20
            pixels[:, :, 2] = 30
            Image.fromarray(pixels, mode="RGB").save(source_path)
            Image.fromarray(pixels, mode="RGB").save(candidate_path, compression="tiff_adobe_deflate")

            equality = verify_lossless_pixel_equality(source_path, candidate_path)

            self.assertTrue(equality["equal"])
            self.assertEqual(equality["differing_pixel_count"], 0)
            self.assertEqual(equality["max_channel_delta"], 0)

    def test_qimage_rgb_array_preserves_lossless_tiff_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.tiff"
            pixels = np.arange(4 * 5 * 3, dtype=np.uint8).reshape((4, 5, 3))
            Image.fromarray(pixels, mode="RGB").save(source_path, compression="tiff_adobe_deflate")

            decoded = qimage_rgb_array(source_path)

            np.testing.assert_array_equal(decoded, pixels)

    def test_write_benchmark_report_writes_json_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            json_path = root / "report.json"
            summary_path = root / "report.tsv"
            payload = {
                "decode_summary": {
                    "png_current": {
                        "all": {
                            "decode_ms_total": 10.0,
                            "decode_ms_median": 3.0,
                            "decode_ms_p90": 4.0,
                            "decode_ms_max": 5.0,
                        }
                    }
                },
                "prepare_summary": {
                    "png_current": {
                        "all": {
                            "prepare_total_ms_total": 12.0,
                            "prepare_total_ms_median": 3.5,
                            "prepare_total_ms_p90": 4.5,
                        }
                    }
                },
                "asset_summary": {
                    "png_current": {
                        "disk_size_total_bytes": 1234,
                        "pixel_mismatch_count": 0,
                        "orientation_tags": [],
                    }
                },
                "projected_sizes": {
                    "png_current": {
                        "projected_total_bytes": 5678,
                    }
                },
            }

            write_benchmark_report(payload, json_path=json_path, summary_path=summary_path)

            self.assertEqual(json.loads(json_path.read_text(encoding="utf-8")), payload)
            summary_text = summary_path.read_text(encoding="utf-8")
            self.assertIn("variant\tbucket", summary_text)
            self.assertIn("png_current\tall", summary_text)

    def test_default_variants_include_tiff_deflate(self) -> None:
        variant_names = {variant.name for variant in default_format_variants()}

        self.assertIn("png_current", variant_names)
        self.assertIn("tiff_deflate", variant_names)


if __name__ == "__main__":
    unittest.main()