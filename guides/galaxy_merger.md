# Galaxy Merger

## Introduction

**Galaxy Merger** is the PhysicsEngine galaxy merger lab running inside CAst. Two disk galaxies fall toward each other. Each has a dark halo, a central bulge, a stellar disk, a cold gas disk that forms new stars, and a hot gas atmosphere. The mode integrates every particle under gravity, records the encounter, and lets you scrub through it as starlight and dust, a density volume, or individual particles.

The simulation, the scenes, the optical renderer, and the exports are the lab's own code. CAst supplies the control panel, the file dialogs, and an optional Milky Way and star background.

### What you can do with this mode

- **Run an encounter.** Pick one of the lab's built-in mergers, set the particle budget, and press **Run**. **Stop** keeps what has been computed and **Continue** carries on from there.
- **Change the physics.** Edit the time step, gravity, softening, and every galaxy's mass, size, gas, star formation, tilt, position, velocity, and random seed before a run.
- **Watch it as light.** The optical picture reconstructs starlight, dust lanes, young blue stars, and H-alpha and OIII emission from the particles. Camera compositions frame the disks face-on, edge-on, at first passage, or after it.
- **Add a sky.** Put CAst's Milky Way and a field of stars behind the galaxies. The stars can be random or real Hipparcos stars.
- **Save and reload.** Recordings save as `.traj` files and optical settings as `.look` files.
- **Export.** Save a single PNG, a numbered PNG sequence, or an MP4.

### What this is not

- Gravity is a direct sum over particle pairs with softening. It is accurate but slow for large particle counts, so the higher resolutions take minutes to hours.
- Gas is a simplified model with pressure, cooling, and star formation. It is not a full hydrodynamics code.
- The optical picture and the emission lines are illustrative reconstructions. Colors and brightness are not calibrated.
- The units are simulation units with G = 1, not years or kiloparsecs.
- The view needs OpenGL 3.3 and the Windows build of CAst. If either is missing, the mode says so and the other modes keep working.

---

## How It Works

### Step 1: Open the mode

Use one of these:

- On the mode picker, open **Simulations** and click **Galaxy Merger**.
- From any mode, choose **Mode > Galaxy Merger**.
- Choose **Mode > Choose Mode...** to return to the mode picker.

No image folder is required. The mode opens with the **Galaxy merger** scene loaded and not yet run.

### Step 2: Choose a simulation

The **Simulation** list holds the lab's built-in encounters:

| Simulation | What it shows |
|------------|---------------|
| Galaxy merger | Seeded spiral arms, initial young stars, continuous star formation, and emission regions |
| Seeded merger | An earlier seeded-arm model with random star formation |
| Retrograde merger | One disk spins against the orbit, which changes the tidal tails |

**Load scene** opens any two-galaxy `.scene` file, such as the unseeded `galaxy_merger.scene` in CAst's `photometry_app\native\galaxy_merger` folder.

### Step 3: Run

Under **Parameters**, choose a **Resolution**:

- **Minimal**: 400 disk particles per galaxy, a short preview.
- **Optimal**: 2,400 disk particles per galaxy, the desktop budget.
- **Fine**: 4,800 disk particles per galaxy. Allow about four times the Optimal run time.
- **Ultra**: 8,000 disk particles per galaxy and a thinner disk.

Resolution changes particle counts only, not galaxy mass, geometry, time step, or duration. Press **Run**. The progress bar and status line show the step and the estimated time left. You can watch the recording grow while it runs.

**Stop** pauses between steps and keeps the partial recording. **Continue** resumes from that point. Changing any parameter clears the checkpoint, and the next **Run** starts over.

### Step 4: Watch and move

Under **Playback**, **Play** or **Pause** the recording and drag **Time** to scrub. **Speed** is simulation time per second. Playback reads saved snapshots and never changes the run.

Click the view, then use the mouse and keys:

| Input | Action |
|-------|--------|
| Middle or left drag | Orbit |
| Shift + drag, or right drag | Pan |
| Wheel | Zoom |
| Space | Pause or play |
| F | Frame the whole encounter |
| F1 | Hide or show the controls |

### Step 5: Save or export

**Save recording** writes a `.traj` file and **Load recording** opens one for playback. A recording keeps the playback data, including stellar populations, formation history, and remaining gas, but not a checkpoint to continue the run.

Under **Export**, set **Width**, **Height**, **FPS**, **Format**, and the **From** and **To** times, then click **Export**. Leave the output folder as it is to save in `Pictures\CAst Galaxy Merger`, or click **Browse**. **Save frame** writes a single PNG of the current view. Exports render the current camera and appearance without the panel. MP4 export uses the FFmpeg that ships with CAst.

