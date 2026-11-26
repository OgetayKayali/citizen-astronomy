"""Observed aperture Afρ and electronic ICQ records for the same-field comet workflow."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import math
import re

from astropy.constants import au

from photometry_app.core.comet_photometry import comet_catalog_band


# Apparent solar magnitudes at 1 au, Willmer's online table, Vega for
# Johnson B,V and AB for Sloan bands: https://mips.as.arizona.edu/~cnaw/sun.html
SOLAR_MAGNITUDES = {"B": -26.11, "V": -26.75, "SG": -26.47, "SR": -26.93, "SI": -27.05,
                    "PS1g": -26.54, "PS1r": -26.93, "PS1i": -27.05, "PS1z": -27.07}
# Receiver-specific keys from https://cobs.si/help/icq_list/ref_catalogues/
# and https://cobs.si/help/icq_list/obs_methods/
COBS_APASS_KEYS = {"B": "AB", "V": "AV", "SG": "AG", "SR": "AR", "SI": "AI"}
COBS_FILTER_KEYS = {"B": "D", "V": "V", "SG": "g", "SR": "r", "SI": "h"}
ICQ_EXTINCTION_KEYS = {"Green average": "a", "Green winter": "w", "Green summer": "s", "Measured coefficient": "d"}


def clear_filter(name: str) -> bool:
    return name.strip().casefold() in {"l", "lum", "luminance", "clear", "unfiltered"}


def preparation_band(name: str, reference_clear: bool) -> str | None:
    return "V" if reference_clear and clear_filter(name) else comet_catalog_band(name)


def magnitude_system(filter_name: str, band: str | None, catalog: str = "APASS") -> str:
    if not band:
        return "Instrumental"
    if clear_filter(filter_name):
        return f"{filter_name} ({catalog} V-referenced, untransformed)"
    return f"{catalog} {band} zero point (untransformed)"


def aperture_dust_proxy(row: dict, geometry: dict) -> dict:
    """Afρ = 4 r_h² Δ² (F_comet/F_sun)/ρ. Δ and ρ in cm, r_h in au.

    Magnitude form: https://academic.oup.com/mnras/article/441/1/739/982847
    Measured broadband flux includes any gas and nucleus in the aperture.
    """
    band = comet_catalog_band(row["filter"])
    r, delta = geometry.get("heliocentric_au"), geometry.get("observer_au")
    result = {"heliocentric_au": r, "observer_au": delta, "geometry_source": geometry.get("source"),
              "projected_aperture_radius_km": None, "solar_magnitude_1au": SOLAR_MAGNITUDES.get(band),
              "afrho_cm": None, "afrho_mag_error_cm": None, "afrho_note": ""}
    if not all(isinstance(x, (float, int)) and math.isfinite(x) and x > 0 for x in (r, delta)):
        result["afrho_note"] = "Supply both distances at the observation epoch."
        return result
    theta = math.radians(row["radius_arcsec"] / 3600)
    if not 0 < theta < math.pi / 2:
        raise ValueError("The angular aperture radius must be between zero and 90 degrees.")
    delta_cm = delta * au.cgs.value
    rho_cm = delta_cm * math.tan(theta)
    result["projected_aperture_radius_km"] = rho_cm / 1e5
    if band not in SOLAR_MAGNITUDES or row["zero_point"] is None or row["magnitude"] is None:
        result["afrho_note"] = "Afρ requires a positive comet flux and a zero point in the image passband."
        return result
    ratio = 10**(0.4 * (SOLAR_MAGNITUDES[band] - row["magnitude"]))
    afrho = 4 * (r * delta_cm)**2 * ratio / rho_cm
    result["afrho_cm"] = afrho
    error = row["magnitude_error_estimate"]
    if error is not None:
        result["afrho_mag_error_cm"] = afrho * math.log(10) * 0.4 * error
    result["afrho_note"] = "Broadband aperture proxy at observed phase; uncertainty from magnitude only."
    return result


def icq_target(name: str) -> str:
    """Prefill common ICQ designations; retain the positions of split components."""
    # COBS records for 3I/ATLAS use "  3I" in the first four columns.
    interstellar = re.match(r"^(\d{1,3})I(?:[/\s]|$)", name, re.I)
    if interstellar:
        return f"{int(interstellar[1]):3d}I       "
    number = re.match(r"^(\d{1,3})P(?:-([A-Za-z]))?(?:[/\s]|$)", name, re.I)
    if number:
        return f"{int(number[1]):3d}       {(number[2] or ' ').lower()}"
    found = re.search(r"\b([CP])/(\d{4})\s*([A-Z])(\d{1,2})(?:-([A-Za-z]))?\b", name)
    if found:
        kind, year, halfmonth, sequence, component = found.groups()
        return f"{'  P' if kind == 'P' else '   '}{year}{halfmonth}{sequence:<2}{(component or ' ').lower()}"
    return ""


def _put(record: list[str], first: int, width: int, value: str) -> None:
    if len(value) != width or not value.isascii() or not value.isprintable():
        raise ValueError(f"Value exceeds the ICQ field in columns {first}–{first + width - 1}.")
    record[first - 1:first - 1 + width] = value


def build_icq_record(row: dict, frames: list[dict], inputs: dict, *,
                     closest_comparison: float | None, pixel_scales: tuple[float, float]) -> str:
    """Format the CCD extension using the COBS column definitions.

    https://cobs.si/help/icq_format/ and https://cobs.si/help/icq_list/ref_catalogues/
    The observer confirms that the selected aperture contains the visible coma.
    """
    mag, error = row["magnitude"], row["magnitude_error_estimate"]
    if row.get("zero_point") is None or mag is None or not math.isfinite(mag):
        raise ValueError("ICQ requires a catalog-referenced magnitude for the selected aperture.")
    if not inputs.get("total"):
        raise ValueError("Inspect the growth curve and confirm that the aperture encloses the visible coma.")
    if row["quality"] and not inputs.get("quality_reviewed"):
        raise ValueError(f"Review the measurement quality before exporting: {row['quality']}")
    if not frames or len(frames) > 99:
        raise ValueError("An ICQ record can represent 1–99 stacked exposures.")
    epochs = [datetime.fromisoformat(f["midpoint"]) for f in frames]
    if any(t.utcoffset() is None for t in epochs):
        raise ValueError("ICQ requires UTC mid-exposure times.")
    # Equal-weight mean of normalized frames, hence equal-weight observation epoch.
    midpoint = datetime.fromtimestamp(math.fsum(t.timestamp() for t in epochs) / len(epochs), UTC)
    if max(epochs) - min(epochs) >= timedelta(days=1):
        raise ValueError("Prepare a single observing night's exposures for one ICQ record.")
    midnight = midpoint.replace(hour=0, minute=0, second=0, microsecond=0)
    tick = round((midpoint - midnight).total_seconds() / 864)
    day = midnight + timedelta(seconds=864 * tick)
    decimal_date = f"{day:%Y %m %d}.{tick % 100:02d}"
    designation = inputs.get("designation", "").rstrip()
    # The first three columns are significant (spaces for long-period comets).
    if not designation or len(designation) > 11 or not re.fullmatch(r"[ A-Za-z0-9]+", designation):
        raise ValueError("Enter the object's ICQ identification in columns 1–11, including leading spaces.")
    observer = inputs.get("observer", "").strip()
    if not re.fullmatch(r"[A-Za-z]{3}(?:[A-Za-z]{2}|\d{2})?", observer):
        raise ValueError("Enter your assigned three- or five-character ICQ/COBS observer code.")
    method = inputs.get("method", "")
    catalog = inputs.get("catalog", "")
    if not re.fullmatch(r"[A-Za-z0-9]", method) or not re.fullmatch(r"[A-Za-z0-9]{1,2}", catalog):
        raise ValueError("Supply the ICQ method and catalog keys for the actual image filter and comparison band.")
    if clear_filter(row["filter"]) and method == "V":
        raise ValueError("Choose the ICQ method for the recorded clear or luminance filter.")
    inst = inputs.get("instrument", "")
    if not re.fullmatch(r"[A-Za-z0-9]", inst):
        raise ValueError("Supply the ICQ telescope-type key.")
    aperture, focal = inputs.get("aperture_cm", 0), inputs.get("f_ratio", 0)
    if not all(isinstance(n, (float, int)) and math.isfinite(n) and n > 0 for n in (aperture, focal)):
        raise ValueError("Enter the telescope aperture in cm and its focal ratio.")
    if round(focal) < 1:
        raise ValueError("The rounded focal ratio must be at least 1.")
    exposure = round(math.fsum(f["exposure"] for f in frames))
    if not 1 <= exposure <= 26999:
        raise ValueError("Combined exposure is outside the ICQ range of 1–26,999 seconds.")
    whole, remainder = divmod(exposure, 1000)
    exposure_code = (chr(64 + whole) if whole else "a") + (f"{remainder:03d}" if whole else f"{remainder:3d}")
    record = [" "] * 129
    _put(record, 1, 11, designation.ljust(11))
    _put(record, 12, 13, decimal_date)
    model = row.get("extinction_model")
    if row.get("target_stack_extinction_mag") is not None and model in ICQ_EXTINCTION_KEYS:
        _put(record, 26, 1, ICQ_EXTINCTION_KEYS[model])
    _put(record, 27, 1, method)
    _put(record, 29, 4, f"{mag:4.1f}")
    if row["quality"] or error is not None and error > 0.3:
        _put(record, 33, 1, ":")
    _put(record, 34, 2, catalog.ljust(2))
    _put(record, 36, 5, f"{aperture:5.1f}")
    _put(record, 41, 1, inst)
    _put(record, 42, 2, f"{round(focal):2d}")
    _put(record, 44, 4, exposure_code)
    coma = inputs.get("coma_arcmin", "")
    if coma:
        # Preserve the observer's significant figures. Align the decimal point
        # in column 52 even for estimates that warrant only a whole arcminute.
        if not re.fullmatch(r"\d{1,2}(?:\.\d{1,2})?", coma) or float(coma) <= 0:
            raise ValueError("Enter the measured coma diameter in arcminutes, to at most two decimals.")
        integer, point, fraction = coma.partition(".")
        _put(record, 50, 5, f"{integer:>2}{point or ' '}{fraction:<2}")
    condensation = inputs.get("condensation", "")
    if condensation:
        if not re.fullmatch(r"(?:[0-8]/?|9)", condensation):
            raise ValueError("Enter a coma condensation from 0 to 9; use 5/ for the 5–6 interval.")
        _put(record, 56, 2, condensation.ljust(2))
    if row.get("tail_length_arcmin") is not None:
        arcmin = row["tail_length_arcmin"]
        if arcmin >= 99.95:
            _put(record, 59, 5, f"{arcmin / 60:5.2f}")
        else:
            in_seconds = arcmin < 1
            _put(record, 60, 5, f"{arcmin * 60 if in_seconds else arcmin:4.1f}{'s' if in_seconds else 'm'}")
        _put(record, 65, 3, f"{round(row['tail_pa_deg']) % 360:3d}")
    _put(record, 69, 6, "ICQ XX")
    _put(record, 76, 5, observer.ljust(5))
    _put(record, 81, 1, "I")
    diameter = 2 * row["radius_arcsec"]
    unit, size = "s", diameter
    if size >= 99.995:
        unit, size = "m", diameter / 60
    if size >= 99.995:
        unit, size = "d", diameter / 3600
    _put(record, 87, 7, f"C{size:5.2f}{unit}")
    for key, column in (("camera", 94), ("chip", 98), ("software", 102)):
        code = inputs.get(key, "")
        if code:
            if not re.fullmatch(r"[A-Za-z0-9]{3}", code):
                raise ValueError(f"Enter a registered three-character {key} key or leave it empty.")
            _put(record, column, 3, code)
    calibration = inputs.get("calibration", "")
    if calibration not in tuple("012345"):
        raise ValueError("Select the bias, dark, and flat corrections applied to the images.")
    _put(record, 106, 1, calibration)
    _put(record, 108, 2, f"{len(frames):2d}")
    if len(frames) > 1:
        _put(record, 110, 1, "*")
    if error is not None:
        _put(record, 111, 4, f"{error:4.2f}")
    if closest_comparison is not None:
        _put(record, 116, 4, f"{closest_comparison:4.1f}")
    scales = pixel_scales
    unit = "s"
    if max(scales) >= 99.95:
        scales = tuple(n / 60 for n in scales)
        unit = "m"
    _put(record, 121, 9, f"{scales[0]:4.1f}{unit}{scales[1]:4.1f}")
    note = f" Comment: CAst, {row['magnitude_system']}, filter {row['filter']}."
    if row.get("aperture_semimajor_arcsec") is not None:
        note += (f" Circular detector aperture; area-equivalent sky diameter {diameter:.2f} arcsec,"
                 f" local sky semiaxes {row['aperture_semimajor_arcsec']:.2f} and {row['aperture_semiminor_arcsec']:.2f} arcsec.")
    if row.get("radial_filter"):
        note += " Radial star removal applied."
    if row.get("target_stack_extinction_mag") is not None and model in ICQ_EXTINCTION_KEYS:
        note += f" Differential extinction: {model}, k={row['extinction_coefficient_mag_airmass']:.4f} mag/airmass."
    return "".join(record) + note
