from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QRectF
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication
from unittest.mock import MagicMock

from photometry_app.core.sky_atlas import SkyAtlasObject
from photometry_app.ui.main_window import _SkyAtlasViewWidget, _SkyVisibleObject


_APP = QApplication.instance() or QApplication([])


class SkyViewPhase3PerfBehaviorTests(unittest.TestCase):
    def test_unified_partition_skips_fast_star_groups(self) -> None:
        widget = _SkyAtlasViewWidget()
        widget._star_renderer._available = True
        widget._star_renderer._require_vao = True
        widget._sky_view_star_renderer_disabled_for_session = False

        faint = SkyAtlasObject(
            name="HIP 1",
            object_type="star",
            ra_deg=10.0,
            dec_deg=20.0,
            magnitude=7.0,
            catalog="Hipparcos",
            searchable=False,
            label_visible=False,
            selectable=False,
        )
        named = SkyAtlasObject(
            name="Vega",
            object_type="star",
            ra_deg=279.2,
            dec_deg=38.8,
            magnitude=0.03,
            catalog="Local",
            searchable=True,
            label_visible=True,
            selectable=True,
        )
        visible = [
            _SkyVisibleObject(faint, 10.0, 10.0, 1.0, 7.0, 40.0),
            _SkyVisibleObject(named, 20.0, 20.0, 1.0, 0.03, 50.0),
        ]
        fast_groups, rich, gpu = widget._partition_visible_objects(visible)
        self.assertEqual(fast_groups, {})
        self.assertEqual(gpu, [])
        self.assertEqual([item.sky_object.name for item in rich], ["Vega"])

    def test_label_stars_remain_overlay_candidates_while_dragging(self) -> None:
        widget = _SkyAtlasViewWidget()
        widget._star_renderer._available = True
        widget._star_renderer._require_vao = True
        widget._drag_origin = QPoint(1, 1)
        labeled = SkyAtlasObject(
            name="Vega",
            object_type="star",
            ra_deg=279.2,
            dec_deg=38.8,
            magnitude=0.03,
            catalog="Local",
            searchable=True,
            label_visible=True,
            selectable=True,
        )
        fast = SkyAtlasObject(
            name="HIP 2",
            object_type="star",
            ra_deg=11.0,
            dec_deg=21.0,
            magnitude=4.0,
            catalog="Hipparcos",
            searchable=False,
            label_visible=False,
            selectable=False,
        )
        visible = [
            _SkyVisibleObject(fast, 10.0, 10.0, 1.0, 4.0, 40.0),
            _SkyVisibleObject(labeled, 20.0, 20.0, 1.0, 0.03, 50.0),
        ]
        fast_groups, rich, gpu = widget._partition_visible_objects(visible)
        self.assertEqual(fast_groups, {})
        self.assertEqual(gpu, [])
        self.assertEqual([item.sky_object.name for item in rich], ["Vega"])

    def test_interaction_busy_skips_hit_grid_rebuild(self) -> None:
        widget = _SkyAtlasViewWidget()
        widget._drag_origin = QPoint(1, 1)
        selected = SkyAtlasObject(
            name="Sel",
            object_type="star",
            ra_deg=1.0,
            dec_deg=2.0,
            magnitude=2.0,
            catalog="Local",
            searchable=True,
            label_visible=True,
            selectable=True,
        )
        widget._selected_object = selected
        widget._last_projected_objects = [(5.0, 5.0, 8.0, selected)]
        widget._star_hit_grid.insert(5.0, 5.0, 8.0, selected)
        rich = [_SkyVisibleObject(selected, 12.0, 14.0, 1.0, 2.0, 30.0)]
        painter = MagicMock()
        widget._draw_object_overlays(
            painter,
            rich,
            QRectF(0, 0, 200, 200),
            draw_star_sprites=False,
            ground_geometry=None,
            all_visible_objects=rich,
        )
        self.assertTrue(widget._deferred_hit_grid_dirty)
        self.assertEqual(len(widget._star_hit_grid.query(5.0, 5.0)), 1)

    def test_interaction_busy_draws_labels_without_hit_grid_rebuild(self) -> None:
        widget = _SkyAtlasViewWidget()
        widget._drag_origin = QPoint(1, 1)
        labeled = SkyAtlasObject(
            name="Vega",
            object_type="star",
            ra_deg=279.2,
            dec_deg=38.8,
            magnitude=0.03,
            catalog="Local",
            searchable=True,
            label_visible=True,
            selectable=True,
        )
        rich = [_SkyVisibleObject(labeled, 12.0, 14.0, 1.0, 0.03, 30.0)]
        painter = MagicMock()
        label_tuple = (
            (0, 0.0, 0.0, 0.0, 0, "Vega"),
            QFont(),
            QPointF(20.0, 20.0),
            QRectF(20.0, 20.0, 40.0, 12.0),
            "Vega",
            QColor("#ffffff"),
            QColor("#000000"),
            labeled,
        )
        widget._build_object_label_candidates = MagicMock(return_value=[label_tuple])  # type: ignore[method-assign]
        widget._draw_object_overlays(
            painter,
            rich,
            QRectF(0, 0, 200, 200),
            draw_star_sprites=False,
            ground_geometry=None,
            all_visible_objects=rich,
        )
        self.assertTrue(widget._deferred_hit_grid_dirty)
        self.assertGreaterEqual(painter.drawText.call_count, 2)

    def test_legacy_fast_groups_can_be_rebuilt_for_visual_fallback(self) -> None:
        widget = _SkyAtlasViewWidget()
        star = SkyAtlasObject(
            name="HIP 3",
            object_type="star",
            ra_deg=12.0,
            dec_deg=22.0,
            magnitude=6.2,
            catalog="Hipparcos",
            searchable=False,
            label_visible=False,
            selectable=False,
        )
        groups = widget._legacy_fast_star_groups_from_visible_objects(
            [_SkyVisibleObject(star, 30.0, 40.0, 1.0, 6.2, 45.0)]
        )
        self.assertEqual(sum(len(points) for points in groups.values()), 1)

    def test_visual_fallback_groups_include_bright_stars(self) -> None:
        widget = _SkyAtlasViewWidget()
        star = SkyAtlasObject(
            name="Vega",
            object_type="star",
            ra_deg=279.2,
            dec_deg=38.8,
            magnitude=0.03,
            catalog="Local",
            searchable=True,
            label_visible=True,
            selectable=True,
        )
        groups = widget._legacy_fast_star_groups_from_visible_objects(
            [_SkyVisibleObject(star, 30.0, 40.0, 1.0, 0.03, 45.0)]
        )
        self.assertEqual(sum(len(points) for points in groups.values()), 1)

    def test_label_candidate_origin_is_stable_single_anchor(self) -> None:
        origins = _SkyAtlasViewWidget._object_label_candidate_origins(
            x=100.0,
            y=80.0,
            hit_radius=8.0,
            text_width=40.0,
            text_ascent=10.0,
            text_height=14.0,
            baseline_y=75.6,
            is_star=True,
        )
        self.assertEqual(len(origins), 1)
        self.assertGreater(origins[0].x(), 100.0)


if __name__ == "__main__":
    unittest.main()
