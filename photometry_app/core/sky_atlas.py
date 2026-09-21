from __future__ import annotations

import csv
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from time import perf_counter, sleep
from typing import Any, cast
from urllib.parse import quote_plus

import requests
from astroquery.vizier import Vizier

from photometry_app.core.sky_atlas_catalog_storage import sky_atlas_catalog_root

_HIPPARCOS_VIZIER_CATALOG = "I/239/hip_main"
_TYCHO2_VIZIER_CATALOG = "I/259/tyc2"
_GAIA_DR3_VIZIER_CATALOG = "I/355/gaiadr3"
_GAIA_FLATHUB_ENDPOINT = "https://flathub.flatironinstitute.org/api/gaiadr3"
_GAIA_STREAM_CONNECT_TIMEOUT_SEC = 15
_GAIA_STREAM_READ_TIMEOUT_SEC = 60
_GAIA_PROGRESS_EVERY_STARS = 100_000
_GAIA_PROGRESS_MIN_INTERVAL_SEC = 0.25
_VIZIER_QUERY_TIMEOUT_SEC = 60
_SKY_ATLAS_HTTP_USER_AGENT = "CitizenAstronomy-SkyAtlas/1.0"
_NGC2000_VIZIER_CATALOG = "VII/118/ngc2000"
_VDB_VIZIER_CATALOG = "VII/21/catalog"
_LDN_VIZIER_CATALOG = "VII/7A/ldn"
_LBN_VIZIER_CATALOG = "VII/9"
_BARNARD_VIZIER_CATALOG = "VII/220A"
_SHARPLESS_VIZIER_CATALOG = "VII/20"
_RCW_VIZIER_CATALOG = "VII/216"
_HASH_PN_VIZIER_CATALOG = "V/163/pnmain"
_DEFAULT_SCIENTIFIC_STAR_MAGNITUDE_LIMIT = 9.5
_HIPPARCOS_PRACTICAL_MAGNITUDE_LIMIT = 12.0
_TYCHO_PRACTICAL_MAGNITUDE_LIMIT = 12.5
_MAX_SKY_ATLAS_STAR_MAGNITUDE_LIMIT = 15.0
SKY_ATLAS_WIDE_FIELD_STAR_MAGNITUDE = 5.0
SKY_ATLAS_STARTUP_STAR_MAGNITUDE = 6.5
_STARTUP_CACHE_FALLBACK_SLACK_MAG = 1.0
_SCIENTIFIC_CATALOG_CACHE_SCHEMA_VERSION = 1
_DEEP_SKY_CATALOG_CACHE_SCHEMA_VERSION = 1
_NAMED_OBJECT_DEDUPLICATION_RADIUS_DEG = 0.12
_ALWAYS_VISIBLE_SKY_ATLAS_CATALOGS = frozenset({"Hipparcos", "Tycho-2", "Gaia DR3", "Bright Star", "Local", "Solar System"})
SKY_ATLAS_DEEP_SKY_CATALOG_NAMES = (
    "Messier",
    "NGC",
    "IC",
    "VdB",
    "LDN",
    "LBN",
    "Barnard",
    "Sharpless",
    "RCW",
    "HASH PN",
)
_DEEP_SKY_CATALOG_COLORS = {
    "Messier": "#a7f3d0",
    "NGC": "#93c5fd",
    "IC": "#bfdbfe",
    "VdB": "#fde68a",
    "LDN": "#c4b5fd",
    "LBN": "#67e8f9",
    "Barnard": "#e9d5ff",
    "Sharpless": "#fda4af",
    "RCW": "#fdba74",
    "HASH PN": "#5eead4",
}
_HASH_PN_SKIP_STATUSES = frozenset({"Q", "TEST"})
_HASH_PN_STATUS_TYPES: dict[str, str] = {
    "T": "Planetary Nebula",
    "L": "Planetary Nebula",
    "P": "Planetary Nebula",
    "C": "Planetary Nebula",
    "A": "Planetary Nebula",
    "B": "Planetary Nebula",
    "HII": "HII Region",
    "SNR": "Supernova Remnant",
    "SR?": "Supernova Remnant",
    "RNE": "Reflection Nebula",
    "EMO": "Emission Nebula",
    "G": "Galaxy",
    "G?": "Galaxy",
}
_HIPPARCOS_IDENTIFIER_PATTERN = re.compile(r"^HIP\s*(\d+)\b", flags=re.IGNORECASE)
_MESSIER_PATTERN = re.compile(r"^(?:MESSIER|M)\s*0*(\d+)\b", flags=re.IGNORECASE)
_NGC_PATTERN = re.compile(r"^NGC\s*0*(\d+[A-Za-z]?)\b", flags=re.IGNORECASE)
_IC_PATTERN = re.compile(r"^IC\s*0*(\d+[A-Za-z]?)\b", flags=re.IGNORECASE)
_VDB_PATTERN = re.compile(r"^(?:VDB|V\s*DB)\s*0*(\d+[A-Za-z]?)\b", flags=re.IGNORECASE)
_BARNARD_PATTERN = re.compile(r"^(?:BARNARD|B)\s*0*(\d+[A-Za-z]?)\b", flags=re.IGNORECASE)
_SH2_PATTERN = re.compile(r"^(?:SH\s*2|SH2|SHARPLESS)\s*-?\s*0*(\d+[A-Za-z]?)\b", flags=re.IGNORECASE)
_RCW_PATTERN = re.compile(r"^RCW\s*0*(\d+[A-Za-z]?)\b", flags=re.IGNORECASE)
_HASH_PN_PATTERN = re.compile(r"^HASH\s*0*(\d+)\b", flags=re.IGNORECASE)
_OTHER_IDENTIFIER_PATTERNS: tuple[tuple[re.Pattern[str], str, int], ...] = (
    (re.compile(r"^HR\s*0*(\d+[A-Za-z]?)\b", flags=re.IGNORECASE), "HR {value}", 10),
    (re.compile(r"^HD\s*0*(\d+[A-Za-z]?)\b", flags=re.IGNORECASE), "HD {value}", 11),
    (re.compile(r"^HIP\s*0*(\d+[A-Za-z]?)\b", flags=re.IGNORECASE), "HIP {value}", 12),
    (re.compile(r"^BD\s*([+\-].+)$", flags=re.IGNORECASE), "BD {value}", 13),
    (re.compile(r"^CD\s*([+\-].+)$", flags=re.IGNORECASE), "CD {value}", 14),
    (re.compile(r"^CPD\s*([+\-].+)$", flags=re.IGNORECASE), "CPD {value}", 15),
    (re.compile(r"^SAO\s*0*(\d+[A-Za-z]?)\b", flags=re.IGNORECASE), "SAO {value}", 16),
    (re.compile(r"^GJ\s*([0-9A-Za-z.+\- ]+)$", flags=re.IGNORECASE), "GJ {value}", 17),
    (re.compile(r"^LHS\s*([0-9A-Za-z.+\- ]+)$", flags=re.IGNORECASE), "LHS {value}", 18),
    (re.compile(r"^LACAILLE\s*([0-9A-Za-z.+\- ]+)$", flags=re.IGNORECASE), "Lacaille {value}", 19),
)
CancelCheck = Callable[[], None]


class SkyAtlasCatalogCancelled(Exception):
    """Raised when a Sky Atlas catalog download is cancelled."""


def _raise_if_catalogue_cancelled(cancel_check: CancelCheck | None) -> None:
    if cancel_check is not None:
        cancel_check()


_TECHNICAL_NAME_PREFIXES = (
    "HD ",
    "HIP ",
    "HR ",
    "BD",
    "CD",
    "CPD",
    "SAO ",
    "GJ ",
    "LHS ",
    "LACAILLE ",
    "LS ",
    "PM ",
    "PN ",
    "OAO ",
    "TYC ",
    "GAIA ",
    "2MASS ",
    "WISE ",
    "IRAS ",
    "CED ",
    "CYG OB2 ",
    "VI CYG ",
    "PZT",
    "BAC ",
    "MOAI ",
    "LSF ",
    "KOBE-",
    "** ",
)


@dataclass(frozen=True, slots=True)
class SkyAtlasObject:
    name: str
    object_type: str
    ra_deg: float
    dec_deg: float
    magnitude: float | None
    catalog: str
    aliases: tuple[str, ...] = ()
    color: str = "#f8fbff"
    constellation: str = ""
    description: str = ""
    searchable: bool = True
    label_visible: bool = True
    selectable: bool = True

    @property
    def search_text(self) -> str:
        return " ".join((self.name, self.object_type, self.catalog, self.constellation, *self.aliases)).casefold()


@dataclass(frozen=True, slots=True)
class SkyAtlasLiteratureLink:
    label: str
    url: str


def load_local_sky_atlas_objects() -> tuple[SkyAtlasObject, ...]:
    return _load_packaged_named_sky_atlas_objects()


