from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import warnings

import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.io.fits import Header
from astropy.wcs import WCS
from astropy.wcs.utils import fit_wcs_from_points, proj_plane_pixel_scales
from scipy.spatial import cKDTree

from photometry_app.core.catalogs import CatalogService
from photometry_app.core.image_io import read_header_and_shape, read_photometry_image_data
from photometry_app.core.models import CatalogStar, PlateSolveResult, SolvedField, WcsStatus
from photometry_app.core.wcs import extract_solved_field, validate_wcs


_PLATE_SCALE_ARCSEC_FACTOR = 206.26480624709636
_GAIA_MAXIMUM_MAGNITUDE = 15.5
_GAIA_ROW_LIMIT = None
_MINIMUM_FINAL_MATCHES = 10
_MAXIMUM_FINAL_RMS_PIXELS = 2.5
_MAXIMUM_FINAL_P90_PIXELS = 4.0


@dataclass(frozen=True, slots=True)
class MetadataWcsSeed:
    center_ra_deg: float
    center_dec_deg: float
    pixel_scale_x_arcsec: float
    pixel_scale_y_arcsec: float
    width: int
    height: int

    @property
    def mean_pixel_scale_arcsec(self) -> float:
        return (self.pixel_scale_x_arcsec + self.pixel_scale_y_arcsec) / 2.0

    @property
    def field_radius_deg(self) -> float:
        diagonal_pixels = math.hypot(self.width, self.height)
        image_radius = diagonal_pixels * max(self.pixel_scale_x_arcsec, self.pixel_scale_y_arcsec) / 7200.0
        return max(image_radius * 1.2, image_radius + 0.12, 0.2)

    @property
    def center(self) -> SkyCoord:
        return SkyCoord(self.center_ra_deg * u.deg, self.center_dec_deg * u.deg, frame="icrs")


@dataclass(frozen=True, slots=True)
class _DetectedSource:
    x: float
    y: float
    peak: float


@dataclass(frozen=True, slots=True)
class _SimilaritySolution:
    matrix: np.ndarray
    offset: np.ndarray
    catalog_indices: np.ndarray
    detected_indices: np.ndarray
    rms_pixels: float


