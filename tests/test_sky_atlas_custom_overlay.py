from __future__ import annotations

import unittest
from pathlib import Path

from photometry_app.core.settings import (
    SkyAtlasCustomOverlayRecord,
    SkyAtlasCustomOverlaySurvey,
    _coerce_sky_atlas_custom_overlay_surveys,
    _coerce_sky_atlas_custom_overlays,
    _migrate_sky_atlas_custom_overlay_surveys,
    _serialize_sky_atlas_custom_overlay_surveys,
    _serialize_sky_atlas_custom_overlays,
    sky_atlas_custom_overlays_for_active_survey,
)
from photometry_app.core.sky_atlas_custom_overlay import (
    _downscale_overlay_arrays,
    _array_to_overlay_rgb_alpha,
    apply_overlay_edge_feather,
    apply_overlay_tone_controls,
    build_overlay_display_qimage,
    is_sky_atlas_overlay_path,
    LoadedSkyAtlasCustomOverlay,
)
import numpy as np


class SkyAtlasCustomOverlaySettingsTest(unittest.TestCase):
    def test_custom_overlay_settings_round_trip(self) -> None:
        records = [
            SkyAtlasCustomOverlayRecord(
                overlay_id="overlay-1",
                display_name="tile-a.png",
                cached_image_path="C:/cache/sky-atlas-overlays/overlay-1/overlay.png",
                cached_wcs_path="C:/cache/sky-atlas-overlays/overlay-1/overlay.wcs.fits",
                source_image_path="C:/images/tile-a.png",
                width=2048,
                height=1024,
            )
        ]
        payload = _serialize_sky_atlas_custom_overlays(records)
        assert payload is not None
        restored = _coerce_sky_atlas_custom_overlays(payload)
        self.assertEqual(restored, records)


