from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from astropy.io import fits
from astropy.io.fits import Header
from PIL import Image
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel

from photometry_app.core.models import PlateSolveResult, SolvedField, WcsStatus
from photometry_app.core.settings import AppSettings
from photometry_app.core.solar_system import estimate_visible_magnitude_limit
from photometry_app.core.sky_explorer import (
    SkyExplorerCancelled,
    SkyExplorerStarlessWcsChoice,
    SkyExplorerStarlessWcsPrompt,
    SkyExplorerWcsCandidate,
    _resolve_source_field,
    explore_sky_image,
    sky_explorer_external_wcs_error,
)
from photometry_app.ui.sky_explorer_starless_wcs_dialog import (
    SkyExplorerStarlessWcsDialog,
    format_sky_explorer_wcs_candidate,
)


def _wcs_header(*, ra_deg: float = 83.822, dec_deg: float = -5.391, crpix1: float = 50.0, crpix2: float = 40.0) -> Header:
    header = Header()
    header["CTYPE1"] = "RA---TAN"
    header["CTYPE2"] = "DEC--TAN"
    header["CRVAL1"] = ra_deg
    header["CRVAL2"] = dec_deg
    header["CRPIX1"] = crpix1
    header["CRPIX2"] = crpix2
    header["CD1_1"] = -0.00028
    header["CD1_2"] = 0.0
    header["CD2_1"] = 0.0
    header["CD2_2"] = 0.00028
    return header


def _write_fits(path: Path, *, width: int = 100, height: int = 80, header: Header | None = None) -> None:
    fits.PrimaryHDU(
        data=np.zeros((height, width), dtype=np.float32),
        header=header if header is not None else Header(),
    ).writeto(path, overwrite=True)


def _write_png(path: Path, *, width: int = 100, height: int = 80) -> None:
    Image.new("L", (width, height), 0).save(path)


def _solved_result(source_path: Path) -> PlateSolveResult:
    return PlateSolveResult(
        source_path=source_path,
        status=WcsStatus.SOLVED,
        solved_field=SolvedField(
            center_ra_deg=10.0,
            center_dec_deg=20.0,
            radius_deg=0.2,
            width=100,
            height=80,
            wcs_path=source_path,
        ),
        reasons=["metadata-seeded Gaia matching"],
    )


class SkyExplorerStarlessWcsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_valid_embedded_wcs_skips_star_check_and_solvers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "starless.fits"
            _write_fits(source_path, header=_wcs_header())
            callback = Mock()
            with patch("photometry_app.core.sky_explorer.recover_wcs_racing_available_solvers") as recover:
                solved_field, used_fallback = _resolve_source_field(
                    source_path,
                    AppSettings.from_root(root),
                    starless_choice_callback=callback,
                )
            recover.assert_not_called()
            callback.assert_not_called()
            self.assertFalse(used_fallback)
            self.assertAlmostEqual(solved_field.center_ra_deg, 83.822, places=3)
            self.assertEqual(solved_field.wcs_path, source_path)

    def test_same_size_companion_fits_is_used_without_a_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "nebula.png"
            companion_path = root / "nebula.fits"
            _write_png(source_path)
            _write_fits(companion_path, header=_wcs_header(ra_deg=10.5, dec_deg=-2.25))
            progress: list[str] = []
            callback = Mock()
            with patch("photometry_app.core.sky_explorer.recover_wcs_racing_available_solvers") as recover:
                solved_field, used_fallback = _resolve_source_field(
                    source_path,
                    AppSettings.from_root(root),
                    progress_callback=progress.append,
                    starless_choice_callback=callback,
                )
            recover.assert_not_called()
            callback.assert_not_called()
            self.assertFalse(used_fallback)
            self.assertEqual(solved_field.wcs_path, companion_path)
            self.assertTrue(any("Using WCS from nebula.fits (same pixel size)." in message for message in progress))

    def test_mismatched_companion_is_named_and_plate_solve_still_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "nebula.png"
            _write_png(source_path, width=100, height=80)
            _write_fits(root / "nebula.fits", width=40, height=40, header=_wcs_header())
            prompts: list[SkyExplorerStarlessWcsPrompt] = []

            def _choose(prompt: SkyExplorerStarlessWcsPrompt) -> SkyExplorerStarlessWcsChoice:
                prompts.append(prompt)
                return SkyExplorerStarlessWcsChoice(action="plate_solve")

            with (
                patch("photometry_app.core.sky_explorer._count_plate_solve_sources", return_value=0),
                patch(
                    "photometry_app.core.sky_explorer.recover_wcs_racing_available_solvers",
                    return_value=_solved_result(source_path),
                ) as recover,
            ):
                _solved_field, used_fallback = _resolve_source_field(
                    source_path,
                    AppSettings.from_root(root),
                    starless_choice_callback=_choose,
                )
            recover.assert_called_once()
            self.assertTrue(used_fallback)
            self.assertIsNone(prompts[0].embedded_candidate)
            self.assertIn("40×40", prompts[0].companion_unavailable_note or "")
            self.assertIn("100×80", prompts[0].companion_unavailable_note or "")

    def test_staralignment_sidecar_can_be_trusted_when_the_frame_is_starless(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "aligned.fits"
            _write_fits(source_path, header=_wcs_header())
            source_path.with_suffix(".xdrz").write_text("sidecar", encoding="utf-8")
            prompts: list[SkyExplorerStarlessWcsPrompt] = []

            def _choose(prompt: SkyExplorerStarlessWcsPrompt) -> SkyExplorerStarlessWcsChoice:
                prompts.append(prompt)
                return SkyExplorerStarlessWcsChoice(action="use_embedded")

            progress: list[str] = []
            with (
                patch("photometry_app.core.sky_explorer._count_plate_solve_sources", return_value=1),
                patch("photometry_app.core.sky_explorer.recover_wcs_racing_available_solvers") as recover,
            ):
                solved_field, used_fallback = _resolve_source_field(
                    source_path,
                    AppSettings.from_root(root),
                    progress_callback=progress.append,
                    starless_choice_callback=_choose,
                )
            recover.assert_not_called()
            self.assertFalse(used_fallback)
            self.assertEqual(solved_field.wcs_path, source_path)
            self.assertAlmostEqual(solved_field.center_ra_deg, 83.822, places=3)
            self.assertIsNotNone(prompts[0].embedded_candidate)
            assert prompts[0].embedded_candidate is not None
            self.assertTrue(prompts[0].embedded_candidate.staralignment_sidecar)
            self.assertTrue(any("Star matching was skipped" in message for message in progress))

    def test_chosen_file_wcs_is_used_when_the_pixel_size_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "starless.png"
            wcs_path = root / "original.fits"
            _write_png(source_path)
            _write_fits(wcs_path, header=_wcs_header(ra_deg=12.0, dec_deg=34.0))
            with (
                patch("photometry_app.core.sky_explorer._count_plate_solve_sources", return_value=0),
                patch("photometry_app.core.sky_explorer.recover_wcs_racing_available_solvers") as recover,
            ):
                solved_field, used_fallback = _resolve_source_field(
                    source_path,
                    AppSettings.from_root(root),
                    starless_choice_callback=lambda _prompt: SkyExplorerStarlessWcsChoice(
                        action="use_file",
                        wcs_path=wcs_path,
                    ),
                )
            recover.assert_not_called()
            self.assertFalse(used_fallback)
            self.assertEqual(solved_field.wcs_path, wcs_path)
            self.assertAlmostEqual(solved_field.center_dec_deg, 34.0, places=3)

    def test_cancel_stops_before_plate_solving(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "starless.png"
            _write_png(source_path)
            with (
                patch("photometry_app.core.sky_explorer._count_plate_solve_sources", return_value=0),
                patch("photometry_app.core.sky_explorer.recover_wcs_racing_available_solvers") as recover,
                self.assertRaises(SkyExplorerCancelled),
            ):
                _resolve_source_field(
                    source_path,
                    AppSettings.from_root(root),
                    starless_choice_callback=lambda _prompt: SkyExplorerStarlessWcsChoice(action="cancel"),
                )
            recover.assert_not_called()

    def test_frames_with_stars_still_plate_solve_without_a_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "field.png"
            _write_png(source_path)
            callback = Mock()
            with (
                patch("photometry_app.core.sky_explorer._count_plate_solve_sources", return_value=12),
                patch(
                    "photometry_app.core.sky_explorer.recover_wcs_racing_available_solvers",
                    return_value=_solved_result(source_path),
                ) as recover,
            ):
                _solved_field, used_fallback = _resolve_source_field(
                    source_path,
                    AppSettings.from_root(root),
                    starless_choice_callback=callback,
                )
            callback.assert_not_called()
            recover.assert_called_once()
            self.assertTrue(used_fallback)

    def test_callers_without_a_prompt_still_plate_solve_a_vetoed_header(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "aligned.fits"
            _write_fits(source_path, header=_wcs_header())
            source_path.with_suffix(".xdrz").write_text("sidecar", encoding="utf-8")
            with patch(
                "photometry_app.core.sky_explorer.recover_wcs_racing_available_solvers",
                return_value=_solved_result(source_path),
            ) as recover:
                _solved_field, used_fallback = _resolve_source_field(source_path, AppSettings.from_root(root))
            recover.assert_called_once()
            self.assertTrue(used_fallback)

    def test_external_wcs_error_reports_a_different_pixel_size(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wcs_path = root / "other.fits"
            _write_fits(wcs_path, width=20, height=10, header=_wcs_header())
            message = sky_explorer_external_wcs_error(100, 80, wcs_path)
            self.assertIsNotNone(message)
            assert message is not None
            self.assertIn("20×10", message)
            self.assertIn("100×80", message)

    def test_visible_limit_uses_the_sky_explorer_resolver_when_a_callback_is_set(self) -> None:
        callback = Mock()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "starless.png"
            _write_png(source_path)
            with (
                patch(
                    "photometry_app.core.sky_explorer._resolve_source_field",
                    side_effect=SkyExplorerCancelled("stop"),
                ) as resolve,
                self.assertRaises(SkyExplorerCancelled),
            ):
                estimate_visible_magnitude_limit(
                    source_path,
                    settings=AppSettings.from_root(root),
                    starless_choice_callback=callback,
                )
        self.assertIs(resolve.call_args.kwargs["starless_choice_callback"], callback)

    def test_visible_limit_keeps_its_own_solver_without_a_starless_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "field.fits"
            _write_fits(source_path, header=_wcs_header())

            class _Sentinel(Exception):
                pass

            with (
                patch("photometry_app.core.sky_explorer._resolve_source_field") as sky_resolve,
                patch(
                    "photometry_app.core.solar_system._resolve_source_field",
                    side_effect=_Sentinel("local"),
                ),
                self.assertRaises(_Sentinel),
            ):
                estimate_visible_magnitude_limit(source_path, settings=AppSettings.from_root(root))
        sky_resolve.assert_not_called()

    def test_explore_sky_image_forwards_the_starless_callback(self) -> None:
        callback = Mock()
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch(
                    "photometry_app.core.sky_explorer._resolve_source_field",
                    side_effect=SkyExplorerCancelled("stop"),
                ) as resolve,
                self.assertRaises(SkyExplorerCancelled),
            ):
                explore_sky_image(
                    Path(temp_dir) / "frame.fits",
                    settings=AppSettings.from_root(Path(temp_dir)),
                    starless_choice_callback=callback,
                )
        self.assertIs(resolve.call_args.kwargs["starless_choice_callback"], callback)

    def test_dialog_offers_the_embedded_solution_and_names_a_sidecar(self) -> None:
        candidate = SkyExplorerWcsCandidate(
            center_ra_deg=83.822,
            center_dec_deg=-5.391,
            radius_deg=0.2,
            width_arcmin=1.7,
            height_arcmin=1.3,
            staralignment_sidecar=True,
        )
        prompt = SkyExplorerStarlessWcsPrompt(
            image_path=Path("aligned.fits"),
            image_width=100,
            image_height=80,
            embedded_candidate=candidate,
            companion_unavailable_note=None,
        )
        dialog = SkyExplorerStarlessWcsDialog(prompt)
        self.assertEqual(dialog.windowTitle(), "This image looks starless")
        self.assertTrue(dialog._use_embedded_button.isEnabled())
        self.assertTrue(dialog._use_embedded_button.isDefault())
        label_text = " ".join(label.text() for label in dialog.findChildren(QLabel))
        self.assertIn("83.8220°", label_text)
        self.assertIn("StarAlignment sidecar", label_text)
        self.assertIn("1.7 × 1.3 arcmin", format_sky_explorer_wcs_candidate(candidate))
        dialog._accept_embedded()
        self.assertEqual(dialog.choice().action, "use_embedded")

    def test_dialog_disables_embedded_wcs_and_keeps_a_size_mismatch_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "starless.png"
            other_path = root / "other.fits"
            _write_fits(other_path, width=20, height=10, header=_wcs_header())
            prompt = SkyExplorerStarlessWcsPrompt(
                image_path=image_path,
                image_width=100,
                image_height=80,
                embedded_candidate=None,
                companion_unavailable_note="starless.fits is beside this image but is 40×40 px, while this image is 100×80 px.",
            )
            dialog = SkyExplorerStarlessWcsDialog(prompt)
            self.assertFalse(dialog._use_embedded_button.isEnabled())
            self.assertTrue(dialog._use_file_button.isDefault())
            self.assertIn("40×40", dialog._notice.text())
            with patch.object(QFileDialog, "getOpenFileName", return_value=(str(other_path), "")):
                dialog._pick_wcs_file()
            self.assertEqual(dialog.choice().action, "cancel")
            self.assertIn("20×10", dialog._notice.text())
            self.assertTrue(dialog.isVisible() or dialog.result() == 0)

            matched_path = root / "matched.fits"
            _write_fits(matched_path, header=_wcs_header())
            with patch.object(QFileDialog, "getOpenFileName", return_value=(str(matched_path), "")):
                dialog._pick_wcs_file()
            self.assertEqual(dialog.choice().action, "use_file")
            self.assertEqual(dialog.choice().wcs_path, matched_path)

            dialog._accept_plate_solve()
            self.assertEqual(dialog.choice().action, "plate_solve")
