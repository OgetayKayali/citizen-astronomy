# Sky Explorer

## Introduction

Every deep image is denser than it looks. Behind the obvious bright star or the familiar nebula sits a field full of named objects, anonymous Gaia stars, cataloged variables, exoplanet hosts, and sometimes a solar-system interloper that happened to cross your frame on that night.

**Sky Explorer** turns a single plate-solved image into an annotated sky census. You open a FITS, XISF, or common image format, choose which object classes you care about, click **Explore**, and CAst queries the major catalogs that cover your footprint. Results appear as overlays on your image and as a searchable table you can inspect, filter, center on, and export.

### What you can do with this mode

- **Identify what is in your field.** Deep-sky objects, stars, variables, exoplanet hosts, Gaia sources, and known solar-system bodies are matched to your WCS footprint and drawn on the image.
- **Choose how deep the census goes.** Cycle object-type modes from **Simple** (six common deep-sky classes) through **Advanced** and **Scientific** (SIMBAD-style type codes) to **Catalog** (NGC, IC, LDN, VdB, LBN, and similar, labeled with catalog names only).
- **Compare with survey imaging.** Overlay DSS2, PanSTARRS, SDSS, 2MASS, WISE, GALEX, SHS, IPHAS, or NSNS DR0.2 cutouts with an interactive divider so you can compare your frame to public survey data.
- **Annotate by hand.** Draw circles, ellipses, rulers, and text labels; edit stroke, fill, weight, opacity, and fonts; keep automatic catalog overlays on or off.
- **Probe magnitude reach.** Use **Tools → Estimate magnitude limit...** to mark a Gaia magnitude ladder or estimate the actual detection limit from SNR probing.
- **Export the view.** Save still images of the annotated field at the original image resolution, with overlay text and strokes sized to match the on-screen view (not a window screenshot), GIF/MP4 comparison animations when a survey layer is loaded, or **Tools → Create collage** for catalog objects that have usable angular-size metadata.

### Why this matters

Catalog pages and planetarium software know the sky in the abstract. Sky Explorer answers a more practical question: *what is in this exposure?* That is the question you ask when planning follow-up photometry, writing an observing report, teaching a class from your own data, or simply wanting to understand the field you spent the night collecting.

---

## How It Works

### Step 1: Switch Mode and Load a Source

Enter **Sky Explorer** from the mode launcher or the mode menu.

Use **Image** or **Survey** (or `File > Open File` for an image) to choose how to start. Those two source buttons sit together on the left of the workflow row with accent styling.

- **Image** opens the usual file picker for a FITS, XISF, TIFF, PNG, JPEG, or camera RAW source image. If an image is already loaded, you can **Replace** it or add a new file as a **Comparison**.
- **Survey** asks you to pick a public sky survey, then downloads that field as the primary image using the initial RA, Dec, FOV, and pixel size from **Settings → Sky Explorer** (default center: Trifid Nebula / M20). The center tile loads as a low-res preview, then its detail refine starts before surrounding tiles; neighbors follow with the same preview→detail pattern. Each tile keeps its own stretch, so brightness does not jump when neighbors arrive. Loaded tiles stay available when you pan away and return; empty survey coverage shows a hatched “No survey coverage” tile instead of a black square. Panning stays responsive because network and stretch work run off the UI thread. **Explore** catalogs the full visible mosaic (not only the center cell), and markers are not clipped at tile edges. If a survey is already loaded, you can **Replace** it or add another survey as a **Comparison**; the wipe bar spans the full view, and a comparison survey loads with the same center-first tiled path as the primary field. If an image is already loaded, the first **Survey** click overlays that survey as the comparison.

Supported upload formats:

- `.fits` / `.fit`
- `.xisf`
- `.tif` / `.tiff`
- `.png`
- `.jpg` / `.jpeg`
- camera RAW (for example `.dng`, `.cr2` / `.cr3`, `.nef`, `.arw`, `.orf`, `.rw2`, `.raf`, `.pef`)

