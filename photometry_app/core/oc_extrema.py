from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
import csv
import math
import re
import uuid

from astropy.time import Time
import numpy as np

from photometry_app.core.models import LightCurvePoint, LightCurveSeries
from photometry_app.core.plotting import _is_magnitude_axis, _light_curve_point_error, _light_curve_point_value


EXTREMUM_MAXIMUM = "maximum"
EXTREMUM_MINIMUM = "minimum"
ORIGIN_MEASURED = "measured"
ORIGIN_IMPORTED = "imported"
ORIGIN_AAVSO = "aavso"

_JD_HEADER_ALIASES = {
    "jd",
    "hjd",
    "bjd",
    "date",
    "julian",
    "julian_date",
    "julian_date_utc",
    "jd_max",
    "jd(max)",
    "hjd_max",
}
_JD_MIN_ALIASES = {"jd_min", "jd(min)", "hjd_min"}
_JD_MAX_ALIASES = {"jd_max", "jd(max)", "hjd_max", "jd(maximum)"}
_MAG_HEADER_ALIASES = {"mag", "magnitude", "mag_v", "reported_value", "differential_magnitude", "standard_magnitude"}
_ERR_HEADER_ALIASES = {"merr", "mag_err", "error", "err", "reported_uncertainty", "differential_magnitude_error"}
_NAME_HEADER_ALIASES = {"name", "star", "source_name"}
_OBJECT_HEADER_ALIASES = {"object", "object_name"}
_FILTER_HEADER_ALIASES = {"filt", "filter", "filter_name", "band"}
_KIND_HEADER_ALIASES = {"kind", "type", "extremum"}
_TIME_HEADER_ALIASES = {"observation_time", "observation_time_utc", "time"}


@dataclass(frozen=True, slots=True)
class ExtremumRecord:
    record_id: str
    star_name: str
    source_id: str
    session_name: str
    kind: str
    jd: float
    jd_error: float | None = None
    magnitude: float | None = None
    magnitude_error: float | None = None
    amplitude: float | None = None
    amplitude_error: float | None = None
    filter_name: str = ""
    origin: str = ORIGIN_MEASURED
    notes: str = ""


@dataclass(slots=True)
class OcStarLog:
    star_key: str
    star_name: str
    source_id: str = ""
    t0_hjd: float | None = None
    period_days: float | None = None
    oc_kind: str = EXTREMUM_MAXIMUM
    records: list[ExtremumRecord] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class OcResidual:
    record: ExtremumRecord
    epoch: int
    calculated_jd: float
    oc_days: float


@dataclass(frozen=True, slots=True)
class OcSession:
    session_name: str
    series: LightCurveSeries
    origin: str = ORIGIN_MEASURED
    notes: str = ""


@dataclass(frozen=True, slots=True)
class PhotometryImport:
    sessions: tuple[OcSession, ...]
    records: tuple[ExtremumRecord, ...]
    notes: tuple[str, ...] = ()


def apply_star_name(log: OcStarLog, star_name: str) -> OcStarLog:
    name = " ".join(str(star_name or "").split())
    if not name:
        return log
    log.star_name = name
    if any(record.star_name != name for record in log.records):
        log.records = [replace(record, star_name=name) for record in log.records]
    return log


def make_star_key(source_id: str | None, star_name: str | None) -> str:
    source = " ".join(str(source_id or "").split())
    if source:
        return source
    return " ".join(str(star_name or "").split()).casefold()


def observation_jd(observation_time: object) -> float | None:
    if observation_time is None:
        return None
    if isinstance(observation_time, (int, float)):
        value = float(observation_time)
        return value if math.isfinite(value) and value > 0 else None
    try:
        value = float(Time(observation_time).jd)
    except Exception:
        return None
    return value if math.isfinite(value) and value > 0 else None


