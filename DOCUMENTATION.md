# Citizen Photometry Documentation



## Overview



Citizen Photometry is a desktop application for aperture-photometry inspection of known objects in astronomy image sequences.



Its main workflow is:



1. open a folder containing FITS or XISF images

2. scan metadata and validate WCS

3. look up cataloged field objects

4. preview which candidates should be processed

5. measure target and comparison stars in each usable frame

6. compute differential brightness products

7. inspect results interactively

8. export tables, plots, and annotated images

The top menu also includes a `Calibrate` action for preparing raw image folders before analysis. Its dialog separates the pipeline into science images, calibration masters for bias/dark/flat correction, and optional post-calibration WCS alignment that writes separate aligned FITS copies.



The software is designed primarily around known variable stars and field inspection of known exoplanet catalog entries, with a first-pass Transient Finder mode for repeated solved image folders. Transient Finder checks WCS, uses astrometry.net for missing solutions when configured, queries one magnitude-limited Gaia comparison catalog for the solved sequence, caps crowded wide-field source lists to the strongest detections, and reports point-source candidates only after frame-to-frame aperture and peak measurements show significant variability rather than static-star or seeing artifacts.



## H-R Diagram Documentation



The H-R Diagram mode has its own detailed guide in [guides/hr_diagram.md](guides/hr_diagram.md).



That document covers:



- prerequisites for H-R source images

- full-field measurement versus ROI filtering

- Generate, Update Diagram, and Regenerate behavior

- ROI draft versus applied semantics

- derived H-R quantities and calibration logic

- plot controls, results-table behavior, and output files

- current limitations and troubleshooting



## Folder Layout



Recommended layout:



```text

Photometry/

  Files/

    ObjectA/

      image_001.fits

      image_002.fits

    ObjectB/

      image_001.xisf

```



Supported input choices when opening data:



- project root

- `Files` folder

- single object folder



## Installation And Launch



Install:



```powershell

python -m pip install -e .

```



Launch:



```powershell

python -m photometry_app.main

```



Optional astrometry.net key:



```powershell

$env:CITIZEN_PHOTOMETRY_ASTROMETRY_API_KEY = "your-api-key"

```



## Step-By-Step User Guide



### 1. Open A Folder



Use `File > Open Folder`.



You can open:



- the workspace root

- the `Files` folder

- a single object folder



After opening, the application scans automatically. There is no separate scan button in the current workflow.



### 2. Review The Loaded Results Dialog



After scanning, the application opens a `Loaded Results` dialog.



That dialog summarizes each discovered object and lets you choose which one becomes the active object in the Workspace strip.



It also shows:



- total files per object

- solved and needs-solve counts

- invalid counts

- a note preview for the currently selected object



![Loaded Results dialog showing per-object scan counts and note preview.](docs/screenshots/loaded_results_dialog.png)



Loaded Results dialog with current object counts, solve-state columns, and per-object note preview.



### 3. Use The Workspace Strip



The old always-visible Browser pane is gone from the differential-photometry workflow.



Instead, the upper-left area exposes a compact `Workspace` strip with:



- `Generate`

- `CDS`

- `Simbad`

- `Gaia`

- `VSX`

- an inline active-object label to the right of `Generate`



`Generate` always runs against the active object shown in that strip.



![Workspace strip showing Generate, catalog shortcuts, and the active object label.](docs/screenshots/workspace_strip.png)



Workspace strip with the active object inline beside Generate and the external catalog shortcuts on the same row.



### 4. Configure Settings If Needed



Use `Settings > Open Settings`.



Important settings include:



- astrometry API key

- cache directory

- nearby comparison-star count

- fixed or adaptive aperture mode

- fixed aperture and annulus radii

- FWHM-scaled aperture multipliers

- frame-edge exclusion margin

- preview candidate caps by count or faint-limit magnitude

- variable-star limit mode and value

- designation-family filters such as Named, Gaia, ASAS-SN, ATLAS, ZTF, and Other

- aligned-stack assumption



### 5. Choose Alignment And Mode Options



Under `Settings` you can control:



- `Alignment`

  - `Non-Aligned`

  - `Aligned`

- `Mode`

  - `Auto`

  - `Manual`



`Aligned` tells the app to solve one representative frame and reuse that geometry for the rest of the aligned stack.



