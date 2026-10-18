# Black Hole

## Introduction

**Black Hole** is the PhysicsEngine black hole light lab running inside CAst. It traces light around a nonspinning Schwarzschild or spinning Kerr black hole, evolves a gas disk around it, and draws what the bent light does to the disk and to the sky behind it.

The rendering, the gas simulation, camera paths, and exports are the lab's own code. CAst supplies the control panel and the background: the same Milky Way texture Sky Atlas uses (ESA Gaia Sky 2020), in place of the lab's ESO panorama. It is placed with the same orientation the lab gave its panorama.

### What you can do with this mode

- **Move the camera.** Drag to orbit, right-drag to look around, middle-drag to slide sideways, and scroll to change distance. W A S D fly, Q and E move down and up, and Shift moves faster.
- **Choose the spacetime.** Schwarzschild, or Kerr with spin from 0 to 0.9.
- **Watch the gas evolve.** The disk is an effective-potential fluid with pressure, viscous transport, and cooling, solved on the GPU, or on the CPU as a double-precision reference.
- **Change how the disk looks.** View emission, surface density, temperature, or flow tracers. Turn the volumetric layer on or off and set its thickness and opacity.
- **Save the view.** **Save PNG...** writes the viewport and a text file with every scene parameter.
- **Export a movie.** Eight camera presets, from a still view to a cinematic slingshot, as MP4 or a PNG sequence up to 4K.

### What this is not

- The fluid is two-dimensional. Vertical structure is reconstructed for drawing, not evolved. It is not a GRMHD simulation.
- Physical appearance includes gravitational redshift, Doppler shifts, and g⁴ brightness, but emission colors are illustrative and luminosity is not calibrated.
- The camera does not cross the horizon. It stops 0.02 rg outside it.
- The Milky Way is Earth's sky, not the sky around a real black hole.
- The view needs OpenGL 3.3 and the Windows build of CAst. If either is missing, the mode says so and the other modes keep working.

---

## How It Works

### Step 1: Open the mode

Use one of these:

- On the mode picker, open **Simulations** and click **Black Hole**.
- From any mode, choose **Mode > Black Hole**.
- Choose **Mode > Choose Mode...** to return to the mode picker.

No image folder is required.

### Step 2: Wait for the gas

When the mode opens, a box in the middle of the view reads **Preparing fluid history...** with a percentage. The lab builds 512 gravitational time units of gas history before playback, so that light which left the disk earlier can show the disk as it was then. On a recent GPU this takes a few seconds. The status line then reads **Fluid ready** and names the solver.

Changing the spin, outer radius, viscosity, stirring, fluid grid, or solver rebuilds the history in the background. If playback is faster than the gas can be prepared, the status reads **Buffering fluid / playback limited**.

### Step 3: Move

Click the view, then use the mouse and keys:

| Input | Action |
|-------|--------|
| Left drag | Orbit around the hole |
| Right drag | Look around without moving |
| Middle drag | Slide sideways and up or down |
| Wheel | Move closer or farther |
| W A S D, Q E | Fly; hold Shift for three times the speed |
| Space | Pause or play |
| R | Reset the scene |
| F1 | Hide or show the controls |

Movement slows near the horizon. **Smoothing** under **Camera** sets how quickly the view follows; zero is immediate.

### Step 4: Save a picture or a movie

**Save PNG...** asks for a file name, then saves the viewport at its current size together with `name.png.txt`, which records the spacetime, camera, disk, and sky settings.

Under **Video export**, choose a **Camera preset**, then **Resolution**, **Frame rate**, **Duration**, and **Format**. Turn on **Preview shot** to watch the path in the view before exporting. Leave **Output path** blank to save in `Pictures\CAst Black Hole`, or click **Browse output...**. Click **Export video / frames**. The view shows the frames as they render, and **Stop export (keep partial)** keeps what has been written. MP4 export uses the FFmpeg that ships with CAst.

---

## Settings Reference

### Top of the panel

- **Spacetime**: Schwarzschild or Kerr (rotating).
- **Spin**: dimensionless spin a*, 0 to 0.9, for a prograde disk. **Cinematic 0.6** restores the default spin and cinematic colors.
- **Horizon / ISCO**: the horizon radius and the innermost stable circular orbit, in rg (GM/c²).
- **Pause / Play** and **Reset scene**.
- **Physical appearance**: adds gravitational redshift, gas Doppler shifts, and g⁴ brightness. Off is **cinematic color / balanced brightness**.

### Camera

- **Distance** and **Horizon clearance**: two views of the same position. Clearance is the distance above the horizon.
- **Near horizon** puts the camera 0.1 rg above the horizon. **Look outward** turns it to face away from the hole.
- **Inclination**: measured from the disk axis; 90 degrees is edge-on. **Azimuth** and **Field of view**.
- **Pan**, **Tilt**, **Roll**: aim independently of position. **Center aim / level** resets them.
- **Smoothing** (seconds) and **Move speed** (rg per second).
- **Orbit camera** with **Orbit speed** in degrees per second.

### Visual effects

