"""Verify CAst against independent integration of the 76 original 3I/ATLAS FITS images.

Run from the project root: python scripts/verify_comet_3i_atlas.py DATASET_DIRECTORY
The pinned observatory ephemeris and file hashes allow entirely offline replay.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from scipy.integrate import quad

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from photometry_app.core.comet_photometry import (
    CometFrame, build_comet_stack_pair, export_comet_measurements, fit_comet_zero_point, measure_comet_aperture,
)
from photometry_app.core.comet_references import read_comparison_sequence


def circle_overlap(x, y, r):
    """Independent pixel/disk overlap by integrating a vertical chord of the disk."""
    dx, dy = abs(x), abs(y)
    if (dx + 0.5)**2 + (dy + 0.5)**2 <= r*r:
        return 1.0
    if max(dx - 0.5, 0)**2 + max(dy - 0.5, 0)**2 >= r*r:
        return 0.0
    a, b = max(-r, x - 0.5), min(r, x + 0.5)
    points = []
    for edge in (y - 0.5, y + 0.5):
        if abs(edge) < r:
            crossing = math.sqrt(r*r - edge*edge)
            points.extend(v for v in (-crossing, crossing) if a < v < b)

    def chord(u):
        h = math.sqrt(max(0, r*r - u*u))
        return max(0, min(y + 0.5, h) - max(y - 0.5, -h))

    return quad(chord, a, b, points=points, epsabs=2e-13)[0]


def independent_reference(frames, radii, star=None, annulus=(24, 36)):
    """Read the original, already star-registered detector sections. No CAst or Photutils math."""
    ref_wcs = WCS(fits.getheader(frames[0].path)).celestial
    cx, cy = (map(float, ref_wcs.world_to_pixel_values(frames[0].ra_deg, frames[0].dec_deg))
              if star is None else (star.x, star.y))
    extent = annulus[1] + 2
    offsets = np.arange(-extent, extent + 1)
    xx, yy = np.meshgrid(int(cx) + offsets, int(cy) + offsets)
    summed = np.zeros_like(xx, dtype=float)
    for frame in frames:
        px, py = (map(float, ref_wcs.world_to_pixel_values(frame.ra_deg, frame.dec_deg))
                  if star is None else (cx, cy))
        sx, sy = xx + (px - cx), yy + (py - cy)
        ix, iy = np.floor(sx).astype(int), np.floor(sy).astype(int)
        fx, fy = sx - ix, sy - iy
        with fits.open(frame.path, memmap=True) as hdus:
            raw = hdus[0].data
            sample = ((1-fx)*(1-fy)*raw[iy, ix] + fx*(1-fy)*raw[iy, ix+1]
                      + (1-fx)*fy*raw[iy+1, ix] + fx*fy*raw[iy+1, ix+1])
        summed += sample / frame.exposure
    stack = summed / len(frames)
    overlap = np.vectorize(circle_overlap, otypes=[float])
    dx, dy = xx - cx, yy - cy
    sky = stack[(overlap(dx, dy, annulus[1]) - overlap(dx, dy, annulus[0])) > 0.99]
    for _ in range(5):
        center, spread = np.median(sky), np.std(sky)
        clipped = sky[abs(sky-center) <= 3*spread]
        if clipped.size == sky.size:
            break
        sky = clipped
    background = float(np.median(sky))
    reference = {}
    for r in radii:
        weights = overlap(dx, dy, r)
        flux = float(np.sum((stack - background)*weights))
        error = np.std(sky) * np.sqrt(np.sum(weights**2) + np.pi/2 * weights.sum()**2 / sky.size)
        reference[r] = (flux, float(2.5 / math.log(10) * error / flux))
    return reference


def export_dialog_result(path, fixture, pair, frames, comparisons, zero):
    """Exercise the desktop measurement/review path with the offline inputs."""
    from types import SimpleNamespace
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from photometry_app.ui.comet_photometry_dialog import CometPhotometryDialog
    app = QApplication.instance() or QApplication([])
    first = frames[0]
    detection = SimpleNamespace(name="3I/ATLAS", designation="3I", object_type="Comet")
    metadata = {str(f.path.resolve()): SimpleNamespace(filter_name=f.filter_name,
                date_obs=f.midpoint-timedelta(seconds=f.exposure/2), exposure_seconds=f.exposure)
                for f in frames}
    dlg = CometPhotometryDialog(frame_paths=[f.path for f in frames], frame_metadata=metadata,
        detection_result=SimpleNamespace(detections=[detection], filter_name=first.filter_name),
        detection_index=0, reference_path=first.path, observer=fixture["site"])
    pair.provenance.update(observer=fixture["site"], comparison_catalog="APASS", clear_v_reference=True,
        geometry=dict(heliocentric_au=float(np.mean([r["r_au"] for r in fixture["frames"]])),
                      observer_au=float(np.mean([r["delta_au"] for r in fixture["frames"]])),
                      source="Pinned JPL Horizons mean at exposure midpoints"))
    dlg._prepared(pair, comparisons, "Five frozen APASS DR9 solar-color comparison stars.")
    dlg.solar_colors.setChecked(True)
    dlg._measure()
    current = next(r for r in dlg.rows if r["selected_aperture"])
    np.testing.assert_allclose(current["flux_counts_per_second"], fixture["reference_flux"]["12"], rtol=1e-10)
    np.testing.assert_allclose(current["zero_point"], zero.value, atol=1e-10, rtol=0)
    np.testing.assert_allclose(current["magnitude"], fixture["reference_l_v_mag"], atol=1e-10, rtol=0)
    assert current["afrho_cm"] is None and current["comparison_count"] == 5
    path.parent.mkdir(parents=True, exist_ok=True)
    export_comet_measurements(path, dlg.rows)
    print(f"Desktop measurement CSV: {path.resolve()}")
    dlg.close()
    app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--export-csv", type=Path, help="Verify the desktop measurement path and save its growth curve.")
    args = parser.parse_args()
    fixture = json.loads((ROOT / "tests/data/comet_3i_atlas.json").read_text(encoding="utf-8"))
    frames = []
    for row in fixture["frames"]:
        path = args.dataset / row["file"]
        with path.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != row["sha256"]:
                raise ValueError(f"FITS input differs from the verified exposure: {path}")
        header = fits.getheader(path)
        midpoint = datetime.fromisoformat(row["midpoint_utc"]).replace(tzinfo=timezone.utc)
        frames.append(CometFrame(path, midpoint, header["EXPTIME"], row["ra_deg"],
                                 row["dec_deg"], header["FILTER"]))
    pair = build_comet_stack_pair(frames, reference_path=args.dataset / fixture["reference"],
                                  tracking_source=fixture["source"])
    if any(f["star_reprojected"] for f in pair.provenance["frames"]):
        raise AssertionError("The independent reference assumes identical star grids.")
    radii = (6, 12, 18)
    independent = independent_reference(frames, radii)
    print(f"{pair.frame_count} frames; center ({pair.center_x:.8f}, {pair.center_y:.8f}); "
          f"{pair.pixel_scale:.8f} arcsec/pixel; {pair.provenance['units']}")
    print("radius_px      CAst native flux/s     independent flux/s       instrumental mag")
    valid = pair.comet_coverage == len(frames)
    for radius in radii:
        result = measure_comet_aperture(pair.comet, x=pair.center_x, y=pair.center_y,
            radius=radius, sky_inner=24, sky_outer=36, valid_mask=valid)
        if result.flags or result.flux is None:
            raise AssertionError(f"Incomplete reference measurement: {result.flags}")
        print(f"{radius:9d} {result.flux:23.16g} {independent[radius][0]:23.16g} "
              f"{result.instrumental_magnitude:22.13f}")
        np.testing.assert_allclose(result.flux, independent[radius][0], rtol=1e-10, atol=0)
        np.testing.assert_allclose(result.flux, fixture["reference_flux"][str(radius)],
                                   rtol=1e-10, atol=0)
    _, comparisons = read_comparison_sequence(ROOT / "tests/data/comet_3i_atlas_apass_v.csv", pair, "V")
    measures, zeros, variances = {}, [], []
    valid = pair.star_coverage == len(frames)
    for star in comparisons:
        m = measure_comet_aperture(pair.stars, x=star.x, y=star.y, radius=8, sky_inner=12,
                                   sky_outer=20, valid_mask=valid)
        measures[star.source_id] = m
        flux, mag_err = independent_reference(frames, (8,), star, annulus=(12, 20))[8]
        np.testing.assert_allclose(m.flux, flux, rtol=1e-10)
        zeros.append(star.magnitude + 2.5*math.log10(flux))
        variances.append(max(0.0001, star.catalog_error**2 + mag_err**2))
    zero = fit_comet_zero_point(comparisons, measures)
    values, var = np.array(zeros), np.array(variances)
    offset = abs(values - np.median(values))
    used = offset <= np.maximum(0.15, 3*np.sqrt(var + (1.4826*np.median(offset))**2))
    expected = np.average(values[used], weights=1/var[used])
    assert zero is not None and zero.count == np.count_nonzero(used)
    np.testing.assert_allclose(zero.value, expected, rtol=0, atol=1e-10)
    print(f"L, APASS V-referenced: {zero.value - 2.5*math.log10(independent[12][0]):.12f} mag "
          f"(r=12 px, {zero.count}/{len(comparisons)} stars, ZP={zero.value:.12f}, "
          f"ZP scatter={zero.scatter:.6f} mag)")
    np.testing.assert_allclose(zero.value - 2.5*math.log10(independent[12][0]),
                               fixture["reference_l_v_mag"], atol=1e-10, rtol=0)
    if args.export_csv:
        export_dialog_result(args.export_csv, fixture, pair, frames, comparisons, zero)


if __name__ == "__main__":
    main()
