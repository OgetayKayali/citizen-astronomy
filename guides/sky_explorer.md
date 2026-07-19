# Sky Explorer

## Introduction

Every deep image is denser than it looks. Behind the obvious bright star or the familiar nebula sits a field full of named objects, anonymous Gaia stars, cataloged variables, exoplanet hosts, and sometimes a solar-system interloper that happened to cross your frame on that night.

**Sky Explorer** turns a single plate-solved image into an annotated sky census. You open a FITS, XISF, or common image format, choose which object classes you care about, click **Explore**, and CAst queries the major catalogs that cover your footprint. Results appear as overlays on your image and as a searchable table you can inspect, filter, center on, and export.

### What you can do with this mode

- **Identify what is in your field.** Deep-sky objects, stars, variables, exoplanet hosts, Gaia sources, and known solar-system bodies are matched to your WCS footprint and drawn on the image.
- **Choose how deep the census goes.** Cycle object-type modes from **Simple** (six common deep-sky classes) through **Advanced** to **Scientific** (SIMBAD-style type codes).
- **Compare with survey imaging.** Overlay DSS2 Blue, SHS Hα, PanSTARRS, or IPHAS Hα cutouts with an interactive divider so you can compare your frame to public survey data.
- **Annotate by hand.** Draw circles, ellipses, and text labels; edit stroke, fill, weight, opacity, and fonts; keep automatic catalog overlays on or off.
- **Probe magnitude reach.** **Mag Limit** marks representative Gaia stars in half-magnitude bins so you can see how deep the frame goes.
- **Export the view.** Save still images of the annotated field, GIF/MP4 comparison animations when a survey layer is loaded, or a **Collage** of catalog objects that have usable angular-size metadata.

### Why this matters

Catalog pages and planetarium software know the sky in the abstract. Sky Explorer answers a more practical question: *what is in this exposure?* That is the question you ask when planning follow-up photometry, writing an observing report, teaching a class from your own data, or simply wanting to understand the field you spent the night collecting.

---

## How It Works

### Step 1: Switch Mode and Open an Image

Enter **Sky Explorer** from the mode launcher or the mode menu.

Click **Open** (or use `File > Open File`) and choose a source image. Supported formats:

- `.fits` / `.fit`
- `.xisf`
- `.tif` / `.tiff`
- `.png`
- `.jpg` / `.jpeg`

The image preview loads into the panel. The primary button switches from **Open** to **Explore**. Status text indicates that the image is ready to explore.

Sky Explorer works on **one image at a time** (not a time-series folder). Prefer a frame with a valid celestial WCS already written into the headers. If WCS is missing, configure an **astrometry.net API key** in Settings so CAst can solve the frame before catalog queries.

### Step 2: Choose Object Types

The object-type table lists classes CAst can search for. Columns include stroke/fill/text styling, group, object type, and a short description.

Cycle the mode button through:

| Mode | What you get |
|------|----------------|
| **Simple** | Six common deep-sky classes: Emission Nebula, Reflection Nebula, Dark Nebula, Galaxy, Open Cluster, Globular Cluster |
| **Advanced** | Broader human-readable classes (planetary nebulae, SNRs, AGN, stars, variables, asteroids, and more) |
| **Scientific** | Exact SIMBAD-style object-type codes plus extras such as Gaia Star, VSX Variable, Exoplanet Host, Asteroid/Comet |

Help text under Simple mode: *Simple mode keeps the six common deep-sky classes visible. Switch to Advanced for broader classes or Scientific for exact SIMBAD-style codes.*

Use **Select All** / **Unselect All** to toggle rows quickly. Open the **Filter** menu and optionally enable **Hide Objects Without Magnitude** to drop catalog hits that lack a usable magnitude.

Click a stroke/fill/text cell to customize how that type is drawn. Per-type colors and fonts are remembered with your layout.

### Step 3: Explore

Click **Explore**. The button shows **Exploring...** while CAst:

1. Validates (or solves) WCS and computes the field footprint
2. Queries each enabled catalog layer for objects inside that footprint
3. Filters and sorts results
4. Builds automatic overlays and fills the results table

Progress and summary notes stream into the **Work Log** tab. When finished, review the **Results** table and the annotated image.

You need at least one selected object type whose catalog layer is enabled under **Settings → Sky Explorer → Catalog Sources**.

---

## Input Requirements

