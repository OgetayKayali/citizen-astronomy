from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io.fits import Header
from astropy.wcs.utils import proj_plane_pixel_scales

from photometry_app.core.wcs import celestial_wcs, validate_wcs


_PLATE_SCALE_ARCSEC_FACTOR = 206.26480624709636
_MIN_PLAUSIBLE_SCALE_ARCSEC = 0.05
_MAX_PLAUSIBLE_SCALE_ARCSEC = 60.0
_AGREE_FRACTION_OF_FOV = 0.25
_AGREE_FLOOR_DEG = 0.15
_OPTICS_SCALE_TOLERANCE = 0.35


@dataclass(frozen=True, slots=True)
class ImagePointingAssessment:
    mount_ra_deg: float | None = None
    mount_dec_deg: float | None = None
    wcs_ra_deg: float | None = None
    wcs_dec_deg: float | None = None
    wcs_usable: bool = False
    optics_scale_arcsec: float | None = None
    wcs_scale_arcsec: float | None = None
    separation_deg: float | None = None
    agreement: str = "unknown"  # agree | disagree | mount_only | wcs_only | none
    preferred_source: str = "none"  # mount | wcs | none
    prefer_astrometry_first: bool = False
    messages: tuple[str, ...] = field(default_factory=tuple)

    @property
    def preferred_ra_deg(self) -> float | None:
        if self.preferred_source == "mount":
            return self.mount_ra_deg
        if self.preferred_source == "wcs":
            return self.wcs_ra_deg
        return None

    @property
    def preferred_dec_deg(self) -> float | None:
        if self.preferred_source == "mount":
            return self.mount_dec_deg
        if self.preferred_source == "wcs":
            return self.wcs_dec_deg
        return None


def assess_image_pointing(
    header: Header,
    width: int | None,
    height: int | None,
    *,
    source_path: Path | None = None,
) -> ImagePointingAssessment:
    """Compare mount/header pointing with embedded WCS before long recovery work."""
    messages: list[str] = []
    mount = mount_pointing_coordinate(header)
    optics_scale = optics_pixel_scale_arcsec(header)
    wcs_pointing = embedded_wcs_pointing(header, width, height, source_path=source_path)

    mount_ra = float(mount.ra.deg) if mount is not None else None
    mount_dec = float(mount.dec.deg) if mount is not None else None
    wcs_usable = False
    wcs_ra = None
    wcs_dec = None
    wcs_scale = None
    if wcs_pointing is not None:
        wcs_usable, wcs_ra, wcs_dec, wcs_scale, wcs_notes = wcs_pointing
        messages.extend(wcs_notes)

    if mount is not None:
        messages.append(
            f"Mount/header pointing: RA {mount_ra:.5f} deg, Dec {mount_dec:.5f} deg"
            f" ({_format_sky_coord(mount)})."
        )
    else:
        messages.append("Mount/header pointing: not found.")

    if wcs_usable and wcs_ra is not None and wcs_dec is not None:
        wcs_coord = SkyCoord(wcs_ra * u.deg, wcs_dec * u.deg, frame="icrs")
        scale_text = (
            f", plate scale ~{wcs_scale:.3f} arcsec/px"
            if wcs_scale is not None and np.isfinite(wcs_scale)
            else ""
        )
        messages.append(
            f"Embedded WCS center: RA {wcs_ra:.5f} deg, Dec {wcs_dec:.5f} deg"
            f" ({_format_sky_coord(wcs_coord)}){scale_text}."
        )
    elif any("Embedded WCS" in message for message in messages):
        pass
    else:
        messages.append("Embedded WCS center: no usable celestial WCS.")

    if optics_scale is not None:
        messages.append(f"Optics-implied plate scale: ~{optics_scale:.3f} arcsec/px.")

    separation = None
    if mount is not None and wcs_usable and wcs_ra is not None and wcs_dec is not None:
        separation = float(
            mount.separation(SkyCoord(wcs_ra * u.deg, wcs_dec * u.deg, frame="icrs")).deg
        )
        fov_radius = _estimated_fov_radius_deg(width, height, optics_scale, wcs_scale)
        agree_limit = max(_AGREE_FLOOR_DEG, _AGREE_FRACTION_OF_FOV * 2.0 * fov_radius)
        if separation <= agree_limit:
            agreement = "agree"
            preferred = _prefer_when_agreeing(optics_scale, wcs_scale)
            prefer_nova = False
            messages.append(
                f"Mount and embedded WCS agree within {separation:.3f} deg "
                f"(limit {agree_limit:.3f} deg); preferring {preferred} pointing."
            )
        else:
            agreement = "disagree"
            preferred, prefer_nova, reason = _prefer_when_disagreeing(optics_scale, wcs_scale)
            messages.append(
                f"Mount and embedded WCS disagree by {separation:.3f} deg "
                f"(limit {agree_limit:.3f} deg); {reason}"
            )
    elif mount is not None:
        agreement = "mount_only"
        preferred = "mount"
        prefer_nova = False
        messages.append("Using mount/header pointing because no usable embedded WCS center is available.")
    elif wcs_usable:
        agreement = "wcs_only"
        preferred = "wcs"
        prefer_nova = False
        messages.append("Using embedded WCS pointing because mount/header RA/Dec was not found.")
    else:
        agreement = "none"
        preferred = "none"
        prefer_nova = True
        messages.append("No reliable mount or embedded WCS pointing is available.")

    return ImagePointingAssessment(
        mount_ra_deg=mount_ra,
        mount_dec_deg=mount_dec,
        wcs_ra_deg=wcs_ra,
        wcs_dec_deg=wcs_dec,
        wcs_usable=wcs_usable,
        optics_scale_arcsec=optics_scale,
        wcs_scale_arcsec=wcs_scale,
        separation_deg=separation,
        agreement=agreement,
        preferred_source=preferred,
        prefer_astrometry_first=prefer_nova or agreement == "disagree",
        messages=tuple(messages),
    )


