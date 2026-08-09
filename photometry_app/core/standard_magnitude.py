from __future__ import annotations

from typing import Any

import math

import numpy as np
import requests
from astropy import units as u
from astropy.coordinates import SkyCoord
from astroquery.vizier import Vizier

from photometry_app.core.models import CatalogStar, PhotometryMeasurement, SolvedField


_BAND_PRIORITY = {"vsp": 3, "apass": 2, "gaia-g": 1}

# FILTER token -> photometric band used for standard ZP (Phoranso-like).
_FILTER_TO_STANDARD_BAND = {
    "u": "U",
    "johnsonu": "U",
    "johnson_u": "U",
    "b": "B",
    "johnsonb": "B",
    "johnson_b": "B",
    "tb": "B",
    "v": "V",
    "johnsonv": "V",
    "johnson_v": "V",
    "tg": "V",
    "cv": "V",
    "clear": "V",
    "unfiltered": "V",
    "l": "V",
    "lum": "V",
    "luminance": "V",
    "r": "R",
    "rc": "R",
    "r_c": "R",
    "cousinsr": "R",
    "cousins_r": "R",
    "tr": "R",
    "cr": "R",
    "i": "I",
    "ic": "I",
    "i_c": "I",
    "cousinsi": "I",
    "cousins_i": "I",
    "su": "SU",
    "sloanu": "SU",
    "sloan_u": "SU",
    "sg": "SG",
    "sloang": "SG",
    "sloan_g": "SG",
    "g": "SG",
    "gp": "SG",
    "g_prime": "SG",
    "sr": "SR",
    "sloanr": "SR",
    "sloan_r": "SR",
    "rp": "SR",
    "r_prime": "SR",
    "si": "SI",
    "sloani": "SI",
    "sloan_i": "SI",
    "ip": "SI",
    "i_prime": "SI",
    "sz": "SZ",
    "sloanz": "SZ",
    "sloan_z": "SZ",
    "z": "SZ",
    "zp": "SZ",
    "z_prime": "SZ",
}

_APASS_COLUMN_TO_BAND = {
    "Bmag": "B",
    "Vmag": "V",
    "g'mag": "SG",
    "gmag": "SG",
    "r'mag": "SR",
    "rmag": "SR",
    "i'mag": "SI",
    "imag": "SI",
}

_APASS_ERROR_COLUMNS = {
    "B": "e_Bmag",
    "V": "e_Vmag",
    "SG": "e_g'mag",
    "SR": "e_r'mag",
    "SI": "e_i'mag",
}

_VSP_BAND_ALIASES = {
    "U": "U",
    "B": "B",
    "V": "V",
    "R": "R",
    "I": "I",
    "RJ": "R",
    "IJ": "I",
    "SG": "SG",
    "SR": "SR",
    "SI": "SI",
    "SZ": "SZ",
    "TG": "V",
    "TB": "B",
    "TR": "R",
}

_MATCH_SEPARATION_ARCSEC = 2.0
_VSP_API_URL = "https://app.aavso.org/vsp/api/chart/"
_APASS_CATALOG = "II/336/apass9"


def normalize_photometric_band(filter_name: str | None) -> str | None:
    """Map a FITS FILTER value to the standard photometric band used for ZP."""
    if not filter_name:
        return None
    normalized = (
        filter_name.strip()
        .lower()
        .replace(" ", "")
        .replace("-", "_")
        .replace("'", "")
        .replace("′", "")
    )
    if normalized in _FILTER_TO_STANDARD_BAND:
        return _FILTER_TO_STANDARD_BAND[normalized]
    # Compact forms like "JohnsonV"
    compact = normalized.replace("_", "")
    return _FILTER_TO_STANDARD_BAND.get(compact)


def set_band_magnitude(
    target: CatalogStar | PhotometryMeasurement | dict[str, Any],
    band: str,
    mag: float,
    *,
    error: float | None = None,
    source: str,
) -> None:
    if not band or not np.isfinite(mag):
        return
    bands = _band_magnitudes_mutable(target)
    existing = bands.get(band)
    if isinstance(existing, dict):
        existing_source = str(existing.get("source") or "")
        if _BAND_PRIORITY.get(existing_source, 0) > _BAND_PRIORITY.get(source, 0):
            return
    entry: dict[str, object] = {"mag": float(mag), "source": source}
    if error is not None and np.isfinite(error) and error >= 0:
        entry["error"] = float(error)
    bands[band] = entry


