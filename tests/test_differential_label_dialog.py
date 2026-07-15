from __future__ import annotations

import os
import unittest
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from photometry_app.core.models import CatalogStar, LightCurvePoint, LightCurveSeries
from photometry_app.ui.differential_label_dialog import DifferentialQuickLabelDialog


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class DifferentialQuickLabelDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_dialog_saves_label_and_advances_to_next_source(self) -> None:
        entries = (
            CatalogStar(catalog="vsx", source_id="vsx-1", name="Alpha", ra_deg=10.0, dec_deg=20.0, magnitude=12.3, is_variable=True),
            CatalogStar(catalog="gaia-dr3", source_id="gaia-2", name="Beta", ra_deg=11.0, dec_deg=21.0, magnitude=13.1, is_variable=False),
        )
        series_map = {
            entry.source_id: LightCurveSeries(
                object_name="Demo",
                source_id=entry.source_id,
                source_name=entry.name,
                filter_name="V",
                points=[
                    LightCurvePoint(
                        observation_time=datetime(2026, 5, 1, 3, index, 0),
                        file_path=Path(f"frame_{entry.source_id}_{index}.fits"),
                        differential_magnitude=12.0 + (index * 0.05),
                        instrumental_magnitude=-9.0,
                        flux=5000.0,
                        flux_error=20.0,
                    )
                    for index in range(3)
                ],
                candidate_score=7.5,
                variability_metrics={"mad": 0.04, "rms": 0.07},
            )
            for entry in entries
        }
        saved: list[tuple[str, str]] = []

        dialog = DifferentialQuickLabelDialog(
            entries=entries,
            label_options=(("Detectable Variable", "detectable_variable"), ("Not Detectable", "not_detectable"), ("Ambiguous", "ambiguous")),
            label_lookup=lambda _entry: None,
            save_label=lambda entry, label: saved.append((entry.source_id, label)),
            series_lookup=lambda entry: series_map.get(entry.source_id),
            details_lookup=lambda entry: f"Review {entry.name}",
            theme="dark",
        )

        self.assertIn("Alpha", dialog._entry_label.text())
        self.assertIn("Review Alpha", dialog._details_output.toPlainText())
        label_buttons = dialog.findChildren(type(dialog._next_button))
        self.assertTrue(any(button.text() == "Detectable Variable" and button.focusPolicy() == Qt.FocusPolicy.NoFocus for button in label_buttons))
        self.assertEqual(dialog._previous_button.text(), "<")
        self.assertEqual(dialog._next_button.text(), ">")
        self.assertEqual(dialog._train_button.text(), "Train")
        self.assertEqual(dialog._light_curve_widget._theme, "dark")

        dialog._save_current_label("detectable_variable")

        self.assertEqual(saved, [("vsx-1", "detectable_variable")])
        self.assertEqual(dialog.saved_count, 1)
        self.assertIn("Beta", dialog._entry_label.text())

        dialog.close()


if __name__ == "__main__":
    unittest.main()