`Manual` enables saved manual source definitions instead of automatic target selection.



### 6. Start Processing



Choose the active object in `Loaded Results` and click `Generate` in the Workspace strip.



What happens next depends on the object mode:



- in automatic mode, the app prepares a preview of eligible field targets

- in manual mode with a saved target, preview can be skipped and processing starts directly



### 7. Use The Preview Dialog



The preview dialog is the last selection step before full processing.



It shows:



- how many variable stars were found in the field

- how many remain after designation-family filtering

- how many remain after preview caps

- how many will be processed by default



You can adjust:



- default selection by percentage or absolute count

- designation-family checkboxes such as ZTF and ATLAS

- source-row selections directly in the dialog



The `Process Default` action uses the current theme accent color to emphasize the recommended default selection.



### 8. Let The Processing Run Complete



During processing the app will:



1. resolve or reuse WCS

2. query Gaia DR3, VSX, and exoplanet metadata

3. choose candidate variables and reference stars

4. estimate apertures if adaptive mode is enabled

5. measure sources in each usable frame

6. compute differential photometry products

7. build light curves by source and filter

8. record notes, flags, and run history



### 9. Inspect Results



Main result areas:



- File Results

- Source Results

- Measurements

- Run History

- annotated image panel

- light-curve panel

- detail pane

- work log



Useful current workflow notes:



- the Measurements table is loaded on demand with `Measure`, so large filtered result sets do not automatically populate that table after every refresh

- the Light Curve `Filter` button owns the saved filtering dialog, while the checkbox beside it enables or disables the active filter set

- `Fit Period` now writes Work Log entries before and after a fit, showing the displayed value that was replaced and the fitted result



### 10. Source Results Actions



The Source Results toolbar is where most per-source refinement actions live.



Current actions include:



- `Pull Period` for literature-period lookup

- `Calculate Period` for local period inference

- `Find Better Fit` for comparison-star optimization against literature period behavior

- `Increase SNR` for conservative period-aware binning of the selected source rows



When `Increase SNR` has already been applied to the selected source during the current session, that action changes to `Reset SNR` and restores the cached original measurements for that source.



At the moment, that `Reset SNR` availability is session-local rather than persisted across app restarts.



![Source Results action row with period and SNR tools.](docs/screenshots/source_results_actions.png)



Source Results actions after processing, including period tools, comparison-fit refinement, and Increase SNR.



## How Comparison Stars Are Selected



The application uses the term `reference stars` for the field-level pool of stars that can be used for differential photometry, and `comparison stars` for the subset of those references actually used for one target measurement in one image.



There are two different workflows depending on the object mode.



### Manual Mode



In manual mode, the saved manual comparison stars are used directly.



That means:



- the app does not auto-pick Gaia reference stars for that object

- the saved manual comparison sources become the reference pool

- each target measurement uses those saved sources when they are successfully measured in the current frame



### Automatic Mode



In automatic mode, comparison-star selection happens in two stages.



#### Stage 1: Build A Reference-Star Pool For The Object



The pipeline first selects a field-level pool of Gaia stars that are suitable as references.



Rules used for that pool:



1. only Gaia stars with a catalog magnitude are considered

2. stars must fall inside the active magnitude range

3. stars too close to known variable stars are rejected

4. the remaining stars are ranked so moderate-brightness stars are preferred over very bright or very faint ones

5. the pool is capped at the highest-ranked candidates



More specifically:



- if you do not set a custom reference-star magnitude range, the default allowed range is 8.0 to 16.0 mag

- if you do set `reference star min magnitude` or `reference star max magnitude`, those values become the active range

- stars within 30 arcseconds of a known variable star are excluded from the reference pool

- candidates are ranked to prefer the narrower band from 10.0 to 13.5 mag when possible

- within the active range, stars closer to the midpoint of that range are preferred

- the automatic reference-star pool is limited to 25 stars



This is why the selected references often look clustered around similar magnitudes even when the field itself contains much brighter and much fainter stars. The algorithm intentionally avoids the extremes and favors a moderate brightness band.



#### Stage 2: Pick The Nearby Comparison Subset For Each Target Measurement



Once the field-level reference pool exists, the app still does not use all of those stars for every target.



For each non-reference target measurement in each frame and filter:



1. the app looks at the measured reference stars available in that same frame and filter

2. it removes references that are unusable in that frame

