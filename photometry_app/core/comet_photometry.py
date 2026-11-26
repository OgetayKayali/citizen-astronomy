"""Linear, aperture-based comet photometry, independent of the discovery scores.

The initial workflow measures calibrated monochrome images. Reported errors are
empirical background errors plus comparison-star calibration scatter; they are
not a complete detector/systematic error budget.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from collections.abc import Callable, Sequence
import csv
import json
import math
import os
import tempfile
import zipfile

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.stats import sigma_clip
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_area
from photutils.aperture import CircularAperture, CircularAnnulus
from scipy.ndimage import binary_propagation, shift
from photometry_app.core.comet_aperture_geometry import aperture_scale
from photometry_app.core.comet_morphology import RadialStarFilter, rectangle_separation


@dataclass(frozen=True)
class CometFrame:
    path: Path
    midpoint: datetime
    exposure: float
    ra_deg: float
    dec_deg: float
    filter_name: str


@dataclass
class CometStackPair:
    stars: np.ndarray
    comet: np.ndarray
    star_coverage: np.ndarray
    comet_coverage: np.ndarray
    center_x: float
    center_y: float
    pixel_scale: float
    frame_count: int
    reference_path: Path
    header_text: str
    provenance: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ApertureMeasurement:
    radius_px: float
    flux: float | None
    flux_error: float | None
    instrumental_magnitude: float | None
    magnitude_error: float | None
    background: float
    background_rms: float
    coverage: float
    flags: tuple[str, ...] = ()
    unfiltered_flux: float | None = None
    replaced_pixels: int = 0
    sky_pixels: int = 0
    sky_pixels_clipped: int = 0


@dataclass
class ComparisonStar:
    source_id: str
    name: str
    x: float
    y: float
    magnitude: float
    catalog_error: float
    color_bv: float | None = None
    selected: bool = True


@dataclass(frozen=True)
class ZeroPoint:
    value: float
    error: float
    scatter: float
    count: int
    residuals: dict[str, float]
    rejected: tuple[str, ...]


# Deliberately narrower than the general photometry aliases: clear/L must not
# silently acquire a standard V-band label, nor Cousins R a Sloan r calibration.
def comet_catalog_band(filter_name: str) -> str | None:
    value = filter_name.strip().lower().replace(" ", "").replace("_", "")
    for band in "griz":
        if value.replace("-", "") in (f"ps1{band}", f"panstarrs{band}"):
            return f"PS1{band}"
    return {
        "b": "B", "johnsonb": "B", "v": "V", "johnsonv": "V",
        "g": "SG", "g'": "SG", "sg": "SG", "sloang": "SG",
        "r'": "SR", "sr": "SR", "sloanr": "SR",
        "i'": "SI", "si": "SI", "sloani": "SI",
    }.get(value)


def _check_cancel(cancel: Callable[[], bool] | None) -> None:
    if cancel and cancel():
        raise InterruptedError("Comet photometry cancelled.")


def site_from_comet_headers(paths):
    """Use FITS east-positive geodetic degrees only when the sequence agrees."""
    from .image_io import read_header
    site = None
    for path in paths:
        try:
            h = read_header(path)
            lon = float(h.get("OBSGEO-L", h.get("SITELONG")))
            lat = float(h.get("OBSGEO-B", h.get("SITELAT")))
            elevation = float(h.get("OBSGEO-H", h.get("SITEELEV", 0))) / 1000
        except (OSError, TypeError, ValueError, KeyError):
            return None
        if not np.isfinite([lon, lat, elevation]).all() or not (-180 <= lon <= 360 and -90 <= lat <= 90):
            return None
        current = ((lon + 180) % 360 - 180, lat, elevation)
        if site is not None and not np.allclose(site, current, rtol=0, atol=1e-5):
            return None
        site = current
    return dict(zip(("lon", "lat", "elevation"), site)) if site is not None else None


def _spatial_grid(wcs: WCS):
    """Keep projection, frame and SIP coefficients; exposure dates do not move an ICRS grid."""
    if any(item is not None for item in (wcs.cpdis1, wcs.cpdis2, wcs.det2im1, wcs.det2im2)):
        return None
    header = wcs.to_header(relax=True)
    for key in ("DATE-OBS", "MJD-OBS", "DATE-END", "MJD-END", "DATE-AVG", "MJD-AVG"):
        header.remove(key, ignore_missing=True)
    return header


def comet_image_units(header):
    """Return the input unit and whether exposure normalization is necessary."""
    from astropy import units as u
    label = str(header.get("BUNIT", "")).strip()
    if not label:
        return "native input units / s", True
    # Common FITS detector labels are not uniformly cased or pluralized.
    import re
    unit = re.sub(r"\b(adu|counts?|electrons?|seconds?|sec)\b", lambda m: {
        "adu": "adu", "count": "ct", "counts": "ct", "electron": "electron",
        "electrons": "electron", "second": "s", "seconds": "s", "sec": "s",
    }[m.group().lower()], label, flags=re.I)
    try:
        parsed = u.Unit(unit)
    except ValueError as exc:
        raise ValueError(f"Unsupported FITS BUNIT {label!r}. Use linear detector counts or counts per second.") from exc
    for base in (u.adu, u.ct, u.electron, u.dimensionless_unscaled):
        if parsed == base:
            return f"{base} / s" if base != u.one else "relative units / s", True
        if parsed == base / u.s:
            return f"{base} / s" if base != u.one else "relative units / s", False
    raise ValueError(f"Unsupported FITS BUNIT {label!r}. Use linear detector counts or counts per second.")


def mask_cast_alignment_padding(data, header):
    """CAst's registration writer stores its exterior footprint as zeros."""
    if not any("Citizen Photometry aligned this frame" in line for line in header.get("HISTORY", ())):
        return data, False
    border = np.zeros(data.shape, dtype=bool)
    border[0] = data[0] == 0
    border[-1] = data[-1] == 0
    border[:, 0] |= data[:, 0] == 0
    border[:, -1] |= data[:, -1] == 0
    if not border.any():
        return data, False
    padding = binary_propagation(border, mask=(data == 0))
    return np.where(padding, np.nan, data), True


