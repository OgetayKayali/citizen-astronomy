# Differential Photometry

## Purpose

Differential Photometry mode is the main time-series science workflow in Citizen Astronomy (CAst).

Its job is to take a folder of repeated observations of one field, identify known or manually defined targets in that field, measure those targets consistently across the frame set, compare each target against nearby comparison stars, and turn the result into inspectable light curves, measurement tables, annotated images, and export bundles.

The mode is built for guided desktop analysis rather than a script-only reduction pipeline. It combines file scanning, WCS validation or fallback solving, catalog lookup, aperture photometry, uncertainty propagation, quality analysis, interactive review, and export.

At a high level, Differential Photometry answers four questions:

1. Which sources in this field should be analyzed?
2. What did each source measure in each frame?
3. How did the source vary relative to stable nearby stars?
4. Which results are credible enough to inspect or export?

## Scope

Differential Photometry is centered on known-source time-series work.

It supports:

- Known variable-star workflows built around Gaia DR3 plus AAVSO VSX.
- Known exoplanet catalog entries in the same field.
- Manual target, comparison, and check-star definitions.
- Interactive quality review and export.
- Conservative Gaia-based candidate discovery as a Differential follow-up workflow.
- Conservative SNR-increase derivations for weak light curves.

It does not try to merge all filters into one master light curve. Light curves stay separated by filter on purpose.

## Inputs

Differential Photometry is folder-based.

Recommended layout:

```text
Photometry/
  Files/
    ObjectA/
      frame_001.fits
      frame_002.fits
    ObjectB/
      frame_001.xisf
```

The user can open:

- A workspace root.
- A Files folder.
- A single object folder.

The mode expects repeated images of the same target field. The implementation is primarily designed around FITS, .fit, .fits, and XISF inputs used in the standard scan-and-process workflow.

## Core Concepts

### Object

An object is one logical target folder under Files. The pipeline processes one object at a time.

### Solved field

A solved field is a frame with usable celestial WCS. Differential Photometry depends on WCS to map catalog coordinates into image coordinates.

### Variable source

A variable source is usually a known VSX entry, but can also be a saved manual target or check source, an explicitly selected exoplanet-host entry, or a retained Discover candidate.

### Reference star

A reference star is a Gaia star used as a local comparison source for differential photometry.

### Measurement

A measurement is one per-source, per-frame photometric record. It contains image-space position, flux, errors, instrumental and differential magnitudes, saturation and QA state, and provenance about the comparison stars used.

### Light-curve series

A light-curve series is built from all non-reference measurements for one source in one filter.

## High-Level Workflow

The Differential Photometry workflow is:

1. Open the guided workflow window.
2. Choose the image folder in the Folder step.
3. Scan files and classify their metadata and WCS state.
4. Assume one object for the selected folder and make the first object ready for Generate.
5. Choose Generate options: Light Curves are always enabled, Pull Periods and Calculate Periods default on, and Create Measurements Table defaults off.
6. Resolve WCS for each usable frame.
7. Query Gaia, VSX, and exoplanet catalog data for the representative solved field.
8. Select targets and comparison stars.
9. Measure target and reference apertures in each frame.
10. Reuse cached measurements when signatures still match.
11. Compute differential magnitudes, calibrated magnitudes, and QA flags.
12. Build per-filter light curves.
13. Run the selected follow-up steps for literature periods, calculated periods, or the Measurements table.
14. Inspect tables, annotated image overlays, simplified workflow progress, work-log notes, and plots.
15. Optionally refine with comparison optimization, candidate discovery, or SNR derivation.
16. Export a report bundle, plots, or annotated images.

## Architecture Map

The main Differential Photometry implementation is split across these modules:

- photometry_app/core/pipeline.py
  Orchestrates scan, preview, WCS resolution, catalog lookup, cache reuse, measurement, and final report creation.
- photometry_app/core/photometry.py
  Performs aperture photometry, background statistics, recentering, saturation handling, and adaptive aperture sizing.
- photometry_app/core/matching.py
  Selects reference stars, applies differential photometry, scores quality, and builds light-curve series.
- photometry_app/core/error_calculations.py
  Defines flux and magnitude uncertainty propagation helpers.