3. it chooses only the nearest usable references on the sky

4. it combines those nearby references into one ensemble comparison flux

5. it computes the target differential magnitude from the target flux relative to that ensemble reference flux



Reference stars are treated as usable in a frame only when they have:



- positive measured flux

- no saturation flag

- no near-saturation flag

- no hard quality flag



The number of nearby comparison stars used per target comes from the `nearby comparison-star count` setting. If fewer usable references are available in that frame, the app uses fewer.



In the current default configuration, that nearby comparison count is 5 unless you change it in Settings.



#### How The Ensemble Comparison Is Combined



After the nearby comparison subset is chosen, the app combines them into a single ensemble comparison reference.



- if valid flux uncertainties are available, it uses an inverse-variance weighted mean of the comparison-star fluxes

- if those uncertainties are not usable, it falls back to the median comparison-star flux



The target differential magnitude is then computed from the ratio of the target flux to that ensemble comparison flux.



#### Why The Comparison Stars Often Look Similar In Brightness



That behavior is expected with the current algorithm.



The stars you see used for one variable usually cluster in brightness because the app is doing all of the following on purpose:



- limiting the field-level reference pool to a moderate magnitude range

- preferring the middle of that range over the bright and faint ends

- excluding stars near known variables

- choosing only the nearest few usable references for each target rather than the whole field



So even if the field contains a much larger brightness spread, the comparison-star subset for a given variable will often be made of stars with similar catalog magnitudes and similar sky position.



#### Settings That Control The Behavior



The main settings affecting automatic comparison-star selection are:



- `nearby comparison-star count`

- `reference star min magnitude`

- `reference star max magnitude`



If you want the algorithm to consider brighter or fainter comparison stars, the main lever is the reference-star magnitude range. If you want each variable to use a larger or smaller local ensemble, the main lever is the nearby comparison-star count.



### Science-Ready Output Bundle



`File > Export Report` writes the usual CSV/JSON/plot bundle plus science-oriented artifacts:



- accepted observations CSV

- accepted observations JSON using the `science-observation-v3` schema

- rejected observations CSV

- rejected observations JSON using the same schema

- reference-star manifest CSV

- reduction manifest JSON

- provenance manifest JSON with file notes, calibration-state counts, comparison-method counts, and reference-star summary

- AAVSO Extended text export

- AAVSO preflight JSON



The canonical observation JSON files are intended for downstream scripting and publication workflows that need stable field names without depending on CSV column order. In `science-observation-v3`, each row also carries aperture radii, centroid and background context, saturation inputs, comparison-method metadata, and explicit calibration state with zero-point context so downstream review does not have to infer whether a row is only differential or has a usable calibration basis.



### Current Publication Gaps



The report bundle is now structured for science-ready review and downstream tooling, but it is still conservative about claiming full publication or submission readiness.



Current gaps to keep in mind:



- zero-point calibrated `STD` AAVSO rows can now be emitted without transformation coefficients, so `TRANS=NO` plus `MTYPE=STD` is supported for CV-style clear-filter reductions; this is still not a full transformation-coefficient workflow

- AAVSO chart and sequence handling is currently a single settings value, not a per-target or per-session managed sequence record

- airmass is taken from image headers when available and otherwise estimated geometrically from the target coordinates, observation time, and configured observing-site latitude/longitude/elevation; rows still warn when neither source is available

- the bundle records reduction metadata and canonical row schema, but it does not yet emit a richer machine-readable provenance chain for calibration frames, flats, darks, or transformation coefficients

- observer and instrument metadata are workspace settings, so mixed-instrument or mixed-site runs should still be split manually before submission



For now, the intended workflow is: use the bundle as a reviewable reduction package, inspect the preflight warnings, and only treat the AAVSO file as upload-ready when the metadata and calibration warnings match the actual observing session.



### UI Tour And Screenshot Guide



If you want to refresh screenshots again later, these are still the most useful places to capture them.



### Current Screenshot Refresh Set



The most outdated screenshots are the ones tied to the old Browser-pane and Start-button workflow.



If you are refreshing this document now, capture these three views first:



1. Workspace strip with the active object visible.

  Include `Generate`, the inline active-object label, and the catalog shortcut buttons.

2. Loaded Results dialog after a scan.

  Include object counts, solved / needs-solve / invalid columns, and the note preview.

