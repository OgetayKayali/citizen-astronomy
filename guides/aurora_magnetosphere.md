# Aurora & Magnetosphere Lab

## Open and compare

Choose **Simulations → Aurora & Magnetosphere Lab**, or its Mode-menu entry. No input files or network access are needed. The bundled Earth texture and OpenGL 3.3 renderer use CAst's existing Qt/NumPy/PyOpenGL stack. For development: `python -m photometry_app.ui.magnetosphere_view`.

Pause at **45 model minutes**, select **Pressure comparison**, then switch between Quiet Solar Wind and the northward/southward CME presets. Time, camera and graph scales are preserved.

| At 45 model minutes | Quiet | Northward CME | Southward CME |
|---|---:|---:|---:|
| Near-Earth speed, km/s | 400 | 800 | 800 |
| Proton density, cm⁻³ | 5 | 18 | 18 |
| IMF Bz, nT | +3 | +15 | −15 |
| Proton dynamic pressure, nPa | 1.338 | 19.269 | 19.269 |
| Displayed magnetopause nose, R_E | 10.973 | 7.352 | 5.826 |
| Displayed bow-shock nose, R_E | 14.367 | 9.561* | 9.561* |
| Normalized dayside driver C | 0 | 0 | 5.241 |
| Stored-flux teaching proxy F | 0 | 0 | 0.913 |
| Monitor-to-input delay, minutes | 4.247 | 2.124 | 2.124 |

*The CME bow-shock estimates extrapolate beyond the paper's recommended 0.6–11 nPa pressure range. The UI reports this explicitly. These are teaching scenarios, not observed events or forecast validation. Northward C = 0 means this particular dayside coupling channel is zero under By = 0, not that all energy transfer vanishes.*

Both CMEs compress the magnetosphere through pressure. Southward IMF further reduces the Shue nose and increases flaring, while loading the tail proxy. The pressure-only bow-shock fit has no IMF term, so its noses match at equal pressure. At 100 minutes, the input has recovered but the southward case retains tail memory and release.

## Controls and reading the scene

Drag to orbit; Shift-drag or right-drag to pan; wheel to zoom. **Dayside** is the initial framing for a more visible Earth. **Earth**, **Overview**, **Magnetotail**, **Polar view**, **Pressure comparison**, and **Topology · schematic** provide other views. Earth remains one R_E in radius. Night-side fill is a display aid, not emission.

The coordinates are GSM-like: +X toward the Sun, +Z north, plasma moving toward −X. Lengths are Earth radii (6371.2 km). The Sun arrow gives direction, not position. The axisymmetric bow-shock fit is aligned to +X; its original aberrated-GSE frame is approximated without orbital aberration.

Pause, reset, adjust playback speed or scrub. Scrubbing and reset pause; hidden modes stop their timer. Parameter changes rebuild the complete scenario in a background worker, rather than injecting a change at the current cursor. Quiet mode applies controls as constant wind. Duration is in model minutes, from upstream leading-edge onset to recovery onset.

- **Speed:** pressure ∝ v²; Newell driver ∝ v^(4/3); higher speed shortens transport delay.
- **Density:** pressure ∝ n; no direct density term in this Newell driver.
- **Bz:** controls the Shue fit and southward coupling independently of proton pressure.
- **Tilt:** rotates dipole geometry and footpoints. It is absent from the chosen axisymmetric boundary fits and coupling formula; seasonal/asymmetric response is omitted.

The HUD separates the **+40 R_E monitor** from the delayed **+24 R_E near-Earth input**. The latter is an unshocked input plane outside all candidate bow-shock noses in the control range, not Earth's surface, the magnetopause or an L1 spacecraft. It drives the boundary response. Relaxed positions and instantaneous empirical equilibrium targets are reported separately.

The graph shows dashed upstream pressure, solid near-Earth pressure, and relaxed magnetopause/bow-shock noses. Pressure and distance have separate labeled axes; matching heights do not imply equal quantities. The pressure scale expands when custom inputs exceed the default 30 nPa ceiling; the right scale stays at 0–24 R_E. Invalid bow-shock segments are omitted.