| Requirement | Why |
|-------------|-----|
| Celestial WCS (or astrometry.net key) | Catalog positions must project onto image pixels |
| Network access | SIMBAD, VizieR, NASA Exoplanet Archive, Horizons, hips2fits |
| Observation time in header (`DATE-OBS`) | Required for solar-system predictions |
| Observing site in Settings | Improves Horizons geometry for SSO search |
| Catalog Sources enabled | Layers you want must be checked in Sky Explorer settings |

Without WCS and without an API key, Explore cannot place catalogs on the image.

---

## The Identification Pipeline

Sky Explorer’s core call is `explore_sky_image` in the application core. Conceptually:

### 1. Resolve WCS and footprint

CAst validates embedded celestial WCS keywords. If they are missing or unusable and an astrometry.net key is available, it solves the image (cached under the Sky Explorer WCS cache). From the solved frame it derives center, search radius, and corner sky coordinates for catalog queries.

### 2. Query catalog layers

Enabled layers run in a fixed order:

1. **Deep sky** (SIMBAD + VizieR supplements such as HyperLEDA, Sharpless, Barnard, van den Bergh, NGC 2000)
2. **General SIMBAD objects**
3. **Solar system** (JPL Horizons / known SSO search, magnitude ≤ 18)
4. **Variable stars** (VSX)
5. **Gaia DR3 stars**
6. **Exoplanet hosts** (NASA Exoplanet Archive)

Wide fields are tiled for SIMBAD (about 20′ tiles with a time budget) so large mosaics still return useful hits without hanging forever.

### 3. Filter and sort

Non–deep-sky objects fainter than **Gaia Max Mag** are dropped. Deep-sky objects with unknown magnitude are kept unless you enabled **Hide Objects Without Magnitude**. Results are sorted by layer, then magnitude, then name.

Gaia rows shown in the UI are capped (brightest first; display limited to a few hundred) even when the query itself retrieved more.

### 4. Present overlays and table

Automatic annotations use catalog angular sizes and position angles when available (ellipses for galaxies, scaled nebulae when enabled). Labels use short names. Colors follow object-group settings and any per-type overrides from the type table.

### Dense HyperLEDA exception

When object-type mode is **Scientific** and **every** Scientific type is selected, unnamed HyperLEDA galaxies are included for a denser galaxy census. Otherwise HyperLEDA entries generally need a usable name/alias to appear.

---

## Results, Inspector, and Navigation

### Results table

Columns: **Name**, **Type**, **Mag**, **RA**, **Dec**, **Angular Size**.

Rows are grouped (Nebulae / ISM, Galaxies, Stars, Variable Stars, Solar System, Exoplanets, and so on). Click a group header to collapse or expand it (`[collapsed]` appears when closed).

| Action | Effect |
|--------|--------|
| Click a row | Fills **Inspector**; highlights the object on the image |
| Ctrl/Shift-click rows | Multi-select for **Collage...** (the current row still drives Inspector, centering, and overlay highlighting) |
| Double-click a row | Opens the object’s catalog page in your browser (Gaia → VizieR, VSX → AAVSO, exoplanets → NASA archive, otherwise SIMBAD) |
| **Center Object** | Pans the image so the selection sits near the view center |

### Inspector and Work Log

Bottom tabs:

- **Work Log** — Explore progress and summary notes
- **Inspector** — Coordinates, identifiers, and metadata for the selected row

Placeholders appear until you have explored or selected something.

---

## Image Tools

### Display

The **Display** menu controls how your source image is stretched for viewing:

- **Auto Stretch**
- **None**
- **Asinh**
- **Sqrt**
- **Log**

Also available: **Curves**, **Invert**, and **Reset**. Stretching is for inspection; catalog positions come from WCS, not from the display transform.

### Surveys

Open **Surveys** and pick a HiPS survey cutout aligned to your WCS:

- **None**
- **DSS2 Blue**
- **SHS Ha**
- **PanSTARRS**
- **IPHAS DR2 Ha**

When a survey is active, an interactive divider lets you wipe between your image and the survey raster. Nested Display controls can stretch the survey pane independently. Pan/zoom may refine the cutout after the view settles.

Survey rasters are cached locally (Sky Explorer survey cache) so repeat comparisons are faster.

### Mag Limit

Toggle **Mag Limit** to annotate representative Gaia stars in **0.5-mag** bins. By default, one example star is chosen per bin (near the bin center magnitude, preferring stars closer to the field center). Marker colors, stroke, text, and sizes are configurable in Settings.