3. Source Results action row after processing.

  Include `Pull Period`, `Calculate Period`, `Find Better Fit`, and `Increase SNR` or `Reset SNR`.



Current reference screenshots are committed under `docs/screenshots/` and can be regenerated with `scripts/generate_doc_screenshots.py`.



#### Screenshot 1: Workspace Strip



Capture:



- Workspace strip

- active-object label

- Generate and catalog shortcut buttons



Suggested caption:



`Workspace strip after choosing an active object, with Generate and the catalog shortcuts ready for the next processing run.`



#### Screenshot 2: Preview Dialog Before Processing



Capture:



- candidate count summary

- designation-family filters

- percentage or count selection controls

- source selection table



Suggested caption:



`Preview dialog showing which cataloged targets will be processed.`



#### Screenshot 3: Annotated Image View



Capture:



- source marker

- aperture ring

- annulus ring

- comparison-star overlays



Suggested caption:



`Annotated image view used to verify that apertures and annuli are centered on the intended stars.`



#### Screenshot 4: Light-Curve Panel With Fit Enabled



Capture:



- source selector

- x-axis mode

- fit mode controls

- plotted points with error bars

- overlaid fitted curve



Suggested caption:



`Light-curve panel with themed plotting, error bars, and an optional fitted overlay.`



#### Screenshot 5: Measurements Or Source Results Table



Capture:



- one of the results tables

- a selected row

- the detail pane for that selection

- optionally the Source Results action row or the Measure button



Suggested caption:



`Results tables make it possible to inspect per-source and per-frame measurements in detail.`



#### File Results



Useful for checking frame-level measurement counts, flagged results, and notes.



#### Source Results



Summarizes each cataloged source with its role, magnitude, measurement count, filters, flagged count, source-level SNR, period values, and variability metrics.



#### Measurements



One row per source per frame.



The Measurements table is loaded explicitly through `Measure`, using the current search text and active light-curve filters.



Columns include:



- time

- file

- source

- filter

- role

- flux

- differential magnitude

- error

- flags

- catalog



#### Annotated Image Panel



Used to verify whether the aperture and annulus are actually sitting on the intended source.



Typical overlays include:



- target source

- comparison stars

- aperture and annulus rings



This is one of the most important quality-control views in the application.



#### Light-Curve Panel



The light-curve panel supports:



- source selection

- previous and next target navigation

- keyboard left and right shortcuts

- calendar-time or Julian Date x-axis

- a saved Light Curve filter workflow with enable/disable toggle

- optional fit overlays

- polynomial fit degree control

- periodic fit period and harmonic controls

- period estimation helper

- themed plotting



### 11. Export Results



Use the `File` menu to export:



- light-curve plots

- annotated images



Depending on the export path, the application can also write measurements CSV, light-curves CSV, and a run-summary JSON file.



## Themes



The current theme system supports:



- Normal

- Dark

- Dracula

- Nord

- Tokyo Night

- Gruvbox

- Catppuccin

- Solarized Dark

- One Dark

- Customize



Custom themes allow editing colors for:



- window background

- panel background

- text

- menu background and text

- accent color

- plot background

- plot axis text

- plot data points

- plot fit curve



You can also import and export theme JSON files.



If a workspace does not explicitly set a theme, the application falls back to the last theme used when the program was previously closed.



## How The Software Measures Brightness



Citizen Photometry uses aperture photometry.



For each source:



1. a circular aperture is placed on the target position

2. a surrounding annulus is used to estimate local sky background

3. the annulus median is scaled by aperture area and subtracted from the raw aperture sum

4. the result becomes the background-corrected source flux



In automatic mode, target positions come from catalog coordinates projected through the frame WCS, then refined in the image plane.



In manual mode, target and comparison positions come from saved source definitions and optional recentering.



## How Error Bars Are Calculated



### What The Live App Currently Uses



The plotted measurement error bars in the current processing workflow come from two layers:



1. flux uncertainty estimation during aperture photometry

2. differential-magnitude uncertainty propagation during comparison-star correction



### Flux Uncertainty In The Active Photometry Path



In the active measurement code, the app estimates flux error with a compact aperture-plus-background model:



$$

\sigma_F = \sqrt{\lvert F \rvert + A\sigma_{bg}^2\left(1 + \frac{A}{B}\right)}

$$



Where:



