from __future__ import annotations

from pathlib import Path
import unittest

from photometry_app.core.catalogs import DEFAULT_GAIA_TILE_OPTIONS
from photometry_app.core.models import (
    CatalogStar,
    FieldCatalog,
    FileScanResult,
    ObservationMetadata,
    PlateSolveResult,
    SolvedField,
    WcsStatus,
)
from photometry_app.core.pipeline import PhotometryPipeline, _photometry_gaia_magnitude_limit


class PhotometryCatalogQueryTest(unittest.TestCase):
    def test_gaia_magnitude_limit_keeps_a_usable_comparison_star_pool(self) -> None:
        self.assertEqual(_photometry_gaia_magnitude_limit(None), 16.0)
        self.assertEqual(_photometry_gaia_magnitude_limit(13.5), 16.0)
        self.assertEqual(_photometry_gaia_magnitude_limit(18.0), 18.0)

    def test_wide_field_photometry_catalog_uses_one_gaia_cone(self) -> None:
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
        self.assertEqual(len(service.fields), 1)
        self.assertAlmostEqual(service.fields[0].radius_deg, DEFAULT_GAIA_TILE_OPTIONS.max_radius_deg)
        self.assertEqual(service.kwargs[0]["gaia_max_magnitude"], 16.0)
        self.assertEqual(service.kwargs[0]["gaia_row_cap"], 5000)
        self.assertTrue(any("central 0.3500 deg" in line for line in progress))
        self.assertEqual(catalog.variable_stars[0].name, "XZ And")
