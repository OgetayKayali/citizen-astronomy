# Citizen Astronomy (CAst)



Citizen Astronomy (CAst) is a Windows-first desktop application for turning folders of astrophotography images into inspectable light curves, measurement tables, and annotated image exports.



It is designed for users who already capture FITS or XISF images and want a guided desktop workflow for known targets rather than a script-heavy reduction pipeline.



## What Changed Recently



If you last looked at the app before the recent UI and workflow updates, these are the biggest changes to know first:



- the old always-visible Browser pane is gone in differential-photometry mode

- scans now open a `Loaded Results` dialog where you choose the active object

- processing starts from `Generate` in the Workspace strip, which also exposes `CDS`, `Simbad`, `Gaia`, and `VSX`

- the top menu bar now has a direct `Calibrate` action instead of the old `Analysis` menu, opening a staged calibration pipeline for science frames, bias/dark/flat masters, and optional WCS alignment of calibrated outputs

- the Measurements table is now loaded on demand with `Measure` instead of rebuilding automatically after every refresh

- Source Results now exposes `Pull Period`, `Calculate Period`, `Find Better Fit`, `Discover`, and `Increase SNR` / `Reset SNR`, and Discover now uses the shared `Settings > General > Photometry Workers` limit for its per-frame measurement phases

- Light Curve filtering has its own dialog plus an enable toggle in the toolbar

- Differential `Annotated Image` now uses one compact toolbar row, keeps overlay and manual-tracking toggles in `Settings > Light Curve`, and right-anchors a narrower aperture-preset/editor cluster so the stretch and display controls stay visually separate

- `Fit Period` now writes before-and-after values to the Work Log

- asteroid/comet `Discover` now also reads a dedicated `Settings > Open Settings > Asteroid/Comet > Discovery Advanced` group where you can keep the whole subgroup together or split it into overlapping frame batches, reuse the shared `Recover/Discover Workers` limit there, optionally bin temporary working frames at `2x2`, `3x3`, or `4x4`, reject residual detections below or above chosen local-SNR bounds before tracklet linking, tune the minimum linked-frame count plus the point-detector threshold/FWHM, cap how many residuals are kept per frame, adjust the edge margin, switch between point-only, streak-only, or hybrid point-plus-streak residual detection with configurable streak area and elongation thresholds, set separate linear-motion deflection RMS limits for stronger `Potential Discoveries` versus review-only `Borderline Review` tracklets, and optionally enable a final synthetic-track sweep with configurable max motion, velocity-grid spacing, angle spacing, and stacked-SNR thresholds for either the whole detailed group or each overlapping Discover batch; pressing `Discover` now opens a pipeline popup so the run-time batching/binning/alignment choices can be adjusted for the current run, including a faster first-pass scan that can retry automatically with the detailed full-resolution search if nothing is found

- HR mode now defaults the plot to `Gaia BP-RP` versus `Gaia Absolute G Magnitude`, adds physical secondary scales with color temperature on the bottom axis and decade-scaled luminosity on the left axis while moving the raw Gaia axes to the top and right when those conversions are meaningful, keeps the source-image preview in color for RGB inputs, shows a `Proper Motion` column in the HR Source Results table, adds a right-click `Copy Name` action for HR source rows, prefers exact-object catalog pages for the HR browser buttons by using SIMBAD identifier lookups when a real object name is known and CDS/VizieR exact searches for Gaia DR3 fallback cases, lets you edit the plot title directly, adds temperature and luminosity values to the HR plot popup, and now includes a beginner-safe `Find Motion Group` workflow that uses a saved detection preset by default while an `Advanced...` dialog exposes optional expert controls for backend selection, strictness, parallax handling, HR cleanup, and automatic `Only Group` filtering. The workflow still uses Gaia proper motion plus parallax when appropriate, can use either the built-in lightweight clustering path or `Sklearn DBSCAN`, highlights candidate stars across the plot, Source Results table, and motion overlays, and keeps the HR plot and scientific export marking highlighted members distinctly. It still supports both current-view snapshot export and scientific-style HR export while keeping fixed or brightness-scaled plot markers, Gaia-G magnitude range filtering, a Settings-based `Require Parallax` toggle, configurable selected-point circle color/opacity/size-factor controls, optional class-guide lines and the age guide for the Gaia BP-RP plus Gaia Absolute G educational HR view, plot-point selection popups, Gaia proper-motion overlays on the HR Source Image tab that follow the plotted ROI subset and support Ctrl + left-click add/remove toggles directly from the image, a left-side Source Results tab that keeps the Work Log attached beneath the table, stable high-density plot downsampling, automatic surfacing of currently plotted rows after `Update Diagram`, manual add/remove plot overrides from HR Source Results, and a Source Results option to pin selected stars to the top

