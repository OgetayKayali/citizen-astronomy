from __future__ import annotations

import colorsys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
import hashlib
import math
from pathlib import Path
import re
import time

from astroquery.simbad import Simbad
from astroquery.vizier import Vizier
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.table import Row
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales

from photometry_app.core.catalogs import CatalogService
from photometry_app.core.image_io import read_header, read_header_and_shape
from photometry_app.core.models import CatalogStar, SolvedField
from photometry_app.core.scanner import inspect_fits_file
from photometry_app.core.settings import AppSettings, resolve_astrometry_timeout_seconds
from photometry_app.core.solar_system import SolarSystemSearchResult, search_nearby_known_solar_system_objects
from photometry_app.core.survey_images import SKY_EXPLORER_SURVEY_FIELD_MOSAIC_RADIUS
from photometry_app.core.wcs import AstrometryNetClient, celestial_wcs, extract_solved_field, infer_astrometry_solve_hints, validate_wcs


SKY_EXPLORER_LAYER_ORDER: tuple[str, ...] = (
    "deep_sky",
    "general_objects",
    "solar_system",
    "variable_stars",
    "gaia_stars",
    "exoplanets",
)

_LAYER_TITLES: dict[str, str] = {
    "deep_sky": "Deep Sky",
    "general_objects": "General Objects",
    "solar_system": "Asteroids/Comets",
    "variable_stars": "Variable Stars",
    "gaia_stars": "Gaia Stars",
    "exoplanets": "Exoplanet Hosts",
}

_MAX_GAIA_STARS = 250
_MAX_SIMBAD_GENERAL_OBJECTS = 300
_MAX_SIMBAD_DEEP_SKY_OBJECTS = 5000
_MAX_NGC2000_OBJECTS = 64
_MAX_HYPERLEDA_GALAXIES = 2000
_MAX_SHARPLESS_OBJECTS = 256
_MAX_BARNARD_OBJECTS = 256
_MAX_VDB_OBJECTS = 256
_MAX_HASH_PN_OBJECTS = 512
_SIMBAD_TIMEOUT_SECONDS = 12
_SIMBAD_SEARCH_RADIUS_EXPANSION = 1.05
_SIMBAD_TILED_DEEP_SKY_RADIUS = 20.0 * u.arcmin
_SIMBAD_TILED_DEEP_SKY_STEP_FACTOR = 1.3
_SIMBAD_MAX_FINE_QUERY_REGIONS = 36
_SIMBAD_WIDE_FIELD_QUERY_REGIONS = 9
_SIMBAD_LAYER_QUERY_BUDGET_SECONDS = 35.0
_NGC2000_CATALOG = "VII/118/ngc2000"
_HYPERLEDA_PGC_CATALOG = "VII/237/pgc"
_SHARPLESS_CATALOG = "VII/20"
_BARNARD_CATALOG = "VII/220A"
_VDB_CATALOG = "VII/21/catalog"
_HASH_PN_CATALOG = "V/163/pnmain"
_MIN_HYPERLEDA_MAJOR_AXIS_ARCMIN = 0.2
_HASH_PN_DIAMETER_SENTINEL_ARCSEC = 99999.0
_HASH_PN_POSITION_ANGLE_SENTINEL_DEG = 999.0
_HASH_PN_STATUS_OBJECT_TYPES: dict[str, tuple[str, str]] = {
    "T": ("Planetary Nebula", "PN"),
    "L": ("Planetary Nebula", "PN"),
    "P": ("Planetary Nebula", "PN"),
    "C": ("Planetary Nebula", "PN"),
    "A": ("Planetary Nebula", "PN"),
    "B": ("Planetary Nebula", "PN"),
    "HII": ("HII Region", "HII"),
    "SNR": ("Supernova Remnant", "SNR"),
    "SR?": ("Supernova Remnant", "SNR"),
    "RNE": ("Reflection Nebula", "RNe"),
    "G": ("Galaxy", "G"),
    "G?": ("Galaxy", "G"),
    "STAR": ("Star", "*"),
    "EM*": ("Emission-Line Star", "Em*"),
    "?EM*": ("Emission-Line Star", "Em*"),
    "EMO": ("Emission Nebula", "EmO"),
    "WD*": ("White Dwarf", "WD*"),
    "Y*O": ("Young Stellar Object", "Y*O"),
    "Y*?": ("Young Stellar Object", "Y*O"),
    "CL*": ("Open Cluster", "Cl*"),
    "SY*": ("Emission-Line Star", "Em*"),
    "SY?": ("Emission-Line Star", "Em*"),
    "HH": ("Nebula", "HH"),
    "HH?": ("Nebula", "HH"),
    "CIR": ("Nebula", "cir"),
    "AGB": ("Star", "*"),
    "AGB?": ("Star", "*"),
}
_STAR_LIKE_SIMBAD_TYPES = (
    "star",
    "variable",
    "candidate",
    "white dwarf",
    "brown dwarf",
    "multiple object",
    "high proper-motion star",
    "low-mass star",
)


@dataclass(frozen=True, slots=True)
class SkyExplorerObjectTypeDefinition:
    key: str
    title: str
    description: str
    query_layers: tuple[str, ...]
    simple_visible: bool = False
    default_checked: bool = False
    group_key: str = ""
    group_title: str = ""
    stroke_color: str = ""
    fill_color: str = ""

    def __post_init__(self) -> None:
        group_key, group_title = _sky_explorer_object_type_group(self.key, self.title, self.description, self.query_layers)
        if not self.group_key:
            object.__setattr__(self, "group_key", group_key)
        if not self.group_title:
            object.__setattr__(self, "group_title", group_title)
        resolved_group_key = self.group_key or group_key
        stroke_color, fill_color = _sky_explorer_object_type_colors(self.key, resolved_group_key)
        if not self.stroke_color:
            object.__setattr__(self, "stroke_color", stroke_color)
        if not self.fill_color:
            object.__setattr__(self, "fill_color", fill_color)


def _sky_explorer_rgb_to_hex(red: float, green: float, blue: float) -> str:
    return "#{0:02x}{1:02x}{2:02x}".format(
        max(0, min(255, int(round(red * 255.0)))),
        max(0, min(255, int(round(green * 255.0)))),
        max(0, min(255, int(round(blue * 255.0)))),
    )


SKY_EXPLORER_OBJECT_TYPE_GROUP_ORDER: tuple[str, ...] = (
    "nebula",
    "galaxy",
    "active_galaxy",
    "cluster",
    "star",
    "variable_star",
    "stellar_remnant",
    "high_energy",
    "solar_system",
    "exoplanet",
    "catalog",
    "other",
)

_SKY_EXPLORER_OBJECT_TYPE_GROUP_TITLES: dict[str, str] = {
    "nebula": "Nebulae / ISM",
    "galaxy": "Galaxies",
    "active_galaxy": "Active Galaxies / AGN",
    "cluster": "Star Clusters / Associations",
    "star": "Stars",
    "variable_star": "Variable Stars",
    "stellar_remnant": "Stellar Remnants",
    "high_energy": "High-Energy / Exotic",
    "solar_system": "Solar System",
    "exoplanet": "Exoplanets",
    "catalog": "Catalogs",
    "other": "Other / Unclassified",
}

# HSV families chosen for contrast on typical Ha-red / broadband sky images.
# Nebulae stay warm (amber/gold) so they do not disappear against H-alpha.
_SKY_EXPLORER_OBJECT_TYPE_GROUP_PALETTES: dict[str, tuple[float, float, float]] = {
    "nebula": (0.10, 0.80, 0.94),
    "galaxy": (0.48, 0.62, 0.78),
    "active_galaxy": (0.73, 0.70, 0.92),
    "cluster": (0.57, 0.58, 0.94),
    "star": (0.58, 0.22, 0.97),
    "variable_star": (0.92, 0.70, 0.95),
    "stellar_remnant": (0.78, 0.48, 0.88),
    "high_energy": (0.86, 0.76, 0.96),
    "solar_system": (0.33, 0.68, 0.82),
    "exoplanet": (0.42, 0.60, 0.86),
    "catalog": (0.10, 0.22, 0.98),
    "other": (0.60, 0.10, 0.70),
}

# Explicit hue/sat/value offsets for named Simple/Advanced classes.
_SKY_EXPLORER_OBJECT_TYPE_COLOR_SHIFTS: dict[str, tuple[float, float, float]] = {
    "emission_nebula": (0.00, 0.04, 0.00),
    "reflection_nebula": (0.045, -0.14, 0.04),
    "dark_nebula": (-0.02, 0.06, -0.30),
    "planetary_nebula": (0.05, -0.08, 0.02),
    "hii_region": (-0.02, 0.06, 0.00),
    "supernova_remnant": (-0.035, 0.04, -0.08),
    "molecular_cloud": (0.02, 0.00, -0.18),
    "star_forming_region": (0.01, 0.02, 0.00),
    "nebula": (0.00, -0.02, -0.04),
    "galaxy": (0.00, 0.00, 0.00),
    "galaxy_pair": (-0.02, 0.04, 0.02),
    "galaxy_group": (0.02, 0.00, -0.04),
    "galaxy_cluster": (0.03, 0.06, -0.08),
    "open_cluster": (-0.02, -0.08, 0.06),
    "globular_cluster": (0.05, 0.10, -0.10),
    "asterism": (0.00, -0.16, 0.04),
    "association": (0.03, -0.04, 0.00),
    "cluster_with_nebulosity": (-0.04, 0.04, 0.00),
    "messier": (-0.01, -0.10, 0.01),
    "ngc": (0.03, 0.06, 0.00),
    "ic": (0.04, 0.16, -0.01),
    "lbn": (0.02, 0.22, -0.01),
    "vdb": (0.01, 0.28, -0.02),
    "ldn": (-0.02, 0.20, -0.02),
    "hash_pn": (-0.03, 0.32, -0.03),
    "sharpless": (-0.04, 0.36, -0.03),
    "barnard": (-0.05, 0.30, -0.04),
}


def sky_explorer_object_type_group_palette(group_key: str) -> tuple[float, float, float]:
    return _SKY_EXPLORER_OBJECT_TYPE_GROUP_PALETTES.get(
        group_key,
        _SKY_EXPLORER_OBJECT_TYPE_GROUP_PALETTES["other"],
    )


def _sky_explorer_vary_object_type_hsv(
    key: str,
    base_hue: float,
    base_saturation: float,
    base_value: float,
) -> tuple[float, float, float]:
    shift = _SKY_EXPLORER_OBJECT_TYPE_COLOR_SHIFTS.get(key)
    if shift is not None:
        hue = (base_hue + shift[0]) % 1.0
        saturation = max(0.18, min(0.94, base_saturation + shift[1]))
        value = max(0.34, min(0.98, base_value + shift[2]))
        return hue, saturation, value
    digest = hashlib.sha1(key.encode("utf-8")).digest()
    hue = (base_hue + (((digest[0] / 255.0) - 0.5) * 0.05)) % 1.0
    saturation = max(0.22, min(0.92, base_saturation + (((digest[1] / 255.0) - 0.5) * 0.10)))
    value = max(0.42, min(0.98, base_value + (((digest[2] / 255.0) - 0.5) * 0.08)))
    return hue, saturation, value


def _sky_explorer_hsv_to_marker_colors(hue: float, saturation: float, value: float) -> tuple[str, str]:
    red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value)
    fill_color = _sky_explorer_rgb_to_hex(red, green, blue)
    stroke_color = _sky_explorer_rgb_to_hex(red * 0.58, green * 0.58, blue * 0.58)
    return stroke_color, fill_color


def _sky_explorer_object_type_colors(key: str, group_key: str = "other") -> tuple[str, str]:
    base_hue, base_saturation, base_value = sky_explorer_object_type_group_palette(group_key)
    hue, saturation, value = _sky_explorer_vary_object_type_hsv(key, base_hue, base_saturation, base_value)
    return _sky_explorer_hsv_to_marker_colors(hue, saturation, value)


def _sky_explorer_hex_to_rgb(value: str) -> tuple[float, float, float] | None:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 7 or not normalized.startswith("#"):
        return None
    try:
        red = int(normalized[1:3], 16) / 255.0
        green = int(normalized[3:5], 16) / 255.0
        blue = int(normalized[5:7], 16) / 255.0
    except ValueError:
        return None
    return red, green, blue


def sky_explorer_object_type_colors_for_group_hue(key: str, group_key: str, base_color: str) -> tuple[str, str]:
    parsed_color = _sky_explorer_hex_to_rgb(base_color)
    if parsed_color is None:
        return _sky_explorer_object_type_colors(key, group_key)
    default_hue, default_saturation, default_value = sky_explorer_object_type_group_palette(group_key)
    base_hue, base_saturation, base_value = colorsys.rgb_to_hsv(*parsed_color)
    if base_saturation < 0.12:
        base_hue = default_hue
        base_saturation = default_saturation
    base_saturation = max(0.30, min(0.92, base_saturation))
    base_value = max(0.50, min(0.98, base_value if base_value >= 0.35 else default_value))
    hue, saturation, value = _sky_explorer_vary_object_type_hsv(key, base_hue, base_saturation, base_value)
    return _sky_explorer_hsv_to_marker_colors(hue, saturation, value)


def sky_explorer_object_type_group_definitions() -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (
            group_key,
            _SKY_EXPLORER_OBJECT_TYPE_GROUP_TITLES[group_key],
            _sky_explorer_object_type_colors(f"{group_key}:group", group_key)[1],
        )
        for group_key in SKY_EXPLORER_OBJECT_TYPE_GROUP_ORDER
    )


def _sky_explorer_selection_key(value: str) -> str:
    return str(value or "").strip().casefold()


def _sky_explorer_group_title(group_key: str) -> str:
    return _SKY_EXPLORER_OBJECT_TYPE_GROUP_TITLES.get(group_key, _SKY_EXPLORER_OBJECT_TYPE_GROUP_TITLES["other"])


def _sky_explorer_object_type_group(
    key: str,
    title: str,
    description: str,
    query_layers: tuple[str, ...],
) -> tuple[str, str]:
    normalized_key = _sky_explorer_selection_key(key)
    text = f"{title} {description}".casefold()
    if "solar_system" in query_layers or normalized_key == "asteroid_comet":
        return "solar_system", _sky_explorer_group_title("solar_system")
    if "exoplanets" in query_layers or "exoplanet" in normalized_key or "extra-solar planet" in text:
        return "exoplanet", _sky_explorer_group_title("exoplanet")
    if "quasar" in text or "blazar" in text or "seyfert" in text or "active galax" in text or "agn" in text or "liner" in text or normalized_key in {"q?", "qso", "bz?", "bl?", "bla", "bll", "ovv", "ag?", "agn", "lin", "syg", "sy1", "sy2", "rg"}:
        return "active_galaxy", _sky_explorer_group_title("active_galaxy")
    if "galax" in text or normalized_key in {"g", "g?", "scg", "clg", "grg", "cgg", "pag", "ig", "sc?", "c?g", "gr?", "pog", "gic", "bic", "gig", "gip", "h2g", "lsb", "emg", "sbg", "bcg"}:
        return "galaxy", _sky_explorer_group_title("galaxy")
    if any(token in text for token in ("nebula", "cloud", "hii", "molecular", "supernova remnant", "star-forming", "herbig-haro", "outflow", "bubble", "globule", "shell")) or normalized_key in {"pn?", "cgb", "bub", "emo", "cld", "gne", "dne", "rne", "moc", "glb", "cor", "sfr", "hvc", "hii", "pn", "sh", "sr?", "snr", "of?", "out", "hh"}:
        return "nebula", _sky_explorer_group_title("nebula")
    if any(token in text for token in ("cluster", "association", "asterism", "stellar stream", "moving group")) or normalized_key in {"c?*", "gl?", "cl*", "glc", "opc", "as*", "st*", "mgr"}:
        return "cluster", _sky_explorer_group_title("cluster")
    if "variable_stars" in query_layers or "variable" in text or normalized_key in {"v*", "v*?", "ir*", "or*", "er*", "rc*", "rc?", "ro*", "a2*", "psr", "by*", "rs*", "pu*", "rr*", "ce*", "ds*", "rv*", "wv*", "wv?", "bc*", "cc*", "gd*", "sx*", "lp*", "lp?", "mi*", "mi?", "eb?", "eb*", "wu*", "el*", "cv*", "no*"}:
        return "variable_star", _sky_explorer_group_title("variable_star")
    if any(token in text for token in ("white dwarf", "neutron star", "black hole", "pulsar")) or normalized_key in {"white_dwarf", "wd*", "wd?", "n*", "n*?", "bh?", "psr"}:
        return "stellar_remnant", _sky_explorer_group_title("stellar_remnant")
    if any(token in text for token in ("radio", "infrared", "ultraviolet", "x-ray", "xray", "gamma", "maser", "gravitational", "lensing", "wave event")) or normalized_key in {"rad", "mr", "cm", "mm", "smm", "hi", "rb", "mas", "ir", "fir", "mir", "nir", "uv", "x", "ux?", "ulx", "gam", "gb", "grv", "lev", "ls?", "le?", "li?", "gle", "gls", "gwe"}:
        return "high_energy", _sky_explorer_group_title("high_energy")
    if "gaia_stars" in query_layers or "star" in text or normalized_key in {"star", "*", "blu", "y*o", "y*?", "ae*", "em*", "be*", "bs*", "rg*", "ab*", "c*", "s*", "sg*", "s*r", "s*y", "s*b", "hs*", "pagb", "lm*", "lm?", "bd*", "bd?", "oh*", "tt*", "tt?", "wr*", "wr?", "pm*", "hv*", "**", "**?", "sb*"}:
        return "star", _sky_explorer_group_title("star")
    return "other", _sky_explorer_group_title("other")


def _simple_object_type_definition(
    key: str,
    title: str,
    description: str,
    *query_layers: str,
) -> SkyExplorerObjectTypeDefinition:
    return SkyExplorerObjectTypeDefinition(
        key=key,
        title=title,
        description=description,
        query_layers=tuple(query_layers),
        simple_visible=True,
        default_checked=True,
    )


def _advanced_object_type_definition(
    code: str,
    description: str,
    *query_layers: str,
) -> SkyExplorerObjectTypeDefinition:
    return SkyExplorerObjectTypeDefinition(
        key=_sky_explorer_selection_key(code),
        title=code,
        description=description,
        query_layers=tuple(query_layers),
    )


def _detailed_object_type_definition(
    key: str,
    title: str,
    description: str,
    *query_layers: str,
) -> SkyExplorerObjectTypeDefinition:
    return SkyExplorerObjectTypeDefinition(
        key=key,
        title=title,
        description=description,
        query_layers=tuple(query_layers),
    )


_SKY_EXPLORER_SIMPLE_OBJECT_TYPE_DEFINITIONS: tuple[SkyExplorerObjectTypeDefinition, ...] = (
    _simple_object_type_definition("emission_nebula", "Emission Nebula", "Ionized gas clouds glowing from nearby stars.", "deep_sky"),
    _simple_object_type_definition("reflection_nebula", "Reflection Nebula", "Dust clouds reflecting nearby starlight.", "deep_sky"),
    _simple_object_type_definition("dark_nebula", "Dark Nebula", "Light-blocking dust lanes and dark molecular clouds.", "deep_sky"),
    _simple_object_type_definition("galaxy", "Galaxy", "External galaxies inside the solved image footprint.", "deep_sky"),
    _simple_object_type_definition("open_cluster", "Open Cluster", "Loose groups of stars such as Messier and NGC open clusters.", "deep_sky"),
    _simple_object_type_definition("globular_cluster", "Globular Cluster", "Dense spherical star clusters in the Milky Way halo.", "deep_sky"),
)


_SKY_EXPLORER_ADVANCED_OBJECT_TYPE_DEFINITIONS: tuple[SkyExplorerObjectTypeDefinition, ...] = (
    _SKY_EXPLORER_SIMPLE_OBJECT_TYPE_DEFINITIONS
    + (
        _detailed_object_type_definition("planetary_nebula", "Planetary Nebula", "Ionized shells around evolved stars.", "deep_sky"),
        _detailed_object_type_definition("hii_region", "HII Region", "Ionized hydrogen regions and bright star-forming gas clouds.", "deep_sky"),
        _detailed_object_type_definition("supernova_remnant", "Supernova Remnant", "Expanding debris shells left behind by supernova explosions.", "deep_sky"),
        _detailed_object_type_definition("molecular_cloud", "Molecular Cloud", "Dense gas and dust clouds associated with star formation.", "deep_sky"),
        _detailed_object_type_definition("star_forming_region", "Star-Forming Region", "Named star-birth regions and embedded nursery complexes.", "deep_sky"),
        _detailed_object_type_definition("nebula", "Nebula", "General nebulous objects not matched to a narrower nebula class.", "deep_sky"),
        _detailed_object_type_definition("asterism", "Asterism", "Named stellar patterns that are not formal clusters.", "deep_sky"),
        _detailed_object_type_definition("association", "Association", "Loose stellar associations and related groupings.", "deep_sky"),
        _detailed_object_type_definition("cluster_with_nebulosity", "Cluster with Nebulosity", "Clusters embedded in or attached to visible nebulosity.", "deep_sky"),
        _detailed_object_type_definition("galaxy_pair", "Galaxy Pair", "Interacting or visually paired galaxies.", "deep_sky"),
        _detailed_object_type_definition("galaxy_group", "Galaxy Group", "Small physical groups of galaxies.", "deep_sky"),
        _detailed_object_type_definition("galaxy_cluster", "Galaxy Cluster", "Large galaxy clusters and clusters of galaxies.", "deep_sky"),
        _detailed_object_type_definition("quasar", "Quasar", "Quasars and quasi-stellar active galactic nuclei.", "deep_sky"),
        _detailed_object_type_definition("active_galactic_nucleus", "Active Galactic Nucleus", "Active nuclei and related energetic galaxy cores.", "deep_sky"),
        _detailed_object_type_definition("seyfert_galaxy", "Seyfert Galaxy", "Seyfert active galaxies identified by their nuclear spectra.", "deep_sky"),
        _detailed_object_type_definition("blazar", "Blazar", "Blazars, BL Lac objects, and strongly beamed active nuclei.", "deep_sky"),
        _detailed_object_type_definition("radio_source", "Radio Source", "Named radio emitters and radio-selected sources.", "deep_sky"),
        _detailed_object_type_definition("infrared_source", "Infrared Source", "Infrared-selected sources and embedded IR objects.", "deep_sky"),
        _detailed_object_type_definition("ultraviolet_source", "Ultraviolet Source", "Ultraviolet-selected astronomical sources.", "deep_sky"),
        _detailed_object_type_definition("xray_source", "X-Ray Source", "X-ray emitters such as compact remnants and hot active systems.", "deep_sky"),
        _detailed_object_type_definition("gamma_source", "Gamma-Ray Source", "Gamma-ray and high-energy sources.", "deep_sky"),
        _detailed_object_type_definition("gravitational_lens", "Gravitational Lens", "Objects identified as gravitational lenses or lens systems.", "deep_sky"),
        _detailed_object_type_definition("asteroid_comet", "Asteroid/Comet", "Known solar-system objects predicted in the field.", "solar_system"),
        _detailed_object_type_definition("variable_star", "Variable Star", "Catalogued variable stars from VSX-style sources.", "variable_stars", "general_objects"),
        _detailed_object_type_definition("star", "Star", "Catalogued field stars from Gaia or similar catalogs.", "gaia_stars", "general_objects"),
        _detailed_object_type_definition("binary_or_multiple_star", "Binary/Multiple Star", "Binary stars, double stars, and higher-multiplicity systems.", "general_objects"),
        _detailed_object_type_definition("young_stellar_object", "Young Stellar Object", "YSOs, T Tauri stars, Herbig objects, and related young stars.", "general_objects"),
        _detailed_object_type_definition("emission_line_star", "Emission-Line Star", "Stars identified by prominent emission-line spectra.", "general_objects"),
        _detailed_object_type_definition("white_dwarf", "White Dwarf", "White dwarfs and degenerate stellar remnants.", "general_objects"),
        _detailed_object_type_definition("brown_dwarf", "Brown Dwarf", "Brown dwarfs and substellar dwarf objects.", "general_objects"),
        _detailed_object_type_definition("exoplanet_host", "Exoplanet Host", "Known exoplanet host stars in the image footprint.", "exoplanets", "general_objects"),
        _detailed_object_type_definition("general_object", "General Object", "Named catalog objects that do not fit a more specific type bucket.", "general_objects"),
        _detailed_object_type_definition("other_deep_sky", "Other Deep-Sky", "Remaining non-stellar deep-sky objects not matched to a named class.", "deep_sky"),
    )
)


