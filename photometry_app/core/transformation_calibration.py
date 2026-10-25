"""Standard-field image acquisition using the existing plate solver and photometry."""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import math
from pathlib import Path
import re

import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.io.fits import Header
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales

from photometry_app.core.alignment import _WCS_KEY_PATTERN
from photometry_app.core.image_io import is_raw_camera_image_path, read_header_and_shape, read_image_data
from photometry_app.core.models import FileScanResult, PlateSolveResult, WcsStatus
from photometry_app.core.photometry import measure_targets, resolve_aperture_profile
from photometry_app.core.scanner import inspect_fits_file
from photometry_app.core.standard_magnitude import VspServiceError, query_vsp_field, get_band_magnitudes
from photometry_app.core.transformation import StandardObservation, MAG_FACTOR
from photometry_app.core.wcs import celestial_wcs, extract_solved_field


def inspect_calibration_images(paths: list[Path], filter_system: str,
                               progress=lambda message: None, cancelled=lambda: False) -> dict:
    """Suggest channel roles from headers, leaving ambiguous mappings to the user.

    The selected system supplies context: an RGB green channel is not assumed
    to be a standard V filter. No coefficients are inferred from filter names.
    """
    aliases = ({"B": {"b", "blue", "tb"}, "V": {"g", "green", "tg"}}
               if filter_system == "rgb" else {"B": {"b", "blue"}, "V": {"v", "visual"}})
    groups, cameras, telescopes = {}, set(), set()
    canonical = {}
    for index, path in enumerate(dict.fromkeys(paths)):
        if cancelled():
            raise ValueError("Image inspection cancelled.")
        progress(f"Reading image headers {index + 1}/{len(paths)}…")
        if is_raw_camera_image_path(path):
            raise ValueError(f"{path.name}: use separately extracted, linear blue and green images; camera RAW is not supported here.")
        header, _, _ = read_header_and_shape(path)
        if int(header.get("NAXIS", 2)) != 2:
            raise ValueError(f"{path.name}: use separate 2D channel images, rather than a combined color image.")
        token = str(header.get("FILTER") or "").strip()
        if not token:
            raise ValueError(f"{path.name}: missing FILTER header. Add the channel name to the image metadata before calibration.")
        token = canonical.setdefault(token.casefold(), token)
        groups.setdefault(token, []).append(path)
        for key, values in (("INSTRUME", cameras), ("TELESCOP", telescopes)):
            value = str(header.get(key) or "").strip()
            if value:
                values.add(value)
    suggestions = {}
    for band, names in aliases.items():
        candidates = []
        for token in groups:
            parts = set(re.findall(r"[a-z0-9]+", token.casefold()))
            if parts & {"sloan", "sdss", "ha", "oiii", "sii", "luminance", "clear"}:
                continue
            if parts & names:
                candidates.append(token)
        suggestions[band] = candidates[0] if len(candidates) == 1 and filter_system != "custom" else ""
    return dict(groups=groups, blue=suggestions["B"], visual=suggestions["V"],
                cameras=sorted(cameras), telescopes=sorted(telescopes))


def calibrate_standard_images(paths, settings, profile_options, *, min_snr=None,
                              progress=lambda message: None, cancelled=lambda: False):
    """One guided operation; retain measurements even when fitting fails."""
    from photometry_app.core.transformation import fit_profile, apply_calibration_snr_limit

    options = dict(profile_options)
    rows, provenance = measure_standard_images(paths, settings, options["blue_filter"],
                                               options["visual_filter"], progress, cancelled,
                                               20. if min_snr is None else min_snr)
    dates = [row.observation_date for row in rows if row.observation_date]
    if dates:
        options["calibration_date"] = max(dates)
    options["provenance"] = provenance
    result = dict(rows=rows, provenance=provenance, calibration_date=options["calibration_date"], profile=None, error="")
    if cancelled():
        result["error"] = "Calibration cancelled. Measurements are available in Advanced."
        return result
    # Select by reference count and color coverage, never by validation score.
    # Keep the highest cutoff giving the most usable standards if the target
    # cannot be met. Measurements are reused at every step.
    thresholds = (100., 75., 50., 40., 30., 20.) if min_snr is None else (float(min_snr),)
    best_count = -1
    for threshold in thresholds:
        if cancelled():
            result["profile"] = None
            result["error"] = "Calibration cancelled. Measurements are available in Advanced."
            return result
        selected = apply_calibration_snr_limit(rows, threshold)
        progress(f"Selecting reliable standards at SNR {threshold:g} (target: 20 paired references)…")
        try:
            profile = fit_profile(selected, **{**options, "minimum_snr": threshold})
        except ValueError as exc:
            if result["profile"] is None:
                result.update(rows=getattr(exc, "observations", selected), min_snr=threshold, error=str(exc))
            continue
        count = len({r["star_id"] for r in profile["observations"] if r["enabled"]})
        if count > best_count:
            best_count = count
            result.update(profile=profile, rows=[StandardObservation(**r) for r in profile["observations"]],
                          min_snr=threshold, error="")
        if count >= 20:
            break
    progress("Finished checking independent validation stars.")
    return result


