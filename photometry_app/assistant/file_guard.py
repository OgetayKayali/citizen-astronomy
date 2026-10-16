"""The only place the assistant may touch the filesystem for writing.

Guarantees, enforced here rather than by each tool:
- files are created only inside an output folder the user approved for the current action;
- nothing is written inside a protected (source) folder, even when it sits inside the output folder;
- existing files are never opened for writing, so nothing is overwritten or truncated;
- there is no delete, move, or rename operation. A file this writer created is removed only when
  writing it failed partway, so a crash never leaves a half-written export behind.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import threading
from typing import BinaryIO


_MAX_NAME_ATTEMPTS = 999
_WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
_INVALID_NAME_CHARACTERS = set('<>:"/\\|?*') | {chr(code) for code in range(32)}


class FileGuardError(PermissionError):
    pass


def resolved_path(path: Path | str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _key(path: Path) -> str:
    return os.path.normcase(str(path))


def is_inside(child: Path, parent: Path) -> bool:
    child_key = _key(child)
    parent_key = _key(parent).rstrip("\\/")
    return child_key == parent_key or child_key.startswith(parent_key + os.sep)


def validate_file_name(name: str) -> str:
    text = str(name).strip()
    if not text or text in {".", ".."}:
        raise FileGuardError("A file name is required.")
    if any(character in _INVALID_NAME_CHARACTERS for character in text):
        raise FileGuardError(f"{text!r} is not a valid file name. Use a plain name without folders or special characters.")
    if text.endswith((".", " ")):
        raise FileGuardError(f"{text!r} is not a valid file name.")
    if text.split(".")[0].casefold() in _WINDOWS_RESERVED_NAMES:
        raise FileGuardError(f"{text!r} is a reserved name on Windows.")
    return text


def _numbered_name(name: str, attempt: int) -> str:
    if attempt <= 1:
        return name
    stem, dot, suffix = name.rpartition(".")
    if not dot or not stem:
        return f"{name} ({attempt})"
    return f"{stem} ({attempt}).{suffix}"


class GuardedFileWriter:
    def __init__(self, protected_roots: Iterable[Path | str] = ()) -> None:
        self._lock = threading.RLock()
        self._protected: list[Path] = []
        self._approved: list[Path] = []
        self._created: list[Path] = []
        for root in protected_roots:
            self.protect(root)

    @property
    def protected_roots(self) -> tuple[Path, ...]:
        with self._lock:
            return tuple(self._protected)

    @property
    def approved_folders(self) -> tuple[Path, ...]:
        with self._lock:
            return tuple(self._approved)

    @property
    def created_files(self) -> tuple[Path, ...]:
        with self._lock:
            return tuple(self._created)

    def protect(self, path: Path | str) -> Path:
        resolved = resolved_path(path)
        with self._lock:
            if not any(_key(existing) == _key(resolved) for existing in self._protected):
                self._protected.append(resolved)
        return resolved

    def is_protected(self, path: Path | str) -> bool:
        resolved = resolved_path(path)
        with self._lock:
            return any(is_inside(resolved, root) for root in self._protected)

    def check_output_folder(self, folder: Path | str) -> Path:
        resolved = resolved_path(folder)
        if not resolved.is_absolute() or resolved.parent == resolved:
            raise FileGuardError(f"{resolved} cannot be used as an output folder. Choose a specific folder.")
        if resolved.exists() and not resolved.is_dir():
            raise FileGuardError(f"{resolved} exists and is not a folder.")
        if self.is_protected(resolved):
            raise FileGuardError(
                f"{resolved} is inside a folder CAst is reading from. Choose an output folder outside your source images."
            )
        return resolved

    @contextmanager
    def approved(self, folder: Path | str) -> Iterator[Path]:
        """Allow writes into ``folder`` only for the duration of one user-approved action."""
        resolved = self.check_output_folder(folder)
        with self._lock:
            self._approved.append(resolved)
        try:
            yield resolved
        finally:
            with self._lock:
                self._approved.remove(resolved)

    def ensure_folder(self, folder: Path | str) -> Path:
        resolved = resolved_path(folder)
        self._check_writable_location(resolved)
        if resolved.exists() and not resolved.is_dir():
            raise FileGuardError(f"{resolved} exists and is not a folder.")
        try:
            resolved.mkdir(parents=True, exist_ok=True)
        except FileExistsError:
            raise FileGuardError(f"{resolved} exists and is not a folder.") from None
        return resolved

    @contextmanager
    def create_file(self, folder: Path | str, file_name: str) -> Iterator[tuple[Path, BinaryIO]]:
        """Create a brand-new file, picking ``name (2).ext`` style names when the name is taken."""
        name = validate_file_name(file_name)
        target_folder = self.ensure_folder(folder)
        path, handle = self._open_new(target_folder, name)
        completed = False
        try:
            yield path, handle
            completed = True
        finally:
            handle.close()
            if not completed:
                self._discard_own_partial_file(path)

    def write_bytes(self, folder: Path | str, file_name: str, data: bytes) -> Path:
        with self.create_file(folder, file_name) as (path, handle):
            handle.write(data)
        return path

    def write_text(self, folder: Path | str, file_name: str, text: str, *, encoding: str = "utf-8") -> Path:
        return self.write_bytes(folder, file_name, text.encode(encoding))

    def _check_writable_location(self, path: Path) -> None:
        with self._lock:
            approved = list(self._approved)
            protected = list(self._protected)
        if not any(is_inside(path, folder) for folder in approved):
            raise FileGuardError(f"Refused to write to {path}: it is not inside an output folder you approved.")
        for root in protected:
            if is_inside(path, root):
                raise FileGuardError(f"Refused to write to {path}: it is inside {root}, which CAst is reading from.")

    def _open_new(self, folder: Path, name: str) -> tuple[Path, BinaryIO]:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        for attempt in range(1, _MAX_NAME_ATTEMPTS + 1):
            candidate = folder / _numbered_name(name, attempt)
            resolved = resolved_path(candidate)
            self._check_writable_location(resolved)
            try:
                descriptor = os.open(resolved, flags, 0o666)
            except FileExistsError:
                continue
            with self._lock:
                self._created.append(resolved)
            return resolved, os.fdopen(descriptor, "wb")
        raise FileGuardError(f"Could not find a free file name for {name!r} in {folder}.")

    def _discard_own_partial_file(self, path: Path) -> None:
        with self._lock:
            owned = any(_key(created) == _key(path) for created in self._created)
            if owned:
                self._created = [created for created in self._created if _key(created) != _key(path)]
        if owned:
            try:
                os.unlink(path)
            except OSError:
                pass
