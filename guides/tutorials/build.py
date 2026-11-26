"""Build the static CAst tutorial directory and its linkable lessons."""

import json
import re
from html import escape
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CAST_URL = "https://www.ogetay.com/citizen-astronomy-cast/"
REFERENCE_ROOT = "https://github.com/OgetayKayali/citizen-astronomy/blob/master/guides/"
OBSERVING = {
    "photometry": {
        "field": "AE UMa",
        "hero": "ae-uma-observation.webp",
        "hero_alt": "AE UMa circled in its star field. Below, the measured light curve shows the star brightening toward the end of the 81-minute sequence.",
        "headline": "Measure a variable star",
        "description": "Compare a star with its neighbors and see how its brightness changes over a night.",
        "own": "To follow with your own images, use individual exposures of the same variable-star field through one filter. Keep the observation times and plate solutions in the image headers. Your target name and light curve will differ.",
        "reference": "differential_photometry",
    },
    "moving-objects": {
        "field": "3I/ATLAS",
        "hero": "3i-atlas-motion.webp",
        "hero_alt": "Two exposures of the same field nine minutes apart. The marked comet shifts down and to the right while the star at the right edge stays in place.",
        "headline": "Find a moving object",
        "description": "Locate a predicted comet in a star field, then follow its motion from one exposure to the next.",
        "own": "To follow with your own images, use separate, time-stamped exposures of the same field through one filter. Plate-solved images let CAst place the predicted objects on the sky. The list of objects depends on where and when you observed.",
        "reference": "asteroid_comet_detection",
    },
}

SKY_GUIDES = {
    "sky-atlas": (
        "Sky Atlas", "Find an object on the sky",
        "Search the atlas and see where an object appears from your observing site.",
    ),
    "sky-explorer": (
        "Sky Explorer", "Identify objects in a photograph",
        "Mark catalog objects on an image, select a result, and save the annotated field.",
    ),
    "hr-diagram": (
        "HR Diagram", "Plot the stars in M46",
        "Build a color–magnitude diagram and follow a plotted star back to the photograph.",
    ),
    "distance-map": (
        "Distance Map", "Explore the depth of the M46 field",
        "Use Gaia distances to separate nearby stars from more distant stars along the same line of sight.",
    ),
}

SIMULATIONS = {
    "black-hole": (
        "Black Hole", "Explore a black hole",
        "Orbit the glowing disk, compare the effects of spin, or save a camera shot.",
        "black_hole",
    ),
    "galaxy-merger": (
        "Galaxy Merger", "Follow two galaxies through an encounter",
        "Choose an encounter and change the view to follow the stars as the galaxies pass.",
        "galaxy_merger",
    ),
    "cluster-flow": (
        "Cluster Flow", "Watch gas move through a galaxy cluster",
        "Compare views of the same gas to find dense regions and compression fronts.",
        "cluster_flow",
    ),
    "binary-lab": (
        "Binary Light Lab", "Measure an eclipse",
        "Watch two stars orbit and see how their alignment changes the light you measure.",
        "binary_light_lab",
    ),
    "aurora-lab": (
        "Aurora & Magnetosphere Lab", "See how solar wind moves Earth’s magnetic boundary",
        "Compare quiet conditions with two solar storms at the same moment in model time.",
        "aurora_magnetosphere",
    ),
}

DOWNLOADS = "https://www.ogetay.com/citizen-astronomy-cast/datasets/"
PACKS = {
    "ae-uma-practice": ("AE UMa practice set", "24 FITS cutouts across 81 minutes", "28 MB"),
    "ae-uma-full": ("AE UMa full sequence", "158 original FITS frames · 30 seconds each", "5.53 GB"),
    "3i-atlas-practice": ("3I/ATLAS practice set", "5 FITS exposures", "418 MB"),
    "3i-atlas-full": ("3I/ATLAS full sequence", "76 original FITS frames · 120 seconds each", "6.31 GB"),
    "lagoon-overlay": ("Lagoon sky overlay", "1 PNG image and its FITS coordinate file", "2.7 MB"),
    "m46": ("M46", "1 plate-solved XISF image", "75 MB"),
    "sn2025rbs-practice": ("SN2025rbs", "4 FITS cutouts of NGC 7331 across two nights", "4.8 MB"),
}
GUIDE_PACKS = {"photometry":"ae-uma-practice", "moving-objects":"3i-atlas-practice",
               **{key:"m46" for key in SKY_GUIDES}, "sky-atlas":"lagoon-overlay"}
FULL_PACKS = {"photometry":"ae-uma-full", "moving-objects":"3i-atlas-full"}
FILE_STEPS = {"photometry":"choose-folder", "moving-objects":"practice-files", "sky-explorer":"open-file",
              "sky-atlas":"choose-image", "hr-diagram":"choose-file", "distance-map":"choose"}
