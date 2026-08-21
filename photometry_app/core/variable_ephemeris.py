from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone, tzinfo
import math
import re
from xml.etree import ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_body, get_sun
from astropy.time import Time
from astropy import units as u
from astropy.utils import iers
from astroquery.simbad import Simbad
from astroquery.vizier import Vizier
import numpy as np
import requests

from photometry_app.core.settings import (
    DEFAULT_EPHEMERIS_MIN_ALTITUDE_DEG,
    normalize_ephemeris_min_altitude_deg,
)


_VSX_OBJECT_API_URL = "https://vsx.aavso.org/index.php"
_VSX_USER_AGENT = "citizen-photometry/0.1"
_VSX_CATALOG = "B/vsx/vsx"
_DEFAULT_MIN_ALTITUDE_DEG = DEFAULT_EPHEMERIS_MIN_ALTITUDE_DEG
_CIVIL_TWILIGHT_ALTITUDE_DEG = -6.0
_MAX_EVENTS = 48
_LOOKBACK_DAYS = 2.0
_HORIZON_DAYS = 4.0
_SIMBAD_CONE_ARCSEC = 30.0

_ECLIPSING_TYPE_TOKENS = frozenset(
    {
        "E",
        "EA",
        "EB",
        "EW",
        "EC",
        "ED",
        "ELL",
        "EP",
        "D",
        "DM",
        "DS",
        "DW",
        "K",
        "KE",
        "KW",
        "SD",
    }
)


class VariableEphemerisLookupError(LookupError):
    """Raised when a variable-star name cannot be resolved to a VSX object."""


@dataclass(frozen=True, slots=True)
class VsxEphemerisStar:
    name: str
    oid: str
    ra_deg: float
    dec_deg: float
    variability_type: str
    period_days: float | None
    epoch_hjd: float | None
    max_mag: float | None
    min_mag: float | None
    eclipse_duration_hours: float | None
    source: str


@dataclass(frozen=True, slots=True)
class VariableEphemerisEvent:
    kind: str
    cycle: int
    utc: datetime
    local: datetime
    altitude_deg: float | None
    sun_altitude_deg: float | None
    is_night: bool
    is_up: bool
    observable: bool
    window_start_local: datetime | None = None
    window_end_local: datetime | None = None
    window_observable: bool | None = None


@dataclass(frozen=True, slots=True)
class VariableEphemerisForecast:
    star: VsxEphemerisStar
    timezone_name: str
    site_configured: bool
    current_phase: float | None
    events: list[VariableEphemerisEvent]
    tonight_events: list[VariableEphemerisEvent]
    tonight_observable: list[VariableEphemerisEvent]
    night_start_local: datetime | None
    night_end_local: datetime | None
    summary: str
    tonight_schedule: TonightSchedule | None = None


@dataclass(frozen=True, slots=True)
class TwilightMark:
    name: str
    local: datetime


@dataclass(frozen=True, slots=True)
class TonightScheduleSample:
    local: datetime
    sun_altitude_deg: float
    moon_altitude_deg: float | None = None
    star_altitude_deg: float | None = None


@dataclass(frozen=True, slots=True)
class TonightSchedule:
    timezone_name: str
    latitude_deg: float
    longitude_deg: float
    start_local: datetime
    end_local: datetime
    moon_illumination_percent: float | None
    samples: tuple[TonightScheduleSample, ...]
    marks: tuple[TwilightMark, ...]
    dark_start_local: datetime | None
    dark_end_local: datetime | None


def lookup_vsx_star_by_name(name: str, timeout_seconds: float = 15.0) -> VsxEphemerisStar:
    query = " ".join(str(name).split())
    if not query:
        raise VariableEphemerisLookupError("Enter a variable-star name to search VSX.")

    timeout = max(1.0, float(timeout_seconds))
    star = _lookup_vsx_object_api(query, timeout)
    if star is not None and star.period_days is not None and star.epoch_hjd is not None:
        return star

    vizier_star = _lookup_vsx_vizier_by_name(query, timeout)
    if vizier_star is not None:
        return _merge_vsx_stars(star, vizier_star) if star is not None else vizier_star

    simbad_star = _lookup_vsx_via_simbad(query, timeout)
    if simbad_star is not None:
        return _merge_vsx_stars(star, simbad_star) if star is not None else simbad_star

    if star is not None:
        return star
    raise VariableEphemerisLookupError(f"No VSX match for '{query}'.")


def build_variable_ephemeris_forecast(
    star_name: str,
    *,
    timezone_name: str = "UTC",
    latitude_deg: float | None = None,
    longitude_deg: float | None = None,
    elevation_m: float | None = None,
    now: datetime | None = None,
    timeout_seconds: float = 15.0,
    min_altitude_deg: float = _DEFAULT_MIN_ALTITUDE_DEG,
) -> VariableEphemerisForecast:
    star = lookup_vsx_star_by_name(star_name, timeout_seconds=timeout_seconds)
    return compute_variable_ephemeris(
        star,
        timezone_name=timezone_name,
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
        elevation_m=elevation_m,
        now=now,
        min_altitude_deg=min_altitude_deg,
    )