- photometry_app/core/models.py
  Defines the report, measurement, light-curve, and manual-configuration data contracts.
- photometry_app/core/exporters.py
  Writes report bundles, science exports, AAVSO-compatible outputs, plots, and annotated images.
- photometry_app/ui/main_window.py
  Builds the Differential Photometry interface and wires user actions.
- photometry_app/ui/light_curve_widget.py
  Renders interactive light curves.
- photometry_app/core/settings.py
  Persists Differential behavior, caches, manual setups, period caches, and UI defaults.

## Workspace And Scanning

When the user opens a Differential Photometry folder, the application scans the workspace automatically.

The scan phase identifies:

- Object folders.
- File metadata such as object name, filter, exposure, frame size, and timestamp.
- WCS status per file: solved, unsolved, or invalid.

The main scan output is a ScanReport containing ObjectScanSummary entries. Each object summary tracks total files plus solved, unsolved, and invalid counts.

When the scan starts from the guided workflow window, the UI no longer opens the old Loaded Results dialog. The workflow assumes the selected folder contains one object, selects the first object summary, and enables Generate inside the workflow window. Direct internal scan paths can still use the loaded-results summary behavior where needed by tests or developer tooling.

## WCS Resolution

Differential Photometry relies on WCS to project catalog coordinates into pixel positions.

The processing phase resolves a solved field for each usable frame. If no solved frame can be established for the object, catalog lookup and photometry are skipped and the report is returned with explanatory notes.

The broader project behavior is:

- Existing valid WCS is reused.
- Missing or invalid WCS can fall back to astrometry.net if configured.
- Only frames with solved fields participate in catalog-driven measurement.

The pipeline chooses a representative solved field from the resolved set and uses that field for the main catalog query.

## Catalog Inputs

Differential Photometry uses multiple catalog sources for different roles.

### Gaia DR3

Gaia DR3 provides field stars and is the main pool for reference-star selection.

### AAVSO VSX

VSX provides known variable-star candidates in the field.

### NASA Exoplanet Archive

Exoplanet entries can also be surfaced as candidate sources in the field catalog.

### External review links

The workspace row exposes CDS, Simbad, Gaia, and VSX shortcuts so the user can inspect the currently selected object or source externally.

## Target Selection

Target selection happens in two phases: preview selection and processing selection.

### Preview phase

The preview flow runs before full processing unless manual mode is already complete.

The preview logic:

1. Resolves a representative solved field.
2. Queries field catalogs.
3. Applies variable-star designation-family filters.
4. Applies preview caps such as maximum candidate count and optional preview magnitude limits.
5. Restores any previously saved explicit source selections.
6. Otherwise preselects the brightest candidates according to the current limit mode.

The preview result is a VariableSelectionPreview object that contains:

- Total variable stars found in the field.
- How many will actually be analyzed.
- Current limit mode and limit value.
- Designation filters.
- Candidate source list.
- Preselected source keys.
- Notes for the user.

### Limit modes

Two limit modes exist:

- Percentage of brightest stars.
- Absolute count of brightest stars.

These settings determine how many designation-matched variable stars are analyzed when there is no explicit saved source subset.

### Explicit source selection

If the user saved explicit source IDs for an object, those selections override the default bright-star limit behavior.

### Manual mode override

If a saved manual configuration is complete enough for processing, the preview is replaced by the manual source set and the workflow moves directly into manual Differential Photometry semantics.

## Manual Mode

Manual mode exists for cases where the user wants to define sources directly rather than depending on catalog positions.

A ManualPhotometryConfig stores:

- Object name.
- Whether the object is in AUTO or MANUAL mode.
- Reference frame name.
- Whether saved comparison stars should be preserved.
- Recenter mode.
- Maximum recenter radius.
- Whether to fall back to WCS coordinates if recentering fails.
- Saved manual sources.

Manual sources have explicit roles:

- Target.
- Comparison.
- Check.

Each saved source stores sky coordinates, reference-frame pixel coordinates, aperture radii, and role.

If manual mode is selected but incomplete, the pipeline records a note and falls back to automatic catalog-based processing.

## Reference-Star Selection

