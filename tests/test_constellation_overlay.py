from __future__ import annotations

import math
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from photometry_app.ui.constellation_overlay import (
    ConstellationDataLoader,
    ConstellationLabel,
    ConstellationOverlay,
    ConstellationOverlaySettings,
)


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _normalized_vector(x_value: float, y_value: float, z_value: float) -> tuple[float, float, float]:
    length = math.sqrt(x_value * x_value + y_value * y_value + z_value * z_value)
    if length <= 1.0e-9:
        return (0.0, 1.0, 0.0)
    return (x_value / length, y_value / length, z_value / length)


def _scene(*, field_width_deg: float = 180.0, field_height_deg: float = 90.0) -> SimpleNamespace:
    return SimpleNamespace(
        viewport_rect=QRectF(0.0, 0.0, 800.0, 450.0),
        device_pixel_ratio=1.0,
        field_width_deg=field_width_deg,
        field_height_deg=field_height_deg,
        camera_forward=(0.0, 1.0, 0.0),
        camera_up=(0.0, 0.0, 1.0),
        equatorial_to_horizon_matrix=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    )


class ConstellationDataLoaderTest(unittest.TestCase):
    def test_packaged_d3_constellation_data_loads_and_caches(self) -> None:
        loader = ConstellationDataLoader()

        first = loader.load()
        second = loader.load()

        self.assertIs(first, second)
        self.assertEqual(loader.last_cache_status, "hit")
        self.assertGreater(len(first.line_segments), 300)
        self.assertGreaterEqual(len(first.labels), 88)
        self.assertIn("Ori", {segment.constellation_id for segment in first.line_segments})
        self.assertIn("Orion", {label.name for label in first.labels})
        self.assertEqual(len({label.constellation_id for label in first.labels}), len(first.labels))


class ConstellationOverlayRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_overlay_projects_and_reuses_projected_cache(self) -> None:
        overlay = ConstellationOverlay()
        scene = _scene(field_width_deg=110.0, field_height_deg=62.0)
        settings = ConstellationOverlaySettings(enabled=True, show_lines=True, show_labels=True)
        image = QImage(800, 450, QImage.Format.Format_RGBA8888)
        image.fill(0)

        painter = QPainter(image)
        first_metrics = overlay.draw(painter, scene, settings)
        second_metrics = overlay.draw(painter, scene, settings)
        painter.end()

        self.assertEqual(first_metrics.cache_status, "miss")
        self.assertEqual(second_metrics.cache_status, "hit")
        self.assertEqual(second_metrics.data_cache_status, "hit")
        self.assertGreater(first_metrics.drawn_line_count, 0)
        self.assertGreater(first_metrics.drawn_label_count, 0)
        self.assertGreater(first_metrics.overlay_seconds, 0.0)

    def test_project_lines_keeps_segment_fully_in_front(self) -> None:
        overlay = ConstellationOverlay()
        scene = _scene()
        line_vectors = np.asarray(
            [[
                _normalized_vector(0.15, 1.0, 0.0),
                _normalized_vector(-0.2, 1.0, 0.08),
            ]],
            dtype=np.float32,
        )

        lines = overlay._project_lines(scene, scene.viewport_rect, line_vectors)

        self.assertEqual(len(lines), 1)
        self.assertGreater(lines[0].length(), 0.0)

    def test_project_lines_skips_segment_fully_behind(self) -> None:
        overlay = ConstellationOverlay()
        scene = _scene()
        line_vectors = np.asarray(
            [[
                _normalized_vector(0.15, -1.0, 0.0),
                _normalized_vector(-0.25, -1.0, 0.12),
            ]],
            dtype=np.float32,
        )

        lines = overlay._project_lines(scene, scene.viewport_rect, line_vectors)

        self.assertEqual(lines, ())

    def test_project_lines_clips_segment_crossing_front_boundary(self) -> None:
        overlay = ConstellationOverlay()
        scene = _scene()
        line_vectors = np.asarray(
            [[
                _normalized_vector(0.12, 1.0, 0.0),
                _normalized_vector(1.0, -0.2, 0.0),
            ]],
            dtype=np.float32,
        )

        lines = overlay._project_lines(scene, scene.viewport_rect, line_vectors)

        self.assertEqual(len(lines), 1)
        viewport_diagonal = math.hypot(scene.viewport_rect.width(), scene.viewport_rect.height())
        self.assertGreater(lines[0].length(), 0.0)
        self.assertLess(lines[0].length(), viewport_diagonal * overlay._MAX_PROJECTED_SEGMENT_DIAGONAL_FACTOR)

    def test_project_lines_rejects_non_finite_projection_points(self) -> None:
        overlay = ConstellationOverlay()
        scene = _scene()
        line_vectors = np.asarray(
            [[
                _normalized_vector(0.1, 1.0, 0.0),
                _normalized_vector(-0.1, 1.0, 0.0),
            ]],
            dtype=np.float32,
        )

        with patch.object(
            overlay,
            "_project_horizon_vectors",
            return_value=(
                np.asarray((120.0, np.nan), dtype=np.float32),
                np.asarray((40.0, 60.0), dtype=np.float32),
                np.asarray((True, True)),
            ),
        ):
            lines = overlay._project_lines(scene, scene.viewport_rect, line_vectors)

        self.assertEqual(lines, ())

    def test_project_lines_rejects_absurdly_long_projected_segments(self) -> None:
        overlay = ConstellationOverlay()
        scene = _scene()
        line_vectors = np.asarray(
            [[
                _normalized_vector(0.1, 1.0, 0.0),
                _normalized_vector(-0.1, 1.0, 0.0),
            ]],
            dtype=np.float32,
        )

        with patch.object(
            overlay,
            "_project_horizon_vectors",
            return_value=(
                np.asarray((30.0, 5000.0), dtype=np.float32),
                np.asarray((30.0, 30.0), dtype=np.float32),
                np.asarray((True, True)),
            ),
        ):
            lines = overlay._project_lines(scene, scene.viewport_rect, line_vectors)

        self.assertEqual(lines, ())

    def test_project_labels_skip_anchor_behind_camera(self) -> None:
        overlay = ConstellationOverlay()
        scene = _scene()
        label = ConstellationLabel(
            constellation_id="TST",
            name="Behind",
            abbreviation="TST",
            anchor_ra_deg=0.0,
            anchor_dec_deg=0.0,
            anchor_unit_vector=_normalized_vector(0.0, -1.0, 0.0),
        )
        label_vectors = np.asarray((label.anchor_unit_vector,), dtype=np.float32)

        projected_labels = overlay._project_labels(scene, scene.viewport_rect, (label,), label_vectors)

        self.assertEqual(projected_labels, ())


if __name__ == "__main__":
    unittest.main()
