from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

from photometry_app.app_metadata import application_install_path
from photometry_app.core.settings import SkyAtlasCustomOverlayRecord, SkyAtlasCustomOverlaySurvey

SKY_ATLAS_SURVEY_DIR_NAME = "survey"
SURVEY_MANIFEST_FILE_NAME = "survey.json"
OVERLAY_IMAGE_FILE_NAME = "overlay.png"
OVERLAY_WCS_FILE_NAME = "overlay.wcs.fits"
OVERLAY_SUBDIR_NAME = "overlays"


def sky_atlas_survey_root() -> Path:
    return application_install_path() / SKY_ATLAS_SURVEY_DIR_NAME


def survey_directory(survey_id: str) -> Path:
    return sky_atlas_survey_root() / str(survey_id).strip()


def overlay_directory(survey_id: str, overlay_id: str) -> Path:
    return survey_directory(survey_id) / OVERLAY_SUBDIR_NAME / str(overlay_id).strip()


def overlay_image_relative_path(overlay_id: str) -> str:
    return f"{OVERLAY_SUBDIR_NAME}/{overlay_id}/{OVERLAY_IMAGE_FILE_NAME}"


def overlay_wcs_relative_path(overlay_id: str) -> str:
    return f"{OVERLAY_SUBDIR_NAME}/{overlay_id}/{OVERLAY_WCS_FILE_NAME}"


def _resolve_storage_path(path_value: str, survey_dir: Path) -> Path:
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (survey_dir / path).resolve()


def resolve_overlay_record_for_survey(
    survey_id: str,
    record: SkyAtlasCustomOverlayRecord,
) -> SkyAtlasCustomOverlayRecord:
    survey_dir = survey_directory(survey_id)
    return replace(
        record,
        cached_image_path=str(_resolve_storage_path(record.cached_image_path, survey_dir)),
        cached_wcs_path=str(_resolve_storage_path(record.cached_wcs_path, survey_dir)),
    )


def resolve_survey_overlay_paths(survey: SkyAtlasCustomOverlaySurvey) -> SkyAtlasCustomOverlaySurvey:
    return replace(
        survey,
        overlays=[
            resolve_overlay_record_for_survey(survey.survey_id, overlay)
            for overlay in survey.overlays
        ],
    )


def _serialize_overlay_record(record: SkyAtlasCustomOverlayRecord, survey_id: str) -> dict[str, object]:
    image_path = Path(record.cached_image_path)
    wcs_path = Path(record.cached_wcs_path)
    survey_dir = survey_directory(survey_id)
    try:
        image_value = str(image_path.resolve().relative_to(survey_dir.resolve()))
    except ValueError:
        image_value = overlay_image_relative_path(record.overlay_id)
    try:
        wcs_value = str(wcs_path.resolve().relative_to(survey_dir.resolve()))
    except ValueError:
        wcs_value = overlay_wcs_relative_path(record.overlay_id)
    return {
        "overlay_id": record.overlay_id,
        "display_name": record.display_name,
        "cached_image_path": image_value.replace("\\", "/"),
        "cached_wcs_path": wcs_value.replace("\\", "/"),
        "source_image_path": record.source_image_path,
        "width": int(record.width),
        "height": int(record.height),
    }


def _overlay_record_from_payload(
    payload: dict[str, object],
    *,
    survey_id: str,
    survey_dir: Path,
) -> SkyAtlasCustomOverlayRecord | None:
    overlay_id = str(payload.get("overlay_id") or "").strip()
    display_name = str(payload.get("display_name") or "").strip()
    cached_image_path = str(payload.get("cached_image_path") or "").strip()
    cached_wcs_path = str(payload.get("cached_wcs_path") or "").strip()
    source_image_path = str(payload.get("source_image_path") or "").strip()
    width = payload.get("width")
    height = payload.get("height")
    if not overlay_id or not display_name or not cached_image_path or not cached_wcs_path:
        if overlay_id:
            cached_image_path = cached_image_path or overlay_image_relative_path(overlay_id)
            cached_wcs_path = cached_wcs_path or overlay_wcs_relative_path(overlay_id)
        else:
            return None
    if width is None or height is None:
        image_path = _resolve_storage_path(cached_image_path, survey_dir)
        if not image_path.is_file():
            return None
        try:
            from photometry_app.core.image_io import read_header_and_shape

            _header, width, height = read_header_and_shape(image_path)
        except Exception:
            return None
    if width is None or height is None:
        return None
    return SkyAtlasCustomOverlayRecord(
        overlay_id=overlay_id,
        display_name=display_name,
        cached_image_path=str(_resolve_storage_path(cached_image_path, survey_dir)),
        cached_wcs_path=str(_resolve_storage_path(cached_wcs_path, survey_dir)),
        source_image_path=source_image_path,
        width=int(width),
        height=int(height),
    )


