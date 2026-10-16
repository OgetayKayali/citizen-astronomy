from __future__ import annotations

from dataclasses import dataclass
import math
from collections.abc import Sequence

import numpy as np

from photometry_app.core.oc_extrema import PhotometryImport, extract_series_samples


# Match the ephemeris forecast window, then keep at least two upcoming cycles
# and stop before a very short period fills the plot.
_FORECAST_DAYS = 4.0
_MIN_FUTURE_CYCLES = 2.0
_MAX_DISPLAY_CYCLES = 12.0
_MIN_POINTS = 3
_MAX_PHASE_BINS = 48
_MIN_PHASE_BINS = 8
_PHASE_SAMPLES = 96
_MAX_FOURIER_HARMONICS = 6


class EphemerisLightCurveError(ValueError):
    """Raised when pulled photometry cannot be drawn on epoch."""


@dataclass(frozen=True, slots=True)
class EpochMagnitudeSample:
    jd: float
    magnitude: float
    uncertainty: float | None = None
    band: str = ""


@dataclass(frozen=True, slots=True)
class EpochLightCurveBin:
    phase: float
    magnitude: float
    count: int


@dataclass(frozen=True, slots=True)
class EpochLightCurveBand:
    label: str
    template: tuple[EpochLightCurveBin, ...]
    curve_epoch: tuple[float, ...]
    curve_magnitude: tuple[float, ...]
    window_epoch: tuple[float, ...]
    window_magnitude: tuple[float, ...]
    window_uncertainty: tuple[float | None, ...]
    observation_count: int


@dataclass(frozen=True, slots=True)
class EpochLightCurve:
    current_epoch: float
    epoch_start: float
    epoch_end: float
    bands: tuple[EpochLightCurveBand, ...]
    observation_count: int
    notes: tuple[str, ...] = ()


def samples_from_photometry_import(imported: PhotometryImport) -> tuple[EpochMagnitudeSample, ...]:
    samples: list[EpochMagnitudeSample] = []
    for session in imported.sessions:
        band = _band_label(session.series.filter_name)
        for jd, magnitude, uncertainty in extract_series_samples(session.series):
            samples.append(
                EpochMagnitudeSample(
                    jd=float(jd),
                    magnitude=float(magnitude),
                    uncertainty=None if uncertainty is None else float(uncertainty),
                    band=band,
                )
            )
    samples.sort(key=lambda sample: (sample.jd, sample.band, sample.magnitude))
    return tuple(samples)


def epoch_display_window(current_epoch: float, period_days: float) -> tuple[float, float]:
    """Epoch axis from the start of the current cycle through upcoming cycles."""
    if not math.isfinite(current_epoch):
        raise EphemerisLightCurveError("The current epoch is not available.")
    period = float(period_days)
    if not math.isfinite(period) or period <= 0:
        raise EphemerisLightCurveError("A positive period is required to place magnitudes on epoch.")
    start = math.floor(float(current_epoch))
    future_cycles = max(_MIN_FUTURE_CYCLES, _FORECAST_DAYS / period)
    end = min(float(current_epoch) + future_cycles, start + _MAX_DISPLAY_CYCLES)
    end = max(end, start + 1.0)
    return float(start), float(end)


