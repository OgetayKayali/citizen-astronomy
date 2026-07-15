from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, cast

from astroquery.vizier import Vizier


from photometry_app.core.sky_atlas_catalog_storage import sky_atlas_catalog_root

_HIPPARCOS_VIZIER_CATALOG = "I/239/hip_main"
_TYCHO2_VIZIER_CATALOG = "I/259/tyc2"
_GAIA_DR3_VIZIER_CATALOG = "I/355/gaiadr3"
_NGC2000_VIZIER_CATALOG = "VII/118/ngc2000"
_VDB_VIZIER_CATALOG = "VII/21/catalog"
_LDN_VIZIER_CATALOG = "VII/7A/ldn"
_LBN_VIZIER_CATALOG = "VII/9"
_DEFAULT_SCIENTIFIC_STAR_MAGNITUDE_LIMIT = 9.5
_HIPPARCOS_PRACTICAL_MAGNITUDE_LIMIT = 12.0
_TYCHO_PRACTICAL_MAGNITUDE_LIMIT = 12.5
_MAX_SKY_ATLAS_STAR_MAGNITUDE_LIMIT = 15.0
_SCIENTIFIC_CATALOG_CACHE_SCHEMA_VERSION = 1
_DEEP_SKY_CATALOG_CACHE_SCHEMA_VERSION = 1
_NAMED_OBJECT_DEDUPLICATION_RADIUS_DEG = 0.12
_ALWAYS_VISIBLE_SKY_ATLAS_CATALOGS = frozenset({"Hipparcos", "Tycho-2", "Gaia DR3", "Bright Star", "Local", "Solar System"})
SKY_ATLAS_DEEP_SKY_CATALOG_NAMES = ("Messier", "NGC", "IC", "VdB", "LDN", "LBN")
_DEEP_SKY_CATALOG_COLORS = {
    "Messier": "#a7f3d0",
    "NGC": "#93c5fd",
    "IC": "#bfdbfe",
    "VdB": "#fde68a",
    "LDN": "#c4b5fd",
    "LBN": "#67e8f9",
}
_HIPPARCOS_IDENTIFIER_PATTERN = re.compile(r"^HIP\s*(\d+)\b", flags=re.IGNORECASE)
_MESSIER_PATTERN = re.compile(r"^(?:MESSIER|M)\s*0*(\d+)\b", flags=re.IGNORECASE)
_NGC_PATTERN = re.compile(r"^NGC\s*0*(\d+[A-Za-z]?)\b", flags=re.IGNORECASE)
_IC_PATTERN = re.compile(r"^IC\s*0*(\d+[A-Za-z]?)\b", flags=re.IGNORECASE)
_VDB_PATTERN = re.compile(r"^(?:VDB|V\s*DB)\s*0*(\d+[A-Za-z]?)\b", flags=re.IGNORECASE)
_BARNARD_PATTERN = re.compile(r"^(?:BARNARD|B)\s*0*(\d+[A-Za-z]?)\b", flags=re.IGNORECASE)
_SH2_PATTERN = re.compile(r"^(?:SH\s*2|SH2|SHARPLESS)\s*-?\s*0*(\d+[A-Za-z]?)\b", flags=re.IGNORECASE)
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


def load_local_sky_atlas_objects() -> tuple[SkyAtlasObject, ...]:
    return _load_packaged_named_sky_atlas_objects()