The side panel reports dimensionless C and F, plus loading and release in proxy units per model minute. Collapse sections or scroll to reach layer and quality controls. Line color identifies structures, not field strength. Particle brightness conveys local prescribed density qualitatively; seed counts and brightness are not calibrated plasma diagnostics.

## Calculations and empirical models

### Pressure and dipole

`P = mp n v²`, with mp = 1.67262192369 × 10⁻²⁷ kg, cm⁻³ → m⁻³, km/s → m/s and Pa → nPa. No factor 1/2 is used. Helium, thermal pressure and magnetic pressure are omitted from this **proton dynamic pressure**.

The vacuum field is `B = 31200 [3 r_hat (m_hat · r_hat) − m_hat] / r³` nT, with the moment pointing southward and tilted in the XZ plane. Undeformed integral curves satisfy `r = L sin²(theta)` and have two Earth footpoints. External deformation is a geometric map, not tracing a disturbed vector field.

### Magnetopause: Shue et al. (1998)

```
r0 = (10.22 + 1.29 tanh[0.184 (Bz + 8.14)]) P^(-1/6.6)
alpha = (0.58 − 0.007 Bz) (1 + 0.024 ln P)
r(theta) = r0 [2 / (1 + cos theta)]^alpha
```

P is in nPa, Bz in nT, r in R_E and theta measured from +X. These are equilibrium parameters, not a dynamic MHD model. CAst omits cusps, By, tilt dependence and dawn/dusk asymmetry, and truncates the divergent tail at X = −60 R_E. Extreme combinations need not be accurate predictions. [Shue et al., 1998, equations 10–11](https://doi.org/10.1029/98JA01103).

### Bow shock: Jelínek et al. (2012)

```
R_BS = 15.02 P^(-1/6.55)
lambda = 1.17
rho² = 4 R_BS (R_BS − x) / lambda²
rho = sqrt(y² + z²)
```

The surface follows by eliminating sigma from the generalized coordinates: `sigma² = sqrt(x² + lambda² rho²) + x = 2 R_BS`. It is an independent parabola, not a scaled Shue surface. Recommended use is dayside, low latitude, 0.6–11 nPa. Mach number, IMF orientation and tilt are absent. [Jelínek et al., 2012, sections 3–4 and conclusion](https://doi.org/10.1029/2011JA017252).

CAst draws only x ≥ 0: independently fitted boundaries should not be extended indefinitely into a shared tail. It flags pressure extrapolation and retains the high-latitude/Mach caveats inside that range. Dayside boundary ordering is tested at control extremes.

**Applicability check:** `vA,min = |Bz| / sqrt(mu0 mp n)` bounds Alfvén speed from below, hence `v / vA,min` bounds Alfvén Mach number from above. A bound ≤ 1 excludes the assumed superfast shock: CAst hides it, displays N/A and warns that the fits are unreliable. A larger bound cannot establish superfast flow without temperature and full IMF. This is a proton-only exclusion check, not shock evolution or an Alfvén-wing simulation. [Lugaz et al., 2016](https://www.nature.com/articles/ncomms13001).

### Normalized Newell driver

Newell's functional form is `v^(4/3) BT^(2/3) sin(theta_c/2)^(8/3)`, with IMF clock angle theta_c and BT = sqrt(By²+Bz²). For By = 0, CAst uses:

```
C = (v / 400)^(4/3) [max(−Bz, 0) / 5]^(2/3)
```

C = 1 at 400 km/s and −5 nT. This dimensionless normalization is not an absolute reconnection voltage, power or flux-transfer rate. [Newell et al., 2007](https://doi.org/10.1029/2006JA012015).

## Shared transport and response clock

At 1×, one playback second equals **60 physical seconds**, displayed as one model minute. Playback controls multiply this speed. Input, travel, response, deformation and Earth texture share this clock; pause/scrub reproduce the same state. The texture has a 1440-model-minute period. Geographic orientation and fixed dipole tilt do not represent a calendar date.

The prescribed source history begins at minute 12:

1. Speed/density ramp from 400 km/s and 5 cm⁻³ to the selected plateau over 2 minutes.
2. A sheath adds up to 25% of the plateau density, fading between minutes 16 and 20.
3. Magnetic-cloud Bz ramps from +3 nT to the selection between minutes 18 and 22.
4. At minute `12 + duration`, all changes return to initial conditions over 12 minutes.

These smooth profiles are chosen teaching inputs, not empirical CME fits. Lower-than-background selections can produce expansion; the explanation reflects this. Supported speed 250–1500 km/s, density 1–40 cm⁻³, Bz −25 to +20 nT, tilt ±30° and duration 20–120 minutes are numerical exploration limits, not calibration claims. The sheath can reach 50 cm⁻³; pressures span approximately 0.10–188.17 nPa.

Transport uses integrated displacement in R_E:

```
D(t) = integral v_source(t) dt / R_E
D(t_source) = D(t) − (40 − x)
wind(t, x) = prescribed_source(t_source)
```

The implementation converts minute-valued times to seconds. Negative source times use initial steady wind. This is **uniform, time-varying plug advection**: every marker translates at current source speed, while carrying retarded wind labels. It avoids characteristic crossing but is not ballistic parcel dynamics, mass-conserving compressible flow, a shock solution or a sheath solver. Local carried speed and common marker translation speed differ during transients. Arrival times are not forecasts.

The same D(t) places the leading-edge sheet, advects plasma/IMF seeds and samples a 256-point spatial wind texture for density and IMF polarity. The sheet fades at the +24 input region. There is no independent front or IMF clock. Paths deflected around the obstacle remain schematic.

Near-Earth input supplies equilibrium r0, alpha and R_BS. Displayed values follow `dq/dt = (q_eq − q)/2 min`. This chosen relaxation stands in for unresolved response/sheath transit, not a fitted motion law. The default 0.1-minute grid uses exact exponential updates for constant step targets, followed by interpolation. Wind parameters are sampled together and pressure recalculated from them.

## Tail memory, topology and reconnection

The bounded teaching proxy F follows:

```
loading = C (1 − F) / 10 min
release = F / 20 min
dF/dt = loading − release
```

Steady input gives `F_eq = C/(C+0.5)`. Steady scenarios initialize there; events start from F = 0. Exact constant-driver exponential steps preserve [0,1]. Once C vanishes, F decays on a chosen 20-minute timescale. Loading/release can balance in a sustained event; they do not identify substorms. Saturation and timescales are uncalibrated; F is not Earth's open magnetic-flux fraction.

F drives tail stretch and its region marker. The dayside marker follows C/(1+C), switching off when its driver vanishes even if the tail remains loaded. Neither marker locates a solved reconnection site.

The topology preset shows:

- **Closed terrestrial lines, cyan:** two Earth footpoints. Deformation confines them within the magnetopause. No lines are cut or reconnected.
- **Open examples, pink:** one Earth footpoint, continuing to the outer tail viewing boundary. Fixed cubic curves/counts illustrate connectivity, not field tracing, magnetic flux or opening of individual closed lines.
- **Interplanetary lines, gold:** no Earth footpoint. Arrows show transported Bz polarity. Draping is illustrative, not flux-frozen evolution.

Southward IMF favors dayside opening; convection can transport flux into the tail where closure and energy release can occur. C, F and the examples explain that sequence. CAst does not solve induction, force balance, divergence-free disturbed fields, heating, reconnection electric fields or flux conservation. Projected curve crossings are not reconnection.

Unlike CAst, CCMC-hosted SWMF/BATSRUS solves 3D MHD and can couple magnetospheric/ionospheric components. Its separation of upstream input from the simulation boundary informed this increment; CAst does not run or reproduce SWMF. [NASA CCMC SWMF](https://ccmc.gsfc.nasa.gov/models/SWMF~2023/).

## Preparation for aurora

`Timeline.auroral_driver(time)` returns a typed immutable handoff: physical elapsed seconds, near-Earth wind, normalized coupling, storage/release proxies and tilt. **Energy flux (mW/m²) and characteristic energy (keV) remain None.** Brightness, latitude, oval position and precipitation cannot be inferred from F alone.

A later stage should select and validate a precipitation model before drawing aurora. OVATION Prime is an example of statistical precipitation organized by magnetic latitude/local time and solar-wind inputs. It is not implemented here. [NASA CCMC OVATION Prime](https://ccmc.gsfc.nasa.gov/models/Ovation-Prime~2.3/).

A future adapter needs model/version provenance, UTC/coordinate transforms, required full wind inputs, magnetic-local-time/latitude grids, species, energy flux and characteristic energy with units, hemisphere handling and validity limits. Optical emission needs a separate atmospheric/altitude response and viewing geometry. Acceptance should include quiet/disturbed reference cases, coordinate/unit checks, hemispheric consistency, published-output comparison and uncertainty labeling. Numerical consistency here is insufficient validation for that future model.

## Implementation and verification

`core/magnetosphere.py` owns pure calculations, deterministic history, applicability flags, topology seeds and the precipitation contract. `ui/magnetosphere_gl.py` retains static VBOs, shader deformation and existing camera controls, adding a small updated wind texture. `ui/magnetosphere_view.py` supplies readouts, cached graph paths and coalescing background updates; stale revisions cannot overwrite newer settings. Transparent layers read opaque Earth depth without writing depth; blending is illustrative.

Reproduce with Python 3.11+ and the project dependencies:

```
python -m pytest tests/test_magnetosphere.py tests/test_magnetosphere_ui.py tests/test_mode_launcher.py tests/test_settings.py tests/test_assistant_guides.py -q
python scripts/smoke_magnetosphere.py --output Exports/magnetosphere-increment2
```

Tests cover units and independent formula checks; dayside ordering at extremes; tilt/footpoints; equal-pressure/different-coupling cases; causal delay and spatial profile parity; shared front travel; sheath/cloud ordering; storage balance, recovery and time-step convergence; replay; applicability; unset precipitation fields; Qt controls and application integration. These validate implementation/internal consistency, not observed accuracy.

The real GPU smoke captures quiet/north/south, arrival, compression, recovery, topology and Earth views; exercises orbit, layer toggles, pause/replay, background edits and hidden-mode behavior; and reports GL errors/frame intervals to `Exports/magnetosphere-increment2/result.json`. Short runs are not sustained benchmarks. The full application regression suite and frozen EXE rebuild are outside this increment.

Verification on 2026-10-23: **128 tests and 5 subtests passed** across the five modules above. The final desktop smoke reported **no GL errors**, **62.7 FPS** average and **18.1 ms** 95th-percentile frame interval during a 3.5-second sample on an NVIDIA GeForce RTX 4070 Ti SUPER, at 1440 × 980 logical pixels / 2160 × 1470 captured pixels, with 48 terrestrial lines, the topology examples and balanced plasma enabled. An earlier cached-graph run averaged 51.4 FPS; short measurements vary with desktop load. Both preset comparisons and the unsupported-shock view were visually inspected.

The old workspace .venv launcher references a missing interpreter. Verification uses Codex's bundled Python 3.12 with existing workspace site-packages and TEMP/TMP in a writable Exports subfolder; application dependencies and the virtual environment are unchanged.

Earth texture: [NASA Blue Marble: Next Generation](https://science.nasa.gov/earth/earth-observatory/blue-marble-next-generation/base-topography/), reduced to 2048 × 1024; provenance in `photometry_app/data/earth_blue_marble.txt`. [NASA SVS 4188](https://svs.gsfc.nasa.gov/4188) and [4189](https://svs.gsfc.nasa.gov/4189) provide visual context; CAst does not claim their simulation fidelity.

