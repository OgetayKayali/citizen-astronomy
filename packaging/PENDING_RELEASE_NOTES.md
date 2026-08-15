# Pending release notes

Fixes staged for the next update. Use these notes for the `-Notes` parameter of
`packaging/publish_github_update.ps1`, then clear this file after publishing.

## Since 0.1.1-alpha.8

### Differential Photometry

- Folder WCS sanity: detection→Gaia scoring with skip-brightest, coherent-shift subtraction, and soft-accept for stable moderate matches; all tunables are in Settings → Advanced so good embedded WCS (e.g. W UMa) is accepted while bad CRVAL/CCVALS cases still repair or re-solve.
- Scan Comps replaces Find Better Fit: build a mag/color/distance candidate pool, favourite comps, browse combination light curves, and apply a chosen set to refresh the differential curve and sticky Source Results comps (favourite checkboxes now stick correctly).

### Sky Explorer

- Restore Display stretch **None** and apply it automatically for already-stretched uploads (e.g. 8-bit TIFF/JPEG finals) instead of re-running STF.
- Fix Explore SIMBAD queries for modern astroquery (use `ra`/`dec` instead of legacy `ra(d)`/`dec(d)` that now raise).
- Object-type stroke, fill, and text colors stay independent when edited; Advanced/Scientific pickers now open on the displayed color. Default category hues were retuned (nebulae amber/gold so they contrast with H-alpha).
- Auto/Manual is a mode switch (fixed width, same as Export): Manual keeps catalog overlays, lets you add marks, and right-click Delete removes unwanted automatic annotations from the image or Source Results. After drawing a mark, property edits update that last mark until you switch tools; selecting a mark again applies further edits to it. Text annotations include opacity.
- Settings → Visuals: set a default label font, style (Regular/Bold/Italic/Bold Italic), and size for all object-type categories; per-type Text edits still override.
- Object-type stroke, fill, and text edits update the image overlay immediately (no unselect/reselect).
- Catalog object-type mode: pick NGC, IC, LDN, VdB, LBN, Messier, Barnard, Sharpless, or HASH PN and show only those objects, labeled with catalog names, in a light white–yellow–orange palette. HASH PN uses usual names (not HASH numbers). Cluster members and paper IDs such as `IC 5146 1` or `[RAB2011] IC1` are not treated as Index Catalogue objects.
- Settings → Visuals: Stroke Outline color and width for the dark halo around marker strokes; set width to 0 to turn it off.
- Export Image saves the annotated field at the original image resolution (not a window screenshot), with label and stroke sizes matched to the on-screen view; JPEG uses high quality.

### App

- Settings: move WCS sanity, shared workers, and Increase SNR from General into a new Advanced tab so the dialog fits on screen.
- Saving Settings applies the changes and stays in the current mode instead of returning to the Mode Selection window.
- Open camera RAW (DNG, CR2/CR3, NEF, ARW, ORF, RW2, RAF, PEF, and related LibRaw types) anywhere JPEG is already accepted; Differential Photometry still skips RAW/JPG/PNG as unsuitable.
- Unsigned macOS alpha: pack a Drive-ready build kit on Windows (`packaging/pack_macos_build_kit.ps1`), build `Citizen Astronomy.app` on a Mac (`packaging/build_macos_unsigned.sh`); bundle Astropy/NumPy/photutils/PyVO metadata and use an early runtime fallback for missing optional Astropy/PyVO data files at startup (resolve local SIMBAD criteria JSON; do not stub it empty); first launch via right-click → Open; no Velopack in-app updates on Mac yet.
- Update Available window is wider and shows a scrollable What's new list so longer release notes stay readable.