def load_sky_atlas_objects(
    cache_dir: Path | None = None,
    *,
    maximum_scientific_magnitude: float = _DEFAULT_SCIENTIFIC_STAR_MAGNITUDE_LIMIT,
    download_if_missing: bool = False,
    enabled_deep_sky_catalogs: frozenset[str] | set[str] | tuple[str, ...] | None = None,
    progress_callback=None,
) -> tuple[SkyAtlasObject, ...]:
    packaged_objects = list(_load_packaged_named_sky_atlas_objects())
    catalogs = normalize_sky_atlas_deep_sky_catalogs(enabled_deep_sky_catalogs)
    if "Messier" in catalogs:
        packaged_objects = _merge_packaged_targets_with_deep_sky_objects(
            packaged_objects,
            _load_packaged_messier_objects(),
        )
    if cache_dir is None:
        return filter_sky_atlas_objects_by_deep_sky_catalogs(
            tuple(sorted(packaged_objects, key=lambda item: (_magnitude_sort_key(item.magnitude), item.name.casefold()))),
            catalogs,
        )
    try:
        scientific_stars = load_scientific_sky_atlas_star_objects(
            cache_dir,
            maximum_magnitude=maximum_scientific_magnitude,
            download_if_missing=download_if_missing,
            progress_callback=progress_callback,
        )
    except Exception:
        scientific_stars = ()
    if scientific_stars:
        packaged_objects = _merge_packaged_targets_with_scientific_stars(packaged_objects, scientific_stars)
    try:
        deep_sky_objects = load_sky_atlas_deep_sky_objects(
            cache_dir,
            enabled_catalogs=catalogs,
            download_if_missing=download_if_missing,
        )
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
) -> tuple[SkyAtlasObject, ...]:
    catalogs = normalize_sky_atlas_deep_sky_catalogs(enabled_catalogs)
    if not catalogs:
        return ()
    loaded: list[SkyAtlasObject] = []
    for catalog_name in SKY_ATLAS_DEEP_SKY_CATALOG_NAMES:
        if catalog_name not in catalogs:
            continue
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
) -> tuple[SkyAtlasObject, ...]:
    catalog_root = sky_atlas_catalog_root(cache_dir)
    requested_limit = max(1.0, min(_MAX_SKY_ATLAS_STAR_MAGNITUDE_LIMIT, float(maximum_magnitude)))
    merged: list[SkyAtlasObject] = []

    hip_limit = min(requested_limit, _HIPPARCOS_PRACTICAL_MAGNITUDE_LIMIT)
    if progress_callback is not None:
        progress_callback(f"Loading Hipparcos stars to mag {hip_limit:.1f}...", None)
    try:
        hipparcos = _load_or_download_named_star_catalog(
            catalog_root,
            catalog_key="hipparcos",
            catalog_label="Hipparcos",
            maximum_magnitude=hip_limit,
            download_if_missing=download_if_missing,
            downloader=_download_hipparcos_star_objects,
        )
        merged.extend(hipparcos)
    except Exception:
        if progress_callback is not None:
            progress_callback("Hipparcos load failed; continuing with other catalogs...", None)

    if requested_limit > 9.5 + 1.0e-6:
        tycho_limit = min(requested_limit, _TYCHO_PRACTICAL_MAGNITUDE_LIMIT)
        if progress_callback is not None:
            progress_callback(f"Loading Tycho-2 stars to mag {tycho_limit:.1f}...", None)
        try:
            tycho = _load_or_download_named_star_catalog(
                catalog_root,
                catalog_key="tycho2",
                catalog_label="Tycho-2",
                maximum_magnitude=tycho_limit,
                download_if_missing=download_if_missing,
                downloader=_download_tycho2_star_objects,
                minimum_magnitude=9.4,
            )
            merged = _merge_star_catalogs(merged, tycho)
        except Exception:
            if progress_callback is not None:
                progress_callback("Tycho-2 load failed; continuing...", None)

    if requested_limit > _TYCHO_PRACTICAL_MAGNITUDE_LIMIT + 1.0e-6:
        if progress_callback is not None:
            progress_callback(f"Loading Gaia DR3 stars to mag {requested_limit:.1f}...", None)
        try:
            gaia = _load_or_download_gaia_star_objects(
                catalog_root,
                maximum_magnitude=requested_limit,
                download_if_missing=download_if_missing,
                progress_callback=progress_callback,
                minimum_magnitude=_TYCHO_PRACTICAL_MAGNITUDE_LIMIT,
            )
            merged = _merge_star_catalogs(merged, gaia)
        except Exception:
            if progress_callback is not None:
                progress_callback("Gaia DR3 load failed; continuing...", None)

    return tuple(sorted(merged, key=lambda item: (_magnitude_sort_key(item.magnitude), item.name.casefold())))


