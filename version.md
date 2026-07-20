# Version history

Current app version: **0.1.1-alpha.5**

This log tracks finished fixes and features for the next published update. Prefer one finalized entry per change—not intermediate debugging steps. Keep each entry to one or two sentences.

## Pending for next release (after 0.1.1-alpha.5)

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
