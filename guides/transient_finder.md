# Transient Finder

## Introduction

Some of the most important things in the sky do not move -- they change.

A star that was quiet last night can brighten tonight. A source can appear where nothing was visible before. Another can fade until it is gone. Novae, supernovae, flaring stars, eclipsing systems at the right phase, and other abrupt or repeating brightness changes all share that signature: the same sky position, different brightness across repeated frames.

**Citizen Astronomy (CAst)** includes **Transient Finder** for exactly that problem. You open a folder of repeated FITS or XISF images of the same field. CAst solves missing WCS when needed, detects point sources, groups detections that share a fixed sky position, and screens those groups for real frame-to-frame flux changes -- rejecting the static stars and many seeing-driven false alarms that would otherwise dominate a naive search.

This is not asteroid hunting. Moving objects belong in Asteroid/Comet Detection. Transient Finder looks for **stationary** variables and appear/disappear events.

### What you can do with this mode

- **Search a night (or nights) of repeated frames** for positions that brighten, fade, appear, or disappear.
- **Solve unsolved frames** through astrometry.net when an API key is configured, caching solved WCS for reuse.
- **Use one shared Gaia DR3 veto catalog** for the whole sequence instead of pulling a deep catalog for every frame.
- **Review candidates visually** with Center Candidate, Blink, stretch/curves controls, and an Inspector summary.
- **Export blink animations** (GIF/MP4) for teaching, sharing, or follow-up discussion.
- **Train a local classifier** (when labels are available) so future searches can show an ML score beside each candidate.

### Why this matters

Professional transient brokers watch enormous fractions of the sky -- and still cannot watch every amateur field, every cadence, every filter. Your sequence may catch a flare, an eclipse, or a brief event that nobody else sampled at the right time. Transient Finder is a triage tool: it does not replace careful photometry or formal reporting, but it can point you to the handful of sky positions worth a closer look.

---

## How It Works

### Step 1: Switch Mode and Open a Folder

1. Choose **Transient Finder** from the Mode menu / launcher.
2. Click **Open** (or use `File > Open Folder` / `File > Open File` in this mode).
3. Select a folder containing repeated sky images.

**Open** loads the folder, clears prior results, and can preview the first supported image. It does **not** start the search automatically.

### Step 2: Tune Search Knobs

Before searching, the top row exposes:

| Control | Default | Role |
|---|---|---|
| **Min Frames** | 2 | Minimum number of strong-evidence frames preferred for a candidate |
| **Threshold** | 5.0 σ | DAOStarFinder detection threshold above clipped background noise |
| **ROI** | 3% | Border percentage excluded to reduce corner/edge artifacts |

### Step 3: Search

Click **Search**. The button shows **Searching...** while a background worker runs `search_transients_in_folder()`. Progress and the final report are written to the **Work Log**.

---

## Input Requirements

| Requirement | Detail |
|---|---|
| Formats | `.fit`, `.fits`, `.xisf` |
| Layout | A folder tree of repeated frames of roughly the same field |
| WCS | Embedded celestial WCS preferred |
| Astrometry fallback | If a frame is unsolved and an astrometry.net API key is set, CAst solves it and caches the result |
| WCS cache | Under the app cache directory as `transient-wcs/` |
| Minimum solved frames | Default 2 (configurable) |

Without an API key, unsolved frames are skipped. Observation timestamps come from image headers (with the configured observation timezone for naive values).

---

## The Search Pipeline

### 1. Scan and Resolve WCS

CAst recursively scans the folder for supported images, then for each frame:

1. Validates embedded WCS.
2. If unusable and an API key is available, solves via **astrometry.net**, writing solved products into the `transient-wcs` cache.
3. Frames that still lack WCS are skipped with a Work Log note.

### 2. Shared Gaia Veto Catalog

Instead of querying a deep Gaia catalog for every frame, Transient Finder builds **one combined footprint** for the solved sequence (mean center; radius large enough to cover all frames) and queries a **magnitude-limited Gaia DR3** cone:

- Default limit: **Gaia G ≤ 18.0**
- Used as a veto / neighbor catalog for the whole run
- Cached under the catalogs cache directory

This is much cheaper on crowded wide fields than a full deep pull per frame.

Crowding caps also apply later:

- Up to **25,000** detections retained per frame (brightest first)
- Final candidate list capped at the strongest **500** objects

### 3. Per-Frame Point-Source Detection

