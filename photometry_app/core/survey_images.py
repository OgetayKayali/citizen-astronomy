from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Any

from astroquery.hips2fits import hips2fits
from astropy.wcs import WCS
import numpy as np


_CACHE_DIRECTORY_NAME = "sky-explorer-surveys"
_CACHE_FORMAT_VERSION = 1
_MAX_CACHE_FILES = 64


@dataclass(frozen=True, slots=True)
class SurveyDefinition:
    key: str
    title: str
    hips_id: str

    @property
    def survey_id(self) -> str:
        return self.hips_id


SURVEY_DEFINITIONS: tuple[SurveyDefinition, ...] = (
    SurveyDefinition("dss2_blue", "DSS2 Blue", "CDS/P/DSS2/blue"),
    SurveyDefinition("shs_ha", "SHS Ha", "CDS/P/SHS"),
    SurveyDefinition("panstarrs", "PanSTARRS", "CDS/P/PanSTARRS/DR1/color-i-r-g"),
    SurveyDefinition("iphas_dr2_ha", "IPHAS DR2 Ha", "CDS/P/IPHAS/DR2/Ha"),
)
SURVEY_IMAGE_DEFINITIONS = SURVEY_DEFINITIONS
SURVEY_DEFINITIONS_BY_KEY: Mapping[str, SurveyDefinition] = MappingProxyType(
    {definition.key: definition for definition in SURVEY_DEFINITIONS}
)


def survey_definition_for_key(survey_key: str) -> SurveyDefinition:
    normalized_key = str(survey_key or "").strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return SURVEY_DEFINITIONS_BY_KEY[normalized_key]
    except KeyError as exc:
        available = ", ".join(definition.key for definition in SURVEY_DEFINITIONS)
        raise ValueError(f"Unknown survey key {survey_key!r}; expected one of: {available}.") from exc


def get_survey_definition(survey_key: str) -> SurveyDefinition:
    return survey_definition_for_key(survey_key)


@dataclass(frozen=True, slots=True)
class SurveyImageRequest:
    survey_key: str
    wcs: WCS
    width: int
    height: int
    target_rect: tuple[float, float, float, float]
    cache_dir: Path
    progress_callback: Callable[[str], None] | None = None

    @property
    def target_rectangle(self) -> tuple[float, float, float, float]:
        return self.target_rect


@dataclass(frozen=True, slots=True)
class SurveyImageResult:
    survey: SurveyDefinition
    image_data: np.ndarray
    target_rect: tuple[float, float, float, float]
    loaded_from_cache: bool

    @property
    def data(self) -> np.ndarray:
        return self.image_data

    @property
    def target_rectangle(self) -> tuple[float, float, float, float]:
        return self.target_rect

    @property
    def from_cache(self) -> bool:
        return self.loaded_from_cache


def retrieve_survey_image(request: SurveyImageRequest) -> SurveyImageResult:
    survey = survey_definition_for_key(request.survey_key)
    width, height, target_rect = _validated_request_geometry(request)
    query_wcs = _query_wcs(request.wcs, width=width, height=height)
    wcs_header = _serialized_wcs_header(query_wcs)
    cache_metadata = _cache_metadata(
        survey=survey,
        wcs_header=wcs_header,
        width=width,
        height=height,
        target_rect=target_rect,
    )
    cache_root = Path(request.cache_dir).expanduser() / _CACHE_DIRECTORY_NAME
    cache_path = cache_root / f"{_cache_key(cache_metadata)}.npz"

    cached_data = _load_cached_image(cache_path, cache_metadata, width=width, height=height)
    if cached_data is not None:
        _emit_progress(request.progress_callback, f"Loaded cached {survey.title} survey image.")
        return SurveyImageResult(survey, cached_data, target_rect, True)

    _emit_progress(request.progress_callback, f"Querying CDS hips2fits for {survey.title}.")
    response = _query_hips2fits(survey, query_wcs)
    try:
        image_data = _image_data_from_response(response, width=width, height=height)
    finally:
        close_response = getattr(response, "close", None)
        if callable(close_response):
            close_response()

    _store_cached_image(cache_path, cache_metadata, image_data)
    _prune_cache(cache_root)
    _emit_progress(request.progress_callback, f"Downloaded {survey.title} survey image.")
    return SurveyImageResult(survey, image_data, target_rect, False)


def fetch_survey_image(request: SurveyImageRequest) -> SurveyImageResult:
    return retrieve_survey_image(request)


