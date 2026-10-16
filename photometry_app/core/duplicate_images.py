"""Find image files that are copies of each other. Files are only opened for reading."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path

from photometry_app.core.image_io import read_header
from photometry_app.core.observation_deck import normalize_filter_name, path_key


EXACT_COPY = "exact_copy"
SAME_EXPOSURE = "same_exposure"

_EDGE_BYTES = 64 * 1024
_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    kind: str
    paths: tuple[Path, ...]
    detail: str

    @property
    def keep(self) -> Path:
        return self.paths[0]

    @property
    def copies(self) -> tuple[Path, ...]:
        return self.paths[1:]


@dataclass(frozen=True, slots=True)
class DuplicateReport:
    groups: tuple[DuplicateGroup, ...]
    checked_files: int
    unreadable: tuple[tuple[Path, str], ...]

    @property
    def copy_count(self) -> int:
        return sum(len(group.copies) for group in self.groups)

    def groups_of(self, kind: str) -> tuple[DuplicateGroup, ...]:
        return tuple(group for group in self.groups if group.kind == kind)


def find_duplicate_images(
    paths: Iterable[Path],
    *,
    stage_of: Callable[[Path, object | None], str] | None = None,
    check_cancelled: Callable[[], None] = lambda: None,
    progress: Callable[[str], None] = lambda _message: None,
) -> DuplicateReport:
    """Group exact byte-for-byte copies, then frames that record the same exposure.

    Same-exposure frames share DATE-OBS, exposure, filter, camera, and size, but their bytes differ, for
    example a FITS sub also saved as XISF. Frames at different processing stages (a raw light and its
    calibrated version) are not grouped when ``stage_of`` tells them apart.
    """
    unique: dict[str, Path] = {}
    for path in paths:
        unique.setdefault(path_key(path), Path(path))
    files = sorted(unique.values(), key=_keep_order)
    unreadable: dict[str, tuple[Path, str]] = {}

    sizes: dict[int, list[Path]] = defaultdict(list)
    for path in files:
        check_cancelled()
        try:
            sizes[path.stat().st_size].append(path)
        except OSError as exc:
            unreadable[path_key(path)] = (path, _reason(exc))

    exact_groups: list[DuplicateGroup] = []
    copies: set[str] = set()
    candidates = [group for group in sizes.values() if len(group) > 1]
    total = sum(len(group) for group in candidates)
    done = 0
    for same_size in candidates:
        by_edges: dict[str, list[Path]] = defaultdict(list)
        for path in same_size:
            check_cancelled()
            done += 1
            if done % 200 == 0:
                progress(f"Comparing file contents ({done}/{total})")
            try:
                by_edges[_edge_digest(path)].append(path)
            except OSError as exc:
                unreadable[path_key(path)] = (path, _reason(exc))
        for same_edges in by_edges.values():
            if len(same_edges) < 2:
                continue
            by_content: dict[str, list[Path]] = defaultdict(list)
            for path in same_edges:
                try:
                    by_content[_full_digest(path, check_cancelled)].append(path)
                except OSError as exc:
                    unreadable[path_key(path)] = (path, _reason(exc))
            for same_content in by_content.values():
                if len(same_content) < 2:
                    continue
                group = tuple(sorted(same_content, key=_keep_order))
                exact_groups.append(DuplicateGroup(EXACT_COPY, group, _size_text(group[0])))
                copies.update(path_key(path) for path in group[1:])

    exposures: dict[tuple[object, ...], list[Path]] = defaultdict(list)
    details: dict[tuple[object, ...], str] = {}
    remaining = [path for path in files if path_key(path) not in copies and path_key(path) not in unreadable]
    for index, path in enumerate(remaining, start=1):
        check_cancelled()
        if index % 200 == 0:
            progress(f"Reading headers ({index}/{len(remaining)})")
        try:
            header = read_header(path)
        except Exception as exc:
            unreadable[path_key(path)] = (path, _reason(exc))
            continue
        key = _exposure_key(header)
        if key is None:
            continue
        stage = stage_of(path, header) if stage_of is not None else ""
        exposures[(*key, stage)].append(path)
        details.setdefault((*key, stage), _exposure_text(key))

    exposure_groups = [
        DuplicateGroup(SAME_EXPOSURE, tuple(sorted(group, key=_keep_order)), details[key])
        for key, group in exposures.items()
        if len(group) > 1
    ]
    groups = sorted((*exact_groups, *exposure_groups), key=lambda group: (group.kind != EXACT_COPY, _keep_order(group.keep)))
    return DuplicateReport(
        groups=tuple(groups),
        checked_files=len(files),
        unreadable=tuple(sorted(unreadable.values(), key=lambda item: _keep_order(item[0]))),
    )


def _keep_order(path: Path) -> tuple[int, str]:
    """Suggest keeping the copy nearest the top of the folder tree, then the first by name."""
    return (len(Path(path).parts), str(path).casefold())


def _edge_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        digest.update(handle.read(_EDGE_BYTES))
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        if size > _EDGE_BYTES:
            handle.seek(max(_EDGE_BYTES, size - _EDGE_BYTES))
            digest.update(handle.read(_EDGE_BYTES))
    return digest.hexdigest()


def _full_digest(path: Path, check_cancelled: Callable[[], None]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            check_cancelled()
            digest.update(chunk)
    return digest.hexdigest()


def _exposure_key(header: object) -> tuple[object, ...] | None:
    getter = getattr(header, "get", None)
    if getter is None:
        return None
    timestamp = " ".join(str(getter("DATE-OBS") or "").split()).upper()
    if len(timestamp) < 16:
        return None
    exposure = _number(getter("EXPTIME"))
    if exposure is None:
        exposure = _number(getter("EXPOSURE"))
    filter_name = normalize_filter_name(str(getter("FILTER") or "").strip() or None) or ""
    camera = " ".join(str(getter("INSTRUME") or "").split()).casefold()
    return (
        timestamp.rstrip("Z"),
        round(exposure, 3) if exposure is not None else None,
        filter_name,
        camera,
        _number(getter("NAXIS1")),
        _number(getter("NAXIS2")),
    )


def _exposure_text(key: tuple[object, ...]) -> str:
    timestamp, exposure, filter_name = key[0], key[1], key[2]
    parts = [f"DATE-OBS {timestamp}"]
    if exposure is not None:
        parts.append(f"{exposure:g} s")
    if filter_name:
        parts.append(f"filter {filter_name}")
    return ", ".join(parts)


def _number(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _size_text(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return "identical contents"
    return f"identical contents, {size / 1_048_576:.1f} MB each"


def _reason(exc: BaseException) -> str:
    return str(exc).strip() or exc.__class__.__name__
