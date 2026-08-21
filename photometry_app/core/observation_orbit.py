from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from photometry_app.core.observation_map import (
    ObservationMapDay,
    ObservationMapResult,
    contribution_level,
    contribution_span_bounds,
)

SEASON_BIN_COUNT = 72
_MIN_DISPLAY_YEARS = 1.0
_WEEKLY_AFTER_YEARS = 25.0
_MAX_ARCHIVE_YEARS = 40.0
_MONTH_LABELS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


@dataclass(frozen=True, slots=True)
class OrbitDay:
    observation_date: date
    year_fraction: float
    elapsed_years: float
    exposure_seconds: float
    frame_count: int
    level: int
    is_empty: bool
    aggregated_days: int = 1


@dataclass(frozen=True, slots=True)
class OrbitSeasonBin:
    index: int
    year_fraction: float
    exposure_seconds: float
    night_count: int
    level: int


@dataclass(frozen=True, slots=True)
class OrbitYearSummary:
    year: int
    night_count: int
    exposure_seconds: float
    elapsed_years: float


@dataclass(frozen=True, slots=True)
class ObservationOrbit:
    start_date: date
    end_date: date
    first_activity: date
    last_activity: date
    span_years: float
    display_years: float
    days: tuple[OrbitDay, ...]
    season_bins: tuple[OrbitSeasonBin, ...]
    years: tuple[OrbitYearSummary, ...]
    night_count: int
    total_exposure_seconds: float
    max_seconds: float
    uses_weekly_inner: bool

    @property
    def total_exposure_hours(self) -> float:
        return float(self.total_exposure_seconds) / 3600.0


def year_fraction(day: date) -> float:
    days_in_month = monthrange(day.year, day.month)[1]
    return ((day.month - 1) + (day.day - 1) / float(days_in_month)) / 12.0


def date_from_year_fraction(year: int, fraction: float) -> date:
    clamped = min(max(float(fraction), 0.0), 0.999999)
    total = clamped * 12.0
    month = min(12, int(total) + 1)
    day_frac = total - int(total)
    days_in_month = monthrange(year, month)[1]
    day = min(days_in_month, max(1, int(day_frac * days_in_month) + 1))
    return date(year, month, day)


def elapsed_years(day: date, start: date) -> float:
    return float(day.year - start.year) + (year_fraction(day) - year_fraction(start))


def archive_span_years(start: date, end: date) -> float:
    return max(0.0, elapsed_years(end, start))


def archive_span_caption(nights: int, span_years: float) -> str:
    if nights <= 0:
        return "No dated imaging nights yet"
    night_word = "imaging night" if nights == 1 else "imaging nights"
    if span_years < 1.0:
        months = max(1, int(round(span_years * 12.0)))
        unit = "month" if months == 1 else "months"
        return f"{nights:,} {night_word} across {months} {unit}"
    years_text = f"{span_years:.1f}".rstrip("0").rstrip(".")
    unit = "year" if abs(span_years - 1.0) < 0.05 else "years"
    return f"{nights:,} {night_word} across {years_text} {unit}"


def archive_duration_headline(span_years: float, *, night_count: int = 0) -> str:
    if span_years < 0.08:
        return "1 NIGHT" if night_count <= 1 else f"{night_count} NIGHTS"
    if span_years < 1.0:
        months = max(1, int(round(span_years * 12.0)))
        unit = "MONTH" if months == 1 else "MONTHS"
        return f"{months} {unit}"
    years_text = f"{span_years:.1f}".rstrip("0").rstrip(".")
    unit = "YEAR" if abs(span_years - 1.0) < 0.05 else "YEARS"
    return f"{years_text} {unit}"


INTEGRATION_LEGEND_LABELS = ("0 h", "<2 h", "2–4 h", "4–8 h", "8 h+")


def orbit_integration_level(exposure_seconds: float) -> int:
    hours = float(exposure_seconds) / 3600.0
    if hours <= 0.0:
        return 0
    if hours < 2.0:
        return 1
    if hours < 4.0:
        return 2
    if hours < 8.0:
        return 3
    return 4


def month_activity(
    orbit: ObservationOrbit,
    *,
    year: int | None = None,
    month: int,
) -> tuple[int, float]:
    nights = 0
    seconds = 0.0
    for item in orbit.days:
        if item.is_empty or item.observation_date.month != month:
            continue
        if year is not None and item.observation_date.year != year:
            continue
        nights += max(1, item.aggregated_days)
        seconds += item.exposure_seconds
    return nights, seconds


def add_display_years(start: date, years: float) -> date:
    absolute = float(start.year) + year_fraction(start) + float(years)
    year = int(absolute)
    fraction = absolute - year
    if fraction < 0.0:
        year -= 1
        fraction += 1.0
    try:
        return date_from_year_fraction(year, fraction)
    except ValueError:
        return start


def orbit_display_bounds(first: date, last: date) -> tuple[date, date]:
    if last < first:
        first, last = last, first
    span = archive_span_years(first, last)
    if span < _MIN_DISPLAY_YEARS:
        return first, add_display_years(first, _MIN_DISPLAY_YEARS)
    if span > _MAX_ARCHIVE_YEARS:
        return add_display_years(last, -_MAX_ARCHIVE_YEARS), last
    return first, last


