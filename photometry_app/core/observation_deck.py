from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import Enum
import json
import os
from pathlib import Path

from photometry_app.core.image_io import is_supported_image_path, read_header
from photometry_app.core.observation_map import (
    ObservationMapDay,
    ObservationMapResult,
    FILENAME_METADATA_PATTERN,
    filename_exposure_seconds,
    filename_observation_date,
    frame_type_is_calibration,
    frame_type_is_light,
    header_exposure_seconds,
    header_observation_date,
)
from photometry_app.core.wcs import validate_wcs


class ImageKind(str, Enum):
    LIGHT_SUBFRAME = "light_subframe"
    CALIBRATED_LIGHT = "calibrated_light"
    REGISTERED_LIGHT = "registered_light"
    NORMALIZED_LIGHT = "normalized_light"
    INTEGRATION = "integration"
    MASTER_CALIBRATION = "master_calibration"
    CALIBRATION_FRAME = "calibration_frame"
    OTHER = "other"


KIND_LABELS: dict[ImageKind, str] = {
    ImageKind.LIGHT_SUBFRAME: "Light subframe",
    ImageKind.CALIBRATED_LIGHT: "Calibrated light",
    ImageKind.REGISTERED_LIGHT: "Registered light",
    ImageKind.NORMALIZED_LIGHT: "Normalized light",
    ImageKind.INTEGRATION: "Integration",
    ImageKind.MASTER_CALIBRATION: "Master calibration",
    ImageKind.CALIBRATION_FRAME: "Calibration frame",
    ImageKind.OTHER: "Other image",
}

# Prefer the earliest processing stage present so WBPP/NINA trees are not double-counted.
STAT_KIND_PRIORITY: tuple[ImageKind, ...] = (
    ImageKind.LIGHT_SUBFRAME,
    ImageKind.CALIBRATED_LIGHT,
    ImageKind.REGISTERED_LIGHT,
    ImageKind.NORMALIZED_LIGHT,
)

_LIGHT_DIR_NAMES = frozenset({"light", "lights", "sub", "subs", "subframe", "subframes", "raw", "raws"})
_CALIBRATED_DIR_NAMES = frozenset({"calibrated", "calibrated_lights", "cfa", "debayered"})
_REGISTERED_DIR_NAMES = frozenset({"registered", "aligned", "alignment", "drizzle", "registered_lights"})
_NORMALIZED_DIR_NAMES = frozenset({"normalized", "normalization", "lnorm", "nml", "xnml", "lnml"})
_INTEGRATION_DIR_NAMES = frozenset(
    {
        "integration",
        "integrations",
        "stack",
        "stacked",
        "stacks",
        "masterlights",
        "master_lights",
        "mosaic",
        "mosaics",
    }
)
_MASTER_DIR_NAMES = frozenset({"master", "masters"})
_CALIBRATION_DIR_NAMES = frozenset(
    {
        "bias",
        "biases",
        "dark",
        "darks",
        "darkflat",
        "darkflats",
        "flat",
        "flats",
        "flatdarks",
        "offset",
        "offsets",
        "calibration",
        "calib",
    }
)
_PROCESSED_DIR_NAMES = frozenset({"processed", "output", "outputs", "export", "exports", "final", "jpeg", "jpg", "preview"})
_INTEGRATION_STEM_TOKENS = (
    "integrated",
    "integration",
    "_stack",
    "stack_",
    "stacked",
    "_mosaic",
    "masterlight",
    "master_light",
)
_NORMALIZATION_STEM_TOKENS = ("_nml", "lnml", "xnml", "lnorm", "normalized")
_MASTER_CALIBRATION_STEM_TOKENS = ("masterdark", "masterflat", "masterbias", "master_dark", "master_flat", "master_bias")

_FILTER_ALIASES = {
    "l": "L",
    "lum": "L",
    "luminance": "L",
    "clear": "L",
    "r": "R",
    "red": "R",
    "g": "G",
    "green": "G",
    "b": "B",
    "blue": "B",
    "ha": "Ha",
    "h-alpha": "Ha",
    "halpha": "Ha",
    "hα": "Ha",
    "sii": "SII",
    "s2": "SII",
    "oiii": "OIII",
    "o3": "OIII",
    "osc": "OSC",
    "rgb": "RGB",
    "lrgb": "LRGB",
}

