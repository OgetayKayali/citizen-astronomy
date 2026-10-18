"""Real catalog stars for the Black Hole and Galaxy Merger star backgrounds.

Stars come from the Hipparcos and Tycho-2 files Sky Atlas caches under
``<install>/sky-atlas``. For the black hole they are bucketed on the equal-angle
cube map its shader uses (``cubeFace`` in ``native/blackhole/.../shaders.hpp``):
each cell lists every star within ``MARGIN`` radians of it, brightest first, so
the shader reads one cell per pixel. The galaxy merger draws each star once as a
point (``load_catalog_stars``).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from photometry_app.app_metadata import application_install_path

GRID = 96
MARGIN = 0.008  # radians; starMargin in the shader must match
MAXIMUM_MAGNITUDE = 9.5
TYCHO_MINIMUM_MAGNITUDE = 9.4  # Hipparcos covers brighter stars
# Outer ring at 1.08*MARGIN with 16 points: the sagitta between points stays outside MARGIN,
# and no cell (at least ~1.4*MARGIN across at GRID 96) fits between samples.
_RING_OFFSETS = tuple(
    (radius * math.cos(angle), radius * math.sin(angle))
    for radius in (MARGIN * 0.36, MARGIN * 0.72, MARGIN * 1.08)
    for angle in np.linspace(0.0, math.tau, 16, endpoint=False)
)


@dataclass(frozen=True)
class StarCatalogTextures:
    cells: np.ndarray  # float32 (GRID, 6*GRID, 2): first entry, entry count
    stars: np.ndarray  # float32 (entries, 4): equatorial unit vector, V magnitude
    colors: np.ndarray  # uint8 (entries, 4): sRGB color from B-V, alpha 255
    star_count: int
    magnitude_limit: float
    note: str


def equatorial_vectors(ra_deg: np.ndarray, dec_deg: np.ndarray) -> np.ndarray:
    ra = np.radians(np.asarray(ra_deg, dtype=np.float64))
    dec = np.radians(np.asarray(dec_deg, dtype=np.float64))
    return np.stack((np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)), axis=-1)


def cube_cells(vectors: np.ndarray, grid: int = GRID) -> np.ndarray:
    """Flat cell index face*grid*grid + j*grid + i, matching the shader's cubeFace."""

    v = np.asarray(vectors, dtype=np.float64)
    x, y, z = v[..., 0], v[..., 1], v[..., 2]
    ax, ay, az = np.abs(x), np.abs(y), np.abs(z)
    on_x = (ax >= ay) & (ax >= az)
    on_y = ~on_x & (ay >= az)
    on_z = ~on_x & ~on_y
    face = np.where(on_x, np.where(x > 0, 0, 1), np.where(on_y, np.where(y > 0, 2, 3), np.where(z > 0, 4, 5)))
    major = np.where(on_x, ax, np.where(on_y, ay, az))
    u = np.where(on_x, y, x) / major
    w = np.where(on_z, y, z) / major
    i = np.clip(np.floor((np.arctan(u) * (4.0 / math.pi) * 0.5 + 0.5) * grid), 0, grid - 1).astype(np.int64)
    j = np.clip(np.floor((np.arctan(w) * (4.0 / math.pi) * 0.5 + 0.5) * grid), 0, grid - 1).astype(np.int64)
    return face * grid * grid + j * grid + i