def seed_gaia_g_band_magnitude(star: CatalogStar) -> None:
    if star.magnitude is None or not np.isfinite(float(star.magnitude)):
        return
    set_band_magnitude(star, "G", float(star.magnitude), source="gaia-g")


def resolve_band_catalog_magnitude(
    source: CatalogStar | PhotometryMeasurement,
    band: str | None,
) -> tuple[float | None, float | None, str | None]:
    """Resolve catalog mag for a photometric band with VSP → APASS → Gaia G priority."""
    bands = get_band_magnitudes(source)
    if band:
        entry = bands.get(band)
        if isinstance(entry, dict) and entry.get("mag") is not None:
            try:
                mag = float(entry["mag"])
            except (TypeError, ValueError):
                mag = None
            if mag is not None and np.isfinite(mag):
                error = _as_optional_float(entry.get("error"))
                return mag, error, str(entry.get("source") or "unknown")

    # Gaia G fallback
    if isinstance(source, CatalogStar) and source.magnitude is not None and np.isfinite(float(source.magnitude)):
        return float(source.magnitude), None, "gaia-g"
    if isinstance(source, PhotometryMeasurement):
        g_entry = bands.get("G")
        if isinstance(g_entry, dict) and g_entry.get("mag") is not None:
            try:
                return float(g_entry["mag"]), _as_optional_float(g_entry.get("error")), str(g_entry.get("source") or "gaia-g")
            except (TypeError, ValueError):
                pass
        if source.catalog_magnitude is not None and np.isfinite(float(source.catalog_magnitude)):
            return float(source.catalog_magnitude), None, "gaia-g"
    return None, None, None


def get_band_magnitudes(source: CatalogStar | PhotometryMeasurement | dict[str, Any]) -> dict[str, dict[str, object]]:
    if isinstance(source, dict):
        raw = source.get("band_magnitudes")
        if isinstance(raw, dict):
            return {str(key): dict(value) for key, value in raw.items() if isinstance(value, dict)}
        return {}
    if isinstance(source, CatalogStar):
        raw = source.metadata.get("band_magnitudes")
        if isinstance(raw, dict):
            return {str(key): dict(value) for key, value in raw.items() if isinstance(value, dict)}
        return {}
    raw = getattr(source, "band_magnitudes", None)
    if isinstance(raw, dict):
        return {str(key): dict(value) for key, value in raw.items() if isinstance(value, dict)}
    return {}


def copy_band_magnitudes(source: CatalogStar | PhotometryMeasurement) -> dict[str, dict[str, object]]:
    return {band: dict(entry) for band, entry in get_band_magnitudes(source).items()}


def prefer_catalog_display_magnitude(star: CatalogStar) -> None:
    """Set CatalogStar.magnitude from the best available scientific band (V→B→R→I→G)."""
    bands = get_band_magnitudes(star)
    for band in ("V", "B", "R", "I", "SG", "SR", "SI", "G"):
        entry = bands.get(band)
        if not isinstance(entry, dict):
            continue
        mag = _as_optional_float(entry.get("mag"))
        if mag is not None:
            star.magnitude = float(mag)
            return
    if star.magnitude is None:
        return
    seed_gaia_g_band_magnitude(star)


def enrich_stars_with_standard_catalogs(
    stars: list[CatalogStar],
    solved_field: SolvedField,
    *,
    aavso_chart_id: str | None = None,
    mag_limit: float | None = None,
    progress_callback: Any | None = None,
    separation_arcsec: float = _MATCH_SEPARATION_ARCSEC,
    label: str = "star",
) -> dict[str, object]:
    """Attach APASS + VSP (and seeded Gaia G) band mags onto catalog stars by sky match."""
    notes: dict[str, object] = {"apass_matches": 0, "vsp_matches": 0, "vsp_chart_id": None}
    if not stars:
        return notes

    for star in stars:
        seed_gaia_g_band_magnitude(star)

    if progress_callback is not None:
        progress_callback(f"Querying APASS9 for band-matched comparison magnitudes ({label}s).")
    apass_stars = query_apass_field(solved_field, mag_limit=mag_limit)
    notes["apass_matches"] = merge_band_magnitudes_by_sky_match(
        stars,
        apass_stars,
        source="apass",
        separation_arcsec=separation_arcsec,
    )
    if progress_callback is not None:
        progress_callback(
            f"APASS9 merge complete: {notes['apass_matches']} {label}(s) received band magnitudes."
        )

    if progress_callback is not None:
        progress_callback(f"Querying AAVSO VSP for sequence/chart comparison magnitudes ({label}s).")
    vsp_stars, chart_id = query_vsp_field(
        solved_field,
        aavso_chart_id=aavso_chart_id,
        mag_limit=mag_limit,
    )
    notes["vsp_chart_id"] = chart_id
    notes["vsp_matches"] = merge_band_magnitudes_by_sky_match(
        stars,
        vsp_stars,
        source="vsp",
        separation_arcsec=separation_arcsec,
    )
    if progress_callback is not None:
        chart_note = f" (chart {chart_id})" if chart_id else ""
        progress_callback(
            f"VSP merge complete{chart_note}: {notes['vsp_matches']} {label}(s) received sequence magnitudes."
        )

    for star in stars:
        prefer_catalog_display_magnitude(star)
    return notes


