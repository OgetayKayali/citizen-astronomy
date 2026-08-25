"""Astropy/PyVO frozen-app helpers.

PyInstaller can omit tiny package-data files. Astropy then tries its data server;
404s for optional/local package assets must not abort CAst startup.

Important: wrapping get_pkg_data_filename breaks Astropy's caller-package
detection (find_current_module). We must restore the real caller package and
resolve known local package files (especially astroquery SIMBAD criteria JSON)
before any download attempt.
"""

from __future__ import annotations

from pathlib import Path
import importlib.util
import json
import sys
import tempfile

_KNOWN_OPTIONAL_MARKERS = (
    "astropy_icon.png",
    "crossdomain.xml",
    "clientaccesspolicy.xml",
)

_VOTABLE_REFFRAME_JSON_NAME = "ivoa-vocalubary_refframe-v20220222.json"

_LOCAL_PACKAGE_DATA_FILES = {
    "query_criteria_fields.json": "astroquery.simbad",
    _VOTABLE_REFFRAME_JSON_NAME: "astropy.io.votable",
}

_VOTABLE_REFFRAME_TERMS = (
    "AZ_EL",
    "BODY",
    "ECLIPTIC",
    "EQUATORIAL",
    "FK4",
    "FK5",
    "GALACTIC",
    "GALACTIC_I",
    "GENERIC_GALACTIC",
    "ICRS",
    "SUPER_GALACTIC",
    "UNKNOWN",
    "barycentric",
    "ecl_FK4",
    "ecl_FK5",
    "eq_FK4",
    "eq_FK5",
    "galactic",
    "geo_app",
    "supergalactic",
    "xy",
)

_MIN_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)

_FALLBACK_TEXT = {
    "crossdomain.xml": (
        '<?xml version="1.0"?>\n'
        "<cross-domain-policy>\n"
        "</cross-domain-policy>\n"
    ),
    "clientaccesspolicy.xml": (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<access-policy></access-policy>\n"
    ),
    _VOTABLE_REFFRAME_JSON_NAME: json.dumps(
        {"terms": {name: {} for name in _VOTABLE_REFFRAME_TERMS}},
        separators=(",", ":"),
    ),
}


def _normalize_data_name(data_name: object) -> str:
    return str(data_name or "").replace("\\", "/")


def _basename(data_name: object) -> str:
    return Path(_normalize_data_name(data_name)).name.lower()


def _is_optional_astropy_data_failure(text: object) -> bool:
    haystack = str(text).lower()
    if ".json" in haystack:
        return False
    if any(marker in str(text) for marker in _KNOWN_OPTIONAL_MARKERS):
        return True
    return (
        "data.astropy.org" in haystack
        or "astropy-data" in haystack
        or "unable to open any source" in haystack
    )


def _json_stub_marker(data_name: object) -> str | None:
    haystack = _normalize_data_name(data_name).lower()
    for name in _FALLBACK_TEXT:
        if name.endswith(".json") and name in haystack:
            return name
    name = _basename(data_name)
    if name.endswith(".json") and name in _FALLBACK_TEXT:
        return name
    return None


def _marker_from_text(text: object) -> str:
    json_marker = _json_stub_marker(text)
    if json_marker is not None:
        return json_marker
    raw = str(text)
    for marker in _KNOWN_OPTIONAL_MARKERS:
        if marker in raw:
            return marker
    lower = raw.lower()
    if ".png" in lower:
        return "astropy_icon.png"
    if "clientaccesspolicy" in lower:
        return "clientaccesspolicy.xml"
    if ".xml" in lower:
        return "crossdomain.xml"
    return "crossdomain.xml"


def _known_optional_basename(value: object) -> str | None:
    name = _basename(value)
    return name if name in _KNOWN_OPTIONAL_MARKERS else None


def _fallback_path(marker: str) -> Path:
    directory = Path(tempfile.gettempdir()) / "citizen_astronomy_astropy_data"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / Path(marker).name
    if not path.is_file():
        if path.suffix.lower() == ".png":
            path.write_bytes(_MIN_PNG)
        else:
            path.write_text(_FALLBACK_TEXT.get(marker, ""), encoding="utf-8")
    return path


def _fallback_contents(marker: str, *, encoding: str | None) -> str | bytes:
    if marker.endswith(".png"):
        data = _MIN_PNG
        return data if encoding is None else data.decode("latin-1")
    text = _FALLBACK_TEXT.get(marker, "")
    return text if encoding is not None else text.encode("utf-8")


def _resolve_local_package_data_file(data_name: object, package: str | None = None) -> str | None:
    """Resolve packaged data without re-entering Astropy download logic."""

    normalized = _normalize_data_name(data_name)
    basename = Path(normalized).name
    package_candidates: list[str] = []
    if package:
        package_candidates.append(str(package))
    mapped_package = _LOCAL_PACKAGE_DATA_FILES.get(basename.lower())
    if mapped_package is not None:
        package_candidates.append(mapped_package)

    seen: set[str] = set()
    for package_name in package_candidates:
        if package_name in seen:
            continue
        seen.add(package_name)
        try:
            spec = importlib.util.find_spec(package_name)
        except Exception:
            continue
        if spec is None or not spec.origin:
            continue
        candidate = Path(spec.origin).resolve().parent / Path(normalized)
        if candidate.is_file():
            return str(candidate)
        # Common layout: data/<file> next to the package __init__.
        alt = Path(spec.origin).resolve().parent / "data" / basename
        if alt.is_file():
            return str(alt)
    return None


