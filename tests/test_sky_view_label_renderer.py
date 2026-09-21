from __future__ import annotations

import os
import unittest

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication

from photometry_app.ui.sky_view_label_renderer import (
    FontAtlas,
    GpuLabelDraw,
    GpuMarkerDraw,
    SkyViewLabelRenderer,
    font_atlas_key,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class SkyViewLabelRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.font = QFont("Segoe UI")
        self.font.setPointSizeF(10.0)
        self.renderer = SkyViewLabelRenderer()

    def test_font_atlas_measures_and_bakes_whole_strings(self) -> None:
        atlas = FontAtlas(width=256, height=256)
        width, ascent, height = atlas.measure_text(self.font, "NGC 1234")
        self.assertGreater(width, 0.0)
        self.assertGreater(ascent, 0.0)
        self.assertGreater(height, 0.0)
        sprite = atlas.sprite(self.font, "NGC 1234")
        assert sprite is not None
        self.assertGreater(sprite.width, sprite.height)
        self.assertGreater(atlas.glyph_count, 0)

    def test_baked_string_sprite_is_wide_readable_text(self) -> None:
        sprite = self.renderer.atlas.sprite(self.font, "Vega")
        assert sprite is not None
        self.assertGreater(sprite.width, sprite.height)
        region = self.renderer.atlas.image.copy(sprite.x, sprite.y, sprite.width, sprite.height)
        opaque = 0
        columns_with_ink = 0
        for x in range(region.width()):
            column_has_ink = False
            for y in range(region.height()):
                if region.pixelColor(x, y).alpha() > 20:
                    opaque += 1
                    column_has_ink = True
            if column_has_ink:
                columns_with_ink += 1
        self.assertGreater(opaque, 30)
        self.assertGreater(columns_with_ink, 8)

    def test_pack_instances_draws_one_shadow_and_one_text_quad(self) -> None:
        labels = (
            GpuLabelDraw(
                font=self.font,
                origin_x=120.0,
                origin_y=80.0,
                text="M 31",
                color=QColor(220, 230, 245, 240),
                shadow_color=QColor(8, 14, 26, 160),
            ),
        )
        instances = self.renderer.pack_instances(labels)
        self.assertEqual(instances.shape[1], 12)
        self.assertEqual(instances.shape[0], 2)
        shadow_alpha = float(instances[0, 11])
        text_alpha = float(instances[-1, 11])
        self.assertLess(shadow_alpha, text_alpha)
        self.assertGreater(text_alpha, 0.8)
        self.assertAlmostEqual(float(instances[0, 0]) - float(instances[1, 0]), 1.0, places=4)
        self.assertAlmostEqual(float(instances[0, 1]) - float(instances[1, 1]), 1.0, places=4)
        uv_width = float(instances[1, 6]) - float(instances[1, 4])
        self.assertGreater(uv_width, 0.005)

    def test_top_left_origin_shifts_by_ascent(self) -> None:
        color = QColor("#dfe7f2")
        baseline = self.renderer.pack_instances(
            (
                GpuLabelDraw(self.font, 10.0, 40.0, "A", color, baseline=True),
            )
        )
        top_left = self.renderer.pack_instances(
            (
                GpuLabelDraw(self.font, 10.0, 40.0, "A", color, baseline=False),
            )
        )
        self.assertEqual(baseline.shape[0], 1)
        self.assertEqual(top_left.shape[0], 1)
        self.assertGreater(float(top_left[0, 1]), float(baseline[0, 1]))

    def test_pack_marker_instances_keeps_center_and_colors(self) -> None:
        markers = (
            GpuMarkerDraw(
                x=40.0,
                y=55.0,
                radius=6.5,
                fill=QColor(80, 200, 180, 40),
                edge=QColor(80, 200, 180, 160),
                stroke=1.5,
            ),
        )
        instances = self.renderer.pack_marker_instances(markers)
        self.assertEqual(instances.shape, (1, 12))
        self.assertAlmostEqual(float(instances[0, 0]), 40.0)
        self.assertAlmostEqual(float(instances[0, 1]), 55.0)
        self.assertAlmostEqual(float(instances[0, 2]), 6.5)
        self.assertGreater(float(instances[0, 11]), float(instances[0, 7]))

    def test_draw_without_gl_returns_false(self) -> None:
        labels = (
            GpuLabelDraw(self.font, 0.0, 0.0, "Vega", QColor("white")),
        )
        self.assertFalse(self.renderer.available)
        self.assertFalse(self.renderer.draw(labels, QRectF(0.0, 0.0, 800.0, 600.0)))
        self.assertEqual(self.renderer.metrics.backend, "cpu")

    def test_shaders_sample_atlas_in_one_instanced_pass(self) -> None:
        self.assertIn("attribute vec2 a_origin;", SkyViewLabelRenderer._VERTEX_SHADER)
        self.assertIn("attribute vec2 a_uv0;", SkyViewLabelRenderer._VERTEX_SHADER)
        self.assertIn("uniform vec4 u_viewport_rect;", SkyViewLabelRenderer._VERTEX_SHADER)
        self.assertIn("uniform sampler2D u_atlas;", SkyViewLabelRenderer._FRAGMENT_SHADER)
        self.assertIn("length(v_offset_px)", SkyViewLabelRenderer._MARKER_FRAGMENT_SHADER)
        self.assertTrue(hasattr(SkyViewLabelRenderer, "_draw_label_instances"))
        self.assertIn("discard", SkyViewLabelRenderer._FRAGMENT_SHADER)

    def test_restore_qt_unpack_uses_default_alignment(self) -> None:
        stores: dict[int, int] = {}

        class _FakeGL:
            def glPixelStorei(self, enum, value):
                stores[int(enum)] = int(value)

        SkyViewLabelRenderer._restore_qt_unpack(_FakeGL())
        self.assertEqual(stores[0x0CF5], 4)
        self.assertEqual(stores[0x0CF2], 0)
        self.assertEqual(stores[0x0CF3], 0)
        self.assertEqual(stores[0x0CF4], 0)

    def test_font_keys_distinguish_weight_and_size(self) -> None:
        bold = QFont(self.font)
        bold.setBold(True)
        larger = QFont(self.font)
        larger.setPointSizeF(14.0)
        self.assertNotEqual(font_atlas_key(self.font), font_atlas_key(bold))
        self.assertNotEqual(font_atlas_key(self.font), font_atlas_key(larger))


if __name__ == "__main__":
    unittest.main()
