"""A compact map of CAst's modes and menus that the assistant always has in view."""

from __future__ import annotations

from dataclasses import dataclass

from photometry_app.core.models import AppMode


@dataclass(frozen=True, slots=True)
class ModeInfo:
    mode: AppMode
    name: str
    opens: str
    summary: str
    guide_id: str | None
    on_launcher: bool = True


MODES: tuple[ModeInfo, ...] = (
    ModeInfo(
        AppMode.DIFFERENTIAL_PHOTOMETRY,
        "Differential Photometry",
        "a folder of time-series frames of one field (File > Open Folder)",
        "Light curves of variable stars from comparison-star photometry, period fitting, O-C, and AAVSO reports.",
        "differential_photometry",
    ),
    ModeInfo(
        AppMode.ASTEROID_COMET_DETECTION,
        "Asteroid/Comet Detection",
        "a folder of plate-solved frames of one field (File > Open Folder)",
        "Known asteroid and comet predictions, blinking, Align, Discover, Synthetic Track, Plots, and Trajectory View.",
        "asteroid_comet_detection",
    ),
    ModeInfo(
        AppMode.HR_DIAGRAM,
        "HR Diagram",
        "one image of a star field or cluster (File > Open File)",
        "Gaia-matched color-magnitude diagram of the stars in the image, with Find Cluster for co-moving groups and Filter for color, distance, luminosity, and stellar class.",
        "hr_diagram",
    ),
    ModeInfo(
        AppMode.TRANSIENT_FINDER,
        "Transient Finder",
        "a folder of repeated images of the same field (File > Open Folder)",
        "Finds sources that vary significantly between frames.",
        "transient_finder",
    ),
    ModeInfo(
        AppMode.SKY_VIEW,
        "Sky Atlas",
        "no files needed",
        "Interactive sky atlas with search, observer location and time, layers, and custom surveys from your own images.",
        "sky_atlas",
    ),
    ModeInfo(
        AppMode.SKY_EXPLORER,
        "Sky Explorer",
        "one plate-solved or solvable image (File > Open File)",
        "Identifies catalog objects in an image's field, compares with survey cutouts, estimates magnitude limits, and builds collages.",
        "sky_explorer",
    ),
    ModeInfo(
        AppMode.DISTANCE_MAP,
        "Distance Map",
        "one plate-solved or solvable image (File > Open File)",
        "3D map of the field's stars from Gaia parallaxes, with Find Cluster and Tomography.",
        "distance_map",
    ),
    ModeInfo(
        AppMode.ASTROSTACK,
        "Deep Stack",
        "a folder of related frames (File > Open Folder)",
        "Cumulative stacking animation with running signal, noise, SNR, and FWHM metrics, exported as GIF or MP4. Its guide is titled AstroStack.",
        "astrostack",
    ),
    ModeInfo(
        AppMode.ASTRO_TOOLS,
        "Observation Deck",
        "one or more master imaging folders added inside the mode",
        "Library dashboard of your whole imaging archive: observation history, filter usage, and targets. "
        "Images can be excluded from its statistics without touching the files.",
        "observation_deck",
        on_launcher=False,
    ),
)

MENUS: tuple[tuple[str, str], ...] = (
    ("File", "Open File (Ctrl+O), Open Folder (Ctrl+Shift+O), export actions for the open mode, Clear All Cache, Check for Updates"),
    ("Mode", "Choose Mode... (the launcher cards) and one entry per mode"),
    ("Calibrate", "menu-bar button that opens the calibration pipeline for bias, dark, and flat frames"),
    ("Tools", "AI Assistant (Ctrl+Shift+A), Create collage, Estimate magnitude limit..., Clear magnitude limit markers"),
    (
        "Settings",
        "Open Settings (tabs: General, Advanced, Differential Photometry, HR Diagram, Asteroid/Comet, Sky Explorer, Setup, AI Assistant), "
        "Catalog Cache, Theme, Light Curve, HR Diagram, Asteroid/Comet, Alignment",
    ),
)


def mode_info(mode: AppMode | str) -> ModeInfo | None:
    for info in MODES:
        if info.mode == mode or info.name.lower() == str(mode).lower():
            return info
    return None


def mode_names_for_guide(guide_id: str) -> tuple[str, ...]:
    return tuple(info.name for info in MODES if info.guide_id == guide_id)


def program_map_text() -> str:
    lines = ["Modes (name as shown in the Mode menu; what it opens; what it does):"]
    for info in MODES:
        where = "" if info.on_launcher else " Mode menu only, not a launcher card."
        lines.append(f"- {info.name}: opens {info.opens}. {info.summary}{where}")
    lines.append("Menus:")
    lines.extend(f"- {name}: {items}" for name, items in MENUS)
    return "\n".join(lines)
