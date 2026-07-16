# Asteroid and Comet Detection

## Introduction

Most of the Solar System is invisible until something moves.

In a single deep exposure, an asteroid can look like any other faint star. In a sequence of frames, it drifts. That drift is the signal: a rock or icy nucleus crossing your field while the background stars stay put. Professional surveys find thousands of these movers every year -- and still miss faint, fast, or poorly timed objects that pass through amateur fields with no one watching.

**Citizen Astronomy (CAst)** turns a plate-solved image or a folder of subframes into a guided moving-object workstation. You can recover catalog-known asteroids and comets predicted in your field, blink the sequence to confirm motion by eye, align frames onto a common sky grid, search for unmatched movers with **Discover**, confirm faint candidates with **Synthetic Track**, inspect paths with **Plots**, and explore orbits in a heliocentric **Trajectory View** built from JPL Horizons.

### What you can do with this mode

- **Recover known objects.** Query SkyBoT (with Horizons / Miriade backup for special cases) for asteroids and comets predicted in your footprint, then score which ones are actually visible in your pixels.
- **Estimate your depth.** Use Gaia stars as a practical visible-magnitude probe so you know which predicted objects are realistically within reach.
- **Align sequences.** Reproject already-solved frames onto one reference WCS and write an `aligned/` folder without touching the originals.
- **Discover unmatched movers.** Subtract a static sky model, find residual detections, link them into tracklets, and separate stronger **Potential Discoveries** from **Borderline Review** candidates.
- **Confirm with Synthetic Track.** Shift and stack on predicted motion to pull a faint mover out of the noise.
- **Review with Plots and Trajectory View.** Inspect predicted-versus-measured paths on the sky, and open a 3D heliocentric scene with major planets and time playback.
- **Blink and export.** Preload frames for smooth playback, track an object across the sequence, and export annotated images, blink animations, or trail movies.

### Why this matters

Catalog ephemerides tell you where an object *should* be. Your images tell you whether it *is* there -- and whether something else is moving that the catalogs never listed for that night. Recovering a faint comet, confirming an interstellar visitor such as 3I/ATLAS against your own data, or isolating an unmatched tracklet for follow-up is real observational work. CAst is built to make that workflow visual, reviewable, and exportable.

---

## How It Works

### Step 1: Open a Source

Switch to **Asteroid / Comet Detection** and open either:

- a **single** solved FITS / FIT / XISF image, or
- a **folder** of solved subframes (a sequence)

Use `File > Open File`, `File > Open Folder`, or the primary **Open** button when no source is loaded yet.

If the folder contains mixed filters or exposures, CAst may ask you to choose groups first (**Select Asteroid/Comet Groups**). You can load one or more groups; the live **Group** selector always includes an **All** option plus per-filter / per-exposure subsets.

After a folder loads, detection can start automatically. The primary workflow button then becomes **Generate** for the loaded source.

### Step 2: Understand the Workspace

The mode is organized around:

| Area | Role |
|---|---|
| Top workflow row | **Open / Generate / Apply**, **Align**, **Discover**, **Trajectory View**, **Synthetic Track**, **Plots**, **Group**, **Frame** |
| Image pane | Annotated field with overlays, blink controls, levels, stretch |
| Predicted Objects table | Known predictions (and temporary discovery candidates) |
| Work Log | Narrative progress for Generate, Align, Discover, Estimate |
| Inspector | Frame, setup, field, and selected-object details |
| Selection actions | Synthetic Track, Plots, Trajectory View for the selected row |

### Step 3: Generate Known Predictions

**Generate** is the heart of known-object recovery. It:

1. Resolves usable WCS for each frame (header → local Gaia fallback → optional astrometry.net).
2. Queries predicted solar-system objects inside the field.
3. Projects those predictions into the current frame.
4. Measures local peaks near each prediction.
5. Scores confidence and fills the **Predicted Objects** table.

When a visible-limit estimate is pending, the primary button can become **Apply**, which stores the new magnitude limit and regenerates.

---

## Known-Object Prediction

### Ephemeris Sources

CAst uses a layered prediction chain:

1. **SkyBoT** (IMCCE) -- primary cone search for asteroids and comets in the field footprint. Position-error handling is relaxed so high-uncertainty objects are less likely to be dropped by wrapper defaults.
2. **Interstellar / special-case refresh** -- for identities such as `3I` / `3I/ATLAS`, CAst can refresh ambiguous or magnitude-less rows using **JPL Horizons**, with **IMCCE Miriade** as another targeted path.
3. Frame timing uses mid-exposure when possible (`DATE-OBS` + half the exposure).