def scale_wcs_for_pixel_sampling(wcs: WCS, sampling_step: int) -> WCS:
    """Expand a sliced WCS pixel scale when native pixels were subsampled."""
    step = int(sampling_step)
    if step <= 1:
        return wcs
    if not wcs.wcs.has_cd():
        return wcs
    scaled = wcs.deepcopy()
    scaled.wcs.cd = np.asarray(scaled.wcs.cd, dtype=float) * float(step)
    return scaled


def survey_target_rect_in_source_pixels(
    source_wcs: WCS,
    viewport_wcs: WCS,
    *,
    output_width: int,
    output_height: int,
) -> tuple[float, float, float, float]:
    """Map survey output corners back onto the source-image pixel grid."""
    if output_width <= 0 or output_height <= 0:
        raise ValueError("Survey output width and height must be positive.")
    corner_points = (
        (0.0, 0.0),
        (float(output_width), 0.0),
        (0.0, float(output_height)),
        (float(output_width), float(output_height)),
    )
    source_x_values: list[float] = []
    source_y_values: list[float] = []
    for output_x, output_y in corner_points:
        world_x, world_y = viewport_wcs.pixel_to_world_values(output_x, output_y)
        source_x, source_y = source_wcs.world_to_pixel_values(world_x, world_y)
        source_x_values.append(float(source_x))
        source_y_values.append(float(source_y))
    left = min(source_x_values)
    top = min(source_y_values)
    right = max(source_x_values)
    bottom = max(source_y_values)
    width = right - left
    height = bottom - top
    if width <= 0.0 or height <= 0.0:
        raise ValueError("Survey target rectangle has non-positive size.")
    return left, top, width, height


def _validated_request_geometry(
    request: SurveyImageRequest,
) -> tuple[int, int, tuple[float, float, float, float]]:
    if isinstance(request.width, bool) or isinstance(request.height, bool):
        raise ValueError("Survey image width and height must be positive integers.")
    try:
        width = int(request.width)
        height = int(request.height)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Survey image width and height must be positive integers.") from exc
    if width != request.width or height != request.height or width <= 0 or height <= 0:
        raise ValueError("Survey image width and height must be positive integers.")

    try:
        target_rect = tuple(float(value) for value in request.target_rect)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Survey image target_rect must contain four finite numeric values.") from exc
    if len(target_rect) != 4 or not np.all(np.isfinite(target_rect)):
        raise ValueError("Survey image target_rect must contain four finite numeric values.")
    if target_rect[2] <= 0.0 or target_rect[3] <= 0.0:
        raise ValueError("Survey image target_rect width and height must be positive.")
    return width, height, target_rect


def _query_wcs(source_wcs: WCS, *, width: int, height: int) -> WCS:
    if not isinstance(source_wcs, WCS):
        raise ValueError("Survey image requests require an astropy WCS.")
    if not source_wcs.has_celestial:
        raise ValueError("Survey image requests require a celestial astropy WCS.")
    query_wcs = source_wcs.celestial.deepcopy()
    if query_wcs.pixel_n_dim != 2 or query_wcs.world_n_dim != 2:
        raise ValueError("Survey image requests require a two-dimensional celestial WCS.")
    query_wcs.array_shape = (height, width)
    if query_wcs.array_shape != (height, width) or query_wcs.pixel_shape != (width, height):
        raise ValueError("Could not apply the requested pixel shape to the survey WCS.")
    return query_wcs


def _serialized_wcs_header(query_wcs: WCS) -> str:
    return query_wcs.to_header(relax=True).tostring(sep="\n", endcard=False, padding=False)


def _cache_metadata(
    *,
    survey: SurveyDefinition,
    wcs_header: str,
    width: int,
    height: int,
    target_rect: tuple[float, float, float, float],
) -> dict[str, Any]:
    return {
        "version": _CACHE_FORMAT_VERSION,
        "survey_key": survey.key,
        "survey_id": survey.hips_id,
        "wcs_header": wcs_header,
        "width": width,
        "height": height,
        "target_rect": list(target_rect),
    }


def _cache_key(metadata: Mapping[str, Any]) -> str:
    serialized = json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _query_hips2fits(survey: SurveyDefinition, query_wcs: WCS) -> Any:
    return hips2fits.query_with_wcs(hips=survey.hips_id, wcs=query_wcs, format="fits")