def enrich_gaia_stars_with_standard_catalogs(
    gaia_stars: list[CatalogStar],
    solved_field: SolvedField,
    *,
    aavso_chart_id: str | None = None,
    mag_limit: float | None = None,
    progress_callback: Any | None = None,
) -> dict[str, object]:
    """Attach APASS + VSP band mags onto Gaia comps. Returns provenance notes."""
    return enrich_stars_with_standard_catalogs(
        gaia_stars,
        solved_field,
        aavso_chart_id=aavso_chart_id,
        mag_limit=mag_limit,
        progress_callback=progress_callback,
        label="Gaia star",
    )


def query_apass_field(solved_field: SolvedField, *, mag_limit: float | None = None) -> list[CatalogStar]:
    center = SkyCoord(solved_field.center_ra_deg * u.deg, solved_field.center_dec_deg * u.deg)
    radius = max(0.02, float(solved_field.radius_deg)) * u.deg
    columns = ["RAJ2000", "DEJ2000", "Bmag", "e_Bmag", "Vmag", "e_Vmag", "g'mag", "e_g'mag", "r'mag", "e_r'mag", "i'mag", "e_i'mag"]
    vizier = Vizier(columns=columns, row_limit=-1)
    if mag_limit is not None and np.isfinite(mag_limit):
        vizier.COLUMN_FILTERS = {"Vmag": f"<{float(mag_limit):.3f}"}
    try:
        tables = vizier.query_region(center, radius=radius, catalog=_APASS_CATALOG)
    except Exception:
        return []
    if not tables:
        return []

    stars: list[CatalogStar] = []
    for table in tables:
        for row in table:
            ra = _as_optional_float(row.get("RAJ2000"))
            dec = _as_optional_float(row.get("DEJ2000"))
            if ra is None or dec is None:
                continue
            band_magnitudes: dict[str, dict[str, object]] = {}
            for column, band in _APASS_COLUMN_TO_BAND.items():
                mag = _as_optional_float(row.get(column))
                if mag is None:
                    continue
                error = _as_optional_float(row.get(_APASS_ERROR_COLUMNS.get(band, "")))
                entry: dict[str, object] = {"mag": mag, "source": "apass"}
                if error is not None:
                    entry["error"] = error
                band_magnitudes[band] = entry
            if not band_magnitudes:
                continue
            primary = band_magnitudes.get("V") or next(iter(band_magnitudes.values()))
            stars.append(
                CatalogStar(
                    catalog="apass9",
                    source_id=f"apass-{ra:.6f}-{dec:.6f}",
                    name=f"APASS {ra:.5f} {dec:.5f}",
                    ra_deg=float(ra),
                    dec_deg=float(dec),
                    magnitude=float(primary["mag"]),
                    is_variable=False,
                    metadata={"band_magnitudes": band_magnitudes},
                )
            )
    return stars


