# Pending release notes

Fixes staged for the next update. Use these notes for the `-Notes` parameter of
`packaging/publish_github_update.ps1`, then clear this file after publishing.

## Since 0.1.1-alpha.9

### Differential Photometry

- Export → Target Field Animation: crop and align the selected target, correct clouds and transparency changes from the local stars visible in the crop, apply a shared stretch, and export the field beside its light curve.

### HR Diagram

- Cap the Gaia/VizieR field query (G ≤ 18) and tile wide crowded fields so VizieR no longer crashes with a raw parse error or returns only a truncated slice; Gaia magnitude, row cap, and tiling are adjustable in Settings → HR Diagram.

### Asteroid / Comet

- Before long WCS recovery, compare mount/header pointing with embedded WCS, log the choice, prefer astrometry.net when a key is set or the two disagree, and keep Gaia tiling from using broken stub WCS.

### App

- Settings: keep Save/Cancel visible by capping the dialog to about 90% of the screen and scrolling tall tabs.
- Local Gaia WCS recovery: cap catalog rows and retry dense-field VizieR failures with brighter magnitude cuts, then a smaller search radius; demote saturated/near-ceiling peaks and spatially thin match stars so local solves work without an API key.
- Settings → General: Astrometry Timeout (seconds) next to the API key; default remains 300s.
