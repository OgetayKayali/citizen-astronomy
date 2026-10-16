# Pending release notes

Fixes staged for the next update. Use these notes for the `-Notes` parameter of
`packaging/publish_github_update.ps1`, then clear this file after publishing.

## Since 0.1.1-alpha.16

### Differential Photometry

- Label comparison stars with AAVSO AUID (UID) when they match the VSP sequence, or with an APASS designation otherwise, instead of long Gaia source numbers.
- Ephemeris Light Curve pulls the latest 30 days of AAVSO data and draws a smooth curve you can zoom and drag, with upcoming cycles by epoch and the current epoch marked.

### HR Diagram

- Add a Filter button between Advanced and Only Group for color/temperature, distance, luminosity, and stellar class. Stars missing the selected measurement are left out, and the Work Log lists the ranges and what was removed.

### Sky Explorer

- Starless frames can still be explored from an existing WCS: a same-size FITS beside a PNG, JPEG, or TIFF is used automatically, and Explore or Mag Limit asks before plate-solving when the image has almost no stars.
- Read PixInsight 1.9 XISF plate solutions stored as AstrometricSolution properties, so a solved starless frame is used directly instead of being sent to Gaia or astrometry.net.
- Estimate magnitude limit queries Pan-STARRS (north) or DES DR1 (south, SkyMapper fallback) for stars fainter than Gaia’s ~21 mag cutoff, and the Work Log states it when a catalog cannot reach the requested final magnitude.
- Scientific and Advanced galaxy searches mark faint background galaxies from DES (south) or Pan-STARRS (north), not only the few brightest SIMBAD/HyperLEDA names.
- Create collage includes those faint survey galaxies (30″ default crop when size is missing), and double-click opens NED for the galaxies or the matching VizieR catalog for survey stars.
- Explore and Mag Limit faint-star search change to a clickable **Terminate** button as soon as the query starts, including while the catalog thread is still starting.
- A drawn ROI shows its plate-solved field of view at the top-left corner, with a Sky Explorer Visuals option to hide that label.
- Mag Limit faint-star search uses the drawn ROI, so a small rectangle is queried on its own instead of as the whole frame.
- Estimate Magnitude Limit separates Magnitude ladder and Automatic limit, with shared bright/faint/step controls, a live summary, and a ladder option to mark only sources detected in the image.

### Observation Deck

- Right-click images or folders in the Library tree to **Exclude from Statistics** (for example duplicate copies) or **Include in Statistics** again; excluded files stay listed but dimmed, the choice survives rescans, and the files themselves are not changed.

### App

- Add an AI Assistant (Tools > AI Assistant) that uses your own OpenAI-compatible, Claude, or Gemini API key (saved in the system credential store) to answer how-to questions from the CAst guides, read folders, headers, and WCS to report which images show objects such as globular clusters, switch modes, and build an Observation Deck while finding exact and same-exposure duplicate images to leave out, each action after you approve. It never changes, moves, or deletes your files, and it sends no image pixels.
- Redesign the mode picker into Detect & Measure and Explore, Visualize & Process rows that fill the window, with each mode's title and description under its preview image.
