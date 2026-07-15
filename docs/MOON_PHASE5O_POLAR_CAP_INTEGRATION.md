# Moon Phase 5O: Polar-Cap Footprint Removal

Phase 5O removes the remaining blurry cap-core footprint after the south-pole singularity and blend-ring issues were already contained.

## Builder Changes

- Polar-cap albedo generation keeps the `2048` px default and records cap size in `build_manifest.json`.
- Boundary matching remains in place, but the builder now also measures cap-core sharpness and cap-versus-surrounding detail inside the cap interior.
- A tapered appearance-harmonization pass can raise cap-core acutance and local contrast without changing the global Moon outside the cap path.
- New build controls:
  `--polar-cap-albedo-sharpness`
  `--polar-cap-local-contrast`
  `--polar-cap-boundary-match-strength`
  `--polar-cap-core-match-strength`

## Diagnostics

`build_manifest.json` and the Moon overlay now report:

- `polar_cap_size_px`
- `polar_cap_core_sharpness_estimate`
- `polar_cap_boundary_luma_delta`
- `polar_cap_boundary_contrast_delta`
- `polar_cap_core_vs_surrounding_detail_delta`
- `polar_cap_footprint_risk`

The footprint-risk check is aimed at the actual failure mode: a cap core that has gone visibly softer than the cap outskirts and surrounding Moon. Conservative polar normals remain unchanged; cap albedo integration is the priority in this pass.

## Debug Views

`Polar-cap route` remains the high-level routing view.

`Polar-cap footprint` adds a core-versus-blend-versus-surrounding breakdown:

- blue: surrounding lat-long path
- yellow: cap blend band
- red: cap core
