# Updates

Version history for Citizen Astronomy (CAst), newest first. Installer releases and delta updates are published on the [Releases](https://github.com/OgetayKayali/citizen-astronomy/releases) page.

Current app version: **0.1.1-alpha.6**

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
