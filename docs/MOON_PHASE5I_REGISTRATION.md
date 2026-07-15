# Moon Phase 5I Texture Registration

The Moon shader convention is `moon_shader_equirectangular_v1`:

- `u = fract(0.5 + longitude / 2pi)`, increasing with east longitude.
- `v = clamp(0.5 - latitude / pi, 0, 1)`, with north at smaller `v`.
- The seam is `u = 0/1` at `-180/+180` degrees and the central meridian is `u = 0.5`.
- Files are stored top-to-bottom as north-to-south images. Global images and tiles are both mirrored during OpenGL upload, so no authoring-time vertical flip is implied.

## Source Inventory

| Role | File | Dimensions | Orientation and Use |
| --- | --- | ---: | --- |
| Active global albedo | `textures/moon_lroc_color_16bit_srgb_8k.tif` | 8192 x 4096 | Canonical shader UV source; SHA-256 `DB7808E878B6A55EB409BB231EAB8DEB477F84B5C9D7396D76FF73E5D54992D9` |
| Active global normal input | `textures/moon_ldem_16.tif` | 5760 x 2880 | Canonical-height source; normal LODs are derived in memory |
| Real tiled albedo input | `textures/moon_lroc_color_16bit_srgb_8k.tif` | 8192 x 4096 | Must register to active global albedo before tiles activate |
| Real tiled normal input | `textures/moon_ldem_16.tif` | 5760 x 2880 | Height-derived normal tiles; same declared transforms as albedo |
| Real generated tiles | `assets/moon_tiles/albedo/L0..L3`, `normal/L0..L3` | 512 x 512 tiles | Generated from the declared source/transform in `build_manifest.json` |
| Synthetic global/tiled albedo | `assets/moon_tiles_synthetic/source/synthetic_moon_uv_grid.png` | 512 x 256 | Coordinate-debug source shared by global and tiles |
| Synthetic flat normal | `assets/moon_tiles_synthetic/source/synthetic_moon_flat_normal.png` | 512 x 256 | Coordinate-debug normal source |
| Synthetic generated tiles | `assets/moon_tiles_synthetic/albedo/L0..L3`, `normal/L0..L3` | 32 x 32 tiles | Synthetic registration regression path |

There are no persistent resized Moon texture cache files in the runtime path. `MoonCache` generates global albedo and normal LOD images in memory at widths 512, 1024, 2048, 4096, and 8192 where supported.

## Validation Contract

`scripts/build_moon_tiles.py validate-registration` compares the active global albedo path with the generated tile path at identical UV positions and reports normalized RGB/luminance deltas, a best-fit longitude shift, possible `u`/`v` flips, and patch matches around Tycho, Copernicus, Mare Crisium, Mare Imbrium, and Plato.

Every build manifest records source hashes, source dimensions, convention metadata, explicit transforms, and whether registration passed for the active global hash. Sky View refuses unregistered real tiles by default and uses the global texture instead; the Moon overlay displays the rejection and source IDs. The development-only override is `CITIZEN_PHOTOMETRY_SKY_VIEW_MOON_DEBUG_ALLOW_UNREGISTERED_TILES=1`.
