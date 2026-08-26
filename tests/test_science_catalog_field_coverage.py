from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from photometry_app.core.catalogs import DEFAULT_GAIA_TILE_OPTIONS
from photometry_app.core.distance_map import build_distance_map
from photometry_app.core.models import (
    CatalogStar,
    FieldCatalog,
    FileScanResult,
    ObservationMetadata,
    PlateSolveResult,
    SolvedField,
    WcsStatus,
)
from photometry_app.core.pipeline import PhotometryPipeline
from photometry_app.core.settings import AppSettings
from photometry_app.core.sky_explorer import (
    SkyExplorerCorner,
    SkyExplorerFieldFootprint,
    SkyExplorerLayerSummary,
    explore_sky_image,
)
from photometry_app.core.transient import _query_gaia_veto_stars

_WIDE_FIELD_RADIUS_DEG = 3.2512
_WCS_PROBE_MAX_RADIUS_DEG = float(DEFAULT_GAIA_TILE_OPTIONS.max_radius_deg)


def _wide_field(wcs_path: Path) -> SolvedField:
    return SolvedField(
        center_ra_deg=145.938,
        center_dec_deg=55.957,
        radius_deg=_WIDE_FIELD_RADIUS_DEG,
        width=6248,
        height=4176,
        wcs_path=wcs_path,
    )