- $F$ is the background-subtracted flux

- $A$ is the aperture area in pixels

- $B$ is the annulus area in pixels

- $\sigma_{bg}$ is the local background standard deviation estimated from the annulus



Interpretation:



- the $\lvert F \rvert$ term acts as the source Poisson-like contribution

- the background term accounts for pixel-to-pixel sky noise inside the aperture

- the $A/B$ factor adds the uncertainty introduced by estimating the background from a finite annulus



If the local annulus statistic is unavailable or invalid, the code falls back to a frame-level background estimate.



### Differential Magnitude Error



After comparison-star selection, the current automatic differential-magnitude uncertainty is propagated from target and reference fractional flux uncertainties:



$$

\sigma_{\Delta m} = \frac{2.5}{\ln 10}\sqrt{\left(\frac{\sigma_t}{F_t}\right)^2 + \left(\frac{\sigma_r}{F_r}\right)^2}

$$



Where:



- $F_t$ is target flux

- $\sigma_t$ is target flux error

- $F_r$ is the comparison-reference flux

- $\sigma_r$ is the comparison-reference flux error



In the current automatic workflow, the comparison-reference flux is built from the median of the selected nearby comparison-star fluxes, and the reference flux error is approximated from the available comparison-star flux errors.



### What The Error Bars Mean In Practice



The error bars shown in the light-curve panel should be interpreted as measurement uncertainty estimates, not guarantees of astrophysical truth.



They mainly reflect:



- source-counting noise

- local sky-background noise

- uncertainty in the comparison reference



They do not automatically correct every real-world issue such as:



- flat-field residuals

- blending by neighboring stars

- thin cloud structure

- systematic focus drift

- mount or tracking artifacts



### Additional Uncertainty Utilities In The Codebase



The repository also contains a richer uncertainty module in `photometry_app/core/error_calculations.py`.



That module includes utilities for:



- CCD-style flux uncertainty with source, sky, read-noise, and dark-current terms

- flux-to-magnitude conversion

- ensemble comparison-star error propagation

- empirical scatter estimation from residuals

- scintillation error estimation

- quadrature combination of theoretical, empirical, and scintillation terms



Those functions provide the technical basis for more advanced uncertainty modeling, but the currently active live photometry path uses the simpler aperture-background and differential-propagation formulas described above.



## What Methods Are Used For Curve Fitting



The fit overlays in the light-curve panel are for visual interpretation. They are not a full astrophysical model-fitting engine.



### Available Fit Modes



The current app supports two overlay fit modes:



1. polynomial fit

2. periodic harmonic fit



### Polynomial Fit



The polynomial option:



- sorts the points by x value

- normalizes the x-axis span for numerical stability

- fits a polynomial of user-selected degree using `numpy.polyfit`

- evaluates the result on a smooth grid for display



This is useful for:



- smooth trends

- gradual rises or fades

- quick visual summarization of non-periodic changes



It is not ideal for sharp eclipses, transit models, or physically constrained stellar variability analysis.



### Periodic Fit



The periodic option builds a truncated harmonic series.



It:



- converts the chosen period from hours to days

- builds a design matrix of sine and cosine terms

- fits coefficients by linear least squares with `numpy.linalg.lstsq`

- evaluates the resulting harmonic model on a dense grid



The harmonic count is user-controlled. More harmonics allow more structure, but also increase the risk of overfitting sparse or noisy data.



### Period Guessing



The `Guess Period` tool uses Lomb-Scargle power estimation.



The implementation:



- requires enough timed observations

- searches over a frequency range derived from cadence and total span

- uses `astropy.timeseries.LombScargle.autopower`

- returns the period corresponding to the strongest peak



This result should be treated as a starting guess, not a confirmed period determination.



## Comparison-Star Selection



In automatic mode, reference stars are selected from Gaia stars that satisfy brightness and separation constraints.



Current selection rules include:



- Gaia magnitude must be available

- Gaia magnitude must lie roughly between 8 and 16

- stars too close to cataloged variable targets are excluded

- the remaining candidates are ranked by brightness and truncated to the configured limit



During differential photometry, a small nearby subset of those references is chosen for each target.



## Adaptive Apertures



If adaptive aperture mode is enabled, the app estimates a representative frame FWHM from sampled stars and scales the aperture and annulus radii by the configured multipliers.



