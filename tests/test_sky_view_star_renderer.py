from __future__ import annotations

import unittest

from photometry_app.ui.sky_view_star_renderer import (
    StarAppearanceSample,
    StarCatalogInput,
    StarHitGrid,
    StarInstanceInput,
    StarRenderer,
    StarRendererSettings,
    metadata_does_not_affect_psf,
    star_fov_size_scale,
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

    def test_medium_magnitude_stars_remain_visible_on_textured_sky(self) -> None:
        wide = StarRendererSettings(
            field_width_deg=90.0,
            magnitude_size_contrast=1.0,
            limiting_magnitude=8.7,
            device_pixel_ratio=1.0,
        )
        sample = self.renderer._compute_appearance(5.0, wide)
        self.assertTrue(sample.visible)
        self.assertGreater(sample.compact_intensity, 0.9)
        self.assertGreater(sample.compact_radius_px, 1.7)
        self.assertIn(
            "float magnitude_slope = 0.08 + 0.03 * contrast;",
            StarRenderer._CATALOG_VERTEX_SHADER,
        )

    def test_visibility_fade_changes_intensity_not_footprint(self) -> None:
        full = self.renderer.appearance_for_magnitude(7.0, visibility=1.0)
        faded = self.renderer.appearance_for_magnitude(7.0, visibility=0.35)
        self.assertAlmostEqual(full.compact_radius_px, faded.compact_radius_px, places=6)
        self.assertLess(faded.compact_intensity, full.compact_intensity * 0.5)

    def test_faint_catalog_stars_keep_a_visible_disk(self) -> None:
        sample = self.renderer._compute_appearance(10.5, self.settings)
        self.assertTrue(sample.visible)
        self.assertGreaterEqual(sample.compact_radius_px, 2.2)
        self.assertGreater(sample.compact_intensity, 0.2)
        self.assertIn("0.32 + 0.28 * contrast", StarRenderer._CATALOG_VERTEX_SHADER)
        self.assertIn("1.65 + 4.35", StarRenderer._CATALOG_VERTEX_SHADER)

    def test_subpixel_faint_stars_use_minimum_footprint(self) -> None:
        settings = StarRendererSettings(field_width_deg=60.0, limiting_magnitude=9.0, device_pixel_ratio=1.0)
        self.renderer.apply_settings(**{name: getattr(settings, name) for name in settings.__dataclass_fields__})
        # Force a magnitude that would otherwise have a tiny raw radius.
        sample = self.renderer._compute_appearance(10.5, settings)
        if sample.visible and sample.compact_radius_px > 0.0:
            self.assertGreaterEqual(sample.compact_radius_px, settings.faint_min_radius_px - 1.0e-6)

    def test_wide_field_stars_are_smaller_than_default_fov(self) -> None:
        at_60 = StarRendererSettings(visible_magnitude_limit=5.0, field_width_deg=60.0, device_pixel_ratio=1.0)
        at_sky = StarRendererSettings(visible_magnitude_limit=5.0, field_width_deg=360.0, device_pixel_ratio=1.0)
        mag0_60 = self.renderer._compute_appearance(0.0, at_60)
        mag0_sky = self.renderer._compute_appearance(0.0, at_sky)
        self.assertLess(mag0_sky.compact_radius_px, mag0_60.compact_radius_px * 0.55)
        self.assertLess(star_fov_size_scale(360.0), 0.4)
        self.assertAlmostEqual(star_fov_size_scale(60.0), 1.0, places=5)
        self.assertIn("u_fov_size_scale", StarRenderer._CATALOG_VERTEX_SHADER)
        self.assertIn("effective_limit + 1.15", StarRenderer._CATALOG_VERTEX_SHADER)

    def test_visible_limit_adaptation_brightens_naked_eye_stars_with_stable_magnitude_scale(self) -> None:
        wide = StarRendererSettings(visible_magnitude_limit=5.0, field_width_deg=60.0, device_pixel_ratio=1.0)
        narrow = StarRendererSettings(visible_magnitude_limit=10.0, field_width_deg=8.0, device_pixel_ratio=1.0)
        mag0_wide = self.renderer._compute_appearance(0.0, wide)
        mag1_wide = self.renderer._compute_appearance(1.0, wide)
        mag0_narrow = self.renderer._compute_appearance(0.0, narrow)
        mag1_narrow = self.renderer._compute_appearance(1.0, narrow)
        self.assertGreater(mag0_narrow.compact_intensity, mag0_wide.compact_intensity)
        self.assertGreater(mag1_narrow.compact_intensity, mag1_wide.compact_intensity)
        wide_ratio = mag0_wide.compact_intensity / mag1_wide.compact_intensity
        narrow_ratio = mag0_narrow.compact_intensity / mag1_narrow.compact_intensity
        self.assertAlmostEqual(wide_ratio, narrow_ratio, places=5)
        self.assertIn("u_visible_magnitude_limit - 5.0", StarRenderer._CATALOG_VERTEX_SHADER)

    def test_bright_stars_have_halo_parameters_while_keeping_compact_core(self) -> None:
        bright = self.renderer.appearance_for_magnitude(0.5)
        faint = self.renderer.appearance_for_magnitude(6.5)
        self.assertGreater(bright.halo_intensity, 0.0)
        self.assertGreater(bright.halo_radius_px, bright.compact_radius_px)
        self.assertEqual(faint.halo_intensity, 0.0)
        self.assertGreater(bright.compact_intensity, faint.compact_intensity)
        self.assertIn("float outer_aura = 0.055", StarRenderer._HALO_FRAGMENT_SHADER)
        self.assertIn("vec3 halo_color = mix", StarRenderer._HALO_FRAGMENT_SHADER)
        self.assertIn(") * edge_fade * edge_fade;", StarRenderer._HALO_FRAGMENT_SHADER)
        self.assertIn(") * edge_fade * edge_fade;", StarRenderer._COMPACT_FRAGMENT_SHADER)

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

    def test_static_catalog_packs_celestial_vectors_once(self) -> None:
        self.renderer.set_static_catalog(
            (
                StarCatalogInput(0.0, 0.0, 1.5, "#ff0000", 1),
                StarCatalogInput(90.0, 30.0, 4.0, "#ffffff", 2),
            )
        )

        data = self.renderer._catalog_cpu_data
        self.assertEqual(self.renderer.static_catalog_count, 2)
        self.assertAlmostEqual(float(data[0, 0]), 1.0, places=6)
        self.assertAlmostEqual(float(data[0, 1]), 0.0, places=6)
        self.assertAlmostEqual(float(data[0, 2]), 0.0, places=6)
        self.assertAlmostEqual(float(data[1, 2]), 0.5, places=6)
        self.assertEqual(float(data[0, 3]), 1.5)
        self.assertEqual(float(data[1, 8]), 2.0)
        self.assertEqual(self.renderer._catalog_gpu_generation, -1)

    def test_vao_creation_separates_preexisting_qpainter_error(self) -> None:
        class FakeFunctions:
            def __init__(self) -> None:
                self.errors = [0x0502, 0, 0]

            def glGetError(self) -> int:
                return self.errors.pop(0) if self.errors else 0

        class FakeGl:
            def __init__(self) -> None:
                self.bound: list[int] = []

            @staticmethod
            def glGenVertexArrays(count: int) -> int:
                if count != 1:
                    raise AssertionError(f"expected one VAO, got {count}")
                return 37

            def glBindVertexArray(self, vao_id: int) -> None:
                self.bound.append(vao_id)

            def glDeleteVertexArrays(self, count: int, vao_ids: list[int]) -> None:
                _ = (count, vao_ids)

        functions = FakeFunctions()
        gl = FakeGl()
        self.renderer._functions = functions
        self.renderer._gl = gl

        self.renderer._ensure_vao_bound()

        self.assertEqual(self.renderer._gl_vao_id, 37)
        self.assertEqual(self.renderer.diagnostics().preexisting_gl_errors, (0x0502,))
        self.assertEqual(gl.bound, [37])


if __name__ == "__main__":
    unittest.main()
