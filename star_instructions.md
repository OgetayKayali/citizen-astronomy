You are modifying my existing Python/PyQt sky-atlas application. It uses a

QOpenGLWidget and currently renders stars through a mixture of:

- QPainter point batches for faint/background stars

- QPainter pixmap sprites for bright, named, selectable, searchable, or labeled stars

- CPU-side visibility collection, projection, grouping, and hit registration

The application already has a reasonably sophisticated visibility and catalog

pipeline. Do not replace working systems unnecessarily. The primary goal is to

replace the final star drawing architecture with a unified, fast,

Stellarium-inspired GPU point-source renderer.

Before editing the code, inspect the existing implementation and report:

1. The current star rendering call path.

2. Which parts run once at catalog load, once per camera change, and once per

   frame.

3. The number of star-related draw calls per frame.

4. The approximate number of QPainter groups generated.

5. The number of visible and candidate stars.

6. How many bytes are uploaded to the GPU each frame.

7. CPU time spent in:

   - visibility collection

   - projection

   - partitioning/group creation

   - QPainter star rendering

   - hit-target registration

8. Whether the existing experimental OpenGL star code is usable or should be

   replaced.

Do not merely tune the existing brightness, radius, or contrast constants.

Replace the underlying rendering model.

==================================================

SYSTEMS THAT MUST BE PRESERVED

==================================================

Preserve the existing behavior and interfaces for:

- Magnitude-sorted catalog storage.

- Spatial candidate selection.

- Rough dot-product or angular culling before full projection.

- Smooth FOV-dependent limiting magnitude.

- Smooth visibility fading near the limiting magnitude.

- Visible-object caching.

- Forced inclusion of the selected object.

- Star-density safety budgets.

- Ground and horizon rejection.

- Atmospheric extinction.

- Twinkle suppression or freezing while dragging, zooming, or animating.

- Separate labels, selection rings, hover rings, and object overlays.

- Current coordinate systems and sky projection.

- Current object searching and selection behavior.

- Existing tests unless a test encodes an obsolete rendering implementation.

Do not replace the current spatial index with HEALPix or another system during

the first phase unless profiling proves that candidate collection is the main

bottleneck.

==================================================

SYSTEMS THAT MUST BE REPLACED

==================================================

Replace the following architecture:

- QPainter point rendering for faint stars.

- QPen width as the primary representation of stellar magnitude.

- Separate unrelated visual systems for faint and bright stars.

- The hard visual distinction around magnitude 5.8.

- Metadata such as searchable, selectable, or label-visible determining which

  star glyph is used.

- Conventional alpha compositing as the main representation of stellar light.

- Visibility alpha shrinking the star footprint toward zero.

- Bright stars represented mainly as one increasingly large blurred pixmap.

- Per-frame QPainter grouping by color, alpha bucket, and width bucket.

- Rebuilding star groups only to animate twinkle.

- Hit-target registration being tightly coupled to the star draw submission.

- Any production code path where the intended GPU star pass is permanently

  disabled.

All stars, regardless of name, searchability, selectability, or label state,

must use the same compact stellar point-spread-function renderer.

Searchability and selection may affect hit testing and overlays, but must not

change the underlying star glyph.

==================================================

TARGET RENDERING ARCHITECTURE

==================================================

Create a dedicated StarRenderer class or module with clear ownership of:

- Shader compilation.

- VAO, VBO, texture, framebuffer, and other OpenGL resources.

- Visible-star instance-buffer updates.

- Compact PSF rendering.

- Bright-halo rendering.

- Magnitude lookup-table management.

- B−V color lookup-table management.

- GPU timing.

- OpenGL state setup and restoration.

- Resource cleanup when the OpenGL context is destroyed.

Use one static unit quad made from two triangles.

Render one instance per visible star using instanced drawing:

    glDrawArraysInstanced(...)

or:

    glDrawElementsInstanced(...)

The normal stellar pass should require one principal draw call.

A second draw call is permitted for broad halos around only the brightest

stars.

Do not use:

- One draw call per star.

- QPainter for the main star-light pass.

- Geometry shaders.

- Immediate-mode OpenGL.

- Per-frame texture creation.

- Per-frame shader compilation.

- Python objects created individually for every star during rendering.

- Python loops that issue one OpenGL command per star.

==================================================

PHASE 1: KEEP CPU VISIBILITY, REPLACE DRAWING

==================================================

For the first implementation, preserve the existing

*collect*visible_objects() and sky projection system.

Convert the resulting visible-star list into one contiguous NumPy structured

array or equivalent packed buffer.

Suggested data:

    dtype = [

        ("x_px", "f4"),

        ("y_px", "f4"),

        ("magnitude", "f4"),

        ("bv_index", "u1"),

        ("visibility", "f4"),

        ("altitude_deg", "f4"),

        ("star_id", "u4"),

        ("flags", "u1"),

    ]

