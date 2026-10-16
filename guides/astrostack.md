# Deep Stack

## Introduction

Some images are not meant to be inspected one frame at a time. They are meant to accumulate.

**Deep Stack** is CAst's cumulative stacking and export mode. You open a folder of related frames, optionally crop the region you care about, let CAst align the sequence when needed, and export a GIF or MP4 animation in which each frame adds one more exposure to the stack. Along the way, the mode can overlay live measurements such as signal, noise, SNR, FWHM, frame count, and total integration time.

The internal mode name in code and settings is `ASTROSTACK`, and preset and export files still use `astrostack` in their names. Everything you see on screen is labeled **Deep Stack**.

### What you can do with this mode

- **Build a cumulative stack animation.** Export GIF or MP4 animations that show the stack improving as more frames are added.
- **Align solved image sequences.** If your source frames are not already aligned, CAst reprojects them onto the first frame's WCS grid before stacking.
- **Crop to the interesting region.** Define a crop on the reference frame so alignment, measurement, and export focus on the target field.
- **Measure stack quality live.** Plot or label running values such as **Signal**, **Noise**, **SNR**, **FWHM**, **Frame count**, and **Total integration time**.
- **Choose where signal and noise are measured.** Draw a **Signal** region and a **Background** region, or let CAst use whole-image statistics.
- **Review lunar conditions.** Plot **Moon Illumination** (%) and a **Moonlight Impact** score from each frame's observation time, your observing site, and the image target. These values are informational and do not change the stack.
- **Annotate the export.** Add circles, rectangles, text, live value labels, and plot cards, then save or reload those layouts as presets.
- **Iterate quickly.** **Fast mode** writes up to 60 evenly spaced animation frames instead of one per cumulative stack.

### Why this matters

Deep-sky imaging is often judged by the final integrated result, but the path to that result is part of the story. A cumulative stacking animation shows how signal emerges from noise, how integration time changes the image, and whether your sequence is improving in the way you expect. That is useful for teaching, for outreach, for documenting a workflow, and for sanity-checking your data before committing to heavier post-processing.

---

## How It Works

### Step 1: Open the Mode and a Folder

Open **Deep Stack** from the mode picker (the card is in **Explore, Visualize & Process**: "Stack, align, and export cumulative deep-sky animations from solved frames.") or from **Mode > Deep Stack**.

Click **Open** in the toolbar, or choose **File > Open Folder** (`Ctrl+Shift+O`). The folder dialog is titled **Select Deep Stack image folder**.

Choose a folder that contains a related sequence of supported image files. Deep Stack is a **folder workflow**:

- Only files directly inside the selected folder are used. Subfolders are not scanned.
- Frames are sorted by filename (case-insensitive). Name your files so that this order is the order you want them stacked.
- The first frame becomes the preview, the crop reference, and the WCS reference for alignment.

While the first frame loads, a **Deep Stack** progress dialog shows **Loading Deep Stack folder...**. The toolbar counter then reads **Frames: N**.

If no supported images are found, the view shows:

`No supported images were found in the selected folder.`

