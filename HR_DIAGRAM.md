# HR Diagram Guide



## Purpose



The HR Diagram mode builds an interactive color-magnitude view from a single solved linear RGB image.



It is designed for field inspection rather than time-series differential photometry. The workflow measures stars on one source image, cross-matches them to Gaia, derives plot-ready quantities, and lets you inspect the filtered result interactively.



In the current implementation, HR mode does not use the object-folder batch workflow. It works from one selected source image at a time.



## What HR Mode Expects



Use HR mode when you have:



- a solved FITS, FIT, or XISF source image

- a linear RGB image, ideally color-calibrated

- valid celestial WCS in the file header or synthesized XISF astrometric metadata

- network access for Gaia and VSX catalog lookups



The UI itself describes the intended input as a solved linear RGB image. The preview can be stretched for inspection, but measurement is still performed on the original linear image data.



## High-Level Workflow



The HR workflow has two layers:



1. full-field measurement

2. fast display filtering



The important design rule is:



- source detection and measurement happen for the full field of view when you generate the diagram

- ROI selection only filters which already-measured rows are shown on the diagram; the `Source Results` table can still show the broader matching set and highlights which rows currently survive onto the plot



That separation is what makes `Update Diagram` fast when only the ROI changes.



## User Workflow



### 1. Switch To HR Mode



Open the app and switch the application mode to HR Diagram.



### 2. Select The Source Image



Choose the image from `File > Open File` or use the HR source image input.



The image should be:



- plate solved

- RGB or compatible 3-channel XISF/FITS data

- linear if you want the photometric measurements to remain meaningful



### 3. Confirm The Working Directory



The working directory is where HR artifacts are written. If you do not pick one explicitly, the app derives a default from the selected source image.



### 4. Set The Max Source Limit



`Max Sources` limits how many Gaia-matched detections are actually measured.



- `0` means all matched sources

- the default is `5000`

- when the field is dense, the brightest matched detections are kept first



This limit directly affects full regeneration time.



### 5. Optionally Draft An ROI



The image view supports draft ROI drawing with:



- `Circle ROI`

- `Rectangle ROI`

- the `Image` and `Results` buttons above the left pane now switch between the source-image view and the source-results view without using tabs

- `Settings > HR Diagram` now holds the live ROI `Tool` selector and `Invert ROI` toggle



Current behavior:



- plain left-drag and mouse wheel keep their default pan/zoom behavior

- `Circle ROI` is the default draft shape

- hold `Shift` and left-drag on the image to draft the currently selected ROI shape


- ROI shapes always keep stars inside the region unless `Invert ROI` is enabled, in which case the final ROI mask is flipped

- starting a new drag replaces the previous draft ROI instead of stacking another draft region on top of it

- the shared `Reset` button clears the draft ROI and restores the default image stretch and zoom

- the current diagram does not change until `Generate`, `Update Diagram`, or `Regenerate` is pressed



### 6. Click Generate



On first run, the button reads `Generate`.



The app then:



1. resolves the WCS footprint of the source image

2. queries the field catalogs

3. detects stars on the image itself

4. cross-matches those detections to Gaia

5. measures matched sources in the image

6. writes HR output files

7. refreshes the plot and `Source Results` table

While generation is running, HR mode shows a thin progress strip above the main split view and temporarily replaces the left footer's draft-ROI note with the current preparation message. If catalog-name searching is enabled, that same progress strip and footer note continue working after the image and plot appear while bright targets are resolved in the background. When that follow-up search finishes or fails, the footer note returns to its usual ROI-specific status text.



### 7. Inspect The Diagram And Table



After generation, you can:



- change the X and Y axes

- start from the default `Gaia BP-RP` versus `Gaia Absolute G Magnitude` educational view

- read a bottom-axis color-temperature scale in Kelvin and a left-axis decade-scaled luminosity axis in `L_sun` whenever the current HR axes support those physical conversions, with the raw Gaia axes moving to the top and right in that mode

- hide flagged rows

- hide saturated rows

- use the persisted `Require Parallax` setting from `Settings > HR Diagram` to keep only rows with positive parallax-derived values when needed

- constrain the plot to a Gaia G apparent-magnitude range with lower and upper bounds

- overlay educational stellar-class guide lines for the Gaia BP-RP versus Gaia Absolute G Magnitude educational HR view, including Main Sequence, Giants, Supergiants, Subgiants, and White Dwarfs