def build_epoch_light_curve(
    samples: Sequence[EpochMagnitudeSample],
    *,
    epoch_hjd: float,
    period_days: float,
    current_epoch: float,
) -> EpochLightCurve:
    period = float(period_days)
    reference = float(epoch_hjd)
    if not math.isfinite(period) or period <= 0 or not math.isfinite(reference):
        raise EphemerisLightCurveError("VSX period and epoch are required before a light curve can be placed on epoch.")
    if not math.isfinite(current_epoch):
        raise EphemerisLightCurveError("The current epoch is not available.")

    grouped: dict[str, list[EpochMagnitudeSample]] = {}
    for sample in samples:
        if not _sample_is_usable(sample):
            continue
        grouped.setdefault(_band_label(sample.band), []).append(sample)
    usable_count = sum(len(group) for group in grouped.values())
    if usable_count < _MIN_POINTS:
        raise EphemerisLightCurveError("Need at least three AAVSO magnitudes to draw a light curve.")

    epoch_start, epoch_end = epoch_display_window(current_epoch, period)
    bands: list[EpochLightCurveBand] = []
    skipped: list[str] = []
    for label, group in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0].casefold())):
        band = _build_band(
            group,
            label=label,
            epoch_hjd=reference,
            period_days=period,
            epoch_start=epoch_start,
            epoch_end=epoch_end,
        )
        if band is None:
            skipped.append(label)
            continue
        bands.append(band)
    if not bands:
        raise EphemerisLightCurveError(
            "These magnitudes do not cover enough of the cycle to draw a future light curve."
        )

    notes: list[str] = []
    if skipped:
        names = ", ".join(skipped)
        notes.append(f"Left out {names} because that band did not cover enough of the cycle.")
    return EpochLightCurve(
        current_epoch=float(current_epoch),
        epoch_start=epoch_start,
        epoch_end=epoch_end,
        bands=tuple(bands),
        observation_count=sum(band.observation_count for band in bands),
        notes=tuple(notes),
    )


def _build_band(
    samples: Sequence[EpochMagnitudeSample],
    *,
    label: str,
    epoch_hjd: float,
    period_days: float,
    epoch_start: float,
    epoch_end: float,
) -> EpochLightCurveBand | None:
    if len(samples) < _MIN_POINTS:
        return None
    phases: list[float] = []
    magnitudes: list[float] = []
    epochs: list[float] = []
    uncertainties: list[float | None] = []
    for sample in samples:
        epoch = (float(sample.jd) - epoch_hjd) / period_days
        if not math.isfinite(epoch):
            continue
        phases.append(_phase_from_epoch(epoch))
        magnitudes.append(float(sample.magnitude))
        epochs.append(float(epoch))
        uncertainties.append(sample.uncertainty)
    if len(phases) < _MIN_POINTS:
        return None
    template = _bin_phases(phases, magnitudes)
    if len(template) < 2:
        return None
    curve_epoch, curve_magnitude = _project_template(template, epoch_start, epoch_end)
    if len(curve_epoch) < 2:
        return None
    window: list[tuple[float, float, float | None]] = []
    for epoch, magnitude, uncertainty in zip(epochs, magnitudes, uncertainties, strict=True):
        if epoch_start - 1e-9 <= epoch <= epoch_end + 1e-9:
            window.append((epoch, magnitude, uncertainty))
    window.sort(key=lambda item: item[0])
    return EpochLightCurveBand(
        label=label,
        template=tuple(template),
        curve_epoch=tuple(curve_epoch),
        curve_magnitude=tuple(curve_magnitude),
        window_epoch=tuple(item[0] for item in window),
        window_magnitude=tuple(item[1] for item in window),
        window_uncertainty=tuple(item[2] for item in window),
        observation_count=len(phases),
    )