PACK_HELP = {
    "photometry":"Choose the 24-frame practice set for a quick run or the 158-frame sequence to reproduce the full night. Extract your ZIP and open its folder in CAst.",
    "moving-objects":"The practice set contains the five exposures used in the walkthrough. To track the comet across the full night, extract the 76-frame sequence and open the 3I ATLAS full folder instead.",
    "sky-atlas":"Start with the built-in atlas. For the optional photo overlay, extract the ZIP, keep Lagoon.png and Lagoon.fits together, and select Lagoon.png.",
    "sky-explorer":"Extract the ZIP and open M46.xisf, or start with a public survey in CAst.",
    "hr-diagram":"Extract the ZIP and open M46.xisf. Connect to the internet for the first Gaia catalog lookup.",
    "distance-map":"Extract the ZIP and open M46.xisf. CAst uses Gaia distances to build the map.",
}

def download(pack):
    name, _, size = PACKS[pack]
    return f'<a class="download-file" href="{DOWNLOADS}{pack}.zip" download>Download {name} <small>ZIP · {size}</small></a>'

def inline(value):
    """Only allow bold control labels and links to steps in this guide."""
    value = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escape(value))
    return re.sub(r"\[([^\]]+)\]\(#([a-z0-9-]+)\)", r'<a href="#\2">\1</a>', value)


def page(title, description, body, *, index=False, full_sequence=False):
    header_action = '<a class="home-link" href="index.html">All tutorials</a>'
    if index:
        release = "https://github.com/OgetayKayali/citizen-astronomy/releases/latest/download/"
        header_action = f"""<details class="program-download"><summary>Download CAst <span aria-hidden="true">↓</span></summary>
<div><a href="{release}CitizenAstronomy.CAst-alpha-Setup.exe">Windows installer</a>
<a href="{release}CitizenAstronomy-macos-arm64-unsigned.zip">macOS · Apple silicon</a>
<a href="{CAST_URL}#install">Installation notes</a></div></details>"""
    css_version = "full-sequences" if index or full_sequence else "observations-3"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="{escape(description)}">
<title>{escape(title)} | Citizen Astronomy</title>
<link rel="stylesheet" href="tutorial.css?v={css_version}"><script defer src="tutorial.js?v=observations-3"></script></head>
<body><a class="skip" href="#main">Skip to tutorial</a>
<header class="masthead"><div class="wrap mast-inner">
<a class="brand" href="{CAST_URL}"><b>CAst</b><span>Citizen Astronomy</span></a>
{header_action}
</div></header>
{body}
<footer><div class="wrap foot-inner"><a href="index.html">All tutorials</a><a href="{CAST_URL}">Citizen Astronomy on ogetay.com ↗</a></div></footer>
</body></html>
"""


def step_list(groups, collapsible=False):
    output = []
    for group in groups:
        links = "".join(
            f'<li><a href="#{s["id"]}">{escape(s["title"])}</a></li>' for s in group["steps"]
        )
        if collapsible:
            output.append(f'<details class="task-fold" name="observing-tasks"><summary>{escape(group["title"])}</summary><ol>{links}</ol></details>')
        else:
            output.append(f'<section class="step-group"><h3>{escape(group["title"])}</h3><ol>{links}</ol></section>')
    return "".join(output)


def capture(course, step):
    frames = [step]
    labels = [step.get("imageLabel", "Screenshot")]
    switches = ""
    if "comparison" in step:
        frames.append(step["comparison"])
        labels.append(step["comparison"]["label"])
        buttons = "".join(f'<button type="button">{escape(label)}</button>' for label in labels)
        switches = f'<div class="frame-switch" role="group" aria-label="Compare screenshots" hidden>{buttons}</div>'
    images = []
    for frame, label in zip(frames, labels):
        image = f'images/{course}/{escape(frame["image"])}'
        caption = f'{escape(label)} · ' if len(frames) > 1 else ''
        images.append(f'''<a class="enlarge" href="{image}" aria-label="Enlarge {escape(label)} for {escape(step["title"])}">
<img src="{image}" loading="lazy" alt="{escape(frame["alt"])}">
<span class="zoom-label">{caption}Enlarge screenshot <span aria-hidden="true">↗</span></span></a>''')
    return f'<figure class="capture">{switches}{"".join(images)}</figure>'


def article(course, step, group, number):
    actions = "".join(f"<li>{inline(action)}</li>" for action in step["actions"])
    choices = []
    for link in step["links"]:
        target_page = f'{link["guide"]}.html' if "guide" in link else ""
        choices.append(f'<a class="choice" href="{target_page}#{link["id"]}">{escape(link["label"])} <span aria-hidden="true">→</span></a>')
    example = ""
    if "example" in step:
        artifact = step["example"]
        example = f'<a class="sample" href="examples/{escape(artifact["file"])}">{escape(artifact["label"])} ↗</a>'
    return f"""<article class="lesson" id="{step["id"]}" aria-labelledby="title-{step["id"]}">

