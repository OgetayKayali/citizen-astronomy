from __future__ import annotations



import os

import unittest

from concurrent.futures import Future, ProcessPoolExecutor as ConcurrentProcessPoolExecutor, ThreadPoolExecutor as ConcurrentThreadPoolExecutor

from datetime import datetime, timedelta

from pathlib import Path

from types import SimpleNamespace

from unittest.mock import patch

from dataclasses import dataclass, replace

import numpy as np



from photometry_app.core.catalogs import LiteraturePeriodResult

from photometry_app.core.discovery import MovingObjectDiscoveryResult

from photometry_app.core.models import CatalogStar, FileScanResult, LightCurvePoint, LightCurveSeries, ObservationMetadata, PhotometryMeasurement, PlateSolveResult, SolvedField, WcsStatus

from photometry_app.core.settings import AppSettings

from photometry_app.core.plotting import FitPeriodInferenceResult

from photometry_app.ui.workers import AsteroidDiscoveryWorker, CalculatePeriodWorker, DiscoverBatchResult, DiscoverSourcesWorker, ImageDisplayPreloadWorker, LiteraturePeriodWorker, OptimizeComparisonFitWorker, SkyExplorerSurveyWorker, UpdateCheckWorker, UpdateDownloadWorker





os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")





class AsteroidDiscoveryWorkerTest(unittest.TestCase):

    def test_discovery_worker_runs_estimate_before_search(self) -> None:

        frame_paths = [Path("frame_01.fit"), Path("frame_02.fit"), Path("frame_03.fit")]

        frame_metadata = {

            str(path.resolve()): ObservationMetadata(

                date_obs=datetime(2025, 1, 14, 21, 12) + timedelta(minutes=index),

                filter_name="L",

                exposure_seconds=60.0,

                width=64,

                height=64,

                object_name="Demo",

                frame_type="Light",

            )

            for index, path in enumerate(frame_paths)

        }

        settings = AppSettings.from_root(Path("."))

        estimate_result = SimpleNamespace(dimmest_visible_magnitude=12.5, source_path=frame_paths[0])

        discovery_result = MovingObjectDiscoveryResult(

            reference_path=frame_paths[0],

            preview_data=np.ones((4, 4), dtype=np.float32),

            candidate_count=0,

            aligned_frame_count=3,

            candidates=(),

            summary_text="Recovered 0 known object(s) and found 0 potential discovery candidate(s) from 3 frame(s).",

        )

        completed: list[MovingObjectDiscoveryResult] = []
        intermediate: list[MovingObjectDiscoveryResult] = []

        worker = AsteroidDiscoveryWorker(

            frame_paths=frame_paths,

            frame_metadata=frame_metadata,

            known_detection_result=None,

            reference_path=frame_paths[0],

            settings=settings,

            assume_aligned=True,

        )

        worker.discovery_completed.connect(completed.append)
        worker.discovery_intermediate_result.connect(intermediate.append)

        with (

            patch("photometry_app.ui.workers.estimate_visible_magnitude_limit", return_value=estimate_result) as estimate_mock,

            patch("photometry_app.ui.workers.discover_unmatched_moving_candidates", return_value=discovery_result) as discover_mock,

        ):

            worker.run()

        estimate_mock.assert_not_called()

        self.assertIsNone(discover_mock.call_args.kwargs["estimate_result"])
        self.assertEqual(len(intermediate), 1)
        self.assertIsNone(intermediate[0].estimate_result)
        self.assertEqual(intermediate[0].benchmark_known_count, 0)
        self.assertIn("Known-object benchmark counts remain unavailable", intermediate[0].summary_text)

        self.assertEqual(completed, [discovery_result])

    def test_discovery_worker_estimate_summary_counts_known_objects_within_limit(self) -> None:

        frame_paths = [Path("frame_01.fit"), Path("frame_02.fit"), Path("frame_03.fit")]

        frame_metadata = {

            str(path.resolve()): ObservationMetadata(

                date_obs=datetime(2025, 1, 14, 21, 12) + timedelta(minutes=index),

                filter_name="L",

                exposure_seconds=60.0,

                width=64,

                height=64,

                object_name="Demo",

                frame_type="Light",

            )

            for index, path in enumerate(frame_paths)

        }

        settings = AppSettings.from_root(Path("."))
        estimate_result = SimpleNamespace(dimmest_visible_magnitude=12.5, source_path=frame_paths[0])
        known_detection_result = SimpleNamespace(
            detections=[
                SimpleNamespace(predicted_magnitude=12.2),
                SimpleNamespace(predicted_magnitude=12.5),
                SimpleNamespace(predicted_magnitude=13.1),
                SimpleNamespace(predicted_magnitude=None),
            ]
        )
        discovery_result = MovingObjectDiscoveryResult(

            reference_path=frame_paths[0],

            preview_data=np.ones((4, 4), dtype=np.float32),

            candidate_count=0,

            aligned_frame_count=3,

            candidates=(),

            summary_text="Recovered 0 known object(s) and found 0 potential discovery candidate(s) from 3 frame(s).",

        )

        intermediate: list[MovingObjectDiscoveryResult] = []

        worker = AsteroidDiscoveryWorker(

            frame_paths=frame_paths,

            frame_metadata=frame_metadata,

            known_detection_result=known_detection_result,

            reference_path=frame_paths[0],

            settings=settings,

            assume_aligned=True,

        )

        worker.discovery_intermediate_result.connect(intermediate.append)

        with (

            patch("photometry_app.ui.workers.estimate_visible_magnitude_limit", return_value=estimate_result),

            patch("photometry_app.ui.workers.discover_unmatched_moving_candidates", return_value=discovery_result),

        ):

            worker.run()

        self.assertEqual(len(intermediate), 1)
        self.assertEqual(intermediate[0].benchmark_known_count, 2)
        self.assertIn("2 known object(s) are within that estimate", intermediate[0].summary_text)


class ImageDisplayPreloadWorkerTest(unittest.TestCase):

    def test_image_display_preload_worker_emits_completed_result(self) -> None:

        worker = ImageDisplayPreloadWorker(frame_paths=[Path("demo.fit")], max_parallel_workers=1)

        completed: list[object] = []

        batches: list[DiscoverBatchResult] = []

        failed: list[str] = []

        worker.preload_completed.connect(completed.append)

        worker.preload_failed.connect(failed.append)

        fake_result = SimpleNamespace(image_displays={})



        with patch("photometry_app.ui.workers.preload_image_displays", return_value=fake_result):

            worker.run()



        self.assertEqual(completed, [fake_result])

        self.assertEqual(failed, [])

    def test_discovery_worker_does_not_enter_detailed_retry_after_cancel_request(self) -> None:

        frame_paths = [Path("frame_01.fit"), Path("frame_02.fit"), Path("frame_03.fit")]

        frame_metadata = {
            str(path.resolve()): ObservationMetadata(
                date_obs=datetime(2025, 1, 14, 21, 12) + timedelta(minutes=index),
                filter_name="L",
                exposure_seconds=60.0,
                width=64,
                height=64,
                object_name="Demo",
                frame_type="Light",
            )
            for index, path in enumerate(frame_paths)
        }

        settings = AppSettings.from_root(Path("."))
        estimate_result = SimpleNamespace(dimmest_visible_magnitude=12.5, source_path=frame_paths[0])
        empty_result = MovingObjectDiscoveryResult(
            reference_path=frame_paths[0],
            preview_data=np.ones((4, 4), dtype=np.float32),
            candidate_count=0,
            aligned_frame_count=3,
            candidates=(),
            summary_text="Recovered 0 known object(s) and found 0 potential discovery candidate(s) from 3 frame(s).",
        )

        cancelled: list[str] = []
        worker = AsteroidDiscoveryWorker(
            frame_paths=frame_paths,
            frame_metadata=frame_metadata,
            known_detection_result=None,
            reference_path=frame_paths[0],
            settings=settings,
            assume_aligned=True,
            retry_with_detailed_search=True,
        )
        worker.discovery_cancelled.connect(cancelled.append)

        call_count = 0

        def discover_side_effect(*_args: object, **_kwargs: object) -> MovingObjectDiscoveryResult:
            nonlocal call_count
            call_count += 1
            worker.request_cancel()
            return empty_result

        with (
            patch("photometry_app.ui.workers.estimate_visible_magnitude_limit", return_value=estimate_result),
            patch("photometry_app.ui.workers.discover_unmatched_moving_candidates", side_effect=discover_side_effect),
        ):
            worker.run()

        self.assertEqual(call_count, 1)
        self.assertEqual(cancelled, ["Discover cancelled."])

    def test_discovery_worker_requests_confirmation_before_running_sweep(self) -> None:

        frame_paths = [Path("frame_01.fit"), Path("frame_02.fit"), Path("frame_03.fit")]
        frame_metadata = {
            str(path.resolve()): ObservationMetadata(
                date_obs=datetime(2025, 1, 14, 21, 12) + timedelta(minutes=index),
                filter_name="L",
                exposure_seconds=60.0,
                width=64,
                height=64,
                object_name="Demo",
                frame_type="Light",
            )
            for index, path in enumerate(frame_paths)
        }
        settings = AppSettings.from_root(Path("."))
        estimate_result = SimpleNamespace(dimmest_visible_magnitude=12.5, source_path=frame_paths[0])
        residual_result = MovingObjectDiscoveryResult(
            reference_path=frame_paths[0],
            preview_data=np.ones((4, 4), dtype=np.float32),
            candidate_count=0,
            aligned_frame_count=3,
            candidates=(),
            summary_text="Residual-only Discover result.",
        )
        final_result = MovingObjectDiscoveryResult(
            reference_path=frame_paths[0],
            preview_data=np.ones((4, 4), dtype=np.float32),
            candidate_count=1,
            aligned_frame_count=3,
            candidates=(),
            summary_text="Discover result with final synthetic sweep.",
        )

        worker = AsteroidDiscoveryWorker(
            frame_paths=frame_paths,
            frame_metadata=frame_metadata,
            known_detection_result=None,
            reference_path=frame_paths[0],
            settings=settings,
            assume_aligned=True,
            enable_synthetic_sweep=True,
        )
        requested: list[MovingObjectDiscoveryResult] = []
        completed: list[MovingObjectDiscoveryResult] = []
        worker.sweep_confirmation_requested.connect(
            lambda result: (requested.append(result), worker.provide_sweep_confirmation(True))
        )
        worker.discovery_batch_ready.connect(batches.append)

        worker.discovery_completed.connect(completed.append)

        with (
            patch("photometry_app.ui.workers.estimate_visible_magnitude_limit", return_value=estimate_result),
            patch(
                "photometry_app.ui.workers.discover_unmatched_moving_candidates",
                side_effect=[residual_result, final_result],
            ) as discover_mock,
        ):
            worker.run()

        self.assertEqual(discover_mock.call_count, 2)
        self.assertFalse(discover_mock.call_args_list[0].kwargs["enable_synthetic_sweep"])
        self.assertTrue(discover_mock.call_args_list[1].kwargs["enable_synthetic_sweep"])
        self.assertEqual(requested, [residual_result])
        self.assertEqual(completed, [final_result])

    def test_discovery_worker_can_auto_continue_sweep_without_prompt(self) -> None:

        frame_paths = [Path("frame_01.fit"), Path("frame_02.fit"), Path("frame_03.fit")]
        frame_metadata = {
            str(path.resolve()): ObservationMetadata(
                date_obs=datetime(2025, 1, 14, 21, 12) + timedelta(minutes=index),
                filter_name="L",
                exposure_seconds=60.0,
                width=64,
                height=64,
                object_name="Demo",
                frame_type="Light",
            )
            for index, path in enumerate(frame_paths)
        }
        settings = AppSettings.from_root(Path("."))
        estimate_result = SimpleNamespace(dimmest_visible_magnitude=12.5, source_path=frame_paths[0])
        residual_result = MovingObjectDiscoveryResult(
            reference_path=frame_paths[0],
            preview_data=np.ones((4, 4), dtype=np.float32),
            candidate_count=0,
            aligned_frame_count=3,
            candidates=(),
            summary_text="Residual-only Discover result.",
        )
        final_result = MovingObjectDiscoveryResult(
            reference_path=frame_paths[0],
            preview_data=np.ones((4, 4), dtype=np.float32),
            candidate_count=1,
            aligned_frame_count=3,
            candidates=(),
            summary_text="Discover result with final synthetic sweep.",
        )

        worker = AsteroidDiscoveryWorker(
            frame_paths=frame_paths,
            frame_metadata=frame_metadata,
            known_detection_result=None,
            reference_path=frame_paths[0],
            settings=settings,
            assume_aligned=True,
            enable_synthetic_sweep=True,
            auto_continue_synthetic_sweep=True,
        )
        requested: list[MovingObjectDiscoveryResult] = []
        completed: list[MovingObjectDiscoveryResult] = []
        worker.sweep_confirmation_requested.connect(requested.append)
        worker.discovery_completed.connect(completed.append)

        with (
            patch("photometry_app.ui.workers.estimate_visible_magnitude_limit", return_value=estimate_result),
            patch(
                "photometry_app.ui.workers.discover_unmatched_moving_candidates",
                side_effect=[residual_result, final_result],
            ) as discover_mock,
        ):
            worker.run()

        self.assertEqual(discover_mock.call_count, 2)
        self.assertEqual(requested, [])
        self.assertEqual(completed, [final_result])


