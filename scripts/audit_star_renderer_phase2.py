"""Generate StarRenderer phase-2 audit tables (model-only; no OpenGL context)."""

from __future__ import annotations

from photometry_app.ui.sky_view_star_renderer import StarRenderer, StarRendererSettings

MAGNITUDES = (
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


def main() -> None:
    renderer = StarRenderer()
    settings = StarRendererSettings(field_width_deg=60.0, device_pixel_ratio=1.0)
    renderer.apply_settings(**{name: getattr(settings, name) for name in settings.__dataclass_fields__})
    print("mag raw_phys rend_phys rend_logic intensity halo_r_logic halo_i visible")
    for magnitude in MAGNITUDES:
        sample = renderer.appearance_for_magnitude(float(magnitude), visibility=1.0)
        print(
            f"{magnitude:6} "
            f"{sample.raw_radius_physical_px:8.4f} "
            f"{sample.compact_radius_physical_px:8.4f} "
            f"{sample.compact_radius_px:8.4f} "
            f"{sample.compact_intensity:10.6f} "
            f"{sample.halo_radius_px:8.4f} "
            f"{sample.halo_intensity:10.6f} "
            f"{int(sample.visible)}"
        )
    print()
    print("Uploaded bytes (no halo):")
    for count in (2500, 6000, 9000):
        print(f"  {count}: {StarRenderer.uploaded_bytes_for_star_counts(count, 0)} bytes")
    print("Fade (@mag 8.2):")
    for visibility in (1.0, 0.8, 0.6, 0.4, 0.2, 0.1, 0.05, 0.0):
        sample = renderer.appearance_for_magnitude(8.2, visibility=visibility)
        print(f"  vis={visibility:.2f} radius={sample.compact_radius_px:.4f} intensity={sample.compact_intensity:.6f}")


if __name__ == "__main__":
    main()
