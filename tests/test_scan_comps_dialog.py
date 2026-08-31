from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QCheckBox, QDialog

from photometry_app.core.models import LightCurvePoint, LightCurveSeries, PhotometryMeasurement
from photometry_app.core.scan_comps import ScanCompReferenceInput
from photometry_app.ui.scan_comps_dialog import ScanCompsDialog, ScanCompsDialogResult
from photometry_app.ui.workers import ScanCompSetEvaluation


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class ScanCompsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _ensure_app()

    def _target_and_refs(self) -> tuple[list[PhotometryMeasurement], list[PhotometryMeasurement], list[ScanCompReferenceInput]]:
        t0 = datetime(2026, 3, 16, 1, 0, 0)
        targets = [
            PhotometryMeasurement(
                source_id="vsx-1",
                source_name="Target",
                catalog="vsx",
                object_name="Demo",
                file_path=Path("frame_01.fits"),
                observation_time=t0 + timedelta(minutes=index * 10),
                filter_name="V",
                ra_deg=10.0,
                dec_deg=20.0,
                x=50.0,
                y=60.0,
                flux=5000.0,
                flux_error=15.0,
                instrumental_magnitude=-9.0,
                differential_magnitude=0.3,
                is_variable=True,
                is_reference=False,
            )
            for index in range(3)
        ]
        refs = []
        inputs = []
        for index, source_id in enumerate(("gaia-1", "gaia-2", "gaia-3"), start=1):
            refs.append(
                PhotometryMeasurement(
                    source_id=source_id,
                    source_name=f"Comp {index}",
                    catalog="gaia",
                    object_name="Demo",
                    file_path=Path("frame_01.fits"),
                    observation_time=t0,
                    filter_name="V",
                    ra_deg=10.0 + 0.01 * index,
                    dec_deg=20.0 + 0.01 * index,
                    x=70.0,
                    y=80.0,
                    flux=6000.0,
                    flux_error=12.0,
                    instrumental_magnitude=-8.0,
                    differential_magnitude=None,
                    is_variable=False,
                    is_reference=True,
                    catalog_magnitude=12.0 + 0.05 * index,
                )
            )
            inputs.append(
                ScanCompReferenceInput(
                    source_id=source_id,
                    source_name=f"Comp {index}",
                    ra_deg=10.0 + 0.01 * index,
                    dec_deg=20.0 + 0.01 * index,
                    magnitude=12.0 + 0.05 * index,
                    bp_rp=0.8 + 0.02 * index,
                )
            )
        return targets, refs, inputs

    def test_favourite_checkbox_toggled_updates_favourite_ids(self) -> None:
        targets, refs, inputs = self._target_and_refs()
        dialog = ScanCompsDialog(
            report_token=1,
            target_source_id="vsx-1",
            target_source_name="Target",
            filter_name="V",
            target_ra_deg=10.0,
            target_dec_deg=20.0,
            target_magnitude=12.1,
            target_bp_rp=0.85,
            target_measurements=targets,
            reference_measurements=refs,
            reference_inputs=inputs,
        )
        dialog._build_candidates()
        self.assertGreaterEqual(dialog._pool_table.rowCount(), 2)
        first = dialog._pool_table.cellWidget(0, 0)
        second = dialog._pool_table.cellWidget(1, 0)
        self.assertIsInstance(first, QCheckBox)
        self.assertIsInstance(second, QCheckBox)
        first.setChecked(True)
        second.setChecked(True)
        self.assertEqual(len(dialog._favourite_ids), 2)
        first.setChecked(False)
        self.assertEqual(len(dialog._favourite_ids), 1)
        dialog.close()

    def test_build_pool_and_favourite_combinations(self) -> None:
        targets, refs, inputs = self._target_and_refs()
        dialog = ScanCompsDialog(
            report_token=1,
            target_source_id="vsx-1",
            target_source_name="Target",
            filter_name="V",
            target_ra_deg=10.0,
            target_dec_deg=20.0,
            target_magnitude=12.1,
            target_bp_rp=0.85,
            target_measurements=targets,
            reference_measurements=refs,
            reference_inputs=inputs,
        )
        dialog._comps_per_set_input.setValue(2)
        dialog._build_candidates()
        self.assertEqual(dialog._stack.currentIndex(), 1)
        self.assertGreaterEqual(len(dialog._candidates), 2)
        for candidate in dialog._candidates[:2]:
            dialog._favourite_ids.add(candidate.source_id)
        with patch.object(dialog, "_start_worker"):
            dialog._continue_to_combinations()
        self.assertEqual(dialog._stack.currentIndex(), 2)
        self.assertEqual(len(dialog._combo_order), 1)
        dialog.close()

    def test_use_selected_returns_result(self) -> None:
        targets, refs, inputs = self._target_and_refs()
        dialog = ScanCompsDialog(
            report_token=1,
            target_source_id="vsx-1",
            target_source_name="Target",
            filter_name="V",
            target_ra_deg=10.0,
            target_dec_deg=20.0,
            target_magnitude=12.1,
            target_bp_rp=0.85,
            target_measurements=targets,
            reference_measurements=refs,
            reference_inputs=inputs,
        )
        group = ("gaia-1", "gaia-2")
        series = LightCurveSeries(
            object_name="Demo",
            source_id="vsx-1",
            source_name="Target",
            filter_name="V",
            points=[
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 1, 0, 0),
                    file_path=Path("frame_01.fits"),
                    differential_magnitude=0.25,
                    instrumental_magnitude=-9.0,
                    flux=5000.0,
                    flux_error=15.0,
                )
            ],
        )
        dialog._combo_order = [group]
        dialog._current_combo_index = 0
        dialog._evaluations[group] = ScanCompSetEvaluation(
            comparison_source_ids=group,
            comparison_source_names=("Comp 1", "Comp 2"),
            target_measurements=targets,
            series=series,
            valid_point_count=1,
            excluded_point_count=0,
        )
        dialog._use_selected()
        self.assertEqual(dialog.result(), int(QDialog.DialogCode.Accepted))
        result = dialog.selected_result()
        self.assertIsInstance(result, ScanCompsDialogResult)
        assert result is not None
        self.assertEqual(result.comparison_source_ids, group)
        dialog.close()

    def test_favourite_select_all_and_ready_badge(self) -> None:
        targets, refs, inputs = self._target_and_refs()
        dialog = ScanCompsDialog(
            report_token=1,
            target_source_id="vsx-1",
            target_source_name="Target",
            filter_name="V",
            target_ra_deg=10.0,
            target_dec_deg=20.0,
            target_magnitude=12.1,
            target_bp_rp=0.85,
            target_measurements=targets,
            reference_measurements=refs,
            reference_inputs=inputs,
        )
        dialog._comps_per_set_input.setValue(2)
        dialog._build_candidates()
        dialog._favourite_all()
        self.assertEqual(len(dialog._favourite_ids), len(dialog._candidates))
        self.assertEqual(dialog._favourite_count_label.property("readyState"), "ready")
        dialog._clear_favourites()
        self.assertEqual(dialog._favourite_count_label.property("readyState"), "pending")
        dialog.close()

    def test_step_badges_navigate_when_ready(self) -> None:
        targets, refs, inputs = self._target_and_refs()
        dialog = ScanCompsDialog(
            report_token=1,
            target_source_id="vsx-1",
            target_source_name="Target",
            filter_name="V",
            target_ra_deg=10.0,
            target_dec_deg=20.0,
            target_magnitude=12.1,
            target_bp_rp=0.85,
            target_measurements=targets,
            reference_measurements=refs,
            reference_inputs=inputs,
        )
        dialog._comps_per_set_input.setValue(2)
        dialog._build_candidates()
        for candidate in dialog._candidates[:2]:
            dialog._favourite_ids.add(candidate.source_id)
        with patch.object(dialog, "_start_worker"):
            dialog._request_step(2)
        self.assertEqual(dialog._stack.currentIndex(), 2)
        dialog._request_step(1)
        self.assertEqual(dialog._stack.currentIndex(), 1)
        dialog._request_step(0)
        self.assertEqual(dialog._stack.currentIndex(), 0)
        dialog.close()

    def test_combination_star_toggle_and_overview_mode(self) -> None:
        from PySide6.QtWidgets import QToolButton

        targets, refs, inputs = self._target_and_refs()
        dialog = ScanCompsDialog(
            report_token=1,
            target_source_id="vsx-1",
            target_source_name="Target",
            filter_name="V",
            target_ra_deg=10.0,
            target_dec_deg=20.0,
            target_magnitude=12.1,
            target_bp_rp=0.85,
            target_measurements=targets,
            reference_measurements=refs,
            reference_inputs=inputs,
        )
        group = ("gaia-1", "gaia-2")
        series = LightCurveSeries(
            object_name="Demo",
            source_id="vsx-1",
            source_name="Target",
            filter_name="V",
            points=[
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 1, 0, 0),
                    file_path=Path("frame_01.fits"),
                    differential_magnitude=0.25,
                    instrumental_magnitude=-9.0,
                    flux=5000.0,
                    flux_error=15.0,
                )
            ],
        )
        dialog._combo_order = [group]
        dialog._current_combo_index = 0
        dialog._evaluations[group] = ScanCompSetEvaluation(
            comparison_source_ids=group,
            comparison_source_names=("Comp 1", "Comp 2"),
            target_measurements=targets,
            series=series,
            valid_point_count=1,
            excluded_point_count=0,
        )
        dialog._populate_combo_list()
        dialog._toggle_star_group(group)
        self.assertIn(group, dialog._starred_set_keys)
        item = dialog._combo_list.item(0)
        row = dialog._combo_list.itemWidget(item)
        self.assertIsNotNone(row)
        assert row is not None
        star = row.findChild(QToolButton, "scanCompsStarButton")
        self.assertIsNotNone(star)
        assert star is not None
        self.assertEqual(star.text(), "★")
        with patch.object(dialog._plot, "plot_overview") as plot_overview:
            dialog._preview_mode_combo.setCurrentIndex(
                dialog._preview_mode_combo.findData("overview")
            )
            plot_overview.assert_called()
        dialog.close()

    def test_combinations_page_widens_window(self) -> None:
        targets, refs, inputs = self._target_and_refs()
        dialog = ScanCompsDialog(
            report_token=1,
            target_source_id="vsx-1",
            target_source_name="Target",
            filter_name="V",
            target_ra_deg=10.0,
            target_dec_deg=20.0,
            target_magnitude=12.1,
            target_bp_rp=0.85,
            target_measurements=targets,
            reference_measurements=refs,
            reference_inputs=inputs,
        )
        dialog.resize(1000, 700)
        dialog._stack.setCurrentIndex(2)
        self.assertGreaterEqual(dialog.width(), dialog._combinations_window_size[0])
        self.assertGreaterEqual(dialog.height(), dialog._combinations_window_size[1])
        dialog.close()


class ScanCompsWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _ensure_app()

    def test_worker_evaluates_groups_in_parallel_with_filtered_refs(self) -> None:
        from concurrent.futures import Future, ThreadPoolExecutor as ConcurrentThreadPoolExecutor
        from datetime import datetime
        from pathlib import Path

        from photometry_app.core.models import LightCurvePoint, LightCurveSeries, PhotometryMeasurement
        from photometry_app.ui.workers import ScanCompsBatchResult, ScanCompsWorker, _ComparisonGroupSeriesDiagnostics

        class RecordingThreadPoolExecutor(ConcurrentThreadPoolExecutor):
            created_max_workers: list[int] = []
            submitted_ref_counts: list[int] = []

            def __init__(self, max_workers: int | None = None, *args: object, **kwargs: object) -> None:
                type(self).created_max_workers.append(int(max_workers or 0))

            def submit(self, fn: object, *args: object, **kwargs: object) -> Future:
                # args: targets, selected_refs, group, names, y_axis_mode
                selected_refs = args[1]
                type(self).submitted_ref_counts.append(len(selected_refs))
                future: Future = Future()
                try:
                    future.set_result(fn(*args, **kwargs))
                except Exception as exc:
                    future.set_exception(exc)
                return future

            def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
                return None

        t0 = datetime(2026, 3, 16, 1, 0, 0)
        targets = [
            PhotometryMeasurement(
                source_id="vsx-1",
                source_name="Target",
                catalog="vsx",
                object_name="Demo",
                file_path=Path("frame_01.fits"),
                observation_time=t0,
                filter_name="V",
                ra_deg=10.0,
                dec_deg=20.0,
                x=50.0,
                y=60.0,
                flux=5000.0,
                flux_error=15.0,
                instrumental_magnitude=-9.0,
                differential_magnitude=0.3,
                is_variable=True,
                is_reference=False,
            )
        ]
        refs = [
            PhotometryMeasurement(
                source_id=f"gaia-{index}",
                source_name=f"Comp {index}",
                catalog="gaia",
                object_name="Demo",
                file_path=Path("frame_01.fits"),
                observation_time=t0,
                filter_name="V",
                ra_deg=10.0 + (0.01 * index),
                dec_deg=20.0,
                x=50.0,
                y=60.0,
                flux=4000.0,
                flux_error=12.0,
                instrumental_magnitude=-8.5,
                differential_magnitude=None,
                is_variable=False,
                is_reference=True,
            )
            for index in range(1, 6)
        ]
        groups = [("gaia-1", "gaia-2"), ("gaia-3", "gaia-4")]
        ready: list[object] = []
        completed: list[object] = []

        def evaluate_side_effect(target_measurements, reference_measurements, comparison_group, **kwargs):
            series = LightCurveSeries(
                object_name="Demo",
                source_id="vsx-1",
                source_name="Target",
                filter_name="V",
                points=[
                    LightCurvePoint(
                        observation_time=t0,
                        file_path=Path("frame_01.fits"),
                        differential_magnitude=0.2,
                        instrumental_magnitude=-9.0,
                        flux=5000.0,
                        flux_error=15.0,
                    )
                ],
            )
            diagnostics = _ComparisonGroupSeriesDiagnostics(
                comparison_source_ids=tuple(comparison_group),
                comparison_source_names=tuple(comparison_group),
                updated_target_row_count=1,
                usable_target_row_count=1,
                differential_value_count=1,
                excluded_target_row_count=0,
            )
            return list(target_measurements), series, None, diagnostics

        worker = ScanCompsWorker(
            report_token=7,
            target_source_id="vsx-1",
            target_source_name="Target",
            filter_name="V",
            target_measurements=targets,
            reference_measurements=refs,
            comparison_groups=groups,
            max_parallel_workers=2,
        )
        worker.set_ready.connect(ready.append)
        worker.batch_completed.connect(completed.append)

        with (
            patch("photometry_app.ui.workers.ThreadPoolExecutor", RecordingThreadPoolExecutor),
            patch(
                "photometry_app.ui.workers._evaluate_comparison_source_group_core",
                side_effect=evaluate_side_effect,
            ),
        ):
            RecordingThreadPoolExecutor.created_max_workers = []
            RecordingThreadPoolExecutor.submitted_ref_counts = []
            worker.start()
            self.assertTrue(worker.wait(5000))

        self.app.processEvents()
        self.assertEqual(RecordingThreadPoolExecutor.created_max_workers, [2])
        self.assertEqual(RecordingThreadPoolExecutor.submitted_ref_counts, [2, 2])
        self.assertEqual(len(ready), 2)
        self.assertEqual(len(completed), 1)
        batch = completed[0]
        self.assertIsInstance(batch, ScanCompsBatchResult)
        assert isinstance(batch, ScanCompsBatchResult)
        self.assertEqual(batch.evaluated_combination_count, 2)
        self.assertFalse(batch.cancelled)


if __name__ == "__main__":
    unittest.main()