def measure_standard_images(paths: list[Path], settings, blue_filter: str, visual_filter: str,
                            progress=lambda message: None, cancelled=lambda: False,
                            min_snr: float = 100.0) -> tuple[list[StandardObservation], str]:
    from photometry_app.core.pipeline import PhotometryPipeline

    def check_cancel():
        if cancelled():
            raise ValueError("Calibration measurement cancelled.")

    mapping = {blue_filter.strip().casefold(): "B", visual_filter.strip().casefold(): "V"}
    if len(mapping) != 2 or not all(mapping):
        raise ValueError("Specify two different input FILTER names before measuring images.")
    scans = []
    for path in paths:
        check_cancel()
        if is_raw_camera_image_path(path):
            raise ValueError("Extract calibrated linear blue and green channels before this step; combined RAW luminance is not supported.")
        header, _, _ = read_header_and_shape(path)
        if int(header.get("NAXIS", 2)) != 2:
            raise ValueError(f"{path.name}: extract a single linear channel first.")
        airmass = header.get("AIRMASS")
        if airmass is not None and (not math.isfinite(float(airmass)) or not 1 <= float(airmass) <= 2):
            raise ValueError(f"{path.name}: this first-order calibration requires airmass from 1 to 2.")
        scan = inspect_fits_file(path, path.parent.name, observation_timezone=settings.observation_timezone)
        exposure = scan.metadata.exposure_seconds
        if exposure is None or not math.isfinite(exposure) or exposure <= 0:
            raise ValueError(f"{path.name}: positive exposure time is required.")
        if scan.metadata.date_obs is None:
            raise ValueError(f"{path.name}: observation date is required.")
        if (scan.metadata.filter_name or "").strip().casefold() not in mapping:
            raise ValueError(f"{path.name}: FILTER does not match either selected input passband.")
        scans.append(scan)
    # Filters can shift the field; never reuse a solve merely because aligned mode is on.
    solve_settings = replace(settings, assume_aligned_images=False)
    progress("Solving standard-field images…")
    solved = PhotometryPipeline()._resolve_summary_fields(scans, solve_settings, progress)
    solved, crop_note, source_paths = limit_wide_calibration_fields(solved, settings, progress, check_cancel)
    rows = []
    charts = set()
    standards_cache = {}
    for scan, solve in solved:
        check_cancel()
        field = solve.solved_field
        if field is None:
            raise ValueError(f"No plate solution for {scan.path.name}.")
        if field.radius_deg > _MAX_CALIBRATION_RADIUS_DEG:
            raise ValueError("This calibration currently supports fields up to one degree across; differential extinction is not fitted.")
        key = (round(field.center_ra_deg, 2), round(field.center_dec_deg, 2), round(field.radius_deg, 2))
        if key not in standards_cache:
            progress("Retrieving AAVSO standard-field magnitudes…")
            try:
                standards_cache[key] = query_vsp_field(field, standard_field=True, raise_service_errors=True)
            except VspServiceError as exc:
                raise ValueError(_vsp_unavailable_message(_cropped_field_name(crop_note), str(exc))) from exc
        standards, chart = standards_cache[key]
        if not standards:
            name = _cropped_field_name(crop_note)
            where = f" for {name}" if name else ""
            raise ValueError(
                f"No AAVSO standards were returned{where}. "
                "Use a recognized standard field such as M67 or NGC 7790."
            )
        if chart:
            charts.add(chart)
        standards = [s for s in standards if all(b in get_band_magnitudes(s) for b in ("B", "V"))]
        progress(f"Measuring {len(standards)} standards in {scan.path.name}…")
        aperture = resolve_aperture_profile(scan.path, field.wcs_path, [], standards, settings)
        measured = measure_targets(scan.path, scan, field.wcs_path, [], standards,
                                   aperture_radius=aperture.aperture_radius,
                                   annulus_inner_radius=aperture.annulus_inner_radius,
                                   annulus_outer_radius=aperture.annulus_outer_radius,
                                   frame_edge_margin_percent=settings.frame_edge_margin_percent,
                                   saturation_filter_enabled=True, settings=settings,
                                   adaptive_frame_fwhm_pixels=aperture.fwhm_pixels)
        by_id = {star.source_id: star for star in standards}
        position_rejections = _standard_position_rejections(measured, by_id, field.wcs_path)
        for row in measured:
            if row.instrumental_magnitude is None or row.flux is None or row.flux <= 0 or row.flux_error is None or row.flux_error <= 0:
                continue
            bands = get_band_magnitudes(by_id[row.source_id])
            if any(bands[b].get("error") is None or float(bands[b]["error"]) <= 0 for b in ("B", "V")):
                continue
            reasons = []
            if row.is_saturated or row.is_near_saturated:
                reasons.append("Saturated or near saturation")
            if row.flux/row.flux_error < min_snr:
                reasons.append(f"SNR below {min_snr:g}")
            if row.flags:
                reasons.extend(row.flags)
            if row.source_id in position_rejections:
                reasons.append(position_rejections[row.source_id])
            if any(other is not row and other.source_id not in position_rejections
                   and math.hypot(other.x-row.x, other.y-row.y) < 2*aperture.aperture_radius for other in measured):
                reasons.append("Overlapping standard-star apertures")
            exposure_path = source_paths.get(scan.path.resolve(), scan.path.resolve())
            rows.append(StandardObservation(
                row.source_id, str(exposure_path), mapping[scan.metadata.filter_name.strip().casefold()],
                row.instrumental_magnitude + 2.5*math.log10(scan.metadata.exposure_seconds),
                MAG_FACTOR*row.flux_error/row.flux,
                float(bands["B"]["mag"]), float(bands["V"]["mag"]), float(bands["B"]["error"]), float(bands["V"]["error"]),
                enabled=not reasons, reason="; ".join(reasons), observation_date=scan.metadata.date_obs.date().isoformat(),
            ))
    if not rows:
        raise ValueError("No usable standard-star measurements were obtained.")
    provenance = "AAVSO VSP standard fields; charts " + ", ".join(sorted(charts))
    if crop_note:
        provenance = f"{provenance} {crop_note}"
    return rows, provenance