def solve_wcs_from_metadata_and_gaia(
    source_path: Path,
    cache_dir: Path,
    *,
    progress_callback: Callable[[str], None] | None = None,
    catalog_service: CatalogService | None = None,
) -> PlateSolveResult:
    """Solve a metadata-seeded image by matching detected stars to Gaia DR3."""
    reasons: list[str] = []
    try:
        header, width, height = read_header_and_shape(source_path)
    except Exception as exc:
        return _unsolved_result(source_path, f"Could not read image metadata for the Gaia WCS fallback: {exc}")

    seed = infer_metadata_wcs_seed(header, width, height)
    if seed is None:
        return _unsolved_result(
            source_path,
            "Gaia WCS fallback needs center RA/Dec, focal length, pixel size, and image dimensions.",
        )

    cache_dir.mkdir(parents=True, exist_ok=True)
    solved_path = cache_dir / f"{_source_cache_key(source_path)}_local_gaia_solved.fits"
    cached_field = _load_cached_field(solved_path, seed)
    if cached_field is not None:
        if progress_callback is not None:
            progress_callback("Loaded the cached metadata-seeded Gaia WCS solution.")
        return PlateSolveResult(
            source_path=source_path,
            status=WcsStatus.SOLVED,
            solved_field=cached_field,
            reasons=[],
        )
    solved_path.unlink(missing_ok=True)

    if progress_callback is not None:
        progress_callback(
            "Embedded WCS was unusable; matching image stars to Gaia using the header pointing and optical scale."
        )

    provisional_field = SolvedField(
        center_ra_deg=seed.center_ra_deg,
        center_dec_deg=seed.center_dec_deg,
        radius_deg=seed.field_radius_deg,
        width=seed.width,
        height=seed.height,
        wcs_path=source_path,
    )
    try:
        service = catalog_service or CatalogService(cache_dir / "catalogs")
        gaia_stars = service.query_gaia_stars_limited(
            provisional_field,
            maximum_magnitude=_GAIA_MAXIMUM_MAGNITUDE,
            row_limit=_GAIA_ROW_LIMIT,
            progress_callback=progress_callback,
        )
    except Exception as exc:
        return _unsolved_result(source_path, f"Gaia WCS fallback catalog query failed: {exc}")

    gaia_stars = _usable_gaia_stars(gaia_stars)
    if len(gaia_stars) < _MINIMUM_FINAL_MATCHES:
        return _unsolved_result(
            source_path,
            f"Gaia WCS fallback found only {len(gaia_stars)} usable catalog stars.",
        )

    try:
        detected_sources = _detect_image_sources(source_path)
    except Exception as exc:
        return _unsolved_result(source_path, f"Gaia WCS fallback could not detect image stars: {exc}")
    if len(detected_sources) < _MINIMUM_FINAL_MATCHES:
        return _unsolved_result(
            source_path,
            f"Gaia WCS fallback detected only {len(detected_sources)} usable image stars.",
        )
    if progress_callback is not None:
        progress_callback(
            f"Gaia WCS fallback is fitting rotation and parity from "
            f"{len(detected_sources)} image stars and {len(gaia_stars)} catalog stars."
        )

    try:
        fitted = _fit_gaia_wcs(detected_sources, gaia_stars, seed)
    except Exception as exc:
        reasons.append(f"Gaia WCS fallback fit failed: {exc}")
        fitted = None
    if fitted is None:
        reason = reasons[0] if reasons else "Gaia WCS fallback could not find an unambiguous star-pattern match."
        return _unsolved_result(source_path, reason)
    fitted_wcs, match_count, rms_pixels = fitted

    fitted_header = fitted_wcs.to_header(relax=True)
    fitted_header["WCSMETH"] = ("GAIA-MATCH", "WCS recovered by metadata-seeded Gaia matching")
    fitted_header["WCSMATCH"] = (match_count, "Gaia stars used for local WCS fit")
    fitted_header["WCSRMS"] = (rms_pixels, "Gaia WCS fit RMS in pixels")
    fitted_header["SRCW"] = (seed.width, "Source image width in pixels")
    fitted_header["SRCH"] = (seed.height, "Source image height in pixels")
    fits.PrimaryHDU(header=fitted_header).writeto(solved_path, overwrite=True)

    valid, validation_reasons = validate_wcs(fitted_header)
    solved_field = extract_solved_field(fitted_header, seed.width, seed.height, solved_path) if valid else None
    if solved_field is None:
        solved_path.unlink(missing_ok=True)
        return PlateSolveResult(
            source_path=source_path,
            status=WcsStatus.UNSOLVED,
            solved_field=None,
            reasons=[*reasons, *validation_reasons],
        )

    if progress_callback is not None:
        progress_callback(
            f"Recovered a celestial WCS from {match_count} Gaia star matches "
            f"(RMS {rms_pixels:.2f} px)."
        )
    return PlateSolveResult(
        source_path=source_path,
        status=WcsStatus.SOLVED,
        solved_field=solved_field,
        reasons=[],
    )


def infer_metadata_wcs_seed(
    header: Header,
    width: int | None,
    height: int | None,
) -> MetadataWcsSeed | None:
    if width is None or height is None or width <= 0 or height <= 0:
        return None
    center = _header_center_coordinate(header)
    if center is None:
        return None

    direct_scale = _positive_header_float(header, "PIXSCALE", "SECPIX", "SCALE")
    if direct_scale is not None:
        scale_x = direct_scale
        scale_y = direct_scale
    else:
        focal_length_mm = _positive_header_float(header, "FOCALLEN", "FOCALLENGTH", "FOCAL")
        pixel_size_x_um = _positive_header_float(header, "XPIXSZ", "PIXSIZE1", "PIXELX")
        pixel_size_y_um = _positive_header_float(header, "YPIXSZ", "PIXSIZE2", "PIXELY")
        if focal_length_mm is None or (pixel_size_x_um is None and pixel_size_y_um is None):
            return None
        pixel_size_x_um = pixel_size_x_um or pixel_size_y_um
        pixel_size_y_um = pixel_size_y_um or pixel_size_x_um
        if pixel_size_x_um is None or pixel_size_y_um is None:
            return None
        pixel_size_x_um *= _binning_multiplier(header, "XPIXSZ", "XBINNING", "CCDXBIN")
        pixel_size_y_um *= _binning_multiplier(header, "YPIXSZ", "YBINNING", "CCDYBIN")
        scale_x = _PLATE_SCALE_ARCSEC_FACTOR * pixel_size_x_um / focal_length_mm
        scale_y = _PLATE_SCALE_ARCSEC_FACTOR * pixel_size_y_um / focal_length_mm

    if not all(np.isfinite(value) and 0.01 <= value <= 120.0 for value in (scale_x, scale_y)):
        return None
    return MetadataWcsSeed(
        center_ra_deg=float(center.ra.deg),
        center_dec_deg=float(center.dec.deg),
        pixel_scale_x_arcsec=float(scale_x),
        pixel_scale_y_arcsec=float(scale_y),
        width=int(width),
        height=int(height),
    )