def _survey_from_manifest(path: Path) -> SkyAtlasCustomOverlaySurvey | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    survey_id = str(payload.get("survey_id") or path.parent.name).strip()
    name = str(payload.get("name") or survey_id).strip()
    if not survey_id or not name:
        return None
    filter_name = str(payload.get("filter_name") or "").strip()
    survey_dir = path.parent.resolve()
    overlays: list[SkyAtlasCustomOverlayRecord] = []
    raw_overlays = payload.get("overlays")
    if isinstance(raw_overlays, list):
        for item in raw_overlays:
            if not isinstance(item, dict):
                continue
            record = _overlay_record_from_payload(item, survey_id=survey_id, survey_dir=survey_dir)
            if record is not None:
                overlays.append(record)
    if not overlays:
        overlays_dir = survey_dir / OVERLAY_SUBDIR_NAME
        if overlays_dir.is_dir():
            for overlay_dir in sorted(overlays_dir.iterdir()):
                if not overlay_dir.is_dir():
                    continue
                overlay_id = overlay_dir.name
                image_path = overlay_dir / OVERLAY_IMAGE_FILE_NAME
                wcs_path = overlay_dir / OVERLAY_WCS_FILE_NAME
                if not image_path.is_file() or not wcs_path.is_file():
                    continue
                manifest_path = overlay_dir / "overlay.json"
                display_name = overlay_id
                source_image_path = ""
                width = 0
                height = 0
                if manifest_path.is_file():
                    try:
                        overlay_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                    except Exception:
                        overlay_payload = {}
                    if isinstance(overlay_payload, dict):
                        display_name = str(overlay_payload.get("display_name") or display_name).strip() or display_name
                        source_image_path = str(overlay_payload.get("source_image_path") or "").strip()
                        width = int(overlay_payload.get("width") or 0)
                        height = int(overlay_payload.get("height") or 0)
                if width <= 0 or height <= 0:
                    try:
                        from photometry_app.core.image_io import read_header_and_shape

                        _header, width, height = read_header_and_shape(image_path)
                    except Exception:
                        continue
                overlays.append(
                    SkyAtlasCustomOverlayRecord(
                        overlay_id=overlay_id,
                        display_name=display_name,
                        cached_image_path=str(image_path.resolve()),
                        cached_wcs_path=str(wcs_path.resolve()),
                        source_image_path=source_image_path,
                        width=int(width),
                        height=int(height),
                    )
                )
    return SkyAtlasCustomOverlaySurvey(
        survey_id=survey_id,
        name=name,
        filter_name=filter_name,
        overlays=overlays,
    )


def discover_surveys() -> list[SkyAtlasCustomOverlaySurvey]:
    root = sky_atlas_survey_root()
    if not root.is_dir():
        return []
    surveys: list[SkyAtlasCustomOverlaySurvey] = []
    seen_ids: set[str] = set()
    for survey_dir in sorted(root.iterdir()):
        if not survey_dir.is_dir():
            continue
        survey = _survey_from_manifest(survey_dir / SURVEY_MANIFEST_FILE_NAME)
        if survey is None:
            continue
        if survey.survey_id in seen_ids:
            continue
        seen_ids.add(survey.survey_id)
        surveys.append(resolve_survey_overlay_paths(survey))
    return surveys


def _copy_overlay_files_to_survey(
    survey_id: str,
    record: SkyAtlasCustomOverlayRecord,
) -> SkyAtlasCustomOverlayRecord:
    destination_dir = overlay_directory(survey_id, record.overlay_id)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination_image = destination_dir / OVERLAY_IMAGE_FILE_NAME
    destination_wcs = destination_dir / OVERLAY_WCS_FILE_NAME

    survey_dir = survey_directory(survey_id)
    source_image = _resolve_storage_path(record.cached_image_path, survey_dir)
    source_wcs = _resolve_storage_path(record.cached_wcs_path, survey_dir)
    if source_image.is_file() and source_image.resolve() != destination_image.resolve():
        shutil.copy2(source_image, destination_image)
    if source_wcs.is_file() and source_wcs.resolve() != destination_wcs.resolve():
        shutil.copy2(source_wcs, destination_wcs)

    overlay_manifest = {
        "overlay_id": record.overlay_id,
        "display_name": record.display_name,
        "source_image_path": record.source_image_path,
        "width": int(record.width),
        "height": int(record.height),
    }
    (destination_dir / "overlay.json").write_text(
        json.dumps(overlay_manifest, indent=2),
        encoding="utf-8",
    )

    return replace(
        record,
        cached_image_path=str(destination_image.resolve()),
        cached_wcs_path=str(destination_wcs.resolve()),
    )