def compute_variable_ephemeris(
    star: VsxEphemerisStar,
    *,
    timezone_name: str = "UTC",
    latitude_deg: float | None = None,
    longitude_deg: float | None = None,
    elevation_m: float | None = None,
    now: datetime | None = None,
    min_altitude_deg: float = _DEFAULT_MIN_ALTITUDE_DEG,
) -> VariableEphemerisForecast:
    zone = _resolve_timezone(timezone_name)
    now_utc = _as_utc(now if now is not None else datetime.now(UTC))
    now_local = now_utc.astimezone(zone)
    location = _observer_location(latitude_deg, longitude_deg, elevation_m)
    site_configured = location is not None
    resolved_timezone_name = _timezone_label(zone, timezone_name)
    min_altitude_deg = normalize_ephemeris_min_altitude_deg(min_altitude_deg)
    night_start, night_end = _local_night_window(now_local, zone, location)
    current_phase = current_ephemeris_phase(star, now_utc)
    events: list[VariableEphemerisEvent] = []
    if star.period_days is not None and star.period_days > 0 and star.epoch_hjd is not None:
        events = _upcoming_events(
            star,
            now_utc=now_utc,
            zone=zone,
            location=location,
            min_altitude_deg=min_altitude_deg,
        )
    tonight_events = [
        event
        for event in events
        if night_start is not None
        and night_end is not None
        and _event_overlaps_interval(event, night_start, night_end)
    ]
    tonight_observable = [event for event in tonight_events if _event_is_observable(event)]
    summary = _forecast_summary(
        star,
        timezone_name=resolved_timezone_name,
        site_configured=site_configured,
        current_phase=current_phase,
        events=events,
        tonight_events=tonight_events,
        tonight_observable=tonight_observable,
        night_start=night_start,
        night_end=night_end,
        now_utc=now_utc,
    )
    return VariableEphemerisForecast(
        star=star,
        timezone_name=resolved_timezone_name,
        site_configured=site_configured,
        current_phase=current_phase,
        events=events,
        tonight_events=tonight_events,
        tonight_observable=tonight_observable,
        night_start_local=night_start,
        night_end_local=night_end,
        summary=summary,
        tonight_schedule=compute_site_tonight_schedule(
            timezone_name=timezone_name,
            latitude_deg=latitude_deg,
            longitude_deg=longitude_deg,
            elevation_m=elevation_m,
            now=now_utc,
            star=star,
        ),
    )


def current_ephemeris_phase(star: VsxEphemerisStar, now: datetime) -> float | None:
    if star.period_days is None or star.period_days <= 0 or star.epoch_hjd is None:
        return None
    now_utc = _as_utc(now)
    elapsed_days = _datetime_to_jd(now_utc) - float(star.epoch_hjd)
    return (elapsed_days / float(star.period_days)) % 1.0


def event_kinds_for_variability_type(variability_type: str) -> tuple[tuple[str, float], ...]:
    if _is_eclipsing_type(variability_type):
        return (("Min I", 0.0), ("Min II", 0.5))
    return (("Max", 0.0),)


_SCHEDULE_START_HOUR = 18
_SCHEDULE_END_HOUR = 10
_SCHEDULE_SAMPLE_COUNT = 97
_NAUTICAL_TWILIGHT_ALTITUDE_DEG = -12.0
_ASTRONOMICAL_TWILIGHT_ALTITUDE_DEG = -18.0


def compute_site_tonight_schedule(
    *,
    timezone_name: str = "UTC",
    latitude_deg: float | None = None,
    longitude_deg: float | None = None,
    elevation_m: float | None = None,
    now: datetime | None = None,
    star: VsxEphemerisStar | None = None,
) -> TonightSchedule | None:
    zone = _resolve_timezone(timezone_name)
    now_utc = _as_utc(now if now is not None else datetime.now(UTC))
    now_local = now_utc.astimezone(zone)
    location = _observer_location(latitude_deg, longitude_deg, elevation_m)
    return build_tonight_schedule(
        star,
        timezone_name=_timezone_label(zone, timezone_name),
        zone=zone,
        now_local=now_local,
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
        elevation_m=elevation_m,
        location=location,
    )


def build_tonight_schedule(
    star: VsxEphemerisStar | None,
    *,
    timezone_name: str,
    zone: tzinfo,
    now_local: datetime,
    latitude_deg: float | None,
    longitude_deg: float | None,
    elevation_m: float | None,
    location: EarthLocation | None,
) -> TonightSchedule | None:
    if location is None or latitude_deg is None or longitude_deg is None:
        return None
    session_date = now_local.date()
    if now_local.hour < 12:
        session_date = session_date - timedelta(days=1)
    start_local = datetime(
        session_date.year,
        session_date.month,
        session_date.day,
        _SCHEDULE_START_HOUR,
        0,
        0,
        tzinfo=zone,
    )
    end_local = start_local + timedelta(hours=24 - _SCHEDULE_START_HOUR + _SCHEDULE_END_HOUR)
    samples = _sample_tonight_schedule(
        start_local,
        end_local,
        location,
        ra_deg=None if star is None else star.ra_deg,
        dec_deg=None if star is None else star.dec_deg,
    )
    if not samples:
        return None
    marks, dark_start, dark_end = twilight_marks_from_samples(samples)
    moon_percent: float | None = None
    try:
        from photometry_app.core.moonlight import moon_illumination_percent

        midpoint = start_local + (end_local - start_local) / 2
        moon_percent = float(
            moon_illumination_percent(
                midpoint,
                latitude_deg=float(latitude_deg),
                longitude_deg=float(longitude_deg),
                elevation_m=float(elevation_m or 0.0),
            )
        )
    except Exception:
        moon_percent = None
    return TonightSchedule(
        timezone_name=timezone_name,
        latitude_deg=float(latitude_deg),
        longitude_deg=float(longitude_deg),
        start_local=start_local,
        end_local=end_local,
        moon_illumination_percent=moon_percent,
        samples=samples,
        marks=marks,
        dark_start_local=dark_start,
        dark_end_local=dark_end,
    )


