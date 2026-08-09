from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from photometry_app.ui.curves_widget import HistogramCurvesWidget


class HistogramCurvesWidgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_paint_uses_smooth_curve_samples_instead_of_control_polyline(self) -> None:
        widget = HistogramCurvesWidget()
        widget.resize(320, 180)
        widget.set_curve_points(((0.0, 0.0), (0.5, 0.2), (1.0, 1.0)))

        sampled: list[tuple[tuple[float, float], ...]] = []

        def fake_sample(points, *, sample_count=96):
            result = tuple((float(index) / max(1, sample_count - 1), 0.0) for index in range(sample_count))
            sampled.append(result)
            return result

        with patch(
            "photometry_app.core.plotting.sample_image_curve_polyline",
            side_effect=fake_sample,
        ):
            widget.paintEvent(None)

        self.assertEqual(len(sampled), 1)
        self.assertGreaterEqual(len(sampled[0]), 48)


if __name__ == "__main__":
    unittest.main()