def load_sky_atlas_objects(
    cache_dir: Path | None = None,
    *,
    maximum_scientific_magnitude: float = _DEFAULT_SCIENTIFIC_STAR_MAGNITUDE_LIMIT,
    download_if_missing: bool = False,
    enabled_deep_sky_catalogs: frozenset[str] | set[str] | tuple[str, ...] | None = None,
    progress_callback=None,
    cancel_check: CancelCheck | None = None,
    star_magnitude_achieved: list[float] | None = None,
) -> tuple[SkyAtlasObject, ...]:
    packaged_objects = list(_load_packaged_named_sky_atlas_objects())
    catalogs = normalize_sky_atlas_deep_sky_catalogs(enabled_deep_sky_catalogs)
    if "Messier" in catalogs:
        packaged_objects = _merge_packaged_targets_with_deep_sky_objects(
            packaged_objects,
            _load_packaged_messier_objects(),
        )
    if cache_dir is None:
        if star_magnitude_achieved is not None:
            star_magnitude_achieved.clear()
            star_magnitude_achieved.append(0.0)
        return filter_sky_atlas_objects_by_deep_sky_catalogs(
            tuple(sorted(packaged_objects, key=lambda item: (_magnitude_sort_key(item.magnitude), item.name.casefold()))),
            catalogs,
        )
    try:
        scientific_stars, achieved_limit = _load_scientific_sky_atlas_star_objects(
            cache_dir,
            maximum_magnitude=maximum_scientific_magnitude,
            download_if_missing=download_if_missing,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
    except SkyAtlasCatalogCancelled:
        raise
    except Exception:
        scientific_stars = ()
        achieved_limit = 0.0
    if star_magnitude_achieved is not None:
        star_magnitude_achieved.clear()
        star_magnitude_achieved.append(float(achieved_limit))
    if scientific_stars:
        packaged_objects = _merge_packaged_targets_with_scientific_stars(packaged_objects, scientific_stars)
    _raise_if_catalogue_cancelled(cancel_check)
    try:
        deep_sky_objects = load_sky_atlas_deep_sky_objects(
            cache_dir,
            enabled_catalogs=catalogs,
            download_if_missing=download_if_missing,
            cancel_check=cancel_check,
        )
    except SkyAtlasCatalogCancelled:
        raise
    except Exception:
        deep_sky_objects = ()
    if deep_sky_objects:
        packaged_objects = _merge_packaged_targets_with_deep_sky_objects(packaged_objects, deep_sky_objects)
    return filter_sky_atlas_objects_by_deep_sky_catalogs(
        tuple(sorted(packaged_objects, key=lambda item: (_magnitude_sort_key(item.magnitude), item.name.casefold()))),
        catalogs,
    )


def default_enabled_sky_atlas_deep_sky_catalogs() -> frozenset[str]:
    return frozenset({"Messier", "NGC"})


def normalize_sky_atlas_deep_sky_catalogs(
    enabled_deep_sky_catalogs: frozenset[str] | set[str] | tuple[str, ...] | None,
) -> frozenset[str]:
    if enabled_deep_sky_catalogs is None:
        return default_enabled_sky_atlas_deep_sky_catalogs()
    allowed = {name.casefold(): name for name in SKY_ATLAS_DEEP_SKY_CATALOG_NAMES}
    normalized: set[str] = set()
    for catalog in enabled_deep_sky_catalogs:
        resolved = allowed.get(str(catalog or "").strip().casefold())
        if resolved is not None:
            normalized.add(resolved)
    return frozenset(normalized)


def filter_sky_atlas_objects_by_deep_sky_catalogs(
    objects: tuple[SkyAtlasObject, ...],
    enabled_deep_sky_catalogs: frozenset[str] | set[str] | tuple[str, ...] | None,
) -> tuple[SkyAtlasObject, ...]:
    enabled = normalize_sky_atlas_deep_sky_catalogs(enabled_deep_sky_catalogs)
    filtered: list[SkyAtlasObject] = []
    for item in objects:
        catalog = str(item.catalog or "").strip()
        if item.object_type.casefold() == "star" or catalog in _ALWAYS_VISIBLE_SKY_ATLAS_CATALOGS:
            filtered.append(item)
            continue
        if catalog in enabled:
            filtered.append(item)
    return tuple(filtered)


def load_sky_atlas_deep_sky_objects(
    cache_dir: Path,
    *,
    enabled_catalogs: frozenset[str] | set[str] | tuple[str, ...] | None = None,
    download_if_missing: bool = False,
    cancel_check: CancelCheck | None = None,
) -> tuple[SkyAtlasObject, ...]:
    catalogs = normalize_sky_atlas_deep_sky_catalogs(enabled_catalogs)
    if not catalogs:
        return ()
    loaded: list[SkyAtlasObject] = []
    for catalog_name in SKY_ATLAS_DEEP_SKY_CATALOG_NAMES:
        if catalog_name not in catalogs:
            continue
        _raise_if_catalogue_cancelled(cancel_check)
        loaded.extend(
            _load_or_download_deep_sky_catalog(
                cache_dir,
                catalog_name,
                download_if_missing=download_if_missing,
            )
        )
    return tuple(sorted(loaded, key=lambda item: (_magnitude_sort_key(item.magnitude), item.name.casefold())))


def load_scientific_sky_atlas_star_objects(
    cache_dir: Path,
    *,
    maximum_magnitude: float = _DEFAULT_SCIENTIFIC_STAR_MAGNITUDE_LIMIT,
    download_if_missing: bool = False,
    progress_callback=None,
    cancel_check: CancelCheck | None = None,
) -> tuple[SkyAtlasObject, ...]:
    objects, _achieved = _load_scientific_sky_atlas_star_objects(
        cache_dir,
        maximum_magnitude=maximum_magnitude,
        download_if_missing=download_if_missing,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
    )
    return objects


def _load_scientific_sky_atlas_star_objects(
    cache_dir: Path,
    *,
    maximum_magnitude: float = _DEFAULT_SCIENTIFIC_STAR_MAGNITUDE_LIMIT,
    download_if_missing: bool = False,
    progress_callback=None,
    cancel_check: CancelCheck | None = None,
) -> tuple[tuple[SkyAtlasObject, ...], float]:
    catalog_root = sky_atlas_catalog_root(cache_dir)
    requested_limit = max(1.0, min(_MAX_SKY_ATLAS_STAR_MAGNITUDE_LIMIT, float(maximum_magnitude)))
    merged: list[SkyAtlasObject] = []
    achieved_limit = 0.0

    hip_limit = min(requested_limit, _HIPPARCOS_PRACTICAL_MAGNITUDE_LIMIT)
    if progress_callback is not None:
        progress_callback(f"Loading Hipparcos stars to mag {hip_limit:.1f}...", None)
    try:
        hipparcos, hip_achieved = _load_or_download_named_star_catalog(
            catalog_root,
            catalog_key="hipparcos",
            catalog_label="Hipparcos",
            maximum_magnitude=hip_limit,
            download_if_missing=download_if_missing,
            downloader=_download_hipparcos_star_objects,
            cancel_check=cancel_check,
        )
        merged.extend(hipparcos)
        achieved_limit = max(achieved_limit, hip_achieved)
    except SkyAtlasCatalogCancelled:
        raise
    except Exception:
        if progress_callback is not None:
            progress_callback("Hipparcos load failed; continuing with other catalogs...", None)

    if requested_limit > 9.5 + 1.0e-6:
        tycho_limit = min(requested_limit, _TYCHO_PRACTICAL_MAGNITUDE_LIMIT)
        if progress_callback is not None:
            progress_callback(f"Loading Tycho-2 stars to mag {tycho_limit:.1f}...", None)
        try:
            tycho, tycho_achieved = _load_or_download_named_star_catalog(
                catalog_root,
                catalog_key="tycho2",
                catalog_label="Tycho-2",
                maximum_magnitude=tycho_limit,
                download_if_missing=download_if_missing,
                downloader=_download_tycho2_star_objects,
                minimum_magnitude=9.4,
                cancel_check=cancel_check,
            )
            merged = _merge_star_catalogs(merged, tycho)
            achieved_limit = max(achieved_limit, tycho_achieved)
        except SkyAtlasCatalogCancelled:
            raise
        except Exception:
            if progress_callback is not None:
                progress_callback("Tycho-2 load failed; continuing...", None)

    if requested_limit > _TYCHO_PRACTICAL_MAGNITUDE_LIMIT + 1.0e-6:
        if progress_callback is not None:
            progress_callback(f"Loading Gaia DR3 stars to mag {requested_limit:.1f}...", None)
        try:
            gaia, gaia_achieved = _load_or_download_gaia_star_objects(
                catalog_root,
                maximum_magnitude=requested_limit,
                download_if_missing=download_if_missing,
                progress_callback=progress_callback,
                minimum_magnitude=_TYCHO_PRACTICAL_MAGNITUDE_LIMIT,
                cancel_check=cancel_check,
            )
            merged = _merge_star_catalogs(merged, gaia)
            achieved_limit = max(achieved_limit, gaia_achieved)
        except SkyAtlasCatalogCancelled:
            raise
        except Exception:
            if progress_callback is not None:
                progress_callback("Gaia DR3 load failed; continuing...", None)

    return (
        tuple(sorted(merged, key=lambda item: (_magnitude_sort_key(item.magnitude), item.name.casefold()))),
        achieved_limit,
    )


def _load_or_download_named_star_catalog(
    catalog_root: Path,
    *,
    catalog_key: str,
    catalog_label: str,
    maximum_magnitude: float,
    download_if_missing: bool,
    downloader,
    minimum_magnitude: float | None = None,
    cancel_check: CancelCheck | None = None,
) -> tuple[tuple[SkyAtlasObject, ...], float]:
    cache_path = _star_catalog_cache_path(catalog_root, catalog_key, maximum_magnitude)
    cached = _load_cached_scientific_star_objects(cache_path, maximum_magnitude)
    if cached:
        if minimum_magnitude is None:
            return cached, float(maximum_magnitude)
        return (
            tuple(item for item in cached if item.magnitude is None or item.magnitude > float(minimum_magnitude)),
            float(maximum_magnitude),
        )
    fallback = _best_available_named_star_catalog_cache(catalog_root, catalog_key, maximum_magnitude)
    if fallback is not None:
        fallback_path, fallback_limit = fallback
        if (
            (not download_if_missing)
            and fallback_limit > float(maximum_magnitude) + _STARTUP_CACHE_FALLBACK_SLACK_MAG
        ):
            return (), 0.0
        fallback_objects = _load_cached_scientific_star_objects(fallback_path, fallback_limit)
        if fallback_objects:
            filtered = fallback_objects
            if minimum_magnitude is not None:
                filtered = tuple(
                    item
                    for item in fallback_objects
                    if item.magnitude is None or item.magnitude > float(minimum_magnitude)
                )
            if fallback_limit + 1.0e-6 >= float(maximum_magnitude):
                requested = tuple(
                    item
                    for item in filtered
                    if item.magnitude is None or item.magnitude <= float(maximum_magnitude) + 1.0e-6
                )
                if requested:
                    if fallback_limit > float(maximum_magnitude) + 1.0e-6:
                        _store_startup_star_catalog_cache(
                            catalog_root,
                            catalog_key,
                            catalog_label,
                            requested,
                            float(maximum_magnitude),
                        )
                    return requested, float(maximum_magnitude)
            if filtered and ((not download_if_missing) or fallback_limit + 1.0e-6 >= float(maximum_magnitude)):
                return filtered, float(fallback_limit)
    if not download_if_missing:
        return (), 0.0
    _raise_if_catalogue_cancelled(cancel_check)
    downloaded = tuple(
        sorted(
            downloader(maximum_magnitude),
            key=lambda item: (_magnitude_sort_key(item.magnitude), item.name.casefold()),
        )
    )
    _raise_if_catalogue_cancelled(cancel_check)
    _store_cached_scientific_star_objects(
        cache_path,
        downloaded,
        maximum_magnitude,
        catalog_label=catalog_label,
    )
    if catalog_key == "hipparcos":
        _store_startup_star_catalog_cache(
            catalog_root,
            catalog_key,
            catalog_label,
            downloaded,
            SKY_ATLAS_STARTUP_STAR_MAGNITUDE,
        )
    if minimum_magnitude is None:
        return downloaded, float(maximum_magnitude)
    return (
        tuple(item for item in downloaded if item.magnitude is None or item.magnitude > float(minimum_magnitude)),
        float(maximum_magnitude),
    )


def _star_catalog_cache_path(catalog_root: Path, catalog_key: str, maximum_magnitude: float) -> Path:
    normalized_limit = f"{float(maximum_magnitude):.1f}".replace(".", "p")
    catalog_root.mkdir(parents=True, exist_ok=True)
    return catalog_root / f"{catalog_key}_vmag_le_{normalized_limit}.json"


def _scientific_catalog_cache_path(cache_dir: Path, maximum_magnitude: float) -> Path:
    # Backward-compatible Hipparcos path helper used by older tests.
    return _star_catalog_cache_path(sky_atlas_catalog_root(cache_dir), "hipparcos", maximum_magnitude)


def _best_available_named_star_catalog_cache(
    catalog_root: Path,
    catalog_key: str,
    maximum_magnitude: float,
) -> tuple[Path, float] | None:
    if not catalog_root.exists():
        return None
    requested_limit = float(maximum_magnitude)
    best_path: Path | None = None
    best_limit = float("-inf")
    prefix = f"{catalog_key}_vmag_le_"
    for candidate_path in catalog_root.glob(f"{prefix}*.json"):
        suffix = candidate_path.stem.removeprefix(prefix).replace("p", ".")
        try:
            candidate_limit = float(suffix)
        except ValueError:
            continue
        if candidate_limit + 1.0e-6 < requested_limit:
            continue
        if best_path is None or candidate_limit < best_limit:
            best_path = candidate_path
            best_limit = candidate_limit
    if best_path is None:
        for candidate_path in catalog_root.glob(f"{prefix}*.json"):
            suffix = candidate_path.stem.removeprefix(prefix).replace("p", ".")
            try:
                candidate_limit = float(suffix)
            except ValueError:
                continue
            if candidate_limit > requested_limit + 1.0e-6:
                continue
            if candidate_limit > best_limit:
                best_path = candidate_path
                best_limit = candidate_limit
    if best_path is None:
        return None
    return best_path, best_limit


def _best_available_scientific_catalog_cache(
    cache_dir: Path,
    maximum_magnitude: float,
) -> tuple[Path, float] | None:
    return _best_available_named_star_catalog_cache(
        sky_atlas_catalog_root(cache_dir),
        "hipparcos",
        maximum_magnitude,
    )


def _merge_star_catalogs(
    primary: list[SkyAtlasObject] | tuple[SkyAtlasObject, ...],
    secondary: tuple[SkyAtlasObject, ...],
) -> list[SkyAtlasObject]:
    merged = list(primary)
    known = {(round(item.ra_deg, 4), round(item.dec_deg, 4)) for item in merged}
    for star in secondary:
        key = (round(star.ra_deg, 4), round(star.dec_deg, 4))
        if key in known:
            continue
        known.add(key)
        merged.append(star)
    return merged


@lru_cache(maxsize=1)
def _load_packaged_named_sky_atlas_objects() -> tuple[SkyAtlasObject, ...]:
    data_path = resources.files("photometry_app").joinpath("data/sky_atlas_bright_objects.json")
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    objects: list[SkyAtlasObject] = []
    for entry in payload.get("objects", []):
        magnitude = _optional_float(entry.get("magnitude"))
        ra_deg = _normalized_ra_deg(float(entry["ra_deg"]))
        dec_deg = max(-90.0, min(90.0, float(entry["dec_deg"])))
        aliases = tuple(str(alias).strip() for alias in entry.get("aliases", []) if str(alias).strip())
        object_type = str(entry.get("type", "Object")).strip() or "Object"
        catalog = str(entry.get("catalog", "Local")).strip() or "Local"
        display_name, resolved_aliases = _resolve_sky_atlas_name_and_aliases(
            str(entry["name"]).strip(),
            aliases,
            object_type=object_type,
        )
        objects.append(
            SkyAtlasObject(
                name=display_name,
                object_type=object_type,
                ra_deg=ra_deg,
                dec_deg=dec_deg,
                magnitude=magnitude,
                catalog=catalog,
                aliases=resolved_aliases,
                color=str(entry.get("color", "#f8fbff")).strip() or "#f8fbff",
                constellation=str(entry.get("constellation", "")).strip(),
                description=str(entry.get("description", "")).strip(),
            )
        )
    return tuple(sorted(objects, key=lambda item: (_magnitude_sort_key(item.magnitude), item.name.casefold())))


def all_sky_atlas_deep_sky_catalogs() -> frozenset[str]:
    return frozenset(SKY_ATLAS_DEEP_SKY_CATALOG_NAMES)


def format_sky_atlas_search_result_title(sky_object: SkyAtlasObject) -> str:
    object_type = str(sky_object.object_type or "").strip() or "Object"
    name = str(sky_object.name or "").strip() or object_type
    return f"{object_type}  {name}"


def load_sky_atlas_search_objects(
    cache_dir: Path | None = None,
    *,
    displayed_objects: tuple[SkyAtlasObject, ...] = (),
) -> tuple[SkyAtlasObject, ...]:
    """Build a search index of packaged targets plus every cached catalog.

    Display filters do not apply here: disabled deep-sky catalogs stay searchable
    when their cache is already on disk. Missing catalogs are not downloaded.
    """
    merged = list(_load_packaged_named_sky_atlas_objects())
    merged = _merge_sky_atlas_search_objects(merged, _load_packaged_messier_objects())
    if cache_dir is not None:
        try:
            cached_catalogs = load_sky_atlas_deep_sky_objects(
                cache_dir,
                enabled_catalogs=all_sky_atlas_deep_sky_catalogs(),
                download_if_missing=False,
            )
        except Exception:
            cached_catalogs = ()
        merged = _merge_sky_atlas_search_objects(merged, cached_catalogs)
    searchable_displayed = tuple(
        item
        for item in displayed_objects
        if item.object_type.casefold() != "star" or item.searchable or item.label_visible
    )
    merged = _merge_sky_atlas_search_objects(merged, searchable_displayed)
    return tuple(sorted(merged, key=lambda item: (_magnitude_sort_key(item.magnitude), item.name.casefold())))


def search_sky_atlas_objects(
    objects: tuple[SkyAtlasObject, ...],
    query: str,
    *,
    limit: int = 80,
    magnitude_limit: float | None = None,
    deep_sky_magnitude_limit: float | None = None,
    require_searchable: bool = True,
    apply_magnitude_limits: bool = True,
) -> tuple[SkyAtlasObject, ...]:
    normalized_query = str(query or "").strip().casefold()
    star_limit = None if magnitude_limit is None else float(magnitude_limit)
    object_limit = (
        float(deep_sky_magnitude_limit)
        if deep_sky_magnitude_limit is not None
        else (None if magnitude_limit is None else max(16.0, float(magnitude_limit)))
    )

    def _passes_magnitude(item: SkyAtlasObject) -> bool:
        if not apply_magnitude_limits:
            return True
        if item.magnitude is None:
            return True
        if item.object_type.casefold() == "star":
            return star_limit is None or item.magnitude <= star_limit
        return object_limit is None or item.magnitude <= object_limit

    def _is_candidate(item: SkyAtlasObject) -> bool:
        if not _passes_magnitude(item):
            return False
        if not require_searchable:
            return True
        if item.searchable:
            return True
        return item.object_type.casefold() != "star"

    candidates = tuple(item for item in objects if _is_candidate(item))
    if not normalized_query:
        return tuple(
            sorted(candidates, key=lambda item: (_magnitude_sort_key(item.magnitude), item.name.casefold()))[
                : max(1, int(limit))
            ]
        )

    ranked: list[tuple[int, int, float, str, SkyAtlasObject]] = []
    for item in candidates:
        rank = _sky_atlas_search_rank(item, normalized_query)
        if rank is None:
            continue
        named_penalty = 0 if sky_atlas_object_has_given_name(item) else 1
        ranked.append((rank, named_penalty, _magnitude_sort_key(item.magnitude), item.name.casefold(), item))
    return tuple(item for *_sort, item in sorted(ranked)[: max(1, int(limit))])


def _sky_atlas_search_rank(item: SkyAtlasObject, normalized_query: str) -> int | None:
    name = item.name.casefold()
    aliases = tuple(alias.casefold() for alias in item.aliases)
    compact_query = re.sub(r"[\s\-_]", "", normalized_query)
    compact_name = re.sub(r"[\s\-_]", "", name)
    compact_aliases = tuple(re.sub(r"[\s\-_]", "", alias) for alias in aliases)
    if (
        normalized_query == name
        or normalized_query in aliases
        or compact_query == compact_name
        or compact_query in compact_aliases
    ):
        return 0
    if (
        name.startswith(normalized_query)
        or any(alias.startswith(normalized_query) for alias in aliases)
        or compact_name.startswith(compact_query)
        or any(alias.startswith(compact_query) for alias in compact_aliases)
    ):
        return 1
    if (
        normalized_query in name
        or any(normalized_query in alias for alias in aliases)
        or compact_query in compact_name
        or any(compact_query in alias for alias in compact_aliases)
    ):
        return 2
    if len(normalized_query) < 3:
        return None
    text = item.search_text
    if normalized_query in text or compact_query in re.sub(r"[\s\-_]", "", text):
        return 3
    return None


def _merge_sky_atlas_search_objects(
    primary: list[SkyAtlasObject] | tuple[SkyAtlasObject, ...],
    extra: tuple[SkyAtlasObject, ...] | list[SkyAtlasObject],
) -> list[SkyAtlasObject]:
    merged = list(primary)
    known_names = {item.name.casefold() for item in merged}
    known_aliases = {alias.casefold() for item in merged for alias in item.aliases}
    known_coordinates = {(round(item.ra_deg, 3), round(item.dec_deg, 3)) for item in merged}
    for item in extra:
        if item.name.casefold() in known_names:
            continue
        if any(alias.casefold() in known_aliases for alias in item.aliases):
            continue
        coordinate_key = (round(item.ra_deg, 3), round(item.dec_deg, 3))
        if coordinate_key in known_coordinates:
            continue
        merged.append(item)
        known_names.add(item.name.casefold())
        known_aliases.update(alias.casefold() for alias in item.aliases)
        known_coordinates.add(coordinate_key)
    return merged


def _load_cached_scientific_star_objects(cache_path: Path, maximum_magnitude: float) -> tuple[SkyAtlasObject, ...] | None:
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cache_path.unlink(missing_ok=True)
        return None
    if int(payload.get("schema_version", -1)) != _SCIENTIFIC_CATALOG_CACHE_SCHEMA_VERSION:
        cache_path.unlink(missing_ok=True)
        return None
    cached_limit = _optional_float(payload.get("maximum_magnitude"))
    if cached_limit is None or abs(cached_limit - float(maximum_magnitude)) > 1.0e-6:
        cache_path.unlink(missing_ok=True)
        return None
    raw_objects = payload.get("objects", [])
    if not raw_objects:
        cache_path.unlink(missing_ok=True)
        return None
    objects: list[SkyAtlasObject] = []
    for entry in raw_objects:
        raw_name = str(entry.get("name", "")).strip()
        base_aliases = tuple(str(alias).strip() for alias in entry.get("aliases", []) if str(alias).strip())
        hip_number = _extract_hip_identifier(raw_name, base_aliases)
        proper_names = () if hip_number is None else _load_packaged_star_name_aliases().get(hip_number, ())
        display_name, resolved_aliases = _resolve_sky_atlas_name_and_aliases(
            raw_name,
            base_aliases,
            object_type=str(entry.get("type", "Star")).strip() or "Star",
            preferred_name_candidates=proper_names,
        )
        has_actual_name = any(_is_actual_name_candidate(name) for name in proper_names)
        objects.append(
            SkyAtlasObject(
                name=display_name,
                object_type=str(entry.get("type", "Star")).strip() or "Star",
                ra_deg=_normalized_ra_deg(float(entry["ra_deg"])),
                dec_deg=max(-90.0, min(90.0, float(entry["dec_deg"]))),
                magnitude=_optional_float(entry.get("magnitude")),
                catalog=str(entry.get("catalog", "Hipparcos")).strip() or "Hipparcos",
                aliases=resolved_aliases,
                color=str(entry.get("color", "#f8fbff")).strip() or "#f8fbff",
                constellation=str(entry.get("constellation", "")).strip(),
                description=str(entry.get("description", "")).strip(),
                searchable=bool(entry.get("searchable", False)) or has_actual_name,
                label_visible=bool(entry.get("label_visible", False)) or has_actual_name,
                selectable=bool(entry.get("selectable", False)),
            )
        )
    return tuple(sorted(objects, key=lambda item: (_magnitude_sort_key(item.magnitude), item.name.casefold())))


def _store_startup_star_catalog_cache(
    catalog_root: Path,
    catalog_key: str,
    catalog_label: str,
    objects: tuple[SkyAtlasObject, ...],
    maximum_magnitude: float,
) -> None:
    if catalog_key != "hipparcos":
        return
    startup_limit = min(float(maximum_magnitude), SKY_ATLAS_STARTUP_STAR_MAGNITUDE)
    if startup_limit + 1.0e-6 < SKY_ATLAS_STARTUP_STAR_MAGNITUDE:
        return
    startup_path = _star_catalog_cache_path(catalog_root, catalog_key, SKY_ATLAS_STARTUP_STAR_MAGNITUDE)
    if startup_path.exists():
        return
    startup_objects = tuple(
        item
        for item in objects
        if item.magnitude is None or item.magnitude <= SKY_ATLAS_STARTUP_STAR_MAGNITUDE + 1.0e-6
    )
    if not startup_objects:
        return
    try:
        _store_cached_scientific_star_objects(
            startup_path,
            startup_objects,
            SKY_ATLAS_STARTUP_STAR_MAGNITUDE,
            catalog_label=catalog_label,
        )
    except OSError:
        return


def _store_cached_scientific_star_objects(
    cache_path: Path,
    objects: tuple[SkyAtlasObject, ...],
    maximum_magnitude: float,
    *,
    catalog_label: str = "Hipparcos",
) -> None:
    payload = {
        "schema_version": _SCIENTIFIC_CATALOG_CACHE_SCHEMA_VERSION,
        "catalog": catalog_label,
        "maximum_magnitude": float(maximum_magnitude),
        "objects": [
            {
                "name": item.name,
                "type": item.object_type,
                "ra_deg": float(item.ra_deg),
                "dec_deg": float(item.dec_deg),
                "magnitude": item.magnitude,
                "catalog": item.catalog,
                "aliases": list(item.aliases),
                "color": item.color,
                "constellation": item.constellation,
                "description": item.description,
                "searchable": item.searchable,
                "label_visible": item.label_visible,
                "selectable": item.selectable,
            }
            for item in objects
        ],
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _download_tycho2_star_objects(maximum_magnitude: float) -> tuple[SkyAtlasObject, ...]:
    vizier = Vizier(columns=["TYC1", "TYC2", "TYC3", "RA_ICRS", "DE_ICRS", "VTmag", "BTmag"], row_limit=-1)
    vizier.ROW_LIMIT = -1
    vizier.TIMEOUT = _VIZIER_QUERY_TIMEOUT_SEC
    try:
        result = cast(Any, vizier).query_constraints(
            catalog=_TYCHO2_VIZIER_CATALOG,
            VTmag=f"<={float(maximum_magnitude):.1f}",
        )
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Tycho-2 catalog download failed: {exc}") from exc
    if not result:
        return ()
    table = result[0]
    colnames = set(getattr(table, "colnames", ()))
    objects: list[SkyAtlasObject] = []
    for row in table:
        ra_deg = _optional_float(row["RA_ICRS"]) if "RA_ICRS" in colnames else None
        dec_deg = _optional_float(row["DE_ICRS"]) if "DE_ICRS" in colnames else None
        magnitude = _optional_float(row["VTmag"]) if "VTmag" in colnames else None
        if ra_deg is None or dec_deg is None or magnitude is None:
            continue
        if magnitude > float(maximum_magnitude):
            continue
        tyc1 = _optional_int(row["TYC1"]) if "TYC1" in colnames else None
        tyc2 = _optional_int(row["TYC2"]) if "TYC2" in colnames else None
        tyc3 = _optional_int(row["TYC3"]) if "TYC3" in colnames else None
        name = (
            f"TYC {tyc1}-{tyc2}-{tyc3}"
            if tyc1 is not None and tyc2 is not None and tyc3 is not None
            else f"TYC {len(objects) + 1}"
        )
        bt_mag = _optional_float(row["BTmag"]) if "BTmag" in colnames else None
        bv_index = None if bt_mag is None else bt_mag - magnitude
        objects.append(
            SkyAtlasObject(
                name=name,
                object_type="Star",
                ra_deg=_normalized_ra_deg(ra_deg),
                dec_deg=max(-90.0, min(90.0, dec_deg)),
                magnitude=magnitude,
                catalog="Tycho-2",
                aliases=(name,),
                color=_color_from_bv_index(bv_index),
                description="Tycho-2 star catalog",
                searchable=False,
                label_visible=False,
                selectable=magnitude <= 6.0,
            )
        )
    return tuple(objects)


def _gaia_npy_cache_path(catalog_root: Path, maximum_magnitude: float) -> Path:
    normalized_limit = f"{float(maximum_magnitude):.1f}".replace(".", "p")
    catalog_root.mkdir(parents=True, exist_ok=True)
    return catalog_root / f"gaia_dr3_vmag_le_{normalized_limit}.npy"


def _gaia_cache_dtype():
    import numpy as np

    return np.dtype(
        [
            ("source_id", "i8"),
            ("ra_deg", "f8"),
            ("dec_deg", "f8"),
            ("magnitude", "f4"),
            ("bp_rp", "f4"),
        ]
    )


def _load_cached_gaia_npy(cache_path: Path) -> tuple[SkyAtlasObject, ...] | None:
    if not cache_path.exists():
        return None
    try:
        import numpy as np

        records = np.load(cache_path, allow_pickle=False)
    except Exception:
        return None
    if records is None or len(records) == 0:
        return None
    return tuple(_gaia_object_from_record(record) for record in records)


def _store_cached_gaia_npy(cache_path: Path, records) -> None:
    import numpy as np

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    array = np.asarray(records, dtype=_gaia_cache_dtype())
    temporary_path = cache_path.with_name(f"{cache_path.name}.tmp.npy")
    try:
        np.save(temporary_path, array, allow_pickle=False)
        temporary_path.replace(cache_path)
    finally:
        if temporary_path.exists() and temporary_path != cache_path:
            temporary_path.unlink(missing_ok=True)


def _best_available_gaia_npy_cache(catalog_root: Path, maximum_magnitude: float) -> tuple[Path, float] | None:
    if not catalog_root.exists():
        return None
    requested_limit = float(maximum_magnitude)
    best_path: Path | None = None
    best_limit = float("-inf")
    prefix = "gaia_dr3_vmag_le_"
    for candidate_path in catalog_root.glob(f"{prefix}*.npy"):
        suffix = candidate_path.stem.removeprefix(prefix).replace("p", ".")
        try:
            candidate_limit = float(suffix)
        except ValueError:
            continue
        if candidate_limit + 1.0e-6 < requested_limit:
            continue
        if best_path is None or candidate_limit < best_limit:
            best_path = candidate_path
            best_limit = candidate_limit
    if best_path is None:
        for candidate_path in catalog_root.glob(f"{prefix}*.npy"):
            suffix = candidate_path.stem.removeprefix(prefix).replace("p", ".")
            try:
                candidate_limit = float(suffix)
            except ValueError:
                continue
            if candidate_limit > requested_limit + 1.0e-6:
                continue
            if candidate_limit > best_limit:
                best_path = candidate_path
                best_limit = candidate_limit
    if best_path is None:
        return None
    return best_path, best_limit


def _load_or_download_gaia_star_objects(
    catalog_root: Path,
    *,
    maximum_magnitude: float,
    download_if_missing: bool,
    progress_callback=None,
    minimum_magnitude: float = _TYCHO_PRACTICAL_MAGNITUDE_LIMIT,
    cancel_check: CancelCheck | None = None,
) -> tuple[tuple[SkyAtlasObject, ...], float]:
    npy_path = _gaia_npy_cache_path(catalog_root, maximum_magnitude)
    cached = _load_cached_gaia_npy(npy_path)
    if cached is None:
        json_path = _star_catalog_cache_path(catalog_root, "gaia_dr3", maximum_magnitude)
        cached = _load_cached_scientific_star_objects(json_path, maximum_magnitude)
        if cached is not None:
            return (
                tuple(item for item in cached if item.magnitude is None or item.magnitude > float(minimum_magnitude)),
                float(maximum_magnitude),
            )
        fallback = _best_available_gaia_npy_cache(catalog_root, maximum_magnitude)
        if fallback is not None:
            fallback_path, fallback_limit = fallback
            fallback_objects = _load_cached_gaia_npy(fallback_path)
            if fallback_objects is not None:
                return (
                    tuple(
                        item
                        for item in fallback_objects
                        if item.magnitude is None or item.magnitude > float(minimum_magnitude)
                    ),
                    float(fallback_limit),
                )
    else:
        return (
            tuple(item for item in cached if item.magnitude is None or item.magnitude > float(minimum_magnitude)),
            float(maximum_magnitude),
        )
    if not download_if_missing:
        return (), 0.0
    downloaded, records = _download_gaia_star_objects_from_flathub(
        maximum_magnitude,
        minimum_magnitude=minimum_magnitude,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
    )
    _raise_if_catalogue_cancelled(cancel_check)
    try:
        _store_cached_gaia_npy(npy_path, records)
    except Exception:
        if progress_callback is not None:
            progress_callback("Gaia DR3 catalog downloaded, but saving the cache file failed.", None)
    if progress_callback is not None:
        progress_callback(f"Gaia DR3 download complete ({len(downloaded):,} stars).", 100.0)
    return downloaded, float(maximum_magnitude)


def _gaia_object_from_record(record) -> SkyAtlasObject:
    source_id = _optional_int(record["source_id"] if hasattr(record, "dtype") else record[0])
    if hasattr(record, "dtype"):
        ra_deg = float(record["ra_deg"])
        dec_deg = float(record["dec_deg"])
        magnitude = _optional_float(record["magnitude"])
        bp_rp = _optional_float(record["bp_rp"])
    else:
        ra_deg = float(record[1])
        dec_deg = float(record[2])
        magnitude = _optional_float(record[3])
        bp_rp = _optional_float(record[4]) if len(record) > 4 else None
    name = f"Gaia DR3 {source_id}" if source_id is not None and source_id > 0 else "Gaia star"
    return SkyAtlasObject(
        name=name,
        object_type="Star",
        ra_deg=_normalized_ra_deg(ra_deg),
        dec_deg=max(-90.0, min(90.0, dec_deg)),
        magnitude=magnitude,
        catalog="Gaia DR3",
        aliases=(name,),
        color=_color_from_bv_index(None if bp_rp is None else bp_rp * 0.85),
        description="Gaia DR3 star catalog",
        searchable=False,
        label_visible=False,
        selectable=False,
    )


def _gaia_object_from_csv_row(row: dict[str, str], fallback_index: int) -> SkyAtlasObject | None:
    ra_deg = _optional_float(row.get("ra") or row.get("RAJ2000"))
    dec_deg = _optional_float(row.get("dec") or row.get("DEJ2000"))
    magnitude = _optional_float(row.get("phot_g_mean_mag") or row.get("Gmag"))
    if ra_deg is None or dec_deg is None or magnitude is None:
        return None
    source_id = _optional_int(row.get("source_id") or row.get("Source"))
    name = f"Gaia DR3 {source_id}" if source_id is not None else f"Gaia {fallback_index}"
    bp_rp = _optional_float(row.get("bp_rp") or row.get("BP-RP") or row.get("BP_RP"))
    return SkyAtlasObject(
        name=name,
        object_type="Star",
        ra_deg=_normalized_ra_deg(ra_deg),
        dec_deg=max(-90.0, min(90.0, dec_deg)),
        magnitude=magnitude,
        catalog="Gaia DR3",
        aliases=(name,),
        color=_color_from_bv_index(None if bp_rp is None else bp_rp * 0.85),
        description="Gaia DR3 star catalog",
        searchable=False,
        label_visible=False,
        selectable=False,
    )


def _count_gaia_flathub_stars(minimum_magnitude: float, maximum_magnitude: float) -> int | None:
    try:
        response = requests.post(
            f"{_GAIA_FLATHUB_ENDPOINT}/count",
            json={"phot_g_mean_mag": {"gte": float(minimum_magnitude), "lte": float(maximum_magnitude)}},
            timeout=(_GAIA_STREAM_CONNECT_TIMEOUT_SEC, _GAIA_STREAM_READ_TIMEOUT_SEC),
            headers={"User-Agent": _SKY_ATLAS_HTTP_USER_AGENT},
        )
        response.raise_for_status()
        return int(response.json())
    except (TypeError, ValueError, requests.RequestException):
        return None


def _download_gaia_star_objects_from_flathub(
    maximum_magnitude: float,
    *,
    minimum_magnitude: float,
    progress_callback=None,
    cancel_check: CancelCheck | None = None,
) -> tuple[tuple[SkyAtlasObject, ...], list[tuple[int, float, float, float, float]]]:
    _raise_if_catalogue_cancelled(cancel_check)
    if progress_callback is not None:
        progress_callback("Preparing Gaia DR3 catalog download...", None)
    expected_count = _count_gaia_flathub_stars(minimum_magnitude, maximum_magnitude)
    if progress_callback is not None:
        if expected_count:
            progress_callback(f"Downloading Gaia DR3 ({expected_count:,} stars)...", 0.0)
        else:
            progress_callback("Downloading Gaia DR3 catalog...", 0.0)
    params = {
        "fields": "source_id,ra,dec,phot_g_mean_mag,bp_rp",
        "phot_g_mean_mag": f"{float(minimum_magnitude):.3f},{float(maximum_magnitude):.3f}",
    }
    try:
        response = requests.get(
            f"{_GAIA_FLATHUB_ENDPOINT}/data/csv",
            params=params,
            stream=True,
            timeout=(_GAIA_STREAM_CONNECT_TIMEOUT_SEC, _GAIA_STREAM_READ_TIMEOUT_SEC),
            headers={"User-Agent": _SKY_ATLAS_HTTP_USER_AGENT},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Gaia DR3 catalog download failed: {exc}") from exc
    objects: list[SkyAtlasObject] = []
    records: list[tuple[int, float, float, float, float]] = []
    bytes_read = 0
    last_progress_at = 0.0
    try:
        reader = csv.DictReader(response.iter_lines(decode_unicode=True))
        for row in reader:
            _raise_if_catalogue_cancelled(cancel_check)
            if not row:
                continue
            sky_object = _gaia_object_from_csv_row(row, len(objects) + 1)
            if sky_object is None or sky_object.magnitude is None:
                continue
            if sky_object.magnitude <= float(minimum_magnitude) or sky_object.magnitude > float(maximum_magnitude):
                continue
            source_id = _optional_int(row.get("source_id") or row.get("Source")) or 0
            bp_rp = _optional_float(row.get("bp_rp") or row.get("BP-RP") or row.get("BP_RP"))
            objects.append(sky_object)
            records.append(
                (
                    int(source_id),
                    float(sky_object.ra_deg),
                    float(sky_object.dec_deg),
                    float(sky_object.magnitude),
                    float(bp_rp) if bp_rp is not None else float("nan"),
                )
            )
            bytes_read += 48
            if len(objects) % _GAIA_PROGRESS_EVERY_STARS == 0:
                sleep(0.001)
                if progress_callback is not None:
                    now = perf_counter()
                    if now - last_progress_at >= _GAIA_PROGRESS_MIN_INTERVAL_SEC:
                        last_progress_at = now
                        downloaded_mb = bytes_read / (1024 * 1024)
                        if expected_count:
                            progress_callback(
                                (
                                    f"Downloading Gaia DR3 ({len(objects):,} / {expected_count:,} stars, "
                                    f"{downloaded_mb:.0f} MB)..."
                                ),
                                min(99.0, (len(objects) / expected_count) * 100.0),
                            )
                        else:
                            progress_callback(
                                f"Downloading Gaia DR3 ({len(objects):,} stars, {downloaded_mb:.0f} MB)...",
                                None,
                            )
    finally:
        response.close()
    if not objects:
        raise RuntimeError("Gaia DR3 catalog download returned no stars.")
    return tuple(objects), records


def _download_hipparcos_star_objects(maximum_magnitude: float) -> tuple[SkyAtlasObject, ...]:
    vizier = Vizier(
        columns=["HIP", "RAICRS", "DEICRS", "Vmag", "B-V", "HD", "BD", "CoD", "CPD"],
        row_limit=-1,
    )
    vizier.ROW_LIMIT = -1
    vizier.TIMEOUT = _VIZIER_QUERY_TIMEOUT_SEC
    try:
        result = cast(Any, vizier).query_constraints(
            catalog=_HIPPARCOS_VIZIER_CATALOG,
            Vmag=f"<={float(maximum_magnitude):.1f}",
        )
    except Exception as exc:  # pragma: no cover - network/library failure path
        raise RuntimeError(f"Hipparcos catalog download failed: {exc}") from exc
    if not result:
        return ()
    table = result[0]
    colnames = set(getattr(table, "colnames", ()))
    objects: list[SkyAtlasObject] = []
    proper_name_aliases = _load_packaged_star_name_aliases()
    for row in table:
        hip_value = _optional_int(row["HIP"])
        ra_deg = _optional_float(row["RAICRS"])
        dec_deg = _optional_float(row["DEICRS"])
        magnitude = _optional_float(row["Vmag"])
        if hip_value is None or ra_deg is None or dec_deg is None or magnitude is None:
            continue
        if magnitude > float(maximum_magnitude):
            continue
        hip_name = f"HIP {hip_value}"
        extra_aliases: list[str] = [hip_name]
        if "HD" in colnames:
            hd_value = _optional_int(row["HD"])
            if hd_value is not None:
                extra_aliases.append(f"HD {hd_value}")
        for field_name, prefix in (("BD", "BD"), ("CoD", "CD"), ("CPD", "CPD")):
            if field_name not in colnames:
                continue
            raw_identifier = str(row[field_name]).strip()
            if raw_identifier and raw_identifier != "--":
                normalized_identifier = _normalized_signed_catalog_identifier(raw_identifier)
                if normalized_identifier:
                    extra_aliases.append(f"{prefix} {normalized_identifier}")
        preferred_names = proper_name_aliases.get(hip_value, ())
        display_name, resolved_aliases = _resolve_sky_atlas_name_and_aliases(
            hip_name,
            tuple(extra_aliases),
            object_type="Star",
            preferred_name_candidates=preferred_names,
        )
        has_actual_name = any(_is_actual_name_candidate(name) for name in preferred_names)
        objects.append(
            SkyAtlasObject(
                name=display_name,
                object_type="Star",
                ra_deg=_normalized_ra_deg(ra_deg),
                dec_deg=max(-90.0, min(90.0, dec_deg)),
                magnitude=magnitude,
                catalog="Hipparcos",
                aliases=resolved_aliases,
                color=_color_from_bv_index(_optional_float(row["B-V"]) if "B-V" in colnames else None),
                description="Hipparcos scientific star catalog",
                searchable=has_actual_name,
                label_visible=has_actual_name,
                selectable=magnitude <= 4.5,
            )
        )
    return tuple(objects)


def _merge_packaged_targets_with_scientific_stars(
    packaged_objects: list[SkyAtlasObject],
    scientific_stars: tuple[SkyAtlasObject, ...],
) -> list[SkyAtlasObject]:
    merged = list(packaged_objects)
    named_objects = tuple(item for item in packaged_objects if item.searchable)
    known_coordinates = {(round(item.ra_deg, 4), round(item.dec_deg, 4)) for item in packaged_objects}
    for star in scientific_stars:
        coordinate_key = (round(star.ra_deg, 4), round(star.dec_deg, 4))
        if coordinate_key in known_coordinates:
            continue
        if any(
            item.object_type.casefold() == "star"
            and _angular_separation_deg(item.ra_deg, item.dec_deg, star.ra_deg, star.dec_deg)
            <= _NAMED_OBJECT_DEDUPLICATION_RADIUS_DEG
            for item in named_objects
        ):
            continue
        merged.append(star)
    return merged


def _merge_packaged_targets_with_deep_sky_objects(
    packaged_objects: list[SkyAtlasObject],
    deep_sky_objects: tuple[SkyAtlasObject, ...],
) -> list[SkyAtlasObject]:
    merged = list(packaged_objects)
    known_names = {item.name.casefold() for item in packaged_objects}
    known_aliases = {alias.casefold() for item in packaged_objects for alias in item.aliases}
    known_coordinates = {(round(item.ra_deg, 3), round(item.dec_deg, 3)) for item in packaged_objects}
    for deep_sky_object in deep_sky_objects:
        if deep_sky_object.name.casefold() in known_names:
            continue
        if any(alias.casefold() in known_aliases for alias in deep_sky_object.aliases):
            continue
        coordinate_key = (round(deep_sky_object.ra_deg, 3), round(deep_sky_object.dec_deg, 3))
        if coordinate_key in known_coordinates:
            continue
        merged.append(deep_sky_object)
        known_names.add(deep_sky_object.name.casefold())
        known_aliases.update(alias.casefold() for alias in deep_sky_object.aliases)
        known_coordinates.add(coordinate_key)
    return merged


def _deep_sky_catalog_cache_path(cache_dir: Path, catalog_name: str) -> Path:
    scientific_dir = sky_atlas_catalog_root(cache_dir) / "deep-sky"
    scientific_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", catalog_name.casefold()).strip("-") or catalog_name.casefold()
    return scientific_dir / f"{slug}.json"


def _load_or_download_deep_sky_catalog(
    cache_dir: Path,
    catalog_name: str,
    *,
    download_if_missing: bool,
) -> tuple[SkyAtlasObject, ...]:
    if catalog_name == "Messier":
        packaged = _load_packaged_messier_objects()
        if packaged:
            return packaged
    cache_path = _deep_sky_catalog_cache_path(cache_dir, catalog_name)
    cached_objects = _load_cached_deep_sky_objects(cache_path, catalog_name)
    if cached_objects is not None:
        return cached_objects
    if not download_if_missing:
        return ()
    try:
        downloaded_objects = _download_deep_sky_catalog_objects(catalog_name)
    except Exception:
        return ()
    if downloaded_objects:
        _store_cached_deep_sky_objects(cache_path, downloaded_objects, catalog_name)
    return downloaded_objects


@lru_cache(maxsize=1)
def _load_packaged_messier_objects() -> tuple[SkyAtlasObject, ...]:
    data_path = resources.files("photometry_app").joinpath("data/sky_atlas_messier.json")
    try:
        payload = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, FileNotFoundError):
        return ()
    objects: list[SkyAtlasObject] = []
    for entry in payload.get("objects", []):
        magnitude = _optional_float(entry.get("magnitude"))
        aliases = tuple(str(alias).strip() for alias in entry.get("aliases", []) if str(alias).strip())
        object_type = str(entry.get("type", "Deep Sky Object")).strip() or "Deep Sky Object"
        display_name, resolved_aliases = _resolve_sky_atlas_name_and_aliases(
            str(entry["name"]).strip(),
            aliases,
            object_type=object_type,
        )
        objects.append(
            SkyAtlasObject(
                name=display_name,
                object_type=object_type,
                ra_deg=_normalized_ra_deg(float(entry["ra_deg"])),
                dec_deg=max(-90.0, min(90.0, float(entry["dec_deg"]))),
                magnitude=magnitude,
                catalog="Messier",
                aliases=resolved_aliases,
                color=str(entry.get("color", _DEEP_SKY_CATALOG_COLORS["Messier"])).strip() or _DEEP_SKY_CATALOG_COLORS["Messier"],
                constellation=str(entry.get("constellation", "")).strip(),
                description=str(entry.get("description", "")).strip() or "Messier catalog object",
                searchable=True,
                label_visible=True,
                selectable=True,
            )
        )
    return tuple(sorted(objects, key=lambda item: (_magnitude_sort_key(item.magnitude), item.name.casefold())))


def _load_cached_deep_sky_objects(cache_path: Path, catalog_name: str) -> tuple[SkyAtlasObject, ...] | None:
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cache_path.unlink(missing_ok=True)
        return None
    if int(payload.get("schema_version", -1)) != _DEEP_SKY_CATALOG_CACHE_SCHEMA_VERSION:
        cache_path.unlink(missing_ok=True)
        return None
    if str(payload.get("catalog", "")).strip() != catalog_name:
        cache_path.unlink(missing_ok=True)
        return None
    objects: list[SkyAtlasObject] = []
    for entry in payload.get("objects", []):
        objects.append(
            SkyAtlasObject(
                name=str(entry.get("name", "")).strip() or catalog_name,
                object_type=str(entry.get("type", "Deep Sky Object")).strip() or "Deep Sky Object",
                ra_deg=_normalized_ra_deg(float(entry["ra_deg"])),
                dec_deg=max(-90.0, min(90.0, float(entry["dec_deg"]))),
                magnitude=_optional_float(entry.get("magnitude")),
                catalog=str(entry.get("catalog", catalog_name)).strip() or catalog_name,
                aliases=tuple(str(alias).strip() for alias in entry.get("aliases", []) if str(alias).strip()),
                color=str(entry.get("color", _DEEP_SKY_CATALOG_COLORS.get(catalog_name, "#f8fbff"))).strip()
                or "#f8fbff",
                constellation=str(entry.get("constellation", "")).strip(),
                description=str(entry.get("description", "")).strip(),
                searchable=bool(entry.get("searchable", True)),
                label_visible=bool(entry.get("label_visible", True)),
                selectable=bool(entry.get("selectable", True)),
            )
        )
    return tuple(sorted(objects, key=lambda item: (_magnitude_sort_key(item.magnitude), item.name.casefold())))


def _store_cached_deep_sky_objects(
    cache_path: Path,
    objects: tuple[SkyAtlasObject, ...],
    catalog_name: str,
) -> None:
    payload = {
        "schema_version": _DEEP_SKY_CATALOG_CACHE_SCHEMA_VERSION,
        "catalog": catalog_name,
        "objects": [
            {
                "name": item.name,
                "type": item.object_type,
                "ra_deg": float(item.ra_deg),
                "dec_deg": float(item.dec_deg),
                "magnitude": item.magnitude,
                "catalog": item.catalog,
                "aliases": list(item.aliases),
                "color": item.color,
                "constellation": item.constellation,
                "description": item.description,
                "searchable": item.searchable,
                "label_visible": item.label_visible,
                "selectable": item.selectable,
            }
            for item in objects
        ],
    }
    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _download_deep_sky_catalog_objects(catalog_name: str) -> tuple[SkyAtlasObject, ...]:
    if catalog_name == "Messier":
        return _load_packaged_messier_objects()
    if catalog_name in {"NGC", "IC"}:
        return _download_ngc2000_objects(catalog_name)
    if catalog_name == "VdB":
        return _download_vdb_objects()
    if catalog_name == "LDN":
        return _download_ldn_objects()
    if catalog_name == "LBN":
        return _download_lbn_objects()
    if catalog_name == "Barnard":
        return _download_barnard_objects()
    if catalog_name == "Sharpless":
        return _download_sharpless_objects()
    if catalog_name == "RCW":
        return _download_rcw_objects()
    if catalog_name == "HASH PN":
        return _download_hash_pn_objects()
    return ()


def _download_messier_objects() -> tuple[SkyAtlasObject, ...]:
    return _load_packaged_messier_objects()


def _download_ngc2000_objects(catalog_name: str) -> tuple[SkyAtlasObject, ...]:
    vizier = Vizier(columns=["Name", "Type", "RAB2000", "DEB2000", "mag", "size", "Desc"], row_limit=-1)
    vizier.ROW_LIMIT = -1
    try:
        result = cast(Any, vizier).get_catalogs(_NGC2000_VIZIER_CATALOG)
    except Exception as exc:  # pragma: no cover - network/library failure path
        raise RuntimeError(f"{catalog_name} catalog download failed: {exc}") from exc
    if not result:
        return ()
    objects: list[SkyAtlasObject] = []
    for table in result:
        for row in table:
            raw_name = str(row["Name"]).strip() if "Name" in row.colnames else ""
            object_name = _ngc2000_display_name(raw_name)
            if catalog_name == "NGC" and not object_name.startswith("NGC "):
                continue
            if catalog_name == "IC" and not object_name.startswith("IC "):
                continue
            ra_deg, dec_deg = _row_ra_dec_degrees(row, ra_keys=("RAB2000", "RAJ2000", "_RAJ2000"), dec_keys=("DEB2000", "DEJ2000", "_DEJ2000"))
            if ra_deg is None or dec_deg is None:
                continue
            type_code = str(row["Type"]).strip() if "Type" in row.colnames else ""
            objects.append(
                SkyAtlasObject(
                    name=object_name,
                    object_type=_ngc2000_type_label(type_code),
                    ra_deg=_normalized_ra_deg(ra_deg),
                    dec_deg=max(-90.0, min(90.0, dec_deg)),
                    magnitude=_optional_float(row["mag"]) if "mag" in row.colnames else None,
                    catalog=catalog_name,
                    aliases=(object_name,),
                    color=_DEEP_SKY_CATALOG_COLORS[catalog_name],
                    description=str(row["Desc"]).strip() if "Desc" in row.colnames else f"{catalog_name} catalog object",
                )
            )
    return tuple(objects)


def _download_vdb_objects() -> tuple[SkyAtlasObject, ...]:
    vizier = Vizier(columns=["**"], row_limit=-1)
    vizier.ROW_LIMIT = -1
    try:
        result = cast(Any, vizier).get_catalogs(_VDB_VIZIER_CATALOG)
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"VdB catalog download failed: {exc}") from exc
    if not result:
        return ()
    objects: list[SkyAtlasObject] = []
    for table in result:
        for row in table:
            vdb_number = _optional_int(row["VdB"]) if "VdB" in row.colnames else None
            ra_deg, dec_deg = _row_ra_dec_degrees(row, ra_keys=("_RA", "RAJ2000", "RAdeg"), dec_keys=("_DE", "DEJ2000", "DEdeg"))
            if vdb_number is None or ra_deg is None or dec_deg is None:
                continue
            object_name = f"VdB {vdb_number}"
            objects.append(
                SkyAtlasObject(
                    name=object_name,
                    object_type="Reflection Nebula",
                    ra_deg=_normalized_ra_deg(ra_deg),
                    dec_deg=max(-90.0, min(90.0, dec_deg)),
                    magnitude=_optional_float(row["Vmag"]) if "Vmag" in row.colnames else None,
                    catalog="VdB",
                    aliases=(object_name,),
                    color=_DEEP_SKY_CATALOG_COLORS["VdB"],
                    description="van den Bergh reflection nebula",
                )
            )
    return tuple(objects)


def _download_ldn_objects() -> tuple[SkyAtlasObject, ...]:
    vizier = Vizier(columns=["**"], row_limit=-1)
    vizier.ROW_LIMIT = -1
    try:
        result = cast(Any, vizier).get_catalogs(_LDN_VIZIER_CATALOG)
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"LDN catalog download failed: {exc}") from exc
    if not result:
        return ()
    objects: list[SkyAtlasObject] = []
    for table in result:
        for row in table:
            ldn_number = _optional_int(row["LDN"]) if "LDN" in row.colnames else None
            ra_deg, dec_deg = _row_ra_dec_degrees(
                row,
                ra_keys=("RAJ2000", "_RAJ2000", "RAdeg", "_RA"),
                dec_keys=("DEJ2000", "_DEJ2000", "DEdeg", "_DE"),
            )
            if ldn_number is None or ra_deg is None or dec_deg is None:
                continue
            object_name = f"LDN {ldn_number}"
            objects.append(
                SkyAtlasObject(
                    name=object_name,
                    object_type="Dark Nebula",
                    ra_deg=_normalized_ra_deg(ra_deg),
                    dec_deg=max(-90.0, min(90.0, dec_deg)),
                    magnitude=None,
                    catalog="LDN",
                    aliases=(object_name,),
                    color=_DEEP_SKY_CATALOG_COLORS["LDN"],
                    description="Lynds Dark Nebula",
                )
            )
    return tuple(objects)


