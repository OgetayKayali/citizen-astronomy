# Sky Atlas

## Introduction

Most sky maps show you the heavens as a catalog. **Sky Atlas** tries to make them feel inhabited.

Sky Atlas is CAst's immersive all-sky exploration mode. You can search bright stars, Messier targets, constellations, and the live Moon; change time and observer location; tune star density and Milky Way rendering; and layer your own sky-registered imagery on top of the atlas. It is the mode you use when you want context: where an object sits, what surrounds it, and how the sky changes with time and location.

Some internal controls still use the older phrase **Sky View**. In the launcher, mode menu, and user-facing documentation, this mode should be understood as **Sky Atlas**.

### What you can do with this mode

- **Explore the sky interactively.** Pan, zoom, search, and click visible targets to inspect their RA, Dec, altitude, azimuth, and constellation context.
- **Simulate time and location.** Change UTC time, pause or resume time flow, and switch observing sites to see how the sky changes from different places on Earth.
- **Control star and deep-sky density.** Tune limiting magnitude and catalog visibility so the atlas stays readable or becomes much deeper.
- **Render the Milky Way and constellations.** Adjust bloom, spikes, constellation styling, and Milky Way detail/tone.
- **Work with the Moon as a live object.** Search for the Moon and inspect its topocentric position from your chosen site and time.
- **Create your own surveys from your own data.** Import sky-registered PNG, XISF, TIFF, or FITS images into named custom surveys and display them as overlays on the atlas.

### Why this matters

A star chart is most useful when it becomes personal. The question is not only “where is M31?” but “what does the sky look like from my site, on my night, around the target I care about?” Sky Atlas combines packaged catalogs, downloadable all-sky star data, and your own overlay imagery so you can move between outreach, planning, and visual inspection without leaving the app.

---

## How It Works

### Step 1: Open Sky Atlas

Open **Sky Atlas** from the mode launcher or the mode menu.

On first use, the mode starts with packaged named targets and then loads the brighter full-sky Hipparcos catalog for broader coverage. The initial status message explains:

`Sky View starts with packaged named targets and loads the brighter full-sky Hipparcos catalog the first time you open it.`

### Step 2: Search for a Target

Use **Search sky catalog** to look for:

- bright stars
- Messier targets
- constellations and aliases
- the live **Moon**

The search field placeholder is:

`Search bright stars, Messier targets, constellations, Moon, and aliases`

Choosing a result centers the atlas on that object and pins its details in the left-hand object panel.

### Step 3: Set Time

Open **Date and time** to control the simulation clock.

The popup uses **UTC** and offers:

- **-1 d**
- **-1 h**
- **Now**
- **+1 h**
- **+1 d**

You can also pause and resume time with **Pause time** / **Play time**. This makes it easy to inspect how an object rises, sets, or shifts relative to the horizon and constellations.

### Step 4: Set Observer Location

Open **Observer location** to choose where the atlas should observe from.

You can:

- quick-switch among saved sites
- open **Manage Locations**
- use the full **Observer Location** dialog
- click a world map or type **Latitude**, **Longitude**, and **Elevation**
- save the site with **Save Location**

Observer location matters because it changes:

- altitude and azimuth
- horizon placement
- ground context
- the live Moon position

### Step 5: Turn Layers On and Off

Use the main toggles to control:

- **Toggle equatorial grid**
- **Toggle alt-az grid**
- **Toggle constellations**
- **Toggle ground and horizon**
- **Custom sky map overlays**

Then open **Sky Atlas settings** and **Sky View options** for deeper control over catalogs and rendering.

---

## Searching and Inspecting Objects

### Pinned object panel

When nothing is selected, the panel says:

- `No object selected`
- `Click a visible object to pin its details here`

After you click a target, the panel can show:

- **RA**
- **Dec**
- **Alt**
- **Az**
- **Constellation**
- object name / aliases
- magnitude and related metadata where available