def read_moving_target_rate(path, header, exposure):
    """The stack and per-frame workflows must apply the same units and pixel masks."""
    from .image_io import read_image_data
    from .osc import has_cfa, read_plane_extensions
    if has_cfa(header):
        raise ValueError(f"{path.name}: extract a calibrated monochrome channel from the Bayer mosaic first.")
    data = read_image_data(path)
    if data.ndim != 2:
        raise ValueError(f"{path.name}: use a linear monochrome image.")
    units, divide = comet_image_units(header)
    data, padding = mask_cast_alignment_padding(data, header)
    threshold = header.get("SATURATE", header.get("SATLEVEL"))
    if threshold is not None:
        if not np.isfinite(float(threshold)) or float(threshold) <= 0:
            raise ValueError(f"{path.name}: the saturation threshold must be finite and positive.")
        data = np.where(data >= float(threshold), np.nan, data)
    _, satfrac = read_plane_extensions(path, data.shape) if header.get("EXTEND") else (None, None)
    if satfrac is not None:
        data = np.where(satfrac == 0, data, np.nan)
    return (data / exposure if divide else data), units, {
        "alignment_padding_masked": padding, "divided_by_exposure": divide,
        "saturation_masked": threshold is not None or satfrac is not None,
    }


def build_comet_stack_pair(
    frames: Sequence[CometFrame], *, reference_path: Path,
    progress: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    tracking_source: str = "Predicted linear motion",
) -> CometStackPair:
    """Equal-weight mean of count rates, streamed one frame at a time.

Each image is reprojected to the reference stars and then shifted to the comet.
Only pixels with complete coverage are subsequently accepted for photometry.
Input images must already be calibrated, linear and monochrome.
"""
    from photometry_app.core.image_io import read_header_and_shape
    from reproject import reproject_interp

    if not frames:
        raise ValueError("Select at least one frame.")
    if len({f.path.resolve() for f in frames}) != len(frames):
        raise ValueError("An exposure occurs more than once in the selected frame list.")
    if len({f.filter_name.strip().lower() for f in frames}) != 1:
        raise ValueError("Choose frames from one filter. Different passbands cannot share a stack.")
    reference = next((f for f in frames if f.path.resolve() == reference_path.resolve()), frames[0])
    header, width, height = read_header_and_shape(reference.path)
    if width is None or height is None:
        raise ValueError("Use calibrated monochrome images; RGB channel measurements are not supported yet.")
    shape = (height, width)
    wcs = WCS(header).celestial
    if not wcs.has_celestial:
        raise ValueError("Plate solve every selected frame before preparing comet photometry.")
    area = abs(float(proj_plane_pixel_area(wcs)))
    reference_grid = _spatial_grid(wcs)
    rate_units, _ = comet_image_units(header)
    cx, cy = map(float, wcs.world_to_pixel(SkyCoord(reference.ra_deg, reference.dec_deg, unit="deg")))
    if not np.isfinite([cx, cy, area]).all() or area <= 0 or not (0 <= cx < shape[1] and 0 <= cy < shape[0]):
        raise ValueError("The predicted comet is outside the reference image or its WCS is invalid.")
    header_text = header.tostring()
    scale = aperture_scale(header_text, cx, cy).equivalent_arcsec
    star_sum = np.zeros(shape, dtype=np.float64)
    comet_sum = np.zeros(shape, dtype=np.float64)
    star_count = np.zeros(shape, dtype=np.uint32)
    comet_count = np.zeros(shape, dtype=np.uint32)
    manifest = []
    for index, frame in enumerate(frames):
        _check_cancel(cancel)
        if not np.isfinite(frame.exposure) or frame.exposure <= 0:
            raise ValueError(f"{frame.path.name}: a positive exposure time is required.")
        if progress:
            progress(f"Preparing frame {index + 1} of {len(frames)} · {frame.path.name}")
        frame_header, _, _ = read_header_and_shape(frame.path)
        rate, units, mask_info = read_moving_target_rate(frame.path, frame_header, frame.exposure)
        if units != rate_units:
            raise ValueError("Use frames with consistent pixel units (FITS BUNIT).")
        source_wcs = WCS(frame_header).celestial
        if not source_wcs.has_celestial:
            raise ValueError(f"{frame.path.name}: no celestial plate solution.")
        source_area = abs(float(proj_plane_pixel_area(source_wcs)))
        if not np.isfinite(source_area) or source_area <= 0:
            raise ValueError(f"{frame.path.name}: invalid pixel scale.")
        if not 0.98 <= source_area / area <= 1.02:
            raise ValueError("Use one optical setup: the selected frames have different pixel areas.")
        same_grid = (rate.shape == shape and reference_grid is not None
                     and reference_grid == _spatial_grid(source_wcs))
        if same_grid or frame.path.resolve() == reference.path.resolve():
            aligned = rate
        else:
            aligned, footprint = reproject_interp((rate, source_wcs), wcs, shape_out=shape)
            aligned[footprint < 0.999] = np.nan
            aligned *= area / source_area
        px, py = map(float, wcs.world_to_pixel(SkyCoord(frame.ra_deg, frame.dec_deg, unit="deg")))
        if not np.isfinite([px, py]).all():
            raise ValueError(f"{frame.path.name}: invalid comet position.")
        shifted = (aligned if (cx, cy) == (px, py) else
                   shift(aligned, (cy - py, cx - px), order=1, mode="constant",
                         cval=np.nan, prefilter=False))
        valid = np.isfinite(aligned)
        np.add(star_sum, aligned, out=star_sum, where=valid)
        star_count += valid
        valid = np.isfinite(shifted)
        np.add(comet_sum, shifted, out=comet_sum, where=valid)
        comet_count += valid
        stat = frame.path.stat()
        manifest.append({"path": str(frame.path.resolve()), "size": stat.st_size,
                         "mtime_ns": stat.st_mtime_ns, "midpoint": frame.midpoint.isoformat(),
                         "exposure": frame.exposure, "ra_deg": frame.ra_deg, "dec_deg": frame.dec_deg,
                         "calstat": str(frame_header.get("CALSTAT", "")),
                         "input_bunit": str(frame_header.get("BUNIT", "")),
                         **mask_info,
                         "star_reprojected": not same_grid and frame.path.resolve() != reference.path.resolve()})
        del aligned, shifted, rate
    stars = np.divide(star_sum, star_count, out=star_sum, where=star_count > 0)
    comet = np.divide(comet_sum, comet_count, out=comet_sum, where=comet_count > 0)
    stars[star_count == 0] = np.nan
    comet[comet_count == 0] = np.nan
    return CometStackPair(stars, comet, star_count, comet_count, cx, cy, scale, len(frames),
                         reference.path, header_text, {
                             "aperture_scale_model": "Local area-equivalent celestial WCS",
                             "frames": manifest, "filter": reference.filter_name,
                             "tracking": tracking_source, "units": rate_units,
                             "stack": "Equal-weight mean of exposure-normalized linear frames",
                             "uncertainty": "Background estimate only; detector and resampling covariance not modeled",
                         })


