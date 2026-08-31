from __future__ import annotations

import unittest

from photometry_app.core.scan_comps import (
    MAX_COMBINATIONS_SOFT_CAP,
    ScanCompReferenceInput,
    build_scan_comp_candidates,
    combination_count,
    iter_unordered_comp_sets,
)


class ScanCompsHelpersTests(unittest.TestCase):
    def test_iter_unordered_comp_sets_dedupes_and_is_unordered(self) -> None:
        sets = iter_unordered_comp_sets(["b", "a", "a", "c"], 2)
        self.assertEqual(sets, [("a", "b"), ("a", "c"), ("b", "c")])
        self.assertEqual(combination_count(3, 2), 3)

    def test_build_scan_comp_candidates_ranks_by_closest_magnitude_then_distance(self) -> None:
        references = [
            ScanCompReferenceInput("far_match", "FarMatch", 10.5, 20.0, 12.05, 0.82),
            ScanCompReferenceInput("near_mismatch", "NearMismatch", 10.01, 20.01, 14.0, 0.81),
            ScanCompReferenceInput("best", "Best", 10.01, 20.01, 12.05, 0.81),
            ScanCompReferenceInput("mid", "Mid", 10.2, 20.2, 12.4, 0.9),
        ]
        candidates, color_used = build_scan_comp_candidates(
            target_ra_deg=10.0,
            target_dec_deg=20.0,
            target_magnitude=12.0,
            target_bp_rp=0.8,
            references=references,
            pool_size=10,
        )
        self.assertFalse(color_used)
        self.assertEqual([item.source_id for item in candidates], ["best", "far_match", "mid", "near_mismatch"])
        self.assertLess(candidates[0].delta_mag or 99.0, candidates[-1].delta_mag or 0.0)

    def test_build_scan_comp_candidates_optional_hard_filters(self) -> None:
        references = [
            ScanCompReferenceInput("best", "Best", 10.01, 20.01, 12.05, 0.81),
            ScanCompReferenceInput("wrong_mag", "WrongMag", 10.01, 20.01, 14.0, 0.81),
            ScanCompReferenceInput("wrong_color", "WrongColor", 10.01, 20.01, 12.05, 1.5),
            ScanCompReferenceInput("far", "Far", 10.5, 20.0, 12.05, 0.82),
        ]
        candidates, color_used = build_scan_comp_candidates(
            target_ra_deg=10.0,
            target_dec_deg=20.0,
            target_magnitude=12.0,
            target_bp_rp=0.8,
            references=references,
            max_delta_mag=1.0,
            max_delta_color=0.3,
            max_separation_arcmin=30.0,
            pool_size=10,
        )
        self.assertTrue(color_used)
        self.assertEqual([item.source_id for item in candidates], ["best", "far"])

    def test_build_scan_comp_candidates_skips_color_when_missing(self) -> None:
        references = [
            ScanCompReferenceInput("a", "A", 10.01, 20.01, 12.1, None),
            ScanCompReferenceInput("b", "B", 10.02, 20.02, 12.2, None),
        ]
        candidates, color_used = build_scan_comp_candidates(
            target_ra_deg=10.0,
            target_dec_deg=20.0,
            target_magnitude=12.0,
            target_bp_rp=None,
            references=references,
            pool_size=10,
        )
        self.assertFalse(color_used)
        self.assertEqual(len(candidates), 2)
        self.assertFalse(any(item.color_used for item in candidates))

    def test_soft_cap_constant(self) -> None:
        self.assertEqual(MAX_COMBINATIONS_SOFT_CAP, 200)
        self.assertGreater(combination_count(20, 3), MAX_COMBINATIONS_SOFT_CAP)


if __name__ == "__main__":
    unittest.main()