The exact layout may be optimized for alignment and reduced bandwidth.

Upload only the currently visible star instances, unless the existing

projection is moved to the GPU.

Reuse allocated arrays and GPU buffers. Do not allocate a new Python object or

new independent array for every star on every frame.

Use buffer orphaning, glBufferSubData, persistent mapping, or another suitable

streaming strategy. Explain the selected method.

==================================================

UNIFY ALL STAR APPEARANCE

==================================================

Remove the current fast-star versus rich-star distinction as a visual

decision.

All stars must use the same compact PSF shader.

Bright, named, selectable, searchable, hovered, and selected stars may

additionally receive:

- A label.

- A selection ring.

- A hover ring.

- A hit-test entry.

- A broad bright-star halo.

They must not switch to a fundamentally different sprite shape.

A magnitude 5.79 star and magnitude 5.81 star should transition continuously

with no visible architecture boundary.

==================================================

MAGNITUDE MODEL

==================================================

Do not map visual magnitude linearly to radius.

Create a unified magnitude-to-appearance model that outputs at least:

- compact rendered radius

- compact-source intensity

- broad-halo intensity

- broad-halo radius

- visible/not-visible state

Use a lookup table sampled in approximately 0.05-magnitude increments.

Regenerate the table only when relevant settings change, such as:

- FOV

- exposure

- global star brightness

- star-size scale

- magnitude-size contrast

- device pixel ratio

- eye-adaptation state

- limiting magnitude

- halo amount

A suitable starting point is:

    lnL = -0.921034 * (magnitude + 12.12331) + fovFactor

Use a zoom-sensitive exposure term such as:

    zoomExposure = pow(60.0 / max(FOV_degrees, 0.7), 0.8)

The exact curve may be adjusted perceptually, but all formulas must be

documented.

Separate the following concepts:

1. Source intensity:

   Controls emitted additive light.

2. Compact PSF radius:

   Controls the apparent stellar footprint.

3. Visibility fade:

   Primarily controls intensity near the limiting magnitude.

4. Broad halo:

   A separate layer for only bright stars.

Do not allow every brightness-related setting to alter both intensity and size

without a documented reason.

==================================================

FAINT-STAR STABILITY

==================================================

Implement a Stellarium-style minimum visible footprint.

Use physical framebuffer pixels consistently.

Starting behavior:

    if rawRadius < 0.3 px:

        star is not rendered

    if 0.3 px <= rawRadius < 1.2 px:

        renderedRadius = 1.2 px

        intensity *= pow(rawRadius / 1.2, 3.0)

    otherwise:

        renderedRadius = rawRadius

For very bright stars, compress excessive radius growth:

    if renderedRadius > approximately 8 px:

        use square-root or logarithmic compression for additional growth

The purpose is:

- Faint stars maintain a stable antialiased footprint.

- Fainter magnitude is represented mainly by lower intensity.

- Stars do not crawl, blink, or change raster shape because their radius moves

  through unstable fractional-pixel values.

- Bright stars grow smoothly without becoming enormous disks.

Do not multiply the compact radius strongly by the existing visibility alpha.

Near the magnitude limit, fade intensity while preserving the minimum

footprint until the star becomes invisible.

==================================================

COMPACT STAR PSF

==================================================

Render normal stars as compact screen-facing point-spread-function sprites, not

hard circles.

Implement either:

A. A 16×16 or 32×32 single-channel PSF texture.

or:

B. An analytic fragment-shader PSF.

An analytic starting profile may be:

    vec2 p = uv * 2.0 - 1.0;

    float r2 = dot(p, p);

    float core   = exp(-18.0 * r2);

    float middle = 0.22  *exp(-5.0*  r2);

    float wing   = 0.025  *exp(-1.3*  r2);

    float mask = smoothstep(1.0, 0.72, r2);

    float psf = (core + middle + wing) * mask;

Adjust the relative contribution based on brightness:

- Faint stars should be dominated by the compact core.

- Medium-bright stars may show a small middle wing.

- Broad wings should not make every faint star fuzzy.

- Bright stars must retain a sharp central peak.

Discard fragments whose contribution is negligible when this improves

performance without producing visible clipping.

==================================================

BRIGHT STAR HALOS

==================================================

Bright stars must not be represented by one large blurred sprite.

Use two conceptually separate components:

1. Normal compact star pass:

   - sharp central core

   - compact PSF

   - normal magnitude-dependent radius and intensity

2. Optional halo pass:

   - only sufficiently bright stars

   - broad radius

   - low intensity

   - subtle falloff

   - additive blending

   - must never replace the compact core

Generate a compact halo-instance list containing only stars above a

configurable brightness threshold.

Render the halo list with no more than one additional instanced draw call.

Expose settings for:

- halo threshold

- halo radius scale

- halo intensity

- optional diffraction spikes

Spikes should be disabled by default or remain subtle.

==================================================

LINEAR-LIGHT ADDITIVE BLENDING

==================================================

Render stellar light in linear RGB.

For the star pass use:

    glEnable(GL_BLEND)

    glBlendEquation(GL_FUNC_ADD)

    glBlendFunc(GL_ONE, GL_ONE)

Also use:

    glDisable(GL_DEPTH_TEST)

    glDepthMask(GL_FALSE)

Restore the previous OpenGL state afterward.

Prefer rendering stars into an RGBA16F offscreen framebuffer.

After the star and other linear-light sky contributions are accumulated, apply

tone mapping.

A suitable starting tone mapper is:

    mapped = 1.0 - exp(-hdrColor * exposure)

Then convert linear RGB to sRGB for display.

Do not use SRC_ALPHA / ONE_MINUS_SRC_ALPHA for the primary star-light pass.

Alpha may still be used as auxiliary data, but the visible stellar energy

should be accumulated additively.

==================================================

COLOR

==================================================

Use the catalog’s B−V data to determine stellar color.

Implement either:

- A 128-entry B−V color lookup texture.

- A compact uniform or shader lookup table.

- A validated B−V-to-temperature-to-RGB approximation.

Colors should remain mostly near white:

- Hot stars: slightly blue-white.

- Solar-type stars: neutral or warm white.

- Cool stars: pale yellow or orange-white.

Avoid strongly saturated red and blue dots.

Convert source color to linear RGB before additive blending.

Allow a user color-saturation control, but keep a scientifically reasonable

default.

==================================================

TWINKLE

==================================================

Move twinkle into the GPU shader.

Twinkle should modulate intensity slightly, not position.

Preserve the existing behavior where twinkle is disabled or frozen during:

- dragging

- camera animation

- wheel-zoom settling

- other active interaction states

Use star ID and time to generate deterministic temporally smooth noise.

Do not generate a completely independent random value every frame.

Make twinkle stronger near the horizon. A suitable altitude weighting is:

    horizonWeight = min(1.0, 1.0 - 0.9 * sin(altitude))

Disable or heavily reduce twinkle for faint stars so they do not flash on and

off.

Only the time and interaction uniforms should need updating for twinkle.

Twinkle must not require rebuilding star geometry or QPainter groups.

==================================================

ATMOSPHERIC EXTINCTION

==================================================

Preserve the existing extinction behavior.

Extinction should reduce the additive source intensity.

Stars that become fainter than the effective visibility threshold after

extinction may be skipped.

Do not use extinction to produce large uncontrolled changes in compact radius.

Pass altitude or a precomputed extinction factor to the shader, depending on

which is more efficient and consistent with the existing atmospheric model.

==================================================

LABELS AND OVERLAYS

==================================================

Keep labels, selection rings, hover rings, DSO symbols, and other UI overlays

separate from the star-light pass.

QPainter may continue to render text and interface overlays after the OpenGL

star pass.

Only perform text layout for stars that actually qualify for labels.

Do not generate a text object or label layout for every visible catalog star.

The selected object must remain visible and interactive even when it is

normally fainter than the active limit.

==================================================

HIT TESTING

==================================================

Decouple hit testing from the star draw call.

Preserve current clickability behavior, but build a separate compact

screen-space index after visible positions are known.

Use a uniform grid, for example:

- cell size around 16–24 pixels

- each cell stores indices of interactive visible objects

- mouse queries inspect the current and neighboring cells

Do not perform an expensive scan over every visible star for each pointer

query.

Do not necessarily place every faint noninteractive background star into the

hit-test index.

Document which stars remain searchable, selectable, or hoverable.

==================================================

OPENGL / PYQT INTEGRATION

==================================================

Show all required changes in:

- initializeGL()

- resizeGL()

- paintGL()

- context destruction or cleanup handling

Account correctly for:

- QOpenGLWidget’s internal framebuffer

- devicePixelRatio

- logical versus physical pixels

- viewport dimensions

- resizing

- context recreation

- QPainter and native OpenGL transitions

- beginNativePainting() / endNativePainting() when required

- framebuffer binding restoration

- premultiplied versus straight-alpha expectations in Qt

- OpenGL errors and shader compile/link failures

Define one pixel convention and use it consistently.

Prefer physical framebuffer pixels for PSF radius calculations.

==================================================

PERFORMANCE REQUIREMENTS

==================================================

The final implementation must satisfy:

- One principal draw call for compact stars.

- At most one additional principal draw call for bright halos.

- No per-star draw calls.

- No QPainter point groups for the main star field.

- No per-frame shader compilation.

- No per-frame texture creation.

- No full-catalog GPU upload every frame.

- No Python rendering loop that issues commands per star.

