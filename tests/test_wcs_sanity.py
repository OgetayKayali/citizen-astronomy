from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from astropy.io import fits
from astropy.io.fits import Header
from astropy.wcs import WCS

from photometry_app.core.local_wcs import _DetectedSource
from photometry_app.core.models import CatalogStar, SolvedField
from photometry_app.core.wcs import celestial_wcs
from photometry_app.core.wcs_sanity import (
    EmbeddedWcsPolicy,
    WcsSanityCheckResult,
    WcsSanityOptions,
    apply_embedded_wcs_policy,
    diagnose_embedded_wcs_policy,
    evaluate_ccvals_keyword_sanity,
    evaluate_wcs_sanity,
    magnitude_bins_for_options,
    options_from_settings,
    parse_ccvals_center,
    resolve_embedded_wcs_with_sanity,
    select_gaia_sanity_candidates,
    try_repair_crval_from_ccvals,
    _adaptive_probe_fractions,
    _select_quality_detections,
)


def _tan_wcs(width: int = 300, height: int = 300, crval=(180.0, 10.0), scale_arcsec: float = 1.0) -> WCS:
    header = Header()
    header["CTYPE1"] = "RA---TAN"
    header["CTYPE2"] = "DEC--TAN"
    header["CUNIT1"] = "deg"
    header["CUNIT2"] = "deg"
    header["CRVAL1"] = crval[0]
    header["CRVAL2"] = crval[1]
    header["CRPIX1"] = (width + 1) / 2.0
    header["CRPIX2"] = (height + 1) / 2.0
    header["CD1_1"] = -scale_arcsec / 3600.0
    header["CD1_2"] = 0.0
    header["CD2_1"] = 0.0
    header["CD2_2"] = scale_arcsec / 3600.0
    return celestial_wcs(header)


def _catalog_star(source_id: str, ra_deg: float, dec_deg: float, magnitude: float) -> CatalogStar:
    return CatalogStar(
        catalog="gaia-dr3",
        source_id=source_id,
        name=source_id,
        ra_deg=ra_deg,
        dec_deg=dec_deg,
        magnitude=magnitude,
        is_variable=False,
    )