def _download_lbn_objects() -> tuple[SkyAtlasObject, ...]:
    vizier = Vizier(columns=["**"], row_limit=-1)
    vizier.ROW_LIMIT = -1
    try:
        result = cast(Any, vizier).get_catalogs(_LBN_VIZIER_CATALOG)
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"LBN catalog download failed: {exc}") from exc
    if not result:
        return ()
    objects: list[SkyAtlasObject] = []
    for table in result:
        for row in table:
            lbn_number = _optional_int(row["LBN"]) if "LBN" in row.colnames else None
            ra_deg, dec_deg = _row_ra_dec_degrees(
                row,
                ra_keys=("RAJ2000", "_RAJ2000", "RAdeg", "_RA"),
                dec_keys=("DEJ2000", "_DEJ2000", "DEdeg", "_DE"),
            )
            if lbn_number is None or ra_deg is None or dec_deg is None:
                continue
            object_name = f"LBN {lbn_number}"
            objects.append(
                SkyAtlasObject(
                    name=object_name,
                    object_type="Bright Nebula",
                    ra_deg=_normalized_ra_deg(ra_deg),
                    dec_deg=max(-90.0, min(90.0, dec_deg)),
                    magnitude=None,
                    catalog="LBN",
                    aliases=(object_name,),
                    color=_DEEP_SKY_CATALOG_COLORS["LBN"],
                    description="Lynds Bright Nebula",
                )
            )
    return tuple(objects)


