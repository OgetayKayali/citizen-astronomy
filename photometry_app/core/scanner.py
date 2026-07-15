from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
import re

from photometry_app.core.image_io import is_supported_image_path, read_header_and_shape
from photometry_app.core.models import FileScanResult, ObjectScanSummary, ObservationMetadata, ScanReport, WcsStatus
from photometry_app.core.wcs import validate_wcs


DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
)

_MIN_UTC_DATETIME = datetime.min.replace(tzinfo=UTC)

SUPPORTED_FITS_SUFFIXES = {".fit", ".fits", ".xisf"}

FILENAME_METADATA_PATTERN = re.compile(
    r"(?P<frame_type>[A-Za-z]+)_(?P<object_name>.+?)_(?P<exposure_seconds>\d+(?:\.\d+)?)s_(?P<binning>Bin\d+)_(?P<filter_name>[^_]+)_(?P<date>\d{8})-(?P<time>\d{6})_(?P<sequence>\d+)",
    re.IGNORECASE,
)

_DATE_OBS_TIMEZONE_PATTERN = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$", re.IGNORECASE)


def scan_fits_tree(root_path: Path, observation_timezone: str = "UTC") -> ScanReport:
    object_map: dict[str, list[FileScanResult]] = defaultdict(list)

    for object_folder, fits_path in _discover_candidate_paths(root_path):
        result = inspect_fits_file(fits_path, object_folder, observation_timezone=observation_timezone)
        object_map[object_folder].append(result)

    object_summaries = [
        ObjectScanSummary(object_name=name, files=sorted(results, key=_scan_sort_key))
        for name, results in sorted(object_map.items())
    ]
    return ScanReport(root_path=root_path, object_summaries=object_summaries)


def _discover_candidate_paths(root_path: Path) -> list[tuple[str, Path]]:
    files_root = root_path / "Files"
    if files_root.exists():
        discovered = _discover_from_collection_root(files_root)
        if discovered:
            return discovered

    discovered = _discover_from_collection_root(root_path)
    if discovered:
        return discovered

    direct_files = sorted(
        path for path in root_path.iterdir() if path.is_file() and is_supported_image_path(path)
    )
    return [(root_path.name, path) for path in direct_files]


def _discover_from_collection_root(collection_root: Path) -> list[tuple[str, Path]]:
    discovered: list[tuple[str, Path]] = []
    for object_dir in sorted(path for path in collection_root.iterdir() if path.is_dir()):
        for fits_path in sorted(
            path for path in object_dir.iterdir() if path.is_file() and is_supported_image_path(path)
        ):
            discovered.append((object_dir.name, fits_path))
    return discovered


def inspect_fits_file(path: Path, object_folder: str, observation_timezone: str = "UTC") -> FileScanResult:
    reasons: list[str] = []
    filename_metadata = _parse_filename_metadata(path, observation_timezone=observation_timezone)
    try:
        header, width, height = read_header_and_shape(path)
        header_date_obs_value = header.get("DATE-OBS")
        header_date_obs = _parse_date_obs(header_date_obs_value, observation_timezone=observation_timezone)
        metadata = ObservationMetadata(
            date_obs=header_date_obs,
            filter_name=_as_clean_string(header.get("FILTER")) or filename_metadata.filter_name,
            exposure_seconds=_as_float(header.get("EXPTIME")) if _as_float(header.get("EXPTIME")) is not None else filename_metadata.exposure_seconds,
            width=width,
            height=height,
            object_name=_as_clean_string(header.get("OBJECT")) or filename_metadata.object_name or object_folder,
            frame_type=_as_clean_string(header.get("IMAGETYP")) or filename_metadata.frame_type,
            binning=_extract_binning_from_header(header) or filename_metadata.binning,
        )
        solved, wcs_reasons = validate_wcs(header, path)
        reasons.extend(wcs_reasons)
        status = WcsStatus.SOLVED if solved else WcsStatus.UNSOLVED
    except Exception as exc:
        metadata = ObservationMetadata(
            date_obs=None,
            filter_name=filename_metadata.filter_name,
            exposure_seconds=filename_metadata.exposure_seconds,
            width=None,
            height=None,
            object_name=filename_metadata.object_name or object_folder,
            frame_type=filename_metadata.frame_type,
            binning=filename_metadata.binning,
        )
        status = WcsStatus.INVALID
        reasons.append(f"Failed to read image header: {exc}")

    return FileScanResult(
        path=path,
        object_folder=object_folder,
        metadata=metadata,
        wcs_status=status,
        reasons=reasons,
    )


def _scan_sort_key(result: FileScanResult) -> tuple[datetime, str]:
    date_obs = result.metadata.date_obs or _MIN_UTC_DATETIME
    return date_obs, result.path.name.lower()


def _parse_date_obs(value: object, observation_timezone: str = "UTC") -> datetime | None:
    if value is None:
        return None

    text = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return _normalize_observation_time(datetime.strptime(text, fmt), observation_timezone)
        except ValueError:
            continue

    try:
        return _normalize_observation_time(datetime.fromisoformat(text.replace("Z", "+00:00")), observation_timezone)
    except ValueError:
        return None


def parse_observation_timestamp(value: object, observation_timezone: str = "UTC") -> datetime | None:
    return _parse_date_obs(value, observation_timezone=observation_timezone)


def _as_clean_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_binning_from_header(header: object) -> str | None:
    for key in ("XBINNING", "CCDXBIN", "XBIN"):
        value = _as_int(getattr(header, "get", lambda _key: None)(key))
        if value is not None:
            return f"Bin{value}"
    return None


def _parse_filename_metadata(path: Path, observation_timezone: str = "UTC") -> ObservationMetadata:
    match = FILENAME_METADATA_PATTERN.search(path.stem)
    if not match:
        return ObservationMetadata(
            date_obs=None,
            filter_name=None,
            exposure_seconds=None,
            width=None,
            height=None,
            object_name=None,
            frame_type=None,
            binning=None,
        )

    groups = match.groupdict()
    return ObservationMetadata(
        date_obs=None,
        filter_name=_as_clean_string(groups["filter_name"]),
        exposure_seconds=_as_float(groups["exposure_seconds"]),
        width=None,
        height=None,
        object_name=_as_clean_string(groups["object_name"]),
        frame_type=_as_clean_string(groups["frame_type"].upper()),
        binning=_as_clean_string(groups["binning"]),
    )


def _date_obs_has_explicit_timezone(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, datetime):
        return value.tzinfo is not None
    text = str(value).strip()
    return bool(_DATE_OBS_TIMEZONE_PATTERN.search(text))


def date_obs_has_explicit_timezone(value: object) -> bool:
    return _date_obs_has_explicit_timezone(value)


def _normalize_observation_time(timestamp: datetime, observation_timezone: str) -> datetime:
    if timestamp.tzinfo is not None:
        return timestamp.astimezone(UTC)
    return timestamp.replace(tzinfo=UTC)