def twilight_marks_from_samples(
    samples: tuple[TonightScheduleSample, ...] | list[TonightScheduleSample],
) -> tuple[tuple[TwilightMark, ...], datetime | None, datetime | None]:
    if len(samples) < 2:
        return (), None, None
    nautical_dusk = _first_crossing(samples, _NAUTICAL_TWILIGHT_ALTITUDE_DEG, descending=True)
    astro_dusk = _first_crossing(samples, _ASTRONOMICAL_TWILIGHT_ALTITUDE_DEG, descending=True)
    astro_dawn = _first_crossing(samples, _ASTRONOMICAL_TWILIGHT_ALTITUDE_DEG, descending=False)
    nautical_dawn = _first_crossing(samples, _NAUTICAL_TWILIGHT_ALTITUDE_DEG, descending=False)
    marks: list[TwilightMark] = []
    if nautical_dusk is not None:
        marks.append(TwilightMark("Nautical Dusk", nautical_dusk))
    if astro_dawn is not None:
        marks.append(TwilightMark("Astronomical Dawn", astro_dawn))
    if nautical_dawn is not None:
        marks.append(TwilightMark("Nautical Dawn", nautical_dawn))
    dark_start = astro_dusk
    dark_end = astro_dawn
    if dark_start is None and samples[0].sun_altitude_deg <= _ASTRONOMICAL_TWILIGHT_ALTITUDE_DEG:
        dark_start = samples[0].local
    if dark_end is None and samples[-1].sun_altitude_deg <= _ASTRONOMICAL_TWILIGHT_ALTITUDE_DEG:
        dark_end = samples[-1].local
    if dark_start is not None and dark_end is not None and dark_end > dark_start:
        dark_mid = dark_start + (dark_end - dark_start) / 2
        marks.append(TwilightMark("Astronomical Dark", dark_mid))
    return tuple(marks), dark_start, dark_end


def format_latitude_dms(latitude_deg: float) -> str:
    return _format_dms(latitude_deg, "N", "S")


def format_longitude_dms(longitude_deg: float) -> str:
    return _format_dms(longitude_deg, "E", "W")


def format_site_coordinate_lines(latitude_deg: float, longitude_deg: float) -> tuple[str, str]:
    dms = f"{format_latitude_dms(latitude_deg)}, {format_longitude_dms(longitude_deg)}"
    decimal = f"{latitude_deg:.6f}, {longitude_deg:.6f}"
    return dms, decimal


