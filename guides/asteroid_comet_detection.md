# Asteroid and Comet Detection

## Introduction

Most of the Solar System is invisible until something moves.

In a single deep exposure, an asteroid can look like any other faint star. In a sequence of frames, it drifts. That drift is the signal: a rock or icy nucleus crossing your field while the background stars stay put. Professional surveys find thousands of these movers every year, and they still miss faint, fast, or poorly timed objects that pass through amateur fields.

**Citizen Astronomy (CAst)** turns a plate-solved image or a folder of subframes into a moving-object workstation. You can list the catalogued asteroids and comets predicted in your field and check which ones your pixels actually show, blink the sequence to see motion by eye, align frames onto a common sky grid, search for unmatched movers with **Discover**, confirm faint targets with **Synthetic Track**, inspect paths with **Plots**, and explore orbits in a heliocentric **Trajectory View** built from JPL Horizons.

### What you can do with this mode

- **Recover known objects.** Query SkyBoT for asteroids and comets predicted in your footprint, project them onto the frame, and score which ones are visible in your pixels.
- **Blink and track.** Preload the sequence for smooth playback, keep the selected object centered, and scrub its motion in **Sky View**.
- **Align sequences.** Reproject already-solved frames onto one reference WCS and write an `aligned/` folder without touching the originals.
- **Discover unmatched movers.** Subtract a static sky model, find residual detections, link them into tracklets, and separate stronger **Potential Discoveries** from **Borderline Review** candidates, with a known-object recovery benchmark alongside.
- **Confirm with Synthetic Track.** Shift and stack on the object's motion to pull a faint mover out of the noise.
- **Review with Plots and Trajectory View.** Inspect predicted-versus-measured paths, and open a 3D heliocentric scene with planets and time playback.
- **Label candidates.** Save Real Mover / Artifact / Known Object / Noise / Unsure labels and train a local model that scores later candidates.
- **Export.** Save the image view, blink animations (GIF/MP4), target-aligned trail animations (GIF), Synthetic Track stacks, Trajectory View stills or animations, and Discover CSV tables.

### Why this matters

Catalog ephemerides tell you where an object *should* be. Your images tell you whether it *is* there, and whether something else is moving that the catalogs did not list for that night. Recovering a faint comet, checking an interstellar object such as 3I/ATLAS against your own data, or isolating an unmatched tracklet for follow-up is real observational work. CAst makes that workflow visual and reviewable.

---

## How It Works

### Step 1: Open a Source

Choose **Asteroid / Comet Detection** on the launcher (under the science workflows), or **Mode > Asteroid/Comet Detection** if CAst is already open.

You can open:

- a **single** solved image with **File > Open File** (`Ctrl+O`). Generate starts automatically.
- a **folder** of subframes with the **Open** button on the workflow row. Generate starts automatically on the first frame.
- a **folder** with **File > Open Folder** (`Ctrl+Shift+O`). The sequence loads, and you click **Generate** when ready.

Supported images are FITS, XISF, TIFF, PNG, JPEG, and camera RAW. Frames in a folder are sorted by observation time. If the folder mixes filters or exposures, the **Group** selector lists **All** plus one entry per filter/exposure combination, for example `Filter L | 120 s (100)`. The **Frame** selector picks the frame shown and used as the reference for Generate.

Once a source is loaded, the primary button reads **Generate**. To open a different folder, use **File > Open Folder** or **File > Open File**.

### Step 2: Understand the Workspace

| Area | Role |
|---|---|
| Workflow row | **Open / Generate / Apply**, **Align**, then **Trajectory View**, **Synthetic Track**, **Discover**, **Plots**, then **Group** and **Frame** |
| Image toolbar | **Display** menu, **Target Marker**, **Center Object**, **Track Object**, **Blink**, **Export**, **Hide Info / Show Info** |
| Image view | The current frame with markers, labels, an optional RA/Dec grid, and an information panel on the right |
| Predicted Objects table | Known predictions, plus `Candidate …` rows after Discover |
| Work Log | Timestamped progress for Generate, Align, Discover, Synthetic Track, exports, and catalog queries |
| Inspector | Text details for the selected row |
| Sky View | An isolated motion canvas for the predicted objects, with a time slider |

