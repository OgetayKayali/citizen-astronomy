# Moon Phase 5L: Polar UV Guard

The Moon uses equirectangular UV mapping. Longitude has no unique value at either lunar pole, so high-frequency albedo or tangent-space normals can converge visually into a radial point where a visible pole approaches the terminator.

## Runtime Behavior

The shader now applies a conservative polar guard:

- Terrain normal influence fades from `polar_normal_fade_start_lat_deg` to `polar_normal_fade_end_lat_deg`.
- At the exact pole, `polar_use_analytic_normal` resolves shading to the analytic sphere normal.
- Tiled albedo fades only at more extreme latitude, toward a longitude-averaged global sample, retaining most polar geography while removing the point-spread pattern.
- Longitude textures remain repeat-wrapped; tile bounds continue to support `u = 0/1` wrap without clamping unrelated UVs into a tile.

The UV debug view marks polar guard regions in magenta and the longitude seam in cyan.

## Diagnostics

The Moon debug overlay and `scripts/moon_visual_smoke.py` report:

- diagnostic lunar latitude/longitude and projected pole location
- visible north/south pole and seam-crossing state
- pole and seam proximity factors
- polar albedo and normal fade factors
- analytic-normal and singularity-guard state
- seam repeat-wrap state

## Tile Build

LDEM-derived normal tiles receive additional polar smoothing and derivative suppression. `build_manifest.json` records the pass count and latitude fade band in `normal.polar_smoothing`.
