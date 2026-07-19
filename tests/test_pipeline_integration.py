from __future__ import annotations



import json

import os

import time

import tempfile

import unittest

from concurrent.futures import ThreadPoolExecutor as ConcurrentThreadPoolExecutor

from dataclasses import replace

from datetime import UTC, datetime

from pathlib import Path

from unittest.mock import patch



import numpy as np

from astropy.io import fits



from photometry_app.core.models import (

    CatalogStar,

    FieldCatalog,

    ManualSourceRole,

    ObjectPhotometryMode,

    PhotometryMeasurement,

    PlateSolveResult,

    ProcessingReport,

    RecenterMode,

    SolvedField,

    VariableStarDesignationFamily,

    VariableStarLimitMode,

    WcsStatus,

)

from photometry_app.core.pipeline import PhotometryPipeline, science_export_metadata_from_settings

from photometry_app.core.catalogs import CatalogService

from photometry_app.core.exporters import ScienceExportMetadata

from photometry_app.core.photometry import ApertureProfile

from photometry_app.core.settings import AppSettings

from photometry_app.core.wcs import extract_solved_field





class FakeCatalogService:

    call_count = 0



    def __init__(self, cache_dir: Path) -> None:

        self.cache_dir = cache_dir



    def query_field_catalog(self, solved_field: object) -> FieldCatalog:

        type(self).call_count += 1

        return FieldCatalog(

            center_ra_deg=83.822,

            center_dec_deg=-5.391,

            radius_deg=0.25,

            gaia_stars=[CatalogStar("gaia-dr3", "gaia-ref", "gaia-ref", 83.8223, -5.3913, 11.0, False)],

            variable_stars=[CatalogStar("vsx", "vsx-var", "R Cas", 83.8220, -5.3910, 8.0, True)],

        )





class EmptyCatalogService:

    def __init__(self, cache_dir: Path) -> None:

        self.cache_dir = cache_dir



    def query_field_catalog(self, solved_field: object) -> FieldCatalog:

        return FieldCatalog(

            center_ra_deg=83.822,

            center_dec_deg=-5.391,

            radius_deg=0.25,

            gaia_stars=[CatalogStar("gaia-dr3", "gaia-ref", "gaia-ref", 83.8223, -5.3913, 11.0, False)],

            variable_stars=[],

        )





class FailingAstrometryClient:

    def __init__(self, api_key: str) -> None:

        self.api_key = api_key



    def solve_file(self, fits_path: Path, cache_dir: Path) -> object:

        raise RuntimeError("simulated astrometry failure")





class RecordingAstrometryClient:

    calls: list[tuple[Path, object]] = []



    def __init__(self, api_key: str) -> None:

        self.api_key = api_key



    def solve_file(self, fits_path: Path, cache_dir: Path, timeout_seconds: int = 300, hints: object = None) -> object:

        type(self).calls.append((fits_path, hints))

        header = fits.Header()

        header["CTYPE1"] = "RA---TAN"

        header["CTYPE2"] = "DEC--TAN"

        header["CRVAL1"] = 83.822

        header["CRVAL2"] = -5.391

        header["CRPIX1"] = 16.0

        header["CRPIX2"] = 16.0

        header["CD1_1"] = -0.00028

        header["CD1_2"] = 0.0

        header["CD2_1"] = 0.0

        header["CD2_2"] = 0.00028

        solved_path = cache_dir / f"{fits_path.stem}_solved.fits"

        solved_path.parent.mkdir(parents=True, exist_ok=True)

        fits.PrimaryHDU(data=np.ones((32, 32), dtype=np.float32), header=header).writeto(solved_path, overwrite=True)

        return __import__("photometry_app.core.models", fromlist=["PlateSolveResult", "WcsStatus"]).PlateSolveResult(

            source_path=fits_path,

            status=__import__("photometry_app.core.models", fromlist=["WcsStatus"]).WcsStatus.SOLVED,

            solved_field=extract_solved_field(header, 32, 32, solved_path),

            reasons=[],

        )





class SlowAstrometryClient(RecordingAstrometryClient):

    def solve_file(self, fits_path: Path, cache_dir: Path, timeout_seconds: int = 300, hints: object = None) -> object:

        time.sleep(0.2)

        return super().solve_file(fits_path, cache_dir, timeout_seconds=timeout_seconds, hints=hints)