def _standard_position_rejections(measured, standards, wcs_path):
    """Reject centroids inconsistent with the frame's common catalog offset.

    In crowded fields, unconstrained recentring can measure a bright neighbor
    instead of the faint standard. A common WCS offset is harmless; a source
    that moves differently from the ensemble is not a reliable association.
    This check uses positions only, including for held-out validation stars.
    """
    quality = [r for r in measured if r.source_id in standards and not r.flags
               and not r.is_saturated and not r.is_near_saturated
               and r.flux is not None and r.flux_error is not None and r.flux_error > 0
               and r.flux/r.flux_error >= 20 and math.isfinite(r.x) and math.isfinite(r.y)]
    if len(quality) < 6:
        return {}
    header, _, _ = read_header_and_shape(wcs_path)
    wcs = celestial_wcs(header)
    offsets = {}
    for row in measured:
        star = standards.get(row.source_id)
        if star is None:
            continue
        x, y = _world_pixel(wcs, star.ra_deg, star.dec_deg)
        offset = np.array([row.x-x, row.y-y])
        if np.all(np.isfinite(offset)):
            offsets[row.source_id] = offset
    samples = np.array([offsets[r.source_id] for r in quality if r.source_id in offsets])
    if len(samples) < 6:
        return {}
    center = np.median(samples, axis=0)
    scatter = 1.4826*np.median(np.abs(samples-center), axis=0)
    limit = max(1.5, 4*float(np.linalg.norm(scatter)))
    return {star: f"Catalog-position mismatch: {np.linalg.norm(offset-center):.2f} px from ensemble (limit {limit:.2f} px)"
            for star, offset in offsets.items() if np.linalg.norm(offset-center) > limit}


