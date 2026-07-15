# Differential Photometry Mode

## Brief Summary

Differential Photometry mode is the main time-series science workflow in Citizen Astronomy.

It takes a folder of repeated observations of one sky field, solves or reuses WCS, finds catalog and manual targets in that field, measures their brightness consistently in each frame, compares those measurements against nearby comparison stars, builds light curves, scores data quality, and exports science-ready outputs.

In short: it turns a stack of repeated images into traceable, reviewable relative-brightness measurements over time.

## What The Mode Is For

Differential photometry is used when the scientific question is not just “how bright is this source in one image?” but “how does this source change over time relative to stable stars in the same field?”

That matters because many effects can change the raw measured flux in every frame:

- sky transparency
- extinction
- thin clouds
- seeing changes
- focus drift
- throughput variation
- detector response drift

By comparing the target against nearby stars observed in the same frame, many common-mode effects divide out. That is the scientific reason differential photometry is more robust than raw single-source photometry for variability work.

## What The Mode Does

Differential Photometry mode performs these major jobs:

1. Scan the selected folder and identify usable image files.
2. Resolve or reuse WCS so sky coordinates can be mapped into pixel positions.
3. Query field catalogs such as Gaia DR3, VSX, and exoplanet catalogs.
4. Select the target sources to analyze.
5. Select comparison stars automatically or use manual comparison stars.
6. Measure target and comparison flux in each solved frame.
7. Compute instrumental, differential, and sometimes locally calibrated magnitudes.
8. Score each measurement for quality and mark problematic points.
9. Build per-source, per-filter light curves.
10. Support follow-up analysis such as period lookup, period calculation, comparison optimization, discovery follow-up, and SNR derivation.
11. Export tables, plots, annotated images, and AAVSO-oriented outputs.

## The Science Goal

The scientific objective is to estimate relative brightness changes as reliably as possible.

For a target source with flux $F_t$ and a comparison ensemble with flux $F_r$, the core quantity is:

$$
m_{\mathrm{diff}} = -2.5 \log_{10}\left(\frac{F_t}{F_r}\right)
$$

This is not an absolute standard-star reduction pipeline. It is a practical, field-local, comparison-based workflow built to preserve provenance and uncertainty while remaining interactive.

## Inputs And Assumptions

The mode is folder-based and expects repeated observations of the same field.

Typical accepted inputs are FITS, FIT, and XISF files arranged as one object folder under a `Files` hierarchy or selected directly as one folder.

The scientific assumptions are:

- the field is mostly the same from frame to frame
- timestamps are meaningful enough to order the time series
- a usable WCS exists or can be recovered
- enough non-variable field stars exist to act as references
- the target is not so saturated or truncated that aperture photometry becomes meaningless

## High-Level Workflow

The implemented workflow is:

1. Open a project root, `Files` folder, or one object folder.
2. Scan files and classify metadata and WCS state.
3. Choose or infer the active object.
4. Generate Differential outputs.
5. Resolve WCS for usable frames.
6. Query catalogs using a representative solved field.
7. Choose targets to analyze.
8. Choose comparison stars.
9. Measure target and comparison sources in each frame.
10. Reuse cached measurements when the inputs still match.
11. Apply differential photometry.
12. Run quality analysis.
13. Build per-filter light curves.
14. Optionally run refinement workflows.
15. Review and export.

## Architecture Map

The implementation is spread across these core modules:

- `photometry_app/core/pipeline.py`: orchestration of scanning, WCS resolution, catalog lookup, cache reuse, measurement, and report creation
- `photometry_app/core/photometry.py`: aperture photometry, background estimation, recentering, saturation handling, adaptive apertures
- `photometry_app/core/matching.py`: reference-star selection, ensemble comparison, differential magnitudes, quality scoring, light-curve construction
- `photometry_app/core/error_calculations.py`: uncertainty propagation utilities
- `photometry_app/core/models.py`: report, measurement, series, and manual-mode data contracts
- `photometry_app/core/exporters.py`: CSV, JSON, plot, annotated-image, and AAVSO-oriented export logic
- `photometry_app/ui/main_window.py`: Differential UI workflow and actions
- `photometry_app/ui/light_curve_widget.py`: interactive light-curve review

