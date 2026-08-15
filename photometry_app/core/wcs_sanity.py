from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re
import warnings

import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.io.fits import Header
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales

from photometry_app.core.catalogs import CatalogService
from photometry_app.core.local_wcs import _detect_image_sources, _unique_nearest_matches
from photometry_app.core.models import CatalogStar, SolvedField
from photometry_app.core.wcs import celestial_wcs, extract_solved_field, validate_wcs


_CCVALS_DISAGREE_ARCSEC = 15.0
_MIN_DETECTION_SAMPLE = 8
_MIN_BIN_GAIA_STARS = 5
_DEFAULT_MAGNITUDE_BINS: tuple[tuple[float, float], ...] = (
    (5.0, 10.0),
    (10.0, 12.0),
    (12.0, 14.0),
    (14.0, 16.0),
    (16.0, 18.0),
)


@dataclass(frozen=True, slots=True)
class WcsSanityOptions:
    enabled: bool = True
    approval_percent: float = 90.0
    max_median_residual_arcsec: float = 3.0
    match_tolerance_arcsec: float = 8.0
    detection_sample_count: int = 80
    skip_brightest_detections: int = 10
    subtract_coherent_shift: bool = True
    soft_accept_enabled: bool = True
    soft_approval_percent: float = 65.0
    soft_max_median_residual_arcsec: float = 5.0
    soft_max_coherent_shift_arcsec: float = 6.0
    frame_margin_percent: float = 25.0
    gaia_max_magnitude: float = 18.0
    ccvals_repair_enabled: bool = True
    # Retained for settings compatibility with older installs.
    candidate_count: int = 10
    min_matches: int = 5
    gaia_min_magnitude: float = 5.0
    isolation_arcsec: float = 8.0


@dataclass(frozen=True, slots=True)
class WcsSanityCheckResult:
    passed: bool
    status: str
    reasons: list[str] = field(default_factory=list)
    match_count: int = 0
    candidate_count: int = 0
    median_residual_arcsec: float | None = None
    coherent_shift_arcsec: float | None = None
    approved_bin: tuple[float, float] | None = None


@dataclass(frozen=True, slots=True)
class EmbeddedWcsResolution:
    accepted: bool
    solved_field: SolvedField | None
    reasons: list[str] = field(default_factory=list)
    repaired: bool = False


@dataclass(frozen=True, slots=True)
class EmbeddedWcsPolicy:
    """WCS reading method chosen from one probe frame and applied independently to each frame."""

    mode: str  # accept | ccvals_repair | resolve
    reasons: list[str] = field(default_factory=list)