<p class="overline">{escape(group)} <span aria-hidden="true">/</span> Step {number:02}</p>
<h2 tabindex="-1" id="title-{step["id"]}">{escape(step["title"])}</h2>
<p class="before"><strong>Start here</strong> {inline(step["requires"])}</p>
<ol class="actions">{actions}</ol>
{capture(course, step)}
<div class="checkpoint"><strong>What you should see</strong><p>{inline(step["check"])}</p>{example}</div>
<nav class="next" aria-label="Continue from {escape(step["title"])}">{"".join(choices)}
<a class="back-topics" href="#tasks">Browse all steps</a></nav>
</article>"""


def course_page(key, data, steps):
    reference_file = (OBSERVING[key]["reference"] if key in OBSERVING else SIMULATIONS[key][3]
                      if key in SIMULATIONS else key.replace("-", "_"))
    reference = f"{REFERENCE_ROOT}{reference_file}.md"
    if key in GUIDE_PACKS:
        pack = GUIDE_PACKS[key]
        links = download(pack)
        if key in FULL_PACKS:
            links = f'<div class="sequence-links">{links}{download(FULL_PACKS[key])}</div>'
        setup = f'<div class="guide-files">{links}<span>{PACK_HELP[key]} <a href="#{FILE_STEPS[key]}">See the file step</a>.</span></div>'
        if key == "sky-explorer":
            setup = setup.replace("</span>", ' <a href="#survey">Start with a public survey</a>.</span>')
    else:
        setup = f'<p class="preset-note">{escape(data["dataset"])}</p>'
    topics = step_list(data["groups"])
    articles = []
    for group in data["groups"]:
        for step in group["steps"]:
            articles.append(article(key, step, group["title"], len(articles) + 1))
    title = escape(data["title"])
    return page(data["title"], data["intro"], f"""
<main id="main" class="wrap guide" tabindex="-1">
<nav class="crumbs" aria-label="Breadcrumb"><a href="index.html">Tutorials</a><span>/</span><span>{title}</span></nav>
<header class="guide-head"><h1>{title}</h1>{setup}</header>
<div class="reader"><aside class="rail"><details class="step-menu" open><summary id="tasks">All steps in this guide</summary>
<nav aria-label="Step index">{topics}<a class="reference" href="{reference}">Full mode reference ↗</a></nav></details></aside>
<div class="lessons">{''.join(articles)}</div></div>
</main><dialog aria-labelledby="viewer-title"><div class="viewer-head"><strong id="viewer-title">Screenshot</strong>
<div><button class="scale" type="button" aria-pressed="false">Actual size</button>
<button class="close-viewer" type="button" autofocus>Close</button></div></div>
<div class="viewer-image"><img alt=""></div></dialog>""", full_sequence=key in FULL_PACKS)


def course_card(key, field, headline, description, detail):
    packs = (mapping[key] for mapping in (FULL_PACKS, GUIDE_PACKS) if key in mapping)
    resource = ''.join(f'<div class="tile-resource">{download(pack)}</div>' for pack in packs)
    return f"""<article class="guide-tile"><a class="course-card" href="{key}.html">
<img class="card-art art-{key}" src="images/cards/{key}.webp?v=2" loading="lazy" alt="">
<div class="card-copy"><p class="overline">{escape(field)}</p><h3>{escape(headline)}</h3><p>{escape(description)}</p>
<div class="card-bottom"><span>{escape(detail)}</span><b>Open guide →</b></div></div></a>{resource}</article>"""

def dataset_library():
    guides = {"ae-uma-practice":("photometry.html#choose-folder","Select the AE UMa folder"),
              "ae-uma-full":("photometry.html#choose-folder","Open the full photometry sequence"),
              "3i-atlas-practice":("moving-objects.html#practice-files","Open the five-image exercise"),
              "3i-atlas-full":("moving-objects.html#choose-folder","Open the full comet sequence"),
              "lagoon-overlay":("sky-atlas.html#choose-image","Place the Lagoon image on your atlas"),
              "m46":("sky-explorer.html#open-file","Open M46 in Sky Explorer"),
              "sn2025rbs-practice":("https://www.ogetay.com/transient-finder/#tutorial","Open Transient Finder")}
    rows = []
    for key, (name, contents, _) in PACKS.items():
        url,label=guides[key]
        if key=="m46":
            contents += " for Sky Explorer, HR Diagram, and Distance Map."
        rows.append(f'<li><div><strong>{name}</strong><p>{contents}</p><a href="{url}">{label} →</a></div>{download(key)}</li>')
    return '<section class="wrap chooser library" id="datasets"><h2>Example datasets</h2><p>Download a ZIP and extract it before opening the files in CAst. Each guide shows which file or folder to select.</p><ul>'+''.join(rows)+'</ul></section>'

def home_page(step_counts):
    observing = [course_card(key, c["field"], c["headline"], c["description"], PACKS[FULL_PACKS[key]][1])
                 for key, c in OBSERVING.items()]
    simulations = [course_card(key, mode, headline, description, f"{step_counts[key]} illustrated steps")
                   for key, (mode, headline, description, _) in SIMULATIONS.items()]
    sky = [course_card(key, mode, headline, description, f"{step_counts[key]} illustrated steps")
           for key, (mode, headline, description) in SKY_GUIDES.items()]
    return page("Citizen Astronomy tutorials", "Step-by-step guides to sky exploration, observing, and simulations in Citizen Astronomy.", f"""