def season_bin_index(fraction: float) -> int:
    return int(min(SEASON_BIN_COUNT - 1, max(0, fraction * SEASON_BIN_COUNT)))


def build_observation_orbit(result: ObservationMapResult | None) -> ObservationOrbit | None:
    if result is None or result.first_date is None or result.last_date is None:
        return None
    first_activity, last_activity = contribution_span_bounds(result)
    if first_activity > last_activity:
        return None
    start, end = orbit_display_bounds(first_activity, last_activity)
    max_seconds = max((item.exposure_seconds for item in result.days), default=0.0)
    display_years = max(archive_span_years(start, end), _MIN_DISPLAY_YEARS)
    weekly_before = None
    if display_years > _WEEKLY_AFTER_YEARS:
        weekly_before = add_display_years(end, -_WEEKLY_AFTER_YEARS)

    days: list[OrbitDay] = []
    week_bucket: dict[date, list[ObservationMapDay]] = defaultdict(list)
    for info in result.days:
        if info.exposure_seconds <= 0.0:
            continue
        day = info.observation_date
        if day < start or day > end:
            continue
        if weekly_before is not None and day < weekly_before:
            week_bucket[_week_key(day)].append(info)
            continue
        days.append(
            _orbit_day(day, start, float(info.exposure_seconds), int(info.frame_count), max_seconds, aggregated_days=1)
        )

    for _week_start, group in week_bucket.items():
        seconds = float(sum(item.exposure_seconds for item in group))
        frames = int(sum(item.frame_count for item in group))
        representative = group[len(group) // 2].observation_date
        days.append(
            _orbit_day(
                representative,
                start,
                seconds,
                frames,
                max_seconds,
                aggregated_days=len(group),
            )
        )
    days.sort(key=lambda item: (item.elapsed_years, item.year_fraction))

    season_exposure = [0.0] * SEASON_BIN_COUNT
    season_nights = [0] * SEASON_BIN_COUNT
    year_nights: dict[int, int] = defaultdict(int)
    year_seconds: dict[int, float] = defaultdict(float)
    year_elapsed: dict[int, float] = {}
    for item in days:
        if item.is_empty:
            continue
        bin_index = season_bin_index(item.year_fraction)
        season_exposure[bin_index] += item.exposure_seconds
        season_nights[bin_index] += 1
        year_nights[item.observation_date.year] += item.aggregated_days
        year_seconds[item.observation_date.year] += item.exposure_seconds
        year_elapsed.setdefault(item.observation_date.year, item.elapsed_years)

    season_max = max(season_exposure, default=0.0)
    season_bins = tuple(
        OrbitSeasonBin(
            index=index,
            year_fraction=(index + 0.5) / SEASON_BIN_COUNT,
            exposure_seconds=season_exposure[index],
            night_count=season_nights[index],
            level=contribution_level(season_exposure[index], max_seconds=season_max),
        )
        for index in range(SEASON_BIN_COUNT)
    )
    years = tuple(
        OrbitYearSummary(
            year=year,
            night_count=year_nights[year],
            exposure_seconds=year_seconds[year],
            elapsed_years=year_elapsed.get(year, 0.0),
        )
        for year in sorted(year_nights)
    )
    return ObservationOrbit(
        start_date=start,
        end_date=end,
        first_activity=first_activity,
        last_activity=last_activity,
        span_years=archive_span_years(first_activity, last_activity),
        display_years=display_years,
        days=tuple(days),
        season_bins=season_bins,
        years=years,
        night_count=sum(1 for item in result.days if item.exposure_seconds > 0.0),
        total_exposure_seconds=float(result.total_exposure_seconds),
        max_seconds=float(max_seconds),
        uses_weekly_inner=weekly_before is not None,
    )


def month_labels() -> tuple[str, ...]:
    return _MONTH_LABELS


def _week_key(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _orbit_day(
    day: date,
    start: date,
    seconds: float,
    frames: int,
    max_seconds: float,
    *,
    aggregated_days: int,
) -> OrbitDay:
    empty = seconds <= 0.0
    return OrbitDay(
        observation_date=day,
        year_fraction=year_fraction(day),
        elapsed_years=elapsed_years(day, start),
        exposure_seconds=float(seconds),
        frame_count=int(frames),
        level=0 if empty else orbit_integration_level(seconds),
        is_empty=empty,
        aggregated_days=aggregated_days,
    )


def sample_orbit_nights(
    *,
    first: date,
    last: date,
    nights: tuple[tuple[date, float, int], ...],
) -> ObservationOrbit | None:
    days = tuple(
        ObservationMapDay(observation_date=day, exposure_seconds=seconds, frame_count=frames)
        for day, seconds, frames in nights
    )
    result = ObservationMapResult(
        root_path=Path("."),
        days=days,
        included_frames=sum(item.frame_count for item in days),
        skipped_files=0,
        total_exposure_seconds=float(sum(item.exposure_seconds for item in days)),
        first_date=first,
        last_date=last,
    )
    return build_observation_orbit(result)
