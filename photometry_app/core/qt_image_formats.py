from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from PySide6.QtCore import QCoreApplication, QLibraryInfo
from PySide6.QtGui import QImageReader


@dataclass(frozen=True, slots=True)
class QtImageFormatSupport:
    supported_formats: tuple[str, ...]
    has_tiff: bool
    has_png: bool
    has_webp: bool
    qt_plugins_path: str
    library_paths: tuple[str, ...]
    imageformats_plugin_directories: tuple[str, ...]
    imageformats_plugins: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "supported_formats": list(self.supported_formats),
            "has_tiff": self.has_tiff,
            "has_png": self.has_png,
            "has_webp": self.has_webp,
            "qt_plugins_path": self.qt_plugins_path,
            "library_paths": list(self.library_paths),
            "imageformats_plugin_directories": list(self.imageformats_plugin_directories),
            "imageformats_plugins": list(self.imageformats_plugins),
        }


def _normalize_supported_formats(values: Iterable[Any]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for value in values:
        if isinstance(value, str):
            text = value
        else:
            try:
                text = bytes(value).decode("ascii", errors="ignore")
            except Exception:
                text = str(value)
        text = text.strip().casefold()
        if text:
            normalized.add(text)
    return tuple(sorted(normalized))


def _dedupe_strings(values: Iterable[str]) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)
    return tuple(deduped)


def _imageformats_plugin_directories(plugin_roots: Iterable[str]) -> tuple[str, ...]:
    directories: list[str] = []
    for root in plugin_roots:
        root_path = Path(str(root).strip())
        if root_path.name.casefold() == "imageformats" and root_path.is_dir():
            directories.append(str(root_path))
            continue
        imageformats_path = root_path / "imageformats"
        if imageformats_path.is_dir():
            directories.append(str(imageformats_path))
    return _dedupe_strings(directories)


def _imageformats_plugins(imageformats_directories: Iterable[str]) -> tuple[str, ...]:
    plugin_names: set[str] = set()
    for directory in imageformats_directories:
        directory_path = Path(directory)
        if not directory_path.is_dir():
            continue
        for child in directory_path.iterdir():
            if child.is_file():
                plugin_names.add(child.name)
    return tuple(sorted(plugin_names))


def query_qt_image_format_support() -> QtImageFormatSupport:
    supported_formats = _normalize_supported_formats(QImageReader.supportedImageFormats())
    qt_plugins_path = str(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath) or "").strip()
    library_paths = _dedupe_strings(QCoreApplication.libraryPaths())
    plugin_roots = _dedupe_strings((qt_plugins_path, *library_paths))
    imageformats_directories = _imageformats_plugin_directories(plugin_roots)
    imageformats_plugins = _imageformats_plugins(imageformats_directories)
    return QtImageFormatSupport(
        supported_formats=supported_formats,
        has_tiff=("tif" in supported_formats or "tiff" in supported_formats),
        has_png=("png" in supported_formats),
        has_webp=("webp" in supported_formats),
        qt_plugins_path=qt_plugins_path,
        library_paths=library_paths,
        imageformats_plugin_directories=imageformats_directories,
        imageformats_plugins=imageformats_plugins,
    )


def format_qt_image_format_support_for_log(support: QtImageFormatSupport) -> str:
    supported = "|".join(support.supported_formats) if support.supported_formats else "none"
    library_paths = "|".join(support.library_paths) if support.library_paths else "none"
    plugin_directories = "|".join(support.imageformats_plugin_directories) if support.imageformats_plugin_directories else "none"
    plugins = "|".join(support.imageformats_plugins) if support.imageformats_plugins else "none"
    return (
        "qt_image_formats="
        f"supported:{supported},"
        f"has_tiff:{int(support.has_tiff)},"
        f"has_png:{int(support.has_png)},"
        f"has_webp:{int(support.has_webp)},"
        f"plugins_path:{support.qt_plugins_path or 'none'},"
        f"library_paths:{library_paths},"
        f"imageformats_dirs:{plugin_directories},"
        f"imageformats_plugins:{plugins}"
    )


def qt_image_decode_failure_reason(image_path: str | Path, *, support: QtImageFormatSupport | None = None) -> str:
    suffix = Path(image_path).suffix.strip().casefold()
    if suffix in {".tif", ".tiff"}:
        _ = support or query_qt_image_format_support()
        return "TIFF tile decode failed; Qt TIFF image plugin unavailable or decode error."
    return "Tile image decode failed; QImage.fromData returned a null image."