Enabling Mag Limit causes Explore to include Gaia even if the Gaia Star type row is unchecked, so the magnitude ladder has data to draw.

### Auto overlays

**Auto** toggles automatic catalog annotations on the image. Turn it off when you want a clean frame for manual drawing or export.

### Manual annotations

Toolbar tools (icon buttons):

- **Mouse** — select / manipulate
- **Circle**
- **Ellipse**
- **Text**

The **Properties** strip edits **Stroke**, **Fill**, **Text**, **Weight**, and **Opacity** (opacity can be adjusted by dragging). Text styles include Regular, Bold, Italic, and Bold Italic.

Right-click the image for:

| Item | Role |
|------|------|
| **Search** | Open a SIMBAD coordinate search in the browser at the click position (uses **Search Radius**) |
| **Detect** | Run a SIMBAD cone search at the click and merge detections into results/overlays |
| **Edit Annotation** / **Delete Annotation** | Modify or remove a manual mark |
| **Clear Manual Annotations** | Remove hand-drawn marks |
| **Clear Detections** | Remove Detect hits |

The **Manual Annotation** dialog edits geometry (X, Y, radii, rotation), line width, font, opacity, colors, and whether text/fill are shown. Manual annotations are stored **per image** for the session.

---

## Exports

### Image...

**Export → Image...** saves the current view (source stretch, overlays, and survey comparison if visible) as PNG, JPEG, or BMP. Default name pattern: `{stem}_sky_explorer.png`.

### Collage...

**Collage...** (beside Mag Limit / Export) builds a figure from catalog objects that have usable angular-size metadata (`catalog_major_axis_arcmin`, `catalog_minor_axis_arcmin`, or `catalog_size_arcmin`). Objects without size data are skipped and counted in the dialog summary and Work Log.

The collage dialog offers:

| Option | Choices / default |
|--------|-------------------|
| **Objects** | **Selected Objects** or **All Eligible Visible Objects** |
| **Layout** | **Uniform Tiles** (same cell size), **Natural Sizes** (each crop keeps its pixel footprint in a grid), or **Packed Mosaic** |
| **Margin** | Extra sky margin around each catalog size; default **15%** |
| **Labels** | Optional object-name strips under each tile |

Crops use the active Sky Explorer stretch/curves/invert settings and the solved WCS so rotated or unequal pixel scales stay correct. Edge targets are padded so they remain centered in their tiles. Column count is chosen automatically. Output formats match still export: PNG, JPEG, or BMP (`{stem}_sky_explorer_collage.png` by default).

**Packed Mosaic** sorts crops by catalog sky footprint from largest to smallest, then packs them into an interlocking mosaic that fills a clean rectangle. Each tile is cover-fitted into its cell with a themed gutter between neighbors, so the sections stay visually separate without black letterboxing.

### Animation...

**Export → Animation...** builds a comparison animation that moves the survey divider. Requirements:

- a survey selected **and** its raster loaded

The **Export Comparison Animation** dialog sets:

- **Duration**
- **Frame rate**
- **Output size**
- **Motion quality** (including smooth divider motion)
- **Divider motion** (for example return divider to the left)
- **Playback** (loop forever for GIF)

Save as GIF or MP4 (`{stem}_sky_explorer_comparison.gif` by default). MP4 needs the packaged video/ffmpeg dependencies.

---

## Settings

Open **Settings → Sky Explorer**.

Intro text in the dialog: *Sky Explorer settings control the manual SIMBAD lookup radius and which background search sources are allowed when Explore resolves a field.*

| Setting | What it does |
|---------|----------------|
| **Search Radius** | Cone radius (arcseconds) for right-click **Search** / **Detect** (default 10″) |
| **Gaia Max Mag** | Magnitude ceiling for Gaia / VSX / exoplanet filtering of non–deep-sky hits (default 17) |
| **Gaia Hard Cap** / **Gaia Cap Rows** | Optional hard limit on Gaia rows retrieved |
| **Mag Limit Examples** | How many Gaia stars to label per 0.5-mag bin |
| Mag Limit marker / text colors and sizes | Appearance of Mag Limit annotations |
| **Galaxy Mag Limit** / **Galaxy Max Mag** | Optionally hide faint or unknown-magnitude galaxies on the overlay |
| **Galaxy Shape Only** | Only draw galaxies that have ellipse metadata |
| **Extended Nebula Scale** | Enlarge nebula overlays for visibility |
| **Scale Stroke Width** | Thicken outlines on large objects |
| **Marker Color Relation** | Fill bright / stroke dark (or the inverse) |
| **Text Color Relation** | Dark or bright default labels |
| **Fill Opacity** / **Stroke Opacity** | Automatic marker transparency |
| **Object Group Colors** | Base hues per result group |
| **Catalog Sources** | Enable/disable: SIMBAD Deep Sky, SIMBAD General Objects, Solar System Objects, VSX Variable Stars, Gaia DR3 Stars, NASA Exoplanet Hosts |

