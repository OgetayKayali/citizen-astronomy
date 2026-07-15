# Recovery Audit: 2026-04-11 Onward

Status snapshot: 2026-04-14 after restoring the missing and partial items identified during the recovery audit.

Legend:
- [x] Implemented in the current workspace
- [x] Implemented and explicitly restored during the 2026-04-14 recovery audit follow-up

## 2026-04-14

- [x] Restored black, midtone, and white level support in `AnnotatedImageRenderSettings` so Differential, HR, and Asteroid/Comet previews can render frames again.
- [x] Kept the first `Source Results` row current after Differential processing so the annotated frame initializes immediately.
- [x] Restored `CatalogService` helper methods to the class body so cache/query helpers are available at runtime again.
- [x] Added focused catalog-service regression coverage for cache-key availability and field-catalog cache round-trips.
- [x] Restored the asteroid/comet 3D View time-axis contract so pyqtgraph distance and magnitude panels use Unix timestamps for plotted data, hover lookup, and playback markers again.
- [x] Restored the asteroid/comet 3D View hover overlays so per-series callouts include the hovered `YYYY-MM-DD` date and the guide date label sits in the visible bottom region.
- [x] Restored asteroid/comet 3D View primary-target state sync so dialog-side reloads refresh the title, summary, and per-type styling from the active loaded target set.
- [x] Restored the Differential VizieR alternate-center retry path and added focused regressions for the retry and warning behavior.

## 2026-04-13

- [x] Differential `Discover` honors `Settings > General > Photometry Workers` during reusable reference measurements and per-candidate photometry.
- [x] Differential `Discover` workflow reuses the solved field catalog, scores non-catalog Gaia stars, optimizes comparison groups, and merges retained candidates into `Source Results`.
- [x] Differential action/image toolbars include `Discover` beside `Find Better Fit` and `Increase SNR`, shrink `Aperture presets`, and right-anchor aperture editor controls.
- [x] Differential moved `Equatorial Grid`, `Mark Saturated`, `Frame Margin`, `Keep Comparison Stars`, and recenter controls into `Settings > Light Curve`, reduced the visible image toolbar to two rows, and replaced the old checkbox with a checkable `Aperture Editor` button.
- [x] Differential removed the visible annotated-image frame chooser, moved aperture preset/editor actions inline after `Reset`, and left frame access available from the results tables.

## 2026-04-12