- overlay an age-guide curve for Gaia BP-RP versus Gaia Absolute G Magnitude views, with an adjustable age in Gyr

- edit the title shown at the top of the plot

- export the live plot area as a themed snapshot or export a separate scientific-style HR plot using the full filtered dataset

- change the number of table rows shown from `Settings > Open Settings > HR Diagram`

- drag the divider between `Source Results` and `Work Log` to give either pane more room

- sort the HR table by clicking a column header

- click plot points to select a source

- inspect a small selection popup from the plot with local row values, derived temperature and luminosity, and cached background-loaded SIMBAD details such as spectral type when available

- click table rows to highlight the same source on the plot

- multi-select `Source Results` rows and use the right-click menu to add them to the plot, remove them from the plot, or clear their manual plot override state

- multi-select `Source Results` rows and use the right-click menu to copy their names to the clipboard for quick searching

- inspect a `Proper Motion` column in the filtered HR table for the currently visible rows

- open the selected source in CDS, SIMBAD, Gaia, or VSX; the HR browser actions now prefer exact identifier lookups over coordinate-only searches when a named object is available

- use `Find Motion Group` to detect the dominant common-motion grouping from Gaia proper motion with a saved detection preset, while `Advanced...` opens optional expert controls for backend choice, strictness, parallax handling, HR cleanup, and automatic `Only Group` filtering; the workflow still highlights that likely cluster-like subset across the HR plot, `Source Results`, and motion overlays before optionally narrowing the view with `Only Group`



The selected HR row is also carried into the left-side `Image` view, where the same star is annotated with its measurement aperture and annulus.



The plot can either scale marker size from Gaia G apparent magnitude, so brighter stars appear as larger circles, or use a fixed point size for every source.



The secondary physical scales are currently meaningful for:



- bottom-axis color temperature when the X axis is `Gaia BP-RP`

- left-axis luminosity when the Y axis is `Gaia Absolute G Magnitude` or `Measured Absolute Magnitude Proxy`



If you switch to axes where those conversions are not meaningful, the plot falls back to showing the primary axis labels on the usual bottom and left sides.



The menu bar path `Settings > HR Diagram` stores the HR plot presentation defaults, including:



- `Require Parallax`

- `Color Saturation`

- `Opacity`

- `Selected Circle Color`

- `Selected Circle Opacity`

- `Selected Circle Size`

- `Data Point Size`

- `Fixed Point Size`

- motion-vector color and width for the `Source Image` overlay

- an optional `Color vectors by angle` setting under `Settings > HR Diagram`, which maps motion-vector direction onto a circular hue palette so stars moving in similar directions share similar colors

- an optional `Scale vector saturation by magnitude` setting under `Settings > HR Diagram`, which makes the smallest motion vectors in the current field the least saturated and the largest the most saturated while keeping a visible minimum color cue

- an optional `Search catalog/designation names` setting that runs a background SIMBAD lookup for bright HR targets after the diagram loads so friendlier names can replace Gaia-style identifiers in `Source Results`, popups, and overlays when available

- a `Name Search Mag` threshold that defaults to `12.0` Gaia G mag and limits which bright rows are included in that background search

- the live ROI `Tool` selector and `Invert ROI` toggle used by the `Image` view



The same defaults are also available in `Open Settings` under the `HR Diagram` tab, including the `Table Row Limit` used for `Source Results`.



Scientific HR exports now pin the bottom temperature axis to the visible Gaia BP-RP anchor temperatures so the export keeps readable labels without repeating the `30,000` K and `2,600` K endpoint ticks.



The `Class Guides` toggle in the HR plot toolbar is an educational overlay only. The guide lines are approximate and are shown only when the plot uses `Gaia BP-RP` versus `Gaia Absolute G Magnitude`.



The shared HR action row above the left pane now keeps `Generate`, `Image`, `Results`, `Stretch`, `Adjust Levels`, `Reset`, and `Show Motion` together so you can switch panes or adjust the image without the left-side controls shifting when the generation action changes between `Open`, `Generate`, `Update Diagram`, or `Regenerate`.



The diagram-summary note now sits at the bottom of the right plot pane, while the draft-ROI note sits at the bottom of the left image-or-results pane. Those two notes are the only persistent bottom-strip text in HR mode.



