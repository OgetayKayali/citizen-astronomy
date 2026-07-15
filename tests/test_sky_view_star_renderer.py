from __future__ import annotations

import unittest

from photometry_app.ui.sky_view_star_renderer import (
    StarAppearanceSample,
    StarHitGrid,
    StarInstanceInput,
    StarRenderer,
    StarRendererSettings,
    metadata_does_not_affect_psf,
)


class StarRendererModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.renderer = StarRenderer()
        self.settings = StarRendererSettings(
            field_width_deg=60.0,
            star_brightness=1.0,
            star_size=1.0,
            magnitude_size_contrast=1.0,
            limiting_magnitude=8.7,
            device_pixel_ratio=1.0,
        )
        self.renderer.apply_settings(**{name: getattr(self.settings, name) for name in self.settings.__dataclass_fields__})

    def test_equal_magnitude_psf_ignores_metadata_flags(self) -> None:
        base = metadata_does_not_affect_psf(
            "#ffffff",
            6.2,
            searchable=False,
            selectable=False,
            label_visible=False,
            settings=self.settings,
        )
        labeled = metadata_does_not_affect_psf(
            "#ff0000",
            6.2,
            searchable=True,
            selectable=True,
            label_visible=True,
            settings=self.settings,
        )
        self.assertAlmostEqual(base.compact_radius_px, labeled.compact_radius_px, places=6)
        self.assertAlmostEqual(base.compact_intensity, labeled.compact_intensity, places=6)

    def test_no_discontinuity_around_magnitude_5_8(self) -> None:
        below = self.renderer.appearance_for_magnitude(5.79)
        above = self.renderer.appearance_for_magnitude(5.81)
        self.assertTrue(below.visible and above.visible)
        self.assertLess(abs(below.compact_radius_px - above.compact_radius_px), 0.15)
        self.assertLess(abs(below.compact_intensity - above.compact_intensity) / max(below.compact_intensity, 1e-6), 0.12)

    def test_visibility_fade_changes_intensity_not_footprint(self) -> None:
        full = self.renderer.appearance_for_magnitude(7.0, visibility=1.0)
        faded = self.renderer.appearance_for_magnitude(7.0, visibility=0.35)
        self.assertAlmostEqual(full.compact_radius_px, faded.compact_radius_px, places=6)
        self.assertLess(faded.compact_intensity, full.compact_intensity * 0.5)

    def test_subpixel_faint_stars_use_minimum_footprint(self) -> None:
        wide = StarRendererSettings(field_width_deg=180.0, limiting_magnitude=9.0, device_pixel_ratio=1.0)
        self.renderer.apply_settings(**{name: getattr(wide, name) for name in wide.__dataclass_fields__})
        # Force a magnitude that would otherwise have a tiny raw radius.
        sample = self.renderer._compute_appearance(10.5, wide)
        if sample.visible and sample.compact_radius_px > 0.0:
            self.assertGreaterEqual(sample.compact_radius_px, wide.faint_min_radius_px - 1.0e-6)

    def test_bright_stars_have_halo_parameters_while_keeping_compact_core(self) -> None:
        bright = self.renderer.appearance_for_magnitude(0.5)
        faint = self.renderer.appearance_for_magnitude(6.5)
        self.assertGreater(bright.halo_intensity, 0.0)
        self.assertGreater(bright.halo_radius_px, bright.compact_radius_px)
        self.assertEqual(faint.halo_intensity, 0.0)
        self.assertGreater(bright.compact_intensity, faint.compact_intensity)

    def test_pack_instances_reuses_buffer_and_counts_halos(self) -> None:
        stars = [
            StarInstanceInput(10.0, 10.0, 0.8, "#eaf0ff", 45.0, 1.0, 1),
            StarInstanceInput(20.0, 20.0, 7.0, "#eaf0ff", 20.0, 0.8, 2),
            StarInstanceInput(30.0, 30.0, 12.0, "#eaf0ff", 10.0, 0.1, 3),
        ]
        compact, halo = self.renderer.pack_instances(stars, self.settings)
        self.assertGreaterEqual(compact.shape[0], 1)
        self.assertGreaterEqual(halo.shape[0], 1)
        self.assertEqual(self.renderer.metrics.halo_star_count, halo.shape[0])

    def test_hit_grid_queries_neighbor_cells(self) -> None:
        grid = StarHitGrid(cell_size_px=20.0)
        payload = object()
        grid.insert(25.0, 25.0, 8.0, payload)
        hits = grid.query(19.0, 19.0)
        self.assertEqual(len(hits), 1)
        self.assertIs(hits[0][3], payload)


if __name__ == "__main__":
    unittest.main()