def query_vsp_field(
    solved_field: SolvedField,
    *,
    aavso_chart_id: str | None = None,
    mag_limit: float | None = None,
    timeout_seconds: float = 30.0,
) -> tuple[list[CatalogStar], str | None]:
    params: dict[str, object] = {"format": "json", "all": "on"}
    chart_id = str(aavso_chart_id or "").strip()
    if chart_id:
        params["chartid"] = chart_id
    else:
        params["ra"] = f"{solved_field.center_ra_deg:.6f}"
        params["dec"] = f"{solved_field.center_dec_deg:.6f}"
        # VSP FOV is arcminutes.
        params["fov"] = f"{max(5.0, float(solved_field.radius_deg) * 2.0 * 60.0):.1f}"
        params["maglimit"] = f"{float(mag_limit):.1f}" if mag_limit is not None and np.isfinite(mag_limit) else "16.5"

    try:
        response = requests.get(_VSP_API_URL, params=params, timeout=timeout_seconds)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return [], None

    returned_chart_id = str(payload.get("chartid") or chart_id or "").strip() or None
    photometry = payload.get("photometry")
    if not isinstance(photometry, list):
        return [], returned_chart_id

    stars: list[CatalogStar] = []
    for item in photometry:
        if not isinstance(item, dict):
            continue
        ra = _parse_vsp_coordinate(item.get("ra"), is_ra=True)
        dec = _parse_vsp_coordinate(item.get("dec"), is_ra=False)
        if ra is None or dec is None:
            continue
        band_magnitudes: dict[str, dict[str, object]] = {}
        for band_item in item.get("bands") or []:
            if not isinstance(band_item, dict):
                continue
            raw_band = str(band_item.get("band") or "").strip().upper()
            band = _VSP_BAND_ALIASES.get(raw_band)
            mag = _as_optional_float(band_item.get("mag"))
            if band is None or mag is None:
                continue
            error = _as_optional_float(band_item.get("error"))
            entry: dict[str, object] = {"mag": mag, "source": "vsp"}
            if error is not None:
                entry["error"] = error
            band_magnitudes[band] = entry
        if not band_magnitudes:
            continue
        auid = str(item.get("auid") or "").strip() or f"vsp-{ra:.6f}-{dec:.6f}"
        label = str(item.get("label") or auid)
        primary = band_magnitudes.get("V") or next(iter(band_magnitudes.values()))
        stars.append(
            CatalogStar(
                catalog="aavso-vsp",
                source_id=auid,
                name=label,
                ra_deg=float(ra),
                dec_deg=float(dec),
                magnitude=float(primary["mag"]),
                is_variable=False,
                metadata={"band_magnitudes": band_magnitudes, "auid": auid},
            )
        )
    return stars, returned_chart_id


def merge_band_magnitudes_by_sky_match(
    targets: list[CatalogStar],
    donors: list[CatalogStar],
    *,
    source: str,
    separation_arcsec: float = _MATCH_SEPARATION_ARCSEC,
) -> int:
    if not targets or not donors:
        return 0
    target_coords = SkyCoord(
        ra=[star.ra_deg for star in targets] * u.deg,
        dec=[star.dec_deg for star in targets] * u.deg,
    )
    donor_coords = SkyCoord(
        ra=[star.ra_deg for star in donors] * u.deg,
        dec=[star.dec_deg for star in donors] * u.deg,
    )
    idx, separation, _ = target_coords.match_to_catalog_sky(donor_coords)
    matched = 0
    max_sep = separation_arcsec * u.arcsec
    for target_index, donor_index in enumerate(idx):
        if separation[target_index] > max_sep:
            continue
        donor = donors[int(donor_index)]
        donor_bands = get_band_magnitudes(donor)
        if not donor_bands:
            continue
        target = targets[target_index]
        for band, entry in donor_bands.items():
            mag = _as_optional_float(entry.get("mag"))
            if mag is None:
                continue
            set_band_magnitude(
                target,
                band,
                mag,
                error=_as_optional_float(entry.get("error")),
                source=str(entry.get("source") or source),
            )
        matched += 1
    return matched


