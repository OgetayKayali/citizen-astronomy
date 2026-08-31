from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import warnings

import requests
from astropy.coordinates import SkyCoord
from astropy import units as u

from photometry_app.core.catalogs import (
    CatalogService,
    DEFAULT_GAIA_TILE_OPTIONS,
    GaiaTileOptions,
    _GAIA_DR3_VIZIER_COLUMNS,
    _GaiaQueryTile,
    _parse_epoch_hjd,
    _solved_field_celestial_wcs,
    build_gaia_query_tiles,
    capped_solved_field,
    fetch_catalog_literature_period_result,
    fetch_catalog_target_details,
    fetch_catalog_targets_at_coordinate,
    gaia_field_needs_tiles,
    summarize_catalog_service_error,
)
from photometry_app.core.models import CatalogStar, SolvedField


class CatalogServiceTest(unittest.TestCase):
    def _solved_field(self) -> SolvedField:
        return SolvedField(
            center_ra_deg=83.822,
            center_dec_deg=-5.391,
            radius_deg=0.25,
            width=6248,
            height=4176,
            wcs_path=Path("test.fits"),
        )

    def test_cache_helpers_are_available_on_instances(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = CatalogService(Path(temp_dir))
            solved_field = self._solved_field()

        self.assertTrue(hasattr(service, "_cache_key"))
        self.assertTrue(hasattr(service, "_field_catalog_center_prefix"))
        self.assertEqual(service._cache_key(solved_field), "field_83.82200_m5.39100_0.25000.json")
        self.assertEqual(service._field_catalog_center_prefix(solved_field), "field_83.82200_m5.39100_")
        self.assertEqual(service.clear_field_cache(solved_field), 0)

    def test_query_field_catalog_round_trips_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = CatalogService(Path(temp_dir))
            solved_field = self._solved_field()
            gaia_star = CatalogStar("gaia-dr3", "gaia-ref", "gaia-ref", 83.822, -5.391, 11.0, False)

            with patch.object(CatalogService, "_query_gaia_field", return_value=[gaia_star]), patch.object(
                CatalogService,
                "_query_vsx",
                return_value=[],
            ), patch.object(CatalogService, "_query_exoplanets", return_value=[]), patch(
                "photometry_app.core.standard_magnitude.enrich_gaia_stars_with_standard_catalogs",
                return_value={"apass_matches": 0, "vsp_matches": 0, "vsp_chart_id": None},
            ):
                catalog = service.query_field_catalog(solved_field)

            self.assertEqual([star.source_id for star in catalog.gaia_stars], ["gaia-ref"])
            self.assertEqual(service.clear_field_cache(solved_field), 1)

    def test_clear_field_cache_removes_capped_and_full_radius_keys_for_the_same_center(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = CatalogService(Path(temp_dir))
            full_field = SolvedField(
                center_ra_deg=145.93800,
                center_dec_deg=55.95700,
                radius_deg=3.2512,
                width=6248,
                height=4176,
                wcs_path=Path("wuma.fits"),
            )
            capped_field = capped_solved_field(full_field, DEFAULT_GAIA_TILE_OPTIONS.max_radius_deg)
            other_field = SolvedField(
                center_ra_deg=10.0,
                center_dec_deg=20.0,
                radius_deg=0.25,
                width=100,
                height=100,
                wcs_path=Path("other.fits"),
            )
            capped_path = Path(temp_dir) / service._field_catalog_cache_key(
                capped_field,
                include_gaia=True,
                include_variable_stars=False,
                include_exoplanets=False,
                gaia_max_magnitude=16.0,
                gaia_row_cap=5000,
                variable_star_max_magnitude=None,
                exoplanet_max_magnitude=None,
            )
            vsx_path = Path(temp_dir) / service._field_catalog_cache_key(
                full_field,
                include_gaia=False,
                include_variable_stars=True,
                include_exoplanets=True,
                gaia_max_magnitude=None,
                gaia_row_cap=None,
                variable_star_max_magnitude=None,
                exoplanet_max_magnitude=None,
            )
            other_path = Path(temp_dir) / service._cache_key(other_field)
            for path in (capped_path, vsx_path, other_path):
                path.write_text("{}", encoding="utf-8")

            removed = service.clear_field_cache(full_field)

            self.assertEqual(removed, 2)
            self.assertFalse(capped_path.exists())
            self.assertFalse(vsx_path.exists())
            self.assertTrue(other_path.exists())

    def test_query_field_catalog_can_skip_gaia_and_exoplanets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = CatalogService(Path(temp_dir))
            solved_field = self._solved_field()
            variable_star = CatalogStar("vsx", "vsx-ref", "RR Lyr", 83.82, -5.39, 12.3, True)

            with patch.object(CatalogService, "_query_gaia_field") as query_gaia, patch.object(
                CatalogService,
                "_query_vsx",
                return_value=[variable_star],
            ) as query_vsx, patch.object(CatalogService, "_query_exoplanets") as query_exoplanets:
                catalog = service.query_field_catalog(
                    solved_field,
                    include_gaia=False,
                    include_variable_stars=True,
                    include_exoplanets=False,
                )

            query_gaia.assert_not_called()
            query_vsx.assert_called_once()
            query_exoplanets.assert_not_called()
            self.assertEqual([star.source_id for star in catalog.variable_stars], ["vsx-ref"])
            self.assertEqual(catalog.gaia_stars, [])
            self.assertEqual(catalog.exoplanets, [])

    def test_query_gaia_filtered_uses_minimal_vizier_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = CatalogService(Path(temp_dir))
            captured: dict[str, object] = {}

            class FakeVizier:
                def __init__(self, *, columns, row_limit, column_filters) -> None:
                    captured["columns"] = columns
                    captured["row_limit"] = row_limit
                    captured["column_filters"] = column_filters

                def query_region(self, center, radius, catalog):  # noqa: ANN001
                    return []

            with patch("photometry_app.core.catalogs.Vizier", FakeVizier):
                stars = service._query_gaia_filtered(
                    SkyCoord(247.498 * u.deg, -28.194 * u.deg),
                    1.0 * u.deg,
                    maximum_magnitude=17.0,
                    row_limit=500,
                )

            self.assertEqual(stars, [])
            self.assertEqual(captured["columns"], list(_GAIA_DR3_VIZIER_COLUMNS))
            self.assertNotIn("*", captured["columns"])

    def test_query_field_catalog_applies_variable_and_exoplanet_magnitude_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = CatalogService(Path(temp_dir))
            solved_field = self._solved_field()
            variable_star = CatalogStar("vsx", "vsx-bright", "Bright Variable", 83.82, -5.39, 7.8, True)
            bright_exoplanet = CatalogStar("nasa-exoplanet-archive", "exo-bright", "Bright Host b", 83.81, -5.38, 7.9, False)
            faint_exoplanet = CatalogStar("nasa-exoplanet-archive", "exo-faint", "Faint Host b", 83.83, -5.37, 10.2, False)

            with patch.object(CatalogService, "_query_vsx_filtered", return_value=[variable_star]) as query_vsx_filtered, patch.object(
                CatalogService,
                "_query_exoplanets",
                return_value=[bright_exoplanet, faint_exoplanet],
            ):
                catalog = service.query_field_catalog(
                    solved_field,
                    include_gaia=False,
                    include_variable_stars=True,
                    include_exoplanets=True,
                    variable_star_max_magnitude=8.0,
                    exoplanet_max_magnitude=8.0,
                )

            query_vsx_filtered.assert_called_once()
            self.assertEqual(query_vsx_filtered.call_args.kwargs["maximum_magnitude"], 8.0)
            self.assertEqual([star.source_id for star in catalog.variable_stars], ["vsx-bright"])
            self.assertEqual([star.source_id for star in catalog.exoplanets], ["exo-bright"])

    def test_query_field_catalog_retries_alternate_solved_field_centers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = CatalogService(Path(temp_dir))
            solved_field = self._solved_field()
            gaia_star = CatalogStar("gaia-dr3", "gaia-ref", "gaia-ref", 83.822, -5.391, 11.0, False)
            attempted_centers: list[tuple[float, float]] = []
            progress_messages: list[str] = []

            def flaky_gaia_query(center, radius, maximum_magnitude=None, row_limit=None):
                attempted_centers.append((round(center.ra.deg, 6), round(center.dec.deg, 6)))
                if len(attempted_centers) == 1:
                    raise requests.ConnectionError("HTTPSConnectionPool(host='vizier.cds.unistra.fr', port=443): failed")
                return [gaia_star]

            with patch.object(CatalogService, "_query_gaia_filtered", side_effect=flaky_gaia_query), patch.object(
                CatalogService,
                "_query_vsx",
                return_value=[],
            ), patch.object(CatalogService, "_query_exoplanets", return_value=[]), patch(
                "photometry_app.core.standard_magnitude.enrich_gaia_stars_with_standard_catalogs",
                return_value={"apass_matches": 0, "vsp_matches": 0, "vsp_chart_id": None},
            ):
                catalog = service.query_field_catalog(solved_field, progress_callback=progress_messages.append)

            self.assertEqual([star.source_id for star in catalog.gaia_stars], ["gaia-ref"])
            self.assertGreaterEqual(len(attempted_centers), 2)
            self.assertNotEqual(attempted_centers[0], attempted_centers[1])
            self.assertTrue(
                any("retrying alternate solved-field center" in message.lower() for message in progress_messages)
            )

    def test_query_field_catalog_surfaces_actionable_vizier_warning_text(self) -> None:
        error = requests.ConnectionError("HTTPSConnectionPool(host='vizier.cds.unistra.fr', port=443): failed")

        message = summarize_catalog_service_error(error)

        self.assertIn("Could not reach the VizieR catalog service", message)
        self.assertIn("Check the network connection or try again later", message)

    def test_summarize_catalog_service_error_rewrites_empty_vizier_parse_failure(self) -> None:
        message = summarize_catalog_service_error(
            ValueError(
                "Failed to parse VIZIER result! The raw response can be found in self.response, "
                "and the error in self.table_parse_error. Exception: Expecting value: line 1 column 1 (char 0)"
            )
        )

        self.assertIn("empty or unreadable", message)
        self.assertNotIn("self.response", message)
        self.assertNotIn("Expecting value", message)

    def test_query_gaia_filtered_retries_empty_vizier_parse_error_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = CatalogService(Path(temp_dir))
            center = SkyCoord(83.822 * u.deg, -5.391 * u.deg)
            parse_error = ValueError(
                "Failed to parse VIZIER result! Exception: Expecting value: line 1 column 1 (char 0)"
            )
            calls = {"count": 0}

            class FakeVizier:
                def __init__(self, *args, **kwargs) -> None:
                    pass

                def query_region(self, _center, radius=None, catalog=None):
                    calls["count"] += 1
                    if calls["count"] == 1:
                        raise parse_error
                    return [[
                        {
                            "Source": "3017367151399567872",
                            "Gmag": 12.4,
                            "RA_ICRS": 83.822,
                            "DE_ICRS": -5.391,
                        }
                    ]]

            with patch("photometry_app.core.catalogs.Vizier", FakeVizier):
                stars = service._query_gaia_filtered(center, 0.25 * u.deg, maximum_magnitude=18.0, row_limit=35000)

            self.assertEqual(calls["count"], 2)
            self.assertEqual([star.source_id for star in stars], ["3017367151399567872"])

    def test_query_field_catalog_forwards_gaia_magnitude_and_row_cap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = CatalogService(Path(temp_dir))
            solved_field = self._solved_field()
            gaia_star = CatalogStar("gaia-dr3", "gaia-ref", "gaia-ref", 83.822, -5.391, 11.0, False)

            with patch.object(CatalogService, "_query_gaia_field", return_value=[gaia_star]) as query_gaia_field, patch.object(
                CatalogService,
                "_query_vsx",
                return_value=[],
            ), patch.object(CatalogService, "_query_exoplanets", return_value=[]), patch(
                "photometry_app.core.standard_magnitude.enrich_gaia_stars_with_standard_catalogs",
                return_value={"apass_matches": 0, "vsp_matches": 0, "vsp_chart_id": None},
            ):
                catalog = service.query_field_catalog(
                    solved_field,
                    gaia_max_magnitude=18.0,
                    gaia_row_cap=35000,
                )

            query_gaia_field.assert_called_once()
            self.assertEqual(query_gaia_field.call_args.kwargs["maximum_magnitude"], 18.0)
            self.assertEqual(query_gaia_field.call_args.kwargs["row_limit"], 35000)
            self.assertEqual([star.source_id for star in catalog.gaia_stars], ["gaia-ref"])

    def test_query_gaia_stars_limited_passes_row_limit_to_filtered_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = CatalogService(Path(temp_dir))
            solved_field = self._solved_field()
            gaia_star = CatalogStar("gaia-dr3", "gaia-ref", "gaia-ref", 83.822, -5.391, 11.0, False)

            with patch.object(CatalogService, "_query_gaia_filtered", return_value=[gaia_star]) as query_gaia_filtered:
                stars = service.query_gaia_stars_limited(solved_field, 16.5, row_limit=250)

            query_gaia_filtered.assert_called_once()
            self.assertEqual(query_gaia_filtered.call_args.kwargs["maximum_magnitude"], 16.5)
            self.assertEqual(query_gaia_filtered.call_args.kwargs["row_limit"], 250)
            self.assertEqual([star.source_id for star in stars], ["gaia-ref"])

    def test_query_gaia_field_stops_between_tiles_when_cancelled(self) -> None:
        import threading

        from photometry_app.core.wcs import WcsSolveCancelled

        with tempfile.TemporaryDirectory() as temp_dir:
            service = CatalogService(Path(temp_dir))
            solved_field = SolvedField(
                center_ra_deg=83.822,
                center_dec_deg=-5.391,
                radius_deg=1.2,
                width=6248,
                height=4176,
                wcs_path=Path("test.fits"),
            )
            cancel_event = threading.Event()
            calls = {"count": 0}

            def fake_query(*_args, **_kwargs):
                calls["count"] += 1
                if calls["count"] == 1:
                    cancel_event.set()
                return [
                    CatalogStar("gaia-dr3", f"gaia-{calls['count']}", f"gaia-{calls['count']}", 83.8, -5.4, 12.0, False)
                ]

            with patch.object(CatalogService, "_query_gaia_filtered", side_effect=fake_query):
                with self.assertRaises(WcsSolveCancelled):
                    service._query_gaia_field(
                        solved_field,
                        maximum_magnitude=14.0,
                        row_limit=100,
                        cancel_event=cancel_event,
                    )
            self.assertEqual(calls["count"], 1)

    def test_gaia_rows_capture_preferred_non_gaia_display_name_in_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = CatalogService(Path(temp_dir))

            stars = service._gaia_stars_from_tables(
                [[
                    {
                        "Source": "3017367151399567872",
                        "Gmag": 1.74,
                        "RA_ICRS": 83.822,
                        "DE_ICRS": -5.391,
                        "HD": "HD 37742",
                        "HIP": "HIP 26727",
                    }
                ]]
            )

        self.assertEqual(len(stars), 1)
        self.assertEqual(stars[0].name, "3017367151399567872")
        self.assertEqual(stars[0].metadata.get("preferred_display_name"), "HD 37742")

    def test_fetch_catalog_target_details_prefers_non_gaia_identifier(self) -> None:
        fake_simbad = patch("photometry_app.core.catalogs.Simbad").start()
        self.addCleanup(patch.stopall)

        simbad_instance = fake_simbad.return_value
        simbad_instance.query_region.return_value = [
            {
                "MAIN_ID": "Gaia DR3 3017367151399567872",
                "IDS": "Gaia DR3 3017367151399567872|HD 37742|HIP 26727",
                "OTYPE": "Star",
                "SP_TYPE": "O9.5Iab",
                "FLUX_V": 1.74,
            }
        ]

        details = fetch_catalog_target_details(83.822, -5.391)

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual(details.main_id, "HD 37742")
        self.assertEqual(details.object_type, "Star")

    def test_fetch_catalog_target_details_handles_lowercase_simbad_columns(self) -> None:
        fake_simbad = patch("photometry_app.core.catalogs.Simbad").start()
        self.addCleanup(patch.stopall)

        class FakeRow:
            colnames = ["main_id", "otype", "sp_type", "V", "ids"]

            def __init__(self) -> None:
                self._values = {
                    "main_id": "HD 61866",
                    "otype": "*",
                    "sp_type": "M3.4III",
                    "V": 9.13,
                    "ids": "Gaia DR3 3029118976498787840|HD 61866|SAO 153236",
                }

            def __getitem__(self, key):
                return self._values[key]

        simbad_instance = fake_simbad.return_value
        simbad_instance.query_region.return_value = [FakeRow()]

        details = fetch_catalog_target_details(115.17113980636, -15.28054080902)

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual(details.main_id, "HD 61866")
        self.assertEqual(details.object_type, "*")
        self.assertEqual(details.spectral_type, "M3.4III")

    def test_fetch_catalog_targets_at_coordinate_returns_sorted_targets(self) -> None:
        fake_simbad = patch("photometry_app.core.catalogs.Simbad").start()
        self.addCleanup(patch.stopall)

        simbad_instance = fake_simbad.return_value
        simbad_instance.query_region.return_value = [
            {
                "MAIN_ID": "NGC 7000",
                "IDS": "NGC 7000",
                "OTYPE": "HII",
                "SP_TYPE": "",
                "V": 4.0,
                "RA_d": 314.750,
                "DEC_d": 44.333,
            },
            {
                "MAIN_ID": "HD 123456",
                "IDS": "HD 123456",
                "OTYPE": "*",
                "SP_TYPE": "B0V",
                "V": 8.2,
                "RA_d": 314.751,
                "DEC_d": 44.334,
            },
        ]

        targets = fetch_catalog_targets_at_coordinate(314.7505, 44.3335, radius_arcsec=10.0)

        self.assertEqual(len(targets), 2)
        self.assertEqual(targets[0].main_id, "HD 123456")
        self.assertEqual(targets[1].main_id, "NGC 7000")
        self.assertLess(targets[0].separation_arcsec or 0.0, targets[1].separation_arcsec or 0.0)


class GaiaQueryTilesTest(unittest.TestCase):
    def _small_field(self) -> SolvedField:
        return SolvedField(
            center_ra_deg=83.822,
            center_dec_deg=-5.391,
            radius_deg=0.25,
            width=6248,
            height=4176,
            wcs_path=Path("test.fits"),
        )

    def _wide_field(self, wcs_path: Path | None = None) -> SolvedField:
        return SolvedField(
            center_ra_deg=327.41,
            center_dec_deg=47.44,
            radius_deg=1.978,
            width=4000,
            height=2245,
            wcs_path=wcs_path or Path("test.fits"),
        )

    def _star(self, source_id: str, ra_deg: float, dec_deg: float) -> CatalogStar:
        return CatalogStar("gaia-dr3", source_id, source_id, ra_deg, dec_deg, 11.0, False)

    def test_smaller_tile_radius_creates_more_tiles(self) -> None:
        solved_field = self._wide_field()

        default_tiles = build_gaia_query_tiles(solved_field)
        finer_tiles = build_gaia_query_tiles(solved_field, GaiaTileOptions(max_radius_deg=0.20))

        self.assertGreater(len(finer_tiles), len(default_tiles))

    def test_small_field_uses_a_single_gaia_tile(self) -> None:
        solved_field = self._small_field()

        tiles = build_gaia_query_tiles(solved_field)

        self.assertFalse(gaia_field_needs_tiles(solved_field))
        self.assertEqual(len(tiles), 1)
        self.assertAlmostEqual(tiles[0].center.ra.deg, solved_field.center_ra_deg, places=5)
        self.assertAlmostEqual(tiles[0].center.dec.deg, solved_field.center_dec_deg, places=5)

    def test_wide_field_tiles_cover_the_image_footprint(self) -> None:
        from astropy.io import fits

        with tempfile.TemporaryDirectory() as temp_dir:
            wcs_path = Path(temp_dir) / "cocoon.wcs.fits"
            width, height, radius_deg = 4000, 2245, 1.978
            scale_deg = (2.0 * radius_deg) / (width**2 + height**2) ** 0.5
            header = fits.Header()
            header["NAXIS"] = 2
            header["NAXIS1"] = width
            header["NAXIS2"] = height
            header["CTYPE1"] = "RA---TAN"
            header["CTYPE2"] = "DEC--TAN"
            header["CRVAL1"] = 327.41
            header["CRVAL2"] = 47.44
            header["CRPIX1"] = width / 2.0
            header["CRPIX2"] = height / 2.0
            header["CDELT1"] = -scale_deg
            header["CDELT2"] = scale_deg
            header["CUNIT1"] = "deg"
            header["CUNIT2"] = "deg"
            fits.PrimaryHDU(header=header).writeto(wcs_path)
            solved_field = self._wide_field(wcs_path)

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                tiles = build_gaia_query_tiles(solved_field)
                celestial = _solved_field_celestial_wcs(solved_field)

            self.assertTrue(gaia_field_needs_tiles(solved_field))
            self.assertGreaterEqual(len(tiles), 8)
            self.assertLessEqual(len(tiles), 64)
            assert celestial is not None
            corners = (
                (0.0, 0.0),
                (float(width), 0.0),
                (0.0, float(height)),
                (float(width), float(height)),
                (width / 2.0, height / 2.0),
            )
            for pixel_x, pixel_y in corners:
                ra_deg, dec_deg = celestial.pixel_to_world_values(pixel_x, pixel_y)
                sky = SkyCoord(float(ra_deg) * u.deg, float(dec_deg) * u.deg)
                covered = any(
                    float(sky.separation(tile.center).deg) <= tile.radius_deg for tile in tiles
                )
                self.assertTrue(covered, f"pixel ({pixel_x}, {pixel_y}) is not covered by any Gaia tile")

    def test_query_gaia_field_merges_and_dedupes_tiles(self) -> None:
        solved_field = self._wide_field()
        tiles = [
            _GaiaQueryTile(
                center=SkyCoord(327.2 * u.deg, 47.2 * u.deg),
                radius_deg=0.3,
                x0=0.0,
                y0=0.0,
                x1=2000.0,
                y1=1122.0,
            ),
            _GaiaQueryTile(
                center=SkyCoord(327.6 * u.deg, 47.6 * u.deg),
                radius_deg=0.3,
                x0=2000.0,
                y0=1122.0,
                x1=4000.0,
                y1=2245.0,
            ),
        ]

        def fake_query(center, radius, maximum_magnitude=None, row_limit=None):
            shared = self._star("shared", 327.41, 47.44)
            unique = self._star(f"tile-{center.ra.deg:.1f}", center.ra.deg, center.dec.deg)
            return [shared, unique]

        with tempfile.TemporaryDirectory() as temp_dir:
            service = CatalogService(Path(temp_dir))
            with patch("photometry_app.core.catalogs.build_gaia_query_tiles", return_value=tiles), patch.object(
                CatalogService,
                "_query_gaia_filtered",
                side_effect=fake_query,
            ) as query_gaia_filtered:
                stars = service._query_gaia_field(solved_field, maximum_magnitude=18.0, row_limit=35000)

        self.assertEqual(query_gaia_filtered.call_count, 2)
        self.assertEqual(sorted(star.source_id for star in stars), ["shared", "tile-327.2", "tile-327.6"])

    def test_query_gaia_field_splits_a_saturated_tile(self) -> None:
        solved_field = self._wide_field()
        parent = _GaiaQueryTile(
            center=SkyCoord(327.41 * u.deg, 47.44 * u.deg),
            radius_deg=0.4,
            x0=0.0,
            y0=0.0,
            x1=2000.0,
            y1=1122.0,
        )
        neighbor = _GaiaQueryTile(
            center=SkyCoord(327.8 * u.deg, 47.7 * u.deg),
            radius_deg=0.3,
            x0=2000.0,
            y0=1122.0,
            x1=4000.0,
            y1=2245.0,
        )
        progress_messages: list[str] = []
        calls: list[tuple[float, float]] = []

        def fake_query(center, radius, maximum_magnitude=None, row_limit=None):
            calls.append((round(center.ra.deg, 5), round(center.dec.deg, 5)))
            if len(calls) == 1:
                return [self._star(f"sat-{index}", 327.41, 47.44) for index in range(int(row_limit))]
            return [self._star(f"ok-{len(calls)}", center.ra.deg, center.dec.deg)]

        with tempfile.TemporaryDirectory() as temp_dir:
            service = CatalogService(Path(temp_dir))
            with patch(
                "photometry_app.core.catalogs.build_gaia_query_tiles",
                return_value=[parent, neighbor],
            ), patch.object(CatalogService, "_query_gaia_filtered", side_effect=fake_query):
                stars = service._query_gaia_field(
                    solved_field,
                    maximum_magnitude=18.0,
                    row_limit=8,
                    progress_callback=progress_messages.append,
                )

        self.assertEqual(len(calls), 6)
        self.assertTrue(any("splitting into 4 smaller tiles" in message for message in progress_messages))
        self.assertTrue(any(star.source_id.startswith("ok-") for star in stars))
        self.assertFalse(any(star.source_id.startswith("sat-") for star in stars))

    def test_wide_field_cache_key_invalidates_single_cone_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = CatalogService(Path(temp_dir))
            small_key = service._field_catalog_cache_key(
                self._small_field(),
                include_gaia=True,
                include_variable_stars=True,
                include_exoplanets=True,
                gaia_max_magnitude=18.0,
                gaia_row_cap=35000,
                variable_star_max_magnitude=None,
                exoplanet_max_magnitude=None,
            )
            wide_key = service._field_catalog_cache_key(
                self._wide_field(),
                include_gaia=True,
                include_variable_stars=True,
                include_exoplanets=True,
                gaia_max_magnitude=18.0,
                gaia_row_cap=35000,
                variable_star_max_magnitude=None,
                exoplanet_max_magnitude=None,
            )
            limited_key = service._gaia_filtered_cache_key(
                self._wide_field(),
                maximum_magnitude=18.0,
                row_limit=35000,
            )

        self.assertNotIn("gaia-tiles", small_key)
        self.assertIn("gaia-tiles", wide_key)
        self.assertTrue(limited_key.endswith("_tiles.json"))

    def test_capped_solved_field_keeps_narrow_fields_and_caps_wide_fields(self) -> None:
        narrow = self._small_field()
        self.assertIs(capped_solved_field(narrow, DEFAULT_GAIA_TILE_OPTIONS.max_radius_deg), narrow)
        wide = self._wide_field()
        capped = capped_solved_field(wide, DEFAULT_GAIA_TILE_OPTIONS.max_radius_deg)
        self.assertAlmostEqual(capped.radius_deg, DEFAULT_GAIA_TILE_OPTIONS.max_radius_deg)
        self.assertEqual(capped.center_ra_deg, wide.center_ra_deg)
        self.assertFalse(gaia_field_needs_tiles(capped))


class LiteraturePeriodFetchTest(unittest.TestCase):
    def test_parse_epoch_hjd_reads_the_leading_julian_date(self) -> None:
        self.assertAlmostEqual(_parse_epoch_hjd("2430000.123 (HJD)"), 2430000.123)
        self.assertIsNone(_parse_epoch_hjd(""))
        self.assertIsNone(_parse_epoch_hjd("none"))

    def test_fetch_vsx_literature_period_includes_epoch(self) -> None:
        star = CatalogStar("vsx", "12345", "DY Her", 10.0, 20.0, 10.5, True)
        html = (
            '<td class="detailtitle">Period</td><td class="detaildata">0.148773 d</td>'
            '<td class="detailtitle">Epoch</td><td class="detaildata">2430000.123 (HJD)</td>'
            '<td class="detailtitle">Rise/eclipse dur.</td><td class="detaildata"></td>'
        )
        response = SimpleNamespace(text=html, raise_for_status=lambda: None)
        with patch("photometry_app.core.catalogs.requests.get", return_value=response):
            result = fetch_catalog_literature_period_result(star)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertAlmostEqual(result.period_days or 0.0, 0.148773)
        self.assertAlmostEqual(result.epoch_hjd or 0.0, 2430000.123)
        self.assertEqual(result.source, "VSX")
