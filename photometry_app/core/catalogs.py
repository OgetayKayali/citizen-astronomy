from __future__ import annotations



from dataclasses import dataclass

from html import unescape

import json

from pathlib import Path

import re

from typing import Callable



from astroquery.simbad import Simbad

from astroquery.vizier import Vizier

from astroquery.ipac.nexsci.nasa_exoplanet_archive import NasaExoplanetArchive

from astropy.coordinates import SkyCoord

from astropy import units as u

import requests



from photometry_app.core.models import CatalogStar, FieldCatalog, SolvedField


_GAIA_DR3_VIZIER_COLUMNS = (
    "Source",
    "RA_ICRS",
    "DE_ICRS",
    "Gmag",
    "Plx",
    "e_Plx",
    "pmRA",
    "pmDE",
    "BP-RP",
    "Name",
    "HIP",
    "HD",
    "HR",
    "TYC2",
    "TYC",
    "BD",
    "CD",
    "CPD",
    "SAO",
    "2MASS",
    "AllWISE",
)



@dataclass(frozen=True)

class LiteraturePeriodResult:

    period_days: float | None = None

    eclipse_duration_hours: float | None = None

    source: str = ""





@dataclass(frozen=True)

class CatalogTargetDetails:

    main_id: str | None = None

    object_type: str | None = None

    spectral_type: str | None = None

    visual_magnitude: float | None = None

    source: str = ""





@dataclass(frozen=True)

class CatalogTargetAtCoordinate:

    main_id: str

    object_type: str | None = None

    ra_deg: float = 0.0

    dec_deg: float = 0.0

    separation_arcsec: float | None = None

    spectral_type: str | None = None

    visual_magnitude: float | None = None

    source: str = "SIMBAD"





