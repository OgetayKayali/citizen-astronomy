from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from photometry_app.app_metadata import application_install_path


SKY_ATLAS_CATALOG_DIR_NAME = "sky-atlas"


def sky_atlas_catalog_root(cache_dir: Path | None = None) -> Path:
    """Resolve where Sky Atlas catalogs are stored.

    Prefer `<cache_dir>/sky-atlas` when a cache directory is supplied (tests /
    configured project cache). Otherwise use `<install>/sky-atlas`, matching
    the shareable survey folder pattern.
    """
    if cache_dir is not None:
        root = Path(cache_dir).expanduser() / SKY_ATLAS_CATALOG_DIR_NAME
        root.mkdir(parents=True, exist_ok=True)
        return root

    install_root = application_install_path() / SKY_ATLAS_CATALOG_DIR_NAME
    install_root.mkdir(parents=True, exist_ok=True)
    return install_root


@dataclass(frozen=True, slots=True)
class StarCatalogDownloadEstimate:
    magnitude_limit: float
    catalog_label: str
    approximate_star_count: int
    approximate_size_mb: float
    notes: str = ""


_STAR_CATALOG_ESTIMATE_ANCHORS: tuple[tuple[float, str, int, float, str], ...] = (
    (6.0, "Hipparcos", 5_000, 0.7, "Bright stars only"),
    (8.0, "Hipparcos", 45_000, 5.0, "Hipparcos all-sky"),
    (9.5, "Hipparcos", 99_000, 12.0, "Default Hipparcos cut"),
    (10.5, "Hipparcos + Tycho-2", 280_000, 40.0, "Adds Tycho-2 mid-faint stars"),
    (11.5, "Tycho-2", 700_000, 95.0, "Dense Tycho-2 coverage"),
    (12.5, "Tycho-2", 1_800_000, 240.0, "Near-complete Tycho-2"),
    (13.5, "Gaia DR3", 6_000_000, 850.0, "Large download; stored on disk"),
    (14.5, "Gaia DR3", 18_000_000, 2_500.0, "Very large download"),
    (15.0, "Gaia DR3", 35_000_000, 4_800.0, "Largest supported all-sky cut"),
)


def estimate_star_catalog_download(magnitude_limit: float) -> StarCatalogDownloadEstimate:
    limit = max(4.0, min(15.0, float(magnitude_limit)))
    lower = _STAR_CATALOG_ESTIMATE_ANCHORS[0]
    upper = _STAR_CATALOG_ESTIMATE_ANCHORS[-1]
    for anchor in _STAR_CATALOG_ESTIMATE_ANCHORS:
        if limit <= anchor[0] + 1.0e-9:
            upper = anchor
            break
        lower = anchor
    if abs(upper[0] - lower[0]) <= 1.0e-9:
        _mag, label, count, size_mb, notes = upper
    else:
        t = (limit - lower[0]) / (upper[0] - lower[0])
        log_count = math.log10(max(1, lower[2])) + t * (
            math.log10(max(1, upper[2])) - math.log10(max(1, lower[2]))
        )
        count = int(round(10**log_count))
        size_mb = lower[3] + t * (upper[3] - lower[3])
        label = upper[1] if t >= 0.5 else lower[1]
        notes = upper[4] if t >= 0.5 else lower[4]
    return StarCatalogDownloadEstimate(
        magnitude_limit=limit,
        catalog_label=label,
        approximate_star_count=max(1, count),
        approximate_size_mb=max(0.1, round(size_mb, 1)),
        notes=notes,
    )


def format_star_count(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.0f}k"
    return f"{count}"


def format_data_size_mb(size_mb: float) -> str:
    if size_mb >= 1000:
        return f"{size_mb / 1000:.1f} GB"
    if size_mb >= 10:
        return f"{size_mb:.0f} MB"
    return f"{size_mb:.1f} MB"


ProgressCallback = Callable[[str, float | None], None]


def write_catalog_manifest(root: Path, payload: dict[str, object]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "manifest.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def read_catalog_manifest(root: Path) -> dict[str, object] | None:
    path = root / "manifest.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