- **Milky Way background**: CAst's all-sky Milky Way, lensed by the black hole. Off shows procedural stars. When it is on, these controls appear:
  - **Sky brightness** and **Sky rotation**.
  - **Sky contrast**: above 1, dust lanes darken and the bright band stands out; below 1 flattens it.
  - **Sky saturation**: 0 is greyscale, 1 is the original colors, higher intensifies them.
  - **Sky black level**: clips the darkest part of the panorama to black, removing faint haze from empty sky.
  - **Sky warmth**: shifts the Milky Way toward blue or gold without changing its brightness.
  - **Star overlay**: adds sharp, colored stars over the Milky Way. Each star stays a point about one pixel wide wherever lensing moves it, and stars near the black hole brighten with the lensing magnification. By default the stars are decorative, at random positions. Its controls are **Star brightness**, **Star density** (how many stars, at unchanged brightness), **Star size**, **Star color** (0 is all white), and **Follow Milky Way** (0 spreads stars evenly; 1 crowds them into the bright galactic band).
  - **Real catalog stars**: replaces the random stars with real Hipparcos stars (about 99,000 to magnitude 9.5) at their true positions behind the Milky Way, colored by their measured B-V color. It reads the catalog Sky Atlas has already saved. If there is none yet, random stars stay on screen and **Download star catalog** fetches it into Sky Atlas's catalog folder. **Faintest star** hides stars fainter than the chosen magnitude; about 6.5 matches a dark naked-eye sky. Brightness is compressed for display: each magnitude steps by 1.585× instead of the real 2.512×, so faint stars stay visible. Lensed images that the black hole stretches too far fade out rather than being drawn wrong. Density and Follow Milky Way don't apply here, because real stars already follow the galaxy.
- **Space blending**: lets the lensed sky show through dim gas. Volumetric cinematic emission with the star background only.
- **Golden inner disk**: golden-yellow inner gas fading to orange outskirts.
- **Fine orbiting particles**: tiny hot glints in the disk's own color that ride the orbiting gas and flare like Iridium satellites, brightening quickly and then fading out. Some last much longer than others. They are purely decorative and are not simulated particles. When they are on, these controls appear:
  - **Particle amount**: how many glints flare at once. Brightness stays the same.
  - **Particle brightness**: peak brightness of each glint relative to the gas around it.
  - **Particle size**: scales every glint. Glints never shrink below about half a screen pixel.
  - **Size variety**: 0 makes all glints the same size; higher values mix tiny specks with larger ones.
  - **Flare duration**: scales how long each flare lasts. Durations follow simulation time, so **Playback rate** also changes them on screen.
  - **Particle whiteness**: 0 keeps each glint the color of the surrounding gas; 1 makes them white-hot.

These effects apply only to cinematic emission view.

### Disk & time

- **Show disk** and **Evolving fluid**. With the fluid off, the disk is the lab's procedural reference disk.
- **Fluid solver**: Auto (prefer GPU), GPU, or CPU reference.
- **Disk view**: Emission, Surface density, Temperature, or Flow tracers.
- **Detail contrast**: emphasizes brightness variations around each ring's mean. Cinematic only.
- **Volumetric disk** with **Thickness** (H/r) and **Opacity** (grey absorption).
- **Viscosity** (alpha) and **Stirring** (prescribed disturbances, not self-generated turbulence).
- **Fluid grid**: 32 x 64 to 256 x 512 cells. Larger grids show finer structure and take longer to prepare.
- **Outer radius**, **Outer fade**, and, in physical appearance, **Temperature**.
- **Mass** in millions of suns and **Playback rate** in physical seconds per second. At 100 million suns one gravitational time unit is about 493 seconds.
- **Light-travel delays**: each ray samples the gas at the time its light left the disk.
- **Elapsed** shows disk time in tg and in hours.

### Appearance & quality

- **Background**: Stars, Lensing grid, or Black.
- **Exposure** and **Bloom**.
- **Ray accuracy**: Preview, Standard, or Fine integration steps.
- **Render scale**: draws the view at a fraction of the window size. **Save PNG...** uses it too; movie exports always render at full resolution.
- **4 samples per pixel**: antialiasing.

### Video export

- **Camera preset**: Current view, Orbit, Push in, Pull back, Above-disk flyby, Rising reveal, Custom A to B, or Cinematic slingshot.
- **Custom A to B**: **Set A from camera** and **Set B from camera** record both ends, including pan, tilt, and roll.
- **Flyby and slingshot**: **Flight altitude**, **Side clearance**, **Pass on left**, **Bank angle**, **Off-center framing**, and, for the slingshot, **Turn arc**.
- **Sweep** (orbit and reveal), **Travel**, and **Ease in / out**.
- **Preview shot**, **Play preview**, and **Shot position**.
- **Resolution** 720p to 4K UHD, **Frame rate** 24 to 60 fps, **Duration** 1 to 120 seconds.
- **Freeze disk animation** keeps the gas still while the camera moves.
- **Format**: MP4 (H.264) or PNG sequence. Each export also writes a text file with the scene and camera path.