def _load_or_download_named_star_catalog(
    catalog_root: Path,
    *,
    catalog_key: str,
    catalog_label: str,
    maximum_magnitude: float,
    download_if_missing: bool,
    downloader,
    minimum_magnitude: float | None = None,
) -> tuple[SkyAtlasObject, ...]:
    cache_path = _star_catalog_cache_path(catalog_root, catalog_key, maximum_magnitude)
    cached = _load_cached_scientific_star_objects(cache_path, maximum_magnitude)
    if cached is not None:
        if minimum_magnitude is None:
            return cached
        return tuple(item for item in cached if item.magnitude is None or item.magnitude > float(minimum_magnitude))
    fallback = _best_available_named_star_catalog_cache(catalog_root, catalog_key, maximum_magnitude)
    if fallback is not None:
        fallback_path, fallback_limit = fallback
        fallback_objects = _load_cached_scientific_star_objects(fallback_path, fallback_limit)
        if fallback_objects is not None:
            filtered = fallback_objects
            if minimum_magnitude is not None:
                filtered = tuple(
                    item
                    for item in fallback_objects
                    if item.magnitude is None or item.magnitude > float(minimum_magnitude)
                )
            return filtered
    if not download_if_missing:
        return ()
    downloaded = tuple(
        sorted(
            downloader(maximum_magnitude),
            key=lambda item: (_magnitude_sort_key(item.magnitude), item.name.casefold()),
        )
    )
    _store_cached_scientific_star_objects(
        cache_path,
        downloaded,
        maximum_magnitude,
        catalog_label=catalog_label,
    )
    if minimum_magnitude is None:
        return downloaded
    return tuple(item for item in downloaded if item.magnitude is None or item.magnitude > float(minimum_magnitude))


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


def search_sky_atlas_objects(
    objects: tuple[SkyAtlasObject, ...],
    query: str,
    *,
    limit: int = 80,
    magnitude_limit: float | None = None,
    deep_sky_magnitude_limit: float | None = None,
) -> tuple[SkyAtlasObject, ...]:
    normalized_query = str(query or "").strip().casefold()
    star_limit = None if magnitude_limit is None else float(magnitude_limit)
    object_limit = (
        float(deep_sky_magnitude_limit)
        if deep_sky_magnitude_limit is not None
        else (None if magnitude_limit is None else max(16.0, float(magnitude_limit)))
    )

    def _passes_magnitude(item: SkyAtlasObject) -> bool:
        if item.magnitude is None:
            return True
        if item.object_type.casefold() == "star":
            return star_limit is None or item.magnitude <= star_limit
        return object_limit is None or item.magnitude <= object_limit

    candidates = tuple(item for item in objects if item.searchable and _passes_magnitude(item))
    if not normalized_query:
        return tuple(sorted(candidates, key=lambda item: (_magnitude_sort_key(item.magnitude), item.name.casefold()))[: max(1, int(limit))])

    ranked: list[tuple[int, float, str, SkyAtlasObject]] = []
    for item in candidates:
        text = item.search_text
        name = item.name.casefold()
        aliases = tuple(alias.casefold() for alias in item.aliases)
        compact_query = normalized_query.replace(" ", "")
        compact_name = name.replace(" ", "")
        compact_aliases = tuple(alias.replace(" ", "") for alias in aliases)
        if (
            normalized_query == name
            or normalized_query in aliases
            or compact_query == compact_name
            or compact_query in compact_aliases
        ):
            rank = 0
        elif (
            name.startswith(normalized_query)
            or any(alias.startswith(normalized_query) for alias in aliases)
            or compact_name.startswith(compact_query)
            or any(alias.startswith(compact_query) for alias in compact_aliases)
        ):
            rank = 1
        elif normalized_query in text or compact_query in text.replace(" ", ""):
            rank = 2
        else:
            continue
        ranked.append((rank, _magnitude_sort_key(item.magnitude), item.name.casefold(), item))
    return tuple(item for *_sort, item in sorted(ranked)[: max(1, int(limit))])


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
    objects: list[SkyAtlasObject] = []
    for entry in payload.get("objects", []):
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