def extract_series_samples(
    series: LightCurveSeries,
    y_axis_mode: str = "standard_magnitude",
) -> list[tuple[float, float, float | None]]:
    samples: list[tuple[float, float, float | None]] = []
    for point in series.points:
        if getattr(point, "excluded_from_analysis", False):
            continue
        jd = observation_jd(point.observation_time)
        value = _light_curve_point_value(point, y_axis_mode)
        if jd is None or value is None or not math.isfinite(float(value)):
            continue
        error = _light_curve_point_error(point, y_axis_mode)
        samples.append((float(jd), float(value), float(error) if error is not None and math.isfinite(float(error)) else None))
    samples.sort(key=lambda item: item[0])
    return samples


def mark_series_extrema(
    series: LightCurveSeries,
    *,
    y_axis_mode: str = "standard_magnitude",
    spline_smoothing: float = 0.35,
    session_name: str | None = None,
    origin: str = ORIGIN_MEASURED,
    min_separation_days: float | None = None,
) -> list[ExtremumRecord]:
    del spline_smoothing
    samples = extract_series_samples(series, y_axis_mode)
    if len(samples) < 4:
        raise ValueError("Need at least four timed points to mark extrema.")
    magnitude_axis = _is_magnitude_axis(y_axis_mode)
    jds = np.asarray([sample[0] for sample in samples], dtype=float)
    typical_cadence = _typical_cadence_days(jds)
    min_separation = _resolve_min_separation_days(min_separation_days, typical_cadence, jds)
    median_error = _median_finite([sample[2] for sample in samples])
    max_indices, min_indices = _sample_extremum_indices(
        samples,
        magnitude_axis=magnitude_axis,
        min_separation_days=min_separation,
    )
    if not max_indices:
        max_indices = [_brightest_index(samples, magnitude_axis=magnitude_axis)]
    if not min_indices:
        min_indices = [_faintest_index(samples, magnitude_axis=magnitude_axis)]
    records = [
        _extremum_from_sample(
            series,
            samples,
            index,
            kind=EXTREMUM_MAXIMUM,
            magnitude_axis=magnitude_axis,
            median_error=median_error,
            typical_cadence=typical_cadence,
            session_name=session_name,
            origin=origin,
        )
        for index in max_indices
    ]
    records.extend(
        _extremum_from_sample(
            series,
            samples,
            index,
            kind=EXTREMUM_MINIMUM,
            magnitude_axis=magnitude_axis,
            median_error=median_error,
            typical_cadence=typical_cadence,
            session_name=session_name,
            origin=origin,
        )
        for index in min_indices
    )
    return _apply_session_amplitude(records)


def mark_extremum_near_jd(
    series: LightCurveSeries,
    jd: float,
    *,
    y_axis_mode: str = "standard_magnitude",
    spline_smoothing: float = 0.35,
    session_name: str | None = None,
    origin: str = ORIGIN_MEASURED,
    min_separation_days: float | None = None,
) -> ExtremumRecord:
    records = mark_series_extrema(
        series,
        y_axis_mode=y_axis_mode,
        spline_smoothing=spline_smoothing,
        session_name=session_name,
        origin=origin,
        min_separation_days=min_separation_days,
    )
    if not records:
        raise ValueError("Could not mark an extremum near the selected point.")
    nearest = min(records, key=lambda item: abs(float(item.jd) - float(jd)))
    span = max((max(item.jd for item in records) - min(item.jd for item in records)), 0.02)
    if abs(float(nearest.jd) - float(jd)) > max(0.08, 0.35 * span):
        raise ValueError("Click closer to a peak or trough, then mark that extremum.")
    return nearest


def compute_oc_residuals(
    records: list[ExtremumRecord],
    *,
    t0_hjd: float,
    period_days: float,
    kind: str = EXTREMUM_MAXIMUM,
) -> list[OcResidual]:
    if not math.isfinite(float(t0_hjd)) or not math.isfinite(float(period_days)) or float(period_days) <= 0:
        return []
    period = float(period_days)
    t0 = float(t0_hjd)
    residuals: list[OcResidual] = []
    for record in records:
        if record.kind != kind:
            continue
        elapsed = (float(record.jd) - t0) / period
        epoch = int(round(elapsed))
        calculated = t0 + (epoch * period)
        residuals.append(
            OcResidual(
                record=record,
                epoch=epoch,
                calculated_jd=calculated,
                oc_days=float(record.jd) - calculated,
            )
        )
    residuals.sort(key=lambda item: item.record.jd)
    return residuals