class CatalogService:

    def __init__(self, cache_dir: Path) -> None:

        self._cache_dir = cache_dir

        self._cache_dir.mkdir(parents=True, exist_ok=True)

        self._vizier = Vizier(columns=["*"], row_limit=-1)



    def query_field_catalog(

        self,

        solved_field: SolvedField,

        *,

        include_gaia: bool = True,

        include_variable_stars: bool = True,

        include_exoplanets: bool = True,

        gaia_max_magnitude: float | None = None,

        gaia_row_cap: int | None = None,

        variable_star_max_magnitude: float | None = None,

        exoplanet_max_magnitude: float | None = None,

        progress_callback: Callable[[str], None] | None = None,

    ) -> FieldCatalog:

        cache_path = self._cache_dir / self._field_catalog_cache_key(
            solved_field,
            include_gaia=include_gaia,
            include_variable_stars=include_variable_stars,
            include_exoplanets=include_exoplanets,
            gaia_max_magnitude=gaia_max_magnitude,
            gaia_row_cap=gaia_row_cap,
            variable_star_max_magnitude=variable_star_max_magnitude,
            exoplanet_max_magnitude=exoplanet_max_magnitude,
        )

        if cache_path.exists():

            try:

                cached_catalog = self._load_cache(cache_path)

                if not include_variable_stars or not _needs_variable_catalog_refresh(cached_catalog):

                    if progress_callback is not None:

                        progress_callback(f"Loaded cached field catalog: {self._catalog_summary(cached_catalog)}.")

                    return cached_catalog

            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):

                cache_path.unlink(missing_ok=True)



        gaia_stars: list[CatalogStar] = []
        if include_gaia:
            if progress_callback is not None:
                gaia_message = (
                    f"Querying Gaia DR3 for the field at RA {solved_field.center_ra_deg:.5f} deg, "
                    f"Dec {solved_field.center_dec_deg:.5f} deg, radius {solved_field.radius_deg:.4f} deg"
                )
                if gaia_max_magnitude is not None:
                    gaia_message += f", G <= {float(gaia_max_magnitude):.1f}"
                if gaia_row_cap is not None:
                    gaia_message += f", capped at {int(gaia_row_cap)} row(s)"
                progress_callback(f"{gaia_message}.")
            gaia_stars = self._query_gaia_field(
                solved_field,
                maximum_magnitude=gaia_max_magnitude,
                row_limit=gaia_row_cap,
                progress_callback=progress_callback,
                query_label="Gaia DR3",
            )
            if progress_callback is not None:
                progress_callback(f"Gaia DR3 lookup complete: {len(gaia_stars)} star(s) returned.")

        variable_stars: list[CatalogStar] = []
        if include_variable_stars:
            if progress_callback is not None:
                variable_message = "Querying VSX for known variable stars in the same field"
                if variable_star_max_magnitude is not None:
                    variable_message += f", max mag <= {float(variable_star_max_magnitude):.1f}"
                progress_callback(f"{variable_message}.")
            vsx_query = self._query_vsx
            if variable_star_max_magnitude is not None:
                vsx_query = lambda center, radius: self._query_vsx_filtered(
                    center,
                    radius,
                    maximum_magnitude=variable_star_max_magnitude,
                )
            variable_stars = self._query_vizier_with_alternate_centers(
                solved_field,
                vsx_query,
                "VSX",
                progress_callback=progress_callback,
            )
            if progress_callback is not None:
                progress_callback(f"VSX lookup complete: {len(variable_stars)} variable star(s) returned.")

        exoplanets: list[CatalogStar] = []
        if include_exoplanets:
            if progress_callback is not None:
                progress_callback("Querying the NASA Exoplanet Archive for host stars in the same field.")
            center = SkyCoord(solved_field.center_ra_deg * u.deg, solved_field.center_dec_deg * u.deg)
            radius = solved_field.radius_deg * u.deg
            exoplanets = self._query_exoplanets(center, radius)
            if exoplanet_max_magnitude is not None:
                exoplanets = _filter_catalog_stars_by_maximum_magnitude(exoplanets, exoplanet_max_magnitude)
            if progress_callback is not None:
                progress_callback(f"Exoplanet lookup complete: {len(exoplanets)} host entry/entries returned.")

        catalog = FieldCatalog(

            center_ra_deg=solved_field.center_ra_deg,

            center_dec_deg=solved_field.center_dec_deg,

            radius_deg=solved_field.radius_deg,

            gaia_stars=gaia_stars,

            variable_stars=variable_stars,

            exoplanets=exoplanets,

        )

        self._store_cache(cache_path, catalog)

        if progress_callback is not None:

            progress_callback(f"Catalog lookup complete: {self._catalog_summary(catalog)} cached.")

        return catalog



    def query_gaia_stars(

        self,

        solved_field: SolvedField,

        progress_callback: Callable[[str], None] | None = None,

    ) -> list[CatalogStar]:

        cache_path = self._cache_dir / self._cache_key(solved_field)

        if cache_path.exists():

            try:

                cached_catalog = self._load_cache(cache_path)

                if cached_catalog.gaia_stars:

                    if progress_callback is not None:

                        progress_callback(

                            "Loaded cached Gaia field stars: "

                            f"{len(cached_catalog.gaia_stars)} source(s) for the current solved field."

                        )

                    return list(cached_catalog.gaia_stars)

            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):

                cache_path.unlink(missing_ok=True)



        center = SkyCoord(solved_field.center_ra_deg * u.deg, solved_field.center_dec_deg * u.deg)

        radius = solved_field.radius_deg * u.deg

        if progress_callback is not None:

            progress_callback(

                f"Querying Gaia DR3 for the asteroid/comet field at RA {solved_field.center_ra_deg:.5f} deg, "

                f"Dec {solved_field.center_dec_deg:.5f} deg, radius {solved_field.radius_deg:.4f} deg."

            )

        gaia_stars = self._query_gaia_field(
            solved_field,
            progress_callback=progress_callback,
            query_label="Gaia DR3",
        )

        partial_catalog = FieldCatalog(

            center_ra_deg=solved_field.center_ra_deg,

            center_dec_deg=solved_field.center_dec_deg,

            radius_deg=solved_field.radius_deg,

            gaia_stars=gaia_stars,

        )

        self._store_cache(cache_path, partial_catalog)

        if progress_callback is not None:

            progress_callback(f"Gaia DR3 lookup complete: {len(gaia_stars)} star(s) returned for visible-limit estimation.")

        return gaia_stars



    def query_gaia_stars_limited(

        self,

        solved_field: SolvedField,

        maximum_magnitude: float,

        row_limit: int | None = None,

        progress_callback: Callable[[str], None] | None = None,

    ) -> list[CatalogStar]:

        magnitude_limit = max(-5.0, min(30.0, float(maximum_magnitude)))

        normalized_row_limit = None if row_limit is None else max(1, int(row_limit))

        cache_path = self._cache_dir / self._gaia_filtered_cache_key(
            solved_field,
            maximum_magnitude=magnitude_limit,
            row_limit=normalized_row_limit,
        )

        if cache_path.exists():

            try:

                cached_catalog = self._load_cache(cache_path)

                if progress_callback is not None:

                    progress_callback(

                        "Loaded cached Gaia transient-veto stars: "

                        f"{len(cached_catalog.gaia_stars)} source(s) at G <= {magnitude_limit:.1f}"
                        f"{'' if normalized_row_limit is None else f' with a {normalized_row_limit}-row cap'}"
                        "."

                    )

                return list(cached_catalog.gaia_stars)

            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):

                cache_path.unlink(missing_ok=True)



        if progress_callback is not None:

            progress_callback(

                f"Querying Gaia DR3 for the transient-veto field at RA {solved_field.center_ra_deg:.5f} deg, "

                f"Dec {solved_field.center_dec_deg:.5f} deg, radius {solved_field.radius_deg:.4f} deg, "

                f"G <= {magnitude_limit:.1f}"
                f"{'' if normalized_row_limit is None else f', capped at {normalized_row_limit} row(s)'}"
                "."

            )

        gaia_stars = self._query_gaia_field(
            solved_field,
            maximum_magnitude=magnitude_limit,
            row_limit=normalized_row_limit,
            progress_callback=progress_callback,
            query_label="Gaia DR3",
        )

        partial_catalog = FieldCatalog(

            center_ra_deg=solved_field.center_ra_deg,

            center_dec_deg=solved_field.center_dec_deg,

            radius_deg=solved_field.radius_deg,

            gaia_stars=gaia_stars,

        )

        self._store_cache(cache_path, partial_catalog)

        if progress_callback is not None:

            progress_callback(

                f"Gaia DR3 transient-veto lookup complete: {len(gaia_stars)} star(s) returned at G <= {magnitude_limit:.1f}."

            )

        return gaia_stars



    def clear_cache(self) -> int:

        removed = 0

        for cache_file in self._cache_dir.glob("*.json"):

            cache_file.unlink(missing_ok=True)

            removed += 1

        return removed



    def clear_field_cache(self, solved_field: SolvedField) -> int:

        cache_stem = self._cache_key(solved_field).removesuffix(".json")

        removed = 0

        for cache_path in self._cache_dir.glob(f"{cache_stem}*.json"):

            cache_path.unlink(missing_ok=True)

            removed += 1

        return removed

    def _query_vizier_with_alternate_centers(

        self,

        solved_field: SolvedField,

        query_function: Callable[[SkyCoord, u.Quantity], list[CatalogStar]],

        query_label: str,

        *,

        progress_callback: Callable[[str], None] | None = None,

    ) -> list[CatalogStar]:

        radius = solved_field.radius_deg * u.deg

        centers = self._candidate_query_centers(solved_field)

        last_error: Exception | None = None

        for attempt_index, center in enumerate(centers, start=1):

            try:

                return query_function(center, radius)

            except Exception as exc:

                last_error = exc

                if attempt_index >= len(centers):

                    break

                if progress_callback is not None:

                    progress_callback(

                        f"{query_label} lookup failed at one solved-field center; retrying alternate solved-field center {attempt_index + 1} of {len(centers)}."

                    )

        if last_error is not None:

            raise last_error

        return []

    def _query_gaia_field(
        self,
        solved_field: SolvedField,
        *,
        maximum_magnitude: float | None = None,
        row_limit: int | None = None,
        progress_callback: Callable[[str], None] | None = None,
        query_label: str = "Gaia DR3",
    ) -> list[CatalogStar]:
        return self._query_vizier_with_alternate_centers(
            solved_field,
            lambda center, radius: self._query_gaia_filtered(
                center,
                radius,
                maximum_magnitude=maximum_magnitude,
                row_limit=row_limit,
            ),
            query_label,
            progress_callback=progress_callback,
        )

    def _candidate_query_centers(self, solved_field: SolvedField) -> tuple[SkyCoord, ...]:

        center = SkyCoord(solved_field.center_ra_deg * u.deg, solved_field.center_dec_deg * u.deg)

        offset_deg = max(float(solved_field.radius_deg) * 0.35, 0.02)

        offset = offset_deg * u.deg

        return (

            center,

            center.spherical_offsets_by(offset, 0.0 * u.deg),

            center.spherical_offsets_by(-offset, 0.0 * u.deg),

            center.spherical_offsets_by(0.0 * u.deg, offset),

            center.spherical_offsets_by(0.0 * u.deg, -offset),

        )

    def _query_gaia(self, center: SkyCoord, radius: u.Quantity) -> list[CatalogStar]:

        return self._query_gaia_filtered(center, radius)



    def _query_gaia_filtered(
        self,
        center: SkyCoord,
        radius: u.Quantity,
        maximum_magnitude: float | None = None,
        row_limit: int | None = None,
    ) -> list[CatalogStar]:

        column_filters: dict[str, str] = {}

        if maximum_magnitude is not None:

            column_filters["Gmag"] = f"<{float(maximum_magnitude):.3f}"

        vizier = Vizier(
            columns=list(_GAIA_DR3_VIZIER_COLUMNS),
            row_limit=-1 if row_limit is None else max(1, int(row_limit)),
            column_filters=column_filters,
        )

        tables = vizier.query_region(center, radius=radius, catalog="I/355/gaiadr3")

        return self._gaia_stars_from_tables(tables)



    def _gaia_stars_from_tables(self, tables: object) -> list[CatalogStar]:

        if not tables:

            return []

        table = tables[0]

        stars: list[CatalogStar] = []

        for row in table:

            source_id = str(row["Source"])

            preferred_display_name = _preferred_gaia_display_name(row, source_id=source_id)

            magnitude = _as_float(row.get("Gmag"))

            stars.append(

                CatalogStar(

                    catalog="gaia-dr3",

                    source_id=source_id,


                    name=source_id,

                    ra_deg=float(row["RA_ICRS"]),

                    dec_deg=float(row["DE_ICRS"]),

                    magnitude=magnitude,

                    is_variable=False,

                    metadata={

                        "bp_rp": _as_float(row.get("BP-RP")),

                        "parallax_mas": _as_float(row.get("Plx")),

                        "parallax_error_mas": _as_float(row.get("e_Plx")),

                        "pm_ra": _as_float(row.get("pmRA")),

                        "pm_dec": _as_float(row.get("pmDE")),

                        "preferred_display_name": preferred_display_name,

                    },

                )

            )

        return stars


    def _query_vsx(self, center: SkyCoord, radius: u.Quantity) -> list[CatalogStar]:

        tables = self._vizier.query_region(center, radius=radius, catalog="B/vsx/vsx")

        return self._vsx_stars_from_tables(tables)


    def _query_vsx_filtered(self, center: SkyCoord, radius: u.Quantity, maximum_magnitude: float) -> list[CatalogStar]:

        vizier = Vizier(columns=["*", "+max"], row_limit=-1, column_filters={"max": f"<{float(maximum_magnitude):.3f}"})

        tables = vizier.query_region(center, radius=radius, catalog="B/vsx/vsx")

        return self._vsx_stars_from_tables(tables)


    def _vsx_stars_from_tables(self, tables: object) -> list[CatalogStar]:

        if not tables:

            return []

        table = tables[0]

        stars: list[CatalogStar] = []

        for row in table:

            source_id = str(row.get("OID", row.get("Name", "unknown")))

            name = str(row.get("Name", source_id))

            magnitude = _as_float(row.get("max"))

            stars.append(

                CatalogStar(

                    catalog="vsx",

                    source_id=source_id,

                    name=name,

                    ra_deg=float(row["RAJ2000"]),

                    dec_deg=float(row["DEJ2000"]),

                    magnitude=magnitude,

                    is_variable=True,

                    metadata={

                        "type": str(row.get("Type", "")).strip(),

                        "period": _as_float(row.get("Period")),

                        "minimum_magnitude": _as_float(row.get("min")),

                    },

                )

            )

        return stars


    def _query_exoplanets(self, center: SkyCoord, radius: u.Quantity) -> list[CatalogStar]:

        try:

            table = NasaExoplanetArchive.query_region(

                table="pscomppars",

                coordinates=center,

                radius=radius,

                cache=False,

            )

        except Exception:

            return []

        if table is None or len(table) == 0:

            return []


        exoplanets: list[CatalogStar] = []

        for row in table:

            host_name = str(_row_value(row, "hostname", "")).strip()

            planet_name = str(_row_value(row, "pl_name", host_name or "unknown")).strip()

            source_id = planet_name or host_name

            if not source_id:

                continue

            try:

                ra_deg = float(_row_value(row, "ra"))

                dec_deg = float(_row_value(row, "dec"))

            except (TypeError, ValueError):

                continue

            exoplanets.append(

                CatalogStar(

                    catalog="nasa-exoplanet-archive",

                    source_id=source_id,

                    name=planet_name,

                    ra_deg=ra_deg,

                    dec_deg=dec_deg,

                    magnitude=_as_float(_row_value(row, "sy_vmag")),

                    is_variable=False,

                    object_type="exoplanet",

                    metadata={

                        "host_name": host_name,

                        "planet_name": planet_name,

                        "discovery_method": str(_row_value(row, "discoverymethod", "")).strip(),

                        "discovery_facility": str(_row_value(row, "disc_facility", "")).strip(),

                        "orbital_period_days": _as_float(_row_value(row, "pl_orbper")),

                        "transit_duration_hours": _as_float(_row_value(row, "pl_trandur")),

                        "planet_radius_earth": _as_float(_row_value(row, "pl_rade")),

                        "planet_mass_earth": _as_float(_row_value(row, "pl_bmasse")),

                        "transit_depth_ppm": _as_float(_row_value(row, "pl_trandep")),

                    },

                )

            )

        return exoplanets


    def _cache_key(self, solved_field: SolvedField) -> str:

        return (

            f"field_{solved_field.center_ra_deg:.5f}_"

            f"{solved_field.center_dec_deg:.5f}_"

            f"{solved_field.radius_deg:.5f}.json"

        ).replace("-", "m")

    def _field_catalog_cache_key(
        self,
        solved_field: SolvedField,
        *,
        include_gaia: bool,
        include_variable_stars: bool,
        include_exoplanets: bool,
        gaia_max_magnitude: float | None,
        gaia_row_cap: int | None,
        variable_star_max_magnitude: float | None,
        exoplanet_max_magnitude: float | None,
    ) -> str:

        base_key = self._cache_key(solved_field)

        if (
            include_gaia
            and include_variable_stars
            and include_exoplanets
            and gaia_max_magnitude is None
            and gaia_row_cap is None
            and variable_star_max_magnitude is None
            and exoplanet_max_magnitude is None
        ):

            return base_key

        cache_stem = base_key.removesuffix(".json")

        included_layers: list[str] = []

        if include_gaia:

            included_layers.append("gaia")

        if include_variable_stars:

            included_layers.append("vsx")

        if include_exoplanets:

            included_layers.append("exoplanets")

        suffix_parts = ["layers-" + ("-".join(included_layers) if included_layers else "none")]

        if include_gaia and gaia_max_magnitude is not None:

            suffix_parts.append(f"gaia-g{float(gaia_max_magnitude):.2f}")

        if include_gaia and gaia_row_cap is not None:

            suffix_parts.append(f"gaia-n{max(1, int(gaia_row_cap))}")

        if include_variable_stars and variable_star_max_magnitude is not None:

            suffix_parts.append(f"vsx-max{float(variable_star_max_magnitude):.2f}")

        if include_exoplanets and exoplanet_max_magnitude is not None:

            suffix_parts.append(f"exo-v{float(exoplanet_max_magnitude):.2f}")

        return f"{cache_stem}_{'_'.join(suffix_parts)}.json"

    def _gaia_filtered_cache_key(
        self,
        solved_field: SolvedField,
        *,
        maximum_magnitude: float,
        row_limit: int | None,
    ) -> str:

        cache_stem = self._cache_key(solved_field).removesuffix(".json")

        suffix = f"_gaia_g{float(maximum_magnitude):.2f}"

        if row_limit is not None:

            suffix += f"_n{max(1, int(row_limit))}"

        return f"{cache_stem}{suffix}.json"

    def _catalog_summary(self, catalog: FieldCatalog) -> str:

        return (

            f"{len(catalog.gaia_stars)} Gaia stars, "

            f"{len(catalog.variable_stars)} VSX variables, "

            f"and {len(catalog.exoplanets)} exoplanet entries"

        )


    def _load_cache(self, cache_path: Path) -> FieldCatalog:

        payload = json.loads(cache_path.read_text(encoding="utf-8"))

        return FieldCatalog(

            center_ra_deg=payload["center_ra_deg"],

            center_dec_deg=payload["center_dec_deg"],

            radius_deg=payload["radius_deg"],

            gaia_stars=[_catalog_star_from_payload(item) for item in payload.get("gaia_stars", [])],

            variable_stars=[_catalog_star_from_payload(item) for item in payload.get("variable_stars", [])],

            exoplanets=[_catalog_star_from_payload(item) for item in payload.get("exoplanets", [])],

        )


    def _store_cache(self, cache_path: Path, catalog: FieldCatalog) -> None:

        payload = {

            "center_ra_deg": catalog.center_ra_deg,

            "center_dec_deg": catalog.center_dec_deg,

            "radius_deg": catalog.radius_deg,

            "gaia_stars": [_catalog_star_to_payload(item) for item in catalog.gaia_stars],

            "variable_stars": [_catalog_star_to_payload(item) for item in catalog.variable_stars],

            "exoplanets": [_catalog_star_to_payload(item) for item in catalog.exoplanets],

        }

        cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def summarize_catalog_service_error(error: str | BaseException) -> str:

    raw_message = " ".join(str(error).split()).strip()

    if not raw_message:

        return "Catalog lookup failed."

    lowered_message = raw_message.lower()

    if raw_message.startswith("Could not reach the VizieR catalog service"):

        return raw_message

    vizier_lookup = (

        "vizier.cds.unistra.fr" in lowered_message

        or "viz-bin/votable" in lowered_message

        or "i/355/gaiadr3" in lowered_message

        or "b/vsx/vsx" in lowered_message

    )

    if vizier_lookup:

        if "timed out" in lowered_message or "timeout" in lowered_message:

            return (

                "Could not reach the VizieR catalog service for the Gaia/VSX lookup before the request timed out. "

                "Wide fields can take longer; retry after a moment or reduce the field size/magnitude limit."

            )

        if "actively refused" in lowered_message or "failed to establish a new connection" in lowered_message:

            return (

                "Could not reach the VizieR catalog service for the Gaia/VSX lookup because the remote server refused "

                "the connection. Check the network connection or try again later."

            )

        return "Could not reach the VizieR catalog service for the Gaia/VSX lookup. Check the network connection or try again later."

    if isinstance(error, requests.RequestException):

        return "A remote catalog service request failed. Check the network connection or try again later."

    return raw_message