def _aperture_weights(aperture, shape: tuple[int, int]) -> np.ndarray:
    mask = aperture.to_mask(method="exact")
    result = mask.to_image(shape)
    return np.zeros(shape) if result is None else result


def _whole_sky_pixels(region, data, valid):
    mask = region.to_mask(method="exact")
    whole = mask.data > 0.99
    expected = int(np.count_nonzero(whole))
    large, small = mask.get_overlap_slices(data.shape)
    if large is None:
        return np.empty(0), expected
    return data[large][whole[small] & valid[large]], expected


def measure_comet_aperture(
    image: np.ndarray, *, x: float, y: float, radius: float,
    sky_inner: float, sky_outer: float, valid_mask: np.ndarray | None = None,
    required_coverage: int = 1,
    sky_patch: tuple[float, float, float] | None = None,
    sky_rectangle: tuple[float, float, float, float] | None = None,
    radial_filter: RadialStarFilter | None = None,
) -> ApertureMeasurement:
    if image.ndim != 2 or not np.isfinite([x, y, radius, sky_inner, sky_outer]).all():
        raise ValueError("Use a two-dimensional image and finite aperture values.")
    annulus_mode = sky_patch is None and sky_rectangle is None
    if radius <= 0 or (annulus_mode and not radius < sky_inner < sky_outer):
        raise ValueError("The sky annulus must lie outside the coma: radius < sky inner < sky outer.")
    if sky_rectangle is not None:
        if sky_patch is not None:
            raise ValueError("Choose one background region.")
        if rectangle_separation(x, y, sky_rectangle) <= radius:
            raise ValueError("Move the sky rectangle clear of the coma aperture.")
    if sky_patch is not None:
        sx, sy, sky_radius = sky_patch
        if not np.isfinite(sky_patch).all() or sky_radius <= 0:
            raise ValueError("Choose a finite sky patch with a positive radius.")
        if math.hypot(sx - x, sy - y) <= radius + sky_radius:
            raise ValueError("Move the sky patch clear of the coma aperture.")
    # Operate on a local bounding box so growth curves do not allocate one full
    # detector-sized mask for every aperture.
    extent = sky_outer if annulus_mode else radius
    x0, x1 = max(0, math.floor(x - extent - 1)), min(image.shape[1], math.ceil(x + extent + 2))
    y0, y1 = max(0, math.floor(y - extent - 1)), min(image.shape[0], math.ceil(y + extent + 2))
    if x1 <= x0 or y1 <= y0:
        raise ValueError("The aperture center lies outside the image.")
    local = np.asarray(image[y0:y1, x0:x1], dtype=float)
    valid = np.isfinite(local)
    if valid_mask is not None:
        valid &= valid_mask[y0:y1, x0:x1] == required_coverage
    aperture = _aperture_weights(CircularAperture((x - x0, y - y0), radius), local.shape)
    coverage = min(1.0, float(np.sum(aperture * valid) / (math.pi * radius**2)))
    if sky_rectangle is not None:
        left, bottom, right, top = sky_rectangle
        # Each retained pixel must be entirely inside the chosen rectangle.
        ix0, ix1 = math.ceil(left+0.5), math.floor(right-0.5)+1
        iy0, iy1 = math.ceil(bottom+0.5), math.floor(top-0.5)+1
        expected_sky_area = max(0, ix1-ix0) * max(0, iy1-iy0)
        roi = np.s_[max(0, iy0):max(0, min(image.shape[0], iy1)),
                    max(0, ix0):max(0, min(image.shape[1], ix1))]
        sky_data = image[roi]
        sky_valid = np.isfinite(sky_data)
        if valid_mask is not None:
            sky_valid &= valid_mask[roi] == required_coverage
        sky = sky_data[sky_valid]
    elif annulus_mode:
        sky, expected_sky_area = _whole_sky_pixels(
            CircularAnnulus((x - x0, y - y0), sky_inner, sky_outer), local, valid)
    else:
        sx0, sx1 = max(0, math.floor(sx - sky_radius - 1)), min(image.shape[1], math.ceil(sx + sky_radius + 2))
        sy0, sy1 = max(0, math.floor(sy - sky_radius - 1)), min(image.shape[0], math.ceil(sy + sky_radius + 2))
        if sx1 <= sx0 or sy1 <= sy0:
            raise ValueError("The sky patch lies outside the image.")
        sky_data = image[sy0:sy1, sx0:sx1]
        sky_valid = np.isfinite(sky_data)
        if valid_mask is not None:
            sky_valid &= valid_mask[sy0:sy1, sx0:sx1] == required_coverage
        sky, expected_sky_area = _whole_sky_pixels(
            CircularAperture((sx - sx0, sy - sy0), sky_radius), sky_data, sky_valid)
    sky_count = sky.size
    if sky_count:
        sky = sigma_clip(sky, sigma=3.0, masked=False)
    if sky.size < 20:
        raise ValueError("Not enough clean sky pixels. Move or enlarge the background region.")
    background, rms = np.median(sky), np.std(sky)
    clipped_count = sky_count - sky.size
    flags = []
    if sky_count < 0.75 * expected_sky_area:
        flags.append("Sky region has incomplete coverage")
    if coverage < 0.999999 or np.any((aperture > 1e-8) & ~valid):
        flags.append("Aperture has missing, saturated, or incompletely stacked pixels")
        return ApertureMeasurement(radius, None, None, None, None, float(background), float(rms), coverage,
                                   tuple(flags), sky_pixels=sky.size, sky_pixels_clipped=clipped_count)
    weights = aperture * valid
    flux = float(np.sum(np.where(valid, local - background, 0) * weights))
    unfiltered_flux = flux
    replaced_pixels = 0
    if radial_filter is not None:
        correction, replaced_pixels = radial_filter.aperture_correction(weights, x0, y0)
        flux -= correction
    # Median sky estimator variance (~pi/2 for Gaussian noise), plus empirical
    # pixel background variance. The UI/export explicitly labels the limits.
    variance = float(rms**2 * (np.sum(weights**2) + math.pi / 2 * np.sum(weights)**2 / sky.size))
    error = math.sqrt(max(0, variance))
    mag = -2.5 * math.log10(flux) if flux > 0 else None
    mag_error = 2.5 / math.log(10) * error / flux if flux > 0 else None
    if flux <= 0:
        flags.append("No positive background-subtracted flux")
    elif error > 0 and flux / error < 5:
        flags.append("Low signal-to-noise")
    return ApertureMeasurement(radius, flux, error, mag, mag_error, float(background), float(rms), coverage,
                               tuple(flags), unfiltered_flux, replaced_pixels, sky.size, clipped_count)