def upsert_records(log: OcStarLog, records: list[ExtremumRecord]) -> OcStarLog:
    existing = {record.record_id: record for record in log.records}
    for record in records:
        replacement_id = _matching_record_id(log.records, record)
        if replacement_id is not None and replacement_id != record.record_id:
            existing.pop(replacement_id, None)
        existing[record.record_id] = record
    merged = sorted(existing.values(), key=lambda item: (item.jd, item.kind, item.session_name))
    return replace_log_records(log, merged)


def remove_records(log: OcStarLog, record_ids: list[str]) -> OcStarLog:
    rejected = set(record_ids)
    return replace_log_records(log, [record for record in log.records if record.record_id not in rejected])


def replace_log_records(log: OcStarLog, records: list[ExtremumRecord]) -> OcStarLog:
    return OcStarLog(
        star_key=log.star_key,
        star_name=log.star_name,
        source_id=log.source_id,
        t0_hjd=log.t0_hjd,
        period_days=log.period_days,
        oc_kind=log.oc_kind,
        records=list(records),
    )


def oc_log_to_payload(log: OcStarLog) -> dict[str, object]:
    return {
        "star_key": log.star_key,
        "star_name": log.star_name,
        "source_id": log.source_id,
        "t0_hjd": log.t0_hjd,
        "period_days": log.period_days,
        "oc_kind": log.oc_kind,
        "records": [extremum_to_payload(record) for record in log.records],
    }


def oc_log_from_payload(payload: object) -> OcStarLog | None:
    if not isinstance(payload, dict):
        return None
    star_key = str(payload.get("star_key") or "").strip()
    star_name = str(payload.get("star_name") or "").strip()
    if not star_key and not star_name:
        return None
    records: list[ExtremumRecord] = []
    raw_records = payload.get("records")
    if isinstance(raw_records, list):
        for item in raw_records:
            record = extremum_from_payload(item)
            if record is not None:
                records.append(record)
    return OcStarLog(
        star_key=star_key or make_star_key(str(payload.get("source_id") or ""), star_name),
        star_name=star_name or star_key,
        source_id=str(payload.get("source_id") or ""),
        t0_hjd=_optional_float(payload.get("t0_hjd")),
        period_days=_optional_float(payload.get("period_days")),
        oc_kind=_normalize_kind(payload.get("oc_kind")),
        records=records,
    )


def extremum_to_payload(record: ExtremumRecord) -> dict[str, object]:
    return {
        "record_id": record.record_id,
        "star_name": record.star_name,
        "source_id": record.source_id,
        "session_name": record.session_name,
        "kind": record.kind,
        "jd": record.jd,
        "jd_error": record.jd_error,
        "magnitude": record.magnitude,
        "magnitude_error": record.magnitude_error,
        "amplitude": record.amplitude,
        "amplitude_error": record.amplitude_error,
        "filter_name": record.filter_name,
        "origin": record.origin,
        "notes": record.notes,
    }


def extremum_from_payload(payload: object) -> ExtremumRecord | None:
    if not isinstance(payload, dict):
        return None
    jd = _optional_float(payload.get("jd"))
    kind = _normalize_kind(payload.get("kind"))
    if jd is None or kind not in {EXTREMUM_MAXIMUM, EXTREMUM_MINIMUM}:
        return None
    return ExtremumRecord(
        record_id=str(payload.get("record_id") or uuid.uuid4().hex),
        star_name=str(payload.get("star_name") or ""),
        source_id=str(payload.get("source_id") or ""),
        session_name=str(payload.get("session_name") or ""),
        kind=kind,
        jd=jd,
        jd_error=_optional_float(payload.get("jd_error")),
        magnitude=_optional_float(payload.get("magnitude")),
        magnitude_error=_optional_float(payload.get("magnitude_error")),
        amplitude=_optional_float(payload.get("amplitude")),
        amplitude_error=_optional_float(payload.get("amplitude_error")),
        filter_name=str(payload.get("filter_name") or ""),
        origin=str(payload.get("origin") or ORIGIN_IMPORTED),
        notes=str(payload.get("notes") or ""),
    )


