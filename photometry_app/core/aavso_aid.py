from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event
import csv
from io import StringIO
import math
import time
from xml.etree import ElementTree

from astropy.time import Time
import requests

from photometry_app.core.models import LightCurvePoint, LightCurveSeries
from photometry_app.core.oc_extrema import ORIGIN_AAVSO, OcSession, PhotometryImport


AID_API_BASE_URL = "https://apps.aavso.org"
AID_PHOTOMETRY_PATH = "/v2/api/observations/photometry/"
VSX_OBJECT_API_URL = "https://vsx.aavso.org/index.php"
AID_USER_AGENT = "citizen-photometry/0.1"
AID_MIN_REQUEST_INTERVAL_SECONDS = 10.0
DEFAULT_MAX_AID_OBSERVATIONS = 25000
MAX_AID_OBSERVATIONS = 50000
AID_NIGHT_GAP_DAYS = 0.35


@dataclass(frozen=True, slots=True)
class AidBandChoice:
    api_id: str
    label: str
    vsx_code: str


AID_BAND_CHOICES: tuple[AidBandChoice, ...] = (
    AidBandChoice("", "All bands", ""),
    AidBandChoice("2", "Johnson V", "V"),
    AidBandChoice("3", "Johnson B", "B"),
    AidBandChoice("7", "Johnson U", "U"),
    AidBandChoice("4", "Cousins R", "R"),
    AidBandChoice("5", "Cousins I", "I"),
    AidBandChoice("8", "Unfiltered (V zeropoint)", "CV"),
    AidBandChoice("9", "Unfiltered (R zeropoint)", "CR"),
    AidBandChoice("0", "Visual", "Vis."),
    AidBandChoice("10", "Johnson R", "RJ"),
    AidBandChoice("11", "Johnson I", "IJ"),
    AidBandChoice("40", "Sloan u", "SU"),
    AidBandChoice("41", "Sloan g", "SG"),
    AidBandChoice("42", "Sloan r", "SR"),
    AidBandChoice("43", "Sloan i", "SI"),
    AidBandChoice("29", "Sloan z", "SZ"),
)


@dataclass(frozen=True, slots=True)
class AidObsTypeChoice:
    code: str
    label: str


AID_OBSTYPE_CHOICES: tuple[AidObsTypeChoice, ...] = (
    AidObsTypeChoice("", "All types"),
    AidObsTypeChoice("CCD", "CCD"),
    AidObsTypeChoice("DSLR", "DSLR"),
    AidObsTypeChoice("PEP", "PEP"),
    AidObsTypeChoice("VIS", "Visual"),
    AidObsTypeChoice("PTG", "Photographic"),
    AidObsTypeChoice("WEB", "Webcam"),
)

AID_MTYPE_CHOICES: tuple[tuple[str, str], ...] = (
    ("STD", "Standard only"),
    ("", "All measurements"),
)


@dataclass(frozen=True, slots=True)
class AidObservation:
    jd: float
    magnitude: float
    uncertainty: float | None = None
    band: str = ""
    obstype: str = ""
    observer: str = ""
    star_name: str = ""
    auid: str = ""
    mtype: str = ""
    validation_flag: str = ""
    fainterthan: bool = False
    observation_id: str = ""


@dataclass(frozen=True, slots=True)
class AidQuery:
    star_name: str
    source_id: str = ""
    api_token: str = ""
    start_jd: float | None = None
    end_jd: float | None = None
    band: str = ""
    obstype: str = ""
    mtype: str = "STD"
    observer: str = ""
    campaign: str = ""
    exclude_fainterthan: bool = True
    skip_discrepant: bool = True
    group_by_night: bool = True
    max_observations: int = DEFAULT_MAX_AID_OBSERVATIONS


@dataclass(frozen=True, slots=True)
class AidDownloadResult:
    imported: PhotometryImport
    source: str
    fetched_count: int
    kept_count: int
    available_count: int | None = None
    truncated: bool = False
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AidFilterStep:
    name: str
    detail: str
    before: int
    removed: int
    remaining: int