def _needs_variable_catalog_refresh(catalog: FieldCatalog) -> bool:

    return bool(catalog.gaia_stars) and not catalog.variable_stars


def _filter_catalog_stars_by_maximum_magnitude(stars: list[CatalogStar], maximum_magnitude: float) -> list[CatalogStar]:

    magnitude_limit = float(maximum_magnitude)

    return [star for star in stars if star.magnitude is not None and float(star.magnitude) <= magnitude_limit]





def _catalog_star_to_payload(star: CatalogStar) -> dict[str, object]:

    return {

        "catalog": star.catalog,

        "source_id": star.source_id,

        "name": star.name,

        "ra_deg": star.ra_deg,

        "dec_deg": star.dec_deg,

        "magnitude": star.magnitude,

        "is_variable": star.is_variable,

        "object_type": star.object_type,

        "metadata": star.metadata,

    }





def _catalog_star_from_payload(payload: dict[str, object]) -> CatalogStar:

    return CatalogStar(

        catalog=str(payload["catalog"]),

        source_id=str(payload["source_id"]),

        name=str(payload["name"]),

        ra_deg=float(payload["ra_deg"]),

        dec_deg=float(payload["dec_deg"]),

        magnitude=_as_float(payload.get("magnitude")),

        is_variable=bool(payload.get("is_variable", False)),

        object_type=str(payload.get("object_type", "star")),

        metadata=dict(payload.get("metadata", {})),

    )





