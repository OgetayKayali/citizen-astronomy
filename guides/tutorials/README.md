# CAst beginner tutorials for ogetay.com

Open the [tutorial index](index.html) in a browser. Each task has its own URL fragment. Copy the address while viewing a task to link a reader directly to it.

| Observing guides | Sky exploration | Simulation guides |
| --- | --- | --- |
| [Differential Photometry](photometry.html) | [Sky Atlas](sky-atlas.html) | [Black Hole](black-hole.html) |
| [Asteroid / Comet Detection](moving-objects.html) | [Sky Explorer](sky-explorer.html) | [Galaxy Merger](galaxy-merger.html) |
| | [HR Diagram](hr-diagram.html) | [Cluster Flow](cluster-flow.html) |
| | [Distance Map](distance-map.html) | [Binary Light Lab](binary-lab.html) |
| | | [Aurora & Magnetosphere Lab](aurora-lab.html) |

## Publish

The [live tutorial index](https://www.ogetay.com/citizen-astronomy-cast/tutorials/) is served from the static `citizen-astronomy-cast/tutorials/` directory in the ogetay.com document root. Publish the index, all eleven guide HTML files, `tutorial.css`, `tutorial.js`, and the entire `images`, `examples`, and `fonts` directories together there. The index includes guide thumbnails and a registered 3I/ATLAS movie. Host the practice and full-sequence ZIPs in the sibling `datasets/` directory. The lesson header and the index link to the appropriate archive. Keep the ZIP filenames in sync with PACKS in build.py.

The homepage links to the examples and tutorial directory. Guides open on their first lesson and support direct links to individual steps. The former Differential Photometry, Asteroid / Comet Detection, Sky Atlas, Sky Explorer, HR Diagram, and Distance Map addresses redirect to their respective guides. The earlier versions are stored as WordPress drafts. Each new guide links to its full mode reference in the project repository.

## Edit a lesson

Edit the appropriate JSON file in `content/` and place replacement screenshots in the corresponding subdirectory of `images/`. Preserve existing step IDs to keep published links working. Each step specifies its starting state, ordered actions, visible result, annotated screenshot, alternative text, and onward links. A second exposure can be added using `comparison` for an interactive blink.

Run `python guides/tutorials/build.py` from the Photometry project root. The build checks screenshot paths and branch destinations before writing the static pages. The generated HTML includes all lesson text and images. JavaScript adds single-step navigation, frame comparison, and the screenshot viewer.

The landing-page animation uses a fixed sky field registered to the first FITS plate solution. The amber brackets follow 3I/ATLAS using Horizons positions at the exposure midpoints.
