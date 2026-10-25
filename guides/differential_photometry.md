# Differential Photometry

Differential photometry measures how a target changes relative to stars in the same field. The target and comparison stars pass through nearly the same atmosphere and optical path, so much of the shared transparency variation divides out.

CAst turns an image sequence into a reviewable measurement workspace. It can identify cataloged variables and exoplanet hosts in the field, select comparison stars for each target, measure every frame, build light curves, estimate periods, search uncataloged field stars for variability, and write a science report with an AAVSO Extended Format file and preflight review.

The automation is a starting point, not a black box. You can inspect the source frame behind a point, change comparison stars, place apertures manually, review rejected measurements, and trace every exported result back to its inputs.

---

## What makes this mode useful

Professional differential-photometry programs already exist. CAst is useful for a different reason: differential photometry is one part of a broader, linked astronomy workspace.

- **The image and the result stay connected.** Select a target or measurement, inspect its aperture on the original frame, and compare it with the light curve and measurement table.
- **It is built for exploration and teaching.** Phase-folded curves, period fits, target-field animations, and O-C diagrams show the physical behavior behind a changing brightness measurement.
- **It analyzes the field, not only one typed coordinate.** CAst returns cataloged variables and exoplanet hosts and can run a separate discovery pass over eligible Gaia stars.
- **Automation does not remove manual control.** Accept the automatic ensemble, use Scan Comps, or construct an independent target/comparison/check-star reduction with Aperture Editor.
- **AAVSO preparation is part of the workflow.** The report includes an Extended Format file, accepted and rejected observations, reference-star records, provenance, and a preflight report. You review and revise before uploading.
- **CAst is free and open source.** The methods, limitations, and exported intermediate records can be inspected rather than hidden behind a proprietary reduction.
- **Differential Photometry is not the whole application.** The same desktop program also contains tools for HR diagrams, sky exploration, asteroid/comet work, and other image-based astronomy.

---

# Tutorial

Follow this section in order for a first reduction. Settings are collected later in a separate reference; the tutorial links to the relevant setting only when it first matters.

## 1. Start Differential Photometry mode

1. Launch CAst.
2. Open **Mode**.
3. Choose **Differential Photometry**.

The window title changes to **Citizen Astronomy - Differential Photometry**. The main action at the top left is **Open**.

> **Screenshot needed:** Mode menu or launcher with Differential Photometry selected.

Use this mode for a time series: repeated observations of the same field. HR Diagram mode is for measuring many stars in one image and is a different workflow.

## 2. Prepare the image sequence

### Supported inputs

The differential scanner accepts:

- `.fit`
- `.fits`
- `.xisf`
- `.tif`
- `.tiff`
- camera RAW formats decoded by LibRaw, including `.cr2`, `.cr3`, `.nef`, `.arw`, `.dng`, `.orf`, `.rw2`, `.raf`, and `.pef`

JPG and PNG files in the selected folder are reported and ignored.

Each useful frame should contain:

- an observation time, normally `DATE-OBS`;
- an exposure time;
- a filter name when filters need to remain separate;
- enough stars for registration, plate solving, and comparison photometry;
- valid celestial WCS, or enough pointing/scale information for CAst to solve the frame.

If a timestamp has no timezone, CAst interprets it using **Settings → Open Settings → Differential Photometry → Image Timestamp Timezone**. Set this before processing if the camera wrote local time.

### Monochrome and color-camera data

Differential measurement requires a two-dimensional image plane. A monochrome FITS/XISF/TIFF sequence is the normal input.

When the scan finds camera RAW files, CAst shows a warning before loading the dataset. Choose **Yes** only after reviewing the limitations. CAst decodes each RAW file as linear 16-bit RGB, then averages the demosaiced red, green, and blue channels into one synthetic luminance plane for aperture photometry.

This direct RAW path is convenient, but it is not equivalent to monochrome measurements through a defined photometric filter. Bayer demosaicing, camera white balance and channel response, differences between the colors of target and comparison stars, and incomplete filter or exposure metadata can introduce systematic error. Treat direct RAW results as provisional until they are validated. Do not submit them to AAVSO as a standard passband merely because CAst produced an export file.

For precision work with one-shot-color or other multichannel data:

1. calibrate and debayer the frames in the usual preprocessing software;
2. extract one consistent channel or a linear luminance plane;
3. save each result as a two-dimensional scientific image;
4. place one filter/channel sequence in the folder you will open.

Do not mix red, green, blue, and luminance measurements as if they were one filter. Process them as separate, consistently named series when color-dependent photometry is required.

Other multichannel FITS, XISF, or TIFF data are not collapsed automatically by the Differential Photometry measurement engine. Export a two-dimensional channel or luminance plane first.

### Calibrated versus uncalibrated frames

You can provide already calibrated light frames. Alternatively, enable **Calibration** in the Differential Photometry Workflow and supply bias, dark, and flat frames when prompted.

For camera RAW precision work, calibrate the sensor data before demosaicing in software designed for the camera format, then export one consistent linear channel or luminance sequence. CAst's direct RAW path begins with a demosaiced RGB decode, so applying masters afterward is not equivalent to calibration on the original Bayer samples.

Already aligned frames can be marked through **Settings → Alignment**. Do not claim a stack is aligned merely because the telescope tracked well; this option means the images share the same pixel geometry.

## 3. Open the folder

1. Click **Open** at the top left.
2. In **Differential Photometry Workflow**, find section **1. Folder**.
3. Click **Folder**.
4. Choose the directory containing the image sequence.
5. Wait while CAst scans the folder.

> **Screenshot needed:** The top-left Open button and the complete Differential Photometry Workflow window.

**File → Open Folder** is an alternative route to a folder scan. For a first run, use **Open** because it keeps the folder, pipeline options, progress, and **Generate** action in one window.

Do not use **File → Open File** for a differential sequence. Differential Photometry operates on a folder.

### Loaded Results

If CAst finds more than one object grouping, **Loaded Results** opens after the scan.

1. Review the Object, Files, Solved, Needs Solve, and Invalid columns.
2. Select the object you want.
3. Click **Use Selected Object**.

**Clear Cache for Selected Object** removes that object's saved scan/reduction state. Use it when the source files or saved manual apertures no longer match the intended reduction.

> **Screenshot needed:** Loaded Results with one object selected.