def fit_comet_zero_point(
    stars: Sequence[ComparisonStar], measurements: dict[str, ApertureMeasurement],
) -> ZeroPoint | None:
    entries = []
    for star in stars:
        m = measurements.get(star.source_id)
        if not star.selected or m is None or m.instrumental_magnitude is None or m.flags:
            continue
        if not np.isfinite([star.magnitude, star.catalog_error, m.magnitude_error or 0]).all():
            continue
        entries.append((star.source_id, star.magnitude - m.instrumental_magnitude,
                        max(0.01**2, star.catalog_error**2 + (m.magnitude_error or 0)**2)))
    if len(entries) < 3:
        return None
    values = np.array([e[1] for e in entries])
    variances = np.array([e[2] for e in entries])
    median = float(np.median(values))
    robust_sigma = 1.4826 * float(np.median(abs(values - median)))
    keep = abs(values - median) <= np.maximum(0.15, 3 * np.sqrt(variances + robust_sigma**2))
    if np.count_nonzero(keep) < 3:
        return None
    weights = 1 / variances[keep]
    zero = float(np.average(values[keep], weights=weights))
    scatter = float(np.sqrt(np.average((values[keep] - zero)**2, weights=weights)))
    # Keep observed calibration scatter as a floor; do not divide it away.
    error = max(math.sqrt(1 / np.sum(weights)), scatter)
    return ZeroPoint(zero, error, scatter, int(sum(keep)),
                     {e[0]: float(e[1] - zero) for e in entries},
                     tuple(e[0] for e, retained in zip(entries, keep) if not retained))


