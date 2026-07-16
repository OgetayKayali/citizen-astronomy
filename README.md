# Citizen Astronomy (CAst)

Every clear night, amateur telescopes around the world capture photons that professional observatories never will -- the right patch of sky, at the right moment, with enough patience to notice something change. Citizen Astronomy turns those images into science.

**CAst** is a Windows desktop application that takes folders of FITS and XISF images and gives you the tools to measure variable stars, discover moving asteroids, build Hertzsprung-Russell diagrams, and explore the sky -- all from one guided interface, no command-line scripting required.

## What you can do

### Differential Photometry

Open a folder of time-series images, and CAst scans your frames, identifies field stars through Gaia DR3 and VSX catalogs, performs aperture photometry with adaptive FWHM-scaled apertures, and produces differential light curves. Fit periods with Lomb-Scargle, refine comparison stars, bin for signal-to-noise, and export science-ready AAVSO reports.

A more detailed guide can be found at [Differential Photometry Guide.](/differential_photometry.md)

### HR Diagram

Load a single deep image of a star cluster and plot a color-magnitude diagram using Gaia BP-RP photometry and absolute magnitudes. Identify co-moving stellar groups with proper-motion clustering, overlay educational class and age guides, and export publication-quality diagrams with temperature and luminosity scales.

A more detailed guide can be found at [HR Diagram Guide](HR_DIAGRAM.md).

### Asteroid and Comet Detection

Point CAst at a sequence of solved frames and it queries known solar system objects predicted in your field -- including faint comets and interstellar visitors. Blink through your frames, track objects across exposures, align subframes onto a common WCS grid, and run the **Discover** pipeline to find moving objects that aren't in the catalogs yet. Confirm candidates with synthetic tracking that shifts and stacks on predicted motion. Inspect paths with **Plots**, and visualize orbits in a heliocentric **Trajectory View** built from JPL Horizons data.

A more detailed guide can be found at [Asteroid and Comet Detection Guide](asteroid_comet_detection.md).

### Sky Explorer

Open any solved image and instantly identify what's in the field: deep-sky objects, variable stars, Gaia catalog stars, exoplanet hosts, and solar system bodies, all overlaid on your image with a searchable results table.

### Transient Finder

Search a folder of repeated sky images for objects that vary significantly between frames. CAst solves missing plate solutions automatically, builds a shared comparison catalog, and flags candidates with real frame-to-frame variability.

### AstroStack

Combine and process image sequences with stacking and signal analysis tools.

### Sky View

An interactive sky atlas with a GPU-rendered Milky Way, constellation overlays, a detailed Moon with tiled albedo and normal-map textures, real-time ephemeris, and smooth pan/zoom navigation.

### Distance Map

Visualize stellar distances and spatial structure within your observed fields.

## Supported inputs

- `.fit` / `.fits` (FITS)
- `.xisf` (PixInsight XISF)

Images can be plate-solved beforehand or solved on the fly through astrometry.net (API key required for unsolved images).

## Installation

**For alpha reviewers:** Download the installer from the [Releases](../../releases) page. After installation, check for updates from **File > Check for Updates** inside the app.

**For developers:**

```powershell
python -m pip install -e .
python -m photometry_app.main
```

Requires Python 3.11+. See the full dependency list in `pyproject.toml`.

## Configuration

- **Astrometry.net API key:** Set in the app Settings dialog, or via the environment variable `CITIZEN_PHOTOMETRY_ASTROMETRY_API_KEY`.
- **Observatory setup:** Telescope, camera, focal length, pixel size, location, and Bortle class can be configured in Settings and are written into exported science reports.
- **Themes:** Eight built-in dark themes (Gruvbox, Nord, Dracula, Tokyo Night, Catppuccin, Solarized Dark, One Dark, and Dark), plus custom theme editing with import/export.

## Outputs

Depending on the mode and export action, CAst can produce:

- Differential light curve CSVs and themed PNG plots
- AAVSO Extended format reports with preflight validation
- Science-ready accepted/rejected observation JSON (schema v3)
- Annotated image exports and animated GIF blink recordings
- HR diagram exports with scientific tables
- Asteroid/comet recovery benchmark CSVs and discovery summaries
- Calibrated FITS outputs with reduction manifests

## Documentation

See [DOCUMENTATION.md](DOCUMENTATION.md) for the full user guide, [differential_photometry.md](differential_photometry.md) for Differential Photometry, [HR_DIAGRAM.md](HR_DIAGRAM.md) for the HR Diagram reference, and [asteroid_comet_detection.md](asteroid_comet_detection.md) for Asteroid/Comet Detection.

## Status

CAst is in **alpha**. It is under active development and being distributed privately for review. Core workflows are functional but some features are still incomplete. The installer is currently unsigned -- Windows SmartScreen may show a warning on first run.

## License

This project is licensed under the [Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International](https://creativecommons.org/licenses/by-nc-nd/4.0/) license. You are free to use and share it for educational and non-commercial purposes, but you may not create derivative works or use it for commercial gain. See [LICENSE](LICENSE) for details.

Developed by Ogetay. For more information, visit [ogetay.com/citizen-astronomy-cast](https://ogetay.com/citizen-astronomy-cast).