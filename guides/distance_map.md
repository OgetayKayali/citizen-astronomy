# Distance Map

## Introduction

Most star fields look flat because images hide the one coordinate your eye cannot see directly: depth.

**Distance Map** takes a solved sky image, queries Gaia DR3 for stars in that footprint, and turns the field into a 3D parallax map. Instead of seeing only where stars land on the detector, you can inspect how they distribute through space, how proper-motion groups cluster, and whether a possible open-cluster-like population stands out against the foreground and background.

This mode is not trying to turn your image into a full physical Milky Way model. It is a field-scale visualization and discovery aid built on Gaia astrometry, designed to make distance structure legible.

### What you can do with this mode

- **Build a 3D map from a single image field.** Open a solved frame and let CAst query Gaia stars inside that footprint.
- **Filter the catalog by usefulness.** Limit stars by G magnitude, distance, parallax SNR, and maximum star count.
- **Inspect the field in 3D.** Switch among overview, top-down, side, and field-centered cameras.
- **Search for likely co-moving groups.** Use **Find Cluster** and the **Advanced...** dialog to highlight likely open-cluster or moving-group members.
- **Compare the 3D view with the original image.** The field image and catalog table stay tied to the same Gaia selection.
- **Save a figure of the 3D view.** Export the rendered distance map as an image.

### Why this matters

Many astronomical images mix stars that only appear close together because they land in the same line of sight. Gaia changed that forever by giving us distances and proper motions at scale. Distance Map brings that perspective into CAst, so your image is not just a 2D patch of the sky but a doorway into the local 3D structure behind it.

---

## How It Works

### Step 1: Open a Source Image

Switch to **Distance Map** mode and click **Open**.

The file picker title is:

`Select source image for Distance Map`

Supported inputs include:

- `.xisf`
- `.fits` / `.fit`
- `.tif` / `.tiff`
- `.png`
- `.jpg` / `.jpeg`

The mode uses one image at a time and expects a usable celestial WCS. If the frame is not already solved, CAst can use the shared astrometry.net resolver when configured.

### Step 2: Let the Initial Build Run

After opening an image, Distance Map restores saved limits and starts a background build automatically.

The main panels are:

- **Field Image**
- **3D Distance Map**
- **Catalog Stars**
- **Work Log**

While the worker runs, the **Open** button temporarily becomes **Building...**.

### Step 3: Review the 3D Field

Once the Gaia query completes, you can inspect:

- the original field image
- the 3D star cloud
- the catalog table
- the work log summary

The default camera is **Orbit Overview**, but you can switch to:

- **Top-Down**
- **Side View**
- **Field Center**
- **Tomography Face-On**

### Step 4: Adjust Query Limits

The main toolbar includes:

- **Mag Limit**
- **Distance Limit**
- **Max Stars**
- **Min SNR**
- **Apply**

Use these controls to rebuild the map with a deeper, nearer, brighter, or cleaner Gaia selection.

### Step 5: Find a Cluster

Click **Find Cluster** to search for a likely open-cluster-like or moving-group population based on Gaia proper motions and, when appropriate, parallax.

If you want finer control first, open **Advanced...** and choose a preset or expert options. After a successful detection:

- likely members are highlighted in 3D
- matching rows are emphasized in the table
- member markers can be shown on the field image
- **Find Cluster** changes to **Find All** so you can clear the highlighted subset

---

## Input Requirements

| Requirement | Why |
|-------------|-----|
| Solved image or astrometry.net access | Gaia stars must be matched to a real sky footprint |
| Network access | Gaia query comes from external catalog services |
| Positive, usable Gaia parallaxes | Needed to turn stars into distances |
| OpenGL-capable environment | Required for the 3D view |

If OpenGL is unavailable, the mode reports:

`OpenGL 3D view is unavailable in this environment.`

---

## The Distance Pipeline

### 1. Resolve the field

Distance Map first validates or solves the image WCS. This is similar in spirit to Sky Explorer: the app needs a reliable celestial footprint before it can ask Gaia what stars live inside it.

### 2. Query Gaia DR3

Using the solved footprint, CAst queries Gaia DR3 through its catalog service. Internally it overfetches beyond the final display count so later filtering still leaves a useful map.

The resulting star set includes data such as:

- name or identifier
- G magnitude
- BP-RP color when available
- parallax and parallax error
- proper motion
- RA / Dec
- derived distance in parsecs

### 3. Convert parallax into distance

Distance Map uses positive parallax stars and derives distance from:

$$
d_{pc} = \frac{1000}{\varpi_{mas}}
$$

where $\varpi$ is parallax in milliarcseconds.

Only stars with usable astrometry make it into the 3D map.

### 4. Build 3D coordinates

The Gaia positions are transformed into 3D Cartesian coordinates in parsecs. That turns the field into a true point cloud rather than a 2D overlay.

The observer sits at the origin, and stars are drawn in space around that reference frame.

### 5. Filter and sort

The main toolbar limits then shape the visible selection:

- **Mag Limit** removes faint stars
- **Distance Limit** rejects stars beyond the chosen depth
- **Max Stars** caps the drawn set
- **Min SNR** rejects weak parallax measurements

By default, the mode also filters stars to the image footprint, so the 3D view corresponds to the actual field rather than the broader Gaia cone alone.

---

## Reading the Visualization

### 3D Distance Map

The central view shows stars in 3D:

- color roughly follows Gaia **BP-RP** from blue to red
- brighter stars appear larger
- likely cluster members gain a stronger visual emphasis

Additional scene elements can be toggled:

- **Labels**
- **Distance Ruler**
- **Parallax Uncertainty**
- **Catalog Sphere**
- **Tomography**