def export_oc_log_csv(log: OcStarLog, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "star_name",
                "source_id",
                "session_name",
                "kind",
                "jd",
                "jd_error",
                "magnitude",
                "magnitude_error",
                "amplitude",
                "amplitude_error",
                "filter_name",
                "origin",
                "epoch",
                "oc_days",
            ]
        )
        residuals = {
            residual.record.record_id: residual
            for residual in compute_oc_residuals(
                log.records,
                t0_hjd=float(log.t0_hjd or 0.0),
                period_days=float(log.period_days or 0.0),
                kind=log.oc_kind,
            )
        } if log.t0_hjd and log.period_days else {}
        for record in log.records:
            residual = residuals.get(record.record_id)
            writer.writerow(
                [
                    record.star_name,
                    record.source_id,
                    record.session_name,
                    record.kind,
                    f"{record.jd:.8f}",
                    "" if record.jd_error is None else f"{record.jd_error:.8f}",
                    "" if record.magnitude is None else f"{record.magnitude:.6f}",
                    "" if record.magnitude_error is None else f"{record.magnitude_error:.6f}",
                    "" if record.amplitude is None else f"{record.amplitude:.6f}",
                    "" if record.amplitude_error is None else f"{record.amplitude_error:.6f}",
                    record.filter_name,
                    record.origin,
                    "" if residual is None else residual.epoch,
                    "" if residual is None else f"{residual.oc_days:.8f}",
                ]
            )


def import_photometry_table(
    path: Path,
    *,
    star_name: str = "",
    source_id: str = "",
) -> PhotometryImport:
    resolved = path.expanduser()
    if not resolved.is_file():
        raise ValueError(f"File not found: {resolved}")
    text = resolved.read_text(encoding="utf-8-sig")
    notes: list[str] = []
    if _looks_like_aavso_extended(text):
        sessions, extra_notes = _import_aavso_extended(text, resolved, star_name=star_name, source_id=source_id)
        return PhotometryImport(sessions=tuple(sessions), records=(), notes=tuple(extra_notes))
    rows, fieldnames = _read_csv_rows(text)
    if not rows or not fieldnames:
        raise ValueError("The selected file has no data rows.")
    header_map = {_normalize_header(name): name for name in fieldnames}
    if _is_extrema_table(header_map):
        records = _import_extrema_rows(rows, header_map, resolved, star_name=star_name, source_id=source_id)
        notes.append(f"Imported {len(records)} extremum row(s) from {resolved.name}.")
        return PhotometryImport(sessions=(), records=tuple(records), notes=tuple(notes))
    sessions = _import_photometry_rows(rows, header_map, resolved, star_name=star_name, source_id=source_id)
    if not sessions:
        raise ValueError("Could not find JD/time and magnitude columns in the selected file.")
    notes.append(f"Imported {sum(len(session.series.points) for session in sessions)} photometry point(s) from {resolved.name}.")
    return PhotometryImport(sessions=tuple(sessions), records=(), notes=tuple(notes))


def series_matches_star(series: LightCurveSeries, *, source_id: str, star_name: str) -> bool:
    if source_id and series.source_id == source_id:
        return True
    wanted = " ".join(star_name.split()).casefold()
    if not wanted:
        return False
    return " ".join(series.source_name.split()).casefold() == wanted or " ".join(series.object_name.split()).casefold() == wanted