def fetch_catalog_literature_period_result(star: CatalogStar, timeout_seconds: float = 15.0) -> LiteraturePeriodResult | None:

    normalized_catalog = str(star.catalog).strip().lower()

    if normalized_catalog == "vsx":

        return _fetch_vsx_literature_period_result(star, timeout_seconds)

    if normalized_catalog == "nasa-exoplanet-archive":

        period_days = _as_float(star.metadata.get("orbital_period_days"))

        duration_hours = _as_float(star.metadata.get("transit_duration_hours"))

        if period_days is None and duration_hours is None:

            return None

        return LiteraturePeriodResult(

            period_days=period_days,

            eclipse_duration_hours=duration_hours,

            source="NASA Exoplanet Archive",

        )

    return None





def fetch_catalog_target_details(

    ra_deg: float,

    dec_deg: float,

    *,

    timeout_seconds: float = 10.0,

    radius_arcsec: float = 2.0,

) -> CatalogTargetDetails | None:

    try:

        target_coord = SkyCoord(float(ra_deg) * u.deg, float(dec_deg) * u.deg, frame="icrs")

    except Exception:

        return None



    simbad = Simbad()

    try:

        simbad.TIMEOUT = max(1, int(round(float(timeout_seconds))))

    except Exception:

        pass

    simbad.ROW_LIMIT = 1

    simbad.add_votable_fields("ids", "otype", "sp_type", "V")



    result = None

    search_radii_arcsec: list[float] = []

    for candidate_radius in (float(radius_arcsec), 5.0, 10.0):

        normalized_radius = max(0.5, candidate_radius)

        if any(abs(existing_radius - normalized_radius) < 0.01 for existing_radius in search_radii_arcsec):

            continue

        search_radii_arcsec.append(normalized_radius)

    for search_radius_arcsec in search_radii_arcsec:

        try:

            result = simbad.query_region(target_coord, radius=search_radius_arcsec * u.arcsec)

        except Exception:

            continue

        if result is not None and len(result) > 0:

            break

    if result is None or len(result) == 0:

        return None



    row = result[0]

    details = CatalogTargetDetails(

        main_id=_preferred_catalog_identifier(_row_text_value(row, "MAIN_ID"), _row_text_value(row, "IDS")),

        object_type=_row_text_value(row, "OTYPE", "OTYPES"),

        spectral_type=_row_text_value(row, "SP_TYPE", "SPTYPE"),

        visual_magnitude=_row_float_value(row, "FLUX_V", "V", "FLUX_G"),

        source="SIMBAD",

    )

    if (

        details.main_id is None

        and details.object_type is None

        and details.spectral_type is None

        and details.visual_magnitude is None

    ):

        return None

    return details





