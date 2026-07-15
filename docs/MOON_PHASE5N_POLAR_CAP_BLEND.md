# Moon Phase 5N: Polar-Cap Blend Polish

Phase 5N targets the visible circular transition ring left after polar-cap routing removed the south-pole UV singularity.

## Blend Policy

- Real north and south cap assets are generated at `2048` px by default.
- Lat-long albedo blends into polar-cap albedo from `60` through `78` degrees absolute latitude.
- The shader uses a quintic `smootherstep` curve, with an optional curve-power control in the manifest and runtime settings.
- Polar-cap normal detail has its own conservative band, fading in only from `78` through `86` degrees.
- The default polar-cap normal strength is lower than the ordinary tiled-normal strength.

## Boundary Matching

The tile builder measures cap-versus-canonical albedo samples around the cap join and writes color and luminance delta metrics to `build_manifest.json`. Optional boundary normalization applies a mild, tapered luminance/contrast/color correction inside the blend region rather than recoloring the full cap or the Moon outside it.

The real Phase 5N asset build records:

- `polar_caps.texture_size = 2048`
- `polar_caps.blend_start_lat_deg = 60`
- `polar_caps.blend_end_lat_deg = 78`
- `polar_caps.normal_blend_start_lat_deg = 78`
- `polar_caps.normal_blend_end_lat_deg = 86`

## Runtime And Diagnostics

Polar-cap textures use linear magnification and trilinear minification to match Moon tile filtering, with clamp-to-edge wrapping in the planar cap chart. The Moon overlay and visual smoke report cap size, albedo band, curve power, boundary color/luminance deltas, separate normal blend factor, filtering policy, and a `polar_cap_ring_risk` indicator.

The Phase 5N smoke result requires bounded boundary deltas, conservative normal blending, and no ring-risk flag for the installed real cap assets. Missing caps continue to use the Phase 5L analytic polar fallback safely.

Phase 5O continues from here by matching the cap core itself to the surrounding Moon so the cap no longer reads as a blurry patch. See `docs/MOON_PHASE5O_POLAR_CAP_INTEGRATION.md`.
