Citizen Astronomy (CAst) — Private Alpha Review Build
=====================================================

PRIVATE ALPHA REVIEW ONLY
-------------------------
This installer is for invited alpha reviewers only.

Do not distribute, mirror, upload, publish, or share this build outside the
private alpha-review program.

Logo designed by Ege Palaz: https://palaz.se/
Developed by Ogetay.
More information: https://ogetay.com/citizen-astronomy-cast

What is included
----------------
- A self-contained Windows desktop app. You do not need Python, pip, Qt,
  astroquery, xisf, or any other developer dependency.
- Packaged Sky View assets, Moon assets, constellation overlays, and local
  sky-atlas data needed for offline startup.

Windows SmartScreen warning
---------------------------
This alpha installer is unsigned. Windows may show a SmartScreen warning such
as "Windows protected your PC" or "Unknown publisher."

If that happens:
1. Click "More info" (if shown)
2. Click "Run anyway"

This is expected for a private unsigned alpha build.

Getting started
---------------
1. Run the installer and accept the default install location unless you have
   a specific reason to change it.
2. Launch "Citizen Astronomy (CAst)" from the Start Menu.
3. On first launch, Windows may take a few seconds to initialize OpenGL and Qt.
4. Open Help > About to confirm you are running the alpha-review build.

File formats to try in this alpha
---------------------------------
Please try opening each of these during your review:

- FITS (.fits, .fit)
- XISF (.xisf)
- TIFF (.tif, .tiff), including LZW-compressed frames if you have them
- PNG (.png)
- JPEG (.jpg, .jpeg)
- WebP (.webp) if you use tiled/WebP assets in your workflow

XISF and FITS are especially important for science-frame workflows.

Feedback and error reporting
----------------------------
Please report crashes, missing assets, file-open failures, Sky View rendering
issues, and workflow blockers to the alpha-review contact who sent you this
build.

If the app fails to start, check whether this file was created:

  %LOCALAPPDATA%\CitizenAstronomy\startup-error.log

If that log exists, include it with your report.

Uninstall
---------
IMPORTANT: Close Citizen Astronomy completely before uninstalling.

1. Exit the app from the main window.
2. Use "Uninstall Citizen Astronomy (CAst) Alpha Review" from the Start Menu
   or Windows Settings > Apps.

If the app is still running during uninstall, Windows may leave locked files
behind and the uninstall may be incomplete.

User settings after uninstall
-----------------------------
These files are stored outside the install folder and may remain after
uninstall by design:

  %LOCALAPPDATA%\CitizenAstronomy\settings.json
  %LOCALAPPDATA%\CitizenAstronomy\state.json

A later reinstall on the same Windows account may reuse those settings.