def _header_center_coordinate(header: Header) -> SkyCoord | None:
    coordinate_pairs = (
        ("CRVAL1", "CRVAL2", "degrees"),
        ("RA", "DEC", "auto"),
        ("OBJCTRA", "OBJCTDEC", "hourangle"),
        ("OBJRA", "OBJDEC", "hourangle"),
        ("TELRA", "TELDEC", "hourangle"),
    )
    for ra_key, dec_key, mode in coordinate_pairs:
        if ra_key not in header or dec_key not in header:
            continue
        coordinate = _parse_header_coordinate(header.get(ra_key), header.get(dec_key), mode)
        if coordinate is not None:
            return coordinate
    return None


def _parse_header_coordinate(ra_value: object, dec_value: object, mode: str) -> SkyCoord | None:
    try:
        if mode == "degrees":
            coordinate = SkyCoord(float(ra_value) * u.deg, float(dec_value) * u.deg, frame="icrs")
        elif mode == "hourangle":
            coordinate = SkyCoord(str(ra_value).strip(), str(dec_value).strip(), unit=(u.hourangle, u.deg), frame="icrs")
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


def _positive_header_float(header: Header, *keys: str) -> float | None:
    for key in keys:
        try:
            value = float(header.get(key))
        except (TypeError, ValueError):
            continue
        if np.isfinite(value) and value > 0:
            return value
    return None


def _binning_multiplier(header: Header, pixel_key: str, *binning_keys: str) -> float:
    if pixel_key in header:
        try:
            comment = str(header.comments[pixel_key]).lower()
        except Exception:
            comment = ""
        if "including binning" in comment or "binned pixel" in comment:
            return 1.0
    software_name = str(header.get("SWCREATE") or header.get("CREATOR") or "").strip().casefold()
    if "n.i.n.a" in software_name or software_name.startswith("nina"):
        # N.I.N.A. writes XPIXSZ/YPIXSZ as the effective pixel pitch for the
        # saved (already binned) image. Multiplying by X/YBINNING again doubles
        # the inferred plate scale and prevents Gaia pattern matching.
        return 1.0
    binning = _positive_header_float(header, *binning_keys)
    return float(binning) if binning is not None else 1.0


def _detect_image_sources(source_path: Path) -> list[_DetectedSource]:
    from photometry_app.core.hr_diagram import _detect_hr_image_sources

    data = np.asarray(read_photometry_image_data(source_path), dtype=float)
    if data.ndim == 3:
        if data.shape[-1] in {3, 4}:
            data = np.mean(data[..., :3], axis=-1)
        elif data.shape[0] in {3, 4}:
            data = np.mean(data[:3], axis=0)
        else:
            raise ValueError("could not determine the image channel axis")
    if data.ndim != 2:
        raise ValueError("only two-dimensional or RGB images are supported")

    rows = _detect_hr_image_sources(data, aperture_radius=4.0, usable_margin=12)
    return [
        _DetectedSource(x=float(row.x), y=float(row.y), peak=float(row.peak))
        for row in rows[:500]
        if np.isfinite(row.x) and np.isfinite(row.y) and np.isfinite(row.peak)
    ]


def _usable_gaia_stars(stars: Sequence[CatalogStar]) -> list[CatalogStar]:
    usable = [
        star
        for star in stars
        if np.isfinite(star.ra_deg)
        and np.isfinite(star.dec_deg)
        and -90.0 <= float(star.dec_deg) <= 90.0
    ]
    usable.sort(
        key=lambda star: (
            float(star.magnitude) if star.magnitude is not None and np.isfinite(star.magnitude) else math.inf,
            star.source_id,
        )
    )
    return usable[:2000]