- a fourth `Transient Finder` mode now accepts a folder of repeated sky images with an HR-style `Open` then `Search` primary action, checks each supported FITS/XISF frame for usable WCS, submits unsolved frames to astrometry.net when an API key is configured, keeps the solved FITS outputs in the transient WCS cache, queries one magnitude-limited Gaia DR3 comparison catalog for the solved sequence instead of pulling the full deep catalog for every frame, caps crowded wide-field detections to the strongest point sources, screens grouped detections for real frame-to-frame aperture and peak variability so static stars and seeing artifacts are rejected, and reports variable candidates with sky coordinates, frame counts, SNR summaries, nearest-Gaia separation, per-frame detections, and a right-side image panel with center/blink/export review controls

- a fifth `Sky Explorer` mode now opens a single FITS or XISF image, checks for usable WCS and falls back to astrometry.net when needed, computes the exact solved footprint from the image center and corners, lets you choose object classes from a left-side `Simple` or `Advanced` object-type panel before exploring, queries the relevant field content from SIMBAD, NGC/IC deep-sky catalogs, Gaia or VSX-style catalogs, and known solar-system services, and presents the result as a filterable overlay-rich image view with a searchable results table, footprint summary, and work-log style narrative aimed at educational field identification

- Sky View's high-detail Milky Way path now validates against a manifest-backed NASA SVS 32k tiled pyramid, and production tile generation now defaults to `tiff_lzw` while keeping `png` and `tiff_deflate` available as explicit supported output formats

- the `Asteroid/Comet Detection` mode now opens either a single solved image or a folder of solved subframes, keeps an always-available `Group` selector with an `All` option plus per-filter/per-exposure subsets so the `Frame` list and blink playback can switch between mixed sequence types live, keeps group changes responsive by reusing cached frame data instead of forcing immediate live remeasurement, now uses a single compact top action row with clearly styled `Generate`, `Estimate`, `Align`, `Recover Known`, and `Discover` buttons plus compact `Group` and `Frame` selectors, gives the secondary workflow buttons the same height as `Generate`, dims inactive buttons explicitly when they are unavailable, removes the redundant image-top metadata strip and left-side summary sentence, hides the old inline status text above `Predicted Objects`, merges the old view and playback strips into one denser image-control row with blink controls anchored on the right beside `Hide Info`, makes `Work Log` the default lower tab ahead of `Inspector` with a flatter 2D tab treatment, adds a visible checked state for `Blink` and `Track Object`, moves `Center Object` and `Track Object` into the image-view toolbar beside a preset blink-cadence selector, replaces the live inline asteroid/comet levels widget with an `Adjust Levels` button that opens a smaller downsampled preview dialog for black, midtone, and white point edits and only applies those changes to the main image after confirmation while keeping the stretch dropdown and invert toggle in the toolbar, and adds a draggable split between the `Predicted Objects` table and the lower panel while storing the asteroid/comet detection magnitude limit in `Settings > Open Settings > Asteroid/Comet` instead of the live toolbar, removes the in-panel observatory-code field, reads observation metadata from each frame header, queries known solar-system objects predicted in the selected field with a more forgiving SkyBoT uncertainty window and split-designation handling, now bypasses astroquery's hard 120-arcsec SkyBoT uncertainty clamp by issuing the VOTable request directly and supplements missing interstellar visitors through targeted IMCCE Miriade ephemeris lookups with a JPL Horizons backup when IMCCE times out so faint or high-uncertainty comets such as `3I/ATLAS` are less likely to be dropped before image confirmation, refreshes targeted interstellar rows such as a generic SkyBoT `3I` result with the authoritative `3I/ATLAS` Horizons identity and magnitude when that extra lookup is available, supports `Center Object`, inline overlay `Blink`, `Export Blink`, `Hide Info`, and `Track Object` image controls, can reproject an already plate-solved multi-frame sequence onto the current reference frame's WCS and save aligned FITS copies into an `aligned` subfolder without touching the originals, now makes both `Recover Known` and `Discover` ask in a pop-up whether the current subgroup is already aligned so they can either skip reprojection and search residuals directly or align first, keeps `Recover Known` available for the dedicated benchmark/export workflow, and now makes `Discover` run the Gaia-based visible-limit estimate automatically, search without discarding catalog-known movers, separate recovered known objects from missed ones and retained unmatched tracklets, and report how many known objects fell within the estimated limit versus how many of those were actually recovered before it splits retained tracklets into stronger `Potential Discoveries` and a review-only `Borderline Review` bucket using configurable linear-motion deflection RMS thresholds; canceling either progress window now requests cancellation of the running background task instead of merely closing the dialog, adds a `Discovery Advanced` group under `Settings > Open Settings > Asteroid/Comet` so moving-object frame preparation and residual scanning can reuse the `Recover/Discover Workers` limit there while optional residual-SNR min/max filters, overlapping frames-per-batch windows, and the potential-versus-review deflection RMS thresholds narrow or split the residual search before tracklet linking, keeps `Track Object` centered on the selected prediction in unaligned sequences by recomputing each frame's predicted coordinates from that frame's own WCS and timestamp even while blink playback is suppressing expensive live peak matching, shows a thinner accent-colored inline progress bar above the asteroid results while background asteroid tasks run, uses a softer theme-blended highlight for likely visible rows, lets you resize every `Predicted Objects` table column manually, keeps `Status` only in the right-side inspector instead of the results table, labels the object-brightness column as `Pred Mag` instead of implying everything is a V-band magnitude, enables numeric sorting for `Pred Mag`, `Confidence`, `Motion`, and `Exposure Motion`, can estimate the dimmest Gaia G star in the current frame to update the saved asteroid/comet detection magnitude limit with a quick 0.5-mag sampled check while keeping that visible-limit estimate explicitly labeled as Gaia based, lets `Settings > Open Settings > Asteroid/Comet` tune the detection magnitude limit itself plus the visible-limit estimate SNR threshold, start magnitude, sample size, required visible-star count, optional faint-star annotations, `Visuals` toggles for labels, prediction crosshairs, selected-object highlighting, and invert-aware annotation colors, plus `Export` controls for GIF resolution and looping, writes detailed visible-limit search progress into the Work Log while it probes each magnitude bin, adds an exposure-motion column that estimates trail length in pixels and flags likely point sources versus tails, preloads fully rendered blink frames in the background before stepping through the sequence, now defaults blink review to `0.05 s`, can export the current blink viewport as an animated GIF for educational walkthroughs with configurable scaling and looping, adds custom-theme controls for the selected asteroid/comet overlay circle color, line color, line width, label size, and label color, expands the image inspector with merged observation date/time plus Moon illumination, a combined filter/exposure row, field scale, and setup details, and adds a dedicated `Setup` settings tab for telescope, camera, location, Bortle class, and derived pixel scale