The **Display** menu holds the stretch (**Auto Stretch**, **Linear**, **Asinh**, **Sqrt**, **Log**), **Curves**, **Invert**, **Reset View**, **Reset Display**, **Show RA/Dec** (live coordinates under the cursor, on by default), **Show Grid** (off by default), and **RA Density** / **Dec Density** for the grid (2 to 12, default 5).

Scroll over the image to zoom and drag to pan. Zooming out stops at the fitted view. The lower tab panel and the whole left panel can be collapsed by dragging their splitters; a **Work Log · Inspector · Sky View** or **Predicted Objects** handle restores them.

### Step 3: Generate Known Predictions

**Generate** works on the current frame. It:

1. Resolves a usable WCS for the frame. An embedded WCS is used if it agrees with the mount/header pointing. Otherwise CAst runs local Gaia matching and astrometry.net together (astrometry.net only when **Astrometry API Key** is set in **Settings > Open Settings > General**) and keeps whichever finishes first.
2. Queries SkyBoT for asteroids and comets in the field at the frame's mid-exposure time.
3. Drops predictions that fall outside the image or below the horizon for your observing site.
4. Projects each prediction into the frame and measures a local peak near it.
5. Scores confidence and fills the **Predicted Objects** table with the first row selected.

The frame needs a usable timestamp (`DATE-OBS` or equivalent). Times without a timezone are read in **Image Timestamp Timezone** (**Settings > Open Settings > Differential Photometry**, under **Science Export**).

After Generate, changing **Frame** recomputes the predicted position and local match for that frame from its own WCS and timestamp. It does not re-query SkyBoT.

---

## Known-Object Prediction

### Ephemeris Sources

1. **SkyBoT** (IMCCE) is the primary cone search for asteroids and comets in the field. The search radius is the solved field radius, clamped to 0.01–10°. The query is geocentric, with a relaxed position-error filter (3600″) so high-uncertainty objects are not dropped. Server errors (500, 502, 503, 504) and unreadable responses are retried; persistent failures are reported in the Work Log with the query status and a preview of the response body.
2. **Interstellar refresh.** For **3I/ATLAS**, CAst refreshes or adds the prediction from **JPL Horizons**, with **IMCCE Miriade** as the fallback path.
3. Frame timing uses mid-exposure when possible (`DATE-OBS` plus half the exposure).

### Magnitude Limit

Generate passes a magnitude limit of **18.0** by default. To change it, enable **Use manual detection magnitude limit override** and set **Manual Limit** in **Settings > Open Settings > Asteroid/Comet > Search > Estimate Options**.

SkyBoT rows fainter than the limit plus **1 mag** are dropped. Comets are kept regardless of magnitude, because their catalog magnitudes are often unreliable.

Predicted magnitudes (**Pred Mag**) come from SkyBoT's V column. They are useful for triage, not a strict V-band guarantee.

### Local Image Confirmation

For each prediction, CAst searches a local window around the predicted pixel and measures a peak:

- Search radius grows modestly with expected trail length (typically clamped between about 5 and 18 pixels).
- Local SNR uses a robust background estimate: peak minus median, divided by $1.4826 \times \mathrm{MAD}$.
- Expected trail length in pixels is estimated from predicted motion rate, exposure time, and plate scale.

A detection is treated as **likely visible** when local SNR is at least **4** and the match offset is within **8** pixels.

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

Status labels:

- **High-confidence match**: likely visible and score ≥ 0.75
- **Plausible visible match**: likely visible and score ≥ 0.5
- **Predicted in field; visual confirmation weak**: bright enough in catalog terms, weak in the image
- **Predicted in field**: ephemeris hit without local confirmation

### Predicted Objects Table

Columns:

- **Object**, **Type**, **Class**
- **Pred Mag**
- **Confidence**
- **Motion** (arcsec/h) and **Direction** (degrees in the image plane)
- **Exposure Motion**: pixels moved during one exposure, labeled **Point source** below 1.5 px or **Tail likely** above
- **Altitude** at the observation time

Click a column header to sort. Select one row to inspect it; Ctrl-click or Shift-click to select several known objects for **Trajectory View**. Double-click a row to open **Plots**.

Highlighted rows are likely visible. After Discover, each Potential Discovery is added as a `Candidate C…` row with **Type** `Potential`, the method that found it under **Class**, and its residual score under **Confidence**.

---

## Inspector, Image Panel, and Sky View

### Inspector

The **Inspector** tab shows the selected row as text:

- For a known object: type and class, confidence, predicted RA/Dec and pixel position, predicted magnitude, motion rate, exposure motion, altitude, and the current frame's predicted position, measured offset, SNR, and flux.
- For a candidate: the method that found it, frames linked, average residual score, peak residual, linearity deflection RMS, motion, displacement, its position in the current frame, and one line per frame detection with pixel position, RA/Dec, and residual score.

### Image Information Panel

The panel on the right of the image (toggled with **Hide Info / Show Info**) shows:

- **Frame**: name, date and time, Moon illumination, filter and exposure, size
- **Setup**: location, elevation, telescope, focal length, aperture, focal ratio, camera, pixel size, pixel scale, Bortle class (from the **Setup** tab in Settings)
- **Field**: center RA, center Dec, radius
- The selected object: type, confidence and status, prediction (Pred Mag, Motion, Altitude), and the current frame match (offset, SNR, FWHM, flux, trail)

### Sky View

**Sky View** draws only the predicted movers, not background stars, so you can watch how objects in the field move without stellar clutter.

- **Time** slider: scrub up to 12 hours before or after the frame time in 0.5 h steps (`UTC +… h`)
- **Now**: return to the observation time
- **Reset View**: recenter the field
- Drag to pan, scroll to zoom, and click an object to show its details and select its row in Predicted Objects

---

## Visible-Limit Estimate

The visible-limit estimate measures how deep the current frame really is by checking which catalogue stars are recovered in your pixels.

### Algorithm

`estimate_visible_magnitude_limit()`:

1. Queries catalogue stars in the field: Gaia DR3 down to about G 21.5, and, fainter than 20.5 mag, Pan-STARRS (Dec ≥ −30°) or DES with SkyMapper as the fallback in the south.
2. Projects them into the image.
3. Walks magnitude bins in **0.5 mag** steps from **Start Mag** (default **15.0**).
4. In each bin, samples up to **Amount of Stars to Check** (default **6**) and asks whether at least **Visible Stars Needed** (default **3**) are recovered at **SNR Threshold** (default **4**) with a small centroid offset.
5. Stops after consecutive unsuccessful bins and reports the dimmest successful bin.

Discover runs this estimate and shows it as **Visible Limit** in Discover Results; the **In Limit** columns and the **Known In Limit** count use it.

> The standalone toolbar **Estimate** button exists in code but is hidden. Its options remain under **Settings > Asteroid/Comet > Search > Estimate Options**. When it is used, the primary button changes to **Apply**, which stores the estimate as the Generate magnitude limit and re-runs Generate.

Catalogue depth is a practical proxy. Trailed movers, different bandpasses, and crowded residuals still change what you can recover.

---

## Blink

### Playback

- Toggle **Blink** (needs at least 2 frames in the active group). The first time, CAst preloads rendered frames before playback starts.
- Frame time comes from **Blink Frame Time** in **Settings > Asteroid/Comet > Export** (0.05 s to 2.00 s, default 0.05 s).
- Blink plays the active **Group**. Switch groups to blink a single filter/exposure set.

### Track Object and Center Object

- **Track Object** (on by default) keeps the selected prediction or candidate centered while blinking and when you change frames. The anchor is **Known / predicted position** by default or **Detected local match** (**Settings > Asteroid/Comet > Visuals > Tracking > Track Object Anchor**).
- **Center Object** centers the selection once and zooms to at least 3×.
- **Target Marker** draws the configured marker (**Square aim**, **Circle**, **Corner brackets**, **Open crosshair**, or **Pointer**) on the selected object only. With Target Marker off, no object markers are drawn. Name labels, when enabled, apply to the selected object only.

### Curves

**Curves** opens a curve and black/midtone/white-point editor for the image display. Changes apply when you confirm. **Reset Display** restores the default stretch and curves.

---

## Align

**Align** reprojects an already plate-solved multi-frame sequence onto the current frame's WCS.

- Requires a loaded folder with at least two frames, all with an embedded celestial WCS. The tooltip names the first unsolved frame if any.
- Uses WCS-based reprojection with an optional star-residual refinement shift.
- Writes FITS copies into an `aligned/` subfolder next to the originals, which stay untouched.
- Reloads the aligned sequence so Blink, Discover, and Synthetic Track work on it.

Align uses every loaded frame, not only the active group.

---

## Discover

**Discover** searches the current group for moving candidates, separates them by quality, and benchmarks the run against the known predictions.