On each solved frame:

1. Convert to a mono plane (RGB images use a mean combination).
2. Estimate clipped background mean/median/stddev.
3. Run `DAOStarFinder` with:
   - FWHM default **3.0 px**
   - Threshold = `detection_sigma × stddev` (default 5σ)
4. Reject sources inside the ROI/edge margin.
5. Convert pixels to RA/Dec with the frame WCS.
6. Keep sources with peak SNR ≥ the detection threshold.
7. Record the nearest Gaia neighbor (name + separation) for context.

Detection SNR here is simply:

$$
\mathrm{SNR}_{\mathrm{det}} = \frac{\mathrm{peak}}{\sigma_{\mathrm{background}}}
$$

### 4. Group by Fixed Sky Position

Detections across frames are grouped if they fall within a **2.5 arcsec** grouping radius. Same-frame duplicates keep the higher-SNR detection. The group center is the mean sky position of its members.

This is the key difference from asteroid discovery: Transient Finder assumes the interesting source stays put.

### 5. Aperture Re-Measure and Variability Screening

For each group, CAst re-measures aperture photometry at the group center on **every** solved frame (not only frames that triggered a DAO detection). Aperture and annulus scales follow the detection FWHM:

$$
\begin{aligned}
R_{\mathrm{ap}} &= \max(3,\, 1.75\,\mathrm{FWHM}) \\
R_{\mathrm{in}} &= R_{\mathrm{ap}} + \max(3,\, 1.25\,\mathrm{FWHM}) \\
R_{\mathrm{out}} &= R_{\mathrm{in}} + \max(4,\, 1.75\,\mathrm{FWHM})
\end{aligned}
$$

Local background comes from a sigma-clipped annulus. Flux and uncertainties are:

$$
\begin{aligned}
F &= \sum_i (p_i - b) \\
\sigma_F &= \sigma_{\mathrm{local}}\sqrt{N_{\mathrm{ap}}} \\
\mathrm{SNR}_{\mathrm{ap}} &= F / \sigma_F
\end{aligned}
$$

### 6. Variability Metrics

Across the measured frame signals, CAst compares the brightest and faintest epochs:

$$
\begin{aligned}
\Delta F &= F_{\mathrm{bright}} - F_{\mathrm{faint}} \\
\sigma_\Delta &= \sqrt{\sigma_{\mathrm{bright}}^2 + \sigma_{\mathrm{faint}}^2} \\
\mathrm{variability\_snr} &= \Delta F / \sigma_\Delta \\
\mathrm{flux\_ratio} &= \frac{F_{\mathrm{bright}} + \mathrm{noise\_floor}}{\max(F_{\mathrm{faint}},\, \mathrm{noise\_floor})}
\end{aligned}
$$

where the noise floor is $\max(\sigma_{\mathrm{bright}},\, \sigma_{\mathrm{faint}},\, 1)$.

A group survives only if the change looks stronger than static stars / mild seeing:

1. $\mathrm{variability\_snr} \ge \max(7.0,\, 1.25 \times \mathrm{detection\_sigma})$
2. And at least one of:
   - $\mathrm{flux\_ratio} \ge 2.0$, or
   - an **absent epoch** (faint flux ≤ 0 or very low SNR), or
   - a strong **host-background** exception (very high variability SNR plus elevated flux and peak ratios)

Groups that fail are counted as rejected “static or seeing-driven” sources in the Work Log.

### 7. Soft Gaia Neighbor Veto

If the nearest Gaia star is within **5 arcsec** and the flux ratio is still below 2.0, the group is rejected. This reduces the flood of ordinary catalog stars that wiggle with seeing. Real events on top of bright hosts can still pass when the flux ratio is large enough or the host-background exception triggers.

---

## Candidates and Review UI

### Candidate Table

Surviving candidates are sorted by variability SNR (then flux ratio, then max SNR) and labeled `TF-001`, `TF-002`, …

Typical columns:

- Candidate
- RA / Dec
- Frames
- Median SNR
- Nearest Gaia
- First UTC / Last UTC
- Label
- ML Score

### Inspector

The Inspector summarizes the selected candidate: coordinates, frame/detection counts, median/max SNR, variability SNR, flux ratio, nearest Gaia separation, and per-frame detection details.

### Work Log

Search progress, skipped frames, rejected-group counts, and the final report text all land here. Prefer the Work Log over looking for a separate status strip.

### Image Panel

