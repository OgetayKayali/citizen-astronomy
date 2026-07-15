from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.table import Table

from photometry_app.core.models import SolvedField
from photometry_app.core.settings import AppSettings
from photometry_app.core.solar_system import SolarSystemDetection, SolarSystemSearchResult
from photometry_app.core.sky_explorer import (
    SkyExplorerCorner,
    SkyExplorerFieldFootprint,
    SkyExplorerLayerSummary,
    SkyExplorerObject,
    _catalog_designation_key,
    _query_hyperleda_galaxy_objects,
    _query_simbad_objects,
    _filter_sky_explorer_objects_by_magnitude,
    _sky_explorer_object_from_barnard_row,
    _ngc2000_type_label,
    _sky_explorer_summaries_for_filtered_objects,
    _sky_explorer_object_from_sharpless_row,
    _sky_explorer_object_from_hyperleda_row,
    _sky_explorer_object_from_ngc2000_row,
    _sky_explorer_object_from_simbad_row,
    _sky_explorer_object_from_vdb_row,
    _simbad_query_regions,
    _solar_system_object_from_result,
    explore_sky_image,
    sky_explorer_object_type_key_for_object,
    sky_explorer_object_type_key_for_catalog_type,
    sky_explorer_object_type_keys_for_object,
    sky_explorer_query_layers_for_object_types,
)