def fetch_catalog_targets_at_coordinate(

    ra_deg: float,

    dec_deg: float,

    *,

    radius_arcsec: float = 10.0,

    timeout_seconds: float = 10.0,

    row_limit: int = 25,

) -> tuple[CatalogTargetAtCoordinate, ...]:

    try:

        target_coord = SkyCoord(float(ra_deg) * u.deg, float(dec_deg) * u.deg, frame="icrs")

    except Exception:

        return ()



    simbad = Simbad()

    try:

        simbad.TIMEOUT = max(1, int(round(float(timeout_seconds))))

    except Exception:

        pass

    simbad.ROW_LIMIT = max(1, int(row_limit))

    simbad.add_votable_fields("ids", "otype", "sp_type", "V", "ra(d)", "dec(d)")



    search_radius_arcsec = max(0.5, float(radius_arcsec))

    try:

        result = simbad.query_region(target_coord, radius=search_radius_arcsec * u.arcsec)

    except Exception:

        return ()



    if result is None or len(result) == 0:

        return ()



    targets: list[CatalogTargetAtCoordinate] = []

    seen_main_ids: set[str] = set()

    for row in result:

        coordinates = _catalog_row_coordinates_deg(row)

        if coordinates is None:

            continue

        row_ra_deg, row_dec_deg = coordinates

        main_id = _preferred_catalog_identifier(_row_text_value(row, "MAIN_ID"), _row_text_value(row, "IDS"))

        if not main_id:

            continue

        normalized_main_id = main_id.casefold()

        if normalized_main_id in seen_main_ids:

            continue

        seen_main_ids.add(normalized_main_id)

        object_coord = SkyCoord(row_ra_deg * u.deg, row_dec_deg * u.deg, frame="icrs")

        separation_arcsec = float(target_coord.separation(object_coord).arcsec)

        targets.append(

            CatalogTargetAtCoordinate(

                main_id=main_id,

                object_type=_row_text_value(row, "OTYPE", "OTYPES"),

                ra_deg=float(row_ra_deg),

                dec_deg=float(row_dec_deg),

                separation_arcsec=separation_arcsec,

                spectral_type=_row_text_value(row, "SP_TYPE", "SPTYPE"),

                visual_magnitude=_row_float_value(row, "FLUX_V", "V", "FLUX_G"),

                source="SIMBAD",

            )

        )



    targets.sort(key=lambda item: item.separation_arcsec if item.separation_arcsec is not None else float("inf"))

    return tuple(targets)





