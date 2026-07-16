# HR Diagram

## Introduction

Every star has a place on a map drawn more than a century ago -- a map that still tells us more about stellar life than almost any other plot in astronomy.

The Hertzsprung-Russell diagram (HR diagram) places stars by color (temperature) against intrinsic brightness (luminosity). Hot blue stars sit to the left. Cool red stars sit to the right. Giants rise above the main sequence. White dwarfs settle below it. Look at a cluster, and you are not looking at a random spray of points: you are looking at a population that formed together, aged together, and left a fingerprint of that shared history on the diagram.

**Citizen Astronomy (CAst)** turns a single deep image of a star field into an interactive HR diagram. You open a plate-solved FITS or XISF frame, CAst detects the stars on your image, matches them to Gaia DR3, computes absolute magnitudes from parallax, and plots a color-magnitude diagram you can explore, filter, and export.

### What you can do with this mode

- **Build a color-magnitude diagram from your own data.** See your field in Gaia BP-RP color versus absolute G magnitude, with educational temperature and luminosity scales.
- **Explore stellar populations.** Overlay main-sequence, giant, subgiant, white-dwarf, and age-guide curves to understand what kinds of stars you are looking at.
- **Find co-moving groups.** Use Gaia proper motions (and parallax when available) to detect stars that share a common space motion -- the kinematic signature of open clusters, associations, and moving groups.
- **Connect the plot to the sky.** Click a point on the diagram and see the same star on your source image, with apertures, selection markers, and optional proper-motion vectors.
- **Export science-ready figures.** Save themed snapshots, clean scientific plots, annotated source images, and working tables for further analysis.

### Why this matters

Professional surveys have mapped billions of stars, but the educational power of an HR diagram is strongest when it is *your* field -- the cluster you imaged, the association in *your* frame. Seeing the main sequence turn off, spotting a handful of giants, or finding a co-moving subgroup in your own data is how stellar evolution stops being an abstract textbook figure and becomes something you measured.

Amateur images of open clusters, globular clusters, and rich Milky Way fields already contain the photometry and the sky positions. Gaia supplies the colors, parallaxes, and proper motions. CAst brings them together.

---

## How It Works

### Step 1: Choose a Source Image

Switch to **HR Diagram** mode and open a single FITS, FIT, or XISF image with `File > Open File`.

HR mode expects:

- a **plate-solved** image with usable celestial WCS
- preferably a **linear RGB** image (color-calibrated if possible)
- network access for Gaia (and related catalog) lookups

The preview can be stretched for comfortable viewing, but photometric measurement is performed on the original linear image data. If the image lacks a valid WCS, configure an astrometry.net API key so CAst can solve it first.

Unlike Differential Photometry, HR mode works from **one source image at a time**, not a time-series folder.

### Step 2: Set Working Directory and Source Limits

The **working directory** stores HR artifacts such as the working table (`hr_working_table.csv` / `.json`). If you do not choose one, CAst derives a default from the selected image path.

**Max Sources** limits how many Gaia-matched detections are measured:

- default: **5000**
- `0` means all matched sources
- in dense fields, the brightest matches are kept first

This limit is the main control on full regeneration time.

### Step 3: Optional ROI

Before generating, you can draft a region of interest on the source image:

- **Circle ROI** (default) or **Rectangle ROI**
- hold **Shift** and left-drag to draft
- plain drag and mouse wheel keep pan/zoom
- **Invert ROI** flips the mask so stars *outside* the region are kept

Important design rule: detection and measurement still happen for the field when you open/prepare the image. The ROI mainly controls which already-measured stars appear on the diagram. That separation is what makes **Update** fast when only the ROI changes.

### Step 4: Open (Generate the Diagram)

Click **Open** to choose the source image (or set the path from the HR controls). Opening the image starts the full preparation workflow automatically. CAst then:

1. Resolves the image WCS footprint.
2. Queries field catalogs (primarily Gaia DR3).
3. Detects stars on the image itself.
4. Cross-matches detections to Gaia.
5. Measures matched sources through aperture photometry.
6. Derives plot quantities (colors, absolute magnitudes, zero-point proxies).
7. Writes working-table outputs and refreshes the plot and Source Results table.

While generation runs, a thin progress strip reports the current stage. Optional background name resolution can continue after the first plot appears.

---

## The Measurement Pipeline

### Star Detection on the Image

CAst does not simply drop Gaia catalog positions onto the plot. It first finds stars that are actually present in *your* pixels.

Detection uses `photutils.detection.DAOStarFinder` on a background-subtracted centroid plane:

- Background median and scatter from 3-sigma clipped statistics.
- Detection threshold: **5 times** the background standard deviation.
- Assumed FWHM for the finder: between 2 and 8 pixels, scaled from the aperture radius (`0.8 * aperture_radius`).
- Border pixels are excluded; detections must lie inside the usable image margin.
- Candidates are sorted brightest-first by peak value.

### Cross-Match to Gaia

Detected positions are converted to sky coordinates through the image WCS and matched to Gaia stars with Astropy's `match_to_catalog_sky`.

The maximum match radius adapts to the plate scale and aperture:

$$
\theta_{\max} = \max\!\left(1.5'',\; \min\!\left(6.0'',\; \max(2.5\,\text{pix}\cdot s,\; r_{\text{ap}})\right)\right)
$$

where $s$ is the mean pixel scale in arcseconds and $r_{\text{ap}}$ is the aperture radius in pixels. When multiple detections compete for the same Gaia star, the closest match wins.

### Aperture Photometry

Matched stars are measured with circular apertures and annuli (same photutils tooling used elsewhere in CAst):

- Fluxes are measured in red, green, and blue channels when available, plus a luminance combination.
- Local sky is estimated from a circular annulus.
- Instrumental magnitudes use $m = -2.5\log_{10}(F)$.
- An instrumental color index $B - R$ is available as a fallback when Gaia BP-RP is missing.
- Saturation and non-positive flux conditions are flagged.

### Zero-Point Proxy from Gaia G

For stars with both a Gaia G magnitude and a usable instrumental luminance magnitude, CAst builds a simple photometric zero point:

$$
\text{ZP} = \mathrm{median}\!\left(G_{\text{Gaia}} - m_{\text{inst, lum}}\right)
$$

Calibrated luminance magnitudes are then:

$$
m_{\text{cal}} = m_{\text{inst, lum}} + \text{ZP}
$$

This is not a full standard-system transformation. It is a practical bridge between your image and Gaia so that a measured absolute-magnitude *proxy* can be computed when useful.

### Absolute Magnitude from Parallax

The default educational Y-axis uses Gaia absolute G magnitude. Given apparent magnitude $m$ and parallax $\varpi$ in milliarcseconds:

$$
M = m + 5\log_{10}(\varpi) - 10
$$

This is the standard conversion when parallax is in mas (equivalent to the usual $M = m - 5\log_{10}(d_{\mathrm{pc}}) + 5$ with $d = 1000/\varpi$).

CAst computes:

- **Gaia Absolute G Magnitude** from Gaia G + Gaia parallax
- **Measured Absolute Magnitude Proxy** from the calibrated luminance magnitude + parallax, when the zero-point step succeeded

Only positive, finite parallaxes are used. Rows without usable parallax can be hidden with **Require Parallax** in Settings.

### Plot Color Index

The default X-axis is **Gaia BP-RP**. If BP-RP is unavailable for a star, CAst falls back to the instrumental blue-minus-red color when that is available. Point display colors preferentially follow a Gaia BP-RP palette, with an instrumental RGB fallback.

---

## Reading the Diagram

### Default Educational View

By default the plot shows:

- **X:** Gaia BP-RP
- **Y:** Gaia Absolute G Magnitude (brighter upward / fainter downward as usual for magnitude axes)

When those axes are active, CAst also shows secondary physical scales:

- **Bottom axis:** approximate color temperature in kelvin
- **Left axis:** luminosity in solar units ($L_\odot$), decade-scaled