_SKY_EXPLORER_SIMBAD_ADVANCED_ROWS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("?", "Object of unknown nature", ("general_objects",)),
    ("ev", "Transient event", ("general_objects",)),
    ("Rad", "Radio-source", ("deep_sky",)),
    ("mR", "Metric radio-source", ("deep_sky",)),
    ("cm", "Centimetric radio-source", ("deep_sky",)),
    ("mm", "Millimetric radio-source", ("deep_sky",)),
    ("smm", "Sub-millimetric source", ("deep_sky",)),
    ("HI", "HI (21cm) source", ("deep_sky",)),
    ("rB", "Radio burst", ("deep_sky",)),
    ("Mas", "Maser", ("deep_sky",)),
    ("IR", "Infrared source", ("deep_sky",)),
    ("FIR", "Far-infrared source", ("deep_sky",)),
    ("MIR", "Mid-infrared source", ("deep_sky",)),
    ("NIR", "Near-infrared source", ("deep_sky",)),
    ("blu", "Blue object", ("general_objects",)),
    ("UV", "UV-emission source", ("deep_sky",)),
    ("X", "X-ray source", ("deep_sky",)),
    ("UX?", "Ultra-luminous X-ray candidate", ("deep_sky",)),
    ("ULX", "Ultra-luminous X-ray source", ("deep_sky",)),
    ("gam", "Gamma-ray source", ("deep_sky",)),
    ("gB", "Gamma-ray burst", ("deep_sky",)),
    ("grv", "Gravitational source", ("deep_sky",)),
    ("Lev", "(Micro)lensing event", ("deep_sky",)),
    ("LS?", "Possible gravitational lens system", ("deep_sky",)),
    ("Le?", "Possible gravitational lens", ("deep_sky",)),
    ("LI?", "Possible gravitationally lensed image", ("deep_sky",)),
    ("gLe", "Gravitational lens", ("deep_sky",)),
    ("gLS", "Gravitational lens system (lens plus images)", ("deep_sky",)),
    ("GWE", "Gravitational wave event", ("deep_sky",)),
    ("vid", "Underdense region of the Universe", ("deep_sky",)),
    ("SCG", "Supercluster of galaxies", ("deep_sky",)),
    ("ClG", "Cluster of galaxies", ("deep_sky",)),
    ("GrG", "Group of galaxies", ("deep_sky",)),
    ("CGG", "Compact group of galaxies", ("deep_sky",)),
    ("PaG", "Pair of galaxies", ("deep_sky",)),
    ("IG", "Interacting galaxies", ("deep_sky",)),
    ("C?*", "Possible open star cluster", ("deep_sky",)),
    ("Gl?", "Possible globular cluster", ("deep_sky",)),
    ("Cl*", "Cluster of stars", ("deep_sky",)),
    ("GlC", "Globular cluster", ("deep_sky",)),
    ("OpC", "Open galactic cluster", ("deep_sky",)),
    ("As*", "Association of stars", ("deep_sky",)),
    ("St*", "Stellar stream", ("deep_sky",)),
    ("MGr", "Moving group", ("deep_sky",)),
    ("PN?", "Possible planetary nebula", ("deep_sky",)),
    ("CGb", "Cometary globule", ("deep_sky",)),
    ("bub", "Bubble", ("deep_sky",)),
    ("EmO", "Emission object", ("deep_sky",)),
    ("Cld", "Cloud", ("deep_sky",)),
    ("GNe", "Galactic nebula", ("deep_sky",)),
    ("DNe", "Dark cloud (nebula)", ("deep_sky",)),
    ("RNe", "Reflection nebula", ("deep_sky",)),
    ("MoC", "Molecular cloud", ("deep_sky",)),
    ("glb", "Globule (low-mass dark cloud)", ("deep_sky",)),
    ("cor", "Dense core", ("deep_sky",)),
    ("SFR", "Star-forming region", ("deep_sky",)),
    ("HVC", "High-velocity cloud", ("deep_sky",)),
    ("HII", "HII ionized region", ("deep_sky",)),
    ("PN", "Planetary nebula", ("deep_sky",)),
    ("sh", "HI shell", ("deep_sky",)),
    ("SR?", "SuperNova remnant candidate", ("deep_sky",)),
    ("SNR", "SuperNova remnant", ("deep_sky",)),
    ("of?", "Outflow candidate", ("deep_sky",)),
    ("out", "Outflow", ("deep_sky",)),
    ("HH", "Herbig-Haro object", ("deep_sky",)),
    ("*", "Star", ("general_objects",)),
    ("V*?", "Star suspected of variability", ("general_objects",)),
    ("Y*O", "Young stellar object", ("general_objects",)),
    ("Y*?", "Young stellar object candidate", ("general_objects",)),
    ("Ae*", "Herbig Ae/Be star", ("general_objects",)),
    ("Em*", "Emission-line star", ("general_objects",)),
    ("Be*", "Be star", ("general_objects",)),
    ("BS*", "Blue straggler star", ("general_objects",)),
    ("RG*", "Red giant branch star", ("general_objects",)),
    ("AB*", "Asymptotic giant branch star", ("general_objects",)),
    ("C*", "Carbon star", ("general_objects",)),
    ("S*", "S star", ("general_objects",)),
    ("sg*", "Evolved supergiant star", ("general_objects",)),
    ("s*r", "Red supergiant star", ("general_objects",)),
    ("s*y", "Yellow supergiant star", ("general_objects",)),
    ("s*b", "Blue supergiant star", ("general_objects",)),
    ("HS*", "Hot subdwarf", ("general_objects",)),
    ("pA*", "Post-AGB star (proto-PN)", ("general_objects",)),
    ("WD*", "White dwarf", ("general_objects",)),
    ("WD?", "White dwarf candidate", ("general_objects",)),
    ("LM*", "Low-mass star", ("general_objects",)),
    ("LM?", "Low-mass star candidate", ("general_objects",)),
    ("BD*", "Brown dwarf", ("general_objects",)),
    ("BD?", "Brown dwarf candidate", ("general_objects",)),
    ("N*", "Confirmed neutron star", ("general_objects",)),
    ("N*?", "Neutron star candidate", ("general_objects",)),
    ("BH?", "Black hole candidate", ("general_objects",)),
    ("OH*", "OH/IR star", ("general_objects",)),
    ("TT*", "T Tau-type star", ("general_objects",)),
    ("TT?", "T Tau star candidate", ("general_objects",)),
    ("WR*", "Wolf-Rayet star", ("general_objects",)),
    ("WR?", "Possible Wolf-Rayet star", ("general_objects",)),
    ("PM*", "High proper-motion star", ("general_objects",)),
    ("HV*", "High-velocity star", ("general_objects",)),
    ("V*", "Variable star", ("general_objects", "variable_stars")),
    ("Ir*", "Variable star of irregular type", ("general_objects", "variable_stars")),
    ("Or*", "Variable star of Orion type", ("general_objects", "variable_stars")),
    ("Er*", "Eruptive variable star", ("general_objects", "variable_stars")),
    ("RC*", "Variable star of R CrB type", ("general_objects", "variable_stars")),
    ("RC?", "Variable star of R CrB type candidate", ("general_objects", "variable_stars")),
    ("Ro*", "Rotationally variable star", ("general_objects", "variable_stars")),
    ("a2*", "Variable star of alpha2 CVn type", ("general_objects", "variable_stars")),
    ("Psr", "Pulsar", ("general_objects", "variable_stars")),
    ("BY*", "Variable of BY Dra type", ("general_objects", "variable_stars")),
    ("RS*", "Variable of RS CVn type", ("general_objects", "variable_stars")),
    ("Pu*", "Pulsating variable star", ("general_objects", "variable_stars")),
    ("RR*", "Variable star of RR Lyr type", ("general_objects", "variable_stars")),
    ("Ce*", "Cepheid variable star", ("general_objects", "variable_stars")),
    ("dS*", "Variable star of delta Sct type", ("general_objects", "variable_stars")),
    ("RV*", "Variable star of RV Tau type", ("general_objects", "variable_stars")),
    ("WV*", "Variable star of W Vir type", ("general_objects", "variable_stars")),
    ("WV?", "Possible variable star of W Vir type", ("general_objects", "variable_stars")),
    ("bC*", "Variable star of beta Cep type", ("general_objects", "variable_stars")),
    ("cC*", "Classical Cepheid (delta Cep type)", ("general_objects", "variable_stars")),
    ("gD*", "Variable star of gamma Dor type", ("general_objects", "variable_stars")),
    ("SX*", "Variable star of SX Phe type (subdwarf)", ("general_objects", "variable_stars")),
    ("LP*", "Long-period variable star", ("general_objects", "variable_stars")),
    ("LP?", "Long-period variable candidate", ("general_objects", "variable_stars")),
    ("Mi*", "Variable star of Mira Cet type", ("general_objects", "variable_stars")),
    ("Mi?", "Mira candidate", ("general_objects", "variable_stars")),
    ("SN*", "SuperNova", ("general_objects",)),
    ("SN?", "SuperNova candidate", ("general_objects",)),
    ("su*", "Sub-stellar object", ("general_objects",)),
    ("Pl?", "Extra-solar planet candidate", ("general_objects", "exoplanets")),
    ("Pl", "Extra-solar confirmed planet", ("general_objects", "exoplanets")),
    ("**?", "Physical binary candidate", ("general_objects",)),
    ("EB?", "Eclipsing binary candidate", ("general_objects", "variable_stars")),
    ("Sy?", "Symbiotic star candidate", ("general_objects",)),
    ("CV?", "Cataclysmic binary candidate", ("general_objects",)),
    ("No?", "Nova candidate", ("general_objects", "variable_stars")),
    ("XB?", "X-ray binary candidate", ("general_objects",)),
    ("LX?", "Low-mass X-ray binary candidate", ("general_objects",)),
    ("HX?", "High-mass X-ray binary candidate", ("general_objects",)),
    ("**", "Double or multiple star", ("general_objects",)),
    ("EB*", "Eclipsing binary", ("general_objects", "variable_stars")),
    ("SB*", "Spectroscopic binary", ("general_objects",)),
    ("El*", "Ellipsoidal variable star", ("general_objects", "variable_stars")),
    ("Sy*", "Symbiotic star", ("general_objects",)),
    ("CV*", "Cataclysmic variable star", ("general_objects", "variable_stars")),
    ("No*", "Nova", ("general_objects", "variable_stars")),
    ("XB*", "X-ray binary", ("general_objects",)),
    ("LXB", "Low-mass X-ray binary", ("general_objects",)),
    ("HXB", "High-mass X-ray binary", ("general_objects",)),
    ("WU*", "Eclipsing binary of W UMa type", ("general_objects", "variable_stars")),
    ("G?", "Possible galaxy", ("deep_sky",)),
    ("SC?", "Possible supercluster of galaxies", ("deep_sky",)),
    ("C?G", "Possible cluster of galaxies", ("deep_sky",)),
    ("Gr?", "Possible group of galaxies", ("deep_sky",)),
    ("G", "Galaxy", ("deep_sky",)),
    ("PoG", "Part of a galaxy", ("deep_sky",)),
    ("GiC", "Galaxy in cluster of galaxies", ("deep_sky",)),
    ("BiC", "Brightest galaxy in a cluster", ("deep_sky",)),
    ("GiG", "Galaxy in group of galaxies", ("deep_sky",)),
    ("GiP", "Galaxy in pair of galaxies", ("deep_sky",)),
    ("rG", "Radio galaxy", ("deep_sky",)),
    ("H2G", "HII galaxy", ("deep_sky",)),
    ("LSB", "Low surface brightness galaxy", ("deep_sky",)),
    ("AG?", "Possible active galaxy nucleus", ("deep_sky",)),
    ("Q?", "Possible quasar", ("deep_sky",)),
    ("Bz?", "Possible blazar", ("deep_sky",)),
    ("BL?", "Possible BL Lac", ("deep_sky",)),
    ("EmG", "Emission-line galaxy", ("deep_sky",)),
    ("SBG", "Starburst galaxy", ("deep_sky",)),
    ("bCG", "Blue compact galaxy", ("deep_sky",)),
    ("LeI", "Gravitationally lensed image", ("deep_sky",)),
    ("LeG", "Gravitationally lensed image of a galaxy", ("deep_sky",)),
    ("LeQ", "Gravitationally lensed image of a quasar", ("deep_sky",)),
    ("AGN", "Active galaxy nucleus", ("deep_sky",)),
    ("LIN", "LINER-type active galaxy nucleus", ("deep_sky",)),
    ("SyG", "Seyfert galaxy", ("deep_sky",)),
    ("Sy1", "Seyfert 1 galaxy", ("deep_sky",)),
    ("Sy2", "Seyfert 2 galaxy", ("deep_sky",)),
    ("Bla", "Blazar", ("deep_sky",)),
    ("BLL", "BL Lac type object", ("deep_sky",)),
    ("OVV", "Optically violently variable object", ("deep_sky",)),
    ("QSO", "Quasar", ("deep_sky",)),
)


_SKY_EXPLORER_SCIENTIFIC_OBJECT_TYPE_DEFINITIONS: tuple[SkyExplorerObjectTypeDefinition, ...] = (
    SkyExplorerObjectTypeDefinition(
        key="asteroid_comet",
        title="Asteroid/Comet",
        description="Known solar-system objects predicted in the field.",
        query_layers=("solar_system",),
    ),
    SkyExplorerObjectTypeDefinition(
        key="star",
        title="Gaia Star",
        description="Catalogued field stars from Gaia or similar catalogs.",
        query_layers=("gaia_stars", "general_objects"),
    ),
    SkyExplorerObjectTypeDefinition(
        key="variable_star",
        title="VSX Variable",
        description="Catalogued variable stars from VSX-style sources.",
        query_layers=("variable_stars", "general_objects"),
    ),
    SkyExplorerObjectTypeDefinition(
        key="exoplanet_host",
        title="Exoplanet Host",
        description="Known exoplanet host stars in the image footprint.",
        query_layers=("exoplanets", "general_objects"),
    ),
    SkyExplorerObjectTypeDefinition(
        key="general_object",
        title="General Object",
        description="Named catalog objects that do not fit a more specific type bucket.",
        query_layers=("general_objects",),
    ),
    SkyExplorerObjectTypeDefinition(
        key="other_deep_sky",
        title="Other Deep-Sky",
        description="Remaining non-stellar deep-sky objects not matched to a named class.",
        query_layers=("deep_sky",),
    ),
)


def _catalog_object_type_definition(key: str, title: str, description: str) -> SkyExplorerObjectTypeDefinition:
    return SkyExplorerObjectTypeDefinition(
        key=key,
        title=title,
        description=description,
        query_layers=("deep_sky",),
        default_checked=True,
        group_key="catalog",
        group_title=_sky_explorer_group_title("catalog"),
    )


_SKY_EXPLORER_CATALOG_OBJECT_TYPE_DEFINITIONS: tuple[SkyExplorerObjectTypeDefinition, ...] = (
    _catalog_object_type_definition("ngc", "NGC", "New General Catalogue objects, labeled with NGC numbers only."),
    _catalog_object_type_definition("ic", "IC", "Index Catalogue objects, labeled with IC numbers only."),
    _catalog_object_type_definition("ldn", "LDN", "Lynds Dark Nebulae, labeled with LDN numbers only."),
    _catalog_object_type_definition("vdb", "VdB", "van den Bergh reflection nebulae, labeled with VdB numbers only."),
    _catalog_object_type_definition("lbn", "LBN", "Lynds Bright Nebulae, labeled with LBN numbers only."),
    _catalog_object_type_definition("messier", "Messier", "Messier catalogue objects, labeled with M numbers only."),
    _catalog_object_type_definition("barnard", "Barnard", "Barnard dark nebulae, labeled with B numbers only."),
    _catalog_object_type_definition("sharpless", "Sharpless", "Sharpless HII regions, labeled with Sh2 numbers only."),
    _catalog_object_type_definition("hash_pn", "HASH PN", "HASH planetary nebulae, labeled with usual names."),
)

SKY_EXPLORER_CATALOG_OBJECT_TYPE_KEYS: tuple[str, ...] = tuple(
    definition.key for definition in _SKY_EXPLORER_CATALOG_OBJECT_TYPE_DEFINITIONS
)
_SKY_EXPLORER_CATALOG_OBJECT_TYPE_KEY_SET = frozenset(SKY_EXPLORER_CATALOG_OBJECT_TYPE_KEYS)

_SKY_EXPLORER_CATALOG_SUPPLEMENT_CATALOGS: dict[str, str] = {
    "ngc": "ngc2000",
    "ic": "ngc2000",
    "messier": "ngc2000",
    "vdb": "vdb",
    "barnard": "barnard",
    "sharpless": "sharpless",
    "hash_pn": "hash_pn",
}

_SKY_EXPLORER_SOURCE_CATALOG_TO_TYPE_KEY: dict[str, str] = {
    "vdb": "vdb",
    "barnard": "barnard",
    "sharpless": "sharpless",
    "hash_pn": "hash_pn",
}


SKY_EXPLORER_OBJECT_TYPE_DEFINITIONS: tuple[SkyExplorerObjectTypeDefinition, ...] = (
    _SKY_EXPLORER_ADVANCED_OBJECT_TYPE_DEFINITIONS
    + tuple(
        _advanced_object_type_definition(code, description, *query_layers)
        for code, description, query_layers in _SKY_EXPLORER_SIMBAD_ADVANCED_ROWS
    )
    + _SKY_EXPLORER_SCIENTIFIC_OBJECT_TYPE_DEFINITIONS
    + _SKY_EXPLORER_CATALOG_OBJECT_TYPE_DEFINITIONS
)

SKY_EXPLORER_SIMPLE_OBJECT_TYPE_KEYS: tuple[str, ...] = tuple(
    definition.key for definition in _SKY_EXPLORER_SIMPLE_OBJECT_TYPE_DEFINITIONS
)

_SKY_EXPLORER_OBJECT_TYPE_BY_KEY = {
    definition.key: definition for definition in SKY_EXPLORER_OBJECT_TYPE_DEFINITIONS
}

_SKY_EXPLORER_OBJECT_TYPE_DEFINITIONS_BY_MODE = {
    "simple": _SKY_EXPLORER_SIMPLE_OBJECT_TYPE_DEFINITIONS,
    "advanced": _SKY_EXPLORER_ADVANCED_OBJECT_TYPE_DEFINITIONS,
    "scientific": tuple(
        _advanced_object_type_definition(code, description, *query_layers)
        for code, description, query_layers in _SKY_EXPLORER_SIMBAD_ADVANCED_ROWS
    )
    + _SKY_EXPLORER_SCIENTIFIC_OBJECT_TYPE_DEFINITIONS,
    "catalog": _SKY_EXPLORER_CATALOG_OBJECT_TYPE_DEFINITIONS,
}

_SKY_EXPLORER_CANONICAL_CODE_BY_NORMALIZED = {
    " ".join(str(code or "").strip().split()).replace(" ", "").upper(): code for code, _, _ in _SKY_EXPLORER_SIMBAD_ADVANCED_ROWS
}