def _fetch_vsx_literature_period_result(star: CatalogStar, timeout_seconds: float) -> LiteraturePeriodResult | None:

    cached_period = _as_float(star.metadata.get("literature_period_days"))

    if cached_period is None:

        cached_period = _as_float(star.metadata.get("period"))

    cached_duration = _as_float(star.metadata.get("literature_eclipse_duration_hours"))

    source_id = str(star.source_id).strip()

    if not source_id:

        if cached_period is None and cached_duration is None:

            return None

        return LiteraturePeriodResult(

            period_days=cached_period,

            eclipse_duration_hours=cached_duration,

            source="VSX cache",

        )



    try:

        response = requests.get(

            f"https://vsx.aavso.org/index.php?view=detail.top&oid={source_id}",

            timeout=timeout_seconds,

            headers={"User-Agent": "citizen-photometry/0.1"},

        )

        response.raise_for_status()

        html_text = response.text

    except requests.RequestException:

        if cached_period is None and cached_duration is None:

            return None

        return LiteraturePeriodResult(

            period_days=cached_period,

            eclipse_duration_hours=cached_duration,

            source="VSX cache",

        )



    period_value = _extract_vsx_detail_value(html_text, "Period")

    duration_value = _extract_vsx_detail_value(html_text, "Rise/eclipse dur.")

    period_days = _parse_period_days(period_value) or cached_period

    duration_hours = _parse_vsx_duration_hours(duration_value, period_days) or cached_duration

    if period_days is None and duration_hours is None:

        return None

    return LiteraturePeriodResult(

        period_days=period_days,

        eclipse_duration_hours=duration_hours,

        source="VSX",

    )





