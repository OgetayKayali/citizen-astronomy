from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import requests
from astropy.coordinates import SkyCoord
from astropy import units as u

from photometry_app.core.catalogs import (
    CatalogService,
    _GAIA_DR3_VIZIER_COLUMNS,
    fetch_catalog_target_details,
    fetch_catalog_targets_at_coordinate,
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
            self.assertEqual(service._cache_key(solved_field), "field_83.82200_m5.39100_0.25000.json")
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
            ), patch.object(CatalogService, "_query_exoplanets", return_value=[]):
                catalog = service.query_field_catalog(solved_field)

            self.assertEqual([star.source_id for star in catalog.gaia_stars], ["gaia-ref"])
            self.assertEqual(service.clear_field_cache(solved_field), 1)

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

            def flaky_gaia_query(center, radius):
                attempted_centers.append((round(center.ra.deg, 6), round(center.dec.deg, 6)))
                if len(attempted_centers) == 1:
                    raise requests.ConnectionError("HTTPSConnectionPool(host='vizier.cds.unistra.fr', port=443): failed")
                return [gaia_star]

            with patch.object(CatalogService, "_query_gaia_filtered", side_effect=flaky_gaia_query), patch.object(
                CatalogService,
                "_query_vsx",
                return_value=[],
            ), patch.object(CatalogService, "_query_exoplanets", return_value=[]):
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
