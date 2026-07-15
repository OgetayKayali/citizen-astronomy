The implementation summary is promising, but I need verification before I

accept it.

Please perform a technical audit of the code you just implemented and provide

the following.

1. ACTIVE BACKEND

Show exactly how the renderer selects between:

- instanced GPU rendering

- expanded-quad GPU fallback

- legacy QPainter session fallback

Add the active backend to the timing footer:

    backend=instanced

    backend=expanded

    backend=legacy

Confirm which backend is expected to run in my normal Windows packaged build.

Also report:

- OpenGL version

- GLSL version

- GPU vendor

- GPU renderer

- whether instancing is available

- whether the HDR FBO is complete

- whether the direct-additive fallback is active

Do not silently fall back. Log the full reason for every fallback.

2. DRAW CALL VERIFICATION

Show the exact OpenGL calls used for:

- compact stars

- bright halos

- tone-map/composite pass

State the expected draw-call count for each configuration:

- stars only

- stars plus halos

- stars plus HDR tone mapping

Clarify whether the reported draw count includes the tone-map fullscreen pass.

3. BUFFER FORMAT AND UPLOADS

Show the exact packed instance structure, byte stride, attribute layout, and

buffer update code.

Report the number of uploaded bytes per frame for a representative view with:

- 2,500 visible stars

- 6,000 visible stars

- 9,000 visible stars

Confirm that shader twinkle updates only uniforms and does not rebuild or

re-upload the star instance buffer when the camera, catalog, FOV, magnitude

limit, and extinction state have not changed.

4. HDR AND COLOR-SPACE AUDIT

Explain the complete framebuffer and color-space pipeline step by step.

Answer explicitly:

- What content is rendered into RGBA16F?

- Is the sky background in the HDR framebuffer or only the stars?

- Is the background already sRGB encoded?

- Are catalog B−V colors converted to linear RGB?

- Is GL_FRAMEBUFFER_SRGB enabled or disabled at each stage?

- What tone mapper is used?

- How is the tone-mapped star layer composited with the existing sky?

- Is the star texture premultiplied?

- What does the alpha channel contain?

- How does the direct-additive fallback differ visually?

Check glCheckFramebufferStatus and log the result.

5. QT/OPENGL STATE AUDIT

Show the exact paintGL ordering between:

- existing OpenGL drawing

- StarRenderer

- QPainter labels and overlays

Show where beginNativePainting/endNativePainting are used, if applicable.

List every OpenGL state modified by StarRenderer and show how it is restored,

including:

- framebuffer binding

- viewport

- shader program

- VAO

- VBO

- active texture unit

- texture bindings

- blend enable

- blend equation

- blend functions

- depth test

- depth mask

- scissor

- culling

- framebuffer sRGB

6. MAGNITUDE CONTINUITY TEST

Generate or log the LUT values for magnitudes:

    -1, 0, 1, 2, 3, 4, 5,

    5.70, 5.75, 5.79, 5.80, 5.81, 5.85, 5.90,

    6, 7, 8, 9, 10, 11, 12

For each magnitude report:

- raw radius

- rendered compact radius

- compact intensity

- halo radius

- halo intensity

- visible flag

Demonstrate that there is no discontinuity around magnitude 5.8.

7. METADATA-INDEPENDENCE TEST

Create otherwise identical stars with equal magnitude and B−V but different

flags:

- searchable

- selectable

- label_visible

- named

- unnamed

- hovered

- selected

Confirm that their normal compact PSF parameters are identical.

Hovered and selected stars may add overlays, but must not switch stellar

glyphs.

8. FAINT-STAR FADE TEST

Demonstrate the behavior of one faint star while its visibility alpha moves

through:

    1.0, 0.8, 0.6, 0.4, 0.2, 0.1, 0.05, 0.0

Report compact radius and intensity at each value.

The compact radius should retain the minimum stable footprint while intensity

fades. Visibility alpha must not shrink the footprint into an unstable

fractional-pixel dot.

9. BRIGHT-STAR STRUCTURE TEST

For a bright star such as Vega, report:

- compact core radius

- compact intensity

- halo radius

- halo intensity

Confirm that disabling the halo leaves a clear compact star and that enabling

the halo adds a broad low-intensity layer without replacing or softening the

core.

10. DEVICE PIXEL RATIO

Verify behavior at devicePixelRatio values:

    1.0

    1.25

    1.5

    2.0

State whether radius values are stored in logical or physical pixels.

The apparent logical size should remain consistent while the physical

framebuffer receives the correct pixel-scaled radius.

11. RESOURCE LIFETIME

Show how OpenGL resources are created and destroyed.

Verify correct cleanup for:

- window close

- QOpenGLWidget context recreation

- resize

- application shutdown

- failed shader initialization

- failed FBO initialization

Connect cleanup to the context's aboutToBeDestroyed signal if appropriate.

12. FULL TEST SUITE

Run the complete project test suite, not only selected star tests.

Report:

- total tests

- passed

- failed

- skipped

- warnings

- total runtime

Clearly distinguish tests using a real OpenGL context from mocked or

model-only tests.

13. BEFORE/AFTER BENCHMARK

Using the same camera view, FOV, viewport, catalog, magnitude limit, and display

scale, provide a table comparing the legacy renderer and unified renderer:

- candidate stars

- visible stars

- halo stars

- collection CPU time

- projection CPU time

- partition/packing CPU time

- upload time

- compact-star GPU time

- halo GPU time

- tone-map time

- QPainter overlay time

- total paintGL time

- draw calls

- uploaded bytes per frame

Measure at:

- wide FOV

- medium FOV

- narrow FOV

- active dragging

- stable view with twinkle

- 100% and 200% display scaling

14. VISUAL VALIDATION

Provide screenshots from the exact same view using:

A. Legacy renderer

B. New renderer with halo disabled

C. New renderer with halo enabled

Use identical magnitude limit, FOV, camera orientation, viewport, and catalog.

The screenshots should demonstrate:

- stable faint-star footprints

- smooth magnitude progression

- subtle B−V colors

- no magnitude-5.8 boundary

- sharp cores for bright stars

- broad but restrained halos

- no square sprite edges

- no clipped quads

- no excessive fuzzy background stars

Do not remove the legacy fallback yet. First provide this audit and fix any

issues it reveals.