def _load_or_download_gaia_star_objects(
    catalog_root: Path,
    *,
    maximum_magnitude: float,
    download_if_missing: bool,
    progress_callback=None,
    minimum_magnitude: float = _TYCHO_PRACTICAL_MAGNITUDE_LIMIT,
) -> tuple[SkyAtlasObject, ...]:
    cache_path = _star_catalog_cache_path(catalog_root, "gaia_dr3", maximum_magnitude)
    cached = _load_cached_scientific_star_objects(cache_path, maximum_magnitude)
    if cached is not None:
        return tuple(
            item for item in cached if item.magnitude is None or item.magnitude > float(minimum_magnitude)
        )
    if not download_if_missing:
        return ()
    downloaded = _download_gaia_star_objects_by_ra_strips(
        maximum_magnitude,
        minimum_magnitude=minimum_magnitude,
        progress_callback=progress_callback,
    )
    _store_cached_scientific_star_objects(cache_path, downloaded, maximum_magnitude, catalog_label="Gaia DR3")
    return downloaded


def _download_gaia_star_objects_by_ra_strips(
    maximum_magnitude: float,
    *,
    minimum_magnitude: float,
    progress_callback=None,
    strip_width_deg: float = 15.0,
) -> tuple[SkyAtlasObject, ...]:
    vizier = Vizier(columns=["Source", "RAJ2000", "DEJ2000", "Gmag", "BP-RP"], row_limit=-1)
    vizier.ROW_LIMIT = -1
    objects: list[SkyAtlasObject] = []
    strips = max(1, int(math.ceil(360.0 / strip_width_deg)))
    for strip_index in range(strips):
        ra_min = strip_index * strip_width_deg
        ra_max = min(360.0, ra_min + strip_width_deg)
        if progress_callback is not None:
            progress_callback(
                f"Downloading Gaia DR3 strip {strip_index + 1}/{strips} (RA {ra_min:.0f}–{ra_max:.0f}°)...",
                (strip_index / strips) * 100.0,
            )
        try:
            result = cast(Any, vizier).query_constraints(
                catalog=_GAIA_DR3_VIZIER_CATALOG,
                Gmag=f"{float(minimum_magnitude):.1f}..{float(maximum_magnitude):.1f}",
                RAJ2000=f"{ra_min:.3f}..{ra_max:.3f}",
            )
        except Exception:
            continue
        if not result:
            continue
        table = result[0]
        colnames = set(getattr(table, "colnames", ()))
        for row in table:
            ra_deg = _optional_float(row["RAJ2000"]) if "RAJ2000" in colnames else None
            dec_deg = _optional_float(row["DEJ2000"]) if "DEJ2000" in colnames else None
            magnitude = _optional_float(row["Gmag"]) if "Gmag" in colnames else None
            if ra_deg is None or dec_deg is None or magnitude is None:
                continue
            if magnitude <= float(minimum_magnitude) or magnitude > float(maximum_magnitude):
                continue
            source_id = _optional_int(row["Source"]) if "Source" in colnames else None
            name = f"Gaia DR3 {source_id}" if source_id is not None else f"Gaia {len(objects) + 1}"
            bp_rp = _optional_float(row["BP-RP"]) if "BP-RP" in colnames else None
            objects.append(
                SkyAtlasObject(
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
            )
    if progress_callback is not None:
        progress_callback("Gaia DR3 download complete.", 100.0)
    return tuple(objects)


def _download_hipparcos_star_objects(maximum_magnitude: float) -> tuple[SkyAtlasObject, ...]:
    vizier = Vizier(
        columns=["HIP", "RAICRS", "DEICRS", "Vmag", "B-V", "HD", "BD", "CoD", "CPD"],
        row_limit=-1,
    )
    vizier.ROW_LIMIT = -1
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
    return scientific_dir / f"{catalog_name.casefold()}.json"


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