class WcsSanityTest(unittest.TestCase):
    def test_parse_ccvals_center(self) -> None:
        header = Header()
        header["CCVALS1"] = "16:31:20.93"
        header["CCVALS2"] = "+11:59:40.78"
        center = parse_ccvals_center(header)
        self.assertIsNotNone(center)
        assert center is not None
        self.assertAlmostEqual(center.ra.degree, 247.8372083, places=4)
        self.assertAlmostEqual(center.dec.degree, 11.9946611, places=4)

    def test_ccvals_keyword_sanity_detects_disagreement(self) -> None:
        header = _tan_wcs(scale_arcsec=1.0).to_header(relax=True)
        header["CCVALS1"] = "16:31:20.93"
        header["CCVALS2"] = "+11:59:40.78"
        header["CRVAL1"] = 247.81776565
        header["CRVAL2"] = 12.03140246
        result = evaluate_ccvals_keyword_sanity(header)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.passed)
        self.assertGreater(result.coherent_shift_arcsec or 0.0, 100.0)
        self.assertGreater(result.coherent_shift_pixels or 0.0, 100.0)

    def test_magnitude_bins_include_sequential_ranges(self) -> None:
        bins = magnitude_bins_for_options(WcsSanityOptions(gaia_min_magnitude=5.0, gaia_max_magnitude=14.0))
        self.assertEqual(bins, [(5.0, 10.0), (10.0, 12.0), (12.0, 14.0)])

    def test_magnitude_bins_respect_custom_min_magnitude(self) -> None:
        bins = magnitude_bins_for_options(WcsSanityOptions(gaia_min_magnitude=8.0, gaia_max_magnitude=14.0))
        self.assertEqual(bins, [(8.0, 13.0), (13.0, 14.0)])

    def test_select_gaia_sanity_candidates_uses_first_nonempty_bin(self) -> None:
        wcs = _tan_wcs()
        stars = []
        for index in range(6):
            world = wcs.pixel_to_world(120 + index * 12, 150)
            stars.append(_catalog_star(f"mid-{index}", float(world.ra.deg), float(world.dec.deg), 11.0 + index * 0.1))
        edge = wcs.pixel_to_world(5, 5)
        stars.append(_catalog_star("edge", float(edge.ra.deg), float(edge.dec.deg), 8.0))
        selected = select_gaia_sanity_candidates(
            stars,
            wcs,
            width=300,
            height=300,
            options=WcsSanityOptions(frame_margin_percent=25.0),
        )
        self.assertTrue(selected)
        self.assertTrue(all(item.source_id.startswith("mid-") for item in selected))

    def test_evaluate_wcs_sanity_passes_bright_bin_and_logs(self) -> None:
        width = height = 400
        wcs = _tan_wcs(width, height, scale_arcsec=1.2)
        header = wcs.to_header(relax=True)
        detected = []
        gaia = []
        for index in range(10):
            x_value = 100 + index * 18
            y_value = 120 + (index % 4) * 40
            world = wcs.pixel_to_world(x_value, y_value)
            detected.append(_DetectedSource(x=x_value + 0.2, y=y_value - 0.1, peak=1000 - index))
            gaia.append(
                _catalog_star(
                    f"g-{index}",
                    float(world.ra.deg),
                    float(world.dec.deg),
                    7.0 + index * 0.2,
                )
            )
        solved_field = SolvedField(
            center_ra_deg=180.0,
            center_dec_deg=10.0,
            radius_deg=0.2,
            width=width,
            height=height,
            wcs_path=Path("dummy.fits"),
        )
        progress: list[str] = []
        with (
            patch("photometry_app.core.wcs_sanity._detect_image_sources", return_value=detected),
            patch("photometry_app.core.wcs_sanity.CatalogService") as catalog_cls,
        ):
            catalog_cls.return_value.query_gaia_stars_limited.return_value = gaia
            result = evaluate_wcs_sanity(
                Path("dummy.fits"),
                header,
                solved_field,
                options=WcsSanityOptions(
                    approval_percent=90.0,
                    frame_margin_percent=25.0,
                    max_median_residual_pixels=2.0,
                    isolation_fwhm_multiplier=0.0,
                ),
                cache_dir=Path("."),
                progress_callback=progress.append,
            )
        self.assertTrue(result.passed)
        self.assertIsNone(result.approved_bin)
        self.assertTrue(any("quality sample" in line and "approved" in line for line in progress))
        self.assertTrue(any("detection→Gaia" in line for line in progress))
        self.assertTrue(any("central 75%" in line for line in progress))

    def test_evaluate_wcs_sanity_passes_when_gaia_outnumbers_detections(self) -> None:
        """Correct WCS must pass even if a dense Gaia bin dwarfs the detection sample."""
        width = height = 400
        wcs = _tan_wcs(width, height, scale_arcsec=1.2)
        header = wcs.to_header(relax=True)
        detected = []
        gaia = []
        for index in range(20):
            x_value = 90 + (index % 5) * 40
            y_value = 100 + (index // 5) * 40
            world = wcs.pixel_to_world(x_value, y_value)
            detected.append(_DetectedSource(x=float(x_value), y=float(y_value), peak=3000 - index))
            gaia.append(
                _catalog_star(
                    f"det-{index}",
                    float(world.ra.deg),
                    float(world.dec.deg),
                    11.0 + index * 0.02,
                )
            )
        # Extra Gaia stars with no corresponding detection must not tank approval.
        for index in range(200):
            x_value = 95 + (index % 10) * 18
            y_value = 105 + (index // 10) * 12
            world = wcs.pixel_to_world(x_value, y_value)
            gaia.append(
                _catalog_star(
                    f"extra-{index}",
                    float(world.ra.deg),
                    float(world.dec.deg),
                    12.0 + (index % 20) * 0.05,
                )
            )
        solved_field = SolvedField(
            center_ra_deg=180.0,
            center_dec_deg=10.0,
            radius_deg=0.2,
            width=width,
            height=height,
            wcs_path=Path("dummy.fits"),
        )
        with (
            patch("photometry_app.core.wcs_sanity._detect_image_sources", return_value=detected),
            patch("photometry_app.core.wcs_sanity.CatalogService") as catalog_cls,
        ):
            catalog_cls.return_value.query_gaia_stars_limited.return_value = gaia
            result = evaluate_wcs_sanity(
                Path("dummy.fits"),
                header,
                solved_field,
                options=WcsSanityOptions(
                    approval_percent=90.0,
                    frame_margin_percent=25.0,
                    isolation_fwhm_multiplier=0.0,
                    quality_sample_max_count=20,
                    soft_accept_enabled=False,
                ),
                cache_dir=Path("."),
            )
        self.assertTrue(result.passed)
        self.assertGreaterEqual(result.match_count, 8)
        self.assertGreaterEqual(result.candidate_count, 8)

    def test_evaluate_wcs_sanity_soft_accepts_stable_moderate_match(self) -> None:
        width = height = 400
        true_wcs = _tan_wcs(width, height, crval=(180.0, 10.0), scale_arcsec=1.2)
        # ~3.6" coherent shift: usable WCS, but hard 90% can miss with a mixed sample.
        shifted_wcs = _tan_wcs(width, height, crval=(180.001, 10.0), scale_arcsec=1.2)
        header = shifted_wcs.to_header(relax=True)
        detected = []
        gaia = []
        for index in range(40):
            x_value = 90 + (index % 8) * 28
            y_value = 100 + (index // 8) * 35
            world = true_wcs.pixel_to_world(x_value, y_value)
            # First 12 detections are intentionally offset (saturated-like bad centroids).
            if index < 12:
                detected.append(
                    _DetectedSource(x=float(x_value + 8.0), y=float(y_value - 6.0), peak=5000 - index)
                )
            else:
                detected.append(_DetectedSource(x=float(x_value), y=float(y_value), peak=5000 - index))
            gaia.append(
                _catalog_star(
                    f"g-{index}",
                    float(world.ra.deg),
                    float(world.dec.deg),
                    10.5 + index * 0.02,
                )
            )
        solved_field = SolvedField(
            center_ra_deg=180.001,
            center_dec_deg=10.0,
            radius_deg=0.2,
            width=width,
            height=height,
            wcs_path=Path("dummy.fits"),
        )
        progress: list[str] = []
        with (
            patch("photometry_app.core.wcs_sanity._detect_image_sources", return_value=detected),
            patch("photometry_app.core.wcs_sanity.CatalogService") as catalog_cls,
        ):
            catalog_cls.return_value.query_gaia_stars_limited.return_value = gaia
            result = evaluate_wcs_sanity(
                Path("dummy.fits"),
                header,
                solved_field,
                options=WcsSanityOptions(
                    approval_percent=99.0,
                    match_tolerance_pixels=7.0,
                    quality_sample_max_count=40,
                    subtract_coherent_shift=True,
                    soft_accept_enabled=True,
                    soft_approval_percent=65.0,
                    soft_max_median_residual_pixels=4.0,
                    soft_max_coherent_shift_pixels=4.0,
                    isolation_fwhm_multiplier=0.0,
                    frame_margin_percent=25.0,
                ),
                cache_dir=Path("."),
                progress_callback=progress.append,
            )
        self.assertTrue(result.passed)
        self.assertTrue(any("soft-accepted" in line or "via soft accept" in line for line in progress))

    def test_evaluate_wcs_sanity_matches_quality_sample_across_gaia_magnitudes(self) -> None:
        width = height = 400
        wcs = _tan_wcs(width, height, scale_arcsec=1.2)
        header = wcs.to_header(relax=True)
        detected = []
        gaia = []
        # Bright bin stars are shifted off detections; mid-bin stars match.
        for index in range(8):
            x_value = 110 + index * 20
            y_value = 140 + (index % 3) * 45
            world = wcs.pixel_to_world(x_value, y_value)
            detected.append(_DetectedSource(x=float(x_value), y=float(y_value), peak=2000 - index))
            gaia.append(
                _catalog_star(
                    f"bright-{index}",
                    float(world.ra.deg) + 0.01,
                    float(world.dec.deg) + 0.01,
                    8.0 + index * 0.1,
                )
            )
            gaia.append(
                _catalog_star(
                    f"mid-{index}",
                    float(world.ra.deg),
                    float(world.dec.deg),
                    11.0 + index * 0.05,
                )
            )
        solved_field = SolvedField(
            center_ra_deg=180.0,
            center_dec_deg=10.0,
            radius_deg=0.2,
            width=width,
            height=height,
            wcs_path=Path("dummy.fits"),
        )
        progress: list[str] = []
        with (
            patch("photometry_app.core.wcs_sanity._detect_image_sources", return_value=detected),
            patch("photometry_app.core.wcs_sanity.CatalogService") as catalog_cls,
        ):
            catalog_cls.return_value.query_gaia_stars_limited.return_value = gaia
            result = evaluate_wcs_sanity(
                Path("dummy.fits"),
                header,
                solved_field,
                options=WcsSanityOptions(approval_percent=90.0, frame_margin_percent=25.0),
                cache_dir=Path("."),
                progress_callback=progress.append,
            )
        self.assertTrue(result.passed)
        self.assertIsNone(result.approved_bin)
        self.assertTrue(any("quality sample" in line and "approved" in line for line in progress))

    def test_evaluate_wcs_sanity_fails_on_shifted_wcs(self) -> None:
        width = height = 400
        true_wcs = _tan_wcs(width, height, crval=(180.0, 10.0), scale_arcsec=1.2)
        bad_wcs = _tan_wcs(width, height, crval=(180.003, 10.002), scale_arcsec=1.2)
        header = bad_wcs.to_header(relax=True)
        detected = []
        gaia = []
        for index in range(12):
            x_value = 100 + index * 18
            y_value = 120 + (index % 3) * 50
            world = true_wcs.pixel_to_world(x_value, y_value)
            detected.append(_DetectedSource(x=float(x_value), y=float(y_value), peak=2000 - index))
            gaia.append(
                _catalog_star(
                    f"g-{index}",
                    float(world.ra.deg),
                    float(world.dec.deg),
                    11.0 + index * 0.05,
                )
            )
        solved_field = SolvedField(
            center_ra_deg=180.003,
            center_dec_deg=10.002,
            radius_deg=0.2,
            width=width,
            height=height,
            wcs_path=Path("dummy.fits"),
        )
        with (
            patch("photometry_app.core.wcs_sanity._detect_image_sources", return_value=detected),
            patch("photometry_app.core.wcs_sanity.CatalogService") as catalog_cls,
        ):
            catalog_cls.return_value.query_gaia_stars_limited.return_value = gaia
            result = evaluate_wcs_sanity(
                Path("dummy.fits"),
                header,
                solved_field,
                options=WcsSanityOptions(approval_percent=90.0, max_median_residual_pixels=2.0),
                cache_dir=Path("."),
            )
        self.assertFalse(result.passed)

    def test_ccvals_repair_rewrites_crval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            source_path = cache_dir / "frame.fits"
            header = _tan_wcs(crval=(247.81776565, 12.03140246)).to_header(relax=True)
            header["CCVALS1"] = "16:31:20.93"
            header["CCVALS2"] = "+11:59:40.78"
            fits.PrimaryHDU(np.zeros((64, 64), dtype=np.float32), header=header).writeto(source_path)

            with fits.open(source_path) as hdul:
                source_header = hdul[0].header.copy()
            repaired_field, reasons = try_repair_crval_from_ccvals(
                source_path,
                source_header,
                64,
                64,
                cache_dir=cache_dir,
            )
            self.assertIsNotNone(repaired_field)
            assert repaired_field is not None
            self.assertTrue(any("Repaired embedded WCS" in reason for reason in reasons))
            with fits.open(repaired_field.wcs_path) as hdul:
                repaired_header = hdul[0].header
            self.assertAlmostEqual(float(repaired_header["CRVAL1"]), 247.8372083, places=4)
            self.assertAlmostEqual(float(repaired_header["CRVAL2"]), 11.9946611, places=4)

    def test_ccvals_repair_writes_sidecar_despite_nonstandard_float_cards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            source_path = cache_dir / "nina_style.fits"
            header = Header()
            header["SIMPLE"] = True
            header["BITPIX"] = -32
            header["NAXIS"] = 2
            header["NAXIS1"] = 64
            header["NAXIS2"] = 64
            header["CTYPE1"] = "RA---TAN"
            header["CTYPE2"] = "DEC--TAN"
            header["CRVAL1"] = 247.81776565
            header["CRVAL2"] = 12.03140246
            header["CRPIX1"] = 32.5
            header["CRPIX2"] = 32.5
            # Long scientific notation + comments like NINA/WCSAutoSolver output.
            header.append(("CD1_1", -2.2045912988e-05, "WCS rotation and scaling matrix"), end=True)
            header.append(("CD1_2", 0.0, "WCS rotation and scaling matrix"), end=True)
            header.append(("CD2_1", 0.0, "WCS rotation and scaling matrix"), end=True)
            header.append(("CD2_2", -2.2065837041e-05, "WCS rotation and scaling matrix"), end=True)
            header.append(("CVALRM", 9.3477174372551e-09, "Mean of WCS residuals (arcsec)"), end=True)
            header["CCVALS1"] = "16:31:20.93"
            header["CCVALS2"] = "+11:59:40.78"
            fits.PrimaryHDU(np.zeros((64, 64), dtype=np.float32), header=header).writeto(
                source_path,
                overwrite=True,
                output_verify="silentfix",
            )
            with fits.open(source_path) as hdul:
                source_header = hdul[0].header.copy()
            repaired_field, reasons = try_repair_crval_from_ccvals(
                source_path,
                source_header,
                64,
                64,
                cache_dir=cache_dir,
            )
            self.assertIsNotNone(repaired_field, msg=str(reasons))
            assert repaired_field is not None
            self.assertTrue(repaired_field.wcs_path.exists())
            with fits.open(repaired_field.wcs_path) as hdul:
                repaired_header = hdul[0].header
            self.assertAlmostEqual(float(repaired_header["CRVAL1"]), 247.8372083, places=4)
            self.assertEqual(str(repaired_header.get("WCSMETH")), "CCVALS-REPAIR")

    def test_resolve_embedded_accepts_when_sanity_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            source_path = cache_dir / "frame.fits"
            header = _tan_wcs().to_header(relax=True)
            fits.PrimaryHDU(np.zeros((64, 64), dtype=np.float32), header=header).writeto(source_path)
            with fits.open(source_path) as hdul:
                source_header = hdul[0].header.copy()
            resolution = resolve_embedded_wcs_with_sanity(
                source_path,
                source_header,
                64,
                64,
                cache_dir=cache_dir,
                options=WcsSanityOptions(enabled=False),
            )
            self.assertTrue(resolution.accepted)
            self.assertIsNotNone(resolution.solved_field)

    def test_diagnose_policy_accepts_when_sanity_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            source_path = cache_dir / "frame.fits"
            header = _tan_wcs().to_header(relax=True)
            fits.PrimaryHDU(np.zeros((64, 64), dtype=np.float32), header=header).writeto(source_path)
            with fits.open(source_path) as hdul:
                source_header = hdul[0].header.copy()
            with patch(
                "photometry_app.core.wcs_sanity.evaluate_wcs_sanity",
                return_value=WcsSanityCheckResult(
                    passed=True,
                    status="passed",
                    reasons=["ok"],
                    match_count=5,
                    candidate_count=5,
                    median_residual_arcsec=0.5,
                    approved_bin=(5.0, 10.0),
                ),
            ):
                policy = diagnose_embedded_wcs_policy(
                    source_path,
                    source_header,
                    64,
                    64,
                    cache_dir=cache_dir,
                    options=WcsSanityOptions(),
                )
            self.assertEqual(policy.mode, "accept")

    def test_apply_policy_accepts_without_gaia(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            source_path = cache_dir / "frame.fits"
            header = _tan_wcs().to_header(relax=True)
            fits.PrimaryHDU(np.zeros((64, 64), dtype=np.float32), header=header).writeto(source_path)
            with fits.open(source_path) as hdul:
                source_header = hdul[0].header.copy()
            with patch("photometry_app.core.wcs_sanity.evaluate_wcs_sanity") as sanity_mock:
                resolution = apply_embedded_wcs_policy(
                    source_path,
                    source_header,
                    64,
                    64,
                    policy=EmbeddedWcsPolicy(mode="accept", reasons=["probe ok"]),
                    cache_dir=cache_dir,
                )
            sanity_mock.assert_not_called()
            self.assertTrue(resolution.accepted)
            self.assertIsNotNone(resolution.solved_field)

    def test_options_from_settings_reads_approval_and_margin(self) -> None:
        class _Settings:
            wcs_sanity_check_enabled = True
            wcs_sanity_candidate_count = 4
            wcs_sanity_min_matches = 20
            wcs_sanity_approval_percent = 90.0
            wcs_sanity_probe_start_percent = 12.0
            wcs_sanity_quality_sample_max_count = 28
            wcs_sanity_minimum_source_snr = 9.0
            wcs_sanity_max_median_residual_pixels = 1.75
            wcs_sanity_match_tolerance_pixels = 2.5
            wcs_sanity_isolation_fwhm_multiplier = 3.0
            wcs_sanity_soft_max_median_residual_pixels = 1.25
            wcs_sanity_soft_max_coherent_shift_pixels = 1.75
            wcs_sanity_ccvals_max_disagreement_pixels = 4.0
            wcs_sanity_gaia_min_magnitude = 14.0
            wcs_sanity_gaia_max_magnitude = 10.0
            wcs_sanity_edge_margin_percent = 25.0
            wcs_sanity_ccvals_repair_enabled = True

        options = options_from_settings(_Settings())
        self.assertEqual(options.approval_percent, 90.0)
        self.assertEqual(options.frame_margin_percent, 25.0)
        self.assertEqual(options.probe_start_fraction, 0.12)
        self.assertEqual(options.match_tolerance_pixels, 2.5)
        self.assertEqual(options.isolation_fwhm_multiplier, 3.0)
        self.assertEqual(options.ccvals_max_disagreement_pixels, 4.0)
        self.assertEqual(options.gaia_min_magnitude, 10.0)
        self.assertEqual(options.gaia_max_magnitude, 14.0)

    def test_adaptive_probe_fractions_start_relative_and_expand_to_full_field(self) -> None:
        fractions = _adaptive_probe_fractions(0.10)
        self.assertEqual(fractions[0], 0.10)
        self.assertEqual(fractions[-1], 1.0)
        self.assertTrue(all(left < right for left, right in zip(fractions, fractions[1:])))

    def test_quality_selection_avoids_clipped_and_low_snr_sources(self) -> None:
        sources = [
            _DetectedSource(float(index * 10), 50.0, 60000.0, snr=100.0, fwhm_px=3.0)
            for index in range(8)
        ]
        sources.extend(
            _DetectedSource(float(index * 10), 100.0, 5000.0 + index * 100.0, snr=30.0, fwhm_px=3.0)
            for index in range(12)
        )
        sources.extend(
            _DetectedSource(float(index * 10), 150.0, 500.0 + index, snr=2.0, fwhm_px=3.0)
            for index in range(8)
        )
        selected = _select_quality_detections(
            sources,
            width=300,
            height=200,
            minimum_snr=8.0,
            max_count=16,
        )
        self.assertGreaterEqual(len(selected), 8)
        self.assertTrue(all(source.peak < 60000.0 for source in selected))
        self.assertTrue(all((source.snr or 0.0) >= 8.0 for source in selected))

    def test_evaluate_wcs_sanity_queries_central_cone_on_wide_field(self) -> None:
        width, height = 2000, 1500
        wcs = _tan_wcs(width, height, scale_arcsec=3.0)
        header = wcs.to_header(relax=True)
        detected = []
        gaia = []
        for index in range(10):
            x_value = 1000 + (index % 5) * 12
            y_value = 750 + (index // 5) * 14
            world = wcs.pixel_to_world(x_value, y_value)
            detected.append(_DetectedSource(x=float(x_value), y=float(y_value), peak=1000 - index))
            gaia.append(
                _catalog_star(
                    f"center-{index}",
                    float(world.ra.deg),
                    float(world.dec.deg),
                    8.0 + index * 0.1,
                )
            )
        # Bright stars far from the center must not force a full-field Gaia dump or tank the score.
        for index in range(8):
            detected.append(_DetectedSource(x=300.0 + index, y=200.0, peak=5000 - index))
        solved_field = SolvedField(
            center_ra_deg=180.0,
            center_dec_deg=10.0,
            radius_deg=1.05,
            width=width,
            height=height,
            wcs_path=Path("dummy.fits"),
        )
        progress: list[str] = []
        with (
            patch("photometry_app.core.wcs_sanity._detect_image_sources", return_value=detected),
            patch("photometry_app.core.wcs_sanity.CatalogService") as catalog_cls,
        ):
            catalog_cls.return_value.query_gaia_stars_limited.return_value = gaia
            result = evaluate_wcs_sanity(
                Path("dummy.fits"),
                header,
                solved_field,
                options=WcsSanityOptions(
                    approval_percent=90.0,
                    frame_margin_percent=25.0,
                    probe_start_fraction=0.10,
                    quality_sample_max_count=20,
                    isolation_fwhm_multiplier=0.0,
                ),
                cache_dir=Path("."),
                progress_callback=progress.append,
            )
        self.assertTrue(result.passed)
        query = catalog_cls.return_value.query_gaia_stars_limited
        query.assert_called_once()
        queried_field = query.call_args.args[0]
        self.assertAlmostEqual(queried_field.radius_deg, solved_field.radius_deg * 0.10)
        self.assertEqual(query.call_args.kwargs["progress_label"], "WCS sanity probe")
        self.assertEqual(result.candidate_count, 10)
        self.assertTrue(any("10% of field radius" in line for line in progress))


if __name__ == "__main__":
    unittest.main()