def mount_pointing_coordinate(header: Header) -> SkyCoord | None:
    """Return telescope/object pointing from non-WCS header keywords only."""
    coordinate_pairs = (
        ("RA", "DEC", "auto"),
        ("OBJCTRA", "OBJCTDEC", "hourangle"),
        ("OBJRA", "OBJDEC", "hourangle"),
        ("TELRA", "TELDEC", "hourangle"),
    )
    for ra_key, dec_key, mode in coordinate_pairs:
        if ra_key not in header or dec_key not in header:
            continue
        coordinate = _parse_coordinate(header.get(ra_key), header.get(dec_key), mode)
        if coordinate is not None:
            return coordinate
    return None


def optics_pixel_scale_arcsec(header: Header) -> float | None:
    direct = _positive_float(header, "PIXSCALE", "SECPIX", "SCALE")
    if direct is not None and _MIN_PLAUSIBLE_SCALE_ARCSEC <= direct <= _MAX_PLAUSIBLE_SCALE_ARCSEC:
        return direct
    focal_length_mm = _positive_float(header, "FOCALLEN", "FOCALLENGTH", "FOCAL")
    pixel_size_um = _positive_float(header, "XPIXSZ", "PIXSIZE1", "PIXELX")
    if pixel_size_um is None:
        pixel_size_um = _positive_float(header, "YPIXSZ", "PIXSIZE2", "PIXELY")
    if focal_length_mm is None or pixel_size_um is None:
        return None
    scale = _PLATE_SCALE_ARCSEC_FACTOR * pixel_size_um / focal_length_mm
    if not np.isfinite(scale) or not (_MIN_PLAUSIBLE_SCALE_ARCSEC <= scale <= _MAX_PLAUSIBLE_SCALE_ARCSEC):
        return None
    return float(scale)


def embedded_wcs_pointing(
    header: Header,
    width: int | None,
    height: int | None,
    *,
    source_path: Path | None = None,
) -> tuple[bool, float, float, float | None, list[str]] | None:
    """Return usability, center, and scale for an embedded celestial WCS."""
    notes: list[str] = []
    valid, reasons = validate_wcs(header, source_path)
    if not valid:
        if reasons:
            notes.append("Embedded WCS is not usable: " + " ".join(str(reason).strip() for reason in reasons if str(reason).strip()))
        return False, 0.0, 0.0, None, notes
    if width is None or height is None or width <= 0 or height <= 0:
        notes.append("Embedded WCS keywords look complete, but image dimensions are missing.")
        return False, 0.0, 0.0, None, notes
    try:
        wcs = celestial_wcs(header)
        center = wcs.pixel_to_world(width / 2.0, height / 2.0)
        scales = proj_plane_pixel_scales(wcs) * u.deg
        scale_arcsec = float(scales.mean().to_value(u.arcsec))
    except Exception as exc:
        notes.append(f"Embedded WCS could not be evaluated at the image center: {exc}")
        return False, 0.0, 0.0, None, notes

    if not np.isfinite(center.ra.deg) or not np.isfinite(center.dec.deg):
        notes.append("Embedded WCS center is non-finite.")
        return False, 0.0, 0.0, None, notes
    if not (_MIN_PLAUSIBLE_SCALE_ARCSEC <= scale_arcsec <= _MAX_PLAUSIBLE_SCALE_ARCSEC):
        notes.append(
            f"Embedded WCS plate scale looks implausible (~{scale_arcsec:.3f} arcsec/px); ignoring it for pointing."
        )
        return False, float(center.ra.deg), float(center.dec.deg), float(scale_arcsec), notes
    return True, float(center.ra.deg), float(center.dec.deg), float(scale_arcsec), notes