### Requirements

- **Generate** has run on the current source
- A **specific** Group when the folder has more than one filter/exposure group (not **All**)
- At least **Minimum Linked Frames** (default **3**) frames in the group, all with an embedded celestial WCS
- Blink preload has finished

The **Discover** tooltip states which requirement is missing.

### Restore or Run New

If a Discover result already exists for the current group, Discover asks whether to **Restore Last Result** or **Run New Scan**.

### Discover Pipeline Dialog

Before a new run, the **Discover Pipeline** dialog sets:

| Option | Meaning |
|---|---|
| **Preset** | **Detailed search** (default; uses saved Discovery Advanced defaults), **Fast scan** (at least 2x2 binning, temporary cache, Residual Min SNR at least 6, smaller overlapping batches), or **Custom** |
| **Alignment** | **Align first** (default) or **Already aligned** |
| **Working Binning** | **Off (1x1)**, **2x2**, **3x3**, **4x4** |
| **Motion Prior Bias** | **Balanced**, **Main-belt bias**, **Faster near-Earth bias** |
| **Temporary Cache** | Write prepared frames to an auto-cleaned cache |
| **Residual Min SNR** / **Residual Max SNR** | Optional residual filters (**Disabled** by default) |
| **Min Seed Displacement** | Minimum motion for a track seed (default 1.5 px) |
| **Frames per Batch** | **Whole group** or overlapping windows of N frames |
| **Single Batch** | Run one sampled batch only (10 frames if batching is off) |
| Retry checkbox | If no candidates are found, retry with the detailed full-resolution search (Fast and Custom only) |

The dialog also previews the typical motion in pixels during one exposure for the chosen bias. Values you run with become the new defaults.

Click **Run Discover**. A **Discover** progress dialog with **Cancel** opens, and **Discover Results** fills in as each batch finishes.

### What the Pipeline Does

1. **Prepare frames**: optional align, bin, cache.
2. **Build a static sky model**: per-pixel median of the aligned sequence.
3. **Make residuals**: subtract the static model from each frame.
4. **Detect residuals**: point, streak, or hybrid detectors (`DAOStarFinder` for points; streaks use area and elongation thresholds). Residuals within 6 px of a known prediction are excluded from the discovery search.
5. **Link tracklets**: connect detections across frames with approximately linear motion.
6. **Linearity screen**: sort tracklets into Potential Discoveries, Borderline Review, or discarded.
7. **Associate known objects**: a known prediction counts as recovered when a tracklet follows its predicted path within about 4.5 px, or within its catalog position uncertainty if that is larger.
8. **Optional final synthetic sweep**: a velocity-grid shift-and-stack pass for faint movers that never linked cleanly.

When **Final Synthetic Sweep** is enabled, Discover pauses after the residual batches and asks **Continue Discover Sweep?**. Click **No** to keep residual-only results; the **Continue** button in Discover Results then runs the sweep later.

### Tracklet Linking

Seed pairs are formed across nearby frames (gap up to 2 frames), filtered by **Min Seed Displacement** and the motion prior, then fit with a linear model in image coordinates versus time:

$$
x(t) = a + v_x t, \qquad y(t) = b + v_y t
$$

Detections rematch within about **2.8 px** of the predicted track. The fit can iterate a few times. Tracklets need at least **Minimum Linked Frames** (default 3) to survive.

### Potential Discoveries vs Borderline Review

After linking, CAst measures how far the tracklet deviates from a straight constant-velocity path (deflection RMS):

| Bucket | Default RMS threshold | Role |
|---|---|---|
| **Potential Discoveries** | ≤ 0.9 px | Stronger candidates; added to the Predicted Objects table |
| **Borderline Review** | ≤ 1.8 px | Weaker or noisier tracklets for manual review |
| Discarded | Above review threshold | High-deflection tracklets |

### Synthetic Sweep

When enabled, the final sweep tests a grid of velocities:

- **Sweep Max Motion** (default 12 px/h)
- **Sweep Motion Step** (default 1.0 px/h)
- **Sweep Angle Step** (default 30°)
- **Sweep Direction Focus**: **All directions (360 deg)** or **Main-belt direction focus** with **Sweep Focus Width** (default 45° half-width). The focused mode uses the direction followed by known main-belt asteroids in the field and falls back to a full sweep if it cannot infer one.
- **Sweep Min Stacked SNR** (default 6.0)
- **Save Sweep Stacks** writes every tested stack as FITS under `synthetic_track` in the data folder