The image preview loads into the panel. **Explore** stays on the workflow row and becomes active once a source is loaded. After a successful Explore, selecting additional object types changes that button to **Update** so only the newly enabled layers are queried.

**Shift+left-drag** on the image draws a rectangular ROI. While an ROI is present, **Explore** queries only that region. Right-click inside the ROI and choose **Remove ROI** to search the full field again. Loading a new image or survey clears the ROI.

Sky Explorer works on **one image at a time** (not a time-series folder). Prefer a frame with a valid celestial WCS already written into the headers. If WCS is missing, CAst recovers a plate solution from local Gaia matching and, when configured, astrometry.net, keeping whichever finishes first.

### Step 2: Choose Object Types

The object-type table lists classes CAst can search for. Columns include stroke/fill/text styling, group, object type, and a short description.

Cycle the mode button through:

| Mode | What you get |
|------|----------------|
| **Simple** | Six common deep-sky classes: Emission Nebula, Reflection Nebula, Dark Nebula, Galaxy, Open Cluster, Globular Cluster |
| **Advanced** | Broader human-readable classes (planetary nebulae, SNRs, AGN, stars, variables, asteroids, and more) |
| **Scientific** | Exact SIMBAD-style object-type codes plus extras such as Gaia Star, VSX Variable, Exoplanet Host, Asteroid/Comet |
| **Catalog** | Named catalogues only: NGC, IC, LDN, VdB, LBN, Messier, Barnard, Sharpless, and HASH PN. Results and overlays use catalog designations (for example `NGC 7000`), not common names. HASH PN is the exception: it uses the usual name from HASH, not the HASH number |

Help text under Simple mode: *Simple mode keeps the six common deep-sky classes visible.* Catalog mode: *Catalog mode shows only objects from the selected catalogs, labeled with catalog names.*

Use **Select All** / **Unselect All** to toggle rows quickly. Open the **Filter** menu and optionally enable **Hide Objects Without Magnitude** to drop catalog hits that lack a usable magnitude.

Click a stroke/fill/text cell to customize how that type is drawn. Those three colors are independent of each other; use **Settings → Sky Explorer → Visuals** to retint a whole group or flip the generated stroke/fill/text relation. Per-type colors and fonts are remembered with your layout. Default nebula colors are amber/gold so labels stay visible against H-alpha red.

### Step 3: Explore

Click **Explore**. The button shows **Terminate** while CAst is querying catalogs, so you can abort a long search. When the query finishes or is terminated, the button returns to **Explore** (or **Update** if extra types were selected after a previous run). While it runs, CAst:

1. Validates (or solves) WCS and computes the field footprint (or the drawn ROI, if one is present)
2. Queries each enabled catalog layer for objects inside that footprint
3. Filters and sorts results
4. Builds automatic overlays and fills the results table

Progress and summary notes stream into the **Work Log** tab. When finished, review the **Results** table and the annotated image.

You need at least one selected object type whose catalog layer is enabled under **Settings → Sky Explorer → Catalog Sources**.

---

## Input Requirements

| Requirement | Why |
|-------------|-----|
| Celestial WCS (or recoverable plate solution) | Catalog positions must project onto image pixels |
| Network access | SIMBAD, VizieR, NASA Exoplanet Archive, Horizons, hips2fits |
| Observation time in header (`DATE-OBS`) | Required for solar-system predictions |
| Observing site in Settings | Improves Horizons geometry for SSO search |
| Catalog Sources enabled | Layers you want must be checked in Sky Explorer settings |

Without WCS, and if both local Gaia matching and astrometry.net fail (or no key is set and local matching cannot recover a solution), Explore cannot place catalogs on the image.

---

## The Identification Pipeline

Sky Explorer’s core call is `explore_sky_image` in the application core. Conceptually:

### 1. Resolve WCS and footprint

