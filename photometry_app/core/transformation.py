"""Validated B/V transformations. Input passbands are explicit, never inferred.

Model: B-b = Z_b + tb*(B-V), V-v = Z_v + tv*(B-V).
The consistent AAVSO color coefficient is Tbv = 1/(1-tb+tv).
Zero points belong to exposures; only color terms belong to an instrument.
"""
from __future__ import annotations

from collections import defaultdict
from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, UTC
import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from photometry_app.core.models import PhotometryMeasurement

SCHEMA_VERSION = 1
MAG_FACTOR = 2.5 / math.log(10)


@dataclass
class StandardObservation:
    star_id: str
    exposure_id: str
    band: str  # Explicit output band, B or V
    instrumental_magnitude: float  # -2.5 log10(flux / exposure seconds)
    instrumental_error: float
    B: float
    V: float
    B_error: float
    V_error: float
    enabled: bool = True
    reason: str = ""
    observation_date: str = ""


class CalibrationSelectionError(ValueError):
    """A failed fit with its screened measurements available for inspection."""

    def __init__(self, message: str, observations: list[StandardObservation]):
        super().__init__(message)
        self.observations = observations


def read_standard_observations(path: Path) -> list[StandardObservation]:
    """Documented long-form CSV, one standard star per exposure and band."""
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = []
        for row in csv.DictReader(stream):
            values = {key: float(row[key]) for key in (
                "instrumental_magnitude", "instrumental_error", "B", "V", "B_error", "V_error"
            )}
            rows.append(StandardObservation(
                star_id=row["star_id"].strip(), exposure_id=row["exposure_id"].strip(),
                band=row["band"].strip(), **values,
                enabled=row.get("enabled", "true").lower() not in {"false", "0", "no"},
                reason=row.get("reason", ""), observation_date=row.get("observation_date", ""),
            ))
    return rows