The footer interaction hint reads:

`FOV ... | Drag to pan | Wheel to zoom | Click star/object for details`

### What can be searched

Sky Atlas emphasizes recognizable and useful objects rather than surfacing every star by default. Search is designed for:

- bright named stars
- Messier objects
- packaged atlas targets
- constellation names / aliases
- the Moon

Deeper scientific stars can still be rendered, but the search UX is intentionally biased toward named or familiar targets.

---

## Built-In Catalogs and Data

### Stars

Sky Atlas uses a tiered full-sky star strategy. Depending on limiting magnitude, it can rely on:

- **Hipparcos**
- **Tycho-2**
- **Gaia DR3**

The settings dialog estimates star counts and download sizes for the selected depth. Typical examples from the built-in estimator are roughly:

- mag `9.5`: Hipparcos, about `99k` stars
- mag `12.5`: Tycho-2, about `1.8M` stars
- mag `15.0`: Gaia DR3, about `35M` stars

### Deep-sky catalogs

The built-in deep-sky object families include:

- **Messier**
- **NGC**
- **IC**
- **VdB**
- **LDN**
- **LBN**

These can be enabled or disabled independently in **Sky Atlas Settings**.

### The Moon

The Moon is not just a static catalog row. Search terms such as **Moon**, **Luna**, and **Earth's Moon** resolve to a live topocentric Moon object derived from the current observer location and simulation time.

That makes Sky Atlas useful for planning lunar context, not only deep-sky navigation.

---

## Settings

Open **Sky Atlas Settings**.

The dialog intro says:

`Adjust what stars and deep-sky objects appear in Sky Atlas.`

### Stars page

Controls include:

- **Limiting magnitude**
- **Star catalog download**
- **Star brightness**
- **Star size**
- **Magnitude size contrast**
- **Twinkling**

The dialog also warns:

`Click OK to download any missing catalog data for the selected magnitude.`

### Deep-Sky Objects page

Controls include:

- **Catalogs to show**
- **Visibility**
- **Object limiting magnitude**
- **Label limiting magnitude**

This lets you choose whether the atlas should stay clean and outreach-friendly or become denser and more scientific.

---

## Rendering and Visual Controls

Open **Sky View options** for the immersive renderer controls.

### Star rendering

Controls include:

- **Max mag**
- **Bloom**
- **Spikes**

Spike modes:

- **Off**
- **Subtle**
- **Classic**

### Milky Way

Milky Way detail modes include:

- **Off**
- **Low**
- **Medium**
- **High**

and more advanced named modes such as:

- **Basic Milky Way**
- **Advanced Milky Way (16k)**
- **High Detail Milky Way (32k)**
- **Fully Milky Way**

Tone presets include:

- **Atlas**
- **Dark sky**
- **Source-like**
- **Custom**

Additional controls:

- **Opacity**
- **Exposure**
- **Preset**
- **Contrast**
- **Saturation**
- **Black point**
- **Gamma**
- **Flip texture**

### Constellations

Constellation controls include:

- **Constellations**
- **Lines**
- **Labels**
- **Color**
- **Line width**
- **Line opacity**
- **Label opacity**
- **Label size**

Color themes include:

- **Blue-white**
- **Gray-white**
- **Soft cyan**
- **Warm amber**

Use **Reset View** if you want to return to a clean baseline camera setup.

---

## Custom Surveys and Overlays

This is one of Sky Atlas's most important advanced features.

### What custom surveys are

Open **Custom sky map overlays** to work with the **Custom Sky Map** system.

This lets you create your own named surveys from **your own sky data**, then display those overlays directly on the atlas.

You can think of this as building a personal sky layer: your calibrated or solved image becomes another survey option beside the built-in atlas background.

### Supported input formats

Custom overlays support:

- **PNG**
- **XISF**
- **TIFF**
- **FITS**

Unsupported types are rejected with:

`Sky Atlas custom overlays support PNG, XISF, TIFF, and FITS images only.`