def _download_barnard_objects() -> tuple[SkyAtlasObject, ...]:
    vizier = Vizier(columns=["**"], row_limit=-1)
    vizier.ROW_LIMIT = -1
    try:
        result = cast(Any, vizier).get_catalogs(_BARNARD_VIZIER_CATALOG)
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Barnard catalog download failed: {exc}") from exc
    if not result:
        return ()
    objects: list[SkyAtlasObject] = []
    for table in result:
        for row in table:
            parsed = _barnard_object_from_row(row)
            if parsed is not None:
                objects.append(parsed)
    return tuple(objects)


def _barnard_object_from_row(row: Any) -> SkyAtlasObject | None:
    barnard_id = _optional_text(row["Barn"]) if "Barn" in getattr(row, "colnames", ()) else ""
    ra_deg, dec_deg = _row_ra_dec_degrees(
        row,
        ra_keys=("_RA.icrs", "RAJ2000", "_RAJ2000", "RAdeg", "_RA"),
        dec_keys=("_DE.icrs", "DEJ2000", "_DEJ2000", "DEdeg", "_DE"),
    )
    if not barnard_id or ra_deg is None or dec_deg is None:
        return None
    object_name = f"Barnard {barnard_id.lstrip('0') or barnard_id}"
    alias = f"B{barnard_id.lstrip('0') or barnard_id}"
    return SkyAtlasObject(
        name=object_name,
        object_type="Dark Nebula",
        ra_deg=_normalized_ra_deg(ra_deg),
        dec_deg=max(-90.0, min(90.0, dec_deg)),
        magnitude=None,
        catalog="Barnard",
        aliases=(object_name, alias),
        color=_DEEP_SKY_CATALOG_COLORS["Barnard"],
        description="Barnard dark nebula",
    )