class SkyExplorerTest(unittest.TestCase):
    def test_planetary_nebula_inherits_simple_nebula_category(self) -> None:
        sky_object = SkyExplorerObject(
            "deep_sky",
            "simbad",
            "PN G123.4+56.7",
            "PN G123.4+56.7",
            "Planetary Nebula",
            0.0,
            0.0,
            0.0,
            0.0,
            None,
            0.0,
            "PN G123.4+56.7",
            {"catalog_type": "PN"},
        )

        object_type_keys = sky_explorer_object_type_keys_for_object(sky_object)

        self.assertIn("planetary_nebula", object_type_keys)
        self.assertIn("emission_nebula", object_type_keys)

    def test_final_magnitude_filter_preserves_deep_sky_known_magnitude_objects(self) -> None:
        bright_deep_sky = SkyExplorerObject(
            "deep_sky",
            "simbad",
            "NGC 2244",
            "NGC 2244",
            "Open Cluster",
            0.0,
            0.0,
            0.0,
            0.0,
            4.8,
            0.0,
            "NGC 2244",
        )
        faint_deep_sky = SkyExplorerObject(
            "deep_sky",
            "simbad",
            "2MASS J06301998+0452397",
            "2MASS J06301998+0452397",
            "Star",
            0.0,
            0.0,
            0.0,
            0.0,
            14.62,
            0.0,
            "2MASS J063019...",
        )
        unknown_nebula = SkyExplorerObject(
            "deep_sky",
            "simbad",
            "Sh2-280",
            "Sh2-280",
            "HII Region",
            0.0,
            0.0,
            0.0,
            0.0,
            None,
            0.0,
            "Sh2-280",
        )
        faint_variable = SkyExplorerObject(
            "variable_stars",
            "vsx",
            "VSX faint",
            "VSX faint",
            "Variable Star",
            0.0,
            0.0,
            0.0,
            0.0,
            10.5,
            0.0,
            "VSX faint",
        )

        filtered_objects = _filter_sky_explorer_objects_by_magnitude(
            (bright_deep_sky, faint_deep_sky, unknown_nebula, faint_variable),
            maximum_magnitude=8.0,
        )

        self.assertEqual([item.source_id for item in filtered_objects], ["NGC 2244", "2MASS J06301998+0452397", "Sh2-280"])

        summaries = _sky_explorer_summaries_for_filtered_objects(
            (
                SkyExplorerLayerSummary("deep_sky", "Deep Sky", 3, 3),
                SkyExplorerLayerSummary("variable_stars", "Variable Stars", 1, 1),
            ),
            filtered_objects,
        )

        summary_by_layer = {summary.layer_key: summary for summary in summaries}
        self.assertEqual(summary_by_layer["deep_sky"].displayed_count, 3)
        self.assertEqual(summary_by_layer["deep_sky"].returned_count, 3)
        self.assertEqual(summary_by_layer["variable_stars"].displayed_count, 0)

    def test_final_magnitude_filter_can_exclude_objects_without_magnitude(self) -> None:
        bright_deep_sky = SkyExplorerObject(
            "deep_sky",
            "simbad",
            "NGC 2244",
            "NGC 2244",
            "Open Cluster",
            0.0,
            0.0,
            0.0,
            0.0,
            4.8,
            0.0,
            "NGC 2244",
        )
        unknown_nebula = SkyExplorerObject(
            "deep_sky",
            "simbad",
            "Sh2-280",
            "Sh2-280",
            "HII Region",
            0.0,
            0.0,
            0.0,
            0.0,
            None,
            0.0,
            "Sh2-280",
        )

        filtered_objects = _filter_sky_explorer_objects_by_magnitude(
            (bright_deep_sky, unknown_nebula),
            maximum_magnitude=8.0,
            exclude_unknown_magnitude=True,
        )

        self.assertEqual([item.source_id for item in filtered_objects], ["NGC 2244"])

    def test_final_magnitude_filter_keeps_faint_named_galaxies_for_galaxy_annotation_controls(self) -> None:
        named_galaxy = SkyExplorerObject(
            "deep_sky",
            "simbad",
            "NGC 7319",
            "NGC 7319",
            "Sy2",
            0.0,
            0.0,
            0.0,
            0.0,
            13.53,
            0.0,
            "NGC 7319",
        )
        faint_variable = SkyExplorerObject(
            "variable_stars",
            "vsx",
            "VSX faint",
            "VSX faint",
            "Variable Star",
            0.0,
            0.0,
            0.0,
            0.0,
            13.53,
            0.0,
            "VSX faint",
        )

        filtered_objects = _filter_sky_explorer_objects_by_magnitude(
            (named_galaxy, faint_variable),
            maximum_magnitude=10.0,
        )

        self.assertEqual([item.source_id for item in filtered_objects], ["NGC 7319"])

    def test_query_layers_follow_selected_object_types(self) -> None:
        self.assertEqual(
            sky_explorer_query_layers_for_object_types(("sy1", "xb*", "asteroid_comet", "star")),
            ("deep_sky", "general_objects", "solar_system", "gaia_stars"),
        )

    def test_simbad_classifier_maps_supernova_remnant_type(self) -> None:
        sky_object = SkyExplorerObject(
            layer_key="deep_sky",
            catalog="simbad",
            source_id="SNR G120.1+1.4",
            name="Tycho Supernova Remnant",
            object_type="SNR",
            ra_deg=0.0,
            dec_deg=0.0,
            pixel_x=0.0,
            pixel_y=0.0,
            magnitude=None,
            angular_distance_arcmin=0.0,
            short_label="Tycho SNR",
            metadata={"object_type": "SNR"},
        )

        self.assertEqual(sky_explorer_object_type_key_for_object(sky_object), "supernova_remnant")

    def test_simbad_type_keys_include_exact_code_and_parents(self) -> None:
        sky_object = SkyExplorerObject(
            layer_key="deep_sky",
            catalog="simbad",
            source_id="NGC 1275",
            name="NGC 1275",
            object_type="Sy1",
            ra_deg=0.0,
            dec_deg=0.0,
            pixel_x=0.0,
            pixel_y=0.0,
            magnitude=None,
            angular_distance_arcmin=0.0,
            short_label="NGC 1275",
            metadata={"object_type": "Sy1"},
        )

        keys = set(sky_explorer_object_type_keys_for_object(sky_object))

        self.assertIn("sy1", keys)
        self.assertIn("syg", keys)
        self.assertIn("agn", keys)
        self.assertIn("galaxy", keys)

    def test_possible_active_galaxy_type_inherits_active_galaxy_and_galaxy_categories(self) -> None:
        sky_object = SkyExplorerObject(
            layer_key="deep_sky",
            catalog="simbad",
            source_id="NGC 7320C",
            name="NGC 7320C",
            object_type="AG?",
            ra_deg=0.0,
            dec_deg=0.0,
            pixel_x=0.0,
            pixel_y=0.0,
            magnitude=17.0,
            angular_distance_arcmin=0.0,
            short_label="NGC 7320C",
            metadata={"object_type": "AG?"},
        )

        keys = set(sky_explorer_object_type_keys_for_object(sky_object))

        self.assertEqual(sky_explorer_object_type_key_for_object(sky_object), "active_galactic_nucleus")
        self.assertIn("ag?", keys)
        self.assertIn("active_galactic_nucleus", keys)
        self.assertIn("galaxy", keys)

    def test_structural_galaxy_group_does_not_match_broad_galaxy_filter(self) -> None:
        sky_object = SkyExplorerObject(
            layer_key="deep_sky",
            catalog="simbad",
            source_id="[CHM2007] HDC 1198",
            name="[CHM2007] HDC 1198",
            object_type="GrG",
            ra_deg=0.0,
            dec_deg=0.0,
            pixel_x=0.0,
            pixel_y=0.0,
            magnitude=None,
            angular_distance_arcmin=0.0,
            short_label="HDC 1198",
            metadata={"object_type": "GrG"},
        )

        keys = set(sky_explorer_object_type_keys_for_object(sky_object))

        self.assertEqual(sky_explorer_object_type_key_for_object(sky_object), "galaxy_group")
        self.assertIn("galaxy_group", keys)
        self.assertIn("grg", keys)
        self.assertNotIn("galaxy", keys)
        self.assertNotIn("g", keys)

    def test_galaxy_in_group_remains_individual_galaxy_not_group_filter(self) -> None:
        sky_object = SkyExplorerObject(
            layer_key="deep_sky",
            catalog="simbad",
            source_id="NGC 7331",
            name="NGC 7331",
            object_type="GiG",
            ra_deg=0.0,
            dec_deg=0.0,
            pixel_x=0.0,
            pixel_y=0.0,
            magnitude=None,
            angular_distance_arcmin=0.0,
            short_label="NGC 7331",
            metadata={"object_type": "GiG"},
        )

        keys = set(sky_explorer_object_type_keys_for_object(sky_object))

        self.assertEqual(sky_explorer_object_type_key_for_object(sky_object), "galaxy")
        self.assertIn("galaxy", keys)
        self.assertIn("g", keys)
        self.assertIn("gig", keys)
        self.assertNotIn("galaxy_group", keys)

    def test_simbad_group_row_keeps_main_group_name_instead_of_embedded_ngc_alias(self) -> None:
        table = Table(
            rows=[(339.656462, 35.356669, "HK NGC 7331  40", "HK NGC 7331  40|NGC 7331", "GrG")],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE"),
        )
        solved_field = SolvedField(
            center_ra_deg=339.6,
            center_dec_deg=35.3,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(339.6 * u.deg, 35.3 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        sky_object = _sky_explorer_object_from_simbad_row(
            table[0],
            layer_key="deep_sky",
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )

        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.name, "HK NGC 7331 40")
        self.assertEqual(sky_object.object_type, "GrG")

    def test_ngc2000_galaxy_code_maps_to_simple_galaxy_type(self) -> None:
        sky_object = SkyExplorerObject(
            layer_key="deep_sky",
            catalog="ngc2000",
            source_id="NGC 7331",
            name="NGC 7331",
            object_type="Galaxy",
            ra_deg=0.0,
            dec_deg=0.0,
            pixel_x=0.0,
            pixel_y=0.0,
            magnitude=10.4,
            angular_distance_arcmin=0.0,
            short_label="NGC 7331",
            metadata={"catalog_type": "GX", "object_type": "Galaxy"},
        )

        self.assertEqual(sky_explorer_object_type_key_for_object(sky_object), "galaxy")
        self.assertIn("galaxy", sky_explorer_object_type_keys_for_object(sky_object))

    def test_ngc2000_row_projects_deep_sky_object_for_m46_style_entry(self) -> None:
        table = Table(
            rows=[("2437", "OC", "07 41.8", "-14 49", "27.0", "6.1", "Open cluster")],
            names=("Name", "Type", "RAB2000", "DEB2000", "size", "mag", "Desc"),
        )
        solved_field = SolvedField(
            center_ra_deg=115.0,
            center_dec_deg=-14.8,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(115.0 * u.deg, -14.8 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        sky_object = _sky_explorer_object_from_ngc2000_row(
            table[0],
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )

        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.name, "NGC 2437")
        self.assertEqual(sky_object.object_type, "Open Cluster")
        self.assertEqual(sky_object.catalog, "ngc2000")
        self.assertEqual(sky_object.pixel_x, 500.0)
        self.assertEqual(sky_object.pixel_y, 400.0)

    def test_ngc2000_row_expands_ic_prefix_and_classifies_nb_as_nebula(self) -> None:
        table = Table(
            rows=[("I402", "NB", "05 06.3", "-09 08", "", "", "Nebula")],
            names=("Name", "Type", "RAB2000", "DEB2000", "size", "mag", "Desc"),
        )
        solved_field = SolvedField(
            center_ra_deg=76.5,
            center_dec_deg=-9.1,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(76.5 * u.deg, -9.1 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        sky_object = _sky_explorer_object_from_ngc2000_row(
            table[0],
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )

        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.name, "IC 402")
        self.assertEqual(sky_object.short_label, "IC 402")
        self.assertEqual(sky_object.object_type, "Nebula")
        self.assertEqual(sky_explorer_object_type_key_for_object(sky_object), "nebula")
        self.assertIn("nebula", sky_explorer_object_type_keys_for_object(sky_object))
        self.assertNotEqual(sky_explorer_object_type_key_for_object(sky_object), "other_deep_sky")

    def test_ngc2000_type_label_maps_planetary_nebula_code(self) -> None:
        self.assertEqual(_ngc2000_type_label("Pl"), "Planetary Nebula")

    def test_sharpless_row_projects_hii_region(self) -> None:
        table = Table(
            rows=[(279, 208.5, -19.1, 20)],
            names=("Sh2", "GLon", "GLat", "Diam"),
        )
        solved_field = SolvedField(
            center_ra_deg=84.0,
            center_dec_deg=-5.0,
            radius_deg=0.7,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(84.0 * u.deg, -5.0 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        sky_object = _sky_explorer_object_from_sharpless_row(
            table[0],
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )

        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.catalog, "sharpless")
        self.assertEqual(sky_object.name, "Sh2-279")
        self.assertEqual(sky_object.object_type, "HII Region")
        self.assertEqual(sky_object.metadata["catalog_size_arcmin"], 20.0)

    def test_barnard_row_projects_dark_nebula(self) -> None:
        table = Table(
            rows=[("33", "05 40 59.0", "-02 27 30", 6.0)],
            names=("Barn", "_RA.icrs", "_DE.icrs", "Diam"),
        )
        solved_field = SolvedField(
            center_ra_deg=85.2,
            center_dec_deg=-2.5,
            radius_deg=0.6,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(85.2 * u.deg, -2.5 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        sky_object = _sky_explorer_object_from_barnard_row(
            table[0],
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )

        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.catalog, "barnard")
        self.assertEqual(sky_object.name, "B 33")
        self.assertEqual(sky_object.object_type, "Dark Nebula")
        self.assertEqual(sky_object.metadata["catalog_size_arcmin"], 6.0)

    def test_vdb_row_projects_reflection_nebula(self) -> None:
        table = Table(
            rows=[(1, 2.69319, 58.76952, 8.6, 4.3, 1.1, "I")],
            names=("VdB", "_RA", "_DE", "Vmag", "BRadMax", "RRadMax", "Type"),
        )
        solved_field = SolvedField(
            center_ra_deg=2.7,
            center_dec_deg=58.7,
            radius_deg=0.6,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(2.7 * u.deg, 58.7 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        sky_object = _sky_explorer_object_from_vdb_row(
            table[0],
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )

        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.catalog, "vdb")
        self.assertEqual(sky_object.name, "VdB 1")
        self.assertEqual(sky_object.object_type, "Reflection Nebula")
        self.assertAlmostEqual(sky_object.metadata["catalog_size_arcmin"], 8.6)

    def test_simbad_row_with_sexagesimal_coordinates_is_projected_into_sky_object(self) -> None:
        table = Table(
            rows=[("07 41 46.8", "-14 48 36", "Cl* NGC 2437", "M 46|NGC 2437", "OpC", 6.1)],
            names=("RA", "DEC", "MAIN_ID", "IDS", "OTYPE", "V"),
        )
        solved_field = SolvedField(
            center_ra_deg=115.0,
            center_dec_deg=-14.8,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(115.0 * u.deg, -14.8 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        sky_object = _sky_explorer_object_from_simbad_row(
            table[0],
            layer_key="deep_sky",
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )

        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.layer_key, "deep_sky")
        self.assertEqual(sky_object.name, "M 46")
        self.assertEqual(sky_object.object_type, "OpC")
        self.assertEqual(sky_object.pixel_x, 500.0)
        self.assertEqual(sky_object.pixel_y, 400.0)

    def test_simbad_cluster_member_keeps_member_name_instead_of_parent_alias(self) -> None:
        table = Table(
            rows=[("07 41 42.0", "-14 47 30", "Cl* NGC 2437 12", "M 46|NGC 2437|Cl* NGC 2437 12", "*", 10.4)],
            names=("RA", "DEC", "MAIN_ID", "IDS", "OTYPE", "V"),
        )
        solved_field = SolvedField(
            center_ra_deg=115.0,
            center_dec_deg=-14.8,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(115.0 * u.deg, -14.8 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        sky_object = _sky_explorer_object_from_simbad_row(
            table[0],
            layer_key="general_objects",
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )

        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.name, "Cl* NGC 2437 12")
        self.assertEqual(sky_object.source_id, "Cl* NGC 2437 12")
        self.assertNotEqual(sky_object.name, "M 46")
        self.assertNotEqual(sky_object.name, "NGC 2437")

    def test_simbad_star_row_keeps_stellar_name_instead_of_parent_cluster_alias(self) -> None:
        table = Table(
            rows=[(115.305755, -14.611649, "TYC 5422-1281-1", "M 46|NGC 2437|TYC 5422-1281-1", "*", 9.8)],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V"),
        )
        solved_field = SolvedField(
            center_ra_deg=115.0,
            center_dec_deg=-14.8,
            radius_deg=0.6,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(115.0 * u.deg, -14.8 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        sky_object = _sky_explorer_object_from_simbad_row(
            table[0],
            layer_key="general_objects",
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )

        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.name, "TYC 5422-1281-1")
        self.assertEqual(sky_object.source_id, "TYC 5422-1281-1")
        self.assertEqual(sky_object.short_label, "TYC 5422-1281-1")
        self.assertNotEqual(sky_object.name, "M 46")
        self.assertNotEqual(sky_object.name, "NGC 2437")

    def test_simbad_star_row_prefers_non_gaia_stellar_alias_over_parent_cluster_alias(self) -> None:
        table = Table(
            rows=[(115.823511, -14.832915, "Gaia DR3 305055123456789", "M 46|NGC 2437|TYC 5422-1967-1|Gaia DR3 305055123456789", "*", 9.17)],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V"),
        )
        solved_field = SolvedField(
            center_ra_deg=115.0,
            center_dec_deg=-14.8,
            radius_deg=0.6,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(115.0 * u.deg, -14.8 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        sky_object = _sky_explorer_object_from_simbad_row(
            table[0],
            layer_key="general_objects",
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )

        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.name, "TYC 5422-1967-1")
        self.assertNotEqual(sky_object.name, "M 46")
        self.assertNotEqual(sky_object.name, "NGC 2437")

    def test_simbad_galaxy_row_prefers_ngc_ic_designation_over_messier(self) -> None:
        table = Table(
            rows=[("00 42 44.3", "+41 16 09", "M 31", "M 31|NGC 224|PGC 2557", "G", 3.44)],
            names=("RA", "DEC", "MAIN_ID", "IDS", "OTYPE", "V"),
        )
        solved_field = SolvedField(
            center_ra_deg=10.7,
            center_dec_deg=41.2,
            radius_deg=0.8,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(10.7 * u.deg, 41.2 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        sky_object = _sky_explorer_object_from_simbad_row(
            table[0],
            layer_key="deep_sky",
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )

        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.name, "NGC 224")
        self.assertEqual(sky_object.short_label, "NGC 224")
        self.assertEqual(sky_object.object_type, "G")

    def test_simbad_galaxy_row_prefers_ngc_suffix_over_2mass_and_b2_aliases(self) -> None:
        table = Table(
            rows=[(338.986267, 33.965492, "2MASS J22355674+3357550", "2MASS J22355674+3357550|B2 2233+33|NGC 7318A", "G", None, 13.6)],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V", "B"),
        )
        solved_field = SolvedField(
            center_ra_deg=339.0,
            center_dec_deg=34.0,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(339.0 * u.deg, 34.0 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        sky_object = _sky_explorer_object_from_simbad_row(
            table[0],
            layer_key="deep_sky",
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )

        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.name, "NGC 7318A")
        self.assertEqual(sky_object.short_label, "NGC 7318A")
        self.assertEqual(sky_object.magnitude, 13.6)

    def test_simbad_b2_catalog_identifier_does_not_become_barnard_designation(self) -> None:
        table = Table(
            rows=[(338.92, 33.95, "B2 2233+33", "B2 2233+33|LEDA 69435", "B2", None, 14.2)],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V", "B"),
        )
        solved_field = SolvedField(
            center_ra_deg=339.0,
            center_dec_deg=34.0,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(339.0 * u.deg, 34.0 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        sky_object = _sky_explorer_object_from_simbad_row(
            table[0],
            layer_key="deep_sky",
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )

        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.name, "B2 2233+33")
        self.assertEqual(sky_object.object_type, "G")
        self.assertEqual(sky_object.magnitude, 14.2)
        self.assertIn("galaxy", sky_explorer_object_type_keys_for_object(sky_object))

    def test_hyphenated_minkowski_identifier_does_not_become_messier_designation(self) -> None:
        self.assertIsNone(_catalog_designation_key("M 1-18"))
        self.assertIsNone(_catalog_designation_key("M1-18"))
        self.assertIsNone(_catalog_designation_key("PN M 1-18"))
        self.assertEqual(_catalog_designation_key("M 1"), "M1")
        self.assertEqual(_catalog_designation_key("M1"), "M1")

    def test_simbad_minkowski_planetary_nebula_keeps_hyphenated_name(self) -> None:
        table = Table(
            rows=[(115.33, -14.72, "M 1-18", "M 1-18|PN G231.1+03.9", "PN", None)],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V"),
        )
        solved_field = SolvedField(
            center_ra_deg=115.0,
            center_dec_deg=-14.8,
            radius_deg=0.6,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(115.0 * u.deg, -14.8 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        sky_object = _sky_explorer_object_from_simbad_row(
            table[0],
            layer_key="deep_sky",
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )

        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.name, "M 1-18")
        self.assertEqual(sky_object.short_label, "M 1-18")
        self.assertNotEqual(sky_object.name, "M 1")
        self.assertEqual(sky_object.object_type, "PN")
        self.assertIn("planetary_nebula", sky_explorer_object_type_keys_for_object(sky_object))

    def test_simbad_row_with_lowercase_decimal_coordinates_uses_degrees(self) -> None:
        table = Table(
            rows=[(76.93587390442, -7.96919960149, "NGC  1799", "NGC  1799|LEDA   16783", "Sy2", None)],
            names=("ra", "dec", "main_id", "ids", "otype", "V"),
        )
        solved_field = SolvedField(
            center_ra_deg=76.9,
            center_dec_deg=-8.0,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(76.9 * u.deg, -8.0 * u.deg)
        seen_coordinates: list[tuple[float, float]] = []

        def world_to_pixel_values(ra_deg: float, dec_deg: float) -> tuple[float, float]:
            seen_coordinates.append((ra_deg, dec_deg))
            return 500.0, 400.0

        wcs = SimpleNamespace(world_to_pixel_values=world_to_pixel_values)

        sky_object = _sky_explorer_object_from_simbad_row(
            table[0],
            layer_key="deep_sky",
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )

        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertAlmostEqual(sky_object.ra_deg, 76.93587390442)
        self.assertAlmostEqual(sky_object.dec_deg, -7.96919960149)
        self.assertAlmostEqual(seen_coordinates[0][0], 76.93587390442)
        self.assertEqual(sky_object.name, "NGC 1799")

    def test_simbad_row_prefers_sharpless_designation_over_ic_alias(self) -> None:
        table = Table(
            rows=[(84.0, -5.0, "IC 434", "IC 434|Sh 2-279", "HII", None)],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V"),
        )
        solved_field = SolvedField(
            center_ra_deg=84.0,
            center_dec_deg=-5.0,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(84.0 * u.deg, -5.0 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        sky_object = _sky_explorer_object_from_simbad_row(
            table[0],
            layer_key="deep_sky",
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )

        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.name, "Sh2-279")

    def test_simbad_row_prefers_vdb_designation_over_stellar_alias(self) -> None:
        table = Table(
            rows=[(2.69319, 58.76952, "HD 627", "HD 627|VdB 1", "RNe", 8.6)],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V"),
        )
        solved_field = SolvedField(
            center_ra_deg=2.7,
            center_dec_deg=58.7,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(2.7 * u.deg, 58.7 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        sky_object = _sky_explorer_object_from_simbad_row(
            table[0],
            layer_key="deep_sky",
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )

        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.name, "VdB 1")

    def test_simbad_row_captures_angular_size_metadata(self) -> None:
        table = Table(
            rows=[(10.0, 20.0, "NGC 7331", "NGC 7331|PGC 69327", "G", 10.4, 10.5)],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V", "GALDIM_MAJAXIS"),
        )
        solved_field = SolvedField(
            center_ra_deg=10.0,
            center_dec_deg=20.0,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(10.0 * u.deg, 20.0 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        sky_object = _sky_explorer_object_from_simbad_row(
            table[0],
            layer_key="deep_sky",
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )

        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.metadata["catalog_size_arcmin"], 10.5)

    def test_simbad_row_captures_galaxy_axes_and_position_angle_metadata(self) -> None:
        table = Table(
            rows=[(10.0, 20.0, "NGC 7331", "NGC 7331|PGC 69327", "G", 10.4, 10.5, 3.2, 166.0)],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V", "GALDIM_MAJAXIS", "GALDIM_MINAXIS", "GALDIM_ANGLE"),
        )
        solved_field = SolvedField(
            center_ra_deg=10.0,
            center_dec_deg=20.0,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(10.0 * u.deg, 20.0 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        sky_object = _sky_explorer_object_from_simbad_row(
            table[0],
            layer_key="deep_sky",
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )

        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.metadata["catalog_major_axis_arcmin"], 10.5)
        self.assertEqual(sky_object.metadata["catalog_minor_axis_arcmin"], 3.2)
        self.assertEqual(sky_object.metadata["catalog_position_angle_deg"], 166.0)

    def test_hyperleda_row_projects_galaxy_with_ellipse_metadata(self) -> None:
        table = Table(
            rows=[("69327", "22 37 04.1", "+34 24 56", "G", "SAb", 2.08, 0.28, 166.0, "NGC 7331", "NGC 7331", 10.35, 10.74)],
            names=("PGC", "RAJ2000", "DEJ2000", "OType", "MType", "logD25", "logR25", "PA", "ANames", "Simbad", "Vmag", "Bmag"),
        )
        solved_field = SolvedField(
            center_ra_deg=339.3,
            center_dec_deg=34.4,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(339.3 * u.deg, 34.4 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        sky_object = _sky_explorer_object_from_hyperleda_row(
            table[0],
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )

        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.catalog, "hyperleda")
        self.assertEqual(sky_object.name, "NGC 7331")
        self.assertEqual(sky_object.source_id, "PGC 69327")
        self.assertEqual(sky_object.magnitude, 10.35)
        self.assertGreater(sky_object.metadata["catalog_major_axis_arcmin"], sky_object.metadata["catalog_minor_axis_arcmin"])
        self.assertEqual(sky_object.metadata["catalog_position_angle_deg"], 166.0)

    def test_hyperleda_row_prefers_compact_ic_alias_as_display_name(self) -> None:
        table = Table(
            rows=[("16742", "05 06 14.9", "-09 06 29", "G", "Sc", 1.27, 0.27, 151.0, "IC402                 MCG-2-13-043          UGCA99", "Simbad")],
            names=("PGC", "RAJ2000", "DEJ2000", "OType", "MType", "logD25", "logR25", "PA", "ANames", "Simbad"),
        )
        solved_field = SolvedField(
            center_ra_deg=76.5,
            center_dec_deg=-9.1,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(76.5 * u.deg, -9.1 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        sky_object = _sky_explorer_object_from_hyperleda_row(
            table[0],
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )

        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.name, "IC 402")
        self.assertEqual(sky_object.object_type, "Galaxy")
        self.assertGreater(sky_object.metadata["catalog_major_axis_arcmin"], 1.0)

    def test_hyperleda_row_prefers_mcg_alias_as_display_name(self) -> None:
        table = Table(
            rows=[("16607", "05 02 37.7", "-08 18 06", "G", "SBcd", 1.11, 0.11, 47.0, "MCG-1-13-049          IRAS05000-0826", "Simbad")],
            names=("PGC", "RAJ2000", "DEJ2000", "OType", "MType", "logD25", "logR25", "PA", "ANames", "Simbad"),
        )
        solved_field = SolvedField(
            center_ra_deg=75.65,
            center_dec_deg=-8.3,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(75.65 * u.deg, -8.3 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        sky_object = _sky_explorer_object_from_hyperleda_row(
            table[0],
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )

        self.assertIsNotNone(sky_object)
        assert sky_object is not None
        self.assertEqual(sky_object.name, "MCG-01-13-049")
        self.assertEqual(sky_object.object_type, "Galaxy")
        self.assertGreater(sky_object.metadata["catalog_major_axis_arcmin"], 1.0)

    def test_hyperleda_named_alias_supplement_includes_mcg_and_skips_anonymous_pgc(self) -> None:
        table = Table(
            rows=[
                ("16607", "05 02 37.7", "-08 18 06", "G", "SBcd", 1.11, 0.11, 47.0, "MCG-1-13-049          IRAS05000-0826", "Simbad"),
                ("999999", "05 03 10.0", "-08 12 00", "G", "", 1.0, 0.1, 20.0, "", "Simbad"),
            ],
            names=("PGC", "RAJ2000", "DEJ2000", "OType", "MType", "logD25", "logR25", "PA", "ANames", "Simbad"),
        )
        solved_field = SolvedField(
            center_ra_deg=75.65,
            center_dec_deg=-8.3,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(75.65 * u.deg, -8.3 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        class FakeVizier:
            def __init__(self, *args, **kwargs) -> None:
                return None

            def query_region(self, *args, **kwargs):
                return [table]

        with patch("photometry_app.core.sky_explorer.Vizier", side_effect=FakeVizier):
            objects, returned_count = _query_hyperleda_galaxy_objects(
                solved_field,
                wcs=wcs,
                field_center=field_center,
                require_named_alias=True,
            )

        self.assertEqual(returned_count, 1)
        self.assertEqual([item.name for item in objects], ["MCG-01-13-049"])

    def test_simbad_queries_deep_sky_with_larger_row_budget_than_general_objects(self) -> None:
        general_table = Table(
            rows=[(10.0, 20.0, "HD 123", "HD 123", "V*", 9.8)],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V"),
        )
        crowded_table = Table(
            rows=[
                (10.0, 20.0, "HD 123", "HD 123", "V*", 9.8),
                (10.1, 20.1, "NGC 7331", "NGC 7331|PGC 69327", "G", 10.4),
            ],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V"),
        )
        solved_field = SolvedField(
            center_ra_deg=10.0,
            center_dec_deg=20.0,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(10.0 * u.deg, 20.0 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))
        query_row_limits: list[int] = []

        class FakeSimbad:
            def __init__(self) -> None:
                self.TIMEOUT = None
                self.ROW_LIMIT = 0

            def add_votable_fields(self, *args) -> None:
                return None

            def query_region(self, *args, **kwargs):
                query_row_limits.append(int(self.ROW_LIMIT))
                if int(self.ROW_LIMIT) <= 300:
                    return general_table
                return crowded_table

        with (
            patch("photometry_app.core.sky_explorer.Simbad", side_effect=FakeSimbad),
            patch("photometry_app.core.sky_explorer._query_sharpless_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_barnard_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_vdb_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_ngc2000_objects", return_value=([], 0)),
        ):
            objects, summaries = _query_simbad_objects(
                solved_field,
                wcs=wcs,
                field_center=field_center,
                selected_layers=("deep_sky", "general_objects"),
            )

        self.assertIn(300, query_row_limits)
        self.assertTrue(any(row_limit > 300 for row_limit in query_row_limits))
        object_names = {item.name for item in objects}
        self.assertIn("HD 123", object_names)
        self.assertIn("NGC 7331", object_names)
        summary_by_layer = {summary.layer_key: summary for summary in summaries}
        self.assertEqual(summary_by_layer["general_objects"].displayed_count, 1)
        self.assertEqual(summary_by_layer["deep_sky"].displayed_count, 1)

    def test_simbad_deep_sky_tiling_recovers_rows_missed_by_single_wide_query(self) -> None:
        general_table = Table(
            rows=[(10.0, 20.0, "HD 123", "HD 123", "V*", 9.8)],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V"),
        )
        galaxy_table = Table(
            rows=[(10.1, 20.1, "LEDA 2051985", "LEDA 2051985|MAPS-PP O-778-973630", "G", 16.2)],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V"),
        )
        empty_deep_sky_table = Table(
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V"),
            dtype=(float, float, "U32", "U64", "U8", float),
        )
        solved_field = SolvedField(
            center_ra_deg=10.0,
            center_dec_deg=20.0,
            radius_deg=0.8,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(10.0 * u.deg, 20.0 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))
        deep_sky_query_radii: list[float] = []
        tiled_deep_sky_query_count = 0
        requested_fields: list[tuple[int, tuple[str, ...]]] = []

        class FakeSimbad:
            def __init__(self) -> None:
                self.TIMEOUT = None
                self.ROW_LIMIT = 0

            def add_votable_fields(self, *args) -> None:
                requested_fields.append((int(self.ROW_LIMIT), tuple(str(arg) for arg in args)))
                return None

            def query_region(self, *args, **kwargs):
                nonlocal tiled_deep_sky_query_count
                radius = kwargs.get("radius")
                radius_deg = float(radius.to_value(u.deg)) if radius is not None else 0.0
                if int(self.ROW_LIMIT) <= 300:
                    return general_table
                deep_sky_query_radii.append(radius_deg)
                if radius_deg > 0.34:
                    return empty_deep_sky_table
                tiled_deep_sky_query_count += 1
                if tiled_deep_sky_query_count == 1:
                    return empty_deep_sky_table
                return galaxy_table

        with (
            patch("photometry_app.core.sky_explorer.Simbad", side_effect=FakeSimbad),
            patch("photometry_app.core.sky_explorer._query_hyperleda_galaxy_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_sharpless_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_barnard_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_vdb_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_ngc2000_objects", return_value=([], 0)),
        ):
            objects, summaries = _query_simbad_objects(
                solved_field,
                wcs=wcs,
                field_center=field_center,
                selected_layers=("deep_sky", "general_objects"),
            )

        self.assertGreater(len(deep_sky_query_radii), 1)
        self.assertTrue(all(radius_deg <= 0.34 for radius_deg in deep_sky_query_radii))
        self.assertTrue(any(row_limit <= 300 and "V" in fields and "B" in fields for row_limit, fields in requested_fields))
        self.assertTrue(any(row_limit > 300 and "dim" in fields and "ra(d)" in fields and "dec(d)" in fields for row_limit, fields in requested_fields))
        self.assertFalse(any(row_limit > 300 and ("V" in fields or "B" in fields) for row_limit, fields in requested_fields))
        object_names = {item.name for item in objects}
        self.assertIn("HD 123", object_names)
        self.assertIn("LEDA 2051985", object_names)
        self.assertEqual(sum(1 for item in objects if item.name == "LEDA 2051985"), 1)
        summary_by_layer = {summary.layer_key: summary for summary in summaries}
        self.assertEqual(summary_by_layer["deep_sky"].returned_count, 1)
        self.assertEqual(summary_by_layer["deep_sky"].displayed_count, 1)

    def test_simbad_deep_sky_wide_field_uses_capped_criteria_queries(self) -> None:
        center = SkyCoord(270.0 * u.deg, -24.0 * u.deg)
        wide_regions = _simbad_query_regions(center, radius=3.0 * u.deg, layer_key="deep_sky")

        self.assertLessEqual(len(wide_regions), 9)
        self.assertTrue(all(float(query_radius.to_value(u.deg)) > 0.34 for _query_center, query_radius in wide_regions))

        empty_deep_sky_table = Table(
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V"),
            dtype=(float, float, "U32", "U64", "U8", float),
        )
        solved_field = SolvedField(
            center_ra_deg=270.0,
            center_dec_deg=-24.0,
            radius_deg=3.0,
            width=5000,
            height=3342,
            wcs_path=Path("lagoon_solution.fits"),
        )
        field_center = SkyCoord(270.0 * u.deg, -24.0 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (2500.0, 1671.0))
        query_radii: list[float] = []
        query_criteria: list[str] = []

        class FakeSimbad:
            def __init__(self) -> None:
                self.TIMEOUT = None
                self.ROW_LIMIT = 0

            def add_votable_fields(self, *args) -> None:
                return None

            def query_region(self, *args, **kwargs):
                query_radii.append(float(kwargs["radius"].to_value(u.deg)))
                query_criteria.append(str(kwargs.get("criteria") or ""))
                return empty_deep_sky_table

        with (
            patch("photometry_app.core.sky_explorer.Simbad", side_effect=FakeSimbad),
            patch("photometry_app.core.sky_explorer._query_hyperleda_galaxy_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_sharpless_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_barnard_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_vdb_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_ngc2000_objects", return_value=([], 0)),
        ):
            _query_simbad_objects(
                solved_field,
                wcs=wcs,
                field_center=field_center,
                selected_layers=("deep_sky",),
            )

        self.assertLessEqual(len(query_radii), 9)
        self.assertTrue(all(radius_deg > 0.34 for radius_deg in query_radii))
        self.assertTrue(all("otype IN" in criteria for criteria in query_criteria))
        self.assertTrue(all("'HII'" in criteria and "'G'" in criteria for criteria in query_criteria))

    def test_simbad_general_objects_respect_stellar_magnitude_limit(self) -> None:
        general_table = Table(
            rows=[
                (10.0, 20.0, "HD 123", "HD 123", "V*", 7.8),
                (10.1, 20.1, "HD 456", "HD 456", "V*", 9.4),
                (10.2, 20.2, "HD 789", "HD 789", "V*", None),
            ],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V"),
        )
        deep_sky_table = Table(
            rows=[
                (10.3, 20.3, "NGC 7331", "NGC 7331|PGC 69327", "G", 10.4),
                (10.4, 20.4, "2MASS J06301998+0452397", "2MASS J06301998+0452397|NGC 2244 100", "*", 14.62),
            ],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V"),
        )
        solved_field = SolvedField(
            center_ra_deg=10.0,
            center_dec_deg=20.0,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(10.0 * u.deg, 20.0 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        class FakeSimbad:
            def __init__(self) -> None:
                self.TIMEOUT = None
                self.ROW_LIMIT = 0

            def add_votable_fields(self, *args) -> None:
                return None

            def query_region(self, *args, **kwargs):
                if int(self.ROW_LIMIT) <= 300:
                    return general_table
                return deep_sky_table

        with (
            patch("photometry_app.core.sky_explorer.Simbad", side_effect=FakeSimbad),
            patch("photometry_app.core.sky_explorer._query_hyperleda_galaxy_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_sharpless_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_barnard_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_vdb_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_ngc2000_objects", return_value=([], 0)),
        ):
            objects, summaries = _query_simbad_objects(
                solved_field,
                wcs=wcs,
                field_center=field_center,
                selected_layers=("deep_sky", "general_objects"),
                maximum_stellar_magnitude=8.0,
            )

        object_names = {item.name for item in objects}
        self.assertIn("HD 123", object_names)
        self.assertNotIn("HD 456", object_names)
        self.assertNotIn("HD 789", object_names)
        self.assertNotIn("2MASS J06301998+0452397", object_names)
        self.assertIn("NGC 7331", object_names)
        summary_by_layer = {summary.layer_key: summary for summary in summaries}
        self.assertEqual(summary_by_layer["general_objects"].displayed_count, 1)
        self.assertEqual(summary_by_layer["deep_sky"].displayed_count, 1)

    def test_simbad_queries_can_include_dense_hyperleda_galaxy_supplement(self) -> None:
        general_table = Table(
            rows=[(10.0, 20.0, "HD 123", "HD 123", "V*", 9.8)],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V"),
        )
        solved_field = SolvedField(
            center_ra_deg=10.0,
            center_dec_deg=20.0,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(10.0 * u.deg, 20.0 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))
        supplemental_galaxy = SkyExplorerObject(
            layer_key="deep_sky",
            catalog="hyperleda",
            source_id="PGC 69327",
            name="NGC 7331",
            object_type="Galaxy",
            ra_deg=10.1,
            dec_deg=20.1,
            pixel_x=520.0,
            pixel_y=410.0,
            magnitude=None,
            angular_distance_arcmin=8.5,
            short_label="NGC 7331",
            metadata={
                "catalog_major_axis_arcmin": 10.5,
                "catalog_minor_axis_arcmin": 3.2,
                "catalog_position_angle_deg": 166.0,
            },
        )

        class FakeSimbad:
            def __init__(self) -> None:
                self.TIMEOUT = None
                self.ROW_LIMIT = 0

            def add_votable_fields(self, *args) -> None:
                return None

            def query_region(self, *args, **kwargs):
                return general_table

        with (
            patch("photometry_app.core.sky_explorer.Simbad", side_effect=FakeSimbad),
            patch("photometry_app.core.sky_explorer._query_sharpless_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_barnard_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_vdb_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_ngc2000_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_hyperleda_galaxy_objects", return_value=([supplemental_galaxy], 1)),
        ):
            objects, summaries = _query_simbad_objects(
                solved_field,
                wcs=wcs,
                field_center=field_center,
                selected_layers=("deep_sky", "general_objects"),
                include_dense_galaxy_catalog=True,
            )

        object_names = {item.name for item in objects}
        self.assertIn("NGC 7331", object_names)
        summary_by_layer = {summary.layer_key: summary for summary in summaries}
        self.assertEqual(summary_by_layer["deep_sky"].displayed_count, 1)
        self.assertEqual(summary_by_layer["deep_sky"].returned_count, 1)

    def test_simbad_deep_sky_rows_deduplicate_ngc2000_ic_rows_by_designation(self) -> None:
        simbad_table = Table(
            rows=[(76.55, -9.12, "IC 402", "IC 402", "Neb", None)],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V"),
        )
        solved_field = SolvedField(
            center_ra_deg=76.5,
            center_dec_deg=-9.1,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(76.5 * u.deg, -9.1 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))
        duplicate_ngc2000_object = SkyExplorerObject(
            layer_key="deep_sky",
            catalog="ngc2000",
            source_id="IC 402",
            name="IC 402",
            object_type="Nebula",
            ra_deg=76.575,
            dec_deg=-9.133333,
            pixel_x=550.0,
            pixel_y=430.0,
            magnitude=None,
            angular_distance_arcmin=2.0,
            short_label="IC 402",
            metadata={"catalog_type": "NB", "catalog_description": "Nebula", "catalog_size_arcmin": None},
        )

        class FakeSimbad:
            def __init__(self) -> None:
                self.TIMEOUT = None
                self.ROW_LIMIT = 0

            def add_votable_fields(self, *args) -> None:
                return None

            def query_region(self, *args, **kwargs):
                return simbad_table

        with (
            patch("photometry_app.core.sky_explorer.Simbad", side_effect=FakeSimbad),
            patch("photometry_app.core.sky_explorer._query_hyperleda_galaxy_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_sharpless_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_barnard_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_vdb_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_ngc2000_objects", return_value=([duplicate_ngc2000_object], 1)),
        ):
            objects, summaries = _query_simbad_objects(
                solved_field,
                wcs=wcs,
                field_center=field_center,
                selected_layers=("deep_sky",),
            )

        self.assertEqual([item.catalog for item in objects], ["simbad"])
        self.assertEqual([item.name for item in objects], ["IC 402"])
        summary_by_layer = {summary.layer_key: summary for summary in summaries}
        self.assertEqual(summary_by_layer["deep_sky"].returned_count, 2)
        self.assertEqual(summary_by_layer["deep_sky"].displayed_count, 1)

    def test_simbad_messier_alias_deduplicates_ngc2000_cluster_row(self) -> None:
        simbad_table = Table(
            rows=[(115.445, -14.810, "Cl* NGC 2437", "M 46|NGC 2437", "OpC", 6.1)],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V"),
        )
        solved_field = SolvedField(
            center_ra_deg=115.0,
            center_dec_deg=-14.8,
            radius_deg=0.6,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(115.0 * u.deg, -14.8 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))
        duplicate_ngc2000_object = SkyExplorerObject(
            layer_key="deep_sky",
            catalog="ngc2000",
            source_id="NGC 2437",
            name="NGC 2437",
            object_type="Open Cluster",
            ra_deg=115.45,
            dec_deg=-14.82,
            pixel_x=525.0,
            pixel_y=420.0,
            magnitude=6.1,
            angular_distance_arcmin=5.0,
            short_label="NGC 2437",
            metadata={"catalog_type": "OC", "catalog_description": "Open cluster", "catalog_size_arcmin": 27.0},
        )

        class FakeSimbad:
            def __init__(self) -> None:
                self.TIMEOUT = None
                self.ROW_LIMIT = 0

            def add_votable_fields(self, *args) -> None:
                return None

            def query_region(self, *args, **kwargs):
                return simbad_table

        with (
            patch("photometry_app.core.sky_explorer.Simbad", side_effect=FakeSimbad),
            patch("photometry_app.core.sky_explorer._query_hyperleda_galaxy_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_sharpless_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_barnard_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_vdb_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_ngc2000_objects", return_value=([duplicate_ngc2000_object], 1)),
        ):
            objects, summaries = _query_simbad_objects(
                solved_field,
                wcs=wcs,
                field_center=field_center,
                selected_layers=("deep_sky",),
            )

        self.assertEqual([item.name for item in objects], ["M 46"])
        self.assertEqual(objects[0].metadata["catalog_size_arcmin"], 27.0)
        summary_by_layer = {summary.layer_key: summary for summary in summaries}
        self.assertEqual(summary_by_layer["deep_sky"].returned_count, 2)
        self.assertEqual(summary_by_layer["deep_sky"].displayed_count, 1)

    def test_simbad_deep_sky_duplicate_keeps_supplemental_geometry(self) -> None:
        simbad_table = Table(
            rows=[(41.0, 60.0, "Sh 2-199", "Sh 2-199|IC 1848", "HII", None)],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V"),
        )
        solved_field = SolvedField(
            center_ra_deg=41.0,
            center_dec_deg=60.0,
            radius_deg=0.9,
            width=1200,
            height=900,
            wcs_path=Path("soul.fits"),
        )
        field_center = SkyCoord(41.0 * u.deg, 60.0 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (600.0, 450.0))
        sharpless_duplicate = SkyExplorerObject(
            layer_key="deep_sky",
            catalog="sharpless",
            source_id="Sh2-199",
            name="Sh2-199",
            object_type="HII Region",
            ra_deg=41.0,
            dec_deg=60.0,
            pixel_x=600.0,
            pixel_y=450.0,
            magnitude=None,
            angular_distance_arcmin=0.0,
            short_label="Sh2-199",
            metadata={"catalog_type": "HII", "catalog_size_arcmin": 90.0, "catalog_description": "HII Region"},
        )

        class FakeSimbad:
            def __init__(self) -> None:
                self.TIMEOUT = None
                self.ROW_LIMIT = 0

            def add_votable_fields(self, *args) -> None:
                return None

            def query_region(self, *args, **kwargs):
                return simbad_table

        with (
            patch("photometry_app.core.sky_explorer.Simbad", side_effect=FakeSimbad),
            patch("photometry_app.core.sky_explorer._query_hyperleda_galaxy_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_sharpless_objects", return_value=([sharpless_duplicate], 1)),
            patch("photometry_app.core.sky_explorer._query_barnard_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_vdb_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_ngc2000_objects", return_value=([], 0)),
        ):
            objects, summaries = _query_simbad_objects(
                solved_field,
                wcs=wcs,
                field_center=field_center,
                selected_layers=("deep_sky",),
            )

        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0].catalog, "simbad")
        self.assertEqual(objects[0].metadata["catalog_size_arcmin"], 90.0)
        self.assertEqual(objects[0].metadata["geometry_catalog"], "sharpless")
        summary_by_layer = {summary.layer_key: summary for summary in summaries}
        self.assertEqual(summary_by_layer["deep_sky"].returned_count, 2)
        self.assertEqual(summary_by_layer["deep_sky"].displayed_count, 1)

    def test_hyperleda_duplicate_enriches_simbad_galaxy_magnitude_without_dense_append(self) -> None:
        simbad_table = Table(
            rows=[(75.20, -8.20, "2MASX J05004800-0812000", "2MASX J05004800-0812000", "G", None)],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V"),
        )
        solved_field = SolvedField(
            center_ra_deg=75.2,
            center_dec_deg=-8.2,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(75.2 * u.deg, -8.2 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))
        duplicate_hyperleda_galaxy = SkyExplorerObject(
            layer_key="deep_sky",
            catalog="hyperleda",
            source_id="PGC 999999",
            name="PGC 999999",
            object_type="Galaxy",
            ra_deg=75.2001,
            dec_deg=-8.2001,
            pixel_x=500.0,
            pixel_y=400.0,
            magnitude=15.7,
            angular_distance_arcmin=0.0,
            short_label="PGC 999999",
            metadata={"catalog_type": "G", "catalog_major_axis_arcmin": 0.8, "catalog_minor_axis_arcmin": 0.4},
        )
        anonymous_nonduplicate = SkyExplorerObject(
            layer_key="deep_sky",
            catalog="hyperleda",
            source_id="PGC 888888",
            name="PGC 888888",
            object_type="Galaxy",
            ra_deg=75.35,
            dec_deg=-8.35,
            pixel_x=650.0,
            pixel_y=550.0,
            magnitude=16.1,
            angular_distance_arcmin=12.0,
            short_label="PGC 888888",
            metadata={"catalog_type": "G", "catalog_major_axis_arcmin": 0.7, "catalog_minor_axis_arcmin": 0.5},
        )

        class FakeSimbad:
            def __init__(self) -> None:
                self.TIMEOUT = None
                self.ROW_LIMIT = 0

            def add_votable_fields(self, *args) -> None:
                return None

            def query_region(self, *args, **kwargs):
                return simbad_table

        with (
            patch("photometry_app.core.sky_explorer.Simbad", side_effect=FakeSimbad),
            patch("photometry_app.core.sky_explorer._query_hyperleda_galaxy_objects", return_value=([duplicate_hyperleda_galaxy, anonymous_nonduplicate], 2)),
            patch("photometry_app.core.sky_explorer._query_sharpless_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_barnard_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_vdb_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_ngc2000_objects", return_value=([], 0)),
        ):
            objects, summaries = _query_simbad_objects(
                solved_field,
                wcs=wcs,
                field_center=field_center,
                selected_layers=("deep_sky",),
                include_dense_galaxy_catalog=False,
            )

        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0].catalog, "simbad")
        self.assertEqual(objects[0].name, "2MASX J05004800-0812000")
        self.assertEqual(objects[0].magnitude, 15.7)
        self.assertEqual(objects[0].metadata["magnitude_catalog"], "hyperleda")
        self.assertEqual(objects[0].metadata["catalog_major_axis_arcmin"], 0.8)
        summary_by_layer = {summary.layer_key: summary for summary in summaries}
        self.assertEqual(summary_by_layer["deep_sky"].displayed_count, 1)

    def test_simbad_deep_sky_rows_deduplicate_ngc_suffix_galaxy_aliases(self) -> None:
        simbad_table = Table(
            rows=[
                (338.986267, 33.965492, "2MASS J22355674+3357550", "2MASS J22355674+3357550|B2 2233+33|NGC 7318A", "G", None, 13.6),
                (338.986300, 33.965500, "NGC 7318A", "NGC 7318A|2MASS J22355674+3357550", "GiP", None, None),
                (338.993293, 33.966063, "NGC 7318B", "NGC 7318B|2MASS J22355840+3357570", "GiP", None, 14.0),
            ],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V", "B"),
        )
        solved_field = SolvedField(
            center_ra_deg=339.0,
            center_dec_deg=34.0,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(339.0 * u.deg, 34.0 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        class FakeSimbad:
            def __init__(self) -> None:
                self.TIMEOUT = None
                self.ROW_LIMIT = 0

            def add_votable_fields(self, *args) -> None:
                return None

            def query_region(self, *args, **kwargs):
                return simbad_table

        with (
            patch("photometry_app.core.sky_explorer.Simbad", side_effect=FakeSimbad),
            patch("photometry_app.core.sky_explorer._query_hyperleda_galaxy_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_sharpless_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_barnard_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_vdb_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_ngc2000_objects", return_value=([], 0)),
        ):
            objects, summaries = _query_simbad_objects(
                solved_field,
                wcs=wcs,
                field_center=field_center,
                selected_layers=("deep_sky",),
            )

        self.assertEqual([item.name for item in objects], ["NGC 7318A", "NGC 7318B"])
        self.assertEqual(objects[0].magnitude, 13.6)
        self.assertEqual(objects[1].magnitude, 14.0)
        summary_by_layer = {summary.layer_key: summary for summary in summaries}
        self.assertEqual(summary_by_layer["deep_sky"].returned_count, 3)
        self.assertEqual(summary_by_layer["deep_sky"].displayed_count, 2)

    def test_simbad_deep_sky_duplicate_promotes_ngc_galaxy_type_over_xray_alias(self) -> None:
        simbad_table = Table(
            rows=[
                (339.01502, 33.97588, "CXOU J223603.6+335833", "CXOU J223603.6+335833", "X", None, None),
                (339.01501, 33.97588, "NGC 7319", "NGC 7319|LEDA 69269|2MASX J22360355+3358327", "Sy2", 13.53, 14.57),
            ],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V", "B"),
        )
        solved_field = SolvedField(
            center_ra_deg=339.0,
            center_dec_deg=34.0,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(339.0 * u.deg, 34.0 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))

        class FakeSimbad:
            def __init__(self) -> None:
                self.TIMEOUT = None
                self.ROW_LIMIT = 0

            def add_votable_fields(self, *args) -> None:
                return None

            def query_region(self, *args, **kwargs):
                return simbad_table

        with (
            patch("photometry_app.core.sky_explorer.Simbad", side_effect=FakeSimbad),
            patch("photometry_app.core.sky_explorer._query_hyperleda_galaxy_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_sharpless_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_barnard_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_vdb_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_ngc2000_objects", return_value=([], 0)),
        ):
            objects, summaries = _query_simbad_objects(
                solved_field,
                wcs=wcs,
                field_center=field_center,
                selected_layers=("deep_sky",),
            )

        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0].name, "NGC 7319")
        self.assertEqual(objects[0].object_type, "Sy2")
        self.assertIn("seyfert_galaxy", sky_explorer_object_type_keys_for_object(objects[0]))
        self.assertIn("galaxy", sky_explorer_object_type_keys_for_object(objects[0]))
        summary_by_layer = {summary.layer_key: summary for summary in summaries}
        self.assertEqual(summary_by_layer["deep_sky"].returned_count, 2)
        self.assertEqual(summary_by_layer["deep_sky"].displayed_count, 1)

    def test_hyperleda_duplicate_can_promote_simbad_galaxy_to_ngc_name(self) -> None:
        simbad_table = Table(
            rows=[(75.20, -8.20, "2MASX J05004800-0812000", "2MASX J05004800-0812000", "G", None)],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V"),
        )
        solved_field = SolvedField(
            center_ra_deg=75.2,
            center_dec_deg=-8.2,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(75.2 * u.deg, -8.2 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))
        named_hyperleda_galaxy = SkyExplorerObject(
            layer_key="deep_sky",
            catalog="hyperleda",
            source_id="PGC 123456",
            name="NGC 1234",
            object_type="Galaxy",
            ra_deg=75.2001,
            dec_deg=-8.2001,
            pixel_x=500.0,
            pixel_y=400.0,
            magnitude=14.2,
            angular_distance_arcmin=0.0,
            short_label="NGC 1234",
            metadata={"catalog_type": "G", "catalog_major_axis_arcmin": 1.1, "has_named_alias": True},
        )

        class FakeSimbad:
            def __init__(self) -> None:
                self.TIMEOUT = None
                self.ROW_LIMIT = 0

            def add_votable_fields(self, *args) -> None:
                return None

            def query_region(self, *args, **kwargs):
                return simbad_table

        with (
            patch("photometry_app.core.sky_explorer.Simbad", side_effect=FakeSimbad),
            patch("photometry_app.core.sky_explorer._query_hyperleda_galaxy_objects", return_value=([named_hyperleda_galaxy], 1)),
            patch("photometry_app.core.sky_explorer._query_sharpless_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_barnard_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_vdb_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_ngc2000_objects", return_value=([], 0)),
        ):
            objects, _summaries = _query_simbad_objects(
                solved_field,
                wcs=wcs,
                field_center=field_center,
                selected_layers=("deep_sky",),
                include_dense_galaxy_catalog=False,
            )

        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0].catalog, "simbad")
        self.assertEqual(objects[0].name, "NGC 1234")
        self.assertEqual(objects[0].short_label, "NGC 1234")
        self.assertEqual(objects[0].magnitude, 14.2)

    def test_hyperleda_named_galaxy_replaces_sparse_ngc2000_ic_fallback(self) -> None:
        solved_field = SolvedField(
            center_ra_deg=76.5,
            center_dec_deg=-9.1,
            radius_deg=0.5,
            width=1000,
            height=800,
            wcs_path=Path("field.fits"),
        )
        field_center = SkyCoord(76.5 * u.deg, -9.1 * u.deg)
        wcs = SimpleNamespace(world_to_pixel_values=lambda ra_deg, dec_deg: (500.0, 400.0))
        hyperleda_galaxy = SkyExplorerObject(
            layer_key="deep_sky",
            catalog="hyperleda",
            source_id="PGC 16742",
            name="IC 402",
            object_type="Galaxy",
            ra_deg=76.561916,
            dec_deg=-9.107256,
            pixel_x=500.0,
            pixel_y=400.0,
            magnitude=None,
            angular_distance_arcmin=2.0,
            short_label="IC 402",
            metadata={
                "catalog_type": "G",
                "catalog_major_axis_arcmin": 1.86,
                "catalog_minor_axis_arcmin": 1.0,
                "catalog_position_angle_deg": 151.0,
            },
        )
        sparse_ngc2000_object = SkyExplorerObject(
            layer_key="deep_sky",
            catalog="ngc2000",
            source_id="IC 402",
            name="IC 402",
            object_type="Deep Sky Object",
            ra_deg=76.575,
            dec_deg=-9.133333,
            pixel_x=560.0,
            pixel_y=430.0,
            magnitude=None,
            angular_distance_arcmin=3.5,
            short_label="IC 402",
            metadata={"catalog_type": "", "catalog_description": "eF, pL, iR, dif", "catalog_size_arcmin": None},
        )

        class FakeSimbad:
            def __init__(self) -> None:
                self.TIMEOUT = None
                self.ROW_LIMIT = 0

            def add_votable_fields(self, *args) -> None:
                return None

            def query_region(self, *args, **kwargs):
                return Table(names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V"))

        with (
            patch("photometry_app.core.sky_explorer.Simbad", side_effect=FakeSimbad),
            patch("photometry_app.core.sky_explorer._query_hyperleda_galaxy_objects", return_value=([hyperleda_galaxy], 1)),
            patch("photometry_app.core.sky_explorer._query_sharpless_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_barnard_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_vdb_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_ngc2000_objects", return_value=([sparse_ngc2000_object], 1)),
        ):
            objects, summaries = _query_simbad_objects(
                solved_field,
                wcs=wcs,
                field_center=field_center,
                selected_layers=("deep_sky",),
            )

        self.assertEqual([item.catalog for item in objects], ["hyperleda"])
        self.assertEqual(objects[0].name, "IC 402")
        self.assertEqual(objects[0].object_type, "Galaxy")
        self.assertEqual(objects[0].metadata["catalog_major_axis_arcmin"], 1.86)
        summary_by_layer = {summary.layer_key: summary for summary in summaries}
        self.assertEqual(summary_by_layer["deep_sky"].returned_count, 2)
        self.assertEqual(summary_by_layer["deep_sky"].displayed_count, 1)

    def test_ngc2000_primary_position_replaces_alias_only_ngc_duplicate(self) -> None:
        simbad_table = Table(
            rows=[
                (
                    185.20,
                    47.55,
                    "2MASX J12212800+4733000",
                    "2MASX J12212800+4733000|NGC 4258",
                    "G",
                    11.0,
                ),
            ],
            names=("RA_d", "DEC_d", "MAIN_ID", "IDS", "OTYPE", "V"),
        )
        solved_field = SolvedField(
            center_ra_deg=184.74,
            center_dec_deg=47.30,
            radius_deg=0.6,
            width=4012,
            height=3009,
            wcs_path=Path("m106.png"),
        )
        field_center = SkyCoord(184.74 * u.deg, 47.30 * u.deg)
        wcs = SimpleNamespace(
            world_to_pixel_values=lambda ra_deg, dec_deg: (
                1200.0 + (float(ra_deg) - 184.74) * 12000.0,
                1500.0 + (float(dec_deg) - 47.30) * 12000.0,
            )
        )
        ngc4258_object = SkyExplorerObject(
            layer_key="deep_sky",
            catalog="ngc2000",
            source_id="NGC 4258",
            name="NGC 4258",
            object_type="Galaxy",
            ra_deg=184.739583,
            dec_deg=47.303889,
            pixel_x=1199.0,
            pixel_y=1546.0,
            magnitude=8.4,
            angular_distance_arcmin=1.0,
            short_label="NGC 4258",
            metadata={"catalog_type": "G", "catalog_description": "Galaxy", "catalog_size_arcmin": 18.6},
        )

        class FakeSimbad:
            def __init__(self) -> None:
                self.TIMEOUT = None
                self.ROW_LIMIT = 0

            def add_votable_fields(self, *args) -> None:
                return None

            def query_region(self, *args, **kwargs):
                return simbad_table

        with (
            patch("photometry_app.core.sky_explorer.Simbad", side_effect=FakeSimbad),
            patch("photometry_app.core.sky_explorer._query_hyperleda_galaxy_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_sharpless_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_barnard_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_vdb_objects", return_value=([], 0)),
            patch("photometry_app.core.sky_explorer._query_ngc2000_objects", return_value=([ngc4258_object], 1)),
        ):
            objects, summaries = _query_simbad_objects(
                solved_field,
                wcs=wcs,
                field_center=field_center,
                selected_layers=("deep_sky",),
            )

        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0].name, "NGC 4258")
        self.assertAlmostEqual(objects[0].ra_deg, 184.739583, places=4)
        self.assertAlmostEqual(objects[0].dec_deg, 47.303889, places=4)
        self.assertAlmostEqual(objects[0].pixel_x, 1199.0, places=1)
        self.assertAlmostEqual(objects[0].pixel_y, 1546.0, places=1)
        summary_by_layer = {summary.layer_key: summary for summary in summaries}
        self.assertEqual(summary_by_layer["deep_sky"].displayed_count, 1)

    def test_solar_system_conversion_uses_predicted_detection_coordinates(self) -> None:
        search_result = SolarSystemSearchResult(
            detection=SolarSystemDetection(
                name="Ceres",
                designation="1 Ceres",
                object_type="Asteroid",
                orbit_class="Main-belt",
                predicted_ra_deg=123.456,
                predicted_dec_deg=-12.345,
                predicted_x=640.0,
                predicted_y=320.0,
                predicted_magnitude=8.9,
                ra_rate_arcsec_per_hour=1.2,
                dec_rate_arcsec_per_hour=-0.8,
                motion_rate_arcsec_per_hour=1.44,
                expected_trail_length_px=2.0,
                positional_uncertainty_arcsec=0.5,
                altitude_deg=45.0,
                likely_visible=True,
                confidence_score=0.9,
                status="Predicted in field",
            ),
            angular_distance_deg=None,
            is_in_image=True,
        )

        field_center = SkyCoord(123.4 * u.deg, -12.3 * u.deg)

        sky_object = _solar_system_object_from_result(search_result, field_center=field_center)

        self.assertEqual(sky_object.source_id, "1 Ceres")
        self.assertEqual(sky_object.name, "Ceres")
        self.assertEqual(sky_object.ra_deg, 123.456)
        self.assertEqual(sky_object.dec_deg, -12.345)
        self.assertEqual(sky_object.pixel_x, 640.0)
        self.assertEqual(sky_object.pixel_y, 320.0)
        self.assertEqual(sky_object.metadata["predicted_motion_arcsec_per_hour"], 1.44)
        self.assertGreater(sky_object.angular_distance_arcmin, 0.0)

    def test_solar_system_lookup_does_not_reuse_observer_code_setting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "field.fits"
            source_path.touch()
            settings = AppSettings.from_root(root)
            settings.observer_code = "AAVSO-ONLY"
            settings.observing_site_latitude_deg = 42.0
            settings.observing_site_longitude_deg = -71.0
            settings.observing_site_elevation_m = 15.0

            solved_field = SolvedField(
                center_ra_deg=150.0,
                center_dec_deg=2.0,
                radius_deg=0.5,
                width=100,
                height=80,
                wcs_path=source_path,
            )
            footprint = SkyExplorerFieldFootprint(
                center_ra_deg=150.0,
                center_dec_deg=2.0,
                radius_deg=0.5,
                width_deg=1.0,
                height_deg=0.8,
                corners=(
                    SkyExplorerCorner("Top Left", 149.5, 2.4),
                    SkyExplorerCorner("Top Right", 150.5, 2.4),
                    SkyExplorerCorner("Bottom Right", 150.5, 1.6),
                    SkyExplorerCorner("Bottom Left", 149.5, 1.6),
                ),
            )

            captured_kwargs: dict[str, object] = {}

            def _fake_solar_search(*args, **kwargs):
                captured_kwargs.update(kwargs)
                return []

            with (
                patch("photometry_app.core.sky_explorer._resolve_source_field", return_value=(solved_field, False)),
                patch("photometry_app.core.sky_explorer._build_field_footprint", return_value=footprint),
                patch("photometry_app.core.sky_explorer.read_header", return_value={}),
                patch("photometry_app.core.sky_explorer.WCS", return_value=SimpleNamespace()),
                patch(
                    "photometry_app.core.sky_explorer.inspect_fits_file",
                    return_value=SimpleNamespace(
                        metadata=SimpleNamespace(
                            date_obs=datetime(2026, 4, 27, 22, 0, tzinfo=UTC),
                            exposure_seconds=120.0,
                        )
                    ),
                ),
                patch(
                    "photometry_app.core.sky_explorer.search_nearby_known_solar_system_objects",
                    side_effect=_fake_solar_search,
                ),
            ):
                result = explore_sky_image(source_path, settings=settings, selected_layers=("solar_system",))

            self.assertEqual(result.layer_summaries, (SkyExplorerLayerSummary("solar_system", "Asteroids/Comets", 0, 0),))
            self.assertIsNone(captured_kwargs.get("observatory_code"))
            self.assertEqual(captured_kwargs.get("observer_latitude_deg"), 42.0)
            self.assertEqual(captured_kwargs.get("observer_longitude_deg"), -71.0)
            self.assertEqual(captured_kwargs.get("observer_elevation_m"), 15.0)

    def test_explore_sky_image_accepts_raster_source_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            source_path = root_path / "field.tiff"
            source_path.write_bytes(b"tiff")
            settings = AppSettings.from_root(root_path)
            solved_field = SolvedField(
                center_ra_deg=150.0,
                center_dec_deg=2.0,
                radius_deg=0.2,
                width=1600,
                height=1200,
                wcs_path=root_path / "field_solution.fits",
            )
            footprint = SkyExplorerFieldFootprint(
                center_ra_deg=150.0,
                center_dec_deg=2.0,
                radius_deg=0.2,
                width_deg=0.4,
                height_deg=0.3,
                corners=(
                    SkyExplorerCorner("Top Left", 149.8, 2.2),
                    SkyExplorerCorner("Top Right", 150.2, 2.2),
                    SkyExplorerCorner("Bottom Right", 150.2, 1.8),
                    SkyExplorerCorner("Bottom Left", 149.8, 1.8),
                ),
            )

            with (
                patch("photometry_app.core.sky_explorer._resolve_source_field", return_value=(solved_field, True)) as resolve_source_field,
                patch("photometry_app.core.sky_explorer._build_field_footprint", return_value=footprint),
                patch("photometry_app.core.sky_explorer.read_header", return_value={}),
                patch("photometry_app.core.sky_explorer.WCS", return_value=SimpleNamespace()),
                patch("photometry_app.core.sky_explorer._query_simbad_objects", return_value=([], [])),
            ):
                result = explore_sky_image(source_path, settings=settings, selected_layers=("deep_sky",))

            resolve_source_field.assert_called_once_with(source_path, settings, progress_callback=None)
            self.assertEqual(result.objects, ())
            self.assertEqual(result.layer_summaries, ())
            self.assertTrue(result.used_astrometry_fallback)

    def test_explore_sky_image_queries_gaia_separately_from_vsx_and_exoplanets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            source_path = root_path / "field.fits"
            source_path.write_bytes(b"fits")
            settings = AppSettings.from_root(root_path)
            settings.sky_explorer_gaia_max_magnitude = 16.8
            settings.sky_explorer_gaia_hard_cap_enabled = True
            settings.sky_explorer_gaia_hard_cap_rows = 321
            solved_field = SolvedField(
                center_ra_deg=150.0,
                center_dec_deg=2.0,
                radius_deg=0.2,
                width=1600,
                height=1200,
                wcs_path=root_path / "field_solution.fits",
            )
            footprint = SkyExplorerFieldFootprint(
                center_ra_deg=150.0,
                center_dec_deg=2.0,
                radius_deg=0.2,
                width_deg=0.4,
                height_deg=0.3,
                corners=(
                    SkyExplorerCorner("Top Left", 149.8, 2.2),
                    SkyExplorerCorner("Top Right", 150.2, 2.2),
                    SkyExplorerCorner("Bottom Right", 150.2, 1.8),
                    SkyExplorerCorner("Bottom Left", 149.8, 1.8),
                ),
            )
            fake_catalog_service = Mock()
            fake_catalog_service.query_gaia_stars_limited.return_value = []
            fake_catalog_service.query_field_catalog.return_value = SimpleNamespace(variable_stars=[], exoplanets=[])

            def fake_catalog_star_objects(
                catalog_stars,
                *,
                layer_key,
                max_entries,
                field_center,
                solved_field,
                wcs,
            ):
                return [], SkyExplorerLayerSummary(layer_key, layer_key, len(catalog_stars), len(catalog_stars))

            with (
                patch("photometry_app.core.sky_explorer._resolve_source_field", return_value=(solved_field, False)),
                patch("photometry_app.core.sky_explorer._build_field_footprint", return_value=footprint),
                patch("photometry_app.core.sky_explorer.read_header", return_value={}),
                patch("photometry_app.core.sky_explorer.celestial_wcs", return_value=SimpleNamespace()),
                patch("photometry_app.core.sky_explorer.CatalogService", return_value=fake_catalog_service),
                patch("photometry_app.core.sky_explorer._catalog_star_objects", side_effect=fake_catalog_star_objects),
            ):
                result = explore_sky_image(
                    source_path,
                    settings=settings,
                    selected_layers=("gaia_stars", "variable_stars", "exoplanets"),
                )

            fake_catalog_service.query_gaia_stars_limited.assert_called_once_with(
                solved_field,
                16.8,
                row_limit=321,
                progress_callback=None,
            )
            fake_catalog_service.query_field_catalog.assert_called_once_with(
                solved_field,
                include_gaia=False,
                include_variable_stars=True,
                include_exoplanets=True,
                variable_star_max_magnitude=16.8,
                exoplanet_max_magnitude=16.8,
                progress_callback=None,
            )
            self.assertEqual(len(result.layer_summaries), 3)

    def test_explore_sky_image_mag_limit_query_ignores_gaia_hard_cap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            source_path = root_path / "field.fits"
            source_path.write_bytes(b"fits")
            settings = AppSettings.from_root(root_path)
            settings.sky_explorer_gaia_max_magnitude = 17.0
            settings.sky_explorer_gaia_hard_cap_enabled = True
            settings.sky_explorer_gaia_hard_cap_rows = 20
            solved_field = SolvedField(
                center_ra_deg=115.44,
                center_dec_deg=-14.84,
                radius_deg=0.8,
                width=1600,
                height=1200,
                wcs_path=root_path / "field_solution.fits",
            )
            footprint = SkyExplorerFieldFootprint(
                center_ra_deg=115.44,
                center_dec_deg=-14.84,
                radius_deg=0.8,
                width_deg=1.0,
                height_deg=0.75,
                corners=(),
            )
            fake_catalog_service = Mock()
            fake_catalog_service.query_gaia_stars_limited.return_value = []

            def fake_catalog_star_objects(
                catalog_stars,
                *,
                layer_key,
                max_entries,
                field_center,
                solved_field,
                wcs,
            ):
                return [], SkyExplorerLayerSummary(layer_key, layer_key, len(catalog_stars), len(catalog_stars))

            with (
                patch("photometry_app.core.sky_explorer._resolve_source_field", return_value=(solved_field, False)),
                patch("photometry_app.core.sky_explorer._build_field_footprint", return_value=footprint),
                patch("photometry_app.core.sky_explorer.read_header", return_value={}),
                patch("photometry_app.core.sky_explorer.celestial_wcs", return_value=SimpleNamespace()),
                patch("photometry_app.core.sky_explorer.CatalogService", return_value=fake_catalog_service),
                patch("photometry_app.core.sky_explorer._catalog_star_objects", side_effect=fake_catalog_star_objects),
            ):
                explore_sky_image(
                    source_path,
                    settings=settings,
                    selected_layers=("gaia_stars",),
                    gaia_object_limit=0,
                    ignore_gaia_hard_cap=True,
                )

            fake_catalog_service.query_gaia_stars_limited.assert_called_once_with(
                solved_field,
                17.0,
                row_limit=None,
                progress_callback=None,
            )


class SkyExplorerCatalogTypeMappingTest(unittest.TestCase):
    def test_sky_explorer_object_type_key_for_catalog_type_maps_simbad_codes(self) -> None:
        self.assertEqual(sky_explorer_object_type_key_for_catalog_type("HII"), "hii_region")
        self.assertEqual(sky_explorer_object_type_key_for_catalog_type("*"), "star")
        self.assertEqual(sky_explorer_object_type_key_for_catalog_type("G"), "galaxy")
        self.assertEqual(sky_explorer_object_type_key_for_catalog_type("V*"), "variable_star")
        self.assertEqual(
            sky_explorer_object_type_key_for_catalog_type("Unknown", layer_key="general_objects"),
            "general_object",
        )