def _format_dms(value_deg: float, positive: str, negative: str) -> str:
    hemisphere = positive if value_deg >= 0 else negative
    total_seconds = round(abs(float(value_deg)) * 3600.0)
    degrees, rem = divmod(int(total_seconds), 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{degrees}° {minutes:02d}' {seconds:02d}\" {hemisphere}"


def _sample_tonight_schedule(
    start_local: datetime,
    end_local: datetime,
    location: EarthLocation,
    *,
    ra_deg: float | None = None,
    dec_deg: float | None = None,
) -> tuple[TonightScheduleSample, ...]:
    duration_seconds = max(60.0, (end_local - start_local).total_seconds())
    offsets = [
        start_local + timedelta(seconds=duration_seconds * index / (_SCHEDULE_SAMPLE_COUNT - 1))
        for index in range(_SCHEDULE_SAMPLE_COUNT)
    ]
    try:
        obstime = Time([_as_utc(moment) for moment in offsets], scale="utc")
        with iers.conf.set_temp("auto_max_age", None):
            frame = AltAz(obstime=obstime, location=location)
            sun_alts = np.asarray(get_sun(obstime).transform_to(frame).alt.deg, dtype=float)
            moon_alts = np.asarray(get_body("moon", obstime, location).transform_to(frame).alt.deg, dtype=float)
            star_alts = None
            if ra_deg is not None and dec_deg is not None:
                coord = SkyCoord(float(ra_deg) * u.deg, float(dec_deg) * u.deg, frame="icrs")
                star_alts = np.asarray(coord.transform_to(frame).alt.deg, dtype=float)
    except Exception:
        return ()
    samples: list[TonightScheduleSample] = []
    for index, moment in enumerate(offsets):
        sun_alt = float(sun_alts[index])
        if not np.isfinite(sun_alt):
            continue
        moon_alt = float(moon_alts[index]) if np.isfinite(moon_alts[index]) else None
        star_alt = None
        if star_alts is not None:
            value = float(star_alts[index])
            star_alt = value if np.isfinite(value) else None
        samples.append(
            TonightScheduleSample(
                local=moment,
                sun_altitude_deg=sun_alt,
                moon_altitude_deg=moon_alt,
                star_altitude_deg=star_alt,
            )
        )
    return tuple(samples)


def _first_crossing(
    samples: tuple[TonightScheduleSample, ...] | list[TonightScheduleSample],
    target_alt: float,
    *,
    descending: bool,
) -> datetime | None:
    for previous, current in zip(samples, samples[1:]):
        before = previous.sun_altitude_deg
        after = current.sun_altitude_deg
        if descending:
            if before > target_alt >= after:
                return _interpolate_crossing(previous, current, target_alt)
        elif before < target_alt <= after:
            return _interpolate_crossing(previous, current, target_alt)
    return None


def _interpolate_crossing(
    previous: TonightScheduleSample,
    current: TonightScheduleSample,
    target_alt: float,
) -> datetime:
    span = current.sun_altitude_deg - previous.sun_altitude_deg
    if abs(span) < 1e-9:
        return current.local
    fraction = (target_alt - previous.sun_altitude_deg) / span
    fraction = max(0.0, min(1.0, fraction))
    delta = current.local - previous.local
    return previous.local + timedelta(seconds=delta.total_seconds() * fraction)


def parse_vsx_object_xml(xml_text: str) -> VsxEphemerisStar | None:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return None
    name = _xml_text(root, "Name")
    if not name:
        return None
    ra_deg = _parse_float(_xml_text(root, "RA2000"))
    dec_deg = _parse_float(_xml_text(root, "Declination2000"))
    if ra_deg is None or dec_deg is None:
        return None
    period_days = _parse_float(_xml_text(root, "Period"))
    variability_type = _xml_text(root, "VariabilityType") or ""
    eclipse_duration_hours = _vsx_eclipse_duration_hours(root, variability_type, period_days)
    return VsxEphemerisStar(
        name=name,
        oid=_xml_text(root, "OID") or name,
        ra_deg=ra_deg,
        dec_deg=dec_deg,
        variability_type=variability_type,
        period_days=period_days,
        epoch_hjd=_parse_float(_xml_text(root, "Epoch")),
        max_mag=_parse_magnitude(_xml_text(root, "MaxMag")),
        min_mag=_parse_magnitude(_xml_text(root, "MinMag")),
        eclipse_duration_hours=eclipse_duration_hours,
        source="VSX",
    )


def _lookup_vsx_object_api(name: str, timeout_seconds: float) -> VsxEphemerisStar | None:
    try:
        response = requests.get(
            _VSX_OBJECT_API_URL,
            params={"view": "api.object", "ident": name},
            timeout=timeout_seconds,
            headers={"User-Agent": _VSX_USER_AGENT},
        )
        response.raise_for_status()
    except requests.RequestException:
        return None
    return parse_vsx_object_xml(response.text)


def _lookup_vsx_vizier_by_name(name: str, timeout_seconds: float) -> VsxEphemerisStar | None:
    tables = _query_vsx_vizier(Name=name, timeout_seconds=timeout_seconds, row_limit=20)
    star = _best_vsx_name_match(name, _vsx_stars_from_vizier_tables(tables, source="VizieR VSX"))
    if star is not None:
        return star
    wildcard = name.strip()
    if "*" not in wildcard:
        tables = _query_vsx_vizier(Name=f"*{wildcard}*", timeout_seconds=timeout_seconds, row_limit=20)
        return _best_vsx_name_match(name, _vsx_stars_from_vizier_tables(tables, source="VizieR VSX"))
    return None


def _lookup_vsx_via_simbad(name: str, timeout_seconds: float) -> VsxEphemerisStar | None:
    coordinate = _simbad_coordinate(name, timeout_seconds)
    if coordinate is None:
        return None
    vizier = Vizier(columns=["*"], row_limit=8)
    try:
        vizier.TIMEOUT = max(1, int(round(timeout_seconds)))
    except Exception:
        pass
    try:
        tables = vizier.query_region(coordinate, radius=_SIMBAD_CONE_ARCSEC * u.arcsec, catalog=_VSX_CATALOG)
    except Exception:
        return None
    stars = _vsx_stars_from_vizier_tables(tables, source="VizieR VSX")
    if not stars:
        return None
    return min(stars, key=lambda star: _sky_separation_deg(coordinate, star.ra_deg, star.dec_deg))


def _query_vsx_vizier(*, Name: str, timeout_seconds: float, row_limit: int) -> object:
    vizier = Vizier(columns=["*"], row_limit=max(1, int(row_limit)))
    try:
        vizier.TIMEOUT = max(1, int(round(timeout_seconds)))
    except Exception:
        pass
    try:
        return vizier.query_constraints(catalog=_VSX_CATALOG, Name=Name)
    except Exception:
        return None


def _vsx_stars_from_vizier_tables(tables: object, *, source: str) -> list[VsxEphemerisStar]:
    if not tables:
        return []
    try:
        table = tables[0]
    except Exception:
        return []
    stars: list[VsxEphemerisStar] = []
    for row in table:
        name = str(row.get("Name", "")).strip()
        ra_deg = _table_float(row, "RAJ2000")
        dec_deg = _table_float(row, "DEJ2000")
        if not name or ra_deg is None or dec_deg is None:
            continue
        period_days = _table_float(row, "Period")
        variability_type = str(row.get("Type", "")).strip()
        eclipse_duration_hours = None
        if _is_eclipsing_type(variability_type):
            eclipse_duration_hours = _duration_hours_from_vsx_api(
                _table_float(row, "Duration"),
                period_days,
            )
        stars.append(
            VsxEphemerisStar(
                name=name,
                oid=str(row.get("OID", name)).strip() or name,
                ra_deg=ra_deg,
                dec_deg=dec_deg,
                variability_type=variability_type,
                period_days=period_days,
                epoch_hjd=_table_float(row, "Epoch"),
                max_mag=_table_float(row, "max"),
                min_mag=_table_float(row, "min"),
                eclipse_duration_hours=eclipse_duration_hours,
                source=source,
            )
        )
    return stars


def _best_vsx_name_match(query: str, stars: list[VsxEphemerisStar]) -> VsxEphemerisStar | None:
    if not stars:
        return None
    normalized = _normalize_star_name(query)
    exact = [star for star in stars if _normalize_star_name(star.name) == normalized]
    if exact:
        return exact[0]
    compact = normalized.replace(" ", "")
    compact_matches = [star for star in stars if _normalize_star_name(star.name).replace(" ", "") == compact]
    if compact_matches:
        return compact_matches[0]
    return stars[0]


def _merge_vsx_stars(primary: VsxEphemerisStar, fallback: VsxEphemerisStar) -> VsxEphemerisStar:
    return VsxEphemerisStar(
        name=primary.name or fallback.name,
        oid=primary.oid or fallback.oid,
        ra_deg=primary.ra_deg,
        dec_deg=primary.dec_deg,
        variability_type=primary.variability_type or fallback.variability_type,
        period_days=primary.period_days if primary.period_days is not None else fallback.period_days,
        epoch_hjd=primary.epoch_hjd if primary.epoch_hjd is not None else fallback.epoch_hjd,
        max_mag=primary.max_mag if primary.max_mag is not None else fallback.max_mag,
        min_mag=primary.min_mag if primary.min_mag is not None else fallback.min_mag,
        eclipse_duration_hours=(
            primary.eclipse_duration_hours
            if primary.eclipse_duration_hours is not None
            else fallback.eclipse_duration_hours
        ),
        source=primary.source if primary.period_days is not None else fallback.source,
    )


def _upcoming_events(
    star: VsxEphemerisStar,
    *,
    now_utc: datetime,
    zone: tzinfo,
    location: EarthLocation | None,
    min_altitude_deg: float,
) -> list[VariableEphemerisEvent]:
    period_days = float(star.period_days or 0.0)
    epoch_hjd = float(star.epoch_hjd or 0.0)
    if period_days <= 0:
        return []
    now_jd = _datetime_to_jd(now_utc)
    start_jd = now_jd - _LOOKBACK_DAYS
    kinds = event_kinds_for_variability_type(star.variability_type)
    window_days = _LOOKBACK_DAYS + _HORIZON_DAYS
    per_kind = max(3, min(_MAX_EVENTS, int((window_days / period_days) + 3)))
    half_window = _eclipse_half_window(star)
    events: list[VariableEphemerisEvent] = []
    for kind, phase_offset in kinds:
        elapsed_cycles = ((start_jd - epoch_hjd) / period_days) - phase_offset
        first_cycle = int(math.ceil(elapsed_cycles - 1e-8))
        for cycle in range(first_cycle, first_cycle + per_kind):
            event_jd = epoch_hjd + (float(cycle) + phase_offset) * period_days
            utc = _jd_to_datetime(event_jd)
            local, altitude_deg, sun_altitude_deg, is_night, is_up = _condition_at_time(
                star,
                utc,
                zone=zone,
                location=location,
                min_altitude_deg=min_altitude_deg,
            )
            window_start = local - half_window if half_window is not None else None
            window_end = local + half_window if half_window is not None else None
            window_observable = None
            if half_window is not None:
                start_obs = _is_night_and_up(
                    star,
                    utc - half_window,
                    zone=zone,
                    location=location,
                    min_altitude_deg=min_altitude_deg,
                )
                end_obs = _is_night_and_up(
                    star,
                    utc + half_window,
                    zone=zone,
                    location=location,
                    min_altitude_deg=min_altitude_deg,
                )
                window_observable = bool((is_night and is_up) or start_obs or end_obs)
            events.append(
                VariableEphemerisEvent(
                    kind=kind,
                    cycle=cycle,
                    utc=utc,
                    local=local,
                    altitude_deg=altitude_deg,
                    sun_altitude_deg=sun_altitude_deg,
                    is_night=bool(is_night),
                    is_up=bool(is_up),
                    observable=bool(is_night and is_up),
                    window_start_local=window_start,
                    window_end_local=window_end,
                    window_observable=window_observable,
                )
            )
    events.sort(key=lambda event: event.utc)
    return events[:_MAX_EVENTS]


def _forecast_summary(
    star: VsxEphemerisStar,
    *,
    timezone_name: str,
    site_configured: bool,
    current_phase: float | None,
    events: list[VariableEphemerisEvent],
    tonight_events: list[VariableEphemerisEvent],
    tonight_observable: list[VariableEphemerisEvent],
    night_start: datetime | None,
    night_end: datetime | None,
    now_utc: datetime,
) -> str:
    if star.period_days is None or star.period_days <= 0 or star.epoch_hjd is None:
        type_note = f" ({star.variability_type})" if star.variability_type else ""
        return (
            f"{star.name}{type_note} is in VSX, but Period or Epoch is missing, "
            "so upcoming times cannot be calculated."
        )
    upcoming = [event for event in events if event.utc >= now_utc]
    if tonight_observable:
        first = tonight_observable[0]
        extras = ""
        if len(tonight_observable) > 1:
            extras = f" plus {len(tonight_observable) - 1} more"
        details = _event_condition_text(first, site_configured)
        return f"Tonight: {_event_time_phrase(first)}{details}{extras}."
    if tonight_events:
        first = tonight_events[0]
        reason = _unobservable_reason(first, site_configured)
        next_observable = next(
            (event for event in upcoming if event.observable and event.utc > first.utc),
            None,
        )
        follow_up = ""
        if next_observable is not None:
            follow_up = (
                f" Next observable {next_observable.kind}: "
                f"{_format_local_datetime(next_observable.local)}."
            )
        return (
            f"Tonight's {_event_time_phrase(first)} is {reason}.{follow_up}"
        ).strip()
    if upcoming:
        first = upcoming[0]
        night_note = ""
        if night_start is not None and night_end is not None:
            night_note = (
                f" Night window {_format_local_time(night_start)}–{_format_local_time(night_end)} {timezone_name}."
            )
        return (
            f"No min/max tonight. Next {_event_time_phrase(first, include_date=True)} "
            f"({timezone_name}).{night_note}"
        ).strip()
    phase_note = "" if current_phase is None else f" Current phase {current_phase:.3f}."
    return f"No upcoming events could be calculated.{phase_note}".strip()


def _event_condition_text(event: VariableEphemerisEvent, site_configured: bool) -> str:
    parts: list[str] = []
    if event.altitude_deg is not None:
        parts.append(f"alt {event.altitude_deg:.0f}°")
    elif site_configured:
        parts.append("altitude unknown")
    if event.is_night:
        parts.append("dark")
    if not parts:
        return ""
    return f" ({', '.join(parts)})"


def _unobservable_reason(event: VariableEphemerisEvent, site_configured: bool) -> str:
    if not event.is_night:
        return "in daylight"
    if not event.is_up:
        if event.altitude_deg is None:
            return "below the horizon"
        return f"too low ({event.altitude_deg:.0f}°)"
    if not site_configured:
        return "outside the local night window"
    return "not observable"


def _local_night_window(
    now_local: datetime,
    zone: tzinfo,
    location: EarthLocation | None,
) -> tuple[datetime | None, datetime | None]:
    session_date = now_local.date()
    if now_local.hour < 12:
        session_date = session_date - timedelta(days=1)
    evening = datetime(session_date.year, session_date.month, session_date.day, 12, 0, 0, tzinfo=zone)
    if location is None:
        night_start = evening.replace(hour=18, minute=0, second=0, microsecond=0)
        night_end = night_start + timedelta(hours=12)
        return night_start, night_end
    search_end = evening + timedelta(hours=24)
    sunset = _find_sun_crossing(evening, search_end, location, descending=True, target_alt=_CIVIL_TWILIGHT_ALTITUDE_DEG)
    sunrise = _find_sun_crossing(
        evening + timedelta(hours=8),
        search_end,
        location,
        descending=False,
        target_alt=_CIVIL_TWILIGHT_ALTITUDE_DEG,
    )
    if sunset is None or sunrise is None or sunrise <= sunset:
        sun_now = _sun_altitude_deg(_as_utc(now_local), location)
        if sun_now is not None and sun_now <= _CIVIL_TWILIGHT_ALTITUDE_DEG:
            return evening, search_end
        return None, None
    return sunset, sunrise


def _find_sun_crossing(
    start_local: datetime,
    end_local: datetime,
    location: EarthLocation,
    *,
    descending: bool,
    target_alt: float = _CIVIL_TWILIGHT_ALTITUDE_DEG,
) -> datetime | None:
    start = Time(_as_utc(start_local), scale="utc")
    end = Time(_as_utc(end_local), scale="utc")
    start_alt = _sun_altitude_deg(_as_utc(start_local), location)
    end_alt = _sun_altitude_deg(_as_utc(end_local), location)
    if start_alt is None or end_alt is None:
        return None
    target = float(target_alt)
    if descending:
        if start_alt <= target or end_alt > target:
            return None
    elif start_alt >= target or end_alt < target:
        return None
    low = start
    high = end
    for _ in range(18):
        mid = Time((low.jd + high.jd) / 2.0, format="jd", scale="utc")
        mid_utc = _time_to_datetime(mid)
        mid_alt = _sun_altitude_deg(mid_utc, location)
        if mid_alt is None:
            return None
        if descending:
            if mid_alt > target:
                low = mid
            else:
                high = mid
        elif mid_alt < target:
            low = mid
        else:
            high = mid
    crossing = _time_to_datetime(high).astimezone(start_local.tzinfo)
    return crossing


def _star_altitude_deg(
    ra_deg: float,
    dec_deg: float,
    when_utc: datetime,
    location: EarthLocation | None,
) -> float | None:
    if location is None:
        return None
    try:
        coord = SkyCoord(float(ra_deg) * u.deg, float(dec_deg) * u.deg, frame="icrs")
        with iers.conf.set_temp("auto_max_age", None):
            altaz = coord.transform_to(AltAz(obstime=Time(_as_utc(when_utc), scale="utc"), location=location))
        return float(altaz.alt.deg)
    except Exception:
        return None


def _sun_altitude_deg(when_utc: datetime, location: EarthLocation | None) -> float | None:
    if location is None:
        return None
    try:
        obstime = Time(_as_utc(when_utc), scale="utc")
        with iers.conf.set_temp("auto_max_age", None):
            altaz = get_sun(obstime).transform_to(AltAz(obstime=obstime, location=location))
        return float(altaz.alt.deg)
    except Exception:
        return None


def _observer_location(
    latitude_deg: float | None,
    longitude_deg: float | None,
    elevation_m: float | None,
) -> EarthLocation | None:
    if latitude_deg is None or longitude_deg is None:
        return None
    try:
        return EarthLocation(
            lat=float(latitude_deg) * u.deg,
            lon=float(longitude_deg) * u.deg,
            height=float(elevation_m or 0.0) * u.m,
        )
    except Exception:
        return None


def _simbad_coordinate(name: str, timeout_seconds: float) -> SkyCoord | None:
    simbad = Simbad()
    try:
        simbad.TIMEOUT = max(1, int(round(timeout_seconds)))
    except Exception:
        pass
    simbad.ROW_LIMIT = 1
    try:
        simbad.add_votable_fields("ra", "dec")
    except Exception:
        pass
    try:
        result = simbad.query_object(name)
    except Exception:
        return None
    if result is None or len(result) == 0:
        return None
    row = result[0]
    ra_deg = _table_float(row, "ra")
    dec_deg = _table_float(row, "dec")
    if ra_deg is None or dec_deg is None:
        ra_deg = _table_float(row, "RA")
        dec_deg = _table_float(row, "DEC")
    if ra_deg is None or dec_deg is None:
        return None
    try:
        return SkyCoord(float(ra_deg) * u.deg, float(dec_deg) * u.deg, frame="icrs")
    except Exception:
        return None


def _sky_separation_deg(coordinate: SkyCoord, ra_deg: float, dec_deg: float) -> float:
    other = SkyCoord(float(ra_deg) * u.deg, float(dec_deg) * u.deg, frame="icrs")
    return float(coordinate.separation(other).deg)


def _is_eclipsing_type(variability_type: str) -> bool:
    tokens = [
        token.strip().upper()
        for token in str(variability_type or "").replace("+", "/").replace(",", "/").split("/")
        if token.strip()
    ]
    if not tokens:
        return False
    return tokens[0] in _ECLIPSING_TYPE_TOKENS or any(token in _ECLIPSING_TYPE_TOKENS for token in tokens[:2])


def _eclipse_half_window(star: VsxEphemerisStar) -> timedelta | None:
    if not _is_eclipsing_type(star.variability_type):
        return None
    hours = star.eclipse_duration_hours
    period_days = star.period_days
    if hours is None or hours <= 0 or period_days is None or period_days <= 0:
        return None
    period_hours = float(period_days) * 24.0
    hours = min(float(hours), period_hours * 0.95)
    return timedelta(hours=hours / 2.0)


def _condition_at_time(
    star: VsxEphemerisStar,
    utc: datetime,
    *,
    zone: tzinfo,
    location: EarthLocation | None,
    min_altitude_deg: float,
) -> tuple[datetime, float | None, float | None, bool, bool]:
    local = utc.astimezone(zone)
    altitude_deg = _star_altitude_deg(star.ra_deg, star.dec_deg, utc, location)
    sun_altitude_deg = _sun_altitude_deg(utc, location)
    is_night = _event_is_night(local, sun_altitude_deg)
    is_up = altitude_deg is None or altitude_deg >= float(min_altitude_deg)
    return local, altitude_deg, sun_altitude_deg, is_night, is_up


def _is_night_and_up(
    star: VsxEphemerisStar,
    utc: datetime,
    *,
    zone: tzinfo,
    location: EarthLocation | None,
    min_altitude_deg: float,
) -> bool:
    _local, _altitude, _sun, is_night, is_up = _condition_at_time(
        star,
        utc,
        zone=zone,
        location=location,
        min_altitude_deg=min_altitude_deg,
    )
    return bool(is_night and is_up)


def _event_overlaps_interval(event: VariableEphemerisEvent, start: datetime, end: datetime) -> bool:
    window_start = event.window_start_local or event.local
    window_end = event.window_end_local or event.local
    return window_start < end and window_end >= start


def _event_is_observable(event: VariableEphemerisEvent) -> bool:
    if event.window_observable is not None:
        return bool(event.window_observable)
    return bool(event.observable)


def _event_time_phrase(event: VariableEphemerisEvent, *, include_date: bool = False) -> str:
    if event.window_start_local is not None and event.window_end_local is not None:
        start = (
            _format_local_datetime(event.window_start_local)
            if include_date
            else _format_local_time(event.window_start_local)
        )
        end = _format_local_time(event.window_end_local)
        return (
            f"{event.kind} eclipse {start}–{end} "
            f"(mid {_format_local_time(event.local)})"
        )
    if include_date:
        return f"{event.kind} at {_format_local_datetime(event.local)}"
    return f"{event.kind} at {_format_local_time(event.local)}"


def format_eclipse_window(event: VariableEphemerisEvent) -> str:
    if event.window_start_local is None or event.window_end_local is None:
        return "—"
    return f"{_format_local_time(event.window_start_local)}–{_format_local_time(event.window_end_local)}"


def _duration_hours_from_vsx_api(value: object, period_days: float | None) -> float | None:
    parsed = _parse_float(value)
    if parsed is None or period_days is None or period_days <= 0:
        return None
    if 0.0 < parsed <= 100.0:
        return (parsed / 100.0) * period_days * 24.0
    if parsed > 100.0:
        return parsed
    return None


def _vsx_eclipse_duration_hours(
    root: ElementTree.Element,
    variability_type: str,
    period_days: float | None,
) -> float | None:
    if not _is_eclipsing_type(variability_type):
        return None
    return _duration_hours_from_vsx_api(
        _xml_text(root, "EclipseDuration"),
        period_days,
    ) or _duration_hours_from_vsx_api(
        _xml_text(root, "RiseDuration"),
        period_days,
    )


def _parse_magnitude(value: str | None) -> float | None:
    if value is None:
        return None
    token = value.replace(":", " ").split()[0] if value.strip() else ""
    return _parse_float(token)


def _xml_text(root: ElementTree.Element, tag: str) -> str | None:
    element = root.find(tag)
    if element is None:
        element = root.find(f".//{tag}")
    if element is None or element.text is None:
        return None
    text = " ".join(element.text.split())
    return text or None


def _table_float(row: object, key: str) -> float | None:
    try:
        value = row.get(key)  # type: ignore[union-attr]
    except Exception:
        try:
            value = row[key]  # type: ignore[index]
        except Exception:
            return None
    return _parse_float(value)


def _parse_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        if hasattr(value, "mask") and bool(getattr(value, "mask")):
            return None
    except Exception:
        pass
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "--", "masked"}:
        return None
    try:
        return float(text.split()[0])
    except (TypeError, ValueError):
        return None