Also persisted automatically: splitter sizes, column widths, and per-type style overrides from the type table.

Global settings that matter here: **Astrometry API Key**, observing site, and image timestamp timezone for `DATE-OBS` interpretation.

---

## Catalogs and Services

| Source | Role |
|--------|------|
| SIMBAD (CDS) | Named deep-sky and general objects; Detect / Search |
| Gaia DR3 (VizieR) | Field stars and Mag Limit ladder |
| VSX (VizieR) | Variable stars |
| NASA Exoplanet Archive | Confirmed exoplanet host stars |
| HyperLEDA / Sharpless / Barnard / VdB / NGC 2000 (VizieR) | Galaxy and nebula supplements |
| JPL Horizons | Known asteroids and comets in the field |
| hips2fits (CDS) | Survey cutouts for comparison |
| astrometry.net | Optional plate-solve fallback |

Results and solves are cached under Sky Explorer catalog / WCS / survey cache directories so repeat work on the same field is cheaper.

---

## Suggested Workflow

1. Open a plate-solved deep image of a rich field (cluster, nebula complex, or wide Milky Way patch).
2. Start in **Simple** mode with the six deep-sky types selected; click **Explore**.
3. Collapse groups you do not need; click interesting rows and use **Center Object**.
4. Switch to **Advanced** or **Scientific** and re-Explore if you need stars, variables, AGN, or SSO.
5. Turn on **Mag Limit** to judge depth; toggle **Auto** off if overlays clutter a presentation frame.
6. Load **DSS2 Blue** or an Hα survey and scrub the divider for a before/after comparison.
7. Add a few manual labels for teaching slides; **Export → Image...** or **Animation...**. Use **Collage...** when you want a multi-object size-aware figure of galaxies/nebulae from the same frame.

---

## How This Differs from Other Modes

| Mode | Sky Explorer relationship |
|------|---------------------------|
| **Differential Photometry** | Measures light curves; Sky Explorer identifies and annotates a single field |
| **HR Diagram** | Builds a CMD from Gaia matches; Sky Explorer shows what object *classes* are present |
| **Asteroid / Comet Detection** | Time-series movers and discovery; Sky Explorer can still flag known SSOs on one frame via Horizons |
| **Transient Finder** | Looks for frame-to-frame variability; Sky Explorer is a static catalog census |
| **Sky Atlas** | All-sky interactive atlas; Sky Explorer is anchored to *your* image pixels |

---

## Limitations

### What Sky Explorer does not do

- It does not replace careful catalog verification for discovery claims.
- It does not photometer targets (use Differential Photometry for that).
- It does not run multi-frame moving-object discovery (use Asteroid / Comet Detection).
- Manual annotations and Detect hits are session/per-image aids, not a full exportable discovery catalog.
- Wide fields may under-sample SIMBAD because of tiling and time budgets.

### Practical caps

- Gaia UI display is limited to the brightest subset of returned stars.
- SIMBAD layers have row and timeout budgets; huge mosaics may miss faint or peripheral objects.
- Solar-system search needs a usable observation time and benefits from a correct site.
- Survey animation export requires a loaded survey raster; MP4 needs ffmpeg support in the build.

### What could be improved

- Richer offline/packaged catalog subsets for travel use
- Explicit export of the results table to CSV
- Clearer progress for per-layer query timing on slow networks

---

## Conclusion

Sky Explorer is the “what am I looking at?” mode. Point it at a solved image, pick the object classes that matter for your night, and let the catalogs paint the field. Use Mag Limit and survey comparison when you are teaching or planning, export the annotated view when you want a figure that still looks like *your* data, and use Collage when you want a tidy multi-object cutout board from catalog sizes.

For shared shell themes and layout conventions, see [Themes, Layout, and Shared UI](themes_layout_ui.md). For the repository map, see [CODEBASE_MAP.md](../CODEBASE_MAP.md).