- asteroid/comet mode now also keeps the latest matching `Discover` result in temporary session state so closing the review window is no longer destructive: clicking `Discover` again for the same subgroup offers `Restore Last Result` versus `Run New Scan`, reliably reopens the stored review dialog for that same result, keeps the main `Predicted Objects` table mixing generated known objects with temporary `Candidate ...` discovery rows for the stronger `Potential Discoveries` bucket only, while borderline `Borderline Review` tracklets remain review-only inside the Discover dialog, and those stronger candidate rows can be selected for centering, trajectory review, marking, and `Synthetic Track` directly from the main window or the Discover review dialog, with candidate synthetic tracking and main-image candidate marking now projecting discovery-linked sky coordinates back into each source frame instead of reusing aligned-search pixels, with batched Discover runs suppressing duplicate candidate tracklets that also match a recovered known object, and with the candidate-strength display relabeled as a residual score rather than a normal photometric SNR and accompanied by linear-motion deflection RMS and max-deflection readouts

- asteroid/comet mode now also offers `Synthetic Track` for the selected prediction or temporary discovery candidate, which shifts and stacks the current frame group on that object's predicted motion, opens a preview dialog with stacked SNR, center offset, stacking motion rate, stacking angle, and per-frame inclusion details, reads `Settings > Asteroid/Comet > Synthetic Tracking` for crop radius, combine mode, and whether mixed `All` groups are allowed, and now lets you `Ctrl` + click the preview to override a misidentified measured peak and recompute the stacked metrics with a `Reset to Auto` fallback

- asteroid/comet blink playback now uses a precise timer, skips per-frame `Predicted Objects` exposure-motion table churn while blink is active, and advances one frame per timer tick instead of visibly skipping ahead after `Discover` adds candidate rows, while `Synthetic Track` no longer fails when the current frame is using prediction-only measurements without a live local-match FWHM value



## What It Does



Citizen Astronomy (CAst) helps you:



- open a workspace, `Files` folder, or a single object folder