_MAX_CALIBRATION_RADIUS_DEG = 0.5
_ALIGNMENT_TOLERANCE_PX = 2.0
_ALIGNMENT_SAMPLE_OFFSET_DEG = 0.2
_HEADER_STRUCTURAL_KEYS = {
    "SIMPLE", "BITPIX", "NAXIS", "NAXIS1", "NAXIS2", "NAXIS3", "EXTEND",
    "PCOUNT", "GCOUNT", "CHECKSUM", "DATASUM", "BSCALE", "BZERO", "BLANK",
}
_GENERIC_OBJECT_NAMES = frozenset({
    "light", "lights", "calibration", "calib", "frame", "frames", "image", "images",
    "science", "master", "object", "target", "unknown", "field", "fields", "test",
    "data", "new", "tmp", "temp",
})


@dataclass(frozen=True, slots=True)
class _StandardField:
    name: str
    ra_deg: float
    dec_deg: float
    aliases: frozenset[str]


def _sexagesimal(ra: str, dec: str) -> tuple[float, float]:
    hours, minutes, seconds = (float(part) for part in ra.split(":"))
    sign = -1.0 if dec.startswith("-") else 1.0
    body = dec[1:] if dec[:1] in "+-" else dec
    degrees, arcminutes, arcseconds = (float(part) for part in body.split(":"))
    return (hours + minutes / 60.0 + seconds / 3600.0) * 15.0, sign * (degrees + arcminutes / 60.0 + arcseconds / 3600.0)


def _field(name: str, ra: str, dec: str, *aliases: str) -> _StandardField:
    ra_deg, dec_deg = _sexagesimal(ra, dec)
    keys = {_compact_name(name), *(_compact_name(alias) for alias in aliases)}
    return _StandardField(name, ra_deg, dec_deg, frozenset(key for key in keys if key))


def _compact_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.casefold())


# AAVSO VSP standard-field centers. M67 uses the standard-field chart center.
_STANDARD_FIELDS = (
    _StandardField("M67", 132.838375, 11.800667, frozenset({"m67", "ngc2682", "messier67"})),
    _field("NGC 7790", "23:58:23.2", "+61:12:25", "cecas"),
    _field("SA92", "00:55:15.5", "+00:47:26"),
    _field("F 11", "01:04:25.0", "+04:12:31"),
    _field("SA93", "01:55:01.8", "+00:49:52"),
    _field("PG0220+132", "02:23:36.9", "+13:27:47"),
    _field("PG0231+051", "02:33:38.5", "+05:19:00"),
    _field("F 24", "02:35:17.0", "+03:42:54"),
    _field("SA94", "02:55:56.1", "+00:43:29"),
    _field("SA95", "03:54:26.8", "+00:13:48"),
    _field("SA96", "04:52:17.3", "+00:03:50"),
    _field("SA97", "05:57:55.8", "+00:05:54"),
    _field("SA98", "06:52:03.5", "-00:19:27"),
    _field("Ru149", "07:24:15.2", "-00:32:12"),
    _field("Ru152", "07:29:59.9", "-02:05:38"),
    _field("SA99", "07:54:50.0", "-00:33:13"),
    _field("SA100", "08:53:14.3", "-00:37:56"),
    _field("PG0918+029", "09:21:32.1", "+02:47:01"),
    _field("PG0942-029", "09:45:11.5", "-03:08:04"),
    _field("SA101", "09:56:35.0", "-00:29:46"),
    _field("GD 108", "10:00:47.8", "-07:32:41"),
    _field("PG1047+033", "10:50:08.3", "-00:01:18"),
    _field("SA102", "10:56:04.1", "-00:30:46"),
    _field("G163-50", "11:07:44.4", "-05:12:50", "g16350", "g1635051"),
    _field("SA103", "11:56:30.1", "-00:35:35"),
    _field("SA104", "12:43:10.2", "-00:33:59"),
    _field("PG1323-086", "13:25:52.4", "-08:49:47"),
    _field("SA105", "13:37:43.6", "-00:20:08"),
    _field("PG1407-013", "14:10:30.0", "-01:27:40"),
    _field("SA106", "14:42:10.5", "-00:17:41"),
    _field("PG1525-071", "15:28:14.0", "-07:15:35"),
    _field("SA107", "15:38:48.6", "-00:13:29"),
    _field("PG1633+099", "16:35:34.6", "+09:48:23"),
    _field("SA108", "16:37:19.0", "-00:14:22"),
    _field("PG1657+078", "16:59:31.3", "+07:43:04"),
)