_FILTER_DIR_HINTS = frozenset(_FILTER_ALIASES) | {
    "ha",
    "oiii",
    "sii",
    "luminance",
    "red",
    "green",
    "blue",
}

_OBSERVATION_DECK_DB_VERSION = 1
_OBSERVATION_DECK_DB_FILE_NAME = "observation_deck.json"


def path_key(path: Path | str) -> str:
    return os.path.normcase(os.path.normpath(str(Path(path).expanduser())))


def path_is_inside(child: Path | str, parent: Path | str) -> bool:
    child_key = path_key(child)
    parent_key = path_key(parent)
    if child_key == parent_key:
        return True
    prefix = parent_key.rstrip("\\/") + os.sep
    return child_key.startswith(prefix)


def relative_path_parts(child: Path | str, parent: Path | str) -> tuple[str, ...] | None:
    if not path_is_inside(child, parent):
        return None
    child_norm = os.path.normpath(str(Path(child).expanduser()))
    parent_norm = os.path.normpath(str(Path(parent).expanduser()))
    if os.path.normcase(child_norm) == os.path.normcase(parent_norm):
        return ()
    rest = child_norm[len(parent_norm.rstrip("\\/")) :].lstrip("\\/")
    return tuple(part for part in rest.split(os.sep) if part)


@dataclass(frozen=True, slots=True)
class DeckImage:
    path: Path
    root_path: Path
    target_name: str
    kind: ImageKind
    filter_name: str | None
    exposure_seconds: float | None
    observation_date: date | None
    object_name: str | None
    frame_type: str | None
    instrument: str | None
    telescope: str | None
    binning: str | None
    width: int | None
    height: int | None
    has_wcs: bool
    read_error: str | None = None

    @property
    def kind_label(self) -> str:
        return KIND_LABELS[self.kind]

    @property
    def target_key(self) -> tuple[str, str]:
        return (str(self.root_path), self.target_name)


@dataclass(frozen=True, slots=True)
class FilterTimeStats:
    filter_name: str
    exposure_seconds: float
    frame_count: int

    @property
    def exposure_hours(self) -> float:
        return float(self.exposure_seconds) / 3600.0


@dataclass(frozen=True, slots=True)
class TargetTimeStats:
    name: str
    root_name: str
    exposure_seconds: float
    frame_count: int
    night_count: int
    integration_count: int
    filter_count: int
    first_date: date | None
    last_date: date | None


@dataclass(frozen=True, slots=True)
class DeckStats:
    scope_label: str
    target_count: int
    subframe_count: int
    integration_count: int
    calibration_count: int
    master_calibration_count: int
    other_count: int
    total_exposure_seconds: float
    night_count: int
    first_date: date | None
    last_date: date | None
    filters: tuple[FilterTimeStats, ...]
    days: tuple[ObservationMapDay, ...]
    targets: tuple[TargetTimeStats, ...]
    solved_count: int
    unsolved_count: int
    instruments: tuple[str, ...]
    telescopes: tuple[str, ...]
    binnings: tuple[tuple[str, int], ...]
    longest_night_date: date | None
    longest_night_seconds: float
    average_subframe_seconds: float

    @property
    def total_exposure_hours(self) -> float:
        return float(self.total_exposure_seconds) / 3600.0


@dataclass(frozen=True, slots=True)
class ObservationDeckLibrary:
    root_paths: tuple[Path, ...]
    images: tuple[DeckImage, ...]
    scanned_files: int
    unreadable_files: int

    @property
    def root_path(self) -> Path | None:
        return self.root_paths[0] if self.root_paths else None

    def images_for_path(self, folder: Path | None = None) -> tuple[DeckImage, ...]:
        if folder is None:
            return self.images
        return tuple(image for image in self.images if path_is_inside(image.path, folder))

    def image_by_path(self, path: Path) -> DeckImage | None:
        wanted = path_key(path)
        for image in self.images:
            if path_key(image.path) == wanted:
                return image
        return None

    def target_names(self) -> tuple[str, ...]:
        names = sorted({image.target_name for image in self.images}, key=str.casefold)
        return tuple(names)


def is_observation_deck_image_path(path: Path) -> bool:
    return is_supported_image_path(path)


def iter_observation_deck_images(root_path: Path) -> list[Path]:
    resolved = root_path.expanduser().resolve()
    if not resolved.is_dir():
        return []
    return sorted(
        (path for path in resolved.rglob("*") if path.is_file() and is_observation_deck_image_path(path)),
        key=lambda item: str(item).casefold(),
    )


