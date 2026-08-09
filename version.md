# Version history

Current app version: **0.1.1-alpha.8**

This log tracks finished fixes and features for the next published update. Group notes by mode (or App/Display). Prefer one short bullet per change—not intermediate debugging steps.

## Pending for next release (after 0.1.1-alpha.8)

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
