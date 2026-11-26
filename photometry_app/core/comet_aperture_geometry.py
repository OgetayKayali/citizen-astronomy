"""Sky-plane geometry of a circular detector aperture at its measured center."""
from dataclasses import dataclass
from functools import lru_cache
import math

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS


@dataclass(frozen=True)
class ApertureScale:
    x_arcsec: float
    y_arcsec: float
    axis_angle_deg: float
    major_arcsec: float
    minor_arcsec: float
    equivalent_arcsec: float


@lru_cache(maxsize=64)
def aperture_scale(header_text: str, x: float, y: float) -> ApertureScale:
    """Central one-pixel derivatives of the full WCS, in the local east/north plane.

    The singular values turn a circular detector radius into local sky ellipse
    semiaxes. Their geometric mean gives the radius of an equal-area circle.
    """
    wcs = WCS(fits.Header.fromstring(header_text)).celestial
    sky = wcs.pixel_to_world(np.array([x, x - 0.5, x + 0.5, x, x]),
                             np.array([y, y, y, y - 0.5, y + 0.5]))
    lon, lat = sky[0].spherical_offsets_to(sky[1:])
    offsets = np.array([lon.arcsec, lat.arcsec])
    jacobian = offsets[:, [1, 3]] - offsets[:, [0, 2]]
    if not np.isfinite(jacobian).all():
        raise ValueError("The aperture center has no finite local celestial pixel scale.")
    major, minor = np.linalg.svd(jacobian, compute_uv=False)
    if minor <= np.finfo(float).eps * major or major <= 0:
        raise ValueError("The local celestial pixel axes are degenerate.")
    dx, dy = np.linalg.norm(jacobian, axis=0)
    angle = np.degrees(np.arccos(np.clip(np.dot(*jacobian.T) / (dx * dy), -1, 1)))
    return ApertureScale(float(dx), float(dy), float(angle), float(major), float(minor),
                         math.sqrt(float(major * minor)))