_SKY_EXPLORER_OBJECT_TYPE_CODE_ALIASES = {
    "GX": "G",
    "PLANETARYNEBULA": "PN",
    "PL": "PN",
    "OPENCLUSTER": "OpC",
    "OC": "OpC",
    "GLOBULARCLUSTER": "GlC",
    "GC": "GlC",
    "ASTERISM": "Ast",
    "AST": "Ast",
    "ASSOCIATION": "As*",
    "CLUSTERWITHNEBULOSITY": "Cl*",
    "EMISSIONNEBULA": "EmO",
    "REFLECTIONNEBULA": "RNe",
    "DARKNEBULA": "DNe",
    "MOLECULARCLOUD": "MoC",
    "STARFORMINGREGION": "SFR",
    "HIIREGION": "HII",
    "NEBULA": "GNe",
    "SUPERNOVAREMNANT": "SNR",
    "SUPERNOVAREMNANTCANDIDATE": "SR?",
    "GALAXYPAIR": "PaG",
    "GALAXYGROUP": "GrG",
    "GROUPOFGALAXIES": "GrG",
    "GALAXYCLUSTER": "ClG",
    "CLUSTEROFGALAXIES": "ClG",
    "QUASAR": "QSO",
    "ACTIVEGALACTICNUCLEUS": "AGN",
    "SEYFERTGALAXY": "SyG",
    "BLAZAR": "Bla",
    "RADIOSOURCE": "Rad",
    "INFRAREDSOURCE": "IR",
    "ULTRAVIOLETSOURCE": "UV",
    "UV-EMISSIONSOURCE": "UV",
    "XRAYSOURCE": "X",
    "X-RAYSOURCE": "X",
    "GAMMARAYSOURCE": "gam",
    "GRAVITATIONALLENS": "gLe",
    "YOUNGSTELLAROBJECT": "Y*O",
    "YOUNGSTELLAROBJECTCANDIDATE": "Y*?",
    "EMISSIONLINESTAR": "Em*",
    "WHITEDWARF": "WD*",
    "WHITEDWARFCANDIDATE": "WD?",
    "BROWNDWARF": "BD*",
    "BROWNDWARFCANDIDATE": "BD?",
    "TTAU-TYPESTAR": "TT*",
    "TTAUTYPESTAR": "TT*",
    "TTAUSTARCANDIDATE": "TT?",
    "VARIABLESTAR": "V*",
    "STARSUSPECTEDOFVARIABILITY": "V*?",
    "VARIABLESTAROFWVIRTYPE": "WV*",
    "POSSIBLEVARIABLESTAROFWVIRTYPE": "WV?",
    "VARIABLESTAROFSXPHETYPE(SUBDWARF)": "SX*",
    "WOLFRAYETSTAR": "WR*",
    "POSSIBLEWOLFRAYETSTAR": "WR?",
    "X-RAYBINARY": "XB*",
    "X-RAYBINARYCANDIDATE": "XB?",
    "SYMBIOTICSTAR": "Sy*",
    "SYMBIOTICSTARCANDIDATE": "Sy?",
    "SUB-STELLAROBJECT": "su*",
    "STELLARSTREAM": "St*",
    "HIIGALAXY": "H2G",
    "RADIOGALAXY": "rG",
    "OPC": "OpC",
    "GLC": "GlC",
}

_SKY_EXPLORER_OBJECT_TYPE_PARENT_CODES = {
    "mR": ("Rad",),
    "cm": ("Rad",),
    "mm": ("Rad",),
    "smm": ("Rad",),
    "HI": ("Rad",),
    "rB": ("Rad",),
    "Mas": ("Rad",),
    "FIR": ("IR",),
    "MIR": ("IR",),
    "NIR": ("IR",),
    "UX?": ("X",),
    "ULX": ("X",),
    "Lev": ("grv",),
    "LS?": ("grv",),
    "Le?": ("grv",),
    "LI?": ("grv",),
    "gLe": ("grv",),
    "gLS": ("grv",),
    "OpC": ("Cl*",),
    "GlC": ("Cl*",),
    "St*": ("As*",),
    "MGr": ("As*",),
    "CGb": ("Cld",),
    "GNe": ("Cld",),
    "DNe": ("Cld",),
    "RNe": ("Cld",),
    "MoC": ("Cld",),
    "glb": ("MoC", "Cld"),
    "cor": ("MoC", "Cld"),
    "SFR": ("Cld",),
    "SR?": ("SNR",),
    "Y*O": ("*",),
    "Ae*": ("Y*O", "*"),
    "TT*": ("Y*O", "*"),
    "TT?": ("Y*?",),
    "Em*": ("*",),
    "Be*": ("Em*", "*"),
    "BS*": ("*",),
    "RG*": ("*",),
    "AB*": ("*",),
    "C*": ("AB*", "*"),
    "S*": ("AB*", "*"),
    "sg*": ("*",),
    "s*r": ("sg*", "*"),
    "s*y": ("sg*", "*"),
    "s*b": ("sg*", "*"),
    "HS*": ("*",),
    "pA*": ("*",),
    "WD*": ("*",),
    "LM*": ("*",),
    "BD*": ("*",),
    "N*": ("*",),
    "OH*": ("*",),
    "WR*": ("*",),
    "PM*": ("*",),
    "HV*": ("*",),
    "Ir*": ("V*", "*"),
    "Or*": ("V*", "Y*O", "*"),
    "Er*": ("V*", "*"),
    "RC*": ("Er*", "V*", "*"),
    "Ro*": ("V*", "*"),
    "a2*": ("Ro*", "V*", "*"),
    "Psr": ("V*", "N*", "*"),
    "BY*": ("Ro*", "V*", "*"),
    "RS*": ("Ro*", "V*", "*"),
    "Pu*": ("V*", "*"),
    "RR*": ("Pu*", "V*", "*"),
    "Ce*": ("Pu*", "V*", "*"),
    "dS*": ("Pu*", "V*", "*"),
    "RV*": ("Pu*", "V*", "*"),
    "WV*": ("Pu*", "V*", "*"),
    "bC*": ("Pu*", "V*", "*"),
    "cC*": ("Ce*", "Pu*", "V*", "*"),
    "gD*": ("Pu*", "V*", "*"),
    "SX*": ("Pu*", "V*", "*"),
    "LP*": ("V*", "*"),
    "Mi*": ("LP*", "V*", "*"),
    "SN*": ("V*", "*"),
    "EB?": ("**?",),
    "Sy?": ("**?",),
    "CV?": ("**?",),
    "No?": ("CV?", "**?"),
    "XB?": ("**?",),
    "LX?": ("XB?", "**?"),
    "HX?": ("XB?", "**?"),
    "EB*": ("**", "V*", "*"),
    "WU*": ("EB*", "**", "V*", "*"),
    "SB*": ("**", "*"),
    "El*": ("**", "V*", "*"),
    "Sy*": ("**", "*"),
    "CV*": ("**", "V*", "*"),
    "No*": ("CV*", "**", "V*", "*"),
    "XB*": ("**", "*"),
    "LXB": ("XB*", "**", "*"),
    "HXB": ("XB*", "**", "*"),
    "IG": (),
    "PaG": (),
    "GrG": (),
    "CGG": (),
    "ClG": (),
    "SCG": (),
    "GiC": ("G",),
    "BiC": ("GiC", "G"),
    "GiG": ("G",),
    "GiP": ("G",),
    "rG": ("G",),
    "H2G": ("G",),
    "LSB": ("G",),
    "EmG": ("G",),
    "SBG": ("G",),
    "bCG": ("G",),
    "AG?": ("G",),
    "Q?": ("AG?", "G"),
    "Bz?": ("AG?", "G"),
    "BL?": ("Bz?", "AG?", "G"),
    "LeI": ("G",),
    "LeG": ("LeI", "G"),
    "LeQ": ("LeI", "QSO", "AGN", "G"),
    "AGN": ("G",),
    "LIN": ("AGN", "G"),
    "SyG": ("AGN", "G"),
    "Sy1": ("SyG", "AGN", "G"),
    "Sy2": ("SyG", "AGN", "G"),
    "Bla": ("AGN", "G"),
    "BLL": ("Bla", "AGN", "G"),
    "OVV": ("Bla", "AGN", "G"),
    "QSO": ("AGN", "G"),
}

_SKY_EXPLORER_SIMPLE_CATEGORY_KEYS_BY_CODE = {
    "EmO": ("emission_nebula",),
    "GNe": ("emission_nebula",),
    "SFR": ("emission_nebula",),
    "HII": ("emission_nebula",),
    "PN?": ("emission_nebula",),
    "PN": ("emission_nebula",),
    "RNe": ("reflection_nebula",),
    "DNe": ("dark_nebula",),
    "G": ("galaxy",),
    "IG": ("galaxy_pair",),
    "PaG": ("galaxy_pair",),
    "GrG": ("galaxy_group",),
    "CGG": ("galaxy_group",),
    "ClG": ("galaxy_cluster",),
    "SCG": ("galaxy_cluster",),
    "GiC": ("galaxy",),
    "BiC": ("galaxy",),
    "GiG": ("galaxy",),
    "GiP": ("galaxy",),
    "rG": ("galaxy",),
    "H2G": ("galaxy",),
    "LSB": ("galaxy",),
    "EmG": ("galaxy",),
    "SBG": ("galaxy",),
    "bCG": ("galaxy",),
    "AG?": ("active_galactic_nucleus", "galaxy"),
    "Q?": ("quasar", "active_galactic_nucleus", "galaxy"),
    "Bz?": ("blazar", "active_galactic_nucleus", "galaxy"),
    "BL?": ("blazar", "active_galactic_nucleus", "galaxy"),
    "AGN": ("galaxy",),
    "LIN": ("galaxy",),
    "SyG": ("galaxy",),
    "Sy1": ("galaxy",),
    "Sy2": ("galaxy",),
    "Bla": ("galaxy",),
    "BLL": ("galaxy",),
    "OVV": ("galaxy",),
    "QSO": ("galaxy",),
    "OpC": ("open_cluster",),
    "GlC": ("globular_cluster",),
}


@dataclass(frozen=True, slots=True)
class SkyExplorerCorner:
    label: str
    ra_deg: float
    dec_deg: float


@dataclass(frozen=True, slots=True)
class SkyExplorerFieldFootprint:
    center_ra_deg: float
    center_dec_deg: float
    radius_deg: float
    width_deg: float | None
    height_deg: float | None
    corners: tuple[SkyExplorerCorner, ...]


@dataclass(frozen=True, slots=True)
class SkyExplorerObject:
    layer_key: str
    catalog: str
    source_id: str
    name: str
    object_type: str
    ra_deg: float
    dec_deg: float
    pixel_x: float
    pixel_y: float
    magnitude: float | None
    angular_distance_arcmin: float
    short_label: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SkyExplorerLayerSummary:
    layer_key: str
    title: str
    returned_count: int
    displayed_count: int
    note: str = ""


@dataclass(frozen=True, slots=True)
class SkyExplorerResult:
    source_path: Path
    solved_field: SolvedField
    used_astrometry_fallback: bool
    footprint: SkyExplorerFieldFootprint
    objects: tuple[SkyExplorerObject, ...]
    layer_summaries: tuple[SkyExplorerLayerSummary, ...]
    warning_messages: tuple[str, ...] = ()
    summary_text: str = ""


def sky_explorer_query_layers_for_object_types(selected_object_type_keys: Sequence[str] | None) -> tuple[str, ...]:
    if not selected_object_type_keys:
        return ()
    requested_types = {
        _sky_explorer_selection_key(str(type_key or ""))
        for type_key in selected_object_type_keys
        if str(type_key or "").strip()
    }
    ordered_layers: list[str] = []
    for layer_key in SKY_EXPLORER_LAYER_ORDER:
        if any(
            layer_key in definition.query_layers
            for definition in SKY_EXPLORER_OBJECT_TYPE_DEFINITIONS
            if definition.key in requested_types
        ):
            ordered_layers.append(layer_key)
    return tuple(ordered_layers)


def sky_explorer_normalized_object_type_keys(selected_object_type_keys: Sequence[str] | None) -> frozenset[str]:
    return frozenset(
        _sky_explorer_selection_key(str(type_key or ""))
        for type_key in (selected_object_type_keys or ())
        if str(type_key or "").strip()
    )


def sky_explorer_simbad_otype_codes_for_object_types(
    selected_object_type_keys: Sequence[str] | None,
    *,
    layer_key: str,
) -> tuple[str, ...]:
    selected_keys = sky_explorer_normalized_object_type_keys(selected_object_type_keys)
    if not selected_keys:
        return tuple(code for code, _description, query_layers in _SKY_EXPLORER_SIMBAD_ADVANCED_ROWS if layer_key in query_layers)
    if selected_keys.issubset(_SKY_EXPLORER_CATALOG_OBJECT_TYPE_KEY_SET):
        if layer_key != "deep_sky":
            return ()
        return tuple(code for code, _description, query_layers in _SKY_EXPLORER_SIMBAD_ADVANCED_ROWS if layer_key in query_layers)

    include_all_for_layer = (
        (layer_key == "deep_sky" and "other_deep_sky" in selected_keys)
        or (layer_key == "general_objects" and "general_object" in selected_keys)
    )
    matched_codes: list[str] = []
    for code, _description, query_layers in _SKY_EXPLORER_SIMBAD_ADVANCED_ROWS:
        if layer_key not in query_layers:
            continue
        if include_all_for_layer or selected_keys.intersection(_object_type_keys_for_simbad_code(code, layer_key=layer_key)):
            matched_codes.append(code)
    return tuple(matched_codes)


def sky_explorer_supplement_catalogs_for_object_types(selected_object_type_keys: Sequence[str] | None) -> frozenset[str]:
    selected_keys = sky_explorer_normalized_object_type_keys(selected_object_type_keys)
    if not selected_keys:
        return frozenset(_SKY_EXPLORER_DEEP_SKY_SUPPLEMENT_CATALOGS)
    enabled: set[str] = set()
    for catalog_name, type_keys in _SKY_EXPLORER_DEEP_SKY_SUPPLEMENT_TYPE_KEYS.items():
        if selected_keys.intersection(type_keys):
            enabled.add(catalog_name)
    for catalog_key in selected_keys:
        supplement_name = _SKY_EXPLORER_CATALOG_SUPPLEMENT_CATALOGS.get(catalog_key)
        if supplement_name:
            enabled.add(supplement_name)
    return frozenset(enabled)


def merge_sky_explorer_results(existing: SkyExplorerResult, additional: SkyExplorerResult) -> SkyExplorerResult:
    merged_objects = list(existing.objects)
    seen_keys = {_sky_object_identity_key(item) for item in merged_objects}
    seen_position_keys = {_sky_object_position_key(item) for item in merged_objects}
    seen_designation_keys = {
        designation_key for item in merged_objects for designation_key in _sky_object_designation_keys(item)
    }
    deep_sky_bucket = [item for item in merged_objects if item.layer_key == "deep_sky"]
    non_deep_sky = [item for item in merged_objects if item.layer_key != "deep_sky"]
    deep_sky_seen_keys = {_sky_object_identity_key(item) for item in deep_sky_bucket}
    deep_sky_seen_positions = {_sky_object_position_key(item) for item in deep_sky_bucket}
    deep_sky_seen_designations = {
        designation_key for item in deep_sky_bucket for designation_key in _sky_object_designation_keys(item)
    }

    additional_deep_sky = [item for item in additional.objects if item.layer_key == "deep_sky"]
    additional_other = [item for item in additional.objects if item.layer_key != "deep_sky"]
    _append_unique_deep_sky_objects(
        deep_sky_bucket,
        additional_deep_sky,
        seen_keys=deep_sky_seen_keys,
        seen_position_keys=deep_sky_seen_positions,
        seen_designation_keys=deep_sky_seen_designations,
    )
    for sky_object in additional_other:
        object_key = _sky_object_identity_key(sky_object)
        if object_key in seen_keys:
            continue
        non_deep_sky.append(sky_object)
        seen_keys.add(object_key)
        seen_position_keys.add(_sky_object_position_key(sky_object))
        seen_designation_keys.update(_sky_object_designation_keys(sky_object))

    merged_objects = deep_sky_bucket + non_deep_sky
    ordered_objects = tuple(sorted(merged_objects, key=_sky_explorer_object_sort_key))
    summary_by_layer = {summary.layer_key: summary for summary in existing.layer_summaries}
    for summary in additional.layer_summaries:
        previous = summary_by_layer.get(summary.layer_key)
        if previous is None:
            summary_by_layer[summary.layer_key] = summary
            continue
        summary_by_layer[summary.layer_key] = SkyExplorerLayerSummary(
            layer_key=summary.layer_key,
            title=summary.title or previous.title,
            returned_count=int(previous.returned_count) + int(summary.returned_count),
            displayed_count=sum(1 for item in ordered_objects if item.layer_key == summary.layer_key),
            note=summary.note or previous.note,
        )
    for layer_key, summary in list(summary_by_layer.items()):
        summary_by_layer[layer_key] = SkyExplorerLayerSummary(
            layer_key=summary.layer_key,
            title=summary.title,
            returned_count=summary.returned_count,
            displayed_count=sum(1 for item in ordered_objects if item.layer_key == layer_key),
            note=summary.note,
        )
    layer_summaries = tuple(
        summary_by_layer[layer_key] for layer_key in SKY_EXPLORER_LAYER_ORDER if layer_key in summary_by_layer
    )
    warning_messages = tuple(dict.fromkeys((*existing.warning_messages, *additional.warning_messages)))
    return SkyExplorerResult(
        source_path=existing.source_path,
        solved_field=existing.solved_field,
        used_astrometry_fallback=bool(existing.used_astrometry_fallback or additional.used_astrometry_fallback),
        footprint=existing.footprint,
        objects=ordered_objects,
        layer_summaries=layer_summaries,
        warning_messages=warning_messages,
        summary_text=_summary_text(
            solved_field=existing.solved_field,
            layer_summaries=layer_summaries,
            used_astrometry_fallback=bool(existing.used_astrometry_fallback or additional.used_astrometry_fallback),
        ),
    )


def _object_type_keys_for_simbad_code(code: str, *, layer_key: str) -> frozenset[str]:
    keys: set[str] = {_sky_explorer_selection_key(code)}
    keys.add(sky_explorer_object_type_key_for_catalog_type(code, layer_key=layer_key))
    for parent_code in _SKY_EXPLORER_OBJECT_TYPE_PARENT_CODES.get(code, ()):
        keys.add(_sky_explorer_selection_key(parent_code))
        keys.add(sky_explorer_object_type_key_for_catalog_type(parent_code, layer_key=layer_key))
    for simple_key in _SKY_EXPLORER_SIMPLE_CATEGORY_KEYS_BY_CODE.get(code, ()):
        keys.add(_sky_explorer_selection_key(simple_key))
    return frozenset(key for key in keys if key)


_SKY_EXPLORER_DEEP_SKY_SUPPLEMENT_CATALOGS: tuple[str, ...] = (
    "hyperleda",
    "sharpless",
    "barnard",
    "vdb",
    "hash_pn",
    "ngc2000",
)

_SKY_EXPLORER_DEEP_SKY_SUPPLEMENT_TYPE_KEYS: dict[str, frozenset[str]] = {
    "hyperleda": frozenset(
        {
            "galaxy",
            "galaxy_pair",
            "galaxy_group",
            "galaxy_cluster",
            "seyfert_galaxy",
            "active_galactic_nucleus",
            "quasar",
            "blazar",
            "other_deep_sky",
        }
    ),
    "sharpless": frozenset({"hii_region", "emission_nebula", "nebula", "star_forming_region", "other_deep_sky"}),
    "barnard": frozenset({"dark_nebula", "nebula", "molecular_cloud", "other_deep_sky"}),
    "vdb": frozenset({"reflection_nebula", "nebula", "other_deep_sky"}),
    "hash_pn": frozenset(
        {
            "planetary_nebula",
            "emission_nebula",
            "nebula",
            "hii_region",
            "supernova_remnant",
            "other_deep_sky",
        }
    ),
    "ngc2000": frozenset(
        {
            "galaxy",
            "open_cluster",
            "globular_cluster",
            "planetary_nebula",
            "emission_nebula",
            "nebula",
            "cluster_with_nebulosity",
            "asterism",
            "other_deep_sky",
        }
    ),
}


def sky_explorer_object_type_definitions_for_mode(mode: str | None) -> tuple[SkyExplorerObjectTypeDefinition, ...]:
    normalized_mode = _sky_explorer_selection_key(str(mode or ""))
    return _SKY_EXPLORER_OBJECT_TYPE_DEFINITIONS_BY_MODE.get(normalized_mode, _SKY_EXPLORER_SIMPLE_OBJECT_TYPE_DEFINITIONS)


def sky_explorer_object_type_key_for_object(sky_object: SkyExplorerObject) -> str:
    object_type_keys = sky_explorer_object_type_keys_for_object(sky_object)
    return object_type_keys[0] if object_type_keys else "general_object"


def sky_explorer_object_type_keys_for_object(sky_object: SkyExplorerObject) -> tuple[str, ...]:
    keys: list[str] = []
    _append_sky_explorer_object_type_key(keys, _sky_explorer_primary_object_type_key_for_object(sky_object))

    canonical_code = _canonical_sky_explorer_object_type_code(
        sky_object.metadata.get("catalog_type") or sky_object.object_type or sky_object.metadata.get("object_type") or ""
    )
    if canonical_code is not None:
        _append_sky_explorer_object_type_key(keys, canonical_code)
        for parent_code in _SKY_EXPLORER_OBJECT_TYPE_PARENT_CODES.get(canonical_code, ()): 
            _append_sky_explorer_object_type_key(keys, parent_code)
        for simple_key in _SKY_EXPLORER_SIMPLE_CATEGORY_KEYS_BY_CODE.get(canonical_code, ()): 
            _append_sky_explorer_object_type_key(keys, simple_key)

    for catalog_key in sky_explorer_catalog_keys_for_object(sky_object):
        _append_sky_explorer_object_type_key(keys, catalog_key)

    return tuple(keys)


def sky_explorer_catalog_keys_for_object(sky_object: SkyExplorerObject) -> tuple[str, ...]:
    keys: list[str] = []
    source_catalog_key = _SKY_EXPLORER_SOURCE_CATALOG_TO_TYPE_KEY.get(_sky_explorer_selection_key(sky_object.catalog))
    if source_catalog_key:
        _append_sky_explorer_object_type_key(keys, source_catalog_key)
    paper_local_keys = _paper_local_catalog_designation_keys(sky_object)
    for text in _sky_explorer_object_identifier_texts(sky_object):
        for designation_key in _catalog_designation_keys_from_text(text):
            if designation_key in paper_local_keys:
                continue
            if not _is_primary_catalog_designation_text(text, designation_key):
                continue
            catalog_key = _catalog_object_type_key_from_designation_key(designation_key)
            _append_sky_explorer_object_type_key(keys, catalog_key)
    return tuple(keys)