That allows the measurement geometry to track seeing and focus better than a single fixed radius.



If the FWHM estimate is unreliable, the code falls back to the configured fixed radii.



## Sky View Renderer Architecture

The standalone Sky View still treats `QPainter` as the default and reference renderer. The current OpenGL work is staged and opt-in.

### Current State

- `QPainter` remains the default/reference Sky View renderer.
- The fullscreen GL atmosphere/mask path is opt-in via `CITIZEN_PHOTOMETRY_SKY_VIEW_GL_ATMOSPHERE=1`.
- When that GL atmosphere path is enabled and succeeds, it owns both atmosphere and flat ground for the atmosphere/ground render stage.
- The separate GL ground path behind `CITIZEN_PHOTOMETRY_SKY_VIEW_GL_GROUND=1` remains available, but it is mostly redundant when the fullscreen GL atmosphere path succeeds.
- The GL faint-star path remains experimental and should not be treated as the preferred performance path yet.

### What GL Atmosphere Replaces

When the fullscreen GL atmosphere path succeeds, that stage skips:

- CPU ground-mask and viewport-geometry generation for the atmosphere/ground stage
- QPainter atmosphere image generation
- QPainter sky-clip subtraction
- QPainter atmosphere `setClipPath`
- QPainter ground and GL-ground fallback work for that stage

### What Remains CPU Or QPainter

The following behavior still stays on the existing CPU or QPainter paths:

- collect, projection, and culling
- stars
- grid
- labels
- hover, selection, and picking
- object-inspector behavior
- footer and cardinal markers
- surrounding UI

### Diagnostics To Preserve

The GL atmosphere timing and detail diagnostics are part of the current stabilization contract:

- `glatmo_enabled`
- `glatmo_draw`
- `glatmo_fallbacks`
- `glatmo_qpainter_fallback`
- `cpu_geom_skipped`
- `cpu_atmo_gen_skipped`
- `cpu_atmo_clip_skipped`

### Safety Invariants

- The QPainter atmosphere/ground fallback must remain available.
- GL atmosphere must disable itself for the session on initialization, compile, link, or draw failure.
- The fullscreen shader horizon-ray math must stay consistent with the CPU screen-ray helper.
- Device-pixel-ratio and logical-viewport handling must not be changed casually.
- Labels, grid, cardinal markers, and footer must remain after the GL atmosphere pass.
- Default behavior should remain QPainter until a deliberate decision is made to promote the GL path.

### Recommended Next Steps

These are planned follow-ups, not current behavior changes:

- add screenshot smoke tests comparing the GL atmosphere path against the QPainter reference
- validate high-DPI and Windows scaling behavior explicitly
- consider making GL atmosphere default only after broader validation across machines and views
- plan the next OpenGL migration target separately; the likely next candidate is star rendering with persistent VBO-style batching rather than reviving the old `GL_POINTS` approach

## Sky View Milky Way Tiled Assets

The default interactive Sky View Milky Way target is the manifest-backed NASA SVS 2020 32k pyramid under `textures/milky_way_tiles_32k_padded_lzw_benchmark/manifest.json`. The older 16k pyramid is no longer part of the runtime asset set.

### Generation Contract

- Use `scripts/generate_milky_way_tiles.py` for offline pyramid generation.
- Production generation now defaults to `--tile-format tiff_lzw`.
- `png` remains supported as the broad fallback output format.
- `tiff_deflate` remains supported as the alternate TIFF-family output format.
- Use `--pad-gutter 1` for the production padded-tree contract so runtime tile sampling can rely on baked gutters instead of rebuilding them at draw time.
- EXR source generation expects the `OpenEXR` Python package to be installed in the active environment.

Example:

```powershell
.venv\Scripts\python.exe scripts/generate_milky_way_tiles.py textures\milkyway_2020_32k.exr textures\milky_way_tiles_32k_padded_lzw_benchmark --tile-format tiff_lzw --pad-gutter 1
```

### Runtime Invariants

- The OpenGL tiled-manifest path is the production route for both the default 16k Milky Way asset and the optional 32k high-detail asset.
- Normal rendering keeps the manifest base underlay drawn beneath detail tiles.
- Do not disable that base underlay in normal mode when detail tiles are resident: the detail tiles are intentionally translucent, so dropping the base creates false dark voids that look like missing coverage.
- The low-resolution base image is assembled from the manifest base level using each tile's declared `content_region`; padded gutters are not part of the stitched base image.