The HR toolbar also includes:



- a title field that edits the plot title live

- a format selector for `PNG`, `PDF`, or `SVG`

- `Snapshot` export, which captures the current plot area exactly as shown with the active theme and zoom level

- `Scientific` export, which renders a cleaner white-background plot from the full filtered HR dataset while keeping the current axis limits

- `Image + Plot...`, which saves the current annotated `Source Image` view and the current visible HR plot side by side as one PNG snapshot

- `Source Image...`, which saves just the current annotated `Source Image` pane



### 8. Update The Diagram Without Re-Measuring



If only the ROI changed and the measurement-affecting settings are unchanged, the button becomes `Update Diagram`.



That path reuses the cached full-field HR working table and only reapplies the ROI to the existing measurements.



If the source image or important measurement settings changed, the button becomes `Regenerate`.



## Generate, Update Diagram, And Regenerate



The button text is meaningful.



### Generate



Shown when no HR working table exists yet.



### Update Diagram



Shown when the app can reuse the existing measurements.



When a draft ROI change is pending, `Update Diagram` uses the same accent-attention styling as `Generate` so it is visually obvious that the cached measurements can be reapplied.



This is valid only when all of the following still match the cached run:



- source image path

- HR max source limit

- aperture radius

- annulus inner radius

- annulus outer radius

- frame-edge margin percent

- saturation-filter enabled state



Typical `Update Diagram` use case:



- draw a different ROI

- leave measurement settings alone

- press `Update Diagram`



### Regenerate



Shown when a cached HR working table exists but is no longer valid for the current settings.



Typical `Regenerate` triggers:



- different source image

- changed aperture or annulus settings

- changed edge margin

- changed saturation filter setting

- changed HR max source count



## ROI Semantics



The HR image panel uses draft ROI and applied ROI as separate concepts.



### Draft ROI



The visible ROI drawn on the image is only a draft until you press the generation button.



### Applied ROI



When you press `Generate`, `Update Diagram`, or `Regenerate`, the current draft ROI is copied into the applied ROI state. The HR plot is then filtered from that applied ROI snapshot, while `Source Results` continues to show the broader matching set and marks which rows are currently plotted.



### Why This Matters



This behavior prevents the plot from changing every time you draw, clear, or tweak a draft region. The diagram only updates when you explicitly ask for it.



### Include, Exclude, And Invert



ROI filtering supports:



- include regions: keep sources inside these regions

- exclude regions: remove sources inside these regions

- invert: flip the final ROI mask



If no applied ROI exists, the whole measured field is shown.



## Source Image Annotations And Motion View



The `Image` view is not limited to ROI drafting.



It also reflects the current HR selection and can optionally visualize Gaia proper motion for the currently visible HR rows.



If the selected source image is RGB, the preview now stays in color instead of collapsing to grayscale.



### Selected Source Annotation



When you select one or more stars in either of these places:



- the `Source Results` table

- the HR plot



and then switch to `Image`, the selected sources are annotated on the image with single-circle markers that use the configured HR selection-circle color.



If `Find Motion Group` is active, the highlighted group members are also annotated automatically on `Source Image` even before you manually reselect rows in `Source Results`.



The image view also recenters itself so the current selection is visible immediately. For multi-row table selections, the `Image` view falls back to the full-field framing and centers on the selected group.



The `Source Image...` action under the top-right `Export` menu writes the current `Image` view to disk with the active overlays included, such as selected-source circles, proper-motion vectors, and ROI drawings.



### Show Motion



The `Show Motion` button enables a proper-motion vector overlay for the currently plotted HR rows.



Current behavior:



- vectors are generated lazily only when `Show Motion` is enabled

- when an applied ROI is active, vectors follow only the stars that currently survive onto the plotted subset

- vector direction follows the source image WCS, so rotated images still show the correct on-image direction of motion

- vector length is scaled from Gaia proper-motion magnitude, using $|\mu| = \sqrt{\mu_{\alpha*}^2 + \mu_{\delta}^2}$ as the value metric

- when `Color vectors by angle` is enabled, the vector hue follows its on-image angle through a circular palette, so $0^\circ$ and $360^\circ$ share the same color while different motion directions separate visually

- when `Scale vector saturation by magnitude` is enabled, the current field's smallest proper-motion vectors use the least saturation and the largest use the most saturation, so magnitude differences are visible without losing the direction color cue