def sky_explorer_catalog_display_name(
    sky_object: SkyExplorerObject,
    *,
    preferred_keys: Sequence[str] | None = None,
) -> str:
    preferred_set = sky_explorer_normalized_object_type_keys(preferred_keys)
    if not preferred_set:
        preferred_set = _SKY_EXPLORER_CATALOG_OBJECT_TYPE_KEY_SET

    display_by_catalog: dict[str, str] = {}
    paper_local_keys = _paper_local_catalog_designation_keys(sky_object)
    for text in _sky_explorer_object_identifier_texts(sky_object):
        for designation_key in _catalog_designation_keys_from_text(text):
            if designation_key in paper_local_keys:
                continue
            if not _is_primary_catalog_designation_text(text, designation_key):
                continue
            catalog_key = _catalog_object_type_key_from_designation_key(designation_key)
            if catalog_key and catalog_key not in display_by_catalog:
                display_by_catalog[catalog_key] = _display_name_from_designation_key(designation_key)

    source_catalog_key = _SKY_EXPLORER_SOURCE_CATALOG_TO_TYPE_KEY.get(_sky_explorer_selection_key(sky_object.catalog))
    if source_catalog_key and source_catalog_key not in display_by_catalog:
        fallback_name = _normalize_identifier(sky_object.source_id) or _normalize_identifier(sky_object.name)
        if fallback_name:
            display_by_catalog[source_catalog_key] = fallback_name

    for catalog_key in SKY_EXPLORER_CATALOG_OBJECT_TYPE_KEYS:
        if catalog_key not in preferred_set:
            continue
        display_name = _catalog_display_name_for_key(sky_object, catalog_key, display_by_catalog)
        if display_name:
            return display_name
    for catalog_key in SKY_EXPLORER_CATALOG_OBJECT_TYPE_KEYS:
        display_name = _catalog_display_name_for_key(sky_object, catalog_key, display_by_catalog)
        if display_name:
            return display_name
    return _normalize_identifier(sky_object.name) or _normalize_identifier(sky_object.source_id) or "Object"


def _catalog_display_name_for_key(
    sky_object: SkyExplorerObject,
    catalog_key: str,
    display_by_catalog: dict[str, str],
) -> str:
    if catalog_key == "hash_pn" and _sky_explorer_selection_key(sky_object.catalog) == "hash_pn":
        hash_label = _hash_pn_preferred_label(sky_object)
        if hash_label:
            return hash_label
    return display_by_catalog.get(catalog_key) or ""


def _is_hash_id_label(text: object) -> bool:
    designation_key = _catalog_designation_key(text)
    return bool(designation_key and re.fullmatch(r"HASH\d+", designation_key))


def _hash_pn_preferred_label(sky_object: SkyExplorerObject) -> str:
    metadata = sky_object.metadata if isinstance(sky_object.metadata, dict) else {}
    for candidate in (
        metadata.get("usual_name"),
        sky_object.name,
        metadata.get("hash_png"),
        metadata.get("simbad_id"),
    ):
        normalized = _normalize_identifier(candidate)
        if not normalized or _is_hash_id_label(normalized):
            continue
        designation_key = _catalog_designation_key(normalized)
        if designation_key and not designation_key.startswith("HASH"):
            return _display_name_from_designation_key(designation_key)
        return normalized
    return ""


def _sky_explorer_object_identifier_texts(sky_object: SkyExplorerObject) -> tuple[str, ...]:
    texts: list[str] = []
    for value in (sky_object.name, sky_object.source_id):
        normalized = _normalize_identifier(value)
        if normalized:
            texts.append(normalized)
    metadata = sky_object.metadata if isinstance(sky_object.metadata, dict) else {}
    for key in ("main_id", "identifiers", "usual_name", "simbad_id"):
        raw_value = metadata.get(key)
        if raw_value is None:
            continue
        for part in re.split(r"[|;,\n]+", str(raw_value)):
            normalized = _normalize_identifier(part)
            if normalized:
                texts.append(normalized)
    return tuple(dict.fromkeys(texts))


def _paper_local_catalog_designation_keys(sky_object: SkyExplorerObject) -> frozenset[str]:
    keys: set[str] = set()
    for text in _sky_explorer_object_identifier_texts(sky_object):
        normalized = _normalize_identifier(text).upper()
        if "[" not in normalized and not normalized.startswith("CL*"):
            continue
        for pattern, prefix, suffix_group in _CATALOG_DESIGNATION_PATTERNS:
            if prefix not in {"IC", "NGC", "M"}:
                continue
            for match in re.finditer(pattern, normalized):
                if _catalog_designation_match_is_paper_local_id(normalized, match):
                    keys.add(_catalog_designation_match_key(match, prefix, suffix_group))
    return frozenset(keys)


def _is_primary_catalog_designation_text(text: object, designation_key: str) -> bool:
    normalized = _normalize_identifier(text)
    if not normalized:
        return False
    stripped = re.sub(r"^(?:NAME\s+)+", "", normalized, flags=re.IGNORECASE).strip()
    if not stripped:
        return False
    if stripped.startswith("[") or stripped.upper().startswith("CL*"):
        return False
    compact_text = re.sub(r"[\s\-]", "", stripped.upper())
    compact_key = re.sub(r"[\s\-]", "", designation_key.upper())
    compact_display = re.sub(r"[\s\-]", "", _display_name_from_designation_key(designation_key).upper())
    if compact_text in {compact_key, compact_display}:
        return True
    number = compact_key.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    if not number or not compact_text.endswith(number):
        return False
    prefix = compact_key[: len(compact_key) - len(number)]
    aliases = {
        "B": ("B", "BARNARD"),
        "SH2": ("SH2", "SHARPLESS", "SH"),
        "VDB": ("VDB", "VANDENBERGH"),
        "M": ("M", "MESSIER"),
        "IC": ("IC", "I"),
        "NGC": ("NGC", "N"),
        "LDN": ("LDN",),
        "LBN": ("LBN",),
        "HASH": ("HASH",),
    }
    return compact_text in {f"{alias}{number}" for alias in aliases.get(prefix, (prefix,))}


def _catalog_object_type_key_from_designation_key(designation_key: str | None) -> str | None:
    normalized = _normalize_identifier(designation_key).upper().replace(" ", "")
    if not normalized:
        return None
    for prefix, catalog_key in (
        ("NGC", "ngc"),
        ("HASH", "hash_pn"),
        ("SH2", "sharpless"),
        ("LDN", "ldn"),
        ("LBN", "lbn"),
        ("VDB", "vdb"),
        ("IC", "ic"),
        ("M", "messier"),
        ("B", "barnard"),
    ):
        if not normalized.startswith(prefix):
            continue
        rest = normalized[len(prefix):]
        if rest and rest[0].isdigit():
            return catalog_key
    return None


def _append_sky_explorer_object_type_key(target: list[str], key: str | None) -> None:
    if not key:
        return
    normalized_key = _sky_explorer_selection_key(key)
    if normalized_key and normalized_key not in target:
        target.append(normalized_key)


def _canonical_sky_explorer_object_type_code(raw_type: object) -> str | None:
    normalized = _normalize_identifier(raw_type)
    if not normalized:
        return None
    compact = normalized.replace(" ", "").upper()
    if compact in _SKY_EXPLORER_CANONICAL_CODE_BY_NORMALIZED:
        return _SKY_EXPLORER_CANONICAL_CODE_BY_NORMALIZED[compact]
    alias = _SKY_EXPLORER_OBJECT_TYPE_CODE_ALIASES.get(compact)
    if alias is not None:
        return alias
    return None


def sky_explorer_object_type_key_for_catalog_type(
    object_type: str | None,
    *,
    layer_key: str | None = None,
) -> str:
    if layer_key == "solar_system":
        return "asteroid_comet"
    if layer_key == "exoplanets":
        return "exoplanet_host"
    if layer_key == "variable_stars":
        return "variable_star"
    if layer_key == "gaia_stars":
        return "star"

    raw_type = _normalize_identifier(object_type or "").upper()
    compact_type = raw_type.replace(" ", "")

    if _sky_explorer_type_matches(compact_type, raw_type, "EMO", "EMN", "Emission Nebula"):
        return "emission_nebula"
    if _sky_explorer_type_matches(compact_type, raw_type, "RFN", "Reflection Nebula"):
        return "reflection_nebula"
    if _sky_explorer_type_matches(compact_type, raw_type, "DNe", "Dark Nebula"):
        return "dark_nebula"
    if _sky_explorer_type_matches(compact_type, raw_type, "PN", "Pl", "Planetary Nebula"):
        return "planetary_nebula"
    if _sky_explorer_type_matches(compact_type, raw_type, "HII", "HII Region"):
        return "hii_region"
    if _sky_explorer_type_matches(compact_type, raw_type, "SNR", "SuperNova Remnant", "Supernova Remnant"):
        return "supernova_remnant"
    if _sky_explorer_type_matches(compact_type, raw_type, "MoC", "Molecular Cloud"):
        return "molecular_cloud"
    if _sky_explorer_type_matches(compact_type, raw_type, "SFR", "Star-Forming Region", "Star Forming Region"):
        return "star_forming_region"
    if _sky_explorer_type_matches(compact_type, raw_type, "NB", "Neb", "GNe", "Nebula"):
        return "nebula"
    if _sky_explorer_type_matches(compact_type, raw_type, "OC", "OpC", "Open Cluster"):
        return "open_cluster"
    if _sky_explorer_type_matches(compact_type, raw_type, "GC", "GlC", "Globular Cluster"):
        return "globular_cluster"
    if _sky_explorer_type_matches(compact_type, raw_type, "Ast", "Asterism"):
        return "asterism"
    if _sky_explorer_type_matches(compact_type, raw_type, "As*", "Association"):
        return "association"
    if _sky_explorer_type_matches(compact_type, raw_type, "Q?", "QSO", "Quasar"):
        return "quasar"
    if _sky_explorer_type_matches(compact_type, raw_type, "Bz?", "BL?", "BLL", "Bla", "Blazar"):
        return "blazar"
    if _sky_explorer_type_matches(compact_type, raw_type, "SyG", "Sy1", "Sy2", "Seyfert Galaxy"):
        return "seyfert_galaxy"
    if _sky_explorer_type_matches(compact_type, raw_type, "AG?", "AGN", "Active Galactic Nucleus"):
        return "active_galactic_nucleus"
    if _sky_explorer_type_matches(compact_type, raw_type, "IG", "PaG", "Interacting Galaxies", "Pair of Galaxies"):
        return "galaxy_pair"
    if _sky_explorer_type_matches(compact_type, raw_type, "GrG", "CGG", "Group of Galaxies", "Compact Group of Galaxies"):
        return "galaxy_group"
    if _sky_explorer_type_matches(compact_type, raw_type, "ClG", "SCG", "Cluster of Galaxies", "Supercluster of Galaxies"):
        return "galaxy_cluster"
    if _sky_explorer_type_matches(compact_type, raw_type, "G", "GX", "GiG", "GiC", "GiP", "LSB", "SBG", "bCG", "Galaxy"):
        return "galaxy"
    if _sky_explorer_type_matches(compact_type, raw_type, "Rad", "Radio Source"):
        return "radio_source"
    if _sky_explorer_type_matches(compact_type, raw_type, "IR", "Infrared Source"):
        return "infrared_source"
    if _sky_explorer_type_matches(compact_type, raw_type, "UV", "Ultraviolet Source"):
        return "ultraviolet_source"
    if _sky_explorer_type_matches(compact_type, raw_type, "X", "X-Ray Source", "X-Ray Binary"):
        return "xray_source"
    if _sky_explorer_type_matches(compact_type, raw_type, "gam", "Gamma-Ray Source"):
        return "gamma_source"
    if _sky_explorer_type_matches(compact_type, raw_type, "Le?", "gLe", "LeI", "LeG", "Gravitational Lens"):
        return "gravitational_lens"
    if _sky_explorer_type_matches(compact_type, raw_type, "V*", "Variable Star"):
        return "variable_star"
    if _sky_explorer_type_matches(compact_type, raw_type, "**", "SB*", "EB*", "Binary Star", "Multiple Object"):
        return "binary_or_multiple_star"
    if _sky_explorer_type_matches(compact_type, raw_type, "Y*O", "TT*", "Or*", "Young Stellar Object", "T Tau Star"):
        return "young_stellar_object"
    if _sky_explorer_type_matches(compact_type, raw_type, "Em*", "Emission-Line Star"):
        return "emission_line_star"
    if _sky_explorer_type_matches(compact_type, raw_type, "WD*", "White Dwarf"):
        return "white_dwarf"
    if _sky_explorer_type_matches(compact_type, raw_type, "BD*", "Brown Dwarf"):
        return "brown_dwarf"
    if _sky_explorer_type_matches(compact_type, raw_type, "Pl", "Planetary System", "Exoplanet Host Star"):
        return "exoplanet_host"
    if _sky_explorer_type_matches(compact_type, raw_type, "*", "Star"):
        return "star"
    if layer_key == "general_objects":
        return "general_object"
    return "other_deep_sky"


def _sky_explorer_primary_object_type_key_for_object(sky_object: SkyExplorerObject) -> str:
    metadata = sky_object.metadata if isinstance(sky_object.metadata, dict) else {}
    return sky_explorer_object_type_key_for_catalog_type(
        str(metadata.get("catalog_type") or sky_object.object_type or metadata.get("object_type") or ""),
        layer_key=sky_object.layer_key,
    )


def _sky_explorer_type_matches(compact_type: str, raw_type: str, *candidates: str) -> bool:
    for candidate in candidates:
        normalized_candidate = _normalize_identifier(candidate).upper()
        compact_candidate = normalized_candidate.replace(" ", "")
        if compact_candidate and compact_type == compact_candidate:
            return True
        if len(normalized_candidate) >= 4 and normalized_candidate in raw_type:
            return True
    return False


class SkyExplorerCancelled(Exception):
    """Raised when the user terminates a Sky Explorer catalog query."""


def _raise_if_sky_explorer_cancelled(cancel_callback: Callable[[], bool] | None) -> None:
    if cancel_callback is not None and cancel_callback():
        raise SkyExplorerCancelled("Sky Explorer query was terminated.")


def explore_sky_image(
    source_path: Path,
    *,
    settings: AppSettings,
    selected_layers: Sequence[str] | None = None,
    selected_object_type_keys: Sequence[str] | None = None,
    gaia_object_limit: int = _MAX_GAIA_STARS,
    include_dense_galaxy_catalog: bool = False,
    ignore_gaia_hard_cap: bool = False,
    progress_callback: Callable[[str], None] | None = None,
    pixel_roi: Sequence[float] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> SkyExplorerResult:
    normalized_layers = _normalize_layers(selected_layers)
    selected_type_keys = sky_explorer_normalized_object_type_keys(selected_object_type_keys)

    def emit_progress(message: str) -> None:
        _raise_if_sky_explorer_cancelled(cancel_callback)
        if progress_callback is not None:
            progress_callback(message)

    emit_progress("Checking the image WCS and field footprint.")
    solved_field, used_astrometry_fallback = _resolve_source_field(source_path, settings, progress_callback=emit_progress)
    survey_mosaic = _is_sky_explorer_survey_tile_wcs_canvas(source_path)
    normalized_pixel_roi = _normalize_sky_explorer_pixel_roi(pixel_roi)
    if survey_mosaic and normalized_pixel_roi is None:
        solved_field = _expand_solved_field_for_survey_mosaic(solved_field)
        emit_progress(
            f"Survey mosaic Explore covers ~{solved_field.radius_deg:.3f} deg "
            f"({2 * SKY_EXPLORER_SURVEY_FIELD_MOSAIC_RADIUS + 1}×"
            f"{2 * SKY_EXPLORER_SURVEY_FIELD_MOSAIC_RADIUS + 1} tiles)."
        )
    wcs = celestial_wcs(read_header(solved_field.wcs_path))
    if normalized_pixel_roi is not None:
        try:
            solved_field, footprint = constrain_solved_field_to_pixel_roi(
                solved_field, wcs, normalized_pixel_roi
            )
        except Exception:
            footprint = _build_field_footprint(solved_field, survey_mosaic=survey_mosaic)
            warning_messages_roi = "Could not apply the drawn ROI; Explore used the full field."
        else:
            warning_messages_roi = None
            emit_progress(
                f"Explore is limited to the drawn ROI ({solved_field.radius_deg:.3f} deg search radius)."
            )
    else:
        footprint = _build_field_footprint(solved_field, survey_mosaic=survey_mosaic)
        warning_messages_roi = None
    field_center = SkyCoord(footprint.center_ra_deg * u.deg, footprint.center_dec_deg * u.deg)
    objects: list[SkyExplorerObject] = []
    layer_summaries: list[SkyExplorerLayerSummary] = []
    warning_messages: list[str] = []
    if warning_messages_roi:
        warning_messages.append(warning_messages_roi)

    catalog_service = CatalogService(settings.cache_dir / "sky-explorer-catalogs")
    if "gaia_stars" in normalized_layers:
        emit_progress("Querying Gaia field stars with the Sky Explorer magnitude settings.")
        gaia_stars = catalog_service.query_gaia_stars_limited(
            solved_field,
            settings.sky_explorer_gaia_max_magnitude,
            row_limit=(
                None
                if ignore_gaia_hard_cap
                else (
                    settings.sky_explorer_gaia_hard_cap_rows
                    if settings.sky_explorer_gaia_hard_cap_enabled
                    else None
                )
            ),
            progress_callback=emit_progress,
        )
        gaia_objects, gaia_summary = _catalog_star_objects(
            gaia_stars,
            layer_key="gaia_stars",
            max_entries=max(0, int(gaia_object_limit)),
            field_center=field_center,
            solved_field=solved_field,
            wcs=wcs,
        )
        objects.extend(gaia_objects)
        layer_summaries.append(gaia_summary)

    field_catalog = None
    if any(layer in normalized_layers for layer in ("variable_stars", "exoplanets")):
        emit_progress("Querying VSX and exoplanet field catalogs.")
        field_catalog = catalog_service.query_field_catalog(
            solved_field,
            include_gaia=False,
            include_variable_stars="variable_stars" in normalized_layers,
            include_exoplanets="exoplanets" in normalized_layers,
            variable_star_max_magnitude=settings.sky_explorer_gaia_max_magnitude,
            exoplanet_max_magnitude=settings.sky_explorer_gaia_max_magnitude,
            progress_callback=emit_progress,
        )

    if field_catalog is not None:
        if "variable_stars" in normalized_layers:
            variable_objects, variable_summary = _catalog_star_objects(
                field_catalog.variable_stars,
                layer_key="variable_stars",
                max_entries=0,
                field_center=field_center,
                solved_field=solved_field,
                wcs=wcs,
            )
            objects.extend(variable_objects)
            layer_summaries.append(variable_summary)
        if "exoplanets" in normalized_layers:
            exoplanet_objects, exoplanet_summary = _catalog_star_objects(
                field_catalog.exoplanets,
                layer_key="exoplanets",
                max_entries=0,
                field_center=field_center,
                solved_field=solved_field,
                wcs=wcs,
            )
            objects.extend(exoplanet_objects)
            layer_summaries.append(exoplanet_summary)

    if any(layer in normalized_layers for layer in ("deep_sky", "general_objects")):
        emit_progress("Querying SIMBAD for named objects in the image footprint.")
        simbad_objects, simbad_summaries = _query_simbad_objects(
            solved_field,
            wcs=wcs,
            field_center=field_center,
            selected_layers=normalized_layers,
            selected_object_type_keys=selected_type_keys,
            include_dense_galaxy_catalog=include_dense_galaxy_catalog,
            maximum_stellar_magnitude=settings.sky_explorer_gaia_max_magnitude,
            cancel_callback=cancel_callback,
        )
        objects.extend(simbad_objects)
        layer_summaries.extend(simbad_summaries)

    if "solar_system" in normalized_layers:
        scan_result = inspect_fits_file(source_path, source_path.parent.name, observation_timezone=settings.observation_timezone)
        observation_time = scan_result.metadata.date_obs
        if observation_time is None:
            warning_messages.append("Skipped asteroid/comet lookup because the image observation time was unavailable.")
            layer_summaries.append(
                SkyExplorerLayerSummary(
                    layer_key="solar_system",
                    title=_LAYER_TITLES["solar_system"],
                    returned_count=0,
                    displayed_count=0,
                    note="Observation timestamp unavailable.",
                )
            )
        else:
            emit_progress("Querying predicted asteroids and comets in the field.")
            solar_system_results = search_nearby_known_solar_system_objects(
                solved_field,
                observation_time=observation_time,
                exposure_seconds=scan_result.metadata.exposure_seconds,
                observer_latitude_deg=settings.observing_site_latitude_deg,
                observer_longitude_deg=settings.observing_site_longitude_deg,
                observer_elevation_m=settings.observing_site_elevation_m,
                search_radius_deg=max(solved_field.radius_deg * 1.05, solved_field.radius_deg + 0.05),
                magnitude_limit=18.0,
                observatory_code=None,
            )
            solar_system_objects = [
                _solar_system_object_from_result(item, field_center=field_center)
                for item in solar_system_results
                if item.is_in_image
            ]
            objects.extend(solar_system_objects)
            layer_summaries.append(
                SkyExplorerLayerSummary(
                    layer_key="solar_system",
                    title=_LAYER_TITLES["solar_system"],
                    returned_count=len(solar_system_results),
                    displayed_count=len(solar_system_objects),
                )
            )

    if selected_type_keys:
        objects = [
            sky_object
            for sky_object in objects
            if selected_type_keys.intersection(sky_explorer_object_type_keys_for_object(sky_object))
        ]
    if normalized_pixel_roi is not None:
        objects = [
            sky_object
            for sky_object in objects
            if sky_explorer_object_in_pixel_roi(sky_object, normalized_pixel_roi)
        ]
    filtered_objects = _filter_sky_explorer_objects_by_magnitude(
        objects,
        maximum_magnitude=settings.sky_explorer_gaia_max_magnitude,
        exclude_unknown_magnitude=settings.sky_explorer_hide_objects_without_magnitude,
    )
    layer_summaries = _sky_explorer_summaries_for_filtered_objects(layer_summaries, filtered_objects)
    ordered_objects = tuple(sorted(filtered_objects, key=_sky_explorer_object_sort_key))
    summary_text = _summary_text(
        solved_field=solved_field,
        layer_summaries=tuple(layer_summaries),
        used_astrometry_fallback=used_astrometry_fallback,
    )
    return SkyExplorerResult(
        source_path=source_path,
        solved_field=solved_field,
        used_astrometry_fallback=used_astrometry_fallback,
        footprint=footprint,
        objects=ordered_objects,
        layer_summaries=tuple(layer_summaries),
        warning_messages=tuple(warning_messages),
        summary_text=summary_text,
    )


def _filter_sky_explorer_objects_by_magnitude(
    objects: Sequence[SkyExplorerObject],
    *,
    maximum_magnitude: float | None,
    exclude_unknown_magnitude: bool = False,
) -> list[SkyExplorerObject]:
    if maximum_magnitude is None:
        if not exclude_unknown_magnitude:
            return list(objects)
        return [sky_object for sky_object in objects if _sky_explorer_numeric_magnitude(sky_object) is not None]
    magnitude_limit = float(maximum_magnitude)
    filtered_objects: list[SkyExplorerObject] = []
    for sky_object in objects:
        magnitude = _sky_explorer_numeric_magnitude(sky_object)
        if sky_object.layer_key == "deep_sky":
            if magnitude is not None or not exclude_unknown_magnitude:
                filtered_objects.append(sky_object)
            continue
        if magnitude is None:
            if not exclude_unknown_magnitude:
                filtered_objects.append(sky_object)
            continue
        if magnitude <= magnitude_limit:
            filtered_objects.append(sky_object)
    return filtered_objects


def _sky_explorer_numeric_magnitude(sky_object: SkyExplorerObject) -> float | None:
    magnitude = sky_object.magnitude
    if magnitude is None:
        return None
    try:
        numeric_magnitude = float(magnitude)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric_magnitude):
        return None
    return numeric_magnitude