CAst validates embedded celestial WCS keywords. If they are missing or unusable, it recovers a plate solution from metadata-seeded Gaia matching and, when an astrometry.net key is set, from nova at the same time, keeping the first success (cached under the Sky Explorer WCS cache). From the solved frame it derives center, search radius, and corner sky coordinates for catalog queries. A drawn ROI shrinks that footprint to the selected rectangle.

### 2. Query catalog layers

Enabled layers run in a fixed order, and within each layer Explore now queries **only the object types you have selected**:

1. **Deep sky** (SIMBAD + matching VizieR supplements such as HyperLEDA, Sharpless, Barnard, van den Bergh, HASH PN via V/163, NGC 2000)
2. **General SIMBAD objects**
3. **Solar system** (JPL Horizons / known SSO search, magnitude ≤ 18)
4. **Variable stars** (VSX)
5. **Gaia DR3 stars**
6. **Exoplanet hosts** (NASA Exoplanet Archive)

If you select additional object types after a successful Explore, the primary button becomes **Update**. Update fetches only the newly selected types and merges them into the existing results; already-loaded types are not re-queried. Unchecking a type still only hides it.

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
| Ctrl/Shift-click rows | Multi-select for **Tools → Create collage** (the current row still drives Inspector, centering, and overlay highlighting) |
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

The **Display** menu controls how your source image (including survey-field tiles) is stretched for viewing:

- **None** (no display stretch; used automatically for already-stretched uploads such as JPEG/8-bit TIFF/PNG finals)
- **STF** (default for linear science frames; darker midtones so bright nebulosity keeps structure)
- **STF Bright** (stronger lift for faint fields)
- **Asinh**
- **Sqrt**
- **Log**

Also available: **Curves**, **Invert**, **Feather** (0–100% soft-blend using overlapping sky on tile edges; default 55%), and **Reset**. Feather fetches each tile with extra sky around its true center and blends only that shared coverage, so stars stay aligned; it does not stretch tile edges. Curves refine the already-stretched preview (including survey tiles) for viewing only; catalog positions come from WCS, not from the display transform.

### Comparison

**Image** and **Survey** also add comparison layers after a source is already loaded:

- A later **Image** click can **Replace** the current image or upload a new file as a full-frame comparison overlay. If the current primary image was opened from a survey field, the first image upload becomes the new primary image and the original survey is reloaded as the comparison layer.
- A later **Survey** click can **Replace** the current survey or add another survey as a comparison. Survey comparison loads a HiPS cutout aligned to your source-image WCS and lazy-loads tiles as you pan (no fixed field lock). On a survey primary, comparison uses the same tiled center→refine→neighbors loader as the primary field, and the wipe bar divides the full view left-to-right across the mosaic (not only the central cell).

Available surveys include DSS2 (Blue/Red/IR), PanSTARRS, SDSS, 2MASS, WISE, GALEX, SHS Hα, IPHAS Hα, and NSNS DR0.2 (Hα, [OIII], [SII]).

When a comparison is active, an interactive divider lets you wipe between the primary image and the comparison layer. The **Display** menu now contains both **Image** and **Comparison** sections so each side can be stretched independently. Survey downloads are cached only for the current session (to keep pans and refinements fast) and are deleted when CAst closes.

### Estimate magnitude limit

Use **Tools → Estimate magnitude limit...** to open the magnitude-limit dialog. Two modes are available:

- **Mark magnitude ladder** — query Gaia up to a chosen max magnitude and annotate representative stars in configurable magnitude bins (bin size, stars per bin, and optional “mark only the faintest” bin).
- **Find actual magnitude limit** — probe Gaia stars at each magnitude step with an SNR detection test and mark confirmed detections at the estimated limit (all visible stars, or only the faintest).

Marker colors, stroke, text, and sizes remain configurable in Settings. **Tools → Clear magnitude limit markers** removes active Mag Limit annotations.

While Mag Limit markers are active, Explore includes Gaia even if the Gaia Star type row is unchecked, so the ladder has data to draw.