def _row_text_value(row: object, *column_names: str) -> str | None:

    for column_name in column_names:

        value = _row_value(row, column_name, None)

        if value is None:

            continue

        if getattr(value, "mask", False) is True:

            continue

        if isinstance(value, bytes):

            text = value.decode("utf-8", errors="ignore").strip()

        else:

            text = str(value).strip()

        if not text or text == "--":

            continue

        return text

    return None





def _row_float_value(row: object, *column_names: str) -> float | None:

    for column_name in column_names:

        value = _row_value(row, column_name, None)

        parsed = _as_float(value)

        if parsed is not None:

            return parsed

    return None





def _catalog_row_coordinates_deg(row: object) -> tuple[float, float] | None:

    ra_value = _row_float_value(row, "RA_d", "ra_d", "RA", "ra")

    dec_value = _row_float_value(row, "DEC_d", "dec_d", "DEC", "dec")

    if ra_value is not None and dec_value is not None:

        return float(ra_value), float(dec_value)



    ra_text = _row_text_value(row, "RA", "ra")

    dec_text = _row_text_value(row, "DEC", "dec")

    if ra_text is None or dec_text is None:

        return None



    for unit_pair in ((u.hourangle, u.deg), (u.deg, u.deg)):

        try:

            coordinates = SkyCoord(ra_text, dec_text, unit=unit_pair, frame="icrs")

        except Exception:

            continue

        return float(coordinates.ra.deg), float(coordinates.dec.deg)

    return None





def _preferred_catalog_identifier(main_id: str | None, ids_text: str | None) -> str | None:

    candidates: list[str] = []

    for candidate in (main_id, *_split_catalog_identifiers(ids_text)):

        cleaned_candidate = _clean_catalog_identifier(candidate)

        if cleaned_candidate is None:

            continue

        if cleaned_candidate not in candidates:

            candidates.append(cleaned_candidate)

    if not candidates:

        return None

    preferred_candidates = [candidate for candidate in candidates if not _catalog_identifier_is_generic(candidate)]

    if preferred_candidates:

        candidates = preferred_candidates

    return min(candidates, key=lambda candidate: (_catalog_identifier_priority(candidate), len(candidate), candidate.lower()))


