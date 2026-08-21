# Pending release notes

Fixes staged for the next update. Use these notes for the `-Notes` parameter of
`packaging/publish_github_update.ps1`, then clear this file after publishing.

## Since 0.1.1-alpha.10

### Differential Photometry

- Add a compact frame dropdown between Display and Center so you can switch images without losing overlays; Auto Stretch keeps the original STF, the Info panel updates to that frame, and a loading popup appears while the next image is prepared.
- Add Blink between the frame selector and Center to flip through processed frames of the current series; Center Object is now labeled Center.
- Add Ephemeris to the left of Scan Comps: look up a variable by name (no image required), pull VSX period/epoch, and show upcoming maxima or minima in the Settings timezone; row color follows a day-to-night gradient, Tonight's Schedule is visible on open with the Moon in white (star curve appears after search), hovering the chart shows time plus Moon and target altitudes, Min/Max points are labeled on the curve, eclipsing stars (EA types such as Algol) show a VSX eclipse window that peaks at mid-eclipse and fades toward contact, the altitude axis stays 0–90°, events from the past two days stay in the list and on tonight's plot, and stars count as up from 5° (changeable in Settings → Setup).
- WCS sanity now uses an adaptive central probe (a fraction of the field, expanding only if needed), quality-selected unsaturated detections, and pixel/FWHM match limits so the check is independent of camera scale; photometry still queries a small Gaia/VSX cone instead of tiling the whole field.
- Target Field Animation: keep crop-then-align by default, with a slower align-then-crop option; set loop duration and output scale, and save GIF or MP4. MP4 can repeat the sequence a chosen number of times (total length is loops × loop duration). Optionally draw a target marker (None, or the Asteroid/Comet styles including Pointer) with length, thickness, and color, and a live preview to the left of the export options. The export window shows a theme-aware pipeline with a progress bar per step and green completion text. Aligning and cropping run in the background without holding every full camera frame in memory.

### Observation Deck

- Add one or more master imaging folders (each add refreshes library stats), rescan to update the saved library. Observation Deck stays closed until that mode is selected, then restores the saved library in the background without scanning folders.
- Observation History is a spiral orbit (one turn per year): the center shows archive length and hover details, month hover follows the calendar angle, night color is integration per night, the outer ring is a smooth observing-pattern halo, and a year calendar opens from a year label or double-click.
- Archive dashboard: compact header KPIs with Library, Observation History, and Filter Usage aligned; Filter Usage uses a large donut beside a compact right-side legend when the panel is wide, and Targets takes the remaining height; filter colors follow the active theme.

### Asteroid / Comet

- Add Pointer marker style: two lines from the top and left that stop short of the object so the target sits in empty space.