Predicted magnitudes (**Pred Mag**) come from the ephemeris services. They are useful for triage, but they are not a strict V-band guarantee across every catalog path.

### Local Image Confirmation

For each prediction, CAst searches a local window around the predicted pixel and measures a peak:

- Search radius grows modestly with expected trail length (typically clamped between about 5 and 18 pixels).
- Local SNR uses a robust background estimate: peak minus median, divided by $1.4826 \times \mathrm{MAD}$.
- Expected trail length in pixels is estimated from predicted motion rate, exposure time, and plate scale.

A detection is treated as **likely visible** when local SNR is at least about **4** and the match offset is within about **8** pixels.

### Confidence Score

Confidence is a triage score in $[0, 0.99]$, not a formal classification probability:

$$
\begin{aligned}
\mathrm{score}
&= 0.1 \\
&+ \min\!\left(0.25,\;\frac{m_{\mathrm{lim}} - m_{\mathrm{pred}}}{\max(2,\,m_{\mathrm{lim}})}\right) \\
&+ \min\!\left(0.4,\;\frac{\mathrm{SNR}}{20}\right) \\
&+ 0.25\left(1 - \frac{\min(\Delta,\,10)}{10}\right) \\
&+ 0.05\ [\mathrm{if\ trail} > 1.5\,\mathrm{px}] \\
&- 0.15\ [\mathrm{if\ near\ edge}]
\end{aligned}
$$

Status labels roughly map as:

- **High-confidence match** -- likely visible and score ≥ 0.75
- **Plausible visible match** -- likely visible and score ≥ 0.5
- **Predicted in field; visual confirmation weak** -- bright enough in catalog terms, weak in the image
- **Predicted in field** -- ephemeris hit without strong local confirmation

### Predicted Objects Table

Typical columns include:

- Object, Type, Class
- **Pred Mag**
- Confidence
- Motion / Direction
- Exposure Motion (point-like versus trailed expectation)
- Altitude

Select a row to center overlays, open **Plots**, open **Trajectory View**, or run **Synthetic Track**.

Default magnitude limit is **18.0** (Settings → Asteroid/Comet). A manual override can be enabled there as well.

---

## Visible-Limit Estimate

How deep is *this* image? Before trusting that a mag-19 prediction "should" be visible, CAst can probe real Gaia G stars on the frame.

### Algorithm

`estimate_visible_magnitude_limit()`:

1. Projects Gaia stars into the image.
2. Walks magnitude bins in **0.5 mag** steps starting from a configurable start magnitude (default **15.0**).
3. In each bin, samples up to **6** stars (default) and asks whether at least **3** are recovered at SNR ≥ **4** with a small centroid offset (about 5 px).
4. Stops after consecutive unsuccessful bins.
5. Reports the dimmest successful Gaia G as the estimated visible limit.

Progress is written to the **Work Log**. When an estimate completes, the primary button may switch to **Apply** so the new limit is stored and Generate is re-run.

> Note: The dedicated toolbar **Estimate** control exists in code but is currently hidden from the main workflow row. Discover can still run a Gaia visible-limit estimate internally, and Estimate Options remain available under Settings.

Gaia G depth is a practical proxy. Trailed movers, different bandpasses, and crowded residuals can still change what you can recover.

---

## Align

**Align** reprojects an already plate-solved multi-frame sequence onto the current reference frame's WCS.

- Uses WCS-based reprojection (with optional star-residual refinement).
- Writes derived FITS copies into an `aligned/` subfolder.
- Leaves original files untouched.
- Reloads the aligned sequence for blink / Discover / Synthetic Track review.

Use Align when frames share the same field but are not yet on a common pixel grid. Discover and Recover flows can also ask whether the current subgroup is already aligned so they can skip redundant reprojection.

---

## Discover

**Discover** searches the current solved subgroup for moving candidates and benchmarks them against known predictions. It is the main image-first discovery / recovery review path in the visible UI.

### Requirements

- A **specific** Group (not **All**)
- At least **3** timed, solved frames
- Prior **Generate** results for known-object association
- Preferably aligned frames (or allow Discover to align first)

### Pipeline Options Popup

Before the run, **Discover Pipeline** lets you choose:

| Option | Meaning |
|---|---|
| Preset | Detailed / Fast / Custom |
| Alignment | Align first, or already aligned |
| Working binning | Off, 2×2, 3×3, 4×4 |
| Motion prior bias | Balanced, main-belt, or near-Earth rate windows |
| Temporary working cache | Speed / disk tradeoff for prepared frames |
| Residual SNR min/max | Optional residual filters (0 = disabled) |
| Min seed displacement | Minimum motion between seed detections (default 1.5 px) |
| Frames per batch | 0 = whole group; otherwise overlapping windows |
| Retry detailed if empty | Useful with Fast preset |

Fast mode typically bins, raises residual SNR, and may batch frames for a quicker first pass, optionally retrying a detailed search if nothing is found.

### What the Pipeline Does

1. **Prepare frames** -- optional align / bin / cache.
2. **Build a static sky model** -- temporal median of the aligned sequence.
3. **Make residuals** -- subtract the static model from each frame.
4. **Detect residuals** -- point, streak, or hybrid detectors (`DAOStarFinder` for points; streak cuts use area / elongation thresholds).
5. **Link tracklets** -- connect detections across frames with approximately linear motion.
6. **Optional synthetic sweep** -- velocity-grid shift-and-stack pass for faint movers that never linked cleanly.
7. **Linearity screen** -- keep cleaner tracklets, review softer ones, suppress junk.
8. **Associate known objects** -- report which catalog predictions were recovered versus missed.

### Residual Detection Defaults

| Setting | Default |
|---|---|
| Detector mode | Hybrid |
| Detection sigma | 5.0 |
| Detection FWHM | 3.0 px |
| Max residuals per frame | 24 |
| Edge margin | 6 px |
| Streak min area | 6 px |
| Streak min elongation | 1.8 |
| Min linked frames | 3 |

Advanced controls live under **Settings → Open Settings → Asteroid/Comet → Discovery Advanced**.

### Tracklet Linking

Seed pairs are formed across nearby frames, filtered by minimum displacement and a motion-rate prior, then fit with a linear model in image coordinates versus time:

$$
x(t) = a + v_x t, \qquad y(t) = b + v_y t
$$

Detections rematch within about **2.8 px** of the predicted track. The fit can iterate a few times. Tracklets need enough frames (default ≥ 3) to survive.

### Potential Discoveries vs Borderline Review

After linking, CAst measures how linearly the tracklet behaves (deflection RMS):

| Bucket | Default RMS threshold | Role |
|---|---|---|
| **Potential Discoveries** | ≤ 0.9 px | Stronger candidates mirrored to the main table when marked |
| **Borderline Review** | ≤ 1.8 px | Review-only weaker / noisier tracklets |
| Suppressed | Above review threshold | Discarded as high-deflection junk |

### Synthetic Sweep (Discovery Advanced)

When enabled, Discover can run a final velocity-grid sweep:

- Max motion (default 12 px/h)
- Motion step (default 1 px/h)
- Angle step (default 30°)
- Optional main-belt direction focus (±45° half-width)
- Minimum stacked SNR (default 6)
- Optional dump of tested stacks

This catches movers too faint or sparse for ordinary residual linking.

### Discover Results Dialog

Tabs typically include:

- Known Recovered
- Known Missed
- Potential Discoveries
- Borderline Review

Useful actions:

- **Trajectory...** -- path review for a selected tracklet
- **Mark Selected / Mark All** -- push stronger candidates onto the main image / Predicted Objects table as temporary `Candidate ...` rows
- **Synthetic Track...** -- confirm the selected candidate
- **Export...** -- Benchmark, Candidate Review, and Summary CSVs
- **Train Model** -- label Real Mover / Artifact / Known Object / Noise / Unsure for the local Random Forest helper
- **Continue** -- continuation path for sweep / follow-on stages when offered

### Session Restore

Closing the review dialog is not destructive for the latest matching Discover result. Clicking **Discover** again for the same subgroup can offer:

- **Restore Last Result**
- **Run New Scan**
- Cancel

Only stronger Potential Discovery candidates are mirrored into the main Predicted Objects table for continued centering, marking, Plots, and Synthetic Track after the dialog closes.

> **Recover Known** remains implemented as a dedicated recovery/benchmark workflow, but its toolbar button is currently hidden. Discover now embeds much of that recovery reporting (known recovered / missed) in its results dialog.

---

## Blink

Blink is how humans still beat algorithms on ambiguous movers.

### Playback