def _download_sharpless_objects() -> tuple[SkyAtlasObject, ...]:
    vizier = Vizier(columns=["**"], row_limit=-1)
    vizier.ROW_LIMIT = -1
    try:
        result = cast(Any, vizier).get_catalogs(_SHARPLESS_VIZIER_CATALOG)
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Sharpless catalog download failed: {exc}") from exc
    if not result:
        return ()
    objects: list[SkyAtlasObject] = []
    for table in result:
        for row in table:
            parsed = _sharpless_object_from_row(row)
            if parsed is not None:
                objects.append(parsed)
    return tuple(objects)


def _sharpless_object_from_row(row: Any) -> SkyAtlasObject | None:
    sh2_number = _optional_int(row["Sh2"]) if "Sh2" in getattr(row, "colnames", ()) else None
    ra_deg, dec_deg = _row_ra_dec_degrees(
        row,
        ra_keys=("RAJ2000", "_RAJ2000", "RAdeg", "_RA"),
        dec_keys=("DEJ2000", "_DEJ2000", "DEdeg", "_DE"),
    )
    if ra_deg is None or dec_deg is None:
        galactic = _galactic_to_icrs_degrees(row)
        if galactic is not None:
            ra_deg, dec_deg = galactic
    if sh2_number is None or ra_deg is None or dec_deg is None:
        return None
    object_name = f"Sh2-{sh2_number}"
    return SkyAtlasObject(
        name=object_name,
        object_type="HII Region",
        ra_deg=_normalized_ra_deg(ra_deg),
        dec_deg=max(-90.0, min(90.0, dec_deg)),
        magnitude=None,
        catalog="Sharpless",
        aliases=(object_name, f"Sharpless {sh2_number}"),
        color=_DEEP_SKY_CATALOG_COLORS["Sharpless"],
        description="Sharpless HII region",
    )


