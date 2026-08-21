from __future__ import annotations

import unittest

from photometry_app.core.target_markers import (
    TARGET_FIELD_MARKER_NONE,
    coerce_asteroid_visual_marker_style,
    coerce_target_field_marker_style,
    pointer_marker_segments,
)


class TargetMarkerTest(unittest.TestCase):
    def test_asteroid_style_accepts_pointer_and_aliases(self) -> None:
        self.assertEqual(coerce_asteroid_visual_marker_style("pointer"), "pointer")
        self.assertEqual(coerce_asteroid_visual_marker_style("open_pointer"), "pointer")
        self.assertEqual(coerce_asteroid_visual_marker_style("crosshair"), "crosshair")
        self.assertEqual(coerce_asteroid_visual_marker_style("nope"), "target")

    def test_target_field_style_defaults_to_none(self) -> None:
        self.assertEqual(coerce_target_field_marker_style(None), TARGET_FIELD_MARKER_NONE)
        self.assertEqual(coerce_target_field_marker_style("none"), TARGET_FIELD_MARKER_NONE)
        self.assertEqual(coerce_target_field_marker_style("pointer"), "pointer")
        self.assertEqual(coerce_target_field_marker_style("square"), "target")
        self.assertEqual(coerce_target_field_marker_style("nope"), TARGET_FIELD_MARKER_NONE)

    def test_pointer_segments_are_left_and_top_arms_with_center_gap(self) -> None:
        segments = pointer_marker_segments(40.0, 40.0, left=2.0, top=2.0, gap=12.0)
        self.assertEqual(len(segments), 2)
        left_arm, top_arm = segments
        self.assertEqual(left_arm, ((2.0, 40.0), (28.0, 40.0)))
        self.assertEqual(top_arm, ((40.0, 2.0), (40.0, 28.0)))
        self.assertLess(left_arm[1][0], 40.0)
        self.assertLess(top_arm[1][1], 40.0)