- [x] Differential removed the visible `Sources` strip, replaced the `Catalog` column with `Type`, split `Find Better Fit` and `Increase SNR` into right-side accent actions, simplified the annotated-image reset behavior, and accented `Use Selected Object` in loaded results.
- [x] Differential moved the filter selector into the Workspace row, removed the inline active-object readout, reordered results tabs to start on `Source Results`, collapsed the results chrome, and switched the frame-edge margin overlay to white.
- [x] Fixed the split `CatalogService` regression so alternate solved-field lookup and the friendly preview warning path work again.
- [x] Softened Differential preview failures from unreachable VizieR requests by retrying alternate solved-field centers and surfacing a concise actionable warning.
- [x] Tightened the Differential layout by restoring Workspace actions to one line, improving catalog-button states, grouping the left side under `Results`, removing the redundant right-side `Analysis` wrapper, and switching Differential to the apply-only `Adjust Levels` workflow.
- [x] Refined the Differential chrome with a cleaner two-row workspace header, draggable split between results and lower detail tabs, flatter tabs, and clearer primary versus secondary action styling.
- [x] Created the top-level `assets` icon folder, removed startup title duplication, and loaded saved settings before the first mode sync.
- [x] Renamed the application to `Citizen Astronomy (CAst)`, showed the active mode in the window title, reordered the `Mode` menu to place `Asteroid/Comet Detection` before `HR Diagram`, and added icon lookup through `assets`.
- [x] Optimized asteroid/comet `Adjust Levels` with a downsampled preview and removed the inline status text above `Predicted Objects`.
- [x] Replaced live inline asteroid/comet level controls with the apply-on-confirmation `Adjust Levels` dialog workflow.
- [x] Refined asteroid/comet image controls with visible checked states, moved `Center Object` and `Track Object` into the image toolbar, replaced the freeform blink duration input with preset cadences, and added the histogram levels strip.
- [x] Refined asteroid/comet UI by matching secondary button height to `Generate`, dimming unavailable actions, flattening lower tabs, merging view/playback strips, and setting the default blink cadence to `0.05 s`.
- [x] Simplified the asteroid/comet main surface by collapsing top controls, moving the saved magnitude limit into settings, trimming redundant metadata, relocating `Hide Info`, and making `Predicted Objects` plus `Work Log`/`Inspector` vertically resizable.
- [x] Replaced heavier `Prepare` and `Search` sections with lighter clusters, emphasized `Generate`, reordered the selected-object toolbar so `Trajectory` and `3D View` lead advanced actions, and collapsed left-side details/work log into tabbed panes.
- [x] Reorganized asteroid/comet mode to separate workflow actions from frame/magnitude controls, group `Recover Known` and `Discover`, move object-specific actions above `Predicted Objects`, and split overlay controls into image-view and playback rows.
- [x] Extended the known-object `Trajectory` window with WCS-carried measured RA/Dec, sky-plane measured centroids plus residuals, and balanced equal-span image-plane limits.
- [x] Refreshed the known-object `Trajectory` window with a darker scientific presentation, endpoint-only labels, residual connectors, offset error bars, and median guides for offset and SNR series.
- [x] Fixed stale asteroid/comet `3D View` primary styling after switching targets by deriving summary and color state from the currently loaded target set, syncing on dialog reloads, deleting dialogs on close, and only reusing matching hidden dialogs.
- [x] Updated asteroid/comet `3D View` hover callouts so each per-series popup includes the hovered `YYYY-MM-DD` x-value plus the y-value.
- [x] Moved the asteroid/comet `3D View` hover date label into the visible bottom region of the time-series plots.
- [x] Added an immediate asteroid/comet `3D View` loading message and forced the modal progress dialog to paint before the Horizons worker starts.
- [x] Fixed asteroid/comet `3D View` time-series panels after the date-axis regression by restoring Unix-timestamp x-values for plotted data, hover lookup, and playback markers.

## 2026-04-11