def _download_rcw_objects() -> tuple[SkyAtlasObject, ...]:
    vizier = Vizier(columns=["**"], row_limit=-1)
    vizier.ROW_LIMIT = -1
    try:
        result = cast(Any, vizier).get_catalogs(_RCW_VIZIER_CATALOG)
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"RCW catalog download failed: {exc}") from exc
    if not result:
        return ()
    objects: list[SkyAtlasObject] = []
    for table in result:
        for row in table:
            parsed = _rcw_object_from_row(row)
            if parsed is not None:
                objects.append(parsed)
    return tuple(objects)


def _rcw_object_from_row(row: Any) -> SkyAtlasObject | None:
    colnames = set(getattr(row, "colnames", ()))
    rcw_number = None
    for key in ("RCW", "Name"):
        if key not in colnames:
            continue
        text = _optional_text(row[key])
        if not text:
            continue
        match = re.search(r"(\d+[A-Za-z]?)", text)
        if match is not None:
            rcw_number = match.group(1).lstrip("0") or match.group(1)
            break
        number = _optional_int(row[key])
        if number is not None:
            rcw_number = str(number)
            break
    ra_deg, dec_deg = _row_ra_dec_degrees(
        row,
        ra_keys=("RAJ2000", "_RAJ2000", "RAdeg", "_RA", "RAB1950"),
        dec_keys=("DEJ2000", "_DEJ2000", "DEdeg", "_DE", "DEB1950"),
    )
    if rcw_number is None or ra_deg is None or dec_deg is None:
        return None
    object_name = f"RCW {rcw_number}"
    return SkyAtlasObject(
        name=object_name,
        object_type="HII Region",
        ra_deg=_normalized_ra_deg(ra_deg),
        dec_deg=max(-90.0, min(90.0, dec_deg)),
        magnitude=None,
        catalog="RCW",
        aliases=(object_name,),
        color=_DEEP_SKY_CATALOG_COLORS["RCW"],
        description="Rodgers, Campbell & Whiteoak HII region",
    )


