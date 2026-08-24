from __future__ import annotations

from dataclasses import FrozenInstanceError
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from astropy.io import fits
from astropy.wcs import WCS
import numpy as np

from photometry_app.core import survey_images
from photometry_app.core.survey_images import (
    SURVEY_DEFINITIONS,
    SurveyImageRequest,
    build_sky_explorer_field_wcs,
    retrieve_survey_image,
    scale_wcs_for_pixel_sampling,
    survey_definition_for_key,
    survey_target_rect_in_source_pixels,
    write_survey_image_fits,
)


def _celestial_wcs() -> WCS:
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [83.822, -5.391]
    wcs.wcs.crpix = [3.0, 2.5]
    wcs.wcs.cdelt = [-0.001, 0.001]
    return wcs


def _request(cache_dir: Path, *, survey_key: str = "dss2_blue") -> SurveyImageRequest:
    return SurveyImageRequest(
        survey_key=survey_key,
        wcs=_celestial_wcs(),
        width=5,
        height=4,
        target_rect=(10, 20, 300, 240),
        cache_dir=cache_dir,
    )


class SurveyImagesTest(unittest.TestCase):
    def test_definitions_are_exact_immutable_and_lookupable(self) -> None:
        self.assertEqual(
            [(definition.key, definition.title, definition.hips_id) for definition in SURVEY_DEFINITIONS],
            [
                ("dss2_blue", "DSS2 Blue", "CDS/P/DSS2/blue"),
                ("dss2_red", "DSS2 Red", "CDS/P/DSS2/red"),
                ("dss2_ir", "DSS2 IR", "CDS/P/DSS2/NIR"),
                ("panstarrs", "PanSTARRS", "CDS/P/PanSTARRS/DR1/color-i-r-g"),
                ("panstarrs_g", "PanSTARRS g", "CDS/P/PanSTARRS/DR1/g"),
                ("panstarrs_r", "PanSTARRS r", "CDS/P/PanSTARRS/DR1/r"),
                ("panstarrs_i", "PanSTARRS i", "CDS/P/PanSTARRS/DR1/i"),
                ("panstarrs_z", "PanSTARRS z", "CDS/P/PanSTARRS/DR1/z"),
                ("panstarrs_y", "PanSTARRS y", "CDS/P/PanSTARRS/DR1/y"),
                ("sdss_u", "SDSS u", "CDS/P/SDSS9/u"),
                ("sdss_g", "SDSS g", "CDS/P/SDSS9/g"),
                ("sdss_r", "SDSS r", "CDS/P/SDSS9/r"),
                ("sdss_i", "SDSS i", "CDS/P/SDSS9/i"),
                ("sdss_z", "SDSS z", "CDS/P/SDSS9/z"),
                ("twomass_j", "2MASS J", "CDS/P/2MASS/J"),
                ("twomass_h", "2MASS H", "CDS/P/2MASS/H"),
                ("twomass_k", "2MASS K", "CDS/P/2MASS/K"),
                ("wise_w1", "WISE W1", "CDS/P/allWISE/W1"),
                ("wise_w2", "WISE W2", "CDS/P/allWISE/W2"),
                ("wise_w3", "WISE W3", "CDS/P/allWISE/W3"),
                ("wise_w4", "WISE W4", "CDS/P/allWISE/W4"),
                ("galex_fuv", "GALEX FUV", "CDS/P/GALEXGR6/AIS/FUV"),
                ("galex_nuv", "GALEX NUV", "CDS/P/GALEXGR6/AIS/NUV"),
                ("shs_ha", "SHS Ha", "CDS/P/SHS"),
                ("iphas_dr2_ha", "IPHAS DR2 Ha", "CDS/P/IPHAS/DR2/Ha"),
                ("nsns_dr0_2_halpha", "NSNS DR0.2 Hα", "simg.de/P/NSNS/DR0_2/halpha"),
                ("nsns_dr0_2_oiii", "NSNS DR0.2 [OIII]", "simg.de/P/NSNS/DR0_2/oiii"),
                ("nsns_dr0_2_sii", "NSNS DR0.2 [SII]", "simg.de/P/NSNS/DR0_2/sii"),
            ],
        )
        self.assertEqual(survey_definition_for_key("SHS-HA").key, "shs_ha")
        self.assertEqual(survey_definition_for_key("nsns_dr0_2_halpha").hips_id, "simg.de/P/NSNS/DR0_2/halpha")
        with self.assertRaises(FrozenInstanceError):
            SURVEY_DEFINITIONS[0].title = "Changed"  # type: ignore[misc]
        with self.assertRaisesRegex(ValueError, "Unknown survey key"):
            survey_definition_for_key("missing")

    def test_query_receives_survey_fits_format_and_requested_wcs_shape(self) -> None:
        pixels = np.arange(20, dtype=np.float32).reshape(4, 5) + 1.0
        with tempfile.TemporaryDirectory() as temporary_directory:
            request = _request(Path(temporary_directory))
            with patch.object(
                survey_images.hips2fits,
                "query_with_wcs",
                return_value=fits.PrimaryHDU(data=pixels),
            ) as query:
                result = retrieve_survey_image(request)

        self.assertFalse(result.loaded_from_cache)
        np.testing.assert_array_equal(result.image_data, pixels)
        self.assertEqual(result.target_rect, (10.0, 20.0, 300.0, 240.0))
        query.assert_called_once()
        query_arguments = query.call_args.kwargs
        self.assertEqual(query_arguments["hips"], "CDS/P/DSS2/blue")
        self.assertEqual(query_arguments["format"], "fits")
        self.assertEqual(query_arguments["wcs"].array_shape, (4, 5))
        self.assertEqual(query_arguments["wcs"].pixel_shape, (5, 4))
        self.assertIsNot(query_arguments["wcs"], request.wcs)
        self.assertIsNone(request.wcs.array_shape)

    def test_channel_first_hdulist_data_is_returned_as_hwc(self) -> None:
        channel_first = np.stack(
            [
                np.full((4, 5), 1.0),
                np.full((4, 5), 2.0),
                np.full((4, 5), 3.0),
            ]
        )
        response = fits.HDUList([fits.PrimaryHDU(), fits.ImageHDU(data=channel_first)])
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.object(survey_images.hips2fits, "query_with_wcs", return_value=response):
                result = retrieve_survey_image(_request(Path(temporary_directory), survey_key="panstarrs"))

        self.assertEqual(result.image_data.shape, (4, 5, 3))
        np.testing.assert_array_equal(result.image_data[..., 0], channel_first[0])
        np.testing.assert_array_equal(result.image_data[..., 1], channel_first[1])
        np.testing.assert_array_equal(result.image_data[..., 2], channel_first[2])

    def test_channel_last_data_is_preserved(self) -> None:
        channel_last = np.arange(60, dtype=np.float32).reshape(4, 5, 3) + 1.0
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.object(
                survey_images.hips2fits,
                "query_with_wcs",
                return_value=fits.PrimaryHDU(data=channel_last),
            ):
                result = retrieve_survey_image(_request(Path(temporary_directory), survey_key="panstarrs"))

        np.testing.assert_array_equal(result.image_data, channel_last)

    def test_second_identical_request_is_loaded_from_cache(self) -> None:
        pixels = np.arange(20, dtype=np.int16).reshape(4, 5) + 1
        progress_messages: list[str] = []
        with tempfile.TemporaryDirectory() as temporary_directory:
            request = _request(Path(temporary_directory))
            request = SurveyImageRequest(
                request.survey_key,
                request.wcs,
                request.width,
                request.height,
                request.target_rect,
                request.cache_dir,
                progress_messages.append,
            )
            with patch.object(
                survey_images.hips2fits,
                "query_with_wcs",
                return_value=fits.PrimaryHDU(data=pixels),
            ) as query:
                first_result = retrieve_survey_image(request)
                second_result = retrieve_survey_image(request)

            cache_files = list(
                (Path(temporary_directory) / "sky-explorer-surveys").glob("*.npz")
            )

        self.assertFalse(first_result.loaded_from_cache)
        self.assertTrue(second_result.loaded_from_cache)
        self.assertEqual(query.call_count, 1)
        self.assertEqual(len(cache_files), 1)
        self.assertEqual(len(cache_files[0].stem), 64)
        np.testing.assert_array_equal(second_result.image_data, pixels)
        self.assertTrue(any("Loaded cached" in message for message in progress_messages))

    def test_corrupt_cache_is_discarded_and_refetched(self) -> None:
        first_pixels = np.arange(20, dtype=np.float32).reshape(4, 5) + 1.0
        replacement_pixels = first_pixels + 100.0
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_dir = Path(temporary_directory)
            request = _request(cache_dir)
            with patch.object(
                survey_images.hips2fits,
                "query_with_wcs",
                side_effect=[
                    fits.PrimaryHDU(data=first_pixels),
                    fits.PrimaryHDU(data=replacement_pixels),
                ],
            ) as query:
                retrieve_survey_image(request)
                cache_path = next((cache_dir / "sky-explorer-surveys").glob("*.npz"))
                cache_path.write_bytes(b"not a numpy archive")
                result = retrieve_survey_image(request)

        self.assertFalse(result.loaded_from_cache)
        self.assertEqual(query.call_count, 2)
        np.testing.assert_array_equal(result.image_data, replacement_pixels)

    def test_no_coverage_and_blank_responses_raise_clear_errors(self) -> None:
        responses = (
            (np.full((4, 5), np.nan), "no coverage.*finite"),
            (np.zeros((4, 5), dtype=np.float32), "blank.*zero"),
        )
        for pixels, expected_message in responses:
            with self.subTest(expected_message=expected_message):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    with patch.object(
                        survey_images.hips2fits,
                        "query_with_wcs",
                        return_value=fits.PrimaryHDU(data=pixels),
                    ):
                        with self.assertRaisesRegex(ValueError, expected_message):
                            retrieve_survey_image(_request(Path(temporary_directory)))

    def test_survey_target_rect_matches_sliced_wcs_footprint(self) -> None:
        source_wcs = _celestial_wcs()
        x0, y0, x1, y1, step = 10, 20, 20, 35, 3
        source_width = x1 - x0
        source_height = y1 - y0
        output_width = max(1, int(math.ceil(source_width / step)))
        output_height = max(1, int(math.ceil(source_height / step)))
        viewport_wcs = source_wcs.slice(
            (slice(y0, y1, step), slice(x0, x1, step)),
            numpy_order=True,
        )
        viewport_wcs.array_shape = (output_height, output_width)

        target_rect = survey_target_rect_in_source_pixels(
            source_wcs,
            viewport_wcs,
            output_width=output_width,
            output_height=output_height,
        )

        self.assertAlmostEqual(target_rect[0], 11.0, places=6)
        self.assertAlmostEqual(target_rect[1], 21.0, places=6)
        self.assertAlmostEqual(target_rect[2], 12.0, places=6)
        self.assertAlmostEqual(target_rect[3], 15.0, places=6)
        self.assertNotEqual(target_rect[2], float(source_width))

    def test_build_sky_explorer_field_wcs_centers_requested_tan_field(self) -> None:
        wcs = build_sky_explorer_field_wcs(
            ra_deg=123.45,
            dec_deg=-22.5,
            fov_arcmin=60.0,
            width_px=800,
            height_px=600,
        )

        self.assertEqual(list(wcs.wcs.ctype), ["RA---TAN", "DEC--TAN"])
        self.assertEqual(wcs.array_shape, (600, 800))
        self.assertEqual(wcs.pixel_shape, (800, 600))
        self.assertAlmostEqual(float(wcs.wcs.crval[0]), 123.45, places=6)
        self.assertAlmostEqual(float(wcs.wcs.crval[1]), -22.5, places=6)
        self.assertAlmostEqual(float(wcs.wcs.crpix[0]), 400.5, places=6)
        self.assertAlmostEqual(float(wcs.wcs.crpix[1]), 300.5, places=6)
        self.assertLess(float(wcs.wcs.cdelt[0]), 0.0)
        self.assertGreater(float(wcs.wcs.cdelt[1]), 0.0)
        self.assertAlmostEqual(abs(float(wcs.wcs.cdelt[0])), 1.0 / 800.0, places=8)

    def test_survey_field_neighbor_order_is_center_then_surrounding_ring(self) -> None:
        from photometry_app.core.survey_images import sky_explorer_survey_field_neighbor_tile_indices

        ordered = sky_explorer_survey_field_neighbor_tile_indices(0, 0, radius=1)

        self.assertEqual(ordered[0], (0, 0))
        self.assertEqual(len(ordered), 9)
        self.assertEqual(set(ordered), {(i, j) for i in (-1, 0, 1) for j in (-1, 0, 1)})

    def test_compose_survey_field_mosaic_places_east_tile_to_the_left(self) -> None:
        from photometry_app.core.survey_images import (
            SKY_EXPLORER_SURVEY_FIELD_DETAIL_PREVIEW,
            SurveyFieldTileCacheEntry,
            compose_sky_explorer_survey_field_mosaic,
            prune_sky_explorer_survey_field_tiles,
            sky_explorer_survey_field_preview_size,
            sky_explorer_survey_field_tile_indices_for_sky,
            sky_explorer_survey_field_tile_spec,
        )

        width, height = 4, 3
        center = np.full((height, width), 10.0, dtype=np.float32)
        east = np.full((height, width), 20.0, dtype=np.float32)
        tiles = {
            (0, 0): SurveyFieldTileCacheEntry(center, "refine"),
            (1, 0): SurveyFieldTileCacheEntry(east, "refine"),
        }
        mosaic, mosaic_wcs = compose_sky_explorer_survey_field_mosaic(
            tiles,
            center_i=0,
            center_j=0,
            origin_ra_deg=270.63,
            origin_dec_deg=-23.03,
            fov_arcmin=30.0,
            width_px=width,
            height_px=height,
            radius=1,
        )

        self.assertEqual(mosaic.shape, (9, 12))
        np.testing.assert_array_equal(mosaic[3:6, 4:8], center)
        np.testing.assert_array_equal(mosaic[3:6, 0:4], east)
        # Empty cells stay black/zero; stretch is frozen from the first tile by the UI.
        self.assertEqual(float(mosaic[0, 0]), 0.0)
        self.assertAlmostEqual(float(mosaic_wcs.wcs.crval[0]), 270.63, places=5)

        # Adding a neighbor must keep the prior center tile pixels.
        north = np.full((height, width), 30.0, dtype=np.float32)
        mosaic_with_north, _wcs = compose_sky_explorer_survey_field_mosaic(
            {
                **tiles,
                (0, 1): SurveyFieldTileCacheEntry(north, "refine"),
            },
            center_i=0,
            center_j=0,
            origin_ra_deg=270.63,
            origin_dec_deg=-23.03,
            fov_arcmin=30.0,
            width_px=width,
            height_px=height,
            radius=1,
        )
        np.testing.assert_array_equal(mosaic_with_north[3:6, 4:8], center)
        np.testing.assert_array_equal(mosaic_with_north[6:9, 4:8], north)

        preview = np.full((2, 2), 12.0, dtype=np.float32)
        preview_mosaic, _wcs = compose_sky_explorer_survey_field_mosaic(
            {(0, 0): SurveyFieldTileCacheEntry(preview, SKY_EXPLORER_SURVEY_FIELD_DETAIL_PREVIEW)},
            center_i=0,
            center_j=0,
            origin_ra_deg=270.63,
            origin_dec_deg=-23.03,
            fov_arcmin=30.0,
            width_px=width,
            height_px=height,
            radius=1,
        )
        self.assertEqual(preview_mosaic.shape, (9, 12))
        self.assertAlmostEqual(float(preview_mosaic[3, 4]), 12.0, places=3)

        from photometry_app.core.survey_images import sky_explorer_survey_field_frozen_levels

        black, mid, white = sky_explorer_survey_field_frozen_levels(center)
        self.assertGreaterEqual(mid, black)
        self.assertGreaterEqual(white, mid)
        self.assertFalse(
            abs(black) <= 1e-6 and abs(mid - 0.5) <= 1e-6 and abs(white - 1.0) <= 1e-6
        )

        self.assertEqual(sky_explorer_survey_field_preview_size(1024, 1024), (256, 256))
        self.assertEqual(sky_explorer_survey_field_preview_size(800, 600, max_edge=200), (200, 150))

        east_spec = sky_explorer_survey_field_tile_spec(
            origin_ra_deg=270.63,
            origin_dec_deg=-23.03,
            fov_arcmin=30.0,
            tile_i=1,
            tile_j=0,
        )
        tile_i, tile_j = sky_explorer_survey_field_tile_indices_for_sky(
            ra_deg=east_spec.ra_deg,
            dec_deg=east_spec.dec_deg,
            origin_ra_deg=270.63,
            origin_dec_deg=-23.03,
            fov_arcmin=30.0,
        )
        self.assertEqual((tile_i, tile_j), (1, 0))
        from photometry_app.core.survey_images import build_sky_explorer_field_wcs

        origin_wcs = build_sky_explorer_field_wcs(
            ra_deg=270.63, dec_deg=-23.03, fov_arcmin=30.0, width_px=width, height_px=height,
        )
        east_wcs = build_sky_explorer_field_wcs(
            ra_deg=east_spec.ra_deg, dec_deg=east_spec.dec_deg, fov_arcmin=30.0,
            width_px=width, height_px=height,
        )
        mid_y = 0.5 * float(height - 1)
        sky_left_origin = origin_wcs.pixel_to_world_values(-0.5, mid_y)
        sky_right_east = east_wcs.pixel_to_world_values(float(width) - 0.5, mid_y)
        self.assertAlmostEqual(float(sky_left_origin[0]) % 360.0, float(sky_right_east[0]) % 360.0, places=5)
        self.assertAlmostEqual(float(sky_left_origin[1]), float(sky_right_east[1]), places=5)

        pruned = prune_sky_explorer_survey_field_tiles(
            {
                **tiles,
                (5, 5): SurveyFieldTileCacheEntry(center, "preview"),
                (-4, 2): SurveyFieldTileCacheEntry(center, "refine"),
                (0, 1): SurveyFieldTileCacheEntry(center, "refine"),
            },
            center_i=0,
            center_j=0,
            max_tiles=3,
        )
        self.assertEqual(len(pruned), 3)
        self.assertIn((0, 0), pruned)
        self.assertNotIn((5, 5), pruned)

    def test_write_survey_image_fits_preserves_wcs_and_rgb_plane_order(self) -> None:
        wcs = build_sky_explorer_field_wcs(
            ra_deg=83.8221,
            dec_deg=-5.3911,
            fov_arcmin=30.0,
            width_px=5,
            height_px=4,
        )
        rgb = np.arange(60, dtype=np.float32).reshape(4, 5, 3) + 1.0

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = write_survey_image_fits(
                Path(temporary_directory) / "survey-field.fits",
                image_data=rgb,
                wcs=wcs,
            )
            with fits.open(output_path, memmap=False) as hdul:
                data = np.array(hdul[0].data, copy=True)
                loaded_wcs = WCS(hdul[0].header, naxis=2)

        self.assertEqual(data.shape, (3, 4, 5))
        np.testing.assert_array_equal(data[0], rgb[..., 0])
        np.testing.assert_array_equal(data[1], rgb[..., 1])
        np.testing.assert_array_equal(data[2], rgb[..., 2])
        np.testing.assert_allclose(loaded_wcs.wcs.crval, wcs.wcs.crval)
        np.testing.assert_allclose(loaded_wcs.wcs.crpix, wcs.wcs.crpix)
        np.testing.assert_allclose(loaded_wcs.wcs.cdelt, wcs.wcs.cdelt)

    def test_scale_wcs_for_pixel_sampling_expands_cd_matrix_subsampled_footprint(self) -> None:
        source_wcs = WCS(naxis=2)
        source_wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        source_wcs.wcs.crval = [312.0, 44.0]
        source_wcs.wcs.crpix = [3124.0, 2088.0]
        source_wcs.wcs.cd = [[-0.001, 0.0], [0.0, 0.001]]
        source_wcs.array_shape = (4176, 6248)

        x0, y0, x1, y1, step = 0, 0, 6248, 4176, 13
        source_width = x1 - x0
        source_height = y1 - y0
        output_width = max(1, int(math.ceil(source_width / step)))
        output_height = max(1, int(math.ceil(source_height / step)))
        viewport_wcs = scale_wcs_for_pixel_sampling(
            source_wcs.slice(
                (slice(y0, y1, step), slice(x0, x1, step)),
                numpy_order=True,
            ),
            step,
        )
        viewport_wcs.array_shape = (output_height, output_width)

        target_rect = survey_target_rect_in_source_pixels(
            source_wcs,
            viewport_wcs,
            output_width=output_width,
            output_height=output_height,
        )

        self.assertLess(target_rect[0], 20.0)
        self.assertLess(target_rect[1], 20.0)
        self.assertGreater(target_rect[0] + target_rect[2], float(source_width) - 20.0)
        self.assertGreater(target_rect[1] + target_rect[3], float(source_height) - 20.0)
        np.testing.assert_allclose(
            viewport_wcs.pixel_to_world_values(0.0, 0.0),
            source_wcs.pixel_to_world_values(float(x0), float(y0)),
            rtol=0.0,
            atol=0.02,
        )
        np.testing.assert_allclose(
            viewport_wcs.pixel_to_world_values(float(output_width), float(output_height)),
            source_wcs.pixel_to_world_values(float(x1), float(y1)),
            rtol=0.0,
            atol=0.02,
        )

    def test_cache_pruning_keeps_only_newest_64_cutouts(self) -> None:
        pixels = np.arange(20, dtype=np.float32).reshape(4, 5) + 1.0
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_root = Path(temporary_directory) / "sky-explorer-surveys"
            cache_root.mkdir()
            old_files: list[Path] = []
            for index in range(66):
                cache_path = cache_root / f"{index:064x}.npz"
                cache_path.write_bytes(b"old")
                os.utime(cache_path, (1_000 + index, 1_000 + index))
                old_files.append(cache_path)

            with patch.object(
                survey_images.hips2fits,
                "query_with_wcs",
                return_value=fits.PrimaryHDU(data=pixels),
            ):
                retrieve_survey_image(_request(Path(temporary_directory)))

            remaining_files = list(cache_root.glob("*.npz"))
            oldest_files_removed = [not cache_path.exists() for cache_path in old_files[:3]]

        self.assertEqual(len(remaining_files), 64)
        self.assertEqual(oldest_files_removed, [True, True, True])

    def test_clear_sky_explorer_survey_image_caches_removes_both_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_dir = Path(temporary_directory)
            surveys, fields = survey_images.sky_explorer_survey_image_cache_dirs(cache_dir)
            surveys.mkdir()
            fields.mkdir()
            (surveys / "a.npz").write_bytes(b"survey")
            (fields / "b.fits").write_bytes(b"field")
            removed = survey_images.clear_sky_explorer_survey_image_caches(cache_dir)
            self.assertEqual(removed, 2)
            self.assertFalse(surveys.exists())
            self.assertFalse(fields.exists())


if __name__ == "__main__":
    unittest.main()
