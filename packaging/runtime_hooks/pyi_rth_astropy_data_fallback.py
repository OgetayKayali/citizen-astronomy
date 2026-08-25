# PyInstaller runtime hook: runs before the app script.
# Self-contained on purpose — do not import photometry_app here (can fail too early).
#
# Wrapping get_pkg_data_filename breaks Astropy caller-package detection, so this
# hook restores the real caller package and resolves local package data files
# (especially astroquery SIMBAD query_criteria_fields.json) before downloading.

from __future__ import annotations


def _install() -> None:
    try:
        from astropy.utils import data as astropy_data
    except Exception:
        return

    if getattr(astropy_data, "_citizen_astronomy_pkgdata_fallback", False):
        return

    from pathlib import Path
    import importlib.util
    import json
    import sys
    import tempfile

    frozen = bool(getattr(sys, "frozen", False))
    known_optional = (
        "astropy_icon.png",
        "crossdomain.xml",
        "clientaccesspolicy.xml",
    )
    votable_refframe_json = "ivoa-vocalubary_refframe-v20220222.json"
    local_package_files = {
        "query_criteria_fields.json": "astroquery.simbad",
        votable_refframe_json: "astropy.io.votable",
    }
    min_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    fallback_text = {
        "crossdomain.xml": '<?xml version="1.0"?>\n<cross-domain-policy>\n</cross-domain-policy>\n',
        "clientaccesspolicy.xml": '<?xml version="1.0"?>\n<access-policy></access-policy>\n',
        votable_refframe_json: json.dumps(
            {
                "terms": {
                    name: {}
                    for name in (
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
                }
            },
            separators=(",", ":"),
        ),
    }

    def normalize_data_name(data_name: object) -> str:
        return str(data_name or "").replace("\\", "/")

    def basename(data_name: object) -> str:
        return Path(normalize_data_name(data_name)).name.lower()

    def is_optional_failure(text: object) -> bool:
        hay = str(text).lower()
        if ".json" in hay:
            return False
        if any(marker in str(text) for marker in known_optional):
            return True
        return (
            "data.astropy.org" in hay
            or "astropy-data" in hay
            or "unable to open any source" in hay
        )

    def json_stub_marker(data_name: object) -> str | None:
        haystack = normalize_data_name(data_name).lower()
        for name in fallback_text:
            if name.endswith(".json") and name in haystack:
                return name
        name = basename(data_name)
        if name.endswith(".json") and name in fallback_text:
            return name
        return None

    def marker_from_text(text: object) -> str:
        json_marker = json_stub_marker(text)
        if json_marker is not None:
            return json_marker
        raw = str(text)
        for marker in known_optional:
            if marker in raw:
                return marker
        lower = raw.lower()
        if ".png" in lower:
            return "astropy_icon.png"
        if "clientaccesspolicy" in lower:
            return "clientaccesspolicy.xml"
        return "crossdomain.xml"

    def fallback_path(marker: str) -> Path:
        directory = Path(tempfile.gettempdir()) / "citizen_astronomy_astropy_data"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / Path(marker).name
        if not path.is_file():
            if path.suffix.lower() == ".png":
                path.write_bytes(min_png)
            else:
                path.write_text(fallback_text.get(marker, ""), encoding="utf-8")
        return path

    def fallback_contents(marker: str, encoding: str | None):
        if marker.endswith(".png"):
            return min_png if encoding is None else min_png.decode("latin-1")
        text = fallback_text.get(marker, "")
        return text if encoding is not None else text.encode("utf-8")

    def known_optional_basename(value: object) -> str | None:
        name = basename(value)
        return name if name in known_optional else None

    def resolve_local_package_data_file(data_name: object, package: str | None = None) -> str | None:
        normalized = normalize_data_name(data_name)
        file_name = Path(normalized).name
        package_candidates = []
        if package:
            package_candidates.append(str(package))
        mapped = local_package_files.get(file_name.lower())
        if mapped is not None:
            package_candidates.append(mapped)
        seen = set()
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
            root = Path(spec.origin).resolve().parent
            candidate = root / Path(normalized)
            if candidate.is_file():
                return str(candidate)
            alt = root / "data" / file_name
            if alt.is_file():
                return str(alt)
        return None

    def caller_package(explicit_package: str | None) -> str | None:
        if explicit_package:
            return explicit_package
        try:
            from astropy.utils.introspection import find_current_module

            module = find_current_module(
                1,
                finddiff=[
                    "astropy.utils.data",
                    "contextlib",
                    "pyi_rth_astropy_data_fallback",
                    "photometry_app.core.astropy_runtime",
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

    original_filename = astropy_data.get_pkg_data_filename
    original_contents = astropy_data.get_pkg_data_contents
    original_download = astropy_data.download_file

    def get_pkg_data_filename(data_name, package=None, show_progress=True, remote_timeout=None):
        normalized = normalize_data_name(data_name)
        local_path = resolve_local_package_data_file(normalized, package)
        if local_path is not None:
            return local_path
        resolved_package = caller_package(package)
        marker = known_optional_basename(normalized)
        if frozen and marker is not None:
            try:
                return original_filename(
                    normalized,
                    package=resolved_package,
                    show_progress=show_progress,
                    remote_timeout=remote_timeout,
                )
            except Exception:
                return str(fallback_path(marker))
        try:
            return original_filename(
                normalized,
                package=resolved_package,
                show_progress=show_progress,
                remote_timeout=remote_timeout,
            )
        except Exception as exc:
            local_retry = resolve_local_package_data_file(normalized, resolved_package)
            if local_retry is not None:
                return local_retry
            json_marker = json_stub_marker(normalized)
            if json_marker is not None:
                return str(fallback_path(json_marker))
            detail = f"{normalized} {exc}"
            if not is_optional_failure(detail):
                raise
            return str(fallback_path(marker_from_text(detail)))

    def get_pkg_data_contents(
        data_name,
        package=None,
        encoding="utf-8",
        show_progress=True,
        remote_timeout=None,
    ):
        normalized = normalize_data_name(data_name)
        local_path = resolve_local_package_data_file(normalized, package)
        if local_path is not None:
            data = Path(local_path).read_bytes()
            if encoding is None or encoding == "binary":
                return data
            return data.decode(encoding)
        resolved_package = caller_package(package)
        marker = known_optional_basename(normalized)
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
                return fallback_contents(marker, encoding)
        try:
            return original_contents(
                normalized,
                package=resolved_package,
                encoding=encoding,
                show_progress=show_progress,
                remote_timeout=remote_timeout,
            )
        except Exception as exc:
            local_retry = resolve_local_package_data_file(normalized, resolved_package)
            if local_retry is not None:
                data = Path(local_retry).read_bytes()
                if encoding is None or encoding == "binary":
                    return data
                return data.decode(encoding)
            json_marker = json_stub_marker(normalized)
            if json_marker is not None:
                return fallback_contents(json_marker, encoding)
            detail = f"{normalized} {exc}"
            if not is_optional_failure(detail):
                raise
            return fallback_contents(marker_from_text(detail), encoding)

    def download_file(remote_url, *args, **kwargs):
        local_path = resolve_local_package_data_file(remote_url)
        if local_path is not None:
            return local_path
        marker = known_optional_basename(remote_url)
        if frozen and marker is not None:
            try:
                return original_download(remote_url, *args, **kwargs)
            except Exception:
                return str(fallback_path(marker))
        try:
            return original_download(remote_url, *args, **kwargs)
        except Exception as exc:
            json_marker = json_stub_marker(remote_url)
            if json_marker is not None:
                return str(fallback_path(json_marker))
            detail = f"{remote_url} {exc}"
            if not is_optional_failure(detail):
                raise
            return str(fallback_path(marker_from_text(detail)))

    astropy_data.get_pkg_data_filename = get_pkg_data_filename
    astropy_data.get_pkg_data_contents = get_pkg_data_contents
    astropy_data.download_file = download_file
    astropy_data._citizen_astronomy_pkgdata_fallback = True


try:
    _install()
except Exception:
    pass