def _fit_gaia_wcs(
    detected_sources: Sequence[_DetectedSource],
    gaia_stars: Sequence[CatalogStar],
    seed: MetadataWcsSeed,
) -> tuple[WCS, int, float] | None:
    detected = np.asarray([(source.x, source.y) for source in detected_sources[:500]], dtype=float)
    gaia_coordinates = SkyCoord(
        np.asarray([star.ra_deg for star in gaia_stars[:2000]], dtype=float) * u.deg,
        np.asarray([star.dec_deg for star in gaia_stars[:2000]], dtype=float) * u.deg,
        frame="icrs",
    )
    east_offsets, north_offsets = seed.center.spherical_offsets_to(gaia_coordinates)
    catalog = np.column_stack(
        (
            east_offsets.to_value(u.arcsec) / seed.mean_pixel_scale_arcsec,
            north_offsets.to_value(u.arcsec) / seed.mean_pixel_scale_arcsec,
        )
    )

    finite_catalog = np.all(np.isfinite(catalog), axis=1)
    catalog = catalog[finite_catalog]
    gaia_coordinates = gaia_coordinates[finite_catalog]
    if len(catalog) < _MINIMUM_FINAL_MATCHES:
        return None

    similarity = _find_similarity_solution(detected, catalog, seed)
    if similarity is None or len(similarity.catalog_indices) < _MINIMUM_FINAL_MATCHES:
        return None

    wcs = _fit_wcs(
        detected[similarity.detected_indices],
        gaia_coordinates[similarity.catalog_indices],
        seed.center,
    )
    if wcs is None:
        return None

    final_catalog_indices = similarity.catalog_indices
    final_detected_indices = similarity.detected_indices
    for tolerance_pixels in (8.0, 5.0, 3.5):
        projected_x, projected_y = wcs.world_to_pixel(gaia_coordinates)
        projected = np.column_stack((projected_x, projected_y))
        catalog_indices, detected_indices, _ = _unique_nearest_matches(projected, detected, tolerance_pixels)
        if len(catalog_indices) < _MINIMUM_FINAL_MATCHES:
            break
        candidate_wcs = _fit_wcs(
            detected[detected_indices],
            gaia_coordinates[catalog_indices],
            seed.center,
        )
        if candidate_wcs is None:
            break
        wcs = candidate_wcs
        final_catalog_indices = catalog_indices
        final_detected_indices = detected_indices

    projected_x, projected_y = wcs.world_to_pixel(gaia_coordinates[final_catalog_indices])
    residuals = np.hypot(
        projected_x - detected[final_detected_indices, 0],
        projected_y - detected[final_detected_indices, 1],
    )
    finite = np.isfinite(residuals)
    if np.count_nonzero(finite) < _MINIMUM_FINAL_MATCHES:
        return None
    residuals = residuals[finite]
    final_catalog_indices = final_catalog_indices[finite]
    final_detected_indices = final_detected_indices[finite]

    median_residual = float(np.median(residuals))
    mad = float(np.median(np.abs(residuals - median_residual)))
    robust_limit = max(2.5, median_residual + 4.0 * max(1.4826 * mad, 0.25))
    inliers = residuals <= robust_limit
    if np.count_nonzero(inliers) >= _MINIMUM_FINAL_MATCHES and not np.all(inliers):
        final_catalog_indices = final_catalog_indices[inliers]
        final_detected_indices = final_detected_indices[inliers]
        refined_wcs = _fit_wcs(
            detected[final_detected_indices],
            gaia_coordinates[final_catalog_indices],
            seed.center,
        )
        if refined_wcs is not None:
            wcs = refined_wcs

    projected_x, projected_y = wcs.world_to_pixel(gaia_coordinates[final_catalog_indices])
    residuals = np.hypot(
        projected_x - detected[final_detected_indices, 0],
        projected_y - detected[final_detected_indices, 1],
    )
    match_count = len(residuals)
    rms_pixels = float(np.sqrt(np.mean(np.square(residuals)))) if match_count else math.inf
    p90_pixels = float(np.percentile(residuals, 90)) if match_count else math.inf
    if match_count < _MINIMUM_FINAL_MATCHES:
        return None
    if not np.isfinite(rms_pixels) or rms_pixels > _MAXIMUM_FINAL_RMS_PIXELS:
        return None
    if not np.isfinite(p90_pixels) or p90_pixels > _MAXIMUM_FINAL_P90_PIXELS:
        return None
    if not _matches_cover_image(detected[final_detected_indices], seed):
        return None
    if not _fitted_wcs_is_plausible(wcs, seed):
        return None
    return wcs, match_count, rms_pixels