def build_star_textures(
    ra_deg: np.ndarray,
    dec_deg: np.ndarray,
    magnitude: np.ndarray,
    rgb: np.ndarray,
    *,
    grid: int = GRID,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    vectors = equatorial_vectors(ra_deg, dec_deg)
    count = len(vectors)
    # Tangent basis; the pole fallback avoids a degenerate east vector.
    reference = np.where(np.abs(vectors[:, 2:3]) < 0.9, [[0.0, 0.0, 1.0]], [[1.0, 0.0, 0.0]])
    east = np.cross(reference, vectors)
    east /= np.linalg.norm(east, axis=1, keepdims=True)
    north = np.cross(vectors, east)
    samples = [vectors] + [vectors + a * east + b * north for a, b in _RING_OFFSETS]
    cells = np.stack([cube_cells(sample, grid) for sample in samples], axis=1)
    total = 6 * grid * grid
    pairs = np.unique(np.arange(count, dtype=np.int64)[:, None] * total + cells)
    star_index, cell_index = np.divmod(pairs, total)
    order = np.lexsort((np.asarray(magnitude, dtype=np.float64)[star_index], cell_index))
    star_index, cell_index = star_index[order], cell_index[order]
    counts = np.bincount(cell_index, minlength=total)
    first = np.concatenate(([0], np.cumsum(counts)[:-1]))
    face, rest = np.divmod(np.arange(total), grid * grid)
    j, i = np.divmod(rest, grid)
    table = np.zeros((grid, 6 * grid, 2), dtype=np.float32)
    table[j, face * grid + i, 0] = first
    table[j, face * grid + i, 1] = counts
    stars = np.empty((len(star_index), 4), dtype=np.float32)
    stars[:, :3] = vectors[star_index]
    stars[:, 3] = np.asarray(magnitude, dtype=np.float64)[star_index]
    colors = np.full((len(star_index), 4), 255, dtype=np.uint8)
    colors[:, :3] = np.asarray(rgb, dtype=np.uint8)[star_index]
    return table, stars, colors


def _hex_rgb(text: str) -> tuple[int, int, int]:
    value = str(text or "").strip().lstrip("#")
    if len(value) != 6:
        return 248, 251, 255
    try:
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except ValueError:
        return 248, 251, 255


def _read_catalog(path: Path, minimum: float | None, maximum: float) -> list[tuple[float, float, float, tuple[int, int, int]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for entry in payload.get("objects", []):
        magnitude = entry.get("magnitude")
        if magnitude is None:
            continue
        magnitude = float(magnitude)
        if magnitude > maximum or (minimum is not None and magnitude <= minimum):
            continue
        rows.append((float(entry["ra_deg"]), float(entry["dec_deg"]), magnitude, _hex_rgb(entry.get("color"))))
    return rows


@dataclass(frozen=True)
class CatalogStars:
    stars: np.ndarray  # float32 (count, 4): equatorial unit vector, V magnitude
    colors: np.ndarray  # uint8 (count, 4): sRGB color from B-V, alpha 255
    magnitude_limit: float
    note: str


def _cached_catalog_rows(
    maximum_magnitude: float, cache_dir: Path | None
) -> tuple[list[tuple[float, float, float, tuple[int, int, int]]], float, str]:
    from photometry_app.core.sky_atlas import cached_scientific_star_catalog_files

    files = cached_scientific_star_catalog_files(cache_dir if cache_dir is not None else application_install_path(), maximum_magnitude)
    rows: list[tuple[float, float, float, tuple[int, int, int]]] = []
    labels: list[str] = []
    limit = 0.0
    seen: set[tuple[float, float]] = set()
    for label, path, file_limit in files:
        minimum = TYCHO_MINIMUM_MAGNITUDE if label == "Tycho-2" else None
        try:
            entries = _read_catalog(path, minimum, maximum_magnitude)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        added = 0
        for row in entries:
            key = (round(row[0], 4), round(row[1], 4))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
            added += 1
        if added:
            labels.append(label)
            limit = max(limit, min(file_limit, maximum_magnitude))
    note = f"{len(rows):,} {' and '.join(labels)} stars to V {limit:.1f} from CAst's Sky Atlas catalog cache"
    return rows, limit, note


def load_star_catalog_textures(
    maximum_magnitude: float = MAXIMUM_MAGNITUDE,
    cache_dir: Path | None = None,
) -> StarCatalogTextures | None:
    """Textures from Sky Atlas's cached catalogs, or None if none are cached."""

    rows, limit, note = _cached_catalog_rows(maximum_magnitude, cache_dir)
    if not rows:
        return None
    ra, dec, magnitude = (np.array([row[k] for row in rows]) for k in range(3))
    rgb = np.array([row[3] for row in rows], dtype=np.uint8)
    cells, stars, colors = build_star_textures(ra, dec, magnitude, rgb)
    return StarCatalogTextures(cells, stars, colors, len(rows), limit, note)


def load_catalog_stars(
    maximum_magnitude: float = MAXIMUM_MAGNITUDE,
    cache_dir: Path | None = None,
) -> CatalogStars | None:
    """Each cached catalog star once, as a direction, magnitude and color; None if none are cached."""

    rows, limit, note = _cached_catalog_rows(maximum_magnitude, cache_dir)
    if not rows:
        return None
    ra, dec, magnitude = (np.array([row[k] for row in rows]) for k in range(3))
    stars = np.empty((len(rows), 4), dtype=np.float32)
    stars[:, :3] = equatorial_vectors(ra, dec)
    stars[:, 3] = magnitude
    colors = np.full((len(rows), 4), 255, dtype=np.uint8)
    colors[:, :3] = np.array([row[3] for row in rows], dtype=np.uint8)
    return CatalogStars(stars, colors, limit, note)


def download_star_catalog(maximum_magnitude: float = MAXIMUM_MAGNITUDE) -> None:
    """Fetch the catalogs into Sky Atlas's cache, exactly as Sky Atlas would."""

    from photometry_app.core.sky_atlas import load_scientific_sky_atlas_star_objects

    load_scientific_sky_atlas_star_objects(
        application_install_path(),
        maximum_magnitude=maximum_magnitude,
        download_if_missing=True,
    )
