# Pending release notes

Fixes staged for the next update. Use these notes for the `-Notes` parameter of
`packaging/publish_github_update.ps1`, then clear this file after publishing.

## Since 0.1.1-alpha.13

### Differential Photometry

- Restore wide-field variable-star light curves (for example W UMa): catalogs use the actual image, not the 0.35° WCS sample cone; Gaia is tiled when the field is wider than one VizieR query. Clearing a folder cache also deletes leftover capped catalogs, and filename/OBJECT names still keep the target.