## Step 1: Workspace Scanning

The mode starts by scanning the selected root and finding object folders and supported files.

This stage extracts or summarizes:

- object name
- file list
- image dimensions
- timestamps
- filter information
- WCS status

Science relevance:

- time-series photometry is only as good as its ordering and frame identity
- WCS status determines whether the catalog-driven workflow can proceed
- filter segregation matters because variability behavior and instrument response depend on bandpass

The scan result becomes the basis for later processing and for workflow summaries shown in the UI.

## Step 2: WCS Resolution

Differential Photometry mode is WCS-aware.

That means sky coordinates from catalogs are projected into image coordinates before measurement. Without usable WCS, the automatic catalog-based workflow cannot reliably place apertures on the intended stars.

The pipeline:

1. checks each file for usable WCS
2. reuses valid existing WCS when present
3. can fall back to solving workflows when configured
4. records notes if a frame remains unsolved or invalid

If no representative solved field exists, the pipeline returns a partial report instead of crashing.

Science relevance:

- WCS ties catalog stars to measured pixels
- reliable source identification is required for consistent multi-frame photometry
- WCS errors directly affect aperture placement and therefore flux stability

## Step 3: Catalog Query

Once at least one solved field exists, the pipeline queries catalogs for the representative field.

The main catalog roles are:

- Gaia DR3: field stars and the main reference-star pool
- AAVSO VSX: known variable stars in the field
- exoplanet entries: host-star targets that may also be relevant for time-series review

Science relevance:

- Gaia provides stable, dense field coverage for comparison-star selection
- VSX provides prior knowledge about known variable sources
- exoplanet-host entries extend the time-series workflow beyond classical stellar variables

## Step 4: Target Selection

The mode supports automatic and explicit target selection.

### Automatic target selection

If there is no saved explicit source list, the pipeline starts from eligible VSX variable stars, applies designation-family filters, and then limits the analysis set by one of these modes:

- percentage of brightest matched variables
- absolute count of brightest matched variables
- optionally the “Analyze Best” recommendation flow

### Explicit target selection

If the user has saved explicit source IDs for that object, those override the default bright-star limit behavior.

### Manual mode

Manual mode replaces catalog-driven target selection with saved manual sources.

Science relevance:

- limiting the source set keeps the analysis tractable and focused
- explicit source persistence avoids accidental drift in repeated reprocessing
- manual mode is essential when catalog positions are incomplete, ambiguous, or intentionally overridden by the observer

## Step 5: Reference-Star Selection

Reference stars are chosen from Gaia stars.

The automatic selector:

- keeps only stars inside the configured magnitude range
- prefers stars in a narrower “good comparison” band
- excludes stars too close to known variable targets
- returns a limited pool of candidate references

The default scientific logic is conservative:

- avoid stars that are too bright and risk saturation
- avoid stars that are too faint and noise-dominated
- avoid stars too close to variable targets so the comparison set is less likely to be contaminated by known variability

Then, during differential computation, the target uses only the nearest subset of valid reference measurements in the current frame.

### Automatic reference selection algorithm

Conceptually the code does this:

```text
reference_candidates = Gaia stars
reference_candidates = keep stars with usable magnitude
reference_candidates = keep stars inside configured magnitude range
reference_candidates = remove stars within exclusion radius of variable targets
reference_candidates = sort by preferred magnitude band and closeness to range midpoint
reference_pool = first N stars
```

Science relevance:

- a good comparison ensemble should be stable, unsaturated, and measured with decent SNR
- choosing local nearby references helps common-mode atmospheric and instrumental effects cancel more effectively

## Step 6: Aperture-Photometry Measurement

This is the heart of the per-frame science measurement.

For each solved frame, the pipeline measures both target sources and comparison stars.

### Frame context

Before measuring sources, the code prepares per-frame context including:

- image array
- WCS
- sigma-clipped background median
- sigma-clipped background scatter
- saturation threshold when available

### Predicted source position

For each source, the pipeline converts sky coordinates to predicted pixel coordinates using WCS.

### Recenter behavior

The pipeline can then refine the pixel position through recentering rather than trusting the WCS prediction exactly.

Supported behavior includes:

- no recentering
- centroid recentering
- limited centroid recentering

Science relevance:

- WCS is usually close, but local centroiding improves repeatability when pointing or WCS are not perfect
- excessive recenter motion is itself diagnostic and later contributes to quality scoring

### Apertures and sky annulus

The measurement uses:

- a circular source aperture
- a circular sky annulus around the source

The annulus estimates local background so the source sum can be background-subtracted.

The basic photometric calculation is:

$$
F = S_{\mathrm{aperture}} - B_{\mathrm{local}} \times N_{\mathrm{aperture}}
$$

where:

- $S_{\mathrm{aperture}}$ is the raw sum inside the source aperture
- $B_{\mathrm{local}}$ is the local median background per pixel from the annulus
- $N_{\mathrm{aperture}}$ is the aperture area in pixels

### Fixed versus adaptive apertures

The mode supports two aperture strategies:

- fixed radii
- FWHM-scaled radii

In FWHM-scaled mode, the code estimates frame FWHM and, where possible, local source FWHM, then scales the aperture and annulus radii from that estimate.

Conceptually:

```text
if aperture_mode == fixed:
    use configured radii
else:
    estimate frame FWHM
    for each star:
        estimate local star FWHM if possible
        if local FWHM is valid:
            use scaled radii from local FWHM
        else if frame FWHM is valid:
            use scaled radii from frame FWHM
        else:
            fall back to fixed radii
```

Science relevance:

- fixed radii are simple and stable
- FWHM-scaled radii adapt better to seeing and focus changes
- oversize apertures add unnecessary noise, while undersize apertures clip flux

### Edge exclusion

If the source or annulus would be too close to the frame edge, the measurement is flagged because a partial aperture or annulus can bias the flux.

### Saturation handling

The code records:

- peak pixel value
- saturation threshold
- saturated pixel count
- saturated versus near-saturated state

Science relevance:

- saturation invalidates the linear flux assumption needed for photometry
- near-saturated measurements are risky and are penalized even if not fully clipped

## Step 7: Flux, Magnitude, And Uncertainty Math

### Flux uncertainty

The code exposes flux-error propagation explicitly in `error_calculations.py`.

Conceptually the variance is:

$$
\sigma_F^2 = F_{\mathrm{source}} + N_{\mathrm{ap}}\,\sigma_{\mathrm{bg}}^2 + \sigma_{\mathrm{bg,est}}^2
$$

with optional contributions from read noise and dark current through the background term.

The implementation uses source, sky, read-noise, dark-current, gain, and optional background-estimation terms. Saturated observations yield `NaN` uncertainty.

### Instrumental magnitude

If the background-subtracted flux is positive, the code computes:

$$
m_{\mathrm{inst}} = -2.5\log_{10}(F)
$$

### Magnitude uncertainty from flux uncertainty

The code uses the standard small-error conversion:

$$
\sigma_m = 1.085736 \frac{\sigma_F}{F}
$$

### Signal-to-noise ratio

If the flux error is positive, the code also records:

$$
\mathrm{SNR} = \frac{F}{\sigma_F}
$$

Science relevance:

- this uncertainty math is what turns a brightness estimate into a scientifically interpretable measurement
- without propagated error, light-curve ranking and outlier handling become much less meaningful

## Step 8: Differential Photometry Transformation

After per-frame measurements exist, the pipeline converts them into differential quantities.

This occurs frame by frame and filter by filter.

### What the code does

For each frame and filter:

1. collect reference-star measurements with positive flux
2. reject saturated or hard-flagged reference points
3. for each non-reference target, choose a comparison set
4. if the target already has explicit comparison IDs, preserve that order
5. otherwise pick the nearest valid references on the sky
6. combine the reference fluxes into one comparison flux
7. compute differential magnitude and uncertainty
8. compute a local zero-point context when catalog magnitudes are available
9. store the comparison provenance back on the measurement

