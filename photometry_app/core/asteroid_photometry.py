"""Time-resolved aperture photometry on original, calibrated moving-target frames."""
from datetime import datetime
from pathlib import Path
import math

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales

from .comet_photometry import read_moving_target_rate, measure_comet_aperture, fit_comet_zero_point
from .image_io import read_header_and_shape


def measure_asteroid_sequence(pair, comparisons, *, radius, sky_inner, sky_outer,
                              star_radius, center, band, target, progress=None, cancel=None):
    """Measure every exposure independently; never derive a light curve from a stack.

    With a matching catalog band each exposure gets its own zero point. Otherwise
    an ensemble of at least three references removes common brightness changes;
    those results have an arbitrary relative zero point. Errors exclude detector
    shot noise and systematics, as in the comet aperture engine.
    """
    reference = WCS(fits.Header.fromstring(pair.header_text)).celestial
    offset = np.asarray(center) - [pair.center_x, pair.center_y]
    references = [s for s in comparisons if s.selected]
    world = [reference.pixel_to_world(s.x, s.y) for s in references]
    rows, instruments, reference_magnitudes = [], [], []
    for source in pair.provenance["frames"]:
        if cancel and cancel():
            raise InterruptedError("Photometry cancelled.")
        path = Path(source["path"])
        if progress:
            progress(f"Measuring exposure {len(rows) + 1}/{pair.frame_count} · {path.name}")
        row = {"target": target, "frame": str(path), "midpoint_utc": source["midpoint"],
               "exposure_seconds": source["exposure"], "filter": pair.provenance["filter"],
               "radius_reference_px": radius, "star_radius_reference_px": star_radius,
               "sky_inner_reference_px": sky_inner, "sky_outer_reference_px": sky_outer,
               "center_x": None, "center_y": None, "radius_native_px": None, "coverage": None,
               "flux_counts_per_second": None, "flux_error_background_only": None,
               "magnitude": None, "magnitude_error_estimate": None,
               "magnitude_system": "Instrumental", "comparison_count": 0,
               "zero_point": None, "zero_point_error": None, "quality": "",
               "tracking": pair.provenance["tracking"],
               "uncertainty_note": "Background + calibration/ensemble estimate; excludes detector shot noise and systematic errors"}
        measured, mags = None, {}
        try:
            stat = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != (source["size"], source["mtime_ns"]):
                raise ValueError("Original image changed; prepare the sequence again")
            header, _, _ = read_header_and_shape(path)
            wcs = WCS(header).celestial
            if not wcs.has_celestial:
                raise ValueError("Frame has no celestial WCS")
            data, _, _ = read_moving_target_rate(path, header, source["exposure"])
            scale = float(np.sqrt(np.prod(proj_plane_pixel_scales(wcs))) * 3600)
            factor = pair.pixel_scale / scale
            predicted = np.asarray(reference.world_to_pixel(SkyCoord(source["ra_deg"], source["dec_deg"], unit="deg")))
            x, y = map(float, wcs.world_to_pixel(reference.pixel_to_world(*(predicted + offset))))
            measured = measure_comet_aperture(data, x=x, y=y, radius=radius * factor,
                sky_inner=sky_inner * factor, sky_outer=sky_outer * factor)
            star_measurements = {}
            usable = []
            for star, coord in zip(references, world):
                sx, sy = map(float, wcs.world_to_pixel(coord))
                # The moving target must not overlap a reference's sky region.
                if math.hypot(sx-x, sy-y) <= (radius + 2.5 * star_radius) * factor:
                    continue
                try:
                    m = measure_comet_aperture(data, x=sx, y=sy, radius=star_radius * factor,
                        sky_inner=1.5 * star_radius * factor, sky_outer=2.5 * star_radius * factor)
                    star_measurements[star.source_id] = m
                    if m.instrumental_magnitude is not None and not m.flags:
                        mags[star.source_id] = (m.instrumental_magnitude, m.magnitude_error or 0)
                        usable.append(star)
                except ValueError:
                    continue
            zp = fit_comet_zero_point(usable, star_measurements) if band else None
            row.update(center_x=x, center_y=y, radius_native_px=radius * factor,
                       flux_counts_per_second=measured.flux, flux_error_background_only=measured.flux_error,
                       coverage=measured.coverage, quality="; ".join(measured.flags))
            if measured.instrumental_magnitude is not None:
                row.update(magnitude=measured.instrumental_magnitude + (zp.value if zp else 0),
                    magnitude_error_estimate=math.hypot(measured.magnitude_error or 0, zp.error if zp else 0))
            if zp:
                row.update(magnitude_system=f"{pair.provenance.get('comparison_catalog', 'APASS')} {band} (untransformed)", comparison_count=zp.count,
                           zero_point=zp.value, zero_point_error=zp.error)
            elif band:
                row["quality"] = (row["quality"] + "; No valid per-exposure catalog calibration").strip("; ")
                # Never mix calibrated and instrumental points under one axis.
                row["magnitude"] = None
        except (OSError, ValueError) as exc:
            row["quality"] = str(exc)
        rows.append(row)
        instruments.append(measured)
        reference_magnitudes.append(mags)
    if not band:
        # Use the same reference ensemble for every corrected exposure.
        eligible = [i for i, row in enumerate(rows) if row["magnitude"] is not None]
        ids = [s.source_id for s in references if eligible and all(s.source_id in reference_magnitudes[i] for i in eligible)]
        if len(ids) >= 3:
            baseline = np.median([[reference_magnitudes[i][s][0] for s in ids] for i in eligible], axis=0)
            for i, (row, measured) in enumerate(zip(rows, instruments)):
                if measured is None or row["magnitude"] is None:
                    continue
                residuals = np.array([reference_magnitudes[i][s][0] for s in ids]) - baseline
                correction = float(np.median(residuals))
                scatter = float(np.std(residuals))
                ref_error = math.sqrt(sum(reference_magnitudes[i][s][1]**2 for s in ids)) / len(ids)
                row.update(magnitude=row["magnitude"] - correction,
                    magnitude_error_estimate=math.hypot(row["magnitude_error_estimate"], max(scatter, ref_error)),
                    magnitude_system="Differential (arbitrary zero point)", comparison_count=len(ids))
        else:
            for row in rows:
                row["quality"] = (row["quality"] + "; Instrumental only; fewer than three common reference stars").strip("; ")
    return sorted(rows, key=lambda r: datetime.fromisoformat(r["midpoint_utc"]))
