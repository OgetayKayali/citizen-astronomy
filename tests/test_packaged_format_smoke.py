from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from photometry_app.app_metadata import APP_VERSION
from photometry_app.core.packaged_format_smoke import (
    build_packaged_format_smoke_result,
    run_about_dialog_smoke,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = ROOT / "packaging" / "fixtures"
DEFAULT_EXE_CANDIDATES = (
    ROOT / "_tmp_alpha_review_dist2" / "CitizenAstronomyAlphaReview" / "CitizenAstronomyAlphaReview.exe",
    ROOT / "_tmp_alpha_review_dist" / "CitizenAstronomyAlphaReview" / "CitizenAstronomyAlphaReview.exe",
)
DEFAULT_EXE = next((candidate for candidate in DEFAULT_EXE_CANDIDATES if candidate.is_file()), DEFAULT_EXE_CANDIDATES[0])


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class PackagedFormatSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        generator = ROOT / "packaging" / "generate_smoke_fixtures.py"
        if not (FIXTURES_DIR / "smoke_tiny.xisf").is_file():
            subprocess.run([str(ROOT / ".venv" / "Scripts" / "python.exe"), str(generator)], cwd=str(ROOT), check=True)

    def test_build_packaged_format_smoke_result_passes_with_fixtures(self) -> None:
        result = build_packaged_format_smoke_result(fixtures_dir=FIXTURES_DIR)

        self.assertTrue(result["success"])
        self.assertTrue(result["xisf_import"]["success"])
        self.assertTrue(result["xisf"]["success"])
        self.assertFalse(result["xisf"].get("placeholder"))
        self.assertTrue(result["png"]["success"])
        self.assertTrue(result["webp"]["success"])
        self.assertTrue(result["fits"]["success"])
        self.assertTrue(result["qt_tiff_lzw"]["success"])

    def test_about_dialog_smoke_does_not_crash(self) -> None:
        result = run_about_dialog_smoke()
        self.assertTrue(result["success"])
        self.assertEqual(result["version"], APP_VERSION)

    def test_missing_xisf_fixture_reports_placeholder_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fixtures_dir = Path(temp_dir)
            (fixtures_dir / "smoke_tiny.png").write_bytes(b"not-a-real-png")
            with patch("photometry_app.core.packaged_format_smoke._check_qt_image_file", return_value={"success": True, "path": "", "error": "", "dimensions": [1, 1]}), patch(
                "photometry_app.core.packaged_format_smoke._check_fits_file",
                return_value={"success": True, "path": "", "error": "", "dimensions": [1, 1]},
            ), patch(
                "photometry_app.core.packaged_format_smoke.build_qt_image_format_smoke_result",
                return_value={"success": True, "qt_image_support": {}},
            ), patch(
                "photometry_app.core.packaged_format_smoke._check_xisf_import",
                return_value={"success": True, "error": ""},
            ):
                result = build_packaged_format_smoke_result(fixtures_dir=fixtures_dir)

        self.assertFalse(result["success"])
        self.assertTrue(result["xisf"]["placeholder"])
        self.assertIn("fixture missing", result["xisf"]["error"])


@unittest.skipUnless(DEFAULT_EXE.is_file(), "packaged alpha-review executable is not built")
class PackagedExecutableSmokeIntegrationTest(unittest.TestCase):
    def test_frozen_executable_packaged_format_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "packaged_format_smoke.json"
            completed = subprocess.run(
                [
                    str(DEFAULT_EXE),
                    "--packaged-format-smoke",
                    "--packaged-format-smoke-fixtures",
                    str(FIXTURES_DIR),
                    "--packaged-format-smoke-output",
                    str(output_path),
                ],
                cwd=str(DEFAULT_EXE.parent),
                capture_output=True,
                text=True,
                check=False,
            )
            payload = json.loads(completed.stdout or output_path.read_text(encoding="utf-8"))

        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        self.assertTrue(payload["success"])
        self.assertTrue(payload["xisf"]["success"])
        self.assertTrue(payload["xisf_import"]["success"])