- `Find Motion Group` uses the currently saved detection preset, can still run either the built-in lightweight density-style grouping pass or an alternative `Sklearn DBSCAN` pass in Gaia proper-motion space, adds parallax when enough rows support it unless the advanced settings say otherwise, optionally applies a second-pass HR consistency refinement, and highlights the dominant group in the plot, `Source Results`, and motion overlay

- `Advanced...` opens the Motion Group Detection dialog, where `Default`, `Tight`, `Loose`, `Parallax Priority`, and `Custom` presets keep the main toolbar simple while still exposing expert controls when needed

- the expert section in `Advanced...` lets you choose the backend, tighten or loosen strictness, require parallax or ignore it, and turn HR cleanup on or off without adding those controls permanently to the toolbar

- `Only Group` switches the HR plot, `Source Results`, and motion overlay from highlight mode into a filtered member-only view

- common-motion members are marked with the configured selected-circle styling in the live plot and scientific export, while `Source Results` and source-image motion vectors still keep their dedicated highlight treatment until `Clear Motion Group` is pressed

- the vector color and width are configured in `Settings > HR Diagram`

- `Ctrl` + left-click on a star in `Source Image` selects that source directly from the image, updates the HR plot selection, opens the plot popup details, and enables the catalog browser buttons even when `Show Motion` is off



This is especially useful when you want to see whether a subset of field stars shares a common apparent motion pattern, such as a cluster-like grouping.



## Age Guide Overlay



The HR plot can overlay an optional age-guide curve when the axes are `Gaia BP-RP` on X and `Gaia Absolute G Magnitude` on Y.



This overlay is meant as a fast visual guide, especially for old cluster fields such as globular clusters.



Current behavior:



- the guide is local and immediate; it does not query any remote service

- the age value is adjustable in Gyr from the HR plot control row

- the guide is only drawn for the Gaia-color versus Gaia-absolute-magnitude view, because the other axis combinations do not share the same interpretation

- when enabled on unsupported axes, the plot keeps working normally and reports that the guide is only available for the Gaia BP-RP / Gaia Absolute G Magnitude view



## Gaia Button Behavior



When the selected HR row is a Gaia DR3 source, the `Gaia` button now opens a direct Gaia TAP query result for that source ID instead of opening only the Gaia Archive home page. The same source ID is also copied to the clipboard.



## Plot Selection Popup



When you click a source directly on the HR plot, the app now shows a small popup near the cursor.



The popup immediately uses data already present in the HR row, including:



- source name and catalog/source ID

- Gaia G apparent magnitude

- BP-RP color

- parallax



If network lookup is available, the app also starts a background SIMBAD query for the selected coordinates and refreshes the popup when a match is found.



Current behavior:



- the lookup runs off the UI thread so plot selection does not freeze the window

- results are cached for the rest of the session, so reselecting the same source does not repeat the network request

- when SIMBAD returns a match, the popup can include object type, SIMBAD identifier, spectral type, and a SIMBAD V magnitude if available



## Source Detection And Matching



The HR workflow is image-first.



It does not iterate over every Gaia source in the solved footprint and try to measure all of them. Instead it:



1. creates a centroid plane

2. runs `DAOStarFinder` on the image

3. keeps valid detections inside the usable image area

4. sorts detections by peak brightness

5. converts detections to sky coordinates using the image WCS

6. matches detections to Gaia sources

7. keeps the best match per Gaia source



Important implementation details:



- the source-detection threshold is based on a sigma-clipped background estimate

- the cross-match radius is bounded rather than unbounded

- wide fields still cache the full Gaia field, but only Gaia-matched image detections are measured



This is the main reason HR preparation scales much better than a naive full-footprint measurement approach.



## How Measurements Are Computed



Each matched source stores one `HrMeasurementRow`.



Key fields include:



- source identity and catalog metadata

- sky position and image coordinates

- aperture and annulus radii

- per-channel fluxes and flux errors

- instrumental magnitudes

- signal-to-noise estimates

- background levels

- saturation status

- flags

- calibrated luminance magnitude

- absolute-magnitude proxy

- Gaia absolute G magnitude

- plot color index



### Photometry Planes



If the image is RGB, the workflow measures:



- red

- green

- blue

- luminance



The luminance plane is the mean of the three RGB planes.