def compute_standard_magnitude_context(
    target: PhotometryMeasurement,
    references: list[PhotometryMeasurement],
) -> tuple[float | None, float | None, float | None, float | None, int, str | None, str | None]:
    """Return standard mag, error, ZP, ZP error, count, band, dominant source."""
    if target.instrumental_magnitude is None:
        return None, None, None, None, 0, None, None

    band = normalize_photometric_band(target.filter_name)
    zero_points: list[float] = []
    reference_errors: list[float] = []
    sources: list[str] = []
    for reference in references:
        catalog_mag, catalog_error, catalog_source = resolve_band_catalog_magnitude(reference, band)
        if catalog_mag is None or reference.instrumental_magnitude is None:
            continue
        zero_point = float(catalog_mag - reference.instrumental_magnitude)
        if not np.isfinite(zero_point):
            continue
        zero_points.append(zero_point)
        if catalog_source:
            sources.append(catalog_source)
        if catalog_error is not None and catalog_error > 0:
            inst_error = _instrumental_magnitude_error(reference)
            if inst_error is not None and inst_error > 0:
                reference_errors.append(math.sqrt(catalog_error * catalog_error + inst_error * inst_error))
            else:
                reference_errors.append(float(catalog_error))
        else:
            inst_error = _instrumental_magnitude_error(reference)
            reference_errors.append(inst_error if inst_error is not None and inst_error > 0 else float("nan"))

    if not zero_points:
        return None, None, None, None, 0, band, None

    zero_point_array = np.asarray(zero_points, dtype=float)
    error_array = np.asarray(reference_errors, dtype=float)
    finite_error_mask = np.isfinite(error_array) & (error_array > 0)
    if finite_error_mask.all():
        weights = 1.0 / np.square(error_array)
        zero_point_magnitude = float(np.average(zero_point_array, weights=weights))
        zero_point_magnitude_error = math.sqrt(1.0 / float(np.sum(weights)))
    else:
        zero_point_magnitude = float(np.mean(zero_point_array))
        if zero_point_array.size > 1:
            zero_point_magnitude_error = float(np.std(zero_point_array, ddof=1) / math.sqrt(zero_point_array.size))
        else:
            zero_point_magnitude_error = None

    standard_magnitude = float(target.instrumental_magnitude + zero_point_magnitude)
    target_mag_error = _instrumental_magnitude_error(target)
    standard_magnitude_error = None
    if target_mag_error is not None and zero_point_magnitude_error is not None:
        standard_magnitude_error = math.sqrt(target_mag_error * target_mag_error + zero_point_magnitude_error * zero_point_magnitude_error)
    elif target_mag_error is not None:
        standard_magnitude_error = float(target_mag_error)

    dominant_source = None
    if sources:
        dominant_source = max(set(sources), key=sources.count)
    return (
        standard_magnitude,
        standard_magnitude_error,
        zero_point_magnitude,
        zero_point_magnitude_error,
        int(zero_point_array.size),
        band,
        dominant_source,
    )


def catalog_has_band_magnitudes(stars: list[CatalogStar]) -> bool:
    for star in stars:
        bands = get_band_magnitudes(star)
        if any(str(entry.get("source") or "") in {"vsp", "apass"} for entry in bands.values()):
            return True
    return False


def _band_magnitudes_mutable(target: CatalogStar | PhotometryMeasurement | dict[str, Any]) -> dict[str, dict[str, object]]:
    if isinstance(target, dict):
        bands = target.setdefault("band_magnitudes", {})
        if not isinstance(bands, dict):
            bands = {}
            target["band_magnitudes"] = bands
        return bands
    if isinstance(target, CatalogStar):
        bands = target.metadata.setdefault("band_magnitudes", {})
        if not isinstance(bands, dict):
            bands = {}
            target.metadata["band_magnitudes"] = bands
        return bands
    bands = getattr(target, "band_magnitudes", None)
    if not isinstance(bands, dict):
        bands = {}
        target.band_magnitudes = bands
    return bands


def _instrumental_magnitude_error(measurement: PhotometryMeasurement) -> float | None:
    if measurement.flux is None or measurement.flux_error is None or measurement.flux <= 0 or measurement.flux_error < 0:
        return None
    return (2.5 / math.log(10.0)) * (measurement.flux_error / measurement.flux)


def _as_optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(resolved):
        return None
    return resolved


def _parse_vsp_coordinate(value: object, *, is_ra: bool) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        unit = u.hourangle if is_ra else u.deg
        return float(SkyCoord(f"{text} 0", unit=(unit, u.deg)).ra.deg) if is_ra else float(
            SkyCoord(f"0 {text}", unit=(u.hourangle, u.deg)).dec.deg
        )
    except Exception:
        try:
            # Sexagesimal alone: HH:MM:SS or DD:MM:SS
            parts = text.replace(" ", ":").split(":")
            if len(parts) < 2:
                return None
            sign = -1.0 if parts[0].startswith("-") else 1.0
            numbers = [abs(float(part)) for part in parts]
            while len(numbers) < 3:
                numbers.append(0.0)
            amount = numbers[0] + numbers[1] / 60.0 + numbers[2] / 3600.0
            if is_ra:
                return sign * amount * 15.0
            return sign * amount
        except Exception:
            return None