@dataclass(frozen=True, slots=True)
class AidFilterReport:
    kept: list[AidObservation]
    steps: tuple[AidFilterStep, ...]
    unique_bands: tuple[str, ...]
    unique_obstypes: tuple[str, ...]
    unique_mtypes: tuple[str, ...]
    observer_counts: tuple[tuple[str, int], ...]


class AidFilterRejectedAllError(ValueError):
    def __init__(self, message: str, notes: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.notes = notes


def normalize_max_aid_observations(value: object) -> int:
    try:
        count = int(round(float(value)))
    except (TypeError, ValueError):
        count = DEFAULT_MAX_AID_OBSERVATIONS
    return max(100, min(MAX_AID_OBSERVATIONS, count))


def band_choice_for_api_id(api_id: str) -> AidBandChoice | None:
    wanted = str(api_id or "").strip()
    for choice in AID_BAND_CHOICES:
        if choice.api_id == wanted:
            return choice
    return None


def download_aid_photometry(
    query: AidQuery,
    *,
    progress_callback=None,
    cancel_event: Event | None = None,
    request_get=None,
    sleep=time.sleep,
) -> AidDownloadResult:
    star_name = " ".join(str(query.star_name or "").split())
    if not star_name:
        raise ValueError("Enter the AAVSO / VSX star name before pulling AID data.")
    if query.start_jd is not None and query.end_jd is not None and query.end_jd < query.start_jd:
        raise ValueError("End JD must be on or after start JD.")

    getter = request_get or _default_get
    token = str(query.api_token or "").strip()
    if token:
        observations, available, source = _download_official_aid(
            query,
            token=token,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
            request_get=getter,
            sleep=sleep,
        )
    else:
        observations, available, source = _download_vsx_aid(
            query,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
            request_get=getter,
        )
    fetched_count = len(observations)
    analysis = analyze_aid_filters(observations, query)
    kept = analysis.kept
    imported = aid_observations_to_import(
        kept,
        star_name=star_name,
        source_id=query.source_id,
        group_by_night=query.group_by_night,
    )
    truncated = available is not None and available > fetched_count
    if fetched_count >= normalize_max_aid_observations(query.max_observations) and (
        available is None or available > fetched_count
    ):
        truncated = True
    notes = list(format_aid_download_notes(
        query,
        source=source,
        fetched_count=fetched_count,
        available_count=available,
        truncated=truncated,
        analysis=analysis,
        imported_sessions=len(imported.sessions),
        import_notes=imported.notes,
    ))
    if not kept:
        raise AidFilterRejectedAllError(
            _empty_after_filters_message(star_name, fetched_count, analysis),
            notes=tuple(notes),
        )
    return AidDownloadResult(
        imported=PhotometryImport(
            sessions=imported.sessions,
            records=imported.records,
            notes=tuple(notes),
        ),
        source=source,
        fetched_count=fetched_count,
        kept_count=len(kept),
        available_count=available,
        truncated=truncated,
        notes=tuple(notes),
    )


def filter_aid_observations(observations: list[AidObservation], query: AidQuery) -> list[AidObservation]:
    return analyze_aid_filters(observations, query).kept


def analyze_aid_filters(observations: list[AidObservation], query: AidQuery) -> AidFilterReport:
    start_jd = query.start_jd
    end_jd = query.end_jd
    band = _normalize_band_code(band_choice_for_api_id(query.band).vsx_code if band_choice_for_api_id(query.band) else query.band)
    obstype = _normalize_obstype(query.obstype)
    mtype = str(query.mtype or "").strip().casefold()
    observer = str(query.observer or "").strip().casefold()
    remaining = list(observations)
    steps: list[AidFilterStep] = []

    def apply_step(name: str, detail: str, keep) -> None:
        nonlocal remaining
        before = len(remaining)
        kept = [item for item in remaining if keep(item)]
        steps.append(AidFilterStep(name=name, detail=detail, before=before, removed=before - len(kept), remaining=len(kept)))
        remaining = kept

    if start_jd is None and end_jd is None:
        apply_step("JD range", "not set (all dates)", lambda _item: True)
    else:
        start_label = "any" if start_jd is None else f"{start_jd:.5f}"
        end_label = "any" if end_jd is None else f"{end_jd:.5f}"
        apply_step(
            "JD range",
            f"{start_label} to {end_label}",
            lambda item: (start_jd is None or item.jd >= start_jd) and (end_jd is None or item.jd <= end_jd),
        )
    band_label = band_choice_for_api_id(query.band)
    apply_step(
        "Band",
        band_label.label if band_label is not None and band_label.api_id else (band or "all bands"),
        lambda item: not band or _normalize_band_code(item.band) == band,
    )
    obstype_label = next((choice.label for choice in AID_OBSTYPE_CHOICES if choice.code == query.obstype), query.obstype or "all types")
    apply_step(
        "Observation type",
        obstype_label if obstype else "all types",
        lambda item: not obstype or _normalize_obstype(item.obstype) == obstype,
    )
    mtype_label = next((label for value, label in AID_MTYPE_CHOICES if value == query.mtype), query.mtype or "all measurements")
    apply_step(
        "Measurement type",
        mtype_label if mtype else "all measurements",
        lambda item: (
            not mtype
            or _normalize_mtype(item.mtype) in {mtype, ""}
            or (mtype == "std" and _normalize_obstype(item.obstype) == "vis")
        ),
    )
    apply_step(
        "Observer",
        observer.upper() if observer else "all observers",
        lambda item: not observer or str(item.observer or "").strip().casefold() == observer,
    )
    apply_step(
        "Fainter-than / upper limits",
        "excluded" if query.exclude_fainterthan else "kept",
        lambda item: not (query.exclude_fainterthan and item.fainterthan),
    )
    apply_step(
        "Discrepant AID flags",
        "skipped" if query.skip_discrepant else "kept",
        lambda item: not (query.skip_discrepant and _is_discrepant(item.validation_flag)),
    )
    band_counts = _count_labels(observations, lambda item: item.band or "(blank)")
    obstype_counts = _count_labels(observations, lambda item: item.obstype or "(blank)")
    mtype_counts = _count_labels(observations, lambda item: item.mtype or "(blank)")
    observer_counts = _count_labels(observations, lambda item: item.observer or "(blank)")
    return AidFilterReport(
        kept=remaining,
        steps=tuple(steps),
        unique_bands=tuple(label for label, _count in band_counts),
        unique_obstypes=tuple(label for label, _count in obstype_counts),
        unique_mtypes=tuple(label for label, _count in mtype_counts),
        observer_counts=observer_counts,
    )


def format_aid_query_notes(query: AidQuery) -> list[str]:
    band_choice = band_choice_for_api_id(query.band)
    band_label = band_choice.label if band_choice is not None else (query.band or "All bands")
    obstype_label = next((choice.label for choice in AID_OBSTYPE_CHOICES if choice.code == query.obstype), query.obstype or "All types")
    mtype_label = next((label for value, label in AID_MTYPE_CHOICES if value == query.mtype), query.mtype or "All measurements")
    start_label = "any" if query.start_jd is None else f"{query.start_jd:.5f}"
    end_label = "any" if query.end_jd is None else f"{query.end_jd:.5f}"
    source_label = "official AAVSO API" if str(query.api_token or "").strip() else "AAVSO VSX (no token)"
    return [
        f"AID query source: {source_label}.",
        f"AID query star: {query.star_name}.",
        f"AID query JD range: {start_label} to {end_label}.",
        f"AID query band: {band_label}.",
        f"AID query observation type: {obstype_label}.",
        f"AID query measurement type: {mtype_label}.",
        f"AID query observer: {query.observer or '(all observers)'}.",
        f"AID query campaign: {query.campaign or '(none)'}.",
        f"AID query exclude fainter-than: {'yes' if query.exclude_fainterthan else 'no'}.",
        f"AID query skip discrepant flags: {'yes' if query.skip_discrepant else 'no'}.",
        f"AID query split nightly sessions: {'yes' if query.group_by_night else 'no'}.",
        f"AID query maximum observations: {normalize_max_aid_observations(query.max_observations)}.",
    ]


def format_aid_download_notes(
    query: AidQuery,
    *,
    source: str,
    fetched_count: int,
    available_count: int | None,
    truncated: bool,
    analysis: AidFilterReport,
    imported_sessions: int,
    import_notes: tuple[str, ...] = (),
) -> list[str]:
    source_label = "AAVSO API" if source == "aavso-api" else "AAVSO VSX"
    notes = list(format_aid_query_notes(query))
    available_text = str(available_count) if available_count is not None else "unknown"
    notes.append(f"AAVSO {source_label} returned {fetched_count} observation(s); catalog reported {available_text} available.")
    if truncated:
        notes.append(
            f"Download stopped at {fetched_count} of {available_count or fetched_count} AID observation(s). "
            "Narrow the JD range, band, or observation type, or raise the maximum."
        )
    notes.extend(_format_aid_value_inventory(analysis))
    for step in analysis.steps:
        notes.append(
            f"AID filter {step.name} ({step.detail}): {step.before} in, removed {step.removed}, {step.remaining} remaining."
        )
    notes.append(f"AID filters kept {len(analysis.kept)} observation(s).")
    notes.extend(import_notes)
    if analysis.kept:
        notes.append(
            f"Pulled {len(analysis.kept)} {source_label} observation(s) for {query.star_name} "
            f"into {imported_sessions} session(s)."
        )
    return notes


def _empty_after_filters_message(star_name: str, fetched_count: int, analysis: AidFilterReport) -> str:
    killers = [step for step in analysis.steps if step.removed > 0]
    if not killers:
        return (
            f"AAVSO returned {fetched_count} observation(s) for {star_name}, "
            "but none remained after the selected filters."
        )
    dominant = max(killers, key=lambda step: step.removed)
    return (
        f"AAVSO returned {fetched_count} observation(s) for {star_name}, "
        f"but none remained after the selected filters. "
        f"{dominant.name} ({dominant.detail}) removed {dominant.removed}."
    )


def _count_labels(observations: list[AidObservation], getter) -> tuple[tuple[str, int], ...]:
    counts: dict[str, int] = {}
    for item in observations:
        label = str(getter(item) or "").strip() or "(blank)"
        counts[label] = counts.get(label, 0) + 1
    return tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold())))