- automatically scan supported image files and read useful metadata

- choose the active object from a Loaded Results summary dialog after scanning

- validate existing WCS or optionally solve unsolved images through astrometry.net

- calibrate image folders with bias, dark, and flat frames, optionally aligning the calibrated FITS outputs afterward

- query known field objects from VSX, Gaia DR3, and the NASA Exoplanet Archive

- query known asteroids and comets already inside a solved image footprint and score likely visible matches against the image itself

- explore a single solved sky image with `Sky Explorer`, including automatic WCS fallback solving, center/corner footprint reporting, and multi-layer overlays for deep-sky objects, stars, variables, and known solar-system bodies

- search repeated image folders for stationary transient candidates by solving missing WCS through astrometry.net, using a shared magnitude-limited Gaia comparison catalog, grouping point-source detections by sky position, and retaining only candidates with significant frame-to-frame variability evidence

- align already plate-solved asteroid/comet subframes onto one reference WCS grid and write derived FITS copies into an `aligned` folder for motion inspection

- open a trajectory window for a selected known asteroid/comet directly from the main `Predicted Objects` table after `Generate`, either with the `Trajectory` button or by double-clicking the selected row, to inspect predicted and measured motion across the current frame group with a darker presentation, endpoint-only frame labels, balanced plot geometry, image-plane and sky-plane residual connectors, and match-offset error bars on measured points

- open a `3D View` window for one or multiple selected known asteroids/comets directly from the main `Predicted Objects` table after `Generate`, which shows an immediate `3D View is loading...` progress message while the JPL Horizons context is being fetched, opens maximized in windowed full-screen style, queries JPL Horizons heliocentric state vectors for the selected set plus Earth, preserves that full selected set when you switch span presets such as `1y` or `5y`, keeps the other generated objects from the current run available in the `Objects` menu for later add/remove actions, now keeps those non-selected menu targets available even when group-wide live measurements are missing by falling back to the current frame prediction, renders the large left pane as a GPU-backed OpenGL scene, keeps the scientific plots and frame table in a resizable right-side splitter so the plots can take most of that column, overlays the additional selected targets in both the 3D and top-down views while the first selected target continues to drive the detailed table/playback readout, labels the Sun, planets, Earth, and selected objects directly in the view, keeps those labels anchored more tightly when objects are added or removed, moves the label font/size/bold/italic controls plus separate asteroid/comet color pickers under a `Settings` popup beside playback, adds an `Objects` dropdown with checkboxes for both the initially selected targets and the other generated objects from the current run so more can be added later without reopening the window, keeps the heliocentric top-down plot focused on the selected object trajectories while using the full subplot width, moves the distance and Horizons literature-magnitude time-series onto pyqtgraph-backed panels for faster interaction, keeps live time-series hover guides with a vertical cursor line, line-intersection markers, per-series value callouts, and an on-plot hovered `YYYY-MM-DD` date label anchored inside the plot near the x-axis without the old scaled `UTC (x1e+09)` axis text, keeps the current playback markers on those time-series, keeps manual overview/top-down camera adjustments stable during playback, and adds a `Side View` camera option alongside the existing overview, top-down, and follow modes

- review a solved asteroid/comet subgroup with `Discover`, which now runs the Gaia-based visible-limit estimate automatically, opens a run-options popup for alignment mode, temporary working-cache usage, working-image binning, residual local-SNR thresholds, overlapping batch windows, and an optional fast-scan-then-detailed retry path, then searches movers without discarding catalog-known objects so the results dialog can separate recovered known objects, missed known objects, stronger `Potential Discoveries`, and review-only `Borderline Review` tracklets while also reporting how many generated known objects were inside the estimated visible limit and how many of those were recovered; Discover can now also run the optional synthetic sweep after normal residual linking either once across the detailed whole-group stage or separately inside each overlapping batch window, so faint movers that never linked cleanly frame-to-frame still get one last velocity-grid pass before benchmarking and review, with subpixel stack shifts plus `Discovery Advanced` controls for a full 360-degree sweep versus main-belt-direction focus, an explicit main-belt focus-width window, an optional `synthetic_track` FITS dump of every tested stack, and multi-core vector processing that follows the existing `Search Workers` limit. Batched runs still suppress duplicate tracklets that match the same recovered known object, so one mover is not shown as both a recovery and a discovery candidate, and a post-link linear-motion screen suppresses higher-deflection junk tracklets before review while exposing configurable deflection RMS thresholds in `Discovery Advanced`. That same Discover review dialog now also exposes `Export Benchmark...`, `Export Unmatched...`, and `Export Summary...` CSV actions plus `Mark Selected on Main Image`, `Mark All on Main Image`, and `Synthetic Track...`, keeps the latest matching result temporarily restorable on the next Discover click for that subgroup, and mirrors only the stronger potential-discovery candidates back into the main `Predicted Objects` table as temporary `Candidate ...` rows for continued review after the dialog closes, including direct main-window `Trajectory` access for those candidate rows