If the image is monochrome, only luminance is used.



### Aperture Photometry



For each plane:



- the aperture sum is measured in a circular aperture

- the local background is estimated from a circular annulus

- the background contribution is subtracted

- flux error and SNR are derived from the measurement statistics



If the background-subtracted flux is non-positive, that plane is flagged and no magnitude is produced for it.



### Saturation And Edge Handling



Rows may be flagged or excluded from zero-point calibration when they are:



- saturated

- near saturation

- too close to the image edge according to the configured frame-edge margin

- non-positive after background subtraction



## Derived Plot Quantities



### Instrumental Magnitude



For a valid plane measurement, instrumental magnitude is computed as:



$$m_{inst} = -2.5 \log_{10}(F)$$



where $F$ is the background-subtracted flux.



### Instrumental Blue Minus Red



If both instrumental blue and red magnitudes exist:



$$m_{B-R} = m_{blue} - m_{red}$$



### Plot Color Index



The plotted color index prefers Gaia `BP-RP`.



Fallback behavior:



- use Gaia `BP-RP` when available

- otherwise use instrumental blue minus red



### Zero-Point Calibration



The app derives a luminance zero-point offset from rows that are usable for calibration.



Conceptually:



$$ZP = \mathrm{median}(G_{Gaia} - m_{inst,L})$$



Then:



$$m_{cal,L} = m_{inst,L} + ZP$$



Only non-saturated rows with usable Gaia G and instrumental luminance magnitude participate.



### Measured Absolute Magnitude Proxy



If calibrated luminance magnitude and positive parallax are available, the app computes:



$$M_{proxy} = m_{cal,L} + 5\log_{10}(\varpi_{mas}) - 10$$



where $\varpi_{mas}$ is parallax in milliarcseconds.



This is called a proxy because it is built from luminance calibration tied to Gaia G rather than a strict passband-correct absolute magnitude.



### Gaia Absolute G Magnitude



If Gaia G apparent magnitude and positive parallax are available, the app also computes:



$$M_G = G_{Gaia} + 5\log_{10}(\varpi_{mas}) - 10$$



This gives you a clean Gaia-only absolute-magnitude axis that does not depend on the image-measured luminance calibration.



## Plot Controls



The HR plot supports these X axes:



- Gaia BP-RP

- Instrumental Blue - Red

- Resolved Color Index



The Y axis supports:



- Measured Absolute Magnitude Proxy

- Gaia Absolute G Magnitude

- Calibrated Luminance Magnitude

- Gaia G Magnitude



Additional controls:



- `Hide flagged`

- `Hide saturated`

- `Reset`



Notes:



- the plot uses deterministic downsampling for large fields so the displayed distribution stays visually stable when table limits change

- the selected source is highlighted on the plot

- if the selected source would otherwise be omitted from the display sample, it is forced into the displayed set so the highlight remains visible

- `Settings > HR Diagram` controls whether positive parallax is required, how saturated the red/blue plot colors appear, point opacity, the selected-point circle color/opacity/size factor, and whether marker sizes are fixed or scaled by Gaia G apparent magnitude



## Image Preview Controls



The image panel includes:



- stretch mode: `Linear`, `Asinh`, `Sqrt`, `Log`

- brightness

- contrast

- `Reset View`

- `Reset Display`



These controls are visual only.



They do not change:



- measurement values

- ROI coordinates

- source detection

- aperture photometry



The implementation explicitly keeps measurement and ROI logic on the original linear image while the preview uses a non-linear visual reference.



## HR Results Table



The `Source Results` table shows the currently visible table subset from the filterable HR rows, even when the plot itself is narrowed by ROI or manual plot overrides.



Columns:



- Source

- Catalog

- Gaia G

- BP-RP

- Calibrated

- Abs Proxy

- SNR

- Zero Point

- Flags



Important behavior:



- the table row limit only controls how many rows are shown in the table, not how many rows were measured

- sorting is numeric-aware for numeric columns

- the table is model-backed rather than `QTableWidget`-backed so large row counts remain responsive

- rows that are currently included on the HR plot are tinted with a secondary background color

- rows that are currently included on the HR plot are also floated above non-plotted rows in `Source Results` after `Generate`, `Update Diagram`, or `Regenerate`

- rows added or removed manually from the plot via the `Source Results` right-click menu use distinct highlight colors so the override state is visible