def target_name_for_path(path: Path, root: Path) -> str:
    parts = relative_path_parts(path, root)
    if parts is None or len(parts) <= 1:
        return Path(root).name
    return parts[0]


def classify_image_kind(path: Path, header: object | None = None, *, root: Path | None = None) -> ImageKind:
    dir_names = _relative_dir_names(path, root)
    stem = path.stem.casefold()
    imagetyp = _header_text(header, "IMAGETYP")
    masttype = _header_text(header, "MASTTYPE")
    light_status = frame_type_is_light(imagetyp)
    calibration_type = frame_type_is_calibration(imagetyp)
    mast_normalized = _normalize_token(masttype)

    if mast_normalized:
        if mast_normalized in {"light", "masterlight", "master light"} or mast_normalized.startswith("light"):
            return ImageKind.INTEGRATION
        if any(mast_normalized.startswith(prefix) for prefix in ("bias", "dark", "flat", "offset")):
            return ImageKind.MASTER_CALIBRATION

    if _dir_hit(dir_names, _NORMALIZED_DIR_NAMES) or _stem_has_any(stem, _NORMALIZATION_STEM_TOKENS):
        return ImageKind.NORMALIZED_LIGHT
    if _dir_hit(dir_names, _INTEGRATION_DIR_NAMES) or _stem_has_any(stem, _INTEGRATION_STEM_TOKENS):
        return ImageKind.INTEGRATION
    if _dir_hit(dir_names, _REGISTERED_DIR_NAMES):
        return ImageKind.REGISTERED_LIGHT
    if _dir_hit(dir_names, _CALIBRATED_DIR_NAMES):
        return ImageKind.CALIBRATED_LIGHT
    if _dir_hit(dir_names, _MASTER_DIR_NAMES):
        if "light" in stem or light_status is True:
            return ImageKind.INTEGRATION
        return ImageKind.MASTER_CALIBRATION
    if _stem_has_any(stem, _MASTER_CALIBRATION_STEM_TOKENS):
        return ImageKind.MASTER_CALIBRATION
    if stem.startswith("master"):
        if "light" in stem:
            return ImageKind.INTEGRATION
        return ImageKind.MASTER_CALIBRATION
    if _dir_hit(dir_names, _CALIBRATION_DIR_NAMES) or calibration_type:
        return ImageKind.CALIBRATION_FRAME
    if _dir_hit(dir_names, _PROCESSED_DIR_NAMES):
        return ImageKind.OTHER
    if light_status is True or _dir_hit(dir_names, _LIGHT_DIR_NAMES):
        return ImageKind.LIGHT_SUBFRAME
    if light_status is False:
        return ImageKind.CALIBRATION_FRAME
    return ImageKind.LIGHT_SUBFRAME


def inspect_deck_image(
    path: Path,
    *,
    root: Path,
    observation_timezone: str = "UTC",
) -> DeckImage:
    target = target_name_for_path(path, root)
    header = None
    read_error = None
    try:
        header = read_header(path)
    except Exception as exc:
        read_error = str(exc).strip() or exc.__class__.__name__

    kind = classify_image_kind(path, header, root=root)
    filter_name = _extract_filter_name(path, header, root=root)
    exposure = header_exposure_seconds(header) if header is not None else None
    if exposure is None:
        exposure = filename_exposure_seconds(path)
    observation_date = header_observation_date(header, observation_timezone=observation_timezone) if header is not None else None
    if observation_date is None:
        observation_date = filename_observation_date(path)
    object_name = _header_text(header, "OBJECT")
    frame_type = _header_text(header, "IMAGETYP")
    if not frame_type:
        match = FILENAME_METADATA_PATTERN.search(path.stem)
        if match is not None:
            frame_type = match.group("frame_type")
    instrument = _header_text(header, "INSTRUME")
    telescope = _header_text(header, "TELESCOP")
    binning = _extract_binning(header)
    width = _header_int(header, "NAXIS1")
    height = _header_int(header, "NAXIS2")
    has_wcs = False
    if header is not None:
        try:
            solved, _reasons = validate_wcs(header, path)
            has_wcs = bool(solved)
        except Exception:
            has_wcs = False
    return DeckImage(
        path=path.expanduser().resolve(),
        root_path=root.expanduser().resolve(),
        target_name=target,
        kind=kind,
        filter_name=filter_name,
        exposure_seconds=float(exposure) if exposure is not None else None,
        observation_date=observation_date,
        object_name=object_name,
        frame_type=frame_type,
        instrument=instrument,
        telescope=telescope,
        binning=binning,
        width=width,
        height=height,
        has_wcs=has_wcs,
        read_error=read_error,
    )