### Comparison-flux algorithm

The ensemble reference flux is built in flux space.

If usable flux errors exist, the code uses inverse-variance weighting:

$$
w_i = \frac{1}{\sigma_{F,i}^2}
$$

$$
F_r = \frac{\sum_i w_i F_i}{\sum_i w_i}
$$

and the ensemble flux error becomes:

$$
\sigma_{F_r} = \sqrt{\frac{1}{\sum_i w_i}}
$$

If valid flux errors are not available, the code falls back to a more conservative center estimate, effectively a median reference flux.

### Differential magnitude algorithm

The target differential magnitude is:

$$
m_{\mathrm{diff}} = -2.5\log_{10}\left(\frac{F_t}{F_r}\right)
$$

The propagated uncertainty is:

$$
\sigma_{\mathrm{diff}} = \frac{2.5}{\ln 10}\sqrt{\left(\frac{\sigma_{F_t}}{F_t}\right)^2 + \left(\frac{\sigma_{F_r}}{F_r}\right)^2}
$$

### Local calibrated magnitude context

If comparison stars have catalog magnitudes, the code also estimates a local zero point.

For one reference star:

$$
ZP = m_{\mathrm{catalog}} - m_{\mathrm{inst,ref}}
$$

For multiple references, the pipeline averages the zero points, preferably with inverse-variance weighting when the needed errors exist.

Then:

$$
m_{\mathrm{cal}} = m_{\mathrm{inst,target}} + ZP
$$

Science relevance:

- flux-space ensemble comparison is statistically safer than averaging magnitudes directly
- preserving comparison IDs and names on each measurement is important scientific provenance
- calibrated magnitude is contextual and local, not a replacement for rigorous transformation pipelines

### Explicit algorithm summary

```text
for each frame and filter:
    references = valid reference measurements
    for each target measurement:
        if target has explicit comparison IDs:
            comp_set = those references in saved order
        else:
            comp_set = nearest valid references

        if comp_set is empty:
            flag measurement
            continue

        F_ref, sigma_ref = weighted ensemble flux(comp_set)
        m_diff = -2.5 * log10(F_target / F_ref)
        sigma_diff = propagated target+ensemble error
        if catalog magnitudes exist for references:
            estimate zero point and local calibrated magnitude
        store comparison provenance
```

## Step 9: Quality Analysis

After the differential values exist, the pipeline scores each measurement.

This is one of the most important scientific review layers because not every measured point deserves equal trust.

The analysis groups rows by source and filter, orders them in time, and then checks:

- low SNR
- very low SNR exclusion
- large centroid shift
- excessive centroid shift exclusion
- comparison-star disagreement
- global robust outliers using a median-and-MAD scale
- local Hampel outliers
- hard failure states such as saturation or non-positive flux

### Quality score

Each point starts at `1.0` and is penalized as issues accumulate.

The code then derives a quality weight approximately as:

$$
w_{\mathrm{quality}} = \frac{q}{\sigma^2}
$$

where $q$ is the quality score and $\sigma$ is the relevant error term.

### Cleaned view versus raw view

The pipeline does not throw every suspicious point away. Instead, it records exclusion state and reasons.

That supports two scientific needs:

- preserve raw provenance
- allow a cleaned analysis view for more stable light curves and follow-up metrics

### Explicit quality-analysis algorithm

```text
group rows by source and filter
sort each group in time order
measure global center and robust scale
for each row:
    start quality_score = 1.0
    penalize low SNR
    penalize large centroid drift
    penalize comparison disagreement
    mark exclusion for strong failures
    test global MAD outlier condition
    test local Hampel outlier condition
    if hard flag exists, exclude
    if quality_score too low, exclude
    compute quality weight
```

Science relevance:

- a time series is only as believable as its worst systematics
- separating flags from outright exclusion keeps the observer in the loop
- local and global outlier tests catch different failure modes

## Step 10: Light-Curve Construction

