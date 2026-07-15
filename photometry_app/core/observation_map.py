from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import re

from photometry_app.core.image_io import read_header
from photometry_app.core.scanner import FILENAME_METADATA_PATTERN, parse_observation_timestamp

FITS_SUBFRAME_SUFFIXES = {".fit", ".fits"}

_EXPOSURE_HEADER_KEYS: tuple[tuple[str, float], ...] = (
    ("EXPTIME", 1.0),
    ("EXPOSURE", 1.0),
    ("EXPOSURE_TIME", 1.0),
    ("EXPOSUREMS", 0.001),
)

_ACCEPTED_LIGHT_TYPES = {
    "light",
    "light frame",
    "lightframe",
    "science",
    "object",
}

_REJECTED_CALIBRATION_TYPES = {
    "bias",
    "dark",
    "flat",
    "darkflat",
    "flatfield",
    "flat field",
    "offset",
    "bias frame",
    "dark frame",
    "flat frame",
}

_EXCLUDED_DIR_NAMES = {
    "aligned",
    "alignment",
    "bias",
    "biases",
    "calibration",
    "calibrated",
    "dark",
    "darks",
    "darkflat",
    "darkflats",
    "flat",
    "flats",
    "integration",
    "integrations",
    "master",
    "masters",
    "masterlights",
    "output",
    "processed",
    "registered",
    "stack",
    "stacked",
    "stacks",
}

_EXCLUDED_NAME_TOKENS = (
    "master",
    "stacked",
    "stack_",
    "_stack",
    "integrated",
    "integration",
    "calibrated",
    "_calib",
    "aligned",
    "_mosaic",
)

_EXPOSURE_NUMBER_PATTERN = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


@dataclass(frozen=True, slots=True)
class ObservationMapFrame:
    path: Path
    observation_date: date
    exposure_seconds: float
    object_name: str | None = None


@dataclass(frozen=True, slots=True)
class ObservationMapDay:
    observation_date: date
    exposure_seconds: float
    frame_count: int


@dataclass(frozen=True, slots=True)
class ObservationMapResult:
    root_path: Path
    days: tuple[ObservationMapDay, ...]
    included_frames: int
    skipped_files: int
    total_exposure_seconds: float
    first_date: date | None
    last_date: date | None

    @property
    def total_exposure_hours(self) -> float:
        return float(self.total_exposure_seconds) / 3600.0

    def day_map(self) -> dict[date, ObservationMapDay]:
        return {day.observation_date: day for day in self.days}


def is_fits_subframe_candidate(path: Path) -> bool:
    return path.is_file() and path.suffix.casefold() in FITS_SUBFRAME_SUFFIXES


def path_looks_like_master_or_processed(path: Path, *, root: Path | None = None) -> bool:
    resolved = path.resolve()
    try:
        relative_parts = resolved.relative_to(root.resolve()).parts[:-1] if root is not None else resolved.parts[:-1]
    except ValueError:
        relative_parts = resolved.parts[:-1]
    for part in relative_parts:
        if part.casefold() in _EXCLUDED_DIR_NAMES:
            return True
    stem = resolved.stem.casefold()
    if stem.startswith("master") or stem.startswith("stack"):
        return True
    return any(token in stem for token in _EXCLUDED_NAME_TOKENS)


def frame_type_is_calibration(frame_type: str | None) -> bool:
    if frame_type is None:
        return False
    normalized = " ".join(str(frame_type).strip().casefold().replace("_", " ").split())
    if not normalized:
        return False
    if normalized in _REJECTED_CALIBRATION_TYPES:
        return True
    if any(normalized.startswith(prefix) for prefix in ("bias", "dark", "flat")):
        return True
    return False


def frame_type_is_light(frame_type: str | None) -> bool | None:
    if frame_type is None:
        return None
    normalized = " ".join(str(frame_type).strip().casefold().replace("_", " ").split())
    if not normalized:
        return None
    if normalized in _ACCEPTED_LIGHT_TYPES or normalized.startswith("light"):
        return True
    if frame_type_is_calibration(normalized):
        return False
    return None


def _coerce_exposure_seconds(value: object, scale: float) -> float | None:
    if value is None:
        return None
    try:
        exposure = float(value) * scale
    except (TypeError, ValueError):
        match = _EXPOSURE_NUMBER_PATTERN.search(str(value))
        if match is None:
            return None
        exposure = float(match.group(0)) * scale
    if not (exposure > 0.0) or exposure != exposure:  # NaN check
        return None
    return float(exposure)


def header_exposure_seconds(header: object) -> float | None:
    getter = getattr(header, "get", None)
    if getter is None:
        return None
    for key, scale in _EXPOSURE_HEADER_KEYS:
        if key not in header:
            continue
        exposure = _coerce_exposure_seconds(getter(key), scale)
        if exposure is not None:
            return exposure
    return None


def filename_exposure_seconds(path: Path) -> float | None:
    match = FILENAME_METADATA_PATTERN.search(path.stem)
    if match is None:
        return None
    return _coerce_exposure_seconds(match.group("exposure_seconds"), 1.0)