def _format_aid_value_inventory(analysis: AidFilterReport) -> list[str]:
    notes: list[str] = []
    if analysis.unique_bands:
        notes.append("AID returned bands: " + ", ".join(analysis.unique_bands[:12]) + _ellipsis(len(analysis.unique_bands), 12) + ".")
    if analysis.unique_obstypes:
        notes.append("AID returned observation types: " + ", ".join(analysis.unique_obstypes[:12]) + _ellipsis(len(analysis.unique_obstypes), 12) + ".")
    if analysis.unique_mtypes:
        notes.append("AID returned measurement types: " + ", ".join(analysis.unique_mtypes[:12]) + _ellipsis(len(analysis.unique_mtypes), 12) + ".")
    if analysis.observer_counts:
        top = ", ".join(f"{name} ({count})" for name, count in analysis.observer_counts[:8])
        notes.append("AID returned observers: " + top + _ellipsis(len(analysis.observer_counts), 8) + ".")
    return notes


def _ellipsis(total: int, shown: int) -> str:
    extra = total - shown
    return f", +{extra} more" if extra > 0 else ""


def aid_observations_to_import(
    observations: list[AidObservation],
    *,
    star_name: str,
    source_id: str = "",
    group_by_night: bool = True,
) -> PhotometryImport:
    if not observations:
        return PhotometryImport(sessions=(), records=(), notes=())
    groups: list[list[AidObservation]]
    if group_by_night:
        groups = _group_observations_by_night(observations)
    else:
        by_band: dict[str, list[AidObservation]] = {}
        for observation in sorted(observations, key=lambda item: (item.jd, item.band)):
            by_band.setdefault(observation.band or "-", []).append(observation)
        groups = list(by_band.values())
    sessions: list[OcSession] = []
    used_names: dict[str, int] = {}
    for group in groups:
        filter_name = group[0].band or "-"
        date_label = _session_date_label(group[0].jd)
        base_name = f"AAVSO {date_label} [{filter_name}]" if group_by_night else f"AAVSO AID [{filter_name}]"
        used_names[base_name] = used_names.get(base_name, 0) + 1
        session_name = base_name if used_names[base_name] == 1 else f"{base_name} ({used_names[base_name]})"
        points = [
            LightCurvePoint(
                observation_time=_jd_to_datetime(observation.jd),
                file_path=Path("aavso-aid"),
                differential_magnitude=observation.magnitude,
                instrumental_magnitude=None,
                flux=None,
                flux_error=None,
                standard_magnitude=observation.magnitude,
                standard_magnitude_error=observation.uncertainty,
                differential_magnitude_error=observation.uncertainty,
            )
            for observation in group
        ]
        series = LightCurveSeries(
            object_name=session_name,
            source_id=source_id,
            source_name=star_name,
            filter_name=filter_name,
            points=points,
        )
        sessions.append(
            OcSession(
                session_name=session_name,
                series=series,
                origin=ORIGIN_AAVSO,
                notes="AAVSO AID",
            )
        )
    return PhotometryImport(
        sessions=tuple(sessions),
        records=(),
        notes=(f"Imported {sum(len(session.series.points) for session in sessions)} AAVSO AID point(s).",),
    )


