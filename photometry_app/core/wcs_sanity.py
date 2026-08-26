from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import hashlib
import math
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

from photometry_app.core.catalogs import CatalogService, DEFAULT_GAIA_TILE_OPTIONS, capped_solved_field
from photometry_app.core.local_wcs import _DetectedSource, _detect_image_sources, _unique_nearest_matches
from photometry_app.core.models import CatalogStar, SolvedField
from photometry_app.core.wcs import celestial_wcs, extract_solved_field, validate_wcs


_MIN_DETECTION_SAMPLE = 8
# Stay at or below the Gaia tile threshold so a WCS spot-check is one VizieR cone.
_WCS_SANITY_PROBE_MAX_RADIUS_DEG = float(DEFAULT_GAIA_TILE_OPTIONS.max_radius_deg)
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
    probe_start_fraction: float = 0.10
    quality_sample_max_count: int = 32
    minimum_source_snr: float = 8.0
    max_median_residual_pixels: float = 2.0
    match_tolerance_pixels: float = 3.0
    subtract_coherent_shift: bool = True
    soft_accept_enabled: bool = True
    soft_approval_percent: float = 65.0
    soft_max_median_residual_pixels: float = 1.5
    soft_max_coherent_shift_pixels: float = 2.0
    frame_margin_percent: float = 25.0
    gaia_max_magnitude: float = 18.0
    isolation_fwhm_multiplier: float = 2.5
    ccvals_max_disagreement_pixels: float = 5.0
    ccvals_repair_enabled: bool = True
    # Retained for settings compatibility with older installs.
    candidate_count: int = 10
    min_matches: int = 5
    gaia_min_magnitude: float = 5.0
    max_median_residual_arcsec: float = 3.0
    match_tolerance_arcsec: float = 8.0
    detection_sample_count: int = 80
    skip_brightest_detections: int = 10
    soft_max_median_residual_arcsec: float = 5.0
    soft_max_coherent_shift_arcsec: float = 6.0
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
    median_residual_pixels: float | None = None
    coherent_shift_pixels: float | None = None
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
    max_disagreement_pixels: float = 5.0,
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
            max_disagreement_pixels=max_disagreement_pixels,
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

    _emit(progress_callback, "Checking embedded WCS against quality-selected image stars and Gaia.")
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
            max_disagreement_pixels=options.ccvals_max_disagreement_pixels,
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
    keyword_result = evaluate_ccvals_keyword_sanity(
        header,
        max_disagreement_pixels=options.ccvals_max_disagreement_pixels,
    )
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

    pixel_scales = proj_plane_pixel_scales(wcs) * 3600.0
    mean_scale = float(np.mean(pixel_scales))
    if not np.isfinite(mean_scale) or mean_scale <= 0:
        mean_scale = 1.0
    center_x = width / 2.0
    center_y = height / 2.0

    try:
        detected_sources = _detect_image_sources(source_path)
    except Exception as exc:
        if keyword_result is not None and not keyword_result.passed:
            return keyword_result
        reason = f"WCS sanity check skipped; could not detect image stars: {exc}"
        _emit(progress_callback, reason)
        return WcsSanityCheckResult(passed=True, status="skipped", reasons=[reason])

    probe_samples = _adaptive_probe_samples(
        detected_sources,
        solved_field=solved_field,
        mean_scale_arcsec=mean_scale,
        width=width,
        height=height,
        x_min=x_min,
        x_max=x_max,
        y_min=y_min,
        y_max=y_max,
        options=options,
    )
    if not probe_samples:
        if keyword_result is not None and not keyword_result.passed:
            return keyword_result
        reason = (
            "WCS sanity check skipped; the adaptive probe could not find enough "
            "unsaturated, measurable image stars."
        )
        _emit(progress_callback, reason)
        return WcsSanityCheckResult(passed=True, status="skipped", reasons=[reason])

    service = catalog_service or CatalogService((cache_dir or Path(".")) / "catalogs")
    selected_sources: list[_DetectedSource] = []
    probe_fraction = 1.0
    gaia_stars: list[CatalogStar] = []
    projected: list[tuple[float, float, float, CatalogStar]] = []
    isolated: list[tuple[float, float, float, CatalogStar]] = []
    isolation_pixels = 0.0
    median_fwhm_px: float | None = None
    for probe_index, (candidate_fraction, candidate_radius_px, candidate_sources) in enumerate(probe_samples):
        # Sample cone only — the original solved_field remains the science catalog radius.
        probe_field = capped_solved_field(
            solved_field,
            min(
                _WCS_SANITY_PROBE_MAX_RADIUS_DEG,
                max(1.0e-6, float(solved_field.radius_deg) * candidate_fraction),
            ),
        )
        _emit(
            progress_callback,
            (
                f"WCS sanity adaptive probe {candidate_fraction * 100.0:.0f}% of field radius "
                f"({candidate_radius_px:.0f} px, {probe_field.radius_deg:.4f} deg): "
                f"{len(candidate_sources)} quality-selected detection(s)."
            ),
        )
        _emit_quality_sample_summary(progress_callback, candidate_sources)
        try:
            candidate_gaia = service.query_gaia_stars_limited(
                probe_field,
                maximum_magnitude=options.gaia_max_magnitude,
                row_limit=5000,
                progress_callback=progress_callback,
                progress_label="WCS sanity probe",
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
                    coherent_shift_pixels=keyword_result.coherent_shift_pixels,
                )
            reason = f"WCS sanity check skipped; Gaia query failed: {exc}"
            _emit(progress_callback, reason)
            return WcsSanityCheckResult(passed=True, status="skipped", reasons=[reason])

        candidate_projected = _project_gaia_in_usable_frame(
            candidate_gaia,
            wcs,
            x_min=x_min,
            x_max=x_max,
            y_min=y_min,
            y_max=y_max,
        )
        candidate_projected = [
            item
            for item in candidate_projected
            if float(np.hypot(item[1] - center_x, item[2] - center_y)) <= candidate_radius_px
        ]
        candidate_fwhm = _median_detection_fwhm(candidate_sources)
        candidate_isolation_pixels = _resolved_isolation_pixels(
            candidate_fwhm,
            options.isolation_fwhm_multiplier,
        )
        candidate_isolated = _isolated_projected_gaia(
            candidate_projected,
            candidate_isolation_pixels,
        )
        selected_sources = candidate_sources
        probe_fraction = candidate_fraction
        gaia_stars = candidate_gaia
        projected = candidate_projected
        isolated = candidate_isolated
        isolation_pixels = candidate_isolation_pixels
        median_fwhm_px = candidate_fwhm
        if len(isolated) >= max(_MIN_BIN_GAIA_STARS, int(options.min_matches)):
            break
        if probe_index + 1 < len(probe_samples):
            _emit(
                progress_callback,
                (
                    f"WCS sanity probe found only {len(isolated)} isolated Gaia star(s); "
                    "expanding the relative probe."
                ),
            )

    detected_points = np.asarray(
        [(source.x, source.y) for source in selected_sources],
        dtype=float,
    )
    _emit(
        progress_callback,
        (
            f"WCS sanity detections: selected {len(detected_points)} quality source(s) "
            f"inside the {probe_fraction * 100.0:.0f}% relative probe "
            f"({len(detected_sources)} total detected)."
        ),
    )
    _emit(
        progress_callback,
        f"WCS sanity Gaia catalog: {len(gaia_stars)} star(s) at G <= {options.gaia_max_magnitude:.1f}.",
    )
    tolerance_pixels = max(
        1.0,
        float(options.match_tolerance_pixels),
        0.75 * median_fwhm_px if median_fwhm_px is not None else 0.0,
    )
    hard_residual_limit_pixels = max(0.25, float(options.max_median_residual_pixels))
    approval_fraction = min(1.0, max(0.0, float(options.approval_percent) / 100.0))
    soft_approval_fraction = min(1.0, max(0.0, float(options.soft_approval_percent) / 100.0))
    _emit(
        progress_callback,
        (
            f"WCS sanity detection→Gaia check: {len(detected_points)} quality detection(s); "
            f"match tolerance {tolerance_pixels:.2f} px ({tolerance_pixels * mean_scale:.2f}\"); "
            f"hard approval {options.approval_percent:.0f}%"
            + (
                f", soft accept >= {options.soft_approval_percent:.0f}% "
                f"with residual <= {options.soft_max_median_residual_pixels:.2f} px "
                f"and shift <= {options.soft_max_coherent_shift_pixels:.2f} px"
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
    _emit(
        progress_callback,
        (
            f"WCS sanity projected {len(projected)} Gaia star(s) into the probe region "
            f"({len(isolated)} isolated, neighbor > {isolation_pixels:.2f} px"
            f"{'' if median_fwhm_px is None else f' = {options.isolation_fwhm_multiplier:.2f}× median FWHM'})."
        ),
    )

    reasons: list[str] = []
    if keyword_result is not None:
        reasons.extend(keyword_result.reasons)

    if len(isolated) < max(_MIN_BIN_GAIA_STARS, int(options.min_matches)):
        reason = (
            "WCS sanity check skipped; the adaptive probe did not contain enough "
            "isolated Gaia references."
        )
        reasons.append(reason)
        _emit(progress_callback, reason)
        if keyword_result is not None and not keyword_result.passed:
            return WcsSanityCheckResult(
                passed=False,
                status="keyword_fail",
                reasons=reasons,
                coherent_shift_arcsec=keyword_result.coherent_shift_arcsec,
                coherent_shift_pixels=keyword_result.coherent_shift_pixels,
            )
        return WcsSanityCheckResult(passed=True, status="skipped", reasons=reasons)

    catalog_points = np.asarray([(item[1], item[2]) for item in isolated], dtype=float)
    probe_tolerance = tolerance_pixels * (1.5 if options.subtract_coherent_shift else 1.0)
    detection_indices, catalog_indices, distances_px = _unique_nearest_matches(
        detected_points,
        catalog_points,
        probe_tolerance,
    )
    coherent_shift_pixels = None
    if len(detection_indices) >= max(3, int(options.min_matches)):
        offset_vectors = detected_points[detection_indices] - catalog_points[catalog_indices]
        median_offset_px = np.median(offset_vectors, axis=0)
        coherent_shift_pixels = float(np.hypot(median_offset_px[0], median_offset_px[1]))
        if options.subtract_coherent_shift and coherent_shift_pixels > 0.05:
            shifted_detections = detected_points - median_offset_px
            detection_indices, catalog_indices, distances_px = _unique_nearest_matches(
                shifted_detections,
                catalog_points,
                tolerance_pixels,
            )

    matched = int(len(detection_indices))
    total = int(len(detected_points))
    approval = (matched / total) if total else 0.0
    median_residual_pixels = float(np.median(distances_px)) if matched else None
    median_residual_arcsec = (
        median_residual_pixels * mean_scale if median_residual_pixels is not None else None
    )
    coherent_shift_arcsec = (
        coherent_shift_pixels * mean_scale if coherent_shift_pixels is not None else None
    )

    residual_ok = (
        median_residual_pixels is not None
        and median_residual_pixels <= hard_residual_limit_pixels
    )
    hard_approved = (
        approval >= approval_fraction
        and matched >= max(3, int(options.min_matches))
        and residual_ok
    )
    soft_approved = bool(
        options.soft_accept_enabled
        and matched >= max(3, int(options.min_matches))
        and approval >= soft_approval_fraction
        and median_residual_pixels is not None
        and median_residual_pixels <= float(options.soft_max_median_residual_pixels)
        and coherent_shift_pixels is not None
        and coherent_shift_pixels <= float(options.soft_max_coherent_shift_pixels)
    )
    approved = hard_approved or soft_approved
    residual_text = (
        f", median residual {median_residual_pixels:.2f} px ({median_residual_arcsec:.2f}\")"
        f", coherent shift {coherent_shift_pixels:.2f} px ({coherent_shift_arcsec:.2f}\")"
        if median_residual_pixels is not None and coherent_shift_pixels is not None
        else ""
    )
    if hard_approved:
        verdict = " - approved."
    elif soft_approved:
        verdict = " - soft-accepted (stable pixel residual/shift)."
    else:
        verdict = " - below approval threshold."
    message = (
        f"WCS sanity quality sample: matched {matched}/{total} detections "
        f"({approval * 100.0:.1f}%){residual_text}{verdict}"
    )
    reasons.append(message)
    _emit(progress_callback, message)

    if approved:
        summary = (
            f"WCS sanity check passed using quality-selected, unsaturated detections "
            f"({matched}/{total} = {approval * 100.0:.1f}%"
            f"{'' if hard_approved else ' via soft accept'}, "
            f"median residual {median_residual_pixels:.2f} px ({median_residual_arcsec:.2f}\")"
            f"{'' if coherent_shift_pixels is None else f', coherent shift {coherent_shift_pixels:.2f} px ({coherent_shift_arcsec:.2f}\")'})."
        )
        reasons.append(summary)
        _emit(progress_callback, summary)
        return WcsSanityCheckResult(
            passed=True,
            status="passed",
            reasons=reasons,
            match_count=matched,
            candidate_count=total,
            median_residual_arcsec=median_residual_arcsec,
            coherent_shift_arcsec=coherent_shift_arcsec,
            median_residual_pixels=median_residual_pixels,
            coherent_shift_pixels=coherent_shift_pixels,
        )

    reason = (
        "WCS sanity check failed; the quality-selected sample did not reach "
        f"{options.approval_percent:.0f}% approval with median residual <= "
        f"{hard_residual_limit_pixels:.2f} px"
        + (
            f" or soft-accept (>= {options.soft_approval_percent:.0f}% with stable pixel residual/shift)"
            if options.soft_accept_enabled
            else ""
        )
        + "."
    )
    reasons.append(reason)
    _emit(progress_callback, reason)
    if keyword_result is not None and not keyword_result.passed:
        return WcsSanityCheckResult(
            passed=False,
            status="failed",
            reasons=reasons,
            match_count=matched,
            candidate_count=total,
            median_residual_arcsec=median_residual_arcsec,
            coherent_shift_arcsec=keyword_result.coherent_shift_arcsec,
            median_residual_pixels=median_residual_pixels,
            coherent_shift_pixels=keyword_result.coherent_shift_pixels,
        )
    return WcsSanityCheckResult(
        passed=False,
        status="failed",
        reasons=reasons,
        match_count=matched,
        candidate_count=total,
        median_residual_arcsec=median_residual_arcsec,
        coherent_shift_arcsec=coherent_shift_arcsec,
        median_residual_pixels=median_residual_pixels,
        coherent_shift_pixels=coherent_shift_pixels,
    )


def _adaptive_probe_samples(
    detected_sources: Sequence[_DetectedSource],
    *,
    solved_field: SolvedField,
    mean_scale_arcsec: float,
    width: int,
    height: int,
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    options: WcsSanityOptions,
) -> list[tuple[float, float, list[_DetectedSource]]]:
    center_x = width / 2.0
    center_y = height / 2.0
    samples: list[tuple[float, float, list[_DetectedSource]]] = []
    max_fraction = min(
        1.0,
        _WCS_SANITY_PROBE_MAX_RADIUS_DEG / max(1.0e-6, float(solved_field.radius_deg)),
    )
    for fraction in _adaptive_probe_fractions(options.probe_start_fraction, max_fraction):
        radius_deg = min(
            _WCS_SANITY_PROBE_MAX_RADIUS_DEG,
            max(1.0e-6, float(solved_field.radius_deg) * fraction),
        )
        radius_px = radius_deg * 3600.0 / max(mean_scale_arcsec, 1.0e-9)
        inside = [
            source
            for source in detected_sources
            if x_min <= source.x <= x_max
            and y_min <= source.y <= y_max
            and float(np.hypot(source.x - center_x, source.y - center_y)) <= radius_px
        ]
        selected = _select_quality_detections(
            inside,
            width=width,
            height=height,
            minimum_snr=options.minimum_source_snr,
            max_count=options.quality_sample_max_count,
        )
        if len(selected) >= _MIN_DETECTION_SAMPLE:
            samples.append((fraction, radius_px, selected))
    return samples


def _adaptive_probe_fractions(start_fraction: float, max_fraction: float = 1.0) -> list[float]:
    ceiling = min(1.0, max(0.01, float(max_fraction)))
    current = min(ceiling, max(0.01, float(start_fraction)))
    fractions = [current]
    while current < ceiling - 1.0e-9:
        current = min(ceiling, max(current + 0.05, current * 1.8))
        if current - fractions[-1] > 1.0e-9:
            fractions.append(current)
    return fractions


def _select_quality_detections(
    sources: Sequence[_DetectedSource],
    *,
    width: int,
    height: int,
    minimum_snr: float,
    max_count: int,
) -> list[_DetectedSource]:
    usable = [
        source
        for source in sources
        if np.isfinite(source.x)
        and np.isfinite(source.y)
        and np.isfinite(source.peak)
        and float(source.peak) > 0.0
    ]
    if not usable:
        return []

    snr_filtered = [
        source
        for source in usable
        if source.snr is None
        or not np.isfinite(source.snr)
        or float(source.snr) >= max(1.0, float(minimum_snr))
    ]
    if len(snr_filtered) >= _MIN_DETECTION_SAMPLE:
        usable = snr_filtered

    fwhm_values = np.asarray(
        [
            float(source.fwhm_px)
            for source in usable
            if source.fwhm_px is not None and np.isfinite(source.fwhm_px)
        ],
        dtype=float,
    )
    if len(fwhm_values) >= _MIN_DETECTION_SAMPLE:
        median_fwhm = float(np.median(fwhm_values))
        fwhm_filtered = [
            source
            for source in usable
            if source.fwhm_px is None
            or not np.isfinite(source.fwhm_px)
            or 0.5 * median_fwhm <= float(source.fwhm_px) <= 2.0 * median_fwhm
        ]
        if len(fwhm_filtered) >= _MIN_DETECTION_SAMPLE:
            usable = fwhm_filtered

    peaks = np.asarray([float(source.peak) for source in usable], dtype=float)
    max_peak = float(np.max(peaks))
    near_ceiling = peaks >= max_peak * 0.985
    exact_ceiling = np.isclose(peaks, max_peak, rtol=1.0e-4, atol=1.0)
    if len(usable) >= _MIN_DETECTION_SAMPLE and int(np.count_nonzero(exact_ceiling)) >= max(2, len(usable) // 8):
        unsaturated = [source for source, is_ceiling in zip(usable, near_ceiling) if not is_ceiling]
        if len(unsaturated) >= _MIN_DETECTION_SAMPLE:
            usable = unsaturated
            peaks = np.asarray([float(source.peak) for source in usable], dtype=float)

    if len(usable) >= 12:
        low_peak, high_peak = np.percentile(peaks, [15.0, 90.0])
        moderate = [
            source
            for source in usable
            if float(low_peak) <= float(source.peak) <= float(high_peak)
        ]
        if len(moderate) >= _MIN_DETECTION_SAMPLE:
            usable = moderate
            peaks = np.asarray([float(source.peak) for source in usable], dtype=float)

    target_peak = float(np.percentile(peaks, 65.0))
    ranked = sorted(
        usable,
        key=lambda source: (
            abs(math.log(max(float(source.peak), 1.0e-9)) - math.log(max(target_peak, 1.0e-9))),
            -(float(source.snr) if source.snr is not None and np.isfinite(source.snr) else 0.0),
        ),
    )
    return _spatial_quality_sample(
        ranked,
        width=width,
        height=height,
        limit=max(_MIN_DETECTION_SAMPLE, int(max_count)),
    )


def _spatial_quality_sample(
    ranked_sources: Sequence[_DetectedSource],
    *,
    width: int,
    height: int,
    limit: int,
    grid: int = 4,
) -> list[_DetectedSource]:
    if len(ranked_sources) <= limit:
        return list(ranked_sources)
    grid_size = max(1, int(grid))
    buckets: list[list[_DetectedSource]] = [[] for _ in range(grid_size * grid_size)]
    for source in ranked_sources:
        column = min(grid_size - 1, max(0, int(float(source.x) * grid_size / max(width, 1))))
        row = min(grid_size - 1, max(0, int(float(source.y) * grid_size / max(height, 1))))
        buckets[row * grid_size + column].append(source)
    selected: list[_DetectedSource] = []
    cursor = 0
    while len(selected) < limit:
        progressed = False
        for bucket in buckets:
            if cursor < len(bucket):
                selected.append(bucket[cursor])
                progressed = True
                if len(selected) >= limit:
                    break
        if not progressed:
            break
        cursor += 1
    return selected


def _median_detection_fwhm(sources: Sequence[_DetectedSource]) -> float | None:
    values = [
        float(source.fwhm_px)
        for source in sources
        if source.fwhm_px is not None and np.isfinite(source.fwhm_px) and float(source.fwhm_px) > 0
    ]
    return float(np.median(values)) if values else None


def _resolved_isolation_pixels(median_fwhm_px: float | None, multiplier: float) -> float:
    if float(multiplier) <= 0.0:
        return 0.0
    if median_fwhm_px is None:
        return max(1.0, float(multiplier))
    return max(1.0, float(multiplier) * float(median_fwhm_px))


def _emit_quality_sample_summary(
    progress_callback: Callable[[str], None] | None,
    sources: Sequence[_DetectedSource],
) -> None:
    if not sources:
        return
    peaks = [float(source.peak) for source in sources]
    snr_values = [
        float(source.snr)
        for source in sources
        if source.snr is not None and np.isfinite(source.snr)
    ]
    fwhm = _median_detection_fwhm(sources)
    details = f"background-subtracted peak {min(peaks):.1f}-{max(peaks):.1f}"
    if snr_values:
        details += f", SNR {min(snr_values):.1f}-{max(snr_values):.1f}"
    if fwhm is not None:
        details += f", median FWHM {fwhm:.2f} px"
    _emit(progress_callback, f"WCS sanity source quality: {details}.")


def evaluate_ccvals_keyword_sanity(
    header: Header,
    *,
    max_disagreement_pixels: float = 5.0,
) -> WcsSanityCheckResult | None:
    ccvals = parse_ccvals_center(header)
    if ccvals is None or "CRVAL1" not in header or "CRVAL2" not in header:
        return None
    try:
        crval = SkyCoord(float(header["CRVAL1"]) * u.deg, float(header["CRVAL2"]) * u.deg, frame="icrs")
    except Exception:
        return None
    separation = float(ccvals.separation(crval).arcsec)
    scale_arcsec = _header_mean_pixel_scale_arcsec(header)
    separation_pixels = separation / scale_arcsec if scale_arcsec is not None else None
    threshold_pixels = max(0.1, float(max_disagreement_pixels))
    if separation_pixels is None:
        return WcsSanityCheckResult(
            passed=True,
            status="keyword_skipped",
            reasons=[
                f"CCVALS and CRVAL differ by {separation:.1f}\", but pixel-scale "
                "comparison is unavailable; leaving CRVAL unchanged."
            ],
            coherent_shift_arcsec=separation,
        )
    if separation_pixels <= threshold_pixels:
        return WcsSanityCheckResult(
            passed=True,
            status="keyword_ok",
            reasons=[
                f"CCVALS and CRVAL agree within {separation_pixels:.2f} px "
                f"({separation:.1f}\")."
            ],
            coherent_shift_arcsec=separation,
            coherent_shift_pixels=separation_pixels,
        )
    return WcsSanityCheckResult(
        passed=False,
        status="keyword_fail",
        reasons=[
            f"Embedded WCS keywords disagree: CCVALS and CRVAL differ by "
            f"{separation_pixels:.2f} px ({separation:.1f}\"; "
            f"threshold {threshold_pixels:.2f} px)."
        ],
        coherent_shift_arcsec=separation,
        coherent_shift_pixels=separation_pixels,
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
    max_disagreement_pixels: float = 5.0,
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
    scale_arcsec = _header_mean_pixel_scale_arcsec(header)
    if scale_arcsec is None:
        return None, [
            f"CCVALS repair skipped; CRVAL differs by {separation:.1f}\", but "
            "the WCS pixel scale is unavailable."
        ]
    separation_pixels = separation / scale_arcsec
    threshold_pixels = max(0.1, float(max_disagreement_pixels))
    if separation_pixels <= threshold_pixels:
        return None, [
            f"CCVALS repair skipped; CRVAL already within {separation_pixels:.2f} px "
            f"({separation:.1f}\") of CCVALS."
        ]

    repaired = header.copy()
    repaired["CRVAL1"] = (float(ccvals.ra.deg), "Repaired from CCVALS1")
    repaired["CRVAL2"] = (float(ccvals.dec.deg), "Repaired from CCVALS2")
    repaired["WCSMETH"] = ("CCVALS-REPAIR", "CRVAL replaced from CCVALS sexagesimal center")
    repaired["WCSREPR"] = (separation, "Original CRVAL-CCVALS separation (arcsec)")
    repaired["WCSREPP"] = (separation_pixels, "Original CRVAL-CCVALS separation (pixels)")

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
        f"Repaired embedded WCS by replacing CRVAL with CCVALS "
        f"(was off by {separation_pixels:.2f} px / {separation:.1f}\")."
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
        probe_start_fraction=min(
            1.0,
            max(
                0.01,
                float(getattr(settings, "wcs_sanity_probe_start_percent", 10.0)) / 100.0,
            ),
        ),
        quality_sample_max_count=max(
            _MIN_DETECTION_SAMPLE,
            int(getattr(settings, "wcs_sanity_quality_sample_max_count", 32)),
        ),
        minimum_source_snr=max(
            1.0,
            float(getattr(settings, "wcs_sanity_minimum_source_snr", 8.0)),
        ),
        max_median_residual_pixels=max(
            0.1,
            float(getattr(settings, "wcs_sanity_max_median_residual_pixels", 2.0)),
        ),
        match_tolerance_pixels=max(
            0.5,
            float(getattr(settings, "wcs_sanity_match_tolerance_pixels", 3.0)),
        ),
        subtract_coherent_shift=bool(getattr(settings, "wcs_sanity_subtract_coherent_shift", True)),
        soft_accept_enabled=bool(getattr(settings, "wcs_sanity_soft_accept_enabled", True)),
        soft_approval_percent=min(
            100.0, max(1.0, float(getattr(settings, "wcs_sanity_soft_approval_percent", 65.0)))
        ),
        soft_max_median_residual_pixels=max(
            0.1,
            float(getattr(settings, "wcs_sanity_soft_max_median_residual_pixels", 1.5)),
        ),
        soft_max_coherent_shift_pixels=max(
            0.1,
            float(getattr(settings, "wcs_sanity_soft_max_coherent_shift_pixels", 2.0)),
        ),
        frame_margin_percent=min(
            90.0, max(0.0, float(getattr(settings, "wcs_sanity_edge_margin_percent", 25.0)))
        ),
        gaia_max_magnitude=max(gaia_min, gaia_max),
        gaia_min_magnitude=min(gaia_min, gaia_max),
        candidate_count=max(3, int(getattr(settings, "wcs_sanity_candidate_count", 10))),
        min_matches=max(1, int(getattr(settings, "wcs_sanity_min_matches", 5))),
        isolation_fwhm_multiplier=max(
            0.0,
            float(getattr(settings, "wcs_sanity_isolation_fwhm_multiplier", 2.5)),
        ),
        ccvals_max_disagreement_pixels=max(
            0.1,
            float(getattr(settings, "wcs_sanity_ccvals_max_disagreement_pixels", 5.0)),
        ),
        ccvals_repair_enabled=bool(getattr(settings, "wcs_sanity_ccvals_repair_enabled", True)),
    )


def _header_mean_pixel_scale_arcsec(header: Header) -> float | None:
    try:
        scales = proj_plane_pixel_scales(celestial_wcs(header)) * 3600.0
        mean_scale = float(np.mean(scales))
    except Exception:
        return None
    return mean_scale if np.isfinite(mean_scale) and mean_scale > 0 else None


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

    for key in ("CCVALS1", "CCVALS2", "WCSMETH", "WCSREPR", "WCSREPP"):
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