class SkyAtlasCustomOverlaySurveySettingsTest(unittest.TestCase):
    def test_survey_settings_round_trip(self) -> None:
        overlay = SkyAtlasCustomOverlayRecord(
            overlay_id="overlay-1",
            display_name="tile-a.png",
            cached_image_path="C:/cache/sky-atlas-overlays/overlay-1/overlay.png",
            cached_wcs_path="C:/cache/sky-atlas-overlays/overlay-1/overlay.wcs.fits",
            source_image_path="C:/images/tile-a.png",
            width=2048,
            height=1024,
        )
        surveys = [
            SkyAtlasCustomOverlaySurvey(
                survey_id="survey-1",
                name="Hydrogen Alpha",
                filter_name="Ha",
                overlays=[overlay],
            )
        ]
        payload = _serialize_sky_atlas_custom_overlay_surveys(surveys)
        assert payload is not None
        restored = _coerce_sky_atlas_custom_overlay_surveys(payload)
        self.assertEqual(restored, surveys)

    def test_legacy_overlay_migration_creates_default_survey(self) -> None:
        overlay = SkyAtlasCustomOverlayRecord(
            overlay_id="overlay-legacy",
            display_name="legacy.png",
            cached_image_path="C:/cache/legacy.png",
            cached_wcs_path="C:/cache/legacy.wcs.fits",
            source_image_path="C:/images/legacy.png",
            width=512,
            height=512,
        )
        surveys, active_id = _migrate_sky_atlas_custom_overlay_surveys(None, [overlay], None)
        self.assertIsNotNone(surveys)
        assert surveys is not None
        self.assertEqual(len(surveys), 1)
        self.assertEqual(surveys[0].overlays, [overlay])
        self.assertEqual(active_id, surveys[0].survey_id)

    def test_active_survey_overlays_resolve_from_settings(self) -> None:
        import tempfile
        from dataclasses import replace
        from unittest.mock import patch

        from photometry_app.core.settings import _settings_from_payload

        overlay_a = SkyAtlasCustomOverlayRecord(
            overlay_id="overlay-a",
            display_name="a.png",
            cached_image_path="C:/cache/a.png",
            cached_wcs_path="C:/cache/a.wcs.fits",
            source_image_path="C:/images/a.png",
            width=100,
            height=100,
        )
        overlay_b = SkyAtlasCustomOverlayRecord(
            overlay_id="overlay-b",
            display_name="b.png",
            cached_image_path="C:/cache/b.png",
            cached_wcs_path="C:/cache/b.wcs.fits",
            source_image_path="C:/images/b.png",
            width=100,
            height=100,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = _settings_from_payload({}, Path(temp_dir) / "config.json", use_last_theme=False)
        settings = replace(
            settings,
            sky_atlas_custom_overlay_surveys=[
                SkyAtlasCustomOverlaySurvey(
                    survey_id="survey-a",
                    name="Survey A",
                    filter_name="Ha",
                    overlays=[overlay_a],
                ),
                SkyAtlasCustomOverlaySurvey(
                    survey_id="survey-b",
                    name="Survey B",
                    filter_name="OIII",
                    overlays=[overlay_b],
                ),
            ],
            sky_atlas_custom_overlay_active_survey_id="survey-b",
        )
        with patch("photometry_app.core.sky_atlas_survey_storage.discover_surveys", return_value=[]):
            with patch(
                "photometry_app.core.sky_atlas_survey_storage.migrate_legacy_survey_to_disk",
                side_effect=lambda survey: survey,
            ):
                overlays = sky_atlas_custom_overlays_for_active_survey(settings)
        self.assertEqual([overlay.overlay_id for overlay in overlays], [overlay_b.overlay_id])


class SkyAtlasCustomOverlayToneTest(unittest.TestCase):
    def test_apply_overlay_tone_controls_preserves_shape(self) -> None:
        sampled = np.full((4, 4, 3), 128.0, dtype=np.float32)
        toned = apply_overlay_tone_controls(sampled, brightness=1.2, saturation=1.5)
        self.assertEqual(toned.shape, sampled.shape)
        self.assertGreater(float(toned.max()), 0.0)


class SkyAtlasCustomOverlayFormatTest(unittest.TestCase):
    def test_supported_overlay_suffixes(self) -> None:
        self.assertTrue(is_sky_atlas_overlay_path(Path("tile.png")))
        self.assertTrue(is_sky_atlas_overlay_path(Path("tile.xisf")))
        self.assertTrue(is_sky_atlas_overlay_path(Path("tile.tif")))
        self.assertTrue(is_sky_atlas_overlay_path(Path("tile.tiff")))
        self.assertTrue(is_sky_atlas_overlay_path(Path("tile.fit")))
        self.assertTrue(is_sky_atlas_overlay_path(Path("tile.fits")))
        self.assertFalse(is_sky_atlas_overlay_path(Path("tile.jpg")))

    def test_grayscale_array_converts_to_rgb(self) -> None:
        gray = np.linspace(0.0, 1000.0, 16, dtype=np.float32).reshape(4, 4)
        rgb, alpha = _array_to_overlay_rgb_alpha(gray)
        self.assertIsNone(alpha)
        self.assertEqual(rgb.shape, (4, 4, 3))
        self.assertEqual(rgb.dtype, np.uint8)
        self.assertGreater(int(rgb.max()), 0)

    def test_downscale_overlay_arrays_limits_long_edge(self) -> None:
        rgb = np.zeros((2000, 4000, 3), dtype=np.uint8)
        rgb[..., 0] = 255
        downscaled_rgb, downscaled_alpha, width, height = _downscale_overlay_arrays(
            rgb,
            None,
            max_long_edge=1024,
        )
        self.assertIsNone(downscaled_alpha)
        self.assertEqual(max(width, height), 1024)
        self.assertEqual(downscaled_rgb.shape, (height, width, 3))


class SkyAtlasCustomOverlayDisplayImageTest(unittest.TestCase):
    def test_build_overlay_display_qimage_preserves_dimensions(self) -> None:
        from astropy.wcs import WCS

        overlay = LoadedSkyAtlasCustomOverlay(
            overlay_id="overlay-test",
            display_name="tile.png",
            image_rgb=np.full((8, 12, 3), 180, dtype=np.uint8),
            image_alpha=None,
            wcs=WCS(naxis=2),
            width=12,
            height=8,
        )
        image = build_overlay_display_qimage(overlay, brightness=1.0, saturation=1.0)
        self.assertFalse(image.isNull())
        self.assertEqual(image.width(), 12)
        self.assertEqual(image.height(), 8)

    def test_build_overlay_display_qimage_feather_uses_rgba(self) -> None:
        from astropy.wcs import WCS

        overlay = LoadedSkyAtlasCustomOverlay(
            overlay_id="overlay-feather",
            display_name="tile.png",
            image_rgb=np.full((8, 12, 3), 180, dtype=np.uint8),
            image_alpha=None,
            wcs=WCS(naxis=2),
            width=12,
            height=8,
        )
        image = build_overlay_display_qimage(overlay, brightness=1.0, saturation=1.0, feather=0.5)
        self.assertFalse(image.isNull())
        self.assertTrue(image.hasAlphaChannel())

    def test_apply_overlay_edge_feather_fades_border_pixels(self) -> None:
        alpha = np.full((16, 16), 255, dtype=np.uint8)
        feathered = apply_overlay_edge_feather(alpha, width=16, height=16, feather_amount=0.8)
        self.assertLess(int(feathered[0, 8]), 255)
        self.assertEqual(int(feathered[8, 8]), 255)


if __name__ == "__main__":
    unittest.main()