class PipelineIntegrationTest(unittest.TestCase):

    def test_science_export_metadata_from_settings_maps_submission_fields(self) -> None:

        settings = AppSettings.from_root(Path(tempfile.mkdtemp()))

        settings.observer_code = "KAY"

        settings.observer_name = "Kay"

        settings.organization = "Citizen Photometry"

        settings.site_name = "Backyard"

        settings.observing_site_latitude_deg = 51.5074

        settings.observing_site_longitude_deg = -0.1278

        settings.observing_site_elevation_m = 35.0

        settings.telescope = "80mm refractor"

        settings.camera = "Mono CMOS"

        settings.filter_system = "Johnson-Cousins"

        settings.aavso_chart_id = "X12345ABC"

        settings.time_standard = "BJD_TDB"

        settings.transformed = True

        settings.reduction_notes = "Rejected cloudy frames."



        metadata = science_export_metadata_from_settings(settings)



        self.assertEqual(metadata.observer_code, "KAY")

        self.assertEqual(metadata.observer_name, "Kay")

        self.assertEqual(metadata.organization, "Citizen Photometry")

        self.assertEqual(metadata.site_name, "Backyard")

        self.assertEqual(metadata.observing_site_latitude_deg, 51.5074)

        self.assertEqual(metadata.observing_site_longitude_deg, -0.1278)

        self.assertEqual(metadata.observing_site_elevation_m, 35.0)

        self.assertEqual(metadata.telescope, "80mm refractor")

        self.assertEqual(metadata.camera, "Mono CMOS")

        self.assertEqual(metadata.filter_system, "Johnson-Cousins")

        self.assertEqual(metadata.aavso_chart_id, "X12345ABC")

        self.assertEqual(metadata.time_standard, "BJD_TDB")

        self.assertTrue(metadata.transformed)

        self.assertEqual(metadata.reduction_notes, "Rejected cloudy frames.")



    def test_scan_workspace_ignores_saved_observation_timezone_for_filename_timestamps(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            fits.PrimaryHDU(data=np.ones((8, 8), dtype=np.float32)).writeto(

                object_dir / "Light_M42_30.0s_Bin1_B_20250205-201036_0001.fit"

            )

            settings = AppSettings.from_root(root)

            settings.observation_timezone = "UTC-05:00"

            settings.save(root)



            pipeline = PhotometryPipeline()

            report = pipeline.scan_workspace(root)



            self.assertIsNone(report.object_summaries[0].files[0].metadata.date_obs)



    def test_preview_aavso_export_forwards_arguments(self) -> None:

        pipeline = PhotometryPipeline()

        report = __import__("photometry_app.core.models", fromlist=["ProcessingReport"]).ProcessingReport(

            object_name="Demo",

            files_processed=0,

            solved_files=0,

            field_catalog=None,

            total_variable_stars_found=0,

            variable_star_limit_mode=VariableStarLimitMode.PERCENT,

            variable_star_limit_value=100,

            reference_stars=[],

            measurements=[],

            light_curves=[],

            notes=[],

        )



        with patch("photometry_app.core.pipeline.preview_aavso_extended_export") as preview_mock:

            preview_mock.return_value = {"warning_count": 0}

            science_metadata = ScienceExportMetadata(observer_code="KAY", aavso_chart_id="X12345ABC")

            measurements = [object()]



            result = pipeline.preview_aavso_export(

                report,

                measurements=measurements,

                science_metadata=science_metadata,

            )



            self.assertEqual(result, {"warning_count": 0})

            preview_mock.assert_called_once_with(

                report,

                measurements=measurements,

                science_metadata=science_metadata,

            )



    def test_export_results_forwards_theme_options(self) -> None:

        pipeline = PhotometryPipeline()

        report = __import__("photometry_app.core.models", fromlist=["ProcessingReport"]).ProcessingReport(

            object_name="Demo",

            files_processed=0,

            solved_files=0,

            field_catalog=None,

            total_variable_stars_found=0,

            variable_star_limit_mode=VariableStarLimitMode.PERCENT,

            variable_star_limit_value=100,

            reference_stars=[],

            measurements=[],

            light_curves=[],

            notes=[],

        )



        with tempfile.TemporaryDirectory() as temp_dir, patch("photometry_app.core.pipeline.export_processing_report") as export_mock:

            export_mock.return_value = {"summary_json": Path(temp_dir) / "summary.json"}

            custom_colors = {"plot_bg": "#1a1b26"}

            science_metadata = ScienceExportMetadata(observer_code="KAY")

            measurements = [object()]

            light_curves = [object()]

            pipeline.export_results(

                report,

                Path(temp_dir),

                measurements=measurements,

                light_curves=light_curves,

                export_suffix="filtered",

                plot_y_axis_mode="flux",

                plot_theme="tokyo-night",

                custom_theme_colors=custom_colors,

                science_metadata=science_metadata,

            )



            export_mock.assert_called_once_with(

                report,

                Path(temp_dir),

                measurements=measurements,

                light_curves=light_curves,

                export_suffix="filtered",

                plot_y_axis_mode="flux",

                plot_theme="tokyo-night",

                custom_theme_colors=custom_colors,

                science_metadata=science_metadata,

            )



    def test_process_object_reports_manual_and_auto_photometry_worker_counts(self) -> None:

        class SingleVariableCatalogService:

            def __init__(self, cache_dir: Path) -> None:

                self.cache_dir = cache_dir



            def query_field_catalog(self, solved_field: object) -> FieldCatalog:

                return FieldCatalog(

                    center_ra_deg=83.822,

                    center_dec_deg=-5.391,

                    radius_deg=0.25,

                    gaia_stars=[CatalogStar("gaia-dr3", "gaia-ref", "gaia-ref", 83.8223, -5.3913, 11.0, False)],

                    variable_stars=[CatalogStar("vsx", "var-1", "Var 1", 83.8220, -5.3910, 9.0, True)],

                )



        def fake_measure_targets(**kwargs: object) -> list[object]:

            scan_result = kwargs["scan_result"]

            source_path = kwargs["source_path"]

            variable_star = kwargs["variable_stars"][0]

            return [

                __import__("photometry_app.core.models", fromlist=["PhotometryMeasurement"]).PhotometryMeasurement(

                    source_id=variable_star.source_id,

                    source_name=variable_star.name,

                    catalog=variable_star.catalog,

                    object_name=scan_result.object_folder,

                    file_path=source_path,

                    observation_time=scan_result.metadata.date_obs,

                    filter_name=scan_result.metadata.filter_name,

                    ra_deg=variable_star.ra_deg,

                    dec_deg=variable_star.dec_deg,

                    x=16.0,

                    y=16.0,

                    flux=5000.0,

                    flux_error=20.0,

                    instrumental_magnitude=-9.0,

                    differential_magnitude=None,

                    is_variable=True,

                    is_reference=False,

                    flags=[],

                )

            ]



        class RecordingThreadPoolExecutor(ConcurrentThreadPoolExecutor):

            created_max_workers: list[int] = []



            def __init__(self, max_workers: int | None = None, *args: object, **kwargs: object) -> None:

                type(self).created_max_workers.append(int(max_workers or 0))

                super().__init__(max_workers=max_workers, *args, **kwargs)



        scenarios = [

            ({"cache_dir": ".photometry-cache", "photometry_parallel_workers": 2}, None, "Submitting 3 photometry file(s) with 2 worker(s) (manual).", 2),

            ({"cache_dir": ".photometry-cache", "photometry_parallel_workers": 0}, 6, "Submitting 3 photometry file(s) with 3 worker(s) (auto from 6 logical CPU thread(s)).", 3),

        ]



        for payload, cpu_count, expected_message, expected_workers in scenarios:

            with self.subTest(expected_message=expected_message), tempfile.TemporaryDirectory() as temp_dir:

                root = Path(temp_dir)

                object_dir = root / "Files" / "M42"

                object_dir.mkdir(parents=True)

                for index in range(3):

                    self._write_solved_fits(object_dir / f"frame_{index:03d}.fits")



                (root / ".photometry-settings.json").write_text(json.dumps(payload), encoding="utf-8")

                RecordingThreadPoolExecutor.created_max_workers = []

                progress_messages: list[str] = []

                pipeline = PhotometryPipeline()

                patches = [

                    patch("photometry_app.core.pipeline.CatalogService", SingleVariableCatalogService),

                    patch("photometry_app.core.pipeline.measure_targets", side_effect=fake_measure_targets),

                    patch("photometry_app.core.pipeline.ThreadPoolExecutor", RecordingThreadPoolExecutor),

                ]

                if cpu_count is not None:

                    patches.append(patch("photometry_app.core.pipeline.os.cpu_count", return_value=cpu_count))



                with patches[0], patches[1], patches[2]:

                    if len(patches) == 4:

                        with patches[3]:

                            report = pipeline.process_object(root, "M42", progress_callback=progress_messages.append)

                    else:

                        report = pipeline.process_object(root, "M42", progress_callback=progress_messages.append)



                self.assertEqual(report.files_processed, 3)

                self.assertIn(expected_message, progress_messages)

                self.assertIn(expected_workers, RecordingThreadPoolExecutor.created_max_workers)



    def test_process_object_measures_multiple_files_in_parallel(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            for index in range(3):

                self._write_solved_fits(object_dir / f"frame_{index:03d}.fits")



            settings_path = root / ".photometry-settings.json"

            settings_path.write_text(json.dumps({"cache_dir": ".photometry-cache"}), encoding="utf-8")



            class SingleVariableCatalogService:

                def __init__(self, cache_dir: Path) -> None:

                    self.cache_dir = cache_dir



                def query_field_catalog(self, solved_field: object) -> FieldCatalog:

                    return FieldCatalog(

                        center_ra_deg=83.822,

                        center_dec_deg=-5.391,

                        radius_deg=0.25,

                        gaia_stars=[CatalogStar("gaia-dr3", "gaia-ref", "gaia-ref", 83.8223, -5.3913, 11.0, False)],

                        variable_stars=[CatalogStar("vsx", "var-1", "Var 1", 83.8220, -5.3910, 9.0, True)],

                    )



            def slow_measure_targets(**kwargs: object) -> list[object]:

                time.sleep(0.2)

                scan_result = kwargs["scan_result"]

                source_path = kwargs["source_path"]

                variable_star = kwargs["variable_stars"][0]

                return [

                    __import__("photometry_app.core.models", fromlist=["PhotometryMeasurement"]).PhotometryMeasurement(

                        source_id=variable_star.source_id,

                        source_name=variable_star.name,

                        catalog=variable_star.catalog,

                        object_name=scan_result.object_folder,

                        file_path=source_path,

                        observation_time=scan_result.metadata.date_obs,

                        filter_name=scan_result.metadata.filter_name,

                        ra_deg=variable_star.ra_deg,

                        dec_deg=variable_star.dec_deg,

                        x=16.0,

                        y=16.0,

                        flux=5000.0,

                        flux_error=20.0,

                        instrumental_magnitude=-9.0,

                        differential_magnitude=None,

                        is_variable=True,

                        is_reference=False,

                        flags=[],

                    )

                ]



            started = time.perf_counter()

            pipeline = PhotometryPipeline()

            with patch("photometry_app.core.pipeline.CatalogService", SingleVariableCatalogService), patch(

                "photometry_app.core.pipeline.measure_targets", side_effect=slow_measure_targets

            ):

                report = pipeline.process_object(root, "M42")

            elapsed = time.perf_counter() - started



            self.assertEqual(report.files_processed, 3)

            self.assertEqual(len(report.measurements), 3)

            self.assertLess(elapsed, 0.5)



    def test_process_object_skips_saturated_selected_variable_star_when_filter_enabled(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            self._write_solved_fits(object_dir / "frame_001.fits")



            settings_path = root / ".photometry-settings.json"

            settings_path.write_text(json.dumps({"cache_dir": ".photometry-cache", "saturation_filter_enabled": True}), encoding="utf-8")



            class SingleVariableCatalogService:

                def __init__(self, cache_dir: Path) -> None:

                    self.cache_dir = cache_dir



                def query_field_catalog(self, solved_field: object) -> FieldCatalog:

                    return FieldCatalog(

                        center_ra_deg=83.822,

                        center_dec_deg=-5.391,

                        radius_deg=0.25,

                        gaia_stars=[CatalogStar("gaia-dr3", "gaia-ref", "gaia-ref", 83.8223, -5.3913, 11.0, False)],

                        variable_stars=[CatalogStar("vsx", "var-1", "Var 1", 83.8220, -5.3910, 9.0, True)],

                    )



            def fake_measure_targets(**kwargs: object) -> list[object]:

                scan_result = kwargs["scan_result"]

                source_path = kwargs["source_path"]

                variable_star = kwargs["variable_stars"][0]

                return [

                    __import__("photometry_app.core.models", fromlist=["PhotometryMeasurement"]).PhotometryMeasurement(

                        source_id=variable_star.source_id,

                        source_name=variable_star.name,

                        catalog=variable_star.catalog,

                        object_name=scan_result.object_folder,

                        file_path=source_path,

                        observation_time=scan_result.metadata.date_obs,

                        filter_name=scan_result.metadata.filter_name,

                        ra_deg=variable_star.ra_deg,

                        dec_deg=variable_star.dec_deg,

                        x=16.0,

                        y=16.0,

                        flux=5000.0,

                        flux_error=20.0,

                        instrumental_magnitude=-9.0,

                        differential_magnitude=None,

                        is_variable=True,

                        is_reference=False,

                        flags=["Saturated source core detected (peak 65535 >= 65535; 2 saturated pixel(s))."],

                        peak_pixel_value=65535.0,

                        saturation_threshold=65535.0,

                        saturated_pixel_count=2,

                        is_saturated=True,

                    )

                ]



            pipeline = PhotometryPipeline()

            with patch("photometry_app.core.pipeline.CatalogService", SingleVariableCatalogService), patch(

                "photometry_app.core.pipeline.measure_targets", side_effect=fake_measure_targets

            ):

                report = pipeline.process_object(root, "M42")



            self.assertEqual(len(report.light_curves), 0)

            self.assertEqual(len(report.measurements), 0)

            self.assertTrue(any("skipped 1 selected variable star" in note.lower() for note in report.notes))

            self.assertTrue(any("saturated" in note.lower() for note in report.file_notes.get("frame_001.fits", [])))



    def test_process_object_keeps_near_saturated_selected_variable_star(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            self._write_solved_fits(object_dir / "frame_001.fits")



            settings_path = root / ".photometry-settings.json"

            settings_path.write_text(json.dumps({"cache_dir": ".photometry-cache", "saturation_filter_enabled": True}), encoding="utf-8")



            class SingleVariableCatalogService:

                def __init__(self, cache_dir: Path) -> None:

                    self.cache_dir = cache_dir



                def query_field_catalog(self, solved_field: object) -> FieldCatalog:

                    return FieldCatalog(

                        center_ra_deg=83.822,

                        center_dec_deg=-5.391,

                        radius_deg=0.25,

                        gaia_stars=[CatalogStar("gaia-dr3", "gaia-ref", "gaia-ref", 83.8223, -5.3913, 11.0, False)],

                        variable_stars=[CatalogStar("vsx", "var-1", "Var 1", 83.8220, -5.3910, 9.0, True)],

                    )



            def fake_measure_targets(**kwargs: object) -> list[object]:

                scan_result = kwargs["scan_result"]

                source_path = kwargs["source_path"]

                variable_star = kwargs["variable_stars"][0]

                return [

                    __import__("photometry_app.core.models", fromlist=["PhotometryMeasurement"]).PhotometryMeasurement(

                        source_id=variable_star.source_id,

                        source_name=variable_star.name,

                        catalog=variable_star.catalog,

                        object_name=scan_result.object_folder,

                        file_path=source_path,

                        observation_time=scan_result.metadata.date_obs,

                        filter_name=scan_result.metadata.filter_name,

                        ra_deg=variable_star.ra_deg,

                        dec_deg=variable_star.dec_deg,

                        x=16.0,

                        y=16.0,

                        flux=5000.0,

                        flux_error=20.0,

                        instrumental_magnitude=-9.0,

                        differential_magnitude=None,

                        is_variable=True,

                        is_reference=False,

                        flags=["Source core is near saturation (peak 65000 is 99.2% of 65535)."],

                        peak_pixel_value=65000.0,

                        saturation_threshold=65535.0,

                        saturated_pixel_count=0,

                        is_saturated=False,

                    )

                ]



            pipeline = PhotometryPipeline()

            with patch("photometry_app.core.pipeline.CatalogService", SingleVariableCatalogService), patch(

                "photometry_app.core.pipeline.measure_targets", side_effect=fake_measure_targets

            ):

                report = pipeline.process_object(root, "M42")



            self.assertEqual(len(report.measurements), 1)

            self.assertFalse(any("skipped 1 selected variable star" in note.lower() for note in report.notes))

            self.assertTrue(report.measurements[0].is_near_saturated)



    def test_process_object_analyze_best_filters_variable_targets_by_adu_range(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            self._write_solved_fits(object_dir / "frame_001.fits")

            (root / ".photometry-settings.json").write_text(json.dumps({"cache_dir": ".photometry-cache"}), encoding="utf-8")

            class MixedVariableCatalogService:

                def __init__(self, cache_dir: Path) -> None:

                    self.cache_dir = cache_dir

                def query_field_catalog(self, solved_field: object) -> FieldCatalog:

                    return FieldCatalog(

                        center_ra_deg=83.822,

                        center_dec_deg=-5.391,

                        radius_deg=0.25,

                        gaia_stars=[CatalogStar("gaia-dr3", "gaia-ref", "gaia-ref", 83.8223, -5.3913, 11.0, False)],

                        variable_stars=[

                            CatalogStar("vsx", "var-dim", "Dim", 83.8220, -5.3910, 12.0, True),

                            CatalogStar("vsx", "var-good", "Good", 83.8221, -5.3911, 11.0, True),

                            CatalogStar("vsx", "var-hot", "Hot", 83.8222, -5.3912, 10.0, True),

                        ],

                    )

            def fake_measure_targets(**kwargs: object) -> list[PhotometryMeasurement]:

                scan_result = kwargs["scan_result"]

                source_path = kwargs["source_path"]

                variable_stars = list(kwargs["variable_stars"])

                rows: list[PhotometryMeasurement] = []

                for variable_star in variable_stars:

                    if variable_star.source_id == "var-dim":

                        peak_pixel_value = 700.0

                    elif variable_star.source_id == "var-hot":

                        peak_pixel_value = 60000.0

                    else:

                        peak_pixel_value = 12000.0

                    rows.append(

                        PhotometryMeasurement(

                            source_id=variable_star.source_id,

                            source_name=variable_star.name,

                            catalog=variable_star.catalog,

                            object_name=scan_result.object_folder,

                            file_path=source_path,

                            observation_time=scan_result.metadata.date_obs,

                            filter_name=scan_result.metadata.filter_name,

                            ra_deg=variable_star.ra_deg,

                            dec_deg=variable_star.dec_deg,

                            x=16.0,

                            y=16.0,

                            flux=5000.0,

                            flux_error=20.0,

                            instrumental_magnitude=-9.0,

                            differential_magnitude=None,

                            is_variable=True,

                            is_reference=False,

                            flags=[],

                            peak_pixel_value=peak_pixel_value,

                            background_level=100.0,

                            saturation_threshold=65535.0,

                            saturated_pixel_count=0,

                            is_saturated=False,

                        )

                    )

                return rows

            estimate_result = type("EstimateResult", (), {"dimmest_visible_magnitude": 14.0})()

            pipeline = PhotometryPipeline()

            with (

                patch("photometry_app.core.pipeline.CatalogService", MixedVariableCatalogService),

                patch("photometry_app.core.pipeline.estimate_visible_magnitude_limit", return_value=estimate_result),

                patch("photometry_app.core.pipeline.measure_targets", side_effect=fake_measure_targets),

            ):

                report = pipeline.process_object(root, "M42", analyze_best_targets=True)

            self.assertEqual([star.source_id for star in report.field_catalog.variable_stars], ["var-good"])

            self.assertEqual([measurement.source_id for measurement in report.measurements], ["var-good"])

            self.assertTrue(any("ADU screen kept 1 of 3" in note for note in report.notes))



    def test_resolve_summary_fields_reuses_single_solve_when_aligned_option_enabled(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            self._write_unsolved_fits(object_dir / "frame_001.fits")

            self._write_unsolved_fits(object_dir / "frame_002.fits")

            (root / ".photometry-settings.json").write_text(

                json.dumps({"cache_dir": ".photometry-cache", "astrometry_api_key": "demo", "assume_aligned_images": True}),

                encoding="utf-8",

            )



            pipeline = PhotometryPipeline()

            report = pipeline.scan_workspace(root)

            settings = AppSettings.from_root(root)

            RecordingAstrometryClient.calls = []



            with patch("photometry_app.core.pipeline.AstrometryNetClient", RecordingAstrometryClient):

                results = pipeline._resolve_summary_fields(report.object_summaries[0].files, settings)



            self.assertEqual(len(RecordingAstrometryClient.calls), 1)

            self.assertEqual(len(results), 2)

            self.assertTrue(all(result.solved_field is not None for _, result in results))

            self.assertTrue(any("Reused plate solve from aligned frame" in " ".join(result.reasons) for _, result in results))



    def test_resolve_summary_fields_reuses_aligned_frame_wcs_from_one_representative_solve(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            first_path = object_dir / "frame_001.xisf"

            second_path = object_dir / "frame_002.xisf"

            self._write_xisf_with_wcs(first_path)

            self._write_xisf_with_wcs(second_path)

            first_path.with_suffix(".xdrz").write_text("sidecar", encoding="utf-8")

            second_path.with_suffix(".xdrz").write_text("sidecar", encoding="utf-8")

            (root / ".photometry-settings.json").write_text(

                json.dumps({"cache_dir": ".photometry-cache", "astrometry_api_key": "demo"}),

                encoding="utf-8",

            )



            pipeline = PhotometryPipeline()

            report = pipeline.scan_workspace(root)

            settings = AppSettings.from_root(root)

            RecordingAstrometryClient.calls = []



            with patch("photometry_app.core.pipeline.AstrometryNetClient", RecordingAstrometryClient):

                results = pipeline._resolve_summary_fields(report.object_summaries[0].files, settings)



            self.assertEqual(len(RecordingAstrometryClient.calls), 1)

            self.assertIsNotNone(RecordingAstrometryClient.calls[0][1])

            self.assertEqual(len(results), 2)

            self.assertTrue(all(result.solved_field is not None for _, result in results))

            self.assertTrue(any("Reused plate solve from aligned frame" in " ".join(result.reasons) for _, result in results))



    def test_resolve_summary_fields_submits_remote_solves_in_parallel(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            for index in range(3):

                self._write_unsolved_fits(object_dir / f"frame_{index:03d}.fits")

            (root / ".photometry-settings.json").write_text(

                json.dumps({"cache_dir": ".photometry-cache", "astrometry_api_key": "demo"}),

                encoding="utf-8",

            )



            pipeline = PhotometryPipeline()

            report = pipeline.scan_workspace(root)

            settings = AppSettings.from_root(root)

            SlowAstrometryClient.calls = []



            started = time.perf_counter()

            with patch("photometry_app.core.pipeline.AstrometryNetClient", SlowAstrometryClient):

                results = pipeline._resolve_summary_fields(report.object_summaries[0].files, settings)

            elapsed = time.perf_counter() - started



            self.assertEqual(len(results), 3)

            self.assertEqual(len(SlowAstrometryClient.calls), 3)

            self.assertLess(elapsed, 0.5)

    def test_resolve_summary_fields_uses_metadata_seeded_gaia_without_api_key(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "WASP-12b"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "nina_binned_frame.fits"

            header = fits.Header()

            header["DATE-OBS"] = "2026-01-20T02:11:37"

            header["FILTER"] = "R"

            header["EXPTIME"] = 230.0

            header["OBJECT"] = "WASP-12b"

            header["RA"] = 97.6344880776984

            header["DEC"] = 29.6913781086925

            header["FOCALLEN"] = 2939.0

            header["XPIXSZ"] = 7.52

            header["YPIXSZ"] = 7.52

            header["XBINNING"] = 2

            header["YBINNING"] = 2

            header["SWCREATE"] = "N.I.N.A. 3.2.0.9001 (x64)"

            fits.PrimaryHDU(data=np.ones((32, 48), dtype=np.float32), header=header).writeto(image_path)

            (root / ".photometry-settings.json").write_text(

                json.dumps({"cache_dir": ".photometry-cache"}),

                encoding="utf-8",

            )

            pipeline = PhotometryPipeline()

            report = pipeline.scan_workspace(root)

            settings = AppSettings.from_root(root)

            solved_field = SolvedField(

                center_ra_deg=97.6355,

                center_dec_deg=29.6727,

                radius_deg=0.42,

                width=48,

                height=32,

                wcs_path=image_path,

            )

            local_result = PlateSolveResult(

                source_path=image_path,

                status=WcsStatus.SOLVED,

                solved_field=solved_field,

                reasons=[],

            )

            with (

                patch(

                    "photometry_app.core.pipeline.solve_wcs_from_metadata_and_gaia",

                    return_value=local_result,

                ) as local_solver,

                patch("photometry_app.core.pipeline.AstrometryNetClient") as astrometry_client,

            ):

                results = pipeline._resolve_summary_fields(report.object_summaries[0].files, settings)

            local_solver.assert_called_once()

            astrometry_client.assert_not_called()

            self.assertEqual(len(results), 1)

            self.assertIsNotNone(results[0][1].solved_field)

            self.assertEqual(results[0][1].solved_field.center_ra_deg, 97.6355)

            self.assertIn(

                "Recovered WCS via metadata-seeded Gaia matching.",

                results[0][1].reasons,

            )



    def test_preview_variable_selection_falls_back_to_alternate_solved_field_catalog(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "HAT-P-12b"

            object_dir.mkdir(parents=True)

            self._write_solved_fits_at(object_dir / "frame_a.fits", 209.4619388516007, 43.32652575561414)

            self._write_solved_fits_at(object_dir / "frame_b.fits", 209.46915121774558, 43.34629510104009)



            settings_path = root / ".photometry-settings.json"

            settings_path.write_text(json.dumps({"cache_dir": ".photometry-cache"}), encoding="utf-8")



            class AlternateFieldCatalogService:

                def __init__(self, cache_dir: Path) -> None:

                    self.cache_dir = cache_dir



                def query_field_catalog(self, solved_field: object) -> FieldCatalog:

                    if round(solved_field.center_ra_deg, 3) == round(209.4619388516007, 3):

                        return FieldCatalog(

                            center_ra_deg=solved_field.center_ra_deg,

                            center_dec_deg=solved_field.center_dec_deg,

                            radius_deg=solved_field.radius_deg,

                            gaia_stars=[],

                            variable_stars=[],

                        )

                    return FieldCatalog(

                        center_ra_deg=solved_field.center_ra_deg,

                        center_dec_deg=solved_field.center_dec_deg,

                        radius_deg=solved_field.radius_deg,

                        gaia_stars=[CatalogStar("gaia-dr3", "gaia-ref", "gaia-ref", solved_field.center_ra_deg, solved_field.center_dec_deg, 11.0, False)],

                        variable_stars=[CatalogStar("vsx", "hat-p-12", "HAT-P-12", solved_field.center_ra_deg, solved_field.center_dec_deg, 12.8, True)],

                    )



            pipeline = PhotometryPipeline()

            with patch("photometry_app.core.pipeline.CatalogService", AlternateFieldCatalogService):

                preview = pipeline.preview_variable_selection(root, "HAT-P-12b")



            self.assertEqual(preview.total_variable_stars_found, 1)

            self.assertEqual(preview.analyzed_variable_star_count, 1)



    def test_process_object_limits_to_brightest_variable_star_fraction(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "solved.fits"

            self._write_solved_fits(image_path)



            def fake_measure_targets(**kwargs: object) -> list[object]:

                variable_stars = kwargs["variable_stars"]

                self.assertEqual(len(variable_stars), 1)

                self.assertEqual(variable_stars[0].source_id, "var-bright")

                scan_result = kwargs["scan_result"]

                source_path = kwargs["source_path"]

                return [

                    __import__("photometry_app.core.models", fromlist=["PhotometryMeasurement"]).PhotometryMeasurement(

                        source_id="var-bright",

                        source_name="Bright",

                        catalog="vsx",

                        object_name=scan_result.object_folder,

                        file_path=source_path,

                        observation_time=scan_result.metadata.date_obs,

                        filter_name=scan_result.metadata.filter_name,

                        ra_deg=83.8220,

                        dec_deg=-5.3910,

                        x=16.0,

                        y=16.0,

                        flux=5000.0,

                        flux_error=20.0,

                        instrumental_magnitude=-9.0,

                        differential_magnitude=None,

                        is_variable=True,

                        is_reference=False,

                        flags=[],

                    )

                ]



            class ManyVariableCatalogService:

                def __init__(self, cache_dir: Path) -> None:

                    self.cache_dir = cache_dir



                def query_field_catalog(self, solved_field: object) -> FieldCatalog:

                    return FieldCatalog(

                        center_ra_deg=83.822,

                        center_dec_deg=-5.391,

                        radius_deg=0.25,

                        gaia_stars=[CatalogStar("gaia-dr3", "gaia-ref", "gaia-ref", 83.8223, -5.3913, 11.0, False)],

                        variable_stars=[

                            CatalogStar("vsx", "var-bright", "Bright", 83.8220, -5.3910, 9.0, True),

                            CatalogStar("vsx", "var-mid", "Mid", 83.8230, -5.3920, 11.0, True),

                            CatalogStar("vsx", "var-faint", "Faint", 83.8240, -5.3930, 13.0, True),

                            CatalogStar("vsx", "var-fainter", "Fainter", 83.8250, -5.3940, 14.0, True),

                            CatalogStar("vsx", "var-faintest", "Faintest", 83.8260, -5.3950, 15.0, True),

                            CatalogStar("vsx", "var-last", "Last", 83.8270, -5.3960, 16.0, True),

                            CatalogStar("vsx", "var-none", "NoMag", 83.8280, -5.3970, None, True),

                            CatalogStar("vsx", "var-none2", "NoMag2", 83.8290, -5.3980, None, True),

                            CatalogStar("vsx", "var-none3", "NoMag3", 83.8300, -5.3990, None, True),

                            CatalogStar("vsx", "var-none4", "NoMag4", 83.8310, -5.4000, None, True),

                        ],

                    )



            settings_path = root / ".photometry-settings.json"

            settings_path.write_text(

                json.dumps({"cache_dir": ".photometry-cache", "variable_star_limit_mode": "percent", "variable_star_limit_value": 10}),

                encoding="utf-8",

            )



            pipeline = PhotometryPipeline()

            with patch("photometry_app.core.pipeline.CatalogService", ManyVariableCatalogService), patch(

                "photometry_app.core.pipeline.measure_targets", side_effect=fake_measure_targets

            ):

                report = pipeline.process_object(root, "M42")



            self.assertEqual(report.total_variable_stars_found, 10)

            self.assertEqual(report.variable_star_limit_mode, VariableStarLimitMode.PERCENT)

            self.assertEqual(report.variable_star_limit_value, 10)

            self.assertEqual(len(report.field_catalog.variable_stars), 1)

            self.assertEqual(report.field_catalog.variable_stars[0].source_id, "var-bright")



    def test_process_object_limits_to_brightest_variable_star_count(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "solved.fits"

            self._write_solved_fits(image_path)



            def fake_measure_targets(**kwargs: object) -> list[object]:

                variable_stars = kwargs["variable_stars"]

                self.assertEqual(len(variable_stars), 2)

                self.assertEqual([item.source_id for item in variable_stars], ["var-bright", "var-mid"])

                return []



            class ManyVariableCatalogService:

                def __init__(self, cache_dir: Path) -> None:

                    self.cache_dir = cache_dir



                def query_field_catalog(self, solved_field: object) -> FieldCatalog:

                    return FieldCatalog(

                        center_ra_deg=83.822,

                        center_dec_deg=-5.391,

                        radius_deg=0.25,

                        gaia_stars=[CatalogStar("gaia-dr3", "gaia-ref", "gaia-ref", 83.8223, -5.3913, 11.0, False)],

                        variable_stars=[

                            CatalogStar("vsx", "var-bright", "Bright", 83.8220, -5.3910, 9.0, True),

                            CatalogStar("vsx", "var-mid", "Mid", 83.8230, -5.3920, 11.0, True),

                            CatalogStar("vsx", "var-faint", "Faint", 83.8240, -5.3930, 13.0, True),

                        ],

                    )



            settings_path = root / ".photometry-settings.json"

            settings_path.write_text(

                json.dumps({"cache_dir": ".photometry-cache", "variable_star_limit_mode": "count", "variable_star_limit_value": 2}),

                encoding="utf-8",

            )



            pipeline = PhotometryPipeline()

            with patch("photometry_app.core.pipeline.CatalogService", ManyVariableCatalogService), patch(

                "photometry_app.core.pipeline.measure_targets", side_effect=fake_measure_targets

            ):

                report = pipeline.process_object(root, "M42")



            self.assertEqual(report.total_variable_stars_found, 3)

            self.assertEqual(report.variable_star_limit_mode, VariableStarLimitMode.COUNT)

            self.assertEqual(report.variable_star_limit_value, 2)

            self.assertEqual(len(report.field_catalog.variable_stars), 2)



    def test_preview_variable_selection_reports_configured_analyzed_count(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "solved.fits"

            self._write_solved_fits(image_path)



            class ManyVariableCatalogService:

                def __init__(self, cache_dir: Path) -> None:

                    self.cache_dir = cache_dir



                def query_field_catalog(self, solved_field: object) -> FieldCatalog:

                    return FieldCatalog(

                        center_ra_deg=83.822,

                        center_dec_deg=-5.391,

                        radius_deg=0.25,

                        gaia_stars=[],

                        variable_stars=[

                            CatalogStar("vsx", "var-bright", "Bright", 83.8220, -5.3910, 9.0, True),

                            CatalogStar("vsx", "var-mid", "Mid", 83.8230, -5.3920, 11.0, True),

                            CatalogStar("vsx", "var-faint", "Faint", 83.8240, -5.3930, 13.0, True),

                        ],

                    )



            settings_path = root / ".photometry-settings.json"

            settings_path.write_text(

                json.dumps({"cache_dir": ".photometry-cache", "variable_star_limit_mode": "count", "variable_star_limit_value": 2}),

                encoding="utf-8",

            )



            pipeline = PhotometryPipeline()

            with patch("photometry_app.core.pipeline.CatalogService", ManyVariableCatalogService):

                preview = pipeline.preview_variable_selection(root, "M42")



            self.assertEqual(preview.total_variable_stars_found, 3)

            self.assertEqual(preview.analyzed_variable_star_count, 2)

            self.assertEqual(preview.variable_star_limit_mode, VariableStarLimitMode.COUNT)

            self.assertEqual(preview.variable_star_limit_value, 2)



    def test_preview_variable_selection_returns_candidate_sources_and_saved_preselection(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "solved.fits"

            self._write_solved_fits(image_path)



            class MixedCatalogService:

                def __init__(self, cache_dir: Path) -> None:

                    self.cache_dir = cache_dir



                def query_field_catalog(self, solved_field: object) -> FieldCatalog:

                    return FieldCatalog(

                        center_ra_deg=83.822,

                        center_dec_deg=-5.391,

                        radius_deg=0.25,

                        gaia_stars=[],

                        variable_stars=[

                            CatalogStar("vsx", "var-bright", "Bright", 83.8220, -5.3910, 9.0, True),

                            CatalogStar("vsx", "var-mid", "Mid", 83.8230, -5.3920, 11.0, True),

                        ],

                        exoplanets=[

                            CatalogStar("nasa", "exo-1", "HAT-P-12 b", 83.8240, -5.3930, 12.0, True, object_type="exoplanet"),

                        ],

                    )



            settings_path = root / ".photometry-settings.json"

            settings_path.write_text(

                json.dumps(

                    {

                        "cache_dir": ".photometry-cache",

                        "selected_catalog_source_ids": {"M42": ["vsx:var-mid", "nasa:exo-1"]},

                    }

                ),

                encoding="utf-8",

            )



            previous_config_path = os.environ.get("CITIZEN_PHOTOMETRY_CONFIG_PATH")

            os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = str(settings_path)

            try:

                pipeline = PhotometryPipeline()

                with patch("photometry_app.core.pipeline.CatalogService", MixedCatalogService):

                    preview = pipeline.preview_variable_selection(root, "M42")

            finally:

                if previous_config_path is None:

                    os.environ.pop("CITIZEN_PHOTOMETRY_CONFIG_PATH", None)

                else:

                    os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = previous_config_path



            self.assertEqual([item.name for item in preview.candidate_sources], ["Bright", "Mid", "HAT-P-12 b"])

            self.assertEqual(preview.preselected_source_keys, ["vsx:var-mid", "nasa:exo-1"])

            self.assertEqual(preview.analyzed_variable_star_count, 1)



    def test_preview_variable_selection_honors_preview_candidate_limits(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "solved.fits"

            self._write_solved_fits(image_path)



            class MixedCatalogService:

                def __init__(self, cache_dir: Path) -> None:

                    self.cache_dir = cache_dir



                def query_field_catalog(self, solved_field: object) -> FieldCatalog:

                    return FieldCatalog(

                        center_ra_deg=83.822,

                        center_dec_deg=-5.391,

                        radius_deg=0.25,

                        gaia_stars=[],

                        variable_stars=[

                            CatalogStar("vsx", "var-bright", "Bright", 83.8220, -5.3910, 9.0, True),

                            CatalogStar("vsx", "var-mid", "Mid", 83.8230, -5.3920, 11.0, True),

                            CatalogStar("vsx", "var-faint", "Faint", 83.8240, -5.3930, 13.0, True),

                        ],

                        exoplanets=[

                            CatalogStar("nasa", "exo-1", "HAT-P-12 b", 83.8240, -5.3930, 12.0, True, object_type="exoplanet"),

                        ],

                    )



            settings_path = root / ".photometry-settings.json"

            settings_path.write_text(

                json.dumps(

                    {

                        "cache_dir": ".photometry-cache",

                        "variable_star_limit_mode": "count",

                        "variable_star_limit_value": 2,

                        "preview_variable_star_max_count": 1,

                        "preview_variable_star_min_magnitude": 8.5,

                        "preview_variable_star_max_magnitude": 10.0,

                    }

                ),

                encoding="utf-8",

            )



            previous_config_path = os.environ.get("CITIZEN_PHOTOMETRY_CONFIG_PATH")

            os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = str(settings_path)

            try:

                pipeline = PhotometryPipeline()

                with patch("photometry_app.core.pipeline.CatalogService", MixedCatalogService):

                    preview = pipeline.preview_variable_selection(root, "M42")

            finally:

                if previous_config_path is None:

                    os.environ.pop("CITIZEN_PHOTOMETRY_CONFIG_PATH", None)

                else:

                    os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = previous_config_path



            self.assertEqual(preview.total_variable_stars_found, 3)

            self.assertEqual([item.name for item in preview.candidate_sources], ["Bright", "HAT-P-12 b"])

            self.assertEqual(preview.preselected_source_keys, ["vsx:var-bright"])

            self.assertTrue(any("magnitude 8.5 to 10.0" in note for note in preview.notes))



    def test_preview_variable_selection_keeps_saved_sources_visible_outside_preview_limits(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "solved.fits"

            self._write_solved_fits(image_path)



            class MixedCatalogService:

                def __init__(self, cache_dir: Path) -> None:

                    self.cache_dir = cache_dir



                def query_field_catalog(self, solved_field: object) -> FieldCatalog:

                    return FieldCatalog(

                        center_ra_deg=83.822,

                        center_dec_deg=-5.391,

                        radius_deg=0.25,

                        gaia_stars=[],

                        variable_stars=[

                            CatalogStar("vsx", "var-bright", "Bright", 83.8220, -5.3910, 9.0, True),

                            CatalogStar("vsx", "var-mid", "Mid", 83.8230, -5.3920, 11.0, True),

                            CatalogStar("vsx", "var-faint", "Faint", 83.8240, -5.3930, 13.0, True),

                        ],

                    )



            settings_path = root / ".photometry-settings.json"

            settings_path.write_text(

                json.dumps(

                    {

                        "cache_dir": ".photometry-cache",

                        "preview_variable_star_max_count": 1,

                        "preview_variable_star_min_magnitude": 8.5,

                        "preview_variable_star_max_magnitude": 10.0,

                        "selected_catalog_source_ids": {"M42": ["vsx:var-faint"]},

                    }

                ),

                encoding="utf-8",

            )



            previous_config_path = os.environ.get("CITIZEN_PHOTOMETRY_CONFIG_PATH")

            os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = str(settings_path)

            try:

                pipeline = PhotometryPipeline()

                with patch("photometry_app.core.pipeline.CatalogService", MixedCatalogService):

                    preview = pipeline.preview_variable_selection(root, "M42")

            finally:

                if previous_config_path is None:

                    os.environ.pop("CITIZEN_PHOTOMETRY_CONFIG_PATH", None)

                else:

                    os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = previous_config_path



            self.assertEqual([item.name for item in preview.candidate_sources], ["Bright", "Faint"])

            self.assertEqual(preview.preselected_source_keys, ["vsx:var-faint"])



    def test_process_object_honors_explicitly_selected_catalog_sources(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "solved.fits"

            self._write_solved_fits(image_path)



            def fake_measure_targets(**kwargs: object) -> list[object]:

                variable_stars = kwargs["variable_stars"]

                self.assertEqual(len(variable_stars), 1)

                self.assertEqual(variable_stars[0].source_id, "var-mid")

                return []



            class MixedCatalogService:

                def __init__(self, cache_dir: Path) -> None:

                    self.cache_dir = cache_dir



                def query_field_catalog(self, solved_field: object) -> FieldCatalog:

                    return FieldCatalog(

                        center_ra_deg=83.822,

                        center_dec_deg=-5.391,

                        radius_deg=0.25,

                        gaia_stars=[CatalogStar("gaia-dr3", "gaia-ref", "gaia-ref", 83.8223, -5.3913, 11.0, False)],

                        variable_stars=[

                            CatalogStar("vsx", "var-bright", "Bright", 83.8220, -5.3910, 9.0, True),

                            CatalogStar("vsx", "var-mid", "Mid", 83.8230, -5.3920, 11.0, True),

                        ],

                        exoplanets=[

                            CatalogStar("nasa", "exo-1", "HAT-P-12 b", 83.8240, -5.3930, 12.0, True, object_type="exoplanet"),

                            CatalogStar("nasa", "exo-2", "KELT-9 b", 83.8250, -5.3940, 12.5, True, object_type="exoplanet"),

                        ],

                    )



            settings_path = root / ".photometry-settings.json"

            settings_path.write_text(

                json.dumps(

                    {

                        "cache_dir": ".photometry-cache",

                        "variable_star_limit_mode": "percent",

                        "variable_star_limit_value": 100,

                        "selected_catalog_source_ids": {"M42": ["vsx:var-mid", "nasa:exo-1"]},

                    }

                ),

                encoding="utf-8",

            )



            pipeline = PhotometryPipeline()

            with patch("photometry_app.core.pipeline.CatalogService", MixedCatalogService), patch(

                "photometry_app.core.pipeline.measure_targets", side_effect=fake_measure_targets

            ):

                report = pipeline.process_object(root, "M42")



            self.assertEqual([item.source_id for item in report.field_catalog.variable_stars], ["var-mid"])

            self.assertEqual([item.source_id for item in report.field_catalog.exoplanets], ["exo-1"])

            self.assertTrue(any("explicitly selected" in note for note in report.notes))



    def test_process_object_can_filter_out_gaia_designations(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "solved.fits"

            self._write_solved_fits(image_path)



            def fake_measure_targets(**kwargs: object) -> list[object]:

                variable_stars = kwargs["variable_stars"]

                self.assertEqual(len(variable_stars), 1)

                self.assertEqual(variable_stars[0].name, "HW And")

                return []



            class MixedDesignationCatalogService:

                def __init__(self, cache_dir: Path) -> None:

                    self.cache_dir = cache_dir



                def query_field_catalog(self, solved_field: object) -> FieldCatalog:

                    return FieldCatalog(

                        center_ra_deg=83.822,

                        center_dec_deg=-5.391,

                        radius_deg=0.25,

                        gaia_stars=[CatalogStar("gaia-dr3", "gaia-ref", "gaia-ref", 83.8223, -5.3913, 11.0, False)],

                        variable_stars=[

                            CatalogStar("vsx", "gaia-var", "Gaia DR3 369045992621934464", 83.8220, -5.3910, 9.0, True),

                            CatalogStar("vsx", "named-var", "HW And", 83.8230, -5.3920, 11.0, True),

                        ],

                    )



            settings_path = root / ".photometry-settings.json"

            settings_path.write_text(

                json.dumps(

                    {

                        "cache_dir": ".photometry-cache",

                        "variable_star_designation_filters": [VariableStarDesignationFamily.NAMED.value],

                    }

                ),

                encoding="utf-8",

            )



            pipeline = PhotometryPipeline()

            with patch("photometry_app.core.pipeline.CatalogService", MixedDesignationCatalogService), patch(

                "photometry_app.core.pipeline.measure_targets", side_effect=fake_measure_targets

            ):

                report = pipeline.process_object(root, "M42")



            self.assertEqual(report.total_variable_stars_found, 2)

            self.assertEqual(len(report.field_catalog.variable_stars), 1)

            self.assertEqual(report.field_catalog.variable_stars[0].name, "HW And")

            self.assertEqual(report.variable_star_designation_filters, [VariableStarDesignationFamily.NAMED])



    def test_process_object_reports_progress_updates(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "solved.fits"

            self._write_solved_fits(image_path)



            progress_messages: list[str] = []



            def fake_measure_targets(**kwargs: object) -> list[object]:

                scan_result = kwargs["scan_result"]

                source_path = kwargs["source_path"]

                return [

                    __import__("photometry_app.core.models", fromlist=["PhotometryMeasurement"]).PhotometryMeasurement(

                        source_id="vsx-var",

                        source_name="R Cas",

                        catalog="vsx",

                        object_name=scan_result.object_folder,

                        file_path=source_path,

                        observation_time=scan_result.metadata.date_obs,

                        filter_name=scan_result.metadata.filter_name,

                        ra_deg=83.8220,

                        dec_deg=-5.3910,

                        x=16.0,

                        y=16.0,

                        flux=5000.0,

                        flux_error=20.0,

                        instrumental_magnitude=-9.0,

                        differential_magnitude=None,

                        is_variable=True,

                        is_reference=False,

                        flags=[],

                    ),

                ]



            settings_path = root / ".photometry-settings.json"

            settings_path.write_text(json.dumps({"cache_dir": ".photometry-cache"}), encoding="utf-8")



            pipeline = PhotometryPipeline()

            with patch("photometry_app.core.pipeline.CatalogService", FakeCatalogService), patch(

                "photometry_app.core.pipeline.measure_targets", side_effect=fake_measure_targets

            ):

                report = pipeline.process_object(root, "M42", progress_callback=progress_messages.append)



            self.assertEqual(len(report.measurements), 1)

            self.assertTrue(any("Starting processing for M42." in item for item in progress_messages))

            self.assertTrue(any("Catalog lookup complete" in item for item in progress_messages))

            self.assertTrue(any("Completed processing" in item for item in progress_messages))



    def test_process_object_uses_resolved_aperture_profile(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "solved.fits"

            self._write_solved_fits(image_path)



            def fake_measure_targets(**kwargs: object) -> list[object]:

                self.assertEqual(kwargs["aperture_radius"], 4.25)

                self.assertEqual(kwargs["annulus_inner_radius"], 7.5)

                self.assertEqual(kwargs["annulus_outer_radius"], 11.25)

                return []



            settings_path = root / ".photometry-settings.json"

            settings_path.write_text(json.dumps({"cache_dir": ".photometry-cache"}), encoding="utf-8")



            pipeline = PhotometryPipeline()

            with patch("photometry_app.core.pipeline.CatalogService", FakeCatalogService), patch(

                "photometry_app.core.pipeline.resolve_aperture_profile",

                return_value=ApertureProfile(

                    aperture_radius=4.25,

                    annulus_inner_radius=7.5,

                    annulus_outer_radius=11.25,

                    mode=__import__("photometry_app.core.models", fromlist=["PhotometryApertureMode"]).PhotometryApertureMode.FWHM_SCALED,

                    fwhm_pixels=2.5,

                    note="Adaptive aperture sizing used frame FWHM 2.50 px.",

                ),

            ), patch("photometry_app.core.pipeline.measure_targets", side_effect=fake_measure_targets):

                report = pipeline.process_object(root, "M42")



            self.assertIn("Adaptive aperture sizing used frame FWHM 2.50 px.", report.file_notes["solved.fits"])



    def test_process_object_reuses_cached_measurements_on_second_run(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "solved.fits"

            self._write_solved_fits(image_path)



            measure_call_count = {"count": 0}



            def fake_measure_targets(**kwargs: object) -> list[object]:

                measure_call_count["count"] += 1

                scan_result = kwargs["scan_result"]

                source_path = kwargs["source_path"]

                return [

                    __import__("photometry_app.core.models", fromlist=["PhotometryMeasurement"]).PhotometryMeasurement(

                        source_id="vsx-var",

                        source_name="R Cas",

                        catalog="vsx",

                        object_name=scan_result.object_folder,

                        file_path=source_path,

                        observation_time=scan_result.metadata.date_obs,

                        filter_name=scan_result.metadata.filter_name,

                        ra_deg=83.8220,

                        dec_deg=-5.3910,

                        x=16.0,

                        y=16.0,

                        flux=5000.0,

                        flux_error=20.0,

                        instrumental_magnitude=-9.0,

                        differential_magnitude=None,

                        is_variable=True,

                        is_reference=False,

                        flags=[],

                    ),

                    __import__("photometry_app.core.models", fromlist=["PhotometryMeasurement"]).PhotometryMeasurement(

                        source_id="gaia-ref",

                        source_name="gaia-ref",

                        catalog="gaia-dr3",

                        object_name=scan_result.object_folder,

                        file_path=source_path,

                        observation_time=scan_result.metadata.date_obs,

                        filter_name=scan_result.metadata.filter_name,

                        ra_deg=83.8223,

                        dec_deg=-5.3913,

                        x=17.0,

                        y=17.0,

                        flux=7000.0,

                        flux_error=20.0,

                        instrumental_magnitude=-9.6,

                        differential_magnitude=None,

                        is_variable=False,

                        is_reference=True,

                        flags=[],

                    ),

                ]



            FakeCatalogService.call_count = 0

            pipeline = PhotometryPipeline()

            settings_path = root / ".photometry-settings.json"

            settings_path.write_text(json.dumps({"cache_dir": ".photometry-cache"}), encoding="utf-8")



            with patch("photometry_app.core.pipeline.CatalogService", FakeCatalogService), patch(

                "photometry_app.core.pipeline.measure_targets", side_effect=fake_measure_targets

            ):

                first_report = pipeline.process_object(root, "M42")

                second_report = pipeline.process_object(root, "M42")



            self.assertEqual(measure_call_count["count"], 1)

            self.assertEqual(FakeCatalogService.call_count, 2)

            self.assertEqual(len(first_report.measurements), 2)

            self.assertEqual(len(second_report.measurements), 2)

            self.assertIn("Reused cached photometry measurements.", second_report.file_notes["solved.fits"])



            run_log_path = root / ".photometry-cache" / "runs" / "M42.jsonl"

            self.assertTrue(run_log_path.exists())

            run_log_lines = run_log_path.read_text(encoding="utf-8").strip().splitlines()

            self.assertEqual(len(run_log_lines), 2)



    def test_process_object_ignores_measurement_cache_when_aperture_algorithm_changes(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "solved.fits"

            self._write_solved_fits(image_path)



            measure_call_count = {"count": 0}



            def fake_measure_targets(**kwargs: object) -> list[object]:

                measure_call_count["count"] += 1

                scan_result = kwargs["scan_result"]

                source_path = kwargs["source_path"]

                return [

                    __import__("photometry_app.core.models", fromlist=["PhotometryMeasurement"]).PhotometryMeasurement(

                        source_id="vsx-var",

                        source_name="R Cas",

                        catalog="vsx",

                        object_name=scan_result.object_folder,

                        file_path=source_path,

                        observation_time=scan_result.metadata.date_obs,

                        filter_name=scan_result.metadata.filter_name,

                        ra_deg=83.8220,

                        dec_deg=-5.3910,

                        x=16.0,

                        y=16.0,

                        flux=5000.0,

                        flux_error=20.0,

                        instrumental_magnitude=-9.0,

                        differential_magnitude=None,

                        is_variable=True,

                        is_reference=False,

                        aperture_radius=4.0 + measure_call_count["count"],

                        annulus_inner_radius=8.0 + measure_call_count["count"],

                        annulus_outer_radius=12.0 + measure_call_count["count"],

                        flags=[],

                    )

                ]



            pipeline = PhotometryPipeline()

            settings_path = root / ".photometry-settings.json"

            settings_path.write_text(json.dumps({"cache_dir": ".photometry-cache"}), encoding="utf-8")

            previous_config_path = os.environ.get("CITIZEN_PHOTOMETRY_CONFIG_PATH")

            os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = str(settings_path)



            try:

                with patch("photometry_app.core.pipeline.CatalogService", FakeCatalogService), patch(

                    "photometry_app.core.pipeline.measure_targets", side_effect=fake_measure_targets

                ):

                    pipeline.process_object(root, "M42")

                    cache_files = list((root / ".photometry-cache" / "measurements" / "M42").glob("*.json"))

                    self.assertEqual(len(cache_files), 1)

                    payload = json.loads(cache_files[0].read_text(encoding="utf-8"))

                    payload.pop("algorithm_version", None)

                    cache_files[0].write_text(json.dumps(payload, indent=2), encoding="utf-8")



                    progress_messages: list[str] = []

                    report = pipeline.process_object(root, "M42", progress_callback=progress_messages.append)

            finally:

                if previous_config_path is None:

                    os.environ.pop("CITIZEN_PHOTOMETRY_CONFIG_PATH", None)

                else:

                    os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = previous_config_path



            self.assertEqual(measure_call_count["count"], 2)

            self.assertIn("Cache miss: photometry aperture algorithm changed.", report.file_notes["solved.fits"])

            self.assertTrue(any("Cache miss: photometry aperture algorithm changed." in message for message in progress_messages))

            self.assertEqual(report.measurements[0].aperture_radius, 6.0)



    def test_process_object_invalidates_measurement_cache_when_aperture_settings_change(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "solved.fits"

            self._write_solved_fits(image_path)



            measure_call_count = {"count": 0}



            def fake_measure_targets(**kwargs: object) -> list[object]:

                measure_call_count["count"] += 1

                scan_result = kwargs["scan_result"]

                source_path = kwargs["source_path"]

                return [

                    __import__("photometry_app.core.models", fromlist=["PhotometryMeasurement"]).PhotometryMeasurement(

                        source_id="vsx-var",

                        source_name="R Cas",

                        catalog="vsx",

                        object_name=scan_result.object_folder,

                        file_path=source_path,

                        observation_time=scan_result.metadata.date_obs,

                        filter_name=scan_result.metadata.filter_name,

                        ra_deg=83.8220,

                        dec_deg=-5.3910,

                        x=16.0,

                        y=16.0,

                        flux=5000.0,

                        flux_error=20.0,

                        instrumental_magnitude=-9.0,

                        differential_magnitude=None,

                        is_variable=True,

                        is_reference=False,

                        flags=[],

                    )

                ]



            settings_path = root / ".photometry-settings.json"

            settings_path.write_text(json.dumps({"cache_dir": ".photometry-cache", "aperture_radius_pixels": 5.0}), encoding="utf-8")



            pipeline = PhotometryPipeline()

            with patch("photometry_app.core.pipeline.CatalogService", FakeCatalogService), patch(

                "photometry_app.core.pipeline.resolve_aperture_profile",

                return_value=ApertureProfile(5.0, 8.0, 12.0, __import__("photometry_app.core.models", fromlist=["PhotometryApertureMode"]).PhotometryApertureMode.FIXED),

            ), patch("photometry_app.core.pipeline.measure_targets", side_effect=fake_measure_targets):

                pipeline.process_object(root, "M42")



            settings_path.write_text(json.dumps({"cache_dir": ".photometry-cache", "aperture_radius_pixels": 6.0}), encoding="utf-8")



            with patch("photometry_app.core.pipeline.CatalogService", FakeCatalogService), patch(

                "photometry_app.core.pipeline.resolve_aperture_profile",

                return_value=ApertureProfile(6.0, 9.0, 13.0, __import__("photometry_app.core.models", fromlist=["PhotometryApertureMode"]).PhotometryApertureMode.FIXED),

            ), patch("photometry_app.core.pipeline.measure_targets", side_effect=fake_measure_targets):

                pipeline.process_object(root, "M42")



            self.assertEqual(measure_call_count["count"], 2)



    def test_process_object_ignores_corrupt_measurement_cache_and_recomputes(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "solved.fits"

            self._write_solved_fits(image_path)



            measure_call_count = {"count": 0}



            def fake_measure_targets(**kwargs: object) -> list[object]:

                measure_call_count["count"] += 1

                scan_result = kwargs["scan_result"]

                source_path = kwargs["source_path"]

                return [

                    __import__("photometry_app.core.models", fromlist=["PhotometryMeasurement"]).PhotometryMeasurement(

                        source_id="vsx-var",

                        source_name="R Cas",

                        catalog="vsx",

                        object_name=scan_result.object_folder,

                        file_path=source_path,

                        observation_time=scan_result.metadata.date_obs,

                        filter_name=scan_result.metadata.filter_name,

                        ra_deg=83.8220,

                        dec_deg=-5.3910,

                        x=16.0,

                        y=16.0,

                        flux=5000.0,

                        flux_error=20.0,

                        instrumental_magnitude=-9.0,

                        differential_magnitude=None,

                        is_variable=True,

                        is_reference=False,

                        flags=["synthetic flag"],

                    ),

                ]



            settings_path = root / ".photometry-settings.json"

            previous_config_path = os.environ.get("CITIZEN_PHOTOMETRY_CONFIG_PATH")

            os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = str(settings_path)

            try:

                settings_path.write_text(json.dumps({"cache_dir": ".photometry-cache"}), encoding="utf-8")



                stable_key = __import__("photometry_app.core.pipeline", fromlist=["_stable_file_key"])._stable_file_key(image_path)

                cache_path = root / ".photometry-cache" / "measurements" / "M42" / f"{stable_key}.json"

                cache_path.parent.mkdir(parents=True, exist_ok=True)

                cache_path.write_text("{not valid json", encoding="utf-8")



                pipeline = PhotometryPipeline()

                with patch("photometry_app.core.pipeline.CatalogService", FakeCatalogService), patch(

                    "photometry_app.core.pipeline.measure_targets", side_effect=fake_measure_targets

                ):

                    report = pipeline.process_object(root, "M42")

            finally:

                if previous_config_path is None:

                    os.environ.pop("CITIZEN_PHOTOMETRY_CONFIG_PATH", None)

                else:

                    os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = previous_config_path



            self.assertEqual(measure_call_count["count"], 1)

            self.assertIn("Ignored corrupt cached photometry measurements.", report.file_notes["solved.fits"])

            self.assertEqual(len(report.measurements), 1)



    def test_process_object_reports_cache_miss_reason_when_photometry_settings_change(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "solved.fits"

            self._write_solved_fits(image_path)



            measure_call_count = {"count": 0}



            def fake_measure_targets(**kwargs: object) -> list[object]:

                measure_call_count["count"] += 1

                scan_result = kwargs["scan_result"]

                source_path = kwargs["source_path"]

                return [

                    __import__("photometry_app.core.models", fromlist=["PhotometryMeasurement"]).PhotometryMeasurement(

                        source_id="vsx-var",

                        source_name="R Cas",

                        catalog="vsx",

                        object_name=scan_result.object_folder,

                        file_path=source_path,

                        observation_time=scan_result.metadata.date_obs,

                        filter_name=scan_result.metadata.filter_name,

                        ra_deg=83.8220,

                        dec_deg=-5.3910,

                        x=16.0,

                        y=16.0,

                        flux=5000.0,

                        flux_error=20.0,

                        instrumental_magnitude=-9.0,

                        differential_magnitude=None,

                        is_variable=True,

                        is_reference=False,

                        flags=["synthetic flag"],

                    ),

                ]



            settings_path = root / ".photometry-settings.json"

            previous_config_path = os.environ.get("CITIZEN_PHOTOMETRY_CONFIG_PATH")

            os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = str(settings_path)

            try:

                settings_path.write_text(json.dumps({"cache_dir": ".photometry-cache", "saturation_filter_enabled": True}), encoding="utf-8")



                pipeline = PhotometryPipeline()

                with patch("photometry_app.core.pipeline.CatalogService", FakeCatalogService), patch(

                    "photometry_app.core.pipeline.measure_targets", side_effect=fake_measure_targets

                ):

                    pipeline.process_object(root, "M42")



                    settings_path.write_text(json.dumps({"cache_dir": ".photometry-cache", "saturation_filter_enabled": False}), encoding="utf-8")

                    progress_messages: list[str] = []

                    report = pipeline.process_object(root, "M42", progress_callback=progress_messages.append)

            finally:

                if previous_config_path is None:

                    os.environ.pop("CITIZEN_PHOTOMETRY_CONFIG_PATH", None)

                else:

                    os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = previous_config_path



            self.assertEqual(measure_call_count["count"], 2)

            self.assertIn("Cache miss: photometry settings changed.", report.file_notes["solved.fits"])

            self.assertTrue(any("Cache miss: photometry settings changed." in message for message in progress_messages))



    def test_process_object_handles_astrometry_failure_without_crashing(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "UnsolvedObject"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "unsolved.fits"

            self._write_unsolved_fits(image_path)



            settings_path = root / ".photometry-settings.json"

            settings_path.write_text(json.dumps({"cache_dir": ".photometry-cache", "astrometry_api_key": "demo"}), encoding="utf-8")



            pipeline = PhotometryPipeline()

            with patch("photometry_app.core.pipeline.AstrometryNetClient", FailingAstrometryClient):

                report = pipeline.process_object(root, "UnsolvedObject")



            self.assertEqual(report.solved_files, 0)

            self.assertEqual(len(report.measurements), 0)

            self.assertTrue(any("WCS resolution failed" in note for note in report.notes))



    def test_process_object_handles_empty_variable_catalog(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "NoVariableField"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "solved.fits"

            self._write_solved_fits(image_path)



            settings_path = root / ".photometry-settings.json"

            settings_path.write_text(json.dumps({"cache_dir": ".photometry-cache"}), encoding="utf-8")



            pipeline = PhotometryPipeline()

            with patch("photometry_app.core.pipeline.CatalogService", EmptyCatalogService):

                report = pipeline.process_object(root, "NoVariableField")



            self.assertEqual(report.solved_files, 1)

            self.assertEqual(len(report.measurements), 0)

            self.assertEqual(len(report.light_curves), 0)

            self.assertTrue(any("No known VSX variable stars" in note for note in report.notes))



    def test_process_object_uses_saved_manual_aperture_config(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "solved.fits"

            self._write_solved_fits(image_path)



            settings_path = root / ".photometry-settings.json"

            settings_path.write_text(

                json.dumps(

                    {

                        "cache_dir": ".photometry-cache",

                        "manual_photometry_configs": {

                            "M42": {

                                "mode": ObjectPhotometryMode.MANUAL.value,

                                "reference_frame_name": "solved.fits",

                                "recenter_mode": RecenterMode.CENTROID_LIMITED.value,

                                "max_recenter_radius_pixels": 4.0,

                                "fallback_to_wcs_on_centroid_failure": True,

                                "sources": [

                                    {

                                        "source_id": "manual-target-1",

                                        "name": "M42",

                                        "role": ManualSourceRole.TARGET.value,

                                        "ra_deg": 83.8220,

                                        "dec_deg": -5.3910,

                                        "reference_frame_name": "solved.fits",

                                        "reference_x": 16.0,

                                        "reference_y": 16.0,

                                        "aperture_radius": 6.0,

                                        "annulus_inner_radius": 9.0,

                                        "annulus_outer_radius": 13.0,

                                    },

                                    {

                                        "source_id": "manual-comp-1",

                                        "name": "Comp 1",

                                        "role": ManualSourceRole.COMPARISON.value,

                                        "ra_deg": 83.8223,

                                        "dec_deg": -5.3913,

                                        "reference_frame_name": "solved.fits",

                                        "reference_x": 17.0,

                                        "reference_y": 17.0,

                                        "aperture_radius": 6.0,

                                        "annulus_inner_radius": 9.0,

                                        "annulus_outer_radius": 13.0,

                                    },

                                    {

                                        "source_id": "manual-check-1",

                                        "name": "Check Star",

                                        "role": ManualSourceRole.CHECK.value,

                                        "ra_deg": 83.8230,

                                        "dec_deg": -5.3920,

                                        "reference_frame_name": "solved.fits",

                                        "reference_x": 18.0,

                                        "reference_y": 18.0,

                                        "aperture_radius": 6.0,

                                        "annulus_inner_radius": 9.0,

                                        "annulus_outer_radius": 13.0,

                                    },

                                ],

                            }

                        },

                    }

                ),

                encoding="utf-8",

            )



            def fake_measure_manual_sources(**kwargs: object) -> list[object]:

                manual_config = kwargs["manual_config"]

                self.assertEqual(manual_config.mode, ObjectPhotometryMode.MANUAL)

                self.assertEqual(manual_config.target_source.source_id, "manual-target-1")

                self.assertEqual(len(manual_config.comparison_sources), 1)

                scan_result = kwargs["scan_result"]

                source_path = kwargs["source_path"]

                models = __import__("photometry_app.core.models", fromlist=["PhotometryMeasurement"])

                return [

                    models.PhotometryMeasurement(

                        source_id="manual-target-1",

                        source_name="M42",

                        catalog="manual",

                        object_name=scan_result.object_folder,

                        file_path=source_path,

                        observation_time=scan_result.metadata.date_obs,

                        filter_name=scan_result.metadata.filter_name,

                        ra_deg=83.8220,

                        dec_deg=-5.3910,

                        x=16.0,

                        y=16.0,

                        flux=5000.0,

                        flux_error=20.0,

                        instrumental_magnitude=-9.0,

                        differential_magnitude=None,

                        is_variable=True,

                        is_reference=False,

                        flags=[],

                        comparison_source_ids=["manual-comp-1"],

                        comparison_source_names=["Comp 1"],

                        aperture_radius=6.0,

                        annulus_inner_radius=9.0,

                        annulus_outer_radius=13.0,

                    ),

                    models.PhotometryMeasurement(

                        source_id="manual-comp-1",

                        source_name="Comp 1",

                        catalog="manual",

                        object_name=scan_result.object_folder,

                        file_path=source_path,

                        observation_time=scan_result.metadata.date_obs,

                        filter_name=scan_result.metadata.filter_name,

                        ra_deg=83.8223,

                        dec_deg=-5.3913,

                        x=17.0,

                        y=17.0,

                        flux=7000.0,

                        flux_error=20.0,

                        instrumental_magnitude=-9.6,

                        differential_magnitude=None,

                        is_variable=False,

                        is_reference=True,

                        flags=[],

                        aperture_radius=6.0,

                        annulus_inner_radius=9.0,

                        annulus_outer_radius=13.0,

                    ),

                    models.PhotometryMeasurement(

                        source_id="manual-check-1",

                        source_name="Check Star",

                        catalog="manual",

                        object_name=scan_result.object_folder,

                        file_path=source_path,

                        observation_time=scan_result.metadata.date_obs,

                        filter_name=scan_result.metadata.filter_name,

                        ra_deg=83.8230,

                        dec_deg=-5.3920,

                        x=18.0,

                        y=18.0,

                        flux=4900.0,

                        flux_error=22.0,

                        instrumental_magnitude=-8.9,

                        differential_magnitude=None,

                        is_variable=True,

                        is_reference=False,

                        is_check=True,

                        flags=[],

                        comparison_source_ids=["manual-comp-1"],

                        comparison_source_names=["Comp 1"],

                        aperture_radius=6.0,

                        annulus_inner_radius=9.0,

                        annulus_outer_radius=13.0,

                    ),

                ]



            pipeline = PhotometryPipeline()

            with patch("photometry_app.core.pipeline.CatalogService", FakeCatalogService), patch(

                "photometry_app.core.pipeline.measure_manual_sources", side_effect=fake_measure_manual_sources

            ), patch("photometry_app.core.pipeline.measure_targets") as measure_targets_mock:

                report = pipeline.process_object(root, "M42")



            measure_targets_mock.assert_not_called()

            self.assertEqual(report.photometry_mode, ObjectPhotometryMode.MANUAL)

            self.assertIsNotNone(report.manual_config)

            self.assertEqual(len(report.measurements), 3)

            self.assertEqual(len(report.light_curves), 2)

            self.assertEqual(report.field_catalog.variable_stars[0].catalog, "manual")

            self.assertEqual(report.reference_stars[0].catalog, "manual")


    def test_process_object_appends_auto_saved_manual_apertures_to_catalog_results(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "solved.fits"

            self._write_solved_fits(image_path)

            config_path = root / "settings.json"

            state_path = root / "state.json"

            state_path.write_text("{}", encoding="utf-8")

            config_path.write_text(

                json.dumps(

                    {

                        "cache_dir": ".photometry-cache",

                        "manual_photometry_configs": {

                            "M42": {

                                "mode": ObjectPhotometryMode.AUTO.value,

                                "reference_frame_name": "solved.fits",

                                "sources": [

                                    {

                                        "source_id": "manual-target-1",

                                        "name": "Manual Target",

                                        "role": ManualSourceRole.TARGET.value,

                                        "ra_deg": 83.8220,

                                        "dec_deg": -5.3910,

                                        "reference_frame_name": "solved.fits",

                                        "reference_x": 16.0,

                                        "reference_y": 16.0,

                                        "aperture_radius": 6.0,

                                        "annulus_inner_radius": 9.0,

                                        "annulus_outer_radius": 13.0,

                                    },

                                    {

                                        "source_id": "manual-comp-1",

                                        "name": "Manual Comp",

                                        "role": ManualSourceRole.COMPARISON.value,

                                        "ra_deg": 83.8223,

                                        "dec_deg": -5.3913,

                                        "reference_frame_name": "solved.fits",

                                        "reference_x": 17.0,

                                        "reference_y": 17.0,

                                        "aperture_radius": 6.0,

                                        "annulus_inner_radius": 9.0,

                                        "annulus_outer_radius": 13.0,

                                    },

                                ],

                            }

                        },

                    }

                ),

                encoding="utf-8",

            )


            def fake_measure_targets(**kwargs: object) -> list[object]:

                self.assertEqual([star.catalog for star in kwargs["variable_stars"]], ["vsx"])

                self.assertNotIn("manual", [star.catalog for star in kwargs["reference_stars"]])

                scan_result = kwargs["scan_result"]

                source_path = kwargs["source_path"]

                return [

                    PhotometryMeasurement(

                        source_id="vsx-var",

                        source_name="R Cas",

                        catalog="vsx",

                        object_name=scan_result.object_folder,

                        file_path=source_path,

                        observation_time=scan_result.metadata.date_obs,

                        filter_name=scan_result.metadata.filter_name,

                        ra_deg=83.8220,

                        dec_deg=-5.3910,

                        x=16.0,

                        y=16.0,

                        flux=5000.0,

                        flux_error=20.0,

                        instrumental_magnitude=-9.0,

                        differential_magnitude=None,

                        is_variable=True,

                        is_reference=False,

                        flags=[],

                        comparison_source_ids=["gaia-ref"],

                        comparison_source_names=["gaia-ref"],

                        aperture_radius=6.0,

                        annulus_inner_radius=9.0,

                        annulus_outer_radius=13.0,

                    ),

                    PhotometryMeasurement(

                        source_id="gaia-ref",

                        source_name="gaia-ref",

                        catalog="gaia-dr3",

                        object_name=scan_result.object_folder,

                        file_path=source_path,

                        observation_time=scan_result.metadata.date_obs,

                        filter_name=scan_result.metadata.filter_name,

                        ra_deg=83.8223,

                        dec_deg=-5.3913,

                        x=17.0,

                        y=17.0,

                        flux=7000.0,

                        flux_error=20.0,

                        instrumental_magnitude=-9.6,

                        differential_magnitude=None,

                        is_variable=False,

                        is_reference=True,

                        flags=[],

                        aperture_radius=6.0,

                        annulus_inner_radius=9.0,

                        annulus_outer_radius=13.0,

                    ),

                ]


            def fake_measure_manual_sources(**kwargs: object) -> list[object]:

                manual_config = kwargs["manual_config"]

                self.assertEqual(manual_config.mode, ObjectPhotometryMode.AUTO)

                scan_result = kwargs["scan_result"]

                source_path = kwargs["source_path"]

                return [

                    PhotometryMeasurement(

                        source_id="manual-target-1",

                        source_name="Manual Target",

                        catalog="manual",

                        object_name=scan_result.object_folder,

                        file_path=source_path,

                        observation_time=scan_result.metadata.date_obs,

                        filter_name=scan_result.metadata.filter_name,

                        ra_deg=83.8220,

                        dec_deg=-5.3910,

                        x=16.0,

                        y=16.0,

                        flux=5200.0,

                        flux_error=20.0,

                        instrumental_magnitude=-9.1,

                        differential_magnitude=None,

                        is_variable=True,

                        is_reference=False,

                        flags=[],

                        comparison_source_ids=["manual-comp-1"],

                        comparison_source_names=["Manual Comp"],

                        aperture_radius=6.0,

                        annulus_inner_radius=9.0,

                        annulus_outer_radius=13.0,

                    ),

                    PhotometryMeasurement(

                        source_id="manual-comp-1",

                        source_name="Manual Comp",

                        catalog="manual",

                        object_name=scan_result.object_folder,

                        file_path=source_path,

                        observation_time=scan_result.metadata.date_obs,

                        filter_name=scan_result.metadata.filter_name,

                        ra_deg=83.8223,

                        dec_deg=-5.3913,

                        x=17.0,

                        y=17.0,

                        flux=7000.0,

                        flux_error=20.0,

                        instrumental_magnitude=-9.6,

                        differential_magnitude=None,

                        is_variable=False,

                        is_reference=True,

                        flags=[],

                        aperture_radius=6.0,

                        annulus_inner_radius=9.0,

                        annulus_outer_radius=13.0,

                    ),

                ]


            pipeline = PhotometryPipeline()

            with patch.dict(

                os.environ,

                {

                    "CITIZEN_PHOTOMETRY_CONFIG_PATH": str(config_path),

                    "CITIZEN_PHOTOMETRY_STATE_PATH": str(state_path),

                },

                clear=False,

            ), patch("photometry_app.core.pipeline.CatalogService", FakeCatalogService), patch(

                "photometry_app.core.pipeline.measure_targets", side_effect=fake_measure_targets

            ) as measure_targets_mock, patch(

                "photometry_app.core.pipeline.measure_manual_sources", side_effect=fake_measure_manual_sources

            ) as measure_manual_sources_mock:

                report = pipeline.process_object(root, "M42")


            measure_targets_mock.assert_called_once()

            measure_manual_sources_mock.assert_called_once()

            self.assertEqual(report.photometry_mode, ObjectPhotometryMode.AUTO)

            self.assertIsNotNone(report.manual_config)

            assert report.manual_config is not None

            self.assertEqual(report.manual_config.mode, ObjectPhotometryMode.AUTO)

            self.assertEqual([entry.catalog for entry in report.field_catalog.variable_stars], ["vsx", "manual"])

            self.assertIn("manual", {entry.catalog for entry in report.reference_stars})

            self.assertIn("manual-target-1", {series.source_id for series in report.light_curves})


    def test_load_cached_processing_report_rejects_manual_only_payload_for_auto_processing(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "solved.fits"

            self._write_solved_fits(image_path)

            config_path = root / "settings.json"

            state_path = root / "state.json"

            state_path.write_text("{}", encoding="utf-8")

            config_path.write_text(

                json.dumps(

                    {

                        "cache_dir": ".photometry-cache",

                        "manual_photometry_configs": {

                            "M42": {

                                "mode": ObjectPhotometryMode.AUTO.value,

                                "sources": [

                                    {

                                        "source_id": "manual-target-1",

                                        "name": "Manual Target",

                                        "role": ManualSourceRole.TARGET.value,

                                        "ra_deg": 83.8220,

                                        "dec_deg": -5.3910,

                                        "reference_frame_name": "solved.fits",

                                        "reference_x": 16.0,

                                        "reference_y": 16.0,

                                        "aperture_radius": 6.0,

                                        "annulus_inner_radius": 9.0,

                                        "annulus_outer_radius": 13.0,

                                    },

                                    {

                                        "source_id": "manual-comp-1",

                                        "name": "Manual Comp",

                                        "role": ManualSourceRole.COMPARISON.value,

                                        "ra_deg": 83.8223,

                                        "dec_deg": -5.3913,

                                        "reference_frame_name": "solved.fits",

                                        "reference_x": 17.0,

                                        "reference_y": 17.0,

                                        "aperture_radius": 6.0,

                                        "annulus_inner_radius": 9.0,

                                        "annulus_outer_radius": 13.0,

                                    },

                                ],

                            }

                        },

                    }

                ),

                encoding="utf-8",

            )

            pipeline = PhotometryPipeline()

            with patch.dict(

                os.environ,

                {

                    "CITIZEN_PHOTOMETRY_CONFIG_PATH": str(config_path),

                    "CITIZEN_PHOTOMETRY_STATE_PATH": str(state_path),

                },

                clear=False,

            ):

                settings = AppSettings.from_root(root)

                auto_config = settings.manual_photometry_configs["M42"]

                manual_report = ProcessingReport(

                    object_name="M42",

                    files_processed=1,

                    solved_files=1,

                    field_catalog=FieldCatalog(center_ra_deg=83.822, center_dec_deg=-5.391, radius_deg=0.25),

                    measurements=[],

                    light_curves=[],

                    photometry_mode=ObjectPhotometryMode.MANUAL,

                    manual_config=replace(auto_config, mode=ObjectPhotometryMode.MANUAL),

                )

                summary_files = pipeline.scan_workspace(root).object_summaries[0].files

                pipeline.save_cached_processing_report(

                    root,

                    manual_report,

                    summary_files=summary_files,

                    manual_config_override=auto_config,

                    settings_override=settings,

                )

                loaded_report, cache_note = pipeline.load_cached_processing_report(

                    root,

                    "M42",

                    summary_files=summary_files,

                    manual_config_override=auto_config,

                    settings_override=settings,

                )


            self.assertIsNone(loaded_report)

            self.assertEqual(cache_note, "Ignored cached manual-only light-curve results because automatic processing is active.")



    def test_process_object_ignores_incomplete_saved_manual_aperture_config(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "solved.fits"

            self._write_solved_fits(image_path)



            config_path = root / "settings.json"

            state_path = root / "state.json"

            state_path.write_text("{}", encoding="utf-8")

            config_path.write_text(

                json.dumps(

                    {

                        "cache_dir": ".photometry-cache",

                        "manual_photometry_configs": {

                            "M42": {

                                "mode": ObjectPhotometryMode.MANUAL.value,

                                "reference_frame_name": "solved.fits",

                                "sources": [

                                    {

                                        "source_id": "manual-target-1",

                                        "name": "M42",

                                        "role": ManualSourceRole.TARGET.value,

                                        "ra_deg": 83.8220,

                                        "dec_deg": -5.3910,

                                        "reference_frame_name": "solved.fits",

                                        "reference_x": 16.0,

                                        "reference_y": 16.0,

                                        "aperture_radius": 6.0,

                                        "annulus_inner_radius": 9.0,

                                        "annulus_outer_radius": 13.0,

                                    }

                                ],

                            }

                        },

                    }

                ),

                encoding="utf-8",

            )



            def fake_measure_targets(**kwargs: object) -> list[object]:

                scan_result = kwargs["scan_result"]

                source_path = kwargs["source_path"]

                models = __import__("photometry_app.core.models", fromlist=["PhotometryMeasurement"])

                return [

                    models.PhotometryMeasurement(

                        source_id="vsx-var",

                        source_name="R Cas",

                        catalog="vsx",

                        object_name=scan_result.object_folder,

                        file_path=source_path,

                        observation_time=scan_result.metadata.date_obs,

                        filter_name=scan_result.metadata.filter_name,

                        ra_deg=83.8220,

                        dec_deg=-5.3910,

                        x=16.0,

                        y=16.0,

                        flux=5000.0,

                        flux_error=20.0,

                        instrumental_magnitude=-9.0,

                        differential_magnitude=None,

                        is_variable=True,

                        is_reference=False,

                        flags=[],

                        comparison_source_ids=["gaia-ref"],

                        comparison_source_names=["gaia-ref"],

                        aperture_radius=6.0,

                        annulus_inner_radius=9.0,

                        annulus_outer_radius=13.0,

                    ),

                    models.PhotometryMeasurement(

                        source_id="gaia-ref",

                        source_name="gaia-ref",

                        catalog="gaia-dr3",

                        object_name=scan_result.object_folder,

                        file_path=source_path,

                        observation_time=scan_result.metadata.date_obs,

                        filter_name=scan_result.metadata.filter_name,

                        ra_deg=83.8223,

                        dec_deg=-5.3913,

                        x=17.0,

                        y=17.0,

                        flux=7000.0,

                        flux_error=20.0,

                        instrumental_magnitude=-9.6,

                        differential_magnitude=None,

                        is_variable=False,

                        is_reference=True,

                        flags=[],

                        aperture_radius=6.0,

                        annulus_inner_radius=9.0,

                        annulus_outer_radius=13.0,

                    ),

                ]



            pipeline = PhotometryPipeline()

            with patch.dict(

                os.environ,

                {

                    "CITIZEN_PHOTOMETRY_CONFIG_PATH": str(config_path),

                    "CITIZEN_PHOTOMETRY_STATE_PATH": str(state_path),

                },

                clear=False,

            ), patch("photometry_app.core.pipeline.CatalogService", FakeCatalogService), patch(

                "photometry_app.core.pipeline.measure_manual_sources"

            ) as measure_manual_sources_mock, patch(

                "photometry_app.core.pipeline.measure_targets", side_effect=fake_measure_targets

            ) as measure_targets_mock:

                report = pipeline.process_object(root, "M42")



            measure_manual_sources_mock.assert_not_called()

            measure_targets_mock.assert_called_once()

            self.assertEqual(report.photometry_mode, ObjectPhotometryMode.AUTO)

            self.assertIsNone(report.manual_config)

            self.assertEqual(report.field_catalog.variable_stars[0].catalog, "vsx")

            self.assertTrue(any("Ignored incomplete manual aperture setup" in note for note in report.notes))



    def test_catalog_service_recovers_from_corrupt_cache_file(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            cache_dir = Path(temp_dir)

            service = CatalogService(cache_dir)

            solved_field = __import__("photometry_app.core.models", fromlist=["SolvedField"]).SolvedField(

                center_ra_deg=83.822,

                center_dec_deg=-5.391,

                radius_deg=0.25,

                width=32,

                height=32,

                wcs_path=cache_dir / "dummy.fits",

            )

            cache_path = cache_dir / service._cache_key(solved_field)

            cache_path.write_text("{broken json", encoding="utf-8")



            with patch.object(CatalogService, "_query_gaia", return_value=[CatalogStar("gaia-dr3", "gaia-ref", "gaia-ref", 83.8, -5.39, 11.0, False)]), patch.object(

                CatalogService,

                "_query_vsx",

                return_value=[CatalogStar("vsx", "vsx-var", "R Cas", 83.82, -5.391, 8.0, True)],

            ), patch.object(

                CatalogService,

                "_query_exoplanets",

                return_value=[CatalogStar("nasa-exoplanet-archive", "Kepler-10 b", "Kepler-10 b", 83.81, -5.38, 11.2, False, object_type="exoplanet")],

            ):

                catalog = service.query_field_catalog(solved_field)



            self.assertEqual(len(catalog.gaia_stars), 1)

            self.assertEqual(len(catalog.variable_stars), 1)

            self.assertEqual(len(catalog.exoplanets), 1)

            self.assertTrue(cache_path.exists())



    def test_catalog_service_loads_cached_exoplanet_entries(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            cache_dir = Path(temp_dir)

            service = CatalogService(cache_dir)

            solved_field = __import__("photometry_app.core.models", fromlist=["SolvedField"]).SolvedField(

                center_ra_deg=83.822,

                center_dec_deg=-5.391,

                radius_deg=0.25,

                width=32,

                height=32,

                wcs_path=cache_dir / "dummy.fits",

            )

            cache_path = cache_dir / service._cache_key(solved_field)



            with patch.object(CatalogService, "_query_gaia", return_value=[]), patch.object(

                CatalogService,

                "_query_vsx",

                return_value=[],

            ), patch.object(

                CatalogService,

                "_query_exoplanets",

                return_value=[

                    CatalogStar(

                        "nasa-exoplanet-archive",

                        "TOI-700 d",

                        "TOI-700 d",

                        84.0,

                        -5.2,

                        13.1,

                        False,

                        object_type="exoplanet",

                        metadata={"host_name": "TOI-700", "orbital_period_days": 37.4},

                    )

                ],

            ):

                service.query_field_catalog(solved_field)



            cached_catalog = service.query_field_catalog(solved_field)



            self.assertEqual(len(cached_catalog.exoplanets), 1)

            self.assertEqual(cached_catalog.exoplanets[0].object_type, "exoplanet")

            self.assertEqual(cached_catalog.exoplanets[0].metadata.get("host_name"), "TOI-700")



    def test_catalog_service_refreshes_suspicious_empty_variable_cache(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            cache_dir = Path(temp_dir)

            service = CatalogService(cache_dir)

            solved_field = __import__("photometry_app.core.models", fromlist=["SolvedField"]).SolvedField(

                center_ra_deg=83.822,

                center_dec_deg=-5.391,

                radius_deg=0.25,

                width=32,

                height=32,

                wcs_path=cache_dir / "dummy.fits",

            )



            with patch.object(CatalogService, "_query_gaia", return_value=[CatalogStar("gaia-dr3", "gaia-ref", "gaia-ref", 83.8, -5.39, 11.0, False)]), patch.object(

                CatalogService,

                "_query_vsx",

                return_value=[],

            ), patch.object(

                CatalogService,

                "_query_exoplanets",

                return_value=[],

            ):

                empty_catalog = service.query_field_catalog(solved_field)



            self.assertEqual(len(empty_catalog.variable_stars), 0)



            with patch.object(CatalogService, "_query_gaia", return_value=[CatalogStar("gaia-dr3", "gaia-ref", "gaia-ref", 83.8, -5.39, 11.0, False)]), patch.object(

                CatalogService,

                "_query_vsx",

                return_value=[CatalogStar("vsx", "vsx-var", "R Cas", 83.82, -5.391, 8.0, True)],

            ) as query_vsx, patch.object(

                CatalogService,

                "_query_exoplanets",

                return_value=[],

            ):

                refreshed_catalog = service.query_field_catalog(solved_field)



            self.assertEqual(len(refreshed_catalog.variable_stars), 1)

            self.assertEqual(query_vsx.call_count, 1)



    def test_clear_catalog_cache_removes_cached_catalog_files(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            catalog_dir = root / ".photometry-cache" / "catalogs"

            catalog_dir.mkdir(parents=True)

            (catalog_dir / "field_a.json").write_text("{}", encoding="utf-8")

            (catalog_dir / "field_b.json").write_text("{}", encoding="utf-8")



            pipeline = PhotometryPipeline()

            removed = pipeline.clear_catalog_cache(root)



            self.assertEqual(removed, 2)

            self.assertEqual(list(catalog_dir.glob("*.json")), [])



    def test_clear_object_catalog_cache_removes_matching_field_only(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            object_dir = root / "Files" / "M42"

            object_dir.mkdir(parents=True)

            image_path = object_dir / "solved.fits"

            self._write_solved_fits(image_path)



            pipeline = PhotometryPipeline()

            settings_path = root / ".photometry-settings.json"

            settings_path.write_text(json.dumps({"cache_dir": ".photometry-cache"}), encoding="utf-8")



            catalog_dir = root / ".photometry-cache" / "catalogs"

            service = CatalogService(catalog_dir)

            with fits.open(image_path) as hdul:

                header = hdul[0].header

                solved_field = extract_solved_field(header, int(header["NAXIS1"]), int(header["NAXIS2"]), image_path)

            self.assertIsNotNone(solved_field)

            matching_cache = catalog_dir / service._cache_key(solved_field)

            matching_cache.parent.mkdir(parents=True, exist_ok=True)

            matching_cache.write_text("{}", encoding="utf-8")

            other_cache = catalog_dir / "field_other.json"

            other_cache.write_text("{}", encoding="utf-8")



            removed = pipeline.clear_object_catalog_cache(root, "M42")



            self.assertEqual(removed, 1)

            self.assertFalse(matching_cache.exists())

            self.assertTrue(other_cache.exists())



    def _write_solved_fits(self, path: Path) -> None:

        self._write_solved_fits_at(path, 83.822, -5.391)



    def _write_unsolved_fits(self, path: Path) -> None:

        data = np.ones((32, 32), dtype=np.float32)

        header = fits.Header()

        header["DATE-OBS"] = "2026-03-16T01:02:03"

        header["FILTER"] = "R"

        header["EXPTIME"] = 60.0

        header["OBJECT"] = "M42"

        fits.PrimaryHDU(data=data, header=header).writeto(path)



    def _write_xisf_with_wcs(self, path: Path) -> None:

        from xisf import XISF



        data = np.ones((32, 32, 1), dtype=np.float32)

        fits_keywords = {

            "DATE-OBS": [{"value": "2026-03-16T01:02:03", "comment": "Observation timestamp"}],

            "FILTER": [{"value": "R", "comment": "Filter name"}],

            "EXPTIME": [{"value": "60.0", "comment": "Exposure seconds"}],

            "OBJECT": [{"value": "M42", "comment": "Target name"}],

            "CTYPE1": [{"value": "RA---TAN", "comment": "WCS axis"}],

            "CTYPE2": [{"value": "DEC--TAN", "comment": "WCS axis"}],

            "CRVAL1": [{"value": "83.822", "comment": "Reference RA"}],

            "CRVAL2": [{"value": "-5.391", "comment": "Reference Dec"}],

            "CRPIX1": [{"value": "16.0", "comment": "Reference pixel"}],

            "CRPIX2": [{"value": "16.0", "comment": "Reference pixel"}],

            "CD1_1": [{"value": "-0.00028", "comment": "WCS matrix"}],

            "CD1_2": [{"value": "0.0", "comment": "WCS matrix"}],

            "CD2_1": [{"value": "0.0", "comment": "WCS matrix"}],

            "CD2_2": [{"value": "0.00028", "comment": "WCS matrix"}],

        }

        XISF.write(str(path), data, image_metadata={"FITSKeywords": fits_keywords})



    def _write_solved_fits_at(self, path: Path, center_ra_deg: float, center_dec_deg: float) -> None:

        data = np.ones((32, 32), dtype=np.float32)

        header = fits.Header()

        header["DATE-OBS"] = datetime(2026, 3, 16, 1, 2, 3).isoformat()

        header["FILTER"] = "R"

        header["EXPTIME"] = 60.0

        header["OBJECT"] = "M42"

        header["CTYPE1"] = "RA---TAN"

        header["CTYPE2"] = "DEC--TAN"

        header["CRVAL1"] = center_ra_deg

        header["CRVAL2"] = center_dec_deg

        header["CRPIX1"] = 16.0

        header["CRPIX2"] = 16.0

        header["CD1_1"] = -0.00028

        header["CD1_2"] = 0.0

        header["CD2_1"] = 0.0

        header["CD2_2"] = 0.00028

        fits.PrimaryHDU(data=data, header=header).writeto(path)



    def _write_unsolved_fits(self, path: Path) -> None:

        data = np.ones((32, 32), dtype=np.float32)

        header = fits.Header()

        header["DATE-OBS"] = datetime(2026, 3, 16, 1, 2, 3).isoformat()

        header["FILTER"] = "R"

        header["EXPTIME"] = 60.0

        header["OBJECT"] = "UnsolvedObject"

        fits.PrimaryHDU(data=data, header=header).writeto(path)