def _event_is_night(local: datetime, sun_altitude_deg: float | None) -> bool:
    if sun_altitude_deg is not None:
        return sun_altitude_deg <= _CIVIL_TWILIGHT_ALTITUDE_DEG
    return local.hour >= 18 or local.hour < 6


_SKY_RGB_STOPS: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (0.00, (16, 22, 48)),
    (0.12, (36, 32, 86)),
    (0.26, (78, 46, 112)),
    (0.40, (168, 68, 52)),
    (0.55, (214, 110, 48)),
    (0.70, (236, 168, 72)),
    (0.85, (246, 208, 108)),
    (1.00, (252, 236, 168)),
)


def daylight_sky_factor(
    *,
    sun_altitude_deg: float | None,
    local: datetime | None = None,
) -> float:
    if sun_altitude_deg is not None:
        return _factor_from_sun_altitude(sun_altitude_deg)
    if local is not None:
        return _factor_from_local_clock(local)
    return 0.5


def ephemeris_sky_rgb(factor: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, float(factor)))
    for index in range(1, len(_SKY_RGB_STOPS)):
        t1, color1 = _SKY_RGB_STOPS[index]
        t0, color0 = _SKY_RGB_STOPS[index - 1]
        if t <= t1:
            span = t1 - t0
            blend = 0.0 if span <= 0 else (t - t0) / span
            return (
                int(round(color0[0] + (color1[0] - color0[0]) * blend)),
                int(round(color0[1] + (color1[1] - color0[1]) * blend)),
                int(round(color0[2] + (color1[2] - color0[2]) * blend)),
            )
    return _SKY_RGB_STOPS[-1][1]


