from __future__ import annotations

import os
import unittest

from PySide6.QtWidgets import QApplication, QPushButton

from photometry_app.ui.color_picker import (
    ColorPickerPopup,
    apply_swatch_button,
    checkerboard_pixmap,
    clear_recent_colors,
    normalize_hex_color,
    recent_colors,
    remember_recent_color,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class ColorPickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        clear_recent_colors()

    def test_normalize_hex_color_accepts_short_and_none_tokens(self) -> None:
        self.assertEqual(normalize_hex_color("#abc"), "#aabbcc")
        self.assertEqual(normalize_hex_color("38BDF8"), "#38bdf8")
        self.assertIsNone(normalize_hex_color("none"))
        self.assertIsNone(normalize_hex_color("transparent"))
        self.assertIsNone(normalize_hex_color(None))
        self.assertEqual(normalize_hex_color("nope", default="#111827"), "#111827")

    def test_recent_colors_ignore_none_and_keep_newest_first(self) -> None:
        remember_recent_color(None)
        remember_recent_color("#ef4444")
        remember_recent_color("#38bdf8")
        remember_recent_color("#EF4444")
        self.assertEqual(recent_colors(), ("#ef4444", "#38bdf8"))

    def test_apply_swatch_button_uses_checkerboard_for_none(self) -> None:
        button = QPushButton()
        apply_swatch_button(button, None)
        self.assertIn("dashed", button.styleSheet())
        self.assertFalse(button.icon().isNull())
        apply_swatch_button(button, "#22c55e")
        self.assertIn("#22c55e", button.styleSheet())
        self.assertTrue(button.icon().isNull())

    def test_checkerboard_pixmap_is_opaque(self) -> None:
        pixmap = checkerboard_pixmap(16)
        self.assertEqual(pixmap.width(), 16)
        self.assertGreater(pixmap.toImage().pixelColor(0, 0).alpha(), 0)

    def test_popup_can_select_no_fill_and_live_drag_does_not_commit(self) -> None:
        popup = ColorPickerPopup(allow_none=True)
        popup.set_color("#38bdf8")
        self.assertEqual(popup.current_color(), "#38bdf8")

        changed: list[object] = []
        committed: list[object] = []
        popup.colorChanged.connect(changed.append)
        popup.colorCommitted.connect(committed.append)
        popup._handle_sv_changed(0.4, 0.6)
        self.assertEqual(len(changed), 1)
        self.assertEqual(committed, [])
        self.assertEqual(recent_colors(), ())

        self.assertIsNotNone(popup._none_button)
        popup._none_button.click()
        self.assertIsNone(popup.current_color())
        self.assertEqual(committed[-1], None)
        popup.close()

    def test_popup_without_none_keeps_a_hex_color(self) -> None:
        popup = ColorPickerPopup(allow_none=False)
        popup.set_color(None)
        self.assertIsNotNone(popup.current_color())
        self.assertTrue(str(popup.current_color()).startswith("#"))
        popup.close()

    def test_empty_hex_selects_no_fill_when_allowed(self) -> None:
        popup = ColorPickerPopup(allow_none=True)
        popup.set_color("#38bdf8")
        popup._hex_input.setText("")
        popup._handle_hex_finished()
        self.assertIsNone(popup.current_color())
        popup.close()
