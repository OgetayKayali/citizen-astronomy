"""Literature spectral classifications for HR rows, with a bounded field cache.

SIMBAD positions are ICRS J2000; HR Gaia coordinates are at epoch J2016.
Exact Gaia DR3 identifiers take precedence over conservative position matches.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
import time
from collections import Counter
from pathlib import Path

import numpy as np
import requests
from scipy.spatial import cKDTree


_ENDPOINT = "https://simbad.cds.unistra.fr/simbad/sim-tap/sync"
_CACHE_SECONDS = 30 * 86400
_MAX_ROWS = 50000


def literature_spectral_type(value: str | None) -> str | None:
    """Return only defensible OBAFGKM groups; preserve broad OB separately."""
    text = (value or "").strip()
    if not text or "+" in text:
        return None
    if re.fullmatch(r"OB[-?: ]*", text):
        return "OB"
    # Do not turn white dwarfs, carbon stars, composites or cross-class ranges
    # into ordinary OBAFGKM stars by taking their first character.
    if re.search(r"[/\-]\s*[OBAFGKM]", text):
        return None
    match = re.match(r"^([OBAFGKM])(?=\d|\(|\s|[IV:]|$)", text)
    return match.group(1) if match else None


def literature_luminosity_class(value: str | None) -> str | None:
    text = (value or "").strip()
    if "+" in text:
        return None
    if re.match(r"^D[ABCOQXZ]", text):
        return "White Dwarfs"
    if literature_spectral_type(text) not in tuple("OBAFGKM"):
        return None
    # Keep intermediate/uncertain classes unknown instead of assigning a side.
    if re.search(r"[IV][ab0-9]*\s*[/\-]\s*[IV]", text):
        return None
    match = re.search(r"(?<![A-Za-z])(III|IV|VI|II|I|V)(?:ab|a|b)?(?![IV])", text)
    if not match:
        return None
    return {"I": "Supergiants", "III": "Giants", "IV": "Subgiants", "V": "Main Sequence"}.get(match.group(1))


def _query_field(ra: float, dec: float, radius: float) -> list[dict]:
    query = f"""SELECT TOP {_MAX_ROWS + 1} b.main_id,b.ra,b.dec,b.sp_type,
        b.sp_bibcode,b.sp_qual,ids.ids FROM basic AS b
        LEFT JOIN ids ON b.oid=ids.oidref
        WHERE 1=CONTAINS(POINT('ICRS',b.ra,b.dec),
        CIRCLE('ICRS',{ra:.10f},{dec:.10f},{radius:.10f}))
        AND b.sp_type IS NOT NULL AND b.sp_type != ''"""
    response = requests.get(
        _ENDPOINT,
        params={"request": "doQuery", "lang": "adql", "format": "json", "query": query, "MAXREC": _MAX_ROWS + 1},
        headers={"User-Agent": "CitizenAstronomy/HR-literature-types"},
        timeout=(5, 20),
    )
    response.raise_for_status()
    payload = response.json()
    names = [column["name"] for column in payload["metadata"]]
    if not {"main_id", "ra", "dec", "sp_type", "sp_bibcode", "sp_qual", "ids"}.issubset(names):
        raise ValueError("Unexpected SIMBAD spectral-type response")
    records = [dict(zip(names, row, strict=True)) for row in payload["data"]]
    if len(records) > _MAX_ROWS:
        raise ValueError("SIMBAD spectral-type field exceeds the safe row limit")
    return records


def _gaia_ids(record: dict) -> set[str]:
    return set(re.findall(r"(?:^|\|)Gaia DR3 (\d+)(?=\||$)", record.get("ids") or ""))


def _validate_records(records: list[dict]) -> None:
    if not isinstance(records, list) or len(records) > _MAX_ROWS:
        raise ValueError("Invalid spectral catalog")
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Invalid spectral catalog row")
        if not all(isinstance(record.get(key), str) and record[key].strip() for key in ("main_id", "sp_type")):
            raise ValueError("Missing spectral classification")
        if not all(record.get(key) is None or isinstance(record[key], str) for key in ("ids", "sp_bibcode", "sp_qual")):
            raise ValueError("Invalid spectral provenance")
        ra, dec = float(record["ra"]), float(record["dec"])
        if not (0 <= ra < 360 and -90 <= dec <= 90):
            raise ValueError("Invalid spectral coordinates")


def _unit_vector(ra: float, dec: float) -> np.ndarray:
    ra, dec = math.radians(ra), math.radians(dec)
    return np.array([math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec)])


def apply_literature_types(rows: list, records: list[dict]) -> tuple[int, int]:
    """Match identifiers, then unique reciprocal positions within one arcsecond.

    Position fallback requires both Gaia proper-motion components. Conflicting
    Gaia identifiers and duplicate/ambiguous counterparts are never accepted.
    """
    _validate_records(records)
    by_id: dict[str, list[int]] = {}
    for index, record in enumerate(records):
        for source_id in _gaia_ids(record):
            by_id.setdefault(source_id, []).append(index)
    exact: dict[int, int] = {}
    candidates: dict[int, int] = {}
    tree = cKDTree([_unit_vector(float(r["ra"]), float(r["dec"])) for r in records]) if records else None
    limit = 2 * math.sin(math.radians(1 / 3600) / 2)
    for index, row in enumerate(rows):
        if row.catalog != "gaia-dr3":
            continue
        matches = by_id.get(str(row.source_id), [])
        if matches:
            if len(matches) == 1:
                exact[index] = matches[0]
            continue
        pmra = getattr(row, "pm_ra_mas_per_year", None)
        pmdec = getattr(row, "pm_dec_mas_per_year", None)
        if not records or pmra is None or pmdec is None or not all(map(math.isfinite, (pmra, pmdec))):
            continue
        cosine = math.cos(math.radians(row.dec_deg))
        if abs(cosine) < 0.01:
            continue
        vector = _unit_vector(row.ra_deg - 16 * pmra / (3_600_000 * cosine), row.dec_deg - 16 * pmdec / 3_600_000)
        nearby = tree.query_ball_point(vector, limit)
        if len(nearby) == 1 and not _gaia_ids(records[int(nearby[0])]):
            candidates[index] = int(nearby[0])
    used = list(exact.values()) + list(candidates.values())
    counts = Counter(used)
    matched_exact = matched_position = 0
    for index, record_index in {**candidates, **exact}.items():
        if counts[record_index] != 1:
            continue
        record = records[record_index]
        row = rows[index]
        row.literature_spectral_type = record["sp_type"]
        row.spectral_type_source = "CDS/SIMBAD"
        row.spectral_type_reference = record.get("sp_bibcode") or None
        row.spectral_type_quality = record.get("sp_qual") or None
        row.spectral_type_object = record["main_id"]
        row.spectral_type_match_method = "Gaia DR3 identifier" if index in exact else "position (J2000, <=1 arcsec)"
        matched_exact += index in exact
        matched_position += index not in exact
    return matched_exact, matched_position


def enrich_hr_spectral_types(rows: list, solved_field: object, cache_dir: Path, progress_callback=None) -> bool:
    """Enrich in the HR worker; a network/cache failure never prevents plotting."""
    report = progress_callback or (lambda message: None)
    if not rows:
        return True
    ra, dec, radius = (float(getattr(solved_field, name)) for name in ("center_ra_deg", "center_dec_deg", "radius_deg"))
    if not all(map(math.isfinite, (ra, dec, radius))) or not (0 <= ra < 360 and -90 <= dec <= 90 and 0 < radius <= 180):
        report("Literature spectral types unavailable: invalid field footprint.")
        return False
    # Extra margin accommodates epoch differences near the image edge.
    radius = min(180, radius + 0.01)
    key = hashlib.sha256(f"v1:{ra:.10f}:{dec:.10f}:{radius:.10f}".encode()).hexdigest()
    path = Path(cache_dir) / "hr-spectral-types" / f"{key}.json"
    cached = None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        _validate_records(cached["records"])
        if not isinstance(cached["fetched_at"], (int, float)) or not math.isfinite(cached["fetched_at"]):
            cached = None
    except (OSError, ValueError, KeyError, TypeError):
        cached = None
    fresh = cached is not None and 0 <= time.time() - cached["fetched_at"] < _CACHE_SECONDS
    if fresh:
        records = cached["records"]
        report("Using cached SIMBAD literature spectral types.")
    else:
        report("Looking up literature spectral types in SIMBAD (one field query).")
        try:
            records = _query_field(ra, dec, radius)
            _validate_records(records)
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            if cached is None:
                report(f"Literature spectral types unavailable ({type(error).__name__}); unclassified stars remain Unknown. Generate again to retry.")
                return False
            records = cached["records"]
            report("SIMBAD unavailable; using older cached literature spectral types.")
        else:
            temporary = None
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as handle:
                    temporary = Path(handle.name)
                    json.dump({"fetched_at": time.time(), "records": records}, handle)
                temporary.replace(path)
            except OSError:
                report("Literature types loaded, but the cache could not be saved.")
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
    try:
        exact, position = apply_literature_types(rows, records)
    except (ValueError, KeyError, TypeError):
        report("Literature spectral types unavailable: invalid cached catalog data. Regenerate after clearing this field's spectral cache.")
        return False
    report(f"Literature spectral types: {exact + position} of {len(rows)} measured stars matched ({exact} Gaia IDs, {position} positions). Remaining stars are Unknown unless estimated fallback is enabled. Source: CDS/SIMBAD.")
    return True
