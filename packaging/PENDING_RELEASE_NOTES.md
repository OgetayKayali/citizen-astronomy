# Pending release notes

Fixes staged for the next update. Use these notes for the `-Notes` parameter of
`packaging/publish_github_update.ps1`, then clear this file after publishing.

## Since 0.1.1-alpha.12

### Asteroid / Comet

- Fix asteroid/comet detection failing on valid SkyBoT results: namespaced IMCCE VOTables are parsed as XML, and a missing Astropy coordinate-frame file no longer makes the packaged app treat that XML as JSON.

### App

- Recover missing WCS by running astrometry.net and local Gaia matching together and using the first success.