def _download_hash_pn_objects() -> tuple[SkyAtlasObject, ...]:
    vizier = Vizier(columns=["**"], row_limit=-1)
    vizier.ROW_LIMIT = -1
    try:
        result = cast(Any, vizier).get_catalogs(_HASH_PN_VIZIER_CATALOG)
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"HASH PN catalog download failed: {exc}") from exc
    if not result:
        return ()
    objects: list[SkyAtlasObject] = []
    for table in result:
        for row in table:
            parsed = _hash_pn_object_from_row(row)
            if parsed is not None:
                objects.append(parsed)
    return tuple(objects)


def _hash_pn_object_from_row(row: Any) -> SkyAtlasObject | None:
    hash_id = _hash_pn_identifier(row)
    ra_deg, dec_deg = _row_ra_dec_degrees(
        row,
        ra_keys=("RAJ2000", "RAdeg", "_RAJ2000", "_RA"),
        dec_keys=("DEJ2000", "DEdeg", "_DEJ2000", "_DE"),
    )
    if hash_id is None or ra_deg is None or dec_deg is None:
        return None
    status_code = _optional_text(row["PNstat"]).upper() if "PNstat" in getattr(row, "colnames", ()) else ""
    if status_code in _HASH_PN_SKIP_STATUSES:
        return None
    usual_name = _hash_pn_usual_name(row)
    png_name = _hash_pn_png_name(row)
    hash_label = f"HASH {hash_id}"
    display_name = usual_name or png_name or hash_label
    aliases = _deduplicated_names(
        (
            display_name,
            usual_name,
            png_name,
            hash_label,
            *_designation_search_aliases(usual_name or display_name),
        )
    )
    object_type = _HASH_PN_STATUS_TYPES.get(status_code, "Planetary Nebula" if not status_code else f"HASH Object ({status_code})")
    status_label = {
        "T": "True PN",
        "L": "Likely PN",
        "P": "Possible PN",
        "C": "New candidate",
        "A": "PAGB / Pre-PN",
        "B": "Possible Pre-PN",
    }.get(status_code, status_code or "HASH PN")
    return SkyAtlasObject(
        name=display_name,
        object_type=object_type,
        ra_deg=_normalized_ra_deg(ra_deg),
        dec_deg=max(-90.0, min(90.0, dec_deg)),
        magnitude=None,
        catalog="HASH PN",
        aliases=aliases,
        color=_DEEP_SKY_CATALOG_COLORS["HASH PN"],
        description=f"HASH PN · {status_label}",
    )


def _hash_pn_usual_name(row: Any) -> str:
    colnames = set(getattr(row, "colnames", ()))
    for key in ("Name", "SimbadID"):
        if key not in colnames:
            continue
        candidate = _optional_text(row[key])
        if candidate and not _is_hash_or_png_label(candidate):
            return candidate
    return ""


def _is_hash_or_png_label(value: str) -> bool:
    compact = re.sub(r"[\s\-_]", "", str(value or "")).casefold()
    if compact.startswith("hash") and compact[4:].isdigit():
        return True
    return compact.startswith("png")


def sky_atlas_object_has_given_name(sky_object: SkyAtlasObject) -> bool:
    """True when the object has a discoverer or usual name, not only HASH/PNG."""
    for candidate in (sky_object.name, *sky_object.aliases):
        if candidate and not _is_hash_or_png_label(candidate):
            return True
    return False


def _designation_search_aliases(name: str) -> tuple[str, ...]:
    stripped = str(name or "").strip()
    if not stripped:
        return ()
    variants = {stripped}
    variants.add(re.sub(r"(?<=[A-Za-z])[\-_]+(?=\d)", " ", stripped))
    variants.add(re.sub(r"(?<=[A-Za-z])\s+(?=\d)", "-", stripped))
    variants.add(re.sub(r"[\s\-_]+", "", stripped))
    return tuple(item for item in variants if item)


def sky_atlas_literature_identifier(sky_object: SkyAtlasObject) -> str:
    named = [
        candidate.strip()
        for candidate in (sky_object.name, *sky_object.aliases)
        if candidate.strip() and not _is_hash_or_png_label(candidate)
    ]
    if named:
        return named[0]
    for candidate in (sky_object.name, *sky_object.aliases):
        if candidate.strip():
            return candidate.strip()
    return sky_object.name.strip() or sky_object.catalog


def sky_atlas_literature_links(sky_object: SkyAtlasObject) -> tuple[SkyAtlasLiteratureLink, ...]:
    identifier = sky_atlas_literature_identifier(sky_object)
    if not identifier:
        return ()
    encoded = quote_plus(identifier)
    ads_query = f'object:"{identifier}"' if sky_atlas_object_has_given_name(sky_object) else identifier
    links = [
        SkyAtlasLiteratureLink(
            "SIMBAD",
            f"https://simbad.cds.unistra.fr/simbad/sim-id?Ident={encoded}",
        ),
        SkyAtlasLiteratureLink(
            "ADS",
            f"https://ui.adsabs.harvard.edu/search/q={quote_plus(ads_query)}&sort=citation_count%20desc",
        ),
        SkyAtlasLiteratureLink(
            "VizieR",
            f"https://vizier.cds.unistra.fr/viz-bin/VizieR-S?{encoded}",
        ),
    ]
    hash_id = _hash_id_from_sky_object(sky_object)
    if hash_id is not None:
        links.append(
            SkyAtlasLiteratureLink(
                "HASH",
                f"https://vizier.cds.unistra.fr/viz-bin/VizieR-4?-source={_HASH_PN_VIZIER_CATALOG}&HASH={quote_plus(hash_id)}",
            )
        )
    return tuple(links)


def _hash_id_from_sky_object(sky_object: SkyAtlasObject) -> str | None:
    for candidate in (sky_object.name, *sky_object.aliases):
        match = _HASH_PN_PATTERN.match(str(candidate).strip())
        if match is not None:
            return match.group(1)
    return None


def _hash_pn_identifier(row: Any) -> str | None:
    colnames = set(getattr(row, "colnames", ()))
    for key in ("HASH", "idPNMain"):
        if key not in colnames:
            continue
        number = _optional_int(row[key])
        if number is not None:
            return str(number)
        text = _optional_text(row[key])
        digits = re.sub(r"[^\d]", "", text)
        if digits:
            return digits
    return None


def _hash_pn_png_name(row: Any) -> str:
    png_value = _optional_text(row["PNG"]) if "PNG" in getattr(row, "colnames", ()) else ""
    if not png_value:
        return ""
    compact = png_value.upper().replace(" ", "")
    if compact.startswith("PNG"):
        compact = compact[3:]
    return f"PN G{compact}" if compact else ""


def _galactic_to_icrs_degrees(row: Any) -> tuple[float, float] | None:
    colnames = set(getattr(row, "colnames", ()))
    lon = _optional_float(row["GLon"]) if "GLon" in colnames else None
    lat = _optional_float(row["GLat"]) if "GLat" in colnames else None
    if lon is None or lat is None:
        return None
    from astropy.coordinates import SkyCoord
    import astropy.units as u

    coordinates = SkyCoord(l=float(lon) * u.deg, b=float(lat) * u.deg, frame="galactic").icrs
    return float(coordinates.ra.deg), float(coordinates.dec.deg)


def _row_ra_dec_degrees(
    row: Any,
    *,
    ra_keys: tuple[str, ...] = ("RAJ2000", "RAB2000", "_RAJ2000", "_RA", "RAdeg", "RAICRS"),
    dec_keys: tuple[str, ...] = ("DEJ2000", "DEB2000", "_DEJ2000", "_DE", "DEdeg", "DEICRS"),
) -> tuple[float | None, float | None]:
    from astropy.coordinates import SkyCoord
    import astropy.units as u

    colnames = set(getattr(row, "colnames", ()))
    for ra_key, dec_key in zip(ra_keys, dec_keys):
        if ra_key not in colnames or dec_key not in colnames:
            continue
        ra_value = row[ra_key]
        dec_value = row[dec_key]
        ra_float = _optional_float(ra_value)
        dec_float = _optional_float(dec_value)
        if ra_float is not None and dec_float is not None and abs(ra_float) <= 360.0 and abs(dec_float) <= 90.0:
            return ra_float, dec_float
        ra_text = str(ra_value).strip()
        dec_text = str(dec_value).strip()
        if not ra_text or not dec_text or ra_text == "--" or dec_text == "--":
            continue
        try:
            coordinates = SkyCoord(ra_text, dec_text, unit=(u.hourangle, u.deg), frame="icrs")
        except Exception:
            try:
                coordinates = SkyCoord(ra_text, dec_text, unit=(u.deg, u.deg), frame="icrs")
            except Exception:
                continue
        return float(coordinates.ra.deg), float(coordinates.dec.deg)
    # Try mismatched key combinations for catalogs that only expose one naming scheme.
    for ra_key in ra_keys:
        if ra_key not in colnames:
            continue
        for dec_key in dec_keys:
            if dec_key not in colnames:
                continue
            ra_float = _optional_float(row[ra_key])
            dec_float = _optional_float(row[dec_key])
            if ra_float is not None and dec_float is not None and abs(ra_float) <= 360.0 and abs(dec_float) <= 90.0:
                return ra_float, dec_float
    return None, None


