# AstroStack

## Introduction

Some images are not meant to be inspected one frame at a time. They are meant to accumulate.

**AstroStack** is CAst's cumulative stacking and export mode. You open a folder of related frames, optionally crop the region you care about, let CAst align the sequence when needed, and export an animation that shows the stack deepening frame by frame. Along the way, the mode can overlay live measurements such as signal, noise, SNR, FWHM, frame count, and total integration time.

In the current UI, this mode may still appear as **Deep Stack**. This guide uses the product name **AstroStack**, while noting the current on-screen labels when they matter.

### What you can do with this mode

- **Build a cumulative stack animation.** Export GIF or MP4 animations that show the stack improving as more frames are added.
- **Align solved image sequences.** If your source frames are not already aligned, CAst can reproject them onto a common WCS grid before stacking.
- **Crop to the interesting region.** Define a crop on the reference frame so the exported stack focuses on the target field.
- **Measure stack quality live.** Plot or label running values such as **Signal**, **Noise**, **SNR**, **FWHM**, **Frame count**, and **Total integration time**.
- **Annotate the export.** Add shapes, text, and plot overlays, then save or reload those layouts as presets.
- **Compare processing choices quickly.** Fast mode can subsample the exported animation frames so you can iterate without waiting for every cumulative frame to be written.

### Why this matters

Deep-sky imaging is often judged by the final integrated result, but the path to that result is part of the story. A cumulative stacking animation shows how signal emerges from noise, how integration time changes the image, and whether your sequence is improving in the way you expect. That is useful for teaching, for outreach, for documenting a workflow, and for sanity-checking your data before committing to heavier post-processing.

---

## How It Works

### Step 1: Open a Folder

Switch to **AstroStack** mode (currently labeled **Deep Stack** in the launcher and menu), then click **Open**.

Choose a folder containing a related sequence of supported image files. AstroStack expects a **folder workflow**, not a single-image workflow. The app scans that folder, sorts the frames by filename, and uses the first frame as the preview and alignment reference.

If no supported images are found, the mode stops with:

`No supported images were found in the selected folder.`

### Step 2: Inspect the Reference Frame

Once loaded, the mode shows the reference image and updates the counter to **Frames: N**.

The empty-state hint before loading reads:

`Open a Deep Stack folder to inspect the reference frame and define an optional crop with the Crop tool.`

By default, CAst also prepares a live plot overlay titled **Stack SNR** so the exported animation can visualize how the cumulative stack improves.

### Step 3: Optional Crop

Click **Crop** and drag a rectangle on the reference image.

The crop is not just cosmetic. It can define:

- the area used for export
- the region used during alignment
- the portion of the stack you want viewers to focus on

When crop mode is active, the UI explains:

`Drag on the image to select a crop region. Click Crop again to cancel.`

If a crop is already present, the button changes state so you can reset it.

### Step 4: Optional Display and Overlay Setup

Before exporting, you can tune the preview and add overlays:

- **Display**
- **Curves**
- **Invert**
- **Reset View**
- **Reset Display**

Stretch choices include:

- **Auto Stretch**
- **Linear**
- **Asinh**
- **Sqrt**
- **Log**

You can also add shapes, text, live metric labels, and chart overlays. These are useful if you want the animation to explain itself without narration.

### Step 5: Stack

Click **Stack** when the layout looks right.

AstroStack then asks whether the sequence is already aligned through the **Deep Stack Alignment** prompt:

- **Yes**: treat the dataset as already aligned
- **No**: align the sequence before stacking
- **Cancel**: stop

After that, the **Deep Stack Export** dialog lets you choose:

- **Fast mode**
- **Format** (`GIF` or `MP4`)
- **Frame Rate**
- **Frame Time**
- **Output size**
- **Playback** (`Loop forever` for GIF)

Finally, choose the output file path and export.

---

## Input Requirements

| Requirement | Why |
|-------------|-----|
| A folder of supported images | AstroStack works on sequences, not one frame |
| Preferably plate-solved frames | Needed if CAst must align the sequence |
| At least two solved frames for alignment | Single-frame or unsolved sets cannot be aligned this way |
| `reproject` installed | Required for WCS reprojection during alignment |
| FFmpeg support for MP4 | Needed for MP4 export |

AstroStack can preview common supported image types, but alignment relies on valid celestial WCS and is meant for astronomical frames that map onto the same sky field.

---

## The Stacking Pipeline

### 1. Load and sort the sequence

AstroStack reads supported images from the selected folder only and sorts them by filename. The first frame becomes the preview and the WCS reference for any later alignment.

### 2. Optional WCS alignment

If you answer **No** to the “already aligned” question, CAst runs sequence alignment through the same WCS-aware alignment core used elsewhere in the app.

In broad terms, it:

1. Validates celestial WCS on the reference and source frames
2. Reprojects each frame onto the reference geometry
3. Uses a fast affine path when the mapping is close to affine
4. Falls back to `reproject_interp(...)` when necessary
5. Optionally refines the alignment with a star-residual shift

If a crop exists, alignment is performed on the cropped reference grid rather than always on the full frame.

### 3. Build the cumulative stack

AstroStack uses a **running mean**, not a running sum. That means:

- frame 1 initializes the stack
- each new frame updates the cumulative average

So the exported sequence shows the stack evolving from the first frame through the full integrated result.

### 4. Measure cumulative quality

For each cumulative stack step, CAst can measure:

- **Frame count**
- **Total integration time**
- **FWHM**
- **Signal**
- **Noise**
- **SNR**

If you define both signal and background measurement regions, signal/noise are based on those ROIs. Otherwise CAst falls back to global image statistics, using bright-end and background heuristics. That makes the metric overlays informative, but they should not be confused with full science-grade aperture photometry.

### 5. Render overlays and write the animation

Each exported frame is the current cumulative stack plus whatever overlays you configured:

- text
- shapes
- chart overlays
- live metric labels such as **Signal** and **Noise**

The default **Stack SNR** plot is tied to the cumulative metric series, not just a decorative panel.

---

## Overlays, Plots, and Presets

### Live metric overlays

AstroStack supports live metric labels and plots using:

- **Frame count**
- **Total integration time**
- **FWHM**
- **SNR**
- **Signal**
- **Noise**

Default plot labels include:

- `Stacked frames`
- `Integration time (s)`
- `FWHM (px)`
- `SNR`
- `Signal`
- `Noise`

### Annotation controls

The side controls include:

- **Properties**
- **Plot Properties**
- **Functions**
- **Layers**

Common editable properties include:

- **Stroke**
- **Fill**
- **Text**
- **Opacity**
- **Weight**
- **Prefix**

Plot styling sections include:

- **Data**
- **Card**
- **Text · Title**
- **Text · Labels**
- **Plot area**
- **Chart**

### Presets

Use:

- **Save Preset**
- **Load Preset**

Preset files use the filter:

`Deep Stack Preset (*.astrostack.json);;JSON Files (*.json)`

Presets can store:

- overlay layers
- crop region
- signal region
- background region
- plot styling

Modern presets normalize geometry to a reference image size so they can be restored on different-sized images more robustly.

---

## Export Options

### GIF

GIF is the straightforward choice for loopable web or presentation exports.

Options include:

- frame rate / frame time
- output size
- **Loop forever**

### MP4

MP4 is better for smoother presentation playback and smaller high-quality motion exports, but it depends on FFmpeg support in the runtime. If the required video path is unavailable, CAst warns that MP4 export is unavailable.

### Fast mode

**Fast mode** does not skip alignment or skip the cumulative stacking calculations. It only reduces how many animation frames are written to disk.

In practice:

- full export can write every cumulative step
- fast mode subsamples the export, up to about 60 frames

This makes it useful for previews and iteration.

### Output size

Output size scales from a fraction of the stacked frame to full size. Very small exports are useful for quick review; full-size exports are better when you want to preserve detail.

---

## Suggested Workflow

1. Put a solved image sequence for one target into a dedicated folder.
2. Open that folder in **AstroStack**.
3. Inspect the reference frame and define a crop if the full frame is too wide.
4. Set display stretch and add any labels or live SNR/FWHM plots you want in the export.
5. Save a preset if this is a layout you will reuse.
6. Click **Stack** and tell CAst whether the sequence is already aligned.
7. Try **Fast mode** first for a quick preview export.
8. If the result looks right, re-export at full quality or as MP4.

---

## How This Differs from Other Modes

| Mode | AstroStack relationship |
|------|--------------------------|
| **Differential Photometry** | Measures light curves; AstroStack is for cumulative image stacking and visual export |
| **Asteroid / Comet Detection** | Works on moving-object time series; AstroStack assumes you want a deepening integrated stack |
| **Transient Finder** | Searches for variability between frames; AstroStack emphasizes cumulative signal growth |
| **Sky Explorer** | Identifies what is in one field; AstroStack builds an animation from many frames |

---

## Limitations

### What AstroStack does not do

- It is not a full astrophotography post-processing suite.
- It does not replace dedicated calibration, background extraction, deconvolution, or color-combination tools.
- It does not compute science-grade photometric measurements from the live SNR overlays.
- It does not align arbitrary unrelated images from different fields.

### Practical caveats

- Alignment requires usable celestial WCS and at least two solved frames.
- All frames must come from the same folder for the built-in sequence alignment path.
- `Fast mode` still performs the core work; it only writes fewer output frames.
- MP4 export depends on FFmpeg support.
- FWHM is a heuristic quality indicator derived from a bright peak, not a full PSF analysis pipeline.
- Saving or loading normalized presets without an open AstroStack image folder is blocked because the geometry is image-relative.

### Naming note

The current code and some on-screen labels still say **Deep Stack**. This guide uses **AstroStack** because that is the intended product name, but if you see **Deep Stack** in the current build, it refers to the same mode.

---

## Conclusion

AstroStack is CAst's “show the image getting deeper” mode. Open a folder, decide whether the frames are already aligned, define the region that matters, and export a cumulative stack that tells a visual story about integration time and signal growth. It is especially useful when you want a clear, reproducible presentation of how a deep result emerges from a sequence of exposures.

For shared shell conventions and theme controls, see [Themes, Layout, and Shared UI](themes_layout_ui.md).
