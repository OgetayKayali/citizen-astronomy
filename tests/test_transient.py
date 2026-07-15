from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from photometry_app.core.models import CatalogStar, PlateSolveResult, WcsStatus
from photometry_app.core.settings import AppSettings
from photometry_app.core.transient import search_transients_in_folder
from photometry_app.core.wcs import extract_solved_field


class FakeCatalogService:
    def __init__(self, stars: list[CatalogStar]) -> None:
        self._stars = list(stars)
        self.full_field_calls: list[object] = []
        self.limited_field_calls: list[tuple[object, float]] = []

    def query_gaia_stars(self, solved_field, progress_callback=None) -> list[CatalogStar]:
        self.full_field_calls.append(solved_field)
        return list(self._stars)

    def query_gaia_stars_limited(self, solved_field, maximum_magnitude: float, progress_callback=None) -> list[CatalogStar]:
        self.limited_field_calls.append((solved_field, maximum_magnitude))
        return list(self._stars)


class FakeAstrometryClient:
    def __init__(self, solved_header: fits.Header) -> None:
        self.solved_header = solved_header
        self.calls: list[Path] = []

    def solve_file(self, fits_path: Path, cache_dir: Path, timeout_seconds: int = 300, hints: object = None) -> PlateSolveResult:
        self.calls.append(fits_path)
        cache_dir.mkdir(parents=True, exist_ok=True)
        solved_path = cache_dir / f"{fits_path.stem}_solved.fits"
        fits.PrimaryHDU(data=np.zeros((64, 64), dtype=np.float32), header=self.solved_header).writeto(solved_path, overwrite=True)
        return PlateSolveResult(
            source_path=fits_path,
            status=WcsStatus.SOLVED,
            solved_field=extract_solved_field(self.solved_header, 64, 64, solved_path),
            reasons=[],
        )


