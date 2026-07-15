from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from photometry_app.core.sky_atlas import (
    SkyAtlasObject,
    _load_packaged_star_name_aliases,
    _resolve_sky_atlas_name_and_aliases,
    filter_sky_atlas_objects_by_deep_sky_catalogs,
    load_local_sky_atlas_objects,
    load_scientific_sky_atlas_star_objects,
    load_sky_atlas_deep_sky_objects,
)


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


if __name__ == "__main__":
    unittest.main()
