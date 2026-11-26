# Measure 3I/ATLAS in CAst

Use this walkthrough to measure the light inside a chosen coma aperture and repeat the measurement on the same pixels. The example contains **76 luminance (L) exposures**. Report their sky-subtracted flux in native FITS units per second and their magnitude on the instrumental scale. An instrumental magnitude has an arbitrary zero point.

## Prepare the observing sequence

1. Open **Asteroid / Comet Detection**. Choose **File > Open Folder** and select `example_data\Asteroid-Comet (3I ATLAS-mainbelts)` alongside the Photometry project. Keep the **L / 120 s** frames together. Select the first exposure, ending in `0001_c.fits`, as the reference for this walkthrough.
2. Check your **Observing Site** settings before running **Generate**. The example FITS headers give latitude **+31.5472°** and east longitude **−99.3825°**. When no site is configured, the comet workspace can recover consistent coordinates from the FITS headers. The benchmark assumes sea level because the headers omit elevation. Use the exposure times in the headers. On 2025 December 7 the first exposure started at **09:00:30.776 UTC**, and the last ended at **11:54:21.165 UTC**. The filenames carry different clock times.
3. Click **Generate**, select **3I/ATLAS** (also designated **C/2025 N1**) in **Predicted Objects**, then click **Photometry**. Verify the target name in the new window. The other moving objects in this field have their own measurements.
4. In **Frames**, check the rows you intend to use. Each example image is 6248 × 4176 pixels. Its FITS history records alignment to a common star grid. Verify the upstream dark, bias and flat processing against your reduction record before checking **Images are calibrated and linear**. The example headers omit the calibration-status and physical-unit keywords.
5. Choose **JPL Horizons** under **Advanced controls > Tracking**, then **Prepare Photometry**. CAst uses the position at each exposure midpoint. The **Comet** image follows the moving target. **Comparison Stars** shows the field aligned on stars. Both use a mean of exposure-normalized frames. A center change on the completed stack moves the aperture only. Re-prepare to change the tracking.

## Measure the coma

1. Click **Show Comet** to inspect the predicted center. To adjust it, click **Set Coma Center** and click once on the condensation. **Reset to Predicted Center** restores the reference ephemeris position. Use the predicted center for comparisons that specify it.
2. Set **Coma radius** to **12 pixels** and leave **Scale sky annulus with aperture** checked. For this example the sky annulus runs from **24 to 36 pixels**. The inner circle defines the source aperture. The two outer circles bound the pixels used to estimate the sky. If comparing with a report in arcseconds, check whether it quotes a radius or a diameter.
3. Inspect the sky annulus for coma, stars, gradients and trails. To sample another region, select **Advanced controls > Background > Separate sky patch**, click **Pick Sky Patch on Image**, and click on clear sky. Set the patch radius in pixels. For a rectangular area choose **Separate sky rectangle**, click **Pick Sky Rectangle on Image**, and click two opposite corners. Keep the whole region free of visible coma and field stars, and check its outline. A different sky region defines a different measurement.
4. Inspect **Growth Curve** and **Measurements**. Each point sums the sky-subtracted light inside a different radius about the same center, using the same sky estimate. A curve that levels off supports an aperture choice. A rise at a stellar trail, or a fall when the sky overlaps the outer coma, calls for inspecting the image. Quote the actual aperture with any brightness value.
5. Read **Quality** before using a result. Incomplete coverage or saturation can invalidate an aperture. Some CAst-aligned images in this dataset have zero-filled borders. CAst masks the edge-connected padding and preserves isolated zero-valued pixels. A nonpositive net flux has no logarithmic magnitude. The reported uncertainty estimates the contribution of the sky and, where available, calibration scatter. Source shot noise, pixel correlations from resampling and photometric transformations require separate treatment.

## Calibrate with comparison stars

For a supported filter such as Johnson V, review **Comparison Stars** on the star-aligned image. Select a row to inspect its aperture and residual. Exclude blended stars, trails and poor fits. **Only solar-like stars (B−V 0.5–0.8)** narrows the comparison ensemble. With at least three usable stellar widths, **Use 4 × FWHM Radius** sets the star aperture radius to four times the median full width at half maximum, rounded up in pixels. Inspect the new apertures for neighbors. This setting changes the comparison-star fluxes used for the zero point.

CAst needs at least three accepted stars with magnitudes and uncertainties in the requested band to apply an APASS zero point. The output names the band and labels the calibration as untransformed. For the L frames in this example, expect **Instrumental** with the default settings. To calibrate against V comparison stars, enable **Advanced controls > Reference clear/L to APASS V** before preparing. Report this result as **L, APASS V-referenced, untransformed**. The difference between the camera's luminance response and Johnson V can affect the comet and stars differently.