def _caller_package(explicit_package: str | None) -> str | None:
    if explicit_package:
        return explicit_package
    try:
        from astropy.utils.introspection import find_current_module

        module = find_current_module(
            1,
            finddiff=[
                "astropy.utils.data",
                "contextlib",
                "photometry_app.core.astropy_runtime",
                "pyi_rth_astropy_data_fallback",
            ],
        )
    except Exception:
        return None
    if module is None:
        return None
    package = getattr(module, "__package__", None)
    if package:
        return str(package)
    name = str(getattr(module, "__name__", "") or "")
    if "." in name:
        return name.rpartition(".")[0]
    return name or None


def install_astropy_pkgdata_fallback() -> None:
    """Install import-safe fallbacks for optional Astropy/PyVO package data."""

    try:
        from astropy.utils import data as astropy_data
    except Exception:
        return

    if getattr(astropy_data, "_citizen_astronomy_pkgdata_fallback", False):
        return

    frozen = bool(getattr(sys, "frozen", False))
    original_filename = astropy_data.get_pkg_data_filename
    original_contents = astropy_data.get_pkg_data_contents
    original_download = astropy_data.download_file

    def get_pkg_data_filename(data_name, package=None, show_progress=True, remote_timeout=None):
        normalized = _normalize_data_name(data_name)
        local_path = _resolve_local_package_data_file(normalized, package)
        if local_path is not None:
            return local_path

        resolved_package = _caller_package(package)
        marker = _known_optional_basename(normalized)
        if frozen and marker is not None:
            try:
                return original_filename(
                    normalized,
                    package=resolved_package,
                    show_progress=show_progress,
                    remote_timeout=remote_timeout,
                )
            except Exception:
                return str(_fallback_path(marker))
        try:
            return original_filename(
                normalized,
                package=resolved_package,
                show_progress=show_progress,
                remote_timeout=remote_timeout,
            )
        except Exception as exc:
            local_retry = _resolve_local_package_data_file(normalized, resolved_package)
            if local_retry is not None:
                return local_retry
            json_marker = _json_stub_marker(normalized)
            if json_marker is not None:
                return str(_fallback_path(json_marker))
            if not _is_optional_astropy_data_failure(f"{normalized} {exc}"):
                raise
            return str(_fallback_path(_marker_from_text(f"{normalized} {exc}")))

    def get_pkg_data_contents(
        data_name,
        package=None,
        encoding="utf-8",
        show_progress=True,
        remote_timeout=None,
    ):
        normalized = _normalize_data_name(data_name)
        local_path = _resolve_local_package_data_file(normalized, package)
        if local_path is not None:
            data = Path(local_path).read_bytes()
            if encoding is None or encoding == "binary":
                return data
            return data.decode(encoding)

        resolved_package = _caller_package(package)
        marker = _known_optional_basename(normalized)
        if frozen and marker is not None:
            try:
                return original_contents(
                    normalized,
                    package=resolved_package,
                    encoding=encoding,
                    show_progress=show_progress,
                    remote_timeout=remote_timeout,
                )
            except Exception:
                return _fallback_contents(marker, encoding=encoding)
        try:
            return original_contents(
                normalized,
                package=resolved_package,
                encoding=encoding,
                show_progress=show_progress,
                remote_timeout=remote_timeout,
            )
        except Exception as exc:
            local_retry = _resolve_local_package_data_file(normalized, resolved_package)
            if local_retry is not None:
                data = Path(local_retry).read_bytes()
                if encoding is None or encoding == "binary":
                    return data
                return data.decode(encoding)
            json_marker = _json_stub_marker(normalized)
            if json_marker is not None:
                return _fallback_contents(json_marker, encoding=encoding)
            if not _is_optional_astropy_data_failure(f"{normalized} {exc}"):
                raise
            return _fallback_contents(_marker_from_text(f"{normalized} {exc}"), encoding=encoding)

    def download_file(remote_url, *args, **kwargs):
        local_path = _resolve_local_package_data_file(remote_url)
        if local_path is not None:
            return local_path
        marker = _known_optional_basename(remote_url)
        if frozen and marker is not None:
            try:
                return original_download(remote_url, *args, **kwargs)
            except Exception:
                return str(_fallback_path(marker))
        try:
            return original_download(remote_url, *args, **kwargs)
        except Exception as exc:
            if _json_stub_marker(remote_url) is not None:
                return str(_fallback_path(_json_stub_marker(remote_url)))
            if not _is_optional_astropy_data_failure(f"{remote_url} {exc}"):
                raise
            return str(_fallback_path(_marker_from_text(f"{remote_url} {exc}")))

    astropy_data.get_pkg_data_filename = get_pkg_data_filename  # type: ignore[assignment]
    astropy_data.get_pkg_data_contents = get_pkg_data_contents  # type: ignore[assignment]
    astropy_data.download_file = download_file  # type: ignore[assignment]
    astropy_data._citizen_astronomy_pkgdata_fallback = True  # type: ignore[attr-defined]