Reference-star selection is performed from Gaia stars.

The selection logic:

- Uses numeric magnitude bounds.
- Defaults to a broad allowed range of 8.0 to 16.0 mag.
- Prefers stars in a narrower preferred band of 10.0 to 13.5 mag.
- Excludes stars too close to known variable targets.
- Uses a default exclusion radius of 30 arcsec around variable coordinates.
- Selects a limited reference pool, then uses only the nearest subset per target measurement.

During full processing, the user setting nearby_reference_count controls how many nearby comparison stars can contribute to one target's differential measurement.

If manual mode is active, saved manual comparison stars replace the automatic Gaia reference selection.

## Aperture Photometry

The actual flux measurement work is performed by photometry_app/core/photometry.py.

### Frame context

For each 2D science frame, the measurement code prepares a frame context containing:

- Image array.
- WCS.
- Sigma-clipped background median.
- Sigma-clipped background standard deviation.
- Saturation threshold when available.

Non-2D inputs are rejected for Differential aperture measurement.

### Apertures and annuli

Measurements use:

- A circular source aperture.
- A circular sky annulus.

The annulus is used to estimate local background and subtract it from the source sum.

### Aperture modes

Two aperture modes exist:

- Fixed radii.
- FWHM-scaled radii.

When fixed mode is active, the configured pixel radii are used directly.

When FWHM-scaled mode is active, the code attempts to estimate source or frame FWHM and scale aperture and annulus radii from that estimate.

The current adaptive strategy is:

1. Prefer local per-source post-recenter FWHM when it is reliable.
2. Fall back to frame-level FWHM.
3. Fall back to fixed radii if no reliable FWHM can be estimated.

This is an important design choice. The project intentionally moved away from a weaker estimator that over-inflated apertures on faint stars by using too much noisy cutout data.

### Recenter behavior

Catalog positions are converted from sky coordinates to predicted pixel coordinates through WCS. The code can then recenter the aperture based on image data rather than forcing a pure WCS position.

Supported recenter modes include:

- none
- centroid
- centroid_limited

This matters because WCS is often good enough to land near the source, but aperture placement is more stable when a local centroid correction is allowed.

### Edge exclusion

Photometry respects a frame-edge margin percentage. Targets too close to the border are skipped or flagged so partial apertures do not contaminate the light curve.

### Saturation handling

Measurements track:

- Peak pixel value.
- Saturation threshold.
- Saturated pixel count.
- Whether the source is saturated.
- Whether the source is near saturated.

Near saturation is defined by a saturation fraction threshold of 0.95.

Saturated and near-saturated points matter downstream because Differential Photometry excludes or downweights them.

## Measurement Record

Each PhotometryMeasurement stores more than just brightness.

Important fields include:

- Source identity and catalog.
- Object name.
- File path and observation time.
- Filter.
- RA and Dec.
- Pixel x and y.
- Flux and flux error.
- Instrumental magnitude.
- Differential magnitude and error.
- Calibrated magnitude and error.
- Zero-point magnitude and error.
- Whether the source is variable, reference, or check.
- Comparison source IDs and names.
- Comparison reference flux.
- Comparison scatter.
- Aperture and annulus radii used.
- SNR.
- Background level and scatter.
- Centroid shift.
- Saturation state.
- QA flags.
- Quality score and quality weight.
- Exclusion reasons.

This rich measurement object is what powers the table view, QA inspector, filtering, light-curve plotting, and science export.

## Measurement Uncertainty And Science Math

The project exposes its uncertainty math directly in photometry_app/core/error_calculations.py.

### Flux uncertainty

Flux error is computed from source, sky, read-noise, dark-current, and optional background-estimation terms.

Conceptually:

```text
variance = source_electrons
         + aperture_pixels * background_variance_per_pixel
         + background_estimation_term
```

When the data are in ADU, the code converts through gain as needed.

Saturated measurements return NaN uncertainty.

### Magnitude uncertainty from flux

Magnitude error uses the standard small-error conversion:

```text
sigma_mag = 1.085736 * sigma_flux / flux
```

### Differential magnitude

For a target flux F_t and ensemble comparison flux F_r, the differential magnitude is:

