from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from astropy.wcs import WCS

from photometry_app.core.settings import SkyAtlasCustomOverlayRecord, SkyAtlasCustomOverlaySurvey
from photometry_app.core.sky_atlas_survey_storage import (
    SURVEY_MANIFEST_FILE_NAME,
    discover_surveys,
    overlay_directory,
    persist_survey_to_disk,
    resolve_overlay_record_for_survey,
    sky_atlas_survey_root,
    sync_surveys_to_disk,
)


class SkyAtlasSurveyStorageTest(unittest.TestCase):
    def _write_overlay_files(self, survey_id: str, overlay_id: str) -> SkyAtlasCustomOverlayRecord:
        overlay_dir = overlay_directory(survey_id, overlay_id)
        overlay_dir.mkdir(parents=True, exist_ok=True)
        image_path = overlay_dir / "overlay.png"
        wcs_path = overlay_dir / "overlay.wcs.fits"
        rgba = np.zeros((8, 10, 4), dtype=np.uint8)
        rgba[..., :3] = 180
        rgba[..., 3] = 255
        from PIL import Image

        from photometry_app.core.sky_atlas_custom_overlay import _write_overlay_wcs_cache

        Image.fromarray(rgba, mode="RGBA").save(image_path, format="PNG")
        _write_overlay_wcs_cache(wcs_path, WCS(naxis=2), 10, 8)
        return SkyAtlasCustomOverlayRecord(
            overlay_id=overlay_id,
            display_name="tile.png",
            cached_image_path=f"overlays/{overlay_id}/overlay.png",
            cached_wcs_path=f"overlays/{overlay_id}/overlay.wcs.fits",
            source_image_path="C:/images/tile.png",
            width=10,
            height=8,
        )

    def test_persist_and_discover_survey_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_root = Path(temp_dir)
            with patch(
                "photometry_app.core.sky_atlas_survey_storage.application_install_path",
                return_value=install_root,
            ):
                survey = SkyAtlasCustomOverlaySurvey(
                    survey_id="survey-ha",
                    name="Hydrogen Alpha",
                    filter_name="Ha",
                    overlays=[self._write_overlay_files("survey-ha", "overlay-1")],
                )
                persisted = persist_survey_to_disk(survey)
                manifest_path = sky_atlas_survey_root() / "survey-ha" / SURVEY_MANIFEST_FILE_NAME
                self.assertTrue(manifest_path.is_file())
                discovered = discover_surveys()
                self.assertEqual(len(discovered), 1)
                self.assertEqual(discovered[0].name, "Hydrogen Alpha")
                self.assertEqual(discovered[0].filter_name, "Ha")
                self.assertEqual(len(discovered[0].overlays), 1)
                resolved = resolve_overlay_record_for_survey(
                    "survey-ha",
                    persisted.overlays[0],
                )
                self.assertTrue(Path(resolved.cached_image_path).is_file())
                self.assertTrue(Path(resolved.cached_wcs_path).is_file())

    def test_sync_surveys_to_disk_removes_deleted_survey(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_root = Path(temp_dir)
            with patch(
                "photometry_app.core.sky_atlas_survey_storage.application_install_path",
                return_value=install_root,
            ):
                survey_a = SkyAtlasCustomOverlaySurvey(
                    survey_id="survey-a",
                    name="Survey A",
                    filter_name="Ha",
                    overlays=[self._write_overlay_files("survey-a", "overlay-a")],
                )
                survey_b = SkyAtlasCustomOverlaySurvey(
                    survey_id="survey-b",
                    name="Survey B",
                    filter_name="OIII",
                    overlays=[self._write_overlay_files("survey-b", "overlay-b")],
                )
                sync_surveys_to_disk([survey_a, survey_b])
                self.assertTrue((sky_atlas_survey_root() / "survey-a").is_dir())
                self.assertTrue((sky_atlas_survey_root() / "survey-b").is_dir())
                sync_surveys_to_disk([survey_a], previous_survey_ids={"survey-a", "survey-b"})
                self.assertTrue((sky_atlas_survey_root() / "survey-a").is_dir())
                self.assertFalse((sky_atlas_survey_root() / "survey-b").exists())

    def test_survey_manifest_uses_portable_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_root = Path(temp_dir)
            with patch(
                "photometry_app.core.sky_atlas_survey_storage.application_install_path",
                return_value=install_root,
            ):
                survey = SkyAtlasCustomOverlaySurvey(
                    survey_id="survey-share",
                    name="Shareable",
                    filter_name="",
                    overlays=[self._write_overlay_files("survey-share", "overlay-share")],
                )
                persist_survey_to_disk(survey)
                manifest = json.loads(
                    (sky_atlas_survey_root() / "survey-share" / SURVEY_MANIFEST_FILE_NAME).read_text(
                        encoding="utf-8"
                    )
                )
                overlay = manifest["overlays"][0]
                self.assertEqual(overlay["cached_image_path"], "overlays/overlay-share/overlay.png")
                self.assertEqual(overlay["cached_wcs_path"], "overlays/overlay-share/overlay.wcs.fits")


if __name__ == "__main__":
    unittest.main()