The raw Gaia axes move to the top and right when those conversions are meaningful. If you switch to axes where the conversions do not apply, the plot falls back to ordinary bottom/left primary labels.

### Temperature from BP-RP

Color temperature is **not** solved from a full stellar-atmosphere model. It is an educational interpolation between fixed BP-RP and temperature anchors spanning roughly:

| BP-RP | Approx. $T$ (K) |
|---|---|
| -0.4 | 30,000 |
| 0.0 | 10,000 |
| 0.6 | 6,200 |
| 1.2 | 4,600 |
| 2.2 | 3,200 |
| 3.0 | 2,600 |

Intermediate values are linearly interpolated in BP-RP.

### Luminosity from Absolute Magnitude

Luminosity relative to the Sun uses:

$$
\frac{L}{L_\odot} = 10^{\,(M_{G,\odot} - M_G)/2.5}
$$

with $M_{G,\odot} = 4.67$ as the solar absolute G magnitude constant used in the plot widget. Tick labels are shown as decade-friendly $L_\odot$ values.

### Class Guides and Age Guide

For the Gaia BP-RP versus Gaia Absolute G educational view you can overlay:

- **Class guides:** Main Sequence, Giants, Supergiants, Subgiants, White Dwarfs
- **Age guide:** an educational parametric curve at a selectable age in Gyr (default 12 Gyr; clamp range roughly 0.1-13.5 Gyr)

These are educational overlays, not fitted theoretical isochrones for your specific cluster metallicity and distance.

### Filtering and Presentation

Useful controls include:

- hide flagged or saturated rows
- **Require Parallax**
- Gaia G apparent-magnitude min/max range
- fixed marker size versus brightness-scaled markers
- color saturation and point opacity
- editable plot title
- high-density downsampling so crowded fields stay interactive

### Source Results and Selection

The Source Results table lists measured stars with columns such as Gaia magnitudes, BP-RP, parallax, and proper motion. Selection is linked across the plot, table, and source image:

- click a plot point to select a star
- click a table row to highlight the same star on the plot
- multi-select rows to add/remove them from the plot or clear manual overrides
- right-click **Copy Name** for quick external searches
- open exact catalog pages in CDS, SIMBAD, Gaia, or VSX when a real object name is known

A small plot popup can show local row values plus derived temperature and luminosity, with cached SIMBAD spectral type when available.

---

## Find Cluster (Common Motion Group)

Open clusters and moving groups are not defined only by where they sit on an HR diagram. They are also co-moving through space. **Find Cluster** looks for that kinematic signature in your Gaia-matched sample.

### What it uses

For each eligible star:

- proper motion in RA and Dec (mas/yr)
- optionally parallax (mas), when enough stars have positive parallax values

Features are robustly normalized (median / MAD scaling) before clustering.

### Clustering

CAst runs a DBSCAN-style search:

- **Lightweight** built-in DBSCAN (default for smaller samples)
- **Sklearn DBSCAN** for larger samples (auto-selected around 180+ eligible points), with fallback to the lightweight path if sklearn is unavailable

Neighborhood size (`eps`) is estimated from k-th neighbor distances and scaled by a **strictness** factor. The largest cluster is retained as the candidate common-motion group.

Minimum requirements are intentional and conservative: at least five stars with usable proper motion, and enough points after parallax filtering when parallax mode demands it.

### Presets

| Preset | Intent |
|---|---|
| **Default** | Balanced auto backend, auto parallax when enough stars support it, HR cleanup on |
| **Tight** | Stricter cluster threshold; emphasize the dense core |
| **Loose** | Broader associations; parallax ignored; HR cleanup off |
| **Parallax Priority** | Require positive parallax for every candidate; keep HR cleanup |
| **Custom / Advanced** | Manual backend, strictness, parallax mode, and cleanup |

### HR Cleanup

After the astrometric cluster is found, optional **HR consistency refinement** removes members that are outliers in BP-RP / absolute-magnitude space relative to the group. This reduces contaminants that share motion by chance but do not belong on the same evolutionary sequence.