def parse_official_aid_page(payload: object) -> tuple[list[AidObservation], str | None, int | None]:
    if not isinstance(payload, dict):
        raise ValueError("AAVSO API returned an unexpected response.")
    raw_rows = payload.get("results")
    if not isinstance(raw_rows, list):
        raise ValueError("AAVSO API response did not include observation results.")
    observations = [observation for item in raw_rows if (observation := _observation_from_official(item)) is not None]
    next_url = payload.get("next")
    count = payload.get("count")
    available = int(count) if isinstance(count, int) else None
    return observations, str(next_url) if next_url else None, available


def parse_vsx_aid_document(text: str) -> tuple[list[AidObservation], int | None, str]:
    body = str(text or "").strip()
    if not body:
        raise ValueError("AAVSO VSX returned an empty response.")
    if body.lstrip().startswith("<"):
        return _parse_vsx_object_xml(body)
    return _parse_vsx_csv_table(body), None, ""


def _download_official_aid(
    query: AidQuery,
    *,
    token: str,
    progress_callback,
    cancel_event: Event | None,
    request_get,
    sleep,
) -> tuple[list[AidObservation], int | None, str]:
    observations: list[AidObservation] = []
    available: int | None = None
    next_url: str | None = AID_API_BASE_URL + AID_PHOTOMETRY_PATH
    params = _official_query_params(query)
    page = 0
    last_request_at = 0.0
    max_observations = normalize_max_aid_observations(query.max_observations)
    while next_url:
        _raise_if_cancelled(cancel_event)
        wait_for = AID_MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - last_request_at)
        if page > 0 and wait_for > 0:
            if progress_callback is not None:
                progress_callback(f"Waiting {wait_for:.0f}s for the AAVSO API rate limit…")
            sleep(wait_for)
        _raise_if_cancelled(cancel_event)
        page += 1
        if progress_callback is not None:
            progress_callback(f"Downloading AAVSO AID page {page} for {query.star_name}…")
        response = request_get(
            next_url,
            params=params if page == 1 else None,
            headers=_official_headers(token),
            timeout=60,
        )
        last_request_at = time.monotonic()
        _raise_for_official_status(response)
        page_rows, next_url, available = parse_official_aid_page(response.json())
        observations.extend(page_rows)
        params = None
        if len(observations) >= max_observations:
            observations = observations[:max_observations]
            break
    return observations, available, "aavso-api"


