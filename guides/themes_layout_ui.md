# Themes, Layout, and Shared UI

Citizen Astronomy (CAst) is organized around a mode launcher and a shared application shell. Mode-specific science belongs in the [mode guides](differential_photometry.md); this guide covers the parts that stay the same wherever you work: how folders are opened, how the shell is laid out, themes, shared result panels, and common UI troubleshooting.

---

## Application Shell

### Mode launcher

On startup (or when you return to the home screen), CAst shows a **mode launcher** with two groups:

- **Science Workflows** — Differential Photometry, Asteroid / Comet Detection, Transient Finder
- **Visualizations & Tools** — Sky Atlas, Sky Explorer, HR Diagram, Distance Map, AstroStack

Each card opens that mode’s workspace. **Observation Deck** is available from the mode menu but is not shown as a launcher card.

### Main window layout

Once a mode is open, the typical shell includes:

| Area | Role |
|------|------|
| Menu bar | File, mode switching, Settings, Help / About, Check for Updates |
| Mode toolbar / strip | Mode-specific actions (for example Generate, Align, Discover, Search) |
| Image / map panel | Annotated frames, sky view, or diagram canvas |
| Results tables | File, source, measurement, or candidate lists |
| Detail / inspector pane | Context for the current selection |
| Work log | Status, warnings, and progress notes |

Exact panels differ by mode. Differential Photometry, for example, uses a compact **Workspace** strip instead of an always-visible browser pane.

### Switching modes

Use the mode menu (including **Observation Deck**) or return to the launcher and pick another card. The active mode is remembered in settings for the next session.

---

## Folder Layout and Opening Data

### Recommended workspace layout

```text
Photometry/
  Files/
    ObjectA/
      image_001.fits
      image_002.fits
    ObjectB/
      image_001.xisf
```

### What you can open

From **File > Open Folder** you can open:

- the **project / workspace root**
- the **`Files`** folder
- a **single object folder**

After opening, CAst scans supported images automatically. There is no separate Scan button in the current differential workflow.

Supported science formats are primarily `.fit` / `.fits` and `.xisf`. Plate solutions can already be present in the headers, or solved later through astrometry.net when configured.

### Loaded Results

After a multi-object scan, a **Loaded Results** dialog summarizes each discovered object:

- total files
- solved vs needs-solve counts
- invalid counts
- a short note preview for the selected object

Choose which object becomes active in the Workspace strip (or equivalent mode controls), then continue with that mode’s pipeline.

---

## Themes

### Built-in themes

CAst ships dark themes plus a default **Normal** look and a **Customize** editor:

- Normal
- Dark
- Dracula
- Nord
- Tokyo Night
- Gruvbox
- Catppuccin
- Solarized Dark
- One Dark
- Customize

Open theme controls from Settings / theme UI in the application (see **Settings > Open Settings** and the theme picker in the UI).

### Custom themes

**Customize** lets you edit colors for:

- window background
- panel background
- text
- menu background and text
- accent color
- plot background
- plot axis text
- plot data points
- plot fit curve

You can **import** and **export** theme JSON files to share or back up a custom look.

### Persistence

Theme choice is stored at two levels:

1. **Workspace theme** in `.photometry-settings.json` when a workspace explicitly sets one
2. **Last-used app theme** fallback for fresh workspaces that do not store a theme

If a workspace stores a theme, that setting wins over the last-used fallback.

---

## Shared Settings (UI-facing)

Use **Settings > Open Settings** for options that affect many modes. Common entries include:

| Setting area | Examples |
|--------------|----------|
| Astrometry | API key (or env `CITIZEN_PHOTOMETRY_ASTROMETRY_API_KEY`) |
| Cache | Cache directory for catalogs and intermediate products |
| Photometry | Nearby comparison-star count; fixed vs adaptive apertures; radii / FWHM scales |
| Preview caps | Max candidates by count or faint-limit magnitude |
| Variable filters | Limit mode/value; designation families (Named, Gaia, ASAS-SN, ATLAS, ZTF, Other) |
| Alignment assumption | Treat a stack as already aligned so one solve can serve many frames |
| Observatory | Telescope, camera, focal length, pixel size, site, Bortle class (written into science exports) |
| Parallelism | Worker counts for photometry, period search, discovery, and related jobs |

Mode-specific knobs (Discover thresholds, HR source caps, Sky Atlas display, and so on) live in the same Settings dialog or in mode panels — see the mode guides for those.

### Alignment and mode options (differential)

Under Settings for differential work:

- **Alignment** — `Non-Aligned` vs `Aligned` (solve one representative frame and reuse geometry for an aligned stack)
- **Mode** — `Auto` (catalog-driven targets) vs `Manual` (saved manual source definitions)

---

## Shared Result Panels

These patterns appear most clearly in Differential Photometry but carry over conceptually to other science modes.

### File Results

Frame-level counts, flags, and notes. Useful for spotting bad frames before trusting a light curve or discovery run.

### Source / candidate tables

Per-source summaries: role, magnitude, measurement count, filters, flags, SNR, periods, or mode-specific scores. Toolbar actions (period tools, SNR binning, labeling) act on the current selection.