def _sky_explorer_summaries_for_filtered_objects(
    layer_summaries: Sequence[SkyExplorerLayerSummary],
    objects: Sequence[SkyExplorerObject],
) -> list[SkyExplorerLayerSummary]:
    displayed_counts: dict[str, int] = {}
    for sky_object in objects:
        displayed_counts[sky_object.layer_key] = displayed_counts.get(sky_object.layer_key, 0) + 1
    return [
        SkyExplorerLayerSummary(
            layer_key=summary.layer_key,
            title=summary.title,
            returned_count=summary.returned_count,
            displayed_count=displayed_counts.get(summary.layer_key, 0),
            note=summary.note,
        )
        for summary in layer_summaries
    ]


def _normalize_layers(selected_layers: Sequence[str] | None) -> tuple[str, ...]:
    if not selected_layers:
        return SKY_EXPLORER_LAYER_ORDER
    seen: set[str] = set()
    normalized: list[str] = []
    for layer in selected_layers:
        layer_key = str(layer or "").strip().lower()
        if layer_key not in _LAYER_TITLES or layer_key in seen:
            continue
        seen.add(layer_key)
        normalized.append(layer_key)
    return tuple(normalized) or SKY_EXPLORER_LAYER_ORDER


def _resolve_source_field(
    source_path: Path,
    settings: AppSettings,
    *,
    progress_callback: Callable[[str], None] | None = None,
) -> tuple[SolvedField, bool]:
    header, width, height = read_header_and_shape(source_path)
    valid_wcs, reasons = validate_wcs(header, source_path)
    if valid_wcs:
        solved_field = extract_solved_field(header, width, height, source_path)
        if solved_field is None:
            raise ValueError("Could not derive an image footprint from the selected source image.")
        return solved_field, False

    if not settings.astrometry_api_key:
        reason_text = " ".join(reason.strip() for reason in reasons if reason.strip()) or "The selected image does not contain a usable celestial WCS."
        raise ValueError(f"Selected image does not contain a usable celestial WCS. {reason_text}")

    if progress_callback is not None:
        progress_callback("Embedded WCS was unusable; submitting the image to astrometry.net fallback.")
    hints = infer_astrometry_solve_hints(header, width, height, source_path)
    result = AstrometryNetClient(settings.astrometry_api_key).solve_file(
        source_path,
        settings.cache_dir / "sky-explorer-wcs",
        hints=hints,
        timeout_seconds=resolve_astrometry_timeout_seconds(settings),
        progress_callback=progress_callback,
    )
    if result.solved_field is None:
        reason_text = " ".join(reason.strip() for reason in [*reasons, *result.reasons] if reason.strip()) or "Astrometry fallback did not return a valid WCS."
        raise ValueError(f"Could not recover a usable celestial WCS. {reason_text}")
    if progress_callback is not None:
        progress_callback("Recovered a usable WCS via astrometry.net fallback.")
    return result.solved_field, True


def _is_sky_explorer_survey_tile_wcs_canvas(source_path: Path) -> bool:
    name = Path(source_path).name.lower()
    return "tile-wcs-" in name


def _expand_solved_field_for_survey_mosaic(solved_field: SolvedField) -> SolvedField:
    """Inflate the catalog search radius to cover the survey-as-primary tile ring."""
    # One-tile half-diagonal → mosaic half-diagonal spanning ±MOSAIC_RADIUS tiles.
    mosaic_factor = float(2 * SKY_EXPLORER_SURVEY_FIELD_MOSAIC_RADIUS + 1)
    center_ra_deg = float(solved_field.center_ra_deg)
    center_dec_deg = float(solved_field.center_dec_deg)
    try:
        wcs = celestial_wcs(read_header(solved_field.wcs_path))
        # After pan/recenter, CRVAL is the current mosaic-center tile sky, while
        # pixel (W/2, H/2) may still point at a different tile.
        center_ra_deg = float(wcs.wcs.crval[0]) % 360.0
        center_dec_deg = float(wcs.wcs.crval[1])
    except Exception:
        pass
    return replace(
        solved_field,
        center_ra_deg=center_ra_deg,
        center_dec_deg=center_dec_deg,
        radius_deg=max(float(solved_field.radius_deg) * mosaic_factor, float(solved_field.radius_deg)),
    )


def _build_field_footprint(
    solved_field: SolvedField,
    *,
    survey_mosaic: bool = False,
) -> SkyExplorerFieldFootprint:
    wcs = celestial_wcs(read_header(solved_field.wcs_path))
    width_deg = None
    height_deg = None
    mosaic_radius = SKY_EXPLORER_SURVEY_FIELD_MOSAIC_RADIUS if survey_mosaic else 0
    mosaic_span = float(2 * mosaic_radius + 1)
    try:
        scales = proj_plane_pixel_scales(wcs.celestial) * 3600.0
        width_deg = float(scales[0] * solved_field.width * mosaic_span) / 3600.0
        height_deg = float(scales[1] * solved_field.height * mosaic_span) / 3600.0
    except Exception:
        width_deg = None
        height_deg = None
    corners: list[SkyExplorerCorner] = []
    # Mosaic tiles occupy [−R·W, (R+1)·W) × [−R·H, (R+1)·H) in image coords.
    x_min = -float(mosaic_radius) * float(solved_field.width)
    y_min = -float(mosaic_radius) * float(solved_field.height)
    x_max = float(mosaic_radius + 1) * float(solved_field.width) - 1.0
    y_max = float(mosaic_radius + 1) * float(solved_field.height) - 1.0
    corner_points = (
        ("Top Left", x_min, y_min),
        ("Top Right", x_max, y_min),
        ("Bottom Right", x_max, y_max),
        ("Bottom Left", x_min, y_max),
    )
    for label, x_coord, y_coord in corner_points:
        world = wcs.pixel_to_world(x_coord, y_coord)
        corners.append(SkyExplorerCorner(label=label, ra_deg=float(world.ra.deg), dec_deg=float(world.dec.deg)))
    return SkyExplorerFieldFootprint(
        center_ra_deg=solved_field.center_ra_deg,
        center_dec_deg=solved_field.center_dec_deg,
        radius_deg=solved_field.radius_deg,
        width_deg=width_deg,
        height_deg=height_deg,
        corners=tuple(corners),
    )


def _normalize_sky_explorer_pixel_roi(pixel_roi: Sequence[float] | None) -> tuple[float, float, float, float] | None:
    if pixel_roi is None:
        return None
    try:
        x0, y0, x1, y1 = (float(pixel_roi[0]), float(pixel_roi[1]), float(pixel_roi[2]), float(pixel_roi[3]))
    except (IndexError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (x0, y0, x1, y1)):
        return None
    min_x, max_x = (x0, x1) if x0 <= x1 else (x1, x0)
    min_y, max_y = (y0, y1) if y0 <= y1 else (y1, y0)
    if max_x - min_x <= 1.0 or max_y - min_y <= 1.0:
        return None
    return min_x, min_y, max_x, max_y


def sky_explorer_object_in_pixel_roi(sky_object: SkyExplorerObject, pixel_roi: Sequence[float]) -> bool:
    normalized = _normalize_sky_explorer_pixel_roi(pixel_roi)
    if normalized is None:
        return True
    min_x, min_y, max_x, max_y = normalized
    return min_x <= float(sky_object.pixel_x) <= max_x and min_y <= float(sky_object.pixel_y) <= max_y


def constrain_solved_field_to_pixel_roi(
    solved_field: SolvedField,
    wcs: WCS,
    pixel_roi: Sequence[float],
) -> tuple[SolvedField, SkyExplorerFieldFootprint]:
    normalized = _normalize_sky_explorer_pixel_roi(pixel_roi)
    if normalized is None:
        raise ValueError("Sky Explorer ROI is empty.")
    min_x, min_y, max_x, max_y = normalized
    center_x = 0.5 * (min_x + max_x)
    center_y = 0.5 * (min_y + max_y)
    corner_points = (
        ("Top Left", min_x, min_y),
        ("Top Right", max_x, min_y),
        ("Bottom Right", max_x, max_y),
        ("Bottom Left", min_x, max_y),
    )
    corners: list[SkyExplorerCorner] = []
    for label, x_coord, y_coord in corner_points:
        world = wcs.pixel_to_world(x_coord, y_coord)
        corners.append(
            SkyExplorerCorner(label=label, ra_deg=float(world.ra.deg), dec_deg=float(world.dec.deg))
        )
    center_world = wcs.pixel_to_world(center_x, center_y)
    center_coord = SkyCoord(float(center_world.ra.deg) * u.deg, float(center_world.dec.deg) * u.deg)
    radius_deg = 0.0
    for corner in corners:
        radius_deg = max(
            radius_deg,
            float(center_coord.separation(SkyCoord(corner.ra_deg * u.deg, corner.dec_deg * u.deg)).deg),
        )
    radius_deg = max(radius_deg * 1.05, 1.0e-6)
    width_deg = None
    height_deg = None
    try:
        scales = proj_plane_pixel_scales(wcs.celestial) * 3600.0
        width_deg = float(scales[0] * (max_x - min_x)) / 3600.0
        height_deg = float(scales[1] * (max_y - min_y)) / 3600.0
    except Exception:
        width_deg = None
        height_deg = None
    constrained = replace(
        solved_field,
        center_ra_deg=float(center_world.ra.deg) % 360.0,
        center_dec_deg=float(center_world.dec.deg),
        radius_deg=radius_deg,
    )
    return constrained, SkyExplorerFieldFootprint(
        center_ra_deg=constrained.center_ra_deg,
        center_dec_deg=constrained.center_dec_deg,
        radius_deg=constrained.radius_deg,
        width_deg=width_deg,
        height_deg=height_deg,
        corners=tuple(corners),
    )


def _catalog_star_objects(
    stars: Sequence[CatalogStar],
    *,
    layer_key: str,
    max_entries: int,
    field_center: SkyCoord,
    solved_field: SolvedField,
    wcs: WCS,
) -> tuple[list[SkyExplorerObject], SkyExplorerLayerSummary]:
    sorted_stars = sorted(stars, key=lambda item: (item.magnitude is None, item.magnitude if item.magnitude is not None else 99.0, item.name.lower()))
    displayed_stars = sorted_stars if max_entries <= 0 else sorted_stars[:max_entries]
    objects = [
        entry
        for entry in (
            _catalog_star_object_from_star(star, layer_key=layer_key, field_center=field_center, solved_field=solved_field, wcs=wcs)
            for star in displayed_stars
        )
        if entry is not None
    ]
    note = ""
    if max_entries > 0 and len(sorted_stars) > len(displayed_stars):
        note = f"Showing the brightest {len(displayed_stars)} of {len(sorted_stars)} returned entries."
    summary = SkyExplorerLayerSummary(
        layer_key=layer_key,
        title=_LAYER_TITLES[layer_key],
        returned_count=len(sorted_stars),
        displayed_count=len(objects),
        note=note,
    )
    return objects, summary


def _catalog_star_object_from_star(
    star: CatalogStar,
    *,
    layer_key: str,
    field_center: SkyCoord,
    solved_field: SolvedField,
    wcs: WCS,
) -> SkyExplorerObject | None:
    pixel_position = _pixel_position_for_coordinates(star.ra_deg, star.dec_deg, solved_field=solved_field, wcs=wcs)
    if pixel_position is None:
        return None
    pixel_x, pixel_y = pixel_position
    angular_distance_arcmin = float(field_center.separation(SkyCoord(star.ra_deg * u.deg, star.dec_deg * u.deg)).deg) * 60.0
    return SkyExplorerObject(
        layer_key=layer_key,
        catalog=star.catalog,
        source_id=star.source_id,
        name=star.name,
        object_type=star.object_type,
        ra_deg=float(star.ra_deg),
        dec_deg=float(star.dec_deg),
        pixel_x=pixel_x,
        pixel_y=pixel_y,
        magnitude=None if star.magnitude is None else float(star.magnitude),
        angular_distance_arcmin=angular_distance_arcmin,
        short_label=_short_label_for_name(star.name, fallback=star.source_id),
        metadata=dict(star.metadata or {}),
    )