def _resolve_min_separation_days(
    min_separation_days: float | None,
    typical_cadence: float,
    jds: np.ndarray,
) -> float:
    if min_separation_days is not None and math.isfinite(float(min_separation_days)) and float(min_separation_days) > 0:
        return float(min_separation_days)
    span = float(jds[-1] - jds[0]) if jds.size >= 2 else 0.05
    return max(typical_cadence * 8.0, min(0.03, 0.12 * span), 0.008)


def _sample_extremum_indices(
    samples: list[tuple[float, float, float | None]],
    *,
    magnitude_axis: bool,
    min_separation_days: float,
) -> tuple[list[int], list[int]]:
    half_window = max(min_separation_days * 0.45, 0.004)
    max_candidates: list[int] = []
    min_candidates: list[int] = []
    for index, (jd, value, _error) in enumerate(samples):
        nearby = [(other_jd, other_value) for other_jd, other_value, _other_error in samples if abs(other_jd - jd) <= half_window]
        if len(nearby) < 3:
            continue
        if not any(other_jd < jd for other_jd, _other_value in nearby) or not any(other_jd > jd for other_jd, _other_value in nearby):
            continue
        nearby_values = [other_value for _other_jd, other_value in nearby]
        if magnitude_axis:
            if value <= min(nearby_values) + 1.0e-12:
                max_candidates.append(index)
            if value >= max(nearby_values) - 1.0e-12:
                min_candidates.append(index)
        else:
            if value >= max(nearby_values) - 1.0e-12:
                max_candidates.append(index)
            if value <= min(nearby_values) + 1.0e-12:
                min_candidates.append(index)
    return (
        _thin_sample_extrema(samples, max_candidates, min_separation_days, prefer_bright=True, magnitude_axis=magnitude_axis),
        _thin_sample_extrema(samples, min_candidates, min_separation_days, prefer_bright=False, magnitude_axis=magnitude_axis),
    )


def _thin_sample_extrema(
    samples: list[tuple[float, float, float | None]],
    indices: list[int],
    min_separation_days: float,
    *,
    prefer_bright: bool,
    magnitude_axis: bool,
) -> list[int]:
    def sort_key(index: int) -> float:
        value = float(samples[index][1])
        return value if prefer_bright == magnitude_axis else -value

    ranked = sorted(indices, key=sort_key)
    kept: list[int] = []
    for index in ranked:
        jd = float(samples[index][0])
        if any(abs(jd - float(samples[existing][0])) < min_separation_days for existing in kept):
            continue
        kept.append(index)
    kept.sort(key=lambda index: float(samples[index][0]))
    return kept


def _brightest_index(samples: list[tuple[float, float, float | None]], *, magnitude_axis: bool) -> int:
    return min(range(len(samples)), key=lambda index: float(samples[index][1]) if magnitude_axis else -float(samples[index][1]))


def _faintest_index(samples: list[tuple[float, float, float | None]], *, magnitude_axis: bool) -> int:
    return min(range(len(samples)), key=lambda index: -float(samples[index][1]) if magnitude_axis else float(samples[index][1]))