The pipeline then groups usable non-reference measurements by:

- `source_id`
- `filter_name`

This is deliberate. One source observed in multiple filters yields multiple light curves, not one merged series.

Each point records:

- time
- file path
- differential magnitude
- instrumental magnitude
- flux and flux error
- calibrated magnitude and error
- comparison flux
- differential-magnitude error
- quality score and weight
- exclusion state and reasons

Each series also stores variability metrics such as:

- RMS
- MAD
- amplitude
- typical error
- reduced $\chi^2$
- von Neumann ratio
- Stetson $J$
- Stetson $K$
- candidate score

Science relevance:

- these metrics help separate plausible variability from noise or processing artifacts
- keeping filters separate avoids physically misleading cross-band merging

## Step 11: Interactive Review Surfaces

Differential mode is not only a pipeline. It is a science-review environment.

### Annotated Image

The annotated image view supports:

- display and stretch controls
- levels/curves adjustment
- synchronized source overlays
- aperture visualization
- saturation markers
- frame-edge margin overlay
- optional equatorial grid
- export of the annotated image

Scientific value:

- lets the user visually confirm source identity, crowding, aperture placement, and saturation state
- makes it easier to distinguish photometric failure from astrophysical behavior

### Light Curve

The light-curve panel supports:

- interactive pan and zoom
- synchronized point selection
- filtered segment isolation
- reset to full range
- folded and unfolded views
- export in theme or scientific style

Scientific value:

- interactive point-to-image linkage is essential for diagnosing suspect measurements
- folded view supports periodic-variable interpretation rather than just time-order inspection

### Measurements Table

The measurements table is loaded on demand to avoid unnecessary cost on large runs.

It supports filtering by:

- workspace selection
- light-curve filter settings
- quality threshold
- sort mode

Scientific value:

- row-level access is necessary for auditability and export review

## Step 12: Period And Refinement Workflows

Differential mode includes several follow-up workflows beyond raw light-curve generation.

### Pull Period

This retrieves literature or catalog period context when available.

Scientific role:

- helps compare the measured light curve against prior knowledge

### Calculate Period

This derives period estimates from the current light-curve measurements.

Scientific role:

- tests whether the observed data support periodic variability

### Fit Period and folded review

The plot layer supports period-fit review and folded visualization.

Scientific role:

- periodic structure is often more interpretable in phase space than in time space

### Find Better Fit

This searches alternative comparison-star ensembles for the selected source to improve the resulting fit or match quality.

Scientific role:

- a variable can look noisier than it is if the comparison set is suboptimal
- comparison refinement can reduce differential scatter without changing the source itself

### Discover

Differential Discover is a Gaia-based candidate follow-up workflow, distinct from moving-object discovery.

Its design is:

- reuse solved-field catalog context
- build a Gaia reference pool
- test non-catalog Gaia-like sources as candidate variables
- evaluate bounded comparison groups
- keep only stronger ranked candidates
- merge retained candidates back into the Differential source list

Scientific role:

- extends the workflow from known targets to plausible new time-series candidates in the same field

### Increase SNR

Increase SNR derives a higher-SNR light curve from selected rows using conservative period-aware binning.

Scientific role:

- useful for weak but real variability where single-frame points are noisy
- intended as a science-rescue tool, not a cosmetic smoothing control

## Step 13: Manual Mode

Manual mode replaces the automatic source-definition path with saved manual sources.

Each saved source has a role:

- target
- comparison
- check

Manual mode also stores:

- reference frame
- recenter behavior
- fallback behavior
- per-source apertures and annuli

Scientific value:

- supports fields where catalog-driven targeting is insufficient
- preserves expert observer knowledge
- enables deliberate comparison/check-star design rather than fully automatic selection

## Step 14: Caching And Persistence

Differential mode is heavily cache-aware.

### Catalog cache

Catalog lookups are cached under the workspace cache area.

### Measurement cache

Per-file measurement results are cached under the object cache directory.

Cache reuse depends on:

- file identity
- WCS-related inputs
- selected variable and reference source IDs
- the photometry settings signature