def _query_simbad_objects(
    solved_field: SolvedField,
    *,
    wcs: WCS,
    field_center: SkyCoord,
    selected_layers: Sequence[str],
    selected_object_type_keys: Sequence[str] | None = None,
    include_dense_galaxy_catalog: bool = False,
    maximum_stellar_magnitude: float | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> tuple[list[SkyExplorerObject], list[SkyExplorerLayerSummary]]:
    layer_buckets: dict[str, list[SkyExplorerObject]] = {"deep_sky": [], "general_objects": []}
    total_counts: dict[str, int] = {"deep_sky": 0, "general_objects": 0}
    simbad_deep_sky_seen_keys: set[tuple[str, str, int, int]] = set()
    simbad_deep_sky_seen_position_keys: set[tuple[str, int, int]] = set()
    simbad_deep_sky_seen_designation_keys: set[tuple[str, str]] = set()
    selected_type_keys = sky_explorer_normalized_object_type_keys(selected_object_type_keys)
    enabled_supplements = sky_explorer_supplement_catalogs_for_object_types(selected_type_keys)

    center = SkyCoord(solved_field.center_ra_deg * u.deg, solved_field.center_dec_deg * u.deg)
    radius = max(float(solved_field.radius_deg) * _SIMBAD_SEARCH_RADIUS_EXPANSION, 0.01) * u.deg
    for layer_key in ("deep_sky", "general_objects"):
        if layer_key not in selected_layers:
            continue
        _raise_if_sky_explorer_cancelled(cancel_callback)
        result_rows = _query_simbad_region_rows(
            center,
            radius=radius,
            layer_key=layer_key,
            selected_object_type_keys=selected_type_keys,
            cancel_callback=cancel_callback,
        )
        if len(result_rows) == 0:
            continue
        for row in result_rows:
            if _simbad_layer_for_row(row) != layer_key:
                continue
            total_counts[layer_key] += 1
            sky_object = _sky_explorer_object_from_simbad_row(
                row,
                layer_key=layer_key,
                field_center=field_center,
                solved_field=solved_field,
                wcs=wcs,
            )
            if sky_object is not None and _sky_explorer_object_matches_stellar_magnitude_limit(
                sky_object,
                maximum_stellar_magnitude,
            ):
                if layer_key == "deep_sky":
                    _append_unique_deep_sky_objects(
                        layer_buckets[layer_key],
                        (sky_object,),
                        seen_keys=simbad_deep_sky_seen_keys,
                        seen_position_keys=simbad_deep_sky_seen_position_keys,
                        seen_designation_keys=simbad_deep_sky_seen_designation_keys,
                    )
                else:
                    layer_buckets[layer_key].append(sky_object)

    if "deep_sky" in selected_layers:
        seen_keys = {_sky_object_identity_key(item) for item in layer_buckets["deep_sky"]}
        seen_position_keys = {_sky_object_position_key(item) for item in layer_buckets["deep_sky"]}
        seen_designation_keys = {
            designation_key
            for item in layer_buckets["deep_sky"]
            for designation_key in _sky_object_designation_keys(item)
        }
        if "hyperleda" in enabled_supplements:
            _raise_if_sky_explorer_cancelled(cancel_callback)
            hyperleda_objects, hyperleda_count = _query_hyperleda_galaxy_objects(
                solved_field,
                wcs=wcs,
                field_center=field_center,
                require_named_alias=False,
            )
            total_counts["deep_sky"] += hyperleda_count
            _append_unique_deep_sky_objects(
                layer_buckets["deep_sky"],
                hyperleda_objects,
                seen_keys=seen_keys,
                seen_position_keys=seen_position_keys,
                seen_designation_keys=seen_designation_keys,
                allow_new_object=None if include_dense_galaxy_catalog else _hyperleda_object_has_named_alias,
            )
        if "sharpless" in enabled_supplements:
            _raise_if_sky_explorer_cancelled(cancel_callback)
            sharpless_objects, sharpless_count = _query_sharpless_objects(
                solved_field,
                wcs=wcs,
                field_center=field_center,
            )
            total_counts["deep_sky"] += sharpless_count
            _append_unique_deep_sky_objects(
                layer_buckets["deep_sky"],
                sharpless_objects,
                seen_keys=seen_keys,
                seen_position_keys=seen_position_keys,
                seen_designation_keys=seen_designation_keys,
            )
        if "barnard" in enabled_supplements:
            _raise_if_sky_explorer_cancelled(cancel_callback)
            barnard_objects, barnard_count = _query_barnard_objects(
                solved_field,
                wcs=wcs,
                field_center=field_center,
            )
            total_counts["deep_sky"] += barnard_count
            _append_unique_deep_sky_objects(
                layer_buckets["deep_sky"],
                barnard_objects,
                seen_keys=seen_keys,
                seen_position_keys=seen_position_keys,
                seen_designation_keys=seen_designation_keys,
            )
        if "vdb" in enabled_supplements:
            _raise_if_sky_explorer_cancelled(cancel_callback)
            vdb_objects, vdb_count = _query_vdb_objects(
                solved_field,
                wcs=wcs,
                field_center=field_center,
            )
            total_counts["deep_sky"] += vdb_count
            _append_unique_deep_sky_objects(
                layer_buckets["deep_sky"],
                vdb_objects,
                seen_keys=seen_keys,
                seen_position_keys=seen_position_keys,
                seen_designation_keys=seen_designation_keys,
            )
        if "hash_pn" in enabled_supplements:
            _raise_if_sky_explorer_cancelled(cancel_callback)
            hash_pn_objects, hash_pn_count = _query_hash_pn_objects(
                solved_field,
                wcs=wcs,
                field_center=field_center,
            )
            total_counts["deep_sky"] += hash_pn_count
            _append_unique_deep_sky_objects(
                layer_buckets["deep_sky"],
                hash_pn_objects,
                seen_keys=seen_keys,
                seen_position_keys=seen_position_keys,
                seen_designation_keys=seen_designation_keys,
            )
        if "ngc2000" in enabled_supplements:
            _raise_if_sky_explorer_cancelled(cancel_callback)
            ngc2000_objects, ngc2000_count = _query_ngc2000_objects(
                solved_field,
                wcs=wcs,
                field_center=field_center,
            )
            total_counts["deep_sky"] += ngc2000_count
            _append_unique_deep_sky_objects(
                layer_buckets["deep_sky"],
                ngc2000_objects,
                seen_keys=seen_keys,
                seen_position_keys=seen_position_keys,
                seen_designation_keys=seen_designation_keys,
            )

    objects: list[SkyExplorerObject] = []
    summaries: list[SkyExplorerLayerSummary] = []
    for layer_key in ("deep_sky", "general_objects"):
        if layer_key not in selected_layers:
            continue
        bucket = sorted(layer_buckets[layer_key], key=lambda item: (item.magnitude is None, item.magnitude if item.magnitude is not None else 99.0, item.name.lower()))
        objects.extend(bucket)
        summaries.append(
            SkyExplorerLayerSummary(
                layer_key=layer_key,
                title=_LAYER_TITLES[layer_key],
                returned_count=total_counts[layer_key],
                displayed_count=len(bucket),
            )
        )
    return objects, summaries


def _query_simbad_region_rows(
    center: SkyCoord,
    *,
    radius: u.Quantity,
    layer_key: str,
    selected_object_type_keys: Sequence[str] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
):
    simbad = Simbad()
    simbad.TIMEOUT = _SIMBAD_TIMEOUT_SECONDS
    simbad.ROW_LIMIT = _MAX_SIMBAD_DEEP_SKY_OBJECTS if layer_key == "deep_sky" else _MAX_SIMBAD_GENERAL_OBJECTS
    # Modern astroquery SIMBAD (TAP) already returns ICRS degrees as ra/dec.
    # Do not request legacy ra(d)/dec(d) formatting fields — they raise ValueError.
    if layer_key == "deep_sky":
        simbad.add_votable_fields("ids", "otype", "dim", "ra", "dec")
    else:
        simbad.add_votable_fields("ids", "otype", "V", "B", "ra", "dec")
    rows: list[Row] = []
    seen_signatures: set[tuple[str, str, int, int]] = set()
    criteria = _simbad_layer_criteria(layer_key, selected_object_type_keys=selected_object_type_keys)
    if selected_object_type_keys and criteria is None:
        return rows
    deadline = time.monotonic() + _SIMBAD_LAYER_QUERY_BUDGET_SECONDS
    for query_center, query_radius in _simbad_query_regions(center, radius=radius, layer_key=layer_key):
        _raise_if_sky_explorer_cancelled(cancel_callback)
        if time.monotonic() >= deadline:
            break
        query_kwargs = {"radius": query_radius}
        if criteria:
            query_kwargs["criteria"] = criteria
        try:
            result_table = simbad.query_region(query_center, **query_kwargs)
        except Exception:
            continue
        if result_table is None or len(result_table) == 0:
            continue
        for row in result_table:
            row_signature = _simbad_row_signature(row)
            if row_signature in seen_signatures:
                continue
            seen_signatures.add(row_signature)
            rows.append(row)
    return rows


def _simbad_layer_criteria(
    layer_key: str,
    *,
    selected_object_type_keys: Sequence[str] | None = None,
) -> str | None:
    type_codes = sky_explorer_simbad_otype_codes_for_object_types(
        selected_object_type_keys,
        layer_key=layer_key,
    )
    if selected_object_type_keys and not type_codes:
        return None
    if not type_codes:
        return None
    quoted_codes = ",".join("'{}'".format(str(code).replace("'", "''")) for code in type_codes)
    return f"otype IN ({quoted_codes})"


def _simbad_query_regions(center: SkyCoord, *, radius: u.Quantity, layer_key: str) -> list[tuple[SkyCoord, u.Quantity]]:
    if layer_key != "deep_sky" or radius <= _SIMBAD_TILED_DEEP_SKY_RADIUS:
        return [(center, radius)]
    search_radius_deg = float(radius.to_value(u.deg))
    tile_radius_deg = float(_SIMBAD_TILED_DEEP_SKY_RADIUS.to_value(u.deg))
    tile_step_deg = tile_radius_deg * _SIMBAD_TILED_DEEP_SKY_STEP_FACTOR
    ra_offsets_deg = _simbad_query_offsets(search_radius_deg, tile_step_deg)
    dec_offsets_deg = _simbad_query_offsets(search_radius_deg, tile_step_deg)
    query_regions: list[tuple[SkyCoord, u.Quantity]] = []
    for ra_offset_deg in ra_offsets_deg:
        for dec_offset_deg in dec_offsets_deg:
            if math.hypot(ra_offset_deg, dec_offset_deg) > search_radius_deg + tile_radius_deg:
                continue
            query_center = center.spherical_offsets_by(ra_offset_deg * u.deg, dec_offset_deg * u.deg)
            query_regions.append((query_center, _SIMBAD_TILED_DEEP_SKY_RADIUS))
    if not query_regions:
        return [(center, radius)]
    if len(query_regions) > _SIMBAD_MAX_FINE_QUERY_REGIONS:
        return _simbad_wide_field_query_regions(center, search_radius_deg=search_radius_deg)
    return query_regions


def _simbad_wide_field_query_regions(center: SkyCoord, *, search_radius_deg: float) -> list[tuple[SkyCoord, u.Quantity]]:
    if search_radius_deg <= 0.0:
        return [(center, _SIMBAD_TILED_DEEP_SKY_RADIUS)]
    ring_offset_deg = search_radius_deg * 0.55
    tile_radius_deg = max(float(_SIMBAD_TILED_DEEP_SKY_RADIUS.to_value(u.deg)), search_radius_deg * 0.65)
    offsets = (
        (0.0, 0.0),
        (-ring_offset_deg, -ring_offset_deg),
        (-ring_offset_deg, 0.0),
        (-ring_offset_deg, ring_offset_deg),
        (0.0, -ring_offset_deg),
        (0.0, ring_offset_deg),
        (ring_offset_deg, -ring_offset_deg),
        (ring_offset_deg, 0.0),
        (ring_offset_deg, ring_offset_deg),
    )
    return [
        (center.spherical_offsets_by(ra_offset_deg * u.deg, dec_offset_deg * u.deg), tile_radius_deg * u.deg)
        for ra_offset_deg, dec_offset_deg in offsets[:_SIMBAD_WIDE_FIELD_QUERY_REGIONS]
    ]


def _simbad_query_offsets(search_radius_deg: float, step_deg: float) -> list[float]:
    if search_radius_deg <= 0.0 or step_deg <= 0.0:
        return [0.0]
    offsets = [0.0]
    current = step_deg
    while current < search_radius_deg:
        offsets.extend((-current, current))
        current += step_deg
    if all(not math.isclose(abs(offset), search_radius_deg, rel_tol=0.0, abs_tol=1e-6) for offset in offsets):
        offsets.extend((-search_radius_deg, search_radius_deg))
    return sorted(set(round(offset, 6) for offset in offsets))


def _simbad_row_signature(row: Row) -> tuple[str, str, int, int]:
    main_id = _simbad_row_text_value(row, "main_id")
    identifiers = _simbad_row_text_value(row, "ids")
    ra_deg = _simbad_row_float_value(row, "ra_d", "ra")
    dec_deg = _simbad_row_float_value(row, "dec_d", "dec")
    return (
        main_id,
        identifiers,
        int(round(ra_deg * 1_000_000.0)),
        int(round(dec_deg * 1_000_000.0)),
    )


def _simbad_row_text_value(row: Row, *candidate_names: str) -> str:
    matched_name = _matched_simbad_row_column_name(row, *candidate_names)
    if matched_name is None:
        return ""
    return str(row[matched_name] or "").strip()


def _simbad_row_float_value(row: Row, *candidate_names: str) -> float:
    matched_name = _matched_simbad_row_column_name(row, *candidate_names)
    if matched_name is None:
        return 0.0
    try:
        return float(row[matched_name])
    except (TypeError, ValueError):
        return 0.0


def _matched_simbad_row_column_name(row: Row, *candidate_names: str) -> str | None:
    normalized_candidates = {candidate_name.strip().lower() for candidate_name in candidate_names}
    for column_name in row.colnames:
        if column_name.strip().lower() in normalized_candidates:
            return column_name
    return None


def _sky_explorer_object_matches_stellar_magnitude_limit(
    sky_object: SkyExplorerObject,
    maximum_stellar_magnitude: float | None,
) -> bool:
    if maximum_stellar_magnitude is None or sky_object.layer_key != "general_objects":
        return True
    if sky_object.magnitude is None:
        return False
    return float(sky_object.magnitude) <= float(maximum_stellar_magnitude)


def _query_ngc2000_objects(
    solved_field: SolvedField,
    *,
    wcs: WCS,
    field_center: SkyCoord,
) -> tuple[list[SkyExplorerObject], int]:
    center = SkyCoord(solved_field.center_ra_deg * u.deg, solved_field.center_dec_deg * u.deg)
    radius = max(float(solved_field.radius_deg) * _SIMBAD_SEARCH_RADIUS_EXPANSION, 0.01) * u.deg
    vizier = Vizier(columns=["**"], row_limit=_MAX_NGC2000_OBJECTS)
    try:
        tables = vizier.query_region(center, radius=radius, catalog=_NGC2000_CATALOG)
    except Exception:
        return [], 0
    if not tables:
        return [], 0

    objects: list[SkyExplorerObject] = []
    row_count = 0
    for table in tables:
        for row in table:
            row_count += 1
            sky_object = _sky_explorer_object_from_ngc2000_row(
                row,
                field_center=field_center,
                solved_field=solved_field,
                wcs=wcs,
            )
            if sky_object is not None:
                objects.append(sky_object)
    return objects, row_count


def _query_sharpless_objects(
    solved_field: SolvedField,
    *,
    wcs: WCS,
    field_center: SkyCoord,
) -> tuple[list[SkyExplorerObject], int]:
    center = SkyCoord(solved_field.center_ra_deg * u.deg, solved_field.center_dec_deg * u.deg)
    radius = max(float(solved_field.radius_deg) * _SIMBAD_SEARCH_RADIUS_EXPANSION, 0.01) * u.deg
    vizier = Vizier(columns=["**"], row_limit=_MAX_SHARPLESS_OBJECTS)
    try:
        tables = vizier.query_region(center, radius=radius, catalog=_SHARPLESS_CATALOG)
    except Exception:
        return [], 0
    if not tables:
        return [], 0

    objects: list[SkyExplorerObject] = []
    row_count = 0
    for table in tables:
        for row in table:
            row_count += 1
            sky_object = _sky_explorer_object_from_sharpless_row(
                row,
                field_center=field_center,
                solved_field=solved_field,
                wcs=wcs,
            )
            if sky_object is not None:
                objects.append(sky_object)
    return objects, row_count


def _query_barnard_objects(
    solved_field: SolvedField,
    *,
    wcs: WCS,
    field_center: SkyCoord,
) -> tuple[list[SkyExplorerObject], int]:
    center = SkyCoord(solved_field.center_ra_deg * u.deg, solved_field.center_dec_deg * u.deg)
    radius = max(float(solved_field.radius_deg) * _SIMBAD_SEARCH_RADIUS_EXPANSION, 0.01) * u.deg
    vizier = Vizier(columns=["**"], row_limit=_MAX_BARNARD_OBJECTS)
    try:
        tables = vizier.query_region(center, radius=radius, catalog=_BARNARD_CATALOG)
    except Exception:
        return [], 0
    if not tables:
        return [], 0

    objects: list[SkyExplorerObject] = []
    row_count = 0
    for table in tables:
        for row in table:
            row_count += 1
            sky_object = _sky_explorer_object_from_barnard_row(
                row,
                field_center=field_center,
                solved_field=solved_field,
                wcs=wcs,
            )
            if sky_object is not None:
                objects.append(sky_object)
    return objects, row_count


def _query_vdb_objects(
    solved_field: SolvedField,
    *,
    wcs: WCS,
    field_center: SkyCoord,
) -> tuple[list[SkyExplorerObject], int]:
    center = SkyCoord(solved_field.center_ra_deg * u.deg, solved_field.center_dec_deg * u.deg)
    radius = max(float(solved_field.radius_deg) * _SIMBAD_SEARCH_RADIUS_EXPANSION, 0.01) * u.deg
    vizier = Vizier(columns=["**"], row_limit=_MAX_VDB_OBJECTS)
    try:
        tables = vizier.query_region(center, radius=radius, catalog=_VDB_CATALOG)
    except Exception:
        return [], 0
    if not tables:
        return [], 0

    objects: list[SkyExplorerObject] = []
    row_count = 0
    for table in tables:
        for row in table:
            row_count += 1
            sky_object = _sky_explorer_object_from_vdb_row(
                row,
                field_center=field_center,
                solved_field=solved_field,
                wcs=wcs,
            )
            if sky_object is not None:
                objects.append(sky_object)
    return objects, row_count


def _query_hash_pn_objects(
    solved_field: SolvedField,
    *,
    wcs: WCS,
    field_center: SkyCoord,
) -> tuple[list[SkyExplorerObject], int]:
    center = SkyCoord(solved_field.center_ra_deg * u.deg, solved_field.center_dec_deg * u.deg)
    radius = max(float(solved_field.radius_deg) * _SIMBAD_SEARCH_RADIUS_EXPANSION, 0.01) * u.deg
    vizier = Vizier(columns=["**"], row_limit=_MAX_HASH_PN_OBJECTS)
    try:
        tables = vizier.query_region(center, radius=radius, catalog=_HASH_PN_CATALOG)
    except Exception:
        return [], 0
    if not tables:
        return [], 0

    objects: list[SkyExplorerObject] = []
    row_count = 0
    for table in tables:
        for row in table:
            row_count += 1
            sky_object = _sky_explorer_object_from_hash_pn_row(
                row,
                field_center=field_center,
                solved_field=solved_field,
                wcs=wcs,
            )
            if sky_object is not None:
                objects.append(sky_object)
    return objects, row_count


def _query_hyperleda_galaxy_objects(
    solved_field: SolvedField,
    *,
    wcs: WCS,
    field_center: SkyCoord,
    require_named_alias: bool = False,
) -> tuple[list[SkyExplorerObject], int]:
    center = SkyCoord(solved_field.center_ra_deg * u.deg, solved_field.center_dec_deg * u.deg)
    radius = max(float(solved_field.radius_deg) * _SIMBAD_SEARCH_RADIUS_EXPANSION, 0.01) * u.deg
    vizier = Vizier(
        columns=["**"],
        row_limit=_MAX_HYPERLEDA_GALAXIES,
    )
    try:
        tables = vizier.query_region(center, radius=radius, catalog=_HYPERLEDA_PGC_CATALOG)
    except Exception:
        return [], 0
    if not tables:
        return [], 0

    objects: list[SkyExplorerObject] = []
    row_count = 0
    for table in tables:
        for row in table:
            if require_named_alias and not _hyperleda_has_named_alias(row):
                continue
            sky_object = _sky_explorer_object_from_hyperleda_row(
                row,
                field_center=field_center,
                solved_field=solved_field,
                wcs=wcs,
            )
            if sky_object is not None:
                row_count += 1
                objects.append(sky_object)
    return objects, row_count


def _sky_explorer_object_from_ngc2000_row(
    row: Row,
    *,
    field_center: SkyCoord,
    solved_field: SolvedField,
    wcs: WCS,
) -> SkyExplorerObject | None:
    ra_text = _row_text(row, "RAB2000")
    dec_text = _row_text(row, "DEB2000")
    if ra_text is None or dec_text is None:
        return None
    try:
        coordinates = SkyCoord(ra_text, dec_text, unit=(u.hourangle, u.deg), frame="icrs")
    except Exception:
        return None

    ra_deg = float(coordinates.ra.deg)
    dec_deg = float(coordinates.dec.deg)
    pixel_position = _pixel_position_for_coordinates(ra_deg, dec_deg, solved_field=solved_field, wcs=wcs)
    if pixel_position is None:
        return None
    pixel_x, pixel_y = pixel_position

    raw_name = _normalize_identifier(_row_text(row, "Name") or "")
    object_name = _ngc2000_object_name(raw_name)
    object_type_code = _normalize_identifier(_row_text(row, "Type") or "")
    object_type = _ngc2000_type_label(object_type_code)
    magnitude = _row_float(row, "mag")
    angular_distance_arcmin = float(field_center.separation(SkyCoord(ra_deg * u.deg, dec_deg * u.deg)).deg) * 60.0
    return SkyExplorerObject(
        layer_key="deep_sky",
        catalog="ngc2000",
        source_id=object_name,
        name=object_name,
        object_type=object_type,
        ra_deg=ra_deg,
        dec_deg=dec_deg,
        pixel_x=pixel_x,
        pixel_y=pixel_y,
        magnitude=magnitude,
        angular_distance_arcmin=angular_distance_arcmin,
        short_label=_short_label_for_name(object_name, fallback=object_name),
        metadata={
            "catalog_type": object_type_code,
            "catalog_description": _row_text(row, "Desc") or "",
            "catalog_size_arcmin": _row_float(row, "size"),
        },
    )


def _sky_explorer_object_from_sharpless_row(
    row: Row,
    *,
    field_center: SkyCoord,
    solved_field: SolvedField,
    wcs: WCS,
) -> SkyExplorerObject | None:
    galactic_longitude = _row_float(row, "GLon")
    galactic_latitude = _row_float(row, "GLat")
    sh2_number = _row_float(row, "Sh2")
    if galactic_longitude is None or galactic_latitude is None or sh2_number is None:
        return None
    coordinates = SkyCoord(l=float(galactic_longitude) * u.deg, b=float(galactic_latitude) * u.deg, frame="galactic").icrs
    ra_deg = float(coordinates.ra.deg)
    dec_deg = float(coordinates.dec.deg)
    pixel_position = _pixel_position_for_coordinates(ra_deg, dec_deg, solved_field=solved_field, wcs=wcs)
    if pixel_position is None:
        return None
    pixel_x, pixel_y = pixel_position
    object_name = _display_name_from_designation_key(f"SH2{int(round(sh2_number))}")
    angular_distance_arcmin = float(field_center.separation(SkyCoord(ra_deg * u.deg, dec_deg * u.deg)).deg) * 60.0
    return SkyExplorerObject(
        layer_key="deep_sky",
        catalog="sharpless",
        source_id=object_name,
        name=object_name,
        object_type="HII Region",
        ra_deg=ra_deg,
        dec_deg=dec_deg,
        pixel_x=pixel_x,
        pixel_y=pixel_y,
        magnitude=None,
        angular_distance_arcmin=angular_distance_arcmin,
        short_label=_short_label_for_name(object_name, fallback=object_name),
        metadata={
            "catalog_type": "HII",
            "catalog_size_arcmin": _row_float(row, "Diam"),
            "catalog_description": _normalize_identifier(_row_text(row, "Form") or "HII Region"),
        },
    )


def _sky_explorer_object_from_barnard_row(
    row: Row,
    *,
    field_center: SkyCoord,
    solved_field: SolvedField,
    wcs: WCS,
) -> SkyExplorerObject | None:
    ra_text = _row_text(row, "_RA.icrs")
    dec_text = _row_text(row, "_DE.icrs")
    barnard_number = _row_text(row, "Barn")
    if ra_text is None or dec_text is None or barnard_number is None:
        return None
    try:
        coordinates = SkyCoord(ra_text, dec_text, unit=(u.hourangle, u.deg), frame="icrs")
    except Exception:
        return None

    ra_deg = float(coordinates.ra.deg)
    dec_deg = float(coordinates.dec.deg)
    pixel_position = _pixel_position_for_coordinates(ra_deg, dec_deg, solved_field=solved_field, wcs=wcs)
    if pixel_position is None:
        return None
    pixel_x, pixel_y = pixel_position
    object_name = _display_name_from_designation_key(f"B{barnard_number}")
    angular_distance_arcmin = float(field_center.separation(SkyCoord(ra_deg * u.deg, dec_deg * u.deg)).deg) * 60.0
    return SkyExplorerObject(
        layer_key="deep_sky",
        catalog="barnard",
        source_id=object_name,
        name=object_name,
        object_type="Dark Nebula",
        ra_deg=ra_deg,
        dec_deg=dec_deg,
        pixel_x=pixel_x,
        pixel_y=pixel_y,
        magnitude=None,
        angular_distance_arcmin=angular_distance_arcmin,
        short_label=_short_label_for_name(object_name, fallback=object_name),
        metadata={
            "catalog_type": "DNe",
            "catalog_size_arcmin": _row_float(row, "Diam"),
        },
    )


def _sky_explorer_object_from_vdb_row(
    row: Row,
    *,
    field_center: SkyCoord,
    solved_field: SolvedField,
    wcs: WCS,
) -> SkyExplorerObject | None:
    ra_deg = _row_float(row, "_RA")
    dec_deg = _row_float(row, "_DE")
    vdb_number = _row_float(row, "VdB")
    if ra_deg is None or dec_deg is None or vdb_number is None:
        return None
    pixel_position = _pixel_position_for_coordinates(ra_deg, dec_deg, solved_field=solved_field, wcs=wcs)
    if pixel_position is None:
        return None
    pixel_x, pixel_y = pixel_position
    object_name = _display_name_from_designation_key(f"VDB{int(round(vdb_number))}")
    angular_distance_arcmin = float(field_center.separation(SkyCoord(ra_deg * u.deg, dec_deg * u.deg)).deg) * 60.0
    radius_arcmin = max(_row_float(row, "BRadMax") or 0.0, _row_float(row, "RRadMax") or 0.0)
    size_arcmin = (radius_arcmin * 2.0) if radius_arcmin > 0.0 else None
    return SkyExplorerObject(
        layer_key="deep_sky",
        catalog="vdb",
        source_id=object_name,
        name=object_name,
        object_type="Reflection Nebula",
        ra_deg=float(ra_deg),
        dec_deg=float(dec_deg),
        pixel_x=pixel_x,
        pixel_y=pixel_y,
        magnitude=_row_float(row, "Vmag"),
        angular_distance_arcmin=angular_distance_arcmin,
        short_label=_short_label_for_name(object_name, fallback=object_name),
        metadata={
            "catalog_type": "RNe",
            "catalog_size_arcmin": size_arcmin,
            "catalog_description": _normalize_identifier(_row_text(row, "Type") or "Reflection Nebula"),
        },
    )


def _sky_explorer_object_from_hash_pn_row(
    row: Row,
    *,
    field_center: SkyCoord,
    solved_field: SolvedField,
    wcs: WCS,
) -> SkyExplorerObject | None:
    ra_deg = _row_float_any(row, "RAJ2000", "RAdeg", "_RAJ2000", "_RA")
    dec_deg = _row_float_any(row, "DEJ2000", "DEdeg", "_DEJ2000", "_DE")
    hash_id = _hash_pn_identifier(row)
    if ra_deg is None or dec_deg is None or hash_id is None:
        return None
    pixel_position = _pixel_position_for_coordinates(ra_deg, dec_deg, solved_field=solved_field, wcs=wcs)
    if pixel_position is None:
        return None
    pixel_x, pixel_y = pixel_position

    status_code = _hash_pn_status_code(row)
    object_type, catalog_type = _hash_pn_object_type_for_status(status_code)
    object_name = _hash_pn_display_name(row, hash_id=hash_id)
    png_name = _hash_pn_png_name(row)
    usual_name = _normalize_identifier(_row_text_any(row, "Name") or "")
    simbad_id = _normalize_identifier(_row_text_any(row, "SimbadID") or "")
    source_catalog = _normalize_identifier(_row_text_any(row, "Catalogue", "Catalog") or "")
    morphology_main = _normalize_identifier(_row_text_any(row, "MorphMainCl") or "")
    morphology_sub = _normalize_identifier(_row_text_any(row, "MorphSubCl") or "")
    morphology_comment = _normalize_identifier(_row_text_any(row, "MorphCom") or "")
    comment = _normalize_identifier(_row_text_any(row, "Com") or "")
    spectrum_available = _hash_pn_flag_true(_row_text_any(row, "spectrum"))
    hash_link = _normalize_identifier(_row_text_any(row, "HASH-link", "HASH_link") or "")
    major_axis_arcmin = _hash_pn_diameter_arcmin(row, "MajDiam")
    minor_axis_arcmin = _hash_pn_diameter_arcmin(row, "MinDiam")
    position_angle_deg = _hash_pn_position_angle_deg(row)
    geometry_metadata = _catalog_geometry_metadata(
        major_axis_arcmin=major_axis_arcmin,
        minor_axis_arcmin=minor_axis_arcmin,
        position_angle_deg=position_angle_deg,
    )
    status_label = _hash_pn_status_label(status_code)
    description_parts = [part for part in (status_label, morphology_main, morphology_sub, source_catalog) if part]
    identifiers = " | ".join(
        part
        for part in (
            object_name,
            usual_name if usual_name and usual_name != object_name else "",
            png_name,
            simbad_id,
            f"HASH {hash_id}",
        )
        if part
    )
    angular_distance_arcmin = float(field_center.separation(SkyCoord(ra_deg * u.deg, dec_deg * u.deg)).deg) * 60.0
    metadata: dict[str, object] = {
        "catalog_type": catalog_type,
        "catalog_description": " · ".join(description_parts) or "HASH PN via VizieR V/163",
        "hash_id": hash_id,
        "hash_pn_status": status_code or "",
        "hash_pn_status_label": status_label,
        "hash_png": png_name,
        "usual_name": usual_name,
        "hash_source_catalog": source_catalog,
        "hash_spectrum_available": spectrum_available,
        "identifiers": identifiers,
        "vizier_catalog": _HASH_PN_CATALOG,
    }
    if morphology_main:
        metadata["catalog_morphology"] = morphology_main if not morphology_sub else f"{morphology_main}/{morphology_sub}"
    if morphology_comment:
        metadata["hash_morphology_comment"] = morphology_comment
    if comment:
        metadata["hash_comment"] = comment
    if hash_link:
        metadata["hash_object_page"] = hash_link
    metadata.update(geometry_metadata)
    return SkyExplorerObject(
        layer_key="deep_sky",
        catalog="hash_pn",
        source_id=f"HASH {hash_id}",
        name=object_name,
        object_type=object_type,
        ra_deg=float(ra_deg),
        dec_deg=float(dec_deg),
        pixel_x=pixel_x,
        pixel_y=pixel_y,
        magnitude=None,
        angular_distance_arcmin=angular_distance_arcmin,
        short_label=_short_label_for_name(object_name, fallback=f"HASH {hash_id}"),
        metadata=metadata,
    )


def _hash_pn_identifier(row: Row) -> str | None:
    hash_value = _row_float_any(row, "HASH", "idPNMain")
    if hash_value is not None:
        return str(int(round(hash_value)))
    text_value = _row_text_any(row, "HASH", "idPNMain")
    if text_value is None:
        return None
    digits = re.sub(r"[^\d]", "", text_value)
    return digits or None


def _hash_pn_status_code(row: Row) -> str:
    return _normalize_identifier(_row_text_any(row, "PNstat") or "").upper()


def _hash_pn_object_type_for_status(status_code: str) -> tuple[str, str]:
    mapped = _HASH_PN_STATUS_OBJECT_TYPES.get(status_code)
    if mapped is not None:
        return mapped
    if status_code:
        return (f"HASH Object ({status_code})", status_code)
    return ("Planetary Nebula", "PN")


def _hash_pn_status_label(status_code: str) -> str:
    labels = {
        "T": "True PN",
        "L": "Likely PN",
        "P": "Possible PN",
        "C": "New candidate",
        "A": "PAGB / Pre-PN",
        "B": "Possible Pre-PN",
        "X": "Not PN",
        "N": "Not PN (check)",
        "Q": "Artefact",
        "OOUN": "Object of unknown nature",
        "S": "Object to check",
        "O": "Interesting object",
        "TEST": "Test object",
        "K": "Transition object",
        "E": "RCrB / eHe / LTP",
        "R": "RV Tau",
        "CIR": "Circumstellar matter",
        "CGB": "Cometary globule",
        "IISM": "Ionized ISM",
        "EV?": "Possible variable",
        "NS": "Not specified",
    }
    if status_code in labels:
        return labels[status_code]
    mapped = _HASH_PN_STATUS_OBJECT_TYPES.get(status_code)
    if mapped is not None:
        return mapped[0]
    return status_code or "HASH object"


def _hash_pn_display_name(row: Row, *, hash_id: str) -> str:
    usual_name = _normalize_identifier(_row_text_any(row, "Name") or "")
    if usual_name:
        designation = _catalog_designation_key(usual_name)
        if designation is not None:
            return _display_name_from_designation_key(designation)
        return usual_name
    png_name = _hash_pn_png_name(row)
    if png_name:
        return png_name
    simbad_id = _normalize_identifier(_row_text_any(row, "SimbadID") or "")
    if simbad_id:
        designation = _catalog_designation_key(simbad_id)
        if designation is not None:
            return _display_name_from_designation_key(designation)
        return simbad_id
    return f"HASH {hash_id}"


def _hash_pn_png_name(row: Row) -> str:
    png_value = _normalize_identifier(_row_text_any(row, "PNG") or "")
    if not png_value:
        return ""
    compact = png_value.upper().replace(" ", "")
    if compact.startswith("PNG"):
        compact = compact[3:]
    if not compact:
        return ""
    return f"PN G{compact}"


def _hash_pn_diameter_arcmin(row: Row, key: str) -> float | None:
    diameter_arcsec = _row_float(row, key)
    if diameter_arcsec is None or diameter_arcsec >= _HASH_PN_DIAMETER_SENTINEL_ARCSEC or diameter_arcsec <= 0.0:
        return None
    return float(diameter_arcsec) / 60.0


def _hash_pn_position_angle_deg(row: Row) -> float | None:
    position_angle = _row_float_any(row, "EPA")
    if position_angle is None or position_angle >= _HASH_PN_POSITION_ANGLE_SENTINEL_DEG:
        return None
    if position_angle < 0.0 or position_angle > 180.0:
        return None
    return float(position_angle)


def _hash_pn_flag_true(value: str | None) -> bool:
    normalized = _normalize_identifier(value or "").casefold()
    return normalized in {"y", "yes", "true", "1"}


def _row_text_any(row: Row, *keys: str) -> str | None:
    for key in keys:
        value = _row_text(row, key)
        if value is not None:
            return value
    return None


def _sky_explorer_object_from_hyperleda_row(
    row: Row,
    *,
    field_center: SkyCoord,
    solved_field: SolvedField,
    wcs: WCS,
) -> SkyExplorerObject | None:
    ra_text = _row_text(row, "RAJ2000")
    dec_text = _row_text(row, "DEJ2000")
    if ra_text is None or dec_text is None:
        return None
    try:
        coordinates = SkyCoord(ra_text, dec_text, unit=(u.hourangle, u.deg), frame="icrs")
    except Exception:
        return None

    geometry_metadata = _hyperleda_geometry_metadata_from_row(row)
    major_axis_arcmin = geometry_metadata.get("catalog_major_axis_arcmin")
    if not isinstance(major_axis_arcmin, float) or major_axis_arcmin < _MIN_HYPERLEDA_MAJOR_AXIS_ARCMIN:
        return None

    ra_deg = float(coordinates.ra.deg)
    dec_deg = float(coordinates.dec.deg)
    pixel_position = _pixel_position_for_coordinates(ra_deg, dec_deg, solved_field=solved_field, wcs=wcs)
    if pixel_position is None:
        return None
    pixel_x, pixel_y = pixel_position

    source_id = _hyperleda_source_id(row)
    preferred_name = _hyperleda_preferred_name(row, fallback=source_id)
    angular_distance_arcmin = float(field_center.separation(SkyCoord(ra_deg * u.deg, dec_deg * u.deg)).deg) * 60.0
    visual_magnitude = _row_float_any(row, "Vmag", "Bmag", "BT", "B_T", "Btot", "Btc", "btc", "B", "V", "mag")
    metadata = {
        "catalog_type": _row_text(row, "OType") or "G",
        "catalog_morphology": _row_text(row, "MType") or "",
        **geometry_metadata,
    }
    return SkyExplorerObject(
        layer_key="deep_sky",
        catalog="hyperleda",
        source_id=source_id,
        name=preferred_name,
        object_type="Galaxy",
        ra_deg=ra_deg,
        dec_deg=dec_deg,
        pixel_x=pixel_x,
        pixel_y=pixel_y,
        magnitude=visual_magnitude,
        angular_distance_arcmin=angular_distance_arcmin,
        short_label=_short_label_for_name(preferred_name, fallback=source_id),
        metadata=metadata,
    )


def _sky_explorer_object_from_simbad_row(
    row: Row,
    *,
    layer_key: str,
    field_center: SkyCoord,
    solved_field: SolvedField,
    wcs: WCS,
) -> SkyExplorerObject | None:
    coordinates = _row_coordinates_deg(row)
    if coordinates is None:
        return None
    ra_value, dec_value = coordinates
    pixel_position = _pixel_position_for_coordinates(ra_value, dec_value, solved_field=solved_field, wcs=wcs)
    if pixel_position is None:
        return None
    pixel_x, pixel_y = pixel_position
    main_id = _row_text(row, "MAIN_ID") or _row_text(row, "main_id") or "SIMBAD object"
    identifiers = _row_text(row, "IDS") or _row_text(row, "ids") or ""
    angular_distance_arcmin = float(field_center.separation(SkyCoord(ra_value * u.deg, dec_value * u.deg)).deg) * 60.0
    object_type = _simbad_object_type_for_row(row, main_id=main_id, identifiers=identifiers)
    visual_magnitude = _row_float_any(row, "V", "FLUX_V", "B", "FLUX_B")
    preferred_name = _preferred_simbad_name(main_id, identifiers, object_type=object_type)
    geometry_metadata = _catalog_geometry_metadata(
        major_axis_arcmin=_row_float_any(
            row,
            "GALDIM_MAJAXIS",
            "galdim_majaxis",
            "DIM_MAJAXIS",
            "dim_majaxis",
        ),
        minor_axis_arcmin=_row_float_any(
            row,
            "GALDIM_MINAXIS",
            "galdim_minaxis",
            "DIM_MINAXIS",
            "dim_minaxis",
        ),
        position_angle_deg=_row_float_any(
            row,
            "GALDIM_ANGLE",
            "galdim_angle",
            "DIM_ANGLE",
            "dim_angle",
        ),
    )
    return SkyExplorerObject(
        layer_key=layer_key,
        catalog="simbad",
        source_id=preferred_name,
        name=preferred_name,
        object_type=object_type,
        ra_deg=float(ra_value),
        dec_deg=float(dec_value),
        pixel_x=pixel_x,
        pixel_y=pixel_y,
        magnitude=visual_magnitude,
        angular_distance_arcmin=angular_distance_arcmin,
        short_label=_short_label_for_name(preferred_name, fallback=main_id),
        metadata={
            "main_id": main_id,
            "identifiers": identifiers,
            "object_type": object_type,
            **geometry_metadata,
        },
    )


def _solar_system_object_from_result(result: SolarSystemSearchResult, *, field_center: SkyCoord) -> SkyExplorerObject:
    detection = result.detection
    angular_distance_deg = result.angular_distance_deg if result.angular_distance_deg is not None else float(
        field_center.separation(SkyCoord(detection.predicted_ra_deg * u.deg, detection.predicted_dec_deg * u.deg)).deg
    )
    return SkyExplorerObject(
        layer_key="solar_system",
        catalog="skybot",
        source_id=detection.designation or detection.name,
        name=detection.name,
        object_type=detection.object_type,
        ra_deg=float(detection.predicted_ra_deg),
        dec_deg=float(detection.predicted_dec_deg),
        pixel_x=float(detection.predicted_x),
        pixel_y=float(detection.predicted_y),
        magnitude=None if detection.predicted_magnitude is None else float(detection.predicted_magnitude),
        angular_distance_arcmin=float(angular_distance_deg) * 60.0,
        short_label=_short_label_for_name(detection.name, fallback=detection.designation or detection.name),
        metadata={
            "designation": detection.designation,
            "status": detection.status,
            "orbit_class": detection.orbit_class,
            "predicted_motion_arcsec_per_hour": detection.motion_rate_arcsec_per_hour,
        },
    )


def _simbad_object_type_for_row(row: Row, *, main_id: str, identifiers: str) -> str:
    object_type = _row_text(row, "OTYPE") or _row_text(row, "otype") or _row_text(row, "OTYPES") or _row_text(row, "otypes") or "Object"
    if _normalize_identifier(object_type).upper() == "B2" and _looks_like_galaxy_identifier_text(f"{main_id}|{identifiers}"):
        return "G"
    return object_type


def _looks_like_galaxy_identifier_text(text: object) -> bool:
    normalized = _normalize_identifier(text).upper()
    designation_key = _catalog_designation_key_from_text(normalized)
    if designation_key is not None and designation_key.startswith(("NGC", "IC")):
        return True
    return re.search(r"\b(?:2MASX|PGC|LEDA|MCG|UGC|CGCG|KUG|MRK)\b", normalized) is not None


def _pixel_position_for_coordinates(
    ra_deg: float,
    dec_deg: float,
    *,
    solved_field: SolvedField,
    wcs: WCS,
) -> tuple[float, float] | None:
    try:
        pixel_x, pixel_y = wcs.world_to_pixel_values(float(ra_deg), float(dec_deg))
    except Exception:
        return None
    if not np_is_finite(pixel_x) or not np_is_finite(pixel_y):
        return None
    # Survey-as-primary mosaics place tiles outside the central W×H plate (including
    # negative mosaic coords after pan/recenter). Catalog cone searches already limit
    # which sky positions are considered, so only require a finite projection here.
    if _is_sky_explorer_survey_tile_wcs_canvas(solved_field.wcs_path):
        return float(pixel_x), float(pixel_y)
    if pixel_x < 0.0 or pixel_y < 0.0 or pixel_x >= float(solved_field.width) or pixel_y >= float(solved_field.height):
        return None
    return float(pixel_x), float(pixel_y)


def _preferred_simbad_name(main_id: str, identifiers: str, *, object_type: str = "") -> str:
    normalized_main_id = _normalize_identifier(main_id)
    if _looks_like_named_member_star(normalized_main_id):
        return normalized_main_id or "SIMBAD object"
    if _is_star_like_simbad_object_type(object_type):
        return _preferred_simbad_star_name(main_id, identifiers)
    if _is_structural_simbad_galaxy_system_type(object_type):
        if normalized_main_id:
            return normalized_main_id
    candidates = [main_id, *_identifier_candidates(identifiers)]
    preferred_prefixes = _preferred_simbad_designation_prefixes(object_type)
    for prefix in preferred_prefixes:
        for candidate in candidates:
            designation_key = _catalog_designation_key(candidate)
            if (
                designation_key is not None
                and designation_key.startswith(prefix)
                and _is_primary_catalog_designation_text(candidate, designation_key)
            ):
                return _display_name_from_designation_key(designation_key)
    for candidate in candidates:
        normalized = _normalize_identifier(candidate)
        if normalized and "GAIA DR3" not in normalized.upper():
            return normalized
    return _normalize_identifier(main_id) or "SIMBAD object"


def _preferred_simbad_designation_prefixes(object_type: str) -> tuple[str, ...]:
    normalized_type = _normalize_identifier(object_type).upper()
    if "GALAX" in normalized_type or normalized_type in {"G", "G?", "PAIR OF GALAXIES", "GROUP OF GALAXIES", "CLUSTER OF GALAXIES", "IG", "PAG", "GRG", "CLG", "CGG", "SCG", "AGN", "SY1", "SY2", "SYG", "QSO", "BLA", "BLL", "OVV", "RG"}:
        return ("NGC", "IC", "M", "SH2", "VDB", "B")
    return ("SH2", "VDB", "B", "M", "NGC", "IC")


def _preferred_simbad_star_name(main_id: str, identifiers: str) -> str:
    candidates = [main_id, *_identifier_candidates(identifiers)]
    for candidate in candidates:
        normalized = _normalize_identifier(candidate)
        if not normalized or "GAIA DR3" in normalized.upper():
            continue
        designation_key = _catalog_designation_key(normalized)
        if designation_key is not None and not _looks_like_named_member_star(normalized):
            continue
        return normalized
    for candidate in candidates:
        normalized = _normalize_identifier(candidate)
        if normalized:
            return normalized
    return "SIMBAD object"


def _is_star_like_simbad_object_type(object_type: str) -> bool:
    normalized_type = _normalize_identifier(object_type)
    raw_type = normalized_type.upper()
    compact_type = raw_type.replace(" ", "")
    if any(token in normalized_type.lower() for token in _STAR_LIKE_SIMBAD_TYPES):
        return True
    return any(
        _sky_explorer_type_matches(compact_type, raw_type, candidate)
        for candidate in (
            "*",
            "V*",
            "SB*",
            "EB*",
            "Al*",
            "Y*O",
            "YSO",
            "TT*",
            "Or*",
            "FU*",
            "Em*",
            "WD*",
            "BD*",
            "Planetary System",
            "Star",
        )
    )


def _is_structural_simbad_galaxy_system_type(object_type: str) -> bool:
    normalized_type = _normalize_identifier(object_type).upper().replace(" ", "")
    return normalized_type in {
        "IG",
        "PAG",
        "GRG",
        "CGG",
        "CLG",
        "SCG",
        "INTERACTINGGALAXIES",
        "PAIROFGALAXIES",
        "GROUPOFGALAXIES",
        "COMPACTGROUPOFGALAXIES",
        "CLUSTEROFGALAXIES",
        "SUPERCLUSTEROFGALAXIES",
    }


def _identifier_candidates(raw_identifiers: str) -> Iterable[str]:
    for item in str(raw_identifiers or "").split("|"):
        normalized = _normalize_identifier(item)
        if normalized:
            yield normalized


def _normalize_identifier(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _simbad_layer_for_row(row: Row) -> str:
    identifiers = " ".join(_identifier_candidates(_row_text(row, "IDS") or _row_text(row, "ids") or ""))
    main_id = _normalize_identifier(_row_text(row, "MAIN_ID") or _row_text(row, "main_id") or "")
    object_type = (_row_text(row, "OTYPE") or _row_text(row, "otype") or "").strip().lower()
    compact_object_type = object_type.upper().replace(" ", "")
    identifier_text = f"{main_id} {identifiers}".upper()
    if _is_star_like_simbad_object_type(object_type):
        return "general_objects"
    if _catalog_designation_key_from_text(identifier_text) is not None and not _looks_like_named_member_star(main_id):
        return "deep_sky"
    return "deep_sky"


def _looks_like_named_member_star(main_id: str) -> bool:
    normalized = _normalize_identifier(main_id).upper()
    for prefix in ("NGC ", "IC "):
        if normalized.startswith(prefix):
            trailing = normalized[len(prefix):].strip()
            if trailing and all(character.isdigit() or character.isspace() for character in trailing):
                return len(trailing.split()) > 1
    if normalized.startswith(("CL* NGC ", "CL* IC ")):
        trailing = normalized.split(" ", 2)[-1].strip()
        parts = trailing.split()
        return len(parts) > 1 and parts[0].isdigit()
    return False


def _sky_object_identity_key(item: SkyExplorerObject) -> tuple[str, str, int, int]:
    return (
        item.layer_key,
        _normalize_identifier(item.name).upper(),
        int(round(float(item.ra_deg) * 3600.0)),
        int(round(float(item.dec_deg) * 3600.0)),
    )


def _sky_object_position_key(item: SkyExplorerObject) -> tuple[str, int, int]:
    return (
        item.layer_key,
        int(round(float(item.ra_deg) * 360.0)),
        int(round(float(item.dec_deg) * 360.0)),
    )


def _sky_object_designation_key(item: SkyExplorerObject) -> tuple[str, str] | None:
    designation_keys = _sky_object_designation_keys(item)
    return next(iter(designation_keys), None)


def _sky_object_designation_keys(item: SkyExplorerObject) -> set[tuple[str, str]]:
    designations: list[str] = []
    for candidate in (item.name, item.source_id):
        designation = _catalog_designation_key(candidate)
        if designation is not None and designation not in designations:
            designations.append(designation)
    if isinstance(item.metadata, dict):
        identifiers = item.metadata.get("identifiers")
        if isinstance(identifiers, str):
            for candidate in _identifier_candidates(identifiers):
                designation = _catalog_designation_key(candidate)
                if designation is not None and designation not in designations:
                    designations.append(designation)
    return {(item.layer_key, designation) for designation in designations}


def _append_unique_deep_sky_objects(
    target: list[SkyExplorerObject],
    candidates: Sequence[SkyExplorerObject],
    *,
    seen_keys: set[tuple[str, str, int, int]],
    seen_position_keys: set[tuple[str, int, int]],
    seen_designation_keys: set[tuple[str, str]],
    allow_new_object: Callable[[SkyExplorerObject], bool] | None = None,
) -> None:
    for sky_object in candidates:
        object_key = _sky_object_identity_key(sky_object)
        position_key = _sky_object_position_key(sky_object)
        designation_keys = _sky_object_designation_keys(sky_object)
        if object_key in seen_keys or position_key in seen_position_keys or bool(designation_keys & seen_designation_keys):
            _enrich_duplicate_deep_sky_object_geometry(target, sky_object)
            seen_keys.add(object_key)
            seen_position_keys.add(position_key)
            seen_designation_keys.update(designation_keys)
            continue
        if allow_new_object is not None and not allow_new_object(sky_object):
            continue
        seen_keys.add(object_key)
        seen_position_keys.add(position_key)
        seen_designation_keys.update(designation_keys)
        target.append(sky_object)


def _enrich_duplicate_deep_sky_object_geometry(target: list[SkyExplorerObject], candidate: SkyExplorerObject) -> None:
    candidate_designation_keys = _sky_object_designation_keys(candidate)
    candidate_identity_key = _sky_object_identity_key(candidate)
    candidate_position_key = _sky_object_position_key(candidate)
    for index, existing in enumerate(target):
        existing_designation_keys = _sky_object_designation_keys(existing)
        matches_existing = (
            _sky_object_identity_key(existing) == candidate_identity_key
            or _sky_object_position_key(existing) == candidate_position_key
            or bool(candidate_designation_keys & existing_designation_keys)
        )
        if not matches_existing:
            continue
        enriched = _sky_object_with_supplemental_geometry(existing, candidate)
        if enriched is not existing:
            target[index] = enriched
        return


def _primary_catalog_designation_key(item: SkyExplorerObject) -> str | None:
    return _catalog_designation_key(item.name) or _catalog_designation_key(item.source_id)


def _is_primary_designation_holder(item: SkyExplorerObject, shared_designation_keys: set[tuple[str, str]]) -> bool:
    primary = _primary_catalog_designation_key(item)
    if primary is None:
        return False
    return (item.layer_key, primary) in shared_designation_keys


def _sky_object_pair_separation_arcmin(existing: SkyExplorerObject, candidate: SkyExplorerObject) -> float:
    try:
        separation = SkyCoord(existing.ra_deg * u.deg, existing.dec_deg * u.deg).separation(
            SkyCoord(candidate.ra_deg * u.deg, candidate.dec_deg * u.deg)
        )
        return float(separation.arcmin)
    except Exception:
        return 0.0


def _preferred_duplicate_position_source(
    existing: SkyExplorerObject,
    candidate: SkyExplorerObject,
    *,
    shared_designation_keys: set[tuple[str, str]],
) -> str:
    if not shared_designation_keys:
        return "existing"
    existing_primary = _is_primary_designation_holder(existing, shared_designation_keys)
    candidate_primary = _is_primary_designation_holder(candidate, shared_designation_keys)
    if candidate_primary and not existing_primary:
        return "candidate"
    if existing_primary and not candidate_primary:
        return "existing"
    if existing_primary and candidate_primary:
        separation_arcmin = _sky_object_pair_separation_arcmin(existing, candidate)
        ngc_or_ic_designation = any(
            designation_key.startswith(("NGC", "IC"))
            for _layer_key, designation_key in shared_designation_keys
        )
        if separation_arcmin > 5.0 and ngc_or_ic_designation:
            if candidate.catalog == "ngc2000" and existing.catalog != "ngc2000":
                return "candidate"
            if existing.catalog == "ngc2000" and candidate.catalog != "ngc2000":
                return "existing"
    return "existing"


def _sky_object_with_supplemental_geometry(existing: SkyExplorerObject, candidate: SkyExplorerObject) -> SkyExplorerObject:
    existing_metadata = dict(existing.metadata) if isinstance(existing.metadata, dict) else {}
    candidate_metadata = candidate.metadata if isinstance(candidate.metadata, dict) else {}
    merged_metadata = dict(existing_metadata)
    changed = False
    geometry_changed = False
    replacement_values: dict[str, object] = {}
    shared_designation_keys = _sky_object_designation_keys(existing) & _sky_object_designation_keys(candidate)
    if _preferred_duplicate_position_source(
        existing,
        candidate,
        shared_designation_keys=shared_designation_keys,
    ) == "candidate":
        replacement_values["ra_deg"] = candidate.ra_deg
        replacement_values["dec_deg"] = candidate.dec_deg
        replacement_values["pixel_x"] = candidate.pixel_x
        replacement_values["pixel_y"] = candidate.pixel_y
        replacement_values["angular_distance_arcmin"] = candidate.angular_distance_arcmin
        changed = True
    for key in ("catalog_size_arcmin", "catalog_major_axis_arcmin", "catalog_minor_axis_arcmin", "catalog_position_angle_deg"):
        candidate_value = _metadata_positive_float(candidate_metadata, key)
        if candidate_value is None:
            continue
        existing_value = _metadata_positive_float(merged_metadata, key)
        if existing_value is not None and existing_value >= candidate_value:
            continue
        merged_metadata[key] = candidate_value
        changed = True
        geometry_changed = True
    existing_magnitude = _finite_float(existing.magnitude)
    candidate_magnitude = _finite_float(candidate.magnitude)
    if existing_magnitude is None and candidate_magnitude is not None:
        replacement_values["magnitude"] = candidate_magnitude
        merged_metadata.setdefault("magnitude_catalog", candidate.catalog)
        merged_metadata.setdefault("magnitude_source_id", candidate.source_id)
        changed = True
    preferred_name = _preferred_duplicate_deep_sky_name(existing, candidate)
    if preferred_name is not None and preferred_name != existing.name:
        replacement_values["name"] = preferred_name
        if existing.source_id == existing.name:
            replacement_values["source_id"] = preferred_name
        replacement_values["short_label"] = _short_label_for_name(preferred_name, fallback=existing.short_label or existing.name)
        changed = True
    preferred_object_type = _preferred_duplicate_deep_sky_object_type(existing, candidate)
    if preferred_object_type is not None and preferred_object_type != existing.object_type:
        replacement_values["object_type"] = preferred_object_type
        merged_metadata["object_type"] = preferred_object_type
        changed = True
    if changed:
        if geometry_changed:
            merged_metadata.setdefault("geometry_catalog", candidate.catalog)
            merged_metadata.setdefault("geometry_source_id", candidate.source_id)
        if "catalog_description" not in merged_metadata and "catalog_description" in candidate_metadata:
            merged_metadata["catalog_description"] = candidate_metadata["catalog_description"]
        return replace(existing, metadata=merged_metadata, **replacement_values)
    return existing


def _preferred_duplicate_deep_sky_name(existing: SkyExplorerObject, candidate: SkyExplorerObject) -> str | None:
    if "galaxy" not in sky_explorer_object_type_keys_for_object(existing) and "galaxy" not in sky_explorer_object_type_keys_for_object(candidate):
        return None
    existing_designation = _catalog_designation_key(existing.name) or _catalog_designation_key(existing.source_id)
    candidate_designation = _catalog_designation_key(candidate.name) or _catalog_designation_key(candidate.source_id)
    if candidate_designation is None or _ngc_ic_designation_priority(candidate_designation) >= 2:
        return None
    if existing_designation is not None and _ngc_ic_designation_priority(existing_designation) <= _ngc_ic_designation_priority(candidate_designation):
        return None
    return _display_name_from_designation_key(candidate_designation)


def _preferred_duplicate_deep_sky_object_type(existing: SkyExplorerObject, candidate: SkyExplorerObject) -> str | None:
    candidate_object_type = _normalize_identifier(candidate.object_type)
    if not candidate_object_type:
        return None
    existing_keys = set(sky_explorer_object_type_keys_for_object(existing))
    candidate_keys = set(sky_explorer_object_type_keys_for_object(candidate))
    promoted_keys = (
        "seyfert_galaxy",
        "active_galactic_nucleus",
        "galaxy_pair",
        "galaxy_group",
        "galaxy_cluster",
        "galaxy",
    )
    if any(type_key in candidate_keys and type_key not in existing_keys for type_key in promoted_keys):
        return candidate_object_type
    return None


def _ngc_ic_designation_priority(designation: str | None) -> int:
    normalized = _normalize_identifier(designation).upper().replace(" ", "")
    if normalized.startswith("NGC"):
        return 0
    if normalized.startswith("IC"):
        return 1
    return 2


def _hyperleda_object_has_named_alias(sky_object: SkyExplorerObject) -> bool:
    metadata = sky_object.metadata if isinstance(sky_object.metadata, dict) else {}
    if bool(metadata.get("has_named_alias")):
        return True
    return _catalog_named_alias_key(sky_object.name) is not None or _catalog_named_alias_key(sky_object.source_id) is not None


def _metadata_positive_float(metadata: dict[str, object], key: str) -> float | None:
    value = metadata.get(key)
    return _positive_float(value)


def _positive_float(value: object) -> float | None:
    if not isinstance(value, (int, float, str)):
        return None
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric_value) or numeric_value <= 0.0:
        return None
    return numeric_value


def _finite_float(value: object) -> float | None:
    if not isinstance(value, (int, float, str)):
        return None
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric_value):
        return None
    return numeric_value


