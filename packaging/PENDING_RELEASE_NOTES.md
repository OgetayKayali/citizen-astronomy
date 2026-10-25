# Pending release notes

Fixes staged for the next update. Use these notes for the `-Notes` parameter of
`packaging/publish_github_update.ps1`, then clear this file after publishing.

## Since 0.1.1-alpha.18

### Binary Light Lab

- Add six detached-binary examples and 16 Library models for Roche and contact binaries, pulsators, spots, dwarf-nova outbursts, novae, and flares. Record a live flux or magnitude curve with band, exposure, third light, and noise, then save the system or export the model curve and the observations. Glow and star colors change only the picture.

### Aurora & Magnetosphere Lab

- Add an orbitable Earth and magnetosphere with quiet solar wind and northward and southward CME presets on one scrubbable timeline. It shows proton dynamic pressure, a Shue magnetopause, a Jelínek bow shock, delayed near-Earth wind, and schematic closed, open, and interplanetary field lines. Auroral precipitation is not drawn.

### App

- About links to the Discord.

### HR Diagram

- The Filter window gives each limit an On/Off switch. A section stays closed and does not remove stars until that switch is On.
- Stellar-class filters now prefer published SIMBAD spectral types, retrieved in a cached bulk field query. Spectral and luminosity-class filtering are independent; color-based estimates require an explicit fallback option. Info cards and HR exports preserve classification provenance. This fixes known B stars disappearing because of color estimates or missing luminosity-guide classifications.

### Differential Photometry

- Comparison stars and the check star have separate Show references and Show check switches on the light curve. A check star is measured like the target, left out of the comparison ensemble, and marked on the image with a blue circle and square, distinct from the gold comparison circles.
- Add Photometric Transformation in the workflow and under Settings. It fits a B/V instrument profile from standard-field images, including extracted RGB channels, or from a measurements CSV, then applies that profile the next time you generate. The light curve can show Transformed Magnitude (B/V) separately from Standard Magnitude. Profiles can be saved, imported, and exported, including TG/TA coefficients. AAVSO export sets TRANS from the applied result; the old transformed checkbox does not mark data as transformed.
- Calculate Periods starts off. Turn it on when you want a period inferred from these observations.
- Measurements & residuals has a Minimum SNR control that updates which standards are excluded without measuring again. The Reason column groups rows by exclusion category.
- Select, beside Filter, boxes light-curve points. Remove hides them from the light curve and extended report without deleting files. Mark files prefixes those names with (marked). Reset restores the curve.
- A camera or telescope header that disagrees with the calibration setup is a warning. The images stay loaded and the setup values are kept, because image metadata can be wrong.
- Standard-field images wider than one degree are cropped around the field, such as M67, and aligned when the frames do not already match.
- If AAVSO's chart service times out during calibration, the message says the service did not respond. A recognized field such as M67 is not described as missing.