def normalize_master_roots(root_paths: Sequence[Path], *, touch_filesystem: bool = True) -> tuple[Path, ...]:
    resolved: list[Path] = []
    seen: set[str] = set()
    for raw in root_paths:
        path = Path(raw).expanduser()
        if touch_filesystem:
            path = path.resolve()
        key = path_key(path)
        if key in seen:
            continue
        seen.add(key)
        resolved.append(path)
    kept: list[Path] = []
    for path in resolved:
        if any(path_key(path) != path_key(parent) and path_is_inside(path, parent) for parent in resolved):
            continue
        kept.append(path)
    return tuple(kept)


def scan_observation_deck(
    root_path: Path | Sequence[Path],
    *,
    observation_timezone: str = "UTC",
    progress_callback: Callable[[int, int, Path], None] | None = None,
) -> ObservationDeckLibrary:
    if isinstance(root_path, (str, Path)):
        requested = (Path(root_path),)
    else:
        requested = tuple(Path(item) for item in root_path)
    roots = normalize_master_roots(requested)
    missing = [path for path in roots if not path.is_dir()]
    if missing:
        raise ValueError(f"Folder not found: {missing[0]}")
    if not roots:
        return ObservationDeckLibrary(root_paths=(), images=(), scanned_files=0, unreadable_files=0)

    candidates: list[tuple[Path, Path]] = []
    seen: set[Path] = set()
    for root in roots:
        for path in iter_observation_deck_images(root):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            candidates.append((path, root))

    images: list[DeckImage] = []
    unreadable = 0
    total = len(candidates)
    for index, (path, root) in enumerate(candidates, start=1):
        if progress_callback is not None:
            progress_callback(index, total, path)
        image = inspect_deck_image(path, root=root, observation_timezone=observation_timezone)
        if image.read_error:
            unreadable += 1
        images.append(image)
    return ObservationDeckLibrary(
        root_paths=roots,
        images=tuple(images),
        scanned_files=total,
        unreadable_files=unreadable,
    )


def merge_observation_deck_libraries(
    existing: ObservationDeckLibrary | None,
    incoming: ObservationDeckLibrary,
) -> ObservationDeckLibrary:
    if existing is None or not existing.root_paths:
        return incoming
    incoming_files = {path_key(image.path) for image in incoming.images}
    kept_existing = [
        image for image in existing.images if path_key(image.path) not in incoming_files
    ]
    roots = normalize_master_roots((*existing.root_paths, *incoming.root_paths), touch_filesystem=False)
    rebound: list[DeckImage] = []
    seen: set[str] = set()
    for image in (*kept_existing, *incoming.images):
        key = path_key(image.path)
        if key in seen:
            continue
        owner = _owning_master_root(image.path, roots)
        if owner is None:
            continue
        seen.add(key)
        if path_key(image.root_path) != path_key(owner):
            rebound.append(
                replace(
                    image,
                    root_path=owner,
                    target_name=target_name_for_path(image.path, owner),
                )
            )
        else:
            rebound.append(image)
    return ObservationDeckLibrary(
        root_paths=roots,
        images=tuple(rebound),
        scanned_files=len(rebound),
        unreadable_files=sum(1 for image in rebound if image.read_error),
    )


def _owning_master_root(path: Path, roots: Sequence[Path]) -> Path | None:
    owners = [root for root in roots if path_is_inside(path, root)]
    if not owners:
        return None
    return max(owners, key=lambda item: len(Path(item).parts))


def remove_observation_deck_root(library: ObservationDeckLibrary, root_path: Path) -> ObservationDeckLibrary:
    wanted = path_key(root_path)
    roots = tuple(path for path in library.root_paths if path_key(path) != wanted)
    images = tuple(image for image in library.images if path_key(image.root_path) != wanted)
    return ObservationDeckLibrary(
        root_paths=roots,
        images=images,
        scanned_files=len(images),
        unreadable_files=sum(1 for image in images if image.read_error),
    )