### Auto / Manual

**Auto** / **Manual** is a mode switch (fixed-width, same width as **Export**). Click **Auto** to enter **Manual**. Catalog overlays already on the image stay visible.

- **Auto** — catalog annotations from Explore
- **Manual** — keep those overlays, draw new marks, and delete unwanted automatic annotations

In Manual mode, right-click a selected catalog object on the image or in Source Results and choose **Delete**.

### Manual annotations

Toolbar tools (icon buttons):

- **Mouse** — select / manipulate
- **Circle**
- **Ellipse**
- **Ruler** — drag between two points; shows pixel distance, or angular distance (arcsec / arcmin / degrees) when WCS is available, with units chosen automatically by magnitude
- **Text**

The **Properties** strip edits **Stroke**, **Fill**, **Text**, **Weight**, and **Opacity** (opacity can be adjusted by dragging, and is available for text as well as shapes). After you draw a mark, those edits update that last mark until you switch tools. Clicking a mark again (for example switching back to Circle and selecting the circle) makes further edits apply to that selected mark. Text styles include Regular, Bold, Italic, and Bold Italic.

Right-click the image for:

| Item | Role |
|------|------|
| **Search** | Open a SIMBAD coordinate search in the browser at the click position (uses **Search Radius**) |
| **Detect** | Run a SIMBAD cone search at the click and merge detections into results/overlays |
| **Edit Annotation** / **Delete Annotation** | Modify or remove a hand-drawn mark |
| **Delete** | In Manual mode, remove the selected automatic catalog annotation (image or Source Results) |
| **Clear Manual Annotations** | Remove hand-drawn marks |
| **Clear Detections** | Remove Detect hits |

The **Manual Annotation** dialog edits geometry (X, Y, radii, rotation), line width, font, opacity, colors, and whether text/fill are shown. Manual annotations are stored **per image** for the session.

---

## Exports

### Image...

**Export → Image...** saves the current view (source stretch, overlays, and survey comparison if visible) as PNG, JPEG, or BMP. Default name pattern: `{stem}_sky_explorer.png`.

### Create collage

**Tools → Create collage** builds a figure from catalog objects that have usable angular-size metadata (`catalog_major_axis_arcmin`, `catalog_minor_axis_arcmin`, or `catalog_size_arcmin`). Objects without size data are skipped and counted in the dialog summary and Work Log.

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

Open **Settings → Sky Explorer**. Settings are split into **General** and **Visuals** tabs.

Intro text in the dialog: *Sky Explorer settings are split into General (field/query defaults and catalog sources) and Visuals (marker colors, opacities, and Mag Limit styling).*

### General

| Setting | What it does |
|---------|----------------|
| **Survey Field RA / Dec** | Initial center when Survey loads a field with no user image yet (default: Trifid Nebula / M20) |
| **Survey Field FOV** | Field of view for the survey-as-primary WCS canvas cell (default 45′). About 10× this area is available to pan while the survey overlay loads asynchronously. |
| **Survey Field Width / Height** | Pixel size used for the survey-as-primary WCS canvas grid (default 1024×1024) |
| **Search Radius** | Cone radius (arcseconds) for right-click **Search** / **Detect** (default 10″) |
| **Gaia Max Mag** | Magnitude ceiling for Gaia / VSX / exoplanet filtering of non–deep-sky hits (default 17) |
| **Gaia Hard Cap** / **Gaia Cap Rows** | Optional hard limit on Gaia rows retrieved |
| **Mag Limit Examples** | Default stars to label per magnitude bin (also set in the Mag Limit dialog) |
| **Galaxy Mag Limit** / **Galaxy Max Mag** | Optionally hide faint or unknown-magnitude galaxies on the overlay |
| **Galaxy Shape Only** | Only draw galaxies that have ellipse metadata |
| **Catalog Sources** | Enable/disable: SIMBAD Deep Sky, SIMBAD General Objects, Solar System Objects, VSX Variable Stars, Gaia DR3 Stars, NASA Exoplanet Hosts |