```text
m_diff = -2.5 * log10(F_t / F_r)
```

### Differential magnitude error

The differential magnitude uncertainty is propagated from target and reference flux errors:

```text
sigma_diff = (2.5 / ln(10)) * sqrt((sigma_t / F_t)^2 + (sigma_r / F_r)^2)
```

### Ensemble comparison error

The code also supports weighted ensemble uncertainty calculations in flux space and converts them back into magnitude-space uncertainty.

### Calibrated magnitude context

When reference stars have catalog magnitudes, the pipeline estimates a local zero point:

```text
zero_point = catalog_mag - instrumental_mag
calibrated_mag = target_instrumental_mag + zero_point
```

If multiple references have usable zero points, the zero point is averaged, preferably with inverse-variance weighting when the necessary errors exist.

## Differential Photometry Application

The main Differential transformation happens in apply_differential_photometry().

The algorithm works frame by frame and filter by filter.

1. Collect usable reference-star measurements for that frame and filter.
2. Ignore reference points with hard QA failures, saturation, or non-positive flux.
3. For each non-reference target measurement, choose comparison stars.
4. If an explicit comparison list already exists, preserve that order.
5. Otherwise choose the nearest valid references on the sky.
6. Combine the reference fluxes, preferably with inverse-variance weighting.
7. Compute differential magnitude and error.
8. Compute calibrated magnitude context when enough reference-star catalog information exists.
9. Store comparison-source provenance back into the measurement.

If no usable nearby comparison stars are available, the measurement is flagged accordingly.

## Quality Analysis

After Differential measurements exist, the pipeline runs source-wise quality analysis.

This logic groups measurements by source and filter, sorts them in time order, and evaluates several diagnostics.

### Quality checks

The current checks include:

- Low SNR penalties and exclusion below a stricter threshold.
- Large centroid shift penalties and exclusion when excessive.
- Comparison-star disagreement penalties based on comparison scatter.
- Global robust outlier detection using a median and MAD-based scale.
- Local Hampel-style outlier detection in a moving window.
- Hard-flag exclusion for fatal states such as saturation or non-positive flux.

### Quality score

Each point starts from a quality score of 1.0 and is penalized as issues accumulate.

If the quality score drops below the cleaned-view threshold, the point is excluded from cleaned analysis.

### Quality weight

The quality weight is based on quality score and error, roughly as:

```text
quality_weight = quality_score / error^2
```

This weight is useful for later ranking, fitting, or derived workflows.

### Hard versus soft flags

Hard flags include failures such as:

- Outside the usable image area.
- Non-positive background-subtracted flux.
- No nearby reference stars with positive flux.
- Non-2D image.
- Saturated.

Soft flags degrade quality without always forcing immediate exclusion.

## Light-Curve Construction

Light curves are built only from non-reference measurements with usable values.

The build_light_curve_series() logic groups data by:

- source_id
- filter_name

This means one source observed in multiple filters produces multiple light-curve series, not one merged series.

Each LightCurveSeries contains:

- object_name
- source_id
- source_name
- filter_name
- ordered points
- candidate_score
- variability_metrics

Each LightCurvePoint contains the values needed for plotting and review, including:

- observation time
- file path
- differential magnitude
- instrumental magnitude
- flux and flux error
- calibrated magnitude and error
- comparison reference flux
- differential magnitude error
- quality score and quality weight
- exclusion state and reasons

## Light-Curve Interaction Model

The interactive plot is implemented in LightCurvePlotWidget.

Current behaviors include:

- Drag to pan.
- Wheel to zoom.
- Click a point to synchronize the matching measurement selection.
- Ctrl + left-drag to isolate a visible segment of the current series.
- Reset to restore the full dataset.
- Fold and unfold the light curve by the best available period.
- Export the active light curve in theme-based or scientific style.

The plot supports multiple x-axis modes, including calendar-time style labels, Julian-date style labels, and phase mode for folded views.

The UI intentionally removed the old fixed instruction label under the plot and moved general help into rotating status-bar tips.

## Differential Photometry UI Design

Differential mode is laid out as a two-column science workspace.

### Left column

The left column contains:

- The Workspace action strip.
- The results tabs.
- The lower Work Log, Summary, and QA Details tabs.

The Workspace strip uses Open as the stable entrypoint. Open launches a modeless guided workflow window with Folder, Generate, and Progress sections. Folder uses the normal folder browser, then scan results make the first detected object ready for Generate without showing the old loaded-results popup.

Generate always creates light curves. Pull Periods and Calculate Periods are default-on follow-up steps, while Create Measurements Table is default-off and warns that large folders can take extra time. The workflow window stays open during processing with simplified status and tips; detailed operational messages remain in the Work Log.

The results area is intentionally flatter than before. Earlier heavy group-box framing was removed to give the mode a denser, less cluttered layout.

### Right column

The right column contains:

- Annotated Image.
- Light Curve.

These live in a vertical splitter so the user can allocate more space to the image or the plot.

### Status and progress

Differential mode also uses:

- A top progress bar during scan or processing.
- Rotating status-bar tips for workflow guidance.
- Work Log entries for operational state, including cached period reuse.

## Main Differential Controls

The central Differential controls are:

- Open, which opens the guided workflow window.
- Catalog shortcuts: CDS, Simbad, Gaia, VSX.
- Workspace-level filter selector.
- Pull Period.
- Calculate Period.
- Find Better Fit.
- Discover.
- Increase SNR or Reset SNR.
- Light Curve filter enable toggle and settings dialog.
- Fold, Reset, and export controls for the active plot.

## Measurements Table

The Measurements table is intentionally loaded on demand from the Measurements tab or from the guided Generate workflow when Create Measurements Table is selected.

This avoids rebuilding a large row-level table every time the selection changes.

The measurement filtering stack includes:

- Workspace filter.
- Light Curve filter settings.
- Minimum quality threshold.
- Sort mode.

Sort modes include time order, source order, differential magnitude, and flagged-first review.

## Annotated Image View

The Differential annotated image panel is not just a preview; it is a synchronized science-review surface.

It supports:

- Image display controls.
- Curves-based display adjustment.
- Zoom and reset.
- Selection-driven source overlays.
- Saturation markers.
- Frame-edge margin overlay.
- Equatorial grid when enabled.
- Right-click Search on solved images.
- Interactive aperture editing.

The target aperture is shown distinctly from comparison apertures, and the annotated image can be exported as a PNG product.

## Period And Refinement Workflows

Differential Photometry includes several ways to improve interpretation after the base light curve exists.

### Pull Period

Pull Period retrieves literature or catalog period context when available.

The application persists literature-period cache data in settings so repeated lookups are not always recomputed.

### Calculate Period

Calculate Period computes period results from the currently displayed light-curve measurements. This is separate from simply drawing a fit on the plot.

Calculated period cache data is persisted in settings.

### Fit Period and folded view

The plot-level fit controls support interactive period inference and folded display review. Cached fit periods can be recalled later and are now logged to the Work Log instead of occupying the status-bar tip area.

### Find Better Fit

Find Better Fit searches alternative comparison-star ensembles for the selected source rows to improve period or match quality. Its results can be cached and later recalled.

### Discover

Discover is the Differential candidate-follow-up workflow.

It is not the same as asteroid Discover. In Differential mode it works from the solved field catalog and non-catalog Gaia-like sources.

The current design:

- Reuses the solved-field catalog lookup.
- Builds a reusable Gaia reference pool.
- Tests filtered non-catalog Gaia stars as candidate variables.
- Evaluates bounded comparison groups per candidate.
- Retains only stronger ranked candidates.
- Marks retained candidates with discover_candidate metadata.
- Merges retained candidates back into the Differential source list as candidate rows.
- Skips full period inference so the retained results stay light-curve oriented.
- Emits progress incrementally so the UI can update while Discover is running.

### Increase SNR

Increase SNR derives a higher-SNR light curve from selected source rows using conservative period-aware binning. This is a deliberate science-quality rescue tool rather than a cosmetic smoothing button.

The persisted settings cover items such as:

- Maximum fraction of the period allowed in one bin.
- Maximum absolute bin duration.
- Target SNR.
- Minimum and maximum frames per bin.
- Type-aware thresholds.
- Weighted-flux versus magnitude fallback behavior.
- Sigma clipping.
- Dataset mode.
- Whether the derivation applies to the selected measurements only.
- Whether periodless fallback is allowed.