- Toggle **Blink** (needs ≥ 2 frames).
- Frames are **preloaded** into rendered image caches before smooth playback.
- Default cadence is **50 ms** per frame (Settings; clamped roughly 50–2000 ms).

While blink is active, CAst prefers a fast image-swap path so the UI stays responsive on large FITS sequences.

### Track Object and Center Object

- **Track Object** keeps the selected prediction (or candidate) centered while blinking. The anchor can be **predicted** (default) or **measured** under Visuals settings. In unaligned sequences, predicted coordinates are recomputed per frame from that frame's own WCS and timestamp.
- **Center Object** is a one-shot recenter / zoom on the current selection.

### Hide Info

**Hide Info / Show Info** toggles the right-side image information panel so the field can fill more of the view during review.

### Adjust Levels

**Adjust Levels** opens a downsampled preview dialog for black / midtone / white-point edits. Changes apply to the main view only after confirmation. Stretch and invert remain available in the image toolbar.

---

## Synthetic Track

A single frame may barely show a faint mover. **Synthetic Track** shifts every frame onto the object's predicted motion and stacks the result so the target stays fixed while stars streak.

### How it Works

1. For each frame, predict the object's pixel position at that timestamp.
2. Crop (default radius **24 px**) or optionally shift the full frame.
3. Align those patches/frames to a common object-centered grid.
4. Combine with configurable integration, weighting, and rejection modes.
5. Measure the stacked peak (SNR, offset, flux).

Default combine behavior is an average stack with PSF-oriented weighting available in advanced mode. Mixed **All** groups are blocked unless Settings explicitly allow them.

### Preview Dialog

The preview reports:

- Used / skipped frames
- Stacking motion rate and angle
- Stacked SNR, center offset, peak, flux
- Per-frame inclusion details

**Ctrl + click** overrides a misidentified measured peak and recomputes metrics. **Reset to Auto** restores automatic peak finding. You can export the preview image from the dialog.

Use Synthetic Track on:

- generated known objects after Generate
- temporary discovery candidates after Discover

---

## Plots

Toolbar label: **Plots**. Window title: `Trajectory - …`.

This is the scientific path/residual review for the selected object across the current frame group.

### For Known Objects

- Predicted path versus measured path
- Image-plane trajectory
- Sky-plane offsets
- Offset versus time
- SNR versus time
- Frame table with predicted/measured coordinates, RA/Dec, offset, and SNR

Dark scientific styling keeps the focus on residuals and match quality.

### For Discovery Candidates

- Measured tracklet path with a linear fit
- Residual / quality readouts appropriate to the candidate
- Same darker presentation language as known-object review

Open Plots from the main table after Generate, or from Discover / recovery review dialogs via **Trajectory...**.

---

## Sky View

Results-panel tab: **Sky View**.

This is an **isolated asteroid/comet motion canvas**. It deliberately draws only predicted movers and the selected object's scrubbed trail — not background field stars — so you can watch how objects in the field move over time without stellar clutter.

Controls:

- **Time** slider — scrub hours before/after the frame UTC (`UTC ±… h`)
- **Now** — return to the observation time
- **Reset View** — recenter the field

Select a Predicted Objects row to highlight it, draw its trail, and scrub motion. Click markers to jump between objects.

---

## Trajectory View (3D)

Toolbar label: **Trajectory View**. Window title: `3D View - …`.

This is the heliocentric orbit theater for one or more selected known objects.

### What it Shows

- JPL Horizons **heliocentric state vectors** for the selected objects plus Earth
- A GPU-backed OpenGL scene (soft-fails gracefully if OpenGL is unavailable)
- Major-planet context, orbital arcs, and object labels
- Side scientific plots and a frame / time table
- Playback through the observed window and extended spans

### Camera and Span

Camera modes include:

- Orbit Overview
- Top-Down
- Side View
- Object Follow
- Earth Follow

Span presets typically include a local window around the observations plus ±90d, ±180d, 1y, and 5y. Extending the span keeps the currently selected target set.

### Objects Menu

You can:

- Toggle visibility of initially selected targets
- Add other generated objects from the current run
- Search nearby field objects
- Look up objects by name / designation
- Use planner-style searches for future observation windows

Separate asteroid / comet color styling and label controls live in the dialog Settings.

Trajectory View answers a different question from Plots: not "did this peak match the prediction in my frames?" but "where is this object in the Solar System around the nights I observed?"

---

## Inspector, Setup, and Visuals

### Inspector

The Inspector summarizes:

- Observation date/time
- **Moon illumination**
- Filter / exposure
- Field scale
- Setup details from Settings
- Selected-object metrics (predicted position, motion, SNR, offsets)

### Setup Tab

Store telescope, camera, focal length, aperture, pixel size, derived pixel scale, location, and Bortle class. These feed the Inspector and related metadata displays.

### Visuals

Under Asteroid/Comet Visuals you can control:

- Labels and prediction crosshairs
- Selected-object highlighting versus other generated objects
- Overlay circle / line / label colors and sizes (also themeable)
- Invert-aware annotation colors
- Track Object anchor mode (predicted vs measured)
- Separate styling for marked potential discoveries

### Work Log

Prefer the Work Log while long tasks run. Generate, Align, Discover, and Estimate write a readable narrative there, including visible-limit probe progress.

---

## Exports

| Export | How |
|---|---|
| Annotated still image | **Export → Image...** |
| Blink animation (GIF/MP4) | **Export → Blink...** (resolution / looping in Settings) |
| Trail animation | **Export → Trail...** |
| Discover benchmark / candidate / summary CSVs | Discover dialog **Export...** |
| Recovery benchmark / unmatched / summary CSVs | Recover Known dialog (if invoked) |
| Synthetic Track preview | Preview dialog export |
| Residual debug products | Discover debug / test paths under working outputs |

Blink export uses the configured blink cadence and GIF scale. Animations are especially useful for talks, classroom demos, and sharing a mover that is obvious only when frames are played in sequence.

---

## WCS and Plate Solving

Every frame needs celestial WCS. Resolution order:

1. **Embedded celestial WCS** in the header (including some incomplete headers that can be normalized).
2. **Local Gaia fallback** -- if pointing / optical metadata exist but celestial WCS keywords do not, CAst can solve rotation and parity by matching detected stars to Gaia (`local_wcs.py`). Typical acceptance needs enough matches with low residual RMS.
3. **astrometry.net** -- if an API key is configured, unsolved frames can be submitted and cached.

Without WCS, predictions cannot be projected and Discover cannot link sky-consistent movers.

---

## Suggested Workflow

1. Open a solved subframe folder and load the relevant Group.
2. Let **Generate** populate Predicted Objects.
3. Blink and Track Object to confirm obvious known movers by eye.
4. **Align** if frames are solved but not on one grid.
5. Run **Discover** for recovery benchmarking and unmatched tracklets.
6. Mark interesting Potential Discoveries onto the main table.
7. Confirm faint targets with **Synthetic Track**.
8. Inspect geometry with **Plots**; open **Trajectory View** for orbital context.
9. Export blinks, trails, and CSV summaries for records or collaboration.

---

## Limitations

### What this mode does not do

- **It is not an automated MPC submission pipeline.** Discover produces reviewable candidates and benchmarks; human judgment and external reporting tools are still required.
- **Gaia G depth is not asteroid detectability.** Trailing losses, filter differences, and background structure matter.
- **Confidence is triage.** High confidence means "worth looking at," not "certified identity."
- **Linear tracklets assume short arcs.** Strongly curved motion, very sparse sampling, or severe trailing can break linking.
- **All-group Discover is blocked.** Mixed filter/exposure stacks need a specific subgroup.
- **Aligned outputs are FITS copies.** Originals stay intact; XISF rewrite is not the alignment product.
- **Network services can fail.** SkyBoT, Horizons, Miriade, Gaia, and astrometry.net all need connectivity when used.
- **OpenGL Trajectory View is optional.** The rest of the mode still works if the 3D scene cannot start.
- **Standalone Estimate / Recover Known toolbar buttons are currently hidden.** Their logic remains available through Settings, Discover internals, and code paths.

### What could be improved

- Broader automated reporting formats for follow-up networks
- Richer non-linear / accelerating tracklet models
- Deeper PSF-aware residual detection in crowded fields
- More general interstellar / special-object handling beyond hardcoded high-priority identities
- Fully offline ephemeris packs for remote observing sites

---

## Conclusion

Asteroids and comets announce themselves by moving. CAst gives you the two things that matter most after that: ephemerides you can trust enough to search, and image tools strong enough to confirm, reject, or discover.

Generate places the known Solar System onto your frames. Blink makes motion obvious. Align prepares the stack. Discover digs for what the catalogs did not hand you. Synthetic Track pulls faint signals into view. Plots and Trajectory View turn a blinking speck into a path through the sky -- and through the Solar System.

The next object in your data may already have a name. Or it may be waiting for one.