def ephemeris_sky_text_rgb(background: tuple[int, int, int]) -> tuple[int, int, int]:
    red, green, blue = background
    luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255.0
    if luminance < 0.46:
        return (248, 246, 238)
    return (28, 24, 32)


def event_sky_tooltip(event: VariableEphemerisEvent) -> str:
    parts: list[str] = []
    if event.window_start_local is not None and event.window_end_local is not None:
        parts.append(
            f"Eclipse window {format_eclipse_window(event)} from VSX duration, centered on mid-eclipse."
        )
    if event.sun_altitude_deg is not None:
        parts.append(f"Sun altitude {event.sun_altitude_deg:.0f}°. Gold is daylight, navy is dark.")
    else:
        parts.append("Day/night color estimated from local time. Set an observing site for sun altitude.")
    return " ".join(parts)


def _factor_from_sun_altitude(altitude_deg: float) -> float:
    altitude = float(altitude_deg)
    if altitude <= -18.0:
        return 0.0
    if altitude >= 55.0:
        return 1.0
    if altitude < 0.0:
        t = (altitude - (-18.0)) / 18.0
        return 0.5 * t * t * (3.0 - 2.0 * t)
    t = altitude / 55.0
    return 0.5 + 0.5 * t * t * (3.0 - 2.0 * t)


