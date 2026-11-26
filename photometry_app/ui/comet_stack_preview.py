"""Render a full-size monochrome preview without retaining extra science planes."""
import math

from astropy.visualization import AsinhStretch, ImageNormalize
import numpy as np
from PySide6.QtGui import QImage

from photometry_app.core.plotting import AnnotatedImageDisplay


CONTRAST_PRESETS = ("Balanced", "Faint structures", "Bright core")


def render_comet_stack_preview(data, path, preset="Balanced"):
    height, width = data.shape
    step = max(1, math.ceil(math.sqrt(data.size / 200_000)))
    sample = data[::step, ::step]
    finite = sample[np.isfinite(sample)]
    if not finite.size:
        for top in range(0, height, 64):
            strip = data[top:top+64]
            finite = strip[np.isfinite(strip)]
            if finite.size:
                break
    if not finite.size:
        raise ValueError("The stack contains no finite pixels to display.")
    median = float(np.median(finite))
    sigma = float(1.4826 * np.median(np.abs(finite - median)))
    low = median - 2.5 * sigma
    if preset == "Faint structures":
        high = median + 6 * sigma
    elif preset == "Bright core":
        high = max(median + 48*sigma, float(np.percentile(finite, 99.95)))
    else:
        high = max(median + 18*sigma, float(np.percentile(finite, 99.7)))
    if high <= low:
        high = float(np.max(finite))
    if high <= low:
        # A sparse source can fall between histogram samples.
        high = float(np.nanmax(data))
    if not np.isfinite(high) or high <= low:
        high = low + max(1.0, abs(low)) * 1e-6
    raster = QImage(width, height, QImage.Format.Format_Grayscale8)
    target = np.frombuffer(raster.bits(), dtype=np.uint8).reshape(height, raster.bytesPerLine())[:, :width]
    for top in range(0, height, 64):
        strip = np.subtract(data[top:top+64], low, dtype=np.float64)
        strip *= 1 / (high - low)
        np.nan_to_num(strip, copy=False, nan=0., posinf=1., neginf=0.)
        np.clip(strip, 0, 1, out=strip)
        strip *= 10
        np.arcsinh(strip, out=strip)
        strip *= 255 / np.arcsinh(10)
        target[top:top+64] = np.rint(strip)
    display = AnnotatedImageDisplay(path, data, ImageNormalize(vmin=low, vmax=high, stretch=AsinhStretch()))
    return display, raster
