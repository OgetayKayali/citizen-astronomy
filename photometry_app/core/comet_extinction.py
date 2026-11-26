"""Differential first-order extinction for equal-weight count-rate stacks."""
import numpy as np
from astropy import units as u
from astropy.coordinates import AltAz, EarthLocation, SkyCoord
from astropy.io import fits
from astropy.time import Time
from astropy.utils import iers
from astropy.wcs import WCS

MODELS = ("None", "Green average", "Green winter", "Green summer", "Measured coefficient")


def green_coefficient(model, elevation_km):
    # Green (1992), ICQ 14, 55. The MBK team's published calculator gives these
    # visual Rayleigh, aerosol and ozone terms. Do not apply them to an L band.
    aerosol = {"Green average": 0.120, "Green winter": 0.084, "Green summer": 0.156}[model]
    return float(0.1451*np.exp(-elevation_km/7.996) + aerosol*np.exp(-elevation_km/1.5) + 0.016)


def stack_extinction(altitudes_deg, k):
    """Effective attenuation of a mean flux, in magnitudes along the time axis."""
    if np.any(~np.isfinite(altitudes_deg)) or np.any(altitudes_deg <= 0):
        raise ValueError("Extinction requires above-horizon positions at every selected midpoint.")
    sine = np.sin(np.deg2rad(altitudes_deg))
    airmass = 1 / (sine + 0.025*np.exp(-11*sine))
    return -2.5*np.log10(np.mean(10**(-0.4*k*airmass), axis=-1))


def comparison_extinction(pair, stars, model, custom_k):
    """Return target attenuation and each star's correction to the target's zero point."""
    from .comet_photometry import comet_catalog_band
    site = pair.provenance.get("observer")
    if not isinstance(site, dict):
        raise ValueError("Prepare with an observing site to apply differential extinction.")
    height = float(site.get("elevation", 0))
    if model == "Measured coefficient":
        if not np.isfinite(custom_k) or custom_k <= 0:
            raise ValueError("Enter a positive measured extinction coefficient in mag/airmass.")
        k = custom_k
    else:
        if comet_catalog_band(pair.provenance.get("filter", "")) != "V":
            raise ValueError("The Green visual presets require V images. Use a measured coefficient for this filter.")
        k = green_coefficient(model, height)
    location = EarthLocation.from_geodetic(site["lon"]*u.deg, site["lat"]*u.deg, height*u.km)
    frames = pair.provenance["frames"]
    from datetime import datetime
    epochs = Time([datetime.fromisoformat(f["midpoint"]) for f in frames])
    frame = AltAz(obstime=epochs, location=location, pressure=0*u.hPa)
    target = SkyCoord([f["ra_deg"] for f in frames], [f["dec_deg"] for f in frames], unit="deg")
    with iers.conf.set_temp("auto_download", False):
        target_correction = float(stack_extinction(target.transform_to(frame).alt.deg, k))
        if not stars:
            return k, target_correction, {}
        wcs = WCS(fits.Header.fromstring(pair.header_text)).celestial
        coords = wcs.pixel_to_world([s.x for s in stars], [s.y for s in stars]).icrs[:, None]
        altitudes = coords.transform_to(frame).alt.deg
    visible = np.all(np.isfinite(altitudes) & (altitudes > 0), axis=1)
    differences = stack_extinction(altitudes[visible], k) - target_correction
    return k, target_correction, {s.source_id: float(d) for s, d in zip(
        (star for star, ok in zip(stars, visible) if ok), differences)}