Opening a folder resets the crop, the measurement regions, and all overlay layers, then adds the default **Stack SNR** plot card. Save a preset first if you want to keep a layout (see [Presets](#presets)).

### Step 2: Inspect the Reference Frame

Before a folder is loaded, the view shows:

`Open a Deep Stack folder to inspect the reference frame and define an optional crop with the Crop tool.`

After loading, you see the first frame with the default **Stack SNR** plot card in the corner. The card shows an SNR curve, an **Integration** line (hours and minutes, for example `00h05m`), and a frame counter such as `1/42`.

Before you export, the card and the live value labels show measurements from the first frame only. The full curve is filled in during export, one point per exported frame. When the export finishes, the preview returns to the first-frame values.

Navigate the preview:

- **Hand** tool (default): drag to pan.
- Mouse wheel: zoom in and out around the cursor.
- **Display > Reset View**: fit the image again.

### Step 3: Optional Crop

Click **Crop** and drag a rectangle on the reference image. While crop mode is active, the strip below the image reads:

`Drag on the image to select a crop region. Click Crop again to cancel.`

When you release the mouse, the preview shows only the cropped area. The button changes to **Reset** ("Clear the current crop and use the full aligned frame."). Click **Reset** to return to the full frame.

The crop is used for:

- the alignment grid (when CAst aligns the frames)
- the area cropped from each frame (when you say the frames are already aligned)
- the measurements behind every metric
- the exported animation frames

Set the crop before you place overlays. Layer positions are measured on the displayed canvas, so changing the crop afterward moves them relative to the image.

### Step 4: Optional Measurement Regions

By default, signal and noise are measured from whole-image statistics. To measure them in specific places:

1. Click the **Signal** tool (**S** icon, "Draw the signal measurement region for SNR.") and drag a rectangle over the target, for example a bright part of a galaxy or nebula. The region is green.
2. Click the **Background** tool (**B** icon, "Draw the background measurement region for SNR.") and drag a rectangle over empty sky. The region is orange.

Each region appears in **Layers** as **Signal region** or **Background region**. Drawing a region again replaces it. To remove one, select it in **Layers** and click **Remove**.

Both regions must exist before CAst uses them. With only one region, CAst falls back to whole-image statistics. Both regions are drawn on the exported frames as translucent rectangles.

### Step 5: Optional Display and Overlay Setup

Open the **Display** menu in the toolbar to tune the preview:

- Stretch: **Auto Stretch**, **Linear**, **Asinh**, **Sqrt**, or **Log**. The initial value comes from **Settings > Open Settings > General > Image Display**.
- **Curves**: opens the **Curves** dialog for black point, midtone, white point, and curve adjustments. This edits the stretched display only.
- **Invert**
- **Reset View**
- **Reset Display**: clears levels and curves and restores the stretch and invert defaults.

The exported frames use the same stretch, curves, and invert settings as the preview.

Add overlays from the tool bar at the top right of the panel and the **Functions** group on the left. See [Overlays, Plots, and Presets](#overlays-plots-and-presets).

### Step 6: Stack

Click **Stack** when the layout looks right. **Stack** is enabled once a folder with frames is loaded.

The **Deep Stack Alignment** dialog asks:

`Is this an already aligned dataset?`

- **Yes**: skip alignment and build the cumulative stack from the loaded frames directly. If a crop exists, the same pixel rectangle is cut from every frame. Plate solving is not required for this path, but every frame must have the same pixel dimensions.
- **No** (default): align the frames onto the first frame's WCS first, then build the cumulative stack. Every frame must be plate-solved.
- **Cancel**: stop.

### Step 7: Export

The **Deep Stack Export** dialog lets you choose:

- **Processing**: **Fast mode** (on by default)
- **Format**: `GIF` or `MP4`
- **Frame Rate** (fps) and **Frame Time** (ms), which are linked
- **Output size** (%)
- **Playback**: **Loop forever** (GIF only)

A summary line under the fields shows the stack frame count, the number of animation frames, the length of one pass in seconds, the output size, and the loop setting.

Click **Export**. In **Export Deep Stack Animation**, choose the output file. The default is `<folder name>_astrostack.gif` (or `.mp4`) inside the image folder. CAst corrects the extension to match the format.

A **Deep Stack** progress dialog shows the alignment and stacking progress, for example `Building cumulative Deep Stack 12/42...`, or `Accumulating Deep Stack 12/42 (exporting 5/60 GIF frames)...` in Fast mode. Click **Cancel** to stop; the status bar reports **Deep Stack export canceled.**

When the export finishes, the status bar shows the file name, for example `Exported Deep Stack GIF animation to M31_astrostack.gif.`

---

## Input Requirements

| Requirement | Why |
|-------------|-----|
| A folder of supported images | Deep Stack works on sequences, not single frames |
| Supported formats | FITS (`.fit`, `.fits`), XISF, TIFF, PNG, JPG, and common camera RAW files |
| Grayscale or simple RGB/RGBA data | Other layouts stop the export with `Deep Stack currently supports grayscale or simple RGB/RGBA images only.` |
| Plate-solved frames (celestial WCS) | Required when you answer **No** to the alignment question, and for **Moonlight Impact** |
| At least two solved frames for alignment | Alignment stops with `Alignment requires at least two solved frames.` |
| Same pixel size for every frame | Required when you answer **Yes**; the running mean needs identical arrays |
| `reproject` installed | Required for WCS alignment |
| FFmpeg (from `imageio-ffmpeg`) | Required for MP4 export |
| `EXPTIME` in headers (or exposure in the filename) | Used for **Total integration time** |
| `DATE-OBS` in headers | Used for **Moon Illumination** and **Moonlight Impact** |
| Write access and free disk space in the image folder | Export writes a temporary `astrostack_export_*` folder there |

Deep Stack does not plate-solve frames itself. Solve the sequence first if CAst needs to align it.

---

## The Stacking Pipeline

### 1. Load and sort the sequence

Deep Stack reads supported images from the selected folder only and sorts them by filename. The first frame becomes the preview and the WCS reference for any later alignment.

### 2. Optional WCS alignment

If you answer **No** to the "already aligned" question, CAst runs the WCS alignment core that is shared with other modes. It:

1. Validates the celestial WCS on the reference frame and on every source frame.
2. Uses the cropped reference grid when a crop exists, otherwise the full reference frame.
3. Reprojects each frame onto that grid. When the source-to-reference pixel mapping is affine to within 0.08 px, CAst uses a fast affine transform. Otherwise it uses `reproject_interp(...)` with bilinear interpolation.
4. Refines each frame with a star-residual shift: it detects up to 96 stars in both images, finds the most common offset, and applies it if at least three stars agree and the shift is at least 0.05 px.
5. Writes aligned FITS copies to a temporary folder inside the image folder. The folder is deleted when the export ends.

Pixels that fall outside a source frame's footprint are set to 0. If the frames are offset from each other, crop to the area they all cover to avoid dark edges.

Alignment runs in parallel. The worker count follows **Settings > Open Settings > Advanced > Shared Analysis Workers** (Auto by default), capped at 2 workers for reprojection or 4 for the fast affine path.

Frames whose embedded WCS comes from a PixInsight StarAlignment output (an `.xdrz` sidecar is present) are treated as unsolved, because the warped pixels may no longer match the WCS. Answer **Yes** for such already-registered data.

### 3. Build the cumulative stack

Deep Stack uses an unweighted **running mean**:

- frame 1 initializes the stack
- each new frame *n* updates it as `mean += (frame − mean) / n`

All frames carry equal weight. The exported sequence shows the stack evolving from the first frame to the full integrated result.

### 4. Measure cumulative quality

For each exported stack step, CAst measures **Signal**, **Noise**, **SNR**, and **FWHM** on the current cumulative stack. **Frame count** and **Total integration time** come from the frame index and the frame headers. In Fast mode, only the exported steps are measured, so a plot has at most about 60 points.

The measurement methods are described in [How the Metrics Are Computed](#how-the-metrics-are-computed).

### 5. Render overlays and write the animation

Each exported frame is the current cumulative stack, rendered with the preview's display settings, plus:

- the signal and background measurement regions, if defined
- every visible overlay layer (circles, rectangles, text, live value labels, plots)

The default **Stack SNR** card is tied to the cumulative metric series. Its **Integration** line and frame counter update with each frame.

---

## How the Metrics Are Computed

All measurements are made on the cropped data (when a crop exists). For RGB images, the channels are averaged into one luminance plane first.

### Signal and Noise

**With both a Signal region and a Background region:**

- Noise = 1.4826 × MAD of the background pixels (the median absolute deviation, scaled to match a Gaussian standard deviation). If the MAD is zero, the standard deviation is used.
- Signal = mean of the signal pixels − median of the background pixels.

**Without both regions (whole-image fallback):**

- Noise = 1.4826 × MAD of all pixels (standard deviation if the MAD is zero).
- Signal = 99.5th percentile − median of all pixels.

Signal is clipped at 0 and noise has a floor of 1e-6.

### SNR

SNR = Signal / Noise, recomputed on each cumulative stack. For pure random noise, averaging *n* frames lowers the noise by about √*n*, so the curve rises roughly as √*n* until something else, such as gradients or fixed-pattern noise, limits it.

### FWHM

CAst finds the brightest pixel inside the central part of the image (it ignores a margin of one-eighth of the width and height on each side), cuts a 15 × 15 pixel box around it, subtracts a sigma-clipped local background, and measures where the radial profile falls to half its peak. If that fails, the value is 3.0 px. Because it uses a single bright peak, a saturated star or a hot pixel can dominate it.

### Total integration time

The sum of the exposure times of the frames stacked so far. Exposure comes from the `EXPTIME` header keyword, or from the filename when the header has none. Frames with no exposure contribute 0. The card shows the total rounded to minutes as `HHhMMm`, or `--` when the total is 0.

### Moon Illumination

The Moon's illuminated fraction at each frame's mid-exposure time (`DATE-OBS` plus half the exposure), computed with Astropy from the Sun–Moon–observer phase angle. It is topocentric when an observing site is set and geocentric otherwise. If `DATE-OBS` has no time zone, CAst interprets it using **Image Timestamp Timezone** (**Settings > Open Settings > Differential Photometry > Science Export**).

### Moonlight Impact

A relative 0–100 score based on the Krisciunas–Schaefer (1991) scattered-moonlight model:

1. Compute the Moon's altitude, the target's altitude, the target–Moon separation, the phase angle, and the Earth–Moon distance for the frame time and your site. The target is the center of the first frame's plate solution.
2. Estimate the scattered moonlight brightness in nanoLamberts with V-band extinction k = 0.172 mag per airmass. The brightness is 0 when the Moon is below the horizon.
3. Convert that brightness to a sky-brightness change ΔV against a dark zenith sky of 79 nL, and scale it so that 4.5 mag equals 100.

| Score | Category |
|-------|----------|
| below 20 | **Low** |
| 20 to below 40 | **Moderate** |
| 40 to below 65 | **High** |
| 65 and above | **Severe** |

This requires **Observing Latitude** and **Observing Longitude** (**Settings > Open Settings > Setup > Observing Site**; **Observing Elevation** is optional) and a plate-solved first frame. Frames without the inputs show 0 and **Low**. When a plot uses **Moonlight Impact**, its axis label adds the current category, for example `Moonlight impact (Moderate)`.

The moon values are informational. They do not weight, reject, or change any frame in the stack.

### Value formats

Live **Signal** and **Noise** labels use scientific notation with three decimals, for example `Signal: 1.438e-04`. Moon illumination is shown as a percentage with one decimal.

These metrics are useful for comparing stacks, but they are not a replacement for aperture photometry.

---

## Overlays, Plots, and Presets

### Tools

The tool bar at the top right of the panel has these tools, shown as icons:

| Tool | Tooltip | What it does |
|------|---------|--------------|
| **Hand** | Drag to pan the image. | Default tool. Drag pans; nothing is edited |
| **Mouse** | Select and move overlay layers. | Click a layer to select it, drag to move it, drag a corner handle to resize a rectangle or plot, drag the edge handle to resize a circle. Right-click a layer for **Remove Layer** |
| **Circle** | Draw circular overlay layers. | Press at the center and drag out the radius |
| **Rectangle** | Draw rectangular overlay layers. | Drag from corner to corner |
| **Text** | Place text overlay layers. | Click (without dragging) to open **Add Text Layer**, type the text, click **OK** |
| **Plot** | Draw plot overlay layers with stack metrics. | Drag a rectangle to create a plot card |
| **Signal** (**S**) | Draw the signal measurement region for SNR. | Drag the signal region |
| **Background** (**B**) | Draw the background measurement region for SNR. | Drag the background region |

A click without a drag does not create a circle, rectangle, or plot. Text cannot be edited after it is placed; remove the layer and add it again.

### Properties

The **Properties** strip next to the tools shows the settings for the active drawing tool. With **Hand**, **Mouse**, **Plot**, **Signal**, or **Background** active it reads "Select a drawing tool to edit overlay properties."

- **Circle** and **Rectangle**: **Stroke** color, **Fill** color, **Weight** (0.5–12 px), and **Opacity** (5–100%, from the percentage button's slider, or by dragging horizontally on the **Opacity** label).
- **Text**: **Text** color, font, size (6–120 pt), and style (**Regular**, **Bold**, **Italic**, **Bold Italic**).

Changing a property also updates the selected layer.

### Functions

The **Functions** group on the left places live value labels:

- **Signal** (**S** icon): "Place a live Signal value label on the export."
- **Noise** (**N** icon): "Place a live Noise value label on the export."

Click a button, then click on the image. Each label updates on every exported frame. With a label tool active or a label selected, you can set **Prefix** ("Optional prefix"), text color, font, size, and style. Without a prefix, labels read `Signal: <value>` and `Noise: <value>`; with a prefix, they read `<prefix> <value>`.

### Plot Properties

The **Plot Properties** panel edits the selected plot card (and sets the defaults for the next one you draw):

- **Data**: **Title**, **X metric**, **Y metric**, **X label**, **Y label**. Empty labels use the default axis label for the metric.
- **Card**: **Stroke**, **Fill**, **Weight**, **Radius** (corner radius), **Opacity** (background opacity of the card).
- **Text · Title**: **Horizontal** (**Left**, **Center**, **Right**), **Vertical** (**Top**, **Middle**, **Bottom**), **Offset X**, **Offset Y**, **Color**, **Font**, **Style**, **Size**, **Opacity**.
- **Text · Labels**: **Color**, **Font**, **Style**, **Size**, **Opacity**, **Accent** (the color of the **Integration** label on the stack card), and X/Y offsets for the **X label** and **Y label**.
- **Plot area**: **Left**, **Right**, **Top**, **Bottom** margins in percent.
- **Chart**: **Curve** color, **Opacity**, **Width**, and **Highlight** (current-point) color, **Opacity**, **Radius**.

Size, radius, width, and margin fields show **Auto** at 0, which lets CAst choose a value from the card size.

### Plot metrics

Both axes can use any of these metrics. The default axis labels are listed on the right.

| Metric | Default axis label |
|--------|--------------------|
| **Frame count** | `Stacked frames` |
| **Total integration time** | `Integration time (s)` |
| **FWHM** | `FWHM (px)` |
| **SNR** | `SNR` |
| **Signal** | `Signal` |
| **Noise** | `Noise` |
| **Moon Illumination** | `Moon illumination (%)` |
| **Moonlight Impact** | `Moonlight impact score` |

### The Stack SNR card

Opening a folder adds a **Stack SNR** plot card (Frame count against SNR) with an **Integration** line and a frame counter. Only this card has the integration and counter footer; plots you draw with the **Plot** tool show the chart only.

Click **Hide Info** (bottom of the left panel) to hide this card from the preview and the export; the button then reads **Show Info**. You can also move, resize, restyle, or remove the card like any other plot.

### Layers

**Layers** lists the overlay layers, front to back, followed by **Signal region** and **Background region** if they exist.

- **Up**: move the selected layer toward the front.
- **Down**: move the selected layer toward the back.
- **Remove**: remove the selected layer or measurement region.

Selecting a layer in the list also loads its settings into the property controls.

### Presets

Use **Save Preset** and **Load Preset** under **Layers**. The file filter is:

`Deep Stack Preset (*.astrostack.json);;JSON Files (*.json)`

The default file name is `astrostack-layers.astrostack.json` in the image folder.

A preset stores:

- all overlay layers and their styles
- the crop
- the signal and background regions

It does not store display settings or export options.

Presets store positions as fractions of the canvas (and the crop as fractions of the source image), so the same layout can be reused on images of different sizes and crop shapes.

Notes:

- **Save Preset** is enabled once at least one layer or measurement region exists, and saving needs an open folder.
- Loading a preset into a session that already has layers or a crop asks: "Replace the current overlay layers and crop with the preset?"
- Loading a current-format preset needs an open folder.
- Opening a new folder clears the layers, so load the preset after opening the folder.

---

## Export Options

### Deep Stack Export dialog

| Field | Range | Default |
|-------|-------|---------|
| **Processing: Fast mode** | on / off | on |
| **Format** | `GIF`, `MP4` | `GIF` |
| **Frame Rate** | 0.2–50 fps | 20 fps |
| **Frame Time** | 20–5000 ms | 50 ms |
| **Output size** | 10–100% | 100% |
| **Playback: Loop forever** | on / off (GIF only) | on |

**Frame Rate** and **Frame Time** are the same setting: changing one updates the other.

### GIF

GIF is the simplest choice for web pages and presentations. CAst builds one 256-color palette from the first, middle, and last frames and applies dithering. With **Loop forever** off, the GIF plays once. GIF frames are kept in memory until the file is written, so very long full-size exports use a lot of memory.

### MP4

MP4 gives smoother playback and smaller files for the same detail. CAst encodes H.264 (`yuv420p`, CRF 23) without audio, and pads the frame to a multiple of 16 pixels by repeating the edge. MP4 has no loop setting; looping depends on the player.

MP4 needs FFmpeg from the `imageio-ffmpeg` package. If it is missing, CAst shows **MP4 export unavailable** with install instructions and does not start the export.

### Fast mode

**Fast mode** does not skip alignment or the running mean. It writes at most 60 evenly spaced animation frames, always including the first and the last stack, and measures metrics only for those frames.

With Fast mode off, every cumulative stack becomes an animation frame and is measured.

### Output size

Output size scales the image and all overlays together, from 10% to 100% of the stacked (cropped) frame.

---

## Suggested Workflow

1. Put a solved image sequence for one target into a dedicated folder, named so that filename order is time order.
2. Open that folder in **Deep Stack**.
3. Inspect the reference frame and define a crop if the full frame is too wide or the frames do not overlap fully.
4. Draw **Signal** and **Background** regions if you want SNR measured on your target.
5. Set the display stretch and add the labels and plots you want in the export.
6. Save a preset if this is a layout you will reuse.
7. Click **Stack** and tell CAst whether the sequence is already aligned.
8. Export with **Fast mode** first for a quick preview.
9. If the result looks right, export again with **Fast mode** off, or as MP4.

---

## How This Differs from Other Modes

| Mode | Deep Stack relationship |
|------|--------------------------|
| **Differential Photometry** | Measures light curves; Deep Stack is for cumulative image stacking and visual export |
| **Asteroid / Comet Detection** | Works on moving-object time series; Deep Stack assumes you want a deepening integrated stack |
| **Transient Finder** | Searches for variability between frames; Deep Stack emphasizes cumulative signal growth |
| **Sky Explorer** | Identifies what is in one field; Deep Stack builds an animation from many frames |

---

## Limitations

### What Deep Stack does not do

- It is not a full astrophotography post-processing suite.
- It does not calibrate, reject outliers, weight frames, extract backgrounds, deconvolve, or combine color channels.
- It does not plate-solve frames.
- It does not produce science-grade photometric measurements from the live SNR overlays.
- It does not align unrelated images from different fields.
- It does not save the final stacked image; the output is the animation.

### Practical caveats

- Alignment requires a usable celestial WCS in every frame and at least two frames.
- All frames come from one folder, sorted by filename.
- **Fast mode** still aligns and stacks every frame; it only writes and measures fewer output frames.
- MP4 export depends on FFmpeg.
- FWHM is a quality indicator derived from one bright peak, not a full PSF analysis.
- Before export, plots and live labels show first-frame values only.
- Moon values are estimates for context and do not affect the stack.
- Saving a preset, and loading a current-format preset, needs an open Deep Stack folder because positions are stored relative to the image.
- When you leave Deep Stack and come back, the preview shows the empty-folder message unless **Keep mode memory when switching** is on in **Settings > Open Settings > General**. Reopen the folder to continue.

---

## Conclusion

Deep Stack is CAst's "show the image getting deeper" mode. Open a folder, decide whether the frames are already aligned, define the region that matters, and export a cumulative stack that shows integration time and signal growth. It is especially useful when you want a clear, reproducible presentation of how a deep result emerges from a sequence of exposures.

For shared shell conventions and theme controls, see [Themes, Layout, and Shared UI](themes_layout_ui.md).