### How to create your own survey

1. Open **Custom sky map overlays**
2. Click **Edit Surveys**
3. In **Custom Sky Map Surveys**, click **New Survey**
4. Enter a **Name** and optional **Filter**
5. Click **Add Image...**
6. Import one or more sky-registered images

Sky Atlas tries to use embedded WCS when present. For plain image formats such as PNG or TIFF, it can look for companion WCS/FITS-style information first and otherwise try to solve the field as needed.

### How to use a created survey

Once imported:

1. Return to **Custom Sky Map**
2. Enable **Show custom sky overlays**
3. Pick the survey from the **Survey** dropdown
4. Tune:
   - **Opacity**
   - **Brightness**
   - **Saturation**
   - **Feather**

### How the data is stored

Imported overlays are cached into a survey-specific folder with atlas-ready assets such as:

- a cached `overlay.png`
- a cached `overlay.wcs.fits`
- an `overlay.json` manifest

Survey folders are meant to be portable. The app's own dialog notes that surveys can be shared by copying their survey subfolder.

### Why this matters

This means Sky Atlas is not limited to built-in catalogs or public survey backdrops. You can create your own survey from your own processed data and browse it in the same navigable all-sky environment.

---

## Observer Location and Time Workflow

Sky Atlas becomes much more useful when you treat time and place as first-class controls instead of leaving them at defaults.

### Location workflow

- choose or create a site in **Observer Location**
- save it as a reusable preset
- switch among sites from the popup

### Time workflow

- jump to **Now**
- nudge by hours or days
- pause and inspect a sky moment
- resume playback for a live moving sky

This is especially useful for:

- horizon checks
- constellation orientation
- Moon context
- public outreach and teaching

---

## Suggested Workflow

1. Open **Sky Atlas**.
2. Search for a named star, Messier target, or the Moon.
3. Set your observing site in **Observer location**.
4. Adjust **Date and time** until the sky matches the night you care about.
5. Turn on constellation lines, grids, or horizon overlays as needed.
6. Open **Sky Atlas Settings** and choose a limiting magnitude and object catalog depth that matches your goal.
7. If you have your own registered image data, add it through **Custom Sky Map Surveys** and enable it as an overlay.
8. Fine-tune Milky Way tone and star rendering in **Sky View options**.

---

## How This Differs from Other Modes

| Mode | Sky Atlas relationship |
|------|------------------------|
| **Sky Explorer** | Anchored to one image and its WCS; Sky Atlas is an all-sky interactive environment |
| **Distance Map** | Builds a Gaia-based 3D field visualization; Sky Atlas is a navigable 2D sky context view |
| **HR Diagram** | Builds a stellar population plot from one image; Sky Atlas helps you understand where objects sit on the sky |
| **Differential Photometry** | Measures variability in time-series data; Sky Atlas is for exploration, context, and planning |

---

## Limitations

### What Sky Atlas does not do

- It is not a full planetarium replacement with every possible catalog exposed in the UI.
- It is not an image-anchored annotation mode like Sky Explorer.
- It does not promise a dedicated export workflow in the same way some other modes do.

### Practical caveats

- Deep star catalogs can be large, especially when Gaia DR3 depth is enabled.
- Some data is downloaded on first use.
- Search returns a limited number of combined results and favors recognizable targets.
- Custom overlays need valid sky registration through WCS or solving.
- Imported overlays may be downscaled into cache for practical display.
- Some internal controls still say **Sky View** even though the product mode is **Sky Atlas**.

---

## Conclusion

Sky Atlas is CAst's exploration and context mode. It helps you move from “what object do I want?” to “what does the sky around it look like, from my site, at my time, with my own data layered into the map?” If you want a navigable, customizable sky environment rather than a single-image analysis tool, this is the right place to start.

For shared shell conventions and themes, see [Themes, Layout, and Shared UI](themes_layout_ui.md).
