"""Read-only tools for looking at the user's image folders, headers, and WCS."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator
import math
import os
from pathlib import Path

from astropy.wcs.utils import proj_plane_pixel_scales

from photometry_app.assistant.sky_objects import SkyObjectCatalog, sky_object_catalog
from photometry_app.assistant.tools import ActionPlan, ToolAccess, ToolContext, ToolDefinition, ToolFailure
from photometry_app.core.image_io import is_supported_image_path, read_header_and_shape
from photometry_app.core.wcs import celestial_wcs, validate_wcs


SCAN_LIMIT = 20_000
_MAX_OBJECTS_PER_IMAGE = 8
_MAX_ALL_KEYWORDS = 300
_HEADER_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("object", ("OBJECT",)),
    ("date_obs", ("DATE-OBS", "DATE-LOC")),
    ("exposure_s", ("EXPTIME", "EXPOSURE")),
    ("filter", ("FILTER",)),
    ("frame_type", ("IMAGETYP", "FRAME")),
    ("telescope", ("TELESCOP",)),
    ("camera", ("INSTRUME",)),
    ("binning", ("XBINNING",)),
    ("gain", ("GAIN",)),
    ("sensor_temp_c", ("CCD-TEMP",)),
    ("focal_length_mm", ("FOCALLEN",)),
    ("pixel_size_um", ("XPIXSZ",)),
    ("pointing_ra", ("OBJCTRA", "RA")),
    ("pointing_dec", ("OBJCTDEC", "DEC")),
)


def resolve_folder(text: object) -> Path:
    folder = _absolute_path(text)
    if not folder.is_dir():
        raise ToolFailure(f"{folder} is not a folder CAst can see.")
    return folder


def resolve_image(text: object) -> Path:
    path = _absolute_path(text)
    if not path.is_file():
        raise ToolFailure(f"{path} is not a file CAst can see.")
    if not is_supported_image_path(path):
        raise ToolFailure(f"{path.name} is not an image type CAst reads.")
    return path


def _absolute_path(text: object) -> Path:
    raw = str(text or "").strip().strip('"')
    if not raw:
        raise ToolFailure("A path is required.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ToolFailure(f"{raw!r} is not a full path. Ask the user for the full folder path, such as D:\\Astro\\Lights.")
    return path.resolve()


def iter_image_files(folder: Path, *, recursive: bool, check_cancelled: Callable[[], None] = lambda: None) -> Iterator[Path]:
    """Supported images under folder in a stable order, skipping hidden folders."""
    if not recursive:
        for entry in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
            if entry.is_file() and is_supported_image_path(entry):
                yield entry
        return
    for directory, subdirectories, files in os.walk(folder):
        check_cancelled()
        subdirectories[:] = sorted((name for name in subdirectories if not name.startswith(".")), key=str.casefold)
        for name in sorted(files, key=str.casefold):
            path = Path(directory) / name
            if is_supported_image_path(path):
                yield path


def _relative(path: Path, folder: Path) -> str:
    try:
        return str(path.relative_to(folder)) or "."
    except ValueError:
        return str(path)


def _clean_value(value: object) -> object:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(value, 4) if isinstance(value, float) and math.isfinite(value) else value
    text = " ".join(str(value).split())
    return text[:120]


def header_fields(header: object) -> dict[str, object]:
    fields: dict[str, object] = {}
    for name, keywords in _HEADER_FIELDS:
        for keyword in keywords:
            value = header.get(keyword) if hasattr(header, "get") else None
            if value not in (None, ""):
                fields[name] = _clean_value(value)
                break
    return fields


def wcs_summary(
    header: object,
    width: int | None,
    height: int | None,
    path: Path,
    catalog: SkyObjectCatalog | None,
    *,
    object_type: str = "",
) -> dict[str, object]:
    valid, reasons = validate_wcs(header, path)
    if not valid:
        return {"valid": False, "reason": " ".join(reasons) or "No usable WCS."}
    if not width or not height:
        return {"valid": False, "reason": "The image size is unknown, so the field cannot be placed on the sky."}
    try:
        wcs = celestial_wcs(header)
        center = wcs.pixel_to_world(width / 2, height / 2)
        scale_deg = float(proj_plane_pixel_scales(wcs).mean())
    except Exception as exc:
        return {"valid": False, "reason": f"The WCS could not be evaluated: {exc}"}
    radius = max(scale_deg * math.hypot(width, height) / 2.0, 0.01)
    summary: dict[str, object] = {
        "valid": True,
        "center_ra_deg": round(float(center.ra.deg), 4),
        "center_dec_deg": round(float(center.dec.deg), 4),
        "field_deg": [round(scale_deg * width, 3), round(scale_deg * height, 3)],
        "pixel_scale_arcsec": round(scale_deg * 3600.0, 3),
    }
    if catalog is not None:
        summary["objects_in_field"] = objects_in_field(wcs, width, height, center, radius, catalog, object_type=object_type)
    return summary


def objects_in_field(wcs, width: int, height: int, center, radius: float, catalog: SkyObjectCatalog, *, object_type: str = "") -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for item, _separation in catalog.near(float(center.ra.deg), float(center.dec.deg), radius, object_type=object_type):
        try:
            x, y = wcs.all_world2pix(item.ra_deg, item.dec_deg, 0)
        except Exception:
            continue
        if 0 <= float(x) < width and 0 <= float(y) < height:
            found.append({"name": item.name, "type": item.object_type})
        if len(found) >= _MAX_OBJECTS_PER_IMAGE:
            break
    return found


def image_tools(catalog_cache_dir: Callable[[], Path | None] = lambda: None) -> tuple[ToolDefinition, ...]:
    def catalog() -> SkyObjectCatalog:
        return sky_object_catalog(catalog_cache_dir())

    def list_images(context: ToolContext, arguments: dict[str, object], _plan: ActionPlan | None) -> object:
        folder = resolve_folder(arguments["folder"])
        context.writer.protect(folder)
        recursive = bool(arguments.get("recursive", True))
        max_files = int(arguments.get("max_files", 200))
        extensions: Counter[str] = Counter()
        per_folder: Counter[str] = Counter()
        files: list[dict[str, object]] = []
        total = 0
        for path in iter_image_files(folder, recursive=recursive, check_cancelled=context.check_cancelled):
            total += 1
            extensions[path.suffix.lower()] += 1
            per_folder[_relative(path.parent, folder)] += 1
            if len(files) < max_files:
                try:
                    size_mb = round(path.stat().st_size / 1_048_576, 2)
                except OSError:
                    size_mb = None
                files.append({"file": _relative(path, folder), "size_mb": size_mb})
            if total >= SCAN_LIMIT:
                break
        result: dict[str, object] = {
            "folder": str(folder),
            "images": total,
            "by_extension": dict(sorted(extensions.items())),
            "folders": [{"folder": name, "images": count} for name, count in sorted(per_folder.items(), key=lambda item: item[0].casefold())][:200],
            "files": files,
        }
        if total > len(files):
            result["note"] = f"Only the first {len(files)} of {total} files are listed."
        if total >= SCAN_LIMIT:
            result["note"] = f"Stopped counting at {SCAN_LIMIT} images. Try a narrower folder."
        return result

    def read_header(context: ToolContext, arguments: dict[str, object], _plan: ActionPlan | None) -> object:
        path = resolve_image(arguments["path"])
        context.writer.protect(path.parent)
        try:
            header, width, height = read_header_and_shape(path)
        except Exception as exc:
            raise ToolFailure(f"CAst could not read the header of {path.name}: {exc}") from exc
        result: dict[str, object] = {"path": str(path), "width": width, "height": height, **header_fields(header)}
        result["wcs"] = wcs_summary(header, width, height, path, catalog())
        if arguments.get("all_keywords"):
            cards: list[str] = []
            skipped = 0
            for keyword, value in header.items():
                if keyword in ("COMMENT", "HISTORY", ""):
                    skipped += 1
                    continue
                if len(cards) < _MAX_ALL_KEYWORDS:
                    cards.append(f"{keyword} = {_clean_value(value)}")
            result["keywords"] = cards
            if skipped:
                result["comment_and_history_cards"] = skipped
        return result

    def summarize(context: ToolContext, arguments: dict[str, object], _plan: ActionPlan | None) -> object:
        folder = resolve_folder(arguments["folder"])
        context.writer.protect(folder)
        recursive = bool(arguments.get("recursive", True))
        max_files = int(arguments.get("max_files", 300))
        detail = str(arguments.get("detail", "folders"))
        object_type = str(arguments.get("object_type", "") or "")
        sky = catalog()
        groups: dict[str, dict[str, object]] = {}
        found: dict[str, dict[str, object]] = {}
        rows: list[dict[str, object]] = []
        read = 0
        more_files = False
        for path in iter_image_files(folder, recursive=recursive, check_cancelled=context.check_cancelled):
            if read >= max_files:
                more_files = True
                break
            context.check_cancelled()
            read += 1
            relative_folder = _relative(path.parent, folder)
            group = groups.setdefault(
                relative_folder,
                {"images": 0, "with_wcs": 0, "unreadable": 0, "objects": Counter(), "filters": Counter(), "exposure": 0.0, "dates": [], "found": Counter()},
            )
            group["images"] += 1
            row: dict[str, object] = {"file": _relative(path, folder)}
            try:
                header, width, height = read_header_and_shape(path)
            except Exception as exc:
                group["unreadable"] += 1
                row["error"] = f"Unreadable: {exc}"[:200]
                rows.append(row)
                continue
            fields = header_fields(header)
            wcs = wcs_summary(header, width, height, path, sky, object_type=object_type)
            if fields.get("object"):
                group["objects"][str(fields["object"])] += 1
            if fields.get("filter"):
                group["filters"][str(fields["filter"])] += 1
            if isinstance(fields.get("exposure_s"), (int, float)):
                group["exposure"] += float(fields["exposure_s"])
            if fields.get("date_obs"):
                group["dates"].append(str(fields["date_obs"])[:10])
            if wcs.get("valid"):
                group["with_wcs"] += 1
                for item in wcs.get("objects_in_field", ()):
                    group["found"][(item["name"], item["type"])] += 1
                    entry = found.setdefault(item["name"], {"name": item["name"], "type": item["type"], "images": 0, "folders": []})
                    entry["images"] += 1
                    if relative_folder not in entry["folders"]:
                        entry["folders"].append(relative_folder)
            if detail == "files":
                row.update({key: fields[key] for key in ("object", "filter", "exposure_s", "date_obs", "frame_type") if key in fields})
                row["size"] = f"{width}x{height}" if width and height else None
                if wcs.get("valid"):
                    row["wcs_center"] = [wcs["center_ra_deg"], wcs["center_dec_deg"]]
                    row["field_deg"] = wcs["field_deg"]
                    row["objects_in_field"] = [item["name"] for item in wcs.get("objects_in_field", ())]
                else:
                    row["wcs"] = wcs.get("reason")
                rows.append(row)
        if read == 0:
            return {"folder": str(folder), "images": 0, "note": "No supported images were found here."}

        folder_rows: list[dict[str, object]] = []
        for name, group in sorted(groups.items(), key=lambda item: item[0].casefold()):
            dates = sorted(group["dates"])
            summary: dict[str, object] = {"folder": name, "images": group["images"], "with_wcs": group["with_wcs"]}
            if group["unreadable"]:
                summary["unreadable"] = group["unreadable"]
            if group["objects"]:
                summary["object_keywords"] = [value for value, _count in group["objects"].most_common(5)]
            if group["filters"]:
                summary["filters"] = dict(group["filters"].most_common())
            if group["exposure"]:
                summary["total_exposure_s"] = round(group["exposure"], 1)
            if dates:
                summary["dates"] = dates[0] if dates[0] == dates[-1] else f"{dates[0]} to {dates[-1]}"
            if group["found"]:
                summary["objects_in_field"] = [
                    {"name": object_name, "type": object_kind, "images": count} for (object_name, object_kind), count in group["found"].most_common(10)
                ]
            folder_rows.append(summary)
        result: dict[str, object] = {
            "folder": str(folder),
            "images_read": read,
            "with_wcs": sum(group["with_wcs"] for group in groups.values()),
            "catalog_objects_found": sorted(found.values(), key=lambda item: (-int(item["images"]), str(item["name"]))),
            "folders": folder_rows,
        }
        if object_type:
            result["object_type_filter"] = object_type
        if detail == "files":
            result["files"] = rows
        else:
            errors = [row for row in rows if "error" in row]
            if errors:
                result["unreadable_files"] = errors[:20]
        if more_files:
            result["note"] = f"Read the first {read} images only. Raise max_files or pick a subfolder to read more."
        result["catalog_note"] = "Objects come from CAst's offline catalogs: Messier, globular clusters, and NGC when its cache is downloaded."
        return result

    def look_up(context: ToolContext, arguments: dict[str, object], _plan: ActionPlan | None) -> object:
        name = str(arguments["name"])
        sky = catalog()
        match = sky.look_up(name)
        if match is not None:
            return {"match": match.to_dict()}
        similar = sky.search(name)
        if similar:
            return {"match": None, "similar": [item.to_dict() for item in similar]}
        return {"match": None, "note": "Not in CAst's offline catalogs (Messier, globular clusters, cached NGC)."}

    folder_property = {"type": "string", "maxLength": 1000, "description": "Full folder path."}
    return (
        ToolDefinition(
            name="list_folder_images",
            description="List the images CAst can open in a folder: counts per subfolder and file type, and file names. Reads nothing inside the files.",
            access=ToolAccess.READ,
            run=list_images,
            parameters={
                "type": "object",
                "properties": {
                    "folder": folder_property,
                    "recursive": {"type": "boolean", "description": "Include subfolders. Default true."},
                    "max_files": {"type": "integer", "minimum": 0, "maximum": 2000, "description": "How many file names to return. Default 200."},
                },
                "required": ["folder"],
                "additionalProperties": False,
            },
            activity="Listing images",
        ),
        ToolDefinition(
            name="read_image_header",
            description=(
                "Read one image's header: object, date, exposure, filter, camera, size, and its WCS "
                "(sky center, field size, pixel scale, catalog objects inside the frame). Pixels are not read."
            ),
            access=ToolAccess.READ,
            run=read_header,
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "maxLength": 1000, "description": "Full path of the image file."},
                    "all_keywords": {"type": "boolean", "description": "Also return every header keyword. Default false."},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            activity="Reading a header",
        ),
        ToolDefinition(
            name="summarize_image_headers",
            description=(
                "Read the headers of many images in a folder and summarize them per subfolder: objects, filters, "
                "total exposure, dates, how many have a WCS, and which catalog objects (such as globular clusters) "
                "fall inside the solved frames. Use this to find which images show a kind of object."
            ),
            access=ToolAccess.READ,
            run=summarize,
            parameters={
                "type": "object",
                "properties": {
                    "folder": folder_property,
                    "recursive": {"type": "boolean", "description": "Include subfolders. Default true."},
                    "max_files": {"type": "integer", "minimum": 1, "maximum": 2000, "description": "Most images to read. Default 300."},
                    "detail": {"type": "string", "enum": ["folders", "files"], "description": "folders (default) or one row per file."},
                    "object_type": {
                        "type": "string",
                        "maxLength": 60,
                        "description": "Only report catalog objects of this type, e.g. Globular Cluster, Galaxy, Open Cluster, Nebula.",
                    },
                },
                "required": ["folder"],
                "additionalProperties": False,
            },
            activity="Reading image headers",
        ),
        ToolDefinition(
            name="look_up_sky_object",
            description="Find a deep-sky object by name (M13, NGC 6205, Hercules Cluster) in CAst's offline catalogs: type, coordinates, magnitude.",
            access=ToolAccess.READ,
            run=look_up,
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "maxLength": 100}},
                "required": ["name"],
                "additionalProperties": False,
            },
            activity="Looking up an object",
        ),
    )
