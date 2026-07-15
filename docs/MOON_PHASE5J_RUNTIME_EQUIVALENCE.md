# Moon Phase 5J Runtime Equivalence

Phase 5J treats offline registration as necessary but insufficient. Before the live renderer binds real tiled albedo, it now requires all of the following:

- The Phase 5I manifest registration certificate matches the active global albedo hash.
- Resident tiles belong to the current manifest/cache generation.
- Sampling the visible lunar disc footprint through the same UV-bounds route used by the shader is fully covered by the bound tiles.
- Live global-versus-tiled probes compare against the active global LOD image selected for the same draw and match within tolerance, including Tycho, Copernicus, Mare Crisium, Mare Imbrium, and Plato when they route through bound tiles.

If a check fails, the renderer does not bind tiled albedo and continues with the global Moon texture. Coverage failure reports `Moon tiles found but bound tiles do not safely cover the visible lunar UV footprint.` Live-content failure reports `Moon tiles found but live global/tiled sample probes do not match.`

## Runtime Diagnostics

The Moon debug overlay reports the active source mode, tile root, manifest hash, global/tile source hashes, convention hash, cache generation, bound tile keys and bounds, sampled coverage/fallback fractions, live probe deltas, and whether stale or invalid tile sampling was detected.

The `Tile/global route` debug render mode colors tiled samples green and global fallback samples blue. Red marks an invalid tile-local coordinate and should never be visible. `Global/tiled difference` displays live albedo differences at the current orientation and FOV.

## Cache Contract

The CPU tile cache generation changes when the manifest, tile tree identity, or active global source identity changes. Loaded tile images carry that generation. The OpenGL renderer drops old tiled textures when it receives a new generation, so rebuilding tiles does not require retaining stale GPU tile objects.

## Developer Switches

- `CITIZEN_PHOTOMETRY_DISABLE_TILED_ALBEDO=1`
- `CITIZEN_PHOTOMETRY_DISABLE_TILED_NORMALS=1`
- `CITIZEN_PHOTOMETRY_FORCE_GLOBAL_MOON_TEXTURE=1`

The pre-existing Moon debug controls remain available for global-only and tiled-only albedo comparisons at the same camera state.

## Threshold Reproduction

`scripts/moon_visual_smoke.py` records `moon_phase5j_runtime_transition` for `1.2 x 0.6` degrees and `1.1 x 0.5` degrees. At the reported threshold the first view may remain global; the second may use tiles only when live coverage and equivalence pass. Either safe tiled activation or deliberate global fallback is acceptable; unmatched tiled activation is not.