### Discover Results Window

The summary at the top shows **Visible Limit**, **Known In Limit**, **Recovered In Limit**, **Potential Discoveries**, and **Borderline Review**.

Tabs:

- **Known Recovered**: Object, Source, V_mag, In Limit, Frames, Recovered, Motion, Residual Score
- **Known Missed**: Object, V_mag, In Limit, Status, Confidence, Motion
- **Potential Discoveries** and **Borderline Review**: Candidate, Source, Frames, Motion, Residual Score, Deflection RMS, Label, ML Score

The right side shows the reference image with the selection and a details box.

Buttons:

- **Trajectory...**: open Plots for the selected row
- **Mark Selected**: draw the selected Potential Discoveries on the main image (several rows can be selected)
- **Label...**: open **Label Discover Candidates** for the current candidate tab
- **Synthetic Track...**: run Synthetic Track on the selected Potential Discovery
- **Export...**: **Benchmark CSV...**, **Candidate Review CSV...**, **Summary CSV...**
- **Candidate Label** list with **Save Label**, and **Train Model**
- **Continue**: run the deferred final synthetic sweep
- **Close**

Closing the window keeps the result. Potential Discoveries stay in the Predicted Objects table as `Candidate …` rows, where you can center, track, plot, and Synthetic Track them. Marked candidates are drawn on the main image while **Show marked potential discoveries on the main image** is on. Changing group clears the marks.

> **Recover Known** is implemented as a separate recovery workflow, but its toolbar button is hidden. The **Known Recovered** and **Known Missed** tabs and the Benchmark CSV give the same recovery check inside Discover.

---

## Labels and the Candidate Model

Labels record your judgement about each candidate so CAst can learn which kinds of tracklets you accept.

- In **Potential Discoveries** or **Borderline Review**, select a row, choose **Real Mover**, **Artifact**, **Known Object**, **Noise**, or **Unsure** in **Candidate Label**, and click **Save Label**.
- Or click **Label...** to open **Label Discover Candidates**: a cutout of the candidate blinks through its frames (**Auto Blink**, speed **Slow**, **Normal**, **Fast**, **Very Fast**). Click a label button to save it and move to the next candidate, or use **Previous** and **Skip**.
- Click **Train Model** to fit a Random Forest on all saved asteroid/comet labels. It needs at least two labeled candidates with two different labels; with at least six labels and two per class, it also reports held-out validation metrics.
- The **ML Score** column then shows the model's predicted label and confidence for each candidate. It is advice, not a filter.

Labels and models are stored locally in `%LOCALAPPDATA%\CitizenPhotometry\candidate-training.sqlite3` (or `~/.citizen-photometry/` on other systems).

---

## Synthetic Track

A single frame may barely show a faint mover. **Synthetic Track** shifts every frame onto the object's motion and stacks the result so the target stays fixed while stars streak.

### Running It

1. Select a predicted object or a `Candidate …` row.
2. Click **Synthetic Track** (or **Synthetic Track...** in Discover Results).

It needs at least two frames in the group. On a mixed **All** group it is disabled unless **Track Mixed All Group** (**Allow running from mixed 'All' groups**) is enabled in **Settings > Asteroid/Comet > Search > Discovery Advanced > Preparation**. Blink stops while it runs.

With **Advanced Synthetic Track** (**Open manual one-stack dialog**) enabled, the **Advanced Synthetic Track** dialog opens first:

- **Stack Scope**: **Object-centered crop** or **Full image (manual motion)**
- **Track Crop Radius** (crop mode)
- **Motion (px/h)**, **Motion (arcsec/h)**, **Angle** (full-image mode; prefilled from the object's motion)
- **Integration**, **Weights**, **Rejection**
- **Run Synthetic Track**

### How It Works

1. For each frame, predict the object's pixel position at that frame's time.
2. In crop mode, cut a patch around that position (default radius **24 px**) with subpixel bilinear sampling. In full-image mode, shift each whole frame by the manual motion vector.
3. Reject outlier pixels if a rejection mode is set.
4. Combine with **Average** (default), **Mean** (weighted by **PSF signal weight**, **PSF SNR**, **SNR**, or **Average signal strength**), **Min**, or **Max**.
5. Measure the stacked peak: SNR, center offset, peak value, flux.

### GPU Path

Full-image mode can run on the GPU through CuPy. **Track Backend** controls it: **Auto** (default) uses the GPU when CuPy is available and the stack uses **No rejection**; **CPU only** forces NumPy; **GPU when available** requests CuPy but still falls back to CPU for rejection modes. The first GPU run in a session warms up the backend. The preview reports the backend used, for example `Compute backend: GPU (CuPy full-frame no-rejection stack)`, and the reason for any CPU fallback. Object-centered crop stacks run on the CPU.

### Preview Dialog

The **Synthetic Tracking - …** window reports:

- Used and skipped frames
- Stacking motion rate and angle
- Stacked SNR, center offset, peak value, local flux
- Per-frame inclusion details

**Overlays** toggles markers and labels for **Predicted center**, **Auto peak**, **Measured peak**, and **Known objects**. **Ctrl + click** the object to override a misidentified measured peak and recompute the metrics; **Reset to Auto** restores automatic peak finding. **Export Image...** saves the preview as PNG, JPEG, or BMP at full resolution, or as a linear raw FITS stack without display stretch.

---

## Plots

Toolbar label: **Plots**. Window title: `Trajectory - …`. Double-clicking a row in the Predicted Objects table opens it too.

### For Known Objects

- **Image-plane trajectory**: predicted and measured positions with residual lines
- **Predicted sky-plane path**: predicted path and measured sky centroids in arcseconds
- **Match offset vs time**
- **Detection SNR vs time**
- A frame table with predicted and measured pixel positions, RA/Dec, offset, and SNR

### For Discovery Candidates

- **Image-plane trajectory**: the measured path and the linear-motion fit
- **Sky-plane path**: measured RA/Dec offsets
- **Pixel position vs time**
- **Residual score vs time**
- A table with frame, UTC, x, y, RA, Dec, residual, and peak

The plot toolbar can pan, zoom, and save the figure as an image.

---

## Trajectory View (3D)

Toolbar label: **Trajectory View**. Window title: `3D View - …`.

### Opening It

- With one or more known objects selected, CAst queries JPL Horizons for heliocentric state vectors over the observation window and opens the scene. Candidates have no orbit, so they are not included.
- With nothing selected, it opens an empty Earth-centered scene that you populate with **Lookup**.

### What It Shows

- An OpenGL scene with the Sun, Earth, the selected objects' arcs, and optional **Planets**
- Optional **Meteor Stream** overlay for known shower parents (for example the Perseids from 109P/Swift-Tuttle): an illustrative debris tube along the parent's osculating orbit with Earth-crossing regions highlighted. It is a teaching model, not a dust-trail forecast.
- Optional **Periods** panel with orbital periods
- Info panels: **Heliocentric top-down**, **Sky Track**, **Magnitude** (literature magnitude over the window), **Distance**, and **Data** (a frame table with object and Earth positions and distances; hidden by default)

Drag in the scene to rotate and scroll to zoom.

### Controls

- **Span**: **Local** (45 days either side of the frames), **+/-90d**, **+/-180d**, **1y**, **5y**, **Custom** (with **From**, **To**, and **Apply**). Changing the span keeps the selected objects.
- **Camera**: **Orbit Overview**, **Top-Down**, **Side View**, **Object Follow**, **Earth Follow**
- **⚙** (Display settings): **Show Labels**, **Show Sample Points**, label **Font**, **Size**, **Bold**, **Italic**, **Asteroids** and **Comets** colors, Meteor Stream (**Point density**, **Trail thickness**, **Orbit guide line**, **Glow**, **Animate debris**, **Drift speed**), Sky Track (**Bayer designations**, **Star density**, **Star draw radius**, **Constellation lines**, **Fit Trajectory**, **Entire Sky**), and **Info panels** (**Move Up**, **Move Down**, **Remove**, **Reset Layout**, **Add Panel**)
- **Objects (n/m)**: toggle each object's visibility, and **Nearby Search...** to add objects predicted around the field (**Nearby Field Search**: **Radius**, **Max predicted mag**, **Search Nearby**, **Add Selected**)
- **Lookup**: **Object Lookup** searches the small-body database by name, designation, or keyword (for example `Pallas`, `154P`, `3I`) and adds the selected matches
- **Save**: **Save Trajectory View** exports **Trajectory view only** or **Trajectory view + info panel** as PNG, JPG, GIF, or MP4. Animations cover one pass of the timeline at the current speed, with **Camera motion** **Fixed**, **Slow orbit**, or **Object chase**.
- **UTC** field, return-to-observation-time button, **Play**, **Speed** (**1 h/s** to **30 d/s**, default **1 d/s**), and the **Time** / **Sample** slider

Trajectory View answers a different question from Plots: not "did this peak match the prediction in my frames?" but "where is this object in the Solar System around the nights I observed?"

---

## Settings

All mode settings are in **Settings > Open Settings > Asteroid/Comet**, split into **Search**, **Visuals**, and **Export** tabs. Hover a label for its tooltip.

### Search

**Estimate Options**: **SNR Threshold** (4.0), **Start Mag** (15.0 mag), **Manual Override** (off), **Manual Limit** (18.0 mag), **Amount of Stars to Check** (6), **Visible Stars Needed** (3), **Annotate Lowest Mag Stars** (off).

**Discovery Advanced > Preparation**: **Search Workers** (Auto), **Working Binning** (Off), **Temporary Cache** (off), **Edge Margin** (6 px), **Frames per Batch** (Whole group), **Track Crop Radius** (24 px), **Track Integration** (Average), **Track Weights** (PSF signal weight), **Track Rejection** (No rejection), **Track Backend** (Auto), **Track Mixed All Group** (off), **Advanced Synthetic Track** (off).

**Discovery Advanced > Residual Detection**: **Residual Min SNR** (Disabled), **Residual Max SNR** (Disabled), **Point Detection Threshold** (5.0 sigma), **Point Detection FWHM** (3.0 px), **Max Residuals / Frame** (24), **Residual Detector** (**Hybrid (point + streak)**, **Point-like only**, **Streak-aware only**), **Streak Min Area** (6 px), **Streak Min Elongation** (1.8×).

**Discovery Advanced > Tracklets and Sweep**: **Minimum Linked Frames** (3), **Potential Deflection RMS** (0.9 px), **Review Deflection RMS** (1.8 px), **Final Synthetic Sweep** (off), **Sweep Max Motion** (12 px/h), **Sweep Motion Step** (1.0 px/h), **Sweep Angle Step** (30°), **Sweep Direction Focus** (All directions), **Sweep Focus Width** (45°), **Sweep Min Stacked SNR** (6.0), **Save Sweep Stacks** (off).

### Visuals

**Show marked potential discoveries on the main image** (on), **Show asteroid/comet name labels on the main image** (on), **Marker style** (Square aim), **Track Object Anchor** (Known / predicted position), **Highlight the selected asteroid/comet with distinct colors** (on), **Invert also swaps asteroid/comet annotation and text colors** (on), and **Marker Appearance**: **Line Color**, **Accent Color**, **Label Color**, **Outline Color**, **Thickness** (4.0 px).

### Export

**Blink Frame Time** (0.05 s), **GIF Resolution** (100%), **MP4 Resolution** (100%), **GIF Looping** (on).

### Setup

The **Setup** tab stores telescope, focal length, aperture, camera, pixel size, observing latitude, longitude, and elevation, and Bortle class. The site is used for the horizon check and the information panel.

---

## Exports

| Export | How |
|---|---|
| Image view (PNG/JPEG/BMP) | **Export > Image...** saves the view as currently displayed |
| Blink animation (GIF/MP4) | **Export > Blink...** after blink preload finishes |
| Trail animation (GIF) | **Export > Trail...** with an object or candidate selected; each frame adds one more frame to a target-aligned stack |
| Synthetic Track stack (PNG/JPEG/BMP/FITS) | **Export Image...** in the Synthetic Tracking window |
| Plots figure | Save icon in the plot toolbar |
| Trajectory View still or animation | **Save** in Trajectory View |
| Discover benchmark / candidate / summary CSVs | **Export...** in Discover Results |
| Sweep stacks (FITS) | **Save Sweep Stacks** setting |

Default file names include the selected object, for example `(511) Davida_blink.gif` or `2015 CU58_trail.gif`, and default to the source folder (or beside a single opened file). Discover CSVs default to `<reference frame>_discover_benchmark.csv`, `_discover_candidates.csv`, and `_discover_summary.csv`.

CAst does not write MPC 80-column or ADES reports.

---

## Confirming and Reporting a Candidate

A Discover candidate is a tracklet that moved consistently in your frames. It is not a confirmed discovery. Before you treat one as real:

1. **Blink it.** Select the `Candidate …` row with **Track Object** on, and check that a point source moves steadily from frame to frame.
2. **Check the shape of the track.** Open **Plots**. A real mover follows the linear fit with small residuals; hot pixels, satellite trails, and noise jump or stay put.
3. **Stack on it.** Run **Synthetic Track**. A real mover sharpens into a point while the stars streak.
4. **Rule out known objects.** Look at **Known Missed** in Discover Results. A known object whose prediction was off by more than the association radius can show up as a candidate. Raising the **Manual Limit** and re-running Generate lists fainter catalogued objects.
5. **Rule out fixed sources.** Open the frame in Sky Explorer to see whether a catalogued star or galaxy sits on the track.
6. **Check against independent services.** Search the position and time with the Minor Planet Center's tools for known objects.
7. **Get more data.** Observe the field again on another night and recover the object there.

Reporting is done outside CAst. The Minor Planet Center expects astrometry (time, RA, Dec for each detection) from a registered observatory code, normally in ADES format, and new objects usually need observations on more than one night. CAst shows per-frame RA/Dec for candidates in the Inspector and in Plots, but those come from each frame's plate solution and have not been validated for MPC-grade astrometry.

---

## Shortcuts and Mouse

| Action | Input |
|---|---|
| Open File | `Ctrl+O` |
| Open Folder | `Ctrl+Shift+O` |
| Zoom the image | Mouse wheel |
| Pan the image | Left-drag |
| Open Plots for a row | Double-click the row |
| Select several known objects | Ctrl-click or Shift-click rows |
| Override the measured peak in Synthetic Track | `Ctrl` + click |
| Sky View pan / zoom / select | Drag / wheel / click |

There are no right-click context menus in this mode.

---

## Suggested Workflow

1. Open a subframe folder and pick the **Group** you want.
2. Let **Generate** fill **Predicted Objects**.
3. Blink with **Track Object** on to confirm the known movers by eye.
4. **Align** if frames are solved but not on one grid, or let Discover align first.
5. Run **Discover** for the recovery benchmark and unmatched tracklets.
6. Mark interesting Potential Discoveries and label them.
7. Confirm faint targets with **Synthetic Track**.
8. Inspect geometry with **Plots**; open **Trajectory View** for orbital context.
9. Export blinks, trails, and CSV tables for records or collaboration.

---

## Limitations

### What this mode does not do

- **It does not submit or format reports.** No MPC 80-column or ADES output. Discover produces reviewable candidates; follow-up and reporting happen outside CAst.
- **Catalogue depth is not asteroid detectability.** Trailing losses, filter differences, and background structure matter.
- **Confidence is triage.** High confidence means "worth looking at," not "certified identity."
- **Linear tracklets assume short arcs.** Strongly curved motion, very sparse sampling, or severe trailing can break linking.
- **Discover and Synthetic Track need a single group** when the folder mixes filters or exposures (Synthetic Track can be allowed on **All** in Settings).
- **Align and Discover need embedded WCS.** Generate can solve the current frame itself, but Align and Discover read the WCS stored in each file.
- **Aligned outputs are FITS copies.** Originals stay intact.
- **Network services can fail.** SkyBoT, Horizons, Miriade, Gaia, Pan-STARRS/DES/SkyMapper, and astrometry.net all need a connection when used.
- **OpenGL Trajectory View is optional.** The rest of the mode still works if the 3D scene cannot start.
- **Estimate and Recover Known buttons are hidden.** Their options remain in Settings, and Discover covers the recovery check.

### What could be improved

- Report formats for follow-up networks and the MPC
- Richer non-linear tracklet models
- Deeper PSF-aware residual detection in crowded fields
- More general interstellar and special-object handling beyond 3I/ATLAS
- Offline ephemeris packs for remote observing sites

---

## Conclusion

Asteroids and comets announce themselves by moving. CAst gives you ephemerides good enough to search with, and image tools to confirm, reject, or find movers.

Generate places the known Solar System onto your frames. Blink makes motion obvious. Align prepares the stack. Discover looks for what the catalogs did not list. Synthetic Track pulls faint signals into view. Plots and Trajectory View turn a moving speck into a path across the sky and through the Solar System.

The next object in your data may already have a name. If it does not, the checks above are how you find out.
