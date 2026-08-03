"""Tests for survey tile identity, STF independence, scheduling, and placeholders."""

from __future__ import annotations

import unittest

import numpy as np

from photometry_app.core.survey_tiles import (
    SurveyTileResolution,
    SurveyTileState,
    build_viewport_survey_tile_work,
    compute_survey_tile_stf_parameters,
    initial_survey_tile_work_order,
    is_survey_no_data_error,
    make_survey_tile_key,
    make_survey_tile_placeholder_rgba,
    render_survey_tile_display_rgba,
    survey_tile_indices_for_image_point,
    survey_tile_pixel_rect,
    survey_tile_request_priority,
)


class SurveyTilesTest(unittest.TestCase):
    def test_tile_pixel_rect_and_inverse_are_stable(self) -> None:
        x0, y0, width, height = survey_tile_pixel_rect(1, -2, width_px=100, height_px=80)
        self.assertEqual((x0, y0, width, height), (-100.0, -160.0, 100.0, 80.0))
        tile_i, tile_j = survey_tile_indices_for_image_point(-50.0, -120.0, width_px=100, height_px=80)
        self.assertEqual((tile_i, tile_j), (1, -2))

    def test_tile_keys_differ_by_survey_and_grid(self) -> None:
        a = make_survey_tile_key(
            survey_key="dss2",
            tile_i=0,
            tile_j=0,
            origin_ra_deg=10.0,
            origin_dec_deg=-20.0,
            fov_arcmin=30.0,
            width_px=256,
            height_px=256,
        )
        b = make_survey_tile_key(
            survey_key="dss2",
            tile_i=1,
            tile_j=0,
            origin_ra_deg=10.0,
            origin_dec_deg=-20.0,
            fov_arcmin=30.0,
            width_px=256,
            height_px=256,
        )
        c = make_survey_tile_key(
            survey_key="panstarrs",
            tile_i=0,
            tile_j=0,
            origin_ra_deg=10.0,
            origin_dec_deg=-20.0,
            fov_arcmin=30.0,
            width_px=256,
            height_px=256,
        )
        self.assertNotEqual(a.as_tuple(), b.as_tuple())
        self.assertNotEqual(a.as_tuple(), c.as_tuple())

    def test_initial_work_order_is_center_low_then_high_then_neighbors(self) -> None:
        ordered = initial_survey_tile_work_order(0, 0, radius=1)
        self.assertEqual(ordered[0], (0, 0, SurveyTileResolution.PREVIEW))
        self.assertEqual(ordered[1], (0, 0, SurveyTileResolution.REFINE))
        preview_neighbors = [item for item in ordered[2:] if item[2] is SurveyTileResolution.PREVIEW]
        refine_neighbors = [item for item in ordered[2:] if item[2] is SurveyTileResolution.REFINE]
        self.assertEqual(len(preview_neighbors), 8)
        self.assertEqual(len(refine_neighbors), 8)
        self.assertTrue(all(item[2] is SurveyTileResolution.PREVIEW for item in ordered[2:10]))

    def test_loading_neighbor_does_not_change_center_stf(self) -> None:
        rng = np.random.default_rng(0)
        center = rng.normal(120.0, 18.0, (48, 48)).astype(np.float32)
        center[5, 5] = 800.0
        neighbor = rng.normal(40.0, 5.0, (48, 48)).astype(np.float32)
        center_stf = compute_survey_tile_stf_parameters(center)
        neighbor_stf = compute_survey_tile_stf_parameters(neighbor)
        center_again = compute_survey_tile_stf_parameters(center)
        self.assertEqual(center_stf, center_again)
        self.assertNotEqual(center_stf, neighbor_stf)
        center_rgba = render_survey_tile_display_rgba(center, center_stf)
        center_rgba_again = render_survey_tile_display_rgba(center, center_stf)
        np.testing.assert_array_equal(center_rgba, center_rgba_again)

    def test_constant_tile_does_not_explode(self) -> None:
        constant = np.full((32, 32), 12.0, dtype=np.float32)
        stf = compute_survey_tile_stf_parameters(constant)
        rgba = render_survey_tile_display_rgba(constant, stf)
        self.assertTrue(np.isfinite(rgba).all())
        self.assertEqual(rgba.shape, (32, 32, 4))

    def test_no_data_placeholder_is_not_plain_black(self) -> None:
        rgba = make_survey_tile_placeholder_rgba(width=40, height=30, state=SurveyTileState.NO_DATA)
        self.assertGreater(int(rgba.max()), 0)
        self.assertTrue(is_survey_no_data_error("Survey image has no coverage: empty"))
        self.assertFalse(is_survey_no_data_error("timeout contacting CDS"))

    def test_visible_preview_outranks_distant_refine(self) -> None:
        visible_preview = survey_tile_request_priority(
            tile_i=0, tile_j=0, view_i=0, view_j=0, resolution=SurveyTileResolution.PREVIEW,
        )
        distant_refine = survey_tile_request_priority(
            tile_i=3, tile_j=3, view_i=0, view_j=0, resolution=SurveyTileResolution.REFINE,
        )
        self.assertLess(visible_preview, distant_refine)

    def test_center_refine_outranks_neighbor_preview_and_refine(self) -> None:
        center_refine = survey_tile_request_priority(
            tile_i=0, tile_j=0, view_i=0, view_j=0, resolution=SurveyTileResolution.REFINE,
        )
        neighbor_preview = survey_tile_request_priority(
            tile_i=1, tile_j=0, view_i=0, view_j=0, resolution=SurveyTileResolution.PREVIEW,
        )
        neighbor_refine = survey_tile_request_priority(
            tile_i=-1, tile_j=0, view_i=0, view_j=0, resolution=SurveyTileResolution.REFINE,
        )
        self.assertLess(center_refine, neighbor_preview)
        self.assertLess(center_refine, neighbor_refine)

    def test_viewport_work_orders_center_refine_before_neighbor_preview(self) -> None:
        from photometry_app.core.survey_tiles import SurveyTileRecord, make_survey_tile_key

        def _record(tile_i: int, tile_j: int, state: SurveyTileState) -> SurveyTileRecord:
            key = make_survey_tile_key(
                survey_key="dss2",
                tile_i=tile_i,
                tile_j=tile_j,
                origin_ra_deg=1.0,
                origin_dec_deg=2.0,
                fov_arcmin=20.0,
                width_px=64,
                height_px=64,
            )
            record = SurveyTileRecord(key=key, state=state)
            if state is SurveyTileState.LOW_RES_READY:
                record.preview_display = np.zeros((4, 4, 4), dtype=np.uint8)
            return record

        records = {
            (0, 0): _record(0, 0, SurveyTileState.LOW_RES_READY),
            (1, 0): _record(1, 0, SurveyTileState.NOT_REQUESTED),
        }
        work = build_viewport_survey_tile_work(view_i=0, view_j=0, records=records, radius=1)
        self.assertGreaterEqual(len(work), 2)
        self.assertEqual(work[0][1:], (0, 0, SurveyTileResolution.REFINE))
        self.assertEqual(work[1][3], SurveyTileResolution.PREVIEW)

    def test_viewport_work_skips_ready_tiles(self) -> None:
        from photometry_app.core.survey_tiles import SurveyTileRecord, make_survey_tile_key

        key = make_survey_tile_key(
            survey_key="dss2",
            tile_i=0,
            tile_j=0,
            origin_ra_deg=1.0,
            origin_dec_deg=2.0,
            fov_arcmin=20.0,
            width_px=64,
            height_px=64,
        )
        record = SurveyTileRecord(key=key, state=SurveyTileState.HIGH_RES_READY)
        record.refine_display = np.zeros((8, 8, 4), dtype=np.uint8)
        work = build_viewport_survey_tile_work(view_i=0, view_j=0, records={(0, 0): record}, radius=1)
        self.assertTrue(all(not (item[1] == 0 and item[2] == 0) for item in work))


if __name__ == "__main__":
    unittest.main()