def filename_observation_date(path: Path) -> date | None:
    match = FILENAME_METADATA_PATTERN.search(path.stem)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group("date"), "%Y%m%d").date()
    except ValueError:
        return None


def header_observation_date(header: object, *, observation_timezone: str = "UTC") -> date | None:
    getter = getattr(header, "get", None)
    if getter is None:
        return None
    for key in ("DATE-OBS", "DATEOBS", "DATE_OBS", "OBS-DATE"):
        if key not in header:
            continue
        timestamp = parse_observation_timestamp(getter(key), observation_timezone=observation_timezone)
        if timestamp is not None:
            return timestamp.date()
    return None


def inspect_observation_map_frame(
    path: Path,
    *,
    root: Path | None = None,
    observation_timezone: str = "UTC",
) -> ObservationMapFrame | None:
    if not is_fits_subframe_candidate(path):
        return None
    if path_looks_like_master_or_processed(path, root=root):
        return None
    try:
        header = read_header(path)
    except Exception:
        return None
    if header.get("MASTTYPE") not in (None, ""):
        return None
    frame_type = header.get("IMAGETYP")
    light_status = frame_type_is_light(str(frame_type) if frame_type is not None else None)
    if light_status is False:
        return None
    exposure = header_exposure_seconds(header)
    if exposure is None:
        exposure = filename_exposure_seconds(path)
    if exposure is None:
        return None
    observation_date = header_observation_date(header, observation_timezone=observation_timezone)
    if observation_date is None:
        observation_date = filename_observation_date(path)
    if observation_date is None:
        return None
    object_name = None
    object_value = header.get("OBJECT")
    if object_value not in (None, ""):
        object_name = str(object_value).strip() or None
    return ObservationMapFrame(
        path=path,
        observation_date=observation_date,
        exposure_seconds=float(exposure),
        object_name=object_name,
    )


def iter_fits_files(root_path: Path) -> list[Path]:
    resolved = root_path.expanduser().resolve()
    if not resolved.is_dir():
        return []
    return sorted(
        (
            path
            for path in resolved.rglob("*")
            if path.is_file() and path.suffix.casefold() in FITS_SUBFRAME_SUFFIXES
        ),
        key=lambda item: str(item).casefold(),
    )


def build_observation_map(
    root_path: Path,
    *,
    observation_timezone: str = "UTC",
    progress_callback: Callable[[int, int, Path], None] | None = None,
) -> ObservationMapResult:
    resolved_root = root_path.expanduser().resolve()
    if not resolved_root.is_dir():
        raise ValueError(f"Folder not found: {resolved_root}")

    candidates = iter_fits_files(resolved_root)
    totals: dict[date, float] = defaultdict(float)
    counts: dict[date, int] = defaultdict(int)
    included = 0
    skipped = 0
    total_files = len(candidates)

    for index, path in enumerate(candidates, start=1):
        if progress_callback is not None:
            progress_callback(index, total_files, path)
        frame = inspect_observation_map_frame(
            path,
            root=resolved_root,
            observation_timezone=observation_timezone,
        )
        if frame is None:
            skipped += 1
            continue
        totals[frame.observation_date] += frame.exposure_seconds
        counts[frame.observation_date] += 1
        included += 1

    days = tuple(
        ObservationMapDay(
            observation_date=day,
            exposure_seconds=float(totals[day]),
            frame_count=int(counts[day]),
        )
        for day in sorted(totals)
    )
    first_date = days[0].observation_date if days else None
    last_date = days[-1].observation_date if days else None
    total_exposure = float(sum(day.exposure_seconds for day in days))
    return ObservationMapResult(
        root_path=resolved_root,
        days=days,
        included_frames=included,
        skipped_files=skipped,
        total_exposure_seconds=total_exposure,
        first_date=first_date,
        last_date=last_date,
    )


def format_duration(total_seconds: float) -> str:
    seconds = max(0, int(round(float(total_seconds))))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours}h {minutes:02d}m"
    if minutes > 0:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def contribution_year_bounds(result: ObservationMapResult, year: int | None = None) -> tuple[date, date]:
    if year is not None:
        start = date(year, 1, 1)
        end = date(year, 12, 31)
        return start, end
    if result.first_date is None or result.last_date is None:
        today = date.today()
        return date(today.year, 1, 1), date(today.year, 12, 31)
    return date(result.first_date.year, 1, 1), date(result.last_date.year, 12, 31)


def iter_calendar_days(start: date, end: date) -> Iterator[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def contribution_level(exposure_seconds: float, *, max_seconds: float) -> int:
    if exposure_seconds <= 0.0 or max_seconds <= 0.0:
        return 0
    ratio = float(exposure_seconds) / float(max_seconds)
    if ratio < 0.15:
        return 1
    if ratio < 0.35:
        return 2
    if ratio < 0.65:
        return 3
    return 4