def _download_vsx_aid(
    query: AidQuery,
    *,
    progress_callback,
    cancel_event: Event | None,
    request_get,
) -> tuple[list[AidObservation], int | None, str]:
    max_observations = normalize_max_aid_observations(query.max_observations)
    collected: list[AidObservation] = []
    seen_ids: set[str] = set()
    seen_keys: set[tuple[float, str, str]] = set()
    available: int | None = None
    resolved_name = " ".join(str(query.star_name or "").split())
    start_jd = query.start_jd
    page = 0
    while len(collected) < max_observations:
        _raise_if_cancelled(cancel_event)
        page += 1
        remaining = max_observations - len(collected)
        if progress_callback is not None:
            progress_callback(f"Downloading AAVSO AID for {query.star_name} (batch {page})…")
        params = _vsx_query_params(query, start_jd=start_jd, row_limit=remaining)
        response = request_get(
            VSX_OBJECT_API_URL,
            params=params,
            headers={"User-Agent": AID_USER_AGENT},
            timeout=90,
        )
        response.raise_for_status()
        batch, batch_available, xml_name = parse_vsx_aid_document(response.text)
        if xml_name:
            resolved_name = xml_name
        if batch_available is not None:
            available = batch_available if available is None else max(available, batch_available)
        new_rows = 0
        last_jd = start_jd
        for observation in batch:
            key = (round(observation.jd, 8), observation.band, observation.observer)
            marker = observation.observation_id or f"{key[0]}|{key[1]}|{key[2]}"
            if marker in seen_ids or key in seen_keys:
                continue
            seen_ids.add(marker)
            seen_keys.add(key)
            collected.append(observation)
            new_rows += 1
            last_jd = observation.jd if last_jd is None else max(last_jd, observation.jd)
            if len(collected) >= max_observations:
                break
        if not batch or new_rows == 0:
            break
        if batch_available is not None and len(collected) >= batch_available:
            break
        if last_jd is None or (query.end_jd is not None and last_jd >= query.end_jd):
            break
        if len(batch) < remaining:
            break
        start_jd = float(last_jd) + 1.0e-8
    if not collected:
        raise ValueError(f"AAVSO VSX returned no observations for {resolved_name}.")
    return collected, available, "vsx"


