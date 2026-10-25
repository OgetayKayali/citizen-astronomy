# Binary Light Lab

Open **Simulations → Binary Light Lab** on CAst's launcher, or **Mode → Binary Light Lab**. No image files or network connection are needed. This mode embeds the PhysicsEngine Binary Light Lab's original C++ solver and stellar surface shading, with Qt controls styled for CAst and a smooth HDR stellar glow.

## First experiment

1. Choose **Algol-like / unequal temperatures** and press **Play** or Space. Stars and recorded light advance together. The gold cursor marks the current phase. Pause preserves time and observations; Restart clears the recording.
2. In **Model**, raise inclination toward 90° for deeper eclipses or lower it toward 0° to remove eclipses. Editing a physical or observing parameter starts a new recording.
3. In **Stars**, make the temperatures similar and compare the two minima. Change radii to explore eclipse width, depth, and totality.
4. Choose **Eccentric / displaced secondary**, then vary eccentricity and periastron angle. The secondary eclipse need not occur half a period after the primary.
5. In **Observe**, compare blue and red bands, dilute the eclipse with third light, or introduce reproducible noise. Change the graph from relative flux to differential magnitude; fainter values increase downward.

**Show prediction** reveals the full ideal cycle. By default, the chart displays only acquired samples. **Seek phase** pauses and begins a fresh recording at the requested phase. Adjust sliders or type directly into their number fields.

The detached-binary chart folds samples into one cycle and retains the latest 6,000 samples (ten cycles). Detached-binary sampling is 600 points per period, independently of frame rate. Library surface models use 120 samples per period or time window and retain 6,000 samples. Multimode pulsators and eruptions use elapsed days without folding; their prediction window follows playback. A stalled frame slows playback rather than skipping samples. Gaussian noise uses the original C++ generator with seed 2026. Hiding the mode suspends its timer; returning resumes its previous play/pause state without jumping forward in time.

## Stars and orbit

Mass, radius, and temperature are independent parameters in solar masses, solar radii, and kelvin. No stellar-evolution or mass-radius relation is imposed. Radii retain their physical ratio in the orthographic observer view.

Each star has a user-supplied linear limb-darkening coefficient: `I(mu) = I0 (1 - u + u mu)`. The disk normalization preserves surface flux. Coefficients are not automatically fitted to temperature or passband.

Separation is the relative orbit's **semi-major axis**, in solar radii. Period follows Kepler's third law. Editing period changes separation; editing mass changes period at fixed separation. Phase is elapsed time divided by period; zero means periastron, or the reference epoch for a circular orbit. It need not be an eclipse.

Inclination is 0° face-on and 90° edge-on. Positive observer Z points toward you. Sky rotation changes position angle without changing the unresolved light curve. The **Orbital diagram** inset shows face-on barycentric paths; it hides automatically when the scene is smaller than 850 × 550 logical pixels.

The six examples are Algol-like unequal-temperature stars, Beta Aurigae-like twins, an eccentric detached pair, grazing eclipses, a small red companion, and a non-eclipsing low-inclination control. Named analogues are illustrative configurations, not measured fits to those stars.

## Observe

| Control | Meaning |
| --- | --- |
| Band | Bolometric, or ideal rectangular energy bands at 390–490, 500–600, or 600–750 nm. These are not calibrated Johnson BVR passbands. |
| Distance | Parsecs. Absolute irradiance follows inverse distance squared; normalized light curves and scene framing do not change. |
| Third light | Constant contaminating flux as a fraction of total uneclipsed light in the selected band, not an orbiting third body. |
| Exposure | Centered integration window in seconds, at most 5% of a period. Geometry and eclipse labels are instantaneous; the measured flux is averaged. |
| Noise | Illustrative Gaussian scatter in normalized flux, in ppm; not a telescope noise budget. |
| Comparison flux | Comparison-star flux divided by the uneclipsed target flux. Differential magnitude is `-2.5 log10(relative target flux / comparison ratio)`. |

Kepler's equation determines mass-weighted barycentric positions. Occulted concentric rings use exact angular coverage, linear limb darkening, and 512 integration points with a sin² radial mapping. Bolometric surface flux is `sigma T^4`; band flux integrates the Planck spectrum over 96 wavelength bins. Absolute irradiance is W/m². Finite exposures average 25 flux samples before conversion to magnitude.

## Look and Library