## 4. Configure the workflow

### Photometric Transformation (B/V)

Use **Photometric Transformation…** in the workflow or **Settings → Photometric Transformation…** to calculate and manage instrument profiles. This is separate from bias/dark/flat calibration. A catalog zero point alone does not remove the color response of the telescope, camera, and filters.

The default **Setup** tab supports RGB imaging filters, separate linear blue/green channels, and photometric B/V filters. Filter manufacturers can be selected or typed, with a model/series and other setup details. Brand information describes the equipment; it does **not** supply universal coefficients. A calibration measures the combined response of your telescope, camera, filters, and other optics. **What do I need?** explains reference fields and image requirements. Use **Close** to leave your current settings unchanged. Without an enabled calibration, differential photometry already proceeds without transformation. To disable an existing calibration, uncheck automatic transformation in **Advanced** and choose **Save setup**.

1. Choose your **Filter type** and **Filters** brand/model. Add any reducer, IR-cut filter, camera mode, or channel extraction details under **Advanced → Fit settings**. Telescope and camera can be filled from image headers when available. A saved calibration can instead be selected at the top; selecting it restores its equipment information.
2. Use **Choose images…** or **Choose folder…** to add calibrated observations of an AAVSO standard field, such as M67, NGC 7790, or a Landolt field. A folder includes supported images in its subfolders. Several repeats in both channels, unsaturated stars with good color coverage, and stable low-airmass conditions are recommended. Image requirements are explained in **What do I need?**; no confirmation checkbox is required.
3. Check the detected channels. In RGB mode, common blue/green names (including `B`/`G`, `TB`/`TG`, and manufacturer-prefixed names) are recognized. In photometric mode, CAst looks for B/V. Ambiguous names require a selection in the two channel menus. An entire RGB set can be selected: **red and other unused frames are listed and omitted from this B/V calibration**. Missing `FILTER` headers must be corrected in the images. The selected type supplies context; RGB green is not assumed to be Sloan g, Gaia G, or an already-standard V measurement.
4. Choose **Calibrate automatically**. CAst plate-solves the images, retrieves AAVSO VSP's standard-field catalog, measures standards once, selects a suitable SNR cutoff, screens unreliable measurements and exposures, fits the coefficients, and validates the result on separate stars. Automatic selection tries SNR 100, 75, 50, 40, 30, then 20, targeting 20 distinct standards measured in both bands after screening. It keeps the highest cutoff meeting that target and the required color coverage; if fewer are available, it keeps the cutoff giving the most usable standards, subject to the minimum validation requirements. Saturation, overlap, and other quality exclusions remain in force. The cutoff is never chosen by searching for a passing validation score. A network connection is required for the catalog lookup. Images need exposure time and observation date metadata. Known airmass outside 1–2 is rejected. A field wider than one degree is cropped around the standard field, such as M67, so the measured area stays within one degree. Frames that do not already share a plate-solution orientation are aligned before measurement.
5. When validation passes, choose **Use calibration** to save and enable the result in one action. Generate the science results again to apply it. In **Advanced**, **Save setup**, with automatic transformation unchecked, remembers the equipment without enabling a transformation. Recalibrate after equipment changes and review the calibration at least annually.

The **Advanced** tab separates **Fit settings** from **Measurements & residuals**, retaining manual measurement and refitting, CSV import/export, exact input header names, provenance, date, pairing interval, profile exports, the measurement table, and residual plots. **Minimum SNR** on Measurements & residuals shows the selected calibration signal-to-noise cutoff. Changing it updates exclusions without measuring the images again and switches off automatic SNR selection for subsequent image calibration. **Choose SNR automatically** in Fit settings restores automatic selection. **Automatically exclude outliers and unreliable exposures** controls quality screening during calibration and refitting. Click **Reason / validation** to group rows by exclusion category, such as SNR or overlapping apertures. Low-SNR, saturated, near-saturated, flagged, or overlapping-standard measurements are initially excluded. Missing catalog errors are not invented. Inspect images for blends with other field stars as well. Check or uncheck rows to adjust the fit, then choose **Refit and validate**; exclusions and their reasons are retained. Editing the setup or measurements invalidates the previous result.

The residual plot uses catalog B−V; triangles mark withheld validation stars. Gray crosses mark excluded measurements, and the plot scale follows included data. Excluded values outside the plot range remain available in the table and CSV. Screening uses a robust color-response estimate from fitting stars, with a 4.5-sigma cutoff including measurement uncertainty and ensemble scatter, and a 0.05 mag minimum rejection threshold. Frames with at least six fitting stars and more than 25% inconsistent measurements are excluded when another usable frame in that band remains. This also excludes validation measurements from those frames. A uniform brightness offset is handled by each exposure’s zero point. Individual validation-star residuals are never clipped to obtain a pass; both their per-star means and their individual-observation RMS must pass accuracy checks. Entire stars, including all their repeat exposures, are withheld to avoid testing on the fitting data. If an automatic fit fails, its measurements remain available for inspection and export. Direct camera RAW files and combined color cubes require separate linear channel extraction before this workflow; they are not automatically converted here.

The current acceptance limits are CAst implementation choices, not universal AAVSO rules: at least 12 distinct standards measured in both bands, a training color span of at least 0.8 mag, fitting and validation RMS no greater than 0.03 mag per band, maximum absolute mean validation-star residual no greater than 0.05 mag, and color-term uncertainty no greater than 0.1. Calibration observations with dates must fall within 31 days of the profile date. A failed fit can be inspected and its measurements exported, but cannot become an active profile. Do not remove standards solely to make a nonlinear response pass.

The fitting model is `B − b = Z_B,exposure + Tb_bv × (B−V)` and `V − v = Z_V,exposure + Tv_bv × (B−V)`, with instrumental magnitudes normalized by exposure time. Each exposure has its own zero point. The consistent color coefficient is `Tbv = 1 / (1 − Tb_bv + Tv_bv)`. Generalized least squares accounts for catalog errors shared across repeated measurements; the supplied catalog B and V errors are assumed independent because their covariance is unavailable.