class TransientFinderTest(unittest.TestCase):
    def test_search_retains_variable_uncataloged_source_and_suppresses_static_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            header = self._wcs_header(datetime(2026, 4, 27, 3, 0, tzinfo=UTC))
            known_ra, known_dec = self._sky_at_pixel(header, 20.0, 25.0)
            known_star = CatalogStar("gaia-dr3", "known", "Known Gaia", known_ra, known_dec, 12.0, False)
            for index in range(2):
                sources = [(20.0, 25.0, 850.0), (35.0, 38.0, 720.0)]
                if index == 1:
                    sources.append((41.0, 30.0, 900.0))
                data = self._image_with_sources(sources, seed=index)
                frame_header = header.copy()
                frame_header["DATE-OBS"] = (datetime(2026, 4, 27, 3, 0, tzinfo=UTC) + timedelta(minutes=index)).isoformat()
                fits.PrimaryHDU(data=data, header=frame_header).writeto(root_path / f"frame_{index + 1}.fits")
            settings = self._settings(root_path)
            catalog_service = FakeCatalogService([known_star])

            result = search_transients_in_folder(
                root_path,
                settings,
                catalog_service=catalog_service,
                min_frame_count=2,
                detection_sigma=8.0,
            )

            self.assertEqual(len(catalog_service.full_field_calls), 0)
            self.assertEqual(len(catalog_service.limited_field_calls), 1)
            self.assertEqual(catalog_service.limited_field_calls[0][1], 18.0)
            self.assertEqual(result.total_files, 2)
            self.assertEqual(result.solved_frame_count, 2)
            self.assertEqual(len(result.candidates), 1)
            candidate = result.candidates[0]
            self.assertEqual(candidate.frame_count, 1)
            self.assertEqual(candidate.detection_count, 1)
            self.assertEqual(len(candidate.blink_paths), 2)
            self.assertGreater(candidate.median_snr, 100.0)
            self.assertGreater(candidate.variability_snr, 10.0)
            transient_ra, transient_dec = self._sky_at_pixel(header, 41.0, 30.0)
            self.assertAlmostEqual(candidate.ra_deg, transient_ra, places=3)
            self.assertAlmostEqual(candidate.dec_deg, transient_dec, places=3)

    def test_search_uses_astrometry_fallback_for_unsolved_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            solved_header = self._wcs_header(datetime(2026, 4, 27, 4, 0, tzinfo=UTC))
            for index in range(2):
                header = fits.Header()
                header["DATE-OBS"] = (datetime(2026, 4, 27, 4, 0, tzinfo=UTC) + timedelta(minutes=index)).isoformat()
                header["EXPTIME"] = 60.0
                sources = [(36.0, 32.0, 700.0)] if index == 1 else []
                data = self._image_with_sources(sources, seed=index + 10)
                fits.PrimaryHDU(data=data, header=header).writeto(root_path / f"unsolved_{index + 1}.fits")
            settings = self._settings(root_path)
            settings.astrometry_api_key = "fake-key"
            fake_client = FakeAstrometryClient(solved_header)
            catalog_service = FakeCatalogService([])

            result = search_transients_in_folder(
                root_path,
                settings,
                catalog_service=catalog_service,
                astrometry_client_factory=lambda _api_key: fake_client,
                min_frame_count=2,
                detection_sigma=8.0,
            )

            self.assertEqual(len(catalog_service.full_field_calls), 0)
            self.assertEqual(len(catalog_service.limited_field_calls), 1)
            self.assertEqual(len(fake_client.calls), 2)
            self.assertEqual(result.solved_frame_count, 2)
            self.assertEqual(result.astrometry_solved_count, 2)
            self.assertEqual(len(result.candidates), 1)
            self.assertTrue(all(frame.solved_via_astrometry for frame in result.frame_results))

    def test_roi_margin_excludes_corner_artifact_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            header = self._wcs_header(datetime(2026, 4, 27, 5, 0, tzinfo=UTC))
            for index in range(2):
                sources = [(8.0, 8.0, 900.0)] if index == 1 else []
                data = self._image_with_sources(sources, seed=index + 20)
                frame_header = header.copy()
                frame_header["DATE-OBS"] = (datetime(2026, 4, 27, 5, 0, tzinfo=UTC) + timedelta(minutes=index)).isoformat()
                fits.PrimaryHDU(data=data, header=frame_header).writeto(root_path / f"corner_{index + 1}.fits")
            settings = self._settings(root_path)

            result = search_transients_in_folder(
                root_path,
                settings,
                catalog_service=FakeCatalogService([]),
                min_frame_count=2,
                detection_sigma=8.0,
                edge_margin_fraction=0.20,
            )

            self.assertEqual(len(result.candidates), 0)

    def _settings(self, root_path: Path) -> AppSettings:
        settings = AppSettings.from_root(root_path)
        settings.cache_dir = root_path / "cache"
        return settings

    def _wcs_header(self, timestamp: datetime) -> fits.Header:
        header = fits.Header()
        header["DATE-OBS"] = timestamp.isoformat()
        header["EXPTIME"] = 60.0
        header["FILTER"] = "L"
        header["OBJECT"] = "Transient Field"
        header["CTYPE1"] = "RA---TAN"
        header["CTYPE2"] = "DEC--TAN"
        header["CRVAL1"] = 100.0
        header["CRVAL2"] = 20.0
        header["CRPIX1"] = 32.0
        header["CRPIX2"] = 32.0
        header["CD1_1"] = -1.0 / 3600.0
        header["CD1_2"] = 0.0
        header["CD2_1"] = 0.0
        header["CD2_2"] = 1.0 / 3600.0
        return header

    def _sky_at_pixel(self, header: fits.Header, x: float, y: float) -> tuple[float, float]:
        sky = WCS(header).pixel_to_world(x, y)
        return float(sky.ra.deg), float(sky.dec.deg)

    def _image_with_sources(self, sources: list[tuple[float, float, float]], *, seed: int) -> np.ndarray:
        rng = np.random.default_rng(seed)
        image = rng.normal(100.0, 1.0, size=(64, 64)).astype(np.float32)
        yy, xx = np.indices(image.shape)
        for x, y, amplitude in sources:
            image += amplitude * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2.0 * 1.15 ** 2))
        return image.astype(np.float32)


if __name__ == "__main__":
    unittest.main()