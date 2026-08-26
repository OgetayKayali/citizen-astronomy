from __future__ import annotations

from pathlib import Path
import unittest

from photometry_app.core.catalogs import gaia_field_needs_tiles
from photometry_app.core.models import (
    CatalogStar,
    FieldCatalog,
    FileScanResult,
    ObservationMetadata,
    PlateSolveResult,
    SolvedField,
    WcsStatus,
)
from photometry_app.core.pipeline import (
    PhotometryPipeline,
    _ensure_object_folder_variable_stars,
    _object_target_name_hints,
    _photometry_gaia_magnitude_limit,
)


class PhotometryCatalogQueryTest(unittest.TestCase):
    def test_gaia_magnitude_limit_keeps_a_usable_comparison_star_pool(self) -> None:
        self.assertEqual(_photometry_gaia_magnitude_limit(None), 16.0)
        self.assertEqual(_photometry_gaia_magnitude_limit(13.5), 16.0)
        self.assertEqual(_photometry_gaia_magnitude_limit(18.0), 18.0)

    def test_wide_field_photometry_catalog_uses_the_full_image_field(self) -> None:
        class RecordingCatalogService:
            def __init__(self) -> None:
                self.fields: list[SolvedField] = []
                self.kwargs: list[dict] = []

            def query_field_catalog(self, solved_field, **kwargs):
                self.fields.append(solved_field)
                self.kwargs.append(kwargs)
                return FieldCatalog(
                    center_ra_deg=solved_field.center_ra_deg,
                    center_dec_deg=solved_field.center_dec_deg,
                    radius_deg=solved_field.radius_deg,
                    gaia_stars=[
                        CatalogStar(
                            "gaia-dr3",
                            "g1",
                            "g1",
                            solved_field.center_ra_deg,
                            solved_field.center_dec_deg,
                            11.0,
                            False,
                        )
                    ],
                    variable_stars=[
                        CatalogStar(
                            "vsx",
                            "xz",
                            "XZ And",
                            solved_field.center_ra_deg,
                            solved_field.center_dec_deg,
                            10.0,
                            True,
                        )
                    ],
                )

        solved_field = SolvedField(
            center_ra_deg=29.23328,
            center_dec_deg=42.11177,
            radius_deg=3.2494,
            width=6248,
            height=4176,
            wcs_path=Path("xz.fits"),
        )
        file_result = FileScanResult(
            path=Path("xz.fits"),
            object_folder="test xz",
            metadata=ObservationMetadata(None, "L", 20.0, 6248, 4176, "XZ And"),
            wcs_status=WcsStatus.SOLVED,
        )
        solve_result = PlateSolveResult(
            source_path=Path("xz.fits"),
            status=WcsStatus.SOLVED,
            solved_field=solved_field,
        )
        service = RecordingCatalogService()
        progress: list[str] = []
        catalog = PhotometryPipeline()._best_field_catalog_for_solved_results(
            service,
            [(file_result, solve_result)],
            progress.append,
            gaia_max_magnitude=13.5,
        )
        self.assertTrue(gaia_field_needs_tiles(solved_field))
        self.assertEqual(len(service.fields), 1)
        self.assertAlmostEqual(service.fields[0].radius_deg, solved_field.radius_deg)
        self.assertEqual(service.kwargs[0]["gaia_max_magnitude"], 16.0)
        self.assertEqual(service.kwargs[0]["gaia_row_cap"], 5000)
        self.assertTrue(any("full field" in line for line in progress))
        self.assertTrue(any("tiles" in line for line in progress))
        self.assertEqual(catalog.variable_stars[0].name, "XZ And")
        self.assertAlmostEqual(catalog.radius_deg, solved_field.radius_deg)

    def test_wide_field_photometry_catalog_keeps_off_center_named_variables(self) -> None:
        class LayeredCatalogService:
            def query_field_catalog(self, solved_field, **kwargs):
                include_gaia = kwargs.get("include_gaia", True)
                include_variable_stars = kwargs.get("include_variable_stars", True)
                gaia_stars = []
                variable_stars = []
                if include_gaia:
                    gaia_stars.append(
                        CatalogStar(
                            "gaia-dr3",
                            "g1",
                            "g1",
                            solved_field.center_ra_deg,
                            solved_field.center_dec_deg,
                            11.0,
                            False,
                        )
                    )
                if include_variable_stars:
                    variable_stars.append(
                        CatalogStar(
                            "vsx",
                            "wuma",
                            "W UMa",
                            solved_field.center_ra_deg,
                            solved_field.center_dec_deg + 1.15,
                            7.9,
                            True,
                        )
                    )
                return FieldCatalog(
                    center_ra_deg=solved_field.center_ra_deg,
                    center_dec_deg=solved_field.center_dec_deg,
                    radius_deg=solved_field.radius_deg,
                    gaia_stars=gaia_stars,
                    variable_stars=variable_stars,
                )

        solved_field = SolvedField(
            center_ra_deg=145.938,
            center_dec_deg=55.957,
            radius_deg=3.2512,
            width=6248,
            height=4176,
            wcs_path=Path("wuma.fits"),
        )
        file_result = FileScanResult(
            path=Path("Light_W UMa_10.0s_Bin1_L_20260316-232605_0002_c.xisf"),
            object_folder="Light_BIN-1_6248x4176_EXPOSURE-10.00s_FILTER-L_mono",
            metadata=ObservationMetadata(None, "L", 10.0, 6248, 4176, "W UMa"),
            wcs_status=WcsStatus.SOLVED,
        )
        solve_result = PlateSolveResult(
            source_path=file_result.path,
            status=WcsStatus.SOLVED,
            solved_field=solved_field,
        )
        catalog = PhotometryPipeline()._best_field_catalog_for_solved_results(
            LayeredCatalogService(),
            [(file_result, solve_result)],
            None,
        )
        self.assertEqual([star.name for star in catalog.variable_stars], ["W UMa"])
        self.assertEqual(len(catalog.gaia_stars), 1)
        self.assertGreater(abs(catalog.variable_stars[0].dec_deg - solved_field.center_dec_deg), 0.35)


class ObjectFolderVariableStarTest(unittest.TestCase):
    def test_image_object_name_keeps_w_uma_when_folder_name_has_no_target(self) -> None:
        folder = "Light_BIN-1_6248x4176_EXPOSURE-10.00s_FILTER-L_mono"
        wuma = CatalogStar("vsx", "wuma", "W UMa", 145.938, 55.957, 7.9, True)
        gaia_var = CatalogStar("vsx", "gvar", "Gaia DR3 855234", 145.94, 55.96, 14.2, True)
        selected, notes = _ensure_object_folder_variable_stars(
            [],
            [wuma, gaia_var],
            folder,
        )
        self.assertEqual(selected, [])
        self.assertEqual(notes, [])

        selected, notes = _ensure_object_folder_variable_stars(
            [],
            [wuma, gaia_var],
            folder,
            name_hints=_object_target_name_hints(
                folder,
                [
                    FileScanResult(
                        path=Path("Light_W UMa_10.0s_Bin1_L_20260316-232605_0002_c.xisf"),
                        object_folder=folder,
                        metadata=ObservationMetadata(None, "L", 10.0, 6248, 4176, "W UMa"),
                        wcs_status=WcsStatus.SOLVED,
                    )
                ],
            ),
        )
        self.assertEqual([star.name for star in selected], ["W UMa"])
        self.assertTrue(notes)
        self.assertIn("W UMa", notes[0])

