# Moon Phase 5K: Tiled-Normal Continuity

Phase 5K addresses relief artifacts at the first real-tile activation threshold without disabling correctly registered tiled albedo.

## Runtime Rule

At tile activation, albedo tiles may be used immediately after Phase 5J coverage and equivalence checks pass. Tiled normals are separate:

- `global_normal_strength` and `tiled_normal_strength` are configured independently.
- `global_terminator_normal_strength` and `tiled_terminator_normal_strength` are configured independently.
- Tiled normal contribution remains zero in the first activation band, then fades in with larger apparent Moon diameter.
- Tiled tangent slopes are clamped by `tiled_normal_max_slope`.
- Relief contribution is softened near very low illumination at the terminator.

The reported `1.2 x 0.6` to `1.1 x 0.5` transition therefore uses global albedo/global normal followed by tiled albedo/global normal, preventing an abrupt relief change while preserving the albedo detail improvement.

## Diagnostics

The Moon overlay now reports the active normal source, tile-normal strength and fade factor, terminator normal factor, lower-left terminator candidate tile, maximum normal slope, and artifact-risk flag. Bound normal tiles are analyzed for min/max/mean slope, gradient magnitude, outlier count, and, when available, the matching global-normal region.

`scripts/moon_visual_smoke.py` records the threshold pair and four comparisons at `1.1 x 0.5`:

- tiled albedo plus unpolished fully enabled tiled normal
- tiled albedo plus global normal
- tiled albedo with no normal
- global albedo plus global normal

## Tile Generation

Height-derived normal tiles accept `--normal-smoothing-passes N`; new real builds use `2` mild smoothing passes. `build_manifest.json` stores `normal.smoothing_passes` and `normal.normal_generation` alongside the existing source and convention metadata.
