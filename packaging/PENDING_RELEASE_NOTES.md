# Pending release notes

Fixes staged for the next update. Use these notes for the `-Notes` parameter of
`packaging/publish_github_update.ps1`, then clear this file after publishing.

## Since 0.1.1-alpha.19

### Differential photometry

- Gaia DR3 and VSX lookups can continue through the Harvard and IDIA VizieR mirrors when the primary service is unavailable. Subsequent queries reuse the working mirror, and the work log names the server that answered.
- When a target has no Standard Magnitude values, the light curve shows Differential Magnitude, or Flux (counts) if differential magnitudes are also unavailable. The plot labels the displayed quantity and explains the switch. This works in Overview and individual filter views with previously saved measurements.
- Prepare OSC / Bayer extracts photometry channels from supported Bayer FITS and camera RAW data with calibration, variance, saturation and exposure timing carried into the measurements.
- Automatic comparison-star selection accounts for passband, brightness, precision and saturation. Standard magnitudes require catalog measurements in the observed band.

### Comet and asteroid photometry

- Measure comet coma brightness with adjustable apertures, inspect growth curves and comparison-star residuals, measure the tail, and save or export the observation.
- Measure asteroid brightness on each exposure along the predicted path, inspect its light curve and quality flags, and save the session or export measurements as CSV.

### Tutorials and example reports

- Eleven illustrated tutorials cover photometry, moving objects, sky exploration and simulation modes. The 3I/ATLAS example includes a worked measurement guide, comparison data, and a report in PDF and Word formats.
