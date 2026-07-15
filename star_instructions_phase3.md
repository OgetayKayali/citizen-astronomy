The unified GPU star renderer works, but the Sky Atlas still feels laggy during

pan, zoom, and animation. Focus only on profiling and performance optimization.

Do not redesign the visual appearance and do not add new rendering features.

First, profile a real interactive session for at least:

- 300 frames while continuously dragging

- 300 frames while continuously zooming

- 300 stable frames with twinkle enabled

Measure rolling average and p95 time for:

- candidate-bin selection

- visible-object collection

- coordinate transforms and projection

- creation of _SkyVisibleObject objects

- partitioning

- instance-array packing

- GPU buffer upload

- hit-grid rebuilding

- QPainter background/grid rendering

- beginNativePainting/endNativePainting transition

- compact star GPU pass

- halo pass

- HDR tone-map pass

- labels and overlays

- complete paintGL frame

Print one concise summary identifying the three largest costs. Then optimize the

largest measured costs rather than guessing.

Prioritize these changes:

1. Remove obsolete star partitioning/group construction now that all stars use

   the unified renderer. Send visible stars directly to StarRenderer.

2. Avoid constructing one Python _SkyVisibleObject and multiple temporary

   tuples/lists per star. Use persistent contiguous NumPy arrays and vectorized

   transformations/projection for candidate stars.

3. Cache static per-star values such as magnitude-LUT results, linear RGB,

   twinkle seed, and flags. During camera motion, update only screen position,

   visibility/extinction, and any genuinely dynamic fields.

4. Reuse the instance array and hit-grid storage. Do not allocate fresh lists,

   dictionaries, QPolygonF objects, or NumPy arrays every frame.

5. During drag, wheel settling, and camera animation:

   - suspend hover processing

   - do not rebuild the hit grid every frame

   - defer label layout

   - simplify or temporarily skip nonessential overlays

   - rebuild the accurate hit grid and labels once interaction becomes stable

6. Check whether tiny time changes invalidate the visible-object cache every

   frame. Quantize automatic sky-time updates or update star geometry only when

   the positional change could exceed a small screen-space threshold.

7. Measure HDR enabled versus disabled. If HDR/tonemap costs more than 1 ms p95,

   optimize FBO reuse and state changes. Never recreate or resize the FBO unless

   the framebuffer size actually changes.

8. Measure beginNativePainting/endNativePainting separately. If the transition

   is expensive, reorganize paintGL to minimize QPainter/OpenGL transitions.

   There should be only one OpenGL star section per frame and no unnecessary

   painter flushes.

9. Verify the actual 40-byte VBO layout. For ten float32 values the expected

   offsets are likely:

       position x: 0

       position y: 4

       radius: 8

       intensity: 12

       RGB: 16, 20, 24

       altitude: 28

       seed: 32

       flags: 36

   Correct any mismatched glVertexAttribPointer offsets.

10. Confirm that the renderer is really using backend=instanced and not

    expanded or legacy during normal use.

Performance target:

- Smooth 60 Hz interaction where practical

- total paintGL p95 below 16.7 ms

- star preparation plus upload below 3 ms at 9,000 visible stars

- GPU star and halo passes below 1 ms on the target GPU

- no per-frame hit-grid or label rebuild during continuous dragging

- no visual star popping or reduced star density during interaction

After optimization, provide a compact before/after table for dragging, zooming,

and stable twinkle. Include average frame time, p95 frame time, FPS, largest

three CPU costs, GPU star time, upload bytes, visible stars, and active backend.