def _ngc2000_display_name(raw_name: str) -> str:
    normalized = re.sub(r"\s+", "", str(raw_name or "").strip().upper())
    if normalized.startswith("IC") and normalized[2:].isalnum():
        return f"IC {normalized[2:].lstrip('0') or '0'}"
    if normalized.startswith("I") and normalized[1:].isalnum():
        return f"IC {normalized[1:].lstrip('0') or '0'}"
    if normalized.startswith("NGC") and normalized[3:].isalnum():
        return f"NGC {normalized[3:].lstrip('0') or '0'}"
    if normalized.startswith("N") and normalized[1:].isalnum():
        return f"NGC {normalized[1:].lstrip('0') or '0'}"
    if normalized.isdigit():
        return f"NGC {normalized.lstrip('0') or '0'}"
    return str(raw_name or "").strip() or "NGC/IC object"


def _ngc2000_type_label(type_code: str) -> str:
    normalized = str(type_code or "").strip().upper()
    return {
        "OC": "Open Cluster",
        "GC": "Globular Cluster",
        "PL": "Planetary Nebula",
        "GX": "Galaxy",
        "NB": "Nebula",
        "CL+N": "Cluster with Nebulosity",
        "AST": "Asterism",
    }.get(normalized, normalized or "Deep Sky Object")


def _messier_object_type(row: Any) -> str:
    colnames = set(getattr(row, "colnames", ()))
    for key in ("Type", "class", "Class"):
        if key not in colnames:
            continue
        value = str(row[key]).strip()
        if value and value != "--":
            return value
    return "Deep Sky Object"


def _angular_separation_deg(ra_a_deg: float, dec_a_deg: float, ra_b_deg: float, dec_b_deg: float) -> float:
    ra_a_rad = math.radians(ra_a_deg)
    ra_b_rad = math.radians(ra_b_deg)
    dec_a_rad = math.radians(dec_a_deg)
    dec_b_rad = math.radians(dec_b_deg)
    cosine = (
        math.sin(dec_a_rad) * math.sin(dec_b_rad)
        + math.cos(dec_a_rad) * math.cos(dec_b_rad) * math.cos(ra_a_rad - ra_b_rad)
    )
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


@lru_cache(maxsize=1)
def _load_packaged_star_name_aliases() -> dict[int, tuple[str, ...]]:
    data_path = resources.files("photometry_app").joinpath("data/sky_atlas_star_names.json")
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    aliases_by_hip: dict[int, tuple[str, ...]] = {}
    for entry in payload.get("stars", []):
        hip_value = _optional_int(entry.get("hip"))
        if hip_value is None:
            continue
        names = tuple(
            _deduplicated_names(
                str(name).strip()
                for name in entry.get("names", [])
                if str(name).strip()
            )
        )
        if names:
            aliases_by_hip[int(hip_value)] = names
    return aliases_by_hip


def _resolve_sky_atlas_name_and_aliases(
    raw_name: str,
    aliases: tuple[str, ...],
    *,
    object_type: str,
    preferred_name_candidates: tuple[str, ...] = (),
) -> tuple[str, tuple[str, ...]]:
    candidate_aliases: list[str] = []

    if raw_name:
        candidate_aliases.append(raw_name)

    actual_name_candidates: list[str] = []

    leading_designation, display_remainder = _split_leading_catalog_designation(raw_name)
    if leading_designation is not None:
        candidate_aliases.append(leading_designation)
        if display_remainder:
            actual_name_candidates.append(display_remainder)

    for name in preferred_name_candidates:
        normalized = str(name).strip()
        if not normalized:
            continue
        candidate_aliases.append(normalized)
        if _is_actual_name_candidate(normalized):
            actual_name_candidates.append(normalized)

    for alias in aliases:
        normalized = str(alias).strip()
        if normalized:
            candidate_aliases.append(normalized)

    if raw_name and not leading_designation and _is_actual_name_candidate(raw_name):
        actual_name_candidates.append(raw_name)

    deduplicated_actual_names = _deduplicated_names(actual_name_candidates)
    if deduplicated_actual_names:
        display_name = min(deduplicated_actual_names, key=_actual_name_sort_key)
    else:
        identifier_candidates = _deduplicated_names(_canonical_identifier(candidate) or candidate.strip() for candidate in candidate_aliases if candidate.strip())
        if identifier_candidates:
            display_name = min(identifier_candidates, key=_identifier_sort_key)
        else:
            display_name = raw_name.strip()

    resolved_aliases = tuple(alias for alias in _deduplicated_names(candidate_aliases) if alias.casefold() != display_name.casefold())
    return display_name, resolved_aliases


def _split_leading_catalog_designation(value: str) -> tuple[str | None, str]:
    normalized = str(value or "").strip()
    for pattern, formatter, _priority in (
        (_MESSIER_PATTERN, lambda text: f"M{text}", 0),
        (_NGC_PATTERN, lambda text: f"NGC {text.upper()}", 1),
        (_IC_PATTERN, lambda text: f"IC {text.upper()}", 2),
        (_VDB_PATTERN, lambda text: f"VdB {text.upper()}", 3),
        (_BARNARD_PATTERN, lambda text: f"Barnard {text.upper()}", 4),
        (_SH2_PATTERN, lambda text: f"Sh2-{text.upper()}", 5),
        (_RCW_PATTERN, lambda text: f"RCW {text.upper()}", 6),
        (_HASH_PN_PATTERN, lambda text: f"HASH {text}", 7),
    ):
        match = pattern.match(normalized)
        if match is None:
            continue
        designation = formatter(match.group(1).strip())
        remainder = normalized[match.end() :].strip(" -:\u2013\u2014")
        return designation, remainder
    return None, normalized


def _canonical_identifier(value: str) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    leading_designation, remainder = _split_leading_catalog_designation(normalized)
    if leading_designation is not None and not remainder:
        return leading_designation
    for pattern, formatter, _priority in _OTHER_IDENTIFIER_PATTERNS:
        match = pattern.match(normalized)
        if match is not None:
            cleaned_value = re.sub(r"\s+", " ", match.group(1).strip()).strip()
            return formatter.format(value=cleaned_value.upper() if formatter.startswith(("HR", "HD", "HIP", "SAO")) else cleaned_value)
    return None


def _is_actual_name_candidate(value: str) -> bool:
    normalized = str(value or "").strip()
    if not normalized:
        return False
    if _canonical_identifier(normalized) is not None:
        return False
    uppercase_value = normalized.upper()
    if uppercase_value.startswith(_TECHNICAL_NAME_PREFIXES):
        return False
    digit_count = sum(character.isdigit() for character in normalized)
    if digit_count >= 3:
        return False
    if any(symbol in normalized for symbol in ("+",)) and digit_count >= 1:
        return False
    return True


def _actual_name_sort_key(value: str) -> tuple[int, int, str]:
    normalized = value.strip()
    component_penalty = 1 if re.search(r"\b[A-D]$", normalized) else 0
    generic_penalty = 1 if any(token in normalized.casefold() for token in (" north ", " south ", " major", " minor")) else 0
    return (component_penalty + generic_penalty, len(normalized), normalized.casefold())


def _identifier_sort_key(value: str) -> tuple[int, int, str]:
    normalized = value.strip()
    if _MESSIER_PATTERN.match(normalized):
        return (0, len(normalized), normalized.casefold())
    if _NGC_PATTERN.match(normalized):
        return (1, len(normalized), normalized.casefold())
    if _IC_PATTERN.match(normalized):
        return (2, len(normalized), normalized.casefold())
    if _VDB_PATTERN.match(normalized):
        return (3, len(normalized), normalized.casefold())
    if _BARNARD_PATTERN.match(normalized):
        return (4, len(normalized), normalized.casefold())
    if _SH2_PATTERN.match(normalized):
        return (5, len(normalized), normalized.casefold())
    if _RCW_PATTERN.match(normalized):
        return (6, len(normalized), normalized.casefold())
    if _HASH_PN_PATTERN.match(normalized):
        return (7, len(normalized), normalized.casefold())
    for pattern, _formatter, priority in _OTHER_IDENTIFIER_PATTERNS:
        if pattern.match(normalized):
            return (priority, len(normalized), normalized.casefold())
    return (99, len(normalized), normalized.casefold())


def _deduplicated_names(values: Any) -> tuple[str, ...]:
    deduplicated: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value).strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(normalized)
    return tuple(deduplicated)


def _extract_hip_identifier(name: str, aliases: tuple[str, ...]) -> int | None:
    for value in (name, *aliases):
        match = _HIPPARCOS_IDENTIFIER_PATTERN.match(str(value).strip())
        if match is None:
            continue
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def _normalized_signed_catalog_identifier(value: str) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "").strip())
    if not normalized:
        return ""
    normalized = re.sub(r"^(?:B|C)\s*", "", normalized, flags=re.IGNORECASE)
    return normalized.strip()


def _color_from_bv_index(bv_index: float | None) -> str:
    if bv_index is None:
        return "#f8fbff"
    anchors = (
        (-0.15, (176, 205, 255)),
        (0.0, (213, 231, 255)),
        (0.32, (247, 249, 255)),
        (0.62, (255, 236, 198)),
        (0.95, (255, 202, 141)),
        (1.45, (255, 169, 103)),
    )
    clamped_bv = max(float(anchors[0][0]), min(float(anchors[-1][0]), float(bv_index)))
    for (lower_bv, lower_color), (upper_bv, upper_color) in zip(anchors, anchors[1:]):
        if clamped_bv > upper_bv:
            continue
        if abs(upper_bv - lower_bv) <= 1.0e-9:
            interpolation = 0.0
        else:
            interpolation = (clamped_bv - lower_bv) / (upper_bv - lower_bv)
        red = int(round(lower_color[0] + (upper_color[0] - lower_color[0]) * interpolation))
        green = int(round(lower_color[1] + (upper_color[1] - lower_color[1]) * interpolation))
        blue = int(round(lower_color[2] + (upper_color[2] - lower_color[2]) * interpolation))
        return f"#{red:02x}{green:02x}{blue:02x}"
    red, green, blue = anchors[-1][1]
    return f"#{red:02x}{green:02x}{blue:02x}"


def _optional_text(value: object) -> str:
    if value is None:
        return ""
    mask = getattr(value, "mask", None)
    if mask is True:
        return ""
    if hasattr(mask, "all"):
        try:
            if bool(mask.all()):
                return ""
        except Exception:
            pass
    text = str(value).strip()
    if not text or text in {"--", "nan", "None"}:
        return ""
    return text


def _optional_int(value: object) -> int | None:
    number = _optional_float(value)
    if number is None:
        return None
    try:
        return int(round(number))
    except (TypeError, ValueError):
        return None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    mask = getattr(value, "mask", None)
    if mask is True:
        return None
    if hasattr(mask, "all"):
        try:
            if bool(mask.all()):
                return None
        except Exception:
            pass
    if str(value).strip() == "--":
        return None
    try:
        number = float(cast(Any, value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _magnitude_sort_key(value: float | None) -> float:
    return float(value) if value is not None and math.isfinite(float(value)) else 99.0


def _normalized_ra_deg(value: float) -> float:
    return float(value) % 360.0