def _factor_from_local_clock(local: datetime) -> float:
    minutes = local.hour * 60 + local.minute + local.second / 60.0
    hours_from_noon = (minutes / 60.0) - 12.0
    if hours_from_noon > 12.0:
        hours_from_noon -= 24.0
    elif hours_from_noon < -12.0:
        hours_from_noon += 24.0
    cosine = math.cos(math.pi * abs(hours_from_noon) / 12.0)
    return max(0.0, min(1.0, 0.5 + 0.5 * cosine))


def _normalize_star_name(name: str) -> str:
    return " ".join(str(name).split()).casefold()


def _resolve_timezone(timezone_name: str) -> tzinfo:
    name = str(timezone_name or "").strip() or "UTC"
    if name.upper() in {"UTC", "GMT", "Z"}:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, Exception):
        pass
    offset = _parse_utc_offset(name)
    if offset is not None:
        return timezone(offset)
    return timezone.utc


def _parse_utc_offset(value: str) -> timedelta | None:
    match = re.fullmatch(
        r"(?:UTC|GMT)?\s*([+-])(\d{1,2})(?::?(\d{2}))?",
        value.strip(),
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    sign = 1 if match.group(1) == "+" else -1
    hours = int(match.group(2))
    minutes = int(match.group(3) or 0)
    if hours > 14 or minutes > 59:
        return None
    return timedelta(hours=sign * hours, minutes=sign * minutes)


def _timezone_label(zone: tzinfo, fallback: str) -> str:
    key = getattr(zone, "key", None)
    if isinstance(key, str) and key.strip():
        return key.strip()
    offset = zone.utcoffset(datetime.now(UTC))
    if offset is None:
        return str(fallback or "").strip() or "UTC"
    total_seconds = int(offset.total_seconds())
    if total_seconds == 0:
        return "UTC"
    sign = "+" if total_seconds > 0 else "-"
    total_seconds = abs(total_seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _datetime_to_jd(value: datetime) -> float:
    return float(Time(_as_utc(value), scale="utc").jd)


def _jd_to_datetime(julian_date: float) -> datetime:
    return _time_to_datetime(Time(float(julian_date), format="jd", scale="utc"))


def _time_to_datetime(value: Time) -> datetime:
    iso = value.utc.isot
    parsed = datetime.fromisoformat(str(iso))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _format_local_time(value: datetime) -> str:
    return value.strftime("%H:%M")


def _format_local_datetime(value: datetime) -> str:
    return value.strftime("%a %d %b %Y %H:%M")