def _refine_sample_extremum(
    samples: list[tuple[float, float, float | None]],
    index: int,
    *,
    kind: str,
    magnitude_axis: bool,
) -> tuple[float, float]:
    lo = max(0, index - 2)
    hi = min(len(samples), index + 3)
    chunk = samples[lo:hi]
    jd, magnitude, _error = samples[index]
    if len(chunk) < 3:
        return jd, magnitude
    xs = np.asarray([item[0] for item in chunk], dtype=float)
    ys = np.asarray([item[1] for item in chunk], dtype=float)
    x0 = float(xs[len(xs) // 2])
    rel = xs - x0
    try:
        quadratic, linear, constant = np.polyfit(rel, ys, 2)
    except (TypeError, ValueError, np.linalg.LinAlgError):
        return jd, magnitude
    if abs(float(quadratic)) < 1.0e-12:
        return jd, magnitude
    want_y_minimum = (kind == EXTREMUM_MAXIMUM) if magnitude_axis else (kind == EXTREMUM_MINIMUM)
    if want_y_minimum and float(quadratic) <= 0:
        return jd, magnitude
    if not want_y_minimum and float(quadratic) >= 0:
        return jd, magnitude
    vertex = -float(linear) / (2.0 * float(quadratic))
    refined_jd = x0 + vertex
    if refined_jd < float(xs[0]) or refined_jd > float(xs[-1]):
        return jd, magnitude
    refined_mag = float(quadratic) * vertex * vertex + float(linear) * vertex + float(constant)
    return refined_jd, refined_mag


def _apply_session_amplitude(records: list[ExtremumRecord]) -> list[ExtremumRecord]:
    maxima = [record for record in records if record.kind == EXTREMUM_MAXIMUM and record.magnitude is not None]
    minima = [record for record in records if record.kind == EXTREMUM_MINIMUM and record.magnitude is not None]
    if not maxima or not minima:
        return records
    brightest = min(float(record.magnitude or 0.0) for record in maxima)
    faintest = max(float(record.magnitude or 0.0) for record in minima)
    amplitude = abs(faintest - brightest)
    amplitude_error = None
    max_errors = [float(record.magnitude_error) for record in maxima if record.magnitude_error is not None]
    min_errors = [float(record.magnitude_error) for record in minima if record.magnitude_error is not None]
    if max_errors and min_errors:
        amplitude_error = math.hypot(float(np.median(max_errors)), float(np.median(min_errors)))
    return [replace(record, amplitude=amplitude, amplitude_error=amplitude_error) for record in records]


def _extremum_from_sample(
    series: LightCurveSeries,
    samples: list[tuple[float, float, float | None]],
    index: int,
    *,
    kind: str,
    magnitude_axis: bool,
    median_error: float | None,
    typical_cadence: float,
    session_name: str | None,
    origin: str,
) -> ExtremumRecord:
    jd, magnitude = _refine_sample_extremum(samples, index, kind=kind, magnitude_axis=magnitude_axis)
    nearby = [sample for sample in samples if abs(sample[0] - jd) <= max(typical_cadence * 3.0, 0.01)]
    magnitude_error = _median_finite([sample[2] for sample in nearby]) or median_error
    jd_error = max(typical_cadence / 2.0, 1.0 / 1440.0)
    return ExtremumRecord(
        record_id=uuid.uuid4().hex,
        star_name=series.source_name or series.object_name,
        source_id=series.source_id,
        session_name=session_name or series.object_name,
        kind=kind,
        jd=jd,
        jd_error=jd_error,
        magnitude=magnitude,
        magnitude_error=magnitude_error,
        filter_name=series.filter_name,
        origin=origin,
    )


def _typical_cadence_days(jds: np.ndarray) -> float:
    if jds.size < 2:
        return 5.0 / 1440.0
    deltas = np.diff(np.sort(jds))
    deltas = deltas[np.isfinite(deltas) & (deltas > 0)]
    if deltas.size == 0:
        return 5.0 / 1440.0
    return float(np.median(deltas))


def _median_finite(values: list[float | None]) -> float | None:
    finite = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not finite:
        return None
    return float(np.median(np.asarray(finite, dtype=float)))


def _matching_record_id(records: list[ExtremumRecord], incoming: ExtremumRecord) -> str | None:
    for record in records:
        if record.kind != incoming.kind:
            continue
        if record.session_name != incoming.session_name:
            continue
        if abs(record.jd - incoming.jd) <= 0.001:
            return record.record_id
    return None


def _optional_float(value: object) -> float | None:
    if value in {None, ""}:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _normalize_kind(value: object) -> str:
    text = str(value or "").strip().casefold()
    if text in {"min", "minimum", "faint", "faintest"}:
        return EXTREMUM_MINIMUM
    return EXTREMUM_MAXIMUM


def _normalize_header(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().casefold()).strip("_")


def _looks_like_aavso_extended(text: str) -> bool:
    head = "\n".join(text.splitlines()[:12]).upper()
    return "#TYPE=EXTENDED" in head or ("#DATE=JD" in head and "NAME" in head and "MAG" in head)


def _read_csv_rows(text: str) -> tuple[list[dict[str, str]], list[str]]:
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        return [], []
    sample = "\n".join(lines[:8])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(lines, dialect=dialect)
    fieldnames = [str(name) for name in (reader.fieldnames or []) if str(name).strip()]
    rows = [{str(key): "" if value is None else str(value) for key, value in row.items()} for row in reader]
    return rows, fieldnames


def _is_extrema_table(header_map: dict[str, str]) -> bool:
    keys = set(header_map)
    has_max = bool(keys & {_normalize_header(name) for name in _JD_MAX_ALIASES} or "jd" in keys and "kind" in keys)
    has_min = bool(keys & {_normalize_header(name) for name in _JD_MIN_ALIASES})
    return has_max and (has_min or "kind" in keys)


def _import_extrema_rows(
    rows: list[dict[str, str]],
    header_map: dict[str, str],
    path: Path,
    *,
    star_name: str,
    source_id: str,
) -> list[ExtremumRecord]:
    records: list[ExtremumRecord] = []
    name_key = _first_header(header_map, _NAME_HEADER_ALIASES) or _first_header(header_map, _OBJECT_HEADER_ALIASES)
    filter_key = _first_header(header_map, _FILTER_HEADER_ALIASES)
    kind_key = _first_header(header_map, _KIND_HEADER_ALIASES)
    max_key = _first_header(header_map, _JD_MAX_ALIASES) or (header_map.get("jd") if kind_key else None)
    min_key = _first_header(header_map, _JD_MIN_ALIASES)
    mag_key = _first_header(header_map, _MAG_HEADER_ALIASES)
    err_key = _first_header(header_map, _ERR_HEADER_ALIASES)
    for index, row in enumerate(rows, start=1):
        resolved_name = (row.get(name_key) if name_key else "") or star_name or path.stem
        resolved_filter = (row.get(filter_key) if filter_key else "") or ""
        if kind_key and max_key:
            jd = _optional_float(row.get(max_key))
            if jd is None:
                continue
            records.append(
                ExtremumRecord(
                    record_id=uuid.uuid4().hex,
                    star_name=str(resolved_name).strip(),
                    source_id=source_id,
                    session_name=path.stem,
                    kind=_normalize_kind(row.get(kind_key)),
                    jd=jd,
                    magnitude=_optional_float(row.get(mag_key) if mag_key else None),
                    magnitude_error=_optional_float(row.get(err_key) if err_key else None),
                    filter_name=str(resolved_filter).strip(),
                    origin=ORIGIN_IMPORTED,
                    notes=f"{path.name} row {index}",
                )
            )
            continue
        if max_key:
            jd = _optional_float(row.get(max_key))
            if jd is not None:
                records.append(
                    ExtremumRecord(
                        record_id=uuid.uuid4().hex,
                        star_name=str(resolved_name).strip(),
                        source_id=source_id,
                        session_name=path.stem,
                        kind=EXTREMUM_MAXIMUM,
                        jd=jd,
                        filter_name=str(resolved_filter).strip(),
                        origin=ORIGIN_IMPORTED,
                        notes=f"{path.name} row {index}",
                    )
                )
        if min_key:
            jd = _optional_float(row.get(min_key))
            if jd is not None:
                records.append(
                    ExtremumRecord(
                        record_id=uuid.uuid4().hex,
                        star_name=str(resolved_name).strip(),
                        source_id=source_id,
                        session_name=path.stem,
                        kind=EXTREMUM_MINIMUM,
                        jd=jd,
                        filter_name=str(resolved_filter).strip(),
                        origin=ORIGIN_IMPORTED,
                        notes=f"{path.name} row {index}",
                    )
                )
    if not records:
        raise ValueError("No JD(max)/JD(min) values were found in the extrema table.")
    return records


def _import_photometry_rows(
    rows: list[dict[str, str]],
    header_map: dict[str, str],
    path: Path,
    *,
    star_name: str,
    source_id: str,
) -> list[OcSession]:
    jd_key = _first_header(header_map, _JD_HEADER_ALIASES - {_normalize_header(name) for name in _JD_MIN_ALIASES})
    time_key = _first_header(header_map, _TIME_HEADER_ALIASES)
    mag_key = _first_header(header_map, _MAG_HEADER_ALIASES)
    err_key = _first_header(header_map, _ERR_HEADER_ALIASES)
    name_key = _first_header(header_map, _NAME_HEADER_ALIASES) or _first_header(header_map, _OBJECT_HEADER_ALIASES)
    filter_key = _first_header(header_map, _FILTER_HEADER_ALIASES)
    source_key = _first_header(header_map, {"source_id"})
    if mag_key is None or (jd_key is None and time_key is None):
        return []
    grouped: dict[tuple[str, str], list[LightCurvePoint]] = {}
    for index, row in enumerate(rows, start=1):
        magnitude = _optional_float(row.get(mag_key))
        if magnitude is None:
            continue
        jd = _optional_float(row.get(jd_key)) if jd_key else None
        observation_time = None
        if jd is not None:
            observation_time = Time(jd, format="jd").to_datetime()
        elif time_key:
            observation_time = _parse_datetime(row.get(time_key))
            if observation_time is None:
                continue
        else:
            continue
        series_name = (row.get(name_key) if name_key else "") or star_name or path.stem
        filter_name = (row.get(filter_key) if filter_key else "") or "-"
        row_source_id = (row.get(source_key) if source_key else "") or source_id
        error = _optional_float(row.get(err_key) if err_key else None)
        grouped.setdefault((str(series_name).strip(), str(filter_name).strip(), str(row_source_id).strip()), []).append(
            LightCurvePoint(
                observation_time=observation_time,
                file_path=path,
                differential_magnitude=magnitude,
                instrumental_magnitude=None,
                flux=None,
                flux_error=None,
                standard_magnitude=magnitude,
                standard_magnitude_error=error,
                differential_magnitude_error=error,
            )
        )
    sessions: list[OcSession] = []
    for (name, filter_name, row_source_id), points in grouped.items():
        points.sort(key=lambda point: point.observation_time or datetime.min)
        series = LightCurveSeries(
            object_name=path.stem,
            source_id=row_source_id or source_id,
            source_name=name or star_name or path.stem,
            filter_name=filter_name,
            points=points,
        )
        sessions.append(OcSession(session_name=f"{path.stem} [{filter_name}]", series=series, origin=ORIGIN_IMPORTED, notes=path.name))
    return sessions


def _import_aavso_extended(
    text: str,
    path: Path,
    *,
    star_name: str,
    source_id: str,
) -> tuple[list[OcSession], list[str]]:
    header = None
    data_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        body = line[1:].strip() if line.startswith("#") else line
        if header is None and "NAME" in body.upper() and "DATE" in body.upper() and "," in body:
            header = [part.strip() for part in body.split(",") if part.strip()]
            continue
        if line.startswith("#"):
            continue
        data_lines.append(line)
    if not data_lines:
        raise ValueError("AAVSO file has no observation rows.")
    if header is None:
        header = ["NAME", "DATE", "MAG", "MERR", "FILT"]
    reader = csv.DictReader(data_lines, fieldnames=header)
    rows = [{str(key): "" if value is None else str(value) for key, value in row.items()} for row in reader]
    header_map = {_normalize_header(name): name for name in header}
    sessions = _import_photometry_rows(rows, header_map, path, star_name=star_name, source_id=source_id)
    return sessions, [f"Imported AAVSO Extended photometry from {path.name}."]


def _first_header(header_map: dict[str, str], aliases: set[str]) -> str | None:
    normalized_aliases = {_normalize_header(name) for name in aliases}
    for key, original in header_map.items():
        if key in normalized_aliases:
            return original
    return None


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return Time(text).to_datetime()
    except Exception:
        return None