def _official_query_params(query: AidQuery) -> dict[str, object]:
    params: dict[str, object] = {"target": " ".join(str(query.star_name or "").split())}
    if query.start_jd is not None:
        params["start_date"] = f"{query.start_jd:.8f}"
    if query.end_jd is not None:
        params["end_date"] = f"{query.end_jd:.8f}"
    if query.band:
        params["band"] = str(query.band)
    official_obstype = _official_obstype_code(query.obstype)
    if official_obstype:
        params["obstype"] = official_obstype
    if query.observer:
        params["observer"] = str(query.observer).strip()
    if query.campaign:
        params["obs_campaign"] = str(query.campaign).strip()
    return params


def _vsx_query_params(query: AidQuery, *, start_jd: float | None, row_limit: int) -> dict[str, object]:
    params: dict[str, object] = {
        "view": "api.object",
        "ident": " ".join(str(query.star_name or "").split()),
        "data": str(max(1, row_limit)),
    }
    if start_jd is not None:
        params["fromjd"] = f"{start_jd:.8f}"
    elif query.start_jd is not None:
        params["fromjd"] = f"{query.start_jd:.8f}"
    if query.end_jd is not None:
        params["tojd"] = f"{query.end_jd:.8f}"
    choice = band_choice_for_api_id(query.band)
    if choice is not None and choice.vsx_code:
        params["band"] = choice.vsx_code
    elif query.band:
        params["band"] = query.band
    return params