**Look** controls zoom, display exposure, stellar glow, granulation, star-field brightness, the orbit inset, and optional custom stellar colors. Default colors follow temperature. These controls never modify the measured curve or clear observations. The GPU retains the original procedural stellar surfaces, front-surface occlusion, and background. Rendered pixels are not integrated to calculate flux.

**Stellar glow** controls soft camera bloom. The visible stellar disks feed four Gaussian blur scales in floating-point linear light, combined before exposure and tone mapping. This replaces the original rippling corona, keeps background stars sharp, and fades smoothly as a stellar disk is eclipsed. Zero disables bloom. Its width follows display scaling, and the stellar colors carry into the halo. It is a display effect, not a simulation of coronal plasma or calibrated telescope optics. The approach is inspired by SpaceEngine's documented [HDR and smooth bloom treatment](https://spaceengine.org/news/blog190611/), implemented independently for CAst.

**Library** makes every previously planned family runnable. Select **Load** beside an entry, or use the preset menu. The **Model** tab changes its controls and explains that model's assumptions. The original six detached examples still use the unchanged PhysicsEngine solver.

| Library entry | Implemented model | Main limitations |
| --- | --- | --- |
| EB / Beta Lyrae | Two separately filled Roche equipotential surfaces, gravity darkening, one-pass irradiation and mutual eclipses | Circular synchronous orbit; no mass-transfer stream or disk |
| EW / W Ursae Majoris | One shared Roche equipotential envelope with editable fill-out and component temperatures | No energy transport through the neck |
| Classical / Type II Cepheids | Radius and temperature oscillations, harmonic asymmetry and phase lag | Prescribed waveforms; no stellar-interior solution or period–luminosity calibration |
| RR Lyrae | Short-period asymmetric RRab example | Other RR subtypes and Blazhko modulation are not modeled |
| Mira | Large radial pulsation with temperature-dependent visual molecular-opacity proxy | No detailed atmosphere, dust, wind or shocks |
| Delta Scuti / SX Phoenicis | Two incommensurate radial modes | Prescribed frequencies; no mode excitation or metallicity-dependent spectrum |
| Beta Cephei / SPB | Two quadrupole surface temperature/radius modes with viewing-angle cancellation | Small-perturbation surface normals; no interior eigenfunctions |
| RV Tauri | Alternating deep/shallow cycles; two pulsations per formal period | Prescribed alternation |
| Semiregular giant | Two-mode radial beating | Deterministic, without stochastic convection |
| Rotating spotted star | Cool rotating cap with angular size, latitude and temperature contrast | Fixed spot; no differential rotation or faculae |
| Dwarf nova | White dwarf, companion and optically thick disk heated by prescribed accretion-rate outbursts | Thin steady annuli; no time-dependent disk-instability solver |
| Nova | Single luminosity eruption with an expanding/cooling effective photosphere | Prescribed photosphere; no nuclear or ejecta hydrodynamics |
| Flare star | A localized hot cap with rapid rise and exponential decay | One observer-facing thermal region; no magnetic reconnection model |

