# Pending release notes

Fixes staged for the next update. Use these notes for the `-Notes` parameter of
`packaging/publish_github_update.ps1`, then clear this file after publishing.

## Since 0.1.1-alpha.14

### Differential Photometry

- Automatic, Discover, and Scan Comps comparison stars are chosen per target by closest Gaia magnitude (then sky distance), so bright and faint targets no longer share one mid-range pool.
- Fix Overview showing more comparison stars than Source Results after Generate when per-frame ensembles differ; Overview now follows the same sticky comparison set.
- Restyle Scan Comps to match the Generate workflow: themed cards, step badges, denser Limits layout, Favourites Select All with a green ready badge, clickable pipeline steps, list starring, Overview-with-comps preview, and parallel combination light curves in a wider Combinations page.
- Fix Scan Comps Apply freezing the UI while combination workers shut down, and keep target switching responsive by caching catalog/comparison lookups and skipping a full measurement rescan on every Source Results click.
- Speed WCS recovery on wide fields: stop the losing Gaia/astrometry race for real, keep racing Gaia inside one cheap cone, and reuse one solve across same-night frames with similar pointing.
- Fold locks phase 0 to the VSX/literature period and epoch when those exist (so later nights can shift); without a literature period it still uses the calculated period and Anchor control, and the Work Log records which clock was used. AAVSO export stays a time-series JD file, not the folded view.
- Alpha testing: add an experimental O–C workstation next to Fold. Mark every local max/min in a session (or click one peak/trough) into a persistable log, plot an interactive themed O–C diagram once T₀ and P are set (prefilled from literature when available), show the extrema log full-width with all columns, include other loaded nights, import CAst/AAVSO/historic files, and pull live AID photometry by star name (auto-filled from an automatic scan, editable for manual photometry). Pull AAVSO remembers the last query for this app session, returns to the form if you decline a full-history download, and writes every filter step to the Work Log when nothing remains. AAVSO export stays a time-series JD file. Please try this and report issues.
- Saturation Filter skips only saturated frames, not the whole target; when the setting is off those frames stay on the light curve with a distinct orange × marker.