def _image_data_from_response(response: Any, *, width: int, height: int) -> np.ndarray:
    if response is None:
        raise ValueError("CDS hips2fits returned no image response.")

    candidates: list[Any] = []
    direct_data = getattr(response, "data", None)
    if direct_data is not None:
        candidates.append(direct_data)
    else:
        try:
            hdus = iter(response)
        except TypeError:
            hdus = iter(())
        for hdu in hdus:
            hdu_data = getattr(hdu, "data", None)
            if hdu_data is not None:
                candidates.append(hdu_data)

    if not candidates:
        raise ValueError("CDS hips2fits returned a non-image response with no FITS image data.")

    image_errors: list[ValueError] = []
    for candidate in candidates:
        try:
            return _validated_image_data(candidate, width=width, height=height)
        except ValueError as exc:
            image_errors.append(exc)
    raise image_errors[-1]


def _validated_image_data(data: Any, *, width: int, height: int) -> np.ndarray:
    try:
        image_data = np.asanyarray(data)
    except Exception as exc:
        raise ValueError("CDS hips2fits returned data that is not a usable numeric image.") from exc
    if image_data.size == 0:
        raise ValueError("CDS hips2fits returned a blank image with no pixels.")
    if not np.issubdtype(image_data.dtype, np.number) or np.issubdtype(image_data.dtype, np.complexfloating):
        raise ValueError("CDS hips2fits returned a non-image response; numeric pixels were expected.")

    expected_gray_shape = (height, width)
    expected_color_shape = (height, width, 3)
    expected_channel_first_shape = (3, height, width)
    if image_data.ndim == 2:
        if image_data.shape != expected_gray_shape:
            raise ValueError(
                f"CDS hips2fits returned image shape {image_data.shape}; expected {expected_gray_shape}."
            )
    elif image_data.ndim == 3:
        if image_data.shape == expected_channel_first_shape:
            image_data = np.moveaxis(image_data, 0, -1)
        elif image_data.shape != expected_color_shape:
            raise ValueError(
                "CDS hips2fits returned a non-image channel layout; expected HxWx3 or 3xHxW data."
            )
    else:
        raise ValueError("CDS hips2fits returned a non-image response; expected a 2D or 3-channel image.")

    finite_mask = np.isfinite(image_data)
    if not np.any(finite_mask):
        raise ValueError("Survey image has no coverage: the response contains no finite pixels.")
    finite_values = image_data[finite_mask]
    if not np.any(finite_values != 0):
        raise ValueError("Survey image is blank: all finite pixels are zero.")

    if not np.all(finite_mask):
        finite_minimum = np.min(finite_values)
        finite_maximum = np.max(finite_values)
        image_data = np.nan_to_num(
            image_data,
            copy=True,
            nan=0.0,
            posinf=finite_maximum,
            neginf=finite_minimum,
        )
    return np.array(image_data, copy=True, order="C")


def _load_cached_image(
    cache_path: Path,
    expected_metadata: Mapping[str, Any],
    *,
    width: int,
    height: int,
) -> np.ndarray | None:
    if not cache_path.is_file():
        return None
    try:
        with np.load(cache_path, allow_pickle=False) as cached:
            metadata_value = cached["metadata"]
            if metadata_value.ndim != 0:
                raise ValueError("Invalid survey cache metadata.")
            metadata = json.loads(str(metadata_value.item()))
            if metadata != dict(expected_metadata):
                raise ValueError("Survey cache metadata does not match its request.")
            image_data = _validated_image_data(cached["image_data"], width=width, height=height)
            return np.array(image_data, copy=True, order="C")
    except Exception:
        _discard_cache_file(cache_path)
        return None


def _store_cached_image(cache_path: Path, metadata: Mapping[str, Any], image_data: np.ndarray) -> None:
    temporary_path: Path | None = None
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f".{cache_path.stem}-",
            suffix=".tmp",
            dir=cache_path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            np.savez_compressed(
                temporary_file,
                image_data=image_data,
                metadata=np.asarray(
                    json.dumps(dict(metadata), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                ),
            )
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, cache_path)
    except OSError:
        if temporary_path is not None:
            _discard_cache_file(temporary_path)


def _prune_cache(cache_root: Path, *, max_files: int = _MAX_CACHE_FILES) -> None:
    try:
        cache_files = sorted(
            cache_root.glob("*.npz"),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
            reverse=True,
        )
    except OSError:
        return
    for cache_path in cache_files[max(0, int(max_files)) :]:
        _discard_cache_file(cache_path)


def _discard_cache_file(cache_path: Path) -> None:
    try:
        cache_path.unlink(missing_ok=True)
    except OSError:
        pass


def _emit_progress(progress_callback: Callable[[str], None] | None, message: str) -> None:
    if progress_callback is not None:
        progress_callback(message)