def apass_comparison_stars(catalog, pair: CometStackPair, band: str) -> list[ComparisonStar]:
    """Use only actual requested-band entries; never fall back to Gaia G/V."""
    from photometry_app.core.standard_magnitude import get_band_magnitudes
    from scipy.spatial import cKDTree
    wcs = WCS(fits.Header.fromstring(pair.header_text)).celestial
    stars = []
    catalog = [s for s in catalog if np.isfinite([s.ra_deg, s.dec_deg]).all() and -90 <= s.dec_deg <= 90]
    if not catalog:
        return []
    xs, ys = wcs.world_to_pixel(SkyCoord([s.ra_deg for s in catalog], [s.dec_deg for s in catalog], unit="deg"))
    for source, x, y in zip(catalog, xs, ys):
        bands = get_band_magnitudes(source)
        entry = bands.get(band, {})
        mag = entry.get("mag")
        error = entry.get("error")
        if mag is None or error is None:
            continue
        if not np.isfinite([x, y, float(mag), float(error)]).all() or float(error) <= 0:
            continue
        if not (24 <= x < pair.stars.shape[1] - 24 and 24 <= y < pair.stars.shape[0] - 24):
            continue
        bv = None
        if "B" in bands and "V" in bands:
            bv = float(bands["B"]["mag"]) - float(bands["V"]["mag"])
        stars.append(ComparisonStar(source.source_id, source.name, x, y, float(mag), float(error), bv))
    # Conservative automatic isolation, including the full known comet path.
    path = [wcs.world_to_pixel(SkyCoord(f["ra_deg"], f["dec_deg"], unit="deg"))
            for f in pair.provenance.get("frames", [])]
    if not stars:
        return []
    coords = np.array([(s.x, s.y) for s in stars])
    neighbors, _ = cKDTree(coords).query(coords, k=2)
    path_dist = cKDTree(path).query(coords)[0] if path else np.full(len(stars), np.inf)
    for star, distance, neighbor in zip(stars, path_dist, neighbors[:, 1]):
        star.selected = star.catalog_error <= 0.1 and distance >= 60 and neighbor >= 24
    return sorted(stars, key=lambda s: (not s.selected, s.catalog_error, s.source_id))[:100]