- benchmark a solved asteroid/comet subgroup with `Recover Known`, which detects movers first and then reports which likely visible catalog objects were recovered directly from the image data, which were missed, and which residual tracklets remain unmatched, with `Export Benchmark...`, `Export Unmatched...`, and `Export Summary...` CSV actions for benchmark tables, residual candidate tables, and manuscript-style recovery summaries

- open a separate `Trajectory...` window from the asteroid/comet recovery and discovery review dialogs to inspect each detected tracklet in image space, sky coordinates, and time-series form with the same darker scientific presentation now used by the main known-object trajectory view

- export the current asteroid/comet blink viewport as an animated GIF for educational playback

- preview candidate variable targets before processing

- perform aperture photometry with fixed or FWHM-scaled apertures

- compute differential magnitudes against nearby comparison stars

- inspect file results, source summaries, on-demand measurement tables, annotated images, and light curves in one UI

- refine processed sources with `Pull Period`, `Calculate Period`, `Find Better Fit`, `Discover`, `Increase SNR`, and `Reset SNR`, with Discover reusing the shared General photometry worker count during its per-frame measurement passes

- overlay polynomial or periodic fit curves for visual interpretation

- export CSV, JSON, PNG plots, and annotated image products

- persist workspace settings, custom themes, and the last-used app theme



## Current Workflow At A Glance



The current UI is menu-driven and mode-aware.



1. Launch the program.

2. Pick the mode from the `Mode` menu.

3. In `Differential Photometry`, use `File > Open Folder` and select a project root, `Files` folder, or one object folder.

4. In `HR Diagram`, `Asteroid/Comet Detection`, or `Sky Explorer`, use `File > Open File` and select a solved FITS or XISF image.

5. Differential-photometry scans open a `Loaded Results` chooser so you can select the active object.

6. Adjust settings if needed.

7. Click `Generate` in Differential, HR, or Asteroid/Comet mode, `Search` in Transient Finder, or `Explore` in Sky Explorer.

8. Inspect the results in the mode-specific tables, logs, and annotated image views.

9. In Differential mode, use `Measure` to load the currently filtered Measurements rows only when you want the full per-row table.

10. Use the Source Results actions for period fitting, comparison-star refinement, Gaia-candidate discovery, and conservative SNR binning where available.

11. Export a full report bundle, plots, or annotated images from the `File` menu.

12. Differential-photometry report bundles also include an AAVSO Extended text file plus a preflight JSON review file for accepted variable-star measurements.



## Supported Inputs



- `.fit`

- `.fits`

- `.xisf`



Recommended layout:



```text

Photometry/

  Files/

    ObjectA/

      frame_001.fits

      frame_002.fits

    ObjectB/

      frame_001.fits

```



You can also point the app directly at a single object folder.



## Main Features



- automatic scan on folder open

- Loaded Results popup for choosing the active object after scan, with an accented `Use Selected Object` action

- compact one-line Workspace header with a prominent `Generate` action, an inline pre-run `Filter` selector that defaults to `All`, and quick `CDS`, `Simbad`, `Gaia`, and `VSX` links with clearer hover and disabled states

- object-level processing workflow with preview selection

- preview default-selection controls plus designation-family filters

- variable-star designation-family filters

- preview caps by object count and faint-limit magnitude

- adaptive aperture sizing from estimated frame FWHM

- manual and automatic object modes

- alignment mode for already aligned stacks

- Source Results actions grouped in one row with `Measure`, `Pull Period`, and `Calculate Period` on the left plus accent-outlined `Find Better Fit`, `Discover`, and `Increase SNR` / `Reset SNR` actions on the right

- Light Curve filtering workflow with a saved filter dialog and enable toggle

- on-demand Measurements loading through the `Measure` button

- a grouped left-side `Results` section with source-first result tabs, a `Type` column in `Source Results` that replaces the old visible source-type filter strip, one ordered analysis-action row, and a draggable split above the lower `Work Log` / `Summary` / `QA Details` stack, with flatter differential-mode tab styling and clearer primary-versus-secondary action emphasis