<main id="main" tabindex="-1"><section class="hero"><div class="wrap hero-inner">
<div><h1>Citizen Astronomy tutorials</h1><p>No telescope images needed. Use an example dataset, explore a public sky survey, or try a built-in simulation.</p>
<nav aria-label="Tutorial categories"><a class="hero-link" href="#choose">Observing</a><a class="hero-link" href="#sky">Sky exploration</a>
<a class="hero-link" href="#simulations">Simulations</a><a class="hero-link" href="#datasets">Datasets ↓</a></nav></div>
<figure class="field"><video class="comet-film" muted playsinline loop preload="metadata" poster="images/cards/atlas-poster.webp?v=2" src="images/cards/atlas-motion.mp4?v=2" aria-label="Marked comet 3I/ATLAS moving across a fixed star field on 7 December 2025"></video><figcaption><b>3I/ATLAS</b><span>7 Dec 2025</span><button class="film-toggle" type="button" hidden>Play motion</button></figcaption></figure>
</div></section><section class="wrap chooser" id="choose" aria-labelledby="choose-title"><h2 id="choose-title">Observing guides</h2>
<div class="course-grid">{''.join(observing)}</div></section>
<section class="wrap chooser" id="sky" aria-labelledby="sky-title"><h2 id="sky-title">Sky exploration</h2>
<div class="course-grid">{''.join(sky)}</div></section>
<section class="wrap chooser" id="simulations" aria-labelledby="simulations-title"><h2 id="simulations-title">Simulation guides</h2>
<div class="course-grid">{''.join(simulations)}</div>
</section>{dataset_library()}</main>""", index=True)


def build():
    step_counts = {}
    courses = {key: json.loads((ROOT / "content" / f"{key}.json").read_text(encoding="utf-8-sig"))
               for key in (*OBSERVING, *SKY_GUIDES, *SIMULATIONS)}
    guide_ids = {key: {s["id"] for group in data["groups"] for s in group["steps"]}
                 for key, data in courses.items()}
    for key, data in courses.items():
        steps = [s for group in data["groups"] for s in group["steps"]]
        step_counts[key] = len(steps)
        ids = guide_ids[key]
        if len(ids) != len(steps) or ids & {"main", "topics", "tasks"}:
            raise ValueError(f"Duplicated or reserved step ID in {key}")
        for s in steps:
            if not re.fullmatch(r"[a-z0-9-]+", s["id"]) or not s["actions"] or not s["alt"]:
                raise ValueError(f"Incomplete step: {s['id']}")
            if not (ROOT / "images" / key / s["image"]).is_file():
                raise FileNotFoundError(s["image"])
            if "comparison" in s:
                second = s["comparison"]
                if not second["alt"] or not second["label"] or not (ROOT / "images" / key / second["image"]).is_file():
                    raise ValueError(f"Incomplete comparison in {s['id']}")
            if "example" in s and not (ROOT / "examples" / s["example"]["file"]).is_file():
                raise FileNotFoundError(s["example"]["file"])
            for link in s["links"]:
                if link["id"] not in guide_ids.get(link.get("guide", key), set()):
                    raise ValueError(f"Unknown branch from {key}#{s['id']}: {link}")
            destinations = set()
            for value in [s["requires"], *s["actions"], s["check"]]:
                destinations.update(re.findall(r"\]\(#([\w-]+)\)", value))
            if destinations - ids:
                raise ValueError(f"Unknown links from {s['id']}: {destinations - ids}")
        (ROOT / f"{key}.html").write_text(course_page(key, data, steps), encoding="utf-8")
    (ROOT / "index.html").write_text(home_page(step_counts), encoding="utf-8")


if __name__ == "__main__":
    build()