def _prefer_when_agreeing(optics_scale: float | None, wcs_scale: float | None) -> str:
    if optics_scale is not None and wcs_scale is not None and _scales_agree(optics_scale, wcs_scale):
        return "wcs"
    if optics_scale is not None and wcs_scale is not None and not _scales_agree(optics_scale, wcs_scale):
        return "mount"
    return "wcs"


def _prefer_when_disagreeing(
    optics_scale: float | None,
    wcs_scale: float | None,
) -> tuple[str, bool, str]:
    if wcs_scale is None or not (_MIN_PLAUSIBLE_SCALE_ARCSEC <= wcs_scale <= _MAX_PLAUSIBLE_SCALE_ARCSEC):
        return (
            "mount",
            True,
            "preferring mount pointing and astrometry.net when available because the embedded WCS scale is unusable.",
        )
    if optics_scale is not None and _scales_agree(optics_scale, wcs_scale):
        return (
            "wcs",
            True,
            "preferring embedded WCS pointing (scale matches optics) and checking with astrometry.net when available.",
        )
    if optics_scale is not None:
        return (
            "mount",
            True,
            "preferring mount pointing because optics scale disagrees with the embedded WCS; trying astrometry.net when available.",
        )
    return (
        "mount",
        True,
        "preferring mount pointing and astrometry.net when available because the two sky positions disagree.",
    )


def _scales_agree(optics_scale: float, wcs_scale: float) -> bool:
    if optics_scale <= 0 or wcs_scale <= 0:
        return False
    ratio = max(optics_scale, wcs_scale) / min(optics_scale, wcs_scale)
    return ratio <= (1.0 + _OPTICS_SCALE_TOLERANCE)


def _estimated_fov_radius_deg(
    width: int | None,
    height: int | None,
    optics_scale: float | None,
    wcs_scale: float | None,
) -> float:
    scale = optics_scale if optics_scale is not None else wcs_scale
    if scale is None or width is None or height is None or width <= 0 or height <= 0:
        return 1.0
    diagonal_px = float(np.hypot(width, height))
    return max(0.2, diagonal_px * scale / 7200.0)


def _format_sky_coord(coordinate: SkyCoord) -> str:
    try:
        return coordinate.to_string("hmsdms", precision=1)
    except Exception:
        return f"{coordinate.ra.deg:.5f}, {coordinate.dec.deg:.5f}"


def _parse_coordinate(ra_value: object, dec_value: object, mode: str) -> SkyCoord | None:
    try:
        if mode == "degrees":
            coordinate = SkyCoord(float(ra_value) * u.deg, float(dec_value) * u.deg, frame="icrs")
        elif mode == "hourangle":
            coordinate = SkyCoord(
                str(ra_value).strip(),
                str(dec_value).strip(),
                unit=(u.hourangle, u.deg),
                frame="icrs",
            )
        else:
            ra_text = str(ra_value).strip()
            dec_text = str(dec_value).strip()
            if any(separator in ra_text for separator in (":", " ")):
                coordinate = SkyCoord(ra_text, dec_text, unit=(u.hourangle, u.deg), frame="icrs")
            else:
                coordinate = SkyCoord(float(ra_text) * u.deg, float(dec_text) * u.deg, frame="icrs")
    except Exception:
        return None
    if not np.isfinite(coordinate.ra.deg) or not np.isfinite(coordinate.dec.deg):
        return None
    if not -90.0 <= float(coordinate.dec.deg) <= 90.0:
        return None
    return coordinate


def _positive_float(header: Header, *keys: str) -> float | None:
    for key in keys:
        try:
            value = float(header.get(key))
        except (TypeError, ValueError):
            continue
        if np.isfinite(value) and value > 0:
            return value
    return None
