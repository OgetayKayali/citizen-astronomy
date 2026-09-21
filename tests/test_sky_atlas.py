from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from astropy.table import Table
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from photometry_app.core.sky_atlas import (
    SKY_ATLAS_DEEP_SKY_CATALOG_NAMES,
    SkyAtlasCatalogCancelled,
    SkyAtlasObject,
    _barnard_object_from_row,
    _hash_pn_object_from_row,
    _load_packaged_star_name_aliases,
    _rcw_object_from_row,
    _resolve_sky_atlas_name_and_aliases,
    _sharpless_object_from_row,
    sky_atlas_literature_identifier,
    sky_atlas_literature_links,
    sky_atlas_object_has_given_name,
    filter_sky_atlas_objects_by_deep_sky_catalogs,
    format_sky_atlas_search_result_title,
    load_local_sky_atlas_objects,
    load_scientific_sky_atlas_star_objects,
    load_sky_atlas_deep_sky_objects,
    load_sky_atlas_search_objects,
    normalize_sky_atlas_deep_sky_catalogs,
    search_sky_atlas_objects,
)
from photometry_app.ui.sky_atlas_settings_dialog import SkyAtlasDisplaySettings, SkyAtlasSettingsDialog


class SkyAtlasCatalogCacheTest(unittest.TestCase):
    def test_packaged_objects_prefer_common_name_over_prefixed_catalog_label(self) -> None:
        objects = load_local_sky_atlas_objects()

        orion_nebula = next(item for item in objects if "NGC 1976" in item.aliases)

        self.assertEqual(orion_nebula.name, "Orion Nebula")
        self.assertIn("M42", orion_nebula.aliases)
        self.assertIn("M42 Orion Nebula", orion_nebula.aliases)

    def test_scientific_star_name_resolution_promotes_actual_name_and_keeps_catalog_aliases(self) -> None:
        display_name, aliases = _resolve_sky_atlas_name_and_aliases(
            "HIP 26727",
            ("HIP 26727", "HD 37742"),
            object_type="Star",
            preferred_name_candidates=_load_packaged_star_name_aliases()[26727],
        )

        self.assertEqual(display_name, "Alnitak")
        self.assertIn("HIP 26727", aliases)
        self.assertIn("HD 37742", aliases)

    def test_scientific_catalog_download_is_saved_locally(self) -> None:
        downloaded_objects = (
            SkyAtlasObject(
                name="HIP 12345",
                object_type="Star",
                ra_deg=101.25,
                dec_deg=-16.75,
                magnitude=6.2,
                catalog="Hipparcos",
                searchable=False,
                label_visible=False,
                selectable=False,
            ),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_root = Path(temp_dir)

            with patch(
                "photometry_app.core.sky_atlas._download_hipparcos_star_objects",
                return_value=downloaded_objects,
            ) as download_catalog:
                loaded_objects = load_scientific_sky_atlas_star_objects(
                    cache_root,
                    maximum_magnitude=8.5,
                    download_if_missing=True,
                )

            download_catalog.assert_called_once_with(8.5)
            self.assertEqual(loaded_objects, downloaded_objects)

            cache_path = cache_root / "sky-atlas" / "hipparcos_vmag_le_8p5.json"
            self.assertTrue(cache_path.exists())
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["catalog"], "Hipparcos")
            self.assertEqual(payload["objects"][0]["name"], "HIP 12345")

            with patch("photometry_app.core.sky_atlas._download_hipparcos_star_objects") as download_catalog_again:
                cached_objects = load_scientific_sky_atlas_star_objects(
                    cache_root,
                    maximum_magnitude=8.5,
                    download_if_missing=True,
                )

            download_catalog_again.assert_not_called()
            self.assertEqual(cached_objects[0].name, "HIP 12345")

    def test_scientific_catalog_falls_back_to_best_lower_cached_limit(self) -> None:
        cached_objects = (
            SkyAtlasObject(
                name="HIP 54321",
                object_type="Star",
                ra_deg=88.75,
                dec_deg=7.4,
                magnitude=8.4,
                catalog="Hipparcos",
                searchable=False,
                label_visible=False,
                selectable=False,
            ),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_root = Path(temp_dir)

            with patch(
                "photometry_app.core.sky_atlas._download_hipparcos_star_objects",
                return_value=cached_objects,
            ):
                load_scientific_sky_atlas_star_objects(
                    cache_root,
                    maximum_magnitude=8.5,
                    download_if_missing=True,
                )

            with patch("photometry_app.core.sky_atlas._download_hipparcos_star_objects") as download_catalog:
                loaded_objects = load_scientific_sky_atlas_star_objects(
                    cache_root,
                    maximum_magnitude=9.5,
                    download_if_missing=False,
                )

            download_catalog.assert_not_called()
            self.assertEqual(loaded_objects, cached_objects)

    def test_shallow_cache_request_does_not_parse_a_much_deeper_fallback(self) -> None:
        from photometry_app.core.sky_atlas import _load_or_download_named_star_catalog, _store_cached_scientific_star_objects

        deep_objects = (
            SkyAtlasObject(
                name="HIP Bright",
                object_type="Star",
                ra_deg=10.0,
                dec_deg=20.0,
                magnitude=4.1,
                catalog="Hipparcos",
            ),
            SkyAtlasObject(
                name="HIP Faint",
                object_type="Star",
                ra_deg=11.0,
                dec_deg=21.0,
                magnitude=11.2,
                catalog="Hipparcos",
            ),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog_root = Path(temp_dir)
            _store_cached_scientific_star_objects(
                catalog_root / "hipparcos_vmag_le_12p0.json",
                deep_objects,
                12.0,
                catalog_label="Hipparcos",
            )
            loaded_limits: list[float] = []
            original_load = __import__(
                "photometry_app.core.sky_atlas", fromlist=["_load_cached_scientific_star_objects"]
            )._load_cached_scientific_star_objects

            def tracking_load(cache_path, maximum_magnitude):
                loaded_limits.append(float(maximum_magnitude))
                return original_load(cache_path, maximum_magnitude)

            with patch(
                "photometry_app.core.sky_atlas._load_cached_scientific_star_objects",
                side_effect=tracking_load,
            ):
                objects, achieved = _load_or_download_named_star_catalog(
                    catalog_root,
                    catalog_key="hipparcos",
                    catalog_label="Hipparcos",
                    maximum_magnitude=6.5,
                    download_if_missing=False,
                    downloader=lambda _maximum_magnitude: deep_objects,
                )
        self.assertEqual(objects, ())
        self.assertEqual(achieved, 0.0)
        self.assertNotIn(12.0, loaded_limits)

    def test_deeper_fallback_is_filtered_to_the_requested_magnitude(self) -> None:
        from photometry_app.core.sky_atlas import _load_or_download_named_star_catalog, _store_cached_scientific_star_objects

        deep_objects = (
            SkyAtlasObject(
                name="HIP Bright",
                object_type="Star",
                ra_deg=10.0,
                dec_deg=20.0,
                magnitude=4.1,
                catalog="Hipparcos",
            ),
            SkyAtlasObject(
                name="HIP Faint",
                object_type="Star",
                ra_deg=11.0,
                dec_deg=21.0,
                magnitude=11.2,
                catalog="Hipparcos",
            ),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog_root = Path(temp_dir)
            _store_cached_scientific_star_objects(
                catalog_root / "hipparcos_vmag_le_12p0.json",
                deep_objects,
                12.0,
                catalog_label="Hipparcos",
            )
            objects, achieved = _load_or_download_named_star_catalog(
                catalog_root,
                catalog_key="hipparcos",
                catalog_label="Hipparcos",
                maximum_magnitude=6.5,
                download_if_missing=True,
                downloader=lambda _maximum_magnitude: deep_objects,
            )
            self.assertEqual([item.name for item in objects], ["HIP Bright"])
            self.assertAlmostEqual(achieved, 6.5)
            self.assertTrue((catalog_root / "hipparcos_vmag_le_6p5.json").exists())

    def test_empty_named_catalog_cache_is_ignored_and_redownloaded(self) -> None:
        from photometry_app.core.sky_atlas import _load_or_download_named_star_catalog

        downloaded_objects = (
            SkyAtlasObject(
                name="TYC 1-2-3",
                object_type="Star",
                ra_deg=10.0,
                dec_deg=-20.0,
                magnitude=11.2,
                catalog="Tycho-2",
                searchable=False,
                label_visible=False,
                selectable=False,
            ),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog_root = Path(temp_dir)
            empty_path = catalog_root / "tycho2_vmag_le_12p0.json"
            empty_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "catalog": "Tycho-2",
                        "maximum_magnitude": 12.0,
                        "objects": [],
                    }
                ),
                encoding="utf-8",
            )
            objects, achieved = _load_or_download_named_star_catalog(
                catalog_root,
                catalog_key="tycho2",
                catalog_label="Tycho-2",
                maximum_magnitude=12.0,
                download_if_missing=True,
                downloader=lambda _maximum_magnitude: downloaded_objects,
                minimum_magnitude=9.4,
            )
        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0].name, "TYC 1-2-3")
        self.assertAlmostEqual(achieved, 12.0)