class ScienceCatalogFieldCoverageTest(unittest.TestCase):
    def test_capped_solved_field_is_only_called_from_wcs_sanity(self) -> None:
        production_root = Path(__file__).resolve().parents[1] / "photometry_app"
        offenders: list[str] = []
        for path in production_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "capped_solved_field(" not in text:
                continue
            if path.name == "catalogs.py":
                continue
            if path.name == "wcs_sanity.py":
                continue
            offenders.append(str(path.relative_to(production_root)))
        self.assertEqual(
            offenders,
            [],
            "capped_solved_field() is a WCS probe helper; science catalogs must keep the full image radius.",
        )

    def test_differential_photometry_queries_the_full_wide_field(self) -> None:
        class RecordingCatalogService:
            def __init__(self) -> None:
                self.fields: list[SolvedField] = []

            def query_field_catalog(self, solved_field, **kwargs):
                self.fields.append(solved_field)
                return FieldCatalog(
                    center_ra_deg=solved_field.center_ra_deg,
                    center_dec_deg=solved_field.center_dec_deg,
                    radius_deg=solved_field.radius_deg,
                    gaia_stars=[
                        CatalogStar("gaia-dr3", "g1", "g1", solved_field.center_ra_deg, solved_field.center_dec_deg, 11.0, False)
                    ],
                    variable_stars=[
                        CatalogStar(
                            "vsx",
                            "wuma",
                            "W UMa",
                            solved_field.center_ra_deg,
                            solved_field.center_dec_deg + 1.15,
                            7.9,
                            True,
                        )
                    ],
                )

        solved_field = _wide_field(Path("wuma.fits"))
        file_result = FileScanResult(
            path=Path("Light_W UMa_10.0s_Bin1_L_20260316-232605_0002_c.xisf"),
            object_folder="Light_BIN-1",
            metadata=ObservationMetadata(None, "L", 10.0, 6248, 4176, "W UMa"),
            wcs_status=WcsStatus.SOLVED,
        )
        service = RecordingCatalogService()
        catalog = PhotometryPipeline()._best_field_catalog_for_solved_results(
            service,
            [
                (
                    file_result,
                    PlateSolveResult(source_path=file_result.path, status=WcsStatus.SOLVED, solved_field=solved_field),
                )
            ],
            None,
        )
        self.assertEqual(len(service.fields), 1)
        self.assertAlmostEqual(service.fields[0].radius_deg, _WIDE_FIELD_RADIUS_DEG)
        self.assertGreater(service.fields[0].radius_deg, _WCS_PROBE_MAX_RADIUS_DEG)
        self.assertEqual(catalog.variable_stars[0].name, "W UMa")

    def test_sky_explorer_queries_the_full_wide_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            source_path = root_path / "field.fits"
            source_path.write_bytes(b"fits")
            settings = AppSettings.from_root(root_path)
            solved_field = _wide_field(root_path / "field_solution.fits")
            footprint = SkyExplorerFieldFootprint(
                center_ra_deg=solved_field.center_ra_deg,
                center_dec_deg=solved_field.center_dec_deg,
                radius_deg=solved_field.radius_deg,
                width_deg=5.0,
                height_deg=3.5,
                corners=(
                    SkyExplorerCorner("Top Left", 143.0, 58.0),
                    SkyExplorerCorner("Top Right", 149.0, 58.0),
                    SkyExplorerCorner("Bottom Right", 149.0, 54.0),
                    SkyExplorerCorner("Bottom Left", 143.0, 54.0),
                ),
            )
            fake_catalog_service = Mock()
            fake_catalog_service.query_gaia_stars_limited.return_value = []
            fake_catalog_service.query_field_catalog.return_value = SimpleNamespace(variable_stars=[], exoplanets=[])

            def fake_catalog_star_objects(catalog_stars, *, layer_key, max_entries, field_center, solved_field, wcs):
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
                    selected_layers=("gaia_stars", "variable_stars", "exoplanets"),
                )

            queried_gaia_field = fake_catalog_service.query_gaia_stars_limited.call_args.args[0]
            queried_vsx_field = fake_catalog_service.query_field_catalog.call_args.args[0]
            self.assertAlmostEqual(queried_gaia_field.radius_deg, _WIDE_FIELD_RADIUS_DEG)
            self.assertAlmostEqual(queried_vsx_field.radius_deg, _WIDE_FIELD_RADIUS_DEG)
            self.assertGreater(queried_gaia_field.radius_deg, _WCS_PROBE_MAX_RADIUS_DEG)

    def test_distance_map_queries_the_full_wide_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            source_path = root_path / "field.fits"
            source_path.write_bytes(b"fits")
            settings = AppSettings.from_root(root_path)
            settings.distance_map_limit_to_image_footprint = False
            solved_field = _wide_field(root_path / "field_solution.fits")
            fake_catalog_service = Mock()
            fake_catalog_service.query_gaia_stars_limited.return_value = []

            with (
                patch("photometry_app.core.distance_map._resolve_source_field", return_value=(solved_field, False)),
                patch("photometry_app.core.distance_map.CatalogService", return_value=fake_catalog_service),
            ):
                result = build_distance_map(
                    source_path,
                    settings=settings,
                    max_magnitude=16.0,
                    max_distance_pc=1000.0,
                    max_star_count=100,
                )

            queried_field = fake_catalog_service.query_gaia_stars_limited.call_args.args[0]
            self.assertAlmostEqual(queried_field.radius_deg, _WIDE_FIELD_RADIUS_DEG)
            self.assertGreater(queried_field.radius_deg, _WCS_PROBE_MAX_RADIUS_DEG)
            self.assertAlmostEqual(result.solved_field.radius_deg, _WIDE_FIELD_RADIUS_DEG)

    def test_transient_gaia_veto_queries_the_full_wide_field(self) -> None:
        class RecordingCatalogService:
            def __init__(self) -> None:
                self.fields: list[SolvedField] = []

            def query_gaia_stars_limited(self, solved_field, maximum_magnitude: float, progress_callback=None):
                self.fields.append(solved_field)
                return []

        solved_field = _wide_field(Path("transient.fits"))
        service = RecordingCatalogService()
        notes: list[str] = []
        stars = _query_gaia_veto_stars(service, solved_field, 18.0, notes, None)
        self.assertEqual(stars, [])
        self.assertEqual(len(service.fields), 1)
        self.assertAlmostEqual(service.fields[0].radius_deg, _WIDE_FIELD_RADIUS_DEG)
        self.assertGreater(service.fields[0].radius_deg, _WCS_PROBE_MAX_RADIUS_DEG)
        self.assertEqual(notes, [])