def _catalog_designation_key(name: object) -> str | None:
    normalized = _normalize_identifier(name).upper()
    return _catalog_designation_key_from_text(normalized)


_CATALOG_DESIGNATION_PATTERNS: tuple[tuple[str, str, bool], ...] = (
    (r"\bM\s*0*(\d+)\b(?!\s*[-+]\s*\d)", "M", False),
        (r"\bNGC\s*0*(\d+)\s*([A-Z])?(?!\s*\d)\b", "NGC", True),
        (r"\bIC\s*0*(\d+)\s*([A-Z])?(?!\s*\d)\b", "IC", True),
    (r"\bLDN\s*0*(\d+)\b", "LDN", False),
    (r"\bLBN\s*0*(\d+)\b", "LBN", False),
    (r"\bBARNARD\s*0*(\d+)\b", "B", False),
    (r"\bB\s+0*(\d+)\b", "B", False),
    (r"\bB\s*[- ]\s*0*(\d+)\b", "B", False),
    (r"\b(?:SH\s*0*2|SH2|SHARPLESS)\s*[- ]?0*(\d+)\b", "SH2", False),
    (r"\b(?:VDB|VAN\s+DEN\s+BERGH)\s*0*(\d+)\b", "VDB", False),
    (r"\bHASH\s*0*(\d+)\b", "HASH", False),
)