During science reduction, CAst pairs B/V observations of each target within the selected time limit (default 120 seconds), without pairing across UTC dates or reusing an observation. Choose a shorter interval for rapidly varying objects. Both frames must share suitable, independent comparison stars with actual B and V catalog magnitudes and uncertainties. Gaia G is not used as a substitute. The ensemble is solved jointly, retaining color and coefficient covariance. Check stars are excluded from the comparison ensemble. A profile must match the current telescope/camera/optical setup and be within one year of the science observation. Target and comparison colors must lie within the fitted training range.

The calculation assumes negligible differential extinction. Science frames wider than one degree, and airmass outside 1–2, are left untransformed. Calibration images wider than one degree are cropped around the standard field before the fit. Missing airmass metadata is not proof of suitable observing conditions. Strongly mismatched, narrowband, dual-band, or emission-line observations require separate scientific validation and should not be represented as broadband B/V merely because a stellar fit is possible.

When transformations are available, the light-curve axis **Transformed Magnitude (B/V)** shows them separately from the original **Standard Magnitude** values. Unavailable points are not substituted into that view; its status message explains exclusions. Single-band observations without contemporaneous color remain untransformed. External target-color corrections and integrated RAW channel extraction are not included in this version. Independent per-filter SNR binning clears transformation results; transform the original paired exposures instead.

Profiles can be exported/imported as CAst JSON including all calibration observations and diagnostics. Imported profiles are refitted and revalidated. **Export TG/TA coefficients…** writes the B/V coefficients and uncertainties in INI form; it does not export a transformed ensemble to TransformApplier. Importing an INI coefficient list without calibration/validation evidence is not supported.

For measurements calculated elsewhere, **Load measurements CSV…** accepts these columns:

```csv
star_id,exposure_id,band,instrumental_magnitude,instrumental_error,B,V,B_error,V_error,enabled,reason,observation_date
000-EXAMPLE-1,B-frame-1,B,-7.123,0.004,12.450,11.820,0.010,0.009,true,,2026-10-01
```

`band` is the intended standard B or V band; instrumental magnitude is `−2.5 log10(net flux / exposure seconds)`. Catalog values for each star must be consistent across rows. Record the standard-field chart/source and calibration date in the dialog. This example illustrates the schema, not a real standard star.

AAVSO export now derives `TRANS=YES` from an applied calculation, never the legacy checkbox. Successful results use output B/V even when the input channels were TB/TG. Untransformed known channels cannot be relabeled as a different standard band through the global filter override. `MTYPE=STD` can still accompany `TRANS=NO` for zero-point-calibrated data. Accepted/rejected observation tables include transformation status, reason, profile ID, output band, magnitude, and error. The additional `*_transformations.json` report contains the calibration profiles, paired files, comparison IDs, color, covariance, and separate random/systematic error components. Shared coefficient and comparison-catalog errors must not be divided by the square root of the number of observations.

