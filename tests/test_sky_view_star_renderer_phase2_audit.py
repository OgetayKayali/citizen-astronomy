from __future__ import annotations

import unittest

from photometry_app.ui.sky_view_star_renderer import (
    StarInstanceInput,
    StarRenderer,
    StarRendererSettings,
    metadata_does_not_affect_psf,
)


AUDIT_MAGNITUDES = (
    -1,
    0,
    1,
    2,
    3,
    4,
    5,
    5.70,
    5.75,
    5.79,
    5.80,
    5.81,
    5.85,
    5.90,
    6,
    7,
    8,
    9,
    10,
    11,
    12,
)


class StarRendererPhase2AuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.renderer = StarRenderer()
        self.settings = StarRendererSettings(field_width_deg=60.0, device_pixel_ratio=1.0)
        self.renderer.apply_settings(**{name: getattr(self.settings, name) for name in self.settings.__dataclass_fields__})

    def test_magnitude_lut_audit_table_has_no_5_8_cliff(self) -> None:
        rows = []
        for magnitude in AUDIT_MAGNITUDES:
            sample = self.renderer.appearance_for_magnitude(float(magnitude), visibility=1.0)
            rows.append((magnitude, sample))
            self.assertIsNotNone(sample)
        near = [row for row in rows if 5.7 <= float(row[0]) <= 5.9]
        radii = [sample.compact_radius_px for _, sample in near]
        intensities = [sample.compact_intensity for _, sample in near]
        for left, right in zip(radii, radii[1:]):
            self.assertLess(abs(left - right), 0.08)
        for left, right in zip(intensities, intensities[1:]):
            relative = abs(left - right) / max(left, right, 1.0e-6)
            self.assertLess(relative, 0.08)

    def test_metadata_independence_for_compact_psf(self) -> None:
        variants = (
            dict(searchable=False, selectable=False, label_visible=False),
            dict(searchable=True, selectable=False, label_visible=False),
            dict(searchable=False, selectable=True, label_visible=False),
            dict(searchable=False, selectable=False, label_visible=True),
            dict(searchable=True, selectable=True, label_visible=True),
        )
        samples = [
            metadata_does_not_affect_psf("#c8d7ff", 2.1, settings=self.settings, **flags)
            for flags in variants
        ]
        for sample in samples[1:]:
            self.assertAlmostEqual(samples[0].compact_radius_px, sample.compact_radius_px, places=6)
            self.assertAlmostEqual(samples[0].compact_intensity, sample.compact_intensity, places=6)

    def test_faint_star_fade_keeps_footprint(self) -> None:
        radii = []
        intensities = []
        for visibility in (1.0, 0.8, 0.6, 0.4, 0.2, 0.1, 0.05, 0.0):
            sample = self.renderer.appearance_for_magnitude(8.2, visibility=visibility)
            radii.append(sample.compact_radius_px)
            intensities.append(sample.compact_intensity)
            if visibility > 0.0:
                self.assertGreaterEqual(sample.compact_radius_px, self.settings.faint_min_radius_px - 1.0e-6)
        for radius in radii[:-1]:
            self.assertAlmostEqual(radius, radii[0], places=6)
        self.assertGreater(intensities[0], intensities[3])
        self.assertEqual(intensities[-1], 0.0)

    def test_bright_star_halo_is_separate_layer(self) -> None:
        with_halo = self.renderer.appearance_for_magnitude(0.03)  # Vega-like
        self.assertGreater(with_halo.compact_radius_px, 0.0)
        self.assertGreater(with_halo.halo_radius_px, with_halo.compact_radius_px)
        self.assertGreater(with_halo.halo_intensity, 0.0)
        disabled = StarRendererSettings(
            field_width_deg=60.0,
            device_pixel_ratio=1.0,
            halo_intensity=0.0,
            halo_threshold_magnitude=-5.0,
        )
        self.renderer.apply_settings(**{name: getattr(disabled, name) for name in disabled.__dataclass_fields__})
        no_halo = self.renderer.appearance_for_magnitude(0.03)
        self.assertGreater(no_halo.compact_intensity, 0.0)
        self.assertEqual(no_halo.halo_intensity, 0.0)

    def test_device_pixel_ratio_keeps_logical_size(self) -> None:
        logical_radii = []
        physical_radii = []
        for dpr in (1.0, 1.25, 1.5, 2.0):
            settings = StarRendererSettings(field_width_deg=60.0, device_pixel_ratio=dpr)
            self.renderer.apply_settings(**{name: getattr(settings, name) for name in settings.__dataclass_fields__})
            sample = self.renderer.appearance_for_magnitude(4.0)
            logical_radii.append(sample.compact_radius_px)
            physical_radii.append(sample.compact_radius_physical_px)
            self.assertAlmostEqual(sample.compact_radius_px * dpr, sample.compact_radius_physical_px, places=5)
        for left, right in zip(logical_radii, logical_radii[1:]):
            self.assertAlmostEqual(left, right, places=5)
        self.assertGreater(physical_radii[-1], physical_radii[0])

    def test_upload_byte_estimate_scales_with_instance_stride(self) -> None:
        self.assertEqual(StarRenderer.uploaded_bytes_for_star_counts(2500, 40), 2500 * 40 + 40 * 40)
        self.assertEqual(StarRenderer.uploaded_bytes_for_star_counts(6000, 80), 6000 * 40 + 80 * 40)
        self.assertEqual(StarRenderer.uploaded_bytes_for_star_counts(9000, 120), 9000 * 40 + 120 * 40)

    def test_twinkle_uniform_settings_do_not_invalidate_geometry_cache_key_helper(self) -> None:
        # Packing ignores changing only time/interaction after first pack when caller caches key.
        stars = [StarInstanceInput(10.0, 12.0, 3.0, "#eaf0ff", 40.0, 1.0, 7)]
        first, _ = self.renderer.pack_instances(stars, self.settings)
        self.renderer.apply_settings(time_seconds=12.0, interaction_stable=False, twinkle_amount=0.9)
        second, _ = self.renderer.pack_instances(stars, None)
        self.assertEqual(first.shape, second.shape)
        self.assertTrue((first == second).all())


if __name__ == "__main__":
    unittest.main()
