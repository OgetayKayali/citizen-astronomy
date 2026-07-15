from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import math

from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.stats import sigma_clipped_stats
from astropy.wcs import WCS
import numpy as np
from photutils.detection import DAOStarFinder

from photometry_app.core.catalogs import CatalogService, summarize_catalog_service_error
from photometry_app.core.image_io import read_header, read_header_and_shape, read_photometry_image_data
from photometry_app.core.models import CatalogStar, FileScanResult, ObservationMetadata, PlateSolveResult, SolvedField, WcsStatus
from photometry_app.core.scanner import scan_fits_tree
from photometry_app.core.settings import AppSettings
from photometry_app.core.wcs import AstrometryNetClient, extract_solved_field, infer_astrometry_solve_hints, validate_wcs


_TRANSIENT_ASTROMETRY_CACHE_NAME = "transient-wcs"
_DEFAULT_DETECTION_SIGMA = 5.0
_DEFAULT_DETECTION_FWHM = 3.0
_DEFAULT_GROUPING_RADIUS_ARCSEC = 2.5
_DEFAULT_CATALOG_MATCH_RADIUS_ARCSEC = 2.5
_DEFAULT_EDGE_MARGIN_PX = 6
_DEFAULT_EDGE_MARGIN_FRACTION = 0.0
_DEFAULT_GAIA_VETO_MAGNITUDE_LIMIT = 18.0
_DEFAULT_MAX_FRAME_DETECTIONS = 25_000
_DEFAULT_MAX_CANDIDATE_COUNT = 500
_DEFAULT_VARIABILITY_SIGMA = 7.0
_DEFAULT_MIN_FLUX_RATIO = 2.0


@dataclass(slots=True)
class TransientFrameResult:
    source_path: Path
    metadata: ObservationMetadata
    status: WcsStatus
    solved_field: SolvedField | None
    wcs_path: Path | None
    solved_via_astrometry: bool = False
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TransientSourceDetection:
    source_path: Path
    observation_time: datetime | None
    x: float
    y: float
    ra_deg: float
    dec_deg: float
    snr: float
    flux: float
    peak_value: float
    nearest_catalog_name: str | None = None
    nearest_catalog_separation_arcsec: float | None = None


@dataclass(slots=True)
class TransientCandidate:
    candidate_id: str
    ra_deg: float
    dec_deg: float
    frame_count: int
    detection_count: int
    first_observation: datetime | None
    last_observation: datetime | None
    median_snr: float
    max_snr: float
    nearest_catalog_name: str | None
    nearest_catalog_separation_arcsec: float | None
    detections: tuple[TransientSourceDetection, ...]
    summary_text: str
    variability_snr: float = 0.0
    flux_ratio: float = 0.0
    blink_paths: tuple[Path, ...] = ()


@dataclass(slots=True)
class TransientSearchResult:
    root_path: Path
    frame_results: tuple[TransientFrameResult, ...]
    total_files: int
    solved_frame_count: int
    astrometry_solved_count: int
    detected_source_count: int
    catalog_star_count: int
    candidates: tuple[TransientCandidate, ...]
    notes: tuple[str, ...]
    report_text: str


@dataclass(slots=True)
class _DetectionGroup:
    detections: list[TransientSourceDetection]

    def add(self, detection: TransientSourceDetection) -> None:
        for index, existing in enumerate(self.detections):
            if existing.source_path == detection.source_path:
                if detection.snr > existing.snr:
                    self.detections[index] = detection
                return
        self.detections.append(detection)

    def center(self) -> tuple[float, float]:
        return _mean_sky_position(self.detections)


@dataclass(slots=True)
class _FrameMeasurementContext:
    frame_result: TransientFrameResult
    data: np.ndarray
    wcs: WCS
    global_stddev: float


@dataclass(slots=True)
class _CandidateFrameSignal:
    frame_result: TransientFrameResult
    x: float
    y: float
    flux: float
    flux_uncertainty: float
    snr: float
    peak_snr: float


