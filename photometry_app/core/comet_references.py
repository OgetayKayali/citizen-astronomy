"""Field comparison sequences, catalog passbands, and reusable sky-coordinate CSVs."""
import csv
import math
from pathlib import Path

import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS
from astroquery.vizier import Vizier
import numpy as np

from .comet_photometry import ComparisonStar, apass_comparison_stars
from .models import CatalogStar, SolvedField


def _refcat_field(field, band, server):
    # Tonry et al. (2018), CDS J/ApJ/867/105/refcat2, native PS1 photometry.
    mag = band[-1] + "mag"
    columns = ["RA_ICRS", "DE_ICRS", mag, "e_" + mag, "dupvar"]
    # Ask for the published table. Some VizieR mirrors return an empty RESOURCE
    # for a projected list using the normalized RA_ICRS / DE_ICRS column names.
    query = Vizier(columns=["**"], row_limit=-1,
                   vizier_server=server, timeout=25)
    tables = query.query_region(SkyCoord(field.center_ra_deg, field.center_dec_deg, unit="deg"),
                                radius=field.radius_deg*u.deg, catalog="J/ApJ/867/105/refcat2")
    stars = []
    for table in tables:
        for row in table:
            # Table 4: 2 is unknown variability, 1 is variable, +4 is duplicate.
            if row["dupvar"] not in (0, 2) or any(np.ma.is_masked(row[k]) for k in columns):
                continue
            ra, dec, value, error = map(float, (row["RA_ICRS"], row["DE_ICRS"], row[mag], row["e_"+mag]))
            if value >= 17 or error <= 0:
                continue
            source_id = f"Refcat2 {ra:.7f} {dec:+.7f}"
            stars.append(CatalogStar("ATLAS Refcat2", source_id, source_id, ra, dec, value, False,
                metadata={"band_magnitudes": {band: {"mag": value, "error": error}}}))
    return stars


def query_comet_references(pair, band):
    from requests import RequestException
    from .standard_magnitude import query_apass_field
    wcs = WCS(fits.Header.fromstring(pair.header_text)).celestial
    center = wcs.pixel_to_world(pair.center_x, pair.center_y).icrs
    ra, dec = center.ra.deg, center.dec.deg
    h, w = pair.stars.shape
    radius = min(0.35, max(0.02, math.hypot(w, h) * pair.pixel_scale / 3600))
    field = SolvedField(ra, dec, radius, w, h, pair.reference_path)
    atlas = band.startswith("PS1")
    name = "ATLAS Refcat2" if atlas else "APASS"
    for server in ("vizier.cfa.harvard.edu", "vizier.cds.unistra.fr"):
        try:
            catalog = (_refcat_field(field, band, server) if atlas else
                       query_apass_field(field, mag_limit=17, vizier_server=server))
        except (RequestException, OSError):
            continue
        if catalog:
            return name, apass_comparison_stars(catalog, pair, band)
    return name, []


_COLUMNS = ("catalog", "band", "source_id", "name", "ra_deg", "dec_deg", "magnitude",
            "catalog_error", "color_bv", "selected")


def comparison_path_pixels(pair):
    frames = pair.provenance["frames"]
    coords = SkyCoord([f["ra_deg"] for f in frames], [f["dec_deg"] for f in frames], unit="deg")
    wcs = WCS(fits.Header.fromstring(pair.header_text)).celestial
    return np.column_stack(wcs.world_to_pixel(coords))


def write_comparison_sequence(path: Path, pair, stars, band):
    wcs = WCS(fits.Header.fromstring(pair.header_text)).celestial
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(_COLUMNS)
        for star in stars:
            coord = wcs.pixel_to_world(star.x, star.y).icrs
            ra, dec = coord.ra.deg, coord.dec.deg
            writer.writerow([pair.provenance.get("comparison_catalog", "APASS"), band, star.source_id,
                             star.name, ra, dec, star.magnitude, star.catalog_error, star.color_bv,
                             int(star.selected)])


def read_comparison_sequence(path: Path, pair, band):
    wcs = WCS(fits.Header.fromstring(pair.header_text)).celestial
    stars, names, ids, positions = [], set(), set(), set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"catalog", "band", "source_id", "ra_deg", "dec_deg", "magnitude", "catalog_error"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError("Required CSV columns: " + ", ".join(sorted(required)))
        for n, row in enumerate(reader, start=2):
            if row["band"] != band:
                raise ValueError(f"Row {n}: expected the prepared comparison band {band}.")
            catalog = row["catalog"].strip()
            source_id = row["source_id"].strip()
            ra, dec, mag, error = [float(row[k]) for k in ("ra_deg", "dec_deg", "magnitude", "catalog_error")]
            color = float(row["color_bv"]) if row.get("color_bv") else None
            selected = (row.get("selected") or "1").strip()
            if (not source_id or source_id in ids or not catalog or (ra, dec) in positions
                    or not np.isfinite([ra, dec, mag, error]).all() or error <= 0
                    or not (0 <= ra < 360 and -90 <= dec <= 90)
                    or (color is not None and not math.isfinite(color)) or selected not in ("0", "1")):
                raise ValueError(f"Invalid or duplicate comparison star on CSV row {n}.")
            ids.add(source_id)
            positions.add((ra, dec))
            names.add(catalog)
            x, y = map(float, wcs.world_to_pixel(SkyCoord(ra, dec, unit="deg")))
            if not (0 <= x < pair.stars.shape[1] and 0 <= y < pair.stars.shape[0]):
                raise ValueError(f"Row {n}: {source_id} falls outside the prepared star stack.")
            stars.append(ComparisonStar(source_id, row.get("name") or source_id, x, y, mag, error,
                                        color, selected == "1"))
    if len(names) != 1:
        raise ValueError("Use one named catalog per comparison sequence.")
    return names.pop(), stars