- Reuse CPU arrays and GPU buffers.

- Minimize temporary allocations.

- Do not rebuild geometry merely to animate twinkle.

- Keep labels and UI overlays outside the GPU star-light batch.

- Preserve current star-density safety limits.

Add profiling for:

- candidate count

- visible-star count

- halo-star count

- CPU collection time

- CPU projection time

- instance-buffer preparation time

- GPU upload time

- compact-star GPU time

- halo GPU time

- QPainter overlay time

- total frame time

- draw-call count

- uploaded bytes per frame

- cache-hit status

Use OpenGL timer queries when available.

Use QOpenGLDebugLogger when available.

==================================================

MIGRATION ORDER

==================================================

Implement the work in this order:

PHASE 1

- Keep existing visibility collection and projection.

- Replace QPainter star points and rich star pixmaps with one instanced compact

  GPU PSF pass.

- Preserve QPainter labels and overlays.

- Add performance instrumentation.

PHASE 2

- Add the unified magnitude lookup table.

- Add minimum faint-star footprint and intensity compensation.

- Add bright-radius compression.

- Remove the magnitude-5.8 visual boundary.

- Ensure metadata no longer changes the stellar glyph.

PHASE 3

- Add the separate bright-halo instanced pass.

- Move twinkle fully into the shader.

- Move B−V color lookup into the GPU.

- Render into an HDR linear-light framebuffer and tone-map.

PHASE 4

- Decouple and optimize hit testing.

- Vectorize CPU instance preparation.

- Remove obsolete QPainter star-group code and old pixmap-star caches.

PHASE 5

- Profile again.

- Only then consider moving celestial projection to the GPU or replacing the

  current spatial index.

Do not begin with HEALPix, compute shaders, or a full catalog-system rewrite.

==================================================

SETTINGS

==================================================

Map the existing user settings to clear renderer concepts:

- Limiting magnitude:

  Controls which stars are eligible to appear.

- Star brightness:

  Controls additive source intensity/exposure.

- Star size:

  Controls compact PSF radius scaling.

- Magnitude-size contrast:

  Controls how strongly radius and/or intensity differentiate magnitudes.

- Twinkle:

  Controls shader twinkle amplitude.

Add settings where necessary for:

- faint-star minimum footprint

- bright-radius compression

- halo threshold

- halo strength

- halo radius

- color saturation

- HDR exposure

Do not let the base star-size setting uniformly enlarge every faint star so

much that the field becomes a collection of disks.

==================================================

TESTING

==================================================

Preserve existing behavioral tests for:

- wide-FOV faint-star suppression

- bright stars appearing stronger than faint stars

- interaction-independent density budgets

- frozen twinkle during interaction

- clickable fast/background stars

- visible-object collection

- selected-object forced visibility

Update tests that explicitly depend on QPainter point groups or the obsolete

fast-versus-rich renderer.

Add new tests for:

1. Equal-magnitude stars use equal compact PSF parameters regardless of

   searchable/selectable/label metadata.

2. There is no discontinuity around magnitude 5.8.

3. Visibility fading primarily changes intensity, not compact footprint.

4. Stars below the raw-radius cutoff are omitted.

5. Subpixel faint stars use a stable minimum footprint with compensated

   intensity.

6. Bright stars retain a compact core when a broad halo is enabled.

7. The normal star pass uses one instanced draw call.

8. The halo pass uses no more than one additional draw call.

9. Twinkle changes through uniforms without rebuilding the instance list.

10. Device-pixel-ratio changes preserve the apparent logical size correctly.

11. OpenGL state is restored after the star pass.

12. Selected objects remain visible and clickable beyond the normal magnitude

    limit.

==================================================

DELIVERABLES

==================================================

Provide:

1. A diagnosis of the existing rendering bottlenecks.

2. A file-by-file implementation plan before making changes.

3. The new StarRenderer class or module.

4. Complete vertex and fragment shader source.

5. The compact PSF implementation.

6. The bright-halo implementation.

7. The magnitude lookup-table implementation.

8. The B−V color conversion or lookup implementation.

9. The HDR framebuffer and tone-mapping implementation.

10. Changes required in initializeGL(), resizeGL(), and paintGL().

11. Cleanup and OpenGL-context destruction handling.

12. Updated settings integration.

13. Updated and new tests.

14. Before-and-after performance measurements.

15. A debug overlay or log line showing:

    - visible stars

    - halo stars

    - CPU collection time

    - buffer preparation time

    - GPU star time

    - total frame time

    - draw-call count

16. A removal plan for obsolete QPainter star groups, old pixmap-star caches,

    and disabled experimental GPU code.

17. Documentation of all visual formulas and default parameter values.

Use incremental changes that leave the application runnable after each phase.

Do not provide only pseudocode. Implement the changes using the project’s

existing coding style and architecture.