class WorkerConcurrencyTest(unittest.TestCase):

    def test_discover_sources_worker_builds_raw_light_curves_without_score_filtering(self) -> None:

        source_file = FileScanResult(

            path=Path("frame-001.fits"),

            object_folder="Alpha",

            metadata=ObservationMetadata(

                date_obs=datetime(2025, 1, 1),

                filter_name="V",

                exposure_seconds=60.0,

                width=100,

                height=100,

                object_name="Alpha",

            ),

            wcs_status=WcsStatus.SOLVED,

        )

        solved_field = SolvedField(

            center_ra_deg=10.0,

            center_dec_deg=20.0,

            radius_deg=0.2,

            width=100,

            height=100,

            wcs_path=Path("frame-001.wcs"),

        )

        solved_results = [(source_file, PlateSolveResult(source_path=source_file.path, status=WcsStatus.SOLVED, solved_field=solved_field))]

        ref1 = CatalogStar("gaia", "ref-1", "Ref 1", 10.1, 20.1, 11.0, False)

        ref2 = CatalogStar("gaia", "ref-2", "Ref 2", 10.2, 20.2, 11.2, False)

        candidate1 = CatalogStar("gaia", "cand-1", "Candidate 1", 10.3, 20.3, 12.0, False)

        candidate2 = CatalogStar("gaia", "cand-2", "Candidate 2", 10.4, 20.4, 12.3, False)

        field_catalog = SimpleNamespace(

            gaia_stars=[ref1, ref2, candidate1, candidate2],

            variable_stars=[],

            exoplanets=[],

        )

        settings = AppSettings.from_root(Path("."))

        settings.nearby_reference_count = 1

        settings.discovery_max_candidate_count = 2

        settings.discovery_min_magnitude = 10.0

        settings.discovery_max_magnitude = 14.0

        settings.discovery_min_candidate_score = 25.0

        pipeline = SimpleNamespace(

            _resolve_summary_fields=lambda files, settings, progress: solved_results,

            _best_field_catalog_for_solved_results=lambda catalog_service, solved_results, progress: field_catalog,

        )

        completed: list[object] = []

        batches: list[DiscoverBatchResult] = []

        failed: list[str] = []

        worker = DiscoverSourcesWorker(

            report_token=321,

            pipeline=pipeline,

            source_files=[source_file],

            settings=settings,

            object_name="Alpha",

            fit_config=None,

            y_axis_mode="differential_magnitude",

            period_method="ls",

            period_convention="standard",

            search_multiple_comparison_groups=True,

        )

        worker.discovery_batch_ready.connect(batches.append)

        worker.discovery_completed.connect(completed.append)

        worker.discovery_failed.connect(failed.append)



        def measure_targets_side_effect(

            source_path: Path,

            scan_result: FileScanResult,

            wcs_path: Path,

            variable_stars: list[CatalogStar],

            reference_stars: list[CatalogStar],

            **_kwargs: object,

        ) -> list[PhotometryMeasurement]:

            if reference_stars:

                return [

                    PhotometryMeasurement(

                        source_id=entry.source_id,

                        source_name=entry.name,

                        catalog=entry.catalog,

                        object_name="Alpha",

                        file_path=source_path,

                        observation_time=datetime(2025, 1, 1),

                        filter_name="V",

                        ra_deg=entry.ra_deg,

                        dec_deg=entry.dec_deg,

                        x=20.0,

                        y=20.0,

                        flux=12000.0,

                        flux_error=90.0,

                        instrumental_magnitude=10.0,

                        differential_magnitude=None,

                        is_variable=False,

                        is_reference=True,

                    )

                    for entry in reference_stars

                ]

            measurements: list[PhotometryMeasurement] = []

            for target in variable_stars:

                measurements.extend(

                    [

                        PhotometryMeasurement(

                            source_id=target.source_id,

                            source_name=target.name,

                            catalog=target.catalog,

                            object_name="Alpha",

                            file_path=source_path,

                            observation_time=datetime(2025, 1, 1),

                            filter_name="V",

                            ra_deg=target.ra_deg,

                            dec_deg=target.dec_deg,

                            x=30.0,

                            y=30.0,

                            flux=15000.0,

                            flux_error=100.0,

                            instrumental_magnitude=10.5,

                            differential_magnitude=None,

                            is_variable=True,

                            is_reference=False,

                        )

                        for _ in range(4)

                    ]

                )

            return measurements



        def evaluate_group_side_effect(

            target_measurements: list[PhotometryMeasurement],

            reference_measurements: list[PhotometryMeasurement],

            comparison_source_ids: tuple[str, ...],

            fit_config: object,

            y_axis_mode: str,

            period_method: str,

            period_convention: str,

            **_kwargs: object,

        ) -> tuple[list[PhotometryMeasurement], LightCurveSeries | None, FitPeriodInferenceResult | None, object]:

            source_id = target_measurements[0].source_id

            candidate_score = 40.0 if source_id == "cand-1" else 12.0

            updated_measurements = [

                replace(

                    measurement,

                    differential_magnitude=0.15,

                    comparison_scatter=0.01 if comparison_source_ids == ("ref-1",) else 0.03,

                    comparison_source_ids=list(comparison_source_ids),

                    comparison_source_names=[comparison_source_ids[0]],

                )

                for measurement in target_measurements

            ]

            series = LightCurveSeries(

                object_name="Alpha",

                source_id=source_id,

                source_name=target_measurements[0].source_name,

                filter_name="V",

                points=[

                    LightCurvePoint(

                        observation_time=measurement.observation_time,

                        file_path=measurement.file_path,

                        differential_magnitude=0.15,

                        instrumental_magnitude=measurement.instrumental_magnitude,

                        flux=measurement.flux,

                        flux_error=measurement.flux_error,

                    )

                    for measurement in updated_measurements

                ],

                candidate_score=candidate_score,

            )

            return updated_measurements, series, None, SimpleNamespace(
                comparison_source_ids=comparison_source_ids,
                comparison_source_names=tuple(comparison_source_ids),
                updated_target_row_count=len(updated_measurements),
                usable_target_row_count=len(updated_measurements),
                differential_value_count=len(updated_measurements),
                excluded_target_row_count=0,
            )



        with (

            patch("photometry_app.ui.workers.CatalogService"),

            patch("photometry_app.ui.workers.select_reference_stars", return_value=[ref1, ref2]),

            patch("photometry_app.ui.workers.resolve_aperture_profile", return_value=SimpleNamespace(aperture_radius=5.0, annulus_inner_radius=8.0, annulus_outer_radius=12.0, fwhm_pixels=None, note=None)),

            patch("photometry_app.ui.workers.measure_targets", side_effect=measure_targets_side_effect),

            patch("photometry_app.ui.workers._comparison_source_groups", return_value=[[("ref-1",), ("ref-2",)], 2, False]),

            patch("photometry_app.ui.workers._evaluate_comparison_source_group_core", side_effect=evaluate_group_side_effect),

        ):

            worker.run()



        self.assertEqual(failed, [])

        self.assertEqual(len(completed), 1)

        result = completed[0]

        self.assertEqual(result.report_token, 321)

        self.assertEqual(result.tested_candidate_count, 2)


        self.assertEqual(result.retained_candidate_count, 2)

        self.assertEqual(len(result.source_results), 2)

        self.assertEqual(len(batches), 2)

        self.assertEqual(batches[0].total_candidate_count, 2)

        self.assertTrue(all(batch.current_stage_label == "raw light curve" for batch in batches))

        self.assertEqual(result.source_results[0].entry.source_id, "cand-1")

        self.assertEqual(result.source_results[0].entry.name, "Discover 1")

        self.assertTrue(result.source_results[0].entry.metadata.get("discover_candidate"))


        self.assertEqual([item.entry.source_id for item in result.source_results], ["cand-1", "cand-2"])

        self.assertTrue(all(item.entry.metadata.get("discover_type") == "raw light curve" for item in result.source_results))

        self.assertIsNone(result.source_results[0].best_period_result)


    def test_discover_sources_worker_uses_single_ranked_comparison_pool_when_multi_search_disabled(self) -> None:

        source_files = [

            FileScanResult(

                path=Path(f"frame-00{index}.fits"),

                object_folder="Alpha",

                metadata=ObservationMetadata(

                    date_obs=datetime(2025, 1, index),

                    filter_name="V",

                    exposure_seconds=60.0,

                    width=100,

                    height=100,

                    object_name="Alpha",

                ),

                wcs_status=WcsStatus.SOLVED,

            )

            for index in range(1, 5)

        ]

        solved_results = [

            (

                file_result,

                PlateSolveResult(

                    source_path=file_result.path,

                    status=WcsStatus.SOLVED,

                    solved_field=SolvedField(

                        center_ra_deg=10.0,

                        center_dec_deg=20.0,

                        radius_deg=0.2,

                        width=100,

                        height=100,

                        wcs_path=Path(f"{file_result.path.stem}.wcs"),

                    ),

                ),

            )

            for file_result in source_files

        ]

        ref1 = CatalogStar("gaia", "ref-1", "Ref 1", 10.1, 20.1, 11.0, False)

        ref2 = CatalogStar("gaia", "ref-2", "Ref 2", 10.2, 20.2, 11.1, False)

        candidate = CatalogStar("gaia", "cand-1", "Candidate 1", 10.3, 20.3, 12.0, False)

        field_catalog = SimpleNamespace(

            gaia_stars=[ref1, ref2, candidate],

            variable_stars=[],

            exoplanets=[],

        )

        settings = AppSettings.from_root(Path("."))

        settings.nearby_reference_count = 1

        settings.discovery_max_candidate_count = 1

        settings.discovery_min_magnitude = 10.0

        settings.discovery_max_magnitude = 14.0

        settings.discovery_min_candidate_score = 10.0

        pipeline = SimpleNamespace(

            _resolve_summary_fields=lambda files, settings, progress: solved_results,

            _best_field_catalog_for_solved_results=lambda catalog_service, solved_results, progress: field_catalog,

        )

        completed: list[DiscoverBatchResult] = []

        evaluated_groups: list[tuple[str, ...]] = []

        evaluated_reference_counts: list[int] = []

        calculate_period_flags: list[object] = []

        worker = DiscoverSourcesWorker(

            report_token=654322,

            pipeline=pipeline,

            source_files=source_files,

            settings=settings,

            object_name="Alpha",

            fit_config=None,

            y_axis_mode="differential_magnitude",

            period_method="ls",

            period_convention="standard",

            search_multiple_comparison_groups=False,

        )

        worker.discovery_completed.connect(completed.append)

        def measure_targets_side_effect(

            source_path: Path,

            scan_result: FileScanResult,

            wcs_path: Path,

            variable_stars: list[CatalogStar],

            reference_stars: list[CatalogStar],

            **_kwargs: object,

        ) -> list[PhotometryMeasurement]:

            entries = reference_stars if reference_stars else variable_stars

            measurements: list[PhotometryMeasurement] = []

            for entry in entries:

                measurements.append(

                    PhotometryMeasurement(

                        source_id=entry.source_id,

                        source_name=entry.name,

                        catalog=entry.catalog,

                        object_name="Alpha",

                        file_path=source_path,

                        observation_time=scan_result.metadata.date_obs,

                        filter_name="V",

                        ra_deg=entry.ra_deg,

                        dec_deg=entry.dec_deg,

                        x=25.0,

                        y=25.0,

                        flux=12000.0,

                        flux_error=90.0,

                        instrumental_magnitude=10.0,

                        differential_magnitude=None,

                        is_variable=entry.is_variable,

                        is_reference=not entry.is_variable,

                    )

                )

            return measurements

        def evaluate_group_side_effect(

            target_measurements: list[PhotometryMeasurement],

            reference_measurements: list[PhotometryMeasurement],

            comparison_source_ids: tuple[str, ...],

            *_args: object,

            **_kwargs: object,

        ) -> tuple[list[PhotometryMeasurement], LightCurveSeries | None, None, SimpleNamespace]:

            evaluated_groups.append(comparison_source_ids)

            evaluated_reference_counts.append(len(reference_measurements))

            calculate_period_flags.append(_kwargs.get("calculate_period"))

            updated_measurements = [replace(measurement, differential_magnitude=0.2) for measurement in target_measurements]

            series = LightCurveSeries(

                object_name="Alpha",

                source_id=target_measurements[0].source_id,

                source_name=target_measurements[0].source_name,

                filter_name="V",

                points=[

                    LightCurvePoint(

                        observation_time=measurement.observation_time,

                        file_path=measurement.file_path,

                        differential_magnitude=0.2,

                        instrumental_magnitude=measurement.instrumental_magnitude,

                        flux=measurement.flux,

                        flux_error=measurement.flux_error,

                    )

                    for measurement in updated_measurements

                ],

                candidate_score=42.0,

            )

            return updated_measurements, series, None, SimpleNamespace(

                comparison_source_ids=comparison_source_ids,

                comparison_source_names=tuple(comparison_source_ids),

                updated_target_row_count=len(updated_measurements),

                usable_target_row_count=len(updated_measurements),

                differential_value_count=len(updated_measurements),

                excluded_target_row_count=0,

            )

        with (

            patch("photometry_app.ui.workers.CatalogService"),

            patch("photometry_app.ui.workers.select_reference_stars", return_value=[ref1, ref2]),

            patch("photometry_app.ui.workers.resolve_aperture_profile", return_value=SimpleNamespace(aperture_radius=5.0, annulus_inner_radius=8.0, annulus_outer_radius=12.0, fwhm_pixels=None, note=None)),

            patch("photometry_app.ui.workers.measure_targets", side_effect=measure_targets_side_effect),

            patch("photometry_app.ui.workers._ordered_reference_candidate_ids", return_value=["ref-2", "ref-1"]),

            patch("photometry_app.ui.workers._comparison_source_groups", side_effect=AssertionError("single-pool mode should not enumerate multiple comparison groups")),

            patch("photometry_app.ui.workers._evaluate_comparison_source_group_core", side_effect=evaluate_group_side_effect),

        ):

            worker.run()

        self.assertEqual(evaluated_groups, [("ref-2",)])

        self.assertEqual(evaluated_reference_counts, [len(source_files)])

        self.assertEqual(calculate_period_flags, [False])

        self.assertEqual(len(completed), 1)

        self.assertEqual(completed[0].retained_candidate_count, 1)

        self.assertEqual(completed[0].source_results[0].total_combination_count, 1)

        self.assertEqual(completed[0].source_results[0].evaluated_combination_count, 1)

        self.assertFalse(completed[0].source_results[0].sampled_search)


    def test_discover_sources_worker_batches_candidate_measurement_per_frame(self) -> None:

        source_files = [

            FileScanResult(

                path=Path(f"frame-00{index}.fits"),

                object_folder="Alpha",

                metadata=ObservationMetadata(

                    date_obs=datetime(2025, 1, index),

                    filter_name="V",

                    exposure_seconds=60.0,

                    width=100,

                    height=100,

                    object_name="Alpha",

                ),

                wcs_status=WcsStatus.SOLVED,

            )

            for index in (1, 2)

        ]

        solved_results = [

            (

                file_result,

                PlateSolveResult(

                    source_path=file_result.path,

                    status=WcsStatus.SOLVED,

                    solved_field=SolvedField(

                        center_ra_deg=10.0,

                        center_dec_deg=20.0,

                        radius_deg=0.2,

                        width=100,

                        height=100,

                        wcs_path=Path(f"{file_result.path.stem}.wcs"),

                    ),

                ),

            )

            for file_result in source_files

        ]

        ref1 = CatalogStar("gaia", "ref-1", "Ref 1", 10.1, 20.1, 11.0, False)

        ref2 = CatalogStar("gaia", "ref-2", "Ref 2", 10.2, 20.2, 11.2, False)

        candidate1 = CatalogStar("gaia", "cand-1", "Candidate 1", 10.3, 20.3, 12.0, False)

        candidate2 = CatalogStar("gaia", "cand-2", "Candidate 2", 10.4, 20.4, 12.2, False)

        field_catalog = SimpleNamespace(

            gaia_stars=[ref1, ref2, candidate1, candidate2],

            variable_stars=[],

            exoplanets=[],

        )

        settings = AppSettings.from_root(Path("."))

        settings.nearby_reference_count = 1

        settings.discovery_max_candidate_count = 2

        settings.discovery_min_magnitude = 10.0

        settings.discovery_max_magnitude = 14.0

        settings.discovery_min_candidate_score = 10.0

        pipeline = SimpleNamespace(

            _resolve_summary_fields=lambda files, settings, progress: solved_results,

            _best_field_catalog_for_solved_results=lambda catalog_service, solved_results, progress: field_catalog,

        )

        completed: list[DiscoverBatchResult] = []

        candidate_calls: list[tuple[str, tuple[str, ...], tuple[bool, ...]]] = []

        worker = DiscoverSourcesWorker(

            report_token=654321,

            pipeline=pipeline,

            source_files=source_files,

            settings=settings,

            object_name="Alpha",

            fit_config=None,

            y_axis_mode="differential_magnitude",

            period_method="ls",

            period_convention="standard",

            search_multiple_comparison_groups=True,

        )

        worker.discovery_completed.connect(completed.append)


        def measure_targets_side_effect(

            source_path: Path,

            scan_result: FileScanResult,

            wcs_path: Path,

            variable_stars: list[CatalogStar],

            reference_stars: list[CatalogStar],

            **_kwargs: object,

        ) -> list[PhotometryMeasurement]:

            if reference_stars:

                return [

                    PhotometryMeasurement(

                        source_id=entry.source_id,

                        source_name=entry.name,

                        catalog=entry.catalog,

                        object_name="Alpha",

                        file_path=source_path,

                        observation_time=datetime(2025, 1, 1),

                        filter_name="V",

                        ra_deg=entry.ra_deg,

                        dec_deg=entry.dec_deg,

                        x=20.0,

                        y=20.0,

                        flux=12000.0,

                        flux_error=90.0,

                        instrumental_magnitude=10.0,

                        differential_magnitude=None,

                        is_variable=False,

                        is_reference=True,

                    )

                    for entry in reference_stars

                ]

            candidate_calls.append(

                (

                    source_path.name,

                    tuple(entry.source_id for entry in variable_stars),

                    tuple(bool(entry.is_variable) for entry in variable_stars),

                )

            )

            measurements: list[PhotometryMeasurement] = []

            for target in variable_stars:

                measurements.extend(

                    [

                        PhotometryMeasurement(

                            source_id=target.source_id,

                            source_name=target.name,

                            catalog=target.catalog,

                            object_name="Alpha",

                            file_path=source_path,

                            observation_time=datetime(2025, 1, 1),

                            filter_name="V",

                            ra_deg=target.ra_deg,

                            dec_deg=target.dec_deg,

                            x=30.0,

                            y=30.0,

                            flux=15000.0,

                            flux_error=100.0,

                            instrumental_magnitude=10.5,

                            differential_magnitude=None,

                            is_variable=True,

                            is_reference=False,

                        )

                        for _ in range(4)

                    ]

                )

            return measurements


        def evaluate_group_side_effect(

            target_measurements: list[PhotometryMeasurement],

            reference_measurements: list[PhotometryMeasurement],

            comparison_source_ids: tuple[str, ...],

            fit_config: object,

            y_axis_mode: str,

            period_method: str,

            period_convention: str,

            **_kwargs: object,

        ) -> tuple[list[PhotometryMeasurement], LightCurveSeries | None, FitPeriodInferenceResult | None, object]:

            updated_measurements = [

                replace(

                    measurement,

                    differential_magnitude=0.15,

                    comparison_scatter=0.01,

                    comparison_source_ids=list(comparison_source_ids),

                    comparison_source_names=[comparison_source_ids[0]],

                )

                for measurement in target_measurements

            ]

            series = LightCurveSeries(

                object_name="Alpha",

                source_id=target_measurements[0].source_id,

                source_name=target_measurements[0].source_name,

                filter_name="V",

                points=[],

                candidate_score=35.0,

            )

            return updated_measurements, series, None, SimpleNamespace(
                comparison_source_ids=comparison_source_ids,
                comparison_source_names=tuple(comparison_source_ids),
                updated_target_row_count=len(updated_measurements),
                usable_target_row_count=len(updated_measurements),
                differential_value_count=len(updated_measurements),
                excluded_target_row_count=0,
            )


        with (

            patch("photometry_app.ui.workers.CatalogService"),

            patch("photometry_app.ui.workers.select_reference_stars", return_value=[ref1, ref2]),

            patch("photometry_app.ui.workers.resolve_aperture_profile", return_value=SimpleNamespace(aperture_radius=5.0, annulus_inner_radius=8.0, annulus_outer_radius=12.0, fwhm_pixels=None, note=None)),

            patch("photometry_app.ui.workers.measure_targets", side_effect=measure_targets_side_effect),

            patch("photometry_app.ui.workers._comparison_source_groups", return_value=([("ref-1",)], 1, False)),

            patch("photometry_app.ui.workers._evaluate_comparison_source_group_core", side_effect=evaluate_group_side_effect),

        ):

            worker.run()

        self.assertEqual(len(completed), 1)

        self.assertEqual(completed[0].retained_candidate_count, 2)

        self.assertEqual(len(candidate_calls), len(source_files))

        self.assertEqual({call[0] for call in candidate_calls}, {path.path.name for path in source_files})

        self.assertTrue(all(call[1] == ("cand-1", "cand-2") for call in candidate_calls))

        self.assertTrue(all(call[2] == (True, True) for call in candidate_calls))

    def test_discover_sources_worker_reports_light_curve_failure_diagnostics(self) -> None:

        source_file = FileScanResult(

            path=Path("frame-001.fits"),

            object_folder="Alpha",

            metadata=ObservationMetadata(

                date_obs=datetime(2025, 1, 1),

                filter_name="V",

                exposure_seconds=60.0,

                width=100,

                height=100,

                object_name="Alpha",

            ),

            wcs_status=WcsStatus.SOLVED,

        )

        solved_results = [

            (

                source_file,

                PlateSolveResult(

                    source_path=source_file.path,

                    status=WcsStatus.SOLVED,

                    solved_field=SolvedField(

                        center_ra_deg=10.0,

                        center_dec_deg=20.0,

                        radius_deg=0.2,

                        width=100,

                        height=100,

                        wcs_path=Path("frame-001.wcs"),

                    ),

                ),

            )

        ]

        ref1 = CatalogStar("gaia", "ref-1", "Ref 1", 10.1, 20.1, 11.0, False)

        ref2 = CatalogStar("gaia", "ref-2", "Ref 2", 10.2, 20.2, 11.2, False)

        candidate = CatalogStar("gaia", "cand-1", "Candidate 1", 10.3, 20.3, 12.0, False)

        field_catalog = SimpleNamespace(

            gaia_stars=[ref1, ref2, candidate],

            variable_stars=[],

            exoplanets=[],

        )

        settings = AppSettings.from_root(Path("."))

        settings.nearby_reference_count = 1

        settings.discovery_max_candidate_count = 1

        settings.discovery_min_candidate_score = 10.0

        pipeline = SimpleNamespace(

            _resolve_summary_fields=lambda files, settings, progress: solved_results,

            _best_field_catalog_for_solved_results=lambda catalog_service, solved_results, progress: field_catalog,

        )

        progress_messages: list[str] = []

        completed: list[DiscoverBatchResult] = []

        worker = DiscoverSourcesWorker(

            report_token=778,

            pipeline=pipeline,

            source_files=[source_file],

            settings=settings,

            object_name="Alpha",

            fit_config=None,

            y_axis_mode="differential_magnitude",

            period_method="ls",

            period_convention="standard",

            search_multiple_comparison_groups=True,

        )

        worker.progress_updated.connect(progress_messages.append)

        worker.discovery_completed.connect(completed.append)


        def measure_targets_side_effect(

            source_path: Path,

            scan_result: FileScanResult,

            wcs_path: Path,

            variable_stars: list[CatalogStar],

            reference_stars: list[CatalogStar],

            **_kwargs: object,

        ) -> list[PhotometryMeasurement]:

            if reference_stars:

                return [

                    PhotometryMeasurement(

                        source_id=entry.source_id,

                        source_name=entry.name,

                        catalog=entry.catalog,

                        object_name="Alpha",

                        file_path=source_path,

                        observation_time=datetime(2025, 1, 1),

                        filter_name="V",

                        ra_deg=entry.ra_deg,

                        dec_deg=entry.dec_deg,

                        x=20.0,

                        y=20.0,

                        flux=12000.0,

                        flux_error=90.0,

                        instrumental_magnitude=10.0,

                        differential_magnitude=None,

                        is_variable=False,

                        is_reference=True,

                    )

                    for entry in reference_stars

                ]

            return [

                PhotometryMeasurement(

                    source_id=entry.source_id,

                    source_name=entry.name,

                    catalog=entry.catalog,

                    object_name="Alpha",

                    file_path=source_path,

                    observation_time=datetime(2025, 1, 1),

                    filter_name="V",

                    ra_deg=entry.ra_deg,

                    dec_deg=entry.dec_deg,

                    x=30.0,

                    y=30.0,

                    flux=15000.0,

                    flux_error=100.0,

                    instrumental_magnitude=10.5,

                    differential_magnitude=None,

                    is_variable=True,

                    is_reference=False,

                )

                for entry in variable_stars

                for _ in range(4)

            ]


        with (

            patch("photometry_app.ui.workers.CatalogService"),

            patch("photometry_app.ui.workers.select_reference_stars", return_value=[ref1, ref2]),

            patch("photometry_app.ui.workers.resolve_aperture_profile", return_value=SimpleNamespace(aperture_radius=5.0, annulus_inner_radius=8.0, annulus_outer_radius=12.0, fwhm_pixels=None, note=None)),

            patch("photometry_app.ui.workers.measure_targets", side_effect=measure_targets_side_effect),

            patch("photometry_app.ui.workers._comparison_source_groups", return_value=([("ref-1",), ("ref-2",)], 2, False)),

            patch(

                "photometry_app.ui.workers._evaluate_comparison_source_group_core",

                side_effect=[

                    (

                        [],

                        None,

                        None,

                        SimpleNamespace(

                            comparison_source_ids=("ref-1",),

                            comparison_source_names=("Ref 1",),

                            updated_target_row_count=4,

                            usable_target_row_count=0,

                            differential_value_count=0,

                            excluded_target_row_count=4,

                        ),

                    ),

                    (

                        [],

                        None,

                        None,

                        SimpleNamespace(

                            comparison_source_ids=("ref-2",),

                            comparison_source_names=("Ref 2",),

                            updated_target_row_count=4,

                            usable_target_row_count=1,

                            differential_value_count=0,

                            excluded_target_row_count=3,

                        ),

                    ),

                ],

            ),

        ):

            worker.run()

        self.assertEqual(len(completed), 1)

        self.assertEqual(completed[0].retained_candidate_count, 0)

        self.assertTrue(

            any(

                "no differential light curve could be built. Raw target rows 4; best attempted comparison group (Ref 2) yielded 4 target row(s) after differential photometry, 1 usable value(s), 0 differential magnitude value(s), and 3 excluded point(s)." in message

                for message in progress_messages

            )

        )


    def test_discover_sources_worker_builds_raw_light_curves_for_all_discoverable_stars(self) -> None:

        source_file = FileScanResult(

            path=Path("frame-001.fits"),

            object_folder="Alpha",

            metadata=ObservationMetadata(

                date_obs=datetime(2025, 1, 1),

                filter_name="V",

                exposure_seconds=60.0,

                width=100,

                height=100,

                object_name="Alpha",

            ),

            wcs_status=WcsStatus.SOLVED,

        )

        solved_results = [

            (

                source_file,

                PlateSolveResult(

                    source_path=source_file.path,

                    status=WcsStatus.SOLVED,

                    solved_field=SolvedField(

                        center_ra_deg=10.0,

                        center_dec_deg=20.0,

                        radius_deg=0.2,

                        width=100,

                        height=100,

                        wcs_path=Path("frame-001.wcs"),

                    ),

                ),

            )

        ]

        ref1 = CatalogStar("gaia", "ref-1", "Ref 1", 10.1, 20.1, 11.0, False)

        ref2 = CatalogStar("gaia", "ref-2", "Ref 2", 10.2, 20.2, 11.2, False)

        candidate1 = CatalogStar("gaia", "cand-1", "Candidate 1", 10.3, 20.3, 12.0, False)

        candidate2 = CatalogStar("gaia", "cand-2", "Candidate 2", 10.4, 20.4, 12.4, False)

        field_catalog = SimpleNamespace(

            gaia_stars=[ref1, ref2, candidate1, candidate2],

            variable_stars=[],

            exoplanets=[],

        )

        settings = AppSettings.from_root(Path("."))

        settings.nearby_reference_count = 1

        settings.discovery_max_candidate_count = 1

        settings.discovery_min_candidate_score = 10.0

        pipeline = SimpleNamespace(

            _resolve_summary_fields=lambda files, settings, progress: solved_results,

            _best_field_catalog_for_solved_results=lambda catalog_service, solved_results, progress: field_catalog,

        )

        completed: list[DiscoverBatchResult] = []

        batches: list[DiscoverBatchResult] = []

        worker = DiscoverSourcesWorker(

            report_token=777,

            pipeline=pipeline,

            source_files=[source_file],

            settings=settings,

            object_name="Alpha",

            fit_config=None,

            y_axis_mode="differential_magnitude",

            period_method="ls",

            period_convention="standard",

            search_multiple_comparison_groups=True,

        )

        worker.discovery_batch_ready.connect(batches.append)

        worker.discovery_completed.connect(completed.append)

        def measure_targets_side_effect(

            source_path: Path,

            scan_result: FileScanResult,

            wcs_path: Path,

            variable_stars: list[CatalogStar],

            reference_stars: list[CatalogStar],

            **_kwargs: object,

        ) -> list[PhotometryMeasurement]:

            if reference_stars:

                return [

                    PhotometryMeasurement(

                        source_id=entry.source_id,

                        source_name=entry.name,

                        catalog=entry.catalog,

                        object_name="Alpha",

                        file_path=source_path,

                        observation_time=datetime(2025, 1, 1),

                        filter_name="V",

                        ra_deg=entry.ra_deg,

                        dec_deg=entry.dec_deg,

                        x=20.0,

                        y=20.0,

                        flux=12000.0,

                        flux_error=90.0,

                        instrumental_magnitude=10.0,

                        differential_magnitude=None,

                        is_variable=False,

                        is_reference=True,

                    )

                    for entry in reference_stars

                ]

            if variable_stars and variable_stars[0].name.startswith("Candidate"):

                measurements: list[PhotometryMeasurement] = []

                for entry in variable_stars:

                    measurements.extend(

                        [

                            PhotometryMeasurement(

                                source_id=entry.source_id,

                                source_name=entry.name,

                                catalog=entry.catalog,

                                object_name="Alpha",

                                file_path=source_path,

                                observation_time=datetime(2025, 1, 1),

                                filter_name="V",

                                ra_deg=entry.ra_deg,

                                dec_deg=entry.dec_deg,

                                x=30.0,

                                y=30.0,

                                flux=16000.0,

                                flux_error=100.0,

                                instrumental_magnitude=10.5,

                                differential_magnitude=None,

                                is_variable=True,

                                is_reference=False,

                                peak_pixel_value=5000.0,

                                background_level=200.0,

                                saturation_threshold=10000.0,

                            )

                            for _ in range(4)

                        ]

                    )

                return measurements

            measurements: list[PhotometryMeasurement] = []

            for target in variable_stars:

                measurements.extend(

                    [

                        PhotometryMeasurement(

                            source_id=target.source_id,

                            source_name=target.name,

                            catalog=target.catalog,

                            object_name="Alpha",

                            file_path=source_path,

                            observation_time=datetime(2025, 1, 1),

                            filter_name="V",

                            ra_deg=target.ra_deg,

                            dec_deg=target.dec_deg,

                            x=30.0,

                            y=30.0,

                            flux=15000.0,

                            flux_error=100.0,

                            instrumental_magnitude=10.5,

                            differential_magnitude=None,

                            is_variable=True,

                            is_reference=False,

                        )

                        for _ in range(4)

                    ]

                )

            return measurements

        def evaluate_group_side_effect(

            target_measurements: list[PhotometryMeasurement],

            reference_measurements: list[PhotometryMeasurement],

            comparison_source_ids: tuple[str, ...],

            fit_config: object,

            y_axis_mode: str,

            period_method: str,

            period_convention: str,

            **_kwargs: object,

        ) -> tuple[list[PhotometryMeasurement], LightCurveSeries | None, FitPeriodInferenceResult | None, object]:

            updated_measurements = [

                replace(

                    measurement,

                    differential_magnitude=0.15,

                    comparison_scatter=0.01,

                    comparison_source_ids=list(comparison_source_ids),

                    comparison_source_names=[comparison_source_ids[0]],

                )

                for measurement in target_measurements

            ]

            series = LightCurveSeries(

                object_name="Alpha",

                source_id=target_measurements[0].source_id,

                source_name=target_measurements[0].source_name,

                filter_name="V",

                points=[],

                candidate_score=35.0,

            )

            return updated_measurements, series, None, SimpleNamespace(
                comparison_source_ids=comparison_source_ids,
                comparison_source_names=tuple(comparison_source_ids),
                updated_target_row_count=len(updated_measurements),
                usable_target_row_count=len(updated_measurements),
                differential_value_count=len(updated_measurements),
                excluded_target_row_count=0,
            )

        with (

            patch("photometry_app.ui.workers.CatalogService"),

            patch("photometry_app.ui.workers.select_reference_stars", return_value=[ref1, ref2]),

            patch("photometry_app.ui.workers.resolve_aperture_profile", return_value=SimpleNamespace(aperture_radius=5.0, annulus_inner_radius=8.0, annulus_outer_radius=12.0, fwhm_pixels=None, note=None)),

            patch("photometry_app.ui.workers.measure_targets", side_effect=measure_targets_side_effect),

            patch("photometry_app.ui.workers._comparison_source_groups", return_value=([("ref-1",)], 1, False)),

            patch("photometry_app.ui.workers._evaluate_comparison_source_group_core", side_effect=evaluate_group_side_effect),

        ):

            worker.run()

        self.assertEqual(len(completed), 1)

        self.assertEqual(completed[0].tested_candidate_count, 2)

        self.assertEqual(completed[0].retained_candidate_count, 2)


        self.assertEqual([batch.current_stage_label for batch in batches], ["raw light curve", "raw light curve"])

        self.assertEqual([result.entry.name for result in completed[0].source_results], ["Discover 1", "Discover 2"])


        self.assertEqual(completed[0].source_results[1].entry.metadata.get("discover_type"), "raw light curve")


    def test_discover_sources_worker_honors_allowed_source_ids(self) -> None:

        source_file = FileScanResult(

            path=Path("frame-001.fits"),

            object_folder="Alpha",

            metadata=ObservationMetadata(

                date_obs=datetime(2025, 1, 1),

                filter_name="V",

                exposure_seconds=60.0,

                width=100,

                height=100,

                object_name="Alpha",

            ),

            wcs_status=WcsStatus.SOLVED,

        )

        solved_results = [

            (

                source_file,

                PlateSolveResult(

                    source_path=source_file.path,

                    status=WcsStatus.SOLVED,

                    solved_field=SolvedField(

                        center_ra_deg=10.0,

                        center_dec_deg=20.0,

                        radius_deg=0.2,

                        width=100,

                        height=100,

                        wcs_path=Path("frame-001.wcs"),

                    ),

                ),

            )

        ]

        ref1 = CatalogStar("gaia", "ref-1", "Ref 1", 10.1, 20.1, 11.0, False)

        ref2 = CatalogStar("gaia", "ref-2", "Ref 2", 10.2, 20.2, 11.2, False)

        candidate1 = CatalogStar("gaia", "cand-1", "Candidate 1", 10.3, 20.3, 12.0, False)

        candidate2 = CatalogStar("gaia", "cand-2", "Candidate 2", 10.4, 20.4, 12.3, False)

        field_catalog = SimpleNamespace(

            gaia_stars=[ref1, ref2, candidate1, candidate2],

            variable_stars=[],

            exoplanets=[],

        )

        settings = AppSettings.from_root(Path("."))

        settings.nearby_reference_count = 1

        settings.discovery_max_candidate_count = 5

        settings.discovery_min_magnitude = 10.0

        settings.discovery_max_magnitude = 14.0

        settings.discovery_min_candidate_score = 25.0

        pipeline = SimpleNamespace(

            _resolve_summary_fields=lambda files, settings, progress: solved_results,

            _best_field_catalog_for_solved_results=lambda catalog_service, solved_results, progress: field_catalog,

        )

        completed: list[DiscoverBatchResult] = []

        worker = DiscoverSourcesWorker(

            report_token=999,

            pipeline=pipeline,

            source_files=[source_file],

            settings=settings,

            object_name="Alpha",

            fit_config=None,

            y_axis_mode="differential_magnitude",

            period_method="ls",

            period_convention="standard",

            allowed_source_ids={"cand-2"},

            search_multiple_comparison_groups=True,

        )

        worker.discovery_completed.connect(completed.append)

        def measure_targets_side_effect(

            source_path: Path,

            scan_result: FileScanResult,

            wcs_path: Path,

            variable_stars: list[CatalogStar],

            reference_stars: list[CatalogStar],

            **_kwargs: object,

        ) -> list[PhotometryMeasurement]:

            if reference_stars:

                return [

                    PhotometryMeasurement(

                        source_id=entry.source_id,

                        source_name=entry.name,

                        catalog=entry.catalog,

                        object_name="Alpha",

                        file_path=source_path,

                        observation_time=datetime(2025, 1, 1),

                        filter_name="V",

                        ra_deg=entry.ra_deg,

                        dec_deg=entry.dec_deg,

                        x=20.0,

                        y=20.0,

                        flux=12000.0,

                        flux_error=90.0,

                        instrumental_magnitude=10.0,

                        differential_magnitude=None,

                        is_variable=False,

                        is_reference=True,

                    )

                    for entry in reference_stars

                ]

            target = variable_stars[0]

            return [

                PhotometryMeasurement(

                    source_id=target.source_id,

                    source_name=target.name,

                    catalog=target.catalog,

                    object_name="Alpha",

                    file_path=source_path,

                    observation_time=datetime(2025, 1, 1),

                    filter_name="V",

                    ra_deg=target.ra_deg,

                    dec_deg=target.dec_deg,

                    x=30.0,

                    y=30.0,

                    flux=15000.0,

                    flux_error=100.0,

                    instrumental_magnitude=10.5,

                    differential_magnitude=None,

                    is_variable=True,

                    is_reference=False,

                )

                for _ in range(4)

            ]

        def evaluate_group_side_effect(

            target_measurements: list[PhotometryMeasurement],

            reference_measurements: list[PhotometryMeasurement],

            comparison_source_ids: tuple[str, ...],

            fit_config: object,

            y_axis_mode: str,

            period_method: str,

            period_convention: str,

            **_kwargs: object,

        ) -> tuple[list[PhotometryMeasurement], LightCurveSeries | None, FitPeriodInferenceResult | None, object]:

            updated_measurements = [

                replace(

                    measurement,

                    differential_magnitude=0.15,

                    comparison_scatter=0.01,

                    comparison_source_ids=list(comparison_source_ids),

                    comparison_source_names=[comparison_source_ids[0]],

                )

                for measurement in target_measurements

            ]

            series = LightCurveSeries(

                object_name="Alpha",

                source_id=target_measurements[0].source_id,

                source_name=target_measurements[0].source_name,

                filter_name="V",

                points=[],

                candidate_score=40.0,

            )

            return updated_measurements, series, None, SimpleNamespace(
                comparison_source_ids=comparison_source_ids,
                comparison_source_names=tuple(comparison_source_ids),
                updated_target_row_count=len(updated_measurements),
                usable_target_row_count=len(updated_measurements),
                differential_value_count=len(updated_measurements),
                excluded_target_row_count=0,
            )

        with (

            patch("photometry_app.ui.workers.CatalogService"),

            patch("photometry_app.ui.workers.select_reference_stars", return_value=[ref1, ref2]),

            patch("photometry_app.ui.workers.resolve_aperture_profile", return_value=SimpleNamespace(aperture_radius=5.0, annulus_inner_radius=8.0, annulus_outer_radius=12.0, fwhm_pixels=None, note=None)),

            patch("photometry_app.ui.workers.measure_targets", side_effect=measure_targets_side_effect),

            patch("photometry_app.ui.workers._comparison_source_groups", return_value=([("ref-1",)], 1, False)),

            patch("photometry_app.ui.workers._evaluate_comparison_source_group_core", side_effect=evaluate_group_side_effect),

        ):

            worker.run()

        self.assertEqual(len(completed), 1)

        self.assertEqual(completed[0].tested_candidate_count, 1)

        self.assertEqual(completed[0].retained_candidate_count, 1)

        self.assertEqual([result.entry.source_id for result in completed[0].source_results], ["cand-2"])



    def test_discover_sources_worker_uses_photometry_worker_setting(self) -> None:

        class RecordingThreadPoolExecutor(ConcurrentThreadPoolExecutor):

            created_max_workers: list[int] = []



            def __init__(self, max_workers: int | None = None, *args: object, **kwargs: object) -> None:

                type(self).created_max_workers.append(int(max_workers or 0))

                super().__init__(max_workers=max_workers, *args, **kwargs)



        source_files = [

            FileScanResult(

                path=Path(f"frame-00{index}.fits"),

                object_folder="Alpha",

                metadata=ObservationMetadata(

                    date_obs=datetime(2025, 1, index),

                    filter_name="V",

                    exposure_seconds=60.0,

                    width=100,

                    height=100,

                    object_name="Alpha",

                ),

                wcs_status=WcsStatus.SOLVED,

            )

            for index in (1, 2)

        ]

        solved_results = [

            (

                file_result,

                PlateSolveResult(

                    source_path=file_result.path,

                    status=WcsStatus.SOLVED,

                    solved_field=SolvedField(

                        center_ra_deg=10.0,

                        center_dec_deg=20.0,

                        radius_deg=0.2,

                        width=100,

                        height=100,

                        wcs_path=Path(f"{file_result.path.stem}.wcs"),

                    ),

                ),

            )

            for file_result in source_files

        ]

        ref1 = CatalogStar("gaia", "ref-1", "Ref 1", 10.1, 20.1, 11.0, False)

        ref2 = CatalogStar("gaia", "ref-2", "Ref 2", 10.2, 20.2, 11.2, False)

        candidate = CatalogStar("gaia", "cand-1", "Candidate 1", 10.3, 20.3, 12.0, False)

        field_catalog = SimpleNamespace(

            gaia_stars=[ref1, ref2, candidate],

            variable_stars=[],

            exoplanets=[],

        )

        settings = AppSettings.from_root(Path("."))

        settings.photometry_parallel_workers = 2

        settings.nearby_reference_count = 1

        settings.discovery_max_candidate_count = 1

        settings.discovery_min_candidate_score = 10.0

        pipeline = SimpleNamespace(

            _resolve_summary_fields=lambda files, settings, progress: solved_results,

            _best_field_catalog_for_solved_results=lambda catalog_service, solved_results, progress: field_catalog,

        )

        completed: list[object] = []

        worker = DiscoverSourcesWorker(

            report_token=654,

            pipeline=pipeline,

            source_files=source_files,

            settings=settings,

            object_name="Alpha",

            fit_config=None,

            y_axis_mode="differential_magnitude",

            period_method="ls",

            period_convention="standard",

            search_multiple_comparison_groups=True,

        )

        worker.discovery_completed.connect(completed.append)



        def measure_targets_side_effect(

            source_path: Path,

            scan_result: FileScanResult,

            wcs_path: Path,

            variable_stars: list[CatalogStar],

            reference_stars: list[CatalogStar],

            **_kwargs: object,

        ) -> list[PhotometryMeasurement]:

            stars = reference_stars if reference_stars else variable_stars

            return [

                PhotometryMeasurement(

                    source_id=entry.source_id,

                    source_name=entry.name,

                    catalog=entry.catalog,

                    object_name="Alpha",

                    file_path=source_path,

                    observation_time=datetime(2025, 1, 1),

                    filter_name="V",

                    ra_deg=entry.ra_deg,

                    dec_deg=entry.dec_deg,

                    x=25.0,

                    y=25.0,

                    flux=14000.0,

                    flux_error=100.0,

                    instrumental_magnitude=10.0,

                    differential_magnitude=None if reference_stars else 0.1,

                    is_variable=not bool(reference_stars),

                    is_reference=bool(reference_stars),

                )

                for entry in stars

            ]



        with (

            patch("photometry_app.ui.workers.ThreadPoolExecutor", RecordingThreadPoolExecutor),

            patch("photometry_app.ui.workers.CatalogService"),

            patch("photometry_app.ui.workers.select_reference_stars", return_value=[ref1, ref2]),

            patch("photometry_app.ui.workers.resolve_aperture_profile", return_value=SimpleNamespace(aperture_radius=5.0, annulus_inner_radius=8.0, annulus_outer_radius=12.0, fwhm_pixels=None, note=None)),

            patch("photometry_app.ui.workers.measure_targets", side_effect=measure_targets_side_effect),

            patch(

                "photometry_app.ui.workers._comparison_source_groups",

                return_value=([("ref-1",)], 1, False),

            ),

            patch(

                "photometry_app.ui.workers._evaluate_comparison_source_group",

                return_value=(

                    [

                        replace(

                            measure_targets_side_effect(source_files[0].path, source_files[0], Path("a"), [candidate], [],)[0],

                            differential_magnitude=0.1,

                        )

                    ],

                    LightCurveSeries(

                        object_name="Alpha",

                        source_id="cand-1",

                        source_name="Candidate 1",

                        filter_name="V",

                        points=[],

                        candidate_score=20.0,

                    ),

                    None,

                ),

            ),

        ):

            RecordingThreadPoolExecutor.created_max_workers = []

            worker.run()



        self.assertEqual(len(completed), 1)

        self.assertIn(2, RecordingThreadPoolExecutor.created_max_workers)



    def test_literature_period_worker_uses_thread_pool_for_multiple_entries(self) -> None:

        class RecordingThreadPoolExecutor(ConcurrentThreadPoolExecutor):

            created_max_workers: list[int] = []



            def __init__(self, max_workers: int | None = None, *args: object, **kwargs: object) -> None:

                type(self).created_max_workers.append(int(max_workers or 0))

                super().__init__(max_workers=max_workers, *args, **kwargs)



        entries = [

            CatalogStar(

                catalog="vsx",

                source_id=f"vsx-{index}",

                name=f"Target {index}",

                ra_deg=10.0 + index,

                dec_deg=20.0 + index,

                magnitude=12.0,

                is_variable=True,

            )

            for index in range(3)

        ]

        completed: list[object] = []

        worker = LiteraturePeriodWorker(report_token=123, entries=entries)

        worker.literature_completed.connect(completed.append)



        with (

            patch("photometry_app.ui.workers.ThreadPoolExecutor", RecordingThreadPoolExecutor),

            patch(

                "photometry_app.ui.workers.fetch_catalog_literature_period_result",

                side_effect=lambda entry: LiteraturePeriodResult(period_days=1.0 + int(entry.source_id.split("-")[-1]), source="VSX"),

            ),

        ):

            RecordingThreadPoolExecutor.created_max_workers = []

            worker.run()



        self.assertEqual(len(completed), 1)

        result = completed[0]

        self.assertEqual(result.report_token, 123)

        self.assertEqual(result.success_count, 3)

        self.assertEqual(len(result.results), 3)

        self.assertIn(3, RecordingThreadPoolExecutor.created_max_workers)

        self.assertEqual(result.execution_mode, "thread")

        self.assertFalse(result.cancelled)



    def test_literature_period_worker_honors_configured_thread_limit(self) -> None:

        class RecordingThreadPoolExecutor(ConcurrentThreadPoolExecutor):

            created_max_workers: list[int] = []



            def __init__(self, max_workers: int | None = None, *args: object, **kwargs: object) -> None:

                type(self).created_max_workers.append(int(max_workers or 0))

                super().__init__(max_workers=max_workers, *args, **kwargs)



        entries = [

            CatalogStar(

                catalog="vsx",

                source_id=f"vsx-{index}",

                name=f"Target {index}",

                ra_deg=10.0 + index,

                dec_deg=20.0 + index,

                magnitude=12.0,

                is_variable=True,

            )

            for index in range(4)

        ]

        completed: list[object] = []

        worker = LiteraturePeriodWorker(report_token=123, entries=entries, max_parallel_workers=2)

        worker.literature_completed.connect(completed.append)



        with (

            patch("photometry_app.ui.workers.ThreadPoolExecutor", RecordingThreadPoolExecutor),

            patch(

                "photometry_app.ui.workers.fetch_catalog_literature_period_result",

                side_effect=lambda entry: LiteraturePeriodResult(period_days=1.0 + int(entry.source_id.split("-")[-1]), source="VSX"),

            ),

        ):

            RecordingThreadPoolExecutor.created_max_workers = []

            worker.run()



        self.assertEqual(len(completed), 1)

        self.assertIn(2, RecordingThreadPoolExecutor.created_max_workers)

        self.assertEqual(completed[0].worker_count, 2)



    def test_calculate_period_worker_uses_process_pool_for_multiple_series(self) -> None:

        class RecordingProcessPoolExecutor(ConcurrentProcessPoolExecutor):

            created_max_workers: list[int] = []



            def __init__(self, max_workers: int | None = None, *args: object, **kwargs: object) -> None:

                type(self).created_max_workers.append(int(max_workers or 0))



            def __enter__(self) -> "RecordingProcessPoolExecutor":

                return self



            def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:

                return False



            def submit(self, fn: object, *args: object, **kwargs: object) -> Future:

                future: Future = Future()

                try:

                    future.set_result(fn(*args, **kwargs))

                except Exception as exc:

                    future.set_exception(exc)

                return future



            def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:

                return None



        start = datetime(2026, 3, 16, 1, 0, 0)

        series_items = []

        for index in range(2):

            series = LightCurveSeries(

                object_name="Demo",

                source_id=f"vsx-{index}",

                source_name=f"Target {index}",

                filter_name="R",

                points=[

                    LightCurvePoint(

                        observation_time=start + timedelta(minutes=point_index * 10),

                        file_path=Path(f"frame_{index}_{point_index}.fits"),

                        differential_magnitude=0.25 + (0.01 * point_index),

                        instrumental_magnitude=-9.0,

                        flux=5000.0,

                        flux_error=15.0,

                        differential_magnitude_error=0.03,

                    )

                    for point_index in range(8)

                ],

            )

            series_items.append(((series.source_id, series.filter_name), series))



        completed: list[object] = []

        worker = CalculatePeriodWorker(

            report_token=456,

            series_items=series_items,

            fit_config=None,

            y_axis_mode="differential_magnitude",

            period_method="harmonic_fit",

            period_convention="standard",

        )

        worker.calculation_completed.connect(completed.append)



        def fake_infer(series: LightCurveSeries, fit_config: object | None = None, y_axis_mode: str = "differential_magnitude", method: str = "harmonic_fit", period_convention: str = "standard") -> FitPeriodInferenceResult | None:

            if method == "bls":

                return FitPeriodInferenceResult(period_hours=12.0, method="bls", eclipse_duration_hours=1.5)

            return FitPeriodInferenceResult(period_hours=12.0, method=method)



        with (

            patch("photometry_app.ui.workers.ProcessPoolExecutor", RecordingProcessPoolExecutor),

            patch("photometry_app.ui.workers.os.cpu_count", return_value=8),

            patch("photometry_app.core.period_tasks.infer_fit_period_result", side_effect=fake_infer),

        ):

            RecordingProcessPoolExecutor.created_max_workers = []

            worker.run()



        self.assertEqual(len(completed), 1)

        result = completed[0]

        self.assertEqual(result.report_token, 456)

        self.assertEqual(result.success_count, 2)

        self.assertEqual(len(result.results), 2)

        self.assertIn(2, RecordingProcessPoolExecutor.created_max_workers)

        self.assertEqual(result.execution_mode, "process")

        self.assertFalse(result.cancelled)



    def test_calculate_period_worker_can_skip_bls_duration_pass(self) -> None:

        start = datetime(2026, 3, 16, 1, 0, 0)

        series = LightCurveSeries(

            object_name="Demo",

            source_id="vsx-1",

            source_name="Target",

            filter_name="R",

            points=[

                LightCurvePoint(

                    observation_time=start + timedelta(minutes=point_index * 10),

                    file_path=Path(f"frame_{point_index}.fits"),

                    differential_magnitude=0.25 + (0.01 * point_index),

                    instrumental_magnitude=-9.0,

                    flux=5000.0,

                    flux_error=15.0,

                    differential_magnitude_error=0.03,

                )

                for point_index in range(8)

            ],

        )

        completed: list[object] = []

        worker = CalculatePeriodWorker(

            report_token=789,

            series_items=[((series.source_id, series.filter_name), series)],

            fit_config=None,

            y_axis_mode="differential_magnitude",

            period_method="harmonic_fit",

            period_convention="standard",

            include_bls_duration=False,

        )

        worker.calculation_completed.connect(completed.append)

        called_methods: list[str] = []



        def fake_infer(series: LightCurveSeries, fit_config: object | None = None, y_axis_mode: str = "differential_magnitude", method: str = "harmonic_fit", period_convention: str = "standard") -> FitPeriodInferenceResult | None:

            called_methods.append(method)

            return FitPeriodInferenceResult(period_hours=12.0, method=method)



        with patch("photometry_app.core.period_tasks.infer_fit_period_result", side_effect=fake_infer):

            worker.run()

        self.assertEqual(called_methods, ["harmonic_fit"])

        self.assertEqual(len(completed), 1)

        self.assertEqual(completed[0].success_count, 1)



    def test_calculate_period_worker_honors_configured_process_limit(self) -> None:

        class RecordingProcessPoolExecutor(ConcurrentProcessPoolExecutor):

            created_max_workers: list[int] = []



            def __init__(self, max_workers: int | None = None, *args: object, **kwargs: object) -> None:

                type(self).created_max_workers.append(int(max_workers or 0))



            def __enter__(self) -> "RecordingProcessPoolExecutor":

                return self



            def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:

                return False



            def submit(self, fn: object, *args: object, **kwargs: object) -> Future:

                future: Future = Future()

                try:

                    future.set_result(fn(*args, **kwargs))

                except Exception as exc:

                    future.set_exception(exc)

                return future



            def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:

                return None



        start = datetime(2026, 3, 16, 1, 0, 0)

        series_items = []

        for index in range(4):

            series = LightCurveSeries(

                object_name="Demo",

                source_id=f"vsx-{index}",

                source_name=f"Target {index}",

                filter_name="R",

                points=[

                    LightCurvePoint(

                        observation_time=start + timedelta(minutes=point_index * 10),

                        file_path=Path(f"frame_{index}_{point_index}.fits"),

                        differential_magnitude=0.25 + (0.01 * point_index),

                        instrumental_magnitude=-9.0,

                        flux=5000.0,

                        flux_error=15.0,

                        differential_magnitude_error=0.03,

                    )

                    for point_index in range(8)

                ],

            )

            series_items.append(((series.source_id, series.filter_name), series))



        completed: list[object] = []

        worker = CalculatePeriodWorker(

            report_token=456,

            series_items=series_items,

            fit_config=None,

            y_axis_mode="differential_magnitude",

            period_method="harmonic_fit",

            period_convention="standard",

            max_parallel_workers=2,

        )

        worker.calculation_completed.connect(completed.append)



        def fake_infer(series: LightCurveSeries, fit_config: object | None = None, y_axis_mode: str = "differential_magnitude", method: str = "harmonic_fit", period_convention: str = "standard") -> FitPeriodInferenceResult | None:

            if method == "bls":

                return FitPeriodInferenceResult(period_hours=12.0, method="bls", eclipse_duration_hours=1.5)

            return FitPeriodInferenceResult(period_hours=12.0, method=method)



        with (

            patch("photometry_app.ui.workers.ProcessPoolExecutor", RecordingProcessPoolExecutor),

            patch("photometry_app.ui.workers.os.cpu_count", return_value=8),

            patch("photometry_app.core.period_tasks.infer_fit_period_result", side_effect=fake_infer),

        ):

            RecordingProcessPoolExecutor.created_max_workers = []

            worker.run()



        self.assertEqual(len(completed), 1)

        self.assertIn(2, RecordingProcessPoolExecutor.created_max_workers)

        self.assertEqual(completed[0].worker_count, 2)



    def test_literature_period_worker_can_cancel_before_start(self) -> None:

        entry = CatalogStar(

            catalog="vsx",

            source_id="vsx-1",

            name="Target",

            ra_deg=10.0,

            dec_deg=20.0,

            magnitude=12.0,

            is_variable=True,

        )

        completed: list[object] = []

        worker = LiteraturePeriodWorker(report_token=789, entries=[entry])

        worker.literature_completed.connect(completed.append)

        worker.request_cancel()



        worker.run()



        self.assertEqual(len(completed), 1)

        result = completed[0]

        self.assertTrue(result.cancelled)

        self.assertEqual(result.success_count, 0)

        self.assertEqual(result.total_count, 1)

        self.assertEqual(result.execution_mode, "serial")



    def test_calculate_period_worker_can_cancel_before_start(self) -> None:

        start = datetime(2026, 3, 16, 1, 0, 0)

        series = LightCurveSeries(

            object_name="Demo",

            source_id="vsx-1",

            source_name="Target",

            filter_name="R",

            points=[

                LightCurvePoint(

                    observation_time=start + timedelta(minutes=point_index * 10),

                    file_path=Path(f"frame_{point_index}.fits"),

                    differential_magnitude=0.25 + (0.01 * point_index),

                    instrumental_magnitude=-9.0,

                    flux=5000.0,

                    flux_error=15.0,

                    differential_magnitude_error=0.03,

                )

                for point_index in range(8)

            ],

        )

        completed: list[object] = []

        worker = CalculatePeriodWorker(

            report_token=101,

            series_items=[((series.source_id, series.filter_name), series)],

            fit_config=None,

            y_axis_mode="differential_magnitude",

            period_method="harmonic_fit",

            period_convention="standard",

        )

        worker.calculation_completed.connect(completed.append)

        worker.request_cancel()



        worker.run()



        self.assertEqual(len(completed), 1)

        result = completed[0]

        self.assertTrue(result.cancelled)

        self.assertEqual(result.success_count, 0)

        self.assertEqual(result.total_count, 1)

        self.assertEqual(result.execution_mode, "serial")



    def test_optimize_comparison_fit_worker_selects_best_combination(self) -> None:

        class RecordingProcessPoolExecutor(ConcurrentProcessPoolExecutor):

            created_max_workers: list[int] = []



            def __init__(self, max_workers: int | None = None, *args: object, **kwargs: object) -> None:

                type(self).created_max_workers.append(int(max_workers or 0))



            def submit(self, fn: object, *args: object, **kwargs: object) -> Future:

                future: Future = Future()

                try:

                    future.set_result(fn(*args, **kwargs))

                except Exception as exc:

                    future.set_exception(exc)

                return future



            def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:

                return None



        measurement_time = datetime(2026, 3, 16, 1, 0, 0)

        target_measurements = [

            PhotometryMeasurement(

                source_id="vsx-1",

                source_name="Target",

                catalog="vsx",

                object_name="Demo",

                file_path=Path("frame_01.fits"),

                observation_time=measurement_time,

                filter_name="R",

                ra_deg=10.0,

                dec_deg=20.0,

                x=50.0,

                y=60.0,

                flux=5000.0,

                flux_error=15.0,

                instrumental_magnitude=-9.0,

                differential_magnitude=0.32,

                is_variable=True,

                is_reference=False,

                comparison_source_ids=["gaia-1", "gaia-3"],

                comparison_source_names=["Comp A", "Comp C"],

                differential_magnitude_error=0.03,

            )

        ]

        reference_measurements = [

            PhotometryMeasurement(

                source_id="gaia-1",

                source_name="Comp A",

                catalog="gaia",

                object_name="Demo",

                file_path=Path("frame_01.fits"),

                observation_time=measurement_time,

                filter_name="R",

                ra_deg=10.1,

                dec_deg=20.1,

                x=70.0,

                y=80.0,

                flux=4000.0,

                flux_error=12.0,

                instrumental_magnitude=-8.0,

                differential_magnitude=None,

                is_variable=False,

                is_reference=True,

            ),

            PhotometryMeasurement(

                source_id="gaia-2",

                source_name="Comp B",

                catalog="gaia",

                object_name="Demo",

                file_path=Path("frame_01.fits"),

                observation_time=measurement_time,

                filter_name="R",

                ra_deg=10.2,

                dec_deg=20.2,

                x=75.0,

                y=85.0,

                flux=4100.0,

                flux_error=12.0,

                instrumental_magnitude=-8.1,

                differential_magnitude=None,

                is_variable=False,

                is_reference=True,

            ),

            PhotometryMeasurement(

                source_id="gaia-3",

                source_name="Comp C",

                catalog="gaia",

                object_name="Demo",

                file_path=Path("frame_01.fits"),

                observation_time=measurement_time,

                filter_name="R",

                ra_deg=10.3,

                dec_deg=20.3,

                x=80.0,

                y=90.0,

                flux=4200.0,

                flux_error=12.0,

                instrumental_magnitude=-8.2,

                differential_magnitude=None,

                is_variable=False,

                is_reference=True,

            ),

        ]

        completed: list[object] = []

        progress_messages: list[str] = []

        worker = OptimizeComparisonFitWorker(

            report_token=202,

            target_source_id="vsx-1",

            target_source_name="Target",

            filter_name="R",

            target_measurements=target_measurements,

            reference_measurements=reference_measurements,

            actual_period_days=1.0,

            preferred_comparison_source_ids=["gaia-1", "gaia-3"],

            nearby_reference_count=2,

            fit_config=None,

            y_axis_mode="differential_magnitude",

            period_method="harmonic_fit",

            period_convention="standard",

            max_parallel_workers=2,

        )

        worker.optimization_completed.connect(completed.append)

        worker.progress_updated.connect(progress_messages.append)



        def fake_task(

            _target_measurements: list[PhotometryMeasurement],

            _reference_measurements: list[PhotometryMeasurement],

            comparison_source_ids: tuple[str, ...],

            actual_period_days: float,

            fit_config: object | None,

            y_axis_mode: str,

            period_method: str,

            period_convention: str,

            eclipsing_binary_match_tolerance: float,

        ) -> tuple[tuple[str, ...], float | None, float | None, float | None, int, int, str, bool, float | None, float | None]:

            period_days = {

                ("gaia-1", "gaia-3"): 1.18,

                ("gaia-1", "gaia-2"): 1.00,

                ("gaia-2", "gaia-3"): 0.91,

            }[comparison_source_ids]

            relative_error = abs(period_days - actual_period_days) / actual_period_days

            match_index = max(0.0, 100.0 * (1.0 - relative_error))

            return comparison_source_ids, period_days, relative_error, match_index, 1, 0, period_convention, False, None, None



        def fake_evaluate(

            _target_measurements: list[PhotometryMeasurement],

            _reference_measurements: list[PhotometryMeasurement],

            comparison_source_ids: tuple[str, ...],

            fit_config: object | None,

            y_axis_mode: str,

            period_method: str,

            period_convention: str,

        ) -> tuple[list[PhotometryMeasurement], LightCurveSeries, FitPeriodInferenceResult]:

            period_days = {

                ("gaia-1", "gaia-2"): 1.00,

            }[comparison_source_ids]

            optimized_measurements = [

                replace(

                    target_measurements[0],

                    comparison_source_ids=list(comparison_source_ids),

                    comparison_source_names=[

                        {"gaia-1": "Comp A", "gaia-2": "Comp B", "gaia-3": "Comp C"}[source_id]

                        for source_id in comparison_source_ids

                    ],

                )

            ]

            optimized_series = LightCurveSeries(

                object_name="Demo",

                source_id="vsx-1",

                source_name="Target",

                filter_name="R",

                points=[

                    LightCurvePoint(

                        observation_time=measurement_time + timedelta(minutes=point_index * 10),

                        file_path=Path(f"frame_{point_index:02d}.fits"),

                        differential_magnitude=0.25,

                        instrumental_magnitude=-9.0,

                        flux=5000.0,

                        flux_error=15.0,

                        differential_magnitude_error=0.03,

                    )

                    for point_index in range(8)

                ],

            )

            return optimized_measurements, optimized_series, FitPeriodInferenceResult(period_hours=period_days * 24.0, method="harmonic_fit")



        with (

            patch("photometry_app.ui.workers.ProcessPoolExecutor", RecordingProcessPoolExecutor),

            patch("photometry_app.ui.workers.os.cpu_count", return_value=8),

            patch("photometry_app.ui.workers._ordered_reference_candidate_ids", return_value=["gaia-1", "gaia-2", "gaia-3"]),

            patch(

                "photometry_app.ui.workers._comparison_source_groups",

                return_value=([

                    ("gaia-1", "gaia-3"),

                    ("gaia-1", "gaia-2"),

                    ("gaia-2", "gaia-3"),

                ], 3, False),

            ),

            patch("photometry_app.ui.workers._comparison_fit_task", side_effect=fake_task),

            patch("photometry_app.ui.workers._evaluate_comparison_source_group", side_effect=fake_evaluate),

        ):

            RecordingProcessPoolExecutor.created_max_workers = []

            worker.run()



        self.assertEqual(len(completed), 1)

        result = completed[0]

        self.assertEqual(result.report_token, 202)

        self.assertEqual(result.evaluated_combination_count, 3)

        self.assertFalse(result.sampled_search)

        self.assertEqual(result.execution_mode, "process")

        self.assertEqual(result.worker_count, 2)

        self.assertIsNotNone(result.best_trial)

        assert result.best_trial is not None

        self.assertEqual(result.best_trial.comparison_source_ids, ("gaia-1", "gaia-2"))

        self.assertAlmostEqual(result.best_trial.calculated_period_days or 0.0, 1.0)

        self.assertIn(2, RecordingProcessPoolExecutor.created_max_workers)

        self.assertTrue(any("Comp A, Comp C" in message for message in progress_messages))

        self.assertTrue(any("Comp A, Comp B" in message for message in progress_messages))

        self.assertTrue(any("Best comparison fit for Target [R] uses Comp A, Comp B" in message for message in progress_messages))



    def test_optimize_comparison_fit_worker_honors_configured_workers_above_previous_cap(self) -> None:

        class RecordingProcessPoolExecutor(ConcurrentProcessPoolExecutor):

            created_max_workers: list[int] = []



            def __init__(self, max_workers: int | None = None, *args: object, **kwargs: object) -> None:

                type(self).created_max_workers.append(int(max_workers or 0))



            def submit(self, fn: object, *args: object, **kwargs: object) -> Future:

                future: Future = Future()

                try:

                    future.set_result(fn(*args, **kwargs))

                except Exception as exc:

                    future.set_exception(exc)

                return future



            def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:

                return None



        measurement_time = datetime(2026, 3, 16, 1, 0, 0)

        target_measurements = [

            PhotometryMeasurement(

                source_id="vsx-1",

                source_name="Target",

                catalog="vsx",

                object_name="Demo",

                file_path=Path("frame_01.fits"),

                observation_time=measurement_time,

                filter_name="R",

                ra_deg=10.0,

                dec_deg=20.0,

                x=50.0,

                y=60.0,

                flux=5000.0,

                flux_error=15.0,

                instrumental_magnitude=-9.0,

                differential_magnitude=0.32,

                is_variable=True,

                is_reference=False,

                differential_magnitude_error=0.03,

            )

        ]

        reference_measurements = [

            PhotometryMeasurement(

                source_id=f"gaia-{index}",

                source_name=f"Comp {index}",

                catalog="gaia",

                object_name="Demo",

                file_path=Path("frame_01.fits"),

                observation_time=measurement_time,

                filter_name="R",

                ra_deg=10.0 + (index * 0.01),

                dec_deg=20.0 + (index * 0.01),

                x=70.0 + index,

                y=80.0 + index,

                flux=4000.0,

                flux_error=12.0,

                instrumental_magnitude=-8.0,

                differential_magnitude=None,

                is_variable=False,

                is_reference=True,

            )

            for index in range(30)

        ]

        completed: list[object] = []

        worker = OptimizeComparisonFitWorker(

            report_token=222,

            target_source_id="vsx-1",

            target_source_name="Target",

            filter_name="R",

            target_measurements=target_measurements,

            reference_measurements=reference_measurements,

            actual_period_days=1.0,

            preferred_comparison_source_ids=["gaia-0", "gaia-1"],

            nearby_reference_count=2,

            fit_config=None,

            y_axis_mode="differential_magnitude",

            period_method="harmonic_fit",

            period_convention="standard",

            max_parallel_workers=24,

        )

        worker.optimization_completed.connect(completed.append)



        def fake_task(

            _target_measurements: list[PhotometryMeasurement],

            _reference_measurements: list[PhotometryMeasurement],

            comparison_source_ids: tuple[str, ...],

            actual_period_days: float,

            fit_config: object | None,

            y_axis_mode: str,

            period_method: str,

            period_convention: str,

            eclipsing_binary_match_tolerance: float,

        ) -> tuple[tuple[str, ...], float | None, float | None, float | None, int, int, str, bool, float | None, float | None]:

            return comparison_source_ids, 1.0, 0.0, 100.0, 1, 0, period_convention, False, None, None



        def fake_evaluate(

            _target_measurements: list[PhotometryMeasurement],

            _reference_measurements: list[PhotometryMeasurement],

            comparison_source_ids: tuple[str, ...],

            fit_config: object | None,

            y_axis_mode: str,

            period_method: str,

            period_convention: str,

        ) -> tuple[list[PhotometryMeasurement], LightCurveSeries, FitPeriodInferenceResult]:

            return target_measurements, LightCurveSeries(object_name="Demo", source_id="vsx-1", source_name="Target", filter_name="R", points=[]), FitPeriodInferenceResult(period_hours=24.0, method="harmonic_fit")



        with (

            patch("photometry_app.ui.workers.ProcessPoolExecutor", RecordingProcessPoolExecutor),

            patch("photometry_app.ui.workers.os.cpu_count", return_value=32),

            patch("photometry_app.ui.workers._ordered_reference_candidate_ids", return_value=[f"gaia-{index}" for index in range(30)]),

            patch(

                "photometry_app.ui.workers._comparison_source_groups",

                return_value=([tuple((f"gaia-{index}", f"gaia-{index + 1}")) for index in range(30)], 30, False),

            ),

            patch("photometry_app.ui.workers._comparison_fit_task", side_effect=fake_task),

            patch("photometry_app.ui.workers._evaluate_comparison_source_group", side_effect=fake_evaluate),

        ):

            RecordingProcessPoolExecutor.created_max_workers = []

            worker.run()



        self.assertEqual(len(completed), 1)

        result = completed[0]

        self.assertEqual(result.worker_count, 24)

        self.assertIn(24, RecordingProcessPoolExecutor.created_max_workers)



    def test_optimize_comparison_fit_worker_uses_fallback_candidate_pool_when_threshold_not_met(self) -> None:

        measurement_time = datetime(2026, 3, 16, 1, 0, 0)

        target_measurements = [

            PhotometryMeasurement(

                source_id="vsx-1",

                source_name="Target",

                catalog="vsx",

                object_name="Demo",

                file_path=Path("frame_01.fits"),

                observation_time=measurement_time,

                filter_name="R",

                ra_deg=10.0,

                dec_deg=20.0,

                x=50.0,

                y=60.0,

                flux=5000.0,

                flux_error=15.0,

                instrumental_magnitude=-9.0,

                differential_magnitude=0.32,

                is_variable=True,

                is_reference=False,

                comparison_source_ids=["gaia-1", "gaia-2"],

                comparison_source_names=["Comp A", "Comp B"],

                differential_magnitude_error=0.03,

            )

        ]

        reference_measurements = [

            PhotometryMeasurement(

                source_id=source_id,

                source_name=source_name,

                catalog="gaia",

                object_name="Demo",

                file_path=Path("frame_01.fits"),

                observation_time=measurement_time,

                filter_name="R",

                ra_deg=10.0,

                dec_deg=20.0,

                x=70.0,

                y=80.0,

                flux=4000.0,

                flux_error=12.0,

                instrumental_magnitude=-8.0,

                differential_magnitude=None,

                is_variable=False,

                is_reference=True,

            )

            for source_id, source_name in (("gaia-1", "Comp A"), ("gaia-2", "Comp B"), ("gaia-3", "Comp C"))

        ]

        completed: list[object] = []

        progress_messages: list[str] = []

        worker = OptimizeComparisonFitWorker(

            report_token=303,

            target_source_id="vsx-1",

            target_source_name="Target",

            filter_name="R",

            target_measurements=target_measurements,

            reference_measurements=reference_measurements,

            actual_period_days=1.0,

            preferred_comparison_source_ids=["gaia-1", "gaia-2"],

            nearby_reference_count=2,

            fit_config=None,

            y_axis_mode="differential_magnitude",

            period_method="harmonic_fit",

            period_convention="standard",

            target_magnitude=12.0,

            reference_source_magnitudes={"gaia-1": 11.5, "gaia-2": 12.1, "gaia-3": 12.2},

            stop_match_index_threshold=95.0,

            eclipsing_binary_match_tolerance=2.0,

            fallback_candidate_pool_size=2,

            fallback_magnitude_tolerance=0.5,

        )

        worker.optimization_completed.connect(completed.append)

        worker.progress_updated.connect(progress_messages.append)



        def fake_task(

            _target_measurements: list[PhotometryMeasurement],

            _reference_measurements: list[PhotometryMeasurement],

            comparison_source_ids: tuple[str, ...],

            actual_period_days: float,

            fit_config: object | None,

            y_axis_mode: str,

            period_method: str,

            period_convention: str,

            eclipsing_binary_match_tolerance: float,

        ) -> tuple[tuple[str, ...], float | None, float | None, float | None, int, int, str, bool, float | None, float | None]:

            results = {

                ("gaia-1", "gaia-2"): (1.20, 80.0, "standard"),

                ("gaia-2", "gaia-3"): (1.01, 99.0, "eclipsing_binary"),

            }

            period_days, match_index, resolved_convention = results[comparison_source_ids]

            relative_error = abs(period_days - actual_period_days) / actual_period_days

            return comparison_source_ids, period_days, relative_error, match_index, 1, 0, resolved_convention, resolved_convention == "eclipsing_binary", period_days, match_index



        def fake_evaluate(

            _target_measurements: list[PhotometryMeasurement],

            _reference_measurements: list[PhotometryMeasurement],

            comparison_source_ids: tuple[str, ...],

            fit_config: object | None,

            y_axis_mode: str,

            period_method: str,

            period_convention: str,

        ) -> tuple[list[PhotometryMeasurement], LightCurveSeries, FitPeriodInferenceResult]:

            self.assertEqual(comparison_source_ids, ("gaia-2", "gaia-3"))

            self.assertEqual(period_convention, "eclipsing_binary")

            optimized_measurements = [

                replace(

                    target_measurements[0],

                    comparison_source_ids=list(comparison_source_ids),

                    comparison_source_names=["Comp B", "Comp C"],

                )

            ]

            optimized_series = LightCurveSeries(

                object_name="Demo",

                source_id="vsx-1",

                source_name="Target",

                filter_name="R",

                points=[],

            )

            return optimized_measurements, optimized_series, FitPeriodInferenceResult(period_hours=24.24, method="bls", eclipse_duration_hours=2.0)



        with (

            patch("photometry_app.ui.workers._ordered_reference_candidate_ids", return_value=["gaia-1", "gaia-2", "gaia-3"]),

            patch(

                "photometry_app.ui.workers._comparison_source_groups",

                side_effect=[

                    ([("gaia-1", "gaia-2")], 1, False),

                    ([("gaia-2", "gaia-3")], 1, False),

                ],

            ),

            patch("photometry_app.ui.workers._magnitude_sorted_reference_candidate_ids", return_value=["gaia-2", "gaia-3"]),

            patch("photometry_app.ui.workers._comparison_fit_task", side_effect=fake_task),

            patch("photometry_app.ui.workers._evaluate_comparison_source_group", side_effect=fake_evaluate),

        ):

            worker.run()



        self.assertEqual(len(completed), 1)

        result = completed[0]

        self.assertEqual(result.total_combination_count, 2)

        self.assertEqual(result.evaluated_combination_count, 2)

        assert result.best_trial is not None

        self.assertEqual(result.best_trial.comparison_source_ids, ("gaia-2", "gaia-3"))

        self.assertEqual(result.best_trial.period_convention, "eclipsing_binary")

        self.assertTrue(any("fallback magnitude-matched" in message for message in progress_messages))



    def test_optimize_comparison_fit_worker_uses_calibrated_target_magnitude_for_fallback(self) -> None:

        measurement_time = datetime(2026, 3, 16, 1, 0, 0)

        target_measurements = [

            PhotometryMeasurement(

                source_id="vsx-1",

                source_name="Target",

                catalog="vsx",

                object_name="Demo",

                file_path=Path("frame_01.fits"),

                observation_time=measurement_time,

                filter_name="R",

                ra_deg=10.0,

                dec_deg=20.0,

                x=50.0,

                y=60.0,

                flux=5000.0,

                flux_error=15.0,

                instrumental_magnitude=-9.0,

                differential_magnitude=0.32,

                calibrated_magnitude=12.34,

                is_variable=True,

                is_reference=False,

                comparison_source_ids=["gaia-1", "gaia-2"],

                comparison_source_names=["Comp A", "Comp B"],

                differential_magnitude_error=0.03,

            )

        ]

        reference_measurements = [

            PhotometryMeasurement(

                source_id=source_id,

                source_name=source_name,

                catalog="gaia",

                object_name="Demo",

                file_path=Path("frame_01.fits"),

                observation_time=measurement_time,

                filter_name="R",

                ra_deg=10.0,

                dec_deg=20.0,

                x=70.0,

                y=80.0,

                flux=4000.0,

                flux_error=12.0,

                instrumental_magnitude=-8.0,

                differential_magnitude=None,

                is_variable=False,

                is_reference=True,

            )

            for source_id, source_name in (("gaia-1", "Comp A"), ("gaia-2", "Comp B"), ("gaia-3", "Comp C"))

        ]

        completed: list[object] = []

        progress_messages: list[str] = []

        worker = OptimizeComparisonFitWorker(

            report_token=304,

            target_source_id="vsx-1",

            target_source_name="Target",

            filter_name="R",

            target_measurements=target_measurements,

            reference_measurements=reference_measurements,

            actual_period_days=1.0,

            preferred_comparison_source_ids=["gaia-1", "gaia-2"],

            nearby_reference_count=2,

            fit_config=None,

            y_axis_mode="differential_magnitude",

            period_method="harmonic_fit",

            period_convention="standard",

            target_magnitude=None,

            reference_source_magnitudes={"gaia-1": 11.5, "gaia-2": 12.1, "gaia-3": 12.2},

            stop_match_index_threshold=95.0,

            eclipsing_binary_match_tolerance=2.0,

            fallback_candidate_pool_size=2,

            fallback_magnitude_tolerance=0.5,

        )

        worker.optimization_completed.connect(completed.append)

        worker.progress_updated.connect(progress_messages.append)



        def fake_task(

            _target_measurements: list[PhotometryMeasurement],

            _reference_measurements: list[PhotometryMeasurement],

            comparison_source_ids: tuple[str, ...],

            actual_period_days: float,

            fit_config: object | None,

            y_axis_mode: str,

            period_method: str,

            period_convention: str,

            eclipsing_binary_match_tolerance: float,

        ) -> tuple[tuple[str, ...], float | None, float | None, float | None, int, int, str, bool, float | None, float | None]:

            results = {

                ("gaia-1", "gaia-2"): (1.20, 80.0, "standard"),

                ("gaia-2", "gaia-3"): (1.01, 99.0, "standard"),

            }

            period_days, match_index, resolved_convention = results[comparison_source_ids]

            relative_error = abs(period_days - actual_period_days) / actual_period_days

            return comparison_source_ids, period_days, relative_error, match_index, 1, 0, resolved_convention, False, None, None



        def fake_evaluate(

            _target_measurements: list[PhotometryMeasurement],

            _reference_measurements: list[PhotometryMeasurement],

            comparison_source_ids: tuple[str, ...],

            fit_config: object | None,

            y_axis_mode: str,

            period_method: str,

            period_convention: str,

        ) -> tuple[list[PhotometryMeasurement], LightCurveSeries, FitPeriodInferenceResult]:

            self.assertEqual(comparison_source_ids, ("gaia-2", "gaia-3"))

            return target_measurements, LightCurveSeries(object_name="Demo", source_id="vsx-1", source_name="Target", filter_name="R", points=[]), FitPeriodInferenceResult(period_hours=24.24, method="harmonic_fit")



        with (

            patch("photometry_app.ui.workers._ordered_reference_candidate_ids", return_value=["gaia-1", "gaia-2", "gaia-3"]),

            patch(

                "photometry_app.ui.workers._comparison_source_groups",

                side_effect=[

                    ([("gaia-1", "gaia-2")], 1, False),

                    ([("gaia-2", "gaia-3")], 1, False),

                ],

            ),

            patch("photometry_app.ui.workers._comparison_fit_task", side_effect=fake_task),

            patch("photometry_app.ui.workers._evaluate_comparison_source_group", side_effect=fake_evaluate),

        ):

            worker.run()



        self.assertEqual(len(completed), 1)

        result = completed[0]

        self.assertEqual(result.total_combination_count, 2)

        self.assertEqual(result.evaluated_combination_count, 2)

        assert result.best_trial is not None

        self.assertEqual(result.best_trial.comparison_source_ids, ("gaia-2", "gaia-3"))

        self.assertTrue(any("retrying with up to 2 magnitude-matched reference star(s)" in message for message in progress_messages))



    def test_optimize_comparison_fit_worker_reports_eclipsing_retry_attempt_when_standard_stays_selected(self) -> None:

        measurement_time = datetime(2026, 3, 16, 1, 0, 0)

        target_measurements = [

            PhotometryMeasurement(

                source_id="vsx-1",

                source_name="Target",

                catalog="vsx",

                object_name="Demo",

                file_path=Path("frame_01.fits"),

                observation_time=measurement_time,

                filter_name="R",

                ra_deg=10.0,

                dec_deg=20.0,

                x=50.0,

                y=60.0,

                flux=5000.0,

                flux_error=15.0,

                instrumental_magnitude=-9.0,

                differential_magnitude=0.32,

                is_variable=True,

                is_reference=False,

                differential_magnitude_error=0.03,

            )

        ]

        reference_measurements = [

            PhotometryMeasurement(

                source_id="gaia-1",

                source_name="Comp A",

                catalog="gaia",

                object_name="Demo",

                file_path=Path("frame_01.fits"),

                observation_time=measurement_time,

                filter_name="R",

                ra_deg=10.1,

                dec_deg=20.1,

                x=70.0,

                y=80.0,

                flux=4000.0,

                flux_error=12.0,

                instrumental_magnitude=-8.0,

                differential_magnitude=None,

                is_variable=False,

                is_reference=True,

            ),

            PhotometryMeasurement(

                source_id="gaia-2",

                source_name="Comp B",

                catalog="gaia",

                object_name="Demo",

                file_path=Path("frame_01.fits"),

                observation_time=measurement_time,

                filter_name="R",

                ra_deg=10.2,

                dec_deg=20.2,

                x=75.0,

                y=85.0,

                flux=4100.0,

                flux_error=12.0,

                instrumental_magnitude=-8.1,

                differential_magnitude=None,

                is_variable=False,

                is_reference=True,

            ),

        ]

        progress_messages: list[str] = []

        worker = OptimizeComparisonFitWorker(

            report_token=404,

            target_source_id="vsx-1",

            target_source_name="Target",

            filter_name="R",

            target_measurements=target_measurements,

            reference_measurements=reference_measurements,

            actual_period_days=1.0,

            preferred_comparison_source_ids=["gaia-1", "gaia-2"],

            nearby_reference_count=2,

            fit_config=None,

            y_axis_mode="differential_magnitude",

            period_method="harmonic_fit",

            period_convention="standard",

            eclipsing_binary_match_tolerance=2.0,

        )

        worker.progress_updated.connect(progress_messages.append)



        def fake_task(

            _target_measurements: list[PhotometryMeasurement],

            _reference_measurements: list[PhotometryMeasurement],

            comparison_source_ids: tuple[str, ...],

            actual_period_days: float,

            fit_config: object | None,

            y_axis_mode: str,

            period_method: str,

            period_convention: str,

            eclipsing_binary_match_tolerance: float,

        ) -> tuple[tuple[str, ...], float | None, float | None, float | None, int, int, str, bool, float | None, float | None]:

            return comparison_source_ids, 0.505, 0.495, 50.5, 1, 0, "standard", True, 0.490, 49.0



        def fake_evaluate(

            _target_measurements: list[PhotometryMeasurement],

            _reference_measurements: list[PhotometryMeasurement],

            comparison_source_ids: tuple[str, ...],

            fit_config: object | None,

            y_axis_mode: str,

            period_method: str,

            period_convention: str,

        ) -> tuple[list[PhotometryMeasurement], LightCurveSeries, FitPeriodInferenceResult]:

            return target_measurements, LightCurveSeries(object_name="Demo", source_id="vsx-1", source_name="Target", filter_name="R", points=[]), FitPeriodInferenceResult(period_hours=12.12, method="harmonic_fit")



        with (

            patch("photometry_app.ui.workers._ordered_reference_candidate_ids", return_value=["gaia-1", "gaia-2"]),

            patch("photometry_app.ui.workers._comparison_source_groups", return_value=([("gaia-1", "gaia-2")], 1, False)),

            patch("photometry_app.ui.workers._comparison_fit_task", side_effect=fake_task),

            patch("photometry_app.ui.workers._evaluate_comparison_source_group", side_effect=fake_evaluate),

        ):

            worker.run()



        self.assertTrue(any("eclipsing-binary retry tested period=0.4900 d, match=49.00" in message for message in progress_messages))