### Labels

**Labels** shows a limited set of object names so the map stays readable. Cluster members are favored when a group has been identified.

### Distance Ruler

**Distance Ruler** adds a line-of-sight depth scale in parsecs, which helps you understand whether you are looking at a compact structure or a long foreground/background mix.

### Parallax Uncertainty

**Parallax Uncertainty** draws line-of-sight uncertainty segments for the stars. This is useful when you want to see whether a visually compact group is actually well constrained in depth or only appears so because of large distance uncertainties.

### Catalog Sphere

**Catalog Sphere** draws a wireframe sphere for a matched globular cluster from the bundled catalog. It is a visualization aid, not a universal deep-cluster model.

### Tomography

**Tomography** adds a translucent field slice through the 3D volume. The mode tip explains:

`Ctrl+left-drag in the 3D view to move the slice.`

This is useful when you want to examine how stars line up near a chosen depth plane rather than only from the default orbit view.

---

## Cluster Detection

### Basic Find Cluster

Click **Find Cluster** to ask CAst for a likely moving-group or open-cluster-like subset.

If no good cluster is found, CAst reports:

`Could not identify a likely cluster from the current map stars. Try loosening the preset or increasing the star count.`

### Advanced cluster settings

Open **Advanced...** to reach **Distance Map Cluster Detection**.

The dialog describes itself as:

`Find likely open-cluster members from Gaia proper motion and parallax, then mark them on the 3D map and field image.`

Preset choices:

- **Default**
- **Tight**
- **Loose**
- **Parallax Priority**
- **Custom**

Additional controls include:

- **Turn on Only Group automatically after detection**
- **Show Expert Controls**
- **Backend** (`Auto (recommended)`, `Lightweight`, `Sklearn DBSCAN`)
- **Strictness**
- **Parallax** (`Auto`, `Require parallax`, `Proper motion only`)
- **Apply magnitude/color cleanup**

### Only Group

After a cluster is found, **Only Group** filters the map and table to the highlighted members. This is the easiest way to inspect a candidate group without the surrounding field clutter.

### Model Cluster Depth

**Model Cluster Depth** is a visualization aid. It can remap highlighted members into a simplified center-weighted depth model so a group reads more clearly in 3D. This is not a physical reconstruction of real cluster structure; it is an interpretive display feature.

---

## Field Image and Catalog Table

### Field Image

The left panel shows the original source frame. After cluster detection, likely members can be marked on the image so you can connect the 3D structure back to the actual stars in the field.

When nothing is loaded, the panel says:

`Open an image to preview the field.`

### Catalog Stars table

Columns:

- **Name**
- **G mag**
- **Parallax**
- **Distance (pc)**
- **RA / Dec**

Rows corresponding to highlighted cluster members are visually emphasized. This table is the quickest way to compare the brightest or nearest stars numerically after the build completes.

### Work Log

The log panel reports progress, query counts, rejections, and cluster outcomes. Its placeholder reads:

`Distance Map progress and summary notes will appear here.`

---

## Saving Output

Use **Save** to export the current 3D view as an image.

The save dialog title is:

`Save Distance Map Image`

Formats:

- PNG
- JPEG
- BMP

This is a screenshot-style export of the 3D scene. Distance Map does **not** currently offer a dedicated CSV, FITS, or catalog export path from this panel.

---

## Suggested Workflow

1. Open a solved field that contains a rich Milky Way patch, cluster, or association candidate.
2. Let Distance Map build its initial Gaia selection.
3. Start with the default **Orbit Overview** and inspect the overall depth spread.
4. If the scene is crowded, reduce **Max Stars** or tighten **Distance Limit**.
5. Raise **Min SNR** if noisy parallaxes are cluttering the result.
6. Run **Find Cluster** or open **Advanced...** first if you want a tighter or looser moving-group search.
7. Turn on **Only Group** to study just the suspected members.
8. Enable **Parallax Uncertainty** or **Tomography** when you want to understand depth confidence and slicing.
9. Save a screenshot of the final 3D view.

---

## How This Differs from Other Modes

| Mode | Distance Map relationship |
|------|----------------------------|
| **HR Diagram** | HR mode studies stellar populations in color-magnitude space; Distance Map studies the same field in 3D astrometric space |
| **Sky Explorer** | Sky Explorer identifies what objects are in one image; Distance Map emphasizes Gaia star depth and clustering |
| **Sky Atlas** | Sky Atlas is an all-sky navigable map; Distance Map is a field-specific 3D reconstruction |
| **Differential Photometry** | Differential mode measures brightness changes; Distance Map uses Gaia geometry rather than time-series flux |

---

## Limitations

### What Distance Map does not do

- It does not infer distances for stars without usable parallax.
- It does not perform image-based star detection from scratch for the 3D model; it is Gaia-driven.
- It does not replace a full scientific cluster-membership study.
- It does not export a research-grade table directly from the mode UI.

### Practical caveats

- Unsovled images still need astrometry.net or existing WCS.
- Sparse fields may not produce a useful cluster candidate.
- Very deep or very large queries can become visually crowded unless you tune the limits.
- **Model Cluster Depth** is illustrative, not a physical cluster-depth solution.
- **Catalog Sphere** is only available when a relevant bundled globular entry matches the current context.

---

## Conclusion

Distance Map is CAst's “add depth to the field” mode. Open a solved image, let Gaia provide the third dimension, and use the 3D view to see whether your field is a loose foreground/background mix or a more coherent spatial structure. It is especially valuable when you want to connect a familiar 2D image to the real geometry behind it.

For shared shell conventions and themes, see [Themes, Layout, and Shared UI](themes_layout_ui.md).