def persist_survey_to_disk(survey: SkyAtlasCustomOverlaySurvey) -> SkyAtlasCustomOverlaySurvey:
    survey_dir = survey_directory(survey.survey_id)
    survey_dir.mkdir(parents=True, exist_ok=True)
    (survey_dir / OVERLAY_SUBDIR_NAME).mkdir(parents=True, exist_ok=True)

    persisted_overlays: list[SkyAtlasCustomOverlayRecord] = []
    for overlay in survey.overlays:
        persisted_overlays.append(_copy_overlay_files_to_survey(survey.survey_id, overlay))

    persisted_survey = replace(survey, overlays=persisted_overlays)
    manifest = {
        "survey_id": persisted_survey.survey_id,
        "name": persisted_survey.name,
        "filter_name": persisted_survey.filter_name,
        "overlays": [
            _serialize_overlay_record(overlay, persisted_survey.survey_id)
            for overlay in persisted_overlays
        ],
    }
    (survey_dir / SURVEY_MANIFEST_FILE_NAME).write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    return resolve_survey_overlay_paths(persisted_survey)


def sync_surveys_to_disk(
    surveys: list[SkyAtlasCustomOverlaySurvey],
    *,
    previous_survey_ids: set[str] | None = None,
) -> list[SkyAtlasCustomOverlaySurvey]:
    sky_atlas_survey_root().mkdir(parents=True, exist_ok=True)
    current_ids = {survey.survey_id for survey in surveys}
    for survey_id in previous_survey_ids or set():
        if survey_id not in current_ids:
            delete_survey_directory(survey_id)
    persisted: list[SkyAtlasCustomOverlaySurvey] = []
    for survey in surveys:
        survey_dir = survey_directory(survey.survey_id)
        previous_overlay_ids: set[str] = set()
        manifest_path = survey_dir / SURVEY_MANIFEST_FILE_NAME
        if manifest_path.is_file():
            existing = _survey_from_manifest(manifest_path)
            if existing is not None:
                previous_overlay_ids = {overlay.overlay_id for overlay in existing.overlays}
        persisted_survey = persist_survey_to_disk(survey)
        current_overlay_ids = {overlay.overlay_id for overlay in persisted_survey.overlays}
        for overlay_id in previous_overlay_ids - current_overlay_ids:
            delete_overlay_directory(persisted_survey.survey_id, overlay_id)
        persisted.append(persisted_survey)
    return persisted


def delete_overlay_directory(survey_id: str, overlay_id: str) -> None:
    overlay_dir = overlay_directory(survey_id, overlay_id)
    if overlay_dir.is_dir():
        shutil.rmtree(overlay_dir, ignore_errors=True)


def delete_survey_directory(survey_id: str) -> None:
    survey_dir = survey_directory(survey_id)
    if survey_dir.is_dir():
        shutil.rmtree(survey_dir, ignore_errors=True)


def migrate_legacy_survey_to_disk(survey: SkyAtlasCustomOverlaySurvey) -> SkyAtlasCustomOverlaySurvey:
    migrated_overlays: list[SkyAtlasCustomOverlayRecord] = []
    for overlay in survey.overlays:
        destination_dir = overlay_directory(survey.survey_id, overlay.overlay_id)
        if (destination_dir / OVERLAY_IMAGE_FILE_NAME).is_file() and (
            destination_dir / OVERLAY_WCS_FILE_NAME
        ).is_file():
            migrated_overlays.append(
                replace(
                    overlay,
                    cached_image_path=str((destination_dir / OVERLAY_IMAGE_FILE_NAME).resolve()),
                    cached_wcs_path=str((destination_dir / OVERLAY_WCS_FILE_NAME).resolve()),
                )
            )
            continue
        migrated_overlays.append(_copy_overlay_files_to_survey(survey.survey_id, overlay))
    return persist_survey_to_disk(replace(survey, overlays=migrated_overlays))