The right-side image view supports:

- **Display** stretch modes (Auto / Linear / Asinh / Sqrt / Log), **Curves**, **Invert**, **Reset**
- **Center Candidate** -- keeps the selected source centered
- Blink interval presets (default **0.35 s**)
- **Blink** -- play the candidate’s frame sequence
- **Export Blink** -- write GIF or MP4
- **Hide Info / Show Info**
- Crosshair overlays on the candidate position
- ROI margin visualization matching the search border

Blink paths include the measured frame sequence for that sky position, so you can see appear/disappear behavior even when only some epochs were strong detections.

---

## Exports

Transient Finder currently focuses on visual confirmation exports:

| Export | How |
|---|---|
| Blink animation | **Export Blink** → GIF or MP4 |

Default naming looks like `{folder}/{TF-xxx}_transient_blink.gif`. There is no dedicated candidate CSV / AAVSO exporter in this mode; the Work Log report is the textual summary.

For science-ready light curves of a confirmed variable, switch to **Differential Photometry** and measure the object properly.

---

## Machine Learning Assist

Transient Finder shares CAst’s local candidate-training store (`candidate-training.sqlite3` under the Citizen Photometry app data directory).

- **Train Model** is available on the workflow row.
- Labels can include Real, Artifact, Known Object, Moving Object, Noise, Unsure.
- Features include frame/detection counts, SNR/flux statistics, variability SNR, flux ratio, Gaia neighbor metrics, and time span.
- The classifier is a **Random Forest** (160 trees, balanced class weighting) once enough diverse labels exist.

The results table can show an **ML Score** column. Labeling controls may be less prominent in the current panel layout than Train Model itself; treat ML as an optional assist on top of the variability screen, not as the primary discovery engine.

---

## How This Differs from Other Modes

| Mode | Question it answers |
|---|---|
| **Transient Finder** | Did something at a *fixed* sky position change brightness across my repeated frames? |
| **Differential Photometry → Discover** | Among Gaia/VSX field stars, which ones look variable in a full differential light-curve sense? |
| **Asteroid/Comet → Discover** | What is *moving* across my aligned frames? |
| **HR Diagram** | How do stars in this *single* deep image sit on a color-magnitude diagram? |

Use Transient Finder for triage. Use Differential Photometry when you want calibrated light curves and AAVSO-ready products. Use Asteroid/Comet Detection when the signal is motion.

---

## Suggested Workflow

1. Collect a sequence of the same field (same pointing / filter family helps).
2. Open the folder in Transient Finder.
3. Set Min Frames, Threshold, and ROI for your data quality.
4. Click **Search** and watch the Work Log.
5. Sort/select candidates with high variability SNR.
6. Center and Blink to confirm by eye.
7. Export a blink for anything interesting.
8. Follow up promising sources in Differential Photometry (or external tools) for real measurements and reporting.

---

## Limitations

### What Transient Finder does not do

- **It does not find asteroids.** Movers will not form clean fixed-position groups and are the wrong tool target.
- **It does not produce calibrated magnitudes.** Metrics are instrumental flux ratios and SNRs.
- **It is not a broker or reporting client.** No automatic TNS/AAVSO/MPC submission path.
- **It is not a full variable-star pipeline.** No periodograms, comparison-star ensembles, or AAVSO Extended export here.
- **Gaia G ≤ 18 veto is incomplete.** Fainter hosts and crowded neighbors can confuse the soft veto.
- **Fixed-FWHM DAO detection** can struggle with bad seeing, trailing, saturation, or very extended profiles.
- **Caps exist.** Extremely dense fields are truncated per frame and at the final candidate list.
- **RGB is collapsed to mono** for detection/measurement.
- **Near-edge sources** can be lost to ROI margins or aperture padding even if briefly detected.

### What could be improved

- Optional candidate-table CSV export
- Stronger host-galaxy / difference-imaging paths for supernova work
- Explicit moving-object rejection using short-arc motion tests
- More visible labeling UI for the Random Forest assist
- Per-filter / per-night grouping controls for mixed folders

---

## Conclusion

The sky is not only a map of positions. It is also a record of change. Transient Finder gives amateur sequences a practical way to notice that change: solve the frames, find the point sources, keep the ones that truly vary, and blink them until the eye agrees.

Most candidates will be ordinary. A few will be worth your night. That is how transient astronomy has always worked -- and why a careful search of the data you already have can still matter.