These are representative educational models, not fitted examples or complete implementations of every subtype. The classification choices follow the [AAVSO variability classifications](https://vsx.aavso.org/index.php?view=about.vartypes) and [observing manual](https://www.aavso.org/sites/default/files/EnglishManual-2013.pdf). Roche surface integration follows the general approach described in [PHOEBE's surface-model paper](https://arxiv.org/abs/1609.08135); CAst implements a much simpler independent model. Its prescribed disk response does not solve the [disk-instability equations](https://arxiv.org/abs/1910.01852).

**Surface integration.** Roche potentials use separation as the length unit and the mass ratio as the secondary potential weight. Polar filling factors define separate EB surfaces; EW fill-out interpolates between the inner and first outer critical potentials. Temperature scales with effective gravity raised to the selected exponent. EB irradiation adds absorbed companion flux once, without iterating reflection. A CPU depth buffer integrates the visible mesh, including occultation, at 192 × 192 pixels; the display uses depth-tested geometry from the same mesh and retains HDR bloom. Single-star surface patterns integrate projected triangles. Radial pulsators use band surface flux times instantaneous radius squared, with bolometric flux proportional to R²T⁴.

The band calculation interpolates a cached 96-bin Planck integral. The Mira opacity proxy modifies visual-band brightness only. Disk annuli use T⁴ = 3GMṀ/(8πσr³) × (1 − √(Rin/r)), with a 1,000 K floor. Eruptions use a peak-normalized fast-rise/exponential-decay driver. Only dwarf-nova events recur; nova and flare times never wrap at a cycle boundary.

**Time and normalization.** Roche periods follow Kepler's law. Single-star periods are independent controls; their masses are reference metadata. Multimode examples use the primary period as the playback time scale, while eruptive examples use an editable time window in days. RV Tauri's formal period spans two pulsations. Roche phase zero places the line of centers in the sky plane before the orbital-epoch rotation; the original EA phase convention is unchanged. Relative flux is normalized to the unperturbed star, the quiet accretion system at its reference orientation, or the Roche system at phase zero. Constant third light is defined relative to that baseline. It is not a cycle-average normalization.

Predictions for new models run in the background and use 301 points over the visible window. This cadence and the surface resolution are intended for exploration: narrow flares, short orbital eclipses within long eruption windows, and long exposure windows can be undersampled. Reduce the time window to inspect rapid events. CSV models use 1,001 points. None of the examples is a substitute for a converged precision fit.

For detached EA models, periastron intersection invalidates a system and disables recording/export. Large radius relative to the approximate Roche lobe produces a tidal-distortion notice. The estimate is diagnostic, especially for eccentric or asynchronous systems; non-intersection does not prove a system is physically detached.

The original detached EA model omits Roche distortion, ellipsoidal variation, gravity darkening, irradiation, mass transfer, starspots, atmospheric lines, extinction, Doppler beaming, light-travel delay, and instrumental systematics. It explains eclipse geometry and approximate predictions rather than providing precision fits to observations. Very narrow events and long exposures require convergence studies.

## Save, load, and export

The **Observe** tab uses CAst file dialogs:

- **Save system / Load system** use versioned `.starlab` JSON for the family, physical parameters, variability controls, noise and comparison flux. Loading validates before replacing the current model. Legacy `.binary` files still load; detached models saved with a `.binary` extension retain the exact PhysicsEngine format. Extended models saved under any extension use CAst JSON and cannot be loaded by PhysicsEngine. Unicode filenames are supported.
- **Export model CSV** saves 1,001 samples across a complete cycle, or from time zero through at least one time window for nonperiodic models. Headers include physical parameters and, for new models, family, approximation scope and integration resolution. Columns contain relative flux, magnitude change and irradiance. Original EA component columns retain visible fractions; new-model component columns are contributions to total reference-normalized flux.
- **Export observations** saves the ideal curve and an additional `-observed.csv` with unwrapped cycles, time in days, folded phase, ideal flux, measured flux, and differential magnitude. The header records noise and comparison flux. Nonperiodic exports preserve elapsed time and omit a misleading folded-phase column. Both files replace existing files at those paths.

## Building and validating the embedded engine

On Windows, build with `scripts/build_binary_light_native.ps1` (CMake and MSVC). The resulting `photometry_app/native/cast_stellar_lab.dll` is included by the alpha-review packaging specification. Updating the vendored source uses `python scripts/vendor_binary_light.py C:/path/to/PhysicsEngine`; the manifest records the original source hashes. PhysicsEngine is not needed at runtime.

The CMake target is portable; on other platforms build `native/binary_light` and place `libcast_stellar_lab.dylib` or `libcast_stellar_lab.so` in `photometry_app/native`. Only the Windows binary has been built and visually verified here. OpenGL 3.3 is required for stellar rendering.

Run `python -m pytest tests/test_binary_light.py tests/test_stellar_library.py tests/test_mode_launcher.py` for numerical, file, recording, and control coverage. `python scripts/smoke_binary_light.py --main` verifies the GPU scene, keyboard playback/pause, mode visibility, all five tabs, and the full CAst window, saving screenshots under `build/binary_light_smoke`. `python scripts/verify_binary_light_parity.py C:/path/to/PhysicsEngine` compares all six presets and four band/exposure configurations with the original executable, including its acceptance of CAst's system files and identical CSV metadata.

`python scripts/smoke_stellar_library.py` loads and renders all 16 new Library entries, checks playback, and saves surface/panel captures in `build/stellar_library_smoke`. Numerical tests cover Roche equipotential closure, resolution convergence, R²T⁴ scaling, spot viewing angle, multimode beating, nonrepeating events, exposure averaging and file round-trips.