### Visuals

| Setting | What it does |
|---------|----------------|
| Mag Limit marker / text colors and sizes | Appearance of Mag Limit annotations |
| **Extended Nebula Scale** | Enlarge nebula overlays for visibility |
| **Scale Stroke Width** | Thicken outlines on large objects |
| **Marker Color Relation** | Generated fill bright / stroke dark (or the inverse). Does not rewrite a type you already customized. |
| **Text Color Relation** | Dark or bright generated labels. Per-type text edits stay independent of stroke/fill. |
| **Default Label Font** / **Style** / **Size** | Default family, Regular/Bold/Italic/Bold Italic, and point size for every object-type category. Per-type Text edits in the type table still override this default. |
| **Fill Opacity** / **Stroke Opacity** | Automatic marker transparency |
| **Stroke Outline** / **Stroke Outline Width** | Dark halo around marker strokes so colored outlines stay visible. Set width to 0 to turn it off |
| **Object Group Colors** | Base hues per result group. Nebulae / ISM defaults to amber/gold so markers contrast with H-alpha. |

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
| HyperLEDA / Sharpless / Barnard / VdB / HASH PN (V/163) / NGC 2000 (VizieR) | Galaxy, nebula, and planetary-nebula supplements |
| JPL Horizons | Known asteroids and comets in the field |
| hips2fits (CDS) | Survey cutouts for comparison |
| astrometry.net | Optional plate-solve fallback |

HASH planetary nebulae are queried live from CDS/VizieR catalogue **V/163** (`pnmain`) whenever the Deep Sky layer is enabled. CAst does not redistribute the HASH database. Inspector metadata includes HASH status (true / likely / possible / candidate / mimic), morphology, sizes, and spectrum-availability flags from that catalogue. If you use these results in a publication, cite Parker, Bojičić & Frew 2016, acknowledge the HASH PN database, and acknowledge VizieR/CDS.

Results and plate-solves are cached under Sky Explorer catalog / WCS directories so repeat Explore work on the same field is cheaper. Survey image downloads are session-only and are deleted when CAst closes.

---

## Suggested Workflow

1. Open a plate-solved deep image of a rich field (cluster, nebula complex, or wide Milky Way patch).
2. Start in **Simple** mode with the six deep-sky types selected; click **Explore**.
3. Collapse groups you do not need; click interesting rows and use **Center Object**.
4. Switch to **Advanced** or **Scientific** and re-Explore if you need stars, variables, AGN, or SSO.
5. Use **Tools → Estimate magnitude limit...** to judge depth; switch to **Manual** and delete individual catalog marks if overlays clutter a presentation frame.
6. Click **Survey** to load **DSS2 Blue**, NSNS DR0.2 Hα, or another public survey, or **Image** for a second uploaded frame, then scrub the divider for a before/after comparison.
7. Add a few manual labels for teaching slides; **Export → Image...** or **Animation...**. Use **Tools → Create collage** when you want a multi-object size-aware figure of galaxies/nebulae from the same frame.

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
- Absence of a HASH / VizieR V/163 match is not by itself a discovery of a new planetary nebula.
- It does not embed the interactive HASH website (image cutouts hosted only there, live notes, or SQL sample tools); Sky Explorer uses the VizieR V/163 catalogue tables.
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

Sky Explorer is the “what am I looking at?” mode. Point it at a solved image, pick the object classes that matter for your night, and let the catalogs paint the field. Use Tools → Estimate magnitude limit and survey comparison when you are teaching or planning, export the annotated view when you want a figure that still looks like *your* data, and use Tools → Create collage when you want a tidy multi-object cutout board from catalog sizes.

For shared shell themes and layout conventions, see [Themes, Layout, and Shared UI](themes_layout_ui.md). For the repository map, see [CODEBASE_MAP.md](../CODEBASE_MAP.md).