class SkyAtlasGaiaDownloadTest(unittest.TestCase):
    def test_gaia_csv_row_is_parsed_into_star_object(self) -> None:
        from photometry_app.core.sky_atlas import _gaia_object_from_csv_row

        sky_object = _gaia_object_from_csv_row(
            {
                "source_id": "123",
                "ra": "10.5",
                "dec": "-20.25",
                "phot_g_mean_mag": "13.2",
                "bp_rp": "0.8",
            },
            1,
        )
        self.assertIsNotNone(sky_object)
        self.assertEqual(sky_object.name, "Gaia DR3 123")
        self.assertEqual(sky_object.catalog, "Gaia DR3")
        self.assertAlmostEqual(sky_object.ra_deg, 10.5)
        self.assertAlmostEqual(sky_object.magnitude or 0.0, 13.2)

    def test_gaia_stream_download_can_be_cancelled(self) -> None:
        from photometry_app.core.sky_atlas import _download_gaia_star_objects_from_flathub

        def cancel() -> None:
            raise SkyAtlasCatalogCancelled()

        with self.assertRaises(SkyAtlasCatalogCancelled):
            _download_gaia_star_objects_from_flathub(
                13.0,
                minimum_magnitude=12.5,
                cancel_check=cancel,
            )

    def test_gaia_stream_download_parses_flathub_csv(self) -> None:
        from photometry_app.core.sky_atlas import _download_gaia_star_objects_from_flathub

        csv_text = (
            "source_id,ra,dec,phot_g_mean_mag,bp_rp\n"
            "123,10.5,-20.25,13.2,0.8\n"
        )

        class _FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def iter_lines(self, decode_unicode: bool = False):
                return iter(csv_text.splitlines())

            def close(self) -> None:
                return None

        with (
            patch("photometry_app.core.sky_atlas._count_gaia_flathub_stars", return_value=1),
            patch("photometry_app.core.sky_atlas.requests.get", return_value=_FakeResponse()) as get,
        ):
            objects, records = _download_gaia_star_objects_from_flathub(13.5, minimum_magnitude=12.5)

        get.assert_called_once()
        self.assertIn("/data/csv", get.call_args.args[0])
        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0].name, "Gaia DR3 123")
        self.assertEqual(records[0][0], 123)
        self.assertAlmostEqual(objects[0].magnitude or 0.0, 13.2)

    def test_gaia_npy_cache_round_trip(self) -> None:
        from photometry_app.core.sky_atlas import (
            _gaia_npy_cache_path,
            _load_cached_gaia_npy,
            _store_cached_gaia_npy,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = _gaia_npy_cache_path(Path(temp_dir), 13.5)
            _store_cached_gaia_npy(cache_path, [(123, 10.5, -20.25, 13.2, 0.8)])
            loaded = _load_cached_gaia_npy(cache_path)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded[0].name, "Gaia DR3 123")
        self.assertAlmostEqual(loaded[0].ra_deg, 10.5)
        self.assertAlmostEqual(loaded[0].magnitude or 0.0, 13.2, places=5)

    def test_load_reports_achieved_magnitude_from_cached_catalog(self) -> None:
        from photometry_app.core.sky_atlas import load_sky_atlas_objects

        downloaded_objects = (
            SkyAtlasObject(
                name="HIP 12345",
                object_type="Star",
                ra_deg=12.34,
                dec_deg=-54.32,
                magnitude=6.2,
                catalog="Hipparcos",
                searchable=False,
                label_visible=False,
                selectable=False,
            ),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_root = Path(temp_dir)
            with patch(
                "photometry_app.core.sky_atlas._download_hipparcos_star_objects",
                return_value=downloaded_objects,
            ):
                load_scientific_sky_atlas_star_objects(
                    cache_root,
                    maximum_magnitude=8.5,
                    download_if_missing=True,
                )
            achieved: list[float] = []
            loaded = load_sky_atlas_objects(
                cache_root,
                maximum_scientific_magnitude=13.0,
                download_if_missing=False,
                star_magnitude_achieved=achieved,
            )
        self.assertTrue(any(item.name == "HIP 12345" for item in loaded))
        self.assertEqual(achieved, [8.5])


class SkyAtlasDeepSkyFilterTest(unittest.TestCase):
    def test_filter_keeps_stars_and_enabled_catalogs_only(self) -> None:
        objects = load_local_sky_atlas_objects()
        messier_only = filter_sky_atlas_objects_by_deep_sky_catalogs(objects, {"Messier"})
        catalogs = {item.catalog for item in messier_only if item.object_type.casefold() != "star"}
        self.assertTrue(catalogs.issubset({"Messier", "Local"}))
        self.assertTrue(any(item.catalog == "Messier" for item in messier_only))
        self.assertFalse(any(item.catalog == "NGC" for item in messier_only))

    def test_packaged_messier_catalog_includes_m32(self) -> None:
        from photometry_app.core.sky_atlas import load_sky_atlas_objects, search_sky_atlas_objects

        objects = load_sky_atlas_objects(None, enabled_deep_sky_catalogs={"Messier"})
        matches = search_sky_atlas_objects(objects, "M32", limit=5)
        self.assertTrue(matches)
        self.assertTrue(any(item.name == "M32" or "M32" in item.aliases for item in matches))

    def test_deep_sky_catalog_cache_round_trip(self) -> None:
        downloaded = (
            SkyAtlasObject(
                name="NGC 7000",
                object_type="Nebula",
                ra_deg=314.0,
                dec_deg=44.0,
                magnitude=4.0,
                catalog="NGC",
            ),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_root = Path(temp_dir)
            with patch(
                "photometry_app.core.sky_atlas._download_deep_sky_catalog_objects",
                return_value=downloaded,
            ) as download_catalog:
                loaded = load_sky_atlas_deep_sky_objects(
                    cache_root,
                    enabled_catalogs={"NGC"},
                    download_if_missing=True,
                )
            download_catalog.assert_called_once_with("NGC")
            self.assertEqual(loaded[0].name, "NGC 7000")
            with patch("photometry_app.core.sky_atlas._download_deep_sky_catalog_objects") as download_again:
                cached = load_sky_atlas_deep_sky_objects(
                    cache_root,
                    enabled_catalogs={"NGC"},
                    download_if_missing=True,
                )
            download_again.assert_not_called()
            self.assertEqual(cached[0].name, "NGC 7000")

    def test_discovery_catalogs_are_optional_and_normalized(self) -> None:
        self.assertIn("HASH PN", SKY_ATLAS_DEEP_SKY_CATALOG_NAMES)
        self.assertIn("Barnard", SKY_ATLAS_DEEP_SKY_CATALOG_NAMES)
        self.assertIn("Sharpless", SKY_ATLAS_DEEP_SKY_CATALOG_NAMES)
        self.assertIn("RCW", SKY_ATLAS_DEEP_SKY_CATALOG_NAMES)
        enabled = normalize_sky_atlas_deep_sky_catalogs({"hash pn", "barnard", "sharpless", "rcw"})
        self.assertEqual(enabled, frozenset({"HASH PN", "Barnard", "Sharpless", "RCW"}))

    def test_hash_pn_row_uses_usual_name_and_keeps_hash_alias(self) -> None:
        table = Table(
            rows=[(501, "060.8-03.6", 299.90158, 22.72111, "NGC 6853", "T")],
            names=("HASH", "PNG", "RAJ2000", "DEJ2000", "Name", "PNstat"),
        )
        sky_object = _hash_pn_object_from_row(table[0])
        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.name, "NGC 6853")
        self.assertEqual(sky_object.catalog, "HASH PN")
        self.assertEqual(sky_object.object_type, "Planetary Nebula")
        self.assertIn("HASH 501", sky_object.aliases)
        self.assertIn("PN G060.8-03.6", sky_object.aliases)
        self.assertIn("True PN", sky_object.description)

    def test_hash_pn_keeps_amateur_discoverer_names_searchable(self) -> None:
        table = Table(
            rows=[(22331, "175.0-46.0", 40.0, 10.0, "StDr 1", "C")],
            names=("HASH", "PNG", "RAJ2000", "DEJ2000", "Name", "PNstat"),
        )
        sky_object = _hash_pn_object_from_row(table[0])
        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.name, "StDr 1")
        self.assertTrue(sky_atlas_object_has_given_name(sky_object))
        self.assertIn("StDr-1", sky_object.aliases)
        self.assertIn("StDr1", sky_object.aliases)
        self.assertIn("stdr-1", sky_object.search_text)

    def test_png_only_hash_object_is_not_treated_as_given_name(self) -> None:
        table = Table(
            rows=[(9, "000.0+00.0", 10.0, 20.0, "", "T")],
            names=("HASH", "PNG", "RAJ2000", "DEJ2000", "Name", "PNstat"),
        )
        sky_object = _hash_pn_object_from_row(table[0])
        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.name, "PN G000.0+00.0")
        self.assertFalse(sky_atlas_object_has_given_name(sky_object))

    def test_literature_links_use_usual_name_and_include_ads(self) -> None:
        table = Table(
            rows=[(22331, "175.0-46.0", 40.0, 10.0, "StDr 1", "C")],
            names=("HASH", "PNG", "RAJ2000", "DEJ2000", "Name", "PNstat"),
        )
        sky_object = _hash_pn_object_from_row(table[0])
        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_atlas_literature_identifier(sky_object), "StDr 1")
        labels = [link.label for link in sky_atlas_literature_links(sky_object)]
        urls = {link.label: link.url for link in sky_atlas_literature_links(sky_object)}
        self.assertEqual(labels, ["SIMBAD", "ADS", "VizieR", "HASH"])
        self.assertIn("Ident=StDr+1", urls["SIMBAD"])
        self.assertIn("object", urls["ADS"])
        self.assertIn("StDr+1", urls["ADS"])
        self.assertIn("V/163/pnmain", urls["HASH"])
        self.assertIn("HASH=22331", urls["HASH"])

    def test_hash_pn_skips_artefacts(self) -> None:
        table = Table(
            rows=[(9, "000.0+00.0", 10.0, 20.0, "", "Q")],
            names=("HASH", "PNG", "RAJ2000", "DEJ2000", "Name", "PNstat"),
        )
        self.assertIsNone(_hash_pn_object_from_row(table[0]))

    def test_barnard_and_sharpless_rows_use_catalog_names(self) -> None:
        barnard = Table(
            rows=[("33", 85.2458, -2.4583)],
            names=("Barn", "RAJ2000", "DEJ2000"),
        )
        barnard_object = _barnard_object_from_row(barnard[0])
        self.assertIsNotNone(barnard_object)
        assert barnard_object is not None
        self.assertEqual(barnard_object.name, "Barnard 33")
        self.assertIn("B33", barnard_object.aliases)

        sharpless = Table(
            rows=[(101, 20.0, 0.0)],
            names=("Sh2", "GLon", "GLat"),
        )
        sharpless_object = _sharpless_object_from_row(sharpless[0])
        self.assertIsNotNone(sharpless_object)
        assert sharpless_object is not None
        self.assertEqual(sharpless_object.name, "Sh2-101")
        self.assertEqual(sharpless_object.object_type, "HII Region")

    def test_rcw_row_uses_catalog_number(self) -> None:
        table = Table(
            rows=[(38, 134.8, -47.5)],
            names=("RCW", "RAJ2000", "DEJ2000"),
        )
        sky_object = _rcw_object_from_row(table[0])
        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.name, "RCW 38")
        self.assertEqual(sky_object.catalog, "RCW")

    def test_search_includes_disabled_catalogs_and_ranks_exact_names_first(self) -> None:
        displayed = filter_sky_atlas_objects_by_deep_sky_catalogs(
            load_local_sky_atlas_objects(),
            {"Messier"},
        )
        hidden = SkyAtlasObject(
            name="StDr 1",
            object_type="Planetary Nebula",
            ra_deg=40.0,
            dec_deg=10.0,
            magnitude=None,
            catalog="HASH PN",
            aliases=("StDr-1", "HASH 22331"),
        )
        with patch(
            "photometry_app.core.sky_atlas.load_sky_atlas_deep_sky_objects",
            return_value=(hidden,),
        ):
            search_objects = load_sky_atlas_search_objects(
                Path("."),
                displayed_objects=displayed,
            )
        self.assertFalse(any(item.catalog == "HASH PN" for item in displayed))
        matches = search_sky_atlas_objects(
            search_objects,
            "StDr-1",
            limit=8,
            require_searchable=False,
            apply_magnitude_limits=False,
        )
        self.assertTrue(matches)
        self.assertEqual(matches[0].name, "StDr 1")
        self.assertEqual(format_sky_atlas_search_result_title(matches[0]), "Planetary Nebula  StDr 1")

        andromeda_matches = search_sky_atlas_objects(search_objects, "andromeda", limit=5)
        self.assertTrue(andromeda_matches)
        self.assertEqual(andromeda_matches[0].name, "Andromeda Galaxy")

    def test_one_letter_queries_do_not_scan_search_text(self) -> None:
        catalog_star = SkyAtlasObject(
            name="HIP 1",
            object_type="Star",
            ra_deg=10.0,
            dec_deg=20.0,
            magnitude=6.0,
            catalog="Hipparcos",
            constellation="Andromeda",
        )
        self.assertEqual(search_sky_atlas_objects((catalog_star,), "a", limit=5), ())
        named = SkyAtlasObject(
            name="Altair",
            object_type="Star",
            ra_deg=297.7,
            dec_deg=8.9,
            magnitude=0.8,
            catalog="Bright Star",
        )
        matches = search_sky_atlas_objects((catalog_star, named), "a", limit=5)
        self.assertEqual([item.name for item in matches], ["Altair"])


class SkyAtlasSettingsDialogCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_settings_dialog_lists_discovery_catalogs(self) -> None:
        dialog = SkyAtlasSettingsDialog(SkyAtlasDisplaySettings())
        self.assertIn("HASH PN", dialog._catalog_checks)
        self.assertIn("Barnard", dialog._catalog_checks)
        self.assertIn("Sharpless", dialog._catalog_checks)
        self.assertIn("RCW", dialog._catalog_checks)
        self.assertFalse(dialog._catalog_checks["HASH PN"].isChecked())
        dialog._catalog_checks["HASH PN"].setChecked(True)
        dialog._catalog_checks["Barnard"].setChecked(True)
        enabled = dialog.current_settings().enabled_deep_sky_catalogs()
        self.assertIn("HASH PN", enabled)
        self.assertIn("Barnard", enabled)
        self.assertIn("Messier", enabled)
        dialog.close()


class SkyAtlasSearchDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_dialog_lists_type_prefixed_results_by_relevance(self) -> None:
        from photometry_app.ui.sky_atlas_search_dialog import SkyAtlasSearchDialog

        sirius = SkyAtlasObject(
            name="Sirius",
            object_type="Star",
            ra_deg=101.2872,
            dec_deg=-16.7161,
            magnitude=-1.46,
            catalog="Bright Star",
        )
        nebula = SkyAtlasObject(
            name="Sirius Nebula",
            object_type="Nebula",
            ra_deg=100.0,
            dec_deg=-17.0,
            magnitude=8.0,
            catalog="Local",
        )
        dialog = SkyAtlasSearchDialog(lambda query: search_sky_atlas_objects((nebula, sirius), query))
        dialog.set_query("sirius")
        self.assertEqual([item.name for item in dialog.results], ["Sirius", "Sirius Nebula"])
        self.assertIn("Star  Sirius", dialog._results_list.item(0).text())
        self.assertTrue(dialog._results_list.item(0).text().startswith("Star"))
        self.assertEqual(dialog._results_list.currentRow(), 0)
        selected = dialog._results_list.palette().color(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.HighlightedText,
        )
        highlight = dialog._results_list.palette().color(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Highlight,
        )
        self.assertGreater(selected.lightness(), 220)
        self.assertLess(highlight.lightness(), 90)
        self.assertIn("item:selected:!active", dialog.styleSheet())
        self.assertIn("color: #f8fbff", dialog.styleSheet())
        dialog.close()

    def test_typing_shows_searching_without_blocking_on_the_callback(self) -> None:
        from photometry_app.ui.sky_atlas_search_dialog import SkyAtlasSearchDialog

        calls: list[str] = []

        def _search(query: str) -> tuple[SkyAtlasObject, ...]:
            calls.append(query)
            return ()

        dialog = SkyAtlasSearchDialog(_search)
        dialog.search_input.setText("a")
        self.assertEqual(dialog._status_label.text(), "Searching…")
        self.assertEqual(dialog.results, ())
        self.assertEqual(calls, [])
        dialog.close()


if __name__ == "__main__":
    unittest.main()