def export_comet_measurements(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError("Measure the comet before exporting.")
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def save_comet_session(path: Path, pair: CometStackPair, settings: dict, stars: Sequence[ComparisonStar]) -> None:
    manifest = {"version": 1, "settings": settings, "comparisons": [asdict(s) for s in stars],
                "center_x": pair.center_x, "center_y": pair.center_y, "pixel_scale": pair.pixel_scale,
                "frame_count": pair.frame_count, "reference_path": str(pair.reference_path),
                "header": pair.header_text, "provenance": pair.provenance}
    # Atomic replacement keeps a previous session intact if saving fails.
    fd, temporary = tempfile.mkstemp(prefix=".comet-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle, zipfile.ZipFile(handle, "w") as archive:
            archive.writestr("session.json", json.dumps(manifest, allow_nan=False), compress_type=zipfile.ZIP_DEFLATED)
            # NPZ already compresses each array. Stream it into the stored member
            # to avoid a second detector-sized buffer and a second compression.
            with archive.open("stacks.npz", "w", force_zip64=True) as member:
                np.savez_compressed(member, stars=pair.stars, comet=pair.comet,
                                    star_coverage=pair.star_coverage, comet_coverage=pair.comet_coverage)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_comet_session(path: Path) -> tuple[CometStackPair, dict, list[ComparisonStar], list[str]]:
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("session.json"))
        if manifest.get("version") != 1:
            raise ValueError("This comet session version is not supported.")
        with archive.open("stacks.npz") as member, np.load(member, allow_pickle=False) as arrays:
            pair = CometStackPair(*(arrays[name] for name in
                                   ("stars", "comet", "star_coverage", "comet_coverage")),
                                  manifest["center_x"], manifest["center_y"], manifest["pixel_scale"],
                                  manifest["frame_count"], Path(manifest["reference_path"]),
                                  manifest["header"], manifest["provenance"])
    if pair.stars.ndim != 2 or any(a.shape != pair.stars.shape for a in
                                  (pair.comet, pair.star_coverage, pair.comet_coverage)):
        raise ValueError("Session stack dimensions do not match.")
    if (not np.isfinite([pair.center_x, pair.center_y, pair.pixel_scale]).all() or pair.pixel_scale <= 0
            or not isinstance(pair.frame_count, int) or pair.frame_count < 1
            or not (0 <= pair.center_x < pair.stars.shape[1] and 0 <= pair.center_y < pair.stars.shape[0])):
        raise ValueError("Session geometry is invalid.")
    for stack, coverage in ((pair.stars, pair.star_coverage), (pair.comet, pair.comet_coverage)):
        if (stack.dtype.kind not in "iuf" or not np.isfinite(stack).any()
                or coverage.dtype.kind not in "iu" or coverage.min() < 0 or coverage.max() > pair.frame_count):
            raise ValueError("The saved stack has invalid pixel values or frame coverage.")
    if len(pair.provenance.get("frames", [])) != pair.frame_count:
        raise ValueError("The saved frame list does not match the stack.")
    comparisons = [ComparisonStar(**s) for s in manifest["comparisons"]]
    source_ids = set()
    for star in comparisons:
        if (not isinstance(star.source_id, str) or star.source_id in source_ids
                or not isinstance(star.name, str) or not isinstance(star.selected, bool)
                or not np.isfinite([star.x, star.y, star.magnitude, star.catalog_error]).all()
                or star.catalog_error < 0 or (star.color_bv is not None and not np.isfinite(star.color_bv))):
            raise ValueError("The session contains an invalid or duplicate comparison star.")
        source_ids.add(star.source_id)
    changes = []
    for source in pair.provenance.get("frames", []):
        original = Path(source["path"])
        if not original.exists():
            changes.append(f"Missing original: {original.name}")
        elif (original.stat().st_size, original.stat().st_mtime_ns) != (source["size"], source["mtime_ns"]):
            changes.append(f"Changed original: {original.name}")
    return pair, manifest["settings"], comparisons, changes