def _official_obstype_code(value: object) -> str:
    normalized = _normalize_obstype(value)
    mapping = {
        "ccd": "C",
        "dslr": "D",
        "pep": "P",
        "vis": "V",
        "ptg": "G",
        "web": "W",
    }
    return mapping.get(normalized, str(value or "").strip())


def _official_headers(token: str) -> dict[str, str]:
    return {
        "User-Agent": AID_USER_AGENT,
        "Authorization": f"Token {token}",
        "Accept": "application/json",
    }


def _raise_for_official_status(response) -> None:
    status = int(getattr(response, "status_code", 0) or 0)
    if status in {401, 403}:
        raise ValueError("AAVSO API token was rejected. Check Settings → Science Export.")
    if status == 429:
        raise ValueError("AAVSO API rate limit exceeded. Wait and try a narrower JD range.")
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise ValueError(f"AAVSO API request failed: {exc}") from exc


def _observation_from_official(item: object) -> AidObservation | None:
    if not isinstance(item, dict):
        return None
    jd = _optional_float(item.get("jd_dbl"))
    magnitude = _parse_magnitude(item.get("magnitude"))
    if jd is None or magnitude is None:
        return None
    return AidObservation(
        jd=jd,
        magnitude=magnitude,
        uncertainty=_optional_float(item.get("uncertainty")),
        band=str(item.get("band") or "").strip(),
        obstype=str(item.get("obstype") or "").strip(),
        observer=str(item.get("obscode") or "").strip(),
        star_name=str(item.get("name") or "").strip(),
        auid=str(item.get("auid") or "").strip(),
        fainterthan=bool(item.get("fainterthan")),
        observation_id=str(item.get("id") or "").strip(),
    )


def _parse_vsx_object_xml(text: str) -> tuple[list[AidObservation], int | None, str]:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise ValueError("AAVSO VSX returned unreadable XML.") from exc
    if _local_name(root.tag) != "VSXObject":
        raise ValueError("AAVSO VSX did not recognize that star name.")
    star_name = _xml_child_text(root, "Name")
    data = next((child for child in list(root) if _local_name(child.tag) == "Data"), None)
    if data is None:
        return [], 0, star_name
    observations: list[AidObservation] = []
    available: int | None = None
    has_observation_nodes = False
    for child in list(data):
        local = _local_name(child.tag)
        if local == "Observation":
            has_observation_nodes = True
            observation = _observation_from_vsx_xml(child, star_name)
            if observation is not None:
                observations.append(observation)
        elif local == "Count":
            available = _optional_int(child.text)
    if not has_observation_nodes and data.text and "," in data.text:
        observations = _parse_vsx_csv_table(data.text)
    return observations, available, star_name


def _observation_from_vsx_xml(node: ElementTree.Element, star_name: str) -> AidObservation | None:
    values = {_local_name(child.tag).casefold(): (child.text or "").strip() for child in list(node)}
    jd = _optional_float(values.get("jd"))
    magnitude = _parse_magnitude(values.get("mag"))
    if jd is None or magnitude is None:
        return None
    fainter = values.get("fainterthan") or values.get("fainter_than") or ""
    return AidObservation(
        jd=jd,
        magnitude=magnitude,
        uncertainty=_optional_float(values.get("uncertainty") or values.get("uncert")),
        band=str(values.get("band") or "").strip(),
        obstype=str(values.get("obstype") or values.get("obs_type") or "").strip(),
        observer=str(values.get("obscode") or values.get("by") or "").strip(),
        star_name=str(values.get("name") or star_name).strip(),
        auid="",
        mtype=str(values.get("mtype") or "").strip(),
        validation_flag=str(values.get("valflag") or values.get("val") or "").strip(),
        fainterthan=_is_truthy(fainter) or str(values.get("mag") or "").startswith("<"),
        observation_id=str(values.get("id") or "").strip(),
    )


