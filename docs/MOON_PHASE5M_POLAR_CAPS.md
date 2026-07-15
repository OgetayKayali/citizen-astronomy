# Moon Phase 5M: Polar Caps

At high zoom, the lat-long texture chart collapses all longitudes into one screen-visible lunar pole. Phase 5M routes high-latitude pixels through dedicated north and south polar charts instead of asking equirectangular detail to survive that singularity.

## Assets

`scripts/build_moon_tiles.py` generates:

- `assets/moon_tiles/polar_caps/albedo/north.png`
- `assets/moon_tiles/polar_caps/albedo/south.png`
- optional planar-derived `normal/north.png` and `normal/south.png` when an LDEM height source is available

The caps use an azimuthal-equidistant projection. The pole maps to the texture center; the cap reaches latitude 60 degrees. Phase 5N refines the original join with 2048 px real caps and a smoother albedo blend from 60 through 78 degrees absolute latitude, while delaying cap normal detail until 78 through 86 degrees. `build_manifest.json` records the projection, dimensions, source hashes, source convention, transforms, paths, blend policy, and validation report.

## Runtime

Registered cap assets are bound with the same manifest generation as real tiles. The shader keeps the existing lat-long tiled/global path outside the cap band, blends to the appropriate polar albedo inside the band, and samples only the cap near the pole. Cap normal maps use a stable planar tangent basis and low strength; when unavailable, the Phase 5L analytic-normal fallback remains in force.

The `Polar-cap route` debug view displays lat-long pixels in blue, blend pixels in yellow, and polar-cap pixels in green. The Moon overlay reports cap availability, active route, projection, blend factor, visible pole latitude, and fallback reason.

The blend-ring polish, boundary matching, filtering details, and additional diagnostics are documented in `docs/MOON_PHASE5N_POLAR_CAP_BLEND.md`.

## Safety

Missing or failed cap assets do not disable the Moon. They retain the Phase 5L polar fade and analytic normal behavior. Cap textures are generation-scoped, so manifest or asset changes evict stale CPU/GPU cap resources together with ordinary tiled textures.