def _preferred_gaia_display_name(row: object, *, source_id: str) -> str | None:

    explicit_name = _clean_catalog_identifier(_row_text_value(row, "Name", "name", "DR3Name", "dr3_name"))

    if explicit_name is not None and not _catalog_identifier_is_generic(explicit_name):

        return explicit_name

    candidate_identifiers: list[str] = []

    for column_name in (
        "HIP",
        "HD",
        "HR",
        "TYC2",
        "TYC",
        "BD",
        "CD",
        "CPD",
        "SAO",
        "RAVE5",
        "APASS9",
        "UCAC4",
        "2MASS",
        "AllWISE",
    ):
        candidate_value = _clean_catalog_identifier(_row_text_value(row, column_name, column_name.lower()))
        if candidate_value is None:
            continue
        if candidate_value not in candidate_identifiers:
            candidate_identifiers.append(candidate_value)

    if not candidate_identifiers:

        return None

    return _preferred_catalog_identifier(None, "|".join(candidate_identifiers))



def _split_catalog_identifiers(ids_text: str | None) -> list[str]:

    if ids_text is None:

        return []

    return [item.strip() for item in ids_text.split("|") if item.strip()]



def _clean_catalog_identifier(identifier: str | None) -> str | None:

    if identifier is None:

        return None

    cleaned_identifier = re.sub(r"\s+", " ", str(identifier).strip())

    if not cleaned_identifier or cleaned_identifier == "--":

        return None

    if cleaned_identifier.upper().startswith("NAME "):

        cleaned_identifier = cleaned_identifier[5:].strip()

    return cleaned_identifier or None



def _catalog_identifier_is_generic(identifier: str) -> bool:

    normalized_identifier = identifier.strip().lower()

    generic_prefixes = (

        "gaia dr",

        "gaia edr",

        "2mass ",

        "wise ",

        "allwise ",

        "apass ",

        "ucac",

        "tic ",

        "tycho-2 ",

        "gsc ",

        "ps1 ",

    )

    return normalized_identifier.startswith(generic_prefixes)



def _catalog_identifier_priority(identifier: str) -> int:

    normalized_identifier = identifier.strip().lower()

    preferred_prefixes = (

        "hd ",

        "hr ",

        "hip ",

        "tyc ",

        "bd",

        "cd",

        "cpd",

        "sao ",

        "v* ",

        "cl* ",

        "* ",

    )

    if normalized_identifier.startswith(preferred_prefixes):

        return 0

    if _catalog_identifier_is_generic(identifier):

        return 20

    return 5





def _extract_vsx_detail_value(html_text: str, label: str) -> str | None:

    pattern = re.compile(

        rf"<td[^>]*class=\"detailtitle\"[^>]*>\s*{re.escape(label)}\s*</td>\s*<td[^>]*class=\"detaildata\"[^>]*>(.*?)</td>",

        flags=re.IGNORECASE | re.DOTALL,

    )

    match = pattern.search(html_text)

    if match is None:

        return None

    cell = re.sub(r"<[^>]+>", " ", match.group(1))

    cell = unescape(cell).replace("\xa0", " ")

    cleaned = " ".join(cell.split())

    return cleaned or None





def _parse_period_days(value: str | None) -> float | None:

    return _parse_unitized_duration(value, default_unit="d", convert_to="d")





def _parse_vsx_duration_hours(value: str | None, period_days: float | None) -> float | None:

    if value is None:

        return None

    parenthetical_match = re.search(r"\(([^()]*)\)", value)

    if parenthetical_match is not None:

        parsed_parenthetical = _parse_unitized_duration(parenthetical_match.group(1), default_unit="d", convert_to="h")

        if parsed_parenthetical is not None:

            return parsed_parenthetical

    direct_duration = _parse_unitized_duration(value, default_unit="h", convert_to="h")

    if direct_duration is not None:

        return direct_duration

    percent_match = re.search(r"([-+]?\d+(?:\.\d+)?)\s*%", value)

    if percent_match is None or period_days is None or period_days <= 0:

        return None

    return (float(percent_match.group(1)) / 100.0) * period_days * 24.0





def _parse_unitized_duration(value: str | None, default_unit: str, convert_to: str) -> float | None:

    if value is None:

        return None

    match = re.search(r"([-+]?\d+(?:\.\d+)?)\s*([a-zA-Z]+)?", value)

    if match is None:

        return None

    magnitude = _as_float(match.group(1))

    if magnitude is None:

        return None

    unit = str(match.group(2) or default_unit).strip().lower()

    if unit.startswith("d"):

        value_hours = magnitude * 24.0

    elif unit.startswith("h"):

        value_hours = magnitude

    elif unit.startswith("m"):

        value_hours = magnitude / 60.0

    else:

        return None

    if convert_to == "h":

        return value_hours

    if convert_to == "d":

        return value_hours / 24.0

    return None





def _row_value(row: object, key: str, default: object = None) -> object:

    try:

        return row[key]

    except Exception:

        pass

    try:

        column_names = getattr(row, "colnames", None)

        if column_names is not None:

            normalized_target = str(key).strip().lower()

            for column_name in column_names:

                if str(column_name).strip().lower() != normalized_target:

                    continue

                return row[column_name]

    except Exception:

        pass

    return default





def _as_float(value: object) -> float | None:

    if value is None:

        return None

    try:

        return float(value)

    except (TypeError, ValueError):

        return None