def _bin_phases(phases: Sequence[float], magnitudes: Sequence[float]) -> tuple[EpochLightCurveBin, ...]:
    bin_count = min(_MAX_PHASE_BINS, max(_MIN_PHASE_BINS, len(phases) // 3))
    buckets: list[list[float]] = [[] for _ in range(bin_count)]
    for phase, magnitude in zip(phases, magnitudes, strict=True):
        index = min(bin_count - 1, int(math.floor(phase * bin_count)))
        buckets[index].append(float(magnitude))
    template: list[EpochLightCurveBin] = []
    for index, values in enumerate(buckets):
        if not values:
            continue
        template.append(
            EpochLightCurveBin(
                phase=(index + 0.5) / bin_count,
                magnitude=float(np.median(np.asarray(values, dtype=float))),
                count=len(values),
            )
        )
    template.sort(key=lambda item: item.phase)
    return tuple(template)


def _project_template(
    template: Sequence[EpochLightCurveBin],
    epoch_start: float,
    epoch_end: float,
) -> tuple[list[float], list[float]]:
    ordered = sorted(template, key=lambda item: item.phase)
    if len(ordered) < 2:
        return [], []
    phase_grid, magnitude_grid = _smooth_phase_curve(ordered)
    if len(phase_grid) < 2 or not np.all(np.isfinite(magnitude_grid)):
        return [], []
    # Close each cycle so the next integer epoch continues the same curve.
    phases = np.concatenate([phase_grid, [1.0]])
    magnitudes_cycle = np.concatenate([magnitude_grid, [float(magnitude_grid[0])]])
    epochs: list[float] = []
    magnitudes: list[float] = []
    first_cycle = int(math.floor(epoch_start))
    last_cycle = int(math.ceil(epoch_end - 1e-9))
    for cycle in range(first_cycle, last_cycle + 1):
        for phase, magnitude in zip(phases, magnitudes_cycle, strict=True):
            epoch = float(cycle) + float(phase)
            if epoch < epoch_start - 1e-9 or epoch > epoch_end + 1e-9:
                continue
            epochs.append(epoch)
            magnitudes.append(float(magnitude))
    return epochs, magnitudes


def _smooth_phase_curve(template: Sequence[EpochLightCurveBin]) -> tuple[np.ndarray, np.ndarray]:
    """Periodic Fourier fit of the binned phase curve, kept only as smooth as the data needs."""
    phases = np.asarray([item.phase for item in template], dtype=float)
    magnitudes = np.asarray([item.magnitude for item in template], dtype=float)
    weights = np.asarray([float(max(item.count, 1)) for item in template], dtype=float)
    order = np.argsort(phases, kind="stable")
    phases, magnitudes, weights = _merge_phase_samples(phases[order], magnitudes[order], weights[order])
    grid = np.linspace(0.0, 1.0, _PHASE_SAMPLES, endpoint=False)
    if len(phases) < 2:
        fill = float(magnitudes[0]) if len(magnitudes) else float("nan")
        return grid, np.full(grid.shape, fill)
    max_harmonics = int(min(_MAX_FOURIER_HARMONICS, max(1, (len(phases) - 1) // 2)))
    span = float(np.max(magnitudes) - np.min(magnitudes))
    tolerance = max(0.02, 0.08 * span)
    chosen: np.ndarray | None = None
    previous_error = float("inf")
    for harmonics in range(1, max_harmonics + 1):
        coefficients = _fit_fourier(phases, magnitudes, weights, harmonics)
        if coefficients is None:
            break
        predicted = _eval_fourier(phases, coefficients)
        curve = _eval_fourier(grid, coefficients)
        if not np.all(np.isfinite(predicted)) or not np.all(np.isfinite(curve)):
            break
        if not _curve_stays_near_data(magnitudes, curve):
            break
        error = float(np.sqrt(np.average((predicted - magnitudes) ** 2, weights=weights)))
        if error > previous_error:
            break
        chosen = coefficients
        if error <= tolerance or error > previous_error * 0.9:
            break
        previous_error = error
    if chosen is None:
        return grid, _circular_smooth_curve(phases, magnitudes, grid)
    return grid, _eval_fourier(grid, chosen)


def _merge_phase_samples(
    phases: np.ndarray,
    magnitudes: np.ndarray,
    weights: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    merged_phases: list[float] = []
    merged_magnitudes: list[float] = []
    merged_weights: list[float] = []
    for phase, magnitude, weight in zip(phases, magnitudes, weights, strict=True):
        if merged_phases and abs(float(phase) - merged_phases[-1]) < 1e-6:
            total = merged_weights[-1] + float(weight)
            merged_magnitudes[-1] = (merged_magnitudes[-1] * merged_weights[-1] + float(magnitude) * float(weight)) / total
            merged_weights[-1] = total
            continue
        merged_phases.append(float(phase))
        merged_magnitudes.append(float(magnitude))
        merged_weights.append(float(weight))
    return (
        np.asarray(merged_phases, dtype=float),
        np.asarray(merged_magnitudes, dtype=float),
        np.asarray(merged_weights, dtype=float),
    )


def _fit_fourier(
    phases: np.ndarray,
    magnitudes: np.ndarray,
    weights: np.ndarray,
    harmonics: int,
) -> np.ndarray | None:
    columns = [np.ones(len(phases))]
    for harmonic in range(1, harmonics + 1):
        angle = 2.0 * np.pi * harmonic * phases
        columns.append(np.cos(angle))
        columns.append(np.sin(angle))
    design = np.column_stack(columns)
    scale = np.sqrt(np.maximum(weights, 1e-6))
    weighted_design = design * scale[:, None]
    weighted_magnitudes = magnitudes * scale
    penalty = np.zeros(design.shape[1], dtype=float)
    penalty[1:] = 0.15 * float(np.mean(weights))
    normal = weighted_design.T @ weighted_design
    normal.flat[:: normal.shape[0] + 1] += penalty
    try:
        coefficients = np.linalg.solve(normal, weighted_design.T @ weighted_magnitudes)
    except np.linalg.LinAlgError:
        return None
    return np.asarray(coefficients, dtype=float)


def _eval_fourier(phases: np.ndarray, coefficients: np.ndarray) -> np.ndarray:
    values = np.full(len(phases), float(coefficients[0]), dtype=float)
    harmonic = 1
    index = 1
    while index + 1 < len(coefficients):
        angle = 2.0 * np.pi * harmonic * np.asarray(phases, dtype=float)
        values = values + float(coefficients[index]) * np.cos(angle) + float(coefficients[index + 1]) * np.sin(angle)
        harmonic += 1
        index += 2
    return values


def _curve_stays_near_data(magnitudes: np.ndarray, curve: np.ndarray) -> bool:
    low = float(np.min(magnitudes))
    high = float(np.max(magnitudes))
    span = max(0.05, high - low)
    return float(np.min(curve)) >= low - 0.35 * span and float(np.max(curve)) <= high + 0.35 * span


def _circular_smooth_curve(phases: np.ndarray, magnitudes: np.ndarray, grid: np.ndarray) -> np.ndarray:
    fine_count = 256
    fine = np.linspace(0.0, 1.0, fine_count, endpoint=False)
    extended_phase = np.concatenate([phases - 1.0, phases, phases + 1.0])
    extended_magnitude = np.concatenate([magnitudes, magnitudes, magnitudes])
    order = np.argsort(extended_phase, kind="stable")
    interpolated = np.interp(fine, extended_phase[order], extended_magnitude[order])
    sigma = max(1.5, fine_count / 40.0)
    radius = int(math.ceil(3.0 * sigma))
    offsets = np.arange(-radius, radius + 1, dtype=float)
    kernel = np.exp(-0.5 * (offsets / sigma) ** 2)
    kernel /= np.sum(kernel)
    padded = np.concatenate([interpolated[-radius:], interpolated, interpolated[:radius]])
    smoothed = np.convolve(padded, kernel, mode="valid")
    return np.interp(grid, fine, smoothed)


def _phase_from_epoch(epoch: float) -> float:
    phase = math.fmod(float(epoch), 1.0)
    if phase < 0.0:
        phase += 1.0
    if phase >= 1.0:
        phase = 0.0
    return phase


def _sample_is_usable(sample: EpochMagnitudeSample) -> bool:
    return (
        math.isfinite(sample.jd)
        and sample.jd > 0.0
        and math.isfinite(sample.magnitude)
    )


def _band_label(value: object) -> str:
    label = " ".join(str(value or "").split())
    if not label or label == "-":
        return "—"
    return label
