from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from photometry_app.core.models import LightCurvePoint, LightCurveSeries
from photometry_app.core.oc_extrema import EXTREMUM_MAXIMUM, EXTREMUM_MINIMUM, OcSession, OcStarLog

from photometry_app.ui.oc_dialog import (
    AidDownloadDialog,
    OcDialog,
    aid_dialog_session,
    remember_aid_dialog_session,
)


class OcDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_mark_extrema_appends_max_and_min_to_the_log(self) -> None:
        origin = datetime(2026, 3, 16, 1, 0, 0)
        points = [
            LightCurvePoint(
                observation_time=origin + timedelta(minutes=index * 8),
                file_path=Path(f"frame_{index:02d}.fits"),
                differential_magnitude=10.8 + (0.4 * ((index - 8) / 12.0) ** 2),
                instrumental_magnitude=None,
                flux=None,
                flux_error=None,
                standard_magnitude=10.8 + (0.4 * ((index - 8) / 12.0) ** 2),
                standard_magnitude_error=0.01,
                differential_magnitude_error=0.01,
            )
            for index in range(20)
        ]
        series = LightCurveSeries("Night1", "vsx-1", "DY Her", "V", points)
        dialog = OcDialog(
            log=OcStarLog(star_key="vsx-1", star_name="DY Her", source_id="vsx-1"),
            sessions=[OcSession(session_name="Night1", series=series)],
            y_axis_mode="standard_magnitude",
        )
        dialog._session_list.setCurrentRow(0)
        dialog._handle_mark_extrema()
        kinds = {record.kind for record in dialog.current_log().records}
        self.assertEqual(kinds, {EXTREMUM_MAXIMUM, "minimum"})
        self.assertGreaterEqual(len(dialog.current_log().records), 2)
        dialog.close()

    def test_mark_extrema_keeps_multiple_cycles_in_one_session(self) -> None:
        origin = datetime(2026, 2, 22, 1, 0, 0)
        points = []
        for index in range(90):
            elapsed = index * 8.0
            magnitude = 11.35 + (0.28 * ((1.0 + __import__("math").cos(2.0 * 3.1415926535 * elapsed / 124.0)) * 0.5))
            points.append(
                LightCurvePoint(
                    observation_time=origin + timedelta(minutes=elapsed),
                    file_path=Path(f"frame_{index:02d}.fits"),
                    differential_magnitude=magnitude,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    standard_magnitude=magnitude,
                    standard_magnitude_error=0.01,
                    differential_magnitude_error=0.01,
                )
            )
        series = LightCurveSeries("Night1", "vsx-1", "AE UMa", "B", points)
        dialog = OcDialog(
            log=OcStarLog(star_key="vsx-1", star_name="AE UMa", source_id="vsx-1", period_days=124.0 / 1440.0),
            sessions=[OcSession(session_name="Night1", series=series)],
            y_axis_mode="standard_magnitude",
        )
        dialog._session_list.setCurrentRow(0)
        dialog._handle_mark_extrema()
        maxima = [record for record in dialog.current_log().records if record.kind == EXTREMUM_MAXIMUM]
        minima = [record for record in dialog.current_log().records if record.kind == EXTREMUM_MINIMUM]
        self.assertGreaterEqual(len(maxima), 3)
        self.assertGreaterEqual(len(minima), 3)
        dialog.close()

    def test_star_name_is_editable_and_prefills_aid_query(self) -> None:
        series = LightCurveSeries("Night1", "", "Target", "V", [])
        dialog = OcDialog(
            log=OcStarLog(star_key="target", star_name="Target"),
            sessions=[OcSession(session_name="Night1", series=series)],
        )
        self.assertEqual(dialog._star_name_edit.text(), "Target")
        dialog._star_name_edit.setText("DY Her")
        dialog._handle_star_name_changed()
        self.assertEqual(dialog.current_log().star_name, "DY Her")
        self.assertEqual(dialog.current_log().star_key, "target")
        self.assertTrue(dialog._pull_aavso_button.isEnabled())
        self.assertTrue(hasattr(dialog, "_oc_plot"))
        self.assertEqual(dialog._log_table.columnCount(), 7)
        query_dialog = AidDownloadDialog(star_name="DY Her", observer_code="KAY", has_api_token=False)
        query_dialog._start_jd_spin.setValue(2460018.0)
        query_dialog._end_jd_spin.setValue(2460019.0)
        query = query_dialog.current_query(source_id="vsx-1", api_token="")
        self.assertEqual(query.star_name, "DY Her")
        self.assertEqual(query.observer, "KAY")
        self.assertEqual(query.band, "2")
        self.assertEqual(query.obstype, "CCD")
        self.assertEqual(query.mtype, "STD")
        self.assertTrue(query.exclude_fainterthan)
        self.assertTrue(query.group_by_night)
        query_dialog.close()
        dialog.close()

    def test_aid_dialog_restores_session_fields_but_keeps_current_star(self) -> None:
        remember_aid_dialog_session(None)
        first = AidDownloadDialog(star_name="AE UMa")
        first._start_jd_spin.setValue(2460126.0)
        first._end_jd_spin.setValue(2461126.0)
        first._band_selector.setCurrentIndex(first._band_selector.findData(""))
        first._obstype_selector.setCurrentIndex(first._obstype_selector.findData(""))
        first._observer_edit.setText("OKDA")
        first._exclude_fainterthan.setChecked(False)
        first._skip_discrepant.setChecked(False)
        first._group_by_night.setChecked(False)
        remember_aid_dialog_session(first.session_state())
        first.close()
        restored = AidDownloadDialog(
            star_name="DY Her",
            session=aid_dialog_session(),
            include_star_from_session=False,
        )
        self.assertEqual(restored._star_name_edit.text(), "DY Her")
        self.assertAlmostEqual(restored._start_jd_spin.value(), 2460126.0)
        self.assertAlmostEqual(restored._end_jd_spin.value(), 2461126.0)
        self.assertEqual(restored._band_selector.currentData(), "")
        self.assertEqual(restored._obstype_selector.currentData(), "")
        self.assertEqual(restored._observer_edit.text(), "OKDA")
        self.assertFalse(restored._exclude_fainterthan.isChecked())
        restored.close()
        remember_aid_dialog_session(None)

    def test_pull_aavso_no_on_full_history_reopens_dialog(self) -> None:
        remember_aid_dialog_session(None)
        series = LightCurveSeries("Night1", "vsx-1", "AE UMa", "V", [])
        dialog = OcDialog(
            log=OcStarLog(star_key="vsx-1", star_name="AE UMa", source_id="vsx-1"),
            sessions=[OcSession(session_name="Night1", series=series)],
        )
        calls = {"n": 0}

        def fake_exec(self):
            calls["n"] += 1
            return QDialog.DialogCode.Accepted if calls["n"] == 1 else QDialog.DialogCode.Rejected

        with (
            patch.object(AidDownloadDialog, "exec", fake_exec),
            patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as question,
            patch.object(dialog, "_start_aid_download") as start_download,
        ):
            dialog._handle_pull_aavso_clicked()
        self.assertEqual(calls["n"], 2)
        question.assert_called_once()
        start_download.assert_not_called()
        session = aid_dialog_session()
        self.assertIsNotNone(session)
        dialog.close()
        remember_aid_dialog_session(None)