def observation_deck_database_path() -> Path:
    override = os.getenv("CITIZEN_PHOTOMETRY_OBSERVATION_DECK_PATH")
    if override:
        return Path(override).expanduser()
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "CitizenPhotometry" / _OBSERVATION_DECK_DB_FILE_NAME
    return Path.home() / ".citizen-photometry" / _OBSERVATION_DECK_DB_FILE_NAME


def save_observation_deck_library(library: ObservationDeckLibrary, path: Path | None = None) -> Path:
    destination = path or observation_deck_database_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _OBSERVATION_DECK_DB_VERSION,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "roots": [str(root) for root in library.root_paths],
        "images": [_deck_image_to_payload(image) for image in library.images],
        "scanned_files": library.scanned_files,
        "unreadable_files": library.unreadable_files,
    }
    destination.write_text(json.dumps(payload), encoding="utf-8")
    return destination


def load_observation_deck_library(path: Path | None = None) -> ObservationDeckLibrary | None:
    source = path or observation_deck_database_path()
    if not source.is_file():
        return None
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    roots = tuple(Path(item) for item in payload.get("roots", []) if str(item).strip())
    images: list[DeckImage] = []
    for item in payload.get("images", []):
        image = _deck_image_from_payload(item)
        if image is not None:
            images.append(image)
    return ObservationDeckLibrary(
        root_paths=normalize_master_roots(roots, touch_filesystem=False),
        images=tuple(images),
        scanned_files=int(payload.get("scanned_files", len(images)) or 0),
        unreadable_files=int(payload.get("unreadable_files", 0) or 0),
    )


def _deck_image_to_payload(image: DeckImage) -> dict[str, object]:
    return {
        "path": str(image.path),
        "root_path": str(image.root_path),
        "target_name": image.target_name,
        "kind": image.kind.value,
        "filter_name": image.filter_name,
        "exposure_seconds": image.exposure_seconds,
        "observation_date": image.observation_date.isoformat() if image.observation_date else None,
        "object_name": image.object_name,
        "frame_type": image.frame_type,
        "instrument": image.instrument,
        "telescope": image.telescope,
        "binning": image.binning,
        "width": image.width,
        "height": image.height,
        "has_wcs": image.has_wcs,
        "read_error": image.read_error,
    }


def _deck_image_from_payload(payload: object) -> DeckImage | None:
    if not isinstance(payload, dict):
        return None
    path_text = str(payload.get("path") or "").strip()
    root_text = str(payload.get("root_path") or "").strip()
    if not path_text or not root_text:
        return None
    kind_value = str(payload.get("kind") or ImageKind.OTHER.value)
    try:
        kind = ImageKind(kind_value)
    except ValueError:
        kind = ImageKind.OTHER
    date_text = payload.get("observation_date")
    observation_date = None
    if isinstance(date_text, str) and date_text:
        try:
            observation_date = date.fromisoformat(date_text)
        except ValueError:
            observation_date = None
    return DeckImage(
        path=Path(path_text),
        root_path=Path(root_text),
        target_name=str(payload.get("target_name") or Path(root_text).name),
        kind=kind,
        filter_name=payload.get("filter_name") if isinstance(payload.get("filter_name"), str) else None,
        exposure_seconds=_as_optional_float(payload.get("exposure_seconds")),
        observation_date=observation_date,
        object_name=payload.get("object_name") if isinstance(payload.get("object_name"), str) else None,
        frame_type=payload.get("frame_type") if isinstance(payload.get("frame_type"), str) else None,
        instrument=payload.get("instrument") if isinstance(payload.get("instrument"), str) else None,
        telescope=payload.get("telescope") if isinstance(payload.get("telescope"), str) else None,
        binning=payload.get("binning") if isinstance(payload.get("binning"), str) else None,
        width=_as_optional_int(payload.get("width")),
        height=_as_optional_int(payload.get("height")),
        has_wcs=bool(payload.get("has_wcs")),
        read_error=payload.get("read_error") if isinstance(payload.get("read_error"), str) else None,
    )


def _as_optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def counting_frames(images: Sequence[DeckImage]) -> tuple[DeckImage, ...]:
    grouped: dict[tuple[str, str], list[DeckImage]] = defaultdict(list)
    for image in images:
        grouped[(path_key(image.root_path), image.target_name)].append(image)
    selected: list[DeckImage] = []
    for _target, group in grouped.items():
        chosen_kind = None
        for kind in STAT_KIND_PRIORITY:
            if any(item.kind == kind and item.exposure_seconds for item in group):
                chosen_kind = kind
                break
        if chosen_kind is None:
            continue
        selected.extend(
            item
            for item in group
            if item.kind == chosen_kind and item.exposure_seconds and item.observation_date is not None
        )
    return tuple(selected)


