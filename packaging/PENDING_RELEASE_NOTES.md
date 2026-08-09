# Pending release notes

Fixes staged for the next update. Use these notes for the `-Notes` parameter of
`packaging/publish_github_update.ps1`, then clear this file after publishing.

## Since 0.1.1-alpha.7

### Differential Photometry

- Folder WCS method: pick from the first frame with a central Gaia mag-bin check, then apply to every frame (embedded WCS, CCVALS repair, or re-solve).
- Standard Magnitude Y-axis (default): band-matched VSP → APASS → Gaia G; Calibrated Magnitude stays Gaia G.
- Manual Apply: refreshes aperture RA/Dec with the repaired/solved WCS and sky-matches comps for scientific Standard Magnitude.
- Overview filter: target + comps + check across filters; right-click Icon/Color; Animation reveals all series.
- After Generate/Apply: Overview + Standard Magnitude defaults; light curve follows the selected Source Results row; Apply keeps results visible and skips a Gaia WCS re-probe.
- Aperture Editor: starts empty (independent of automatic results); double-click target, Shift-click comp, Ctrl-click check; place/drag no longer jumps.
- Clear Cache for This Folder also resets saved apertures; File → Clear All Cache… hard-clears cached data (keeps settings).

### Sky Explorer

- Survey Stretch updates live (STF / STF Bright; default STF).
- Curves edit the stretched survey view; Feather soft-blends tile seams (default 55%).

### Asteroid / Comet

- Show RA/Dec (default on) and Show Grid with independent density controls.
- Blink and Export sit beside Hide Info.
- Zoom-out is limited to fit-to-image again.

### Display

- Curves: smooth tone curve from histogram control points (not sharp linear segments).
- Curves edit the already-stretched preview; display only—overlays and measurements unchanged.

### App

- Updates: distinct download vs rebuild phases, rebuild warning, and a busy progress bar so the dialog does not look stuck.
- Contributing: acknowledge Emre Can Alagöz, Julien de Winter, and Enrique Boeneker for support, review, and scientific guidance.