def _cropped_field_name(note: str) -> str:
    marker = "around "
    if marker not in note:
        return ""
    return note.split(marker, 1)[1].split(" and ", 1)[0].rstrip(".").strip()


def _vsp_unavailable_message(field_name: str, reason: str) -> str:
    where = f" for {field_name}" if field_name else ""
    return (
        f"AAVSO VSP did not return standards{where}. {reason} "
        "The images were recognized. Try again when the AAVSO chart service is available."
    )


def limit_wide_calibration_fields(solved, settings, progress=lambda message: None, check_cancel=lambda: None):
    """Crop fields wider than one degree around the standard field, then align if required.

    Each solved frame is cropped in its own pixels, so dither and filter shift stay
    centered without resampling. Resampling is used only when the frames do not
    already share scale and orientation.
    """
    if not solved or any(solve.solved_field is None for _scan, solve in solved):
        return solved, "", {}
    if all(solve.solved_field.radius_deg <= _MAX_CALIBRATION_RADIUS_DEG for _scan, solve in solved):
        return solved, "", {}
    check_cancel()
    target = _resolve_standard_field(solved)
    progress(f"The frame is wider than one degree. Cropping around {target.name}…")
    source_paths: dict[Path, Path] = {}
    cropped = []
    for index, (scan, _solve) in enumerate(solved, start=1):
        check_cancel()
        if _solve.solved_field.radius_deg <= _MAX_CALIBRATION_RADIUS_DEG:
            cropped.append((scan, _solve))
            continue
        progress(f"Cropping {target.name} in {scan.path.name} ({index}/{len(solved)})…")
        output, field = _crop_frame_around_field(scan, _solve, target, settings)
        source_paths[output.resolve()] = scan.path.resolve()
        cropped.append(_solved_copy(output, scan, settings, field))
    if _frames_share_orientation(cropped, target):
        return cropped, f"Cropped to one degree around {target.name}.", source_paths
    progress(f"Aligning {target.name} frames onto one solution…")
    aligned = _align_frames(cropped, source_paths, settings, progress, check_cancel, target)
    return aligned, f"Cropped to one degree around {target.name} and aligned.", source_paths


def _resolve_standard_field(solved) -> _StandardField:
    names = _object_names(solved)
    named = _builtin_field(names)
    if named is not None:
        if not _field_in_any_frame(solved, named):
            raise ValueError(
                f"{named.name} is outside these frames, so they cannot be cropped around it."
            )
        return named
    for name in _header_object_names(solved):
        if not _looks_like_catalog_name(name):
            continue
        located = _query_simbad_field(name)
        if located is None:
            continue
        if not _field_in_any_frame(solved, located):
            raise ValueError(f"{located.name} is outside these frames, so they cannot be cropped around it.")
        return located
    inside = [field for field in _STANDARD_FIELDS if _field_in_any_frame(solved, field)]
    if not inside:
        raise ValueError(
            "This field is wider than one degree. Name the standard field in the OBJECT header, "
            "for example M67 or NGC 7790, so the frames can be centered and cropped."
        )
    return min(inside, key=lambda field: _distance_from_pointing(solved, field))


def _object_names(solved) -> list[str]:
    names = []
    for scan, _solve in solved:
        if scan.metadata.object_name:
            names.append(scan.metadata.object_name)
        names.append(scan.path.parent.name)
        names.append(scan.path.stem)
    return names


def _header_object_names(solved) -> list[str]:
    names = []
    for scan, _solve in solved:
        header, _width, _height = read_header_and_shape(scan.path)
        name = str(header.get("OBJECT") or "").strip()
        if name:
            names.append(name)
    return names