### Measurements

One row per source per frame when the mode exposes a Measurements table. In Differential Photometry the table is loaded **on demand** with **Measure**, so large filtered sets do not auto-fill after every refresh. Columns typically include time, file, source, filter, role, flux, differential magnitude, error, flags, and catalog identifiers.

### Annotated image panel

Quality-control view for apertures, comparison stars, movers, or transient markers. Confirm that overlays sit on the intended objects before trusting exports.

Typical overlays (mode-dependent):

- target / candidate markers
- comparison or reference stars
- aperture and annulus rings
- motion or variability annotations

### Light-curve and plot panels

Where a mode plots time series or diagrams, expect:

- source / series selection
- previous / next navigation (and often keyboard left / right)
- calendar time or Julian Date axes where relevant
- optional saved filters with an enable/disable toggle
- optional fit overlays (polynomial degree, periodic harmonics, period helpers)
- themed plotting colors from the active theme

### Work log

Progress, warnings, and fit notes. Actions such as **Fit Period** may write before/after entries showing the value that was replaced and the new result.

### Differential Workspace strip

In Differential Photometry the upper-left **Workspace** strip replaces the old always-visible browser and exposes:

- **Generate** (always runs against the active object shown inline)
- catalog shortcuts: **CDS**, **Simbad**, **Gaia**, **VSX**
- the active-object label beside Generate

### Preview dialog (differential)

Before full automatic processing, the preview dialog shows how many variables were found, how many remain after designation filters and caps, and how many will be processed by default. You can adjust percentage/count selection, designation checkboxes, and row selections. **Process Default** uses the theme accent color to highlight the recommended path.

### Source Results actions (differential)

| Action | Role |
|--------|------|
| Pull Period | Literature period lookup |
| Calculate Period | Local period inference |
| Find Better Fit | Comparison-star optimization against literature period behavior |
| Increase SNR | Period-aware binning of selected source rows |
| Reset SNR | Appears after Increase SNR in the **current session**; restores cached original measurements |

**Reset SNR** availability is session-local and is not persisted across app restarts.

---

## Mode-Specific UI Pointers

Use the dedicated guides for click-path and science detail:

| Mode | Guide |
|------|-------|
| Differential Photometry | [differential_photometry.md](differential_photometry.md) |
| HR Diagram | [hr_diagram.md](hr_diagram.md) |
| Asteroid / Comet Detection | [asteroid_comet_detection.md](asteroid_comet_detection.md) |
| Transient Finder | [transient_finder.md](transient_finder.md) |
| Sky Explorer | [sky_explorer.md](sky_explorer.md) |
| AstroStack | [astrostack.md](astrostack.md) |
| Sky Atlas | [sky_atlas.md](sky_atlas.md) |
| Distance Map | [distance_map.md](distance_map.md) |
| Sky Explorer | [sky_explorer.md](sky_explorer.md) |

Other modes (Sky Atlas, Sky Explorer, Distance Map, AstroStack, Observation Deck) follow the same shell patterns: open or load data, use the mode toolbar, inspect overlays/tables, and export from the File menu when available.

---

## Screenshot Refresh Notes (maintainers)

If documentation screenshots need refreshing, prioritize views that replaced the old Browser + Start workflow:

1. **Workspace strip** — Generate, active-object label, catalog shortcuts
2. **Loaded Results** — object counts, solve-state columns, note preview
3. **Source Results action row** — Pull Period, Calculate Period, Find Better Fit, Increase SNR / Reset SNR
4. **Preview dialog** — candidate summary, filters, selection table
5. **Annotated image** — apertures, annuli, comparison overlays
6. **Light-curve panel** — themed plot, error bars, optional fit

Helpers:

- `scripts/generate_doc_screenshots.py` — general UI captures
- `scripts/generate_synthetic_tracking_comparison_plots.py` — synthetic-tracking comparison figures under `docs/`

---

## Troubleshooting (UI and layout)

### Nothing happens after opening a folder

- Confirm the folder contains supported image files
- If you opened a workspace root, check that objects live under `Files/`
- Read the work log for scan warnings

### Preview takes a long time to open

Large fields can contain many cataloged variables. Reduce the list with preview max-count, preview max-magnitude, and designation-family filters in Settings.

### Light curves look too noisy

Before changing fit settings, inspect the annotated image for aperture size, contaminated comparisons, blending, weak filters, or variable transparency/seeing.

### The fitted curve looks wrong

Try a lower polynomial degree, fewer periodic harmonics, a more plausible period guess, or a JD x-axis for timing inspection.

### A target appears but should not be measured

It may be retained as an explicit selection, a saved manual target, or still allowed by the current designation-family filters.

### My theme does not persist

Check whether the workspace `.photometry-settings.json` stores an explicit theme (that wins). Otherwise the app falls back to the last theme used when the program closed.

---

## Related reading

- [README.md](../README.md) — product overview, install, outputs
- [CODEBASE_MAP.md](../CODEBASE_MAP.md) — repository and module map for developers
- Mode guides listed above for science workflows