- [x] Changed asteroid/comet `3D View` startup back to loading only the currently selected target set and restored the split between loaded targets and merely available targets.
- [x] Fixed ambiguous periodic-comet Horizons lookups such as `154P` by trying exact comet designation forms like `DES=154P;CAP` first.
- [x] Refined multi-target asteroid/comet `3D View` time-series axes to use calendar-formatted dates and kept the hovered x-value visible as an in-plot `YYYY-MM-DD` label.
- [x] Added separate asteroid and comet color pickers to the 3D View `Settings` popup and applied those colors through the OpenGL scene, labels, and primary time-series styling.
- [x] Changed 3D View object toggles to reuse already loaded Horizons context whenever possible instead of resetting the current camera/view.
- [x] Hardened 3D View pyqtgraph panels during object add/remove reloads by suppressing hover callbacks while plots are rebuilt.
- [x] Migrated the distance and literature-magnitude side plots from Matplotlib to pyqtgraph while preserving the UTC hover guide, inline value callouts, linked date axis, and playback markers.
- [x] Added an explicit hovered UTC x-value label to the 3D View time-series guide overlay.
- [x] Fixed the 3D View hover guide so it no longer expands the date axis toward the Matplotlib epoch.
- [x] Refined 3D View hover behavior so distance and magnitude plots draw a vertical cursor guide, intersection markers, and inline value callouts at the hovered time.
- [x] Rebalanced the right column so plots sit in a resizable splitter above a smaller default table area and kept hover readouts available on the side plots.
- [x] Added a third side plot for literature magnitude over time, including the playback marker, while remaining fail-soft if Horizons lacks usable magnitudes.
- [x] Tightened text placement, focused top-down plot limits on visible trajectories, hid out-of-window planet markers, and added a `Side View` camera mode.
- [x] Moved label controls into a `Settings` popup, replaced the inline object checkbox panel with an `Objects` dropdown, and stopped overview/top-down playback from snapping the camera back every frame.
- [x] Finished visibility controls with label font/size/bold/italic customization plus per-target hide/show toggles, and fixed playback updates so hiding the primary target no longer stops Earth or remaining visible targets.
- [x] Fixed the span reload path so `1y` and `5y` keep every selected target in the shared Horizons scene, added direct Sun/planet/object labels, and standardized selected-object orbit colors.
- [x] Added multi-target 3D View support so multi-row selection can load several known objects into the same heliocentric scene at once.
- [x] Fixed the Windows OpenGL blank-pane failure by clearing `pyqtgraph.opengl` shader-program caches whenever a fresh `GLViewWidget` is created.
- [x] Changed Generate-side 3D View to reuse one existing dialog for different targets as well as same-target reopens.
- [x] Changed the Generate-side 3D View reopen path to reuse a matching hidden dialog instead of rebuilding a fresh OpenGL window.
- [x] Changed 3D View launch from true fullscreen to a maximized windowed view and deferred `GLViewWidget` creation until after the dialog is shown.
- [x] Opened the Generate-side 3D View maximized by default, moved the frame table into the right-hand scientific column, and added optional orbital-period and major-planet overlay modes.
- [x] Fixed Generate-side 3D View reopen reliability, added span presets from the local window to `+/-90d`, `+/-180d`, `1y`, and `5y`, and faded distant trajectory arcs.
- [x] Replaced the large left pane of the Generate-side 3D View with a GPU-backed `pyqtgraph.opengl` scene and added playback, speed presets, and overview/top-down/follow camera modes.
- [x] Fixed the Generate-side 3D View dialog crash by importing the NumPy-based starfield helpers correctly and falling back to a short logged failure message on future render-time exceptions.
- [x] Refreshed the Generate-side 3D View with a darker space-sim presentation, starfield background, stronger object/Earth/Sun emphasis, darker side plots, and themed tables.
- [x] Shortened 3D View failure UX so detailed Horizons errors remain in the Work Log instead of stretching the top status/header area.
- [x] Fixed Generate-side 3D View for long frame groups and interstellar objects such as `3I/ATLAS` by splitting JPL Horizons vector requests into smaller chunks and separating orbit-arc queries from exact frame-time queries.
- [x] Added a Generate-side asteroid/comet `3D View` action that queries heliocentric state vectors in a background worker and opens a separate 3D orbital-context window.
- [x] Extended post-`Generate` trajectory inspection so the main `Predicted Objects` table can open a known object's subgroup trajectory via the new `Trajectory` action or a row double-click.
- [x] Added a separate `Trajectory...` window to asteroid/comet `Discover` and `Recover Known` review dialogs and captured per-detection RA/Dec directly during residual search for those views.
- [x] Added the separate asteroid/comet manuscript draft at `docs/paper/citizen_asteroid_comet_paper.tex`.
- [x] Updated `docs/paper/build_pdf.py` and the paper README so alternate manuscript sources under `docs/paper/` can be compiled directly with `--source`.
- [x] Added an advanced `Recover/Discover Workers` setting and threaded the moving-object search to use a user-selected worker count.
- [x] Refreshed ambiguous targeted interstellar catalog rows such as SkyBoT's generic `3I` result with the authoritative `3I/ATLAS` Horizons identity and magnitude when the targeted fallback is available.
- [x] Renamed asteroid/comet object brightness from `V_mag` to `Pred Mag` and clarified the visible-limit messaging as a Gaia G heuristic.
- [x] Expanded `Recover Known` with `Export Unmatched...` so unmatched candidates can be written directly as CSV with motion, fit, coordinate, and per-frame summary fields.
- [x] Added `Export Summary...` to `Recover Known` so recovered and missed likely visible catalog objects can be written as a concise manuscript-style CSV summary.