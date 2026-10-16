"""Offline deep-sky catalog lookups for the assistant: packaged Messier, globular clusters, and cached NGC."""

from __future__ import annotations

from dataclasses import dataclass
import json
from importlib import resources
from pathlib import Path
import re
import threading

import numpy as np

from photometry_app.core.sky_atlas import load_sky_atlas_search_objects


@dataclass(frozen=True, slots=True)
class SkyObject:
    name: str
    object_type: str
    ra_deg: float
    dec_deg: float
    magnitude: float | None = None
    constellation: str = ""
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"name": self.name, "type": self.object_type, "ra_deg": round(self.ra_deg, 4), "dec_deg": round(self.dec_deg, 4)}
        if self.magnitude is not None:
            result["magnitude"] = self.magnitude
        if self.constellation:
            result["constellation"] = self.constellation
        other_names = [alias for alias in self.aliases if alias != self.name][:4]
        if other_names:
            result["also_known_as"] = other_names
        return result


def _key(name: str) -> str:
    return re.sub(r"[\s_-]+", "", name.casefold()).replace("messier", "m")


class SkyObjectCatalog:
    def __init__(self, objects: tuple[SkyObject, ...]) -> None:
        self._objects = objects
        self._by_key: dict[str, SkyObject] = {}
        for item in objects:
            for alias in (item.name, *item.aliases):
                self._by_key.setdefault(_key(alias), item)
        ra = np.radians([item.ra_deg for item in objects]) if objects else np.zeros(0)
        dec = np.radians([item.dec_deg for item in objects]) if objects else np.zeros(0)
        self._vectors = np.column_stack((np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec))) if objects else np.zeros((0, 3))

    @property
    def objects(self) -> tuple[SkyObject, ...]:
        return self._objects

    def look_up(self, name: str) -> SkyObject | None:
        return self._by_key.get(_key(name))

    def search(self, text: str, *, limit: int = 5) -> list[SkyObject]:
        key = _key(text)
        if not key:
            return []
        return [item for item in self._objects if any(key in _key(alias) for alias in (item.name, *item.aliases))][:limit]

    def near(self, ra_deg: float, dec_deg: float, radius_deg: float, *, object_type: str = "") -> list[tuple[SkyObject, float]]:
        if not self._objects:
            return []
        ra, dec = np.radians(ra_deg), np.radians(dec_deg)
        center = np.array((np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)))
        separations = np.degrees(np.arccos(np.clip(self._vectors @ center, -1.0, 1.0)))
        wanted = object_type.strip().casefold()
        hits = [
            (self._objects[index], float(separations[index]))
            for index in np.flatnonzero(separations <= radius_deg)
            if not wanted or wanted in self._objects[index].object_type.casefold()
        ]
        hits.sort(key=lambda item: item[1])
        return hits


_catalog_lock = threading.Lock()
_catalog_cache: dict[str, SkyObjectCatalog] = {}


def sky_object_catalog(cache_dir: Path | None = None) -> SkyObjectCatalog:
    cache_key = str(cache_dir or "")
    with _catalog_lock:
        catalog = _catalog_cache.get(cache_key)
        if catalog is None:
            catalog = SkyObjectCatalog(_load_objects(cache_dir))
            _catalog_cache[cache_key] = catalog
        return catalog


def _load_objects(cache_dir: Path | None) -> tuple[SkyObject, ...]:
    objects: list[SkyObject] = []
    seen: set[str] = set()
    for item in load_sky_atlas_search_objects(cache_dir):
        if item.object_type.casefold() == "star":
            continue
        entry = SkyObject(
            name=item.name,
            object_type=item.object_type,
            ra_deg=float(item.ra_deg),
            dec_deg=float(item.dec_deg),
            magnitude=item.magnitude,
            constellation=item.constellation,
            aliases=tuple(item.aliases),
        )
        objects.append(entry)
        seen.update(_key(alias) for alias in (entry.name, *entry.aliases))
    for entry in _packaged_globular_clusters():
        if not any(_key(alias) in seen for alias in (entry.name, *entry.aliases)):
            objects.append(entry)
            seen.update(_key(alias) for alias in (entry.name, *entry.aliases))
    return tuple(objects)


def _packaged_globular_clusters() -> list[SkyObject]:
    try:
        payload = json.loads(resources.files("photometry_app").joinpath("data/globular_clusters.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries: list[SkyObject] = []
    for raw in payload if isinstance(payload, list) else ():
        try:
            name = str(raw["name"]).strip()
            entries.append(
                SkyObject(
                    name=name,
                    object_type="Globular Cluster",
                    ra_deg=float(raw["ra_deg"]),
                    dec_deg=float(raw["dec_deg"]),
                    aliases=tuple(str(alias).strip() for alias in raw.get("names", ()) if str(alias).strip()),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return entries
