# Cluster Flow

## Introduction

**Cluster Flow** is the PhysicsEngine Cosmic Flow lab running inside CAst. It simulates gas in a cube of space and shows it as a glowing volume. It has two models:

- **Cluster flow**: a network of gas filaments falling onto the dense knots of a galaxy cluster. Fixed dark halos pull the gas in, the gas cools, and the two central halos fire black-hole outbursts that heat it and push it out.
- **Cosmological volume**: a piece of the expanding universe. Small density ripples grow into filaments and clumps under the gravity of live dark matter and gas while space expands.

The simulation, the renderer, the `.cflow` recordings, and the MP4 export are the lab's own code. CAst supplies the control panel and the file dialogs.

### What you can do with this mode

- **Watch it evolve live.** The simulation runs as soon as the mode opens. Change outbursts, cooling, and accuracy while it runs.
- **Build your own scene.** Choose the model, the cube size, the gas resolution, the cosmological parameters, and the initial gas conditions, then apply and restart.
- **Look inside.** Show gas density, gas speed, temperature, or compression. Cut a thin slice through the cube, fly the camera into it, and let it orbit on its own.
- **Bake and replay.** Compute a run once into a `.cflow` file, then scrub and replay it without recomputing anything.
- **Export.** Save a PNG of the view, or render a baked run as an MP4.

### What this is not

- Both models are simplified visualization models, not research codes. Cluster halos are fixed analytic forces, and cluster mode has no gas self-gravity.
- The cosmological model leaves out radiation, neutrinos, baryon acoustic oscillations, star formation, radiative cooling, and magnetic fields.
- Cluster mode uses simulation code units, not kiloparsecs, years, or Kelvin. Cosmological mode uses comoving Mpc and billions of years.
- Colors show the selected field. They are not calibrated X-ray or optical images, and **Compression / shocks** highlights converging flow, not a measured Mach number.
- The view needs OpenGL 3.3 and the Windows build of CAst. If either is missing, the mode says so and the other modes keep working.

---

## How It Works

### Step 1: Open the mode

Use one of these:

- On the mode picker, open **Simulations** and click **Cluster Flow**.
- From any mode, choose **Mode > Cluster Flow**.
- Choose **Mode > Choose Mode...** to return to the mode picker.

No image folder is required. The mode opens with the cluster scene running. While you are in another mode, the live simulation pauses, and it carries on when you come back.

### Step 2: Watch and move

**Pause** and **Restart** sit at the top of the panel. **Restart** rebuilds the starting state from the current Scene settings. The line below them shows the simulated time and the grid, and the bottom of the panel shows the step time and frame rate.

Click the view, then use the mouse and keys:

| Input | Action |
|-------|--------|
| Left drag | Orbit |
| Right or middle drag, or Shift + left drag | Pan |
| Wheel | Zoom, including into the volume |
| Space | Pause or play |
| H | Hide or show the controls |

### Step 3: Change the scene

Under **Scene**, pick a **Model** and edit the settings. Scene changes wait until you click **Apply scene & restart**, and **Scene changes pending** shows until you do. Physics changes take effect at once.

### Step 4: Bake a recording

Open **Bake**, set **Evolution duration**, **Cached frames**, **Cached volume grid**, and **Cached tracers**, and click **Bake from start...** to choose a `.cflow` file. The bake starts from the beginning with the current Scene and Physics settings, including unapplied scene changes, and runs as fast as the computer allows. The estimate line shows the file size before you start. **Cancel bake (keep frames)** stops early and keeps what is done.

When the bake finishes, the recording opens for replay. **Open cache...** opens an earlier `.cflow` file.

### Step 5: Replay and export

During replay, drag **Cached frame** to scrub, set **Playback frames / second**, and turn **Loop replay** on or off. The camera and appearance stay adjustable. **Scene** shows the settings the recording was made with, and **Return to live** goes back to the live simulation.

To make a video, choose **Movie resolution** and **Movie FPS**, then click **Export MP4...**. Every cached frame becomes one video frame, so the video lasts frame count divided by FPS. Enabled camera rotations follow video time. **Stop export (keep video)** finishes a shorter video. MP4 export uses the FFmpeg that ships with CAst.

**Save image...** in **View** saves the current view as a PNG at the size of the view. Both dialogs start in `Pictures\CAst Cluster Flow`.

---

## Settings Reference

Every control has the lab's own explanation as a tooltip. Hover over it to read it.

### View

These change only the picture, never the simulation.

- **Camera motion**: **Horizontal angle** and **Vertical angle** in degrees, their rotation speeds in degrees per second, and **Rotate horizontally** and **Rotate vertically**. Editing an angle stops that rotation. The vertical orbit goes over the poles. **Stop camera motion** holds the current angles.
- **Display field**: **Gas density**, **Gas speed**, **Temperature**, or **Compression / shocks**.
- **Exposure**: overall brightness.
- **Flow tracers** and **Tracer brightness**: massless markers carried by the gas. They are hidden in compression mode.
- **Slice thickness** and **Slice position**: show only a slab of the cube along its Z axis.
- **Ray samples**: rendering quality through the volume.
- **Reset camera**: restores the default view and stops rotation.
- **Explore volume**: **Camera distance** and **Focus X**, **Focus Y**, **Focus Z**, the orbit center from -1 to +1 across the cube.

### Physics

- **Evolution pace**: requested simulated time per real second.
- Cluster model only:
  - **Dark halo gravity**, **Cooling rate**, **Cooling temperature floor**.
  - **Outbursts**: **Automatic outbursts**, **Outflow strength**, **Interval**, **Injection radius**, **Heating**, **Velocity kick**, **Jet collimation**, and **Trigger outburst** for one outburst now.
- **Timestep & solver**: the **Preset** sets accuracy from **Fast / more diffusive** to **Fine timesteps**. **Second-order transport**, **Courant limit**, and **Maximum timestep** can be set by hand, which selects **Custom**. **Last step** shows the step the solver actually took.

### Scene

- **Model**: **Cluster flow** or **Cosmological volume**.
- Cluster model:
  - **Cluster cube size**: 2 is the original scene with 38 clumps. Larger cubes add more halos and filaments at the same density.
  - **Baryonic gas density**, **Initial temperature**, **Initial inflow**, **Velocity perturbations**, **Filament width**, **Dark halo softening**.
- Cosmological model:
  - **Comoving cube size (Mpc)**, with the resulting cell size shown below it.
  - **H0**, **Dark matter / Omega_dm**, **Baryons / Omega_b**, **Dark energy / Omega_de**. The curvature line shows the result, and **Set flat geometry** makes it zero.
  - **Dark energy w**, **Starting redshift**, **sigma8**, **Spectral index ns**.
  - **Gravity mesh / DM particles**: mesh cells and dark-matter particles per side.
  - **TNG-like cosmology preset**: restores the default cosmology and keeps the cube size.
  - **Initial gas temperature / 10,000 K**.
  - **About cosmology**: a short note on what the model includes.
- Both models: **Gas grid** (cells per side, with the memory use shown below), **Flow tracer count**, and **Random seed**.

### Bake

- **Evolution duration**: cluster code time, or billions of years after the starting redshift in the cosmological model.
- **Cached frames**: saved states, including the first and last.
- **Cached volume grid** and **Cached tracers**: how much detail each saved frame keeps. They are capped by the Gas grid and the tracer count.
- During replay, **Bake another simulation** opens these settings again.