### Only Group

When enabled (manually, or automatically after detection if that Advanced option is on), the diagram and related views can be narrowed to the detected members. After a successful detection, the button becomes **Find All**, which clears the motion group and restores the broader sample.

Members are highlighted consistently across:

- the HR plot
- Source Results
- source-image selection markers and proper-motion overlays
- scientific exports that mark group members distinctly

---

## Source Image Overlays

The left **Image** view keeps your original field in context:

- measurement aperture and annulus for the selected star
- selection circles for plot selection, table selection, or motion-group members
- optional Gaia proper-motion vectors for the currently plotted subset
- vector color/width settings, plus optional color-by-angle mapping so similar directions share similar hues
- Ctrl + click a star on the image to select it (highlights the plot/table and enables catalog browser buttons)
- multi-select Source Results rows to add/remove them from the plot or clear manual overrides
- **Save Image...** for the current annotated view
- **Show Motion / Hide Motion** to toggle Gaia proper-motion vectors for the currently plotted subset

RGB source previews remain in color so the eye can compare the plot to the field.

---

## Update / Reset vs Full Re-prepare

- **Update** applies a draft ROI (and related display filters) to already-measured rows without redoing detection and photometry. Use this for interactive exploration.
- **Reset** clears the applied ROI and restores the broader measured set in the diagram workflow.
- Opening a new image (or changing measurement settings that invalidate the cache, such as max sources or aperture radii) triggers a full re-prepare: catalogs, detection, matching, and measurement.

---

## Exports

HR mode supports several export paths:

| Export | What you get |
|---|---|
| **Snapshot** | Current plot area exactly as shown (theme, zoom, overlays) |
| **Scientific** | Cleaner scientific-style plot from the full filtered dataset at current axis limits |
| **Image + Plot...** | Annotated source image and visible HR plot side by side |
| **Source Image...** | Annotated source-image pane alone |
| **Working table** | `hr_working_table.csv` / `.json` with measured and derived columns |
| **HR File > Export bundle** | Named folder with annotated source image, scientific HR diagram, and matching science-table CSV |

Scientific exports can keep motion-group members visually distinct and pin temperature-axis ticks to readable BP-RP temperature anchors.

---

## Limitations

### What HR mode does not do

- **It is not cluster fitting.** Class guides and the age curve are educational overlays, not tailored isochrones for your cluster's age, metallicity, and reddening.
- **It is not full photometric standardization.** The Gaia G zero-point bridge is a practical proxy, not a Johnson/Cousins or Sloan transformation with color terms.
- **It does not replace Gaia Archive science pipelines.** Parallaxes, BP-RP colors, and proper motions come from Gaia; CAst organizes and visualizes them against your image detections.
- **It is single-image.** Time-series variability and multi-epoch photometry belong in Differential Photometry / Transient Finder, not here.
- **Crowded cores remain hard.** DAOStarFinder + aperture photometry can blend or miss stars in dense cluster cores where PSF photometry would do better.
- **Motion groups are statistical.** Common proper motion does not prove physical membership by itself. Chance alignments, binaries, and field contaminants remain possible.

### What could be improved

- Reddening / extinction corrections and true isochrone overlays for specific clusters
- PSF photometry for crowded fields
- Explicit membership probabilities instead of a single dominant DBSCAN cluster
- Deeper integration of SIMBAD spectral types into automated classification
- Distance-prior or Bayesian absolute-magnitude treatments for noisy parallaxes

---

## Conclusion

An HR diagram is a story about how stars are born, live, and die -- told in color and brightness. With one deep image and Gaia behind it, CAst lets you place *your* field on that story: see the main sequence, notice the outliers, chase a co-moving group, and export figures you can share, teach with, or study further.

The diagram on your screen is not just a plot. It is a census of the stars you pointed at, drawn with the same axes astronomers have used for more than a hundred years.

Every cluster begins as a cloud. Every sequence on the diagram begins with someone deciding to look.