class SkyExplorerSurveyWorkerTest(unittest.TestCase):
    def test_worker_injects_progress_callback_and_returns_result(self) -> None:
        @dataclass(frozen=True)
        class Request:
            value: int
            progress_callback: object | None = None

        progress_messages: list[str] = []
        completed_results: list[object] = []

        def fetcher(request: Request) -> object:
            self.assertIsNotNone(request.progress_callback)
            assert callable(request.progress_callback)
            request.progress_callback("Downloading survey")
            return {"value": request.value}

        worker = SkyExplorerSurveyWorker(
            request=Request(value=7),
            fetcher=fetcher,
        )
        worker.progress_updated.connect(progress_messages.append)
        worker.survey_completed.connect(completed_results.append)

        worker.run()

        self.assertEqual(progress_messages, ["Downloading survey"])
        self.assertEqual(completed_results, [{"value": 7}])


class UpdateWorkerTest(unittest.TestCase):
    def test_check_worker_emits_progress_and_result(self) -> None:
        expected = SimpleNamespace(update_available=True)
        progress_messages: list[str] = []
        completed_results: list[object] = []
        worker = UpdateCheckWorker()
        worker.progress_updated.connect(progress_messages.append)
        worker.update_check_completed.connect(completed_results.append)

        with patch("photometry_app.core.app_updates.check_for_updates", return_value=expected):
            worker.run()

        self.assertEqual(completed_results, [expected])
        self.assertTrue(any("Checking GitHub" in message for message in progress_messages))

    def test_download_worker_forwards_progress_and_result(self) -> None:
        update = SimpleNamespace(version="0.1.1-alpha.2")
        installer_path = Path("update.exe")
        progress_values: list[tuple[int, int]] = []
        completed_paths: list[Path] = []
        worker = UpdateDownloadWorker(update)
        worker.progress_updated.connect(lambda downloaded, total: progress_values.append((downloaded, total)))
        worker.update_download_completed.connect(completed_paths.append)

        def fake_download(_update, *, progress_callback, cancellation_requested):
            self.assertFalse(cancellation_requested())
            progress_callback(50, 100)
            return installer_path

        with patch("photometry_app.core.app_updates.download_update_installer", side_effect=fake_download):
            worker.run()

        self.assertEqual(progress_values, [(50, 100)])
        self.assertEqual(completed_paths, [installer_path])