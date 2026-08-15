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


if __name__ == "__main__":
    unittest.main()