### Diagnostics And Validation

- Enable the live Sky View Milky Way developer panel with `CITIZEN_PHOTOMETRY_SKY_VIEW_MILKY_WAY_DEV_PANEL=1`.
- The runtime TIFF decode failure diagnostic is: `TIFF tile decode failed; Qt TIFF image plugin unavailable or decode error.`
- Development-environment Qt image-format smoke:

```powershell
.venv\Scripts\python.exe -m photometry_app.main --qt-image-format-smoke --qt-image-format-smoke-output _tmp_qt_image_format_smoke_result_source_cli.json
```

- Shipping-bundle Qt image-format smoke:

```powershell
_tmp_shipping_dist\CitizenPhotometryDebug\CitizenPhotometryDebug.exe --qt-image-format-smoke --qt-image-format-smoke-output _tmp_qt_image_format_smoke_result_final_release.json
```

- Focused wide/detail visual validation helper:

```powershell
Set-Item Env:CITIZEN_PHOTOMETRY_MILKY_WAY_TILE_MANIFEST "textures\milky_way_tiles_32k_padded_lzw_benchmark\manifest.json"; .venv\Scripts\python.exe _tmp_sky_view_milky_way_detail_validate.py
```



## Preview Selection Logic



Before full processing, automatic mode builds a preview list from the field catalog.



That preview can be reduced by:



- designation-family filters

- preview max-count cap

- preview max-magnitude cap



Saved explicit selections are retained even if they fall outside the preview cap.



## Exoplanet Entries



Exoplanet entries from the NASA Exoplanet Archive are included primarily for field inspection and metadata browsing.



They can appear in the source list and details pane, but the main photometry workflow is still centered on stellar source measurement and light-curve inspection.



## Troubleshooting



### Nothing Happens After Opening A Folder



Check:



- the folder really contains supported image files

- object folders are laid out under `Files/` if you are using a workspace root

- the work log for scan warnings



### The Preview Takes A Long Time To Open



Large fields can contain many variable-star catalog entries.



Use:



- preview max-count

- preview max-magnitude

- designation-family filters



to reduce the candidate list.



### Light Curves Look Too Noisy



Common causes:



- poor aperture sizing

- unstable or contaminated comparison stars

- blending

- weak signal in the selected filter

- variable transparency or seeing



Check the annotated image before blaming the fit.



### The Fitted Curve Looks Wrong



Try:



- lowering polynomial degree

- reducing periodic harmonics

- checking whether the guessed period is plausible

- switching the x-axis to JD for easier timing inspection



### A Target Appears But Should Not Be Measured



Possible reasons:



- catalog selection retained it intentionally

- it is an explicitly saved target

- the current designation filters still allow its naming family



### My Theme Does Not Persist



Theme persistence now works at two levels:



- workspace theme in `.photometry-settings.json`

- last-used app theme fallback for fresh workspaces



If a workspace explicitly stores a theme, that workspace setting wins.



## Developer Workflow



### Running The Test Suite



The repository currently uses `unittest`.



Run the full suite with:



```powershell

python -m unittest discover -s tests -p "test_*.py"

```



Run a targeted test module with:



```powershell

python -m unittest tests.test_settings

```



### Running The Demo Smoke Test



The repository includes a demo object and a smoke-test script.



Run it with:



```powershell

python scripts\run_demo_smoke_test.py

```



That command processes the `DemoOrion` object and exports results under `Exports/DemoOrion_smoke/`.



### Regenerating The Demo Dataset



If you need to rebuild the demo FITS files, run:



```powershell

python scripts\generate_demo_dataset.py

```



This script regenerates the `Files/DemoOrion/` demo frames using a solved synthetic field.



## Developer Notes



Important code locations:



- `photometry_app/core/photometry.py`: aperture photometry and active flux-error estimation

- `photometry_app/core/matching.py`: differential photometry and differential error propagation

- `photometry_app/core/error_calculations.py`: advanced uncertainty utilities

- `photometry_app/core/plotting.py`: payload building, curve fitting, period estimation, and export plotting

- `photometry_app/ui/main_window.py`: menu actions, current workflow, and plot controls

- `photometry_app/ui/dialogs.py`: settings, preview selection, and theme customization dialogs