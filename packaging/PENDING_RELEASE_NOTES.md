# Pending release notes

Fixes staged for the next update. Use these notes for the `-Notes` parameter of
`packaging/publish_github_update.ps1`, then clear this file after publishing.

## Since 0.1.1-alpha.11

### Differential Photometry

- Date/Time light-curve x-axis is labeled Observation Time (UTC).
- Target Field Animation uses a large live 16:9 preview with Combined / Star Field / Light Curve inspection, a compact right-hand inspector, a readable panel-split slider, and a footer with the output summary plus Export Animation. Resizing the window keeps the preview aspect ratio, and the light-curve preview no longer flashes at the top-left when settings change.

### Sky Explorer

- Image and Survey replace Open and Comparison: they sit together with accent styling, first click loads a file or survey, and later clicks can replace it or add a comparison. Survey list now includes DSS2, PanSTARRS, SDSS, 2MASS, WISE, GALEX, SHS, IPHAS, and NSNS DR0.2.
- Shift+left-drag draws an Explore ROI so catalog queries stay inside that rectangle; right-click the ROI to remove it.
- Explore becomes Terminate while a catalog query is running so you can abort it; the button returns to Explore or Update when the query finishes.

### Asteroid / Comet

- Fix SkyBoT “unreadable response” failures: retry truncated HTTP 200 VOTables, and show parse error, QUERY_STATUS, and body preview in the Work Log and error dialog.