def _looks_like_catalog_name(name: str) -> bool:
    tokens = re.findall(r"[a-z0-9]+", name.casefold())
    return bool(tokens) and any(token not in _GENERIC_OBJECT_NAMES and not token.isdigit() for token in tokens)


def _name_keys(name: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", name.casefold())
    keys = {"".join(tokens)}
    keys.update(tokens)
    keys.update(tokens[index] + tokens[index + 1] for index in range(len(tokens) - 1))
    return {key for key in keys if key}


def _builtin_field(names: list[str]) -> _StandardField | None:
    for name in names:
        keys = _name_keys(name)
        for field in _STANDARD_FIELDS:
            if keys & field.aliases:
                return field
    return None


def _query_simbad_field(name: str) -> _StandardField | None:
    try:
        from astroquery.simbad import Simbad
    except Exception:
        return None
    simbad = Simbad()
    try:
        simbad.TIMEOUT = 20
    except Exception:
        pass
    simbad.ROW_LIMIT = 1
    try:
        table = simbad.query_object(name)
    except Exception:
        return None
    if table is None or len(table) == 0:
        return None
    row = table[0]
    ra = _row_value(row, ("ra", "RA", "RA_d"))
    dec = _row_value(row, ("dec", "DEC", "DEC_d"))
    if ra is None or dec is None:
        return None
    try:
        coord = SkyCoord(float(ra) * u.deg, float(dec) * u.deg)
    except (TypeError, ValueError):
        try:
            coord = SkyCoord(str(ra), str(dec), unit=(u.hourangle, u.deg))
        except Exception:
            return None
    label = str(name).strip() or "Standard field"
    return _StandardField(label, float(coord.ra.deg), float(coord.dec.deg), frozenset({_compact_name(label)}))


def _row_value(row, names: tuple[str, ...]):
    for name in names:
        if name in row.colnames:
            value = row[name]
            if value is not None and str(value).strip():
                return value
    return None


def _field_in_any_frame(solved, field: _StandardField) -> bool:
    return any(_pixel_in_frame(solve, field.ra_deg, field.dec_deg) for _scan, solve in solved)


def _pixel_in_frame(solve, ra_deg: float, dec_deg: float) -> bool:
    try:
        wcs, width, height = _solved_wcs(solve)
        x, y = _world_pixel(wcs, ra_deg, dec_deg)
    except Exception:
        return False
    return 0.0 <= x < width and 0.0 <= y < height


def _distance_from_pointing(solved, field: _StandardField) -> float:
    distances = []
    for _scan, solve in solved:
        if solve.solved_field is None:
            continue
        origin = SkyCoord(solve.solved_field.center_ra_deg * u.deg, solve.solved_field.center_dec_deg * u.deg)
        target = SkyCoord(field.ra_deg * u.deg, field.dec_deg * u.deg)
        distances.append(float(origin.separation(target).deg))
    return min(distances) if distances else math.inf


def _crop_frame_around_field(scan: FileScanResult, solve: PlateSolveResult, target: _StandardField, settings):
    image, source_header, wcs = _image_and_wcs(scan, solve)
    height, width = image.shape
    x, y = _world_pixel(wcs, target.ra_deg, target.dec_deg)
    if not (0.0 <= x < width and 0.0 <= y < height):
        raise ValueError(f"{scan.path.name} does not contain {target.name}, so it cannot be cropped around that field.")
    crop_w, crop_h = _crop_dimensions(width, height, _pixel_scale_deg(wcs))
    output = _crop_output_path(scan.path, settings)
    output.parent.mkdir(parents=True, exist_ok=True)
    field = None
    for _attempt in range(8):
        x0, y0, x1, y1 = _centered_bounds(x, y, crop_w, crop_h, width, height)
        if x1 - x0 < 2 or y1 - y0 < 2 or not (x0 <= x < x1 and y0 <= y < y1):
            break
        cropped = np.ascontiguousarray(image[y0:y1, x0:x1])
        sliced = wcs.slice((slice(y0, y1), slice(x0, x1)))
        header = _header_with_wcs(
            source_header,
            sliced,
            f"Citizen Astronomy cropped this frame to one degree around {target.name}.",
            target.name,
        )
        fits.PrimaryHDU(data=cropped, header=header).writeto(output, overwrite=True)
        field = _field_from_file(output)
        if field is not None and field.radius_deg <= _MAX_CALIBRATION_RADIUS_DEG:
            return output, field
        crop_w = max(2, int(crop_w * 0.9))
        crop_h = max(2, int(crop_h * 0.9))
    raise ValueError(f"{scan.path.name}: could not crop a one-degree field around {target.name}.")


def _crop_dimensions(width: int, height: int, pixel_scale_deg: float) -> tuple[int, int]:
    max_diagonal = 2.0 * _MAX_CALIBRATION_RADIUS_DEG * 0.98 / pixel_scale_deg
    aspect = width / height
    crop_h = max_diagonal / math.hypot(aspect, 1.0)
    crop_w = crop_h * aspect
    return max(2, min(width, int(math.floor(crop_w)))), max(2, min(height, int(math.floor(crop_h))))


def _centered_bounds(x: float, y: float, crop_w: int, crop_h: int, width: int, height: int) -> tuple[int, int, int, int]:
    x0 = int(round(x - crop_w / 2.0))
    y0 = int(round(y - crop_h / 2.0))
    x0 = min(max(0, x0), width - crop_w)
    y0 = min(max(0, y0), height - crop_h)
    return x0, y0, x0 + crop_w, y0 + crop_h


def _crop_output_path(source: Path, settings) -> Path:
    token = hashlib.sha1(str(source.resolve()).encode()).hexdigest()[:8]
    return Path(settings.cache_dir) / "transformation_fields" / f"{source.stem}-{token}.fits"


def _image_and_wcs(scan: FileScanResult, solve: PlateSolveResult):
    image = np.squeeze(np.asarray(read_image_data(scan.path, dtype=None)))
    if image.ndim != 2:
        raise ValueError(f"{scan.path.name}: calibration cropping expects a single 2D image.")
    wcs, width, height = _solved_wcs(solve)
    if width != image.shape[1] or height != image.shape[0]:
        raise ValueError(
            f"{scan.path.name}: the plate solution is {width}×{height}, but the image is {image.shape[1]}×{image.shape[0]}."
        )
    image_header, _image_width, _image_height = read_header_and_shape(scan.path)
    return image, image_header, wcs


def _solved_wcs(solve: PlateSolveResult) -> tuple[WCS, int, int]:
    field = solve.solved_field
    if field is None:
        raise ValueError("A plate solution is required before cropping.")
    header, width, height = read_header_and_shape(field.wcs_path)
    if width is None or height is None:
        raise ValueError(f"{field.wcs_path.name} has no image dimensions.")
    return celestial_wcs(header), int(width), int(height)


def _world_pixel(wcs: WCS, ra_deg: float, dec_deg: float) -> tuple[float, float]:
    x, y = wcs.world_to_pixel_values(ra_deg, dec_deg)
    return float(np.asarray(x).reshape(-1)[0]), float(np.asarray(y).reshape(-1)[0])


def _pixel_scale_deg(wcs: WCS) -> float:
    scales = proj_plane_pixel_scales(wcs) * u.deg
    scale = float(np.mean(np.abs(scales.to_value(u.deg))))
    if not math.isfinite(scale) or scale <= 0.0:
        raise ValueError("The plate solution has no usable pixel scale.")
    return scale


def _header_with_wcs(source_header: Header, wcs: WCS, history: str, object_name: str = "") -> Header:
    output = Header()
    for card in source_header.cards:
        keyword = str(card.keyword)
        if keyword in _HEADER_STRUCTURAL_KEYS or _WCS_KEY_PATTERN.match(keyword):
            continue
        try:
            output.append((card.keyword, card.value, card.comment))
        except Exception:
            continue
    output.update(wcs.to_header(relax=True))
    if object_name and not str(output.get("OBJECT") or "").strip():
        output["OBJECT"] = object_name
    output.add_history(history)
    return output


def _field_from_file(path: Path):
    header, width, height = read_header_and_shape(path)
    return extract_solved_field(header, width, height, path)


def _solved_copy(path: Path, scan: FileScanResult, settings, field):
    if field is None or field.radius_deg > _MAX_CALIBRATION_RADIUS_DEG:
        raise ValueError(f"{scan.path.name}: the cropped field is still wider than one degree.")
    copied = inspect_fits_file(path, scan.object_folder, observation_timezone=settings.observation_timezone)
    if copied.metadata.exposure_seconds is None or copied.metadata.date_obs is None:
        raise ValueError(f"{scan.path.name}: the cropped frame lost its exposure time or observation date.")
    result = PlateSolveResult(source_path=path, status=WcsStatus.SOLVED, solved_field=field, reasons=[])
    return copied, result


def _frames_share_orientation(frames, target: _StandardField) -> bool:
    if len(frames) < 2:
        return True
    origin = SkyCoord(target.ra_deg * u.deg, target.dec_deg * u.deg)
    samples = (
        origin.spherical_offsets_by(_ALIGNMENT_SAMPLE_OFFSET_DEG * u.deg, 0 * u.deg),
        origin.spherical_offsets_by(0 * u.deg, _ALIGNMENT_SAMPLE_OFFSET_DEG * u.deg),
    )
    offsets = [_orientation_offsets(scan, target, samples) for scan, _solve in frames]
    reference = offsets[0]
    for other in offsets[1:]:
        for start, end in zip(reference, other):
            if math.hypot(start[0] - end[0], start[1] - end[1]) > _ALIGNMENT_TOLERANCE_PX:
                return False
    return True


def _orientation_offsets(scan: FileScanResult, target: _StandardField, samples) -> tuple[tuple[float, float], tuple[float, float]]:
    header, _width, _height = read_header_and_shape(scan.path)
    wcs = celestial_wcs(header)
    x0, y0 = _world_pixel(wcs, target.ra_deg, target.dec_deg)
    offsets = []
    for sample in samples:
        x, y = _world_pixel(wcs, float(sample.ra.deg), float(sample.dec.deg))
        offsets.append((x - x0, y - y0))
    return offsets[0], offsets[1]


def _align_frames(frames, source_paths: dict[Path, Path], settings, progress, check_cancel, target: _StandardField):
    from photometry_app.core.alignment import _reproject_image_data

    reference_scan, reference_solve = _best_centered_frame(frames, target)
    reference_header, reference_width, reference_height = read_header_and_shape(reference_scan.path)
    reference_wcs = celestial_wcs(reference_header)
    target_shape = (int(reference_height), int(reference_width))
    output_dir = reference_scan.path.parent / "aligned"
    output_dir.mkdir(parents=True, exist_ok=True)
    aligned = []
    for index, (scan, solve) in enumerate(frames, start=1):
        check_cancel()
        if scan.path.resolve() == reference_scan.path.resolve():
            aligned.append((scan, solve))
            continue
        progress(f"Aligning {scan.path.name} to {reference_scan.path.name} ({index}/{len(frames)})…")
        source_header, _width, _height = read_header_and_shape(scan.path)
        source_wcs = celestial_wcs(source_header)
        data = np.asarray(read_image_data(scan.path), dtype=np.float32)
        try:
            projected = _reproject_image_data(data, source_wcs, reference_wcs, target_shape)
        except Exception as exc:
            raise ValueError(f"Could not align {scan.path.name} onto {reference_scan.path.name}: {exc}") from exc
        output = output_dir / scan.path.name
        header = _header_with_wcs(
            source_header,
            reference_wcs,
            f"Citizen Astronomy aligned this cropped frame onto {reference_scan.path.name}.",
            target.name,
        )
        fits.PrimaryHDU(data=np.asarray(projected, dtype=np.float32), header=header).writeto(output, overwrite=True)
        field = _field_from_file(output)
        source_paths[output.resolve()] = source_paths.get(scan.path.resolve(), scan.path.resolve())
        aligned.append(_solved_copy(output, scan, settings, field))
    return aligned


def _best_centered_frame(frames, target: _StandardField):
    best = frames[0]
    best_distance = math.inf
    for frame in frames:
        scan, _solve = frame
        header, width, height = read_header_and_shape(scan.path)
        if width is None or height is None:
            continue
        wcs = celestial_wcs(header)
        x, y = _world_pixel(wcs, target.ra_deg, target.dec_deg)
        distance = math.hypot(x - width / 2.0, y - height / 2.0)
        if distance < best_distance:
            best = frame
            best_distance = distance
    return best