def search_transients_in_folder(
    root_path: Path,
    settings: AppSettings,
    *,
    min_frame_count: int = 2,
    detection_sigma: float = _DEFAULT_DETECTION_SIGMA,
    detection_fwhm: float = _DEFAULT_DETECTION_FWHM,
    grouping_radius_arcsec: float = _DEFAULT_GROUPING_RADIUS_ARCSEC,
    catalog_match_radius_arcsec: float = _DEFAULT_CATALOG_MATCH_RADIUS_ARCSEC,
    edge_margin_px: int = _DEFAULT_EDGE_MARGIN_PX,
    edge_margin_fraction: float = _DEFAULT_EDGE_MARGIN_FRACTION,
    gaia_veto_magnitude_limit: float = _DEFAULT_GAIA_VETO_MAGNITUDE_LIMIT,
    max_frame_detections: int = _DEFAULT_MAX_FRAME_DETECTIONS,
    max_candidate_count: int = _DEFAULT_MAX_CANDIDATE_COUNT,
    catalog_service: CatalogService | None = None,
    astrometry_client_factory: Callable[[str], AstrometryNetClient] | None = None,
    progress_callback: Callable[[str], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> TransientSearchResult:
    root_path = Path(root_path).expanduser()
    notes: list[str] = []
    min_frame_count = max(1, int(min_frame_count))
    detection_sigma = max(0.5, float(detection_sigma))
    detection_fwhm = max(0.8, float(detection_fwhm))
    grouping_radius_arcsec = max(0.1, float(grouping_radius_arcsec))
    catalog_match_radius_arcsec = max(0.1, float(catalog_match_radius_arcsec))
    edge_margin_px = max(0, int(edge_margin_px))
    edge_margin_fraction = max(0.0, min(0.45, float(edge_margin_fraction)))
    gaia_veto_magnitude_limit = max(-5.0, min(30.0, float(gaia_veto_magnitude_limit)))
    max_frame_detections = max(100, int(max_frame_detections))
    max_candidate_count = max(1, int(max_candidate_count))

    _emit(progress_callback, f"Scanning {root_path} for supported image files.")
    scan_report = scan_fits_tree(root_path, observation_timezone=settings.observation_timezone)
    files = [file_result for summary in scan_report.object_summaries for file_result in summary.files]
    files.sort(key=_file_sort_key)
    if not files:
        notes.append("No supported FITS/XISF images were found in the selected folder.")
        return _build_result(root_path, (), 0, 0, 0, 0, 0, (), notes)

    frame_results: list[TransientFrameResult] = []
    for index, file_result in enumerate(files, start=1):
        _raise_if_cancelled(cancel_callback)
        _emit(progress_callback, f"[WCS {index}/{len(files)}] Checking {file_result.path.name}.")
        frame_result = _resolve_frame_wcs(
            file_result,
            settings,
            astrometry_client_factory=astrometry_client_factory,
            progress_callback=progress_callback,
        )
        frame_results.append(frame_result)
        if frame_result.solved_field is None:
            reason = frame_result.reasons[0] if frame_result.reasons else "No usable WCS was available."
            notes.append(f"Skipped {file_result.path.name}: {reason}")

    solved_frames = [frame_result for frame_result in frame_results if frame_result.solved_field is not None]
    astrometry_solved_count = sum(frame.solved_via_astrometry for frame in solved_frames)
    if len(solved_frames) < min_frame_count:
        notes.append(
            f"Transient search needs at least {min_frame_count} solved frame(s); only {len(solved_frames)} were ready."
        )
        return _build_result(root_path, tuple(frame_results), len(files), len(solved_frames), astrometry_solved_count, 0, 0, (), notes)

    resolved_catalog_service = catalog_service or CatalogService(settings.cache_dir / "catalogs")
    sequence_field = _combined_solved_field(solved_frames)
    gaia_stars = _query_gaia_veto_stars(
        resolved_catalog_service,
        sequence_field,
        gaia_veto_magnitude_limit,
        notes,
        progress_callback,
    )
    catalog_stars_by_key: dict[tuple[str, str], CatalogStar] = {
        (star.catalog, star.source_id): star
        for star in gaia_stars
    }
    frame_contexts = _load_frame_measurement_contexts(solved_frames, notes)
    detections: list[TransientSourceDetection] = []

    for index, frame_result in enumerate(solved_frames, start=1):
        _raise_if_cancelled(cancel_callback)
        assert frame_result.solved_field is not None
        _emit(progress_callback, f"[Transient {index}/{len(solved_frames)}] Detecting point sources in {frame_result.source_path.name}.")
        frame_detections = _detect_uncataloged_sources(
            frame_result,
            gaia_stars,
            detection_sigma=detection_sigma,
            detection_fwhm=detection_fwhm,
            catalog_match_radius_arcsec=catalog_match_radius_arcsec,
            edge_margin_px=edge_margin_px,
            edge_margin_fraction=edge_margin_fraction,
            max_frame_detections=max_frame_detections,
        )
        detections.extend(frame_detections)
        _emit(
            progress_callback,
            f"[Transient {index}/{len(solved_frames)}] Retained {len(frame_detections)} point-source detection(s) for variability screening in {frame_result.source_path.name}.",
        )

    candidates, screened_group_count, evaluated_group_count = _build_candidates(
        detections,
        min_frame_count=min_frame_count,
        grouping_radius_arcsec=grouping_radius_arcsec,
        frame_contexts=frame_contexts,
        detection_sigma=detection_sigma,
        detection_fwhm=detection_fwhm,
    )
    catalog_star_count = len(catalog_stars_by_key)
    if screened_group_count:
        notes.append(
            f"Rejected {screened_group_count} of {evaluated_group_count} source group(s) as static or seeing-driven."
        )
    if not candidates:
        notes.append("No variable point-source candidates were found across the solved frame sequence.")
    elif len(candidates) > max_candidate_count:
        notes.append(
            f"Candidate list capped at the strongest {max_candidate_count} object(s); "
            f"{len(candidates)} repeated uncataloged source group(s) were found before capping."
        )
        candidates = candidates[:max_candidate_count]

    return _build_result(
        root_path,
        tuple(frame_results),
        len(files),
        len(solved_frames),
        astrometry_solved_count,
        len(detections),
        catalog_star_count,
        tuple(candidates),
        notes,
    )


def _resolve_frame_wcs(
    file_result: FileScanResult,
    settings: AppSettings,
    *,
    astrometry_client_factory: Callable[[str], AstrometryNetClient] | None,
    progress_callback: Callable[[str], None] | None,
) -> TransientFrameResult:
    if file_result.wcs_status == WcsStatus.INVALID:
        return TransientFrameResult(
            source_path=file_result.path,
            metadata=file_result.metadata,
            status=WcsStatus.INVALID,
            solved_field=None,
            wcs_path=None,
            reasons=list(file_result.reasons),
        )

    try:
        header, width, height = read_header_and_shape(file_result.path)
    except Exception as exc:
        return TransientFrameResult(
            source_path=file_result.path,
            metadata=file_result.metadata,
            status=WcsStatus.INVALID,
            solved_field=None,
            wcs_path=None,
            reasons=[f"Failed to read image header: {exc}"],
        )

    valid, reasons = validate_wcs(header, file_result.path)
    if valid:
        solved_field = extract_solved_field(header, width, height, file_result.path)
        if solved_field is not None:
            _emit(progress_callback, f"Embedded WCS is usable for {file_result.path.name}.")
            return TransientFrameResult(
                source_path=file_result.path,
                metadata=file_result.metadata,
                status=WcsStatus.SOLVED,
                solved_field=solved_field,
                wcs_path=file_result.path,
                reasons=reasons,
            )

    if not settings.astrometry_api_key and astrometry_client_factory is None:
        return TransientFrameResult(
            source_path=file_result.path,
            metadata=file_result.metadata,
            status=WcsStatus.UNSOLVED,
            solved_field=None,
            wcs_path=None,
            reasons=[*reasons, "Astrometry.net API key not configured; unsolved file skipped."],
        )

    api_key = settings.astrometry_api_key or ""
    client = astrometry_client_factory(api_key) if astrometry_client_factory is not None else AstrometryNetClient(api_key)
    hints = infer_astrometry_solve_hints(header, width, height, file_result.path)
    try:
        plate_result: PlateSolveResult = client.solve_file(
            file_result.path,
            settings.cache_dir / _TRANSIENT_ASTROMETRY_CACHE_NAME,
            hints=hints,
        )
    except Exception as exc:
        return TransientFrameResult(
            source_path=file_result.path,
            metadata=file_result.metadata,
            status=WcsStatus.UNSOLVED,
            solved_field=None,
            wcs_path=None,
            reasons=[*reasons, f"Astrometry.net solve failed: {exc}"],
        )

    solved_field = plate_result.solved_field
    if solved_field is None:
        return TransientFrameResult(
            source_path=file_result.path,
            metadata=file_result.metadata,
            status=plate_result.status,
            solved_field=None,
            wcs_path=None,
            reasons=[*reasons, *plate_result.reasons],
        )

    _emit(progress_callback, f"Solved {file_result.path.name} with astrometry.net; solved FITS saved to {solved_field.wcs_path}.")
    return TransientFrameResult(
        source_path=file_result.path,
        metadata=file_result.metadata,
        status=WcsStatus.SOLVED,
        solved_field=solved_field,
        wcs_path=solved_field.wcs_path,
        solved_via_astrometry=True,
        reasons=[*reasons, *plate_result.reasons],
    )


def _query_gaia_veto_stars(
    catalog_service: CatalogService,
    solved_field: SolvedField,
    magnitude_limit: float,
    notes: list[str],
    progress_callback: Callable[[str], None] | None,
) -> list[CatalogStar]:
    try:
        limited_query = getattr(catalog_service, "query_gaia_stars_limited", None)
        if callable(limited_query):
            return limited_query(solved_field, magnitude_limit, progress_callback=progress_callback)
        return catalog_service.query_gaia_stars(solved_field, progress_callback=progress_callback)
    except Exception as exc:
        message = f"Gaia lookup unavailable for this field: {summarize_catalog_service_error(exc)}"
        notes.append(message)
        _emit(progress_callback, message)
        return []


def _combined_solved_field(frame_results: list[TransientFrameResult]) -> SolvedField:
    solved_fields = [frame.solved_field for frame in frame_results if frame.solved_field is not None]
    if not solved_fields:
        raise ValueError("No solved frame is available for the transient sequence.")
    center_ra_deg, center_dec_deg = _mean_solved_field_center(solved_fields)
    center_coord = SkyCoord(center_ra_deg * u.deg, center_dec_deg * u.deg)
    radius_deg = max(
        float(center_coord.separation(SkyCoord(field.center_ra_deg * u.deg, field.center_dec_deg * u.deg)).deg) + float(field.radius_deg)
        for field in solved_fields
    )
    reference_field = solved_fields[0]
    return SolvedField(
        center_ra_deg=center_ra_deg,
        center_dec_deg=center_dec_deg,
        radius_deg=radius_deg,
        width=reference_field.width,
        height=reference_field.height,
        wcs_path=reference_field.wcs_path,
    )


def _mean_solved_field_center(solved_fields: list[SolvedField]) -> tuple[float, float]:
    pseudo_detections = [
        TransientSourceDetection(
            source_path=field.wcs_path,
            observation_time=None,
            x=0.0,
            y=0.0,
            ra_deg=field.center_ra_deg,
            dec_deg=field.center_dec_deg,
            snr=0.0,
            flux=0.0,
            peak_value=0.0,
        )
        for field in solved_fields
    ]
    return _mean_sky_position(pseudo_detections)


def _detect_uncataloged_sources(
    frame_result: TransientFrameResult,
    gaia_stars: list[CatalogStar],
    *,
    detection_sigma: float,
    detection_fwhm: float,
    catalog_match_radius_arcsec: float,
    edge_margin_px: int,
    edge_margin_fraction: float,
    max_frame_detections: int = _DEFAULT_MAX_FRAME_DETECTIONS,
) -> list[TransientSourceDetection]:
    if frame_result.wcs_path is None:
        return []
    data = _as_mono_image(read_photometry_image_data(frame_result.source_path, dtype=float))
    if data.size == 0:
        return []
    finite_mask = np.isfinite(data)
    if not np.any(finite_mask):
        return []
    mean, median_value, stddev = sigma_clipped_stats(data, mask=~finite_mask)
    del mean
    if not math.isfinite(float(stddev)) or float(stddev) <= 0.0:
        return []
    finder = DAOStarFinder(
        fwhm=detection_fwhm,
        threshold=detection_sigma * float(stddev),
        brightest=max_frame_detections,
    )
    sources = finder(data - float(median_value))
    if sources is None or len(sources) == 0:
        return []

    wcs = WCS(read_header(frame_result.wcs_path))
    height, width = data.shape
    edge_margin = max(int(edge_margin_px), int(round(min(width, height) * max(0.0, min(0.45, float(edge_margin_fraction))))))
    column_names = set(sources.colnames)
    catalog_coords = _catalog_coordinates(gaia_stars)
    source_rows = list(sources)
    source_x_values: list[float] = []
    source_y_values: list[float] = []
    valid_rows: list[object] = []
    for row in source_rows:
        try:
            x = float(row["xcentroid"])
            y = float(row["ycentroid"])
        except Exception:
            continue
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        if x < edge_margin or y < edge_margin or x > width - edge_margin - 1 or y > height - edge_margin - 1:
            continue
        source_x_values.append(x)
        source_y_values.append(y)
        valid_rows.append(row)
    if not valid_rows:
        return []
    try:
        sky_positions = wcs.pixel_to_world(np.asarray(source_x_values, dtype=float), np.asarray(source_y_values, dtype=float))
    except Exception:
        return []
    nearest_indices: np.ndarray | None = None
    nearest_separations_arcsec: np.ndarray | None = None
    if catalog_coords is not None:
        nearest_indices_raw, nearest_separation_raw, _distance = sky_positions.match_to_catalog_sky(catalog_coords)
        nearest_indices = np.asarray(nearest_indices_raw, dtype=int).reshape(-1)
        nearest_separations_arcsec = np.asarray(nearest_separation_raw.to_value(u.arcsec), dtype=float).reshape(-1)
    detections: list[TransientSourceDetection] = []
    for source_index, row in enumerate(valid_rows):
        x = source_x_values[source_index]
        y = source_y_values[source_index]
        ra_deg = float(np.asarray(sky_positions.ra.deg).reshape(-1)[source_index])
        dec_deg = float(np.asarray(sky_positions.dec.deg).reshape(-1)[source_index])
        if not (math.isfinite(ra_deg) and math.isfinite(dec_deg)):
            continue
        flux = _finite_table_value(row, "flux", column_names, default=0.0)
        peak_value = _finite_table_value(row, "peak", column_names, default=0.0)
        snr = max(0.0, peak_value / float(stddev))
        if snr < detection_sigma:
            continue
        nearest_name: str | None = None
        nearest_separation: float | None = None
        if nearest_indices is not None and nearest_separations_arcsec is not None and source_index < len(nearest_indices):
            nearest_star = gaia_stars[int(nearest_indices[source_index])]
            nearest_name = nearest_star.name or nearest_star.source_id
            nearest_separation = float(nearest_separations_arcsec[source_index])
        detections.append(
            TransientSourceDetection(
                source_path=frame_result.source_path,
                observation_time=_coerce_utc(frame_result.metadata.date_obs),
                x=x,
                y=y,
                ra_deg=ra_deg,
                dec_deg=dec_deg,
                snr=snr,
                flux=flux,
                peak_value=peak_value,
                nearest_catalog_name=nearest_name,
                nearest_catalog_separation_arcsec=nearest_separation,
            )
        )
    return detections


def _load_frame_measurement_contexts(
    frame_results: list[TransientFrameResult],
    notes: list[str],
) -> list[_FrameMeasurementContext]:
    contexts: list[_FrameMeasurementContext] = []
    for frame_result in frame_results:
        if frame_result.wcs_path is None:
            continue
        try:
            data = _as_mono_image(read_photometry_image_data(frame_result.source_path, dtype=float))
            finite_mask = np.isfinite(data)
            if data.size == 0 or not np.any(finite_mask):
                continue
            _mean, _median, stddev = sigma_clipped_stats(data, mask=~finite_mask)
            global_stddev = float(stddev)
            if not math.isfinite(global_stddev) or global_stddev <= 0.0:
                continue
            contexts.append(
                _FrameMeasurementContext(
                    frame_result=frame_result,
                    data=data,
                    wcs=WCS(read_header(frame_result.wcs_path)),
                    global_stddev=global_stddev,
                )
            )
        except Exception as exc:
            notes.append(f"Skipped variability measurement for {frame_result.source_path.name}: {exc}")
    return contexts


def _build_candidates(
    detections: list[TransientSourceDetection],
    *,
    min_frame_count: int,
    grouping_radius_arcsec: float,
    frame_contexts: list[_FrameMeasurementContext],
    detection_sigma: float,
    detection_fwhm: float,
) -> tuple[list[TransientCandidate], int, int]:
    if not detections or len(frame_contexts) < 2:
        return [], 0, 0
    groups = _group_detections(detections, grouping_radius_arcsec=grouping_radius_arcsec)
    candidates, screened_group_count = _build_candidate_list(
        groups,
        min_frame_count=min_frame_count,
        frame_contexts=frame_contexts,
        detection_sigma=detection_sigma,
        detection_fwhm=detection_fwhm,
    )
    return candidates, screened_group_count, len(groups)


def _group_detections(
    detections: list[TransientSourceDetection],
    *,
    grouping_radius_arcsec: float,
) -> list[_DetectionGroup]:
    if len(detections) == 1:
        return [_DetectionGroup(detections=[detections[0]])]

    coordinates = SkyCoord([detection.ra_deg for detection in detections] * u.deg, [detection.dec_deg for detection in detections] * u.deg)
    try:
        left_indices, right_indices, _separations, _distances = coordinates.search_around_sky(
            coordinates,
            grouping_radius_arcsec * u.arcsec,
        )
    except Exception:
        return _group_detections_incremental(
            detections,
            grouping_radius_arcsec=grouping_radius_arcsec,
        )

    parents = list(range(len(detections)))
    ranks = [0] * len(detections)

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        if ranks[left_root] < ranks[right_root]:
            parents[left_root] = right_root
        elif ranks[left_root] > ranks[right_root]:
            parents[right_root] = left_root
        else:
            parents[right_root] = left_root
            ranks[left_root] += 1

    for left_index, right_index in zip(left_indices, right_indices, strict=False):
        left = int(left_index)
        right = int(right_index)
        if left != right:
            union(left, right)

    grouped_detections: dict[int, list[TransientSourceDetection]] = {}
    for index, detection in enumerate(detections):
        grouped_detections.setdefault(find(index), []).append(detection)

    groups: list[_DetectionGroup] = []
    for group_detections in grouped_detections.values():
        group = _DetectionGroup(detections=[])
        for detection in sorted(group_detections, key=_detection_sort_key):
            group.add(detection)
        groups.append(group)
    return groups


def _group_detections_incremental(
    detections: list[TransientSourceDetection],
    *,
    grouping_radius_arcsec: float,
) -> list[_DetectionGroup]:
    groups: list[_DetectionGroup] = []
    for detection in sorted(detections, key=_detection_sort_key):
        matched_group = None
        detection_coord = SkyCoord(detection.ra_deg * u.deg, detection.dec_deg * u.deg)
        for group in groups:
            center_ra, center_dec = group.center()
            group_coord = SkyCoord(center_ra * u.deg, center_dec * u.deg)
            if float(detection_coord.separation(group_coord).arcsec) <= grouping_radius_arcsec:
                matched_group = group
                break
        if matched_group is None:
            groups.append(_DetectionGroup(detections=[detection]))
        else:
            matched_group.add(detection)
    return groups


def _build_candidate_list(
    groups: list[_DetectionGroup],
    *,
    min_frame_count: int,
    frame_contexts: list[_FrameMeasurementContext],
    detection_sigma: float,
    detection_fwhm: float,
) -> tuple[list[TransientCandidate], int]:
    candidates: list[TransientCandidate] = []
    screened_group_count = 0
    for group in groups:
        detections_for_candidate = tuple(sorted(group.detections, key=_detection_sort_key))
        ra_deg, dec_deg = _mean_sky_position(detections_for_candidate)
        frame_signals = _measure_candidate_frame_signals(
            ra_deg,
            dec_deg,
            frame_contexts,
            detection_fwhm=detection_fwhm,
        )
        variability = _candidate_variability_metrics(frame_signals, detection_sigma=detection_sigma)
        if variability is None or len(frame_signals) < min_frame_count:
            screened_group_count += 1
            continue
        variability_snr, flux_ratio = variability
        frame_count = sum(1 for signal in frame_signals if signal.flux > 0.0 and signal.snr >= detection_sigma)
        if frame_count < 1:
            screened_group_count += 1
            continue
        observation_times = [detection.observation_time for detection in detections_for_candidate if detection.observation_time is not None]
        snr_values = [detection.snr for detection in detections_for_candidate if math.isfinite(detection.snr)]
        nearest_catalog = min(
            (
                (detection.nearest_catalog_separation_arcsec, detection.nearest_catalog_name)
                for detection in detections_for_candidate
                if detection.nearest_catalog_separation_arcsec is not None
            ),
            default=(None, None),
            key=lambda item: float("inf") if item[0] is None else float(item[0]),
        )
        if nearest_catalog[0] is not None and float(nearest_catalog[0]) <= 5.0 and flux_ratio < _DEFAULT_MIN_FLUX_RATIO:
            screened_group_count += 1
            continue
        median_snr = float(np.median(snr_values)) if snr_values else 0.0
        max_snr = max(snr_values) if snr_values else 0.0
        candidate_id = f"TF-{len(candidates) + 1:03d}"
        first_observation = min(observation_times) if observation_times else None
        last_observation = max(observation_times) if observation_times else None
        catalog_text = "no nearby Gaia source"
        if nearest_catalog[0] is not None:
            catalog_name = nearest_catalog[1] or "nearest Gaia source"
            catalog_text = f"nearest Gaia source {catalog_name} at {float(nearest_catalog[0]):.2f} arcsec"
        summary_text = (
            f"{candidate_id}: RA {ra_deg:.6f} deg, Dec {dec_deg:.6f} deg; "
            f"variable by {variability_snr:.1f} sigma (flux ratio {flux_ratio:.1f}); "
            f"seen above threshold in {frame_count}/{len(frame_signals)} frame(s), median SNR {median_snr:.1f}, {catalog_text}."
        )
        blink_paths = tuple(signal.frame_result.source_path for signal in sorted(frame_signals, key=_signal_sort_key))
        candidates.append(
            TransientCandidate(
                candidate_id=candidate_id,
                ra_deg=ra_deg,
                dec_deg=dec_deg,
                frame_count=frame_count,
                detection_count=len(detections_for_candidate),
                first_observation=first_observation,
                last_observation=last_observation,
                median_snr=median_snr,
                max_snr=max_snr,
                nearest_catalog_name=nearest_catalog[1],
                nearest_catalog_separation_arcsec=nearest_catalog[0],
                detections=detections_for_candidate,
                summary_text=summary_text,
                variability_snr=variability_snr,
                flux_ratio=flux_ratio,
                blink_paths=blink_paths,
            )
        )
    candidates.sort(key=lambda candidate: (-candidate.variability_snr, -candidate.flux_ratio, -candidate.max_snr, candidate.candidate_id))
    for index, candidate in enumerate(candidates, start=1):
        candidate.candidate_id = f"TF-{index:03d}"
        candidate.summary_text = candidate.summary_text.replace(candidate.summary_text.split(":", 1)[0], candidate.candidate_id, 1)
    return candidates, screened_group_count


def _measure_candidate_frame_signals(
    ra_deg: float,
    dec_deg: float,
    frame_contexts: list[_FrameMeasurementContext],
    *,
    detection_fwhm: float,
) -> list[_CandidateFrameSignal]:
    aperture_radius_px = max(3.0, float(detection_fwhm) * 1.75)
    annulus_inner_radius_px = aperture_radius_px + max(3.0, float(detection_fwhm) * 1.25)
    annulus_outer_radius_px = annulus_inner_radius_px + max(4.0, float(detection_fwhm) * 1.75)
    coordinate = SkyCoord(ra_deg * u.deg, dec_deg * u.deg)
    signals: list[_CandidateFrameSignal] = []
    for context in frame_contexts:
        try:
            x_value, y_value = context.wcs.world_to_pixel(coordinate)
            x = float(np.asarray(x_value).reshape(-1)[0])
            y = float(np.asarray(y_value).reshape(-1)[0])
        except Exception:
            continue
        measurement = _measure_aperture_signal(
            context.data,
            x,
            y,
            global_stddev=context.global_stddev,
            aperture_radius_px=aperture_radius_px,
            annulus_inner_radius_px=annulus_inner_radius_px,
            annulus_outer_radius_px=annulus_outer_radius_px,
        )
        if measurement is None:
            continue
        flux, flux_uncertainty, snr, peak_snr = measurement
        signals.append(
            _CandidateFrameSignal(
                frame_result=context.frame_result,
                x=x,
                y=y,
                flux=flux,
                flux_uncertainty=flux_uncertainty,
                snr=snr,
                peak_snr=peak_snr,
            )
        )
    return signals


def _measure_aperture_signal(
    data: np.ndarray,
    x: float,
    y: float,
    *,
    global_stddev: float,
    aperture_radius_px: float,
    annulus_inner_radius_px: float,
    annulus_outer_radius_px: float,
) -> tuple[float, float, float, float] | None:
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    height, width = data.shape
    pad = int(math.ceil(annulus_outer_radius_px)) + 2
    x_center = int(round(x))
    y_center = int(round(y))
    if x_center < pad or y_center < pad or x_center >= width - pad or y_center >= height - pad:
        return None
    cutout = np.asarray(data[y_center - pad : y_center + pad + 1, x_center - pad : x_center + pad + 1], dtype=float)
    yy, xx = np.indices(cutout.shape, dtype=float)
    xx += x_center - pad
    yy += y_center - pad
    radii = np.hypot(xx - x, yy - y)
    aperture_mask = radii <= aperture_radius_px
    annulus_mask = (radii >= annulus_inner_radius_px) & (radii <= annulus_outer_radius_px)
    annulus_values = cutout[annulus_mask]
    annulus_values = annulus_values[np.isfinite(annulus_values)]
    if annulus_values.size < 10:
        return None
    aperture_values = cutout[aperture_mask]
    aperture_values = aperture_values[np.isfinite(aperture_values)]
    if aperture_values.size < 4:
        return None
    _mean, local_background, local_stddev = sigma_clipped_stats(annulus_values)
    local_noise = float(local_stddev)
    if not math.isfinite(local_noise) or local_noise <= 0.0:
        local_noise = float(np.nanstd(annulus_values))
    if not math.isfinite(local_noise) or local_noise <= 0.0:
        local_noise = global_stddev
    local_noise = max(local_noise, global_stddev * 0.25, 1e-6)
    flux = float(np.sum(aperture_values - float(local_background)))
    flux_uncertainty = local_noise * math.sqrt(float(aperture_values.size))
    snr = flux / max(flux_uncertainty, 1e-6)
    peak_snr = (float(np.nanmax(aperture_values)) - float(local_background)) / max(local_noise, 1e-6)
    return flux, flux_uncertainty, snr, peak_snr


def _candidate_variability_metrics(
    frame_signals: list[_CandidateFrameSignal],
    *,
    detection_sigma: float,
) -> tuple[float, float] | None:
    if len(frame_signals) < 2:
        return None
    finite_signals = [
        signal
        for signal in frame_signals
        if math.isfinite(signal.flux) and math.isfinite(signal.flux_uncertainty) and signal.flux_uncertainty > 0.0
    ]
    if len(finite_signals) < 2:
        return None
    brightest_signal = max(finite_signals, key=lambda signal: signal.flux)
    faintest_signal = min(finite_signals, key=lambda signal: signal.flux)
    if brightest_signal.flux <= 0.0 or brightest_signal.snr < detection_sigma:
        return None
    flux_delta = brightest_signal.flux - faintest_signal.flux
    delta_uncertainty = math.hypot(brightest_signal.flux_uncertainty, faintest_signal.flux_uncertainty)
    if flux_delta <= 0.0 or delta_uncertainty <= 0.0:
        return None
    variability_snr = flux_delta / delta_uncertainty
    noise_floor = max(brightest_signal.flux_uncertainty, faintest_signal.flux_uncertainty, 1.0)
    flux_ratio = (brightest_signal.flux + noise_floor) / max(faintest_signal.flux, noise_floor)
    absence_snr = max(2.0, detection_sigma * 0.55)
    has_absent_epoch = faintest_signal.flux <= 0.0 or faintest_signal.snr <= absence_snr
    brightest_peak_signal = max(finite_signals, key=lambda signal: signal.peak_snr)
    faintest_peak_signal = min(finite_signals, key=lambda signal: signal.peak_snr)
    peak_ratio = (brightest_peak_signal.peak_snr + 1.0) / max(faintest_peak_signal.peak_snr + 1.0, 1.0)
    host_background_candidate = (
        variability_snr >= max(28.0, detection_sigma * 5.0)
        and flux_ratio >= 1.35
        and peak_ratio >= 1.45
    )
    if variability_snr < max(_DEFAULT_VARIABILITY_SIGMA, detection_sigma * 1.25):
        return None
    if flux_ratio < _DEFAULT_MIN_FLUX_RATIO and not has_absent_epoch and not host_background_candidate:
        return None
    return float(variability_snr), float(flux_ratio)


def _build_result(
    root_path: Path,
    frame_results: tuple[TransientFrameResult, ...],
    total_files: int,
    solved_frame_count: int,
    astrometry_solved_count: int,
    detected_source_count: int,
    catalog_star_count: int,
    candidates: tuple[TransientCandidate, ...],
    notes: list[str],
) -> TransientSearchResult:
    report_text = _format_report_text(
        total_files=total_files,
        solved_frame_count=solved_frame_count,
        astrometry_solved_count=astrometry_solved_count,
        detected_source_count=detected_source_count,
        catalog_star_count=catalog_star_count,
        candidates=candidates,
        notes=notes,
    )
    return TransientSearchResult(
        root_path=root_path,
        frame_results=frame_results,
        total_files=total_files,
        solved_frame_count=solved_frame_count,
        astrometry_solved_count=astrometry_solved_count,
        detected_source_count=detected_source_count,
        catalog_star_count=catalog_star_count,
        candidates=candidates,
        notes=tuple(notes),
        report_text=report_text,
    )


def _format_report_text(
    *,
    total_files: int,
    solved_frame_count: int,
    astrometry_solved_count: int,
    detected_source_count: int,
    catalog_star_count: int,
    candidates: tuple[TransientCandidate, ...],
    notes: list[str],
) -> str:
    lines = [
        f"Transient Finder scanned {total_files} image(s).",
        f"Solved frames: {solved_frame_count}/{total_files} ({astrometry_solved_count} solved through astrometry.net).",
        f"Gaia comparison sources loaded: {catalog_star_count}.",
        f"Point-source detections retained before variability screening: {detected_source_count}.",
        f"Variable transient candidates retained: {len(candidates)}.",
    ]
    if candidates:
        lines.append("")
        lines.append("Top candidates:")
        lines.extend(candidate.summary_text for candidate in candidates[:12])
    if notes:
        lines.append("")
        lines.append("Notes:")
        lines.extend(f"- {note}" for note in notes)
    return "\n".join(lines)


def _catalog_coordinates(stars: list[CatalogStar]) -> SkyCoord | None:
    if not stars:
        return None
    return SkyCoord([star.ra_deg for star in stars] * u.deg, [star.dec_deg for star in stars] * u.deg)


def _nearest_catalog_match(
    ra_deg: float,
    dec_deg: float,
    stars: list[CatalogStar],
    catalog_coords: SkyCoord | None,
) -> tuple[str | None, float | None]:
    if catalog_coords is None or not stars:
        return None, None
    coordinate = SkyCoord(ra_deg * u.deg, dec_deg * u.deg)
    index, separation, _distance = coordinate.match_to_catalog_sky(catalog_coords)
    match_index = int(np.asarray(index).reshape(-1)[0])
    separation_arcsec = float(np.asarray(separation.to_value(u.arcsec)).reshape(-1)[0])
    star = stars[match_index]
    return star.name or star.source_id, separation_arcsec


def _mean_sky_position(detections: tuple[TransientSourceDetection, ...] | list[TransientSourceDetection]) -> tuple[float, float]:
    if not detections:
        return 0.0, 0.0
    ra_rad = np.deg2rad([detection.ra_deg for detection in detections])
    dec_rad = np.deg2rad([detection.dec_deg for detection in detections])
    x_values = np.cos(dec_rad) * np.cos(ra_rad)
    y_values = np.cos(dec_rad) * np.sin(ra_rad)
    z_values = np.sin(dec_rad)
    x_mean = float(np.mean(x_values))
    y_mean = float(np.mean(y_values))
    z_mean = float(np.mean(z_values))
    hyp = math.hypot(x_mean, y_mean)
    ra_deg = math.degrees(math.atan2(y_mean, x_mean)) % 360.0
    dec_deg = math.degrees(math.atan2(z_mean, hyp))
    return ra_deg, dec_deg


def _as_mono_image(data: np.ndarray) -> np.ndarray:
    image = np.asarray(data, dtype=float)
    if image.ndim == 2:
        return image
    if image.ndim == 3:
        if image.shape[0] in {1, 3} and image.shape[-1] not in {1, 3}:
            image = np.moveaxis(image, 0, -1)
        if image.shape[-1] == 1:
            return np.asarray(image[:, :, 0], dtype=float)
        return np.asarray(np.nanmean(image, axis=-1), dtype=float)
    return np.asarray(image.reshape(image.shape[-2:]), dtype=float) if image.ndim > 3 else np.asarray([], dtype=float)


def _finite_table_value(row: object, column_name: str, column_names: set[str], *, default: float) -> float:
    if column_name not in column_names:
        return default
    try:
        value = float(row[column_name])
    except Exception:
        return default
    return value if math.isfinite(value) else default


def _file_sort_key(file_result: FileScanResult) -> tuple[datetime, str]:
    timestamp = _coerce_utc(file_result.metadata.date_obs) or datetime.min.replace(tzinfo=UTC)
    return timestamp, file_result.path.name.lower()


def _detection_sort_key(detection: TransientSourceDetection) -> tuple[datetime, str, float]:
    timestamp = detection.observation_time or datetime.min.replace(tzinfo=UTC)
    return timestamp, detection.source_path.name.lower(), -detection.snr


def _signal_sort_key(signal: _CandidateFrameSignal) -> tuple[datetime, str]:
    timestamp = _coerce_utc(signal.frame_result.metadata.date_obs) or datetime.min.replace(tzinfo=UTC)
    return timestamp, signal.frame_result.source_path.name.lower()


def _coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _raise_if_cancelled(cancel_callback: Callable[[], bool] | None) -> None:
    if cancel_callback is not None and cancel_callback():
        raise RuntimeError("Transient search cancelled.")


def _emit(progress_callback: Callable[[str], None] | None, message: str) -> None:
    if progress_callback is not None:
        progress_callback(message)