def write_standard_observations(path: Path, rows: list[StandardObservation]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(StandardObservation.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)


_SNR_REASON = "SNR below "
_CATEGORY_MARKERS = (
    ("Catalog-position mismatch", "Source confusion"),
    ("Automatic outlier", "Outlier"),
    ("Automatic exposure", "Unreliable exposure"),
    ("User selection", "User selection"),
    ("SNR below", "SNR"),
    ("Saturated", "Saturated"),
    ("Overlapping", "Overlapping apertures"),
)


def observation_snr(row: StandardObservation) -> float | None:
    """Recover flux SNR from the stored magnitude uncertainty."""
    if row.instrumental_error <= 0 or not math.isfinite(row.instrumental_error):
        return None
    return MAG_FACTOR / row.instrumental_error


def reason_category(reason: str) -> str:
    """Group a measurement by exclusion type, ignoring the numeric SNR cutoff."""
    text = str(reason or "").strip()
    if not text:
        return "Included"
    found: list[str] = []
    for marker, label in _CATEGORY_MARKERS:
        if marker in text and label not in found:
            found.append(label)
    for part in text.split(";"):
        clause = part.strip()
        if clause and not any(marker in clause for marker, _label in _CATEGORY_MARKERS) and clause not in found:
            found.append(clause)
    return "; ".join(found) or "Other"


def apply_calibration_snr_limit(rows: list[StandardObservation], min_snr: float) -> list[StandardObservation]:
    """Refresh SNR exclusions. A row the user checked or unchecked stays that way."""
    updated: list[StandardObservation] = []
    for row in rows:
        parts = [part.strip() for part in row.reason.split(";") if part.strip()]
        automatic = [part for part in parts if part != "User selection"]
        user_locked = "User selection" in parts or row.enabled == bool(automatic)
        kept = [part for part in automatic if not part.startswith(_SNR_REASON)]
        snr = observation_snr(row)
        if snr is not None and snr < float(min_snr):
            kept.append(f"{_SNR_REASON}{float(min_snr):g}")
        if user_locked and row.enabled == (not kept):
            user_locked = False
        if user_locked:
            if "User selection" not in kept:
                kept.append("User selection")
            updated.append(replace(row, reason="; ".join(kept)))
            continue
        updated.append(replace(row, enabled=not kept, reason="; ".join(kept)))
    return updated


def _valid_standard(row: StandardObservation) -> bool:
    return bool(row.star_id and row.exposure_id and row.band in {"B", "V"}
                and all(math.isfinite(value) for value in (
                    row.instrumental_magnitude, row.instrumental_error, row.B, row.V, row.B_error, row.V_error))
                and min(row.instrumental_error, row.B_error, row.V_error) > 0)


def _fit(rows: list[StandardObservation]) -> tuple[np.ndarray, np.ndarray, dict, np.ndarray]:
    """Iterated GLS, retaining shared catalog error across bands and exposures.

    For residual M-m-Z-t(B-V), derivatives with respect to catalog B,V
    are (1-t,t) in B and (-t,1+t) in V. Catalog errors for a standard
    are shared across every exposure, not independent repeat measurements.
    Catalog B and V errors are assumed independent (catalogs omit covariance).
    """
    exposures = sorted({(row.exposure_id, row.band) for row in rows})
    columns = {key: i + 2 for i, key in enumerate(exposures)}
    design = np.zeros((len(rows), 2 + len(columns)))
    y = np.empty(len(rows))
    groups = defaultdict(list)
    for i, row in enumerate(rows):
        design[i, 0 if row.band == "B" else 1] = row.B - row.V
        design[i, columns[row.exposure_id, row.band]] = 1
        y[i] = (row.B if row.band == "B" else row.V) - row.instrumental_magnitude
        groups[row.star_id].append(i)
    if len(rows) <= design.shape[1] or np.linalg.matrix_rank(design) < design.shape[1]:
        raise ValueError("Not enough color coverage within each exposure to separate slopes and zero points.")
    beta = np.zeros(design.shape[1])
    for _ in range(20):
        normal = np.zeros((design.shape[1], design.shape[1]))
        rhs = np.zeros(design.shape[1])
        chi = 0.0
        for indices in groups.values():
            block = [rows[i] for i in indices]
            deriv = np.array([([1-beta[0], beta[0]] if r.band == "B" else [-beta[1], 1+beta[1]]) for r in block])
            catalog_cov = np.diag([block[0].B_error**2, block[0].V_error**2])
            covariance = np.diag([r.instrumental_error**2 for r in block]) + deriv @ catalog_cov @ deriv.T
            a = design[indices]
            z = y[indices]
            solved = np.linalg.solve(covariance, a)
            normal += a.T @ solved
            rhs += solved.T @ z
            residual = z - a @ beta
            chi += float(residual @ np.linalg.solve(covariance, residual))
        updated = np.linalg.solve(normal, rhs)
        converged = np.max(np.abs(updated-beta)) < 1e-9
        beta = updated
        if converged:
            break
    covariance = np.linalg.inv(normal) * max(1.0, chi / (len(rows)-design.shape[1]))
    return beta[:2], covariance[:2, :2], {key: float(beta[i]) for key, i in columns.items()}, y-design @ beta


def profile_digest(profile: dict) -> str:
    content = {key: value for key, value in profile.items() if key != "id"}
    return hashlib.sha256(json.dumps(content, sort_keys=True, allow_nan=False).encode()).hexdigest()[:20]


def _reject_calibration_outliers(rows, held_out):
    """Use training stars alone to screen measurements and entire exposures.

    Median pair slopes within each exposure provide a seed that a single bad
    star cannot drag away from the ensemble. The final coefficients still use
    GLS and its shared catalog uncertainties. Never clip validation residuals.
    A frame rejected by its training stars is also unavailable for validation.
    """
    groups = defaultdict(list)
    for row in rows:
        if row.enabled and row.star_id not in held_out:
            groups[row.exposure_id, row.band].append(row)
    slopes = {}
    for band in ("B", "V"):
        frame_slopes = []
        for (_, frame_band), group in groups.items():
            if frame_band != band:
                continue
            x = np.array([r.B-r.V for r in group])
            y = np.array([(r.B if band == "B" else r.V)-r.instrumental_magnitude for r in group])
            left, right = np.triu_indices(len(group), 1)
            wide = np.abs(x[right]-x[left]) >= .2
            if np.any(wide):
                frame_slopes.append(float(np.median((y[right[wide]]-y[left[wide]]) / (x[right[wide]]-x[left[wide]]))))
        if not frame_slopes:
            raise ValueError("Not enough color coverage within each exposure to screen standards.")
        slopes[band] = float(np.median(frame_slopes))
    rejected, bad_frames = {}, set()
    for key, group in groups.items():
        band = key[1]
        slope = slopes[band]
        values = np.array([(r.B if band == "B" else r.V)-r.instrumental_magnitude-slope*(r.B-r.V) for r in group])
        residuals = values-np.median(values)
        errors = np.array([math.sqrt(r.instrumental_error**2 +
                          ((1-slope)*r.B_error)**2 + (slope*r.V_error)**2)
                          if band == "B" else math.sqrt(r.instrumental_error**2 +
                          (slope*r.B_error)**2 + ((1+slope)*r.V_error)**2) for r in group])
        # A broadly inconsistent frame cannot be repaired by retaining just a
        # few favorable stars. Require a majority consensus before row clipping.
        inconsistent = np.abs(residuals) > np.maximum(.05, 4.5*errors)
        if len(group) >= 6 and np.mean(inconsistent) > .25:
            bad_frames.add(key)
            continue
        scatter = 1.4826*float(np.median(np.abs(residuals)))
        limits = np.maximum(.05, 4.5*np.sqrt(errors**2+scatter**2))
        for row, residual, limit in zip(group, residuals, limits):
            if abs(residual) > limit and "User selection" not in row.reason:
                rejected[row.star_id, row.exposure_id, row.band] = "Automatic outlier: inconsistent with reference stars"
    # Widespread disagreement can be an unsupported color response, rather
    # than isolated bad weather. Keep it visible to the validation checks.
    bad_frames = {key for key in bad_frames
                  if any(other[1] == key[1] and other not in bad_frames for other in groups)}
    result = []
    for row in rows:
        reason = rejected.get((row.star_id, row.exposure_id, row.band))
        if (row.exposure_id, row.band) in bad_frames:
            reason = "Automatic exposure: inconsistent reference-star measurements"
        if row.enabled and reason and "User selection" not in row.reason:
            row = replace(row, enabled=False, reason="; ".join(filter(None, [row.reason, reason])))
        result.append(row)
    return result


def fit_profile(rows: list[StandardObservation], *, name: str, telescope: str, camera: str,
                optical_setup: str, blue_filter: str, visual_filter: str,
                calibration_date: str, provenance: str, equipment: dict | None = None,
                reject_outliers: bool = True, minimum_snr: float | None = None) -> dict:
    # Reconsider automatic exclusions on refit; explicit user choices persist.
    restored = []
    for row in rows:
        parts = [p.strip() for p in row.reason.split(";") if p.strip()]
        kept = [p for p in parts if not p.startswith(("Automatic outlier:", "Automatic exposure:", "Automatic unpaired:"))]
        if kept != parts and "User selection" not in parts:
            row = replace(row, enabled=not kept, reason="; ".join(kept))
        restored.append(row)
    rows = restored
    if minimum_snr is not None:
        if not math.isfinite(minimum_snr) or minimum_snr <= 0:
            raise ValueError("Minimum SNR must be positive and finite.")
        rows = apply_calibration_snr_limit(rows, minimum_snr)
    if not all(str(value).strip() for value in (name, telescope, camera, optical_setup, blue_filter, visual_filter, provenance)):
        raise ValueError("Name, instrument, optical setup, both input filters, and standards provenance are required.")
    if blue_filter.strip().casefold() == visual_filter.strip().casefold():
        raise ValueError("Blue and visual input filters must be different.")
    unsupported = {"ha", "halpha", "h_alpha", "oiii", "sii", "dualband", "triband", "clear", "cv", "cr", "l", "luminance", "lum"}
    if any(token.strip().casefold().replace("-", "").replace(" ", "") in unsupported for token in (blue_filter, visual_filter)):
        raise ValueError("This B/V model supports photometric B/V or broadband blue/green channels, not clear/luminance or narrowband filters.")
    datetime.strptime(calibration_date, "%Y-%m-%d")
    dated = [datetime.strptime(r.observation_date, "%Y-%m-%d").date() for r in rows if r.enabled and r.observation_date]
    if dated and any(abs((d-datetime.strptime(calibration_date, "%Y-%m-%d").date()).days) > 31 for d in dated):
        raise ValueError("Use calibration observations within 31 days of the profile date; compare separate profiles for longer intervals.")
    invalid = [r for r in rows if r.enabled and not _valid_standard(r)]
    if invalid:
        raise ValueError("Enabled standards need finite magnitudes, positive uncertainties, IDs, and band B or V.")
    active = [r for r in rows if r.enabled]
    catalog = {}
    seen = set()
    for row in active:
        key = (row.star_id, row.exposure_id, row.band)
        if key in seen:
            raise ValueError(f"Duplicate standard measurement: {key}")
        seen.add(key)
        values = (row.B, row.V, row.B_error, row.V_error)
        if row.star_id in catalog and catalog[row.star_id] != values:
            raise ValueError(f"Inconsistent catalog photometry for {row.star_id}.")
        catalog[row.star_id] = values
    paired = {star for star in catalog if {r.band for r in active if r.star_id == star} == {"B", "V"}}
    if len(paired) < 12:
        raise ValueError("At least 12 distinct standards measured in both bands are needed (including validation stars).")
    active = [r for r in active if r.star_id in paired]
    ordered = sorted(paired, key=lambda star: (catalog[star][0]-catalog[star][1], star))
    # Stratified by color; hold out entire stars, never just repeat exposures.
    held_out = set(ordered[1::4])
    initial_colors = [r.B-r.V for r in active if r.star_id not in held_out]
    if max(initial_colors)-min(initial_colors) < .8:
        raise ValueError("Training standards must span at least 0.8 mag in B-V.")
    rows = [replace(r, enabled=False, reason="Automatic unpaired: no usable measurement in both bands")
            if r.enabled and r.star_id not in paired else r for r in rows]
    if reject_outliers:
        rows = _reject_calibration_outliers(rows, held_out)
    usable_bands = defaultdict(set)
    for row in rows:
        if row.enabled:
            usable_bands[row.star_id].add(row.band)
    paired = {star for star, bands in usable_bands.items() if bands == {"B", "V"}}
    rows = [replace(r, enabled=False, reason="Automatic unpaired: no usable measurement in both bands")
            if r.enabled and r.star_id not in paired else r for r in rows]
    active = [r for r in rows if r.enabled]
    if len(paired-held_out) < 9 or len(paired & held_out) < 3:
        raise CalibrationSelectionError(
            f"After quality screening, {len(paired-held_out)} fitting and {len(paired & held_out)} independent validation stars remain. "
            "At least 12 distinct standards are needed: 9 fitting and 3 independent validation stars.", rows)
    training = [r for r in active if r.star_id not in held_out]
    colors = [r.B-r.V for r in training]
    if max(colors)-min(colors) < 0.8:
        raise CalibrationSelectionError("Training standards must span at least 0.8 mag in B-V.", rows)
    try:
        slopes, covariance, zero_points, residual = _fit(training)
    except ValueError as exc:
        raise CalibrationSelectionError(str(exc), rows) from exc
    tb, tv = slopes
    denominator = 1-tb+tv
    if denominator <= 0.2:
        raise ValueError("The fitted color response is degenerate or reversed; this passband pair is unsupported.")
    diagnostics = []
    for row in rows:
        if not _valid_standard(row):
            continue
        key = (row.exposure_id, row.band)
        if key not in zero_points:
            if row.enabled:
                raise ValueError("Every validation exposure must also contain training standards.")
            continue
        coefficient = tb if row.band == "B" else tv
        residual_value = (row.B if row.band == "B" else row.V) - row.instrumental_magnitude - zero_points[key] - coefficient*(row.B-row.V)
        diagnostics.append({**asdict(row), "color": row.B-row.V, "residual": float(residual_value),
                            "validation": row.star_id in held_out})
    validation_rms = {}
    validation_observation_rms = {}
    fit_rms = {}
    failures = []
    for band in ("B", "V"):
        # One residual per star prevents many repeats of one star dominating validation.
        grouped = defaultdict(list)
        for d in diagnostics:
            if d["enabled"] and d["validation"] and d["band"] == band:
                grouped[d["star_id"]].append(d["residual"])
        values = np.array([np.mean(v) for v in grouped.values()])
        validation_rms[band] = float(np.sqrt(np.mean(values**2)))
        individual = [d["residual"] for d in diagnostics if d["enabled"] and d["validation"] and d["band"] == band]
        validation_observation_rms[band] = float(np.sqrt(np.mean(np.square(individual))))
        train_values = [d["residual"] for d in diagnostics if d["enabled"] and not d["validation"] and d["band"] == band]
        fit_rms[band] = float(np.sqrt(np.mean(np.square(train_values))))
        if (validation_rms[band] > 0.03 or validation_observation_rms[band] > 0.03
                or np.max(np.abs(values)) > 0.05 or fit_rms[band] > 0.03):
            failures.append(f"{band}: residuals exceed 0.03 mag RMS / 0.05 mag validation limits.")
    if np.max(np.sqrt(np.diag(covariance))) > 0.1:
        failures.append("Color-term uncertainty exceeds 0.1.")
    profile = dict(schema_version=SCHEMA_VERSION, name=name.strip(), telescope=telescope.strip(), camera=camera.strip(),
                   optical_setup=optical_setup.strip(), input_filters={"B": blue_filter.strip(), "V": visual_filter.strip()},
                   calibration_date=calibration_date, provenance=provenance.strip(),
                   coefficients={"Tb_bv": float(tb), "Tv_bv": float(tv), "Tbv": float(1/denominator)},
                   covariance=covariance.tolist(), color_range=[min(colors), max(colors)],
                   validation_rms=validation_rms, validation_observation_rms=validation_observation_rms,
                   fit_rms=fit_rms, validated=not failures, failures=failures,
                   diagnostics=diagnostics, observations=[asdict(r) for r in rows], reject_outliers=reject_outliers,
                   minimum_snr=minimum_snr,
                   assumptions="Linear B/V; independent catalog B,V errors; negligible differential extinction; linear 2D input channels.")
    if equipment:
        profile["equipment"] = {str(key): str(value) for key, value in equipment.items()}
    profile["id"] = profile_digest(profile)
    return profile


def load_profile(path: Path) -> dict:
    """Recompute imported native profiles; do not trust a serialized validated flag."""
    profile = json.loads(path.read_text(encoding="utf-8"))
    if profile.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported transformation profile version.")
    return fit_profile([StandardObservation(**row) for row in profile["observations"]],
                       **{key: profile[key] for key in ("name", "telescope", "camera", "optical_setup", "calibration_date", "provenance")},
                       blue_filter=profile["input_filters"]["B"], visual_filter=profile["input_filters"]["V"],
                       equipment=profile.get("equipment"), reject_outliers=profile.get("reject_outliers", True),
                       minimum_snr=profile.get("minimum_snr"))


def transformation_applied(measurement: PhotometryMeasurement) -> bool:
    result = measurement.transformation
    return bool(result.get("status") == "applied" and result.get("profile_id") and result.get("band") in {"B", "V"}
                and all(isinstance(result.get(k), (int, float)) and math.isfinite(result[k]) for k in ("magnitude", "error"))
                and result["error"] >= 0)


def _inst_error(row: PhotometryMeasurement) -> float | None:
    if row.flux is not None and row.flux_error is not None and row.flux > 0 and row.flux_error > 0:
        value = MAG_FACTOR * row.flux_error / row.flux
        return value if math.isfinite(value) else None
    return None


def _usable(row: PhotometryMeasurement) -> bool:
    return bool(row.instrumental_magnitude is not None and math.isfinite(row.instrumental_magnitude)
                and _inst_error(row) is not None and not row.is_saturated and not row.is_near_saturated
                and not row.excluded_from_analysis)


def _catalog_bv(row: PhotometryMeasurement) -> tuple[np.ndarray, np.ndarray] | None:
    try:
        values = [row.band_magnitudes[band] for band in ("B", "V")]
        if any(v.get("source") not in {"vsp", "apass"} for v in values):
            return None
        mags = np.array([float(v["mag"]) for v in values])
        errors = np.array([float(v["error"]) for v in values])
        if not np.all(np.isfinite(mags)) or not np.all(np.isfinite(errors)) or np.any(errors <= 0):
            return None
        return mags, errors
    except (KeyError, TypeError, ValueError):
        return None


def apply_transformations(measurements: list[PhotometryMeasurement], settings, *,
                          field_radii: dict[Path, float] | None = None) -> list[PhotometryMeasurement]:
    """Pair complete B/V target+comparison frames; solve the ensemble jointly.

    No nearest-color substitution, extrapolation, cross-night pairing, or Gaia
    fallback. All input magnitudes are retained. Unavailable results explain why.
    """
    rows = [replace(row, transformation={}) for row in measurements]
    if not settings.transformation_enabled:
        return rows
    profile = next((p for p in settings.transformation_profiles if p.get("id") == settings.transformation_profile_id), None)
    failure = "No transformation profile selected."
    if profile:
        try:
            valid = profile.get("validated") and profile["id"] == profile_digest(profile)
            valid = valid and profile["schema_version"] == SCHEMA_VERSION
            failure = "Profile failed validation or was modified; refit the calibration."
            if valid:
                failure = "Instrument or optical setup differs from the calibration profile."
                valid = all(str(getattr(settings, setting)).strip() == profile[key] for setting, key in (
                    ("telescope", "telescope"), ("camera", "camera"), ("transformation_optical_setup", "optical_setup")))
            if valid:
                failure = ""
        except (KeyError, TypeError, ValueError):
            failure = "Invalid transformation profile."
    targets = [r for r in rows if not r.is_reference or r.is_check]
    target_ids = {id(r) for r in targets}
    if failure:
        for row in targets:
            row.transformation = {"status": "unavailable", "reason": failure}
        return rows
    mapping = {value.casefold(): band for band, value in profile["input_filters"].items()}
    by_frame = defaultdict(dict)
    by_target = defaultdict(lambda: defaultdict(list))
    frame_reasons = {}
    frame_offsets = {}
    from photometry_app.core.image_io import is_raw_camera_image_path, read_header_and_shape
    from photometry_app.core.wcs import extract_solved_field
    for path in {r.file_path for r in rows}:
        reason = ("Field exceeds one degree; differential extinction is not modeled."
                  if (field_radii or {}).get(path, 0) > 0.5 else "")
        frame_offsets[path] = 0.0
        if is_raw_camera_image_path(path):
            reason = "RAW synthetic luminance cannot be transformed as an extracted color channel."
        elif path.exists():
            try:
                header, width, height = read_header_and_shape(path)
                exposure = float(header.get("EXPTIME", 0) or 0)
                if math.isfinite(exposure) and exposure > 0:
                    frame_offsets[path] = exposure/2
                airmass = header.get("AIRMASS")
                field = extract_solved_field(header, width, height, path)
                if airmass is not None and (not math.isfinite(float(airmass)) or not 1 <= float(airmass) <= 2):
                    reason = "Airmass is outside the supported first-order transformation range (1 to 2)."
                elif field is not None and field.radius_deg > 0.5:
                    reason = "Field exceeds one degree; differential extinction is not modeled."
                elif int(header.get("NAXIS", 2)) != 2:
                    reason = "Transformation requires a separate linear two-dimensional channel."
            except Exception:
                reason = "Could not verify image metadata for transformation."
        frame_reasons[path] = reason
    def midpoint(row):
        return _utc(row.observation_time) + timedelta(seconds=frame_offsets[row.file_path])
    for row in rows:
        band = mapping.get((row.filter_name or "").strip().casefold())
        if band:
            by_frame[(str(row.file_path), band)][row.source_id] = row
        if id(row) in target_ids:
            row.transformation = {"status": "unavailable", "reason": "No usable paired B/V observation within the time limit."}
            if any(flag.startswith("Derived SNR bin") for flag in row.flags):
                row.transformation["reason"] = "Transform original B/V exposures before any independent per-filter binning."
            elif frame_reasons[row.file_path]:
                row.transformation["reason"] = frame_reasons[row.file_path]
            elif band and _usable(row) and row.observation_time is not None:
                by_target[(row.object_name, row.source_id)][band].append(row)
            elif not band:
                row.transformation["reason"] = "Input filter is not mapped by the selected profile."
            elif row.observation_time is None:
                row.transformation["reason"] = "Observation time is required to pair science exposures."
            else:
                row.transformation["reason"] = "Measurement failed quality checks or has no finite instrumental uncertainty."
    max_seconds = float(settings.transformation_max_pair_seconds)
    if not math.isfinite(max_seconds) or max_seconds <= 0:
        return rows
    cal_date = datetime.strptime(profile["calibration_date"], "%Y-%m-%d").date()
    for bands in by_target.values():
        candidates = []
        visuals = sorted(bands["V"], key=midpoint)
        visual_times = [midpoint(row).timestamp() for row in visuals]
        for blue in bands["B"]:
            timestamp = midpoint(blue).timestamp()
            for visual in visuals[bisect_left(visual_times, timestamp-max_seconds):bisect_right(visual_times, timestamp+max_seconds)]:
                bt, vt = midpoint(blue), midpoint(visual)
                gap = abs((bt-vt).total_seconds())
                if bt.date() == vt.date() and gap <= max_seconds:
                    candidates.append((gap, str(blue.file_path), str(visual.file_path), blue, visual))
        used = set()
        for gap, _, _, blue, visual in sorted(candidates, key=lambda item: item[:3]):
            if id(blue) in used or id(visual) in used:
                continue
            used.update((id(blue), id(visual)))
            reason = ""
            if abs((_utc(blue.observation_time).date()-cal_date).days) > 366:
                reason = "Calibration is more than one year from the observation; recalibrate."
            if reason:
                for row in (blue, visual):
                    row.transformation["reason"] = reason
                continue
            refs_b = by_frame[(str(blue.file_path), "B")]
            refs_v = by_frame[(str(visual.file_path), "V")]
            common = set(blue.comparison_source_ids) & set(visual.comparison_source_ids)
            common.discard(blue.source_id)
            comps = []
            lo, hi = profile["color_range"]
            for source in sorted(common):
                b, v = refs_b.get(source), refs_v.get(source)
                if b is None or v is None or not b.is_reference or not v.is_reference or b.is_check or v.is_check:
                    continue
                catalog = _catalog_bv(b)
                other_catalog = _catalog_bv(v)
                if not _usable(b) or not _usable(v) or catalog is None or other_catalog is None:
                    continue
                if not all(np.array_equal(x, y) for x, y in zip(catalog, other_catalog)):
                    continue
                if lo <= catalog[0][0]-catalog[0][1] <= hi:
                    comps.append((b, v, *catalog))
            if not comps:
                for row in (blue, visual):
                    row.transformation["reason"] = "No shared independent comparison stars with B,V magnitudes/errors inside the calibrated color range."
                continue
            _apply_pair(blue, visual, comps, profile, gap)
    groups = {group: str(i+1) if i < 9999 else "na" for i, group in enumerate(sorted({
        row.transformation["group"] for row in rows if transformation_applied(row)
    }))}
    for row in rows:
        if transformation_applied(row):
            row.transformation["group"] = groups[row.transformation["group"]]
    return rows


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _apply_pair(blue, visual, comps, profile, gap):
    tb, tv = (profile["coefficients"][key] for key in ("Tb_bv", "Tv_bv"))
    matrix = np.array([[1-tb, tb], [-tv, 1+tv]])
    inverse = np.linalg.inv(matrix)
    normal = np.zeros((2, 2))
    rhs = np.zeros(2)
    deriv_rhs = np.zeros((2, 2))
    zero_points = []
    weights = []
    catalog_covariances = []
    for b, v, mags, errors in comps:
        covariance = matrix @ np.diag(errors**2) @ matrix.T + np.diag([_inst_error(b)**2, _inst_error(v)**2])
        weight = np.linalg.inv(covariance)
        zp = matrix @ mags - [b.instrumental_magnitude, v.instrumental_magnitude]
        normal += weight
        rhs += weight @ zp
        deriv_rhs += weight @ (-np.eye(2)*(mags[0]-mags[1]))
        zero_points.append(zp)
        weights.append(weight)
        catalog_covariances.append(matrix @ np.diag(errors**2) @ matrix.T)
    zp_cov = np.linalg.inv(normal)
    zp = zp_cov @ rhs
    inst = np.array([blue.instrumental_magnitude, visual.instrumental_magnitude])
    result = inverse @ (inst+zp)
    color = float(result[0]-result[1])
    if not profile["color_range"][0] <= color <= profile["color_range"][1]:
        for row in (blue, visual):
            row.transformation["reason"] = f"Target B-V ({color:.3f}) is outside the calibrated color range."
        return
    # Catalog errors are shared across time for each comparison star. Keep them
    # separate from photon noise and from the profile's common coefficient error.
    catalog_cov = np.zeros((2, 2))
    catalog_jacobians = {}
    chi_square = 0.0
    for (b, v, mags, errors), weight, point, cat_cov in zip(comps, weights, zero_points, catalog_covariances):
        averaging = zp_cov @ weight
        catalog_cov += averaging @ cat_cov @ averaging.T
        catalog_jacobians[b.source_id] = (inverse @ averaging @ matrix).tolist()
        delta = point-zp
        chi_square += float(delta @ weight @ delta)
    scatter_scale = max(1.0, chi_square/max(1, 2*len(comps)-2))
    random_cov = inverse @ (zp_cov*scatter_scale-catalog_cov + np.diag([_inst_error(blue)**2, _inst_error(visual)**2])) @ inverse.T
    # Slopes affect both the ensemble zero point and the target solution.
    jacobian = inverse @ (zp_cov @ deriv_rhs + np.eye(2)*color)
    systematic_cov = jacobian @ np.asarray(profile["covariance"]) @ jacobian.T + inverse @ catalog_cov @ inverse.T
    floor = np.array([max(profile["validation_rms"][band], profile["fit_rms"][band]) for band in ("B", "V")])
    systematic_cov += np.diag(floor**2)
    covariance = random_cov + systematic_cov
    group = hashlib.sha256(f"{blue.source_id}|{blue.file_path}|{visual.file_path}".encode()).hexdigest()[:10]
    for i, (row, band, companion) in enumerate(((blue, "B", visual), (visual, "V", blue))):
        row.transformation = dict(status="applied", band=band, magnitude=float(result[i]),
                                  error=float(np.sqrt(covariance[i, i])), profile_id=profile["id"],
                                  profile_name=profile["name"], calibration_date=profile["calibration_date"],
                                  color=color, color_error=float(np.sqrt(np.array([1., -1.]) @ covariance @ np.array([1., -1.]))),
                                  random_error=float(np.sqrt(random_cov[i, i])), systematic_error=float(np.sqrt(systematic_cov[i, i])),
                                  coefficient_jacobian=jacobian[i].tolist(), covariance=covariance.tolist(),
                                  catalog_jacobians={key: value[i] for key, value in catalog_jacobians.items()},
                                  comparison_scatter_scale=scatter_scale,
                                  comparison_ids=[b.source_id for b, *_ in comps],
                                  comparison_names=[b.source_name for b, *_ in comps],
                                  companion_file=str(companion.file_path), pair_seconds=gap, group=group,
                                  method="Joint B/V ensemble; negligible differential extinction")