def _find_similarity_solution(
    detected: np.ndarray,
    catalog: np.ndarray,
    seed: MetadataWcsSeed,
) -> _SimilaritySolution | None:
    central_radius = min(seed.width, seed.height) * 0.43
    central_catalog = catalog[np.hypot(catalog[:, 0], catalog[:, 1]) <= central_radius]
    triangle_best = _find_triangle_similarity_solution(detected, catalog, central_catalog)
    if triangle_best is not None and len(triangle_best.catalog_indices) >= _MINIMUM_FINAL_MATCHES:
        return triangle_best

    detected_anchors = detected[:55]
    catalog_anchors = (central_catalog if len(central_catalog) >= 15 else catalog)[:55]
    diagonal = math.hypot(seed.width, seed.height)
    image_lengths, image_angles = _pair_features(detected_anchors, 0.06 * diagonal, 0.9 * diagonal)
    catalog_lengths, catalog_angles = _pair_features(catalog_anchors, 0.06 * diagonal, 1.2 * diagonal)
    if len(image_lengths) == 0 or len(catalog_lengths) == 0:
        return None

    ratio = image_lengths[:, None] / catalog_lengths[None, :]
    valid_ratio = np.isfinite(ratio) & (ratio >= 0.65) & (ratio <= 1.55)
    if not np.any(valid_ratio):
        return None

    log_scale_edges = np.arange(math.log(0.65), math.log(1.55) + 0.025, 0.025)
    angle_edges = np.linspace(-math.pi, math.pi, 181)
    candidate_parameters: list[tuple[int, float, float, float]] = []
    for parity in (1, -1):
        theta = image_angles[:, None] - parity * catalog_angles[None, :]
        theta = _wrap_angle(theta)
        theta_reversed = _wrap_angle(theta + math.pi)
        theta_values = np.concatenate((theta[valid_ratio], theta_reversed[valid_ratio]))
        scale_values = np.concatenate((np.log(ratio[valid_ratio]), np.log(ratio[valid_ratio])))
        histogram, _, _ = np.histogram2d(theta_values, scale_values, bins=(angle_edges, log_scale_edges))
        if not np.any(histogram):
            continue
        flat_order = np.argsort(histogram.ravel())[::-1][:14]
        for flat_index in flat_order:
            angle_index, scale_index = np.unravel_index(flat_index, histogram.shape)
            votes = float(histogram[angle_index, scale_index])
            if votes < 3:
                continue
            theta_center = (angle_edges[angle_index] + angle_edges[angle_index + 1]) / 2.0
            scale_center = math.exp(
                (log_scale_edges[scale_index] + log_scale_edges[scale_index + 1]) / 2.0
            )
            candidate_parameters.append((parity, theta_center, scale_center, votes))
    candidate_parameters.sort(key=lambda item: item[3], reverse=True)

    best: _SimilaritySolution | None = None
    for parity, theta, scale, _votes in candidate_parameters[:24]:
        matrix = _similarity_matrix(theta, scale, parity)
        offset_catalog = central_catalog if len(central_catalog) >= 15 else catalog
        for offset in _candidate_offsets(detected[:120], offset_catalog[:120], matrix):
            candidate = _refine_similarity(detected, catalog, matrix, offset, parity)
            if candidate is None:
                continue
            if best is None or (
                len(candidate.catalog_indices),
                -candidate.rms_pixels,
            ) > (
                len(best.catalog_indices),
                -best.rms_pixels,
            ):
                best = candidate
    return best


def _find_triangle_similarity_solution(
    detected: np.ndarray,
    catalog: np.ndarray,
    central_catalog: np.ndarray,
) -> _SimilaritySolution | None:
    if len(detected) < 10 or len(central_catalog) < 10:
        return None
    detected_anchors = detected[:180]
    catalog_anchors = central_catalog[:180]
    detected_descriptors, detected_triangles = _triangle_features(detected_anchors)
    catalog_descriptors, catalog_triangles = _triangle_features(catalog_anchors)
    if len(detected_descriptors) == 0 or len(catalog_descriptors) == 0:
        return None

    descriptor_tree = cKDTree(catalog_descriptors)
    descriptor_distances, descriptor_indices = descriptor_tree.query(
        detected_descriptors,
        k=min(3, len(catalog_descriptors)),
        distance_upper_bound=0.012,
    )
    if descriptor_distances.ndim == 1:
        descriptor_distances = descriptor_distances[:, None]
        descriptor_indices = descriptor_indices[:, None]

    detected_tree = cKDTree(detected)
    ranked_candidates: list[tuple[int, float, int, np.ndarray, np.ndarray]] = []
    seen_transforms: set[tuple[int, int, int, int, int]] = set()
    for detected_triangle_index in range(len(detected_triangles)):
        image_triangle = detected_anchors[detected_triangles[detected_triangle_index]]
        for neighbor_index in range(descriptor_distances.shape[1]):
            descriptor_distance = float(descriptor_distances[detected_triangle_index, neighbor_index])
            catalog_triangle_index = int(descriptor_indices[detected_triangle_index, neighbor_index])
            if not np.isfinite(descriptor_distance) or catalog_triangle_index >= len(catalog_triangles):
                continue
            sky_triangle = catalog_anchors[catalog_triangles[catalog_triangle_index]]
            image_cross = _cross_2d(image_triangle[1] - image_triangle[0], image_triangle[2] - image_triangle[0])
            sky_cross = _cross_2d(sky_triangle[1] - sky_triangle[0], sky_triangle[2] - sky_triangle[0])
            if image_cross == 0.0 or sky_cross == 0.0:
                continue
            parity = 1 if image_cross * sky_cross > 0 else -1
            fitted = _fit_similarity(sky_triangle, image_triangle, parity)
            if fitted is None:
                continue
            matrix, offset = fitted
            scale = math.sqrt(abs(float(np.linalg.det(matrix))))
            if not np.isfinite(scale) or not 0.6 <= scale <= 1.6:
                continue
            theta = math.atan2(float(matrix[1, 0]), float(matrix[0, 0]))
            transform_key = (
                parity,
                int(round(math.degrees(theta) * 5.0)),
                int(round(scale * 200.0)),
                int(round(float(offset[0]) / 8.0)),
                int(round(float(offset[1]) / 8.0)),
            )
            if transform_key in seen_transforms:
                continue
            seen_transforms.add(transform_key)

            transformed = catalog_anchors @ matrix.T + offset
            match_count, match_rms = _fast_unique_match_score(transformed, detected_tree, tolerance_pixels=12.0)
            if match_count < 6:
                continue
            ranked_candidates.append((match_count, -match_rms, parity, matrix, offset))

    ranked_candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best: _SimilaritySolution | None = None
    for _score, _negative_rms, parity, matrix, offset in ranked_candidates[:24]:
        candidate = _refine_similarity(
            detected,
            catalog,
            matrix,
            offset,
            parity,
            tolerances=(20.0, 9.0, 5.0, 3.5),
        )
        if candidate is None:
            continue
        if best is None or (
            len(candidate.catalog_indices),
            -candidate.rms_pixels,
        ) > (
            len(best.catalog_indices),
            -best.rms_pixels,
        ):
            best = candidate
    return best


