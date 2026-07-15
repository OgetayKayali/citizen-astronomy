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
    retrieve_survey_image,
    scale_wcs_for_pixel_sampling,
    survey_definition_for_key,
    survey_target_rect_in_source_pixels,
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
                ("shs_ha", "SHS Ha", "CDS/P/SHS"),
                ("panstarrs", "PanSTARRS", "CDS/P/PanSTARRS/DR1/color-i-r-g"),
                ("iphas_dr2_ha", "IPHAS DR2 Ha", "CDS/P/IPHAS/DR2/Ha"),
            ],
        )
        self.assertIs(survey_definition_for_key("SHS-HA"), SURVEY_DEFINITIONS[1])
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


if __name__ == "__main__":
    unittest.main()