After a derived SNR-binned dataset exists, the same control becomes Reset SNR and restores the original measurements.

## Caching And Persistence

Differential Photometry is heavily cache-aware.

### Settings location

Workspace settings persist in .photometry-settings.json at the root, unless an override path is configured.

### Cache directory

The main cache root is configurable through settings.cache_dir and defaults to a .photometry-cache style workspace cache.

### Catalog cache

Field catalogs are cached under the catalog cache directory and can be cleared globally or for one object.

### Measurement cache

Per-file photometry measurements are cached under:

```text
.photometry-cache/measurements/<ObjectName>/
```

Cache reuse depends on a photometry settings signature and the current variable/reference source IDs. If the signature no longer matches, the file is remeasured.

### Run log

Run history is stored as JSONL under:

```text
.photometry-cache/runs/<ObjectName>.jsonl
```

### Persisted Differential state

Settings also persist Differential-specific state such as:

- Selected catalog source IDs.
- Manual photometry configs.
- Aperture presets.
- Literature period cache.
- Calculated period cache.
- UI tip preference.

## Outputs And Exports

Differential Photometry can export multiple layers of output.

### Report bundle

The report bundle can include:

- Measurements CSV.
- Light-curves CSV.
- Summary JSON.
- Per-series light-curve PNG plots.
- Annotated image PNG exports.

### Science export bundle

The science-ready export path also writes:

- Accepted-observations CSV.
- Rejected-observations CSV.
- Reference-manifest CSV.
- Reduction-manifest JSON.

### AAVSO-oriented outputs

The export pipeline also supports:

- AAVSO Extended text output.
- Preflight JSON review output.

Rows stay differential by default and are only promoted to standard-style export when the required transformed-calibration context exists.

### Plot export styles

The active light curve can be exported in at least two styles:

- Theme-based view.
- Scientific plot.

## Science Interpretation

Differential Photometry in this application is based on relative measurement, not absolute perfection.

Key scientific design principles are:

- Compare targets to nearby stars in the same frame so common atmospheric or throughput variations largely divide out.
- Keep filters separate because astrophysical variability and instrument response depend on bandpass.
- Preserve uncertainty, flags, and exclusion reasons at the point level.
- Distinguish between raw measured values, differential values, and locally calibrated values.
- Keep comparison-star provenance on every target measurement.
- Make review tools interactive so human judgment can remain in the loop.

The application therefore keeps both operational convenience and provenance together. A light curve is never just a line on a plot; it remains attached to source identity, frame identity, comparison identity, quality diagnostics, and exportability.

## Important Settings For Differential Mode

The most important Differential settings include:

- Astrometry API key.
- Cache directory.
- Assume aligned images.
- Nearby comparison-star count.
- Reference-star magnitude range.
- Aperture mode and scaling values.
- Frame-edge margin percent.
- Saturation filter enabled.
- Preview source caps and preview magnitude range.
- Variable designation-family filters.
- Shared worker count.
- Image display defaults.
- Manual configs and aperture presets.
- Period caches.
- Discover thresholds.
- Increase SNR settings.
- Interface tips enabled.

## Failure Modes And Fallback Behavior

Differential Photometry is designed to fail soft where possible.

Examples:

- If no solved field exists, the run returns a report with notes instead of crashing.
- If catalog lookup fails, the run returns a partial report with notes.
- If manual mode is incomplete, automatic processing resumes with an explicit note.
- If adaptive apertures cannot determine a reliable FWHM, the code falls back to fixed radii.
- If a frame has no usable nearby reference stars, affected target points are flagged instead of silently fabricated.
- If cache entries are unusable, the pipeline can recompute measurements.

This behavior is visible in both the Work Log and the report notes.

## Differential Photometry In One Sentence

Differential Photometry mode is a WCS-aware, catalog-guided, cache-backed, interactive time-series workflow that measures target stars against local comparison stars, preserves full per-point quality context, and lets the user move from raw frame folders to exportable light curves without leaving the desktop UI.