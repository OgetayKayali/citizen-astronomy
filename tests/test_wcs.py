from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import requests

from astropy.io import fits
from astropy.io.fits import Header

from photometry_app.core.local_wcs import MetadataWcsSeed, _DetectedSource, _fit_gaia_wcs, infer_metadata_wcs_seed
from photometry_app.core.models import CatalogStar, PlateSolveResult, SolvedField, WcsStatus
from photometry_app.core.wcs import AstrometryNetClient, _prepare_plate_solve_input, celestial_wcs, scale_wcs_pixel_grid, validate_wcs


class AstrometryNetClientTest(unittest.TestCase):
    def test_infer_metadata_wcs_seed_uses_pointing_and_optical_scale(self) -> None:
        header = Header()
        header["RA"] = 60.3992502709086
        header["DEC"] = 36.1313818033603
        header["FOCALLEN"] = 457.66134
        header["XPIXSZ"] = (3.76, "Pixel size including binning, X-axis (um)")
        header["YPIXSZ"] = (3.76, "Pixel size including binning, Y-axis (um)")
        header["XBINNING"] = 2
        header["YBINNING"] = 2

        seed = infer_metadata_wcs_seed(header, 6248, 4176)

        self.assertIsNotNone(seed)
        assert seed is not None
        self.assertAlmostEqual(seed.center_ra_deg, 60.3992502709086)
        self.assertAlmostEqual(seed.center_dec_deg, 36.1313818033603)
        self.assertAlmostEqual(seed.pixel_scale_x_arcsec, 1.6946, places=3)
        self.assertAlmostEqual(seed.pixel_scale_y_arcsec, 1.6946, places=3)
        self.assertEqual((seed.width, seed.height), (6248, 4176))

    def test_infer_metadata_wcs_seed_does_not_double_apply_nina_binning(self) -> None:
        header = Header()
        header["RA"] = 97.6344880776984
        header["DEC"] = 29.6913781086925
        header["FOCALLEN"] = 2939.0
        header["XPIXSZ"] = (7.52, "[um] Pixel X axis size")
        header["YPIXSZ"] = (7.52, "[um] Pixel Y axis size")
        header["XBINNING"] = 2
        header["YBINNING"] = 2
        header["SWCREATE"] = "N.I.N.A. 3.2.0.9001 (x64)"

        seed = infer_metadata_wcs_seed(header, 4784, 3194)

        self.assertIsNotNone(seed)
        assert seed is not None
        expected_scale = 206.26480624709636 * 7.52 / 2939.0
        self.assertAlmostEqual(seed.pixel_scale_x_arcsec, expected_scale, places=4)
        self.assertAlmostEqual(seed.pixel_scale_y_arcsec, expected_scale, places=4)
        self.assertAlmostEqual(seed.field_radius_deg, 0.5416, places=3)

    def test_metadata_seeded_gaia_fit_recovers_rotation_and_parity(self) -> None:
        rng = np.random.default_rng(12345)
        width = 2000
        height = 1500
        scale_arcsec = 1.7
        rotation_rad = np.deg2rad(37.0)
        cosine = float(np.cos(rotation_rad))
        sine = float(np.sin(rotation_rad))

        true_wcs = celestial_wcs(Header())
        true_wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        true_wcs.wcs.cunit = ["deg", "deg"]
        true_wcs.wcs.crval = [60.0, 36.0]
        true_wcs.wcs.crpix = [(width + 1) / 2.0, (height + 1) / 2.0]
        rotation = np.asarray([[cosine, -sine], [sine, cosine]], dtype=float)
        true_wcs.wcs.cd = (scale_arcsec / 3600.0) * rotation @ np.diag([-1.0, 1.0])

        matched_pixels = np.column_stack(
            (
                rng.uniform(100.0, width - 100.0, 36),
                rng.uniform(100.0, height - 100.0, 36),
            )
        )
        world = true_wcs.pixel_to_world(matched_pixels[:, 0], matched_pixels[:, 1])
        gaia_stars = [
            CatalogStar(
                catalog="gaia-dr3",
                source_id=f"gaia-{index}",
                name=f"gaia-{index}",
                ra_deg=float(world[index].ra.deg),
                dec_deg=float(world[index].dec.deg),
                magnitude=8.0 + index * 0.1,
                is_variable=False,
            )
            for index in range(len(matched_pixels))
        ]
        detected_sources = [
            _DetectedSource(
                x=float(pixel[0] + rng.normal(0.0, 0.08)),
                y=float(pixel[1] + rng.normal(0.0, 0.08)),
                peak=float(50000 - index * 500),
            )
            for index, pixel in enumerate(matched_pixels)
        ]
        detected_sources.extend(
            _DetectedSource(
                x=float(rng.uniform(0.0, width)),
                y=float(rng.uniform(0.0, height)),
                peak=float(5000 - index),
            )
            for index in range(12)
        )
        seed = MetadataWcsSeed(
            center_ra_deg=60.03,
            center_dec_deg=35.98,
            pixel_scale_x_arcsec=scale_arcsec,
            pixel_scale_y_arcsec=scale_arcsec,
            width=width,
            height=height,
        )

        fitted = _fit_gaia_wcs(detected_sources, gaia_stars, seed)

        self.assertIsNotNone(fitted)
        assert fitted is not None
        fitted_wcs, match_count, rms_pixels = fitted
        fitted_x, fitted_y = fitted_wcs.world_to_pixel(world)
        fit_error = np.hypot(fitted_x - matched_pixels[:, 0], fitted_y - matched_pixels[:, 1])
        self.assertGreaterEqual(match_count, 30)
        self.assertLess(rms_pixels, 0.3)
        self.assertLess(float(np.median(fit_error)), 0.3)

    def test_metadata_seeded_gaia_fit_rejects_unrelated_star_fields(self) -> None:
        rng = np.random.default_rng(9876)
        width = 1600
        height = 1200
        detected_sources = [
            _DetectedSource(
                x=float(rng.uniform(0.0, width)),
                y=float(rng.uniform(0.0, height)),
                peak=float(50000 - index * 100),
            )
            for index in range(80)
        ]
        gaia_stars = [
            CatalogStar(
                catalog="gaia-dr3",
                source_id=f"unrelated-{index}",
                name=f"unrelated-{index}",
                ra_deg=float(60.0 + rng.uniform(-0.35, 0.35)),
                dec_deg=float(36.0 + rng.uniform(-0.35, 0.35)),
                magnitude=8.0 + index * 0.05,
                is_variable=False,
            )
            for index in range(100)
        ]
        seed = MetadataWcsSeed(
            center_ra_deg=60.0,
            center_dec_deg=36.0,
            pixel_scale_x_arcsec=1.7,
            pixel_scale_y_arcsec=1.7,
            width=width,
            height=height,
        )

        fitted = _fit_gaia_wcs(detected_sources, gaia_stars, seed)

        self.assertIsNone(fitted)

    def test_scale_wcs_pixel_grid_doubles_image_coordinates(self) -> None:
        header = Header()
        header["NAXIS"] = 2
        header["NAXIS1"] = 1000
        header["NAXIS2"] = 800
        header["CTYPE1"] = "RA---TAN"
        header["CTYPE2"] = "DEC--TAN"
        header["CRVAL1"] = 10.0
        header["CRVAL2"] = 20.0
        header["CRPIX1"] = 500.0
        header["CRPIX2"] = 400.0
        header["CD1_1"] = -0.00027
        header["CD1_2"] = 0.0
        header["CD2_1"] = 0.0
        header["CD2_2"] = 0.00027

        source_wcs = celestial_wcs(header)
        target_wcs = scale_wcs_pixel_grid(
            source_wcs,
            source_width=1000,
            source_height=800,
            target_width=2000,
            target_height=1600,
        )

        ra_deg, dec_deg = 10.0, 20.0
        source_x, source_y = source_wcs.world_to_pixel_values(ra_deg, dec_deg)
        target_x, target_y = target_wcs.world_to_pixel_values(ra_deg, dec_deg)
        self.assertAlmostEqual(target_x, source_x * 2.0, places=3)
        self.assertAlmostEqual(target_y, source_y * 2.0, places=3)

    def test_validate_wcs_accepts_three_dimensional_sip_header_via_celestial_slice(self) -> None:
        header = Header()
        header["WCSAXES"] = 3
        header["NAXIS"] = 3
        header["NAXIS1"] = 1000
        header["NAXIS2"] = 800
        header["NAXIS3"] = 3
        header["CTYPE1"] = "RA---TAN-SIP"
        header["CTYPE2"] = "DEC--TAN-SIP"
        header["CTYPE3"] = "WAVE"
        header["CRVAL1"] = 10.0
        header["CRVAL2"] = 20.0
        header["CRVAL3"] = 1.0
        header["CRPIX1"] = 500.0
        header["CRPIX2"] = 400.0
        header["CRPIX3"] = 1.0
        header["CD1_1"] = -0.00027
        header["CD1_2"] = 0.0
        header["CD2_1"] = 0.0
        header["CD2_2"] = 0.00027
        header["CD3_3"] = 1.0
        header["A_ORDER"] = 2
        header["B_ORDER"] = 2
        header["A_0_2"] = 0.0
        header["A_1_1"] = 0.0
        header["A_2_0"] = 0.0
        header["B_0_2"] = 0.0
        header["B_1_1"] = 0.0
        header["B_2_0"] = 0.0

        valid, reasons = validate_wcs(header)

        self.assertTrue(valid)
        self.assertEqual(reasons, [])
        self.assertEqual(celestial_wcs(header).pixel_n_dim, 2)

    def test_prepare_plate_solve_input_keeps_jpeg_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "field.jpg"
            source_path.write_bytes(b"jpeg")

            prepared_input = _prepare_plate_solve_input(source_path, Path(temp_dir))

        self.assertEqual(prepared_input.path, source_path)

    def test_login_retries_after_transient_disconnect(self) -> None:
        client = AstrometryNetClient("demo-key")
        first_session = Mock()
        second_session = Mock()
        success_response = Mock()
        success_response.raise_for_status.return_value = None
        success_response.json.return_value = {"status": "success", "session": "token-123"}
        first_session.post = Mock(side_effect=requests.ConnectionError("Connection aborted."))
        second_session.post = Mock(return_value=success_response)
        client._session = first_session

        def _swap_session(*, reset_login: bool = False) -> None:
            self.assertTrue(reset_login)
            client._session = second_session

        with (
            patch.object(client, "_reset_session", side_effect=_swap_session) as reset_mock,
            patch("photometry_app.core.wcs.time.sleep") as sleep_mock,
        ):
            client._login()

        self.assertEqual(client._login_token, "token-123")
        self.assertEqual(first_session.post.call_count, 1)
        self.assertEqual(second_session.post.call_count, 1)
        self.assertEqual(reset_mock.call_count, 1)
        sleep_mock.assert_called_once_with(1.0)

    def test_upload_retries_with_fresh_session_after_ssl_eof(self) -> None:
        client = AstrometryNetClient("demo-key")
        client._login_token = "session-123"
        first_session = Mock()
        second_session = Mock()
        success_response = Mock()
        success_response.raise_for_status.return_value = None
        success_response.json.return_value = {"status": "success", "subid": 42}
        first_session.post = Mock(side_effect=requests.exceptions.SSLError("EOF occurred in violation of protocol"))
        second_session.post = Mock(return_value=success_response)
        client._session = first_session

        def _swap_session(*, reset_login: bool = False) -> None:
            self.assertFalse(reset_login)
            client._session = second_session

        with tempfile.TemporaryDirectory() as temp_dir:
            fits_path = Path(temp_dir) / "field.fit"
            fits_path.write_bytes(b"fits")
            with (
                patch.object(client, "_reset_session", side_effect=_swap_session) as reset_mock,
                patch("photometry_app.core.wcs.time.sleep") as sleep_mock,
            ):
                submission_id = client._upload_file(fits_path)

        self.assertEqual(submission_id, 42)
        self.assertEqual(first_session.post.call_count, 1)
        self.assertEqual(second_session.post.call_count, 1)
        self.assertEqual(reset_mock.call_count, 1)
        sleep_mock.assert_called_once_with(1.0)

    def test_download_reports_retry_exhaustion_with_operation_name(self) -> None:
        client = AstrometryNetClient("demo-key")
        client._session.get = Mock(side_effect=requests.ConnectionError("Connection aborted."))

        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "solved.fits"
            with (
                patch.object(client, "_reset_session") as reset_mock,
                patch("photometry_app.core.wcs.time.sleep") as sleep_mock,
                self.assertRaises(RuntimeError) as exc_info,
            ):
                client._download_solved_fits(12345, destination)

        self.assertIn("solved FITS download request failed after 3 attempts", str(exc_info.exception))
        self.assertEqual(client._session.get.call_count, 3)
        self.assertEqual(reset_mock.call_count, 2)
        self.assertEqual(sleep_mock.call_count, 2)

    def test_solve_file_ignores_cached_unsolved_result_and_rebuilds_solution(self) -> None:
        client = AstrometryNetClient("demo-key")

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir) / "sky-explorer-wcs"
            source_path = Path(temp_dir) / "field.jpg"
            source_path.write_bytes(b"jpeg")

            stale_result = PlateSolveResult(
                source_path=source_path,
                status=WcsStatus.UNSOLVED,
                solved_field=None,
                reasons=["Missing CTYPE1/CTYPE2 WCS keywords.", "Astropy could not parse WCS: legacy failure"],
            )

            def _write_solved_fits(_job_id: int, destination: Path) -> None:
                header = Header()
                header["CTYPE1"] = "RA---TAN"
                header["CTYPE2"] = "DEC--TAN"
                header["CRVAL1"] = 15.0
                header["CRVAL2"] = 30.0
                header["CRPIX1"] = 50.0
                header["CRPIX2"] = 40.0
                header["CD1_1"] = -0.00027
                header["CD1_2"] = 0.0
                header["CD2_1"] = 0.0
                header["CD2_2"] = 0.00027
                fits.PrimaryHDU(data=np.zeros((80, 100), dtype=np.float32), header=header).writeto(destination, overwrite=True)

            with (
                patch("photometry_app.core.wcs._load_cached_solution", return_value=stale_result),
                patch.object(client, "_login") as login_mock,
                patch.object(client, "_upload_file", return_value=123) as upload_mock,
                patch.object(client, "_wait_for_job", return_value=456) as wait_mock,
                patch.object(client, "_download_solved_fits", side_effect=_write_solved_fits) as download_mock,
            ):
                result = client.solve_file(source_path, cache_dir)

        self.assertEqual(result.status, WcsStatus.SOLVED)
        self.assertIsNotNone(result.solved_field)
        login_mock.assert_called_once_with()
        upload_mock.assert_called_once()
        wait_mock.assert_called_once_with(123, timeout_seconds=300, progress_callback=None)
        download_mock.assert_called_once()