Method references: [AAVSO CCD/CMOS guide, Chapter 6 and Appendix D](https://aavso.org/wp-content/uploads/2026/07/CCD-CMOS-English.pdf), [Transform Generator guide](https://www.aavso.org/sites/default/files/software/tg/TransformGeneratorUsersGuideUpdate2023_Final.pdf), and [VSP standard-field API](https://archive.aavso.org/index.php/api-vsp).

Section **2. Pipeline** contains the reduction stages.

### Pipeline options

- **Calibration** — opens the calibration workflow before science measurement.
- **Star Alignment** — uses the alignment path for the sequence.
- **Plate-solving** — required and kept on. Embedded WCS may be accepted or repaired; otherwise CAst solves the frame.
- **Filter Targets** — required and kept on.
- **Light Curves** — required and kept on.
- **Pull Periods** — requests catalog/literature periods where available.
- **Calculate Periods** — lets CAst infer periods from measured data. Off by default; enable it when needed.
- **Create Measurements Table** — prepares the detailed table during the run. Leave it off when first-run speed matters; the Measurements tab can load it later.

Calibration and alignment are optional. Plate solving, target filtering, and light-curve construction are core stages.

### Target filters

Designation filters limit which catalog naming families enter the run. The available families include named variables and survey-style designations such as Gaia, ASAS-SN, ATLAS, and ZTF.

**Brightest Variables** limits processing to a percentage of the brightest eligible variables.

**Analyze Best** uses a field-screening path intended to keep the most useful measurable targets, including an AAVSO-friendly signal range. It is an alternative to the ordinary brightest-variable selection, not an extra brightness filter to stack on top.

For a first run:

1. leave the designation families enabled;
2. keep **Brightest Variables** at a manageable percentage if the field is very dense;
3. leave **Pull Periods** on; enable **Calculate Periods** only if you want to infer a period from these observations;
4. leave **Create Measurements Table** off unless you need every measurement row immediately.

> **Screenshot needed:** Filter Targets with Brightest Variables and Analyze Best visible.

## 5. Generate the reduction

Click **Generate** in the workflow window.

Section **3. Progress** shows the current stage. The main workspace also records detail in **Work Log**. While processing, **Close** changes to **Terminate** so you can stop the run.

The pipeline:

1. reads image metadata;
2. validates, repairs, or solves WCS;
3. queries Gaia DR3, VSX, and the NASA Exoplanet Archive for the field;
4. builds target-specific comparison pools;
5. estimates seeing and aperture sizes;
6. measures target and comparison fluxes in every usable frame;
7. constructs differential, calibrated, and standard-magnitude contexts;
8. applies measurement-quality analysis;
9. builds one light-curve series per source and filter;
10. pulls and/or calculates periods when requested.

### Select Sources To Process

Some runs show **Select Sources To Process** before full measurement.

- Filter by designation family or source type.
- Source types can include variables, checks, exoplanets, and references.
- **Process Default** applies the configured preview limits.
- **Process All Visible** takes every row remaining after the dialog filters.
- **Process Selected** takes only the highlighted rows.

Use **Process Default** for a first pass. Use **Process Selected** when you already know the objects of interest.

> **Screenshot needed:** Select Sources To Process with source-type filters and all three process buttons.

## 6. Read the workspace

After Generate, the workspace normally opens with:

- **Filter: Overview**
- Y axis **Standard Magnitude**

Overview is a quality-control view. It shows the selected target across filters together with comparison and check stars. Individual filter views also show their corresponding reference stars by default: selecting R shows the target and references measured in R. Use **Show references** and **Show check**, beside **Filter**, to hide or show the comparison stars and the check star on the light curve without changing the photometry. The check star is also marked on the image with a blue circle and square, separate from the gold comparison circles and the green target circle. Both switches apply to Overview and stay in their chosen state when switching filters. Single-filter fitting and folding remain available with references visible.

### Results tabs

- **Source Results** — catalog targets, discovery candidates, and the comparison rows connected to the selected target.
- **File Results** — per-frame processing and QA.
- **Measurements** — detailed measurement rows. Click **Measure** if the table was not created during Generate.
- **Run History** — previous runs and their metadata.

The gear next to Source Results opens **Filter Source Results**. Use this instead of looking for a permanent search row.

### Image panel

The image panel contains:

- **Display** options, including stretch, **Curves**, **Invert**, and zoom reset;
- frame selection and **Blink**;
- **Center** on the selected target;
- catalog shortcuts;
- **Aperture Editor** and **Apply**.

Display stretch changes the preview only. It does not change the measured pixel values.

### Light-curve panel

The light-curve toolbar includes:

- series navigation and Reset;
- **Fold**;
- **O-C**;
- fit controls and **Fit Period**;
- **Filter** and **Select**;
- **Export**.

**Select** is on the light-curve row with **Fold**, **O-C**, and **Filter**. It draws a rectangle on the light curve and selects the points inside it. Click **Select** again to turn it off. When the rectangle contains at least one point, the button becomes **Options**:

- **Remove** hides those points on the light curve and leaves them out of the extended report. It does not delete files or measurements.
- **Mark files** renames each selected file so the name starts with `(marked)`. Nothing is deleted.
- **Select again** is available after a removal, to draw another rectangle while the hidden points stay hidden.
- **Reset** puts the hidden points back on the light curve.

After **Remove**, the button returns to **Select**.

Open **Settings → Light Curve** for axes, fit mode, inference method, phase controls, grid, valid-only display, and aperture-tracking options.

> **Screenshot needed:** Complete Differential Photometry workspace with Source Results, annotated image, Overview, and Work Log visible.

## 7. Select and inspect a target

1. Click a target in **Source Results**.
2. Confirm that the image updates to the target and its apertures.
3. Confirm that the light curve updates to the selected source.
4. Use **Center** if the star is outside the current image view.
5. Change the frame selector or use **Blink** to inspect motion, clouds, tracking, or saturation across the sequence.
6. Click or navigate to a light-curve point and compare it with the corresponding frame and measurement detail.

Comparison stars used by the target remain connected to the result and appear as comparison rows. In Overview, keep the target, comparisons, and check star visible long enough to decide whether a trend is astrophysical or shared by the field.

Right-click a Source Results row for available catalog actions such as **Simbad**, **VSX**, **CDS**, and **Gaia**. Period pull/calculate and **Change Name...** actions are also available where applicable.

Right-click a plot series to change its **Icon** or **Color...**.

## 8. Review measurement quality

Start with the automatic result, then investigate rather than deleting a point only because it looks inconvenient.

Check:

- saturation and near-saturation;
- SNR;
- centroid shift;
- comparison-star scatter;
- background level;
- flux and flux error;
- global and local outlier flags;
- the original image at the same epoch;
- whether comparison and check stars show the same structure.

Use **Filter** to open **Filtering Settings**. This changes which points are shown/used in a view; it does not erase the source measurement record.

The report preserves accepted and rejected observations separately with rejection reasons.

## 9. Choose comparison stars

CAst begins with an automatic target-specific ensemble. Three levels of control are available.

### Automatic ensemble

For each variable, CAst ranks eligible Gaia stars primarily by magnitude similarity and then by separation. Known variables and stars near known variables are excluded from the comparison pool.

Comparison stars are labeled with the AAVSO Unique Identifier (AUID / UID) when they match the VSP sequence, or with an APASS designation when only APASS photometry is available. Gaia source numbers remain only when neither catalog has a match. Manual aperture names that you assign are kept.

Use the automatic result when the comparison series and check star remain stable and the target signal is convincing.

### Scan Comps

1. Select the target.
2. Click **Scan Comps**.
3. In **1. Limits**, optionally constrain magnitude difference, color difference, or separation.
4. In **2. Favourites**, mark promising stars.
5. In **3. Combinations**, inspect candidate ensembles.
6. Click **Use Selected** to apply the chosen set.

The hard delta-magnitude, color, and separation constraints default to off. This avoids rejecting all useful comparisons before the field has been inspected.

> **Screenshot needed:** Scan Comps showing Limits, Favourites, Combinations, and Use Selected.

### Independent manual reduction

1. Turn on **Aperture Editor** above the image.
2. Double-click to place the target.
3. Shift-click to place a comparison star.
4. Ctrl-click to place a check star.
5. Drag an aperture to move it.
6. Drag its handles to change aperture and annulus sizes.
7. Right-click an aperture or its Source Results row for **Delete** or **Change Name**.
8. Click **Apply**.

The manual editor begins as an independent configuration. **Keep Comparison Stars** in **Settings → Light Curve** can seed comparisons from the automatic result when wanted.

Apply remeasures the manual set. Existing curves remain visible until the new result is ready.

> **Screenshot needed:** Aperture Editor with target, comparison, and check apertures plus the Apply button.

## 10. Improve SNR without hiding time structure

Click **Increase SNR** to bin adjacent observations. The button becomes **Reset SNR** after a binned dataset is created.

CAst limits bin duration using the period and variability type:

- sharp variables and transits: 1.5% of the period;
- smooth variables: 5% of the period;
- other/default: 3% of the period;
- absolute default cap: 600 seconds.

The target number of frames is estimated from the SNR deficit and capped at 15 by default.

Outlier rejection inside bins is optional and is **off by default**. Configure binning under **Settings → Open Settings → Advanced → Increase SNR**.

Use Reset SNR to return to the unbinned cached series.

## 11. Periods, folding, and fits

### Pull versus calculate

- **Pull Periods** uses catalog/literature information where available.
- **Calculate Periods** infers a period from the measured series.

The same ideas are available from Source Results context actions for an individual object.

### Inference methods

Open **Settings → Light Curve → Infer**:

- **Harmonic Fit** is the default general method.
- **Box Least Squares** is designed for transit- or eclipse-like box-shaped signals.

Generalized Lomb-Scargle exists in internal analysis code, but it is not a third user-facing Infer choice in the current workspace.

### Display fits

The fit mode can be Polynomial, Periodic, Spline, or Auto. Periodic is the normal choice for a repeating variable. Use **Fit Period** after setting or calculating a plausible period.

### Fold

Click **Fold** to plot phase rather than chronological time. Phase is:

$$
\phi = \left(\frac{t-t_0}{P}\right) \bmod 1
$$

where \(P\) is the period and \(t_0\) is the selected phase anchor. A coherent repeated signal should align from cycle to cycle.

For an exoplanet host, start with BLS, inspect the inferred duration, fold the result, and verify that comparison/check stars do not show the same dip.

## 12. O-C diagrams

An O-C diagram compares an observed extremum time with the time predicted by an ephemeris.

1. Select the variable light curve.
2. Click **O-C**.
3. Confirm the star name.
4. Enter \(T_0\) in HJD and the period in days.
5. Choose whether the ephemeris refers to a minimum or maximum.
6. Use **Mark Extrema** and click measured extrema, **Import...** a file, or **Pull AAVSO...**.
7. Review the extrema log.
8. Use **Export...** to save the O-C records.

For cycle \(E\):

$$
C_E = T_0 + E P
$$

and:

$$
O-C = T_{\mathrm{observed}} - C_E
$$

A flat sequence near zero is consistent with the adopted ephemeris. A slope indicates a period/epoch mismatch. Curvature or structured changes can motivate a refined physical analysis, but do not by themselves prove a period-change mechanism.

**Pull AAVSO...** uses the API token in Science Export when supplied and can fall back to the supported public/catalog route.

> **Screenshot needed:** O-C dialog with ephemeris fields, extrema table, Mark Extrema, and Pull AAVSO.

## 13. Variable Ephemeris

Click **Ephemeris** to inspect event visibility without first opening an image sequence. Site coordinates and minimum altitude come from **Settings → Open Settings → Setup**.

Use this before observing to decide when a predicted eclipse, transit, maximum, or minimum is observable from the configured site.

**Light Curve** downloads other observers' AAVSO photometry, the same source as **Pull AAVSO…** in O-C. CAst folds those magnitudes on the VSX period and reference epoch, fits a smooth periodic curve, and draws it on the **Light curve** tab. Drag to pan and scroll to zoom, as on a measured light curve. The horizontal axis is epoch. A marker shows the current epoch, and the curve to the right of that marker is the expected future light curve, cycle by cycle.

The download opens on the latest 30 days. The end JD is the current time, and the start JD is 30 days earlier. Edit either date before downloading if you want a different window. It uses the API token in Science Export when one is saved, and otherwise uses the public VSX route. Choose one band when you want the curve in a single filter. If several bands come back, each band is drawn on its own. AID dates are JD. The VSX epoch is often HJD, so the marker can sit a few minutes off the true phase.

> **Screenshot needed:** Ephemeris Light curve tab with the current-epoch marker and upcoming cycles.

## 14. Search the field for variable candidates

Generate handles cataloged variables and exoplanet hosts. **Discover** is a separate search over eligible non-cataloged Gaia stars.

1. Complete an ordinary Generate run first.
2. Click **Discover**.
3. In **Run Discover**, leave the whole field selected or Shift-drag a region of interest.
4. Use **Clear ROI** to return to the full field.
5. Enable **Try multiple comparison-star pools** only when the additional search cost is justified.
6. Click **Start Discover**.

Candidates receive variability metrics and a 0–100 candidate score. A high score is a triage signal, not a discovery claim. Check VSX, SIMBAD, survey data, artifacts, and additional nights.

**Label** / **Train Model** supports local detectability labels such as Easy, Possible, Difficult, and Artifact. The optional Random Forest is trained only when labeled examples exist; it is not a universal pretrained classifier.

> **Screenshot needed:** Run Discover with ROI preview and the multiple-comparison-pools option.

## 15. Export plots and animations

The light-curve **Export** menu includes:

- theme-based plot;
- scientific plot;
- light-curve animation;
- **Target Field Animation**.

Target Field Animation is a visual editor. It can crop and align the target through the sequence, apply a display stretch, produce GIF or MP4, and combine the moving field with a still light curve and moving time marker.

Use animations to show whether the target itself changes, whether transparency moves through the field, and how an event maps to the measured curve. They are educational and diagnostic; the measurement table remains the quantitative result.

> **Screenshot needed:** Target Field Animation editor with Combined, Star Field, and Light Curve previews.

## 16. Review and export for AAVSO

CAst prepares the files; the observer remains responsible for reviewing and submitting them.

### One-time metadata

Open **Settings → Open Settings → Differential Photometry → Science Export** and set:

- Observer Code;
- Observer Name and Organization when wanted;
- AAVSO Export Filter fallback;
- AAVSO Sequence/Chart ID;
- AAVSO API Token when using AAVSO data tools;
- Image Timestamp Timezone;
- Transformed Data only when the observations truly are transformed;
- Reduction Notes.

Open **Setup** and enter observing-site latitude, longitude, and elevation. CAst uses them to estimate airmass when an image header does not contain it.

### Quality-control sequence

1. Generate the reduction.
2. Select **Overview**.
3. Use **Standard Magnitude** when a valid band-matched zero point exists.
4. Inspect target, comparisons, check star, exclusions, filters, and suspicious frames.
5. Select the source row or rows to export.
6. Choose **File → Export Report**.
7. Read **AAVSO Preflight Review**.
8. Correct metadata or reduction issues when possible.
9. Use **Export Anyway** only after understanding remaining warnings.

> **Screenshot needed:** AAVSO Preflight Review with STD/DIF counts, warnings, skipped rows, and Export Anyway.

The app does not upload observations automatically. Open the Extended Format file, make any required revisions, and upload it through AAVSO's submission system.

### Preflight warnings to review

- missing observer code;
- missing sequence/chart ID;
- DIF rows where STD was expected;
- unrecognized filter mapped to `O`;
- missing airmass;
- skipped or rejected measurements;
- mixed or incomplete metadata.

`TRANS=YES` is written only when the row is standard (`MTYPE=STD`) and **Transformed Data** is enabled. CAst does not calculate transformation coefficients for you.

### Report location and contents

Reports are written under a `science/<source-name>/` directory derived from the first source frame.

The bundle includes:

- `_measurements.csv`
- `_light_curves.csv`
- `_accepted_observations.csv` and `.json`
- `_rejected_observations.csv` and `.json`
- `_reference_manifest.csv`
- `_reduction_manifest.json`
- `_provenance_manifest.json`
- `_aavso_extended.txt`
- `_aavso_preflight.json`
- `_README.txt`
- `_summary.json`
- `_plots/`
- `_annotated_images/`

The AAVSO file uses:

`NAME, DATE, MAG, MERR, FILT, TRANS, MTYPE, CNAME, CMAG, KNAME, KMAG, AMASS, GROUP, CHART, NOTES`

DATE is Julian Date at mid-exposure. FILT is mapped from recognized aliases; unknown values use `O`. MTYPE is STD when a suitable standard-magnitude context exists, otherwise DIF.

---

# Settings reference

This section collects settings so the operating tutorial can remain coherent.

## General

- **Astrometry API Key** — enables astrometry.net as a parallel solving path.
- **Astrometry Timeout** — default 300 seconds.
- Cache and settings locations — diagnostic paths and reset actions.

Local Gaia matching and embedded WCS validation can still succeed without an astrometry.net key.

## Advanced: WCS Sanity Check

The current dialog exposes quality-sample controls rather than the old documented magnitude-bin controls:

- Enable — on;
- Frame Margin — 25%;
- Probe Start Radius — 10%;
- Quality Sample Cap — 32;
- Minimum Source SNR — 8;
- Hard Approval — 90%;
- Match Tolerance — 3 px;
- Residual Limit — 2 px;
- Isolation Radius — 2.5 × FWHM;
- Subtract Coherent Shift — on;
- Soft Accept — on;
- Soft Approval — 65%;
- Soft Residual — 1.5 px;
- Soft Shift — 2 px;
- CCVALS Difference — 5 px;
- Max Gaia Magnitude — 18;
- CCVALS Repair — on.

Change these only when Work Log shows that the default WCS sanity decision is unsuitable for a repeatable dataset.

## Advanced: Increase SNR

Important defaults:

- maximum default period fraction: 0.03;
- maximum duration: 600 s;
- target SNR: 30;
- maximum frames per bin: 15;
- type-aware fractions: sharp 0.015, smooth 0.05;
- inverse-variance flux weighting: on;
- in-bin outlier rejection: off;
- output dataset: derived, preserving the original series.

## Differential Photometry

- **Comparison Stars per Variable** — 5.
- **Frame Edge Margin** — 5%.
- **Saturation Filter** — on.
- **Reference-Star Magnitude Range** — off unless enabled.
- **Adaptive Aperture Scale** — 1.6 × FWHM.
- **Adaptive Annulus Inner Scale** — 3.0 × FWHM.
- **Adaptive Annulus Outer Scale** — 4.5 × FWHM.
- **Variable Star Limit Mode** — Percentage.
- **Brightest Variable Stars to Analyze** — 100% unless changed.
- **Preview Variable-Star Max Count** — 0, meaning no extra count limit.
- preview magnitude range — off unless enabled;
- designation families — all enabled when no saved subset exists.

The active settings UI uses adaptive FWHM-scaled apertures. Legacy fixed-radius values remain in code paths but are not the normal current mode.

## Science Export

See the AAVSO tutorial for observer, chart, filter, timestamp, transformed-data, and notes fields.

The Time Standard selector records the intended reporting context. Core differential reduction orders measurements from the observation timestamp, and AAVSO DATE is written as UTC Julian Date at mid-exposure. O-C imports can contain JD, HJD, or BJD values supplied by the source. The differential pipeline does not calculate a barycentric correction for each frame.

## Setup

Enter telescope/camera details and observing-site latitude, longitude, and elevation. The site is used for ephemeris altitude and computed airmass.

## Settings → Light Curve

This menu controls:

- X and Y axes;
- fit mode;
- Infer method;
- phase convention and anchor;
- harmonics and smoothing;
- plot grid and valid-only display;
- comparison retention;
- equatorial image grid;
- aperture tracking/recentering;
- WCS fallback behavior.

Automatic catalog photometry can recenter by up to 8 px. The interactive Light Curve recenter control has its own default maximum-shift setting of 6 px.

## Alignment

Choose **Aligned** only for a sequence placed on a shared pixel grid. Otherwise keep **Non-Aligned** so each frame is mapped correctly.

## Catalog Cache

Refresh or clear the field catalog cache when a catalog response is incomplete or no longer matches the intended query. Do not clear it routinely; cached field data makes repeat runs faster.

---

# How the calculations work

This section follows the implemented reduction in calculation order.

## 1. Observation times

The scanner reads `DATE-OBS`. XISF metadata can supply its observation start through the image reader. Naive timestamps use the configured Image Timestamp Timezone.

The light-curve sequence uses the observation time converted to UTC-based Julian Date. The AAVSO exporter shifts the timestamp by half the exposure:

$$
JD_{\mathrm{mid}} = JD_{\mathrm{start}} + \frac{t_{\mathrm{exp}}}{2 \times 86400}
$$

The core reduction does not calculate BJD/TDB. O-C data may be imported with JD, HJD, or BJD labels, so compare like time standards.

## 2. Optional calibration

Master frames are pixel-wise medians. The science frame is:

$$
I_{\mathrm{cal}} =
\frac{I_{\mathrm{light}} - I_{\mathrm{bias}} - s I_{\mathrm{dark}}}
{I_{\mathrm{flat,norm}}}
$$

with:

$$
s = \frac{t_{\mathrm{light}}}{t_{\mathrm{dark}}}
$$

when dark scaling is valid.

The flat is bias/dark corrected as configured, then divided by the median of its positive pixels. Flat values at or below the safety threshold are replaced by 1 to prevent division artifacts. Non-finite calibrated science pixels are set to zero.

## 3. WCS selection and recovery

CAst validates celestial axis types and the CRVAL, CRPIX, and CD/PC transformation. It then chooses a folder policy from a representative frame:

1. accept embedded WCS when image detections agree with Gaia;
2. repair CRVAL from CCVALS when the alternate pointing is coherent;
3. solve with local Gaia matching and, when configured, astrometry.net in parallel.

The first successful valid solver result is used. Each frame still receives its own policy application unless aligned-stack reuse is valid.

## 4. FWHM

For a candidate star, CAst uses a 15 × 15 pixel cutout.

1. Estimate local background with 3-sigma clipping.
2. Subtract the background and clip negative signal to zero.
3. Keep a core above:

$$
\max(0.03 I_{\mathrm{peak}},\ 1.5\sigma_{\mathrm{background}})
$$

4. Compute a signal-weighted centroid.
5. Bin signal in 0.5-pixel radial intervals.
6. Interpolate the radius at half the central peak estimate:

$$
\mathrm{FWHM} = 2r_{1/2}
$$

If the radial estimate fails, weighted second moments provide:

$$
\mathrm{FWHM} = 2.35482\sigma
$$

Accepted values are 0.8–14 px. Frame FWHM is the median from up to 24 bright stars. A valid per-star FWHM is preferred, with frame FWHM as fallback.

## 5. Aperture geometry and recentring

Default adaptive radii are:

$$
r_{\mathrm{ap}} = 1.6\,\mathrm{FWHM}
$$

$$
r_{\mathrm{in}} = 3.0\,\mathrm{FWHM}
$$

$$
r_{\mathrm{out}} = 4.5\,\mathrm{FWHM}
$$

The annuli are forced to remain separated from the aperture.

The automatic catalog position is refined with a local signal-weighted centroid. Automatic displacement is capped at 8 px from the WCS prediction.

## 6. Background-subtracted flux

Photutils circular apertures and annuli are used. Annulus pixels are 3-sigma clipped, and the median surviving sky level is subtracted:

$$
F = S_{\mathrm{ap}} - \widetilde{B}A_{\mathrm{ap}}
$$

where \(S_{\mathrm{ap}}\) is the aperture sum, \(\widetilde{B}\) is local background per pixel, and \(A_{\mathrm{ap}}\) is aperture area.

For positive flux:

$$
m_{\mathrm{inst}} = -2.5\log_{10}(F)
$$

## 7. Active flux and magnitude uncertainty

The live photometry pipeline estimates:

$$
\sigma_F =
\sqrt{
|F| +
n_{\mathrm{ap}}\sigma_B^2 +
\left(\frac{n_{\mathrm{ap}}^2}{n_{\mathrm{sky}}}\right)\sigma_B^2
}
$$

Here \(\sigma_B\) is the local annulus scatter, \(n_{\mathrm{ap}}\) is aperture area, and \(n_{\mathrm{sky}}\) is annulus area.

Magnitude propagation is:

$$
\sigma_m =
\frac{2.5}{\ln 10}\frac{\sigma_F}{F}
\approx
1.085736\frac{\sigma_F}{F}
$$

This is the uncertainty used by current measurements and exports.

`error_calculations.py` also contains tested routines for a gain/read-noise/dark CCD equation, scintillation, and empirical-scatter quadrature. Those routines are not currently connected to `PhotometryMeasurement` or AAVSO MERR. They must not be described as part of the active error budget.

## 8. Comparison ensemble

Comparison candidates are selected per target. Ranking is primarily by closeness to the target's catalog magnitude and then by angular distance. A Gaia source within 30 arcsec of a known variable is excluded.

For valid comparison flux errors:

$$
w_i = \frac{1}{\sigma_{F_i}^2}
$$

$$
F_{\mathrm{ref}} =
\frac{\sum_i w_iF_i}{\sum_i w_i}
$$

If no useful flux errors exist, the ensemble falls back to an unweighted median.

## 9. Differential magnitude

$$
\Delta m =
-2.5\log_{10}\left(\frac{F_{\mathrm{target}}}{F_{\mathrm{ref}}}\right)
$$

Its propagated error is:

$$
\sigma_{\Delta m} =
\frac{2.5}{\ln 10}
\sqrt{
\left(\frac{\sigma_{F_t}}{F_t}\right)^2+
\left(\frac{\sigma_{F_r}}{F_r}\right)^2
}
$$

Shared first-order transparency changes cancel to the extent that target and comparisons experience them equally. Color-dependent extinction, gradients, and local clouds need not cancel perfectly.

## 10. Calibrated and standard magnitude

Each usable reference gives:

$$
ZP_i = m_{\mathrm{catalog},i} - m_{\mathrm{inst},i}
$$

When every reference has a usable uncertainty:

$$
ZP =
\frac{\sum_i ZP_i/\sigma_i^2}{\sum_i1/\sigma_i^2}
$$

Otherwise CAst uses the arithmetic mean of the available zero points, not the median. Its fallback uncertainty is the standard error when more than one zero point exists.

The target magnitude is:

$$
m = m_{\mathrm{inst,target}} + ZP
$$

**Calibrated Magnitude** uses the comparison's available catalog magnitude context. **Standard Magnitude** resolves the measurement filter against band-matched catalog values, preferring AAVSO VSP and APASS where available and falling back toward Gaia when a scientific band match is unavailable.

Standard-magnitude error combines target instrumental error and zero-point error in quadrature when both exist.

## 11. Quality analysis

Every measurement starts with quality score 1.0.

| Condition | Score change | Excluded |
| --- | ---: | --- |
| SNR < 5 | −0.18 | No |
| SNR < 3 | — | Yes |
| Centroid shift > 2.5 px | −0.10 | No |
| Centroid shift > 4.0 px | — | Yes |
| Comparison scatter > 8% | −0.15 | No |
| Comparison scatter > 18% | — | Yes |
| Global MAD z > 4.5 | −0.28 | Yes |
| Local Hampel z > 4.5, window 2 | −0.28 | Yes |
| Each other soft flag | −0.05 | Depends on final score |
| Saturated/non-positive hard flag | — | Yes |
| Final score < 0.35 | — | Yes |

The global robust scale is:

$$
\sigma_{\mathrm{robust}} = 1.4826\,\mathrm{median}(|x-\widetilde{x}|)
$$

Near-saturation begins at 95% of the resolved saturation threshold.

The quality weight used where needed is:

$$
w_q = \frac{q}{\sigma^2}
$$

or \(q\) alone when no positive error is available.

## 12. Period search and fitting

The search lower bound is the larger of the configured minimum and twice median cadence. The upper bound is the smaller of the configured maximum and 95% of the observed span. Solutions within 0.5% of a boundary are rejected.

### Harmonic Fit

At trial period \(P\):

$$
f(t)=a_0+\sum_{k=1}^{K}
\left[
a_k\cos\left(\frac{2\pi kt}{P}\right)+
b_k\sin\left(\frac{2\pi kt}{P}\right)
\right]
$$

with up to six harmonics.

The search uses 240 logarithmically spaced coarse periods, then three narrower refinements. Candidate multiples 2P, 3P, and 4P are tested. Selection uses Bayesian Information Criterion, not residual RMS alone. Eclipsing-binary doubled-period convention is accepted within the implemented BIC tolerance.

### Box Least Squares

BLS tests box-like transit/eclipsing models over period and duration grids with frequency factor 8. It returns a period and event duration and also checks period multiples.

## 13. SNR binning

The desired frames per bin are:

$$
n =
\left\lceil
\left(\frac{\mathrm{SNR}_{\mathrm{target}}}
{\mathrm{median\ SNR}}\right)^2
\right\rceil
$$

subject to the frame cap and the period-derived duration cap.

Fluxes are inverse-variance averaged when enabled. Optional 3.5-sigma MAD clipping inside bins is off by default.

## 14. Discovery metrics

Discovery uses cleaned values when possible and calculates:

- RMS: sample standard deviation;
- MAD: median absolute deviation;
- amplitude: 95th minus 5th percentile, or max minus min for fewer than five points;
- typical error: median finite positive point error;
- reduced chi-square around the inverse-variance weighted mean;
- von Neumann ratio:

$$
\eta = \frac{\mathrm{mean}[(x_{i+1}-x_i)^2]}{\mathrm{Var}(x)}
$$

- Stetson residuals:

$$
\delta_i =
\sqrt{\frac{N}{N-1}}\frac{x_i-\bar{x}}{\sigma_i}
$$

- Stetson J from adjacent products:

$$
J = \mathrm{mean}\left[
\mathrm{sign}(\delta_i\delta_{i+1})
\sqrt{|\delta_i\delta_{i+1}|}
\right]
$$

- Stetson K:

$$
K =
\frac{\mathrm{mean}(|\delta|)}
{\sqrt{\mathrm{mean}(\delta^2)}}
$$

The 0–100 candidate score adds capped contributions:

$$
30\min\left(\frac{\log_{10}\chi_r^2}{1.4},1\right)
$$

for \(\chi_r^2>1\);

$$
20\min\left(\frac{\mathrm{MAD}/\sigma_{\mathrm{typ}}}{6},1\right)
$$

$$
20\min\left(\frac{A/\sigma_{\mathrm{typ}}}{10},1\right)
$$

$$
20\min\left(\frac{J}{4},1\right)
$$

for positive J; and:

$$
10\min\left(\frac{\max(0,2-\eta)}{1.5},1\right)
$$

The total is clipped to 0–100. Stetson K is reported but is not a direct term in this score.

The optional classifier uses a 160-tree Random Forest with balanced-subsample class weighting after the user has created labels.

## 15. Check-star diagnostics

For a check star with catalog magnitude:

$$
r_{\mathrm{check}} =
m_{\mathrm{check,cal}}-m_{\mathrm{check,catalog}}
$$

The report calculates the RMS of these residuals by series. This is an independent diagnostic; it is not added to each point's MERR.

## 16. Airmass

CAst first checks image-header airmass values. If absent and site/target coordinates are available, it transforms the target to altitude/azimuth and estimates:

$$
X = \sec z
$$

where \(z\) is zenith distance. This simple geometric form becomes less reliable close to the horizon.

---

# Limitations

- Photometry requires 2D image data; CAst does not perform a complete debayer/channel-extraction workflow inside Differential Photometry.
- Aperture photometry is not PSF fitting. Crowded and blended fields can require another program.
- First-order shared transparency can cancel, but second-order color extinction is not modeled.
- CAst does not calculate transformation coefficients. The Transformed Data flag is for coefficients established and applied by the observer.
- Standard Magnitude is only as reliable as filter matching, comparison catalogs, and field conditions.
- The active error pipeline does not yet include configured gain/read noise/dark current, scintillation, or empirical series scatter in each MERR.
- Core reduction times are not barycentrically corrected.
- Sub-millimagnitude work is limited by flat-field residuals, fringing, detector nonlinearity, scintillation, guiding, and other systematics not fully modeled here.
- Discovery scores prioritize inspection. They do not establish novelty or astrophysical class.
- AAVSO export is preparation, not automatic validation or submission. The observer must review the file.

---

# Troubleshooting

## No useful frames were loaded

- Confirm the folder contains FITS/FIT/XISF/TIFF, not rendered PNG/JPG files.
- Confirm the images are two-dimensional.
- Check DATE-OBS and exposure metadata.
- Review **Loaded Results** and Work Log.

## WCS fails

- Confirm the header contains a real celestial solution.
- Set an astrometry.net API key when remote solving is needed.
- Verify approximate scale and pointing metadata.
- Inspect Advanced WCS sanity settings only after reading the probe result in Work Log.

## The target is missing

- Check designation-family filters and preview limits.
- Reopen **Select Sources To Process** on a new run.
- Try Process All Visible.
- Confirm the object is returned by VSX or the NASA Exoplanet Archive for the solved field.
- Use Discover only for eligible non-cataloged Gaia candidates; it is not a replacement for a missing catalog query.

## The light curve follows the comparisons

- Inspect Overview.
- Check clouds, gradients, meridian flip, focus, and alignment.
- Use Scan Comps.
- Add a check star.
- Try a manual independent ensemble.

## Standard Magnitude is unavailable

- Confirm the filter name is recognizable.
- Confirm comparison stars have usable band-matched VSP/APASS/catalog values.
- Differential Magnitude remains valid as a relative view when no standard zero point exists.

## AAVSO preflight reports DIF

The row lacks a suitable standard-magnitude calibration. Do not rename DIF to STD manually. Fix the filter/catalog/comparison context or submit it as the measurement type it actually is.

## Clear cache choices

- **Clear Cache for This Folder** resets the current folder and saved manual apertures.
- **Clear Cache for Selected Object** resets one Loaded Results object.
- **File → Clear All Cache...** removes all cached application data while retaining settings.

Use the narrowest action that solves the mismatch.