- an apply-only `Adjust Levels` workflow for the Differential Photometry annotated image view, replacing the old live brightness/contrast controls with black, midtone, and white-point adjustment in a small preview dialog, a single `Reset` action that restores both the view and display settings, one compact image toolbar row with a narrower right-anchored aperture-editor cluster and no visible frame chooser, and a `Settings > Light Curve` submenu that now holds `Equatorial Grid`, `Mark Saturated`, `Frame Margin`, `Keep Comparison Stars`, and recenter controls

- interactive annotated-image overlays

- a dedicated `Asteroid/Comet Detection` mode for single-image or subframe-sequence known-object lookup, confidence scoring, an always-available `Group` selector with `All` plus per-filter/per-exposure subsets for mixed folders, faster cached group/frame switching, a compact top action row with clearly styled `Generate`, `Estimate`, `Align`, `Recover Known`, and `Discover` buttons plus compact `Group` and `Frame` selectors, persistent primary emphasis on `Generate`, matching-height secondary workflow buttons with explicit dim disabled states, a selection-aware action bar above `Predicted Objects` for `Trajectory`, `3D View`, and synthetic tracking, a draggable split between the `Predicted Objects` table and a lower `Work Log` / `Inspector` tab stack with `Work Log` shown first, a flatter 2D tab treatment for that lower panel, one denser image-control row over the overlay with an `Adjust Levels` dialog launcher, visible checked states for `Blink` and `Track Object`, center/track controls beside preset blink cadence choices, and `Hide Info` anchored on the right, user-resizable `Predicted Objects` columns, results-table status removal in favor of the right-side inspector, cached asteroid/comet splitter widths for both the table-versus-lower-panel layout and the image-versus-results layout, numeric sorting for the asteroid brightness/confidence/motion columns, a saved asteroid/comet detection magnitude limit in `Open Settings > Asteroid/Comet` instead of the live toolbar, a more forgiving comet query path for high-uncertainty/interstellar objects including split `3I`/name rows, a direct SkyBoT VOTable request path that preserves the intended relaxed uncertainty filter instead of astroquery's built-in 120-arcsec clamp, a targeted IMCCE Miriade fallback for known interstellar objects with a JPL Horizons backup when IMCCE times out, an extra targeted-refresh pass that upgrades ambiguous interstellar rows such as `3I` to the authoritative `3I/ATLAS` name and magnitude when Horizons is available, pre-rendered blink-style frame inspection with preset cadence choices, inline overlay blink playback controls, animated-GIF export of the current blink viewport with configurable scale and looping, a hideable right-side info panel, direct `Trajectory` access from the main `Predicted Objects` table after `Generate` so a selected known object can be opened immediately in a separate predicted-versus-measured motion window for the current frame group with endpoint-only frame labels, darker scientific styling, balanced plot geometry, image-plane and sky-plane residual connectors, and measured-point match-offset error bars, direct `3D View` access from that same table so one or multiple selected known objects can open a shared maximized windowed heliocentric JPL Horizons state-vector view around the current frame group with a GPU-backed OpenGL left pane, right-column scientific plots plus the frame table, frame playback slider, play/pause controls, speed presets, camera modes such as overview, top-down, and follow views, span presets from the default local window out to `+/-90d`, `+/-180d`, `1y`, and `5y` without dropping the extra selected targets, optional major-planet overlays, optional orbital-period labels for the shown bodies, direct labels for the Sun, planets, Earth, and selected objects, in-dialog label font/size/bold/italic controls, separate asteroid/comet color controls, per-target visibility toggles, extra selected-target orbit overlays in both the 3D scene and top-down plot, cleaned date-formatted pyqtgraph time axes with `YYYY-MM-DD` hover readouts, plus opacity-faded orbit trails that make the observed segment stand out against the distant arc, WCS-based alignment that reprojects already solved multi-frame folders into an `aligned` subfolder on the current reference grid while preserving the source files, a known-object `Recover Known` workflow that detects movers first and then classifies the results into recovered likely visible catalog objects, missed likely visible catalog objects, and still-unmatched candidates, plus `Export Benchmark...`, `Export Unmatched...`, `Export Summary...`, and `Trajectory...` actions for benchmark rows, residual-candidate tables, concise paper-ready recovery summaries, and per-tracklet image-plane/sky-plane motion inspection, an unmatched-moving-candidate `Discover` workflow that now opens a pipeline popup for alignment mode, fast versus detailed search choice, optional automatic detailed retry, working-image binning, temporary prepared-frame cache usage, residual-SNR filtering, and overlapping frames-per-batch windows before it links residual detections into motion-consistent tracklets, opens a ranked review dialog, and now includes matching `Export Benchmark...`, `Export Unmatched...`, `Export Summary...`, `Trajectory...`, `Mark Selected on Main Image`, and `Mark All on Main Image` actions there as well, uses a wider default review window, replaces the old long text block with a compact summary strip for the visible limit, in-limit count, recovery count, and potential discoveries, draws higher-contrast outlined markers that stay readable on both normal and inverted images, lets `Open Settings > Asteroid/Comet > Visuals` toggle generated known-object overlays separately from marked potential discoveries on the main image, adds split custom-theme styling for the selected asteroid/comet overlay versus all other generated objects, while stale crash-leftover Discover cache folders are removed again on the next startup so those temporary working files do not accumulate on disk, prediction-only per-frame tracking so unaligned blink sequences still keep the selected object centered from each frame's own WCS, a quick Gaia G visible-limit estimator for updating the saved detection magnitude limit, clearer asteroid/comet `Pred Mag` labeling so catalog brightness is not mistaken for a strict V-band value, configurable estimate and export controls in `Open Settings > Asteroid/Comet`, optional faint-star estimate annotations, an exposure-motion tail/point-source note, richer frame/setup inspector readouts including Moon illumination and a combined filter/exposure row, invert-aware annotation colors, and a `Setup` settings tab that stores telescope, camera, focal length, aperture, focal ratio, pixel size, derived pixel scale, location, and Bortle class