If those inputs no longer match, the measurements are recomputed.

### Run log

Run summaries are stored as JSONL under the cache tree.

### Persisted Differential state

Settings persist Differential-specific state such as:

- selected source IDs
- manual photometry configs
- aperture presets
- period caches
- UI preferences

Scientific relevance:

- caching speeds iteration without hiding provenance
- signature-based invalidation helps prevent stale science products from being silently reused under different settings

## Step 15: Outputs And Exports

The mode can produce multiple output layers.

### Report bundle

This can include:

- measurements CSV
- light-curves CSV
- summary JSON
- plot PNGs
- annotated image PNGs

### Science export bundle

This can include:

- accepted-observations CSV
- rejected-observations CSV
- reference-manifest CSV
- reduction-manifest JSON

### AAVSO-oriented outputs

The export path also supports:

- AAVSO Extended text output
- preflight JSON review output

Scientific role:

- export keeps enough diagnostic context for downstream review and submission
- accepted and rejected rows remain separated so cleaning decisions stay transparent

## Important Functionalities Visible To The User

The main Differential Photometry user-facing functionalities are:

- guided Open and Generate workflow
- automatic folder scan and object selection
- automatic or manual target selection
- automatic or manual comparison-star handling
- optional adaptive apertures
- saturation-aware measurement
- synchronized annotated image and light curve review
- on-demand measurements table
- period lookup and period calculation
- comparison-star optimization
- candidate discovery follow-up
- SNR derivation and reset
- CSV, JSON, PNG, and AAVSO-oriented export

## Important Settings In Differential Mode

The most consequential settings are:

- astrometry configuration
- cache directory
- assume-aligned behavior where relevant to broader workflows
- nearby comparison-star count
- reference-star magnitude range
- aperture mode and aperture scales
- frame-edge margin percent
- saturation filtering
- preview source limits and designation-family filters
- worker count
- image display defaults
- manual source configuration
- period caches
- Discover thresholds
- Increase SNR settings

These settings matter scientifically because they directly change what gets measured, how it gets measured, and which points are trusted later.

## Failure Modes And Fallback Behavior

The mode is designed to fail softly where possible.

Examples include:

- no solved field returns a partial report rather than a crash
- failed catalog lookup returns a partial report with notes
- incomplete manual mode falls back to automatic processing with an explicit note
- missing reliable FWHM falls back to fixed apertures
- absent usable comparison stars flags the point instead of fabricating a differential value
- invalid or stale cache entries trigger recomputation

Scientific relevance:

- graceful failure is important because silently inventing results is worse than returning fewer results
- explicit notes and work-log messages preserve trust in the pipeline

## End-To-End Algorithm Summary

This is the simplest faithful description of what the mode does.

```text
scan workspace
identify active object
resolve or reuse WCS for each frame
if no solved field exists:
    return partial report

query catalogs for representative field
select targets
select comparison stars

for each solved frame:
    if cached measurements are valid:
        reuse them
    else:
        for each target and reference star:
            project sky position to pixel position with WCS
            recenter locally if enabled
            choose aperture and annulus radii
            measure aperture sum
            estimate local sky background from annulus
            compute background-subtracted flux
            compute flux error, instrumental magnitude, SNR
            record flags, saturation state, centroid shift, provenance
        cache per-frame measurements

for each target measurement:
    choose valid comparison stars from same frame and filter
    combine comparison fluxes into an ensemble reference flux
    compute differential magnitude and uncertainty
    optionally compute local calibrated magnitude context

for each source and filter:
    score measurement quality
    mark excluded points for cleaned view
    build light-curve series
    compute variability metrics

present results in image, table, and plot views
optionally run period and refinement workflows
export review and science outputs
```

## One-Sentence Summary

Differential Photometry mode is a WCS-aware, catalog-guided, comparison-star-based time-series workflow that measures sources consistently across repeated frames, computes relative brightness changes with propagated uncertainty, preserves per-point quality and provenance, and exposes the entire result in an interactive review and export environment.