def build_deck_stats(
    images: Sequence[DeckImage],
    *,
    scope_label: str,
) -> DeckStats:
    counting = counting_frames(images)
    filters: dict[str, list[DeckImage]] = defaultdict(list)
    days_exposure: dict[date, float] = defaultdict(float)
    days_count: dict[date, int] = defaultdict(int)
    target_frames: dict[str, list[DeckImage]] = defaultdict(list)
    for image in counting:
        label = image.filter_name or "Unknown"
        filters[label].append(image)
        if image.observation_date is not None and image.exposure_seconds:
            days_exposure[image.observation_date] += float(image.exposure_seconds)
            days_count[image.observation_date] += 1
        target_frames[image.target_name].append(image)

    filter_stats = tuple(
        sorted(
            (
                FilterTimeStats(
                    filter_name=name,
                    exposure_seconds=float(sum(item.exposure_seconds or 0.0 for item in group)),
                    frame_count=len(group),
                )
                for name, group in filters.items()
            ),
            key=lambda item: (-item.exposure_seconds, item.filter_name.casefold()),
        )
    )
    days = tuple(
        ObservationMapDay(
            observation_date=day,
            exposure_seconds=float(days_exposure[day]),
            frame_count=int(days_count[day]),
        )
        for day in sorted(days_exposure)
    )
    longest_night_date = None
    longest_night_seconds = 0.0
    if days:
        longest = max(days, key=lambda item: item.exposure_seconds)
        longest_night_date = longest.observation_date
        longest_night_seconds = float(longest.exposure_seconds)

    target_groups: dict[tuple[str, str], list[DeckImage]] = defaultdict(list)
    counting_groups: dict[tuple[str, str], list[DeckImage]] = defaultdict(list)
    target_roots: dict[tuple[str, str], Path] = {}
    integration_count = 0
    calibration_count = 0
    master_calibration_count = 0
    other_count = 0
    instruments: set[str] = set()
    telescopes: set[str] = set()
    for image in images:
        key = (path_key(image.root_path), image.target_name)
        target_groups[key].append(image)
        target_roots.setdefault(key, image.root_path)
        if image.kind == ImageKind.INTEGRATION:
            integration_count += 1
        elif image.kind == ImageKind.CALIBRATION_FRAME:
            calibration_count += 1
        elif image.kind == ImageKind.MASTER_CALIBRATION:
            master_calibration_count += 1
        elif image.kind == ImageKind.OTHER:
            other_count += 1
        if image.instrument:
            instruments.add(image.instrument)
        if image.telescope:
            telescopes.add(image.telescope)
    for image in counting:
        counting_groups[(path_key(image.root_path), image.target_name)].append(image)

    target_stats: list[TargetTimeStats] = []
    for (root_key, name), root_path in sorted(
        target_roots.items(),
        key=lambda item: (str(item[1]).casefold(), item[0][1].casefold()),
    ):
        group = target_groups[(root_key, name)]
        counted = counting_groups.get((root_key, name), [])
        dates = [image.observation_date for image in counted if image.observation_date is not None]
        target_stats.append(
            TargetTimeStats(
                name=name,
                root_name=root_path.name,
                exposure_seconds=float(sum(image.exposure_seconds or 0.0 for image in counted)),
                frame_count=len(counted),
                night_count=len(set(dates)),
                integration_count=sum(1 for image in group if image.kind == ImageKind.INTEGRATION),
                filter_count=len({image.filter_name or "Unknown" for image in counted}),
                first_date=min(dates) if dates else None,
                last_date=max(dates) if dates else None,
            )
        )

    binnings: dict[str, int] = defaultdict(int)
    for image in counting:
        if image.binning:
            binnings[image.binning] += 1

    instruments = tuple(sorted(instruments, key=str.casefold))
    telescopes = tuple(sorted(telescopes, key=str.casefold))
    exposures = [float(image.exposure_seconds) for image in counting if image.exposure_seconds]
    return DeckStats(
        scope_label=scope_label,
        target_count=len(target_roots),
        subframe_count=len(counting),
        integration_count=integration_count,
        calibration_count=calibration_count,
        master_calibration_count=master_calibration_count,
        other_count=other_count,
        total_exposure_seconds=float(sum(item.exposure_seconds for item in days)),
        night_count=len(days),
        first_date=days[0].observation_date if days else None,
        last_date=days[-1].observation_date if days else None,
        filters=filter_stats,
        days=days,
        targets=tuple(sorted(target_stats, key=lambda item: (-item.exposure_seconds, item.name.casefold()))),
        solved_count=sum(1 for image in counting if image.has_wcs),
        unsolved_count=sum(1 for image in counting if not image.has_wcs),
        instruments=instruments,
        telescopes=telescopes,
        binnings=tuple(sorted(binnings.items(), key=lambda item: (-item[1], item[0].casefold()))),
        longest_night_date=longest_night_date,
        longest_night_seconds=longest_night_seconds,
        average_subframe_seconds=(sum(exposures) / len(exposures)) if exposures else 0.0,
    )