- selecting a row updates the active browser buttons and plot highlight

- the table supports multi-selection so you can add or remove several rows from the plot in one action

- the `Source Results` right-click menu can also pin the current selection to the top of the table so those stars remain easy to find while you review thousands of rows

- the HR splitter sizes are cached in settings, so custom `Source Results` versus `Work Log` sizing persists across reloads and relaunches



## Browser Buttons



The HR results panel exposes direct catalog links for the selected row:



- `CDS`

- `Simbad`

- `Gaia`

- `VSX`



Availability depends on the selected catalog:



- `Gaia` is enabled for Gaia DR3 rows

- `VSX` is enabled for VSX rows

- `CDS` and `Simbad` are enabled whenever a valid HR row is selected



## Work Log And Progress



The HR panel includes two user-facing progress mechanisms:



- a `Work Log` pane with timestamped messages

- the plot-area status message shown while a diagram is being prepared



During generation the app reports:



- WCS resolution progress

- catalog loading

- HR source-measurement progress

- HR working-table finalization after the last measurement update

- file-writing completion

- field and working-table summary values



When measurement progress messages match the internal `[HR current/total]` format, the progress bar shows percentage complete.

Routine HR progress messages no longer reuse the application status bar, so the bottom footer strip stays reserved for the draft-ROI and diagram-summary notes.



## Files Written By HR Mode



The working directory receives three HR artifacts:



- `hr_field_catalog.json`

- `hr_working_table.json`

- `hr_working_table.csv`



### hr_field_catalog.json



Contains:



- source image path

- solved field center, radius, dimensions, and WCS path

- catalog counts

- serialized Gaia stars

- serialized VSX variables

- serialized exoplanet entries



### hr_working_table.json



Contains:



- source image path

- solved field metadata

- the full serialized `HrWorkingTable`



### hr_working_table.csv



Contains one flat row per measured HR source, including:



- source identity

- Gaia metadata

- image coordinates

- aperture geometry

- per-plane flux and magnitude values

- calibrated and derived quantities

- zero-point usage

- flags



## Performance Notes



The current HR implementation includes several performance-oriented choices:



- image-first source detection rather than full-catalog measurement

- optional source cap for dense fields

- cached full-field measurement reuse for ROI-only updates

- vectorized ROI masking over cached row coordinates

- deterministic plot downsampling for display stability

- model-backed HR table rendering for large row counts



If a field is very dense, the most expensive operations are still:



- full first-time generation

- regenerating after measurement-setting changes



## Troubleshooting



### The Generate Button Says Update Diagram



That means the app believes the existing HR working table is still valid and can be reused.



### The Generate Button Says Regenerate



That means a full remeasurement is required because the cache key no longer matches the current HR setup.



### Clearing The ROI Did Not Clear The Current Diagram



That is expected.



`Clear All` only clears the draft ROI. The current plot keeps using the last applied ROI until you press the generation button again.



### The Image Looks Different But The Results Did Not Change



That is also expected.



Brightness, contrast, and stretch are preview-only controls.



### Some Rows Have Flags Or Missing Values



Common reasons include:



- saturated sources

- near-saturated sources

- non-positive background-subtracted flux

- missing or invalid parallax

- sources near the frame edge



### Why Are There Fewer Plotted Rows Than Measured Rows?



Because the visible plot may exclude rows due to:



- ROI filtering

- manual add/remove overrides from `Source Results`

- stable high-density display downsampling that keeps the visible plot subset from reshuffling when you remove an unrelated star

- hidden flagged rows

- hidden saturated rows

- missing axis values

- parallax requirement

- display downsampling for large fields



## Limitations



Current HR mode is intentionally scoped.



- it works from one source image at a time

- it depends on valid WCS for the selected image

- the absolute magnitude value is a proxy derived from calibrated luminance and parallax, not a full astrophysical passband transform

- it is optimized for interactive inspection, not publication-grade stellar-population analysis



## Related Files



- [README.md](README.md)

- [DOCUMENTATION.md](DOCUMENTATION.md)

- [photometry_app/core/hr_diagram.py](photometry_app/core/hr_diagram.py)

- [photometry_app/ui/hr_plot_widget.py](photometry_app/ui/hr_plot_widget.py)

- [photometry_app/ui/main_window.py](photometry_app/ui/main_window.py)