**Export Comparison CSV…** saves the catalog, passband, sky coordinates, magnitudes, uncertainties and selected stars. **Import Comparison CSV…** places that sequence on the prepared star image using its WCS. Reusing the same sequence lets another observer reproduce the fit. For frames labeled with a Pan-STARRS passband such as `PS1r`, the automatic lookup uses ATLAS Refcat2 photometry on the PS1 system. Check the standard in the exported magnitude-system field. [APASS DR9](https://cdsarc.cds.unistra.fr/viz-bin/cat/II/336) · [ATLAS Refcat2](https://stdatu.stsci.edu/hlsp/atlas-refcat2)

**Advanced controls > Differential extinction** adjusts the calibration for the difference in atmospheric attenuation between the moving target and its comparison stars over the selected exposures. Leave **None** for the benchmark. For Johnson V data prepared with an observing site, choose the applicable Green seasonal or average model. For a different filter choose **Measured coefficient** and enter your extinction coefficient for that passband in mag/airmass. The coefficient, model and target attenuation are recorded in the CSV. Adding a further absolute extinction correction to the reported magnitude would count the common attenuation twice.

## Inspect stars crossing the coma and measure the tail

1. Record the unfiltered result first. If a field-star trail crosses the aperture, enable **Advanced controls > Filter bright stars in coma**. In **Radial ring statistic**, choose **Mean (COBS recipe)** or **Median**. Outside the **Protected core radius** (default 7 px), CAst measures the chosen statistic in two-pixel rings around the coma. A pixel above **Star threshold** times the positive ring level is replaced with that level. The default threshold is 3. [COBS star removal instructions](https://www.cobs.si/help/howto/comet-photometry-tool/)
2. Click **Compare Filtered Aperture…** to look at both cutouts at the same display stretch. Check whether the replacement follows an unrelated star trail or a real comet feature such as a jet. Compare the raw and corrected flux and count of changed pixels in the CSV, which also records the ring statistic. For the 76-frame example at the predicted center, a 12 px aperture, 7 px protected core, ring mean and threshold of 3 give zero replacements. Pixels touching the aperture outside the protected core reach only about 1.06 times their ring mean. The error estimate excludes uncertainty introduced by the filter.
3. In **Comet > Display contrast**, compare **Balanced**, **Faint structures** and **Bright core**. Use the faint-structure stretch to look for the tail and the bright-core stretch to inspect the condensation. All three display the same linear stack and give the same aperture flux.
4. To measure a visible tail on the original comet stack, set the coma center, click **Measure Tail**, then click the visible tip. The ruler gives angular length in arcminutes and position angle in degrees east of celestial north. The CSV records the display contrast used when selecting the endpoint. A faint tail can extend past the visible endpoint. **Clear Tail** removes the measurement.

## Report a calibrated observation

Open **Comet Report…** on the selected aperture. **Aperture Afρ** shows the projected aperture radius once Sun–comet and observer–comet distances are supplied. Horizons preparation supplies mean distances over the selected exposures. For another tracking source, enter distances at the observation epoch and click **Apply** to include them in the CSV and session.

With a matched calibration band and solar magnitude, CAst reports Afρ in centimeters for each aperture. This broadband proxy includes any gas or nuclear light in the aperture and is quoted at the observed phase angle. The example's L passband supports a projected radius, including when V-referenced, but lacks a matched solar magnitude for Afρ. [Afρ definition](https://sbpy.readthedocs.io/en/stable/sbpy/activity/dust.html) · [Solar magnitudes used in the conversion](https://mips.as.arizona.edu/~cnaw/sun.html)

For an observation with a defensible total magnitude, open **ICQ observation**. Enter the assigned observer, instrument, method and catalog keys, telescope aperture in centimeters, focal ratio and actual calibration steps. If you separately estimated the visible coma diameter or degree of condensation, enter those optional values. Inspect the growth curve before confirming that the selected aperture contains the visible coma. Review any flagged quality issue. The preview fills when the required inputs and a catalog-referenced magnitude are available. Use **Copy ICQ Line** or **Save ICQ…** to preserve the fixed columns, including leading spaces. The CCD extension records the photometric aperture diameter separately from the estimated visible coma diameter. [COBS ICQ field definitions](https://cobs.si/help/icq_format/)

## Save and reproduce the measurement

Choose **Export Measurements…** for the CSV and **Save Session…** for the `.castphotometry` session. The CSV contains the selected aperture and growth-curve rows, center, sky geometry, retained and clipped sky-pixel counts, sky RMS, coverage, frame count, tracking, magnitude system and quality. Filter on `selected_aperture` to locate the quoted result. The session stores the linear stacks, WCS, per-frame positions, comparison-star selection, ring statistic and display preset. Open it with **Open Session…** and export again to repeat a measurement from the saved stacks.

When comparing with a second reduction, match the frame list, reference image, tracking positions, center, aperture, sky estimator, stack weighting, calibration band and any star filtering. Compare the sky estimate and net flux before rounding magnitude. A new ephemeris or a subpixel center adjustment can change the sampled flux.

### Rebuild from the 76 FITS files

From a PowerShell prompt in the project root with the CAst environment installed, run the following command. It hashes each input and uses the observatory coordinates and pinned JPL positions in `tests/data/comet_3i_atlas.json`, so a replay works offline.

```powershell
& .\.venv\Scripts\python.exe scripts\verify_comet_3i_atlas.py "..\example_data\Asteroid-Comet (3I ATLAS-mainbelts)"
```

Append `--export-csv ".\Exports\3i_atlas_replay.csv"` to that command to run the desktop measurement calculation and write your own growth-curve CSV. The maintained example records all apertures from the V-referenced run below. [Verified 3I/ATLAS growth curve](examples/3i_atlas_growth_curve.csv)

The verifier builds the CAst stack at the first-frame predicted center. A separate calculation reads original FITS sections, shifts them using bilinear interpolation, estimates the clipped median sky and integrates each boundary pixel by numerical quadrature. Both use one shared 24–36 px sky annulus, an unfiltered aperture and equal frame weights. For a manual GUI comparison at 6 or 18 px, turn off **Scale sky annulus with aperture** and set the two sky radii explicitly to 24 and 36 px.

The predicted center used for measurement on the reference grid is **(3156.89439226, 2191.12473635)** in zero-based pixels. At that location the area-equivalent scale from the distorted WCS is **3.11712246 arcsec/px**. The flux and magnitude columns below agreed between the two reductions to the precision shown.

| Aperture radius (px) | Equivalent radius (arcsec) | Sky-subtracted flux (native units/s) | Instrumental magnitude |
|---:|---:|---:|---:|
| 6 | 18.702735 | 0.022841350126056 | 4.1031955725446 |
| 12 | 37.405469 | 0.04023722930291505 | 3.4884298300619 |
| 18 | 56.108204 | 0.05038428620902147 | 3.2442622242034 |

The printed digits let you check a numerical replay. They exceed the observational precision. The 18 px aperture includes about 25% more net light than the 12 px aperture with the same sky estimate. Treat the 12 px value as a fixed-aperture result. Claiming a total magnitude requires establishing where the growth curve levels off and whether stars or tail structure affect it.

### Reproduce the L magnitude referenced to APASS V

1. Turn on **Reference clear/L to APASS V** and prepare all 76 frames with the same reference and tracking. Keep the predicted center, 12 px coma radius, 24–36 px sky annulus and star filtering off.
2. In **Comparison Stars**, choose **Import Comparison CSV…** and load `tests/data/comet_3i_atlas_apass_v.csv` from the project. Use all five listed stars. Enable **Only solar-like stars (B−V 0.5–0.8)**. Set **Star aperture** to **8 px**. CAst uses a **12–20 px** stellar sky annulus for that radius. Leave **Differential extinction** at **None**.
3. Read the selected aperture in **Measurements**. CAst retained **five of five stars**, with zero point **8.039210057113 mag** and weighted comparison-star scatter **0.008945 mag**. The desktop export gives **11.527639887174882 mag**, with an estimated error of **0.01610983006 mag**. Report **11.528 ± 0.016 mag in L, referenced to APASS V**, and include the 12 px aperture radius. The error combines background and calibration terms. The L-to-V passband mismatch adds a systematic uncertainty outside that estimate.

The same verification command independently integrates the comparison-star fluxes on the original FITS frames and refits the zero point. The calibrated results agreed within **10⁻¹⁰ mag** for these pinned settings. A new unrestricted catalog query can choose a different ensemble and produce a different zero point.

### Check the same frames with Aperture Photometry Tool

Caltech/IPAC’s Aperture Photometry Tool (APT) 3.1.6 measured the coma and all five comparison stars from the same 76 exposures. At the selected 12 px radius, APT measured **11.52645 mag** and the CAst desktop export gave **11.52764 ± 0.01611 mag**. Both are fixed-aperture L magnitudes referenced to APASS V. Reach and colleagues used APT for comet growth curves in their Spitzer survey. [APT methods paper](https://www.ipac.caltech.edu/publication/2012PASP..124..737L) · [Spitzer comet survey, §3](https://ar5iv.labs.arxiv.org/html/1306.2381)

| Coma radius (px) | CAst, original grid (mag) | APT, matched sky (mag) | APT minus original CAst (mag) | APT minus CAst on identical pixels (mag) |
|---:|---:|---:|---:|---:|
| 6 | 12.14241 | 12.13900 | −0.00341 | −0.00389 |
| 12 | 11.52764 | 11.52645 | −0.00119 | −0.00200 |
| 18 | 11.28347 | 11.28376 | +0.00028 | −0.00094 |

The comparison script verifies every FITS file against its pinned SHA-256 hash. It builds exposure-normalized mean cutouts directly from the images, tracking the comet using its saved midpoint positions and keeping stars fixed on their registered grid. APT's source-list mode places fixed apertures on integer pixels. The script therefore samples each cutout with its sky position at the central pixel and measures that grid with both programs. This puts the apertures at the same sky positions. At 12 px, changing the grid moves the CAst result by +0.00081 mag.

Both reductions use the 24–36 px comet sky annulus, an 8 px stellar radius, a 12–20 px stellar annulus and fixed centers. The matched run masks the annulus-edge and outlier pixels excluded by the benchmark’s 3σ sky selection, then APT measures its own annular median and source flux. At 12 px both codes retain 2,002 sky pixels on the centered grid. Their aperture-edge integrals still differ. APT reports an effective area of 454.44 px², while CAst integrates the circle’s 452.39 px². A second APT run on unmasked cutouts lets APT select its own sky samples. It retains 2,223 comet sky pixels and gives **11.52746 mag** at 12 px.

Each zero point is fit to that reduction’s five stellar fluxes with identical catalog magnitudes and weights. The weights use the original CAst stellar measurement errors and catalog errors. The calibrated comparison tests aperture photometry with shared tracking and calibration inputs. The uncertainty in transforming this L passband to standard V remains outside this test.

To replay on this machine from the project root, run the command below. For another installation, substitute the paths to the extracted APT package and Java executable. The tested APT.jar SHA-256 is `9e59a7bf301d29c603b827d9b3872857c8a5adc374958ab78c42712e348bdfde`. [APT 3.1.6 Windows archive](https://web.ipac.caltech.edu/staff/laher/apt/APT_v3.1.6.zip)

```powershell
& .\.venv\Scripts\python.exe scripts/compare_comet_3i_apt.py "../example_data/Asteroid-Comet (3I ATLAS-mainbelts)" --jar benchmark_results/3i_external/software/APT_v3.1.6/APT.jar --java "C:/Program Files/Android/Android Studio/jbr/bin/java.exe" --work benchmark_results/3i_external/replay
```

The work directory contains each FITS cutout, APT preferences, source position, original APT output table and a combined `comparison.csv`. In the CSV, the four unsuffixed measurement columns contain sky-subtracted flux in native units/s. The `_mag` columns contain calibrated magnitudes. `mag_error` is the original CAst background-only measurement error used to weight comparison stars. [Saved comparison for all apertures and stars](examples/3i_atlas_apt_comparison.csv)

## What changed in the Tycho Tracker comparison

CAst already provided star- and comet-aligned stacks, exact circular apertures, annular or separate circular sky, APASS calibration, growth curves and saved sessions. The comparison with Tycho's published comet workflow led to these additions. [Tycho authors' description](https://doi.org/10.5194/epsc-dps2025-1222) · [COBS worked example](https://www.cobs.si/help/howto/comet-photometry-tool/)

| Task | New CAst capability |
|---|---|
| Choose the background and inspect contamination | Rectangular sky selection, a protected-core radial star filter, before/after cutouts and a count of replaced pixels. |
| Set the comparison scale | Stellar FWHM estimates, reusable catalog-star CSVs, native PS1 calibration from ATLAS Refcat2, optional L-to-V referencing and differential extinction. |
| Describe morphology | WCS-based tail length and position angle from a marked endpoint. |
| Interpret and share measurements | Projected aperture radius, observed-phase Afρ for matched passbands and a fixed-column ICQ report with a preview. |
| Reproduce a measurement | Frame hashes, frozen midpoint ephemerides, a frozen comparison sequence and an independent numerical reduction of the original data. |

The Tycho authors describe replacing outliers with a radial median. COBS documents a ring mean. CAst exposes both statistics. For a first comparison use the same annulus and switch star filtering off in both programs. Then match the protected radius and threshold and compare the effect of each documented statistic on the same aperture. [Tycho authors' method](https://doi.org/10.5194/epsc-dps2025-1222) · [COBS filter recipe](https://www.cobs.si/help/howto/comet-photometry-tool/)

The authors' four published test targets are 29P, C/2023 A3, P/2023 S1 and C/2022 U1. The supplied 3I/ATLAS folder contains FITS images only. A direct Tycho residual requires a Tycho reduction of these same exposures with matching settings. [Published benchmark and target list](https://doi.org/10.5194/epsc-dps2025-1222)
