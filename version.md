# Version history

Current app version: **0.1.1-alpha.10**

This log tracks finished fixes and features for the next published update. Group notes by mode (or App/Display). Prefer one short bullet per change—not intermediate debugging steps.

## Pending for next release (after 0.1.1-alpha.10)

## 0.1.1-alpha.10

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

## 0.1.1-alpha.9

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

## 0.1.1-alpha.8

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

## 0.1.1-alpha.7

- HR Diagram Display: Show RA/Dec (default on) and Show Grid with independent RA/Dec density controls; these do not affect Sky Explorer or Differential Photometry.
- HR Diagram: double-click a star on the Source Image to select it on the plot and results table (works even before any plot selection, without interfering with pan or Shift+drag ROI).
- HR Diagram: the prerequisites reminder can be dismissed with Don't show again for this version; the choice is saved and resets automatically after each app update.
- Trajectory View Save: GIF/MP4 exports can use cinematic camera motion presets (Fixed, Slow orbit, Object chase) that keep your current zoom and start from the current view, and Meteor Stream debris keeps drifting in the export when Animate debris is on.
- Asteroid/Comet: with Target Marker off, no markers are drawn; Show Labels names only the selected object using the Marker Appearance Label color (default white).
- Settings: remove the Photometry Mode Auto/Manual submenu from the Settings menu (mode switching behavior is unchanged).
- Asteroid/Comet: image, blink, and trail export dialogs default to filenames that include the selected object name (for example `(511) Davida_blink.mp4` or `2015 CU58_trail.gif`).
- Settings → Sky Explorer: split the long options list into General and Visuals tabs so color/appearance controls stay on Visuals.
- Sky Explorer: add a Ruler annotation tool that measures distance between two points in pixels, or in arcsec/arcmin/degrees (auto-scaled) when WCS is available, with the value shown under the line.
- Sky Explorer: Open starts from an upload or a survey field (Settings RA/Dec/FOV/pixels, default Trifid); survey-as-primary uses independent per-tile loading (center preview→center refine before neighbors, then neighbor preview→detail, viewport-prioritized, non-blocking workers, per-tile STF that does not change when neighbors load, loaded tiles kept, no-data shown as hatch not black), Comparison on a survey primary uses the same center-first tiled wipe across the full mosaic (upload-image Comparison still uses the viewport overlay wipe), and Explore catalogs the full mosaic with annotations that are not clipped at tile edges.
- Sky Explorer: survey image downloads are kept only for the current session and are deleted when the app closes (and cleared again on the next startup if a previous exit left leftovers).
- About and README credits: point more info to the GitHub project, credit Ogetay and Ege Palaz with updated links, and mark the build as Alpha build only.

## 0.1.1-alpha.6

- Differential Photometry: folder scans ignore JPG/PNG files and tell you how many of each type were excluded as unsuitable.
- Deep Stack: presets save overlay and measurement geometry as relative canvas fractions (crop as source-image fractions), so shared presets keep the same look across different image sizes and crop ratios.
- Deep Stack: plot Moon Illumination (%) and Moonlight Impact (relative score with Low/Moderate/High/Severe) per frame from DATE-OBS, observing site, and target WCS via the Krisciunas–Schaefer model; informational only—stack weights still use measured background and noise.
- Differential Photometry: workflow Close becomes Terminate while running and decisively stops the job—the top progress bar halts, the Work Log records the termination, and stale progress from the cancelled run is ignored; quitting the app force-stops leftover background workers.
- Sky Explorer: query HASH planetary nebulae live from VizieR V/163, with status, size, morphology, and spectrum flags in the inspector (CDS catalogue access only; no HASH website scraping or redistributed dump).
- Sky Explorer: Explore queries only the selected object types, and selecting additional types after Explore turns the button into Update so only the newly selected types are fetched.
- Sky Explorer: Mag Limit and Collage move to a Tools menu; Estimate magnitude limit opens a dialog for ladder marking or automatic SNR-based limit finding, with bin size, stars/bin, and mark-only-faintest options.
- Mode switching clears the previous mode's loaded work by default to free memory; enable Keep mode memory when switching in Settings → General to preserve it.
- Themes: add Crimson and Blood Moon, two red/black dark presets available from Settings → Theme.
- Trajectory View: Meteor Stream overlay for known shower parents with numeric density, trail thickness, soft comet-colored line Glow (bright on-path, fades outward), even path-length debris, and playback-synced drift (1× = gentle default pace).
- Asteroid/Comet: remove center circle/dot markers and the Show Markers toggle; Target Marker shows one selected-object style (square aim default, circle, corner brackets, or open crosshair).

## 0.1.1-alpha.5

- Trajectory View starfield and zoom: background stars stay far outside the scene as a true backdrop, and wheel zoom is clamped with a higher zoom-out limit for distant orbits like Pluto.
- Trajectory View Save: Save button next to Lookup exports stills (PNG/JPG) or animations (GIF/MP4), optionally including the info panel beside the 3D view, with duration/frame/size estimates before export.
- Trajectory View Sky Track: a trajectory-centered azimuthal map initially fits all visible colored tracks, while zooming out reveals the complete sky, bright-star labels, and constellation figures. Adaptive Hermite trajectory sampling keeps live playback markers on curved and retrograde paths, with observation markers and click-to-seek synchronized across objects.
- Trajectory View info panels: the four charts default to top-down, Sky Track, magnitude, then distance; each can be resized independently, and Settings lets you reorder them or reset the layout. Data is optional (add/remove from Settings). Sky Track no longer uses a tab bar, drops stacked frame labels, and fits its field to the panel aspect so wide layouts are not mostly empty.
- Trajectory View Sky Track settings: gear menu options for Bayer designation labels (a Ori, z Oph, …), star density, angular draw radius, constellation figures, Fit Trajectory, and Entire Sky.
- Trajectory View opens promptly: the loading dialog appears immediately, and prep no longer re-reads FITS for every detected object before querying Horizons.
- Trajectory View without an observation: available in Asteroid/Comet Detection mode with no selected object, opens Earth-only with zero targets, and Span includes Custom start/end dates that soft-fade path edges like the padded presets.
- Asteroid/Comet action buttons: reordered to Trajectory View, Synthetic Track, Discover, Plots, with a clearer enabled-state hover affordance.
- Startup update check: packaged builds check for updates shortly after launch and only prompt when a newer version is available.

## 0.1.1-alpha.4

- First Velopack-based update packaging with delta hotfixes and the legacy Inno → Velopack migration bootstrap.
- Automatic update checks on packaged-app startup (silent when current or offline).