- selected-object synthetic tracking that shifts and stacks the current asteroid/comet frame group on the predicted motion, previews the resulting object-centered stack in a dialog with stacking rate and angle readouts, uses configurable `Synthetic Tracking` settings for crop radius, mean versus sigma-clipped mean stacking, and mixed-`All`-group safeguards, supports both generated known objects and temporary discovery candidates, and supports `Ctrl` + click manual peak correction with live SNR/offset recomputation plus `Reset to Auto`

- HR Source Image selection overlays for the current HR plot, Source Results selection, or active `Find Motion Group` members, using a single consistent HR selection circle on the image, plus optional Gaia proper-motion vectors for the currently plotted subset, with vector color and width stored in Settings under `HR Diagram`, a `Save Image...` action for the current annotated Source Image view, and an HR-only `File > Export` bundle that writes the annotated Source Image, scientific HR diagram, and matching science-table CSV into a named export folder

- HR plot presentation settings in the menu bar under `Settings > HR Diagram`, including `Require Parallax`, color saturation, point opacity, selected-point circle color/opacity/size factor, and fixed versus brightness-scaled marker sizing

- HR apparent-magnitude min/max filtering, optional class-guide line and age-guide overlays for canonical Gaia BP-RP versus measured absolute-magnitude-proxy plots, a clean Gaia-only absolute-G Y-axis alongside the measured absolute-magnitude proxy, selection popups with cached SIMBAD spectral-type lookup when available, manual Source Results add/remove plot overrides that can override the current ROI, and Shift-drag ROI drafting while plain drag and wheel keep their default pan/zoom behavior

- interactive light-curve panel with JD or calendar-time x-axis

- polynomial and periodic fit overlays

- period estimation with Lomb-Scargle estimation

- Work Log period-fit messages that record the displayed period before and after `Fit Period`

- science-ready report exports with accepted and rejected observations plus reduction manifests

- canonical accepted/rejected observation JSON exports for downstream tooling

- built-in themes, custom theme editing, theme import/export, themed plot exports, and custom asteroid/comet selected-overlay styling inside `Customize Theme`

- naive `DATE-OBS` header values and filename timestamps are interpreted in the configured `Image Timestamp Timezone`; named zones such as `America/New_York` follow DST automatically, explicit header offsets still take precedence, the image header remains authoritative when `DATE-OBS` is present, filename timestamps are only used as a fallback when the header timestamp is missing or unreadable, and XISF files now prefer explicit `Observation:Time:Start` / `Observation:Time:End` UTC properties over naive FITS-style `DATE-OBS` strings when PixInsight wrote both forms



## Installation



Install into your Python environment:



```powershell

python -m pip install -e .

```



## Optional Plate Solving Setup



If your images are not already plate solved, configure an astrometry.net API key.



PowerShell example:



```powershell

$env:CITIZEN_PHOTOMETRY_ASTROMETRY_API_KEY = "your-api-key"

```



You can also store the API key in the app Settings dialog.