def observation_map_from_stats(root_path: Path | None, stats: DeckStats, *, included_frames: int, skipped_files: int) -> ObservationMapResult:
    return ObservationMapResult(
        root_path=root_path or Path(),
        days=stats.days,
        included_frames=included_frames,
        skipped_files=skipped_files,
        total_exposure_seconds=stats.total_exposure_seconds,
        first_date=stats.first_date,
        last_date=stats.last_date,
    )


def header_keyword_rows(header: object) -> tuple[tuple[str, str, str], ...]:
    rows: list[tuple[str, str, str]] = []
    cards = getattr(header, "cards", None)
    if cards is not None:
        for card in cards:
            keyword = str(getattr(card, "keyword", "") or "").strip()
            if not keyword:
                continue
            value = getattr(card, "value", "")
            comment = str(getattr(card, "comment", "") or "")
            rows.append((keyword, _format_header_value(value), comment))
        return tuple(rows)
    keys = getattr(header, "keys", None)
    if callable(keys):
        getter = getattr(header, "get", lambda key: None)
        for key in keys():
            rows.append((str(key), _format_header_value(getter(key)), ""))
    return tuple(rows)


def normalize_filter_name(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    alias = _FILTER_ALIASES.get(text.casefold())
    return alias or text


def _extract_filter_name(path: Path, header: object | None, *, root: Path) -> str | None:
    from_header = normalize_filter_name(_header_text(header, "FILTER"))
    if from_header:
        return from_header
    match = FILENAME_METADATA_PATTERN.search(path.stem)
    if match is not None:
        from_name = normalize_filter_name(match.group("filter_name"))
        if from_name:
            return from_name
    for part in reversed(_relative_dir_names(path, root)):
        alias = _FILTER_ALIASES.get(part)
        if alias:
            return alias
        if part in _FILTER_DIR_HINTS:
            return part.upper()
    return None


def _extract_binning(header: object | None) -> str | None:
    if header is None:
        return None
    for key in ("XBINNING", "CCDXBIN", "XBIN"):
        value = _header_int(header, key)
        if value is not None:
            return f"Bin{value}"
    return None


def _relative_dir_names(path: Path, root: Path | None) -> tuple[str, ...]:
    if root is None:
        parts = Path(path).expanduser().parts[:-1]
        return tuple(part.casefold() for part in parts)
    parts = relative_path_parts(path, root)
    if parts is None:
        parts = Path(path).expanduser().parts[:-1]
        return tuple(part.casefold() for part in parts)
    return tuple(part.casefold() for part in parts[:-1])


def _dir_hit(dir_names: Sequence[str], candidates: Iterable[str]) -> bool:
    wanted = set(candidates)
    return any(name in wanted for name in dir_names)


def _stem_has_any(stem: str, tokens: Iterable[str]) -> bool:
    return any(token in stem for token in tokens)


def _normalize_token(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(str(value).strip().casefold().replace("_", " ").split())


def _header_text(header: object | None, key: str) -> str | None:
    if header is None:
        return None
    getter = getattr(header, "get", None)
    if getter is None:
        return None
    try:
        present = key in header
    except Exception:
        present = True
    if not present:
        return None
    value = getter(key)
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def _header_int(header: object | None, key: str) -> int | None:
    text = _header_text(header, key)
    if text is None:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _format_header_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return repr(value)
    text = str(value)
    return text if len(text) <= 4000 else text[:3997] + "..."