---

## Settings Reference

### Parameters

- **dt**, **Steps**, **Sample every**: the time step, the number of steps, and how often a snapshot is recorded.
- **G** and **Softening**: the gravitational constant and the distance below which gravity is smoothed.
- **Galaxy 1** and **Galaxy 2** (named after the scene's galaxies):
  - Particle counts for the disk, gas, and hot gas.
  - Mass, scale, and cutoff for the halo, disk, and bulge.
  - Disk height, velocity dispersion, and population color variation.
  - Gas mass, height, sound speed, cooling time, star formation time, and feedback.
  - Spiral arm count and strength, inclination, node, reverse spin, center, velocity, and seed.
  - Display radius and emission for each component.
- **Body 1** to **Body 8**: point masses written into the scene, with mass, position, velocity, display radius, and emission.

### Appearance

- **Axes**: draws the x, y, and z axes.
- **Picture**: **Optical**, **Illustrative volume**, or **Particles**. All three show the same simulation.

**Optical**

- **Frame current light**, **Face-on disk**, **Edge-on disk**, **First passage**, **After passage**: camera compositions. The disk views use the first galaxy.
- **Exposure** and **Bloom**.
- **Structure smoothing**, **Fine stellar structure**, **Arm and stream definition**: how sharply the starlight follows the particles.
- **Dust lane structure**, **Dust**, **Reddening**: foreground dust, and how much it removes blue light first.
- **Nebula emission**: overall brightness of the gas glow.
- **Stellar populations and emission lines**: separate lifetimes for blue starlight, ionizing light, and OIII sources, plus young-light boost, association size, ionizing reach, H-alpha and OIII strength, nebula size, and both line colors. **Broadband look** subdues the emission and **Emission emphasis** strengthens it.
- **Stellar texture**: fine grain in the starlight.
- **Save look** and **Load look**: store or restore these optical settings as a `.look` file. CAst's `galaxy_merger` folder includes `broadband.look` and `nebular.look`.
- **Show dynamical halo** and **Show hot atmosphere**: illustrative overlays for the dark halo and hot gas.

**Illustrative volume**

- **Resolution**: Soft, Coarse, Standard, or Fine density field.
- **Field brightness**, **Field contrast**, **Field heat**.

**Particles**

- **Point scale**, **Light smoothing**, **Soften**, **Soften brightness**: the size of each particle and the smooth glow under them.
- **Dust**, **Reddening**, **Gas glow**.
- **Trail**: how much of each point mass's path is drawn.
- **Key light** and **Fill light**: lighting for point masses drawn as spheres.
- Group colors: one swatch per galaxy component, such as **left.disk** or **right.young**. Click a swatch to recolor that group.

### Background

The background is a CAst addition, not part of the lab or the simulation. The merger has no real place in the sky, so treat it as a backdrop. It shows in the view, in **Save frame**, and in exports. Both layers are off until you turn them on.

- **Milky Way**: CAst's all-sky Milky Way image, the same one Sky Atlas uses (ESA Gaia Sky 2020).
  - **Sky brightness**: how bright the band is.
  - **Sky black level**: how much of the dark sky is pushed to black.
  - **Sky saturation**: 0 is gray and 1 is the image's own color.
- **Stars**: a field of points behind the galaxies. Without a catalog, the mode draws 90,000 random stars, crowded toward the Milky Way band, with realistic colors.
  - **Real catalog stars**: uses about 99,000 Hipparcos stars from the catalog Sky Atlas downloads, placed and colored as they appear in the sky. If the catalog is missing, click **Download star catalog**. Random stars are shown until it finishes.
  - **Star brightness** and **Star size**: brightness and size of every star.
  - **Faintest star**: hides stars fainter than this V magnitude.
- **Sky rotation** and **Sky tilt**: turn the backdrop. **Sky tilt** stands the Milky Way plane up, and **Sky rotation** turns its center around the vertical axis. The defaults put the southern Milky Way, around Crux and Centaurus, behind the opening view. At zero rotation and tilt, the band lies flat in the merger's orbital plane.

The galaxies are drawn first and the sky is added with a screen blend, so it shows through the dark parts of the picture without covering the galaxies. To keep faint stars visible, star brightness changes by 1.585 times per magnitude instead of the true 2.512 times.

### Export

- **Preset** fills in common sizes. **Width** and **Height** go up to 8192. MP4 needs even dimensions.
- **FPS** is frames per simulation time unit, independent of playback speed.
- **Format**: PNG sequence or MP4.
- **From** and **To** select the simulation time range.
- **Stop export** stops early. For a PNG sequence, **Continue** resumes the same export. A stopped MP4 is closed as a finished, shorter video.