The Settings dialog stores science export metadata such as observer code, observer name, filter system, time standard, transformed-data flag, and reduction notes. It also includes a dedicated `Setup` tab for site, telescope, camera, focal length, aperture, focal ratio, pixel size, derived pixel scale, and Bortle class. Those values are written into the reduction manifest when you use `File > Export Report`, and the setup details are reused by the asteroid/comet image inspector.



Live HR plot and Source Image presentation controls are available from both the menu bar under `Settings > HR Diagram` and the `Open Settings` dialog's `HR Diagram` tab.



## Launch



```powershell

python -m photometry_app.main

```



You can also launch the installed console entry point:



```powershell

citizen-photometry

```



## Developer Setup



For local development on this repository:



1. Create and activate a Python 3.11+ environment.

2. Install the project in editable mode.

3. Run the test suite.

4. Start the desktop app or run the demo smoke test.



Suggested commands:



```powershell

python -m pip install -e .

python -m unittest discover -s tests -p "test_*.py"

python -m photometry_app.main

python scripts\run_demo_smoke_test.py

```



Useful developer scripts:



- `python scripts\generate_demo_dataset.py` to regenerate the demo FITS dataset

- `python scripts\run_demo_smoke_test.py` to process the demo object and export a smoke-test report

- `python scripts\profile_discover_run.py <folder> --assume-aligned --profile-out discover.prof` to benchmark asteroid/comet `Discover` on a solved frame folder with the current app settings, including a timestamped progress timeline and top cumulative `cProfile` output for the Discover stage



Current repository notes:



- tests are written with `unittest`

- the demo smoke test writes output under `Exports/DemoOrion_smoke/`

- workspace settings are stored in `.photometry-settings.json`

- cached catalogs and run history live under `.photometry-cache/`



## Outputs



Depending on the export action you choose, the program can write:



- measurements CSV

- light curves CSV

- run summary JSON

- accepted observations CSV for science-ready use

- accepted observations JSON in a stable canonical schema

- rejected observations CSV for review and audit

- rejected observations JSON in the same canonical schema

- reference-star manifest CSV

- reduction manifest JSON

- provenance manifest JSON with calibration-state and file-level reduction context

- AAVSO Extended differential text export

- AAVSO preflight JSON with warning counts and STD/DIF row summary

- themed light-curve PNG plots

- annotated image PNG files

- animated GIF blink recordings from the asteroid/comet image view



The AAVSO export now uses `MTYPE=STD` whenever the report can derive a comparison-star zero point from the selected references, including CV-style clear-filter reductions with a V-band zero point. The Settings `transformed` flag now only controls the exported `TRANS=YES/NO` value; rows fall back to `MTYPE=DIF` only when no usable zero point is available.



If the source headers do not include airmass, the exporter now falls back to a geometric estimate using the measurement RA/Dec, observation timestamp, and the observing-site latitude, longitude, and elevation stored in Settings.



The accepted and rejected observation JSON files use a shared `science-observation-v3` schema so external tools can consume the report bundle without reverse-engineering the CSV column order. The schema now carries per-row aperture and QA provenance plus explicit calibration state, zero-point context, and an AAVSO-oriented candidate measurement type.



The science observation rows now also include explicit check-star diagnostics when a check measurement is available in the same frame: check source name, reference magnitude, calibrated magnitude, per-frame residual, and per-series residual RMS.



The science-ready bundle is intentionally conservative: it supports review and downstream tooling now, but full publication-grade provenance and transformation workflows are still incomplete.



The program also maintains:



- `.photometry-settings.json` for workspace settings

- `.photometry-cache/` for cached catalogs, measurements, and run history



## Themes



The app supports built-in and custom themes.



Built-in presets currently include:



- Dark

- Dracula

- Nord

- Tokyo Night

- Gruvbox

- Catppuccin

- Solarized Dark

- One Dark



You can also:



- customize colors

- import and export theme JSON files

- keep the last used theme as the startup default when a workspace has no explicit theme stored yet



## Documentation



For the full step-by-step guide and technical notes, see [DOCUMENTATION.md](DOCUMENTATION.md).



That file covers:



- opening data and processing objects

- preview selection, default-selection controls, and filtering

- Workspace-strip and Loaded Results workflow

- Source Results actions including `Find Better Fit` and `Increase SNR`

- HR Diagram workflow and reference

- developer setup and demo workflow context

- manual interpretation of outputs

- how uncertainty/error bars are calculated

- what fitting methods are used for light-curve overlays

- troubleshooting and workflow notes



For the dedicated HR Diagram guide, see [HR_DIAGRAM.md](HR_DIAGRAM.md).