def _triangle_features(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if len(points) < 3:
        return np.empty((0, 2), dtype=float), np.empty((0, 3), dtype=int)
    tree = cKDTree(points)
    neighbor_count = min(7, len(points))
    _, neighbor_indices = tree.query(points, k=neighbor_count)
    if neighbor_indices.ndim == 1:
        neighbor_indices = neighbor_indices[:, None]

    descriptors: list[tuple[float, float]] = []
    triangles: list[tuple[int, int, int]] = []
    seen_triangles: set[tuple[int, int, int]] = set()
    for center_index, neighbors in enumerate(neighbor_indices):
        usable_neighbors = [int(index) for index in neighbors if int(index) != center_index]
        for first_offset in range(len(usable_neighbors)):
            for second_offset in range(first_offset + 1, len(usable_neighbors)):
                vertex_indices = (
                    center_index,
                    usable_neighbors[first_offset],
                    usable_neighbors[second_offset],
                )
                identity = tuple(sorted(vertex_indices))
                if identity in seen_triangles:
                    continue
                canonical = _canonical_triangle(points, vertex_indices)
                if canonical is None:
                    continue
                descriptor, ordered_vertices = canonical
                seen_triangles.add(identity)
                descriptors.append(descriptor)
                triangles.append(ordered_vertices)
    return np.asarray(descriptors, dtype=float), np.asarray(triangles, dtype=int)


def _canonical_triangle(
    points: np.ndarray,
    vertex_indices: tuple[int, int, int],
) -> tuple[tuple[float, float], tuple[int, int, int]] | None:
    first, second, third = vertex_indices
    opposite_lengths = np.asarray(
        (
            np.linalg.norm(points[second] - points[third]),
            np.linalg.norm(points[first] - points[third]),
            np.linalg.norm(points[first] - points[second]),
        ),
        dtype=float,
    )
    if not np.all(np.isfinite(opposite_lengths)):
        return None
    order = np.argsort(opposite_lengths)
    sorted_lengths = opposite_lengths[order]
    if sorted_lengths[0] < 8.0 or sorted_lengths[2] <= 0.0:
        return None
    shortest_ratio = float(sorted_lengths[0] / sorted_lengths[2])
    middle_ratio = float(sorted_lengths[1] / sorted_lengths[2])
    if shortest_ratio < 0.18 or middle_ratio - shortest_ratio < 0.025 or 1.0 - middle_ratio < 0.025:
        return None
    ordered_vertices = tuple(vertex_indices[int(index)] for index in order)
    return (shortest_ratio, middle_ratio), ordered_vertices


def _fast_unique_match_score(
    transformed_catalog: np.ndarray,
    detected_tree: cKDTree,
    *,
    tolerance_pixels: float,
) -> tuple[int, float]:
    distances, indices = detected_tree.query(
        transformed_catalog,
        k=1,
        distance_upper_bound=tolerance_pixels,
    )
    finite_indices = np.flatnonzero(np.isfinite(distances))
    if len(finite_indices) == 0:
        return 0, math.inf
    order = finite_indices[np.argsort(distances[finite_indices])]
    used_detected: set[int] = set()
    kept_distances: list[float] = []
    for catalog_index in order:
        detected_index = int(indices[catalog_index])
        if detected_index in used_detected:
            continue
        used_detected.add(detected_index)
        kept_distances.append(float(distances[catalog_index]))
    if not kept_distances:
        return 0, math.inf
    return len(kept_distances), float(np.sqrt(np.mean(np.square(kept_distances))))


def _cross_2d(first: np.ndarray, second: np.ndarray) -> float:
    return float(first[0] * second[1] - first[1] * second[0])


def _pair_features(points: np.ndarray, minimum_length: float, maximum_length: float) -> tuple[np.ndarray, np.ndarray]:
    first_indices, second_indices = np.triu_indices(len(points), k=1)
    vectors = points[second_indices] - points[first_indices]
    lengths = np.hypot(vectors[:, 0], vectors[:, 1])
    valid = np.isfinite(lengths) & (lengths >= minimum_length) & (lengths <= maximum_length)
    return lengths[valid], np.arctan2(vectors[valid, 1], vectors[valid, 0])


def _candidate_offsets(detected: np.ndarray, catalog: np.ndarray, matrix: np.ndarray) -> list[np.ndarray]:
    transformed = catalog @ matrix.T
    differences = detected[:, None, :] - transformed[None, :, :]
    flattened = differences.reshape(-1, 2)
    finite = np.all(np.isfinite(flattened), axis=1)
    flattened = flattened[finite]
    if len(flattened) == 0:
        return []
    bin_size = 48.0
    bins = np.floor(flattened / bin_size).astype(np.int64)
    unique_bins, inverse, counts = np.unique(bins, axis=0, return_inverse=True, return_counts=True)
    order = np.argsort(counts)[::-1][:4]
    offsets: list[np.ndarray] = []
    for bin_index in order:
        members = flattened[inverse == bin_index]
        if len(members) == 0:
            continue
        offsets.append(np.median(members, axis=0))
    return offsets


def _refine_similarity(
    detected: np.ndarray,
    catalog: np.ndarray,
    matrix: np.ndarray,
    offset: np.ndarray,
    parity: int,
    *,
    tolerances: tuple[float, ...] = (85.0, 30.0, 12.0, 6.0, 4.0),
) -> _SimilaritySolution | None:
    current_matrix = np.asarray(matrix, dtype=float)
    current_offset = np.asarray(offset, dtype=float)
    catalog_indices = np.asarray([], dtype=int)
    detected_indices = np.asarray([], dtype=int)
    residuals = np.asarray([], dtype=float)
    for tolerance in tolerances:
        transformed = catalog @ current_matrix.T + current_offset
        catalog_indices, detected_indices, residuals = _unique_nearest_matches(
            transformed,
            detected,
            tolerance,
        )
        if len(catalog_indices) < 6:
            return None
        fitted = _fit_similarity(
            catalog[catalog_indices],
            detected[detected_indices],
            parity,
        )
        if fitted is None:
            return None
        current_matrix, current_offset = fitted
        scale = math.sqrt(abs(float(np.linalg.det(current_matrix))))
        if not np.isfinite(scale) or not 0.6 <= scale <= 1.6:
            return None

    if len(catalog_indices) == 0:
        return None
    rms = float(np.sqrt(np.mean(np.square(residuals))))
    return _SimilaritySolution(
        matrix=current_matrix,
        offset=current_offset,
        catalog_indices=catalog_indices,
        detected_indices=detected_indices,
        rms_pixels=rms,
    )


def _fit_similarity(source: np.ndarray, target: np.ndarray, parity: int) -> tuple[np.ndarray, np.ndarray] | None:
    if len(source) < 2 or len(target) != len(source):
        return None
    reflected_source = np.asarray(source, dtype=float).copy()
    if parity < 0:
        reflected_source[:, 1] *= -1.0

    rows = np.zeros((len(source) * 2, 4), dtype=float)
    values = np.zeros(len(source) * 2, dtype=float)
    rows[0::2, 0] = reflected_source[:, 0]
    rows[0::2, 1] = -reflected_source[:, 1]
    rows[0::2, 2] = 1.0
    rows[1::2, 0] = reflected_source[:, 1]
    rows[1::2, 1] = reflected_source[:, 0]
    rows[1::2, 3] = 1.0
    values[0::2] = target[:, 0]
    values[1::2] = target[:, 1]
    try:
        coefficients, _, rank, _ = np.linalg.lstsq(rows, values, rcond=None)
    except np.linalg.LinAlgError:
        return None
    if rank < 4:
        return None
    a, b, tx, ty = coefficients
    rotation_scale = np.asarray([[a, -b], [b, a]], dtype=float)
    reflection = np.diag([1.0, -1.0]) if parity < 0 else np.eye(2)
    return rotation_scale @ reflection, np.asarray([tx, ty], dtype=float)


def _similarity_matrix(theta: float, scale: float, parity: int) -> np.ndarray:
    cosine = math.cos(theta)
    sine = math.sin(theta)
    rotation = np.asarray([[cosine, -sine], [sine, cosine]], dtype=float)
    reflection = np.diag([1.0, -1.0]) if parity < 0 else np.eye(2)
    return scale * rotation @ reflection


def _unique_nearest_matches(
    catalog_points: np.ndarray,
    detected_points: np.ndarray,
    tolerance_pixels: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if len(catalog_points) == 0 or len(detected_points) == 0:
        empty_indices = np.asarray([], dtype=int)
        return empty_indices, empty_indices, np.asarray([], dtype=float)
    differences = catalog_points[:, None, :] - detected_points[None, :, :]
    squared_distances = np.sum(np.square(differences), axis=2)
    nearest_detected = np.argmin(squared_distances, axis=1)
    nearest_squared = squared_distances[np.arange(len(catalog_points)), nearest_detected]
    candidate_catalog = np.flatnonzero(nearest_squared <= tolerance_pixels * tolerance_pixels)
    if len(candidate_catalog) == 0:
        empty_indices = np.asarray([], dtype=int)
        return empty_indices, empty_indices, np.asarray([], dtype=float)

    order = candidate_catalog[np.argsort(nearest_squared[candidate_catalog])]
    used_detected: set[int] = set()
    kept_catalog: list[int] = []
    kept_detected: list[int] = []
    kept_distances: list[float] = []
    for catalog_index in order:
        detected_index = int(nearest_detected[catalog_index])
        if detected_index in used_detected:
            continue
        used_detected.add(detected_index)
        kept_catalog.append(int(catalog_index))
        kept_detected.append(detected_index)
        kept_distances.append(math.sqrt(float(nearest_squared[catalog_index])))
    return (
        np.asarray(kept_catalog, dtype=int),
        np.asarray(kept_detected, dtype=int),
        np.asarray(kept_distances, dtype=float),
    )


def _fit_wcs(pixel_points: np.ndarray, world_coordinates: SkyCoord, center: SkyCoord) -> WCS | None:
    if len(pixel_points) < _MINIMUM_FINAL_MATCHES:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return fit_wcs_from_points(
                (pixel_points[:, 0], pixel_points[:, 1]),
                world_coordinates,
                proj_point=center,
                projection="TAN",
            )
    except Exception:
        return None


def _matches_cover_image(points: np.ndarray, seed: MetadataWcsSeed) -> bool:
    if len(points) < _MINIMUM_FINAL_MATCHES:
        return False
    x_span = float(np.ptp(points[:, 0]))
    y_span = float(np.ptp(points[:, 1]))
    return x_span >= max(20.0, seed.width * 0.12) and y_span >= max(20.0, seed.height * 0.12)


def _fitted_wcs_is_plausible(wcs: WCS, seed: MetadataWcsSeed) -> bool:
    try:
        scales_arcsec = proj_plane_pixel_scales(wcs) * 3600.0
        mean_scale = float(np.mean(scales_arcsec))
        fitted_center = wcs.pixel_to_world(seed.width / 2.0, seed.height / 2.0)
        center_separation_deg = float(seed.center.separation(fitted_center).deg)
    except Exception:
        return False
    if not np.isfinite(mean_scale) or not 0.6 <= mean_scale / seed.mean_pixel_scale_arcsec <= 1.6:
        return False
    return np.isfinite(center_separation_deg) and center_separation_deg <= seed.field_radius_deg


def _wrap_angle(value: np.ndarray) -> np.ndarray:
    return (value + math.pi) % (2.0 * math.pi) - math.pi


def _source_cache_key(source_path: Path) -> str:
    try:
        stat = source_path.stat()
        payload = f"{source_path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
    except OSError:
        payload = str(source_path.resolve())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_cached_field(solved_path: Path, seed: MetadataWcsSeed) -> SolvedField | None:
    if not solved_path.exists():
        return None
    try:
        with fits.open(solved_path) as hdul:
            header = hdul[0].header.copy()
        valid, _ = validate_wcs(header)
        if not valid:
            return None
        return extract_solved_field(header, seed.width, seed.height, solved_path)
    except Exception:
        return None


def _unsolved_result(source_path: Path, reason: str) -> PlateSolveResult:
    return PlateSolveResult(
        source_path=source_path,
        status=WcsStatus.UNSOLVED,
        solved_field=None,
        reasons=[reason],
    )