def diagnose_embedded_wcs_policy(
    source_path: Path,
    header: Header,
    width: int | None,
    height: int | None,
    *,
    cache_dir: Path,
    options: WcsSanityOptions,
    catalog_service: CatalogService | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> EmbeddedWcsPolicy:
    """Inspect one frame to decide which WCS reading method to apply per frame in this folder."""
    resolution = resolve_embedded_wcs_with_sanity(
        source_path,
        header,
        width,
        height,
        cache_dir=cache_dir,
        options=options,
        catalog_service=catalog_service,
        progress_callback=progress_callback,
    )
    if resolution.accepted and resolution.repaired:
        method_reason = "Folder WCS reading method: repair CRVAL from CCVALS on each frame."
        policy = EmbeddedWcsPolicy(mode="ccvals_repair", reasons=[*resolution.reasons, method_reason])
    elif resolution.accepted:
        method_reason = "Folder WCS reading method: read embedded CRVAL/WCS on each frame."
        policy = EmbeddedWcsPolicy(mode="accept", reasons=[*resolution.reasons, method_reason])
    else:
        method_reason = (
            "Folder WCS reading method: re-solve each frame because the probe embedded WCS is unusable."
        )
        policy = EmbeddedWcsPolicy(mode="resolve", reasons=[*resolution.reasons, method_reason])
    _emit(progress_callback, method_reason)
    return policy


def apply_embedded_wcs_policy(
    source_path: Path,
    header: Header,
    width: int | None,
    height: int | None,
    *,
    policy: EmbeddedWcsPolicy,
    cache_dir: Path,
) -> EmbeddedWcsResolution:
    """Apply the chosen WCS reading method to one frame without re-running Gaia spot checks."""
    valid, validation_reasons = validate_wcs(header, source_path)
    reasons = [*validation_reasons, *policy.reasons]

    if policy.mode == "resolve":
        return EmbeddedWcsResolution(accepted=False, solved_field=None, reasons=reasons)

    if not valid:
        return EmbeddedWcsResolution(accepted=False, solved_field=None, reasons=reasons)

    if policy.mode == "ccvals_repair":
        repaired_field, repair_reasons = try_repair_crval_from_ccvals(
            source_path,
            header,
            width,
            height,
            cache_dir=cache_dir,
        )
        reasons.extend(repair_reasons)
        if repaired_field is not None:
            return EmbeddedWcsResolution(
                accepted=True,
                solved_field=repaired_field,
                reasons=reasons,
                repaired=True,
            )
        return EmbeddedWcsResolution(accepted=False, solved_field=None, reasons=reasons)

    solved_field = extract_solved_field(header, width, height, source_path)
    if solved_field is None:
        return EmbeddedWcsResolution(
            accepted=False,
            solved_field=None,
            reasons=[*reasons, "Embedded WCS could not produce a solved field."],
        )
    return EmbeddedWcsResolution(accepted=True, solved_field=solved_field, reasons=reasons)


def resolve_embedded_wcs_with_sanity(
    source_path: Path,
    header: Header,
    width: int | None,
    height: int | None,
    *,
    cache_dir: Path,
    options: WcsSanityOptions,
    catalog_service: CatalogService | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> EmbeddedWcsResolution:
    """Accept, repair, or reject an embedded celestial WCS using Gaia mag-bin spot checks."""
    valid, validation_reasons = validate_wcs(header, source_path)
    if not valid:
        for reason in validation_reasons:
            _emit(progress_callback, reason)
        return EmbeddedWcsResolution(accepted=False, solved_field=None, reasons=list(validation_reasons))

    solved_field = extract_solved_field(header, width, height, source_path)
    if solved_field is None:
        reasons = [*validation_reasons, "Embedded WCS could not produce a solved field."]
        for reason in reasons:
            _emit(progress_callback, reason)
        return EmbeddedWcsResolution(accepted=False, solved_field=None, reasons=reasons)

    if not options.enabled:
        reasons = [*validation_reasons, "WCS sanity check disabled; accepted embedded WCS."]
        for reason in reasons:
            _emit(progress_callback, reason)
        return EmbeddedWcsResolution(accepted=True, solved_field=solved_field, reasons=reasons)

    _emit(progress_callback, "Checking embedded WCS against Gaia stars by magnitude bins.")
    sanity = evaluate_wcs_sanity(
        source_path,
        header,
        solved_field,
        options=options,
        catalog_service=catalog_service,
        cache_dir=cache_dir,
        progress_callback=progress_callback,
    )
    if sanity.passed:
        return EmbeddedWcsResolution(
            accepted=True,
            solved_field=solved_field,
            reasons=[*validation_reasons, *sanity.reasons],
        )

    reasons = [*validation_reasons, *sanity.reasons]
    if options.ccvals_repair_enabled:
        _emit(progress_callback, "Embedded WCS failed Gaia spot-check; trying CCVALS CRVAL repair.")
        repaired_field, repair_reasons = try_repair_crval_from_ccvals(
            source_path,
            header,
            width,
            height,
            cache_dir=cache_dir,
        )
        for reason in repair_reasons:
            _emit(progress_callback, reason)
        reasons.extend(repair_reasons)
        if repaired_field is not None:
            repaired_header = _read_wcs_header(repaired_field.wcs_path)
            repaired_sanity = evaluate_wcs_sanity(
                source_path,
                repaired_header,
                repaired_field,
                options=options,
                catalog_service=catalog_service,
                cache_dir=cache_dir,
                progress_callback=progress_callback,
            )
            reasons.extend(repaired_sanity.reasons)
            if repaired_sanity.passed:
                return EmbeddedWcsResolution(
                    accepted=True,
                    solved_field=repaired_field,
                    reasons=reasons,
                    repaired=True,
                )

    reasons.append("Embedded WCS failed sanity checks; falling back to re-solve.")
    _emit(progress_callback, reasons[-1])
    return EmbeddedWcsResolution(accepted=False, solved_field=None, reasons=reasons)


def evaluate_wcs_sanity(
    source_path: Path,
    header: Header,
    solved_field: SolvedField,
    *,
    options: WcsSanityOptions,
    catalog_service: CatalogService | None = None,
    cache_dir: Path | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> WcsSanityCheckResult:
    keyword_result = evaluate_ccvals_keyword_sanity(header)
    if keyword_result is not None:
        for reason in keyword_result.reasons:
            _emit(progress_callback, reason)

    try:
        wcs = celestial_wcs(header)
    except Exception as exc:
        reason = f"WCS sanity check could not parse celestial WCS: {exc}"
        _emit(progress_callback, reason)
        return WcsSanityCheckResult(passed=False, status="invalid", reasons=[reason])

    width = int(solved_field.width or 0)
    height = int(solved_field.height or 0)
    if width <= 0 or height <= 0:
        reason = "WCS sanity check skipped; image dimensions are unavailable."
        _emit(progress_callback, reason)
        return WcsSanityCheckResult(passed=True, status="skipped", reasons=[reason])

    usable_fraction = _usable_frame_fraction(options.frame_margin_percent)
    x_min, x_max, y_min, y_max = _usable_pixel_bounds(width, height, options.frame_margin_percent)
    _emit(
        progress_callback,
        (
            f"WCS sanity frame margin {options.frame_margin_percent:.0f}% -> "
            f"using central {usable_fraction * 100.0:.0f}% of the frame "
            f"(x={x_min:.0f}-{x_max:.0f}, y={y_min:.0f}-{y_max:.0f})."
        ),
    )

    try:
        detected_sources = _detect_image_sources(source_path)
    except Exception as exc:
        if keyword_result is not None and not keyword_result.passed:
            return keyword_result
        reason = f"WCS sanity check skipped; could not detect image stars: {exc}"
        _emit(progress_callback, reason)
        return WcsSanityCheckResult(passed=True, status="skipped", reasons=[reason])

    usable_detections = sorted(
        (
            source
            for source in detected_sources
            if x_min <= source.x <= x_max and y_min <= source.y <= y_max
        ),
        key=lambda source: float(source.peak),
        reverse=True,
    )
    detected_points = np.asarray(
        [(source.x, source.y) for source in usable_detections],
        dtype=float,
    )
    _emit(
        progress_callback,
        f"WCS sanity detections: {len(detected_points)} source(s) inside the usable frame "
        f"({len(detected_sources)} total detected).",
    )
    if len(detected_points) < 3:
        if keyword_result is not None and not keyword_result.passed:
            return keyword_result
        reason = (
            f"WCS sanity check skipped; only {len(detected_points)} image star(s) "
            "detected inside the usable frame."
        )
        _emit(progress_callback, reason)
        return WcsSanityCheckResult(passed=True, status="skipped", reasons=[reason])

    try:
        service = catalog_service or CatalogService((cache_dir or Path(".")) / "catalogs")
        gaia_stars = service.query_gaia_stars_limited(
            solved_field,
            maximum_magnitude=options.gaia_max_magnitude,
            row_limit=5000,
            progress_callback=progress_callback,
        )
    except Exception as exc:
        if keyword_result is not None and not keyword_result.passed:
            return WcsSanityCheckResult(
                passed=False,
                status="keyword_fail",
                reasons=[
                    *keyword_result.reasons,
                    f"Gaia spot-check unavailable ({exc}); rejecting embedded WCS from keyword disagreement.",
                ],
                coherent_shift_arcsec=keyword_result.coherent_shift_arcsec,
            )
        reason = f"WCS sanity check skipped; Gaia query failed: {exc}"
        _emit(progress_callback, reason)
        return WcsSanityCheckResult(passed=True, status="skipped", reasons=[reason])

    _emit(progress_callback, f"WCS sanity Gaia catalog: {len(gaia_stars)} star(s) at G <= {options.gaia_max_magnitude:.1f}.")

    pixel_scales = proj_plane_pixel_scales(wcs) * 3600.0
    mean_scale = float(np.mean(pixel_scales))
    if not np.isfinite(mean_scale) or mean_scale <= 0:
        mean_scale = 1.0
    search_arcsec = max(0.5, float(options.match_tolerance_arcsec))
    residual_limit_arcsec = max(
        options.max_median_residual_arcsec * 2.0,
        search_arcsec,
    )
    tolerance_pixels = max(1.5, search_arcsec / mean_scale)
    isolation_pixels = (
        0.0
        if float(options.isolation_arcsec) <= 0.0
        else max(1.0, float(options.isolation_arcsec) / mean_scale)
    )
    approval_fraction = min(1.0, max(0.0, float(options.approval_percent) / 100.0))
    soft_approval_fraction = min(1.0, max(0.0, float(options.soft_approval_percent) / 100.0))
    skip_brightest = max(0, int(options.skip_brightest_detections))
    sample_size = max(_MIN_DETECTION_SAMPLE, int(options.detection_sample_count))
    if len(detected_points) <= _MIN_DETECTION_SAMPLE:
        detection_sample = detected_points
        skipped_brightest = 0
    else:
        start = min(skip_brightest, max(0, len(detected_points) - _MIN_DETECTION_SAMPLE))
        detection_sample = detected_points[start : start + sample_size]
        if len(detection_sample) < _MIN_DETECTION_SAMPLE:
            detection_sample = detected_points[:sample_size]
            skipped_brightest = 0
        else:
            skipped_brightest = start
    _emit(
        progress_callback,
        (
            f"WCS sanity detection→Gaia check: sample {len(detection_sample)} detection(s) "
            f"(skipped {skipped_brightest} brightest); "
            f"match tolerance {search_arcsec:.1f}\" ({tolerance_pixels:.2f} px); "
            f"hard approval {options.approval_percent:.0f}%"
            + (
                f", soft accept >= {options.soft_approval_percent:.0f}% "
                f"with residual <= {options.soft_max_median_residual_arcsec:.1f}\" "
                f"and shift <= {options.soft_max_coherent_shift_arcsec:.1f}\""
                if options.soft_accept_enabled
                else ""
            )
            + (
                "; coherent-shift subtraction on."
                if options.subtract_coherent_shift
                else "."
            )
        ),
    )

    projected = _project_gaia_in_usable_frame(
        gaia_stars,
        wcs,
        x_min=x_min,
        x_max=x_max,
        y_min=y_min,
        y_max=y_max,
    )
    isolated = _isolated_projected_gaia(projected, isolation_pixels)
    _emit(
        progress_callback,
        (
            f"WCS sanity projected {len(projected)} Gaia star(s) into the usable frame "
            f"({len(isolated)} isolated, neighbor > {options.isolation_arcsec:.1f}\")."
        ),
    )

    reasons: list[str] = []
    if keyword_result is not None:
        reasons.extend(keyword_result.reasons)

    bins_checked = 0
    for mag_min, mag_max in magnitude_bins_for_options(options):
        # Match detections against Gaia stars brighter than the bin ceiling so a
        # bright-star sample is not forced to find partners only inside a faint bin.
        match_catalog = [item for item in isolated if item[0] < mag_max]
        bin_reference = [item for item in match_catalog if item[0] >= mag_min]
        if len(bin_reference) < _MIN_BIN_GAIA_STARS:
            message = (
                f"WCS sanity G={mag_min:.0f}-{mag_max:.0f}: "
                f"only {len(bin_reference)} isolated Gaia star(s) in bin; trying next bin."
            )
            reasons.append(message)
            _emit(progress_callback, message)
            continue
        if len(match_catalog) < _MIN_BIN_GAIA_STARS:
            message = (
                f"WCS sanity G={mag_min:.0f}-{mag_max:.0f}: "
                f"insufficient isolated Gaia stars brighter than G={mag_max:.0f}; trying next bin."
            )
            reasons.append(message)
            _emit(progress_callback, message)
            continue

        bins_checked += 1
        catalog_points = np.asarray([(item[1], item[2]) for item in match_catalog], dtype=float)
        match_points = detection_sample
        probe_tolerance = tolerance_pixels * (1.5 if options.subtract_coherent_shift else 1.0)
        detection_indices, catalog_indices, distances_px = _unique_nearest_matches(
            match_points,
            catalog_points,
            probe_tolerance,
        )
        coherent_shift = None
        median_offset_px = None
        if len(detection_indices) >= max(3, int(options.min_matches)):
            offset_vectors = match_points[detection_indices] - catalog_points[catalog_indices]
            median_offset_px = np.median(offset_vectors, axis=0)
            coherent_shift = float(np.hypot(median_offset_px[0], median_offset_px[1]) * mean_scale)
            if options.subtract_coherent_shift and coherent_shift is not None and coherent_shift > 0.05:
                shifted_detections = match_points - median_offset_px
                detection_indices, catalog_indices, distances_px = _unique_nearest_matches(
                    shifted_detections,
                    catalog_points,
                    tolerance_pixels,
                )
                if len(detection_indices) >= max(3, int(options.min_matches)):
                    refined_offsets = shifted_detections[detection_indices] - catalog_points[catalog_indices]
                    refined_median = np.median(refined_offsets, axis=0)
                    # Report the pre-subtraction coherent shift; residual is after subtraction.
                    coherent_shift = float(np.hypot(median_offset_px[0], median_offset_px[1]) * mean_scale)
                    _ = refined_median

        matched = int(len(detection_indices))
        total = int(len(detection_sample))
        approval = (matched / total) if total else 0.0
        median_residual = float(np.median(distances_px) * mean_scale) if matched else None

        residual_ok = median_residual is not None and median_residual <= residual_limit_arcsec
        hard_approved = (
            approval >= approval_fraction
            and matched >= max(3, int(options.min_matches))
            and residual_ok
        )
        soft_approved = bool(
            options.soft_accept_enabled
            and matched >= max(3, int(options.min_matches))
            and approval >= soft_approval_fraction
            and median_residual is not None
            and median_residual <= float(options.soft_max_median_residual_arcsec)
            and coherent_shift is not None
            and coherent_shift <= float(options.soft_max_coherent_shift_arcsec)
        )
        approved = hard_approved or soft_approved
        residual_text = (
            f", median residual {median_residual:.2f}\", coherent shift {coherent_shift:.2f}\""
            if median_residual is not None and coherent_shift is not None
            else ""
        )
        if hard_approved:
            verdict = " - approved."
        elif soft_approved:
            verdict = " - soft-accepted (stable residual/shift)."
        else:
            verdict = " - below approval threshold."
        message = (
            f"WCS sanity G<{mag_max:.0f} (bin G={mag_min:.0f}-{mag_max:.0f}): "
            f"matched {matched}/{total} detections ({approval * 100.0:.1f}%){residual_text}"
            f"{verdict}"
        )
        reasons.append(message)
        _emit(progress_callback, message)

        if approved:
            summary = (
                f"WCS sanity check passed using detection→Gaia sample for G={mag_min:.0f}-{mag_max:.0f} "
                f"({matched}/{total} = {approval * 100.0:.1f}%"
                f"{'' if hard_approved else ' via soft accept'}, "
                f"median residual {median_residual:.2f}\""
                f"{'' if coherent_shift is None else f', coherent shift {coherent_shift:.2f}\"'})."
            )
            reasons.append(summary)
            _emit(progress_callback, summary)
            return WcsSanityCheckResult(
                passed=True,
                status="passed",
                reasons=reasons,
                match_count=matched,
                candidate_count=total,
                median_residual_arcsec=median_residual,
                coherent_shift_arcsec=coherent_shift,
                approved_bin=(mag_min, mag_max),
            )

    if bins_checked == 0:
        reason = (
            "WCS sanity check failed; no magnitude bin had enough isolated Gaia stars "
            "to score the detection sample."
        )
    else:
        reason = (
            "WCS sanity check failed; no magnitude bin reached "
            f"{options.approval_percent:.0f}% detection→Gaia approval"
            + (
                f" or soft-accept (>= {options.soft_approval_percent:.0f}% with stable residual/shift)"
                if options.soft_accept_enabled
                else ""
            )
            + f" with median residual <= {residual_limit_arcsec:.1f}\"."
        )
    reasons.append(reason)
    _emit(progress_callback, reason)
    if keyword_result is not None and not keyword_result.passed:
        return WcsSanityCheckResult(
            passed=False,
            status="failed",
            reasons=reasons,
            coherent_shift_arcsec=keyword_result.coherent_shift_arcsec,
        )
    return WcsSanityCheckResult(passed=False, status="failed", reasons=reasons)


def evaluate_ccvals_keyword_sanity(header: Header) -> WcsSanityCheckResult | None:
    ccvals = parse_ccvals_center(header)
    if ccvals is None or "CRVAL1" not in header or "CRVAL2" not in header:
        return None
    try:
        crval = SkyCoord(float(header["CRVAL1"]) * u.deg, float(header["CRVAL2"]) * u.deg, frame="icrs")
    except Exception:
        return None
    separation = float(ccvals.separation(crval).arcsec)
    if separation <= _CCVALS_DISAGREE_ARCSEC:
        return WcsSanityCheckResult(
            passed=True,
            status="keyword_ok",
            reasons=[f"CCVALS and CRVAL agree within {separation:.1f}\"."],
            coherent_shift_arcsec=separation,
        )
    return WcsSanityCheckResult(
        passed=False,
        status="keyword_fail",
        reasons=[
            f"Embedded WCS keywords disagree: CCVALS and CRVAL differ by {separation:.1f}\" "
            f"(threshold {_CCVALS_DISAGREE_ARCSEC:.0f}\")."
        ],
        coherent_shift_arcsec=separation,
    )


def magnitude_bins_for_options(options: WcsSanityOptions) -> list[tuple[float, float]]:
    start = float(options.gaia_min_magnitude)
    end = float(options.gaia_max_magnitude)
    if end < start:
        start, end = end, start
    if end <= start:
        return [(start, start + 2.0)]

    # Prefer the classic 5-10, 10-12, 12-14… ladder when defaults are used.
    if abs(start - 5.0) < 1e-9 and end >= 10.0:
        bins: list[tuple[float, float]] = []
        for mag_min, mag_max in _DEFAULT_MAGNITUDE_BINS:
            if mag_min >= end:
                break
            bins.append((mag_min, min(mag_max, end)))
        return bins or [(start, end)]

    bins = []
    first_hi = min(start + 5.0, end)
    bins.append((start, first_hi))
    lo = first_hi
    while lo < end - 1e-9:
        hi = min(lo + 2.0, end)
        bins.append((lo, hi))
        lo = hi
    return bins


def select_gaia_sanity_candidates(
    gaia_stars: Sequence[CatalogStar],
    wcs: WCS,
    *,
    width: int,
    height: int,
    options: WcsSanityOptions,
) -> list[CatalogStar]:
    """Return Gaia stars inside the usable frame for the first non-empty magnitude bin."""
    x_min, x_max, y_min, y_max = _usable_pixel_bounds(width, height, options.frame_margin_percent)
    projected = _project_gaia_in_usable_frame(
        gaia_stars,
        wcs,
        x_min=x_min,
        x_max=x_max,
        y_min=y_min,
        y_max=y_max,
    )
    for mag_min, mag_max in magnitude_bins_for_options(options):
        selected = [item[3] for item in projected if mag_min <= item[0] < mag_max]
        if selected:
            return selected
    return []


def try_repair_crval_from_ccvals(
    source_path: Path,
    header: Header,
    width: int | None,
    height: int | None,
    *,
    cache_dir: Path,
) -> tuple[SolvedField | None, list[str]]:
    ccvals = parse_ccvals_center(header)
    if ccvals is None:
        return None, ["CCVALS repair unavailable; CCVALS1/CCVALS2 not present or not parseable."]
    if "CRVAL1" not in header or "CRVAL2" not in header:
        return None, ["CCVALS repair unavailable; CRVAL1/CRVAL2 missing."]

    try:
        crval = SkyCoord(float(header["CRVAL1"]) * u.deg, float(header["CRVAL2"]) * u.deg, frame="icrs")
    except Exception as exc:
        return None, [f"CCVALS repair unavailable; could not parse CRVAL: {exc}"]

    separation = float(ccvals.separation(crval).arcsec)
    if separation <= _CCVALS_DISAGREE_ARCSEC:
        return None, [f"CCVALS repair skipped; CRVAL already within {separation:.1f}\" of CCVALS."]

    repaired = header.copy()
    repaired["CRVAL1"] = (float(ccvals.ra.deg), "Repaired from CCVALS1")
    repaired["CRVAL2"] = (float(ccvals.dec.deg), "Repaired from CCVALS2")
    repaired["WCSMETH"] = ("CCVALS-REPAIR", "CRVAL replaced from CCVALS sexagesimal center")
    repaired["WCSREPR"] = (separation, "Original CRVAL-CCVALS separation (arcsec)")

    valid, reasons = validate_wcs(repaired, source_path)
    if not valid:
        return None, [f"CCVALS repair produced an invalid WCS: {'; '.join(reasons)}"]

    cache_dir.mkdir(parents=True, exist_ok=True)
    repaired_path = cache_dir / f"{_source_cache_key(source_path)}_ccvals_repaired_wcs.fits"
    try:
        _write_wcs_sidecar_fits(repaired_path, repaired, width=width, height=height)
    except Exception as exc:
        return None, [f"CCVALS repair could not write a WCS sidecar: {exc}"]
    solved_field = extract_solved_field(repaired, width, height, repaired_path)
    if solved_field is None:
        repaired_path.unlink(missing_ok=True)
        return None, ["CCVALS repair wrote a header but could not extract a solved field."]
    return solved_field, [
        f"Repaired embedded WCS by replacing CRVAL with CCVALS (was off by {separation:.1f}\")."
    ]


def parse_ccvals_center(header: Header) -> SkyCoord | None:
    ra_text = header.get("CCVALS1")
    dec_text = header.get("CCVALS2")
    if ra_text in (None, "") or dec_text in (None, ""):
        return None
    ra_normalized = _normalize_sexagesimal(str(ra_text))
    dec_normalized = _normalize_sexagesimal(str(dec_text))
    if ra_normalized is None or dec_normalized is None:
        return None
    try:
        return SkyCoord(f"{ra_normalized} {dec_normalized}", unit=(u.hourangle, u.deg), frame="icrs")
    except Exception:
        return None


def options_from_settings(settings: object) -> WcsSanityOptions:
    gaia_min = float(getattr(settings, "wcs_sanity_gaia_min_magnitude", 5.0))
    gaia_max = float(getattr(settings, "wcs_sanity_gaia_max_magnitude", 18.0))
    # Prefer the new approval setting; fall back to deriving from legacy min_matches/candidate_count.
    if hasattr(settings, "wcs_sanity_approval_percent"):
        approval_percent = float(getattr(settings, "wcs_sanity_approval_percent"))
    else:
        candidate_count = max(1, int(getattr(settings, "wcs_sanity_candidate_count", 10)))
        min_matches = max(1, int(getattr(settings, "wcs_sanity_min_matches", 5)))
        approval_percent = 100.0 * min_matches / candidate_count
    return WcsSanityOptions(
        enabled=bool(getattr(settings, "wcs_sanity_check_enabled", True)),
        approval_percent=min(100.0, max(1.0, approval_percent)),
        max_median_residual_arcsec=max(
            0.1, float(getattr(settings, "wcs_sanity_max_median_residual_arcsec", 3.0))
        ),
        match_tolerance_arcsec=max(
            0.5, float(getattr(settings, "wcs_sanity_match_tolerance_arcsec", 8.0))
        ),
        detection_sample_count=max(
            _MIN_DETECTION_SAMPLE, int(getattr(settings, "wcs_sanity_detection_sample_count", 80))
        ),
        skip_brightest_detections=max(
            0, int(getattr(settings, "wcs_sanity_skip_brightest_detections", 10))
        ),
        subtract_coherent_shift=bool(getattr(settings, "wcs_sanity_subtract_coherent_shift", True)),
        soft_accept_enabled=bool(getattr(settings, "wcs_sanity_soft_accept_enabled", True)),
        soft_approval_percent=min(
            100.0, max(1.0, float(getattr(settings, "wcs_sanity_soft_approval_percent", 65.0)))
        ),
        soft_max_median_residual_arcsec=max(
            0.1, float(getattr(settings, "wcs_sanity_soft_max_median_residual_arcsec", 5.0))
        ),
        soft_max_coherent_shift_arcsec=max(
            0.1, float(getattr(settings, "wcs_sanity_soft_max_coherent_shift_arcsec", 6.0))
        ),
        frame_margin_percent=min(
            90.0, max(0.0, float(getattr(settings, "wcs_sanity_edge_margin_percent", 25.0)))
        ),
        gaia_max_magnitude=max(gaia_min, gaia_max),
        gaia_min_magnitude=min(gaia_min, gaia_max),
        candidate_count=max(3, int(getattr(settings, "wcs_sanity_candidate_count", 10))),
        min_matches=max(1, int(getattr(settings, "wcs_sanity_min_matches", 5))),
        isolation_arcsec=max(0.0, float(getattr(settings, "wcs_sanity_isolation_arcsec", 8.0))),
        ccvals_repair_enabled=bool(getattr(settings, "wcs_sanity_ccvals_repair_enabled", True)),
    )


def _usable_frame_fraction(frame_margin_percent: float) -> float:
    return 1.0 - min(0.90, max(0.0, float(frame_margin_percent) / 100.0))


def _usable_pixel_bounds(
    width: int,
    height: int,
    frame_margin_percent: float,
) -> tuple[float, float, float, float]:
    usable = _usable_frame_fraction(frame_margin_percent)
    inset = (1.0 - usable) / 2.0
    return width * inset, width * (1.0 - inset), height * inset, height * (1.0 - inset)


def _project_gaia_in_usable_frame(
    gaia_stars: Sequence[CatalogStar],
    wcs: WCS,
    *,
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
) -> list[tuple[float, float, float, CatalogStar]]:
    projected: list[tuple[float, float, float, CatalogStar]] = []
    for star in gaia_stars:
        if star.magnitude is None or not np.isfinite(star.magnitude):
            continue
        try:
            x_value, y_value = wcs.world_to_pixel_values(float(star.ra_deg), float(star.dec_deg))
        except Exception:
            continue
        if not (np.isfinite(x_value) and np.isfinite(y_value)):
            continue
        if x_value < x_min or x_value > x_max or y_value < y_min or y_value > y_max:
            continue
        projected.append((float(star.magnitude), float(x_value), float(y_value), star))
    projected.sort(key=lambda item: (item[0], item[3].source_id))
    return projected


def _isolated_projected_gaia(
    projected: Sequence[tuple[float, float, float, CatalogStar]],
    isolation_pixels: float,
) -> list[tuple[float, float, float, CatalogStar]]:
    """Keep Gaia stars with no projected neighbor inside the isolation radius."""
    if not projected:
        return []
    if isolation_pixels <= 0:
        return list(projected)
    points = np.asarray([(item[1], item[2]) for item in projected], dtype=float)
    isolated: list[tuple[float, float, float, CatalogStar]] = []
    radius_sq = float(isolation_pixels) * float(isolation_pixels)
    for index, item in enumerate(projected):
        deltas = points - points[index]
        # Ignore the star itself; any other neighbor within the radius disqualifies it.
        deltas[index, :] = np.inf
        if np.any(np.sum(np.square(deltas), axis=1) <= radius_sq):
            continue
        isolated.append(item)
    return isolated


def _normalize_sexagesimal(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    text = text.replace(" ", ":")
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        return text
    match = re.fullmatch(
        r"([+-]?)(\d{1,3}):(\d{1,2}):(\d{1,2}(?:\.\d+)?)",
        text,
    )
    if match is None:
        return None
    sign, hours, minutes, seconds = match.groups()
    return f"{sign}{int(hours)}:{int(minutes)}:{float(seconds)}"


def _source_cache_key(source_path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(str(source_path.resolve()).encode("utf-8"))
    return digest.hexdigest()[:16]


def _write_wcs_sidecar_fits(
    path: Path,
    header: Header,
    *,
    width: int | None = None,
    height: int | None = None,
) -> None:
    """Write a WCS-only FITS sidecar, tolerating non-standard source float cards."""
    try:
        clean = celestial_wcs(header).to_header(relax=True)
    except Exception:
        clean = Header()
        for key in (
            "CTYPE1",
            "CTYPE2",
            "CUNIT1",
            "CUNIT2",
            "CRVAL1",
            "CRVAL2",
            "CRPIX1",
            "CRPIX2",
            "CD1_1",
            "CD1_2",
            "CD2_1",
            "CD2_2",
            "CDELT1",
            "CDELT2",
            "CROTA1",
            "CROTA2",
            "PC1_1",
            "PC1_2",
            "PC2_1",
            "PC2_2",
        ):
            if key in header:
                clean[key] = header[key]

    for key in ("CCVALS1", "CCVALS2", "WCSMETH", "WCSREPR"):
        if key not in header:
            continue
        try:
            comment = str(header.comments[key] or "").strip()
        except Exception:
            comment = ""
        clean[key] = (header[key], comment) if comment else header[key]

    if width is not None and int(width) > 0:
        clean["WCSAXES"] = 2
        clean["IMAGEW"] = (int(width), "Image width in pixels")
    if height is not None and int(height) > 0:
        clean["IMAGEH"] = (int(height), "Image height in pixels")

    fits.PrimaryHDU(header=clean).writeto(path, overwrite=True, output_verify="silentfix")


def _read_wcs_header(path: Path) -> Header:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with fits.open(path) as hdul:
            return hdul[0].header.copy()


def _emit(progress_callback: Callable[[str], None] | None, message: str) -> None:
    if progress_callback is not None and message:
        progress_callback(message)