def _catalog_designation_match_key(match: re.Match[str], prefix: str, suffix_group: bool) -> str:
    number = match.group(1).lstrip("0") or "0"
    suffix = match.group(2) if suffix_group and match.lastindex and match.lastindex >= 2 and match.group(2) else ""
    return f"{prefix}{number}{suffix}"


def _catalog_designation_key_from_text(text: object) -> str | None:
    keys = _catalog_designation_keys_from_text(text)
    return keys[0] if keys else None


def _catalog_designation_match_is_paper_local_id(normalized_text: str, match: re.Match[str]) -> bool:
    prefix = normalized_text[: match.start()].rstrip()
    if prefix.endswith("]") or prefix.endswith("CL*"):
        return True
    return bool(re.search(r"\[[^\]]+\]$", prefix))


def _catalog_designation_keys_from_text(text: object) -> tuple[str, ...]:
    normalized = _normalize_identifier(text).upper()
    if not normalized:
        return ()
    found: list[str] = []
    for pattern, prefix, suffix_group in _CATALOG_DESIGNATION_PATTERNS:
        for match in re.finditer(pattern, normalized):
            if _catalog_designation_match_is_paper_local_id(normalized, match):
                continue
            designation_key = _catalog_designation_match_key(match, prefix, suffix_group)
            if designation_key not in found:
                found.append(designation_key)
    return tuple(found)


def _display_name_from_designation_key(designation_key: str) -> str:
    normalized = _normalize_identifier(designation_key).upper().replace(" ", "")
    for prefix in ("NGC", "IC"):
        match = re.fullmatch(rf"{prefix}0*(\d+)([A-Z]?)", normalized)
        if match is not None:
            number, suffix = match.groups()
            return f"{prefix} {number.lstrip('0') or '0'}{suffix}"
    for prefix in ("HASH", "SH2", "LDN", "LBN", "VDB", "M", "B"):
        if normalized.startswith(prefix) and normalized[len(prefix):].isdigit():
            number = normalized[len(prefix):].lstrip("0") or "0"
            if prefix == "SH2":
                return f"Sh2-{number}"
            if prefix == "VDB":
                return f"VdB {number}"
            if prefix == "HASH":
                return f"HASH {number}"
            return f"{prefix} {number}"
    return designation_key


def _catalog_named_alias_key(name: object) -> str | None:
    designation_key = _catalog_designation_key(name)
    if designation_key is not None:
        return designation_key
    normalized = _normalize_identifier(name).upper()
    match = re.search(r"\bMCG\s*([+-]?\d+)\s*[- ](\d+)\s*[- ](\d+)\b", normalized)
    if match is not None:
        zone_text, field_text, object_text = match.groups()
        sign = "+" if zone_text.startswith("+") else "-"
        zone_number = abs(int(zone_text))
        return f"MCG{sign}{zone_number:02d}-{int(field_text):02d}-{int(object_text):03d}"
    return None


def _display_name_from_named_alias_key(alias_key: str) -> str:
    if alias_key.startswith(("NGC", "IC")):
        return _display_name_from_designation_key(alias_key)
    return alias_key


def _ngc2000_type_label(type_code: str) -> str:
    normalized = _normalize_identifier(type_code).upper()
    return {
        "OC": "Open Cluster",
        "GC": "Globular Cluster",
        "PL": "Planetary Nebula",
        "GX": "Galaxy",
        "NB": "Nebula",
        "CL+N": "Cluster with Nebulosity",
        "AST": "Asterism",
    }.get(normalized, normalized or "Deep Sky Object")


def _ngc2000_object_name(raw_name: str) -> str:
    normalized = _normalize_identifier(raw_name)
    compact = normalized.upper().replace(" ", "")
    if compact.startswith("IC") and compact[2:].isdigit():
        return f"IC {compact[2:].lstrip('0') or '0'}"
    if compact.startswith("I") and compact[1:].isdigit():
        return f"IC {compact[1:].lstrip('0') or '0'}"
    if compact.startswith("NGC") and compact[3:].isdigit():
        return f"NGC {compact[3:].lstrip('0') or '0'}"
    if normalized.isdigit():
        return f"NGC {normalized.lstrip('0') or '0'}"
    return normalized or "NGC/IC object"


def _short_label_for_name(name: str, *, fallback: str) -> str:
    normalized = _normalize_identifier(name)
    if normalized:
        if len(normalized) <= 18:
            return normalized
        return normalized[:15].rstrip() + "..."
    return _normalize_identifier(fallback) or "Object"


def _summary_text(
    *,
    solved_field: SolvedField,
    layer_summaries: Sequence[SkyExplorerLayerSummary],
    used_astrometry_fallback: bool,
) -> str:
    layer_bits = [
        f"{summary.title}: {summary.displayed_count}"
        for summary in layer_summaries
        if summary.displayed_count > 0
    ]
    suffix = " via astrometry.net fallback" if used_astrometry_fallback else ""
    layer_text = ", ".join(layer_bits) if layer_bits else "no visible catalog objects"
    return (
        f"Resolved a {solved_field.radius_deg:.3f} deg field{suffix} and prepared {layer_text} "
        f"for image overlay and table review."
    )


def _sky_explorer_object_sort_key(item: SkyExplorerObject) -> tuple[int, float, str]:
    try:
        layer_index = SKY_EXPLORER_LAYER_ORDER.index(item.layer_key)
    except ValueError:
        layer_index = len(SKY_EXPLORER_LAYER_ORDER)
    magnitude_sort = item.magnitude if item.magnitude is not None and math.isfinite(float(item.magnitude)) else 99.0
    return (layer_index, magnitude_sort, item.name.lower())


def _row_text(row: Row, key: str) -> str | None:
    if key not in row.colnames:
        return None
    value = row[key]
    if bool(getattr(value, "mask", False)):
        return None
    text = str(value).strip()
    return text or None


def _row_float(row: Row, key: str) -> float | None:
    if key not in row.colnames:
        return None
    value = row[key]
    if bool(getattr(value, "mask", False)):
        return None
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric_value):
        return None
    return numeric_value


def _row_float_any(row: Row, *keys: str) -> float | None:
    for key in keys:
        value = _row_float(row, key)
        if value is not None:
            return value
    return None


def _catalog_geometry_metadata(
    *,
    major_axis_arcmin: float | None,
    minor_axis_arcmin: float | None,
    position_angle_deg: float | None,
) -> dict[str, float]:
    metadata: dict[str, float] = {}
    normalized_major = float(major_axis_arcmin) if major_axis_arcmin is not None and major_axis_arcmin > 0.0 else None
    normalized_minor = float(minor_axis_arcmin) if minor_axis_arcmin is not None and minor_axis_arcmin > 0.0 else None
    if normalized_major is None and normalized_minor is not None:
        normalized_major = normalized_minor
    if normalized_minor is None and normalized_major is not None:
        normalized_minor = normalized_major
    if normalized_major is not None and normalized_minor is not None and normalized_minor > normalized_major:
        normalized_minor = normalized_major
    if normalized_major is not None:
        metadata["catalog_size_arcmin"] = normalized_major
        metadata["catalog_major_axis_arcmin"] = normalized_major
    if normalized_minor is not None:
        metadata["catalog_minor_axis_arcmin"] = normalized_minor
    if position_angle_deg is not None and math.isfinite(float(position_angle_deg)):
        metadata["catalog_position_angle_deg"] = float(position_angle_deg)
    return metadata


def _sky_explorer_angular_size_arcmin_from_row(row: Row) -> float | None:
    geometry = _catalog_geometry_metadata(
        major_axis_arcmin=_row_float_any(
            row,
            "GALDIM_MAJAXIS",
            "galdim_majaxis",
            "DIM_MAJAXIS",
            "dim_majaxis",
        ),
        minor_axis_arcmin=_row_float_any(
            row,
            "GALDIM_MINAXIS",
            "galdim_minaxis",
            "DIM_MINAXIS",
            "dim_minaxis",
        ),
        position_angle_deg=_row_float_any(
            row,
            "GALDIM_ANGLE",
            "galdim_angle",
            "DIM_ANGLE",
            "dim_angle",
        ),
    )
    size_arcmin = geometry.get("catalog_size_arcmin")
    return float(size_arcmin) if isinstance(size_arcmin, float) else None


def _hyperleda_source_id(row: Row) -> str:
    pgc_value = _row_text(row, "PGC")
    if pgc_value is None:
        return "PGC galaxy"
    return f"PGC {pgc_value}"


def _hyperleda_preferred_name(row: Row, *, fallback: str) -> str:
    for candidate in _hyperleda_alias_candidates(row):
        named_alias_key = _catalog_named_alias_key(candidate)
        if named_alias_key is not None:
            return _display_name_from_named_alias_key(named_alias_key)
    for candidate in _hyperleda_alias_candidates(row):
        if candidate.upper() not in {"---", "NONE", "LEDA", "SIMBAD", "NED"}:
            return candidate
    return fallback


def _hyperleda_has_named_alias(row: Row) -> bool:
    return any(_catalog_named_alias_key(candidate) is not None for candidate in _hyperleda_alias_candidates(row))


def _hyperleda_alias_candidates(row: Row) -> Iterable[str]:
    raw_aliases = _row_text(row, "ANames")
    if raw_aliases:
        for match in re.finditer(r"\b(?:NGC|IC)\s*0*\d+\s*[A-Za-z]?\b", raw_aliases, flags=re.IGNORECASE):
            yield _normalize_identifier(match.group(0))
        for candidate in re.split(r"\s{2,}|[;,|]", raw_aliases):
            normalized = _normalize_identifier(candidate)
            if normalized:
                yield normalized
    source_id = _hyperleda_source_id(row)
    if source_id:
        yield source_id


def _hyperleda_geometry_metadata_from_row(row: Row) -> dict[str, float]:
    major_axis_arcmin = _hyperleda_major_axis_arcmin_from_row(row)
    if major_axis_arcmin is None:
        return {}
    log_ratio = _row_float(row, "logR25")
    minor_axis_arcmin = major_axis_arcmin
    if log_ratio is not None and math.isfinite(log_ratio):
        axis_ratio = max(1.0, 10.0 ** float(log_ratio))
        minor_axis_arcmin = major_axis_arcmin / axis_ratio
    return _catalog_geometry_metadata(
        major_axis_arcmin=major_axis_arcmin,
        minor_axis_arcmin=minor_axis_arcmin,
        position_angle_deg=_row_float(row, "PA"),
    )


def _hyperleda_major_axis_arcmin_from_row(row: Row) -> float | None:
    log_d25 = _row_float(row, "logD25")
    if log_d25 is None or not math.isfinite(log_d25):
        return None
    return 0.1 * (10.0 ** float(log_d25))


def _row_coordinates_deg(row: Row) -> tuple[float, float] | None:
    ra_value = _row_float_any(row, "RA_d", "ra_d", "RA", "ra")
    dec_value = _row_float_any(row, "DEC_d", "dec_d", "DEC", "dec")
    if ra_value is not None and dec_value is not None:
        return float(ra_value), float(dec_value)

    ra_text = _row_text(row, "RA") or _row_text(row, "ra")
    dec_text = _row_text(row, "DEC") or _row_text(row, "dec")
    if ra_text is None or dec_text is None:
        return None

    for unit_pair in ((u.hourangle, u.deg), (u.deg, u.deg)):
        try:
            coordinates = SkyCoord(ra_text, dec_text, unit=unit_pair, frame="icrs")
        except Exception:
            continue
        return float(coordinates.ra.deg), float(coordinates.dec.deg)
    return None


def np_is_finite(value: object) -> bool:
    try:
        return bool(math.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def format_sky_explorer_angular_distance(arcsec: float) -> str:
    """Format an angular separation with auto-selected arcsec / arcmin / degree units."""
    value_arcsec = float(arcsec)
    if not math.isfinite(value_arcsec):
        return "—"
    abs_arcsec = abs(value_arcsec)
    if abs_arcsec < 60.0:
        return f'{value_arcsec:.2f}"'
    abs_arcmin = abs_arcsec / 60.0
    if abs_arcmin < 60.0:
        return f"{value_arcsec / 60.0:.2f}'"
    return f"{value_arcsec / 3600.0:.3f}°"


def format_sky_explorer_ruler_distance_label(
    *,
    pixel_distance: float,
    angular_separation_arcsec: float | None,
) -> str:
    """Label for a Sky Explorer ruler: angular units when WCS is available, otherwise pixels."""
    if angular_separation_arcsec is not None and math.isfinite(float(angular_separation_arcsec)):
        return format_sky_explorer_angular_distance(float(angular_separation_arcsec))
    distance_px = float(pixel_distance)
    if not math.isfinite(distance_px):
        return "—"
    if abs(distance_px) >= 100.0:
        return f"{distance_px:.0f} px"
    if abs(distance_px) >= 10.0:
        return f"{distance_px:.1f} px"
    return f"{distance_px:.2f} px"


def sky_explorer_angular_separation_arcsec(
    ra_a_deg: float,
    dec_a_deg: float,
    ra_b_deg: float,
    dec_b_deg: float,
) -> float:
    first = SkyCoord(float(ra_a_deg) * u.deg, float(dec_a_deg) * u.deg, frame="icrs")
    second = SkyCoord(float(ra_b_deg) * u.deg, float(dec_b_deg) * u.deg, frame="icrs")
    return float(first.separation(second).arcsec)