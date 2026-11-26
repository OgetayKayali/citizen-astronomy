"""Measurements on the native pixel grid of the comet-aligned stack."""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS


def rectangle_separation(x, y, rectangle):
    """Shortest pixel-grid distance from a source center to a sky rectangle."""
    left, bottom, right, top = rectangle
    if not np.isfinite(rectangle).all() or right <= left or top <= bottom:
        raise ValueError("Choose two opposite corners of a sky rectangle with positive area.")
    return math.hypot(max(left-x, x-right, 0), max(bottom-y, y-top, 0))


def comparison_star_fwhm(image, coverage, frame_count, x, y):
    """CAst's radial-profile FWHM estimator, limited to fully covered star cutouts."""
    from photometry_app.core.photometry import _estimate_star_fwhm
    ix, iy = round(x), round(y)
    half_size = 12
    height, width = image.shape
    if not (half_size <= ix < width-half_size and half_size <= iy < height-half_size):
        return None
    box = np.s_[iy-half_size:iy+half_size+1, ix-half_size:ix+half_size+1]
    if np.any(coverage[box] != frame_count) or not np.isfinite(image[box]).all():
        return None
    return _estimate_star_fwhm(image, x, y, half_size=half_size)


def tail_geometry(header_text, center, endpoint):
    """Great-circle length (arcmin) and ICRS position angle east of north (deg)."""
    wcs = WCS(fits.Header.fromstring(header_text)).celestial
    origin, tip = wcs.pixel_to_world([center[0], endpoint[0]], [center[1], endpoint[1]]).icrs
    length = float(origin.separation(tip).arcmin)
    pa = float(origin.position_angle(tip).degree) % 360
    if not np.isfinite([length, pa]).all() or length < 1e-9:
        raise ValueError("Choose a tail endpoint distinct from the coma center and within the plate solution.")
    return length, pa


@dataclass(frozen=True)
class RadialStarFilter:
    y: np.ndarray
    x: np.ndarray
    replacement: np.ndarray
    excess: np.ndarray

    def aperture_correction(self, weights, x0, y0):
        """Integrate replacement corrections with the source aperture's exact weights."""
        iy = self.y - y0
        ix = self.x - x0
        inside = (iy >= 0) & (ix >= 0) & (iy < weights.shape[0]) & (ix < weights.shape[1])
        w = weights[iy[inside], ix[inside]]
        return float(np.sum(self.excess[inside] * w)), int(np.count_nonzero(w > 0))


def build_radial_star_filter(image, coverage, frame_count, center, max_radius,
                             ignore_radius=7.0, coefficient=3.0, statistic="mean"):
    """Two-pixel ring mean (COBS) or median, evaluated on fully covered pixels.

    Compare native, pre-sky-subtraction intensities. Non-positive ring levels
    have no meaningful multiplicative threshold and are skipped. Complete
    the outer ring so its statistic is independent of the chosen aperture.
    """
    x, y = center
    if statistic not in ("mean", "median"):
        raise ValueError("Choose the mean or median radial ring statistic.")
    if not np.isfinite([x, y, max_radius, ignore_radius, coefficient]).all() or (
            ignore_radius < 0 or coefficient < 1 or max_radius <= 0):
        raise ValueError("Choose a positive aperture, a nonnegative protected core and a coefficient of at least one.")
    # An aperture can touch a corner of a pixel whose center is outside it.
    ring_count = max(0, math.ceil((max_radius + math.sqrt(0.5) - ignore_radius) / 2))
    if not ring_count:
        empty = np.empty(0, dtype=np.intp)
        return RadialStarFilter(empty, empty, np.empty(0), np.empty(0))
    extent = ignore_radius + 2 * ring_count
    x0 = max(0, math.floor(x - extent))
    x1 = min(image.shape[1], math.ceil(x + extent) + 1)
    y0 = max(0, math.floor(y - extent))
    y1 = min(image.shape[0], math.ceil(y + extent) + 1)

    def strips():
        dx2 = (np.arange(x0, x1, dtype=float) - x) ** 2
        for top in range(y0, y1, 64):
            bottom = min(top + 64, y1)
            distance = np.sqrt(dx2 + (np.arange(top, bottom, dtype=float)[:, None] - y) ** 2)
            pixels = image[top:bottom, x0:x1]
            valid = ((distance >= ignore_radius) & (distance < extent) & np.isfinite(pixels)
                     & (coverage[top:bottom, x0:x1] == frame_count))
            iy, ix = np.nonzero(valid)
            rings = ((distance[valid] - ignore_radius) / 2).astype(np.intp)
            yield top + iy, x0 + ix, rings, pixels[valid]

    total = np.zeros(ring_count, dtype=float)
    count = np.zeros(ring_count, dtype=np.int64)
    for _, _, rings, values in strips():
        total += np.bincount(rings, weights=values, minlength=ring_count)
        count += np.bincount(rings, minlength=ring_count)
    levels = np.divide(total, count, out=np.zeros_like(total), where=count > 0)
    if statistic == "median":
        # Exact medians. Group native pixel values in a single packed buffer;
        # sorting the whole field by both ring and brightness uses several more.
        offsets = np.concatenate(([0], count.cumsum()))
        cursor = offsets[:-1].copy()
        grouped = np.empty(offsets[-1], dtype=image.dtype)
        for _, _, rings, values in strips():
            if not rings.size:
                continue
            order = np.argsort(rings, kind="stable")
            labels = rings[order]
            starts = np.concatenate(([0], 1+np.flatnonzero(np.diff(labels))))
            ends = np.append(starts[1:], len(order))
            for start, end in zip(starts, ends):
                group = labels[start]
                dest = cursor[group]
                grouped[dest:dest+end-start] = values[order[start:end]]
                cursor[group] += end-start
        for group in np.flatnonzero(count):
            levels[group] = np.median(grouped[offsets[group]:offsets[group+1]], overwrite_input=True)
    ys, xs, replacements, excesses = [], [], [], []
    for iy, ix, rings, values in strips():
        level = levels[rings]
        bright = (level > 0) & (values > coefficient * level)
        if np.any(bright):
            ys.append(iy[bright])
            xs.append(ix[bright])
            replacements.append(level[bright])
            excesses.append(values[bright] - level[bright])
    return RadialStarFilter(
        np.concatenate(ys) if ys else np.empty(0, dtype=np.intp),
        np.concatenate(xs) if xs else np.empty(0, dtype=np.intp),
        np.concatenate(replacements) if replacements else np.empty(0),
        np.concatenate(excesses) if excesses else np.empty(0),
    )