def _parse_vsx_csv_table(text: str) -> list[AidObservation]:
    lines = [line for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return []
    reader = csv.DictReader(StringIO("\n".join(lines)))
    observations: list[AidObservation] = []
    for row in reader:
        normalized = {_normalize_header(key): ("" if value is None else str(value).strip()) for key, value in row.items()}
        jd = _optional_float(normalized.get("jd") or normalized.get("hjd"))
        magnitude = _parse_magnitude(normalized.get("mag") or normalized.get("magnitude"))
        if jd is None or magnitude is None:
            continue
        observations.append(
            AidObservation(
                jd=jd,
                magnitude=magnitude,
                uncertainty=_optional_float(normalized.get("uncert") or normalized.get("uncertainty")),
                band=normalized.get("band") or "",
                obstype=normalized.get("obstype") or normalized.get("obs_type") or "",
                observer=normalized.get("by") or normalized.get("obscode") or normalized.get("observer_code") or "",
                star_name=normalized.get("starname") or normalized.get("name") or "",
                mtype=normalized.get("mtype") or "",
                validation_flag=normalized.get("val") or normalized.get("valflag") or "",
                fainterthan=_is_truthy(normalized.get("fainterthan")) or str(normalized.get("mag") or "").startswith("<"),
                observation_id=normalized.get("obsid") or normalized.get("id") or "",
            )
        )
    return observations


def _group_observations_by_night(observations: list[AidObservation]) -> list[list[AidObservation]]:
    ordered = sorted(observations, key=lambda item: (item.band, item.jd))
    groups: list[list[AidObservation]] = []
    current: list[AidObservation] = []
    for observation in ordered:
        if not current:
            current = [observation]
            continue
        previous = current[-1]
        if observation.band != previous.band or (observation.jd - previous.jd) > AID_NIGHT_GAP_DAYS:
            groups.append(current)
            current = [observation]
            continue
        current.append(observation)
    if current:
        groups.append(current)
    return groups


def _session_date_label(jd: float) -> str:
    try:
        return Time(jd, format="jd").to_value("iso", subfmt="date")
    except Exception:
        return f"JD{int(jd)}"


def _jd_to_datetime(jd: float) -> datetime:
    return Time(jd, format="jd").to_datetime()


def _default_get(url: str, **kwargs):
    return requests.get(url, **kwargs)


def _raise_if_cancelled(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ValueError("AAVSO AID download was cancelled.")


def _xml_child_text(node: ElementTree.Element, name: str) -> str:
    wanted = name.casefold()
    for child in list(node):
        if _local_name(child.tag).casefold() == wanted:
            return (child.text or "").strip()
    return ""


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _optional_float(value: object) -> float | None:
    text = str(value or "").strip().replace("<", "")
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _optional_int(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _parse_magnitude(value: object) -> float | None:
    return _optional_float(value)


def _normalize_header(value: object) -> str:
    return "".join(ch for ch in str(value or "").strip().casefold() if ch.isalnum())


def _normalize_band_code(value: object) -> str:
    text = str(value or "").strip().casefold().replace(" ", "")
    if text in {"vis", "vis.", "visual"}:
        return "vis."
    return text


def _normalize_obstype(value: object) -> str:
    text = str(value or "").strip().casefold()
    aliases = {
        "c": "ccd",
        "ccd": "ccd",
        "d": "dslr",
        "dslr": "dslr",
        "p": "pep",
        "pep": "pep",
        "v": "vis",
        "vis": "vis",
        "visual": "vis",
        "g": "ptg",
        "ptg": "ptg",
        "photographic": "ptg",
        "w": "web",
        "web": "web",
        "webcam": "web",
    }
    return aliases.get(text, text)


def _normalize_mtype(value: object) -> str:
    text = str(value or "").strip().casefold()
    if text in {"std", "standard"}:
        return "std"
    if text in {"dif", "diff", "differential"}:
        return "dif"
    return text


def _is_discrepant(flag: object) -> bool:
    text = str(flag or "").strip().casefold()
    return text in {"t", "d", "x"}


def _is_truthy(value: object) -> bool:
    text = str(value or "").strip().casefold()
    return text in {"1", "true", "yes", "y", "t"}
