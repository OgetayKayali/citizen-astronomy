"""Tests for survey tile identity, STF independence, scheduling, and placeholders."""

from __future__ import annotations

import unittest

import numpy as np

from photometry_app.core.survey_tiles import (
    SurveyTileResolution,
    SurveyTileState,
    apply_survey_tile_edge_feather,
    build_viewport_survey_tile_work,
    compute_survey_tile_stf_parameters,
    initial_survey_tile_work_order,
    is_survey_no_data_error,
    make_survey_tile_key,
    make_survey_tile_placeholder_rgba,
    render_survey_tile_display_rgba,
    survey_tile_draw_rect,
    survey_tile_feather_crop_and_draw,
    survey_tile_fetch_fov_arcmin,
    survey_tile_fetch_size,
    survey_tile_indices_for_image_point,
    survey_tile_max_overlap_scale,
    survey_tile_overlap_scale,
    survey_tile_pixel_rect,
    survey_tile_request_priority,
    survey_tile_sky_center,
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

    def test_stretch_mode_changes_tile_display(self) -> None:
        rng = np.random.default_rng(1)
        data = rng.normal(80.0, 12.0, (48, 48)).astype(np.float32)
        data[10:18, 10:18] += 400.0
        stf = compute_survey_tile_stf_parameters(data, stretch_mode="stf")
        stf_bright = compute_survey_tile_stf_parameters(data, stretch_mode="stf_bright")
        rgba_stf = render_survey_tile_display_rgba(data, stf, stretch_mode="stf")
        rgba_bright = render_survey_tile_display_rgba(data, stf_bright, stretch_mode="stf_bright")
        rgba_log = render_survey_tile_display_rgba(data, stf, stretch_mode="log")
        rgba_inverted = render_survey_tile_display_rgba(data, stf, stretch_mode="stf", inverted=True)

        self.assertGreater(float(np.mean(rgba_bright[..., :3])), float(np.mean(rgba_stf[..., :3])))
        self.assertFalse(np.array_equal(rgba_stf, rgba_log))
        np.testing.assert_array_equal(rgba_inverted[..., :3], 255 - rgba_stf[..., :3])
        np.testing.assert_array_equal(rgba_inverted[..., 3], rgba_stf[..., 3])

    def test_edge_feather_fades_border_pixels(self) -> None:
        alpha = np.full((48, 48), 255, dtype=np.uint8)
        feathered = apply_survey_tile_edge_feather(
            alpha, width=48, height=48, feather_amount=0.7, overlap_scale=survey_tile_overlap_scale(0.7),
        )
        self.assertLess(int(feathered[0, 24]), 255)
        self.assertLess(int(feathered[24, 0]), 255)
        self.assertEqual(int(feathered[24, 24]), 255)
        unchanged = apply_survey_tile_edge_feather(alpha, width=48, height=48, feather_amount=0.0)
        np.testing.assert_array_equal(unchanged, alpha)

    def test_overlap_fetch_keeps_plate_scale_and_centers_draw_rect(self) -> None:
        max_scale = survey_tile_max_overlap_scale()
        self.assertGreater(max_scale, 1.0)
        self.assertAlmostEqual(survey_tile_overlap_scale(0.0), 1.0, places=6)
        self.assertAlmostEqual(survey_tile_overlap_scale(1.0), max_scale, places=6)

        base_w, base_h = 100, 80
        fetch_w, fetch_h = survey_tile_fetch_size(
            width_px=base_w, height_px=base_h, resolution=SurveyTileResolution.REFINE,
        )
        self.assertEqual((fetch_w - base_w) % 2, 0)
        self.assertEqual((fetch_h - base_h) % 2, 0)
        self.assertGreater(fetch_w, base_w)
        self.assertGreater(fetch_h, base_h)
        fetch_fov = survey_tile_fetch_fov_arcmin(
            45.0, width_px=base_w, height_px=base_h, resolution=SurveyTileResolution.REFINE,
        )
        self.assertAlmostEqual(fetch_fov / 45.0, max(fetch_w / base_w, fetch_h / base_h), places=6)

        x0, y0, width, height = survey_tile_pixel_rect(1, -1, width_px=base_w, height_px=base_h)
        dx0, dy0, dwidth, dheight = survey_tile_draw_rect(
            1, -1, width_px=base_w, height_px=base_h, overlap_scale=max_scale,
        )
        self.assertAlmostEqual(x0 + width / 2.0, dx0 + dwidth / 2.0, places=6)
        self.assertAlmostEqual(y0 + height / 2.0, dy0 + dheight / 2.0, places=6)
        self.assertAlmostEqual(dwidth / width, max_scale, places=6)
        self.assertAlmostEqual(dheight / height, max_scale, places=6)

    def test_feather_crop_expands_symmetrically_from_center(self) -> None:
        base_w, base_h = 100, 80
        fetch_w, fetch_h = survey_tile_fetch_size(
            width_px=base_w, height_px=base_h, resolution=SurveyTileResolution.REFINE,
        )
        hard = survey_tile_feather_crop_and_draw(
            image_width=fetch_w, image_height=fetch_h,
            base_width=float(base_w), base_height=float(base_h), feather_amount=0.0,
        )
        soft = survey_tile_feather_crop_and_draw(
            image_width=fetch_w, image_height=fetch_h,
            base_width=float(base_w), base_height=float(base_h), feather_amount=1.0,
        )
        mid = survey_tile_feather_crop_and_draw(
            image_width=fetch_w, image_height=fetch_h,
            base_width=float(base_w), base_height=float(base_h), feather_amount=0.55,
        )
        # Hard crop is the centered base footprint.
        self.assertEqual(hard[2], base_w)
        self.assertEqual(hard[3], base_h)
        self.assertEqual(hard[0], (fetch_w - base_w) // 2)
        self.assertEqual(hard[1], (fetch_h - base_h) // 2)
        self.assertAlmostEqual(hard[4], float(base_w), places=6)
        # Full feather uses the whole overlapped image, still centered.
        self.assertEqual(soft[:4], (0, 0, fetch_w, fetch_h))
        # Intermediate feather keeps equal left/right (and top/bottom) margins.
        self.assertEqual(mid[0], fetch_w - mid[0] - mid[2])
        self.assertEqual(mid[1], fetch_h - mid[1] - mid[3])
        # Draw size grows with crop while staying proportional to the base cell.
        self.assertGreater(mid[4], hard[4])
        self.assertLess(mid[4], soft[4])

    def test_adjacent_overlap_tiles_share_sky_along_seam(self) -> None:
        """Overlapped neighbor plates must agree on sky at the shared base-cell boundary."""
        from photometry_app.core.survey_images import build_sky_explorer_field_wcs

        origin_ra, origin_dec = 270.63, -23.03
        fov_arcmin = 30.0
        width_px = height_px = 120
        key0 = make_survey_tile_key(
            survey_key="dss2_blue",
            tile_i=0,
            tile_j=0,
            origin_ra_deg=origin_ra,
            origin_dec_deg=origin_dec,
            fov_arcmin=fov_arcmin,
            width_px=width_px,
            height_px=height_px,
        )
        key1 = make_survey_tile_key(
            survey_key="dss2_blue",
            tile_i=1,
            tile_j=0,
            origin_ra_deg=origin_ra,
            origin_dec_deg=origin_dec,
            fov_arcmin=fov_arcmin,
            width_px=width_px,
            height_px=height_px,
        )
        ra0, dec0 = survey_tile_sky_center(key0)
        ra1, dec1 = survey_tile_sky_center(key1)
        fetch_w, fetch_h = survey_tile_fetch_size(
            width_px=width_px, height_px=height_px, resolution=SurveyTileResolution.REFINE,
        )
        fetch_fov = survey_tile_fetch_fov_arcmin(
            fov_arcmin, width_px=width_px, height_px=height_px, resolution=SurveyTileResolution.REFINE,
        )
        wcs0 = build_sky_explorer_field_wcs(
            ra_deg=ra0, dec_deg=dec0, fov_arcmin=fetch_fov, width_px=fetch_w, height_px=fetch_h,
        )
        wcs1 = build_sky_explorer_field_wcs(
            ra_deg=ra1, dec_deg=dec1, fov_arcmin=fetch_fov, width_px=fetch_w, height_px=fetch_h,
        )
        x0, y0, crop_w, crop_h, _dw, _dh = survey_tile_feather_crop_and_draw(
            image_width=fetch_w, image_height=fetch_h,
            base_width=float(width_px), base_height=float(height_px), feather_amount=0.0,
        )
        mid_y = y0 + 0.5 * float(crop_h - 1)
        # Origin left base edge ↔ east-tile right base edge.
        sky0 = wcs0.pixel_to_world_values(float(x0) - 0.5, mid_y)
        sky1 = wcs1.pixel_to_world_values(float(fetch_w - x0) - 0.5, mid_y)
        self.assertAlmostEqual(float(sky0[0]) % 360.0, float(sky1[0]) % 360.0, places=5)
        self.assertAlmostEqual(float(sky0[1]), float(sky1[1]), places=5)

    def test_curves_apply_after_survey_tile_stretch(self) -> None:
        from photometry_app.core.survey_tiles import stretch_survey_tile_float

        rng = np.random.default_rng(2)
        data = rng.normal(90.0, 15.0, (40, 40)).astype(np.float32)
        data[8:14, 8:14] += 350.0
        stf = compute_survey_tile_stf_parameters(data, stretch_mode="stf")
        stretched = stretch_survey_tile_float(data, stf, stretch_mode="stf")
        curve_points = ((0.0, 0.0), (0.5, 0.2), (1.0, 1.0))
        curved = render_survey_tile_display_rgba(
            data, stf, stretch_mode="stf", curve_points=curve_points,
        )
        baseline = render_survey_tile_display_rgba(data, stf, stretch_mode="stf")
        self.assertLess(float(np.mean(curved[..., :3])), float(np.mean(baseline[..., :3])))
        self.assertGreater(float(np.mean(stretched)), 0.0)

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
