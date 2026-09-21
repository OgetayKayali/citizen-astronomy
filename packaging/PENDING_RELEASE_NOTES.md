# Pending release notes

Fixes staged for the next update. Use these notes for the `-Notes` parameter of
`packaging/publish_github_update.ps1`, then clear this file after publishing.

## Since 0.1.1-alpha.15

### Sky Atlas

- Slide the bottom menu in and out smoothly on hover instead of snapping into place.
- Add **Treat black as alpha** in Custom Sky Map so black and near-black pixels become transparent and the overlay blends into the sky.
- Restore continuous sky motion while time is running; stars and objects slide between sidereal updates instead of jumping.
- Accelerate stars with reliable GPU instancing and shader-side projection, restore Stellarium-like density with smoother cores and seamlessly fading radial halos, keep decluttered labels stable while dragging, make every visible catalog star identifiable with cursor-local picking, remove spikes and obsolete controls, retain bounded handling for huge catalogs, and report compatibility mode explicitly.
- Fix startup showing the Hipparcos loading screen when the last mode was Sky Atlas; the mode picker opens without loading Sky Atlas until that mode is selected.
- Add Barnard, Sharpless, RCW, and HASH PN catalogs in Sky Atlas Settings so you can plot known nebulae and check whether a target is already catalogued; HASH PN labels use usual names such as StDr-1.
- Selecting an object shows SIMBAD, ADS, and VizieR literature links, plus HASH for HASH PN objects.
- Search opens a centered window that scans every catalog, including hidden ones, and lists matches from most relevant to least with the object type first; the highlighted match stays readable on the dark panel, typing stays smooth, and the status line shows Searching… while catalogs are scanned.
- Draw object names, constellation names, and deep-sky circles on the GPU so zoomed-in HASH PN fields stay smooth and readable; dense catalogs still wait for a closer zoom and keep a cap on how many names appear.
- Fix the Sky Atlas footer so location, FOV, RA/Dec, and Alt/Az stay readable after GPU overlays.
- Restore the previous Milky Way background and remove the starless Gaia glow, with Low/Medium/High still mapping to 4k / 8k / 32k.
- Open on a 60° Orion field showing stars to magnitude 5, then fade in fainter catalog stars gradually as you zoom; all-sky views keep stars small, and already-visible stars brighten so magnitude steps stay even. Opening uses the existing Milky Way preview instead of stitching a 16k atlas or uploading 32k GPU tiles, and does not reload the deep Hipparcos/Gaia catalogs, so the atlas appears immediately.
- Star-catalog downloads run in the background with a cancellable progress bar that appears immediately; the current sky stays interactive and only refreshes when the catalog is ready. Gaia DR3 streams as one catalog file instead of hanging on hundreds of TAP cells. A finished download now keeps the new magnitude after restart; quitting mid-download still restores the last working limit.
- Smooth mouse-wheel zoom so the field of view glides continuously instead of jumping a step at a time.

### Differential Photometry

- Target Field Animation: set the light-curve plot label size so title, axis labels, and ticks stay readable in the exported GIF or MP4.

### Sky Explorer

- Add a **Goto** group between Display and Center Object: enter RA and Dec, then center the loaded survey on those coordinates.
- Add an **ROI** tool next to Text: click for an accent outline, drag a dashed green rectangle so Explore searches only that region, and click again to clear it. Shift+left-drag still works. ROI help rotates in the bottom status-bar tips, not as a hover popup on the image.
- Replace the native color dialog on Stroke, Fill, and Text with a themed picker; Fill includes a no-fill swatch so shapes can keep a visible stroke without a fill.
- Fix circle and ellipse drawing stuttering when catalog markers are on the image.
- Split Export into **Image...** (uploaded image plus annotations) and **Survey...** (survey plus annotations); Animation is unchanged.
- Fix Estimate magnitude limit markers: empty highlighted rings with a soft shade and no center dot, so the faintest stars stay visible.
- Fix Find actual magnitude limit on JPEG/PNG: reuse an existing Sky Explorer or ladder WCS, and load recovered SIP solves as 2D so the estimate does not crash.
- Improve Estimate magnitude limit: share start/increment/final magnitude for both modes, mark the faintest N stars, scale labels by magnitude, and center plus highlight a star selected in the results list.
- Show full Estimate magnitude limit star names on markers and in the results list instead of shortening them with “...”.

### HR Diagram

- Fix JPEG and PNG uploads crashing while matching stars to Gaia; recovered plate-solve WCS is used so the educational HR features work on those images.
